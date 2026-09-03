#!/usr/bin/env python3
"""Offline checks for the isolated release-candidate archive."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "build_release_archive.py"
SPEC = importlib.util.spec_from_file_location("build_release_archive_for_test", SCRIPT_PATH)
assert SPEC and SPEC.loader
ARCHIVE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ARCHIVE
SPEC.loader.exec_module(ARCHIVE)


class ReleaseArchiveTests(unittest.TestCase):
    def test_archive_is_installable_and_excludes_runtime_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = Path(temp_dir) / "candidate.zip"
            archive, sidecar = ARCHIVE.build_archive(PLUGIN_ROOT, archive_path)
            ARCHIVE.verify_archive(archive)
            self.assertTrue(sidecar.is_file())
            with zipfile.ZipFile(archive) as package:
                names = package.namelist()
                plugin_name = PLUGIN_ROOT.name
                root = f"{plugin_name}-release-candidate"
                marketplace_name = f"{root}/.agents/plugins/marketplace.json"
                self.assertIn(marketplace_name, names)
                marketplace = json.loads(package.read(marketplace_name))
                self.assertEqual(marketplace["plugins"][0]["name"], plugin_name)
                self.assertFalse(any("/04_outputs/" in name or name.endswith(".pyc") for name in names))
                self.assertIn(f"{root}/SHA256SUMS.txt", names)


if __name__ == "__main__":
    unittest.main()
