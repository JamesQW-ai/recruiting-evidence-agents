"""Shared run-isolation and audit controls for D13 agents."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


RUN_CONTROL_FILE = "run_control.json"
OPERATION_LOG_FILE = "operation_log.jsonl"
PROCESS_RECORDS_DIR = "过程记录"
PROCESS_RECORD_FILES = {
    "运行摘要.json", "材料映射.jsonl", "材料核验.json", "运行日志.jsonl", "校验和.sha256",
}
PACKAGE_DIR_RE = re.compile(r"^招聘材料包_(第[^_]+批)_(\d{8})_(\d{4})$")
SYSTEM_METADATA = {".DS_Store"}
PLUGIN_ROOT = Path(__file__).parents[1].resolve()
HANDOFF_ARTIFACTS = {
    "materials_zip": "materials.zip",
    "controlled_register": "candidate_process_register.xlsx",
    "base_observations": "base_intake_observations.json",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_batch_label(value: str) -> str:
    value = value.strip()
    if not value or value in {"Unknown", "Not Provided"}:
        return "本批"
    if value.startswith("第") and value.endswith("批"):
        number = re.search(r"\d+", value)
        if number:
            return f"第{number.group(0)}批"
        return value
    number = re.search(r"\d+", value)
    if number:
        return f"第{number.group(0)}批"
    return f"第{value}批"


def business_batch_label(value: str) -> str:
    return safe_batch_label(value)


def display_batch_label(value: str) -> str:
    """Render the business batch label consistently in every user-facing report."""
    label = business_batch_label(value)
    number = re.fullmatch(r"第(\d+)批", label)
    return f"第 {number.group(1)} 批" if number else label


def validate_package_directory_name(path: Path, batch: str) -> None:
    match = PACKAGE_DIR_RE.fullmatch(path.name)
    if match is None or match.group(1) != business_batch_label(batch):
        raise SystemExit(
            f"交付目录必须命名为 招聘材料包_{business_batch_label(batch)}_YYYYMMDD_HHmm：{path.name}"
        )


def validate_package_parent(path: Path, workspace_root: Path | None = None) -> None:
    """Reject an invalid package parent before the builder creates any directory."""
    package = path.resolve()
    try:
        package.relative_to(PLUGIN_ROOT)
    except ValueError:
        pass
    else:
        raise SystemExit("材料包不得写入插件目录；请选择工作目录或使用桌面默认位置。")
    if workspace_root is None:
        raise SystemExit("构建材料包前必须先选择工作目录；未选择时请先确认桌面默认位置。")
    workspace = require_directory(workspace_root, "工作目录")
    if package.parent != workspace:
        raise SystemExit(f"材料包必须直接位于工作目录下：{workspace / package.name}")


def validate_package_location(path: Path, batch: str, workspace_root: Path | None = None) -> None:
    """Keep a user package at the selected workspace root, outside plugin source."""
    validate_package_parent(path, workspace_root)
    validate_package_directory_name(path.resolve(), batch)


def candidate_key(row: dict[str, str]) -> tuple[str, str, str]:
    return (
        row.get("批次", "").strip() or "Unknown",
        row.get("候选人编号", "").strip() or "Unknown",
        row.get("候选人姓名", "").strip() or "Unknown",
    )


def validate_candidate_keys(rows: Iterable[dict[str, str]]) -> list[tuple[str, str, str]]:
    keys = [candidate_key(row) for row in rows]
    if any(batch == "Unknown" or candidate_id == "Unknown" for batch, candidate_id, _ in keys):
        raise SystemExit("受控登记表必须为每个候选人提供唯一的批次和候选人编号。")
    if len({(batch, candidate_id) for batch, candidate_id, _ in keys}) != len(keys):
        raise SystemExit("同一批次中存在重复候选人编号，已阻断以避免跨记录串联。")
    names = {}
    for batch, candidate_id, name in keys:
        names.setdefault((batch, name), []).append(candidate_id)
    ambiguous_names = [key for key, ids in names.items() if len(ids) > 1]
    if ambiguous_names:
        formatted = ", ".join(f"{batch}/{name}" for batch, name in ambiguous_names)
        raise SystemExit(f"同一批次存在同名候选人（{formatted}）；必须先提供可区分的稳定候选人记录。")
    batches = {batch for batch, _, _ in keys}
    if len(batches) != 1:
        raise SystemExit("一次运行只能处理一个批次；请为不同批次使用独立输出目录和独立会话上下文。")
    return keys


def input_fingerprint(entries: Iterable[tuple[str, str, int]]) -> str:
    payload = "\n".join(f"{path}\0{digest}\0{size}" for path, digest, size in sorted(entries))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def batch_id(batch: str, keys: Iterable[tuple[str, str, str]], fingerprint: str) -> str:
    identity = "\n".join("\0".join(key) for key in sorted(keys))
    digest = hashlib.sha256(f"{batch}\n{identity}\n{fingerprint}".encode("utf-8")).hexdigest()[:12].upper()
    return f"D13-{digest}"


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def artifact_record(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise SystemExit(f"不可变交接缺少普通文件 {label}：{path}")
    return {"sha256": sha256_path(path), "byte_size": path.stat().st_size}


def plugin_manifest_hash() -> str:
    manifest = Path(__file__).parents[1] / ".codex-plugin" / "plugin.json"
    return artifact_record(manifest, "插件 manifest")["sha256"]


def handoff_fingerprint(*, source_fingerprint: str, candidate_keys: list[dict[str, str]], handoff: dict[str, Any]) -> str:
    payload = {
        "source_fingerprint": source_fingerprint,
        "candidate_keys": candidate_keys,
        "handoff": handoff,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def handoff_records(root: Path) -> dict[str, Any]:
    handoff: dict[str, Any] = {
        "materials_zip": artifact_record(root / HANDOFF_ARTIFACTS["materials_zip"], "materials.zip"),
        "controlled_register": artifact_record(root / HANDOFF_ARTIFACTS["controlled_register"], "candidate_process_register.xlsx"),
        "plugin_manifest_sha256": plugin_manifest_hash(),
    }
    observations = root / HANDOFF_ARTIFACTS["base_observations"]
    handoff["base_observations"] = (
        artifact_record(observations, "base_intake_observations.json")
        if observations.exists() else {"status": "Not Present"}
    )
    return handoff


def write_run_control(path: Path, *, batch: str, keys: list[tuple[str, str, str]], fingerprint: str) -> dict[str, Any]:
    candidate_keys = [
        {"batch": batch_value, "candidate_id": candidate_id, "candidate": candidate}
        for batch_value, candidate_id, candidate in keys
    ]
    handoff = handoff_records(path.parent)
    payload: dict[str, Any] = {
        "schema_version": 2,
        "created_at": utc_now(),
        "batch": batch,
        "batch_label": business_batch_label(batch),
        "batch_id": batch_id(batch, keys, fingerprint),
        "source_fingerprint": fingerprint,
        "candidate_keys": candidate_keys,
        "handoff": handoff,
        "handoff_fingerprint": handoff_fingerprint(
            source_fingerprint=fingerprint, candidate_keys=candidate_keys, handoff=handoff,
        ),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def load_run_control(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise SystemExit(f"缺少 {RUN_CONTROL_FILE}；禁止复用其他批次或会话的上下文。") from error
    except json.JSONDecodeError as error:
        raise SystemExit(f"{RUN_CONTROL_FILE} 不是有效 JSON：{error}") from error
    if (
        payload.get("schema_version") != 2
        or not isinstance(payload.get("candidate_keys"), list)
        or not isinstance(payload.get("handoff"), dict)
        or not isinstance(payload.get("handoff_fingerprint"), str)
    ):
        raise SystemExit(f"{RUN_CONTROL_FILE} 使用了不支持的结构。")
    return payload


def validate_handoff(root: Path, control: dict[str, Any]) -> None:
    """Reject mixed or altered material controls before Candidate Pack reads them."""
    handoff = control["handoff"]
    expected_fingerprint = handoff_fingerprint(
        source_fingerprint=str(control.get("source_fingerprint", "")),
        candidate_keys=control["candidate_keys"], handoff=handoff,
    )
    if control["handoff_fingerprint"] != expected_fingerprint:
        raise SystemExit("run_control.json 的不可变交接摘要不一致；请从授权源重新构建。")
    if handoff.get("plugin_manifest_sha256") != plugin_manifest_hash():
        raise SystemExit("插件 manifest 与交接控制不一致；请使用同一发布版本从授权源重新构建。")
    for key in ("materials_zip", "controlled_register"):
        expected = handoff.get(key)
        if not isinstance(expected, dict) or expected.get("status"):
            raise SystemExit(f"run_control.json 缺少 {HANDOFF_ARTIFACTS[key]} 的不可变摘要。")
        actual = artifact_record(root / HANDOFF_ARTIFACTS[key], HANDOFF_ARTIFACTS[key])
        if actual != expected:
            raise SystemExit(f"{HANDOFF_ARTIFACTS[key]} 与同次交接摘要不一致；请从授权源重新构建。")
    expected_observations = handoff.get("base_observations")
    observations = root / HANDOFF_ARTIFACTS["base_observations"]
    if expected_observations == {"status": "Not Present"}:
        if observations.exists():
            raise SystemExit("Base 材料观察未绑定到 run_control.json；请从授权源重新构建。")
    elif isinstance(expected_observations, dict):
        actual = artifact_record(observations, HANDOFF_ARTIFACTS["base_observations"])
        if actual != expected_observations:
            raise SystemExit("base_intake_observations.json 与同次交接摘要不一致；请从授权源重新构建。")
    else:
        raise SystemExit("run_control.json 缺少 Base 材料观察的不可变状态。")


def bind_base_observations(path: Path) -> dict[str, Any]:
    """Extend a just-built material handoff with the Base observation artifact."""
    payload = load_run_control(path)
    root = path.parent
    observations = artifact_record(root / HANDOFF_ARTIFACTS["base_observations"], "base_intake_observations.json")
    payload["handoff"]["base_observations"] = observations
    payload["handoff_fingerprint"] = handoff_fingerprint(
        source_fingerprint=payload["source_fingerprint"],
        candidate_keys=payload["candidate_keys"], handoff=payload["handoff"],
    )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def require_directory(path: Path, label: str, *, create: bool = False) -> Path:
    """Return a canonical directory root without accepting a caller-controlled symlink."""
    if path.is_symlink():
        raise SystemExit(f"{label} 必须是非符号链接目录：{path}")
    if path.exists():
        if not path.is_dir():
            raise SystemExit(f"{label} 必须是非符号链接目录：{path}")
    elif create:
        path.mkdir(parents=True, exist_ok=False)
    else:
        raise SystemExit(f"{label} 不存在：{path}")
    return path.resolve()


def require_empty_directory(path: Path, label: str) -> Path:
    root = require_directory(path, label, create=not path.exists())
    if any(root.iterdir()):
        raise SystemExit(f"{label} 必须为空：{root}")
    return root


def paths_overlap(first: Path, second: Path) -> bool:
    """Return whether two canonical directory roots are equal or nested in either direction."""
    try:
        first.relative_to(second)
        return True
    except ValueError:
        pass
    try:
        second.relative_to(first)
        return True
    except ValueError:
        return False


def regular_files(root: Path, *, recursive: bool) -> list[Path]:
    """Inventory only regular files below a trusted root; reject every link and special entry."""
    candidates = root.rglob("*") if recursive else root.iterdir()
    files: list[Path] = []
    for path in sorted(candidates):
        if path.is_symlink():
            raise SystemExit(f"授权目录不允许符号链接：{path}")
        if path.is_file():
            files.append(path)
        elif not path.is_dir():
            raise SystemExit(f"授权目录包含非普通文件：{path}")
    return files


def validate_flat_source_boundary(input_dir: Path) -> list[Path]:
    root = require_directory(input_dir, "input 目录")
    files = regular_files(root, recursive=False)
    nested = [path for path in root.iterdir() if path.is_dir()]
    if nested:
        raise SystemExit("input 必须是扁平授权目录；不读取嵌套目录：" + ", ".join(path.name for path in nested))
    unexpected_hidden = [path.name for path in files if path.name.startswith(".") and path.name not in SYSTEM_METADATA]
    if unexpected_hidden:
        raise SystemExit("input 包含不允许的隐藏文件：" + ", ".join(sorted(unexpected_hidden)))
    return [path for path in files if path.name not in SYSTEM_METADATA]


def validate_base_snapshot_source_boundary(input_dir: Path, manifest_path: Path) -> list[Path]:
    """Allow only the fixed nested layout produced by the Base snapshot adapter."""
    root = require_directory(input_dir, "Base 快照 input 目录")
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise SystemExit("Base 嵌套输入必须具有同一快照根下的普通 manifest 文件。")
    files = regular_files(root, recursive=True)
    directories = [path for path in root.rglob("*") if path.is_dir() and not path.is_symlink()]
    invalid_directories = []
    for directory in directories:
        parts = directory.relative_to(root).parts
        if parts == ("base-source",):
            continue
        if len(parts) == 2 and parts[0] == "base-source" and len(parts[1]) == 4 and parts[1].isdigit():
            continue
        invalid_directories.append(directory.relative_to(root).as_posix())
    if invalid_directories:
        raise SystemExit("Base 快照 input 存在不允许的目录层级：" + ", ".join(sorted(invalid_directories)))
    invalid_files = []
    for path in files:
        relative = path.relative_to(root)
        if path.name in SYSTEM_METADATA:
            continue
        if path.name.startswith("."):
            invalid_files.append(relative.as_posix())
            continue
        if len(relative.parts) == 1:
            continue
        if len(relative.parts) == 3 and relative.parts[0] == "base-source" and relative.parts[1].isdigit() and len(relative.parts[1]) == 4:
            continue
        invalid_files.append(relative.as_posix())
    if invalid_files:
        raise SystemExit("Base 快照 input 存在不允许的文件层级：" + ", ".join(sorted(invalid_files)))
    return [path for path in files if path.name not in SYSTEM_METADATA]


def validate_output_boundary(output_dir: Path, allowed: set[str], *, allowed_directories: set[str] | None = None) -> None:
    root = require_directory(output_dir, "output 目录")
    allowed_directories = allowed_directories or set()
    entries = list(root.iterdir())
    symlinks = sorted(path.name for path in entries if path.is_symlink())
    directories = sorted(path.name for path in entries if path.is_dir() and not path.is_symlink() and path.name not in allowed_directories)
    special = sorted(path.name for path in entries if not path.is_file() and not path.is_dir() and not path.is_symlink())
    actual = {path.name for path in entries if path.is_file()}
    unexpected = sorted(actual - allowed - SYSTEM_METADATA)
    if symlinks or directories or special or unexpected:
        violations = (
            [f"符号链接 {name}" for name in symlinks]
            + [f"目录 {name}" for name in directories]
            + [f"特殊条目 {name}" for name in special]
            + [f"文件 {name}" for name in unexpected]
        )
        raise SystemExit("输出目录结构不符合受控边界，不读取越界条目：" + ", ".join(violations))


def process_records_dir(output_dir: Path) -> Path:
    path = output_dir / PROCESS_RECORDS_DIR
    if path.is_symlink() or not path.is_dir():
        raise SystemExit("过程记录目录不存在或不是普通目录。")
    entries = list(path.iterdir())
    if any(item.is_symlink() or not item.is_file() for item in entries):
        raise SystemExit("过程记录目录包含非普通文件。")
    unexpected = {item.name for item in entries} - PROCESS_RECORD_FILES
    if unexpected:
        raise SystemExit("过程记录目录包含不允许的文件：" + ", ".join(sorted(unexpected)))
    return path


def refresh_process_records(output_dir: Path) -> None:
    """Refresh the non-secret operational log copy and checksums kept with delivery."""
    if not (output_dir / PROCESS_RECORDS_DIR).exists():
        # Legacy test/replay handoffs predate the persistent diagnostic bundle.
        return
    process_dir = process_records_dir(output_dir)
    log = output_dir / OPERATION_LOG_FILE
    if log.is_file():
        (process_dir / "运行日志.jsonl").write_bytes(log.read_bytes())
    files = [item for item in sorted(process_dir.iterdir()) if item.name != "校验和.sha256"]
    root_files = [output_dir / name for name in ("materials.zip", OPERATION_LOG_FILE) if (output_dir / name).is_file()]
    lines = [f"{sha256_path(item)}  过程记录/{item.name}" for item in files]
    lines.extend(f"{sha256_path(item)}  {item.name}" for item in root_files)
    (process_dir / "校验和.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")


def append_operation_log(output_dir: Path, event: str, **details: Any) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    record = {"at": utc_now(), "event": event, **details}
    path = output_dir / OPERATION_LOG_FILE
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def remove_formal_outputs(output_dir: Path) -> None:
    for name in (
        "materials.zip", "candidate_process_register.xlsx", "base_intake_observations.json",
        "follow_up_resolution.json",
        "consolidated_candidate_pack.md", "candidate_pack.html", "source_manifest.md",
        "missing_evidence_list.md",
    ):
        path = output_dir / name
        if path.exists():
            path.unlink()
