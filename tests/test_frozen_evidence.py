#!/usr/bin/env python3
"""Tests for the independent frozen-evidence package builder."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("frozen_evidence", ROOT / "tools" / "build_frozen_evidence.py")
assert SPEC and SPEC.loader
EVIDENCE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = EVIDENCE
SPEC.loader.exec_module(EVIDENCE)


class FrozenEvidenceTests(unittest.TestCase):
    def write_fixture(self, root: Path, *, sealed: bool = False) -> tuple[Path, Path, Path, Path, Path]:
        evidence_root = root / "source"
        evidence_root.mkdir()
        cases = []
        for kind, count in (("Known", 12), ("Holdout", 4)):
            prefix = "KN" if kind == "Known" else "HO"
            for number in range(1, count + 1):
                case_id = f"{prefix}-{number:03d}"
                relative = Path("sealed_control") / f"{case_id}.md" if sealed and case_id == "HO-001" else Path("cases") / f"{case_id}.md"
                source = evidence_root / relative
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text(f"Synthetic evidence for {case_id}.\n", encoding="utf-8")
                cases.append({"id": case_id, "kind": kind, "evidence": [relative.as_posix()]})
        manifest = root / "cases.json"
        manifest.write_text(json.dumps({"cases": cases}, ensure_ascii=False), encoding="utf-8")
        decision_log = root / "decision.md"
        decision_log.write_text("# Decision Log\n\nVersioned decision record.\n", encoding="utf-8")
        boundary = root / "boundary.md"
        boundary.write_text("# Boundary Matrix\n\n| Scenario | Result |\n|---|---|\n| Mixed batch | Blocked |\n", encoding="utf-8")
        plugin = root / "plugin.zip"
        plugin.write_bytes(b"synthetic plugin archive")
        return plugin, manifest, evidence_root, decision_log, boundary

    def test_builds_a_checksum_verified_neutral_evidence_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plugin, manifest, evidence_root, decision_log, boundary = self.write_fixture(root)
            output = EVIDENCE.build_package(
                plugin_zip=plugin, case_manifest=manifest, evidence_root=evidence_root,
                decision_log=decision_log, boundary_matrix=boundary, version="0.5.0",
                output_dir=root / "evidence-package",
            )
            EVIDENCE.verify_package(output)
            self.assertTrue((output / "SHA256SUMS.txt").is_file())
            self.assertIn("12 Known + 4 Holdout", (output / "INDEX.md").read_text(encoding="utf-8"))
            self.assertFalse(any("sealed_control" in path.as_posix() for path in output.rglob("*")))

    def test_rejects_sealed_control_material(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plugin, manifest, evidence_root, decision_log, boundary = self.write_fixture(root, sealed=True)
            with self.assertRaisesRegex(SystemExit, "受限控制材料"):
                EVIDENCE.build_package(
                    plugin_zip=plugin, case_manifest=manifest, evidence_root=evidence_root,
                    decision_log=decision_log, boundary_matrix=boundary, version="0.5.0",
                    output_dir=root / "evidence-package",
                )


if __name__ == "__main__":
    unittest.main()
