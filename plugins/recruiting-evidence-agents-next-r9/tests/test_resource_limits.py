#!/usr/bin/env python3
"""Offline checks for the D13 resource-boundary contract."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPTS = Path(__file__).parents[1] / "scripts"
import sys
sys.path.insert(0, str(SCRIPTS))

import resource_limits


class ResourceLimitsTests(unittest.TestCase):
    @staticmethod
    def zip_info(name: str, *, flags: int = 0, external_attr: int = 0):
        return SimpleNamespace(
            filename=name, file_size=1, compress_size=1, flag_bits=flags,
            external_attr=external_attr, is_dir=lambda: False,
        )

    def test_source_batch_rejects_more_than_two_gib_before_reading(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "oversized.txt"
            with source.open("wb") as stream:
                stream.truncate(resource_limits.MAX_SOURCE_BATCH_BYTES + 1)
            with self.assertRaisesRegex(SystemExit, "2 GB"):
                resource_limits.validate_source_files([source], Path(temp_dir) / "output")

    def test_audio_size_rejects_before_duration_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            audio = Path(temp_dir) / "oversized.mp3"
            with audio.open("wb") as stream:
                stream.truncate(resource_limits.MAX_AUDIO_BYTES + 1)
            with mock.patch.object(resource_limits, "audio_duration_seconds") as duration:
                with self.assertRaisesRegex(SystemExit, "256 MB"):
                    resource_limits.require_audio_file_within_limit(audio)
            duration.assert_not_called()

    def test_audio_duration_rejects_more_than_sixty_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            audio = Path(temp_dir) / "long.mp3"
            audio.write_bytes(b"audio")
            with mock.patch.object(
                resource_limits, "audio_duration_seconds", return_value=resource_limits.MAX_AUDIO_SECONDS + 0.1,
            ):
                with self.assertRaisesRegex(SystemExit, "60 分钟"):
                    resource_limits.require_audio_file_within_limit(audio)

    def test_disk_preflight_blocks_insufficient_output_space(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            required = resource_limits.required_free_bytes(1024)
            with mock.patch.object(
                resource_limits.shutil, "disk_usage", return_value=SimpleNamespace(free=required - 1),
            ):
                with self.assertRaisesRegex(SystemExit, "可用空间不足"):
                    resource_limits.require_free_space(Path(temp_dir), 1024, "输出目录")

    def test_zip_rejects_excessive_compression_ratio_before_member_read(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = Path(temp_dir) / "bomb.zip"
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("input/张三/repeated.txt", b"0" * (resource_limits.MAX_ZIP_COMPRESSION_RATIO * 4096))
            with zipfile.ZipFile(archive_path) as archive:
                with self.assertRaisesRegex(SystemExit, "压缩比"):
                    resource_limits.validate_zip_infos(archive)

    def test_zip_rejects_duplicate_member_path_before_member_read(self) -> None:
        archive = SimpleNamespace(infolist=lambda: [
            self.zip_info("input/张三/resume.txt"), self.zip_info("input/张三/resume.txt"),
        ])
        with self.assertRaisesRegex(SystemExit, "重复成员路径"):
            resource_limits.validate_zip_infos(archive)

    def test_zip_rejects_noncanonical_and_encrypted_members_before_member_read(self) -> None:
        noncanonical = SimpleNamespace(infolist=lambda: [self.zip_info("input/张三/../resume.txt")])
        with self.assertRaisesRegex(SystemExit, "非规范成员路径"):
            resource_limits.validate_zip_infos(noncanonical)
        encrypted = SimpleNamespace(infolist=lambda: [self.zip_info("input/张三/resume.txt", flags=1)])
        with self.assertRaisesRegex(SystemExit, "加密成员"):
            resource_limits.validate_zip_infos(encrypted)

    def test_zip_rejects_symbolic_link_member_before_member_read(self) -> None:
        link_mode = (__import__("stat").S_IFLNK | 0o777) << 16
        archive = SimpleNamespace(infolist=lambda: [
            self.zip_info("input/张三/resume.txt", external_attr=link_mode),
        ])
        with self.assertRaisesRegex(SystemExit, "符号链接成员"):
            resource_limits.validate_zip_infos(archive)

    def test_zip_member_hash_is_streamed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = Path(temp_dir) / "materials.zip"
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("input/张三/resume.txt", b"evidence" * 1024)
            with zipfile.ZipFile(archive_path) as archive:
                info = archive.getinfo("input/张三/resume.txt")
                self.assertEqual(
                    resource_limits.sha256_zip_member(archive, info),
                    __import__("hashlib").sha256(b"evidence" * 1024).hexdigest(),
                )


if __name__ == "__main__":
    unittest.main()
