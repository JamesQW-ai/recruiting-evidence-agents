# Recruiting Evidence Agents v0.4.3-r18

本发布包包含招聘材料插件及独立本地 Marketplace，不含候选人材料、飞书账号、凭据、token 或收件人信息。

## 自动化验收

安装前先在本目录执行：

```bash
(
  cd plugins/recruiting-evidence-agents
  python3 -m unittest discover -s tests -p 'test_*.py' -v
)
```

该命令覆盖 Base 字段兼容性、无 `People Ops审核状态` 的流程契约、单选流程状态归一化、HRD `SAVE` 审计门禁、CEO 包与投递静态边界。它不读取或写入飞书。

多表/多批次范围选择也由测试覆盖：表必须以明确 `tbl...` ID 选择；同一表包含多个 `批次` 时必须传精确批次值，不会产生混批交接。

## 安装

1. 在本目录执行 `shasum -a 256 -c SHA256SUMS.txt`，确认发布包文件完整。
2. 执行以下命令：

   ```bash
   codex plugin marketplace add .
   codex plugin add recruiting-evidence-agents@recruiting-evidence-macos
   codex plugin list --marketplace recruiting-evidence-macos --json
   ```

3. 确认输出同时包含 `installed: true` 和 `enabled: true`，然后新开 Codex 对话。

本版提供 `$base-material-pack-agent`、`$material-pack-agent`、`$candidate-pack-agent`、`$follow-up-incremental-pack-agent` 和 `$hrd-ceo-review-agent`。HRD 可在对话中完成意见确认、CEO 包预览和显式投递；正式 CEO 包包含六项可核验产物，其中仅消息正文、审阅 HTML 和材料 ZIP 对 CEO 外发。存在 HRD 意见时，CEO 包必须读取到同批次的 `SAVE` 审计记录；直接写入 Base 但没有该记录会被阻断。ZIP 入口会在读取前拒绝路径歧义、加密和符号链接成员。完整操作边界见 `plugins/recruiting-evidence-agents/docs/MACOS_HANDOFF_GUIDE.md`。

飞书 Base 写入和消息发送不属于无人值守自动化：HRD 意见写入仍需审阅提案后明确 `SAVE`。补件流程在“生成催办预览”时自动同步内部 `处理状态`，不单独索取确认；预览正确后只需一次“确认发送并监听10分钟”，系统会使用本轮独有的发送键发送已列明的消息并启动独立的有界监听进程。监听优先使用原消息关联；飞书未返回关联字段时，对同一收件人的合格回复按发送顺序一对一处理。监听启动后，当前 Codex 任务创建单次守候心跳，等待期间不输出中间进展，并在完成或失败时自动回到原任务交付结果。监听启动、轮询和结束均保留在本地控制审计；已有发送回执的监听可恢复，且不会重复发送消息。

多表 Base 先展示可见数据表，由用户明确选择 `tbl...` ID。多批次数据表则为每个批次使用一个空的 snapshot/output 目录，传入精确 `--batch`；不得用视图筛选或显示名称猜测替代范围选择。
