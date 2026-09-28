# 配置

部署方式见 [deploy.md](deploy.md)。本文档是**完整环境变量表**——按用途分组。

## 必填与可选

- **认证**：必须配 `WEBHOOK_SECRET` + `NOTIFY_AUTH_USER` + `NOTIFY_AUTH_PASS`，**任一为空即拒绝访问**
- **渠道**：至少配一个（Bark / 飞书 / 企微 / Slack / PushDeer / 通用 Webhook 任一）
- 其余不填走默认值

## 认证（必填，fail-closed）

| 变量 | 默认 | 必填 | 说明 |
| --- | --- | --- | --- |
| `WEBHOOK_SECRET` | 空 | ✅ | Webhook 签名密钥，创建 GitHub App 时填同一值（`openssl rand -hex 32` 生成） |
| `NOTIFY_AUTH_USER` | 空 | ✅ | 面板登录用户名 |
| `NOTIFY_AUTH_PASS` | 空 | ✅ | 面板登录密码；**两者任一为空时面板 / API / 文档一律拒绝访问** |
| `UI_SESSION_SECRET` | 空 | 否 | 面板会话签名密钥；留空则首启随机生成并落库（一般无需设置） |

## 来源过滤

| 变量 | 默认 | 必填 | 说明 |
| --- | --- | --- | --- |
| `NOTIFY_OWNER` | 空 | 否 | 只处理该账号名下仓库事件（GitHub 来源）；留空 = 处理所有仓库（建议填你自己的账号） |
| `GENERIC_TOKEN` | 空 | 否 | 通用 Hook 源 token（启用 `/hooks/<来源 ID>`）；不配 = 该端点 401 |

## 推送渠道（至少配置一个）

| 变量 | 默认 | 必填 | 说明 |
| --- | --- | --- | --- |
| `BARK_URL` | `https://api.day.app` | 否 | Bark 服务地址（自建时改） |
| `BARK_KEY` | 空 | 渠道 | Bark 推送 key |
| `BARK_SOURCE` | `github` | 否 | Bark 通知 subtitle 来源标识 |
| `FEISHU_APP_ID` | 空 | 渠道 | 飞书企业自建应用 App ID（需启用机器人能力、开通发消息权限并发布新版本） |
| `FEISHU_APP_SECRET` | 空 | 渠道 | 飞书应用 App Secret |
| `FEISHU_RECEIVE_ID` | 空 | 渠道 | 飞书接收目标（群 `chat_id` / 用户 `open_id` 等） |
| `FEISHU_RECEIVE_ID_TYPE` | `chat_id` | 否 | 接收 ID 类型：`chat_id` / `open_id` / `user_id` / `union_id` / `email` |
| `WECOM_WEBHOOK` | 空 | 渠道 | 企业微信群机器人 webhook |
| `SLACK_WEBHOOK` | 空 | 渠道 | Slack Incoming Webhook URL（卡片按事件类型给侧栏配色，可在面板切回纯文本） |
| `PUSHDEER_URL` | `https://api2.pushdeer.com` | 否 | PushDeer 服务地址（自建时改） |
| `PUSHDEER_KEY` | 空 | 渠道 | PushDeer 推送 key（多个 key 用英文逗号分隔） |
| `GENERIC_WEBHOOK_URL` | 空 | 渠道 | 通用 Webhook 渠道地址（接收 POST JSON） |
| `GENERIC_WEBHOOK_SECRET` | 空 | 否 | 通用 Webhook 签名密钥（填了则请求带 `X-Bluebird-Signature-256: sha256=<HMAC>`） |

> **动态配置**：`WEBHOOK_SECRET`、`BARK_*`、`FEISHU_*`、`WECOM_*`、`SLACK_WEBHOOK`、`PUSHDEER_*`、`GENERIC_WEBHOOK_*`、`GENERIC_TOKEN` 仅用于**首次启动迁移**为初始实例（写入 `bluebird.db`）；之后以面板配置为准，改配置无需重启、无需改环境变量。

## 监听与部署

| 变量 | 默认 | 必填 | 说明 |
| --- | --- | --- | --- |
| `NOTIFY_HOST` | `0.0.0.0` | 否 | 监听地址 |
| `PORT` / `NOTIFY_PORT` | `8082` | 否 | 监听端口（托管平台注入 `PORT` 自动生效） |
| `BASE_PATH` | 空（根路径） | 否 | 子路径前缀；填 `bluebird` 挂到 `/bluebird/` 下 |
| `NOTIFY_DB` | `/opt/bluebird/bluebird.db` | 否 | SQLite 数据库路径（数据卷默认位置） |

## 行为调优（可选）

| 变量 | 默认 | 必填 | 说明 |
| --- | --- | --- | --- |
| `LOG_RETENTION_DAYS` | `30` | 否 | 审计日志保留天数 |
| `DEDUP_SECONDS` | `60` | 否 | 重复事件去重窗口（秒） |
| `UI_SESSION_HOURS` | `24` | 否 | 面板登录会话时长（小时） |
| `MAX_BODY_BYTES` | `1048576` | 否 | 单请求体上限（字节），超限返回 413 |
| `NOTIFY_TIMEOUT` | `30` | 否 | 单连接读写超时（秒），防慢连接 / 半开连接 |

---

## 安全要点

- **不要把 `.env` 提交到 git**——`.gitignore` / `.dockerignore` 已排除，但 fork 后改了 `.gitignore` 的话先确认 `git ls-files | grep -E '\.env$'` 为空
- `WEBHOOK_SECRET` 长度 ≥ 32 字节随机；推荐 `openssl rand -hex 32`
- `NOTIFY_AUTH_PASS` 与面板里**独立配置项**两侧都要改
- 通用来源的 `GENERIC_TOKEN` 是裸 Bearer Token，建议定期轮换
- 数据卷 `/opt/bluebird` 权限仅服务进程可读：docker-compose 默认；裸 Python 部署请自己 `chmod 700 /opt/bluebird`