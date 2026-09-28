<!--
感谢你的 PR！请勾选下面的清单，并在正文填「改动说明 / 截图 / 测试步骤」。
PR 标题遵循 Conventional Commits（feat: / fix: / docs: / refactor: / test: / ci:）。
-->

## 改了什么

<!-- 简述这次改动解决了什么问题 -->

## 关联 Issue

<!-- 例如：fixes #12, refs #34 -->

## 改动类型

- [ ] 修复 bug（`fix:`）
- [ ] 新功能（`feat:`）
- [ ] 重构（`refactor:`）
- [ ] 文档（`docs:`）
- [ ] 测试（`test:`）
- [ ] CI / 构建（`ci:`）

## 自检清单

- [ ] 已跑 `python3 -m unittest discover -s tests -v` 全绿
- [ ] 已跑 `python3 -m py_compile server.py`
- [ ] 新功能 / 修 bug 都加了测试
- [ ] 未引入第三方依赖（运行时仍是 Python 标准库）
- [ ] UI 改动附了截图 / 录屏（PR 正文里放 `.agent_tmp/` 草稿也行）
- [ ] README / docs/ 同步更新（如适用）
- [ ] commit 消息符合 Conventional Commits

## 风险评估

<!-- 对用户的影响：是否需要重启？是否需要数据迁移？是否有破坏性变更？ -->

## 截图 / GIF

<!-- UI 改动必填 -->

## 补充说明

<!-- 给 reviewer 看的任何细节，例如「这个函数是从 xxx 抽出来的」「默认值改了是因为 xxx」 -->