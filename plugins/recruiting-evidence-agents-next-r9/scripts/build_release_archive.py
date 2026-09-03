#!/usr/bin/env python3
"""Build a deterministic, installable release-candidate archive without runtime output."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Iterable


EXCLUDED_PARTS = {"04_outputs", "__pycache__", ".pytest_cache"}
EXCLUDED_NAMES = {".DS_Store"}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def source_files(plugin_root: Path) -> Iterable[Path]:
    for path in sorted(plugin_root.rglob("*")):
        relative = path.relative_to(plugin_root)
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.name in EXCLUDED_NAMES or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            raise SystemExit(f"发布候选包不接受符号链接：{relative}")
        if path.is_file():
            yield path


def marketplace_payload(plugin_name: str, marketplace_name: str) -> bytes:
    payload = {
        "name": marketplace_name,
        "interface": {"displayName": marketplace_name},
        "plugins": [{
            "name": plugin_name,
            "source": {"source": "local", "path": f"./plugins/{plugin_name}"},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
            "category": "Productivity",
        }],
    }
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def zip_info(name: str, source: Path | None = None) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    mode = stat.S_IMODE(source.stat().st_mode) if source is not None else 0o644
    info.external_attr = (stat.S_IFREG | mode) << 16
    return info


def build_archive(plugin_root: Path, output_path: Path, release_name: str | None = None) -> tuple[Path, Path]:
    plugin_root = plugin_root.resolve()
    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"无法读取插件 manifest：{error}") from error
    plugin_name = manifest.get("name")
    if not isinstance(plugin_name, str) or plugin_name != plugin_root.name:
        raise SystemExit("插件目录名与 manifest name 必须一致。")
    release_name = release_name or f"{plugin_name}-release-candidate"
    if not re.fullmatch(r"[A-Za-z0-9_-]+", release_name):
        raise SystemExit("发布候选名称只能包含字母、数字、下划线和连字符。")

    archive_root = release_name
    entries: dict[str, tuple[bytes, Path | None]] = {
        f"{archive_root}/.agents/plugins/marketplace.json": (
            marketplace_payload(plugin_name, f"{release_name}-marketplace"),
            None,
        ),
    }
    for path in source_files(plugin_root):
        relative = PurePosixPath(path.relative_to(plugin_root).as_posix())
        entry = f"{archive_root}/plugins/{plugin_name}/{relative}"
        entries[entry] = (path.read_bytes(), path)

    checksums = "".join(
        f"{sha256_bytes(content)}  {name}\n"
        for name, (content, _) in sorted(entries.items())
    )
    checksum_entry = f"{archive_root}/SHA256SUMS.txt"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, (content, source) in sorted(entries.items()):
            archive.writestr(zip_info(name, source), content)
        archive.writestr(zip_info(checksum_entry), checksums.encode("utf-8"))

    sidecar = output_path.with_suffix(output_path.suffix + ".sha256")
    sidecar.write_text(
        f"{sha256_bytes(output_path.read_bytes())}  {output_path.name}\n",
        encoding="utf-8",
    )
    return output_path, sidecar


def verify_archive(archive_path: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        bad_member = archive.testzip()
        if bad_member is not None:
            raise SystemExit(f"发布候选包损坏：{bad_member}")
        names = [info.filename for info in archive.infolist() if not info.is_dir()]
        if any(any(part in EXCLUDED_PARTS for part in PurePosixPath(name).parts) for name in names):
            raise SystemExit("发布候选包包含运行输出或缓存目录。")
        if any(name.endswith(".pyc") or name.endswith("/.DS_Store") for name in names):
            raise SystemExit("发布候选包包含系统缓存文件。")
        checksum_names = [name for name in names if name.endswith("/SHA256SUMS.txt")]
        if len(checksum_names) != 1:
            raise SystemExit("发布候选包缺少唯一的 SHA256SUMS.txt。")
        for line in archive.read(checksum_names[0]).decode("utf-8").splitlines():
            digest, name = line.split("  ", 1)
            if name not in names or sha256_bytes(archive.read(name)) != digest:
                raise SystemExit(f"发布候选包校验和不匹配：{name}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a deterministic D13 release-candidate ZIP.")
    parser.add_argument("--plugin-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--release-name", help="Archive root and isolated marketplace identity.")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    archive, sidecar = build_archive(args.plugin_root, args.output.resolve(), args.release_name)
    verify_archive(archive)
    print(json.dumps({"archive": str(archive), "sha256_sidecar": str(sidecar)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
