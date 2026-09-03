#!/usr/bin/env python3
"""Smoke-test documented Python entry points before a formal archive is built."""

from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
DOCUMENTS = [ROOT / "README.md", ROOT / "docs" / "MACOS_HANDOFF_GUIDE.md"] + sorted((ROOT / "skills").glob("*/SKILL.md"))
COMMAND = re.compile(r"python3 scripts/([a-z_]+\.py)")


class InstallerDocumentationTests(unittest.TestCase):
    def test_documented_python_commands_exist_and_accept_help(self) -> None:
        commands = {
            name
            for document in DOCUMENTS
            for name in COMMAND.findall(document.read_text(encoding="utf-8"))
        }
        self.assertTrue(commands)
        for name in sorted(commands):
            script = ROOT / "scripts" / name
            self.assertTrue(script.is_file(), f"documented command is missing: {name}")
            result = subprocess.run(
                [sys.executable, str(script), "--help"],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(result.returncode, 0, f"{name}: {result.stderr}")


if __name__ == "__main__":
    unittest.main()
