#!/usr/bin/env python3
"""Release-gate pressure tests for the documented non-negotiable boundaries."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import build_base_materials as BASE  # noqa: E402
import manage_follow_up as FOLLOW_UP  # noqa: E402
import manage_hrd_review as HRD  # noqa: E402
import run_control  # noqa: E402


class ReleasePressureTests(unittest.TestCase):
    def test_mixed_batch_and_nested_source_are_hard_stops(self) -> None:
        fields = {"batch": {"field_name": "批次"}}
        records = [("rec-1", {"batch": ["Batch 1"]}), ("rec-2", {"batch": ["Batch 2"]})]
        with self.assertRaisesRegex(SystemExit, "请先确认"):
            BASE.select_batch_records(records, fields, "")
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "input"
            (source / "nested").mkdir(parents=True)
            (source / "nested" / "material.txt").write_text("fixture", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "扁平授权目录"):
                run_control.validate_flat_source_boundary(source)

    def test_output_location_and_empty_incremental_scope_are_hard_stops(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            nested = workspace / "technical" / "招聘材料包_第1批_YYYYMMDD_HHmm"
            with self.assertRaisesRegex(SystemExit, "直接位于工作目录"):
                run_control.validate_package_parent(nested, workspace)
            baseline = workspace / "baseline"
            baseline.mkdir()
            (baseline / "base_snapshot_manifest.json").write_text(json.dumps({
                "base": {"base_token": "app", "table_id": "tbl"},
                "selection": {"table_id": "tbl", "batch": "Batch 1"},
                "records": [{"record_id": "rec-1", "candidate_id": "C-1", "candidate": "Fixture"}],
            }), encoding="utf-8")
            prior = BASE.load_incremental_baseline(baseline, "app", "tbl", "Batch 1")
            fields = {
                "candidate_id": {"field_name": "候选人编号"},
                "candidate": {"field_name": "候选人姓名"},
            }
            records = [("rec-1", {"candidate_id": "C-1", "candidate": "Fixture"})]
            with self.assertRaisesRegex(SystemExit, "没有新增候选人"):
                BASE.select_incremental_candidate_records(records, fields, prior)

    def test_unconfirmed_external_actions_and_ceo_overreach_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            control = Path(temp_dir) / "control"
            control.mkdir()
            with self.assertRaisesRegex(SystemExit, "SEND_AND_WATCH"):
                FOLLOW_UP.dispatch_and_watch(
                    control, "https://example.invalid/base?table=tbl", "tbl", Path(temp_dir) / "snapshots",
                    Path(temp_dir) / "handoffs", "profile", "", 1, 1, 1,
                )
        self.assertEqual(HRD.HRD_FIELDS, ("HRD审核意见", "HRD意见状态", "HRD录用建议"))
        self.assertNotIn("CEO最终决策", HRD.HRD_FIELDS)


if __name__ == "__main__":
    unittest.main()
