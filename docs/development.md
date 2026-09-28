# 开发

提 PR / 贡献指南的完整版见 [CONTRIBUTING.md](../CONTRIBUTING.md)。本文档聚焦**动手写代码前需要了解的项目约定**。

---

## 项目结构

```
bluebird/
├── server.py              # 全部后端逻辑（单文件是有意为之）
├── ui.html                # 面板 SPA（不构建，原生 HTML + JS）
├── login.html             # 登录页
├── favicon.ico            # favicon 兜底（运行时根据主题动态生成 SVG）
├── Dockerfile             # 镜像构建（alpine + python:3.11）
├── docker-compose.yml     # 含 healthcheck + 数据卷
├── conftest.py            # pytest 测试环境注入（unittest 不读它）
├── tests/
│   ├── _env.py            # 测试凭据（import server.py 前注入）
│   ├── test_server.py     # 主流程：hooks / 签名 / 去重 / 分发 / 审计 / 子路径
│   ├── test_card.py       # Message Card 中间表示 + 各渠道渲染
│   └── fixtures/example_events.json   # 面板「示例事件推送」用
├── docs/                  # 用户文档（deploy / config / usage / sources-and-channels / development）
└── .github/               # Issue / PR 模板、CI / Release workflow
```

---

## 设计原则（动手前必读）

1. **单文件 `server.py`** 是有意为之，与"零依赖、镜像小"的设计目标直接相关。
   - 新功能若能放进 `server.py` 就放进去
   - **拆模块前请先开 issue 讨论**，不要直接 PR 一个拆文件改动
2. **零运行时第三方依赖**。若你贡献的代码 `import` 第三方包，PR 阶段就会被拒。
3. **前端 `ui.html` / `login.html` 不引入构建工具**（无 npm / Vite / webpack）——保持零依赖原则。
4. **修改前必加测试**：测试夹具在 `tests/fixtures/` 与 `tests/_env.py`，跑测试用标准库 `unittest`。

---

## 开发环境

```bash
git clone https://github.com/hancic128/bluebird.git
cd bluebird
cp .env.example .env       # 填 WEBHOOK_SECRET / NOTIFY_AUTH_USER / NOTIFY_AUTH_PASS / 至少一个渠道 key
python3 server.py          # 标准库直接跑
# 浏览器开 http://127.0.0.1:8082/ui
```

要求：**Python 3.11+**。`zoneinfo`（3.9+）与 dataclass（3.7+）都满足，测试在 3.11 上跑。

无 `requirements.txt` / `pyproject.toml`（运行时零依赖）；测试也不需要额外包。

---

## 跑测试

```bash
python3 -m unittest discover -s tests -v       # 主套件（248 个测试，~3 秒）
python3 -m py_compile server.py                # 语法检查
```

测试夹具加载顺序：`tests/test_card.py`（字母序在前）必须显式 `import tests._env` 才能注入 `os.environ`，否则 `server.py` 模块加载时凭据是空的——这是已知的踩坑点，已在 `conftest.py` + `_env.py` 双轨处理。

`tests/fixtures/example_events.json` 是面板「示例事件推送」用的真实 payload，也是 Card 渲染的测试输入；修改中间表示时请同步更新 fixture。

---

## 新增一个渠道

接一个新推送渠道（比如 Discord）的常规改动：

1. `server.py` 加一个 `_dispatch_<type>()` 函数 + 注册到分发循环
2. 在 `EVENT_PALETTE` 旁给一张表，按事件类型给侧栏 / header 颜色（若渠道支持）
3. `ui.html` 渠道类型下拉、渠道行图标（`#dd-f-type` 那一组 + SVG / PNG data URI）、编辑弹窗表单
4. `tests/test_server.py` 加：成功路径、签名 / Token 类失败、超时重试；`tests/test_card.py` 加：Card 渲染对该渠道的字段映射
5. 文档：`docs/sources-and-channels.md` 加该渠道的配置步骤

> 卡片 / 纯文本的样式选择（飞书 / Slack 那种"用户可切"）不是默认行为，只在跟现有飞书 / Slack 一样对视觉要求高的渠道才加。

---

## 代码风格

- **Python 3.11+**；PEP 8；行宽 100（不强求，README / 源码实际更长）。
- 注释**只解释非显然的意图**（workaround、隐性不变式、刻意取舍）。不要给自解释代码加注释。
- 中文 / 英文混用遵循既有风格：模块 docstring 中文、变量名英文、用户文案中文为主。
- 测试覆盖所有新分支（happy path + 关键失败路径）。
- **commit 消息遵循 Conventional Commits**：`feat:` / `fix:` / `docs:` / `refactor:` / `test:` / `ci:` / `fixup!:`。

---

## 调试技巧

- **`docker logs bluebird`** 第一屏会列出启动时已加载的源 / 渠道，以及 fail-closed 警告（缺凭据会列出来）
- **健康检查** `curl http://127.0.0.1:8082/health` → `{"ok": true}`
- **API 调试**：所有 `/api/*` 路由需要登录后带 cookie；`curl --cookie-jar` 登录后复用
- **面板内预览**：「分发渠道」编辑弹窗底部「预览」分组，选示例事件即可不发消息看卡片效果
- **示例事件推送**：「分发渠道」行内「测试 ▾」按预置示例事件真实下发一条，验证配色与字段

---

## 发版（仅维护者）

```bash
git tag v0.4.0 && git push origin v0.4.0
# → 自动：测试、build + push 镜像 ghcr.io/hancic128/bluebird:{v0.4.0, latest}、GitHub Release
```

版本号遵循 [SemVer](https://semver.org/)：MAJOR 是不兼容变更、MINOR 是新功能、PATCH 是修复。

详情与维护者发布 checklist 见 [CONTRIBUTING.md](../CONTRIBUTING.md) §「发版流程」。