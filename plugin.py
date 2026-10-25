# -*- coding: utf-8 -*-
"""塔菲人格定时/口令切换插件

功能：
- 定时人格：在设定时段内自动使用临时人格，时段外恢复原人格（支持跨天）
- 口令人格：白名单 QQ 发送暗号后立即切换，持续设定时长后自动恢复
- 只做运行时提示词覆盖，不修改 bot_config.toml 等任何配置文件

实现要点：
- 用 chat.receive.before_process 监听暗号
- 用 maisaka.planner.before_request / maisaka.replyer.before_model_request 改写 Context Items
- 用 maisaka.replyer.before_request 追加一条兜底的人格要求
"""

from __future__ import annotations

import re
import time
from datetime import datetime, time as dt_time
from typing import Any, Dict, List, Optional, Tuple

from maibot_sdk import Field, HookHandler, MaiBotPlugin, PluginConfigBase
from maibot_sdk.types import HookMode, HookOrder


# ============================================================
# 配置模型（WebUI 可编辑）
# ============================================================
class PluginSectionConfig(PluginConfigBase):
    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="插件总开关")
    config_version: str = Field(default="1.0.0", description="配置版本号（请勿手动修改）")


class ScheduleConfig(PluginConfigBase):
    __ui_label__ = "定时人格"
    __ui_icon__ = "clock"
    __ui_order__ = 1

    enabled: bool = Field(default=False, description="是否启用定时切换")
    start_time: str = Field(default="23:00", description="开始时间，格式 HH:MM")
    end_time: str = Field(default="07:00", description="结束时间，格式 HH:MM（支持跨天，如 23:00–07:00）")
    persona: str = Field(
        default="你现在是「深夜电台主持人」人格：语速慢、句子短，用低沉温和的语气说话，喜欢用比喻，自称用\"我\"，句尾不再带\"喵\"。",
        description="定时时段内使用的临时人格提示词",
        json_schema_extra={"x-widget": "textarea"},
    )


class PhraseConfig(PluginConfigBase):
    __ui_label__ = "口令人格"
    __ui_icon__ = "key"
    __ui_order__ = 2

    enabled: bool = Field(default=True, description="是否启用口令切换")
    whitelist_qq: List[str] = Field(default_factory=list, description="允许触发口令的 QQ 号列表")
    secret_phrase: str = Field(default="/变身", description="触发暗号（消息中包含该文本即触发）")
    duration_minutes: int = Field(default=5, description="口令切换持续多少分钟后自动恢复")
    persona: str = Field(
        default="你现在切换成「傲娇侦探」人格：说话带点得意和怀疑，喜欢反问，自称\"本侦探\"，语气俏皮但不讨好，句子依然简短。",
        description="口令触发后使用的临时人格提示词",
        json_schema_extra={"x-widget": "textarea"},
    )


class TransitionConfig(PluginConfigBase):
    __ui_label__ = "转场词"
    __ui_icon__ = "message"
    __ui_order__ = 3

    enabled: bool = Field(default=True, description="切换人格时是否发送一条转场提示消息")
    text: str = Field(
        default="【人格切换】塔菲换了个样子喵~",
        description="切换人格时发送的转场词文本；留空则不发送",
        json_schema_extra={"x-widget": "textarea"},
    )


class PersonaSwitchConfig(PluginConfigBase):
    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    phrase: PhraseConfig = Field(default_factory=PhraseConfig)
    transition: TransitionConfig = Field(default_factory=TransitionConfig)


# ============================================================
# 插件主类
# ============================================================
class PersonaSwitchPlugin(MaiBotPlugin):
    """人格定时/口令切换插件。"""

    config_model = PersonaSwitchConfig

    def __init__(self):
        super().__init__()
        # 口令人格的失效时间戳；None 表示未激活
        self._phrase_expire_at: Optional[float] = None
        # 上一次生效的来源，用于只在切换/回退时打日志
        self._last_source: Optional[str] = None
        # 时间格式错误只提醒一次
        self._schedule_warned = False
        # 本轮定时时段是否已经发过转场词
        self._schedule_announced = False

    async def on_load(self) -> None:
        self._phrase_expire_at = None
        self._last_source = None
        self._schedule_announced = False
        cfg = self.config
        self.ctx.logger.info(
            "人格切换插件已加载：定时=%s(%s-%s)，口令=%s(暗号=%s, 白名单=%d人, 持续=%d分钟)，转场词=%s",
            cfg.schedule.enabled,
            cfg.schedule.start_time,
            cfg.schedule.end_time,
            cfg.phrase.enabled,
            cfg.phrase.secret_phrase,
            len(cfg.phrase.whitelist_qq),
            cfg.phrase.duration_minutes,
            cfg.transition.enabled,
        )

    async def on_unload(self) -> None:
        self._phrase_expire_at = None
        self._last_source = None
        self._schedule_announced = False
        self.ctx.logger.info("人格切换插件已卸载，临时人格状态已清空")

    async def on_config_update(self, scope: str, config_data: dict[str, object], version: str) -> None:
        del config_data
        if scope == "self":
            self._schedule_warned = False
            self.ctx.logger.info("人格切换配置已更新：version=%s", version)

    # ===== 时间与状态判断 =====
    @staticmethod
    def _parse_hhmm(raw: str) -> Optional[dt_time]:
        """把 HH:MM 解析成 time；格式不对返回 None。"""
        try:
            hour_str, minute_str = str(raw).strip().split(":", 1)
            return dt_time(hour=int(hour_str), minute=int(minute_str))
        except Exception:
            return None

    def _is_in_schedule(self, now: datetime) -> bool:
        """判断当前是否落在定时时段内（支持跨天）。"""
        cfg = self.config.schedule
        if not cfg.enabled:
            return False
        start = self._parse_hhmm(cfg.start_time)
        end = self._parse_hhmm(cfg.end_time)
        if start is None or end is None:
            if not self._schedule_warned:
                self.ctx.logger.warning(
                    "定时人格时间格式不正确：start=%r end=%r，已按未启用处理",
                    cfg.start_time,
                    cfg.end_time,
                )
                self._schedule_warned = True
            return False

        now_t = now.time()
        if start == end:
            return False
        if start < end:
            return start <= now_t < end
        # 跨天：例如 23:00–07:00
        return now_t >= start or now_t < end

    def _phrase_active(self, now_ts: float) -> bool:
        """口令人格是否仍在生效；过期则清空状态。"""
        if self._phrase_expire_at is None:
            return False
        if now_ts < self._phrase_expire_at:
            return True
        # 到期，自动恢复
        self._phrase_expire_at = None
        return False

    def _resolve_persona(self) -> Optional[Tuple[str, str]]:
        """返回当前应使用的 (来源, 人格文本)；无需覆盖时返回 None。

        优先级：口令人格 > 定时人格 > 原人格。
        同时负责在来源变化时打一条切换/回退日志。
        """
        now = datetime.now()
        now_ts = time.time()

        if self.config.phrase.enabled and self._phrase_active(now_ts):
            source, persona = "phrase", self.config.phrase.persona.strip()
        elif self._is_in_schedule(now):
            source, persona = "schedule", self.config.schedule.persona.strip()
        else:
            source, persona = None, ""

        if source != self._last_source:
            if source == "phrase":
                remain = max(0, int((self._phrase_expire_at or now_ts) - now_ts))
                self.ctx.logger.info(
                    "临时人格生效：来源=口令，剩余 %d 秒，文本=%r",
                    remain,
                    persona[:60],
                )
            elif source == "schedule":
                self.ctx.logger.info("临时人格生效：来源=定时，文本=%r", persona[:60])
            else:
                self.ctx.logger.info("临时人格已结束，恢复原人格")
            self._last_source = source

        if not source or not persona:
            return None
        return source, persona

    # ===== 消息解析 =====
    @staticmethod
    def _extract_sender(message: Dict[str, Any]) -> str:
        """从序列化 SessionMessage 里取发送者 QQ。"""
        info = message.get("message_info") or {}
        user = info.get("user_info") or {}
        return str(
            user.get("user_id")
            or message.get("user_id")
            or message.get("from_id")
            or ""
        ).strip()

    @staticmethod
    def _extract_text(message: Dict[str, Any]) -> str:
        """从序列化 SessionMessage 里取纯文本内容。"""
        raw = message.get("raw_message")
        if isinstance(raw, list):
            chunks = [
                str(seg.get("data") or "")
                for seg in raw
                if isinstance(seg, dict) and seg.get("type") == "text"
            ]
            return "".join(chunks)
        if isinstance(raw, str):
            return raw
        plain = message.get("processed_plain_text") or message.get("text")
        return str(plain or "")

    # ===== 提示词改写 =====
    @staticmethod
    def _iter_system_text_parts(items: List[Any]):
        """遍历所有 SystemMessageItem 里的文本 part。"""
        for item in items:
            if not isinstance(item, dict) or item.get("item_type") != "SystemMessageItem":
                continue
            parts = item.get("parts")
            if not isinstance(parts, list):
                continue
            for part in parts:
                if (
                    isinstance(part, dict)
                    and part.get("type") == "text"
                    and isinstance(part.get("text"), str)
                ):
                    yield part

    def _apply_replyer_persona(self, items: List[Any], persona: str) -> bool:
        """替换 replyer 系统提示词里的身份段，保留后面的说话格式要求。"""
        marker = "现在请你读读之前的聊天记录"
        for part in self._iter_system_text_parts(items):
            text = part["text"]
            index = text.find(marker)
            if index >= 0:
                part["text"] = persona + "\n" + text[index:]
            else:
                part["text"] = f"【临时人格（优先级最高）】\n{persona}\n\n{text}"
            return True
        return False

    def _apply_planner_persona(self, items: List[Any], persona: str) -> bool:
        """替换 planner 系统提示词里的行为风格段，保留工具使用说明。"""
        pattern = re.compile(r"(的行为风格：)([\s\S]*?)(\n\n以上)", re.S)
        for part in self._iter_system_text_parts(items):
            text = part["text"]
            new_text, count = pattern.subn(
                lambda match: match.group(1) + persona + match.group(3),
                text,
                count=1,
            )
            if count:
                part["text"] = new_text
            else:
                part["text"] = f"【临时人格（优先级最高）】\n{persona}\n\n{text}"
            return True
        return False

    async def _announce(self, stream_id: str, source: str) -> bool:
        """在指定会话发送人格转场词。"""
        cfg = self.config.transition
        text = str(cfg.text or "").strip()
        if not cfg.enabled or not text or not stream_id:
            return False
        try:
            ok = await self.ctx.send.text(text, stream_id)
        except Exception as exc:
            self.ctx.logger.warning("人格转场词发送异常：%s", exc)
            return False
        if ok:
            self.ctx.logger.info("已发送人格转场词（来源=%s）：%r", source, text[:60])
        else:
            self.ctx.logger.warning("人格转场词发送失败（来源=%s, stream=%s）", source, stream_id)
        return bool(ok)

    # ===== Hook：监听暗号 =====
    @HookHandler(
        "chat.receive.before_process",
        name="persona_switch_phrase",
        description="白名单 QQ 发送暗号时切换到临时人格，并发送转场词",
        mode=HookMode.BLOCKING,
        order=HookOrder.EARLY,
    )
    async def on_message(self, **kwargs):
        if not self.config.plugin.enabled:
            self._schedule_announced = False
            return {"action": "continue"}

        message = kwargs.get("message") or {}
        if not isinstance(message, dict):
            return {"action": "continue"}

        stream_id = str(message.get("session_id") or "").strip()

        # 定时人格转场：进入时段后，在第一条消息所在会话里发一次
        announced_now = False
        in_schedule = self._is_in_schedule(datetime.now())
        if in_schedule and not self._schedule_announced:
            self._schedule_announced = True
            announced_now = await self._announce(stream_id, "定时")
        elif not in_schedule:
            self._schedule_announced = False

        cfg = self.config.phrase
        if not cfg.enabled:
            return {"action": "continue"}

        sender = self._extract_sender(message)
        text = self._extract_text(message)
        phrase = cfg.secret_phrase.strip()
        if not sender or not text or not phrase:
            return {"action": "continue"}

        whitelist = {str(qq).strip() for qq in (cfg.whitelist_qq or []) if str(qq).strip()}
        if sender not in whitelist:
            return {"action": "continue"}
        if phrase not in text:
            return {"action": "continue"}

        duration = max(1, int(cfg.duration_minutes))
        self._phrase_expire_at = time.time() + duration * 60
        self.ctx.logger.info(
            "收到人格口令：QQ=%s，暗号=%r，切换 %d 分钟",
            sender,
            phrase,
            duration,
        )
        if not announced_now:
            await self._announce(stream_id, "口令")
        return {"action": "continue"}

    # ===== Hook：planner 人格注入 =====
    @HookHandler(
        "maisaka.planner.before_request",
        name="persona_switch_planner",
        description="规划请求前替换行为风格",
        mode=HookMode.BLOCKING,
        order=HookOrder.EARLY,
        timeout_ms=3000,
    )
    async def on_planner_before_request(self, **kwargs):
        picked = self._resolve_persona() if self.config.plugin.enabled else None
        if not picked:
            return {"action": "continue"}

        items = kwargs.get("items")
        if not isinstance(items, list):
            self.ctx.logger.warning("planner Hook 未拿到 items，跳过人格改写")
            return {"action": "continue"}

        _, persona = picked
        if not self._apply_planner_persona(items, persona):
            self.ctx.logger.warning("planner 提示词里没有 SystemMessageItem，人格未注入")
            return {"action": "continue"}
        kwargs["items"] = items
        return {"action": "continue", "modified_kwargs": kwargs}

    # ===== Hook：replyer 兜底人格要求 =====
    @HookHandler(
        "maisaka.replyer.before_request",
        name="persona_switch_replyer_prompt",
        description="给回复请求追加临时人格要求",
        mode=HookMode.BLOCKING,
        order=HookOrder.EARLY,
        timeout_ms=3000,
    )
    async def on_replyer_before_request(self, **kwargs):
        picked = self._resolve_persona() if self.config.plugin.enabled else None
        if not picked:
            return {"action": "continue"}

        _, persona = picked
        extra = str(kwargs.get("extra_prompt") or "").strip()
        line = f"【临时人格（优先于上面所有设定）】{persona}"
        kwargs["extra_prompt"] = f"{extra}\n{line}" if extra else line
        return {"action": "continue", "modified_kwargs": kwargs}

    # ===== Hook：replyer 身份段替换 =====
    @HookHandler(
        "maisaka.replyer.before_model_request",
        name="persona_switch_replyer_items",
        description="回复请求前替换身份段",
        mode=HookMode.BLOCKING,
        order=HookOrder.EARLY,
        timeout_ms=3000,
    )
    async def on_replyer_before_model_request(self, **kwargs):
        picked = self._resolve_persona() if self.config.plugin.enabled else None
        if not picked:
            return {"action": "continue"}

        items = kwargs.get("items")
        if not isinstance(items, list):
            return {"action": "continue"}

        _, persona = picked
        if not self._apply_replyer_persona(items, persona):
            self.ctx.logger.warning("replyer 提示词里没有 SystemMessageItem，身份段未替换")
            return {"action": "continue"}
        kwargs["items"] = items
        return {"action": "continue", "modified_kwargs": kwargs}


def create_plugin():
    return PersonaSwitchPlugin()
