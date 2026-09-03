# Recruiting Evidence Agents

面向招聘材料处理的 Codex Plugin 项目。它提供只读 Feishu Base 材料交接、平面材料整理、证据约束的 Candidate Pack、HRD 意见确认，以及 CEO 交付预览与显式发送能力。

项目不包含真实候选人材料、飞书账号、凭据、token 或收件人信息。材料来源、HRD 建议和 `CEO最终决策` 保持独立边界；插件不会推断或自动写入 CEO 决策。

## 当前内容

- `plugins/recruiting-evidence-agents-next-r9/`：当前开发源码，manifest 版本为 `0.5.0-dev-r9`。
- `release_baselines/v0.5.0_20260901_submitted/`：已提交的 `v0.5.0` 发布基线及其校验文件，仅作可追溯的历史基线保留。
- `.agents/plugins/marketplace.json`：本地 Marketplace 配置。
- `INSTALL_AND_USAGE.md`：历史发布包的安装与使用说明。
- `RELEASE_READINESS_REVIEW.md`：发布审查结论和未关闭事项。
- `release_evidence/`：发布验收记录模板。

## 开发与验证

在开发源码目录中执行最相关的离线测试：

```bash
cd plugins/recruiting-evidence-agents-next-r9
python3 -B -m unittest discover -s tests -p 'test_*.py' -v
```

这些测试使用受控本地 fixture，不访问 Feishu，也不会发送消息或写入业务数据。

## 发布边界

`r9` 是开发版本，不能因仓库存在或离线测试通过而宣称已完成生产发布。安装、端到端验收、HRD `SAVE` 和 CEO 外发仍需遵循既有的显式确认与审查流程。具体约束见 `INSTALL_AND_USAGE.md`、`RELEASE_READINESS_REVIEW.md` 和插件内的各 `SKILL.md`。
