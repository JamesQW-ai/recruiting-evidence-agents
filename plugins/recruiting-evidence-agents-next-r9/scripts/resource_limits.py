"""Non-negotiable local resource limits for D13 material processing."""

from __future__ import annotations

import hashlib
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path
from typing import Iterable


MIB = 1024 * 1024
GIB = 1024 * MIB
MAX_AUDIO_BYTES = 256 * MIB
MAX_AUDIO_SECONDS = 60 * 60
MAX_SOURCE_BATCH_BYTES = 2 * GIB
MIN_FREE_OUTPUT_BYTES = 512 * MIB
MAX_ZIP_MEMBERS = 4096
MAX_ZIP_EXPANDED_BYTES = 2 * GIB
MAX_ZIP_COMPRESSION_RATIO = 100
STREAM_CHUNK_BYTES = MIB
AUDIO_SUFFIXES = {".aac", ".m4a", ".mp3", ".wav"}


def display_bytes(value: int) -> str:
    if value >= GIB:
        return f"{value / GIB:.2f} GB"
    return f"{value / MIB:.2f} MB"


def required_free_bytes(source_bytes: int) -> int:
    return source_bytes + MIN_FREE_OUTPUT_BYTES


def require_free_space(target: Path, source_bytes: int, label: str) -> None:
    root = target if target.exists() else target.parent
    if not root.exists() or not root.is_dir():
        raise SystemExit(f"{label} 的磁盘预检目录不可用：{root}")
    required = required_free_bytes(source_bytes)
    available = shutil.disk_usage(root).free
    if available < required:
        raise SystemExit(
            f"{label} 可用空间不足：需要至少 {display_bytes(required)}，当前仅 {display_bytes(available)}。"
        )


def require_audio_file_within_limit(path: Path) -> None:
    size = path.stat().st_size
    if size > MAX_AUDIO_BYTES:
        raise SystemExit(f"音频文件超过 256 MB 上限：{path.name}（{display_bytes(size)}）")
    duration = audio_duration_seconds(path)
    if duration > MAX_AUDIO_SECONDS:
        raise SystemExit(f"音频文件超过 60 分钟上限：{path.name}（{duration:.1f} 秒）")


def audio_duration_seconds(path: Path) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            ],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SystemExit(f"无法验证音频时长，已阻断处理：{path.name}") from error
    try:
        duration = float(result.stdout.strip())
    except ValueError as error:
        raise SystemExit(f"无法读取音频时长，已阻断处理：{path.name}") from error
    if result.returncode != 0 or duration < 0:
        raise SystemExit(f"无法验证音频时长，已阻断处理：{path.name}")
    return duration


def validate_source_files(files: Iterable[Path], output_dir: Path) -> int:
    paths = list(files)
    total = sum(path.stat().st_size for path in paths)
    if total > MAX_SOURCE_BATCH_BYTES:
        raise SystemExit(f"授权源批次超过 2 GB 上限：{display_bytes(total)}")
    for path in paths:
        if path.suffix.casefold() in AUDIO_SUFFIXES:
            require_audio_file_within_limit(path)
    require_free_space(output_dir, total, "输出目录")
    return total


def validate_declared_base_attachments(attachments: Iterable[dict[str, object]], snapshot_dir: Path, output_dir: Path) -> int:
    total = 0
    for item in attachments:
        size = item.get("reported_size")
        if not isinstance(size, int) or size < 0:
            raise SystemExit(f"Base 附件缺少可验证的大小：{item.get('original_name', 'Not Provided')}")
        total += size
        name = str(item.get("original_name", ""))
        if Path(name).suffix.casefold() in AUDIO_SUFFIXES and size > MAX_AUDIO_BYTES:
            raise SystemExit(f"Base 音频附件超过 256 MB 上限：{name}（{display_bytes(size)}）")
    if total > MAX_SOURCE_BATCH_BYTES:
        raise SystemExit(f"Base 授权源批次超过 2 GB 上限：{display_bytes(total)}")
    require_free_space(snapshot_dir, total, "Base 快照目录")
    require_free_space(output_dir, total, "输出目录")
    return total


def validate_zip_infos(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    infos = [info for info in archive.infolist() if not info.is_dir()]
    if len(infos) > MAX_ZIP_MEMBERS:
        raise SystemExit(f"materials.zip 成员数超过 {MAX_ZIP_MEMBERS} 上限。")
    seen_names: set[str] = set()
    for info in infos:
        name = info.filename
        if (
            not name
            or name.startswith("/")
            or "\\" in name
            or "//" in name
            or any(part in {"", ".", ".."} for part in name.split("/"))
        ):
            raise SystemExit(f"materials.zip 包含非规范成员路径：{name or 'Not Provided'}")
        if name in seen_names:
            raise SystemExit(f"materials.zip 包含重复成员路径：{name}")
        seen_names.add(name)
        if info.flag_bits & 0x1:
            raise SystemExit(f"materials.zip 包含加密成员：{name}")
        unix_mode = info.external_attr >> 16
        if unix_mode and stat.S_ISLNK(unix_mode):
            raise SystemExit(f"materials.zip 包含符号链接成员：{name}")
    expanded = sum(info.file_size for info in infos)
    if expanded > MAX_ZIP_EXPANDED_BYTES:
        raise SystemExit(f"materials.zip 展开体积超过 2 GB 上限：{display_bytes(expanded)}")
    for info in infos:
        if info.file_size > MAX_ZIP_EXPANDED_BYTES:
            raise SystemExit(f"materials.zip 成员展开体积超限：{info.filename}")
        if info.file_size and info.compress_size == 0:
            raise SystemExit(f"materials.zip 包含无效压缩成员：{info.filename}")
        if info.compress_size and info.file_size / info.compress_size > MAX_ZIP_COMPRESSION_RATIO:
            raise SystemExit(f"materials.zip 成员压缩比超过 {MAX_ZIP_COMPRESSION_RATIO}:1：{info.filename}")
    return infos


def sha256_zip_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    digest = hashlib.sha256()
    with archive.open(info, "r") as stream:
        for block in iter(lambda: stream.read(STREAM_CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()
