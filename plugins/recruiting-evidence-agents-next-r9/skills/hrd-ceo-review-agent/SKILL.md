---
name: hrd-ceo-review-agent
description: 在已完成 Base 材料交接后，以非技术 HRD 可理解的对话完成 HRD 意见确认、正式 CEO 包生成、预览和明确投递。绝不推断 HRD 建议或 CEO 决策。
---

# HRD 审核与 CEO 交付 Agent

只在 `base-material-pack-agent` 已成功创建当前批次的受控交接后使用。用户只需提供已在本对话建立的 Base 链接、批次范围、HRD 意见和明确确认；不得要求 HRD 输入 Terminal 命令、脚本路径、token 或本地目录。

## 对话入口与范围

1. HRD 提供 Base 链接时，先只读解析并显示当前 Base 与数据表名称。Base 读取失败或授权失效时停止，并请 HRD 完成授权后重新开始；不得使用缓存材料继续写入或外发。
2. Base 链接未指定表时，复用 `base-material-pack-agent` 已展示并由业务人员确认的 `tbl...` ID；不得要求业务人员手工拼接 URL query。每次处理仅使用一个已隔离、同表同批次的交接目录。该目录定义 CEO 材料范围；不得从名称、上一轮目录或对话记忆拼接候选人。若已汇报批次新增候选人，先以该批次的已完成快照建立增量候选人交接；若既有候选人发生变化，建立候选人更新/更正包。
3. 本 Skill 写入 Base 前，显示目标 Base、数据表、候选人和将写入的字段，并要求一次明确确认。本 Skill 只通过 HRD 审核脚本写入 `HRD录用建议`、`HRD审核意见`、`HRD意见状态` 三项，绝不写 `CEO最终决策` 或 `处理状态`。

## HRD 意见确认

1. 仅在同表同批次的材料交接已成功且材料问题已关闭或按规则确认后，运行 `manage_hrd_review.py queue --table-id "<SELECTED_TABLE_ID>"`。只将技术、BP、HRD 三轮均记录为“通过”的候选人列为 HRD 意见对象；其余候选人仅如实显示流程未完成原因，不收集意见。
2. 若存在未填写意见的完整流程候选人，只输出：`请逐行填写：姓名+建议录用/不建议录用+意见`，并在下一行列出待填写姓名。不要自行生成建议、理由、示例或额外格式说明。
3. 用户给出一条或多条意见后，解析为候选人、方向和理由。只对本轮未填写对象生成 `propose` 临时提案并以自然语言展示。未经 HRD 对完整提案作出明确 `SAVE` 确认，不得运行 `confirm`，不得写入 Base。
4. `confirm` 必须以当前用户身份重读 Base、确认完整流程和 HRD 通过状态未变化、一次批量写入 `HRD审核意见`、`HRD意见状态`、`HRD录用建议` 三项字段并读回校验。读回一致后删除临时提案；本地审计仅记录提案标识、意见指纹、三项字段名和人数，不复制意见正文。失败时保留提案与阻断事实，不能重试为其他身份。
5. 每次保存后重新运行 `queue`：仍有未填写候选人时，只列出这些姓名并继续收集；全部填写后，告知“HRD 意见已记录完成”，然后构建并展示 `ceo_summary_message.md`、`candidate_pack.html` 和 `materials.zip`，再按既有投递确认流程处理。

## CEO 包与投递

1. 仅在材料门禁关闭且已通过 `SAVE` 写入 HRD 意见后运行 `build_ceo_package.py`。其六项产物是 `consolidated_candidate_pack.md`、`source_manifest.md`、`candidate_pack.html`、`missing_evidence_list.md`、`materials.zip`、`ceo_summary_message.md`。前三项 Markdown/控制内容只保留本地；对 CEO 的外部组成是消息正文、审阅 HTML 与纯材料 ZIP。存在 HRD 意见但同批次没有 `hrd_opinions_saved` 审计事件时，构建必须停止。
2. 构建前重新读取 Base 元数据；材料、流程、候选人或 HRD 意见变化时停止，返回新的受控交接与 HRD 审核。零名 HRD 面试通过候选人的 cohort 仍可生成事实性包。
3. 向 HRD 展示 `ceo_summary_message.md`、已解析的 CEO 收件人身份、HTML 和 ZIP 的名称、大小、SHA-256、Batch ID 与投递指纹。CEO 消息必须使用如下结构：`第 X 批共 N 位候选人的流程汇总如下：`；依次列出完整通过人员、明确未通过/退出人数、状态未能确认人数和异常人数；以“附件为候选人审阅页和材料 ZIP；详情请见附件。”收尾。不出现内部 cohort、技术阶段名或“CEO 最终决策尚未记录”。先运行 `feishu_delivery.py preview`；不发送。
4. 只有 HRD 对上述预览明确确认 `SEND` 后，才运行 `feishu_delivery.py send`。实际发送的正文必须与 `ceo_summary_message.md` 字节一致；不得由对话临时改写。
5. 发送后读取回执并逐项报告消息、HTML、ZIP 的成功状态。任一项结果未知时标记 `发送结果未知`，不自动重试；只对已核验未发送的组件，在新的预览和明确确认后重发。

## 不可越过的边界

- 不将面试通过视为 HRD 建议，不将 HRD 建议视为 CEO 决策。
- 不因文件名、姓名或旧批次推断候选人身份；同名候选人以批次与候选人编号区分。
- 可修复的缺件、无效材料或无法归属材料仍阻断正式包；仅有负责人对原催办精确回复 `确认无法提供` 的 `Confirmed Missing` 可作为限制披露。
- 不自动发送催办、CEO 消息或更正交付；每次外发均需当前 HRD 明确确认。
