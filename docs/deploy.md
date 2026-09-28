# 部署指南

Bluebird 是纯 Python 标准库单文件服务，**不依赖 Docker**，但提供 Docker 镜像方便部署。
下面是 4 种部署方式，按自己情况任选其一。配置项完整表见 [config.md](config.md)，使用入门见 [usage.md](usage.md)。

| 方式 | 适合 | 持久化 | 难度 |
| --- | --- | --- | --- |
| [A. 裸 Python](#a-裸-python) | 临时调试 / 已有 Python 环境 | 你自己管 | ⭐ |
| [B. Docker](#b-docker单行命令) | 任何能跑 Docker 的机器 | Docker volume | ⭐ |
| [C. Docker Compose](#c-docker-compose生产推荐) | 自由服务器 / VPS / NAS | 命名 volume | ⭐⭐ |
| [D. 托管平台](#d-托管平台render--zeabur--railway) | 不想自己管服务器 | 看平台（多数免费层是临时盘） | ⭐ |

> **通用要点**：服务优先监听平台注入的 `PORT`，其次 `NOTIFY_PORT`（默认 8082）；默认根路径部署（子域名/独立端口推荐）；设置 `BASE_PATH=bluebird` 可挂到 `/bluebird/` 子路径。

---

## A. 裸 Python

**适合**：临时调试 / 已有 Python 3.11+ 的机器 / 不想装 Docker 的场景。

```bash
git clone https://github.com/hancic128/bluebird.git
cd bluebird
cp .env.example .env       # 填写必填项（见 config.md）
python3 server.py          # 标准库直接跑
```

- 依赖：仅 Python 3.11+（`zoneinfo` 需要 3.9+，dataclass 用 3.7+；测试在 3.11 上跑）。
- 持久化：自己负责数据目录（默认 `NOTIFY_DB=/opt/bluebird/bluebird.db`），改环境变量指到你控制的目录。
- 反向代理、健康检查、systemd 托管等由你自行负责。

> 不要用 `sudo python3 server.py`。若 80/443 端口需要 root，**用 Nginx/Caddy 反代到 8082**（见 [C.2 反代](#c2-反代nginx-示例)）。

---

## B. Docker（单行命令）

**适合**：任何能跑 Docker 的机器、临时测试、CI runner。

```bash
docker run -d --name bluebird -p 8082:8082 \
  -e WEBHOOK_SECRET=xxx -e BARK_KEY=xxx -e NOTIFY_OWNER=your-username \
  -e NOTIFY_AUTH_USER=admin -e NOTIFY_AUTH_PASS=xxx \
  -v bluebird-data:/opt/bluebird \
  ghcr.io/hancic128/bluebird
curl http://127.0.0.1:8082/health   # {"ok": true}
```

- 镜像：`ghcr.io/hancic128/bluebird`（`latest` 与版本 tag 同步推送）。
- 数据卷 `bluebird-data` 命名卷；想用绑定挂载改成 `-v /srv/bluebird:/opt/bluebird`。

---

## C. Docker Compose（生产，推荐）

**适合**：自有服务器 / VPS / NAS / 树莓派。带数据卷、健康检查、重启策略。

### C.1 部署

```bash
git clone https://github.com/hancic128/bluebird.git
cd bluebird
cp .env.example .env       # 填写必填项（见 config.md）
docker compose up -d --build
curl http://127.0.0.1:8082/health   # {"ok": true}
```

升级：

```bash
cd bluebird
git pull
docker compose up -d --build
# 数据卷 /opt/bluebird 自动保留
```

### C.2 反代（Nginx 示例）

**子域名根路径部署（推荐）**——所有路径透传，无需前缀处理：

```nginx
server {
    server_name bluebird.example.com;

    location / {
        proxy_pass http://127.0.0.1:8082;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

**子路径部署**（`BASE_PATH=bluebird` 时）——`location /bluebird/` 无尾斜杠透传完整路径（服务端自剥前缀）：

```nginx
location /bluebird/ {
    proxy_pass http://127.0.0.1:8082;   # 无尾斜杠：透传完整路径（配合 BASE_PATH）
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

> 强制 HTTPS：上面 server 段里加 `listen 443 ssl http2;` + 证书配置，或前置 Cloudflare。**面板登录 Cookie 仅在 HTTPS 下加 `Secure` 标志**。

### C.3 数据迁移

数据卷 `/opt/bluebird` 保存去重记录 / 审计日志 / 动态配置（来源、渠道、开关）。换机器时：

```bash
# 旧机器
docker compose down
rsync -av /opt/bluebird new-server:/opt/bluebird

# 新机器
git clone https://github.com/hancic128/bluebird.git
cd bluebird && cp .env.example .env  # 配置要重新填（不能跨机器带 secret）
docker compose up -d --build
```

---

## D. 托管平台（Render / Zeabur / Railway）

**适合**：不想自己管服务器 / 偶尔访问 / 试用。

### D.1 Render

1. 打开 <https://dashboard.render.com/new/web> → **Connect** 你的 GitHub 仓库（自动识别 Dockerfile）
2. **Health Check Path** 填：`/health`
3. 环境变量按 [config.md](config.md) 填写（必填：`WEBHOOK_SECRET`、`BARK_KEY` 或 `FEISHU_*` 等、`NOTIFY_OWNER`、`NOTIFY_AUTH_USER` / `NOTIFY_AUTH_PASS`）
4. Deploy 后访问 `https://<你的应用>.onrender.com/ui`，Webhook URL 为 `https://<你的应用>.onrender.com/hooks/<来源 ID>`（面板来源详情里复制）

**注意（免费层）**：
- 实例休眠：长时间无请求会停止，首次访问有冷启动延迟（30 秒左右）；Render 面板的 health check 会周期性唤醒
- **磁盘是临时的**：重启 / 重新部署后 SQLite（审计日志、动态配置、去重记录）会清空。需要持久化请挂载 **Persistent Disk** 到 `/opt/bluebird`（付费功能）
- 仓库 push 新代码会自动触发 redeploy（可在 Settings 关闭自动部署）

### D.2 Zeabur / Railway 等

- 从 GitHub 导入本仓库，自动识别 Dockerfile
- 配置环境变量（同 [config.md](config.md)），平台分配端口由 `PORT` 自动适配
- 磁盘持久化：各平台挂载持久卷到 `/opt/bluebird` 即可保留审计数据

---

## 验证清单

部署完之后，建议依次验证：

```bash
# 1. 健康检查
curl https://你的域名/health
# → {"ok": true}

# 2. 通用 Hook（无需 GitHub App；Authorization: Bearer）
curl -X POST https://你的域名/hooks/<来源 ID> \
  -H "Authorization: Bearer $GENERIC_TOKEN" \
  -d '{"title":"测试","body":"链路通了"}'
# → {"ok": true, "source": "generic", "pushed": true}

# 3. 面板
# 浏览器打开 /ui 登录后应能看到记录
```