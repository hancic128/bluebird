# 安全策略

## 支持版本

我们为以下版本提供安全修复：

| 版本 | 支持状态 |
| --- | --- |
| 最新的 minor（`main` 分支 / 最新 `v*` tag） | ✅ 积极维护 |
| 上一 minor | 🟡 仅修复高危漏洞（90 天窗口） |
| 更早版本 | ❌ 不维护 |

请尽快升级到最新版本——`git pull` + 重启容器即可。

---

## 报告漏洞

**请不要**通过 GitHub Issues 公开报告安全问题。请私下联系：

- **首选**：GitHub → [Security Advisories](https://github.com/hancic128/bluebird/security/advisories/new) 提交 private advisory
- **备选**：邮件至 maintainer（见仓库 `CODEOWNERS` / 个人 profile）

报告应包含：

1. 漏洞描述与影响范围
2. 复现步骤 / PoC（payload、命令、截图均可）
3. 受影响版本
4. 你是否已对外公开过（建议在修复前不要）

我们会在 **72 小时内**确认收到，并在 **14 天内**给出修复时间表或拒绝理由（拒绝一般是「不在支持范围」或「非漏洞」）。

---

## 已知安全设计

Bluebird 在设计上做了若干 fail-closed 选择，列出来便于部署者校验：

| 机制 | 默认行为 | 配置 |
| --- | --- | --- |
| 面板 / API / 文档访问 | **任一凭据为空 → 一律拒绝（503）** | `NOTIFY_AUTH_USER` / `NOTIFY_AUTH_PASS` 必须都配 |
| GitHub 来源 `/hooks/<id>` | 未配 secret → 401 | 来源级 Webhook Secret |
| 通用来源 `/hooks/<id>` | 未配 token → 401 | 来源级 Token |
| 写接口 CSRF | 同源校验失败 → 403 | 仅看 `Origin`/`Referer` 头，不依赖 cookie 属性 |
| 面板会话 Cookie | `HttpOnly; SameSite=Lax`；HTTPS 反代追加 `Secure` | `UI_SESSION_SECRET` 留空首启随机生成 |
| 数据库文件 | 自动 `chmod 0600` | `/opt/bluebird/bluebird.db` |
| 单请求体上限 | 1 MB（413 超限） | `MAX_BODY_BYTES` |
| 读写超时 | 30 秒 | `NOTIFY_TIMEOUT` |
| 响应安全头 | CSP / `X-Content-Type-Options` / `X-Frame-Options` / `Referrer-Policy` | 内置 |
| 通用 Webhook 签名 | 可选 `X-Bluebird-Signature-256: sha256=<HMAC-SHA256(body)>` | 渠道级 `GENERIC_WEBHOOK_SECRET` |

详细的威胁模型与默认假设见 README「[安全说明](README.md#安全说明)」一节。

---

## 部署者注意

- 反代（Nginx / Caddy / Cloudflare）务必**强制 HTTPS**，否则面板登录 Cookie 仍是明文传输。
- 数据卷 `/opt/bluebird` 权限仅服务进程可读；docker-compose 已声明 `volumes:` 但**OS 目录权限需自己收紧**（容器内 chmod 会被自动收紧到 0600，但宿主目录需 0700）。
- 不要把 `.env` 提交到 git——`.gitignore` 与 `.dockerignore` 已排除 `.env`、`.env.*`、`.env.bak-*`，但若你 fork 后改了 `.gitignore`，**先确认 `.env` 未被跟踪**：`git ls-files | grep -E '\.env$'`
- `WEBHOOK_SECRET` 一旦怀疑泄露，立刻在 GitHub App 后台更换并重启容器。
- 面板密码（`NOTIFY_AUTH_PASS`）默认走数据库迁移与 .env 迁移；轮换时**改完两边**（环境变量 + 面板设置项）。
- 通用来源的 `GENERIC_TOKEN` 是裸 Bearer Token，建议定期轮换。

---

## 安全相关历史

本项目所有公开漏洞修复会列在下个 Release notes 里（[Releases](https://github.com/hancic128/bluebird/releases)），并在此处补充链接。

截至 v0.3.1，**未收到任何对外披露的漏洞**。

---

## 致谢

负责任披露漏洞的研究者将在修复 release 中致谢（除非你要求匿名）。