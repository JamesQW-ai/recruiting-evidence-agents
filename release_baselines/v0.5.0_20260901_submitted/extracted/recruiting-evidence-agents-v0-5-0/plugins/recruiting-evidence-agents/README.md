# Recruiting Evidence Agents

External release revision: `v0.4.3-r12`. Codex internal revision: see `.codex-plugin/plugin.json`.

This plugin is independent of the historical `v0.1.4` baseline. It accepts either a flat simulated Feishu material repository or a read-only Feishu Base recruiting-process table, structures the sources into a portable material handoff, then produces an evidence-bound Candidate Pack.

## Feishu Base intake

`base-material-pack-agent` is the production-like intake. It resolves an explicit Base URL, reads the selected table as the authorized user, downloads each attachment into a local snapshot, and generates the same controlled register and `materials.zip` contract consumed by the existing Candidate Pack Agent. It never writes to the Base.

The Base scenario requires the approved user scopes `base:block:read`, `base:field:read`, `base:record:read`, `docs:document.media:download`, and `offline_access`. The local `lark-cli` profile owns credentials; the Plugin stores no App Secret, user token, Base token, file token, or account data in final outputs.

```bash
python3 scripts/build_base_materials.py \
  --base-url "<BASE_URL>" \
  [--table-id "<SELECTED_TABLE_ID>"] \
  [--batch "<EXACT_BATCH_VALUE>"] \
  --workspace-root "<SELECTED_WORKSPACE_ROOT>" \
  --snapshot-dir "<EMPTY_LOCAL_SNAPSHOT_DIR>" \
  --output-dir "<EMPTY_OUTPUT_DIR>" \
  --lark-profile "<OPTIONAL_LOCAL_PROFILE>"
python3 scripts/build_candidate_pack.py \
  --output-dir "<EMPTY_OUTPUT_DIR>"
```

同一 Base 有多张数据表时，先由对话列出表并由用户明确选择 `tbl...` ID，再传入 `--table-id`。同一表有多个 `批次` 时，必须传入精确 `--batch`；每个表/批次范围建立独立快照和交接，不会混合候选人或复用另一范围的附件。

业务流程先使用两个只读发现命令，不生成本地交接：

```bash
python3 scripts/build_base_materials.py --base-url "<BASE_URL>" --list-tables
python3 scripts/build_base_materials.py --base-url "<BASE_URL>" --table-id "<SELECTED_TABLE_ID>" --list-batches
```

无论一个或多个批次，业务人员都必须先确认一个精确值，再以 `--batch` 执行材料交接；单批次只是不需要消歧，不代表可以自动开始下载。

本地快照是回放控制件，保留 Base 记录与字段坐标、附件令牌和源哈希，不进入 `materials.zip` 或最终交付物。`materials.zip` 中每位候选人的材料直接放在 `input/<候选人姓名>/`，不再保留来源序号子目录；同一候选人的同名副本以稳定 `__source-0002` 后缀避免覆盖，原始文件名和源字节仍在快照中保留。共享 `技术题目` 仅在所有 Base 副本字节一致时保留一份。

模拟 Base 的验收契约见 [`docs/BASE_INTAKE_ACCEPTANCE_MATRIX.md`](docs/BASE_INTAKE_ACCEPTANCE_MATRIX.md)。其中固定了每个既有候选人场景的预期打包路径和仅基于证据的结论。

Candidate attribution uses readable file content before filename. A file uniquely identified as 李四 but uploaded in 张三's Base field is packaged under 李四 with its original filename and bytes unchanged; this is a traceability notice only and never creates a follow-up, cleanup, or confirmation request. `Invalid Material` is active only when its Base field has no usable material for that component; `Missing Material` and `Unmapped Material` remain active gates. When a usable copy exists alongside an invalid file, report `Invalid Extra Material` with a delete-only action rather than requesting a supplement. `Duplicate Submission` is reported only when source bytes and SHA-256 are identical; every duplicate copy is retained for traceability and never creates a deletion request or follow-up. `Multiple Material Versions` remains separately reported for detailed human review; the plugin neither asks for a supplement nor chooses a preferred version.

The original local `input/` path remains an offline regression fixture and is not overwritten by a Base run.

## 补件催办与增量重建

`follow-up-incremental-pack-agent` 以已完成的 Base 只读快照为输入，在独立本地控制目录中生成缺件催办草稿、记录逐项人工确认，并对新的、可唯一映射的 Base 附件给出重建范围提示。它不修改 Base、不发送消息、不把状态写入 `materials.zip` 或正式五文件。完整的状态转换、增量事件键与重建门禁见 [`docs/FOLLOW_UP_INCREMENTAL_CONTRACT.md`](docs/FOLLOW_UP_INCREMENTAL_CONTRACT.md)。

该 Agent 将“负责人回复”与“附件核验”严格分开：补件必须直接上传到指定 Base 字段，私聊附件不作为材料来源。`已上传` 仅记录为 `Acknowledged`；只有后续 Base 快照中出现可验证的新附件，才能转为 `Verified`。负责人仅在原催办消息下精确回复“确认无法提供”时可转为 `Confirmed Missing`，并保留消息关联证据；该状态不触发 Base 刷新。发送完成后，`watch_follow_up_replies.py` 以同一用户身份对对应私聊进行有限次轮询，不使用无法稳定覆盖用户私聊的 bot 事件；它只在“已上传”回复后创建新的 Base 只读快照。监听器本身不外发消息、不写入 Base，也不生成正式五文件；实际发送仍必须使用用户身份、逐项批准并登记真实发送回执。

## Material intake

`material-pack-agent` is the first agent. It reads the authorized `input/` directory without changing it, then:

- reads filenames, readable document content, and factual submission-register rows;
- gives a uniquely identified document or audio content priority over a filename; a register row is submission provenance and cannot alone establish candidate identity;
- uses `scripts/transcribe_audio.py` for each supported audio file when an explicitly configured local ASR backend is available;
- creates only `input/<candidate>/<original-filename>` paths plus one common technical prompt directly under `input/` in the derived `materials.zip`;
- excludes the submission register, explicitly unrelated files, and `Unmapped` or `Ambiguous` files from the ZIP; and
- keeps mapping and transcription receipts transient for the run rather than shipping them with candidate material; and
- removes every temporary audio normalization and transcription file before completion.

The Plugin never treats an absent or failed transcription as a workflow failure. It records `Audio Content Unverified`; if no other source can uniquely identify the material, the material remains `Unmapped`.

For a Base run, first select a work directory. The package must be its direct child and use a directory named `招聘材料包_第N批_YYYYMMDD_HHmm`, for example `招聘材料包_第1批_YYYYMMDD_HHmm`; the Base adapter rejects other output names or an unbound work directory. The package root receives `materials.zip`, temporary controlled handoff files, and a persistent `过程记录/` directory. The register and run control are same-directory handoff controls for the second agent: they are never put into `materials.zip`, and the second agent deletes the temporary root copies only after successful factual reconciliation and final-output validation. `过程记录/` remains and contains only the fixed, token-free diagnostic files: `运行摘要.json`, `材料映射.jsonl`, `材料核验.json`, `运行日志.jsonl`, and `校验和.sha256`. `run_control.json` records SHA-256 and byte size for the ZIP and register, the source fingerprint, and the current plugin-manifest SHA-256. A Base intake adds the SHA-256 and byte size of `base_intake_observations.json` after it is written. Candidate Pack recomputes every bound value before reading business data; any mismatch, missing binding, or plugin-version mismatch is a hard stop requiring a new build from authorized sources.

Run the second agent with `scripts/build_candidate_pack.py --output-dir <root>/output1` only for a flat offline regression handoff. A Base handoff whose material gate has closed is deliberately blocked before formal output so it cannot create a CEO package without a saved HRD opinion.

## HRD 审核闭环

非技术 HRD 应使用 `$hrd-ceo-review-agent` 在对话中完成本节工作，不需要提供命令或本地路径。该 Skill 先只读确认 Base 与表，再展示 HRD 意见提案；只有明确 `SAVE` 后才写入允许的三项 HRD 字段。下列命令仅用于本地技术验收。Base 必须已有 `HRD审核意见`（文本）、`HRD意见状态`（文本或单选）和 `HRD录用建议`（文本或单选）三个可写字段；插件不创建、猜测或写入 `CEO最终决策`。

HRD 可以在对话中自然表达一个或多个意见。对话必须先将每条意见提取为候选人、`建议录用` 或 `不建议录用`、以及至少一句简短理由，并显示提案；候选人、方向或理由不明确时只追问缺失部分，不保存。随后在同一输出目录创建临时提案，HRD 审阅后才可执行一次显式 `SAVE` 确认。确认时，插件验证当前用户 `open_id` 未改变、重新读取 Base 验证候选人仍为 HRD 面试通过、写入三项字段并回读核验；成功后删除临时提案，仅在 `operation_log.jsonl` 留下不含意见正文的审计事件。

```bash
python3 scripts/manage_hrd_review.py queue \
  --base-url "<BASE_URL>" --table-id "<SELECTED_TABLE_ID>" --output-dir "<CURRENT_OUTPUT_DIR>" \
  --lark-profile "<OPTIONAL_LOCAL_PROFILE>"
python3 scripts/manage_hrd_review.py propose \
  --base-url "<BASE_URL>" --table-id "<SELECTED_TABLE_ID>" --output-dir "<CURRENT_OUTPUT_DIR>" \
  --opinions-json '[{"candidate":"张三","recommendation":"建议录用","rationale":"沟通清晰且岗位匹配。"}]' \
  --lark-profile "<OPTIONAL_LOCAL_PROFILE>"
python3 scripts/manage_hrd_review.py confirm \
  --output-dir "<CURRENT_OUTPUT_DIR>" --proposal-id "<DISPLAYED_PROPOSAL_ID>" \
  --confirm SAVE --lark-profile "<OPTIONAL_LOCAL_PROFILE>"
```

HRD 意见已确认保存后，使用 `scripts/build_ceo_package.py --base-url "<BASE_URL>" --table-id "<SELECTED_TABLE_ID>" --output-dir "<CURRENT_OUTPUT_DIR>" [--lark-profile "<LOCAL_PROFILE>"]` 生成正式 `HRD 候选人意见包`。构建器重新读取当前 Base，并只比较材料与流程元数据（不把之后保存的 HRD 意见或任何 CEO 决定纳入材料摘要）；摘要变化时阻断，要求重新交接和重新审核。当前交接目录定义完整 CEO cohort：每位 `HRD面试流程状态=通过` 的候选人必须已有完整 HRD 意见，零位 HRD 通过候选人仍可生成事实性包。若需要子集，先建立该子集的独立受控交接，不能用 `candidates-json` 从既有材料 ZIP 静默排除候选人。成功后保留六项正式产物和 `operation_log.jsonl`：`ceo_summary_message.md` 是投递正文，且投递时必须原文一致。

The bundled wrapper supports an offline `whisper.cpp` backend. It requires a local `whisper-cli` executable and a local model file; it never downloads a model, calls a cloud service, or retains a machine-generated transcript. See `skills/material-pack-agent/SKILL.md` for the required invocation and receipt fields.

## 资源边界

每次来源批次上限为 2 GB；单个音频上限为 256 MB 且最长 60 分钟。Material Pack 在读取内容或下载 Base 附件前执行大小与磁盘预检，输出目录和 Base 快照目录必须各自保有“来源体积加 512 MB”的可用空间。无法读取 Base 附件大小、无法验证音频时长或任一上限超出时均阻断本次运行。

Candidate Pack 在处理 `materials.zip` 前限制最多 4,096 个非目录成员、最多 2 GB 展开体积、每个成员最多 100:1 压缩比，并拒绝重复路径、非规范路径、加密成员和符号链接成员；随后仅以 1 MB 流式读取成员。它不会解压、重压缩、拆分或改写超限材料。飞书投递仍严格限制 `candidate_pack.html` 与 `materials.zip` 各自不超过 30 MB；超限时停止并要求已批准的替代投递渠道。

## Compatibility

This Plugin's `materials.zip` uses the content-first intake layout and must be processed by its bundled `candidate-pack-agent`. Do not substitute another plugin's material contract.

## macOS handoff

For a different Mac, distribute the release ZIP together with the authorized
`materials.zip`. The recipient should follow
[`docs/MACOS_HANDOFF_GUIDE.md`](docs/MACOS_HANDOFF_GUIDE.md). It contains the
local Marketplace installation steps, first-time Feishu authorization boundary,
environment checks, troubleshooting, and copyable Codex prompt templates.

Do not distribute or copy a `lark-cli` profile, App Secret, user token, or
recipient configuration. Each sender authorizes their own Feishu account on
their own Mac.

## Batch control

Batch ID, stable candidate record ID, material fingerprint, immutable handoff hashes, and `Processed` state are bound in the same output directory's `run_control.json`, then copied into `consolidated_candidate_pack.md` after a successful run. They are never inferred from session memory, prior output folders, or candidate display names. A different batch, an appended candidate, a same-name candidate, or any altered handoff artifact requires a new isolated run directory; mismatched controls are blocked.

- The initial run creates the first batch control section inside `consolidated_candidate_pack.md`.
- A same-batch append reads the preceding required delivery, selects only named new candidates or named candidates whose fingerprint changed, and writes the updated control section only after success.
- An unchanged candidate is reused only when the preceding delivery records the same fingerprint and a successful output; it is excluded from the append ZIP.
- A changed fingerprint requires an isolated reprocess for that candidate; unrelated candidates are not repackaged.
- The review HTML displays the natural-language batch label and aggregate batch facts only. Machine-readable batch and candidate IDs remain in the internal Markdown control layer.

## Review UI

Base 交接的正式 `candidate_pack.html` 为 `HRD 候选人意见包`：批次概览、流程漏斗、互斥的完整通过/明确未通过或退出/状态未能确认计数、按建议分组的已保存 HRD 意见、`Confirmed Missing` 限制和重复提交历史。它不含机器可读编号、`Source ID`、Base 链接或 CEO 决定。离线回归仍使用原 `assets/candidate_pack_ui_template.html`；Base CEO 包使用 `assets/hrd_candidate_pack_ui_template.html`。正式输出保持为六项产物和 `operation_log.jsonl`。

## Feishu Delivery

飞书投递在人工审阅后才可进行，并始终以授权用户身份发送，不使用应用机器人。状态为“可提供”后，才可发送 `ceo_summary_message.md` 的原文、`candidate_pack.html` 和 `materials.zip`；其余三个 Markdown 文件和 `operation_log.jsonl` 仅保留在本地。`scripts/feishu_delivery.py preview` 会验证当前用户身份、明确收件人解析、六项正式文件、CEO 摘要状态和审核页面结构，再将已验证发送者、已解析收件人、批次、正文摘要和两个附件摘要共同纳入 `delivery_fingerprint`；预览不发送任何消息或文件。传入与 `ceo_summary_message.md` 不同的 `--summary` 会阻断，避免正文漂移。中断后的 `delivery_receipt.json` 只记录同一输出目录内的已发送组件与绑定摘要，后续只会续传未完成组件；它与本次确认不匹配时停止。

The delivery script calls local `lark-cli` with `--as user`. The Plugin never embeds an App ID, App Secret, profile, tenant, token, or credential. Every computer uses its own local CLI configuration and every sender authorizes their own Feishu user identity. The CLI owns credential storage and renewal; the only local delivery state is the output-bound `delivery_receipt.json` required for truthful partial-send recovery.

## 发布验收

发布前运行 `python3 -B -m unittest discover -s tests -v`。其中 `tests/test_release_contract.py` 使用仅含合成文本的模拟 Base：它从不可变交接、材料门禁和已保存 HRD 意见构建正式 CEO 包，再验证静态投递快照绑定了已验证发送者与已解析收件人。该 fixture 不读取真实候选人材料，不访问 Base，也不上传或发送飞书消息。

在每台实际发送设备上，先运行 `scripts/feishu_delivery.py preflight` 完成只读身份与授权范围预演；只有人工审阅 `preview` 返回的发送者、收件人、Batch ID 和 `delivery_fingerprint` 后，才可另行执行需要精确 `SEND` 确认的投递。发布验收还应在桌面浏览器中检查生成的 `candidate_pack.html`，确认页面宽度无横向溢出、四项指标和四步漏斗完整可见、意见卡片不重叠。

`CEO最终决策` 写入仍是明确延后项；本插件不会以任何默认值、对话推断或发布验收结果代替这项工作。

### New computer or new Feishu account

Before handling any material, the user or their administrator must install the official CLI, create or select a local profile bound to an organization-authorized Feishu self-built application, and complete that sender's user authorization. The application is a prerequisite for the official API, but delivered messages display as the authorized user, not the application bot. Do not copy a profile, secret, or token between computers.

```bash
npx @larksuite/cli@latest install
lark-cli profile add --name <local-profile> --app-id <app-id> --app-secret-stdin --brand feishu
lark-cli --profile <local-profile> auth login --scope "im:message im:message.send_as_user im:resource offline_access"
```

For email recipients, include `contact:user:search` in the user authorization. The Feishu application must have the matching approved scopes and be available to the sender in that tenant. A cross-tenant account or an account without this authorization is correctly blocked rather than falling back to a bot or another person's session.

Run the following before each first delivery on a computer or after switching accounts. It is a networked authentication check only: it does not resolve the recipient, read an output folder, upload, or send.

```bash
python3 scripts/feishu_delivery.py preflight --recipient email:name@example.com --lark-profile <local-profile>
```

Omit `--lark-profile` only when the intended account is the CLI default profile. The script never calls `lark-cli profile use`, so it cannot silently change the computer's selected account. Use the same optional argument with `preview` and `send` to bind one reviewed delivery to one local sender profile.
