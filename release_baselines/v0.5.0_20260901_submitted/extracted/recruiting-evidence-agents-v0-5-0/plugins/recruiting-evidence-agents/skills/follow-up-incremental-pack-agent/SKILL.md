---
name: follow-up-incremental-pack-agent
description: 从 Base 只读快照生成缺件催办草稿，并依据新附件的内容映射维护本地增量控制状态。用于补件催办和重建判定；不用于替代人工确认或直接作出招聘决定。
---

# 补件催办与增量重建 Agent

只在已有一次 `base-material-pack-agent` 的本地快照后使用。该 Skill 不修改来源文件、不把控制状态写进正式五文件交付目录，也不将流程状态推断为 HRD 建议或 CEO 决定。收到“生成催办预览”后，系统会将本批 `处理状态` 作为内部流程标记同步写入并立即读回验证；该内部写入不单独向业务人员请求确认。

## 本地控制边界

为每次批次选择一个独立的 `CONTROL_DIR`，且它必须不同于 `SNAPSHOT_DIR` 与正式交付目录。三者均属于已选工作目录：材料包直接位于工作目录根下，快照和控制状态位于工作目录的隐藏控制目录。批次、`candidate_id` 和 Base `record_id` 必须在控制状态中同时保存；同名候选人不得仅按姓名合并。一个 session 追加新批次或同一批次新增候选人时，必须新建对应的 `SNAPSHOT_DIR`、`CONTROL_DIR` 和空输出目录，不能沿用上一个批次的上下文。此目录可保存：

- `processed_index.jsonl`：已见附件事件的不可逆索引；
- `follow_up_state.json`：补件项状态及人工确认记录；
- `follow_up_draft.json`：本轮待核验的催办草稿；
- Base 主表存在 `简历负责人`、`技术作业负责人`、`BP面试负责人`、`技术面试负责人`、`HRD面试负责人` 字段时，只读快照会按对应材料字段保存负责人。只有可验证的单人 `open_id` 才能为同一候选人、同一材料的补件草稿预填收件人；该信息不进入 `materials.zip`、登记表或正式五文件。文本占位符（例如 `agents`）会以 `Placeholder` 保留，不会成为发送对象。
- 可选 `contact_map.json`：负责人映射，格式为 `schema_version: 1` 和 `owners` 数组。每项包含 `candidate_id`、`material_field`、`recipient`。该文件按材料字段覆盖 Base 中的负责人；没有可验证 `open_id` 的材料仍需该映射或在当前对话提供收件人。

不得把 Base URL、Base token、文件 token、访问令牌或联系人映射复制到 `materials.zip`、正式五文件或源码材料目录。

## 生成与确认

1. 先对最新只读快照运行：

```bash
python3 scripts/manage_follow_up.py plan \
  --snapshot-dir "<SNAPSHOT_DIR>" \
  --control-dir "<CONTROL_DIR>" \
  --base-url "<BASE_URL>" \
  --table-id "<TABLE_ID>" \
  [--lark-profile "<LOCAL_PROFILE>"]
```

收到“生成催办预览”或等价指令时，必须运行以上完整命令；它会先按当前快照同步本批全部候选人的 `处理状态` 并读回验证，成功后才生成预览。`处理状态` 是内部流程标记：不得展示拟写入确认、不得要求用户为此确认。不得先生成预览、发送消息或只更新消息相关候选人。

2. 阅读 `CONTROL_DIR/follow_up_draft.json`。预览分为“人工归属核查”“补交材料”和“核查与清理”：无法确认归属的材料必须优先展示并生成“人工归属核查”草稿，按该材料阶段的已验证负责人路由；没有负责人时保留草稿并请业务人员指定收件人。后者仅涵盖无法打开但已有可用材料的文件和多个不同版本。`self_service_actions` 是当前 HRD 需要直接在多维表补齐的 HRD 面试材料，不生成消息，也不读取 `HRD面试负责人`。业务回复展示顺序必须为：全部外发预览项在前，`self_service_actions` 单独在最后列出。重复文件仅在字节与 SHA-256 完全相同时作为追溯信息保留，不生成预览、不要求删除。材料实际内容属于其他候选人时，保留原始字节与文件名并按内容归入对应候选人材料目录，不生成预览、不要求清理或确认。对业务人员展示候选人、材料环节、需要动作和消息草稿；不展示 `FU-`/`CL-` 编号、候选人编号、open_id、Base 路径或技术分类。`recipient: Not Provided` 时，请用户在当前对话提供本次收件人邮箱，例如 `email:name@example.com`；不得按姓名猜测联系人，也不要求把邮箱写入插件配置或 Base。
3. 预览已列明每条消息的收件人和正文后，用户只需一次明确确认：`确认发送并监听10分钟`。这同时批准当前所有可发送预览项、发送消息并启动监听；不得再逐项索取第二次业务确认，也不得要求确认 `处理状态` 的写入。HRD 自助补齐事项不发送消息，仍在预览末尾单独列出。缺少收件人的事项只报告未发送，其他已有明确收件人的事项继续执行。

收到该确认后，必须只运行以下受控入口，不能拆分为 `approve`、`mark-processing`、`record-sent` 与 `watch`：

```bash
python3 scripts/manage_follow_up.py dispatch-and-watch \
  --control-dir "<CONTROL_DIR>" \
  --base-url "<BASE_URL>" \
  --table-id "<TABLE_ID>" \
  --snapshot-root "<LOCAL_SNAPSHOT_ROOT>" \
  --handoff-root "<LOCAL_HANDOFF_ROOT>" \
  --lark-profile "<LOCAL_PROFILE>" \
  --confirm SEND_AND_WATCH \
  --timeout-seconds 600 \
  --interval-seconds 15
```

该入口在发送前重新同步并读回本批所有候选人的 `处理状态`，但不向用户展示“拟写入确认”。状态按完整核验结果一次性归并：无法确认归属为 `阻塞`，待补交（包括 HRD 自助补齐）为 `待补全`，核查与清理事项为 `处理中`，其他候选人为 `已处理`。同一候选人有多项时，`阻塞` 优先于 `待补全`，优先于 `处理中`。随后它为全部消息先行预检，再以当前用户身份发送、记录回执并启动一个受控的独立本地监听进程。收到 `reply_watch_detached` 审计和进程号后才可向用户报告“监听已启动”；该进程不受当前对话命令超时影响。监听器随后写入 `reply_watch_started`，每 15 秒写入 `reply_watch_polled`，结束时写入 `reply_watch_finished` 或 `reply_watch_failed`。发送成功后不得把“已发送”误报为“监听完成”或要求用户另行启动监听。任何预检失败均不发送；发送中出现部分失败时立即停止并如实报告已发送和未发送项，不自动重试。

`dispatch-and-watch` 只启动一个本地静默守候进程，不创建 Codex heartbeat 或周期性对话回报。监听完成后将结果写入 `CONTROL_DIR/follow_up_completion.json`：全部已发送事项收到合格回复时，`next_action` 为 `collect_hrd_opinions`；十分钟超时仍有未回复事项时，先处理已回复项并对每个未回复事项自动发送一次可审计的再次催办，`next_action` 为 `await_reminded_replies`。每项超时提醒仅有一次新的幂等消息 ID，不无限循环发送。

读取 `follow_up_completion.json` 后，先读取监听器创建的新 Base 快照与本轮核验结果。`next_action=await_reminded_replies` 时只展示已处理项和已再次催办项；不得构建 Candidate Pack。`next_action=collect_hrd_opinions` 时，直接运行 `manage_hrd_review.py queue`，不得先生成 Candidate Pack、材料包或 CEO 内容。若存在待填写的完整流程候选人，仅输出一行：`请逐行填写：姓名+建议录用/不建议录用+意见`，并在下一行列出待填写姓名；不得额外解释格式或拆成多行示例。

当同一 output 已包含当前快照的 `base_intake_observations.json`，且存在已核验的 `Confirmed Missing` 项时，先导出唯一允许给 Candidate Pack 读取的临时交接文件：

```bash
python3 scripts/manage_follow_up.py export-resolution \
  --control-dir "<CONTROL_DIR>" \
  --output-dir "<CURRENT_OUTPUT_DIR>"
```

该命令只读取控制目录与该 output 中的 Base 观测，验证两者快照时间一致后写入 `follow_up_resolution.json`。Candidate Pack 不接收 `CONTROL_DIR` 参数，也不读取 output 之外的文件夹；成功或阻断后都会删除该临时交接文件。

## 回复与重建门禁

可将收到的回复如实记录：

```bash
python3 scripts/manage_follow_up.py record-reply \
  --control-dir "<CONTROL_DIR>" \
  --item-id "<FU_ID>" \
  --reply-text "<REPLY_TEXT>"
```

补交消息要求负责人将材料直接补充到指定 Base 字段，并在原消息下回复“已上传”；若明确无法提供，可在同一原消息下说明“无法提供”。核查与清理消息要求负责人在 Base 中完成核查或删除无效额外文件后回复“已处理”；系统不会自动删除、移动或选择保留版本。重复文件不生成催办，也不要求删除；错放到其他候选人的材料按实际内容归入对应候选人材料目录，不生成催办或清理项。聊天附件不属于补件入口，不下载或解析。任何补交/核查回复都只转为 `Acknowledged`，不能单独关闭事项；必须再次执行新的只读 Base 快照，且相应缺件已补齐或清理发现已消失，`plan` 才会标记为 `Verified`。相同的附件事件不会再次计入 `new_attachment_events`。只有补交事项可由同一负责人在原催办消息下回复包含“无法提供”的明确说明进入 `Confirmed Missing`；该路径不刷新 Base，不能由手工录入、未关联消息或历史重复项替代。

对实际用户私聊回复，使用同一用户身份进行 P2P 轮询，不使用 bot 事件监听。当前飞书消息事件仅支持 bot 身份，不能监听以本人身份发出的私聊。单次检查可使用：

```bash
python3 scripts/watch_follow_up_replies.py poll-and-refresh \
  --control-dir "<CONTROL_DIR>" \
  --lark-profile "<LOCAL_PROFILE>" \
  --base-url "<BASE_URL>" \
  --snapshot-root "<LOCAL_SNAPSHOT_ROOT>" \
  --handoff-root "<LOCAL_HANDOFF_ROOT>"
```

命令只在发现新回复时创建新的 Base 只读快照。需要持续等待回复时，运行有界监听：

```bash
python3 scripts/watch_follow_up_replies.py watch \
  --control-dir "<CONTROL_DIR>" \
  --lark-profile "<LOCAL_PROFILE>" \
  --base-url "<BASE_URL>" \
  --snapshot-root "<LOCAL_SNAPSHOT_ROOT>" \
  --handoff-root "<LOCAL_HANDOFF_ROOT>" \
  --timeout-seconds 600 \
  --interval-seconds 15
```

收到一次“确认发送并监听10分钟”后，受控监听进程会立即开始每 15 秒一次的监听：补交事项识别“已上传”“已补交”或在原消息下包含“无法提供”的说明，核查与清理事项识别“已处理”或“已删除”。业务人员应在对应催办消息下回复，以确保回复可归属；同时兼容飞书的 `reply_to`、`parent_id` 与 `root_id` 关联字段。若全部已发送事项都收到合格回复，立即停止监听、统一创建一次新的 Base 只读快照，沿用原已选表和批次、以业务命名目录保存，并重新计算本批全部 `处理状态`。若少于全部事项回复，则持续至 10 分钟结束；窗口结束后，只要已收到任一合格回复，也统一创建一次新的 Base 只读快照。`Confirmed Missing` 只记录关联原催办消息的“无法提供”说明。未关联原催办消息的单独回复不会触发刷新，避免同一负责人收到多条催办时误关联。已有发送回执但监听因外部中断未启动时，使用 `resume-watch` 仅恢复这批回执的监听，绝不再次发送消息。结束时必须向使用者汇报已核验完成项、已回复但尚未通过 Base 核验项和未回复项，并等待下一步指令。读取私聊需要用户身份具有 `im:message:readonly` 与 `im:chat:read`，发送还需要 `im:message` 与 `im:message.send_as_user`。缺少权限时停止报告，不能切换为 bot 身份绕过。

`rebuild_candidate_ids` 只是新的已核验附件所影响候选人的重建提示。它不是自动交付：仍需在新的空输出目录依次运行 Base Material Pack Agent 与 Candidate Pack Agent，并由人工复核正式文件。遇到无法确认归属的材料时，保留为人工核查事项；缺少可用材料时才生成补交。无效额外文件和多版本可生成核查与清理预览，但不自动删除、移动或重建。重复文件仅作追溯，不生成催办；错放材料会按实际内容归入对应候选人材料目录，也不生成催办或清理项。Candidate Pack 仅在受控状态已验证、与当前 Base 快照绑定且存在 `Confirmed Missing` 项时放行相应缺件，其他活动问题只保留 `operation_log.jsonl`，不生成正式交付物。

## 当前自动化边界

该版本不启动无界常驻进程；使用方应以有界 `watch`、单次 `poll-and-refresh` 或调度运行。收到“确认发送并监听10分钟”后，可在该有界窗口内自动发送、监听并按上述规则读取 Base；回复本身不能单独关闭补件项。
