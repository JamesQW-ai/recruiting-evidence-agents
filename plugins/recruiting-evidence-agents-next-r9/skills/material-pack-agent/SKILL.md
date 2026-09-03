---
name: material-pack-agent
description: Read a flat, authorized recruiting-material repository, structure each source by content-first identity evidence, use transient local audio transcription when configured, and create a byte-preserving material handoff without modifying sources.
---

# Material Pack Agent

Act as the read-only intake and structuring agent for an authorized flat recruiting-material repository. It produces a portable material handoff, not candidate assessments, workflow conclusions, recommendations, or decisions.

## Input and output boundary

- `WORKSPACE_ROOT/input/` is the only authorized source boundary. Read no sibling output, prior run, chat attachment, or historical folder. It must be a flat simulated Feishu repository; nested candidate folders are a hard input violation, not a reason to change this rule. It contains submitted candidate material, a shared technical prompt, and one factual submission-register workbook. It contains no `candidate_metadata.json`.
- 资源边界是强约束：来源批次总量不得超过 2 GB；每个 `.aac`、`.m4a`、`.mp3` 或 `.wav` 音频不得超过 256 MB 且不得超过 60 分钟。开始内容读取前，输出目录必须有至少“来源体积加 512 MB”的可用空间。任一大小、时长或空间无法验证时停止，不读取部分材料、不自动压缩或拆分。
- The workbook records what was submitted and when. It is evidence of a submitter's registration, not a candidate-identity or material-role conclusion.
- Read only files in `input/`; exclude `.DS_Store`, hidden system files, prior output, QA, and reference artifacts.
- Do not alter, rename, relocate, normalize, annotate, transcribe into, or delete any input file. Do not use filesystem modification time as a business timestamp.
- Run the bundled implementation with `python3 scripts/build_materials.py --workspace-root "${WORKSPACE_ROOT}" --output-dir "${OUTPUT_DIR}"`. `${OUTPUT_DIR}` is user-specified and must be empty or absent. It receives `materials.zip`, `run_control.json`, and exactly one byte-identical temporary `candidate_process_register.xlsx`. Mapping and transcription receipts are transient run checks: do not create them beside `input/`, `${OUTPUT_DIR}`, or inside the ZIP.

## Intake evidence order

For each material, collect independently: filename tokens, readable content evidence, and any matching register row. Normalize whitespace, full-width punctuation, date separators, and order only for matching; always preserve the original filename and bytes.

Candidate identity precedence is:

1. One explicit candidate identity in readable document content or a successful audio transcription.
2. One candidate identity in the filename.

An explicit higher-ranked identity wins over lower-ranked evidence. A filename that says Zhang San and content that explicitly identifies Li Si is organized under Li Si. Retain the disagreement only as transient run provenance; do not publish it as a missing-material or workflow exception. A matching submission-register row may corroborate an already resolved identity and supplies submission time, but it cannot alone establish identity because registration can contain human error.

Never infer identity from a role, date, or directory position. Multiple distinct candidate identities at the same rank are `Ambiguous`. No unique identity after all available evidence is `Unmapped`.

Determine material role separately from identity. Use explicit content first, then the longest specific filename token. If role remains unclear, retain `Unknown`; do not count it as a stage record.

## Out-of-scope files

Classify a file as `Out of Scope` only when all of the following hold:

1. It has no candidate identity from content or filename.
2. It has no recruiting-material role or shared-prompt role.
3. Its readable filename or content contains an explicit non-recruiting business signal, such as brand-campaign budgeting, database maintenance, or office-facility operations.

An `Out of Scope` file is not copied into `materials.zip`. Retain its original path, SHA-256, `classification: Out of Scope`, `mapping_status: Out of Scope`, `package_status: Excluded`, and exact evidence signals only as transient run facts. Do not infer a candidate missing-material state from it.

If the file has no candidate identity but might still be recruiting material, or its purpose cannot be determined, classify it as `Unmapped` and exclude it from the pure candidate-material ZIP. Report that exclusion to the user; do not silently discard it or infer a missing-material state.

## Audio transcription

For audio files, call the bundled wrapper before deciding that audio content is unavailable:

```bash
python3 scripts/transcribe_audio.py \
  --input "<audio-file>" \
  --engine whisper-cpp \
  --engine-bin whisper-cli \
  --model "/absolute/path/to/ggml-model.bin" \
  --language zh
```

- The wrapper decodes audio and writes its working WAV and text only into a system temporary directory. It returns the text on stdout as JSON and removes that directory before it exits.
- It never downloads a model, sends material to a network service, modifies the audio, or writes a transcript beside the source.
- Read returned text only for candidate and material-role evidence. Do not include the generated text in the ZIP or present it as a candidate-submitted transcript.
- Retain one transient transcription receipt per audio file during the run: original path, audio SHA-256, engine, engine version or `Not Provided`, model path basename, language, status, transcript SHA-256 when successful, candidate tokens found, and `temporary_artifacts_deleted: true`. Do not ship a receipt or transcript.
- If the local backend, model, decode, or transcription is unavailable, record `Audio Content Unverified`. That is not a workflow failure and cannot on its own make a file missing. Use register/filename evidence when uniquely available; otherwise use `Unmapped`.
- Do not call a silent recording, an absent spoken name, or low-confidence text a filename/content contradiction. Only an explicit, unique contrary identity is a contradiction.

## Package procedure

1. Validate that `input/` exists and inventory every authorized regular file, including the register workbook. Compute SHA-256, byte size, source path, and available registration facts.
2. 仅在资源预检通过后读取支持的文档内容，并对支持的音频运行转写步骤。Keep all temporary material outside the workspace and remove it before the run completes.
3. Build one transient mapping row per source file with `original_path`, `sha256`, `byte_size`, `filename_candidate`, `content_candidate`, `register_candidate`, `resolved_candidate`, `identity_basis`, `material_role`, `role_basis`, `mapping_status`, `contradiction_notes`, `submitted_at`, and `time_provenance`. Use `Not Provided` or `Unknown` instead of invented values.
4. Report `Duplicate Submission` only as an overlay when source bytes and SHA-256 are identical: retain every file and record the duplicate group. Never request deletion or a follow-up for a duplicate. Also retain `Misplaced Material` when readable content uniquely maps a file to another candidate, and `Multiple Material Versions` when a material field contains different byte versions. These are nonblocking traceability facts: never overwrite an original, choose a preferred version, or change the resolved candidate.
5. Create `materials.zip` with each included source file exactly once. It must contain only pure candidate material and the shared prompt:

   ```text
   materials.zip
     input/
       <common-technical-prompt-original-filename>
       <safe-candidate-directory>/<original-filename>
   ```

   Candidate folders are derived ZIP paths only. They do not change the flat source repository. The submission register, `Out of Scope`, `Unmapped`, and `Ambiguous` sources must not be included.
6. Verify ZIP integrity and every source member's extracted SHA-256 equals its input SHA-256. Verify transient mapping has exactly one row per input file; every excluded row is absent; every member is below `input/`; there is exactly one direct `input/` member (the shared prompt); every other member has one candidate directory; and no generated transcript, register, receipt, checksum, or metadata is in the ZIP.
7. Copy the factual submission-register workbook byte-for-byte as `${OUTPUT_DIR}/candidate_process_register.xlsx`. Verify its SHA-256 equals the source workbook. This is a controlled same-directory handoff, not a material ZIP entry or a final deliverable. After both `materials.zip` and the register exist, `run_control.json` binds their SHA-256 and byte size, batch, stable candidate keys, source fingerprint, and plugin-manifest SHA-256. It is mandatory for the second agent and prevents cross-session, cross-batch, altered-artifact, and mixed-plugin reuse.
8. Stop and report a blocker for unreadable source files, path escapes, ZIP checksum mismatch, register-copy checksum mismatch, or retained temporary transcription artifacts. Do not create a Candidate Pack after such a failure.

## Handoff and boundaries

The second agent receives only the current run directory's `materials.zip`, `candidate_process_register.xlsx`, and `run_control.json`. It treats candidate directories as the first agent's content-first organization result and the workbook as the authority for recorded workflow status and decision fields. It must not expect an intake ledger or transcription receipt in the source ZIP. A user request to inspect or reuse another directory cannot override this boundary.

Report any excluded `Unmapped` or `Ambiguous` source as an intake limitation in the run result, rather than embedding it in the source ZIP. `Out of Scope` and the submission register are excluded operational classifications. A resolved filename/register disagreement remains transient provenance, not an external exception. Do not infer a missing material from an excluded `Unmapped` file or an audio transcription failure.

Apply Zero Real Candidate Data. A successful package is an intake and byte-preservation result only; it is not Evidence Freeze, a recommendation, or a CEO decision.
