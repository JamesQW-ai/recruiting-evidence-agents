---
name: candidate-pack-agent
description: Read a structured material handoff and produce an evidence-bound Candidate Pack without inference, recommendation, or decision fabrication.
---

# Candidate Pack Agent

Act as the evidence organizer. Read only the supplied `materials.zip` or an explicitly authorized extracted copy. Produce traceable facts and disclose gaps. Do not rewrite source files, score candidates, recommend hiring, or make a CEO decision.

> 保留说明：本 Skill 只从 Material Pack 的纯净来源包接力；下列边界确保候选人判断完全由插件内规则完成。

## Execution authority

Use only the direct Codex file, archive, spreadsheet, PDF, browser operations, and the bundled `scripts/build_candidate_pack.py` execution defined in this Skill. Do not run repository code, tests, scripts, or historical automation outside this Plugin. When an input does not meet a rule below, preserve `Unknown` or `Not Provided` and report the blocker; do not add a new decision rule.

## Procedure

1. 验证 ZIP 完整性并执行纯材料布局：每个条目均位于 `input/` 下；仅一份共享技术题目直接位于 `input/`；其他条目必须直接位于 `input/<candidate>/<filename>`，不得包含候选人目录下的子目录。同一候选人的同名副本可使用稳定 `__source-0002` 后缀避免覆盖；不得包含登记表、映射清单、转写回执、校验和、生成逐字稿或其他元数据。
2. Require the same run directory's `candidate_process_register.xlsx` and `run_control.json`. Before reading any business data, recompute and exactly match the run-control SHA-256 and byte-size records for `materials.zip`, the register, and (when bound) `base_intake_observations.json`, and match the bundled plugin-manifest SHA-256. A mismatch, unbound Base observation, absent required artifact, or older control schema is a hard stop requiring a fresh build from authorized sources. Before any ZIP member is read, reject more than 4,096 members, more than 2 GB expanded size, any member above 100:1 compression ratio, duplicate or noncanonical paths, encrypted members, symbolic-link members, or any invalid member; read ZIP members in 1 MB streaming blocks only. Then verify the controlled register keys exactly match `run_control.json` (batch + stable candidate ID + candidate name); never merge by chat history, display name, or a prior output folder. Treat its explicit stage/status and CEO decision fields as authoritative workflow facts; it remains factual and does not create a recommendation or decision. When the handoff came from `$base-material-pack-agent`, also read the sibling `base_intake_observations.json`; it contains only factual intake anomalies and must not change workflow status, recommendation, or decision fields. The optional sibling `follow_up_resolution.json` is the only accepted `Confirmed Missing` handoff; do not read a control directory outside this output root.
3. Treat the candidate folders as the first agent's content-first organization result. Do not expect or reconstruct `candidate_metadata.json` or an intake ledger from the source ZIP.
4. Generate stable internal `Candidate ID` and `Source ID` in Markdown outputs only. On the first run, use the register's explicit candidate order. Never write IDs back into sources or show them in `candidate_pack.html` or the CEO Summary Message.
4. Keep source facts, interviewer statements, analysis, HRD reviewer input, and CEO decision in separate fields. This workflow creates no reviewer recommendation and no CEO decision.
5. Preserve explicit states: `Unknown`, `Not Provided`, `Not Reached`, `Withdrawn`, `Verified`, and `Contradicted`. Never fill a missing field with a plausible value.
6. For audio/transcript materials, record pairing and source availability only. The pure ZIP contains no machine transcription receipt; do not infer transcription success or failure from an audio filename. `Audio Content Unverified` is not a failed gate.
8. Preserve duplicate source bytes as a `Duplicate Submission` observation. When the Base intake provides an `Invalid Material`, `Unmapped Material` or `Missing Material` observation, retain it as factual intake evidence. `Invalid Extra Material` means the same Base field already has a usable material component: carry it as a nonblocking delete-only action, never as a supplement request. Also carry `Misplaced Material`, `Duplicate Submission`, and `Multiple Material Versions` findings into the internal source manifest and aggregate HTML traceability note. A file uniquely identified by readable content as another candidate remains organized in that candidate's directory with original bytes and filename; do not recreate it as an active material blocker.
9. Generate all outputs from one factual reconciliation so counts, IDs, statuses, material-round history, and active exceptions remain consistent.

## Material and workflow contract

- Accept the shared technical-assignment prompt, the material organized by the Material Pack Agent, and the sibling controlled process register. Candidate-directory placement is the first agent's content-first identity result; explicit register fields are the authority for workflow status and decision fields. Do not infer additional identity or role facts from a filename alone.
- The only stages are `Technical Interview`, `BP Interview`, and `HRD Interview`. Do not render, count, require, infer, or create any other stage.
- The technical-assignment prompt is shared context. A candidate's technical-assignment evaluation is source-backed assessment material, not an additional interview round. Do not accept, surface, or infer a technical-submission artifact or its delivery status.
- Do not infer a source-recorded status, reviewer recommendation, or CEO decision from an organization path, a filename, or a missing file.

Do not start this Skill until `$material-pack-agent` or `$base-material-pack-agent` has completed the source handoff. If the supplied ZIP contains a register, generated transcript, mapping receipt, checksum, or any member outside the pure-material layout, or if the sibling controlled register is missing or mismatched, report the blocker and do not present outputs as Skill results.

## Required outputs

Create in one run directory:

1. `consolidated_candidate_pack.md` — machine-readable fact layer for the CEO Agent.
2. `candidate_pack.html` — concise, read-only human review report. Use `assets/candidate_pack_ui_template.html` as the frozen layout baseline. It contains only the natural-language batch label/count, executive summary, four metric cards, `流程推进` funnel, real-name list of candidates who completed the full workflow, real-name rows for `Decision Blocked` items, active material observations, and aggregate nonblocking traceability history. Do not render a candidate detail card, editable field, machine-readable identifier, `Source ID`, evidence matrix, evidence link, popup, dialog, resume preview, audio control, or link to `missing_evidence_list.md`. The exact placeholder contract is defined in `HTML review report contract` below.
3. `source_manifest.md` — Candidate-to-source mapping, Source IDs, source type, stage, date, relative path, and pairing status.
4. `missing_evidence_list.md` — missing, not submitted, not reached, withdrawn, conflicting, or unverifiable items with handling and impact.
5. `CEO Summary Message` — 仅当 `missing_evidence_list.md` 的 `CEO 摘要状态` 为“可提供”时，才在最终用户回复中直接给出完整文本。材料尚有待处理事项时，不生成、预告或提及 CEO 摘要；改为输出本轮材料核验结果，并以“是否生成催办预览？”收尾。不要将任何 CEO 消息写入独立文件、`materials.zip`、`handoff/` 或 `${WORKSPACE_ROOT}/output/`。

### 材料未完成时的业务回复

当 `base_intake_observations.json` 存在活动问题，使用下列固定顺序。只展示候选人、材料环节和业务动作；不展示内部类别名、文件 hash、ID、路径、命令、Base 或技术实现。

1. `需要补交`：逐项列出缺少的材料。HRD 面试材料的缺件明确提示当前 HRD 直接在多维表补齐，不生成催办消息。
2. `需要人工核查`：逐项列出无法确认归属的材料；只要求先确认归属和处理方式，不要求直接补传。
3. `材料说明`：逐项列出不会阻断本轮处理但已被发现的事实：
   - 重复文件：说明已保留全部副本，不要求删除或催办。
   - 可读取且可确认归属的错放材料：说明“材料包已按内容整理到正确候选人名下；多维表未作修改”。
   - 无法打开但同字段已有可用材料的文件：说明“不影响现有材料，请按业务需要决定是否保留该文件”。
   - 多个不同版本：说明“已保留全部版本，未自动选择或删除”。
4. 若没有某类事项，明确写“无”。最后只能问：`是否生成催办预览？`

催办预览只包含需要补交或需要人工核查且存在明确责任人的事项；材料说明中的重复、已整理材料和版本信息仅供业务人员知情，不生成催办。

Use the same user-provided `WORKSPACE_ROOT` as Material Pack Agent. The directory must be named `招聘材料包_<批次>_<采集时间>`; no refresh, live, random suffix, or technical version may appear. Read only the current output directory and the bundled frozen HTML template. Reject any unexpected file, directory, symbolic link, or non-regular filesystem entry other than the fixed `过程记录/` directory. Write the business files and retained diagnostics directly to the package root:

```text
招聘材料包_<批次>_<采集时间>/
  materials.zip
  consolidated_candidate_pack.md
  candidate_pack.html
  source_manifest.md
  missing_evidence_list.md
  operation_log.jsonl
  过程记录/
```

Read the controlled root `candidate_process_register.xlsx`, `run_control.json`, and (when present) `base_intake_observations.json` and `follow_up_resolution.json` only during reconciliation. After all four written artifacts and final checks succeed, delete these temporary root controls. Keep `operation_log.jsonl` and the fixed `过程记录/` directory in the final output directory; it contains only approved diagnostics and no secrets. Do not put credentials, Base/attachment tokens, contact data, authorization artifacts, packaging inventory, or operating-system metadata such as `.DS_Store` in the final output directory.

Do not create an extracted-material directory, audio copy, image preview, JavaScript file, CSS file, or arbitrary sidecar. `过程记录/` is the only allowed persistent directory and must contain exactly `运行摘要.json`、`材料映射.jsonl`、`材料核验.json`、`运行日志.jsonl`、`校验和.sha256`. The CSS and factual detail markup needed by the HTML must be self-contained in `candidate_pack.html`; it must not embed source previews or interactive evidence controls. The CEO Summary remains a direct message.

Run the bundled implementation with `python3 scripts/build_candidate_pack.py --output-dir "${OUTPUT_DIR}"` only for a flat offline regression handoff. The template path is fixed inside the Plugin and cannot be overridden. If a current Base observation has an approved `Confirmed Missing` boundary, first run `manage_follow_up.py export-resolution` to create the same-directory `follow_up_resolution.json`; the implementation validates its snapshot timestamp, original-message association, and exact reply before it applies that status in memory. If an active material problem exists, it must stop with only `operation_log.jsonl` retained and no formal delivery. When the Base material gate is closed, this legacy command stops and requires the HRD review flow. Use `manage_hrd_review.py queue`, then let the conversation extract a visible candidate/recommendation/rationale proposal, then use `propose` and one explicit `confirm --confirm SAVE`. Afterwards use `build_ceo_package.py --base-url "<BASE_URL>" --output-dir "${OUTPUT_DIR}"`; it rechecks Base material/process metadata and creates no package if no saved HRD opinion remains. Only `建议录用` and `不建议录用` are valid HRD recommendations; ambiguity or a missing rationale remains unsaved. `CEO最终决策` is outside this Skill.
When the Base material gate is closed, this legacy command stops and requires the HRD review flow. Run `manage_hrd_review.py queue`; if complete-flow candidates still lack HRD opinions, ask the user for `姓名+建议录用/不建议录用+意见`. After `propose` and one explicit `confirm --confirm SAVE`, rerun `queue`. Only when no complete-flow candidate remains without an opinion may `build_ceo_package.py --base-url "<BASE_URL>" --output-dir "${OUTPUT_DIR}"` build the review message, HTML, and materials ZIP. It rechecks Base material/process metadata and creates no package if any required HRD opinion remains. Only `建议录用` and `不建议录用` are valid HRD recommendations; ambiguity or a missing rationale remains unsaved. `CEO最终决策` is outside this Skill.

## Feishu delivery after review confirmation

飞书投递是外部交付步骤，不属于处理、证据核对、批次控制或来源 ZIP。只有五个正式输出文件和 `operation_log.jsonl` 齐全（部分投递后可额外存在受控 `delivery_receipt.json`）、`CEO 摘要状态`为“可提供”、预览已展示给用户，且用户在同一会话明确确认收件人与已审阅 CEO 摘要后，才能发送。状态为“暂缓”时，不得生成或发送 CEO 摘要；其余交付文件仅保留在本地，供问题处理和后续复核使用。

The Feishu payload is exactly three items: the CEO Summary Message as plain text, `candidate_pack.html`, and `materials.zip`. `consolidated_candidate_pack.md`, `source_manifest.md`, and `missing_evidence_list.md` remain local-only internal artifacts and must never be uploaded or sent to Feishu.

1. On the first delivery for a computer, an account change, or when the user provides `--lark-profile`, run `scripts/feishu_delivery.py preflight --recipient "<recipient>" [--lark-profile "<local-profile>"]` before preview. This verifies the local `lark-cli` user identity and required permissions without reading delivery files, resolving the recipient, uploading, or sending. It must report `ready: true`; otherwise report the blocker and do not fall back to another local user, bot, credential, or profile.
2. Run `scripts/feishu_delivery.py preview --output-dir "${WORKSPACE_ROOT}/output" --recipient "<recipient>" --summary "<reviewed CEO Summary Message>" [--lark-profile "<local-profile>"]`. This validates the verified sender, uniquely resolved recipient, five formal files plus `operation_log.jsonl`, and returns `batch_id`, the two attachment SHA-256 values, the CEO Summary hash, and a combined `delivery_fingerprint`. It contacts Feishu only for user identity and recipient resolution; it does not upload or send.
3. Present the delivery preview, CEO Summary, recipient description, Batch ID, and the short `delivery_fingerprint` to the user. A user may confirm in natural Chinese, for example `检查确认完毕，请在飞书上发给 open_id:ou_xxx` or `检查确认完毕，请在飞书上发给 name@example.com`.
4. Do not infer a recipient from a display name. Accept only `open_id:ou_...`, `chat_id:oc_...`, `email:name@example.com`, or an exact alias in `FEISHU_RECIPIENTS_JSON`. An email is resolved through Feishu CLI only after confirmation; zero or multiple matches block the send. Never use a candidate name, folder name, or filename as a Feishu recipient.
5. Repeat the reviewed CEO Summary verbatim in the send command and require `--confirm SEND`, matching `--batch-id` and `--delivery-fingerprint` returned by preview. Repeat the optional `--lark-profile` from preflight when it was supplied. A changed file, changed batch, missing summary, missing credential, absent confirmation, or profile authorization mismatch must stop before any external request.
6. The script invokes local `lark-cli` with `--as user`, sends the reviewed summary unchanged first, then only `candidate_pack.html` and `materials.zip` as individual Feishu file messages. The messages display as the authorized user, not the application bot. A controlled same-output `delivery_receipt.json` records the bound sender, recipient, hashes and completed components so a partial send can retry only unsent items; never write credentials, tokens or file keys.
7. If an upload or send fails after an earlier message succeeded, report the exact successfully sent files and their message IDs in the conversation, stop immediately, and require a new user confirmation before any retry. Do not silently resend completed messages.

Required Feishu CLI setup: each computer must install official `lark-cli`, configure its own local profile with an organization-authorized Feishu self-built application, then authorize the sender as a user with `im:message`, `im:message.send_as_user`, `im:resource`, and `offline_access`. For email recipient resolution, also authorize `contact:user:search`; `contact:user.id:readonly` is not used by the CLI path. The Plugin has no embedded App ID, secret, profile, user identity, tenant, or recipient and must never copy these between computers. The CLI owns local credential storage and token refresh. Do not run `lark-cli profile use` unless the user explicitly asks; use `--lark-profile` to select the sender for one command. Never ask the user to put tokens, a CLI profile, or any secret into a material file, delivery file, Plugin manifest, or conversation transcript.

The source ZIP remains the Material Pack Agent's byte-preserving and pure source handoff. Candidate Pack Agent must not append, remove, rename, recompress, or otherwise modify any ZIP entry. `materials.zip` contains only the original authorized `input/` material tree; it must never contain a CEO message or generated Candidate Pack artifact. Verify every `input/` entry still matches its source SHA-256.

## Resubmission reconciliation

Use this section only when the authorized source tree includes later delivery records in a candidate metadata update or Material Pack Agent's loose-resubmission inventory.

- Treat the register or inventory as a factual delivery log, not a workflow decision. Read `submission_record_id`, `submitted_at`, `candidate`, `material_role_or_stage`, original source path, packaged ZIP path, mapping basis, `delivery_round`, and `corrects_submission_record_id` or equivalent when supplied. Keep missing values as `Not Provided`.
- For a loose-file inventory record, accept its filename-derived candidate/role mapping only if the Material Pack Agent marked it `Mapped`, its mapped ZIP path is under that candidate's `resubmissions/` directory, it contains exactly one candidate token and one longest-specific role token, and it has an auditable `submitted_at` with `time_provenance` of `Filename` or `Plugin Recorded`. Treat `Unmapped`, `Ambiguous`, absent timestamp provenance, or `input/unmapped-resubmissions/` entries as active material integrity exceptions; never auto-reassign them.
- Keep every first-round and later source record in `source_manifest.md`. Assign a distinct Source ID to every new material, such as `SRC-SK-001-HRD-AUDIO-R2`; retain the original Source ID and record the correction relation. Do not recycle Source IDs.
- A material exception is **active** only when the latest valid registered or filename-mapped submission does not supply the required correctly mapped material. An earlier `Not Submitted`, missing, duplicate, or misplaced delivery is **resolved** only after the later submission passes the mapping, path, time, and pairing checks above.
- Preserve a resolved history in `source_manifest.md` and `consolidated_candidate_pack.md` as `Resolved by resubmission`, with the original and replacement Source IDs/submission record IDs/times. Do not list a resolved material issue in `missing_evidence_list.md`, the HTML active-exception cell, or the CEO message.
- Preserve an unresolved cross-candidate/cross-stage submission as `Contradicted` and active even when its bytes resemble the expected material. Never silently move, rename, or reassign it.
- A later material correction does not itself mark an interview stage as passed. Only the authoritative process-status source determines whether the candidate completed the full workflow.

## Material observations

Apply this section to material observations only; never turn one into a workflow status, recommendation, or CEO decision.

- `Invalid Extra Material`, `Misplaced Material`, `Duplicate Submission`, and `Multiple Material Versions` are nonblocking traceability findings. Retain every Source ID and source byte, mark all findings in `source_manifest.md`, and show only aggregate category counts in HTML. For `Invalid Extra Material`, request deletion of the invalid extra attachment only. Do not merge, choose a preferred version, or request a supplement solely for these findings.
- `Unmapped`, `Ambiguous`, and `Out of Scope` files are deliberately not handed to this agent. Their absence is not missing evidence and must not create a manifest entry, exception count, or inferred candidate state. The controlled register is read during this run then removed; it is not a source ZIP member or final deliverable.
- A filename/register disagreement resolved by the first agent before packaging is not an active exception. Do not reconstruct it from the pure ZIP.
- `Audio Content Unverified` is an upstream intake limitation. The pure ZIP does not disclose it; do not infer it from the presence of an audio file.
- In the HTML, retain packaged `Duplicate Submission` records only as an aggregate traceability note. They are not active material observations and must not be shown as needing a supplement. Do not show source paths, candidate IDs, names inferred from filenames, or generated-transcription text.

## HTML material verification

The `材料核验` section is aggregate evidence presentation only; it does not change workflow status, a recommendation, or a decision.

- State only the total count of unresolved material observations and concise category counts. Separately describe retained duplicate-submission groups as traceability history that does not require a supplement.
- Preserve all source IDs, candidate mappings, submission times, and original/copy details in the three internal Markdown files. Do not expose them in the HTML.
- Resolved resubmissions and retained duplicate submissions are not counted as active exceptions. Do not auto-correct, deduplicate, or imply a hiring outcome.

## CEO Summary Message format

CEO 摘要用于 CEO 知情审阅，不是录用审批或招聘决定。仅当存在 `Invalid Material`、`Missing Material` 或 `Unmapped Material` 等待补或待核实材料时暂缓；`Decision Blocked` 必须在摘要中如实呈现，但不阻断摘要发送。状态为“暂缓”时，遵循“材料未完成时的业务回复”：完整展示需要补交、需要人工核查及材料说明，且以“是否生成催办预览？”收尾；不提及 CEO 摘要、输出文件、处理机制或技术术语。

当 `CEO 摘要状态`为“可提供”时，使用恰好四段的 CEO 摘要，且不包含 Candidate ID、Source ID、机器可读 Batch ID、逐名材料异常详情或来源路径：

1. `这是第X批次的面试，本轮共N名候选人，通过完整流程的有M人，供 CEO 知情审阅。` `X` is the natural-language batch number, and the counts must equal the HTML funnel.
2. `通过完整流程的候选人：姓名1、姓名2。` State `通过完整流程的候选人：无。` when M is 0. These are process facts only; do not call them an offer, recommendation, or CEO decision.
3. Use concise aggregate wording only: `存在异常：D人待人工处理，E项待补或待核实材料。` `D` is the count of unique candidates with at least one authoritative `Decision Blocked` record. `E` is the count of unresolved Base material observations. Retained duplicate submissions are not included in `E`. When `D` and `E` are both 0, state `存在异常：无。` When only one is 0, state the other fact explicitly, for example `存在异常：3人待人工处理，无待补或待核实材料。` Do not name candidates, stages, reasons, source records, HRD 拟办, or CEO decisions in this paragraph.
4. `未通过或主动撤回F人。详情查看 candidate_pack.html，原始资料查看 materials.zip。` Count each candidate at most once. `F` includes only candidates whose authoritative technical-assignment evaluation or interview-stage status is `Stopped` or `Withdrawn`; do not include `Not Reached`, `Decision Blocked`, `Not Provided`, or `Unknown`. State `未通过或主动撤回0人。` when no candidate meets this condition.

Keep `Unknown`, `Not Provided`, `Not Reached`, `Withdrawn`, `Stopped`, and `Decision Blocked` as their source-recorded states in internal outputs. The CEO Summary's aggregate `未通过或主动撤回` count is a concise presentation of only `Stopped` and `Withdrawn` terminal records; it does not replace or collapse the internal states.

## HTML review report contract

`assets/candidate_pack_ui_template.html` is the frozen presentation baseline. Do not edit it while preparing an output. Replace every placeholder exactly once with escaped, source-backed text or the safe markup below; do not leave `{{...}}` in the generated HTML.

- `REPORT_STATUS`: use `材料核验已完成`; when authoritative `Decision Blocked` records exist, append their count as `N 人待人工处理`. Do not imply that an offer, HRD opinion, or CEO decision is complete. `REPORT_GENERATED_AT`: use the Base snapshot's `captured_at`, rendered as `YYYY-MM-DD HH:MM（Asia/Shanghai）`; use `未提供` only when no Base capture time exists.
- `INTERVIEW_BATCH_NAME`: natural-language `第X批`; `BATCH_CANDIDATE_COUNT`: `本轮候选人：N 人`. Do not emit `BATCH-XXX`.
- `EXECUTIVE_SUMMARY`: one concise count-based sentence in the form `第X批 · N 人参与 → M 人完整流程已完成 → D 项待人工处理 → E 人常规结束或未到达`. Never describe a completed interview flow as a pending offer or approval.
- `METRIC_CARDS`: exactly four `<div class="metric ...">` cards, in this order: `完整流程已完成`, `待人工确认`, `常规结束或未到达`, `待补或待核实材料`. The first three counts reconcile to the workflow facts; the fourth is only the count of active Base material observations. Retained duplicate submissions must not be counted as pending material or presented as requiring a resubmission.
- `FUNNEL_STAGES`: exactly four `<div class="funnel-step">` items in this order: `候选人总数`, `技术面试通过`, `BP 面试通过`, `HRD 面试通过`. Each shows the actual count, percentage of the batch total, and a concise factual note. Set `--funnel-width` to that percentage; technical-assignment evaluation is not a funnel stage.
- `PASSED_CANDIDATES`: show only the real names of candidates who completed all authoritative interview stages, from the authorized metadata. State that this is not an offer, HRD opinion, or CEO decision. When no candidate completed the flow, show `暂无完整流程已完成的候选人。` without inferring a decision.
- `DECISION_BLOCKED_ROWS`: show one three-column `<tr>` per authoritative `Decision Blocked` record using the candidate's real name, current process location, completed-process facts, and a Chinese status label `待人工处理`. When there is no such record, use one non-decision empty-state row with `colspan="3"`.
- `OPERATIONS_EXCEPTIONS`: first show whether active Base material observations require a supplement or verification. When none exist, state `材料核验已完成` and `当前没有待补或待核实材料。` Retained duplicate submissions may appear only as an aggregate history note stating that they are preserved for traceability and do not require a supplement. Keep source identifiers and per-file evidence in the internal Markdown files.
- The HTML must contain no `SK-`, `SRC-`, `BATCH-`, source path, URL, `href`, `<a>`, `<button>`, `<dialog>`, `<iframe>`, media control, script, evidence matrix, candidate detail card, editable field, or append subsection. Do not infer a real name from a filename or folder; use only authorized metadata.

## Evidence and decision boundary

- Every material conclusion must cite one or more Source IDs.
- Keep `Unknown` and `Not Provided` explicit.
- Do not infer interview scores, reviewer recommendations, offer outcomes, or CEO approval.
- Do not turn a source-recorded `Passed Gate` into a hiring recommendation.
- Record conflicts and mapping defects with evidence, impact, reversible handling, and escalation owner.

## QA before handoff

- Every ZIP source file appears exactly once in the Manifest.
- Every Manifest Source ID resolves to a ZIP path.
- Batch label, headcounts, funnel percentages, passed-name list, Decision Blocked rows, active material-exception counts, resubmission resolution state, and CEO Summary agree with the authoritative factual reconciliation. Stable IDs and full source mappings remain internal to Markdown outputs.
- HTML preserves every section of the frozen template: batch overview, four metrics, four-stage funnel, passed candidates, Decision Blocked table, and operations exceptions. Its counts reconcile to the same factual source. It contains no unrendered placeholder, candidate detail card, evidence matrix, source ID, machine-readable code, link, dialog, media control, preview, `href`, or JavaScript.
- Validate the generated HTML before handoff: it is validly structured, has no clipped or overlapping text at desktop width, and passes `scripts/feishu_delivery.py preview` static HTML validation before any delivery is shown. Verify in a browser when local-file policy permits; otherwise run the static check and disclose that browser interaction could not be exercised.
- Real names in HTML and the CEO Summary must come from authorized metadata only. Do not infer a name from a folder, filename, or another candidate's source. Stable `SK-XXX` IDs remain present only in internal Markdown for reuse and mapping.
- Missing evidence is disclosed rather than replaced. Resolved resubmissions preserve both the original misplaced and corrective delivery records and are excluded from active material exceptions and the CEO message. First-round duplicate original/copy records remain visible in internal outputs; suppress them from the CEO message only when a later delivery round exists.
- `candidate_pack.html` contains no links or run-relative source URLs and does not reference `.reference` files.
- A clean replay with the same input and the same recorded plugin receipt times produces semantically identical outputs.
- `CEO 摘要状态`为“可提供”时，最终用户回复包含明确标记的 `CEO 摘要（message）` 四段正文；状态为“暂缓”时，回复使用“材料未完成时的业务回复”，展示活动问题与全部材料说明并询问是否生成催办预览。两种情况下都不得将 CEO 消息写入 ZIP 或任何输出文件。
- If Feishu delivery is requested, confirm that `preview` performed no network action; before `send`, confirm the reviewed summary, explicit recipient format, Batch ID, and delivery fingerprint. After `send`, report only the returned message IDs and file names in the conversation. Verify that the source workspace and output directory still contain no credential, recipient, token, delivery-log, extracted-material, or sidecar file.

If any check fails, report the blocker and do not claim an Evidence Freeze.
