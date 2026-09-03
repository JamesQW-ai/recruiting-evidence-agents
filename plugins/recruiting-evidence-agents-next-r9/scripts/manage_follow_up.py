#!/usr/bin/env python3
"""Manage D13 missing-material follow-up state outside source and delivery folders."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import build_base_materials
import build_candidate_pack
import run_control


STATE_FILE = "follow_up_state.json"
INDEX_FILE = "processed_index.jsonl"
DRAFT_FILE = "follow_up_draft.json"
CONTACT_FILE = "contact_map.json"
LOCK_FILE = "follow_up.lock"
AUDIT_FILE = "follow_up_audit.jsonl"
WATCH_LOG_FILE = "reply_watch.log"
COMPLETION_FILE = "follow_up_completion.json"
CONTROL_SCHEMA = 5
PROCESSING_STATUS_READBACK_ATTEMPTS = 3
PROCESSING_STATUS_READBACK_DELAY_SECONDS = 1
TERMINAL_STATUSES = {"Verified", "Confirmed Missing"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CLEANUP_CATEGORIES = {
    "Invalid Extra Material", "Multiple Material Versions",
}
MISSING_MATERIAL_UNAVAILABLE_HINT = "如确认无法提供，请在本条消息下说明“无法提供”。"


def confirmed_missing_reply(value: object) -> bool:
    """Accept an explicit unavailability statement only after message linkage is verified."""
    return isinstance(value, str) and "无法提供" in value.strip()
SELF_SERVICE_MATERIAL_FIELDS = {"HRD面试材料"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise SystemExit(f"{label} 不存在：{path}") from error
    except json.JSONDecodeError as error:
        raise SystemExit(f"{label} 不是有效 JSON：{error}") from error
    if not isinstance(value, dict):
        raise SystemExit(f"{label} 顶层必须是对象。")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    """Atomically replace control JSON so interrupted writes never leave partial state."""
    payload = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


@contextmanager
def control_lock(control_dir: Path):
    """Serialize local control mutations without turning one operator into a daemon."""
    control_dir.mkdir(parents=True, exist_ok=True)
    with (control_dir / LOCK_FILE).open("a+", encoding="utf-8") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise SystemExit("补件控制目录正在被另一项本地操作使用；未合并或覆盖状态。") from error
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def append_audit(control_dir: Path, event: str, **details: Any) -> None:
    record = {"schema_version": CONTROL_SCHEMA, "at": utc_now(), "event": event, **details}
    with (control_dir / AUDIT_FILE).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def require_control_dir(path: Path, snapshot_dir: Path) -> Path:
    control_dir = path.resolve()
    if control_dir == snapshot_dir:
        raise SystemExit("control-dir 必须与只读快照目录不同。")
    control_dir.mkdir(parents=True, exist_ok=True)
    return control_dir


def path_within(path: Path, parent: Path, label: str) -> Path:
    """Resolve a path and require it to stay within a selected local boundary."""
    resolved = path.resolve()
    try:
        resolved.relative_to(parent.resolve())
    except ValueError as error:
        raise SystemExit(f"{label} 必须位于已选工作目录内：{parent.resolve()}") from error
    return resolved


def bind_workspace(
    control_dir: Path, snapshot_dir: Path, workspace_root: Path, output_dir: Path,
) -> dict[str, str]:
    """Bind one follow-up control to its selected workspace and visible business package."""
    workspace = run_control.require_directory(workspace_root, "工作目录")
    control = require_control_dir(control_dir, snapshot_dir)
    control_root = workspace / ".招聘材料控制"
    path_within(control, control_root, "control-dir")
    path_within(snapshot_dir, control_root, "snapshot-dir")
    output = run_control.require_directory(output_dir, "当前材料包目录")
    run_control.validate_package_parent(output, workspace)
    if not (output / "materials.zip").is_file() or not (output / "base_intake_observations.json").is_file():
        raise SystemExit("当前材料包目录不是已完成的 Base 材料交接；不能绑定刷新输出位置。")
    binding = {
        "workspace_root": str(workspace.resolve()),
        "output_dir": str(output.resolve()),
    }
    with control_lock(control):
        state = load_state(control)
        existing = state.get("workspace_binding")
        if isinstance(existing, dict):
            if existing.get("workspace_root") != binding["workspace_root"] or existing.get("output_dir") != binding["output_dir"]:
                raise SystemExit("补件控制已绑定其他工作目录或材料包；不能切换输出位置。")
        else:
            state["workspace_binding"] = {**binding, "bound_at": utc_now()}
            state["updated_at"] = utc_now()
            write_json(control / STATE_FILE, state)
            append_audit(control, "workspace_bound", **binding)
    return binding


def require_workspace_binding(
    control_dir: Path, snapshot_root: Path, handoff_root: Path,
) -> dict[str, str]:
    """Reject refreshes that attempt to redirect a previously selected workspace."""
    state = load_state(control_dir)
    binding = state.get("workspace_binding")
    if not isinstance(binding, dict) or not isinstance(binding.get("workspace_root"), str) or not isinstance(binding.get("output_dir"), str):
        raise SystemExit("补件控制尚未绑定工作目录和当前材料包；请先重新生成催办预览。")
    workspace = Path(binding["workspace_root"]).resolve()
    output = Path(binding["output_dir"]).resolve()
    run_control.require_directory(workspace, "已选工作目录")
    run_control.require_directory(output, "当前材料包目录")
    run_control.validate_package_parent(output, workspace)
    if snapshot_root.resolve() != (workspace / ".招聘材料控制").resolve():
        raise SystemExit("snapshot-root 必须为首次选定工作目录下的 .招聘材料控制；不能切换刷新位置。")
    if handoff_root.resolve() != workspace:
        raise SystemExit("handoff-root 必须与首次选定的工作目录一致；不能切换刷新输出位置。")
    return {"workspace_root": str(workspace), "output_dir": str(output)}


def read_register(snapshot_dir: Path) -> list[dict[str, str]]:
    path = snapshot_dir / "input" / "候选人流程与材料登记表.xlsx"
    rows = build_candidate_pack.register_rows(path)
    names = [row["候选人姓名"] for row in rows]
    if len(names) != len(set(names)):
        raise SystemExit("受控登记表中存在重复候选人姓名，无法安全生成补件项。")
    for row in rows:
        if not row.get("候选人编号"):
            raise SystemExit("受控登记表缺少候选人编号，无法建立稳定补件项。")
    return rows


def attachment_key(item: dict[str, Any]) -> str:
    fields = ("record_id", "field_id", "ordinal", "file_token", "sha256")
    try:
        raw = "\0".join(str(item[field]) for field in fields)
    except KeyError as error:
        raise SystemExit(f"快照附件缺少控制字段：{error.args[0]}") from error
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def item_id(candidate_id: str, field: str, component: str) -> str:
    raw = f"{candidate_id}\0{field}\0{component}".encode("utf-8")
    return "FU-" + hashlib.sha256(raw).hexdigest()[:12].upper()


def cleanup_item_id(candidate_id: str, field: str, categories: list[str], scope: str) -> str:
    raw = f"cleanup\0{candidate_id}\0{field}\0{'|'.join(categories)}\0{scope}".encode("utf-8")
    return "CL-" + hashlib.sha256(raw).hexdigest()[:12].upper()


def review_item_id(candidate_id: str, field: str, scope: str) -> str:
    raw = f"review\0{candidate_id}\0{field}\0{scope}".encode("utf-8")
    return "RV-" + hashlib.sha256(raw).hexdigest()[:12].upper()


def load_processed(control_dir: Path) -> set[str]:
    path = control_dir / INDEX_FILE
    if not path.exists():
        return set()
    keys: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as error:
            raise SystemExit(f"{INDEX_FILE} 第 {line_number} 行不是有效 JSON：{error}") from error
        if not isinstance(item, dict) or not isinstance(item.get("attachment_key"), str):
            raise SystemExit(f"{INDEX_FILE} 第 {line_number} 行缺少 attachment_key。")
        keys.add(item["attachment_key"])
    return keys


def append_processed(control_dir: Path, attachments: list[dict[str, Any]], known: set[str], captured_at: str) -> list[dict[str, Any]]:
    new_events: list[dict[str, Any]] = []
    for attachment in attachments:
        key = attachment_key(attachment)
        if key in known:
            continue
        new_events.append({
            "schema_version": CONTROL_SCHEMA,
            "attachment_key": key,
            "captured_at": captured_at,
            "record_id": str(attachment["record_id"]),
            "field": str(attachment["field"]),
            "file_token": str(attachment["file_token"]),
            "sha256": str(attachment["sha256"]),
            "original_name": str(attachment["original_name"]),
        })
    if new_events:
        with (control_dir / INDEX_FILE).open("a", encoding="utf-8") as stream:
            for event in new_events:
                stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    return new_events


def load_state(control_dir: Path) -> dict[str, Any]:
    path = control_dir / STATE_FILE
    if not path.exists():
        return {"schema_version": CONTROL_SCHEMA, "items": []}
    state = read_json(path, STATE_FILE)
    if state.get("schema_version") not in {1, 2, 3, 4, CONTROL_SCHEMA} or not isinstance(state.get("items"), list):
        raise SystemExit("follow_up_state.json 使用了不支持的结构。")
    if not all(isinstance(item, dict) and isinstance(item.get("id"), str) for item in state["items"]):
        raise SystemExit("follow_up_state.json 包含无效补件项。")
    for item in state["items"]:
        if item.get("status") == "Resolved":
            item["status"] = "Verified"
            item["legacy_status"] = "Resolved"
        elif item.get("status") == "Repeated No Material":
            item["status"] = "Pending Review"
            item["legacy_status"] = "Repeated No Material"
    return state


def load_contacts(control_dir: Path) -> dict[tuple[str, str], str]:
    path = control_dir / CONTACT_FILE
    if not path.exists():
        return {}
    value = read_json(path, CONTACT_FILE)
    if value.get("schema_version") not in {1, CONTROL_SCHEMA} or not isinstance(value.get("owners"), list):
        raise SystemExit("contact_map.json 使用了不支持的结构。")
    result: dict[tuple[str, str], str] = {}
    for item in value["owners"]:
        if not isinstance(item, dict):
            raise SystemExit("contact_map.json 包含无效负责人项。")
        candidate_id = item.get("candidate_id")
        field = item.get("material_field")
        recipient = item.get("recipient")
        if not all(isinstance(value, str) and value for value in (candidate_id, field, recipient)):
            raise SystemExit("contact_map.json 的负责人项必须包含 candidate_id、material_field 和 recipient。")
        result[(candidate_id, field)] = recipient
    return result


def load_base_owners(manifest: dict[str, Any]) -> dict[tuple[str, str], str]:
    """Return verified Base owner open IDs keyed by stable candidate ID and material field."""
    records = manifest.get("records")
    if not isinstance(records, list):
        return {}
    owners: dict[tuple[str, str], str] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        candidate_id = record.get("candidate_id")
        assigned = record.get("owners")
        if not isinstance(candidate_id, str) or not candidate_id or not isinstance(assigned, dict):
            continue
        for material_field, owner in assigned.items():
            if not isinstance(material_field, str) or material_field not in build_base_materials.ROLE_FIELD or not isinstance(owner, dict):
                continue
            open_id = owner.get("open_id")
            if owner.get("status") == "Verified" and isinstance(open_id, str) and open_id.startswith("ou_"):
                owners[(candidate_id, material_field)] = f"open_id:{open_id}"
    return owners


def load_mappings(snapshot_dir: Path) -> list[dict[str, Any]]:
    path = snapshot_dir / ".material_mapping.jsonl"
    if not path.exists():
        raise SystemExit("快照缺少 .material_mapping.jsonl；无法基于内容映射确认缺件。")
    mappings: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            mapping = json.loads(line)
        except json.JSONDecodeError as error:
            raise SystemExit(f".material_mapping.jsonl 第 {line_number} 行不是有效 JSON：{error}") from error
        if not isinstance(mapping, dict):
            raise SystemExit(f".material_mapping.jsonl 第 {line_number} 行必须是对象。")
        mappings.append(mapping)
    return mappings


def mapped_material_components(attachments: list[dict[str, Any]], mappings: list[dict[str, Any]]) -> set[tuple[str, str, str]]:
    by_path = {str(item.get("snapshot_path")): item for item in attachments}
    result: set[tuple[str, str, str]] = set()
    for mapping in mappings:
        attachment = by_path.get(str(mapping.get("original_path")))
        if attachment is None:
            continue
        candidate = mapping.get("resolved_candidate")
        field = attachment.get("field")
        if (
            isinstance(candidate, str)
            and candidate not in {"Unknown", "Not Applicable", ""}
            and field in build_base_materials.ROLE_FIELD
            and mapping.get("classification") not in {"Unmapped", "Ambiguous"}
            and mapping.get("readability") not in {"PDF Invalid", "PDF Unreadable"}
        ):
            component = build_base_materials.material_component(str(field), str(attachment.get("original_name", "")))
            if component != "其他":
                result.add((candidate, str(field), component))
    return result


def material_label(field: str, component: str) -> str:
    return field if component == "材料" else f"{field}（{component}）"


def scope_locations(scope: str) -> list[tuple[str, str]]:
    """Parse factual trace scopes into candidate/field pairs for owner routing."""
    locations: list[tuple[str, str]] = []
    for part in scope.split("；"):
        values = [value.strip() for value in part.split(" / ")]
        if len(values) >= 2 and values[0] and values[1]:
            locations.append((values[0], values[1]))
    return locations


def cleanup_message(item: dict[str, Any]) -> str:
    candidate = str(item["candidate"])
    material = material_label(str(item["material_field"]), str(item.get("material_component", "材料")))
    categories = set(item.get("categories", []))
    actions: list[str] = []
    if "Invalid Extra Material" in categories:
        actions.append("其中有文件无法打开，但已有可用文件，请删除无法打开的文件")
    if "Multiple Material Versions" in categories:
        actions.append("存在多个不同版本，请确认业务保留版本")
    if not actions:
        raise SystemExit("清理事项缺少可执行动作。")
    return f"请核查候选人 {candidate} 的 {material}：" + "；".join(actions) + "。处理后请在本条消息下回复“已处理”。"


def manual_review_message(item: dict[str, Any]) -> str:
    return (
        f"请核查候选人 {item['candidate']} 的 {material_label(str(item['material_field']), '材料')}："
        "其中一份材料无法确认归属。请确认归属后在多维表中完成整理，处理后请在本条消息下回复“已处理”。"
    )


def follow_up_message(item: dict[str, Any]) -> str:
    if item.get("kind") == "Cleanup Review":
        return cleanup_message(item)
    if item.get("kind") == "Manual Verification":
        return manual_review_message(item)
    return (
        f"请在多维表中补充候选人 {item['candidate']} 的 "
        f"{material_label(item['material_field'], item.get('material_component', '材料'))}。"
        f"补充完成后，请在本条消息下回复“已上传”；{MISSING_MATERIAL_UNAVAILABLE_HINT}"
    )


def cleanup_review_items(
    rows: list[dict[str, str]], attachments: list[dict[str, Any]], mappings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert nonblocking trace facts into owner-review items without inferring deletion."""
    by_name = {row["候选人姓名"]: row["候选人编号"] for row in rows}
    observations = build_base_materials.trace_observations(attachments, mappings)
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for observation in observations:
        category = observation["category"]
        if category not in CLEANUP_CATEGORIES:
            continue
        locations = scope_locations(observation["scope"])
        for candidate, field in locations:
            candidate_id = by_name.get(candidate)
            if candidate_id is None or field not in build_base_materials.ROLE_FIELD:
                continue
            key = (candidate_id, field)
            item = grouped.setdefault(key, {
                "candidate": candidate,
                "candidate_id": candidate_id,
                "material_field": field,
                "material_component": "材料",
                "categories": [],
                "scopes": [],
                "facts": [],
            })
            item["categories"].append(category)
            item["scopes"].append(observation["scope"])
            item["facts"].append(observation["fact"])
    for item in grouped.values():
        item["categories"] = sorted(set(item["categories"]))
        item["scope"] = "；".join(sorted(set(item.pop("scopes"))))
        item["facts"] = sorted(set(item["facts"]))
    return sorted(grouped.values(), key=lambda item: (item["candidate"], item["material_field"]))


def normalize_recipient(value: str) -> str:
    recipient = value.strip()
    if recipient.startswith("email:"):
        email = recipient[len("email:"):].strip()
        if EMAIL_RE.fullmatch(email):
            return f"email:{email}"
    elif EMAIL_RE.fullmatch(recipient):
        return f"email:{recipient}"
    elif recipient.startswith("open_id:ou_") and len(recipient) > len("open_id:ou_"):
        return recipient
    raise SystemExit("收件人必须提供邮箱，或 open_id:ou_...；不会按姓名猜测联系人。")


def plan(snapshot_dir: Path, control_dir: Path) -> dict[str, Any]:
    control_dir = require_control_dir(control_dir, snapshot_dir)
    with control_lock(control_dir):
        manifest = read_json(snapshot_dir / "base_snapshot_manifest.json", "base_snapshot_manifest.json")
        if manifest.get("schema_version") not in {1, 2, 3, 4, 5} or not isinstance(manifest.get("attachments"), list):
            raise SystemExit("base_snapshot_manifest.json 使用了不支持的结构。")
        captured_at = manifest.get("captured_at")
        if not isinstance(captured_at, str) or not captured_at:
            raise SystemExit("base_snapshot_manifest.json 缺少 captured_at。")
        attachments = [item for item in manifest["attachments"] if isinstance(item, dict)]
        if len(attachments) != len(manifest["attachments"]):
            raise SystemExit("base_snapshot_manifest.json 包含无效附件。")
        rows = read_register(snapshot_dir)
        mappings = load_mappings(snapshot_dir)
        present = mapped_material_components(attachments, mappings)
        active_observations = build_base_materials.material_observations(rows, attachments, mappings)
        unmapped_scopes = {
            (candidate, field)
            for observation in active_observations if observation["category"] == "Unmapped Material"
            for candidate, field in scope_locations(observation["scope"])
        }
        processed = load_processed(control_dir)
        new_events = append_processed(control_dir, attachments, processed, captured_at)
        new_event_keys = {event["attachment_key"] for event in new_events}
        newly_mapped = mapped_material_components(
            [attachment for attachment in attachments if attachment_key(attachment) in new_event_keys],
            mappings,
        )
        contacts = load_contacts(control_dir)
        base_owners = load_base_owners(manifest)
        state = load_state(control_dir)
        existing = {str(item["id"]): item for item in state["items"]}
        outstanding: list[dict[str, Any]] = []
        manual_review: list[dict[str, Any]] = []
        self_service: list[dict[str, Any]] = []
        verified_ids: list[str] = []
        confirmed_missing: list[dict[str, Any]] = []
        rebuild_ids: list[str] = []
        state_events: list[tuple[str, str]] = []
        for row in rows:
            candidate = row["候选人姓名"]
            candidate_id = row["候选人编号"]
            for field, component in build_base_materials.expected_material_components(row):
                if (candidate, field) in unmapped_scopes:
                    continue
                identifier = item_id(candidate_id, field, component)
                item = existing.get(identifier)
                if (candidate, field, component) in present:
                    if item is not None and item.get("status") != "Verified":
                        item["status"] = "Verified"
                        item["verified_at"] = utc_now()
                        item["resolution"] = "Verified Base attachment mapped by readable content or filename."
                        state_events.append(("material_verified", identifier))
                        if (candidate, field, component) in newly_mapped:
                            rebuild_ids.append(candidate_id)
                    if item is not None:
                        verified_ids.append(identifier)
                    continue
                if item is None:
                    item = {
                        "id": identifier,
                        "candidate_id": candidate_id,
                        "candidate": candidate,
                        "material_field": field,
                        "material_component": component,
                        "status": "Pending Review",
                        "created_at": utc_now(),
                    }
                    state["items"].append(item)
                    existing[identifier] = item
                    state_events.append(("material_opened", identifier))
                elif item.get("status") == "Verified":
                    item["status"] = "Pending Review"
                    item["verification_lost_at"] = utc_now()
                    state_events.append(("verified_material_removed", identifier))
                if item.get("status") == "Confirmed Missing":
                    confirmed_missing.append(item)
                    continue
                if field in SELF_SERVICE_MATERIAL_FIELDS:
                    item["kind"] = "Self-service Upload"
                    item["status"] = "Self-service"
                    item["self_service_action"] = "请直接在多维表补充材料，完成后重新核验。"
                    self_service.append(item)
                    continue
                item["recipient"] = contacts.get((candidate_id, field), base_owners.get((candidate_id, field), "Not Provided"))
                outstanding.append(item)
        active_review_ids: set[str] = set()
        candidate_ids_by_name = {row["候选人姓名"]: row["候选人编号"] for row in rows}
        for observation in active_observations:
            if observation["category"] != "Unmapped Material":
                continue
            for candidate, field in scope_locations(observation["scope"]):
                candidate_id = candidate_ids_by_name.get(candidate)
                if candidate_id is None or field not in build_base_materials.ROLE_FIELD:
                    continue
                identifier = review_item_id(candidate_id, field, observation["scope"])
                active_review_ids.add(identifier)
                item = existing.get(identifier)
                if item is None:
                    item = {
                        "id": identifier,
                        "kind": "Manual Verification",
                        "candidate_id": candidate_id,
                        "candidate": candidate,
                        "material_field": field,
                        "material_component": "材料",
                        "categories": ["Unmapped Material"],
                        "scope": observation["scope"],
                        "fact": observation["fact"],
                        "status": "Pending Review",
                        "created_at": utc_now(),
                    }
                    state["items"].append(item)
                    existing[identifier] = item
                    state_events.append(("manual_review_opened", identifier))
                elif item.get("status") == "Verified":
                    item["status"] = "Pending Review"
                    item["reappeared_at"] = utc_now()
                    state_events.append(("manual_review_reopened", identifier))
                if item.get("status") in TERMINAL_STATUSES:
                    continue
                item["recipient"] = contacts.get(
                    (candidate_id, field), base_owners.get((candidate_id, field), "Not Provided"),
                )
                manual_review.append(item)
        for item in state["items"]:
            if item.get("kind") != "Manual Verification" or item.get("id") in active_review_ids:
                continue
            if item.get("status") not in TERMINAL_STATUSES:
                item["status"] = "Verified"
                item["verified_at"] = utc_now()
                item["resolution"] = "The latest Base snapshot no longer contains this unassigned material."
                state_events.append(("manual_review_verified", str(item["id"])))
        outstanding = manual_review + outstanding
        active_cleanup_ids: set[str] = set()
        for spec in cleanup_review_items(rows, attachments, mappings):
            identifier = cleanup_item_id(
                spec["candidate_id"], spec["material_field"], spec["categories"], spec["scope"],
            )
            active_cleanup_ids.add(identifier)
            item = existing.get(identifier)
            if item is None:
                item = {
                    "id": identifier,
                    "kind": "Cleanup Review",
                    "candidate_id": spec["candidate_id"],
                    "candidate": spec["candidate"],
                    "material_field": spec["material_field"],
                    "material_component": spec["material_component"],
                    "categories": spec["categories"],
                    "scope": spec["scope"],
                    "facts": spec["facts"],
                    "status": "Pending Review",
                    "created_at": utc_now(),
                }
                state["items"].append(item)
                existing[identifier] = item
                state_events.append(("cleanup_opened", identifier))
            elif item.get("status") == "Verified":
                item["status"] = "Pending Review"
                item["reappeared_at"] = utc_now()
                state_events.append(("cleanup_reopened", identifier))
            if item.get("status") in TERMINAL_STATUSES:
                continue
            field = str(item["material_field"])
            item["recipient"] = contacts.get(
                (str(item["candidate_id"]), field),
                base_owners.get((str(item["candidate_id"]), field), "Not Provided"),
            )
            outstanding.append(item)
        for item in state["items"]:
            if item.get("kind") != "Cleanup Review" or item.get("id") in active_cleanup_ids:
                continue
            if item.get("status") not in TERMINAL_STATUSES:
                item["status"] = "Verified"
                item["verified_at"] = utc_now()
                item["resolution"] = "The latest Base snapshot no longer contains this cleanup finding."
                state_events.append(("cleanup_verified", str(item["id"])))
        state["schema_version"] = CONTROL_SCHEMA
        state["updated_at"] = utc_now()
        state["last_snapshot_captured_at"] = captured_at
        state["last_snapshot_dir"] = str(snapshot_dir.resolve())
        write_json(control_dir / STATE_FILE, state)
        for event, identifier in state_events:
            append_audit(control_dir, event, item_id=identifier, snapshot_captured_at=captured_at)
        append_audit(control_dir, "plan_completed", snapshot_captured_at=captured_at, new_attachment_events=len(new_events))
        draft_items = [
            {
                "id": item["id"],
                "kind": item.get("kind", "Material Follow-up"),
                "candidate_id": item["candidate_id"],
                "candidate": item["candidate"],
                "material_field": item["material_field"],
                "material_component": item.get("material_component", "材料"),
                "categories": item.get("categories", ["Missing Material"]),
                "status": item["status"],
                "recipient": item.get("recipient", "Not Provided"),
                "message_draft": follow_up_message(item),
            }
            for item in outstanding
        ]
        draft = {
            "schema_version": CONTROL_SCHEMA,
            "generated_at": utc_now(),
            "snapshot_captured_at": captured_at,
            "new_attachment_events": len(new_events),
            "pending_review": draft_items,
            "self_service_actions": [
                {
                    "candidate": item["candidate"],
                    "material_field": item["material_field"],
                    "material_component": item.get("material_component", "材料"),
                    "action": item["self_service_action"],
                }
                for item in self_service
            ],
            "verified_item_ids": sorted(set(verified_ids)),
            "confirmed_missing_item_ids": sorted({item["id"] for item in confirmed_missing}),
            "rebuild_candidate_ids": sorted(set(rebuild_ids)),
            "send_performed": False,
            "base_written": False,
        }
        write_json(control_dir / DRAFT_FILE, draft)
        return draft


def find_item(state: dict[str, Any], identifier: str) -> dict[str, Any]:
    for item in state["items"]:
        if item["id"] == identifier:
            return item
    raise SystemExit(f"未找到补件项：{identifier}")


def processing_status_for(item: dict[str, Any]) -> str:
    return "处理中" if item.get("kind") == "Cleanup Review" else "待补全"


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


def data_of(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("data")
    return value if isinstance(value, dict) else {}


def processing_field(base_token: str, table_id: str, profile: str | None, cwd: Path) -> dict[str, Any]:
    payload = cli_json(
        ["base", "+field-list", "--base-token", base_token, "--table-id", table_id, "--as", "user", "--json"],
        profile, cwd,
    )
    values = data_of(payload).get("fields") or data_of(payload).get("items") or data_of(payload).get("data")
    if not isinstance(values, list):
        raise SystemExit("Base 字段列表不可用；不发送催办。")
    for field in values:
        if isinstance(field, dict) and str(field.get("field_name", field.get("name", ""))) == "处理状态":
            if str(field.get("type")) not in {"select", "single_select"} or field.get("multiple") is True:
                raise SystemExit("处理状态 必须是可写单选字段；不发送催办。")
            options = field.get("options") or field.get("property", {}).get("options", [])
            names = {str(item.get("name")) for item in options if isinstance(item, dict)}
            if not {"已处理", "处理中", "待补全", "阻塞"} <= names:
                raise SystemExit("处理状态 缺少所需选项；不发送催办。")
            return field
    raise SystemExit("Base 缺少处理状态字段；不发送催办。")


def live_records(base_token: str, table_id: str, profile: str | None, cwd: Path) -> dict[str, dict[str, object]]:
    payload = cli_json(
        [
            "base", "+record-list", "--base-token", base_token, "--table-id", table_id,
            "--field-id", "候选人编号", "--field-id", "候选人姓名", "--field-id", "处理状态",
            "--limit", "200", "--as", "user", "--json",
        ], profile, cwd,
    )
    data = data_of(payload)
    values, field_ids, record_ids, names = data.get("data"), data.get("field_id_list"), data.get("record_id_list"), data.get("fields")
    if not isinstance(values, list) or not isinstance(field_ids, list) or not isinstance(record_ids, list) or not isinstance(names, list):
        raise SystemExit("Base 记录列表不可用；不发送催办。")
    if len(values) != len(record_ids):
        raise SystemExit("Base 记录与 ID 数组未对齐；不发送催办。")
    result: dict[str, dict[str, object]] = {}
    for record_id, row in zip(record_ids, values):
        if not isinstance(record_id, str) or not isinstance(row, list):
            raise SystemExit("Base 记录包含无效项目；不发送催办。")
        item = {str(field_id): row[index] if index < len(row) else None for index, field_id in enumerate(field_ids)}
        for index, name in enumerate(names):
            if isinstance(name, str):
                item[name] = row[index] if index < len(row) else None
        result[record_id] = item
    return result


def text_value(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list) and len(value) == 1:
        return text_value(value[0])
    if isinstance(value, dict):
        name = value.get("name")
        return name.strip() if isinstance(name, str) else ""
    return ""


def snapshot_record_id(state: dict[str, Any], item: dict[str, Any]) -> str:
    snapshot_value = state.get("last_snapshot_dir")
    if not isinstance(snapshot_value, str):
        raise SystemExit("催办控制缺少当前快照绑定；不发送催办。")
    manifest = read_json(Path(snapshot_value) / "base_snapshot_manifest.json", "base_snapshot_manifest.json")
    for record in manifest.get("records", []):
        if (
            isinstance(record, dict)
            and record.get("candidate_id") == item.get("candidate_id")
            and record.get("candidate") == item.get("candidate")
            and isinstance(record.get("record_id"), str)
        ):
            return str(record["record_id"])
    raise SystemExit("当前快照未找到催办候选人的 Base 记录；不发送催办。")


def processing_targets(state: dict[str, Any]) -> list[dict[str, str]]:
    """Derive every selected candidate's status from the same snapshot as the follow-up."""
    snapshot_value = state.get("last_snapshot_dir")
    if not isinstance(snapshot_value, str):
        raise SystemExit("催办控制缺少当前快照绑定；不发送催办。")
    snapshot_dir = Path(snapshot_value)
    manifest = read_json(snapshot_dir / "base_snapshot_manifest.json", "base_snapshot_manifest.json")
    attachments = manifest.get("attachments")
    if not isinstance(attachments, list) or not all(isinstance(item, dict) for item in attachments):
        raise SystemExit("当前快照附件结构无效；不发送催办。")
    rows = read_register(snapshot_dir)
    mappings = load_mappings(snapshot_dir)
    record_by_candidate_id = {
        str(record.get("candidate_id")): str(record.get("record_id"))
        for record in manifest.get("records", [])
        if isinstance(record, dict) and isinstance(record.get("candidate_id"), str) and isinstance(record.get("record_id"), str)
    }
    targets = {
        row["候选人编号"]: {
            "candidate_id": row["候选人编号"],
            "candidate": row["候选人姓名"],
            "record_id": record_by_candidate_id.get(row["候选人编号"], ""),
            "processing_status": "已处理",
        }
        for row in rows
    }
    if any(not item["record_id"] for item in targets.values()):
        raise SystemExit("当前快照缺少候选人的 Base 记录 ID；不发送催办。")
    candidate_ids_by_name = {item["candidate"]: candidate_id for candidate_id, item in targets.items()}
    rank = {"已处理": 0, "处理中": 1, "待补全": 2, "阻塞": 3}

    def promote(candidate: str, status: str) -> None:
        candidate_id = candidate_ids_by_name.get(candidate)
        if candidate_id and rank[status] > rank[targets[candidate_id]["processing_status"]]:
            targets[candidate_id]["processing_status"] = status

    for observation in build_base_materials.material_observations(rows, attachments, mappings):
        candidate = observation["scope"].split(" / ", 1)[0]
        promote(candidate, "阻塞" if observation["category"] == "Unmapped Material" else "待补全")
    for observation in build_base_materials.trace_observations(attachments, mappings):
        if observation["category"] not in CLEANUP_CATEGORIES:
            continue
        for candidate, _ in scope_locations(observation["scope"]):
            promote(candidate, "处理中")
    return [targets[candidate_id] for candidate_id in sorted(targets)]


def synchronize_processing_statuses(
    control_dir: Path, base_url: str, table_id: str, profile: str | None, *, event: str, item_id_value: str = "",
) -> dict[str, Any]:
    with control_lock(control_dir):
        state = load_state(control_dir)
        base_token, selected_table_id = build_base_materials.resolve_table_scope(base_url, table_id, profile, control_dir)
        field = processing_field(base_token, selected_table_id, profile, control_dir)
        field_id = str(field.get("field_id", field.get("id", "")))
        if not field_id:
            raise SystemExit("处理状态字段缺少字段 ID；不发送催办。")
        targets = processing_targets(state)
        records = live_records(base_token, selected_table_id, profile, control_dir)
        updates: dict[str, dict[str, object]] = {}
        for target in targets:
            record = records.get(target["record_id"])
            if record is None:
                raise SystemExit("候选人已不在当前 Base；不发送催办。")
            if text_value(record.get("候选人编号")) != target["candidate_id"] or text_value(record.get("候选人姓名")) != target["candidate"]:
                raise SystemExit("当前 Base 候选人身份与本次快照不一致；不发送催办。")
            if text_value(record.get(field_id)) != target["processing_status"]:
                updates[target["record_id"]] = {field_id: [target["processing_status"]]}
        if updates:
            response = cli_json(
                [
                    "base", "+record-batch-update", "--base-token", base_token, "--table-id", selected_table_id,
                    "--as", "user", "--json", json.dumps({"update_records": updates}, ensure_ascii=False),
                ], profile, control_dir,
            )
            if data_of(response).get("ignored_fields"):
                raise SystemExit("Base 未接受处理状态写入；不发送催办。")
        for attempt in range(PROCESSING_STATUS_READBACK_ATTEMPTS):
            verified = live_records(base_token, selected_table_id, profile, control_dir)
            if all(
                (record := verified.get(target["record_id"])) is not None
                and text_value(record.get(field_id)) == target["processing_status"]
                for target in targets
            ):
                break
            if attempt < PROCESSING_STATUS_READBACK_ATTEMPTS - 1:
                time.sleep(PROCESSING_STATUS_READBACK_DELAY_SECONDS)
        else:
            raise SystemExit("处理状态写入后回读不一致；不发送催办。")
        statuses_by_candidate_id = {target["candidate_id"]: target["processing_status"] for target in targets}
        for controlled_item in state["items"]:
            candidate_id = str(controlled_item.get("candidate_id", ""))
            if candidate_id in statuses_by_candidate_id:
                controlled_item["processing_status"] = statuses_by_candidate_id[candidate_id]
                controlled_item["processing_status_marked_at"] = utc_now()
        state["updated_at"] = utc_now()
        write_json(control_dir / STATE_FILE, state)
        append_audit(control_dir, event, item_id=item_id_value, statuses=targets, updated_records=len(updates))
        return {"processing_statuses": targets, "base_written": bool(updates)}


def prepare_preview(
    snapshot_dir: Path, control_dir: Path, base_url: str, table_id: str, profile: str | None,
    workspace_root: Path | None = None, output_dir: Path | None = None,
) -> dict[str, Any]:
    if (workspace_root is None) != (output_dir is None):
        raise SystemExit("绑定刷新输出位置时必须同时提供 workspace-root 和 output-dir。")
    if workspace_root is not None and output_dir is not None:
        bind_workspace(control_dir, snapshot_dir, workspace_root, output_dir)
    draft = plan(snapshot_dir, control_dir)
    synced = synchronize_processing_statuses(
        control_dir, base_url, table_id, profile, event="preview_processing_statuses_synced",
    )
    draft["processing_statuses"] = synced["processing_statuses"]
    draft["base_written"] = synced["base_written"]
    write_json(control_dir / DRAFT_FILE, draft)
    return draft


def mark_processing(
    control_dir: Path, base_url: str, table_id: str, identifier: str, confirm: str, profile: str | None,
) -> dict[str, Any]:
    if confirm != "MARK":
        raise SystemExit("发送前更新处理状态必须显式提供 --confirm MARK；未写入 Base，也不发送催办。")
    with control_lock(control_dir):
        state = load_state(control_dir)
        item = find_item(state, identifier)
        if item.get("status") not in {"Approved", "Sent"}:
            raise SystemExit(f"催办项当前状态为 {item.get('status')}，必须先批准后才能更新处理状态。")
        candidate_id = str(item["candidate_id"])
    synced = synchronize_processing_statuses(
        control_dir, base_url, table_id, profile, event="processing_status_marked", item_id_value=identifier,
    )
    statuses = {item["candidate_id"]: item["processing_status"] for item in synced["processing_statuses"]}
    return {
        "action": "mark-processing", "item_id": identifier, "processing_status": statuses[candidate_id],
        "processing_statuses": synced["processing_statuses"], "base_written": synced["base_written"], "send_performed": False,
    }


def approve(control_dir: Path, identifier: str, confirm: str, recipient: str = "") -> dict[str, Any]:
    if confirm != "APPROVE":
        raise SystemExit("批准催办必须显式提供 --confirm APPROVE；未发送任何消息。")
    with control_lock(control_dir):
        state = load_state(control_dir)
        item = find_item(state, identifier)
        if item.get("status") not in {"Pending Review", "Acknowledged"}:
            raise SystemExit(f"补件项当前状态为 {item.get('status')}，不能批准催办。")
        if recipient:
            item["recipient"] = normalize_recipient(recipient)
        if item.get("recipient", "Not Provided") == "Not Provided":
            raise SystemExit("负责人未配置；未批准且未发送任何消息。")
        item["status"] = "Approved"
        item["approved_at"] = utc_now()
        state["updated_at"] = utc_now()
        write_json(control_dir / STATE_FILE, state)
        append_audit(control_dir, "follow_up_approved", item_id=identifier, recipient=item["recipient"])
        return {"action": "approve", "item_id": identifier, "recipient": item["recipient"], "send_performed": False, "base_written": False}


def record_reply(control_dir: Path, identifier: str, reply_text: str) -> dict[str, Any]:
    if not reply_text.strip():
        raise SystemExit("回复正文不能为空。")
    with control_lock(control_dir):
        state = load_state(control_dir)
        item = find_item(state, identifier)
        if item.get("status") in TERMINAL_STATUSES:
            raise SystemExit("该补件项已到达终态，不能再记录未核验回复。")
        item["status"] = "Acknowledged"
        item["last_reply"] = reply_text
        item["last_reply_at"] = utc_now()
        state["updated_at"] = utc_now()
        write_json(control_dir / STATE_FILE, state)
        append_audit(control_dir, "reply_acknowledged", item_id=identifier, reply_kind="manual")
        return {"action": "record-reply", "item_id": identifier, "status": "Acknowledged", "verified": False, "send_performed": False, "base_written": False}


def record_sent(control_dir: Path, identifier: str, recipient_open_id: str, message_id: str, sent_at: str, confirm: str) -> dict[str, Any]:
    if confirm != "SENT":
        raise SystemExit("登记真实发送回执必须显式提供 --confirm SENT；未写入监听状态。")
    if not recipient_open_id.startswith("ou_"):
        raise SystemExit("监听私聊必须提供负责人的 open_id（ou_...）。")
    if not message_id.startswith("om_"):
        raise SystemExit("发送回执必须包含飞书 message_id（om_...）。")
    if not sent_at:
        raise SystemExit("发送回执必须包含 sent_at。")
    with control_lock(control_dir):
        state = load_state(control_dir)
        item = find_item(state, identifier)
        if item.get("status") != "Approved":
            raise SystemExit(f"补件项当前状态为 {item.get('status')}，不能登记发送回执。")
        item["status"] = "Sent"
        item["recipient_open_id"] = recipient_open_id
        item["sent_message_id"] = message_id
        item["sent_at"] = sent_at
        state["updated_at"] = utc_now()
        write_json(control_dir / STATE_FILE, state)
        append_audit(control_dir, "follow_up_sent", item_id=identifier, recipient_open_id=recipient_open_id, message_id=message_id)
        return {"action": "record-sent", "item_id": identifier, "status": "Sent", "send_performed": True, "base_written": False}


def update_reply_watch(control_dir: Path, status: str, **details: Any) -> None:
    """Persist a watcher lifecycle state separately from individual follow-up items."""
    with control_lock(control_dir):
        state = load_state(control_dir)
        watch = state.get("reply_watch")
        if not isinstance(watch, dict):
            watch = {}
        if status in {"Running", "Finished"}:
            # A resumed or completed watcher has a new current state. Keep an
            # old failure in the append-only audit log, but not as live state.
            watch.pop("error", None)
            watch.pop("error_type", None)
        watch.update({"status": status, "updated_at": utc_now(), **details})
        state["reply_watch"] = watch
        state["updated_at"] = utc_now()
        write_json(control_dir / STATE_FILE, state)


def reply_watch_deadline(state: dict[str, Any], timeout_seconds: int) -> str:
    """Keep one wall-clock deadline across watcher restarts and system sleep."""
    previous = state.get("reply_watch")
    if isinstance(previous, dict) and isinstance(previous.get("post_reminder_deadline_at"), str):
        return previous["post_reminder_deadline_at"]
    if isinstance(previous, dict) and isinstance(previous.get("deadline_at"), str):
        try:
            deadline = datetime.fromisoformat(previous["deadline_at"].replace("Z", "+00:00"))
        except ValueError as error:
            raise SystemExit("监听截止时间无效；不能静默重置窗口。") from error
        if deadline.tzinfo is None:
            raise SystemExit("监听截止时间缺少时区；不能静默重置窗口。")
        return deadline.astimezone(timezone.utc).isoformat()
    sent_at = []
    for item in state["items"]:
        value = item.get("sent_at")
        if not isinstance(value, str):
            continue
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise SystemExit("已发送催办缺少有效发送时间；不能重置监听窗口。") from error
        if timestamp.tzinfo is None:
            raise SystemExit("已发送催办时间缺少时区；不能重置监听窗口。")
        sent_at.append(timestamp.astimezone(timezone.utc))
    started_at = max(sent_at, default=datetime.now(timezone.utc))
    return (started_at + timedelta(seconds=timeout_seconds)).isoformat()


def start_post_reminder_window(control_dir: Path, timeout_seconds: int) -> str:
    """Open one final bounded reply window after the single timeout reminder round."""
    deadline = (datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)).isoformat()
    update_reply_watch(
        control_dir,
        "Running",
        post_reminder_started_at=utc_now(),
        post_reminder_deadline_at=deadline,
    )
    append_audit(control_dir, "reply_watch_post_reminder_window_started", deadline_at=deadline)
    return deadline


def launch_reply_watch(
    control_dir: Path, base_url: str, snapshot_root: Path, handoff_root: Path, profile: str,
    timeout_seconds: int, interval_seconds: int, download_workers: int,
) -> dict[str, Any]:
    """Detach the bounded watcher so an interactive command timeout cannot end it."""
    with control_lock(control_dir):
        state = load_state(control_dir)
        sent_count = sum(1 for item in state["items"] if item.get("status") == "Sent")
        if not sent_count:
            return {"status": "Not Started", "reason": "No Sent Items"}
        previous = state.get("reply_watch")
        if isinstance(previous, dict) and previous.get("status") == "Running":
            pid = previous.get("pid")
            if isinstance(pid, int):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    pass
                else:
                    return {"status": "Already Running", "pid": pid}
        deadline_at = reply_watch_deadline(state, timeout_seconds)

    command = [
        sys.executable, "-u", str(Path(__file__).with_name("watch_follow_up_replies.py")), "watch",
        "--control-dir", str(control_dir), "--lark-profile", profile, "--base-url", base_url,
        "--snapshot-root", str(snapshot_root), "--handoff-root", str(handoff_root),
        "--download-workers", str(download_workers), "--timeout-seconds", str(timeout_seconds),
        "--interval-seconds", str(interval_seconds), "--deadline-at", deadline_at,
    ]
    log_path = control_dir / WATCH_LOG_FILE
    with log_path.open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            command, cwd=control_dir, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    update_reply_watch(
        control_dir, "Running", pid=process.pid, timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds, deadline_at=deadline_at, log_path=str(log_path),
    )
    append_audit(
        control_dir, "reply_watch_detached", pid=process.pid, sent_items=sent_count,
        timeout_seconds=timeout_seconds, interval_seconds=interval_seconds, deadline_at=deadline_at,
    )
    return {"status": "Started", "pid": process.pid, "log_path": str(log_path)}


def recipient_open_id(recipient: str, profile: str | None, cwd: Path) -> str:
    """Resolve only an explicit recipient; names are never used as a lookup key."""
    normalized = normalize_recipient(recipient)
    if normalized.startswith("open_id:"):
        return normalized.removeprefix("open_id:")
    if not normalized.startswith("email:"):
        raise SystemExit("催办收件人必须是邮箱或 open_id；未发送消息。")
    email = normalized.removeprefix("email:")
    payload = cli_json(
        ["contact", "+search-user", "--query", email, "--as", "user", "--format", "json"],
        profile, cwd,
    )
    users = data_of(payload).get("users", [])
    matches = [
        user for user in users if isinstance(user, dict)
        and email.casefold() in {
            str(user.get("email", "")).casefold(), str(user.get("enterprise_email", "")).casefold(),
        }
        and isinstance(user.get("open_id"), str) and str(user["open_id"]).startswith("ou_")
    ]
    if len(matches) != 1:
        raise SystemExit(f"邮箱未解析为唯一飞书用户：{email}；未发送消息。")
    return str(matches[0]["open_id"])


def message_id(payload: dict[str, Any]) -> str:
    value = data_of(payload).get("message_id")
    if not isinstance(value, str) or not value.startswith("om_"):
        raise SystemExit("飞书发送回执缺少 message_id；已停止后续发送。")
    return value


def send_arguments(open_id: str, text: str, idempotency_key: str, *, dry_run: bool) -> list[str]:
    arguments = [
        "im", "+messages-send", "--as", "user", "--user-id", open_id,
        "--text", text, "--idempotency-key", idempotency_key, "--format", "json",
    ]
    if dry_run:
        arguments.append("--dry-run")
    return arguments


def dispatch_id(control_dir: Path) -> str:
    """Bind retries to one control directory without reusing another test's messages."""
    with control_lock(control_dir):
        state = load_state(control_dir)
        value = state.get("dispatch_id")
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{16}", value):
            value = secrets.token_hex(8)
            state["dispatch_id"] = value
            state["updated_at"] = utc_now()
            write_json(control_dir / STATE_FILE, state)
            append_audit(control_dir, "dispatch_created", dispatch_id=value)
        return value


def pending_send_items(control_dir: Path, profile: str | None) -> list[dict[str, str]]:
    """Return the exact preview items eligible for one confirmed dispatch."""
    with control_lock(control_dir):
        state = load_state(control_dir)
        planned: list[dict[str, str]] = []
        for item in state["items"]:
            if item.get("status") not in {"Pending Review", "Acknowledged"}:
                continue
            recipient = item.get("recipient")
            if not isinstance(recipient, str) or recipient == "Not Provided":
                continue
            open_id = recipient_open_id(recipient, profile, control_dir)
            draft = follow_up_message(item)
            planned.append({"item_id": str(item["id"]), "recipient_open_id": open_id, "message": draft})
        return planned


def send_timeout_reminders(control_dir: Path, profile: str) -> list[dict[str, str]]:
    """Send at most one new reminder for each unanswered, previously approved item."""
    with control_lock(control_dir):
        state = load_state(control_dir)
        dispatch = state.get("dispatch_id")
        if not isinstance(dispatch, str):
            raise SystemExit("催办控制缺少 dispatch_id；不发送超时提醒。")
        pending = [
            dict(item) for item in state["items"]
            if item.get("status") == "Sent"
            and isinstance(item.get("recipient_open_id"), str)
            and not item.get("timeout_reminder_message_id")
        ]
    sent: list[dict[str, str]] = []
    for item in pending:
        message = "提醒：" + follow_up_message(item)
        payload = cli_json(
            send_arguments(
                item["recipient_open_id"], message,
                f"followup-timeout-{dispatch}-{item['id']}", dry_run=False,
            ),
            profile,
            control_dir,
        )
        message_id_value = message_id(payload)
        with control_lock(control_dir):
            state = load_state(control_dir)
            current = find_item(state, str(item["id"]))
            if current.get("status") != "Sent" or current.get("timeout_reminder_message_id"):
                raise SystemExit("超时提醒发送后控制状态已变化；停止后续提醒。")
            current["timeout_reminder_message_id"] = message_id_value
            current["timeout_reminder_sent_at"] = utc_now()
            state["updated_at"] = utc_now()
            write_json(control_dir / STATE_FILE, state)
            append_audit(control_dir, "follow_up_timeout_reminded", item_id=str(item["id"]), message_id=message_id_value)
        sent.append({"item_id": str(item["id"]), "message_id": message_id_value})
    return sent


def record_watch_completion(
    control_dir: Path,
    *,
    timed_out: bool,
    refreshes: int,
    reminders: list[dict[str, str]],
    acknowledged_count: int,
    unanswered_count: int,
) -> dict[str, Any]:
    with control_lock(control_dir):
        state = load_state(control_dir)
        waiting = [item for item in state["items"] if item.get("status") == "Sent"]
        next_action = "await_reminded_replies" if waiting else "collect_hrd_opinions"
        if next_action == "collect_hrd_opinions":
            if reminders:
                message = (
                    f"补件监听已结束：初次十分钟未收到全部回复，已发送 {len(reminders)} 条再次催办；"
                    f"随后已收到并记录 {acknowledged_count} 项回复，本轮核验已完成。"
                )
            else:
                message = f"补件监听已结束：已收到并记录 {acknowledged_count} 项回复，本轮核验已完成。"
        else:
            message = (
                f"补件监听已结束：已收到并记录 {acknowledged_count} 项回复；"
                f"仍有 {unanswered_count} 项未回复，已发送 {len(reminders)} 条再次催办。"
            )
        completion = {
            "status": "Completed",
            "timed_out": timed_out,
            "refreshes": refreshes,
            "timeout_reminders": reminders,
            "next_action": next_action,
            "conversation_notice": {"status": "Pending", "message": message},
            "completed_at": utc_now(),
        }
        write_json(control_dir / COMPLETION_FILE, completion)
        state["watch_completion"] = completion
        state["updated_at"] = utc_now()
        write_json(control_dir / STATE_FILE, state)
        append_audit(control_dir, "reply_watch_completion_recorded", next_action=next_action, reminders=len(reminders))
        return completion


def claim_completion_notice(control_dir: Path) -> dict[str, Any]:
    """Return one terminal listener notice once to the foreground workflow."""
    with control_lock(control_dir):
        completion_path = control_dir / COMPLETION_FILE
        if not completion_path.exists():
            return {"notice_ready": False, "reason": "Not Completed"}
        completion = read_json(completion_path, COMPLETION_FILE)
        notice = completion.get("conversation_notice")
        if not isinstance(notice, dict) or notice.get("status") != "Pending" or not isinstance(notice.get("message"), str):
            return {"notice_ready": False, "reason": "Already Reported"}
        notice["status"] = "Claimed"
        notice["claimed_at"] = utc_now()
        completion["conversation_notice"] = notice
        write_json(completion_path, completion)
        state = load_state(control_dir)
        state["watch_completion"] = completion
        state["updated_at"] = utc_now()
        write_json(control_dir / STATE_FILE, state)
        append_audit(control_dir, "reply_watch_completion_notice_claimed")
        return {
            "notice_ready": True,
            "message": notice["message"],
            "next_action": completion.get("next_action"),
            "timed_out": completion.get("timed_out"),
        }


def hrd_queue_after_completion(control_dir: Path, base_url: str, table_id: str, profile: str) -> dict[str, Any]:
    """Enter the next controlled HRD queue after a clean follow-up terminal state."""
    state = load_state(control_dir)
    binding = state.get("workspace_binding")
    if not isinstance(binding, dict) or not isinstance(binding.get("workspace_root"), str) or not isinstance(binding.get("output_dir"), str):
        raise SystemExit("补件控制缺少已绑定的工作目录或材料包；不能进入 HRD 意见队列。")
    workspace_root = run_control.require_directory(Path(binding["workspace_root"]), "已选工作目录")
    output_dir = run_control.require_directory(Path(binding["output_dir"]), "当前材料包目录")
    run_control.validate_package_parent(output_dir, workspace_root)
    command = [
        sys.executable, str(Path(__file__).with_name("manage_hrd_review.py")), "queue",
        "--base-url", base_url, "--table-id", table_id, "--output-dir", str(output_dir),
        "--lark-profile", profile,
    ]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise SystemExit(
            "补件监听已完成，但无法进入 HRD 意见队列："
            + (result.stderr.strip() or result.stdout.strip() or "Unknown")
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise SystemExit("HRD 意见队列未返回有效 JSON；不能跳过后续处理。") from error
    if not isinstance(payload, dict):
        raise SystemExit("HRD 意见队列返回结构无效；不能跳过后续处理。")
    return payload


def wait_for_completion_notice(
    control_dir: Path, timeout_seconds: int, base_url: str, table_id: str, profile: str,
) -> dict[str, Any]:
    """Wait silently for the bounded listener, then return one terminal result and its required next step."""
    if timeout_seconds < 1:
        raise SystemExit("timeout-seconds 必须至少为 1。")
    deadline = time.monotonic() + timeout_seconds
    while True:
        if (control_dir / COMPLETION_FILE).exists():
            result = claim_completion_notice(control_dir)
            if result.get("notice_ready"):
                response = {"action": "wait-for-completion", **result}
                if result.get("next_action") == "collect_hrd_opinions":
                    response["hrd_queue"] = hrd_queue_after_completion(control_dir, base_url, table_id, profile)
                return response
            raise SystemExit("监听终态已被其他前台操作领取；不重复回显。")
        state = load_state(control_dir)
        watch = state.get("reply_watch")
        if isinstance(watch, dict) and watch.get("status") == "Failed":
            raise SystemExit(f"补件监听失败：{watch.get('error', 'Unknown')}")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SystemExit("等待监听终态超时；本地监听仍可能继续运行，未重复发送任何消息。")
        time.sleep(min(1, remaining))


def read_completion_result(
    control_dir: Path, base_url: str, table_id: str, profile: str,
) -> dict[str, Any]:
    """Recover one persisted terminal result when the foreground wait lost its command output."""
    completion_path = control_dir / COMPLETION_FILE
    if not completion_path.exists():
        state = load_state(control_dir)
        watch = state.get("reply_watch")
        return {
            "action": "read-completion-result",
            "terminal_ready": False,
            "watch_status": watch.get("status") if isinstance(watch, dict) else "Not Started",
        }
    completion = read_json(completion_path, COMPLETION_FILE)
    notice = completion.get("conversation_notice")
    if not isinstance(notice, dict) or not isinstance(notice.get("message"), str):
        raise SystemExit("监听完成记录缺少终态消息；不能假定未收到回复。")
    response = {
        "action": "read-completion-result",
        "terminal_ready": True,
        "message": notice["message"],
        "next_action": completion.get("next_action"),
        "timed_out": completion.get("timed_out"),
        "notice_status": notice.get("status"),
    }
    if completion.get("next_action") == "collect_hrd_opinions":
        response["hrd_queue"] = hrd_queue_after_completion(control_dir, base_url, table_id, profile)
    return response


def dispatch_and_watch(
    control_dir: Path, base_url: str, table_id: str, snapshot_root: Path, handoff_root: Path,
    profile: str | None, confirm: str, timeout_seconds: int, interval_seconds: int, download_workers: int,
    launcher: Any | None = None,
) -> dict[str, Any]:
    """One explicit business confirmation sends every previewed item, then watches replies."""
    if confirm != "SEND_AND_WATCH":
        raise SystemExit("发送并监听必须显式提供 --confirm SEND_AND_WATCH；未发送消息。")
    if not profile:
        raise SystemExit("发送并监听需要明确的飞书 profile；未发送消息。")
    require_workspace_binding(control_dir, snapshot_root, handoff_root)
    synced = synchronize_processing_statuses(
        control_dir, base_url, table_id, profile, event="dispatch_processing_statuses_synced",
    )
    planned = pending_send_items(control_dir, profile)
    if not planned:
        watch_result = launch_reply_watch(
            control_dir, base_url, snapshot_root, handoff_root, profile,
            timeout_seconds, interval_seconds, download_workers,
        )
        return {
            "action": "dispatch-and-watch", "send_performed": False, "sent_items": [],
            "unresolved_recipients": [], "watch": watch_result,
            "task_heartbeat_required": False,
            "foreground_wait_required": watch_result.get("status") in {"Started", "Already Running"},
            **synced,
        }

    current_dispatch_id = dispatch_id(control_dir)

    # Every outgoing message is preflighted before any irreversible send occurs.
    for item in planned:
        cli_json(
            send_arguments(item["recipient_open_id"], item["message"], f"followup-{current_dispatch_id}-{item['item_id']}", dry_run=True),
            profile, control_dir,
        )

    sent_items: list[dict[str, str]] = []
    for item in planned:
        approve(control_dir, item["item_id"], "APPROVE")
        payload = cli_json(
            send_arguments(item["recipient_open_id"], item["message"], f"followup-{current_dispatch_id}-{item['item_id']}", dry_run=False),
            profile, control_dir,
        )
        sent_message_id = message_id(payload)
        record_sent(
            control_dir, item["item_id"], item["recipient_open_id"], sent_message_id, utc_now(), "SENT",
        )
        sent_items.append({"item_id": item["item_id"], "recipient_open_id": item["recipient_open_id"], "message_id": sent_message_id})

    append_audit(
        control_dir, "reply_watch_launching", sent_items=sent_items,
        timeout_seconds=timeout_seconds, interval_seconds=interval_seconds,
    )
    try:
        watch_result = (
            launcher(control_dir, base_url, snapshot_root, handoff_root, profile, timeout_seconds, interval_seconds, download_workers)
            if launcher is not None
            else launch_reply_watch(
                control_dir, base_url, snapshot_root, handoff_root, profile,
                timeout_seconds, interval_seconds, download_workers,
            )
        )
    except BaseException as error:
        append_audit(
            control_dir, "reply_watch_failed", error_type=type(error).__name__, error=str(error),
        )
        raise
    return {
        "action": "dispatch-and-watch", "send_performed": bool(sent_items), "sent_items": sent_items,
        "watch": watch_result, "processing_statuses": synced["processing_statuses"],
        "task_heartbeat_required": False,
        "foreground_wait_required": watch_result.get("status") in {"Started", "Already Running"},
        "base_written": bool(synced["base_written"]) or bool(watch_result.get("base_written")),
    }


def dispatch_and_wait(
    control_dir: Path, base_url: str, table_id: str, snapshot_root: Path, handoff_root: Path,
    profile: str, confirm: str, timeout_seconds: int, interval_seconds: int, download_workers: int,
    wait_timeout_seconds: int,
) -> dict[str, Any]:
    """Send once, then keep this foreground command open until one terminal result exists."""
    dispatch = dispatch_and_watch(
        control_dir, base_url, table_id, snapshot_root, handoff_root,
        profile, confirm, timeout_seconds, interval_seconds, download_workers,
    )
    if not dispatch.get("foreground_wait_required"):
        return {"action": "dispatch-and-wait", "dispatch": dispatch, "terminal": None}
    terminal = wait_for_completion_notice(
        control_dir, wait_timeout_seconds, base_url, table_id, profile,
    )
    return {"action": "dispatch-and-wait", "dispatch": dispatch, "terminal": terminal}


def resume_watch(
    control_dir: Path, base_url: str, snapshot_root: Path, handoff_root: Path, profile: str,
    timeout_seconds: int, interval_seconds: int, download_workers: int,
) -> dict[str, Any]:
    """Resume monitoring existing receipts without approving or sending any message."""
    require_workspace_binding(control_dir, snapshot_root, handoff_root)
    watch_result = launch_reply_watch(
        control_dir, base_url, snapshot_root, handoff_root, profile,
        timeout_seconds, interval_seconds, download_workers,
    )
    return {
        "action": "resume-watch",
        "send_performed": False,
        "watch": watch_result,
        "task_heartbeat_required": False,
        "foreground_wait_required": watch_result.get("status") in {"Started", "Already Running"},
        "base_written": False,
    }


def export_resolution(control_dir: Path, output_dir: Path) -> dict[str, Any]:
    """Export only exact, current-snapshot confirmations into the Candidate Pack input root."""
    output_dir = run_control.require_directory(output_dir, "output 目录")
    run_control.validate_output_boundary(
        output_dir,
        {
            "materials.zip", "candidate_process_register.xlsx", "base_intake_observations.json",
            run_control.RUN_CONTROL_FILE, run_control.OPERATION_LOG_FILE, "follow_up_resolution.json",
        },
        allowed_directories={run_control.PROCESS_RECORDS_DIR},
    )
    observations = read_json(output_dir / "base_intake_observations.json", "base_intake_observations.json")
    captured_at = observations.get("captured_at")
    if observations.get("schema_version") not in {1, 2} or not isinstance(captured_at, str):
        raise SystemExit("Base 观测缺少受控快照时间，不能导出补件确认。")
    with control_lock(control_dir):
        state = load_state(control_dir)
        if state.get("last_snapshot_captured_at") != captured_at:
            raise SystemExit("补件控制状态未绑定到当前 output 的 Base 快照；不能导出确认。")
        confirmations = []
        for item in state["items"]:
            if item.get("kind", "Material Follow-up") != "Material Follow-up" or item.get("status") != "Confirmed Missing":
                continue
            sent_message_id = item.get("sent_message_id")
            if (
                not confirmed_missing_reply(item.get("confirmed_missing_reply"))
                or not isinstance(sent_message_id, str)
                or (
                    item.get("confirmed_missing_reply_to") != sent_message_id
                    and item.get("confirmed_missing_root_id") != sent_message_id
                )
            ):
                raise SystemExit("Confirmed Missing 缺少原催办消息关联证据；不能导出确认。")
            confirmations.append({
                key: item.get(key)
                for key in (
                    "candidate", "material_field", "material_component", "recipient_open_id", "sent_message_id",
                    "confirmed_missing_message_id", "confirmed_missing_reply", "confirmed_missing_reply_to", "confirmed_missing_root_id",
                )
            })
        payload = {"schema_version": 1, "snapshot_captured_at": captured_at, "confirmations": confirmations}
        write_json(output_dir / "follow_up_resolution.json", payload)
        append_audit(control_dir, "resolution_exported", snapshot_captured_at=captured_at, confirmations=len(confirmations))
    return {"action": "export-resolution", "output_dir": str(output_dir), "confirmations": len(confirmations), "base_written": False}


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate and maintain D13 missing-material follow-up control state without sending messages.")
    subparsers = parser.add_subparsers(dest="action", required=True)
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--snapshot-dir", required=True, type=Path)
    plan_parser.add_argument("--control-dir", required=True, type=Path)
    plan_parser.add_argument("--base-url", required=True)
    plan_parser.add_argument("--table-id", required=True)
    plan_parser.add_argument("--lark-profile")
    plan_parser.add_argument("--workspace-root", required=True, type=Path)
    plan_parser.add_argument("--output-dir", required=True, type=Path)
    approve_parser = subparsers.add_parser("approve")
    approve_parser.add_argument("--control-dir", required=True, type=Path)
    approve_parser.add_argument("--item-id", required=True)
    approve_parser.add_argument("--confirm", default="")
    approve_parser.add_argument("--recipient", default="", help="email:... or open_id:ou_... supplied for this follow-up")
    mark_parser = subparsers.add_parser("mark-processing")
    mark_parser.add_argument("--control-dir", required=True, type=Path)
    mark_parser.add_argument("--base-url", required=True)
    mark_parser.add_argument("--table-id", required=True)
    mark_parser.add_argument("--item-id", required=True)
    mark_parser.add_argument("--confirm", default="")
    mark_parser.add_argument("--lark-profile")
    reply_parser = subparsers.add_parser("record-reply")
    reply_parser.add_argument("--control-dir", required=True, type=Path)
    reply_parser.add_argument("--item-id", required=True)
    reply_parser.add_argument("--reply-text", required=True)
    sent_parser = subparsers.add_parser("record-sent")
    sent_parser.add_argument("--control-dir", required=True, type=Path)
    sent_parser.add_argument("--item-id", required=True)
    sent_parser.add_argument("--recipient-open-id", required=True)
    sent_parser.add_argument("--message-id", required=True)
    sent_parser.add_argument("--sent-at", required=True)
    sent_parser.add_argument("--confirm", default="")
    export_parser = subparsers.add_parser("export-resolution")
    export_parser.add_argument("--control-dir", required=True, type=Path)
    export_parser.add_argument("--output-dir", required=True, type=Path)
    for action_name in ("dispatch-and-watch", "dispatch-and-wait"):
        dispatch_parser = subparsers.add_parser(action_name)
        dispatch_parser.add_argument("--control-dir", required=True, type=Path)
        dispatch_parser.add_argument("--base-url", required=True)
        dispatch_parser.add_argument("--table-id", required=True)
        dispatch_parser.add_argument("--snapshot-root", required=True, type=Path)
        dispatch_parser.add_argument("--handoff-root", required=True, type=Path)
        dispatch_parser.add_argument("--lark-profile", required=True)
        dispatch_parser.add_argument("--confirm", default="")
        dispatch_parser.add_argument("--timeout-seconds", type=int, default=600)
        dispatch_parser.add_argument("--interval-seconds", type=int, default=15)
        dispatch_parser.add_argument("--download-workers", type=int, default=8)
        if action_name == "dispatch-and-wait":
            dispatch_parser.add_argument("--wait-timeout-seconds", type=int, default=1260)
    resume_parser = subparsers.add_parser("resume-watch")
    resume_parser.add_argument("--control-dir", required=True, type=Path)
    resume_parser.add_argument("--base-url", required=True)
    resume_parser.add_argument("--snapshot-root", required=True, type=Path)
    resume_parser.add_argument("--handoff-root", required=True, type=Path)
    resume_parser.add_argument("--lark-profile", required=True)
    resume_parser.add_argument("--timeout-seconds", type=int, default=600)
    resume_parser.add_argument("--interval-seconds", type=int, default=15)
    resume_parser.add_argument("--download-workers", type=int, default=8)
    notice_parser = subparsers.add_parser("claim-completion-notice")
    notice_parser.add_argument("--control-dir", required=True, type=Path)
    wait_parser = subparsers.add_parser("wait-for-completion")
    wait_parser.add_argument("--control-dir", required=True, type=Path)
    wait_parser.add_argument("--timeout-seconds", type=int, default=1260)
    wait_parser.add_argument("--base-url", required=True)
    wait_parser.add_argument("--table-id", required=True)
    wait_parser.add_argument("--lark-profile", required=True)
    result_parser = subparsers.add_parser("read-completion-result")
    result_parser.add_argument("--control-dir", required=True, type=Path)
    result_parser.add_argument("--base-url", required=True)
    result_parser.add_argument("--table-id", required=True)
    result_parser.add_argument("--lark-profile", required=True)
    args = parser.parse_args()
    if args.action == "plan":
        snapshot_dir = args.snapshot_dir.resolve()
        control_dir = require_control_dir(args.control_dir, snapshot_dir)
        result = prepare_preview(
            snapshot_dir, control_dir, args.base_url, args.table_id, args.lark_profile,
            args.workspace_root, args.output_dir,
        )
    else:
        control_dir = args.control_dir.resolve()
        if not control_dir.is_dir():
            raise SystemExit("control-dir 不存在。")
        if args.action == "approve":
            result = approve(control_dir, args.item_id, args.confirm, args.recipient)
        elif args.action == "mark-processing":
            result = mark_processing(control_dir, args.base_url, args.table_id, args.item_id, args.confirm, args.lark_profile)
        elif args.action == "record-reply":
            result = record_reply(control_dir, args.item_id, args.reply_text)
        elif args.action == "record-sent":
            result = record_sent(control_dir, args.item_id, args.recipient_open_id, args.message_id, args.sent_at, args.confirm)
        elif args.action == "claim-completion-notice":
            result = claim_completion_notice(control_dir)
        elif args.action == "wait-for-completion":
            result = wait_for_completion_notice(
                control_dir, args.timeout_seconds, args.base_url, args.table_id, args.lark_profile,
            )
        elif args.action == "read-completion-result":
            result = read_completion_result(control_dir, args.base_url, args.table_id, args.lark_profile)
        elif args.action == "dispatch-and-watch":
            result = dispatch_and_watch(
                control_dir, args.base_url, args.table_id, args.snapshot_root, args.handoff_root,
                args.lark_profile, args.confirm, args.timeout_seconds, args.interval_seconds, args.download_workers,
            )
        elif args.action == "dispatch-and-wait":
            result = dispatch_and_wait(
                control_dir, args.base_url, args.table_id, args.snapshot_root, args.handoff_root,
                args.lark_profile, args.confirm, args.timeout_seconds, args.interval_seconds, args.download_workers,
                args.wait_timeout_seconds,
            )
        elif args.action == "resume-watch":
            result = resume_watch(
                control_dir, args.base_url, args.snapshot_root, args.handoff_root,
                args.lark_profile, args.timeout_seconds, args.interval_seconds, args.download_workers,
            )
        else:
            result = export_resolution(control_dir, args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
