#!/usr/bin/env python3
"""Build a D13 Candidate Pack from a pure material ZIP and controlled process register."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo

import run_control
import resource_limits

XML_NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REQUIRED = {
    "候选人编号", "候选人姓名", "技术作业评估状态", "技术面试流程状态",
    "BP面试流程状态", "HRD面试流程状态", "CEO最终决策",
    "当前流程阶段", "当前流程状态",
}
STAGES = (("技术面试", "Technical Interview"), ("BP面试", "BP Interview"), ("HRD面试", "HRD Interview"))
CEO_BLOCKING_OBSERVATIONS = {"Invalid Material", "Missing Material", "Unmapped Material"}
BUNDLED_TEMPLATE = Path(__file__).parents[1] / "assets" / "candidate_pack_ui_template.html"
FOLLOW_UP_RESOLUTION_FILE = "follow_up_resolution.json"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def cell_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference).group(0)
    value = 0
    for char in letters:
        value = value * 26 + ord(char) - ord("A") + 1
    return value - 1


def xlsx_rows(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(item.itertext()) for item in root.findall("x:si", XML_NS)]
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    rows = []
    for row in sheet.findall(".//x:sheetData/x:row", XML_NS):
        values: list[str] = []
        for cell in row.findall("x:c", XML_NS):
            index = cell_index(cell.get("r"))
            while len(values) <= index:
                values.append("")
            raw = cell.findtext("x:v", default="", namespaces=XML_NS)
            values[index] = shared[int(raw)] if cell.get("t") == "s" and raw else raw
        rows.append(values)
    return rows


def register_rows(path: Path) -> list[dict[str, str]]:
    rows = xlsx_rows(path)
    if len(rows) < 3:
        raise SystemExit("controlled process register has no candidate rows")
    headers = rows[1]
    missing = REQUIRED - set(headers)
    if missing:
        raise SystemExit("controlled process register is missing fields: " + ", ".join(sorted(missing)))
    result = []
    for values in rows[2:]:
        row = {header: values[index] if index < len(values) else "Not Provided" for index, header in enumerate(headers)}
        if row["候选人姓名"]:
            result.append(row)
    return result


def source_type(name: str) -> tuple[str, str, str]:
    lowered = name.lower()
    if "简历" in name or "resume" in lowered or "cv" in lowered:
        return "Resume", "Not Applicable", "Not Provided"
    if "评估" in name or "评阅" in name or "反馈" in name:
        return "Technical Assignment Evaluation", "Not a workflow stage", "Not Provided"
    if "技术面试" in name or "techinterview" in lowered:
        return "Technical Interview Material", "Technical Interview", "Not Provided"
    if "bp面试" in name.lower() or "bpinterview" in lowered:
        return "BP Interview Material", "BP Interview", "Not Provided"
    if "hrd面试" in name.lower() or "hrdinterview" in lowered:
        return "HRD Interview Material", "HRD Interview", "Not Provided"
    return "Unknown", "Not Provided", "Not Provided"


def source_id(candidate_index: int, ordinal: int) -> str:
    return f"SRC-SK-{candidate_index:03d}-{ordinal:02d}"


def markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def stage_status(row: dict[str, str], stage_cn: str) -> str:
    return row.get(f"{stage_cn}流程状态", "Not Provided") or "Not Provided"


def load_base_observation_items(path: Path, key: str, *, required: bool) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SystemExit(f"base intake observations are not valid JSON: {error}") from error
    if payload.get("schema_version") not in {1, 2}:
        raise SystemExit("base intake observations use an unsupported schema")
    items = payload.get(key)
    if items is None and not required:
        return []
    if not isinstance(items, list):
        raise SystemExit("base intake observations use an unsupported schema")
    observations: list[dict[str, str]] = []
    required = ("category", "scope", "fact", "handling")
    for item in items:
        if not isinstance(item, dict) or not all(isinstance(item.get(key), str) for key in required):
            raise SystemExit("base intake observations contain an invalid entry")
        observation = {key: item[key] for key in required}
        if isinstance(item.get("resolution_status"), str):
            observation["resolution_status"] = item["resolution_status"]
        observations.append(observation)
    return observations


def load_base_observations(path: Path) -> list[dict[str, str]]:
    return load_base_observation_items(path, "observations", required=True)


def load_base_trace_observations(path: Path) -> list[dict[str, str]]:
    """Read nonblocking intake history; schema-1 handoffs predate this field."""
    return load_base_observation_items(path, "trace_observations", required=False)


def confirmed_missing_reply(value: object) -> bool:
    return isinstance(value, str) and "无法提供" in value.strip()


def confirmed_missing_scopes(resolution_path: Path, observations_path: Path) -> set[tuple[str, str, str]]:
    """Read only current-snapshot confirmations from the same controlled output root."""
    try:
        resolution = json.loads(resolution_path.read_text(encoding="utf-8"))
        observations_payload = json.loads(observations_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise SystemExit(f"补件确认交接不存在：{resolution_path}") from error
    except json.JSONDecodeError as error:
        raise SystemExit(f"补件确认交接或 Base 观测不是有效 JSON：{error}") from error
    if resolution.get("schema_version") != 1 or not isinstance(resolution.get("confirmations"), list):
        raise SystemExit("补件确认交接使用了不支持的结构。")
    captured_at = observations_payload.get("captured_at")
    if not isinstance(captured_at, str) or resolution.get("snapshot_captured_at") != captured_at:
        raise SystemExit("补件确认交接未绑定到当前 Base 观测快照；禁止放行材料问题。")
    confirmed: set[tuple[str, str, str]] = set()
    for item in resolution["confirmations"]:
        if not isinstance(item, dict):
            raise SystemExit("补件确认交接包含无效条目。")
        sent_message_id = item.get("sent_message_id")
        message_linked = (
            item.get("confirmed_missing_reply_to") == sent_message_id
            or item.get("confirmed_missing_root_id") == sent_message_id
        )
        if not (
            isinstance(item.get("candidate"), str)
            and isinstance(item.get("material_field"), str)
            and isinstance(sent_message_id, str)
            and isinstance(item.get("recipient_open_id"), str)
            and isinstance(item.get("confirmed_missing_message_id"), str)
            and confirmed_missing_reply(item.get("confirmed_missing_reply"))
            and message_linked
        ):
            raise SystemExit("补件确认交接缺少原催办消息关联证据；禁止放行材料问题。")
        component = item.get("material_component", "材料")
        if not isinstance(component, str):
            raise SystemExit("Confirmed Missing 缺少材料组件；禁止放行材料问题。")
        confirmed.add((item["candidate"], item["material_field"], component))
    return confirmed


def apply_confirmed_missing_status(
    observations: list[dict[str, str]], resolution_path: Path, observations_path: Path,
) -> list[dict[str, str]]:
    """Annotate only matching Missing Material observations; input evidence stays unchanged."""
    confirmed = confirmed_missing_scopes(resolution_path, observations_path)
    updated: list[dict[str, str]] = []
    for observation in observations:
        item = dict(observation)
        scope_parts = [part.strip() for part in item["scope"].split("/")]
        component = scope_parts[2] if len(scope_parts) == 3 else "材料"
        if (
            item["category"] == "Missing Material"
            and len(scope_parts) in {2, 3}
            and (scope_parts[0], scope_parts[1], component) in confirmed
        ):
            item["resolution_status"] = "Confirmed Missing"
        updated.append(item)
    return updated


def blocking_observations(observations: list[dict[str, str]]) -> list[dict[str, str]]:
    """Only an explicit resolution boundary may release a material blocker."""
    terminal = {"Confirmed Missing"}
    return [
        item for item in observations
        if item["category"] in CEO_BLOCKING_OBSERVATIONS
        and item.get("resolution_status") not in terminal
    ]


def ceo_summary_gate(observations: list[dict[str, str]]) -> tuple[str, str]:
    blockers = blocking_observations(observations)
    if blockers:
        return "暂缓", "；".join(
            f"{item['scope']}：{observation_summary([item])}" for item in blockers
        )
    confirmed = [item for item in observations if item.get("resolution_status") == "Confirmed Missing"]
    if confirmed:
        return "可提供", "材料门禁已关闭；已确认缺件作为限制披露，不视为材料已完整。"
    return "可提供", "没有待核实或待补交的材料；本摘要供 CEO 知情审阅。"


def base_capture_time(path: Path) -> str:
    if not path.exists():
        return "未提供"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SystemExit(f"base intake observations are not valid JSON: {error}") from error
    captured_at = payload.get("captured_at")
    if not isinstance(captured_at, str) or not captured_at:
        return "未提供"
    try:
        moment = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    except ValueError:
        return "未提供"
    return moment.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M（Asia/Shanghai）")


def observation_summary(observations: list[dict[str, str]]) -> str:
    counts = Counter(item["category"] for item in observations)
    labels = {
        "Invalid Material": "无效文件", "Missing Material": "缺少材料", "Unmapped Material": "待核实材料",
        "Invalid Extra Material": "冗余无效文件", "Misplaced Material": "错放材料", "Duplicate Submission": "重复提交", "Multiple Material Versions": "多版本材料",
    }
    return "；".join(f"{count} 项{labels.get(category, '待处理材料')}" for category, count in sorted(counts.items()))


def batch_label(candidates: list[dict[str, str]]) -> str:
    batches = {
        row.get("批次", "").strip()
        for row in candidates
        if row.get("批次", "").strip() not in {"", "Not Provided", "Unknown"}
    }
    if len(batches) != 1:
        return "本批"
    return run_control.display_batch_label(batches.pop())


def render_html(
    template: str,
    candidates: list[dict[str, str]],
    duplicates: int,
    base_observations: list[dict[str, str]],
    captured_at: str = "未提供",
    trace_observations: list[dict[str, str]] | None = None,
) -> str:
    completed = [row for row in candidates if all(stage_status(row, name) == "通过" for name, _ in STAGES)]
    blocked = [row for row in candidates if row["当前流程状态"] == "Decision Blocked"]
    ended = [row for row in candidates if row not in completed and row not in blocked]
    stage_counts = [sum(stage_status(row, cn) == "通过" for row in candidates) for cn, _ in STAGES]
    material_issue_count = len(base_observations)
    decision_rows = "".join(
        "<tr><td>{candidate}</td><td>{stage}</td><td><span class=\"record-flag\">待人工处理</span>"
        "<span class=\"progress-note\">受控流程登记表标记为待人工处理。</span></td></tr>".format(
            candidate=html.escape(row["候选人姓名"]), stage=html.escape(row["当前流程阶段"])
        ) for row in blocked
    ) or "<tr><td colspan=\"3\">暂无待人工确认事项。</td></tr>"
    if material_issue_count:
        material_summary = observation_summary(base_observations)
        operation = f"<strong>材料核验未完成：{material_issue_count} 项待补或待核实材料</strong><p>{html.escape(material_summary)}。</p>"
    else:
        operation = "<strong>材料核验已完成</strong><p>当前没有待补或待核实材料。</p>"
    if duplicates:
        operation += f"<p class=\"operations-history\">另保留 {duplicates} 组重复提交记录，供后续追溯，不需要补交材料。</p>"
    trace = trace_observations or []
    if trace:
        operation += (
            f"<p class=\"operations-history\">另有 {len(trace)} 项非阻断材料追溯发现"
            f"（{html.escape(observation_summary(trace))}），均已保留原始材料，不需要补交。</p>"
        )
    material_card_class = "is-alert" if material_issue_count else "is-neutral"
    material_card_note = observation_summary(base_observations) if material_issue_count else "当前没有待补或待核实材料"
    cards = (
        f"<div class=\"metric is-positive\"><span>完整流程已完成</span><strong>{len(completed)}</strong><small>三轮流程状态均记录为通过</small></div>"
        f"<div class=\"metric is-action\"><span>待人工确认</span><strong>{len(blocked)}</strong><small>当前流程记录需要人工确认</small></div>"
        f"<div class=\"metric is-neutral\"><span>常规结束或未到达</span><strong>{len(ended)}</strong><small>未完成完整流程且不在待确认状态</small></div>"
        f"<div class=\"metric {material_card_class}\"><span>待补或待核实材料</span><strong>{material_issue_count}</strong><small>{html.escape(material_card_note)}</small></div>"
    )
    funnel = [
        ("候选人总数", len(candidates), "登记表的候选人行数。"),
        ("技术面试通过", stage_counts[0], "通过人数占本轮总人数，不代表逐层转化率。"),
        ("BP 面试通过", stage_counts[1], "通过人数占本轮总人数，不代表逐层转化率。"),
        ("HRD 面试通过", stage_counts[2], "通过人数占本轮总人数，不代表逐层转化率。"),
    ]
    funnel_html = "".join(
        f"<div class=\"funnel-step\" style=\"--funnel-width: {count / len(candidates) * 100:.0f}%\">"
        f"<span class=\"funnel-label\">{label}</span><strong>{count}</strong><span>{count / len(candidates) * 100:.0f}%</span><small>{note}</small></div>"
        for label, count, note in funnel
    )
    batch = batch_label(candidates)
    names = "、".join(html.escape(row["候选人姓名"]) for row in completed) or "暂无完整流程已完成的候选人。"
    report_status = "材料核验已完成" if not blocked else f"材料核验已完成；{len(blocked)} 人待人工处理"
    values = {
        "{{REPORT_STATUS}}": report_status,
        "{{REPORT_GENERATED_AT}}": captured_at,
        "{{INTERVIEW_BATCH_NAME}}": batch,
        "{{BATCH_CANDIDATE_COUNT}}": f"本轮候选人：{len(candidates)} 人",
        "{{EXECUTIVE_SUMMARY}}": f"{batch} · {len(candidates)} 人参与 → {len(completed)} 人完整流程已完成 → {len(blocked)} 人待人工处理 → {len(ended)} 人常规结束或未到达。",
        "{{METRIC_CARDS}}": cards,
        "{{FUNNEL_STAGES}}": funnel_html,
        "{{PASSED_CANDIDATES}}": f"<p class=\"approval-names\">{names}</p><p class=\"approval-note\">三轮面试均已记录为通过，不代表录用建议、HRD 意见或 CEO 决定。</p>",
        "{{DECISION_BLOCKED_ROWS}}": decision_rows,
        "{{OPERATIONS_EXCEPTIONS}}": operation,
    }
    for token, value in values.items():
        template = template.replace(token, value)
    return template


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a D13 Candidate Pack from an authorized handoff.")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    output_dir = run_control.require_directory(args.output_dir, "output 目录")
    run_control.validate_output_boundary(
        output_dir,
        {
            "materials.zip", "candidate_process_register.xlsx", "base_intake_observations.json",
            FOLLOW_UP_RESOLUTION_FILE, run_control.RUN_CONTROL_FILE, run_control.OPERATION_LOG_FILE,
        },
        allowed_directories={run_control.PROCESS_RECORDS_DIR},
    )
    zip_path = output_dir / "materials.zip"
    register_path = output_dir / "candidate_process_register.xlsx"
    observations_path = output_dir / "base_intake_observations.json"
    if not zip_path.is_file() or not register_path.is_file():
        raise SystemExit("materials.zip and candidate_process_register.xlsx are both required")
    candidates = register_rows(register_path)
    keys = run_control.validate_candidate_keys(candidates)
    control = run_control.load_run_control(output_dir / run_control.RUN_CONTROL_FILE)
    run_control.validate_handoff(output_dir, control)
    control_keys = [
        (str(item.get("batch")), str(item.get("candidate_id")), str(item.get("candidate")))
        for item in control["candidate_keys"] if isinstance(item, dict)
    ]
    if sorted(control_keys) != sorted(keys):
        raise SystemExit("run_control.json 与当前登记表不一致；禁止复用其他批次或候选人的输出。")
    run_control.append_operation_log(output_dir, "candidate_pack_started", batch_id=control["batch_id"])
    base_observations = load_base_observations(observations_path)
    trace = load_base_trace_observations(observations_path)
    resolution_path = output_dir / FOLLOW_UP_RESOLUTION_FILE
    if resolution_path.is_file():
        base_observations = apply_confirmed_missing_status(
            base_observations, resolution_path, observations_path,
        )
    blocked_observations = blocking_observations(base_observations)
    if blocked_observations:
        run_control.remove_formal_outputs(output_dir)
        run_control.append_operation_log(
            output_dir,
            "candidate_pack_blocked",
            reason="active_material_observations",
            observations=blocked_observations,
            trace_observations=trace,
            batch_id=control["batch_id"],
            candidate_keys=control["candidate_keys"],
            source_fingerprint=control["source_fingerprint"],
        )
        (output_dir / run_control.RUN_CONTROL_FILE).unlink()
        run_control.refresh_process_records(output_dir)
        raise SystemExit("存在未到达规定边界的材料问题；仅保留 operation_log.jsonl，未生成交付物。")
    if observations_path.is_file():
        run_control.append_operation_log(
            output_dir,
            "hrd_review_required",
            batch_id=control["batch_id"],
            source_fingerprint=control["source_fingerprint"],
        )
        raise SystemExit("材料门禁已关闭；必须先完成经确认的 HRD 审核意见，不能生成无 HRD 意见的正式包。")
    captured_at = base_capture_time(observations_path)
    candidate_names = [row["候选人姓名"] for row in candidates]

    with zipfile.ZipFile(zip_path) as archive:
        infos = resource_limits.validate_zip_infos(archive)
        if archive.testzip() is not None:
            raise SystemExit("materials.zip integrity check failed")
        members = sorted(info.filename for info in infos)
        if any(not name.startswith("input/") for name in members):
            raise SystemExit("materials.zip contains a member outside input/")
        direct = [name for name in members if len(PurePosixPath(name).parts) == 2]
        if len(direct) != 1:
            raise SystemExit("materials.zip must have exactly one shared prompt at input root")
        if any(len(PurePosixPath(name).parts) != 3 for name in members if name not in direct):
            raise SystemExit("candidate material must be directly under input/<candidate>/")
        directory_names = sorted({PurePosixPath(name).parts[1] for name in members if len(PurePosixPath(name).parts) >= 3})
        if directory_names != sorted(candidate_names):
            raise SystemExit("candidate directories do not match the controlled process register")
        hashes: dict[str, list[str]] = defaultdict(list)
        for info in infos:
            hashes[resource_limits.sha256_zip_member(archive, info)].append(info.filename)
    duplicate_groups = [members for members in hashes.values() if len(members) > 1]
    source_rows: list[list[str]] = []
    source_ids: list[str] = []
    source_rows.append(["`SRC-SHARED-001`", "Shared", "Shared Technical Assignment Prompt", "Not Applicable", "Not Provided", f"`{direct[0]}`", "Not Applicable"])
    source_ids.append("SRC-SHARED-001")
    per_candidate_ordinal = Counter()
    for name in members:
        if name == direct[0]:
            continue
        parts = PurePosixPath(name).parts
        candidate = parts[1]
        index = candidate_names.index(candidate) + 1
        per_candidate_ordinal[candidate] += 1
        identifier = source_id(index, per_candidate_ordinal[candidate])
        kind, stage, date = source_type(parts[-1])
        source_rows.append([f"`{identifier}`", f"`SK-{index:03d}`", candidate, kind, stage, date, f"`{name}`", "Not Provided"])
        source_ids.append(identifier)

    candidate_rows = []
    for index, row in enumerate(candidates, 1):
        candidate_rows.append([
            f"`SK-{index:03d}`", row["候选人姓名"], row["技术作业评估状态"],
            stage_status(row, "技术面试"), stage_status(row, "BP面试"), stage_status(row, "HRD面试"),
            row["CEO最终决策"], row["当前流程阶段"], row["当前流程状态"],
            row.get("处理状态", "Not Provided"),
        ])
    completed = [row for row in candidates if all(stage_status(row, cn) == "通过" for cn, _ in STAGES)]
    blocked = [row for row in candidates if row["当前流程状态"] == "Decision Blocked"]
    ended = [row for row in candidates if row["当前流程状态"] in {"Stopped", "Withdrawn", "Not Reached"}]
    stage_counts = [sum(stage_status(row, cn) == "通过" for row in candidates) for cn, _ in STAGES]

    archive_hash = sha256_path(zip_path)
    display_label = run_control.display_batch_label(str(control.get("batch", "")) or str(control["batch_label"]))
    consolidated = "# Consolidated Candidate Pack\n\n- Batch ID: `" + control["batch_id"] + "`\n\n## Batch Control\n\n" + markdown_table(
        ["Field", "Value"], [
            ["Batch label", display_label], ["Material source", "`materials.zip`"], ["Material SHA-256", f"`{archive_hash}`"],
            ["Process-status source", "Controlled same-run register; deleted after successful reconciliation"],
            ["Candidate count", str(len(candidates))], ["CEO decision", "Not Provided"], ["Evidence Freeze", "Not Declared"],
        ]) + "\n\n## Factual Reconciliation\n\n" + markdown_table(
        ["Candidate ID", "Candidate", "Technical-assignment status", "Technical Interview", "BP Interview", "HRD Interview", "CEO", "Current stage", "Current status", "Processing status"], candidate_rows
    ) + "\n\n## Counts\n\n" + markdown_table(
        ["Fact", "Count"], [
            ["Complete recorded interview flows", str(len(completed))], ["Technical Interview passed", str(stage_counts[0])],
            ["BP Interview passed", str(stage_counts[1])], ["HRD Interview passed", str(stage_counts[2])],
            ["Source-recorded Decision Blocked", str(len(blocked))], ["Workflow not complete and not Decision Blocked", str(len(ended))], ["Stopped or Withdrawn", str(sum(row["当前流程状态"] in {"Stopped", "Withdrawn"} for row in candidates))],
            ["Active material observations", str(len(base_observations))], ["Nonblocking material trace observations", str(len(trace))],
            ["Retained duplicate submission groups", str(len(duplicate_groups))],
        ]) + "\n\n## Boundaries\n\n- Stage and decision fields above come only from the controlled register, not filenames or transcripts.\n- `通过` is a recorded workflow status, not an HRD recommendation or CEO decision.\n- The temporary controlled register was deleted only after this reconciliation completed.\n"
    missing_rows: list[list[str]] = []
    for item in base_observations:
        missing_rows.append(["P2", item["scope"], item["category"], item["fact"], item["handling"]])
    if not missing_rows:
        missing_rows.append(["P3", "Material intake", "No active observation", "No active material observation was generated during this run.", "No action required."])
    ceo_summary_status, ceo_summary_reason = ceo_summary_gate(base_observations)
    missing = "# Missing Evidence List\n\n" + markdown_table(
        ["Priority", "Scope", "State", "Source-backed fact", "Handling"], missing_rows
    ) + "\n\n## CEO 摘要状态\n\n" + markdown_table(
        ["项目", "当前状态"], [["状态", f"`{ceo_summary_status}`"], ["原因", ceo_summary_reason]]
    ) + "\n\n## Non-findings\n\n- The temporary controlled register and Base-intake observations are not final deliverables and are not embedded in `materials.zip`.\n- An attachment whose content uniquely maps to another candidate is organized under that candidate without changing its filename or bytes.\n"
    manifest = "# Source Manifest\n\n## Manifest Control\n\n" + markdown_table(
        ["Field", "Value"], [["Source archive", "`materials.zip`"], ["Archive SHA-256", f"`{archive_hash}`"], ["ZIP source members", str(len(members))], ["Unique Source IDs", str(len(source_ids))], ["Workflow status source", "Controlled register, read during run then deleted"]]
    ) + "\n\n## Sources\n\n" + markdown_table(
        ["Source ID", "Candidate ID", "Candidate", "Source type", "Stage", "Event date", "Relative ZIP path", "Pairing status"], source_rows
    ) + "\n\n## Material Observations\n\n" + markdown_table(
        ["Observation", "Details", "Handling"],
        [["Duplicate Submission", "; ".join(f"`{item}`" for group in duplicate_groups for item in group) or "None", "All copies retained."],
        ] + [[item["category"], item["fact"], item["handling"]] for item in base_observations + trace]
    ) + "\n"
    html_output = render_html(
        BUNDLED_TEMPLATE.read_text(encoding="utf-8"), candidates, len(duplicate_groups), base_observations,
        captured_at, trace,
    )
    if "{{" in html_output or "}}" in html_output:
        raise SystemExit("HTML template substitution failed")

    (output_dir / "consolidated_candidate_pack.md").write_text(consolidated, encoding="utf-8")
    (output_dir / "missing_evidence_list.md").write_text(missing, encoding="utf-8")
    (output_dir / "source_manifest.md").write_text(manifest, encoding="utf-8")
    (output_dir / "candidate_pack.html").write_text(html_output, encoding="utf-8")
    temporary = {"candidate_process_register.xlsx", run_control.RUN_CONTROL_FILE}
    if observations_path.exists():
        temporary.add(observations_path.name)
    if resolution_path.exists():
        temporary.add(resolution_path.name)
    expected = {"materials.zip", "consolidated_candidate_pack.md", "missing_evidence_list.md", "source_manifest.md", "candidate_pack.html", run_control.OPERATION_LOG_FILE} | temporary
    actual = {path.name for path in output_dir.iterdir() if path.is_file() and path.name != ".DS_Store"}
    if actual != expected:
        raise SystemExit("unexpected candidate-pack output boundary before register cleanup")
    register_path.unlink()
    if observations_path.exists():
        observations_path.unlink()
    if resolution_path.exists():
        resolution_path.unlink()
    (output_dir / run_control.RUN_CONTROL_FILE).unlink()
    run_control.append_operation_log(output_dir, "candidate_pack_completed", batch_id=control["batch_id"], candidate_count=len(candidates))
    run_control.refresh_process_records(output_dir)
    final = {path.name for path in output_dir.iterdir() if path.is_file() and path.name != ".DS_Store"}
    if final != expected - temporary:
        raise SystemExit("temporary process register cleanup failed")
    print({"output_dir": str(output_dir), "candidate_count": len(candidates), "complete_flows": len(completed), "decision_blocked": len(blocked), "active_material_observations": len(base_observations), "nonblocking_trace_observations": len(trace), "retained_duplicate_submission_groups": len(duplicate_groups), "ceo_summary_status": ceo_summary_status, "register_deleted": not register_path.exists(), "base_observations_deleted": not observations_path.exists()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
