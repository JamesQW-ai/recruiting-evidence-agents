#!/usr/bin/env python3
"""Build the formal CEO package only from a closed Base handoff and saved HRD opinions."""

from __future__ import annotations

import argparse
import html
import json
import os
import tempfile
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import build_base_materials
import build_candidate_pack
import manage_hrd_review
import resource_limits
import run_control


FORMAL_OUTPUTS = {
    "materials.zip", "consolidated_candidate_pack.md", "candidate_pack.html",
    "source_manifest.md", "missing_evidence_list.md", "ceo_summary_message.md",
    run_control.OPERATION_LOG_FILE,
}
HANDOFF_OUTPUTS = {
    "materials.zip", "candidate_process_register.xlsx", "base_intake_observations.json",
    "follow_up_resolution.json", run_control.RUN_CONTROL_FILE, run_control.OPERATION_LOG_FILE,
}
TEMPLATE = Path(__file__).parents[1] / "assets" / "hrd_candidate_pack_ui_template.html"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def write_atomic(path: Path, value: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(value)
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def hrd_completion_time(output_dir: Path, batch_id: str, *, required: bool) -> str:
    log = output_dir / run_control.OPERATION_LOG_FILE
    if not log.is_file():
        if required:
            raise SystemExit("存在已保存 HRD 意见，但缺少同批次的 SAVE 审计记录；请先通过 HRD 确认流程保存。")
        return "Not Provided"
    latest = "Not Provided"
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            raise SystemExit("operation_log.jsonl 不是有效审计记录。") from None
        if item.get("batch_id") != batch_id or not isinstance(item.get("at"), str):
            continue
        if item.get("event") == "hrd_opinions_saved":
            latest = item["at"]
        elif (
            item.get("event") == "hrd_opinions_revalidated"
            and isinstance(item.get("source_batch_id"), str)
            and isinstance(item.get("source_save_at"), str)
            and isinstance(item.get("opinion_fingerprint"), str)
        ):
            latest = item["at"]
    if required and latest == "Not Provided":
        raise SystemExit("存在已保存 HRD 意见，但缺少同批次的 SAVE 审计记录；请先通过 HRD 确认流程保存。")
    return latest


def metadata_unchanged(output_dir: Path, context: dict[str, Any], profile: str | None) -> list[tuple[str, dict[str, object]]]:
    observations_path = output_dir / "base_intake_observations.json"
    try:
        snapshot = json.loads(observations_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SystemExit("base_intake_observations.json 不是有效 JSON。") from error
    expected = snapshot.get("metadata_fingerprint")
    if snapshot.get("schema_version") != 2 or not isinstance(expected, str):
        raise SystemExit("当前 Base 交接缺少材料元数据摘要；请从授权 Base 重新构建后再生成 CEO 包。")
    base = context["base"]
    live = manage_hrd_review.records(base["base_token"], base["table_id"], profile, output_dir)
    fields_payload = manage_hrd_review.cli_json(
        ["base", "+field-list", "--base-token", base["base_token"], "--table-id", base["table_id"], "--as", "user", "--json"],
        profile,
        output_dir,
    )
    field_data = build_base_materials.data_of(fields_payload)
    field_items = field_data.get("fields") or field_data.get("items") or field_data.get("data")
    if not isinstance(field_items, list):
        raise SystemExit("Base 字段列表不可用；不能验证 CEO 包的材料元数据。")
    field_by_id = {
        str(field.get("field_id", field.get("id", ""))): {
            "field_id": field.get("field_id", field.get("id")),
            "field_name": field.get("field_name") or field.get("name"),
        }
        for field in field_items
        if isinstance(field, dict) and field.get("field_id", field.get("id"))
    }
    live = scoped_live_records(live, field_by_id, context.get("candidate_keys"))
    actual = build_base_materials.metadata_fingerprint(live, field_by_id)
    if actual != expected:
        raise SystemExit("Base 材料或流程元数据已变化；请先重新进行受控 Base 交接和 HRD 审核。")
    return live


def scoped_live_records(
    live: list[tuple[str, dict[str, object]]], field_by_id: dict[str, dict[str, object]], candidate_keys: object,
) -> list[tuple[str, dict[str, object]]]:
    """Limit metadata validation to the immutable cohort defined by the handoff."""
    if not isinstance(candidate_keys, list):
        return live
    identity_fields = {
        str(field.get("field_name")): field_id
        for field_id, field in field_by_id.items()
        if str(field.get("field_name")) in {"批次", "候选人编号", "候选人姓名"}
    }
    if set(identity_fields) != {"批次", "候选人编号", "候选人姓名"}:
        raise SystemExit("Base 缺少候选人范围核验字段；不能验证 CEO 包。")
    expected = {
        (str(item.get("batch")), str(item.get("candidate_id")), str(item.get("candidate")))
        for item in candidate_keys
        if isinstance(item, dict)
    }
    if len(expected) != len(candidate_keys) or any(not all(key) for key in expected):
        raise SystemExit("受控交接缺少有效候选人范围；不能验证 CEO 包。")
    scoped = []
    observed = set()
    for record_id, values in live:
        key = tuple(
            manage_hrd_review.text_value(values.get(identity_fields[name]))
            for name in ("批次", "候选人编号", "候选人姓名")
        )
        if key in expected:
            scoped.append((record_id, values))
            observed.add(key)
    if observed != expected:
        raise SystemExit("当前 Base 与同次受控登记表候选人范围不一致；不能验证 CEO 包。")
    return scoped


def selected_cohort(register: list[dict[str, str]], candidates_json: str) -> list[dict[str, str]]:
    """The isolated handoff directory defines the CEO material cohort."""
    if not candidates_json:
        return register
    try:
        requested = json.loads(candidates_json)
    except json.JSONDecodeError as error:
        raise SystemExit("candidates-json 必须是候选人姓名 JSON 数组。") from error
    if not isinstance(requested, list) or not all(isinstance(item, str) and item.strip() for item in requested):
        raise SystemExit("candidates-json 必须是候选人姓名 JSON 数组。")
    names = [item.strip() for item in requested]
    cohort_names = [item["候选人姓名"] for item in register]
    if len(names) != len(set(names)) or len(cohort_names) != len(set(cohort_names)) or set(names) != set(cohort_names):
        raise SystemExit("CEO 包必须覆盖当前隔离交接目录的完整候选人范围；如需子集，请先为该子集创建新的受控交接。")
    return register


def saved_opinions_for_cohort(context: dict[str, Any], cohort: list[dict[str, str]]) -> list[dict[str, Any]]:
    cohort_keys = {run_control.candidate_key(item) for item in cohort}
    eligible = [
        item for item in context["eligible"]
        if (str(item["batch"]), str(item["candidate_id"]), str(item["candidate"])) in cohort_keys
    ]
    incomplete = [item["candidate"] for item in eligible if item["opinion_status"] != manage_hrd_review.COMPLETE_STATUS]
    if incomplete:
        raise SystemExit("HRD 面试通过候选人的 HRD 意见尚不完整：" + "、".join(sorted(incomplete)))
    return eligible


def classify_outcomes(cohort: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    outcomes = {"complete_pass": [], "not_passed_or_exited": [], "unconfirmed": []}
    known_unsuccessful = {"不通过", "Stopped", "Withdrawn", "Not Reached"}
    for row in cohort:
        statuses = [build_candidate_pack.stage_status(row, stage) for stage, _ in build_candidate_pack.STAGES]
        if all(status == "通过" for status in statuses):
            outcomes["complete_pass"].append(row)
        elif any(status in known_unsuccessful for status in statuses):
            outcomes["not_passed_or_exited"].append(row)
        else:
            outcomes["unconfirmed"].append(row)
    return outcomes


def limited_names(rows: list[dict[str, str]], limit: int = 3) -> str:
    names = [row["候选人姓名"] for row in rows]
    if not names:
        return "无"
    shown = "、".join(names[:limit])
    return shown if len(names) <= limit else f"{shown} 等 {len(names)} 位"


def ceo_summary_message(control: dict[str, Any], outcomes: dict[str, list[dict[str, str]]], observations: list[dict[str, str]]) -> str:
    complete = outcomes["complete_pass"]
    unsuccessful = outcomes["not_passed_or_exited"]
    unconfirmed = outcomes["unconfirmed"]
    anomaly_names = sorted({item["scope"].split(" / ", 1)[0] for item in observations})
    source_batch = str(control.get("batch", "")).strip() or str(control.get("batch_label", ""))
    batch_label = run_control.display_batch_label(source_batch)
    anomaly_line = (
        "其中存在异常 0 位。"
        if not anomaly_names else
        f"其中存在异常 {len(anomaly_names)} 位：{'、'.join(anomaly_names)}。"
    )
    return (
        f"{batch_label}共 {sum(len(rows) for rows in outcomes.values())} 位候选人的流程汇总如下：\n"
        f"完整通过 {len(complete)} 位：{limited_names(complete)}；\n"
        f"明确未通过/退出 {len(unsuccessful)} 位；\n"
        f"状态未能确认 {len(unconfirmed)} 位。\n"
        f"{anomaly_line}\n"
        "附件为候选人审阅页和材料 ZIP；详情请见附件。"
    )


def duplicate_groups(zip_path: Path) -> list[list[str]]:
    with zipfile.ZipFile(zip_path) as archive:
        infos = resource_limits.validate_zip_infos(archive)
        if archive.testzip() is not None:
            raise SystemExit("materials.zip integrity check failed")
        grouped: dict[str, list[str]] = defaultdict(list)
        for info in infos:
            grouped[resource_limits.sha256_zip_member(archive, info)].append(info.filename)
    return [paths for paths in grouped.values() if len(paths) > 1]


def render_html(
    *, control: dict[str, Any], register: list[dict[str, str]], selected: list[dict[str, Any]],
    outcomes: dict[str, list[dict[str, str]]], observations: list[dict[str, str]], duplicate_count: int, captured_at: str,
    trace_observations: list[dict[str, str]] | None = None,
) -> str:
    source_batch = str(control.get("batch", "")).strip() or str(control.get("batch_label", ""))
    batch_label = run_control.display_batch_label(source_batch)
    total = len(register)
    stage_counts = [sum(build_candidate_pack.stage_status(row, stage) == "通过" for row in register) for stage, _ in build_candidate_pack.STAGES]
    cards = (
        f'<div class="metric"><span>本批候选人</span><strong>{total}</strong><small>受控登记表中的候选人数量</small></div>'
        f'<div class="metric is-positive"><span>完整通过</span><strong>{len(outcomes["complete_pass"])}</strong><small>三轮面试均记录为通过</small></div>'
        f'<div class="metric is-caution"><span>明确未通过/退出</span><strong>{len(outcomes["not_passed_or_exited"])}</strong><small>含不通过、Stopped、Withdrawn、Not Reached</small></div>'
        f'<div class="metric is-neutral"><span>状态未能确认</span><strong>{len(outcomes["unconfirmed"])}</strong><small>不将 Not Provided 推断为流程结果</small></div>'
    )
    funnel_labels = ("候选人总数", "技术面试通过", "BP 面试通过", "HRD 面试通过")
    funnel_counts = [total, *stage_counts]
    funnel = "".join(
        f'<div class="funnel-step stage-{index}" style="--funnel-width: {count / total * 100:.1f}%">'
        f'<span class="funnel-label">{label}</span><div class="funnel-track"><i></i></div>'
        f'<span class="funnel-value"><strong>{count}</strong><small>{count / total * 100:.0f}%</small></span></div>'
        for index, (label, count) in enumerate(zip(funnel_labels, funnel_counts), 1)
    )
    groups = []
    for recommendation, negative in (("建议录用", False), ("不建议录用", True)):
        items = [item for item in selected if item["current_recommendation"] == recommendation]
        if not items:
            continue
        cards_html = "".join(
            '<article class="opinion-card"><h4>' + html.escape(str(item["candidate"])) + '</h4>'
            '<p><span class="opinion-label">HRD 审核意见：</span>' + html.escape(str(item["current_rationale"])) + '</p></article>'
            for item in items
        )
        groups.append(
            f'<div class="opinion-group{" is-negative" if negative else ""}"><h3>{recommendation}（{len(items)} 人）</h3>{cards_html}</div>'
        )
    confirmed = [item for item in observations if item.get("resolution_status") == "Confirmed Missing"]
    confirmed_text = (
        "；".join(html.escape(f"{item['scope']}：{item['fact']}") for item in confirmed)
        if confirmed else "无 Confirmed Missing 项。"
    )
    duplicate_text = (
        f"保留 {duplicate_count} 组重复提交历史；重复提交仅作追溯，不改变任何流程或 HRD 建议。"
        if duplicate_count else "未发现重复提交组。"
    )
    trace = trace_observations or []
    if trace:
        duplicate_text += (
            f"另有 {len(trace)} 项非阻断材料追溯发现（{build_candidate_pack.observation_summary(trace)}），"
            "均保留原始材料，不改变流程、HRD 建议或 CEO 决策。"
        )
    values = {
        "{{CAPTURED_AT}}": html.escape(captured_at), "{{GENERATED_AT}}": html.escape(utc_now()),
        "{{BATCH_LABEL}}": html.escape(batch_label),
        "{{EXECUTIVE_SUMMARY}}": html.escape(
            f"{batch_label}共 {total} 名候选人；完整通过 {len(outcomes['complete_pass'])} 名，"
            f"明确未通过/退出 {len(outcomes['not_passed_or_exited'])} 名，状态未能确认 {len(outcomes['unconfirmed'])} 名。"
            "HRD 审核意见仅展示在 HRD 面试通过且已完整填写的候选人项中；CEO 最终决策仍未记录。"
        ),
        "{{METRIC_CARDS}}": cards, "{{FUNNEL_STAGES}}": funnel, "{{OPINION_GROUPS}}": "".join(groups),
        "{{CONFIRMED_MISSING}}": confirmed_text, "{{DUPLICATE_HISTORY}}": duplicate_text,
    }
    rendered = TEMPLATE.read_text(encoding="utf-8")
    for token, value in values.items():
        rendered = rendered.replace(token, value)
    if "{{" in rendered or "}}" in rendered:
        raise SystemExit("HRD Candidate Pack HTML 模板替换失败。")
    return rendered


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a CEO package from closed material gate and saved HRD opinions.")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--table-id", default="")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--candidates-json", default="", help="Optional explicit candidate-name JSON array.")
    parser.add_argument("--lark-profile")
    arguments = parser.parse_args()
    output_dir = run_control.require_directory(arguments.output_dir, "output 目录")
    run_control.validate_output_boundary(output_dir, HANDOFF_OUTPUTS, allowed_directories={run_control.PROCESS_RECORDS_DIR})
    control, register = manage_hrd_review.read_handoff(output_dir)
    context = manage_hrd_review.queue_context(
        arguments.base_url, output_dir, arguments.lark_profile, table_id=arguments.table_id,
    )
    verified_live = metadata_unchanged(output_dir, context, arguments.lark_profile)
    context = manage_hrd_review.queue_context(
        arguments.base_url, output_dir, arguments.lark_profile, table_id=arguments.table_id,
        live_records=verified_live,
    )
    cohort = selected_cohort(register, arguments.candidates_json)
    selected = saved_opinions_for_cohort(context, cohort)
    outcomes = classify_outcomes(cohort)
    observations = manage_hrd_review.material_gate(output_dir)
    observations_payload = json.loads((output_dir / "base_intake_observations.json").read_text(encoding="utf-8"))
    trace = build_candidate_pack.load_base_trace_observations(output_dir / "base_intake_observations.json")
    captured_at = str(observations_payload["captured_at"])
    archive = output_dir / "materials.zip"
    duplicates = duplicate_groups(archive)
    archive_hash = run_control.sha256_path(archive)
    completion_time = hrd_completion_time(output_dir, control["batch_id"], required=bool(selected))

    opinion_rows = [
        [item["candidate"], item["current_recommendation"], item["current_rationale"], "已填写"]
        for item in selected
    ]
    consolidated = (
        "# HRD Candidate Opinion Pack\n\n"
        f"- Batch ID: `{control['batch_id']}`\n"
        f"- HRD opinion confirmation: `{completion_time}`\n\n"
        "## Submission Scope\n\n" + markdown_table(
            ["Field", "Value"], [["Candidate count", str(len(cohort))], ["Saved HRD opinions", str(len(selected))],
            ["Complete process passed", str(len(outcomes["complete_pass"]))], ["CEO decision", "Not Provided"],
            ["Materials SHA-256", f"`{archive_hash}`"], ["Nonblocking material trace observations", str(len(trace))]],
        ) + "\n\n## Saved HRD Opinions\n\n" + markdown_table(
            ["Candidate", "HRD recommendation", "HRD rationale", "Opinion status"], opinion_rows,
        ) + "\n\n## Nonblocking Material Traceability Findings\n\n" + markdown_table(
            ["Category", "Scope", "Source-backed fact", "Handling"],
            [[item["category"], item["scope"], item["fact"], item["handling"]] for item in trace]
            or [["None", "Material intake", "No nonblocking traceability finding was generated.", "No action required."]],
        ) + "\n\n## Boundary\n\n- This package contains saved HRD opinions only. It does not create or infer a CEO decision.\n"
    )
    confirmed = [item for item in observations if item.get("resolution_status") == "Confirmed Missing"]
    missing = (
        "# Missing Evidence List\n\n## CEO 摘要状态\n\n" + markdown_table(
            ["项目", "当前状态"], [["状态", "`可提供`"], ["原因", "材料门禁已关闭；Confirmed Missing 仅作为限制披露。"]],
        ) + "\n\n## Confirmed Missing 限制\n\n" + markdown_table(
            ["Scope", "Source-backed fact", "Handling"],
            [[item["scope"], item["fact"], item["handling"]] for item in confirmed]
            or [["None", "No Confirmed Missing item.", "No action required."]],
        ) + "\n"
    )
    with zipfile.ZipFile(archive) as source:
        members = sorted(info.filename for info in resource_limits.validate_zip_infos(source))
    manifest = (
        "# Source Manifest\n\n" + markdown_table(
            ["Field", "Value"], [["Source archive", "`materials.zip`"], ["Archive SHA-256", f"`{archive_hash}`"],
            ["ZIP source members", str(len(members))], ["Duplicate submission groups", str(len(duplicates))],
            ["Nonblocking traceability findings", str(len(trace))]],
        ) + "\n\n## Nonblocking Material Traceability Findings\n\n" + markdown_table(
            ["Category", "Scope", "Source-backed fact", "Handling"],
            [[item["category"], item["scope"], item["fact"], item["handling"]] for item in trace]
            or [["None", "Material intake", "No nonblocking traceability finding was generated.", "No action required."]],
        ) + "\n\n## ZIP Members\n\n" + "\n".join(f"- `{member}`" for member in members) + "\n"
    )
    rendered = render_html(
        control=control, register=cohort, selected=selected, outcomes=outcomes, observations=observations,
        duplicate_count=len(duplicates), captured_at=captured_at, trace_observations=trace,
    )
    write_atomic(output_dir / "consolidated_candidate_pack.md", consolidated)
    write_atomic(output_dir / "missing_evidence_list.md", missing)
    write_atomic(output_dir / "source_manifest.md", manifest)
    write_atomic(output_dir / "candidate_pack.html", rendered)
    summary = ceo_summary_message(control, outcomes, observations)
    write_atomic(output_dir / "ceo_summary_message.md", summary + "\n")
    for name in ("candidate_process_register.xlsx", "base_intake_observations.json", "follow_up_resolution.json", run_control.RUN_CONTROL_FILE):
        (output_dir / name).unlink(missing_ok=True)
    run_control.append_operation_log(
        output_dir, "ceo_package_built", batch_id=control["batch_id"], candidate_count=len(selected),
        complete_process_passed=len(outcomes["complete_pass"]), nonblocking_trace_observations=len(trace),
        metadata_fingerprint_checked=True,
    )
    run_control.refresh_process_records(output_dir)
    final = {item.name for item in output_dir.iterdir() if item.is_file() and item.name != ".DS_Store"}
    if final != FORMAL_OUTPUTS:
        raise SystemExit("CEO 包生成后的输出目录不符合正式交付边界。")
    print(json.dumps({"output_dir": str(output_dir), "batch_id": control["batch_id"], "selected_candidates": [item["候选人姓名"] for item in cohort], "ceo_summary_message": summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
