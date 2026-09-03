#!/usr/bin/env python3
"""Queue, confirm, and verify HRD final-round opinions without CEO decisions."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import build_base_materials
import build_candidate_pack
import run_control


PROPOSAL_FILE = "hrd_opinion_proposal.json"
HRD_FIELDS = ("HRD审核意见", "HRD意见状态", "HRD录用建议")
IDENTITY_FIELDS = ("候选人编号", "候选人姓名", "批次", "HRD面试流程状态")
RECOMMENDATIONS = {"建议录用", "不建议录用"}
COMPLETE_STATUS = "已填写"
TEXT_TYPES = {"text", "plain_text"}
SELECT_TYPES = {"single_select", "select"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_hash(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def cli_json(arguments: list[str], profile: str | None, cwd: Path) -> dict[str, Any]:
    command = ["lark-cli"]
    if profile:
        command.extend(["--profile", profile])
    command.extend(arguments)
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise SystemExit(result.stderr.strip() or "lark-cli did not return JSON") from error
    if result.returncode != 0 or not payload.get("ok"):
        message = payload.get("error", {}).get("message", "unknown lark-cli error")
        raise SystemExit(f"lark-cli 请求失败：{message}")
    return payload


def auth_status(profile: str | None, cwd: Path) -> dict[str, Any]:
    """Read the CLI's status-shaped authentication response, not an API envelope."""
    command = ["lark-cli"]
    if profile:
        command.extend(["--profile", profile])
    command.extend(["auth", "status", "--verify", "--json"])
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise SystemExit(result.stderr.strip() or "lark-cli did not return JSON") from error
    if result.returncode != 0:
        message = payload.get("error", {}).get("message", "unknown lark-cli error") if isinstance(payload, dict) else "unknown lark-cli error"
        raise SystemExit(f"lark-cli 请求失败：{message}")
    if not isinstance(payload, dict):
        raise SystemExit("lark-cli auth status 返回了无效结果。")
    return payload


def find_open_id(value: object) -> str | None:
    if isinstance(value, dict):
        for key in ("open_id", "openId"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.startswith("ou_"):
                return candidate
        for child in value.values():
            found = find_open_id(child)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = find_open_id(child)
            if found:
                return found
    return None


def verified_sender(profile: str | None, cwd: Path) -> str:
    payload = auth_status(profile, cwd)
    identities = payload.get("identities")
    user = identities.get("user") if isinstance(identities, dict) else None
    if (
        payload.get("identity") != "user"
        or payload.get("verified") is not True
        or not isinstance(user, dict)
        or user.get("available") is not True
        or user.get("verified") is not True
    ):
        raise SystemExit("当前用户授权状态未通过验证；不写入 Base。")
    open_id = find_open_id(user)
    if not open_id:
        raise SystemExit("无法从当前用户授权状态取得已验证的 open_id；不写入 Base。")
    return open_id


def hrd_output_boundary(output_dir: Path) -> None:
    run_control.validate_output_boundary(
        output_dir,
        {
            "materials.zip", "candidate_process_register.xlsx", "base_intake_observations.json",
            "follow_up_resolution.json", run_control.RUN_CONTROL_FILE, run_control.OPERATION_LOG_FILE,
            PROPOSAL_FILE,
        },
        allowed_directories={run_control.PROCESS_RECORDS_DIR},
    )


def material_gate(output_dir: Path) -> list[dict[str, str]]:
    observations_path = output_dir / "base_intake_observations.json"
    observations = build_candidate_pack.load_base_observations(observations_path)
    resolution_path = output_dir / build_candidate_pack.FOLLOW_UP_RESOLUTION_FILE
    if resolution_path.is_file():
        observations = build_candidate_pack.apply_confirmed_missing_status(
            observations, resolution_path, observations_path,
        )
    blocked = build_candidate_pack.blocking_observations(observations)
    if blocked:
        raise SystemExit("材料门禁尚未关闭；不能开放 HRD 审核意见。")
    return observations


def read_handoff(output_dir: Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    hrd_output_boundary(output_dir)
    control = run_control.load_run_control(output_dir / run_control.RUN_CONTROL_FILE)
    run_control.validate_handoff(output_dir, control)
    register = build_candidate_pack.register_rows(output_dir / "candidate_process_register.xlsx")
    register_keys = run_control.validate_candidate_keys(register)
    control_keys = [
        (str(item.get("batch")), str(item.get("candidate_id")), str(item.get("candidate")))
        for item in control["candidate_keys"] if isinstance(item, dict)
    ]
    if sorted(register_keys) != sorted(control_keys):
        raise SystemExit("run_control.json 与当前受控登记表不一致；不开放 HRD 审核。")
    material_gate(output_dir)
    return control, register


def resolve_base(url: str, profile: str | None, cwd: Path, table_id: str = "") -> tuple[str, str]:
    return build_base_materials.resolve_table_scope(url, table_id, profile, cwd)


def base_fields(base_token: str, table_id: str, profile: str | None, cwd: Path) -> dict[str, dict[str, Any]]:
    payload = cli_json(
        ["base", "+field-list", "--base-token", base_token, "--table-id", table_id, "--as", "user", "--json"],
        profile, cwd,
    )
    data = build_base_materials.data_of(payload)
    items = data.get("fields") or data.get("items") or data.get("data")
    if not isinstance(items, list):
        raise SystemExit("Base 字段列表不可用。")
    fields = {
        str(item.get("field_name", item.get("name"))): item
        for item in items if isinstance(item, dict) and item.get("field_name", item.get("name"))
    }
    required = set(HRD_FIELDS) | set(IDENTITY_FIELDS)
    missing = sorted(required - set(fields))
    if missing:
        raise SystemExit("Base 缺少 HRD 审核所需字段：" + ", ".join(missing))
    if str(fields["HRD审核意见"].get("type")) not in TEXT_TYPES:
        raise SystemExit("HRD审核意见 必须是可写文本字段。")
    for name in ("HRD意见状态", "HRD录用建议"):
        if str(fields[name].get("type")) not in TEXT_TYPES | SELECT_TYPES:
            raise SystemExit(f"{name} 必须是可写文本或单选字段。")
    for name in IDENTITY_FIELDS:
        if str(fields[name].get("type")) in {"formula", "lookup"}:
            raise SystemExit(f"{name} 不能是公式或 lookup 字段。")
    return fields


def records(base_token: str, table_id: str, profile: str | None, cwd: Path) -> list[tuple[str, dict[str, object]]]:
    all_records: list[tuple[str, dict[str, object]]] = []
    offset = 0
    while True:
        payload = cli_json(
            ["base", "+record-list", "--base-token", base_token, "--table-id", table_id,
             "--limit", "200", "--offset", str(offset), "--as", "user", "--json"],
            profile, cwd,
        )
        data = build_base_materials.data_of(payload)
        values, field_ids, record_ids = data.get("data"), data.get("field_id_list"), data.get("record_id_list")
        if not isinstance(values, list) or not isinstance(field_ids, list) or not isinstance(record_ids, list):
            raise SystemExit("Base 记录列表不可用。")
        if not (len(values) == len(record_ids)):
            raise SystemExit("Base 记录与 ID 数组未对齐。")
        for record_id, row in zip(record_ids, values):
            if not isinstance(record_id, str) or not isinstance(row, list):
                raise SystemExit("Base 记录包含无效项目。")
            all_records.append((record_id, {str(field_id): row[index] if index < len(row) else None for index, field_id in enumerate(field_ids)}))
        if not data.get("has_more"):
            return all_records
        if not values:
            raise SystemExit("Base 分页不连续。")
        offset += len(values)


def field_value(row: dict[str, object], field: dict[str, Any]) -> object:
    field_id = str(field.get("field_id", field.get("id", "")))
    return row.get(field_id)


def text_value(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list) and len(value) == 1:
        return text_value(value[0])
    if isinstance(value, dict):
        name = value.get("name")
        return name.strip() if isinstance(name, str) else ""
    return ""


def select_options(field: dict[str, Any]) -> set[str]:
    options = field.get("options")
    if not isinstance(options, list):
        property_value = field.get("property")
        options = property_value.get("options") if isinstance(property_value, dict) else []
    return {str(item.get("name")) for item in options if isinstance(item, dict) and item.get("name")}


def controlled_value(field: dict[str, Any], value: str) -> object:
    field_type = str(field.get("type"))
    if field_type in TEXT_TYPES:
        return value
    if field_type in SELECT_TYPES:
        options = select_options(field)
        if value not in options:
            raise SystemExit(f"Base 单选字段未配置选项：{value}")
        return [value]
    raise SystemExit("Base 目标字段类型不可写。")


def queue_context(
    url: str, output_dir: Path, profile: str | None, *, table_id: str = "",
    live_records: list[tuple[str, dict[str, object]]] | None = None,
) -> dict[str, Any]:
    control, register = read_handoff(output_dir)
    base_token, selected_table_id = resolve_base(url, profile, output_dir, table_id)
    fields = base_fields(base_token, selected_table_id, profile, output_dir)
    live = live_records if live_records is not None else records(base_token, selected_table_id, profile, output_dir)
    by_key: dict[tuple[str, str, str], tuple[str, dict[str, object]]] = {}
    for record_id, row in live:
        key = tuple(text_value(field_value(row, fields[name])) for name in ("批次", "候选人编号", "候选人姓名"))
        if any(not value for value in key) or key in by_key:
            raise SystemExit("Base 候选人稳定标识缺失或重复；不开放 HRD 审核。")
        by_key[key] = (record_id, row)
    eligible, waiting, complete_flow_candidates = [], [], []
    for item in register:
        key = run_control.candidate_key(item)
        record = by_key.get(key)
        if record is None:
            raise SystemExit("当前 Base 与同次受控登记表候选人不一致；不开放 HRD 审核。")
        record_id, row = record
        status = text_value(field_value(row, fields["HRD面试流程状态"]))
        current_opinion = text_value(field_value(row, fields["HRD审核意见"]))
        current_opinion_status = text_value(field_value(row, fields["HRD意见状态"]))
        current_recommendation = text_value(field_value(row, fields["HRD录用建议"]))
        candidate = {"record_id": record_id, "batch": key[0], "candidate_id": key[1], "candidate": key[2]}
        complete_flow = all(
            build_candidate_pack.stage_status(item, stage) == "通过"
            for stage, _ in build_candidate_pack.STAGES
        )
        if complete_flow and status == "通过":
            candidate.update({
                "opinion_status": COMPLETE_STATUS if (
                    current_opinion_status == COMPLETE_STATUS
                    and current_opinion
                    and current_recommendation in RECOMMENDATIONS
                ) else "未填写",
                "current_recommendation": current_recommendation or "Not Provided",
                "current_rationale": current_opinion or "Not Provided",
            })
            eligible.append(candidate)
            complete_flow_candidates.append(key[2])
        elif status in {"", "Not Provided", "Not Reached", "Unknown", "未开始", "进行中", "Decision Blocked", "未通过", "Stopped", "Withdrawn", "通过"}:
            waiting.append({
                "candidate": key[2], "hrd_interview_status": status or "Not Provided",
                "complete_flow": complete_flow,
            })
        else:
            raise SystemExit(f"{key[2]} 的 HRD面试流程状态不在受控枚举中：{status}")
    return {
        "schema_version": 1,
        "batch_id": control["batch_id"],
        "candidate_keys": control.get("candidate_keys"),
        "base": {"base_token": base_token, "table_id": selected_table_id},
        "fields": fields,
        "eligible": eligible,
        "complete_flow_candidates": complete_flow_candidates,
        "pending_opinion_candidates": [item["candidate"] for item in eligible if item["opinion_status"] != COMPLETE_STATUS],
        "ready_for_ceo": all(item["opinion_status"] == COMPLETE_STATUS for item in eligible),
        "waiting": waiting,
    }


def validate_opinions(raw: object, eligible: list[dict[str, Any]]) -> list[dict[str, str]]:
    if not isinstance(raw, list) or not raw:
        raise SystemExit("至少需要一条 HRD 审核意见。")
    by_candidate = {str(item["candidate"]): item for item in eligible}
    selected: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise SystemExit("HRD 审核意见必须是对象数组。")
        candidate = str(item.get("candidate", "")).strip()
        recommendation = str(item.get("recommendation", "")).strip()
        rationale = str(item.get("rationale", "")).strip()
        if candidate not in by_candidate or candidate in seen:
            raise SystemExit("HRD 意见候选人必须唯一且来自当前 HRD 通过队列。")
        if recommendation not in RECOMMENDATIONS:
            raise SystemExit("HRD录用建议 只能是 建议录用 或 不建议录用。")
        if len("".join(rationale.split())) < 6:
            raise SystemExit("每条 HRD 意见必须包含至少一句简短理由。")
        seen.add(candidate)
        selected.append({
            "record_id": str(by_candidate[candidate]["record_id"]), "candidate": candidate,
            "recommendation": recommendation, "rationale": rationale,
        })
    return selected


def queue(arguments: argparse.Namespace) -> int:
    output_dir = run_control.require_directory(arguments.output_dir, "output 目录")
    context = queue_context(arguments.base_url, output_dir, arguments.lark_profile, table_id=arguments.table_id)
    print(json.dumps({
        "batch_id": context["batch_id"], "material_gate": "Closed",
        "complete_flow_candidates": context["complete_flow_candidates"],
        "eligible_candidates": context["eligible"], "pending_opinion_candidates": context["pending_opinion_candidates"],
        "waiting_or_not_applicable": context["waiting"], "ready_for_ceo": context["ready_for_ceo"],
        "next_action": (
            "All required HRD opinions are recorded; build the formal review package."
            if context["ready_for_ceo"] else
            "Collect one recommendation and rationale for every pending candidate, then create a proposal for confirmation."
        ),
    }, ensure_ascii=False, indent=2))
    return 0


def saved_opinion_fingerprint(context: dict[str, Any]) -> str:
    if context["pending_opinion_candidates"]:
        raise SystemExit("仍存在未填写的 HRD 意见；不能重新核验已保存意见。")
    opinions = [
        {
            "candidate_id": item["candidate_id"],
            "candidate": item["candidate"],
            "recommendation": item["current_recommendation"],
            "rationale": item["current_rationale"],
            "opinion_status": item["opinion_status"],
        }
        for item in context["eligible"]
    ]
    return canonical_hash(opinions)


def saved_audit_event(output_dir: Path, batch_id: str) -> dict[str, Any]:
    log = output_dir / run_control.OPERATION_LOG_FILE
    if not log.is_file():
        raise SystemExit("原交接缺少 HRD SAVE 审计记录；不能重新核验。")
    latest: dict[str, Any] | None = None
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            raise SystemExit("原交接的 operation_log.jsonl 不是有效审计记录。") from None
        if item.get("event") == "hrd_opinions_saved" and item.get("batch_id") == batch_id:
            latest = item
    if latest is None or not isinstance(latest.get("at"), str):
        raise SystemExit("原交接缺少同批次的 HRD SAVE 审计记录；不能重新核验。")
    return latest


def revalidate_saved(arguments: argparse.Namespace) -> int:
    output_dir = run_control.require_directory(arguments.output_dir, "output 目录")
    source_dir = run_control.require_directory(arguments.source_output_dir, "source output 目录")
    control, _ = read_handoff(output_dir)
    source_control, _ = read_handoff(source_dir)
    if control["batch"] != source_control["batch"] or control["candidate_keys"] != source_control["candidate_keys"]:
        raise SystemExit("原交接与当前交接的批次或候选人范围不一致；不能重新核验。")
    source_save = saved_audit_event(source_dir, source_control["batch_id"])
    context = queue_context(arguments.base_url, output_dir, arguments.lark_profile, table_id=arguments.table_id)
    fingerprint = saved_opinion_fingerprint(context)
    log = output_dir / run_control.OPERATION_LOG_FILE
    if log.is_file():
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                raise SystemExit("operation_log.jsonl 不是有效审计记录。") from None
            if (
                item.get("event") == "hrd_opinions_revalidated"
                and item.get("batch_id") == control["batch_id"]
                and item.get("source_batch_id") == source_control["batch_id"]
                and item.get("opinion_fingerprint") == fingerprint
            ):
                print(json.dumps({"revalidated": True, "already_recorded": True, "candidate_count": len(context["eligible"])}, ensure_ascii=False))
                return 0
    run_control.append_operation_log(
        output_dir,
        "hrd_opinions_revalidated",
        batch_id=control["batch_id"],
        source_batch_id=source_control["batch_id"],
        source_save_at=source_save["at"],
        source_proposal_id=source_save.get("proposal_id", "Not Provided"),
        candidate_count=len(context["eligible"]),
        opinion_fingerprint=fingerprint,
    )
    print(json.dumps({"revalidated": True, "already_recorded": False, "candidate_count": len(context["eligible"])}, ensure_ascii=False))
    return 0


def propose(arguments: argparse.Namespace) -> int:
    output_dir = run_control.require_directory(arguments.output_dir, "output 目录")
    proposal_path = output_dir / PROPOSAL_FILE
    if proposal_path.exists():
        raise SystemExit("已有待确认的 HRD 意见提案；请确认或显式废弃后再创建。")
    context = queue_context(arguments.base_url, output_dir, arguments.lark_profile, table_id=arguments.table_id)
    sender = verified_sender(arguments.lark_profile, output_dir)
    try:
        raw = json.loads(arguments.opinions_json)
    except json.JSONDecodeError as error:
        raise SystemExit("opinions-json 必须是有效 JSON 数组。") from error
    opinions = validate_opinions(raw, context["eligible"])
    fields = context["fields"]
    proposal = {
        "schema_version": 1, "created_at": utc_now(), "batch_id": context["batch_id"],
        "base": context["base"], "sender_open_id": sender,
        "field_names": list(HRD_FIELDS), "opinions": opinions,
    }
    proposal["proposal_id"] = canonical_hash(proposal)
    proposal_path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "proposal_id": proposal["proposal_id"], "batch_id": proposal["batch_id"], "sender_open_id": sender,
        "opinions": opinions,
        "confirmation_required": "Run confirm with this proposal_id and --confirm SAVE after the HRD reviews this exact summary.",
    }, ensure_ascii=False, indent=2))
    return 0


def confirm(arguments: argparse.Namespace) -> int:
    output_dir = run_control.require_directory(arguments.output_dir, "output 目录")
    hrd_output_boundary(output_dir)
    proposal_path = output_dir / PROPOSAL_FILE
    try:
        proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise SystemExit("没有待确认的 HRD 意见提案。") from error
    expected_id = proposal.get("proposal_id")
    unsigned = {key: value for key, value in proposal.items() if key != "proposal_id"}
    if not isinstance(expected_id, str) or expected_id != canonical_hash(unsigned) or arguments.proposal_id != expected_id:
        raise SystemExit("HRD 意见提案已变化或确认标识不匹配；不写入 Base。")
    if arguments.confirm != "SAVE":
        raise SystemExit("必须使用 --confirm SAVE 明确确认 HRD Base 写入。")
    sender = verified_sender(arguments.lark_profile, output_dir)
    if sender != proposal.get("sender_open_id"):
        raise SystemExit("当前已验证发送者与提案创建者不一致；不写入 Base。")
    control, _ = read_handoff(output_dir)
    base = proposal.get("base")
    if not isinstance(base, dict) or not isinstance(base.get("base_token"), str) or not isinstance(base.get("table_id"), str):
        raise SystemExit("HRD 提案缺少 Base 绑定信息。")
    fields = base_fields(base["base_token"], base["table_id"], arguments.lark_profile, output_dir)
    live = records(base["base_token"], base["table_id"], arguments.lark_profile, output_dir)
    live_by_id = {record_id: row for record_id, row in live}
    updates: dict[str, dict[str, object]] = {}
    for opinion in proposal.get("opinions", []):
        if not isinstance(opinion, dict) or opinion.get("record_id") not in live_by_id:
            raise SystemExit("HRD 提案候选人已不在当前 Base；不写入。")
        row = live_by_id[str(opinion["record_id"])]
        if text_value(field_value(row, fields["HRD面试流程状态"])) != "通过":
            raise SystemExit(f"{opinion.get('candidate', 'Not Provided')} 不再是 HRD 面试通过候选人；不写入。")
        recommendation = str(opinion.get("recommendation", ""))
        rationale = str(opinion.get("rationale", ""))
        if recommendation not in RECOMMENDATIONS or len("".join(rationale.split())) < 6:
            raise SystemExit("HRD 提案不符合推荐方向或理由要求。")
        updates[str(opinion["record_id"])] = {
            "HRD审核意见": controlled_value(fields["HRD审核意见"], rationale),
            "HRD意见状态": controlled_value(fields["HRD意见状态"], COMPLETE_STATUS),
            "HRD录用建议": controlled_value(fields["HRD录用建议"], recommendation),
        }
    response = cli_json(
        ["base", "+record-batch-update", "--base-token", base["base_token"], "--table-id", base["table_id"],
         "--as", "user", "--json", json.dumps({"update_records": updates}, ensure_ascii=False)],
        arguments.lark_profile, output_dir,
    )
    ignored = build_base_materials.data_of(response).get("ignored_fields")
    if ignored:
        raise SystemExit("Base 忽略了部分 HRD 字段写入；请检查字段权限或类型。")
    verified = {record_id: row for record_id, row in records(base["base_token"], base["table_id"], arguments.lark_profile, output_dir)}
    for opinion in proposal["opinions"]:
        row = verified.get(opinion["record_id"])
        if row is None or text_value(field_value(row, fields["HRD审核意见"])) != opinion["rationale"]:
            raise SystemExit("HRD 意见写入后回读不一致；保留提案并停止。")
        if text_value(field_value(row, fields["HRD录用建议"])) != opinion["recommendation"]:
            raise SystemExit("HRD 建议写入后回读不一致；保留提案并停止。")
        if text_value(field_value(row, fields["HRD意见状态"])) != COMPLETE_STATUS:
            raise SystemExit("HRD 意见状态写入后回读不一致；保留提案并停止。")
    proposal_path.unlink()
    run_control.append_operation_log(
        output_dir, "hrd_opinions_saved", batch_id=control["batch_id"], sender_open_id=sender,
        proposal_id=expected_id, opinions_fingerprint=canonical_hash(proposal["opinions"]),
        saved_fields=list(HRD_FIELDS), candidate_count=len(updates),
    )
    print(json.dumps({"saved": True, "batch_id": control["batch_id"], "candidate_count": len(updates)}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage confirmed HRD opinions for a D13 Base handoff.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("queue", "propose", "revalidate-saved"):
        child = subparsers.add_parser(name)
        child.add_argument("--base-url", required=True)
        child.add_argument("--table-id", default="")
        child.add_argument("--output-dir", required=True, type=Path)
        child.add_argument("--lark-profile")
        if name == "propose":
            child.add_argument("--opinions-json", required=True)
        if name == "revalidate-saved":
            child.add_argument("--source-output-dir", required=True, type=Path)
    confirm_parser = subparsers.add_parser("confirm")
    confirm_parser.add_argument("--output-dir", required=True, type=Path)
    confirm_parser.add_argument("--proposal-id", required=True)
    confirm_parser.add_argument("--confirm", required=True)
    confirm_parser.add_argument("--lark-profile")
    arguments = parser.parse_args()
    return {
        "queue": queue,
        "propose": propose,
        "revalidate-saved": revalidate_saved,
        "confirm": confirm,
    }[arguments.command](arguments)


if __name__ == "__main__":
    raise SystemExit(main())
