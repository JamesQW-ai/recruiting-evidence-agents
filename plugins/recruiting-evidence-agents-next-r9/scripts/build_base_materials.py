#!/usr/bin/env python3
"""Read an authorized Feishu Base into a replayable, local material intake."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import build_materials
import resource_limits
import run_control


ATTACHMENT_FIELD = "attachment"
SHARED_PROMPT_FIELD = "技术题目"
OWNER_FIELDS_BY_MATERIAL = {
    "简历材料": "简历负责人",
    "技术作业评估": "技术作业负责人",
    "BP面试材料": "BP面试负责人",
    "技术面试材料": "技术面试负责人",
    "HRD面试材料": "HRD面试负责人",
}
XML_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
XLSX_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
CANONICAL_FIELDS = (
    "候选人编号", "候选人姓名", "性别", "学校", "批次",
    "简历材料", "简历提交时间", "技术题目", "技术题目发布时间",
    "技术作业（仓库链接）", "技术作业提交时间", "技术作业评估", "技术作业评估状态",
    "技术面试流程状态", "技术面试材料", "技术面试发生时间",
    "BP面试流程状态", "BP面试材料", "BP面试发生时间",
    "HRD面试流程状态", "HRD面试材料", "HRD面试发生时间",
    "CEO最终决策", "当前流程阶段", "当前流程状态",
    "处理状态",
)
METADATA_FINGERPRINT_FIELDS = tuple(name for name in CANONICAL_FIELDS if name != "处理状态")
PROCESS_STATUSES = {"已处理", "未处理", "待补全", "处理中", "阻塞", "Unknown", "Not Provided"}
ROLE_FIELD = {
    "简历材料": "Resume",
    "技术作业评估": "Technical Assignment Evaluation",
    "技术面试材料": "Technical Interview",
    "BP面试材料": "BP Interview",
    "HRD面试材料": "HRD Interview",
}
INTERVIEW_MATERIAL_FIELDS = {"技术面试材料", "BP面试材料", "HRD面试材料"}
AUDIO_SUFFIXES = {".aac", ".m4a", ".mp3", ".wav"}
TRANSCRIPT_MARKERS = ("转写", "转录", "记录", "transcript", "transcription")
ATTACHMENT_IDENTITY_FIELDS = ("record_id", "field_id", "ordinal", "file_token")


def metadata_fingerprint(records: list[tuple[str, dict[str, object]]], field_by_id: dict[str, dict[str, object]]) -> str:
    """Bind source material/process facts, excluding plugin-managed processing status."""
    selected = {
        name: field_id
        for field_id, field in field_by_id.items()
        if (name := str(field.get("field_name") or field.get("name") or "")) in METADATA_FINGERPRINT_FIELDS
    }
    missing = set(METADATA_FINGERPRINT_FIELDS) - set(selected)
    if missing:
        raise SystemExit("Base table is missing required fields: " + ", ".join(sorted(missing)))
    payload = {
        "fields": {name: selected[name] for name in sorted(selected)},
        "records": [
            {
                "record_id": record_id,
                "values": {name: values.get(selected[name]) for name in sorted(selected)},
            }
            for record_id, values in sorted(records)
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def recorded(value: str) -> bool:
    return value not in {"", "Not Provided", "Not Reached", "Unknown"}


def expected_material_fields(row: dict[str, str]) -> list[str]:
    return list(dict.fromkeys(field for field, _ in expected_material_components(row)))


def expected_material_components(row: dict[str, str]) -> list[tuple[str, str]]:
    expected = [("简历材料", "材料")]
    if row.get("技术作业评估状态") == "通过":
        expected.append(("技术作业评估", "材料"))
    for stage in ("技术面试", "BP面试", "HRD面试"):
        if recorded(row.get(f"{stage}发生时间", "")):
            expected.extend(((f"{stage}材料", "面试录音"), (f"{stage}材料", "面试转写")))
    return expected


def material_component(field: str, original_name: str) -> str:
    if field not in INTERVIEW_MATERIAL_FIELDS:
        return "材料"
    lowered = original_name.casefold()
    if Path(original_name).suffix.casefold() in AUDIO_SUFFIXES:
        return "面试录音"
    if any(marker in lowered for marker in TRANSCRIPT_MARKERS):
        return "面试转写"
    return "其他"


def missing_material_observation(candidate: str, field: str, component: str) -> dict[str, str]:
    if component == "材料":
        fact = "No Base attachment can be uniquely mapped to this candidate and material field."
        handling = "Keep the recorded process state; request the missing material without substituting another file."
        scope = f"{candidate} / {field}"
    else:
        fact = f"The recorded interview has no uniquely mapped {component} attachment in Base."
        handling = f"Keep the recorded process state; request the missing {component} in the matching Base material field."
        scope = f"{candidate} / {field} / {component}"
    return {"category": "Missing Material", "scope": scope, "fact": fact, "handling": handling}


def material_observations(register_rows: list[dict[str, str]], attachments: list[dict[str, object]], mappings: list[dict[str, object]]) -> list[dict[str, str]]:
    attachment_by_path = {str(item["snapshot_path"]): item for item in attachments}
    observations: list[dict[str, str]] = []
    mapped_components: set[tuple[str, str, str]] = set()
    invalid_components: set[tuple[str, str, str]] = set()

    # Establish usable components first. An invalid extra file must not create a
    # supplement request when the Base field already contains a usable copy.
    for mapping in mappings:
        attachment = attachment_by_path.get(str(mapping.get("original_path")))
        if attachment is None or str(mapping.get("readability")) in {"PDF Invalid", "PDF Unreadable"}:
            continue
        field = str(attachment["field"])
        resolved = str(mapping.get("resolved_candidate"))
        if resolved not in {"Unknown", "Not Applicable"} and field in ROLE_FIELD:
            component = material_component(field, str(attachment["original_name"]))
            if component != "其他":
                mapped_components.add((resolved, field, component))
    for mapping in mappings:
        attachment = attachment_by_path.get(str(mapping.get("original_path")))
        if attachment is None:
            continue
        candidate = str(attachment["candidate"])
        field = str(attachment["field"])
        classification = str(mapping.get("classification"))
        resolved = str(mapping.get("resolved_candidate"))
        if classification in {"Unmapped", "Ambiguous"}:
            observations.append({"category": "Unmapped Material", "scope": f"{candidate} / {field}", "fact": f"Base attachment `{attachment['original_name']}` cannot be uniquely mapped to a candidate from its readable content and filename.", "handling": "Keep the source bytes outside candidate folders; do not assign it to a candidate."})
        elif str(mapping.get("readability")) in {"PDF Invalid", "PDF Unreadable"}:
            component = material_component(field, str(attachment["original_name"]))
            if component != "其他":
                field_component = (candidate, field, component)
                if field_component not in mapped_components:
                    invalid_components.add(field_component)
                    observations.append({"category": "Invalid Material", "scope": f"{candidate} / {field} / {component}", "fact": f"Base attachment `{attachment['original_name']}` cannot be opened as a readable PDF and no usable {component} exists in this Base field.", "handling": f"Request a readable replacement for {component} in the matching Base material field."})
    for row in register_rows:
        candidate = row.get("候选人姓名", "Not Provided") or "Not Provided"
        for field, component in expected_material_components(row):
            if (candidate, field, component) not in mapped_components and (candidate, field, component) not in invalid_components:
                observations.append(missing_material_observation(candidate, field, component))
    unique_observations: list[dict[str, str]] = []
    seen_observations: set[tuple[str, str, str, str]] = set()
    for item in observations:
        fingerprint = (item["category"], item["scope"], item["fact"], item["handling"])
        if fingerprint not in seen_observations:
            seen_observations.add(fingerprint)
            unique_observations.append(item)
    return unique_observations


def trace_observations(attachments: list[dict[str, object]], mappings: list[dict[str, object]]) -> list[dict[str, str]]:
    """Report retained traceability facts without turning them into material blockers."""
    attachment_by_path = {str(item["snapshot_path"]): item for item in attachments}
    included: list[tuple[dict[str, object], dict[str, object]]] = []
    for mapping in mappings:
        attachment = attachment_by_path.get(str(mapping.get("original_path")))
        if attachment is None or mapping.get("package_status") != "Included":
            continue
        resolved = str(mapping.get("resolved_candidate", ""))
        if resolved in {"", "Unknown", "Not Applicable"}:
            continue
        included.append((attachment, mapping))

    observations: list[dict[str, str]] = []
    misplaced: dict[tuple[str, str, str], list[dict[str, object]]] = {}
    by_hash: dict[str, list[tuple[dict[str, object], dict[str, object]]]] = {}
    versions: dict[tuple[str, str], list[tuple[dict[str, object], dict[str, object]]]] = {}
    valid_components: set[tuple[str, str, str]] = set()
    invalid_extras: list[tuple[dict[str, object], dict[str, object], str]] = []
    for attachment, mapping in included:
        candidate = str(attachment["candidate"])
        field = str(attachment["field"])
        resolved = str(mapping["resolved_candidate"])
        component = material_component(field, str(attachment["original_name"]))
        if (
            field in ROLE_FIELD
            and component != "其他"
            and str(mapping.get("readability")) not in {"PDF Invalid", "PDF Unreadable"}
        ):
            valid_components.add((resolved, field, component))
    for attachment, mapping in included:
        candidate = str(attachment["candidate"])
        field = str(attachment["field"])
        component = material_component(field, str(attachment["original_name"]))
        if (
            field in ROLE_FIELD
            and component != "其他"
            and str(mapping.get("readability")) in {"PDF Invalid", "PDF Unreadable"}
            and (candidate, field, component) in valid_components
        ):
            invalid_extras.append((attachment, mapping, component))
    for attachment, mapping in included:
        candidate = str(attachment["candidate"])
        field = str(attachment["field"])
        resolved = str(mapping["resolved_candidate"])
        readable_identity = str(mapping.get("readability")) not in {"PDF Invalid", "PDF Unreadable"}
        if readable_identity and resolved != candidate:
            misplaced.setdefault((candidate, field, resolved), []).append(mapping)
        digest = str(mapping.get("sha256", ""))
        if digest:
            by_hash.setdefault(digest, []).append((attachment, mapping))
        if field == "技术作业评估" and readable_identity and resolved == candidate:
            versions.setdefault((candidate, field), []).append((attachment, mapping))

    for (candidate, field, resolved), group in sorted(misplaced.items()):
        observations.append({
            "category": "Misplaced Material",
            "scope": f"{candidate} / {field}",
            "fact": f"{len(group)} 份材料在该字段中，但其唯一身份映射为 {resolved}。",
            "handling": f"保留原始字节和文件名，并按实际内容归入 {resolved} 的候选人材料目录；该错放事实仅作追溯，不触发催办、清理或人工确认。",
        })
    for attachment, _, component in invalid_extras:
        observations.append({
            "category": "Invalid Extra Material",
            "scope": f"{attachment['candidate']} / {attachment['field']} / {component}",
            "fact": f"Base attachment `{attachment['original_name']}` cannot be opened as a readable PDF, but this field already has a usable {component}.",
            "handling": "Delete the invalid extra attachment only; do not request a supplement or change the recorded process state.",
        })
    for group in sorted(by_hash.values(), key=lambda items: sorted(str(item[0]["candidate"]) for item in items)):
        if len(group) < 2:
            continue
        if all(str(mapping.get("resolved_candidate")) != str(attachment["candidate"]) for attachment, mapping in group):
            continue
        locations = sorted({f"{item[0]['candidate']} / {item[0]['field']}" for item in group})
        observations.append({
            "category": "Duplicate Submission",
            "scope": "；".join(locations),
            "fact": f"{len(group)} 份材料的字节与 SHA-256 完全相同。",
            "handling": "保留所有副本作为追溯历史；不要求删除，不生成催办，也不触发材料门禁。",
        })
    for (candidate, field), group in sorted(versions.items()):
        hashes = {str(mapping.get("sha256", "")) for _, mapping in group}
        if len(hashes) < 2:
            continue
        observations.append({
            "category": "Multiple Material Versions",
            "scope": f"{candidate} / {field}",
            "fact": f"该字段保留 {len(group)} 份材料，其中有 {len(hashes)} 个不同字节版本。",
            "handling": "保留全部版本；不自动判断哪一份更完整或更优。",
        })
    return observations


def write_process_records(
    output_dir: Path, manifest: dict[str, object], mappings: list[dict[str, object]], observations: dict[str, object],
) -> None:
    """Persist a compact diagnostic record without Base/attachment tokens or owner identities."""
    process_dir = output_dir / run_control.PROCESS_RECORDS_DIR
    process_dir.mkdir(exist_ok=False)
    summary = {
        "schema_version": 1,
        "captured_at": manifest["captured_at"],
        "batch": manifest["selection"]["batch"],
        "candidate_scope": manifest["selection"].get("scope", "Full Batch"),
        "record_count": manifest["record_count"],
        "attachment_entries": len(manifest["attachments"]),
        "refresh": manifest["refresh"],
        "active_observation_count": len(observations["observations"]),
        "trace_observation_count": len(observations["trace_observations"]),
        "base_written": False,
    }
    safe_mapping_keys = (
        "original_name", "candidate", "field", "resolved_candidate", "classification", "identity_basis",
        "mapping_status", "readability", "sha256", "package_status", "packaged_path", "material_role",
    )
    mapped = [
        {key: item[key] for key in safe_mapping_keys if key in item}
        for item in mappings
    ]
    (process_dir / "运行摘要.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    (process_dir / "材料映射.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in mapped), encoding="utf-8",
    )
    (process_dir / "材料核验.json").write_text(
        json.dumps(
            {"schema_version": 1, "captured_at": manifest["captured_at"], "observations": observations["observations"],
             "trace_observations": observations["trace_observations"]},
            ensure_ascii=False, indent=2, sort_keys=True,
        ) + "\n", encoding="utf-8",
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def cli_json(arguments: list[str], profile: str | None, cwd: Path, attempts: int = 1) -> dict[str, object]:
    command = ["lark-cli"]
    if profile:
        command.extend(["--profile", profile])
    command.extend(arguments)
    failure = "unknown lark-cli error"
    for attempt in range(attempts):
        result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            payload = None
            failure = result.stderr.strip() or "empty or invalid JSON response"
        else:
            if result.returncode == 0 and payload.get("ok"):
                return payload
            failure = payload.get("error", {}).get("message", "unknown lark-cli error") if isinstance(payload, dict) else "unknown lark-cli error"
        if attempt + 1 < attempts:
            time.sleep(1)
    raise SystemExit(f"lark-cli {arguments[1]} failed after {attempts} attempt(s): {failure}")


def data_of(payload: dict[str, object]) -> dict[str, object]:
    outer = payload.get("data")
    return outer if isinstance(outer, dict) else {}


def value_by_key(value: object, key: str) -> object | None:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = value_by_key(child, key)
            if found is not None:
                return found
    if isinstance(value, list):
        for child in value:
            found = value_by_key(child, key)
            if found is not None:
                return found
    return None


def resolve_base_url(url: str, *, require_table: bool = True) -> tuple[str, str]:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    try:
        base_token = parts[parts.index("base") + 1]
    except (ValueError, IndexError) as error:
        raise SystemExit("Base URL must contain /base/<base-token>") from error
    table_id = parse_qs(parsed.query).get("table", [""])[0]
    if not table_id and require_table:
        raise SystemExit("Base URL must include a table query parameter")
    return base_token, table_id


def select_table_id(url_table_id: str, requested_table_id: str) -> str:
    requested = requested_table_id.strip()
    if requested and not requested.startswith("tbl"):
        raise SystemExit("--table-id 必须是以 tbl 开头的 Base 数据表 ID。")
    if requested and url_table_id and requested != url_table_id:
        raise SystemExit("--table-id 与 Base URL 中的 table 参数不一致。")
    selected = requested or url_table_id
    if not selected:
        raise SystemExit("Base URL 未指定数据表；请先选择数据表并传入 --table-id。")
    return selected


def batch_groups(
    records: list[tuple[str, dict[str, object]]],
    field_by_id: dict[str, dict[str, object]],
) -> dict[str, list[tuple[str, dict[str, object]]]]:
    batch_field_id = next(
        (field_id for field_id, field in field_by_id.items() if field.get("field_name") == "批次"),
        None,
    )
    if batch_field_id is None:
        raise SystemExit("Base table is missing required field: 批次")
    grouped: dict[str, list[tuple[str, dict[str, object]]]] = {}
    for record_id, values in records:
        batch = date_only(values.get(batch_field_id)).strip()
        if not batch or batch in {"Unknown", "Not Provided"}:
            raise SystemExit("Base 记录的 批次 为空或未提供；不能建立隔离交接。")
        grouped.setdefault(batch, []).append((record_id, values))
    return grouped


def batch_choices(
    records: list[tuple[str, dict[str, object]]],
    field_by_id: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    return [
        {"batch": batch, "record_count": len(group)}
        for batch, group in sorted(batch_groups(records, field_by_id).items())
    ]


def batch_discovery_payload(table_id: str, choices: list[dict[str, object]]) -> dict[str, object]:
    if len(choices) == 1:
        batch = str(choices[0]["batch"])
        next_action = f"请确认是否处理 {batch}；确认后使用 --table-id {table_id} --batch {batch} 创建交接。"
    else:
        next_action = "请从上述批次中选择一个精确值；确认后使用 --table-id 和 --batch 创建交接。"
    return {
        "table_id": table_id,
        "batches": choices,
        "selection_required": len(choices) > 1,
        "confirmation_required": True,
        "next_action": next_action,
    }


def select_batch_records(
    records: list[tuple[str, dict[str, object]]],
    field_by_id: dict[str, dict[str, object]],
    requested_batch: str,
) -> tuple[list[tuple[str, dict[str, object]]], str]:
    grouped = batch_groups(records, field_by_id)
    requested = requested_batch.strip()
    if not requested:
        raise SystemExit("请先确认要处理的批次并传入 --batch。")
    selected = grouped.get(requested, [])
    if not selected:
        raise SystemExit(f"Base 数据表中不存在指定批次：{requested}")
    return selected, requested


def candidate_identity(
    record_id: str, values: dict[str, object], field_by_id: dict[str, dict[str, object]],
) -> tuple[str, str, str]:
    candidate_id_field = next(
        (field_id for field_id, field in field_by_id.items() if field.get("field_name") == "候选人编号"),
        None,
    )
    candidate_name_field = next(
        (field_id for field_id, field in field_by_id.items() if field.get("field_name") == "候选人姓名"),
        None,
    )
    if candidate_id_field is None or candidate_name_field is None:
        raise SystemExit("Base 缺少候选人编号或候选人姓名；不能建立增量候选人范围。")
    candidate_id = date_only(values.get(candidate_id_field)).strip()
    candidate = date_only(values.get(candidate_name_field)).strip()
    if not candidate_id or candidate_id in {"Unknown", "Not Provided"} or not candidate:
        raise SystemExit("Base 新增候选人缺少稳定编号或姓名；不能建立增量候选人范围。")
    return record_id, candidate_id, candidate


def load_incremental_baseline(
    snapshot_dir: Path, base_token: str, table_id: str, batch: str,
) -> dict[str, tuple[str, str]]:
    manifest_path = snapshot_dir / "base_snapshot_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise SystemExit(f"增量基线缺少 base_snapshot_manifest.json：{snapshot_dir}") from error
    except json.JSONDecodeError as error:
        raise SystemExit("增量基线的 base_snapshot_manifest.json 不是有效 JSON。") from error
    if not isinstance(manifest, dict) or not isinstance(manifest.get("records"), list):
        raise SystemExit("增量基线缺少受控候选人记录；不能建立增量范围。")
    base = manifest.get("base")
    selection = manifest.get("selection")
    if not isinstance(base, dict) or base.get("base_token") != base_token or base.get("table_id") != table_id:
        raise SystemExit("增量基线不属于当前 Base 或表；不能跨来源建立候选人范围。")
    if not isinstance(selection, dict) or selection.get("table_id") != table_id or selection.get("batch") != batch:
        raise SystemExit("增量基线不属于当前批次；不能跨批次建立候选人范围。")
    baseline: dict[str, tuple[str, str]] = {}
    for item in manifest["records"]:
        if not isinstance(item, dict):
            raise SystemExit("增量基线包含无效候选人记录。")
        record_id = item.get("record_id")
        candidate_id = item.get("candidate_id")
        candidate = item.get("candidate")
        if not all(isinstance(value, str) and value and value not in {"Unknown", "Not Provided"} for value in (record_id, candidate_id, candidate)):
            raise SystemExit("增量基线候选人缺少稳定身份；不能建立候选人范围。")
        if candidate_id in baseline:
            raise SystemExit("增量基线存在重复候选人编号；不能建立候选人范围。")
        baseline[candidate_id] = (record_id, candidate)
    return baseline


def select_incremental_candidate_records(
    records: list[tuple[str, dict[str, object]]], field_by_id: dict[str, dict[str, object]],
    baseline: dict[str, tuple[str, str]],
) -> tuple[list[tuple[str, dict[str, object]]], list[str]]:
    current: dict[str, tuple[str, dict[str, object], str]] = {}
    for record_id, values in records:
        _, candidate_id, candidate = candidate_identity(record_id, values, field_by_id)
        if candidate_id in current:
            raise SystemExit("当前批次存在重复候选人编号；不能建立增量候选人范围。")
        current[candidate_id] = (record_id, values, candidate)
    incremental: list[tuple[str, dict[str, object]]] = []
    candidate_ids: list[str] = []
    for candidate_id, (record_id, values, candidate) in current.items():
        prior = baseline.get(candidate_id)
        if prior is None:
            incremental.append((record_id, values))
            candidate_ids.append(candidate_id)
        elif prior != (record_id, candidate):
            raise SystemExit("已汇报候选人的稳定身份已变化；请创建候选人更新/更正包。")
    if not incremental:
        raise SystemExit("当前批次没有新增候选人；不重新生成整批汇报。")
    return incremental, sorted(candidate_ids)


def resolved_table_id(data: dict[str, object]) -> object | None:
    """Support current `block_id` and legacy `table_id` URL-resolution payloads."""
    return data.get("table_id", data.get("block_id"))


def base_block_items(data: dict[str, object]) -> list[dict[str, object]]:
    raw = data.get("blocks") or data.get("items") or data.get("data")
    if not isinstance(raw, list):
        raise SystemExit("Base block list did not return block items")
    return [item for item in raw if isinstance(item, dict)]


def selectable_tables(blocks: list[dict[str, object]]) -> list[dict[str, object]]:
    tables = []
    for block in blocks:
        table_id = str(block.get("block_id", block.get("table_id", block.get("id", ""))))
        if table_id.startswith("tbl") and block.get("block_type", block.get("type")) == "table":
            tables.append({
                "table_id": table_id,
                "name": str(block.get("name", "Not Provided")),
                "records_count": block.get("records_count", "Not Provided"),
            })
    return sorted(tables, key=lambda item: (str(item["name"]), str(item["table_id"])))


def resolve_table_scope(url: str, requested_table_id: str, profile: str | None, cwd: Path) -> tuple[str, str]:
    base_token, url_table_id = resolve_base_url(url, require_table=False)
    table_id = select_table_id(url_table_id, requested_table_id)
    resolved = cli_json(["base", "+url-resolve", "--url", url, "--as", "user", "--json"], profile, cwd)
    resolved_data = data_of(resolved)
    resolved_token = value_by_key(resolved_data, "base_token")
    if resolved_token is not None and resolved_token != base_token:
        raise SystemExit("Base URL resolution does not match its base token")
    if url_table_id and resolved_table_id(resolved_data) != table_id:
        raise SystemExit("Base URL did not resolve to the requested table")
    blocks = cli_json(["base", "+base-block-list", "--base-token", base_token, "--as", "user", "--json"], profile, cwd)
    matching = [
        item for item in base_block_items(data_of(blocks))
        if str(item.get("block_id", item.get("table_id", item.get("id", "")))) == table_id
    ]
    if not matching:
        raise SystemExit("指定的数据表不属于当前 Base 或当前用户无权访问。")
    if matching[0].get("block_type", matching[0].get("type")) not in {None, "table"}:
        raise SystemExit("selected Base block is not a table")
    return base_token, table_id


def list_tables(url: str, profile: str | None, cwd: Path) -> int:
    base_token, _ = resolve_base_url(url, require_table=False)
    resolved = cli_json(["base", "+url-resolve", "--url", url, "--as", "user", "--json"], profile, cwd)
    resolved_token = value_by_key(data_of(resolved), "base_token")
    if resolved_token is not None and resolved_token != base_token:
        raise SystemExit("Base URL resolution does not match its base token")
    blocks = cli_json(["base", "+base-block-list", "--base-token", base_token, "--as", "user", "--json"], profile, cwd)
    tables = selectable_tables(base_block_items(data_of(blocks)))
    if not tables:
        raise SystemExit("当前 Base 没有可选择的数据表。")
    print(json.dumps({"base_token": base_token, "tables": tables, "selection_required": len(tables) > 1}, ensure_ascii=False, indent=2))
    return 0


def list_batches(url: str, requested_table_id: str, profile: str | None, cwd: Path) -> int:
    base_token, table_id = resolve_table_scope(url, requested_table_id, profile, cwd)
    fields_payload = cli_json(["base", "+field-list", "--base-token", base_token, "--table-id", table_id, "--as", "user", "--json"], profile, cwd)
    field_data = data_of(fields_payload)
    field_items = field_data.get("fields") or field_data.get("items") or field_data.get("data")
    if not isinstance(field_items, list):
        raise SystemExit("Base field list did not return field items")
    fields = [
        {"field_id": item.get("field_id", item.get("id")), "field_name": item.get("field_name", item.get("name")), "type": item.get("type")}
        for item in field_items if isinstance(item, dict)
    ]
    field_by_id = {str(item.get("field_id")): item for item in fields}
    records: list[tuple[str, dict[str, object]]] = []
    offset = 0
    while True:
        response = cli_json(["base", "+record-list", "--base-token", base_token, "--table-id", table_id, "--limit", "200", "--offset", str(offset), "--as", "user", "--json"], profile, cwd)
        data = data_of(response)
        values, field_ids, record_ids = data.get("data"), data.get("field_id_list"), data.get("record_id_list")
        if not isinstance(values, list) or not isinstance(field_ids, list) or not isinstance(record_ids, list):
            raise SystemExit("Base record list did not return a tabular response")
        for record_id, row in zip(record_ids, values):
            if not isinstance(record_id, str) or not isinstance(row, list):
                raise SystemExit("Base record list contains an invalid row")
            records.append((record_id, {str(field_id): row[index] if index < len(row) else None for index, field_id in enumerate(field_ids)}))
        if not data.get("has_more"):
            break
        if not values:
            raise SystemExit("Base pagination reported more records without returning a page")
        offset += len(values)
    payload = batch_discovery_payload(table_id, batch_choices(records, field_by_id))
    payload["base_token"] = base_token
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def select_download_workers(attachment_count: int, requested_workers: int) -> int:
    if attachment_count < 1:
        raise SystemExit("Base 快照中没有可下载附件。")
    if requested_workers < 1:
        raise SystemExit("download-workers 必须至少为 1。")
    return min(attachment_count, requested_workers)


def download_attachment(
    item: dict[str, object],
    downloads_dir: Path,
    base_token: str,
    table_id: str,
    profile: str | None,
    snapshot_dir: Path,
) -> None:
    remote_path = downloads_dir / str(item["file_token"])
    cli_json(
        [
            "base", "+record-download-attachment", "--base-token", base_token,
            "--table-id", table_id, "--record-id", str(item["record_id"]),
            "--file-token", str(item["file_token"]), "--output", str(remote_path.relative_to(snapshot_dir)),
            "--as", "user", "--json",
        ],
        profile,
        snapshot_dir,
        attempts=3,
    )
    if not remote_path.is_file():
        raise SystemExit("Base 附件下载未生成预期的本地文件。")
    item["sha256"] = sha256(remote_path)
    item["byte_size"] = remote_path.stat().st_size
    if isinstance(item["reported_size"], int) and item["reported_size"] != item["byte_size"]:
        raise SystemExit(f"下载附件大小与 Base 元数据不一致：{item['original_name']}")


def attachment_identity(item: dict[str, object]) -> tuple[str, str, int, str]:
    """Return the Base metadata identity used to decide local-byte reuse."""
    try:
        return (
            str(item["record_id"]),
            str(item["field_id"]),
            int(item["ordinal"]),
            str(item["file_token"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise SystemExit("附件缺少增量复用所需的 Base 控制字段。") from error


def load_reusable_attachments(
    previous_snapshot: Path, base_token: str, table_id: str, selection: dict[str, str],
) -> dict[tuple[str, str, int, str], dict[str, object]]:
    """Load only byte-verified attachments from a matching earlier Base snapshot."""
    if previous_snapshot.is_symlink() or not previous_snapshot.is_dir():
        raise SystemExit("前一快照必须是非符号链接目录。")
    manifest_path = previous_snapshot / "base_snapshot_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise SystemExit(f"前一快照缺少 base_snapshot_manifest.json：{previous_snapshot}") from error
    except json.JSONDecodeError as error:
        raise SystemExit("前一快照的 base_snapshot_manifest.json 不是有效 JSON。") from error
    if not isinstance(manifest, dict) or not isinstance(manifest.get("attachments"), list):
        raise SystemExit("前一快照使用了不支持的 manifest 结构。")
    base = manifest.get("base")
    if not isinstance(base, dict) or base.get("base_token") != base_token or base.get("table_id") != table_id:
        raise SystemExit("前一快照不属于当前 Base 或表，禁止复用其中附件。")
    if manifest.get("selection") != selection:
        raise SystemExit("前一快照不属于当前表和批次范围，禁止跨范围复用附件。")

    snapshot_root = previous_snapshot.resolve()
    reusable: dict[tuple[str, str, int, str], dict[str, object]] = {}
    for raw in manifest["attachments"]:
        if not isinstance(raw, dict):
            raise SystemExit("前一快照包含无效附件记录。")
        identity = attachment_identity(raw)
        relative_path = raw.get("snapshot_path")
        expected_hash = raw.get("sha256")
        expected_size = raw.get("byte_size")
        if not isinstance(relative_path, str) or not isinstance(expected_hash, str) or not isinstance(expected_size, int):
            raise SystemExit("前一快照附件缺少可验证的路径、哈希或大小。")
        source = (snapshot_root / relative_path).resolve()
        try:
            source.relative_to(snapshot_root)
        except ValueError as error:
            raise SystemExit("前一快照附件路径越过了快照目录边界。") from error
        if not source.is_file() or source.stat().st_size != expected_size or sha256(source) != expected_hash:
            raise SystemExit("前一快照附件未通过字节校验，禁止复用。")
        if identity in reusable:
            raise SystemExit("前一快照包含重复附件控制身份，禁止增量复用。")
        reusable[identity] = {"source": source, "sha256": expected_hash, "byte_size": expected_size}
    return reusable


def reuse_attachment(item: dict[str, object], reusable: dict[tuple[str, str, int, str], dict[str, object]], downloads_dir: Path) -> bool:
    """Copy a verified prior byte stream into this isolated snapshot when unchanged."""
    previous = reusable.get(attachment_identity(item))
    if previous is None:
        return False
    destination = downloads_dir / str(item["file_token"])
    shutil.copyfile(Path(previous["source"]), destination)
    actual_hash = sha256(destination)
    actual_size = destination.stat().st_size
    if actual_hash != previous["sha256"] or actual_size != previous["byte_size"]:
        raise SystemExit("复用附件的本地字节校验失败。")
    if isinstance(item["reported_size"], int) and item["reported_size"] != actual_size:
        raise SystemExit(f"复用附件大小与当前 Base 元数据不一致：{item['original_name']}")
    item["sha256"] = actual_hash
    item["byte_size"] = actual_size
    return True


def safe_name(name: str) -> str:
    cleaned = "".join("_" if char in "\\/:\x00" else char for char in name).strip()
    return cleaned or "unnamed_attachment"


def xlsx_column(index: int) -> str:
    value = index + 1
    result = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def write_register(path: Path, headers: list[str], rows: list[dict[str, str]]) -> None:
    strings: list[str] = []
    string_ids: dict[str, int] = {}

    def string_id(value: str) -> int:
        if value not in string_ids:
            string_ids[value] = len(strings)
            strings.append(value)
        return string_ids[value]

    sheet_rows = [["Base read-only snapshot"] + [""] * (len(headers) - 1), headers]
    sheet_rows.extend([[row.get(header, "") for header in headers] for row in rows])
    rows_xml: list[str] = []
    for row_index, values in enumerate(sheet_rows, 1):
        cells = "".join(
            f'<c r="{xlsx_column(column)}{row_index}" t="s"><v>{string_id(str(value))}</v></c>'
            for column, value in enumerate(values) if value != ""
        )
        rows_xml.append(f'<row r="{row_index}">{cells}</row>')
    # ElementTree does not expose XML escaping on all supported Python versions.
    from xml.sax.saxutils import escape
    shared_xml = "".join(f"<si><t>{escape(value)}</t></si>" for value in strings)
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="候选人流程与材料登记表" sheetId="1" r:id="rId1"/></sheets></workbook>')
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             f'<worksheet xmlns="{XLSX_NS}"><sheetData>{"".join(rows_xml)}</sheetData></worksheet>')
    shared = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              f'<sst xmlns="{XLSX_NS}" count="{len(strings)}" uniqueCount="{len(strings)}">{shared_xml}</sst>')
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                     '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                     '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
                     '</Types>')
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                 '</Relationships>')
    workbook_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                     '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                     '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>'
                     '</Relationships>')
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
        archive.writestr("xl/sharedStrings.xml", shared)


def date_only(value: object) -> str:
    if isinstance(value, list) and len(value) == 1:
        return date_only(value[0])
    text = "" if value is None else str(value)
    return text[:10] if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-" else text


def owner_snapshot(value: object) -> dict[str, str]:
    """Keep an explicitly assigned Base owner only in the local snapshot."""
    if value in (None, []):
        return {"status": "Not Provided"}
    if isinstance(value, str) and value:
        return {"status": "Placeholder", "name": value}
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        return {"status": "Unverified"}
    owner = value[0]
    open_id = owner.get("id", owner.get("open_id"))
    if not isinstance(open_id, str) or not open_id.startswith("ou_"):
        return {"status": "Unverified"}
    result = {"status": "Verified", "open_id": open_id}
    for key in ("name", "en_name"):
        if isinstance(owner.get(key), str) and owner[key]:
            result["name"] = owner[key]
            break
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Snapshot an authorized Feishu Base and build a material handoff.")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--table-id", default="", help="Explicit Base table ID for a multi-table Base; must match the URL table when both are provided.")
    parser.add_argument("--batch", default="", help="Exact 批次 value for an isolated same-table handoff.")
    parser.add_argument("--list-tables", action="store_true", help="List visible data tables and exit without creating local artifacts.")
    parser.add_argument("--list-batches", action="store_true", help="List exact batch values for the selected table and exit without creating local artifacts.")
    parser.add_argument("--snapshot-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--workspace-root", type=Path, help="Selected work directory; the package must be its direct child.")
    parser.add_argument("--lark-profile")
    parser.add_argument("--download-workers", type=int, default=8)
    parser.add_argument("--previous-snapshot", type=Path, help="Optional verified snapshot from the same Base/table for differential attachment reuse.")
    parser.add_argument("--incremental-from", type=Path, help="Completed same-batch snapshot used to isolate newly added candidates.")
    args = parser.parse_args()
    if args.list_tables and args.list_batches:
        parser.error("--list-tables 与 --list-batches 不能同时使用")
    if args.previous_snapshot is not None and args.incremental_from is not None:
        parser.error("--previous-snapshot 与 --incremental-from 不能同时使用")
    if args.list_tables:
        return list_tables(args.base_url, args.lark_profile, Path.cwd())
    if args.list_batches:
        return list_batches(args.base_url, args.table_id, args.lark_profile, Path.cwd())
    if args.snapshot_dir is None or args.output_dir is None:
        parser.error("构建材料交接时必须同时提供 --snapshot-dir 和 --output-dir")
    if args.workspace_root is None:
        parser.error("构建材料交接时必须提供 --workspace-root；未选择时请先确认桌面默认位置")
    if not args.batch.strip():
        parser.error("构建材料交接时必须明确选择 --batch")
    run_control.validate_package_parent(args.output_dir, args.workspace_root)
    run_control.validate_package_location(args.output_dir, args.batch, args.workspace_root)
    if run_control.paths_overlap(args.snapshot_dir.resolve(), args.output_dir.resolve()):
        raise SystemExit("snapshot directory and output directory must be disjoint")
    target_snapshot_dir = args.snapshot_dir.resolve()
    execution_cwd = args.workspace_root
    base_token, table_id = resolve_table_scope(args.base_url, args.table_id, args.lark_profile, execution_cwd)
    selection = {"table_id": table_id, "batch": args.batch.strip() or "Not Selected"}
    reusable = {}
    previous_snapshot = None
    if args.previous_snapshot is not None:
        if args.previous_snapshot.is_symlink() or not args.previous_snapshot.is_dir():
            raise SystemExit("previous-snapshot 必须是非符号链接目录。")
        previous_snapshot = args.previous_snapshot.resolve()
        if previous_snapshot == target_snapshot_dir:
            raise SystemExit("previous-snapshot 必须是不同于当前目标的已完成快照。")
    incremental_from = None
    if args.incremental_from is not None:
        if args.incremental_from.is_symlink() or not args.incremental_from.is_dir():
            raise SystemExit("incremental-from 必须是不同于当前目标的已完成快照。")
        incremental_from = args.incremental_from.resolve()
        if incremental_from == target_snapshot_dir:
            raise SystemExit("incremental-from 必须是不同于当前目标的已完成快照。")
    fields_payload = cli_json(["base", "+field-list", "--base-token", base_token, "--table-id", table_id, "--as", "user", "--json"], args.lark_profile, execution_cwd)
    field_data = data_of(fields_payload)
    field_items = field_data.get("fields") or field_data.get("items") or field_data.get("data")
    if not isinstance(field_items, list):
        raise SystemExit("Base field list did not return field items")
    fields = [
        {"field_id": item.get("field_id", item.get("id")), "field_name": item.get("field_name", item.get("name")), "type": item.get("type")}
        for item in field_items if isinstance(item, dict)
    ]
    field_by_id = {str(item.get("field_id")): item for item in fields}
    owner_field_ids = {
        material_field: next(
            (
                field_id
                for field_id, field in field_by_id.items()
                if field.get("field_name") == owner_field_name
            ),
            None,
        )
        for material_field, owner_field_name in OWNER_FIELDS_BY_MATERIAL.items()
    }

    records: list[tuple[str, dict[str, object]]] = []
    offset = 0
    while True:
        response = cli_json(["base", "+record-list", "--base-token", base_token, "--table-id", table_id, "--limit", "200", "--offset", str(offset), "--as", "user", "--json"], args.lark_profile, execution_cwd)
        data = data_of(response)
        values = data.get("data")
        field_ids = data.get("field_id_list")
        record_ids = data.get("record_id_list")
        if not isinstance(values, list) or not isinstance(field_ids, list) or not isinstance(record_ids, list):
            raise SystemExit("Base record list did not return a tabular response")
        for record_id, row in zip(record_ids, values):
            if not isinstance(record_id, str) or not isinstance(row, list):
                raise SystemExit("Base record list contains an invalid row")
            records.append((record_id, {str(field_id): row[index] if index < len(row) else None for index, field_id in enumerate(field_ids)}))
        if not data.get("has_more"):
            break
        offset += len(values)
        if not values:
            raise SystemExit("Base pagination reported more records without returning a page")
    if not records:
        raise SystemExit("Base table contains no records")
    source_record_count = len(records)
    records, selected_batch = select_batch_records(records, field_by_id, args.batch)
    selection["batch"] = selected_batch
    if incremental_from is not None:
        baseline = load_incremental_baseline(incremental_from, base_token, table_id, selected_batch)
        records, candidate_ids = select_incremental_candidate_records(records, field_by_id, baseline)
        selection["scope"] = "Incremental Candidate Cohort"
        selection["candidate_ids"] = candidate_ids

    snapshot_dir = run_control.require_empty_directory(args.snapshot_dir, "snapshot directory")
    output_dir = run_control.require_empty_directory(args.output_dir, "output directory")
    if snapshot_dir == output_dir:
        raise SystemExit("snapshot directory and output directory must be different")
    input_dir = snapshot_dir / "input"
    input_dir.mkdir()
    downloads_dir = snapshot_dir / ".base-download"
    downloads_dir.mkdir()
    if previous_snapshot is not None:
        reusable = load_reusable_attachments(previous_snapshot, base_token, table_id, selection)

    attachments: list[dict[str, object]] = []
    register_rows: list[dict[str, str]] = []
    for record_id, values in records:
        field_values: dict[str, str] = {}
        candidate = str(values.get(next((identifier for identifier, field in field_by_id.items() if field.get("field_name") == "候选人姓名"), ""), "") or "Not Provided")
        for field_id, field in field_by_id.items():
            name = str(field.get("field_name", ""))
            value = values.get(field_id)
            if field.get("type") == ATTACHMENT_FIELD:
                items = value if isinstance(value, list) else []
                field_values[name] = ""
                for ordinal, item in enumerate(items, 1):
                    if not isinstance(item, dict) or not isinstance(item.get("file_token"), str) or not isinstance(item.get("name"), str):
                        raise SystemExit(f"invalid attachment metadata for {candidate} / {name}")
                    attachments.append({"record_id": record_id, "candidate": candidate, "field": name, "field_id": field_id, "ordinal": ordinal, "file_token": item["file_token"], "original_name": item["name"], "reported_size": item.get("size")})
            else:
                field_values[name] = date_only(value)
        register_rows.append(field_values)
    if not any(item["field"] == SHARED_PROMPT_FIELD for item in attachments):
        raise SystemExit("Base table has no shared technical-assignment prompt attachment")
    resource_limits.validate_declared_base_attachments(attachments, snapshot_dir, output_dir)

    reused_attachment_entries = sum(reuse_attachment(item, reusable, downloads_dir) for item in attachments)
    download_items = [item for item in attachments if "sha256" not in item]
    if download_items:
        workers = select_download_workers(len(download_items), args.download_workers)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(download_attachment, item, downloads_dir, base_token, table_id, args.lark_profile, snapshot_dir)
                for item in download_items
            ]
            for future in as_completed(futures):
                future.result()
    resource_limits.validate_source_files(
        [downloads_dir / str(item["file_token"]) for item in attachments], output_dir,
    )

    shared = [item for item in attachments if item["field"] == SHARED_PROMPT_FIELD]
    shared_hashes = {str(item["sha256"]) for item in shared}
    if len(shared_hashes) != 1:
        raise SystemExit("shared technical-assignment prompt attachments are not byte-identical")
    shared_name = safe_name(str(shared[0]["original_name"]))
    shared_target = input_dir / shared_name
    shutil.copyfile(downloads_dir / str(shared[0]["file_token"]), shared_target)
    for item in shared:
        item["snapshot_path"] = f"input/{shared_name}"
        item["shared_prompt"] = True

    source_root = input_dir / "base-source"
    source_ordinal = 0
    for item in attachments:
        if item["field"] == SHARED_PROMPT_FIELD:
            continue
        source_ordinal += 1
        original = safe_name(str(item["original_name"]))
        source_target = source_root / f"{source_ordinal:04d}" / original
        source_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(downloads_dir / str(item["file_token"]), source_target)
        item["snapshot_path"] = "input/" + source_target.relative_to(input_dir).as_posix()
        item["shared_prompt"] = False

    by_record_field: dict[tuple[str, str], list[str]] = {}
    for item in attachments:
        by_record_field.setdefault((str(item["record_id"]), str(item["field"])), []).append(str(item["snapshot_path"]))
    headers = [field for field in CANONICAL_FIELDS if field in {str(item.get("field_name")) for item in fields}]
    missing = set(CANONICAL_FIELDS) - set(headers)
    if missing:
        raise SystemExit("Base table is missing required fields: " + ", ".join(sorted(missing)))
    for (record_id, _), row in zip(records, register_rows):
        for field in headers:
            if (record_id, field) in by_record_field:
                row[field] = "\n".join(by_record_field[(record_id, field)])
            else:
                row[field] = row.get(field, "")
        process_status = row.get("处理状态", "") or "Not Provided"
        if process_status not in PROCESS_STATUSES:
            raise SystemExit(f"{row.get('候选人姓名', 'Not Provided')} 的处理状态不在受控枚举中：{process_status}")
    identity_keys = run_control.validate_candidate_keys(register_rows)
    register_path = input_dir / "候选人流程与材料登记表.xlsx"
    write_register(register_path, headers, register_rows)
    if not build_materials.read_xlsx_rows(register_path):
        raise SystemExit("generated controlled register could not be read")

    previous_identities = set(reusable)
    current_identities = {attachment_identity(item) for item in attachments}
    manifest = {
        "schema_version": 5,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "base": {"base_token": base_token, "table_id": table_id},
        "selection": selection,
        "source_record_count": source_record_count,
        "record_count": len(records),
        "records": [
            {
                "record_id": record_id,
                "candidate_id": row.get("候选人编号", "Not Provided") or "Not Provided",
                "candidate": row.get("候选人姓名", "Not Provided") or "Not Provided",
                "owners": {
                    material_field: owner_snapshot(values.get(field_id)) if field_id else {"status": "Not Provided"}
                    for material_field, field_id in owner_field_ids.items()
                },
                "expected_material_fields": expected_material_fields(row),
                "expected_material_components": [
                    {"field": field, "component": component}
                    for field, component in expected_material_components(row)
                ],
            }
            for (record_id, _), row in zip(records, register_rows)
        ],
        "attachments": [{key: item[key] for key in ("record_id", "candidate", "field", "field_id", "ordinal", "file_token", "original_name", "reported_size", "byte_size", "sha256", "snapshot_path", "shared_prompt")} for item in attachments],
        "refresh": {
            "mode": "Differential" if args.previous_snapshot is not None else "Full",
            "reused_attachment_entries": reused_attachment_entries,
            "downloaded_attachment_entries": len(download_items),
            "removed_attachment_entries": len(previous_identities - current_identities),
        },
    }
    (snapshot_dir / "base_snapshot_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    mapping_path = snapshot_dir / ".material_mapping.jsonl"
    result = subprocess.run([sys.executable, str(Path(__file__).with_name("build_materials.py")), "--workspace-root", str(snapshot_dir), "--output-dir", str(output_dir), "--mapping-output", str(mapping_path), "--allow-nested-input"], text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or result.stdout.strip() or "material-pack build failed")
    mappings = [json.loads(line) for line in mapping_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    observations = material_observations(register_rows, attachments, mappings)
    trace = trace_observations(attachments, mappings)
    observations_payload = {
        "schema_version": 2,
        "captured_at": manifest["captured_at"],
        "selection": selection,
        "metadata_fingerprint": metadata_fingerprint(records, field_by_id),
        "observations": observations,
        "trace_observations": trace,
    }
    (output_dir / "base_intake_observations.json").write_text(json.dumps(observations_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    run_control.bind_base_observations(output_dir / run_control.RUN_CONTROL_FILE)
    run_control.append_operation_log(
        output_dir, "base_intake_completed", batch_id=run_control.load_run_control(output_dir / run_control.RUN_CONTROL_FILE)["batch_id"],
        active_observation_count=len(observations), trace_observation_count=len(trace),
    )
    write_process_records(output_dir, manifest, mappings, observations_payload)
    run_control.refresh_process_records(output_dir)
    print(json.dumps({"snapshot_dir": str(snapshot_dir), "output_dir": str(output_dir), "selection": selection, "scope": selection.get("scope", "Full Batch"), "source_record_count": source_record_count, "record_count": len(records), "attachment_entries": len(attachments), "unique_snapshot_files": sum(1 for path in input_dir.rglob("*") if path.is_file()), "base_observations": len(observations), "trace_observations": len(trace), "trace_categories": sorted({item["category"] for item in trace}), "refresh": manifest["refresh"], "base_written": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
