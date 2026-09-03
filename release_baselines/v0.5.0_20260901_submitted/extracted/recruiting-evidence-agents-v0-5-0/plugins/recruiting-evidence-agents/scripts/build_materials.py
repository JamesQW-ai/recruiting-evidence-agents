#!/usr/bin/env python3
"""Build the D13 material handoff from a flat authorized input repository."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path, PurePosixPath

import resource_limits
import run_control

try:
    from pypdf import PdfReader
except ImportError:  # The record remains readable only by filename/register evidence.
    PdfReader = None


RECRUITING_TERMS = (
    "候选人",
    "简历",
    "履历",
    "技术作业",
    "技术题",
    "技术面试",
    "bp面试",
    "hrd面试",
    "面试",
    "评估",
    "评阅",
    "评价",
    "resume",
    "interview",
)
OUT_OF_SCOPE_TERMS = (
    "品牌",
    "内容投放",
    "预算",
    "渠道",
    "数据库",
    "索引",
    "慢查询",
    "绿植",
    "养护",
    "办公区",
)
ROLE_PATTERNS = (
    ("Resume", ("简历", "履历", "resume", "cv")),
    ("Technical Assignment Evaluation", ("技术作业评估", "技术作业评价", "技术作业评阅", "技术题反馈", "ta评价")),
    ("Technical Interview", ("技术面试", "techinterview")),
    ("BP Interview", ("bp面试", "bpinterview")),
    ("HRD Interview", ("hrd面试", "hrdinterview")),
    ("Shared Technical Assignment Prompt", ("技术作业题目", "技术题目（共用）")),
)
XML_NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def cell_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference).group(0)
    result = 0
    for char in letters:
        result = result * 26 + ord(char) - ord("A") + 1
    return result - 1


def read_xlsx_rows(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(item.itertext()) for item in root.findall("x:si", XML_NS)]
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    rows: list[list[str]] = []
    for row in sheet.findall(".//x:row", XML_NS):
        values: list[str] = []
        for cell in row.findall("x:c", XML_NS):
            index = cell_index(cell.get("r"))
            while len(values) <= index:
                values.append("")
            value = cell.find("x:v", XML_NS)
            raw = value.text if value is not None else ""
            values[index] = shared[int(raw)] if cell.get("t") == "s" and raw else raw
        rows.append(values)
    return rows


def register_facts(path: Path) -> tuple[list[str], dict[str, list[dict[str, str]]]]:
    rows = read_xlsx_rows(path)
    if len(rows) < 3:
        return [], {}
    headers = rows[1]
    candidate_index = headers.index("候选人姓名")
    candidates: list[str] = []
    by_filename: dict[str, list[dict[str, str]]] = {}
    for row in rows[2:]:
        if candidate_index >= len(row) or not row[candidate_index]:
            continue
        candidate = row[candidate_index]
        candidates.append(candidate)
        for index, value in enumerate(row):
            if not value.startswith("input/"):
                continue
            filename = PurePosixPath(value).name
            submitted_at = "Not Provided"
            if index + 1 < len(row) and "提交时间" in headers[index + 1]:
                submitted_at = row[index + 1] or "Not Provided"
            by_filename.setdefault(filename, []).append(
                {"candidate": candidate, "submitted_at": submitted_at}
            )
    return list(dict.fromkeys(candidates)), by_filename


def register_identity_rows(path: Path) -> list[dict[str, str]]:
    rows = read_xlsx_rows(path)
    if len(rows) < 3:
        return []
    headers = rows[1]
    required = {"候选人编号", "候选人姓名", "批次"}
    if not required.issubset(headers):
        raise SystemExit("submission register must contain batch and stable candidate fields")
    name_index = headers.index("候选人姓名")
    return [
        {header: (values[index] if index < len(values) else "") for index, header in enumerate(headers)}
        for values in rows[2:]
        if len(values) > name_index and values[name_index]
    ]


def text_for(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt", ".csv"}:
        return path.read_text(encoding="utf-8", errors="replace"), "Readable Text"
    if suffix == ".pdf":
        with path.open("rb") as stream:
            header = stream.read(5)
        if header != b"%PDF-":
            return "", "PDF Invalid"
        if PdfReader is not None:
            try:
                return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages), "Readable PDF"
            except Exception:
                return "", "PDF Unreadable"
        return "", "PDF Content Unverified"
    return "", "Not Readable as Text"


def found_candidates(value: str, candidates: list[str]) -> list[str]:
    return [candidate for candidate in candidates if candidate in value]


def role_for(value: str) -> tuple[str, str]:
    lowered = value.lower()
    for role, terms in ROLE_PATTERNS:
        if any(term.lower() in lowered for term in terms):
            return role, "Content or Filename"
    return "Unknown", "Not Provided"


def audio_receipt(path: Path, source_path: str, source_hash: str, script_path: Path) -> tuple[dict[str, object], str]:
    model = os.environ.get("D13_ASR_MODEL")
    if not model:
        return (
            {
                "original_path": source_path,
                "audio_sha256": source_hash,
                "status": "Audio Content Unverified - ASR Not Configured",
                "temporary_artifacts_deleted": True,
            },
            "",
        )
    command = [
        sys.executable,
        str(script_path),
        "--input",
        str(path),
        "--model",
        model,
        "--engine-bin",
        os.environ.get("D13_ASR_ENGINE_BIN", "whisper-cli"),
        "--language",
        os.environ.get("D13_ASR_LANGUAGE", "zh"),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    try:
        receipt = json.loads(result.stdout)
    except json.JSONDecodeError:
        receipt = {"status": "Audio Content Unverified - Invalid ASR Response"}
    receipt["original_path"] = source_path
    receipt["audio_sha256"] = source_hash
    receipt["temporary_artifacts_deleted"] = True
    return receipt, str(receipt.pop("transcript", ""))


def safe_member_name(*parts: str) -> str:
    cleaned = [re.sub(r"[\\/:]", "_", part) for part in parts]
    return str(PurePosixPath("input", *cleaned))


def source_member_name(candidate: str, path: Path, input_dir: Path) -> str:
    del input_dir
    return safe_member_name(candidate, path.name)


def unique_source_member_name(candidate: str, path: Path, input_dir: Path, used_members: set[str]) -> str:
    """Keep every source in one candidate directory without overwriting same-name files."""
    member = source_member_name(candidate, path, input_dir)
    if member not in used_members:
        used_members.add(member)
        return member
    path_member = PurePosixPath(member)
    for ordinal in range(2, 10000):
        candidate_member = str(path_member.with_name(f"{path_member.stem}__source-{ordinal:04d}{path_member.suffix}"))
        if candidate_member not in used_members:
            used_members.add(candidate_member)
            return candidate_member
    raise SystemExit(f"too many same-name sources for {member}")


def write_zip_entry(archive: zipfile.ZipFile, name: str, source: Path) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    with source.open("rb") as input_stream, archive.open(info, "w") as output_stream:
        for block in iter(lambda: input_stream.read(resource_limits.STREAM_CHUNK_BYTES), b""):
            output_stream.write(block)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the D13 flat-repository material handoff.")
    parser.add_argument("--workspace-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--mapping-output",
        type=Path,
        help="optional JSONL control file for a trusted upstream adapter; never packaged",
    )
    parser.add_argument(
        "--allow-nested-input",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()
    workspace_root = run_control.require_directory(args.workspace_root, "workspace-root")
    input_dir = workspace_root / "input"
    if input_dir.is_symlink() or not input_dir.is_dir():
        raise SystemExit(f"input 必须是 workspace-root 下的非符号链接目录：{input_dir}")
    if run_control.paths_overlap(input_dir.resolve(), args.output_dir.resolve()):
        raise SystemExit("output 目录不能等于、包含或位于授权 input 目录内。")
    output_dir = run_control.require_empty_directory(args.output_dir, "output 目录")

    if args.allow_nested_input:
        files = run_control.validate_base_snapshot_source_boundary(
            input_dir, workspace_root / "base_snapshot_manifest.json",
        )
    else:
        files = run_control.validate_flat_source_boundary(input_dir)
    resource_limits.validate_source_files(files, output_dir)
    workbooks = [path for path in files if path.suffix.lower() == ".xlsx"]
    if len(workbooks) != 1:
        raise SystemExit("exactly one submission-register workbook is required")
    candidates, registrations = register_facts(workbooks[0])
    if not candidates:
        raise SystemExit("submission register contains no candidate names")
    identity_rows = register_identity_rows(workbooks[0])
    identity_keys = run_control.validate_candidate_keys(identity_rows)
    input_entries = [
        ("input/" + path.relative_to(input_dir).as_posix(), sha256(path), path.stat().st_size)
        for path in files
    ]
    mappings: list[dict[str, object]] = []
    receipts: list[dict[str, object]] = []
    packaged: list[tuple[str, Path, str]] = []
    used_member_names: set[str] = set()
    script_path = Path(__file__).with_name("transcribe_audio.py")
    for path in files:
        original_path = "input/" + path.relative_to(input_dir).as_posix()
        source_hash = sha256(path)
        text, readability = text_for(path)
        filename_candidates = found_candidates(path.name, candidates)
        content_candidates = found_candidates(text, candidates)
        registration_rows = registrations.get(path.name, [])
        register_candidates = list(dict.fromkeys(row["candidate"] for row in registration_rows))
        submitted_at = registration_rows[0]["submitted_at"] if len(registration_rows) == 1 else "Not Provided"
        role, role_basis = role_for(f"{path.name}\n{text}")
        transcription_status = "Not Applicable"
        if path.suffix.lower() in {".mp3", ".m4a", ".wav", ".aac"}:
            receipt, transcript = audio_receipt(path, original_path, source_hash, script_path)
            receipts.append(receipt)
            transcription_status = str(receipt["status"])
            content_candidates = list(dict.fromkeys(content_candidates + found_candidates(transcript, candidates)))
            if role == "Unknown":
                role, role_basis = role_for(f"{path.name}\n{transcript}")

        combined = f"{path.name}\n{text}".lower()
        out_scope_signals = [term for term in OUT_OF_SCOPE_TERMS if term.lower() in combined]
        recruiting_signal = any(term.lower() in combined for term in RECRUITING_TERMS)
        is_register = path == workbooks[0]
        is_shared = role == "Shared Technical Assignment Prompt"
        classification = "Candidate Material"
        package_status = "Excluded"
        contradiction_notes: list[str] = []
        if is_register:
            resolved_candidate, basis, member = "Not Applicable", "Submission Register", "Not Packaged"
            classification = "Submission Register"
        elif is_shared:
            resolved_candidate, basis, member = "Not Applicable", "Shared Prompt", safe_member_name(path.name)
            classification = "Shared Material"
            package_status = "Included"
        elif not content_candidates and not filename_candidates and role == "Unknown" and out_scope_signals and not recruiting_signal:
            resolved_candidate, basis, member = "Not Applicable", "Out of Scope Signals", "Not Packaged"
            classification = "Out of Scope"
            package_status = "Excluded"
            contradiction_notes.append("Explicit non-recruiting signals: " + ", ".join(out_scope_signals))
        else:
            ranked = ((content_candidates, "Content"), (filename_candidates, "Filename"))
            resolved_candidate, basis = "Unknown", "Not Provided"
            for values, candidate_basis in ranked:
                if len(values) == 1:
                    resolved_candidate, basis = values[0], candidate_basis
                    break
                if len(values) > 1:
                    resolved_candidate, basis = "Unknown", f"Ambiguous {candidate_basis}"
                    break
            if len(content_candidates) == 1 and len(filename_candidates) == 1 and content_candidates != filename_candidates:
                contradiction_notes.append("Filename/content identity disagreement resolved by content")
            if resolved_candidate == "Unknown":
                classification = "Unmapped" if not basis.startswith("Ambiguous") else "Ambiguous"
                member = "Not Packaged"
            else:
                member = unique_source_member_name(resolved_candidate, path, input_dir, used_member_names)
                package_status = "Included"

        mapping_status = classification if package_status == "Excluded" else (
            f"Mapped to {basis} Candidate" if resolved_candidate != "Unknown" and resolved_candidate != "Not Applicable" else classification
        )
        mappings.append(
            {
                "original_path": original_path,
                "sha256": source_hash,
                "byte_size": path.stat().st_size,
                "readability": readability,
                "filename_candidate": filename_candidates or "Not Provided",
                "content_candidate": content_candidates or "Not Provided",
                "register_candidate": register_candidates or "Not Provided",
                "resolved_candidate": resolved_candidate,
                "identity_basis": basis,
                "material_role": role,
                "role_basis": role_basis,
                "classification": classification,
                "mapping_status": mapping_status,
                "package_status": package_status,
                "packaged_path": member,
                "submitted_at": submitted_at,
                "time_provenance": "Register" if submitted_at != "Not Provided" else "Not Provided",
                "transcription_status": transcription_status,
                "contradiction_notes": contradiction_notes or "None",
            }
        )
        if package_status == "Included":
            packaged.append((member, path, source_hash))

    archive_path = output_dir / "materials.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member, path, _ in packaged:
            write_zip_entry(archive, member, path)

    with zipfile.ZipFile(archive_path) as archive:
        resource_limits.validate_zip_infos(archive)
        names = set(archive.namelist())
        expected = {member for member, _, _ in packaged}
        if names != expected:
            raise SystemExit("archive source entries do not match the pure package set")
        if any(not name.startswith("input/") for name in names):
            raise SystemExit("archive contains a member outside input/")
        direct_input_members = [name for name in names if len(PurePosixPath(name).parts) == 2]
        if len(direct_input_members) != 1:
            raise SystemExit("archive must contain exactly one shared prompt at input root")
        if any(PurePosixPath(name).parts[1] in {"candidates", "shared", "unmapped", "registers"} for name in names):
            raise SystemExit("archive contains a legacy derived directory")
        if any(len(PurePosixPath(name).parts) != 3 for name in names if name not in direct_input_members):
            raise SystemExit("archive candidate entries must be directly under input/<candidate>/")
        if any(row["packaged_path"] in names for row in mappings if row["package_status"] == "Excluded"):
            raise SystemExit("archive contains an excluded source entry")
        for member, path, digest in packaged:
            if resource_limits.sha256_zip_member(archive, archive.getinfo(member)) != digest or digest != sha256(path):
                raise SystemExit(f"checksum mismatch: {member}")
    register_path = output_dir / "candidate_process_register.xlsx"
    with workbooks[0].open("rb") as input_stream, register_path.open("wb") as output_stream:
        for block in iter(lambda: input_stream.read(resource_limits.STREAM_CHUNK_BYTES), b""):
            output_stream.write(block)
    if sha256(register_path) != sha256(workbooks[0]):
        raise SystemExit("process-register copy checksum mismatch")
    run_control.write_run_control(
        output_dir / run_control.RUN_CONTROL_FILE,
        batch=identity_keys[0][0],
        keys=identity_keys,
        fingerprint=run_control.input_fingerprint(input_entries),
    )
    if args.mapping_output is not None:
        args.mapping_output.parent.mkdir(parents=True, exist_ok=True)
        with args.mapping_output.open("w", encoding="utf-8") as stream:
            for row in mappings:
                stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    control = run_control.load_run_control(output_dir / run_control.RUN_CONTROL_FILE)
    run_control.append_operation_log(
        output_dir,
        "material_pack_completed",
        input_files=len(files),
        excluded_files=sum(row["package_status"] == "Excluded" for row in mappings),
        batch_id=control["batch_id"],
    )
    print(json.dumps({"materials_zip": str(archive_path), "process_register": str(register_path), "run_control": str(output_dir / run_control.RUN_CONTROL_FILE), "input_files": len(files), "candidate_materials": sum(row["classification"] == "Candidate Material" and row["package_status"] == "Included" for row in mappings), "shared_materials": sum(row["classification"] == "Shared Material" and row["package_status"] == "Included" for row in mappings), "excluded_files": sum(row["package_status"] == "Excluded" for row in mappings), "audio_receipts_checked": len(receipts)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
