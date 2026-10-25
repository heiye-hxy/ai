# 提交到麦麦插件中心 —— 操作步骤

插件中心的收录规则：**插件必须有独立的公开 GitHub 仓库，仓库根目录有 `_manifest.json`（v2），并且至少发布过一个 GitHub Release**。登记通过 Issue 提交。

当前目录已经是符合要求的发布包，只差你用自己的 GitHub 账号推上去。

---

## 0. 先改掉占位符

编辑 `_manifest.json`，把 4 处 `YOUR_GITHUB_NAME` 换成你的 GitHub 用户名（或组织名）：

```json
"urls": {
  "repository": "https://github.com/你的用户名/maibot-persona-switch",
  "homepage": "https://github.com/你的用户名/maibot-persona-switch",
  "documentation": "https://github.com/你的用户名/maibot-persona-switch/blob/main/README.md",
  "issues": "https://github.com/你的用户名/maibot-persona-switch/issues"
}
```

同时按需修改：

- `id`：当前是 `com.taffy.persona-switch`。建议改成 `github.你的用户名.persona-switch`（官方推荐格式），并保持以后不变。
- `author.url`：改成你的主页地址（B 站 / GitHub 均可）。
- `license`：当前是 MIT，`LICENSE` 文件也是 MIT，两者要一致。

> 注意：manifest 里不要加 `categories`、`keywords`、`repository_url` 这类未定义字段，会导致校验失败。

---

## 1. 创建公开仓库并推送

在 GitHub 新建一个 **Public** 仓库，例如 `maibot-persona-switch`（不要勾选自动生成 README，避免冲突）。

然后在 `maibot-persona-switch` 目录里执行：

```bash
git init
git add .
git commit -m "feat: 人格定时/口令切换插件 v1.0.0"
git branch -M main
git remote add origin https://github.com/你的用户名/maibot-persona-switch.git
git push -u origin main
```

确认仓库根目录能看到：`_manifest.json`、`plugin.py`、`config.toml`、`LICENSE`、`README.md`。

---

## 2. 发布 GitHub Release（必须）

1. 打开仓库 → Releases → **Draft a new release**
2. Tag 填 `v1.0.0`（或 `1.0.0`），目标分支选 `main`
3. 标题写 `v1.0.0`
4. 说明里写本次更新内容
5. **不要留作草稿**，点击 Publish release

> 只打 Tag 不发布 Release、或保存为草稿，都不满足收录要求。

---

## 3. 提交 Issue 登记

打开：<https://github.com/Mai-with-u/plugin-repo/issues/new/choose>
选择 **“Add Plugin / 添加插件”** 模板，填写：

- **插件 ID**：`com.taffy.persona-switch`（与 `_manifest.json` 的 `id` 保持一致）
- **插件仓库**：`https://github.com/你的用户名/maibot-persona-switch`（不要带 `.git`）

提交后：

1. CI 会自动读取仓库根目录的 `_manifest.json` 并校验，结果会评论在 Issue 里。
2. 如果失败，按提示修复后在该 Issue 里发一条 `/recheck`（每个 Issue 最多 8 次 AI 审核）。
3. 校验通过后由维护者 `/approve`，插件进入插件中心。

---

## 4. 后续发版

- 修改 `_manifest.json` 的 `version`（三段式，如 `1.0.1`）→ 提交 → 打对应 Tag → 发布 Release。
- 已收录的版本 Tag/commit 不可再改；修 bug 请发新版本号。
- 插件 `id` 永远不要变。

---

## 检查清单

- [ ] `_manifest.json` 在仓库根目录，`manifest_version = 2`
- [ ] `id` 唯一且后续不再变化
- [ ] 填好了 `author.name` 和 `author.url`
- [ ] `urls.repository` 是公开仓库的 HTTPS 地址
- [ ] `LICENSE` 与 manifest 的 `license` 字段一致
- [ ] `version`、`host_application.*`、`sdk.*` 都是三段式，且前两位不是 99/999
- [ ] `README.md` 有功能介绍、安装、配置说明
- [ ] 已发布至少一个 GitHub Release
- [ ] Issue 里的插件 ID 与仓库地址正确
