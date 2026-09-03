#!/usr/bin/env python3
"""Build a versioned, auditable evidence package beside the runtime plugin."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path


CASE_ID = re.compile(r"(?:KN|HO)-[0-9]{3}$")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+$")
FORBIDDEN_EVIDENCE_PARTS = {"sealed_control", "sealed_control_list", "creator_notes"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def validate_case_manifest(payload: object) -> list[dict[str, object]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise SystemExit("案例清单必须包含 cases 数组。")
    cases = payload["cases"]
    if len(cases) != 16:
        raise SystemExit("冻结证据包必须恰好包含 12 个 Known 和 4 个 Holdout 案例。")
    seen: set[str] = set()
    known = holdout = 0
    for item in cases:
        if not isinstance(item, dict):
            raise SystemExit("案例清单项必须是对象。")
        case_id = item.get("id")
        kind = item.get("kind")
        evidence = item.get("evidence")
        if not isinstance(case_id, str) or not CASE_ID.fullmatch(case_id) or case_id in seen:
            raise SystemExit("案例 ID 必须是唯一的中性 KN-xxx 或 HO-xxx 标识。")
        if kind not in {"Known", "Holdout"}:
            raise SystemExit(f"案例 {case_id} 缺少有效 kind。")
        if (kind == "Known") != case_id.startswith("KN-"):
            raise SystemExit(f"案例 {case_id} 的 ID 与 kind 不一致。")
        if not isinstance(evidence, list) or not evidence or not all(isinstance(value, str) and value for value in evidence):
            raise SystemExit(f"案例 {case_id} 必须列出至少一个证据文件。")
        seen.add(case_id)
        known += kind == "Known"
        holdout += kind == "Holdout"
    if known != 12 or holdout != 4:
        raise SystemExit("冻结证据包必须恰好包含 12 个 Known 和 4 个 Holdout 案例。")
    return cases


def reject_forbidden_evidence(relative: Path) -> None:
    if any(part.lower() in FORBIDDEN_EVIDENCE_PARTS for part in relative.parts):
        raise SystemExit(f"冻结证据包不得包含受限控制材料：{relative}")


def copy_case_evidence(cases: list[dict[str, object]], evidence_root: Path, destination: Path) -> list[Path]:
    copied: list[Path] = []
    for case in cases:
        case_id = str(case["id"])
        for raw_path in case["evidence"]:
            relative = Path(str(raw_path))
            if relative.is_absolute() or ".." in relative.parts:
                raise SystemExit(f"案例 {case_id} 的证据路径必须相对于 evidence root：{relative}")
            reject_forbidden_evidence(relative)
            source = evidence_root / relative
            if not source.is_file() or source.is_symlink() or not inside(source, evidence_root):
                raise SystemExit(f"案例 {case_id} 的证据文件不可用：{relative}")
            target = destination / "evidence" / case_id / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            copied.append(target)
    return copied


def copy_review_document(source: Path, destination: Path, label: str) -> Path:
    if not source.is_file() or source.is_symlink():
        raise SystemExit(f"{label} 必须是常规文件。")
    text = source.read_text(encoding="utf-8")
    if not text.strip() or "/Users/" in text or "/private/" in text or "file://" in text:
        raise SystemExit(f"{label} 必须非空且不得包含开发机绝对路径。")
    shutil.copyfile(source, destination)
    return destination


def write_checksums(root: Path) -> None:
    entries = [path for path in sorted(root.rglob("*")) if path.is_file() and path.name != "SHA256SUMS.txt"]
    lines = [f"{sha256(path)}  {path.relative_to(root).as_posix()}" for path in entries]
    (root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def verify_package(root: Path) -> None:
    manifest = json.loads((root / "case_manifest.json").read_text(encoding="utf-8"))
    cases = validate_case_manifest(manifest)
    expected = {(str(case["id"]), Path(str(item))) for case in cases for item in case["evidence"]}
    evidence_root = root / "evidence"
    actual = {
        (relative.parts[0], Path(*relative.parts[1:]))
        for path in evidence_root.rglob("*")
        if path.is_file()
        for relative in [path.relative_to(evidence_root)]
    }
    if expected != actual:
        raise SystemExit("冻结证据包的案例证据与清单不一致。")
    for line in (root / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        digest, relative = line.split("  ", 1)
        path = root / relative
        if not path.is_file() or sha256(path) != digest:
            raise SystemExit(f"冻结证据包校验和不匹配：{relative}")


def build_package(
    *, plugin_zip: Path, case_manifest: Path, evidence_root: Path,
    decision_log: Path, boundary_matrix: Path, version: str, output_dir: Path,
) -> Path:
    if not VERSION.fullmatch(version):
        raise SystemExit("冻结证据版本必须为 x.y.z。")
    if not plugin_zip.is_file() or plugin_zip.is_symlink():
        raise SystemExit("必须提供一个常规的正式插件 ZIP。")
    if output_dir.exists():
        raise SystemExit("冻结证据输出目录必须尚不存在，避免覆盖已冻结产物。")
    cases = validate_case_manifest(json.loads(case_manifest.read_text(encoding="utf-8")))
    with tempfile.TemporaryDirectory(dir=output_dir.parent) as temporary:
        stage = Path(temporary) / f"Recruiting-Evidence-v{version}-Evidence"
        stage.mkdir()
        (stage / "case_manifest.json").write_text(
            json.dumps({"version": version, "cases": cases}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        copy_review_document(decision_log, stage / "DECISION_LOG.md", "Decision Log")
        copy_review_document(boundary_matrix, stage / "BOUNDARY_MATRIX.md", "Boundary Matrix")
        copied = copy_case_evidence(cases, evidence_root, stage)
        (stage / "INDEX.md").write_text(
            "# Recruiting Evidence Frozen Evidence\n\n"
            f"- Version: `v{version}`\n"
            f"- Plugin archive: `{plugin_zip.name}`\n"
            f"- Plugin SHA-256: `{sha256(plugin_zip)}`\n"
            "- Cases: `12 Known + 4 Holdout`\n"
            "- Holdout evaluation: `Pending TA Lead release and independent replay`\n"
            "- Runtime plugin, test fixture, source pack, sealed controls, and creator notes are separate artifacts.\n",
            encoding="utf-8",
        )
        if len(copied) < 16:
            raise SystemExit("每个案例至少需要一项实际证据。")
        write_checksums(stage)
        verify_package(stage)
        shutil.move(str(stage), output_dir)
    return output_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a versioned frozen evidence package.")
    parser.add_argument("--plugin-zip", type=Path, required=True)
    parser.add_argument("--case-manifest", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--decision-log", type=Path, required=True)
    parser.add_argument("--boundary-matrix", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = build_package(
        plugin_zip=args.plugin_zip.resolve(), case_manifest=args.case_manifest.resolve(),
        evidence_root=args.evidence_root.resolve(), decision_log=args.decision_log.resolve(),
        boundary_matrix=args.boundary_matrix.resolve(), version=args.version, output_dir=args.output_dir.resolve(),
    )
    print(json.dumps({"evidence_package": str(output), "version": args.version}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
