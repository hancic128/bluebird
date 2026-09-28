# 贡献指南

感谢你考虑为 **青鸟 Bluebird** 贡献代码 / 文档 / 反馈。本项目目标是保持**单文件、零依赖、自托管友好**的轻量通知网关；本文档说明贡献前需要了解的所有约定。

> **TL;DR**
> - 单测门槛：所有改动都要通过 `python3 -m unittest discover -s tests`
> - 风格：Python 3.11、遵循 PEP 8、不引入第三方依赖
> - Commit：遵循 [Conventional Commits](https://www.conventionalcommits.org/)（`feat:` / `fix:` / `docs:` / `refactor:` / `test:` / `ci:` / `fixup!:`）
> - 提 PR：先开 issue 讨论大方向，再发 PR；CI 跑通后等 review

---

## 1. 项目结构

详见 [docs/development.md § 项目结构](docs/development.md#项目结构)。

**单文件原则**：`server.py` 刻意保持单文件，与"零依赖"的设计目标直接相关。新功能若能放进 `server.py` 就放进去；如果扩到 3000+ 行或新增大量复杂逻辑，再讨论拆模块。**拆分前请先开 issue 讨论**，不要直接 PR 一个拆文件改动。

---

## 2. 开发环境

详见 [docs/development.md § 开发环境](docs/development.md#开发环境)。简版：

```bash
# Python 3.11+；推荐 pyenv / uv / asdf
python3 -m unittest discover -s tests -v   # 跑测试（标准库 unittest，无需安装 pytest）
python3 -m py_compile server.py            # 语法检查
```

**无 `requirements.txt`**：运行时零第三方依赖。若你贡献的代码引入了 `import` 第三方包，PR 阶段就会被拒。

**最小可复现环境**：

```bash
cp .env.example .env
# 填 WEBHOOK_SECRET / NOTIFY_AUTH_USER / NOTIFY_AUTH_PASS / 至少一个渠道 key
python3 server.py
# 浏览器开 http://127.0.0.1:8082/ui
```

---

## 3. 提交规则

### Commit 消息

遵循 Conventional Commits：

```
feat: 增加 Discord 渠道
fix: 通用 Webhook 签名校验在收到空 body 时不再报 500
docs: 部署文档补充 Zeabur 持久卷挂载说明
refactor: 把 Card 字段构造从 is_point(..., meta) 抽出 is_point()
test: 覆盖 watch 事件无 actor 时的渲染
ci: release.yml 增加 platforms: linux/arm64
```

`fixup!:` 前缀用于「在原提交上 squash 修订」，rebase 后会自动合并。

### PR 流程

1. **先开 issue 讨论**——尤其是新渠道、新事件源、架构调整。直接发 PR 改大方向会被关。
2. Fork + 从 `main` 切分支（建议命名 `feat/xxx` / `fix/xxx`）。
3. PR 标题遵循 Conventional Commits（`feat: ...`），正文填 `.github/PULL_REQUEST_TEMPLATE.md` 的清单。
4. **CI 必须绿**：CI（push / PR）和 Release（tag）共享同一组测试，PR merge 前需要 CI 通过。
5. UI 改动请附截图/GIF（screenshots / `.agent_tmp/` 草稿可放 PR 正文）。
6. 一事一 PR，避免巨型 diff。
7. 触发 review 后等 maintainer 处理；冲突自己 rebase，不要 merge commit。

### 行为准则

请阅读 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。社区期待所有互动以善意、对事不对人为前提。

---

## 4. 代码风格

完整指南见 [docs/development.md § 代码风格](docs/development.md#代码风格)。要点：

- **Python 3.11+**；PEP 8；行宽 100（不强求）。
- 注释**只解释非显然的意图**，不要给自解释代码加注释。
- 不要在 `server.py` 引入第三方包；标准库够用。
- UI 改动放在 `ui.html` / `login.html`，**不使用 npm / 打包**——保持零依赖原则。
- 测试覆盖所有新分支（happy path + 关键失败路径）。

---

## 5. 报告 Bug

请走 [GitHub Issues](https://github.com/hancic128/bluebird/issues/new?template=bug_report.yml)，按 `bug_report.yml` 的清单填。

**安全漏洞**请**不要**开 public issue——参考 [SECURITY.md](SECURITY.md) 私下报告。

---

## 6. 提 Feature Request

走 `feature_request.yml` 模板。提需求前请先想清楚：

- 这个需求是**多数自托管用户的**还是**少数人的定制**？少数定制请考虑 fork。
- 是否能复用现有数据流（**不**新增 webhook 端点、**不**新增面板页签）？
- 是否会破坏"零依赖 / 单文件 / 25 MB 镜像"的设计目标？

维护者保留对范围扩张说"不"的权力——这是为了保持项目可维护性。

---

## 7. 发版流程（仅维护者）

```bash
git tag v0.4.0 && git push origin v0.4.0
# → CI 跑测试、build + push ghcr.io/hancic128/bluebird:{v0.4.0, latest}、创建 Release
```

发版前请确认 `main` 在干净状态、所有 PR 已合并。版本号遵循 [SemVer](https://semver.org/)：MAJOR 是不兼容变更、MINOR 是新功能、PATCH 是修复。

---

## 8. 致谢

任何形式的贡献（issue、PR、文档、错误报告、配方分享）都被赞赏。首次贡献者会在下一个 release notes 的 `Thanks to` 段提名（除非你选择匿名）。