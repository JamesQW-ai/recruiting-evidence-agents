#!/usr/bin/env python3
"""Create a self-contained formal-identity archive from the development plugin."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import tempfile
from pathlib import Path

import build_release_archive


DEVELOPMENT_ONLY = {
    "DEVELOPMENT_BASELINE.md",
    "test_control",
    "release",
    "tests",
    "04_outputs",
    "__pycache__",
    ".pytest_cache",
}
DEVELOPMENT_ONLY_FILES = {
    Path("scripts/build_formal_release.py"),
    Path("scripts/build_release_archive.py"),
    Path("scripts/run_workflow_acceptance.sh"),
}
PUBLIC_TEXT_ROOTS = (Path("README.md"), Path("docs"), Path("skills"), Path("assets"))
PUBLIC_STAGE_PATTERN = re.compile(r"\bD1[23]\b|20\d{6}", re.IGNORECASE)


def reject_development_residue(root: Path, source_root: Path) -> None:
    source_text = str(source_root.resolve())
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in DEVELOPMENT_ONLY for part in relative.parts) or path.suffix == ".pyc":
            raise SystemExit(f"正式包包含开发残留：{relative}")
        if path.is_file() and path.suffix in {".md", ".json", ".html"}:
            text = path.read_text(encoding="utf-8")
            if source_text in text or "@media (max-width" in text:
                raise SystemExit(f"正式包包含开发路径或移动端规则：{relative}")
    for public_root in PUBLIC_TEXT_ROOTS:
        target = root / public_root
        paths = [target] if target.is_file() else target.rglob("*") if target.is_dir() else []
        for path in paths:
            if not path.is_file() or path.suffix not in {".md", ".yaml", ".html"}:
                continue
            if PUBLIC_STAGE_PATTERN.search(path.read_text(encoding="utf-8")):
                raise SystemExit(f"正式包面向安装者的内容不得包含阶段或开发日期标识：{path.relative_to(root)}")


def build_formal_release(source_root: Path, release_summary: Path, version: str, output: Path) -> tuple[Path, Path]:
    if not re.fullmatch(r"0\.5\.0\+codex\.[0-9]{14}", version):
        raise SystemExit("正式版本必须为 0.5.0+codex.YYYYMMDDHHMMSS。")
    summary = release_summary.read_text(encoding="utf-8")
    if not summary.strip() or re.search(r"/Users/|/private/|file://", summary):
        raise SystemExit("发布摘要必须非空且不得包含开发机绝对路径。")
    with tempfile.TemporaryDirectory() as temp_dir:
        stage = Path(temp_dir) / "recruiting-evidence-agents"
        shutil.copytree(source_root, stage, ignore=shutil.ignore_patterns(*DEVELOPMENT_ONLY, "*.pyc"))
        for relative in DEVELOPMENT_ONLY_FILES:
            (stage / relative).unlink(missing_ok=True)
        manifest_path = stage / ".codex-plugin" / "plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["name"] = "recruiting-evidence-agents"
        manifest["version"] = version
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (stage / "RELEASE_SUMMARY.md").write_text(summary, encoding="utf-8")
        reject_development_residue(stage, source_root)
        return build_release_archive.build_archive(stage, output, "recruiting-evidence-agents-v0-5-0")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the final D13 formal identity package.")
    parser.add_argument("--source-plugin", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--release-summary", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    archive, sidecar = build_formal_release(args.source_plugin.resolve(), args.release_summary.resolve(), args.version, args.output.resolve())
    build_release_archive.verify_archive(archive)
    print(json.dumps({"archive": str(archive), "sha256_sidecar": str(sidecar), "version": args.version}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
