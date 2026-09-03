#!/usr/bin/env python3
"""Formal release identity and exclusion checks."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("build_formal_release_for_test", ROOT / "scripts" / "build_formal_release.py")
assert SPEC and SPEC.loader
FORMAL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = FORMAL
SPEC.loader.exec_module(FORMAL)


class FormalReleaseTests(unittest.TestCase):
    def test_formal_archive_has_final_identity_and_no_development_residue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            summary = temp / "summary.md"
            summary.write_text("# Release Summary\n\n- Real acceptance validation: Not Provided\n", encoding="utf-8")
            archive, _ = FORMAL.build_formal_release(ROOT, summary, "0.5.0+codex.20260827000000", temp / "formal.zip")
            with zipfile.ZipFile(archive) as package:
                names = package.namelist()
                manifest_name = "recruiting-evidence-agents-v0-5-0/plugins/recruiting-evidence-agents/.codex-plugin/plugin.json"
                manifest = json.loads(package.read(manifest_name))
                self.assertEqual(manifest["name"], "recruiting-evidence-agents")
                self.assertEqual(manifest["version"], "0.5.0+codex.20260827000000")
                self.assertIn("recruiting-evidence-agents-v0-5-0/plugins/recruiting-evidence-agents/RELEASE_SUMMARY.md", names)
                self.assertFalse(any(
                    "DEVELOPMENT_BASELINE" in name
                    or "/test_control/" in name
                    or "/tests/" in name
                    or name.endswith("/build_formal_release.py")
                    for name in names
                ))
                public_text = "\n".join(
                    package.read(name).decode("utf-8")
                    for name in names
                    if "/README.md" in name or "/docs/" in name or "/skills/" in name
                )
                self.assertNotRegex(public_text, r"\bD1[23]\b|20\d{6}")


if __name__ == "__main__":
    unittest.main()
