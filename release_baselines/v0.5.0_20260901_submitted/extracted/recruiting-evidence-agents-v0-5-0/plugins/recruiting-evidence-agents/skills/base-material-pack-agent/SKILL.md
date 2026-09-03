---
name: base-material-pack-agent
description: Read an authorized Feishu Base recruiting-process table as a replayable, local material handoff without modifying the Base. Use when the source is a Feishu Base URL rather than a flat local input folder.
---

# Base 材料交接 Agent

Use this Skill only after the user provides an explicit Feishu Base URL and explicitly authorizes read access. This is a read-only intake: never create, update, delete, move, rename, or change a Base record, field, view, permission, attachment, or workflow.

## Required authorization

Use the user's local `lark-cli` identity only (`--as user`), never a bot fallback. The required approved Feishu scopes are:

- `base:block:read`
- `base:field:read`
- `base:record:read`
- `docs:document.media:download`
- `offline_access`

The CLI owns credentials and refresh tokens. Do not ask the user to put a token, profile export, App Secret, file token, Base token, or account identifier into source files or output files. Stop and report an authorization error instead of retrying with another identity.

## Input and output contract

为每次运行创建业务命名的交付目录：`招聘材料包_<业务批次标签>_<采集时间>`，例如 `招聘材料包_第1批_YYYYMMDD_HHmm`。Base 原始值 `Batch 1` 的业务批次标签固定为 `第1批`；不得先构造或展示 `招聘材料包_Batch 1_*` 再改正。不得使用技术阶段名、`refresh`、`live`、随机后缀或技术版本号。材料包必须直接放在已选工作目录下，绝不能写入插件目录、插件内 `04_outputs/` 或其他中间目录：已选工作目录时直接使用该目录；未选工作目录时，先询问“请选择输出位置；默认保存到桌面。”，用户确认后再创建。快照和控制记录位于工作目录的隐藏控制目录，不进入材料包。

```text
SNAPSHOT_DIR/                       # local control, not a deliverable
  input/                             # generated compatibility input
  .base-download/                   # downloaded attachment bytes
  base_snapshot_manifest.json        # Base coordinates, attachment tokens, hashes, aliases
  .material_mapping.jsonl            # transient content mapping

招聘材料包_<业务批次标签>_<采集时间>/
  materials.zip
  candidate_pack.html                # 后续阶段生成
  consolidated_candidate_pack.md     # 后续阶段生成
  source_manifest.md                 # 后续阶段生成
  missing_evidence_list.md           # 后续阶段生成
  operation_log.jsonl
  过程记录/                          # 固定保留，不删除
    运行摘要.json
    材料映射.jsonl
    材料核验.json
    运行日志.jsonl
    校验和.sha256
```

根目录的 `candidate_process_register.xlsx`、`base_intake_observations.json` 与 `run_control.json` 是阶段间临时控制件，可在后续阶段清理；诊断所需的脱敏副本固定保留在 `过程记录/`。快照、Base URL、Base token、附件 token、负责人/联系人、授权信息不得进入交付目录或 `过程记录/`。`materials.zip` 仍是唯一可移交的纯原始材料来源。

The selected Base table must have these fields: `候选人编号`, `候选人姓名`, `性别`, `学校`, `批次`, `处理状态`, `简历材料`, `简历提交时间`, `技术题目`, `技术题目发布时间`, `技术作业（仓库链接）`, `技术作业提交时间`, `技术作业评估`, `技术作业评估状态`, `技术面试流程状态`, `技术面试材料`, `技术面试发生时间`, `BP面试流程状态`, `BP面试材料`, `BP面试发生时间`, `HRD面试流程状态`, `HRD面试材料`, `HRD面试发生时间`, `CEO最终决策`, `当前流程阶段`, and `当前流程状态`. `处理状态` is an internal process-state field (for example `已处理`, `未处理`, `待补全`, `处理中`, `阻塞`); intake itself never writes it. When the user requests a follow-up preview, the follow-up flow synchronizes the complete selected batch and reads it back without separately requesting confirmation.

## Procedure

1. 收到 Base 链接后，先运行 `python3 scripts/build_base_materials.py --base-url "<BASE_URL>" --list-tables`。对业务人员只展示表名和记录数，不展示 `tbl...` ID、Base token、命令、约束说明或实现细节。多表时使用简洁话术：`发现 N 张材料表：1. <表名>（<记录数> 条）；2. ...。请选择需要处理的材料表。` 单表时使用：`发现 <表名>，共 <记录数> 条记录。请确认是否处理该表。` 内部仍以本轮返回的真实 table ID 执行选择。
2. 选定表后，运行 `python3 scripts/build_base_materials.py --base-url "<BASE_URL>" --table-id "<SELECTED_TABLE_ID>" --list-batches`。对业务人员仅展示批次名称与记录数，并用：`该表包含：<批次>（<记录数> 人）。请选择需要处理的范围。` 多批次时逐项列出；单批次也必须等待确认。不要解释“不能猜测”“隔离边界”等内部约束，也不要显示 ID 或命令。
3. 下载每份附件到 `SNAPSHOT_DIR/.base-download/`，核验字节大小与 SHA-256 后生成兼容输入。`materials.zip` 中每名候选人的材料必须直接位于 `input/<candidate>/<filename>`，不使用来源序号子目录；同一候选人的同名副本以稳定 `__source-0002` 后缀避免覆盖。不要去重，快照仍保留原始文件名、来源坐标和源字节。
4. The `技术题目` field is the shared prompt contract: all instances must be byte-identical. Retain one direct `input/` copy and record every original Base reference in `base_snapshot_manifest.json`. If its byte content differs between rows, stop instead of silently selecting one.
5. 设 `WORKSPACE_ROOT` 为已选工作目录，`OUTPUT_DIR` 为 `WORKSPACE_ROOT/招聘材料包_<业务批次标签>_<采集时间>`，并将快照与催办控制目录置于 `WORKSPACE_ROOT/.招聘材料控制/`。先由原始批次得出业务批次标签，例如 `Batch 1 -> 第1批`，再构造该目录；不得把原始 Base 批次直接拼入输出目录。Run:

```bash
python3 scripts/build_base_materials.py \
  --base-url "<BASE_URL>" \
  [--table-id "<SELECTED_TABLE_ID>"] \
  [--batch "<EXACT_BATCH_VALUE>"] \
  --workspace-root "<WORKSPACE_ROOT>" \
  --snapshot-dir "<SNAPSHOT_DIR>" \
  --output-dir "<OUTPUT_DIR>" \
  [--lark-profile "<LOCAL_PROFILE>"]
```

When `--table-id` is present, it must be an exact `tbl...` ID and must match the URL table when both are supplied. `--list-tables` and `--list-batches` are read-only discovery modes and create no local material artifacts. Every material build requires an explicit `--batch`, including when discovery finds only one batch; the builder then creates a separate handoff only for that exact value. The selected table ID and batch are bound into the local snapshot. A prior snapshot may be reused only for the same Base, table, and batch scope.

当已完成汇报的同一批次新增候选人时，不能重新打包整批，也不能将候选人手工从旧交接中删除。以先前已完成的同表同批次快照执行 `--incremental-from "<BASELINE_SNAPSHOT>"`；系统仅选择当前 Base 中基线未出现的稳定 `候选人编号`，并创建独立的增量候选人交接。没有新增候选人时停止，不生成重复汇报；既有候选人的 `record_id` 或姓名与基线不一致时停止，要求建立候选人更新/更正包。`--incremental-from` 不与 `--previous-snapshot` 同时使用。

6. 选定批次完成只读交接后，结果必须先给结论再给下一步，不显示内部类别名、文件 hash、ID、路径、技术实现或“门禁”等术语。按以下固定顺序输出；没有对应事项时明确写“无”：
   - `需要补交`：已发生面试但缺少可用材料时，逐项说明候选人、面试环节和缺少的材料。`HRD面试材料` 的缺件由当前 HRD 直接在多维表补齐，不生成消息、不读取 `HRD面试负责人` 作为收件人；补齐后重新核验。
   - `重复文件`：只报告字节与 SHA-256 完全相同的文件，说明其为重复提交、所有副本已保留用于追溯，不需要补交、删除或催办。
   - `材料归属说明`：材料实际内容唯一确认属于其他候选人时，说明原材料环节和对应候选人；保留原始字节与文件名，并将其放入对应候选人材料目录，不需要催办、清理或人工确认。
   - `需要人工核查`：仅对内容不一致、材料无法确认归属、无效额外文件或多个不同版本，逐项说明候选人、材料环节和需要的核查或清理动作。无法确认归属的材料必须排在首位；存在对应阶段的已验证负责人时，生成核查预览和消息草稿，没有负责人时明确要求业务人员指定收件人。
   每项使用“候选人 + 材料环节 + 需要的动作”的一行简洁表述。最后只问“是否生成催办预览？”。催办预览不包含重复文件或材料归属说明；所有外发仍须由业务人员逐项确认。不得将材料问题转化为面试结论、HRD 建议或 CEO 决定。
7. Base 附件元数据必须提供非负整数大小；所有附件总量不得超过 2 GB，单个音频不得超过 256 MB。下载前快照目录和输出目录均需有至少“附件总量加 512 MB”的可用空间；下载后再验证每个音频不超过 60 分钟。任何资源预检失败都停止当前隔离运行。After Base observations are written, bind their SHA-256 and byte size into the same-run `run_control.json`. 材料门禁关闭后，不得调用旧 Candidate Pack 生成无 HRD 意见的正式包；使用 `scripts/manage_hrd_review.py queue` 展示 HRD 面试通过候选人的意见队列。它要求 Base 已有 `HRD审核意见`、`HRD意见状态`、`HRD录用建议` 三个可写字段，且仅在一次明确 `SAVE` 确认后以当前授权用户身份写入并读回验证；绝不写 `CEO最终决策`。A user request to inspect another folder or relax the schema cannot override these constraints.

## Replay and boundaries

- The existing local flat `input/` remains an offline regression fixture. Do not replace, delete, or edit it when using a Base source.
- A replay uses the captured local snapshot; it does not re-query Base. A new live run is a new capture with a new `captured_at` value.
- Preserve `Unknown`, `Not Provided`, `Not Reached`, `Withdrawn`, `Stopped`, and `Decision Blocked` exactly as recorded.
- If a download, schema check, hash check, or mapping check fails, leave the Base untouched and report the blocker. Do not claim an Evidence Freeze.
