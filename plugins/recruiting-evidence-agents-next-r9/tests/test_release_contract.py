#!/usr/bin/env python3
"""Offline release-contract coverage for the formal CEO package path."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import build_base_materials as BASE  # noqa: E402
import build_candidate_pack as CANDIDATE  # noqa: E402
import build_ceo_package as CEO  # noqa: E402
import feishu_delivery as DELIVERY  # noqa: E402
import run_control  # noqa: E402


class ReleaseContractTests(unittest.TestCase):
    def fixture_base(self):
        fields = []
        field_by_id = {}
        for index, name in enumerate(
            list(BASE.CANONICAL_FIELDS)
            + ["HRD审核意见", "HRD意见状态", "HRD录用建议"],
            start=1,
        ):
            field_id = f"fld{index:03d}"
            field = {"field_id": field_id, "field_name": name, "type": 1}
            fields.append(field)
            field_by_id[field_id] = field

        values = {name: "Not Provided" for name in BASE.CANONICAL_FIELDS}
        values.update(
            {
                "候选人编号": "C-FIXTURE-001",
                "候选人姓名": "Release Fixture",
                "批次": "Fixture Batch",
                "技术作业评估状态": "通过",
                "技术面试流程状态": "通过",
                "BP面试流程状态": "通过",
                "HRD面试流程状态": "通过",
                "CEO最终决策": "Not Provided",
                "当前流程阶段": "HRD审核",
                "当前流程状态": "已完成",
                "处理状态": "通过",
                "HRD审核意见": "建议进入 CEO 审阅。",
                "HRD意见状态": "已填写",
                "HRD录用建议": "建议录用",
            }
        )
        record = (
            "rec_fixture_001",
            {
                field_id: values.get(field["field_name"], "Not Provided")
                for field_id, field in field_by_id.items()
            },
        )
        fields_by_name = {str(field["field_name"]): field for field in fields}
        return {"base_token": "app_fixture", "table_id": "tbl_fixture"}, fields_by_name, field_by_id, [record]

    def write_handoff(self, output_dir, field_by_id, records, *, hrd_status="通过", confirmed_hrd=None):
        material_root = output_dir.parent / "material-source"
        material_root.mkdir()
        (material_root / "C-FIXTURE-001_resume.txt").write_text(
            "Synthetic release fixture only. No real candidate data.", encoding="utf-8"
        )
        with zipfile.ZipFile(output_dir / "materials.zip", "w") as archive:
            archive.write(
                material_root / "C-FIXTURE-001_resume.txt",
                "C-FIXTURE-001_resume.txt",
            )

        rows = [
            {
                "候选人编号": "C-FIXTURE-001",
                "候选人姓名": "Release Fixture",
                "批次": "Fixture Batch",
                "技术作业评估状态": "通过",
                "技术面试流程状态": "通过",
                "BP面试流程状态": "通过",
                "HRD面试流程状态": hrd_status,
                "CEO最终决策": "Not Provided",
                "当前流程阶段": "HRD审核",
                "当前流程状态": "已完成",
                "处理状态": "通过",
            }
        ]
        BASE.write_register(
            output_dir / "candidate_process_register.xlsx",
            list(rows[0]),
            rows,
        )
        observations = {
            "schema_version": 2,
            "source": "synthetic-release-fixture",
            "captured_at": "2026-08-24T00:00:00+00:00",
            "metadata_fingerprint": BASE.metadata_fingerprint(records, field_by_id),
            "observations": [],
        }
        (output_dir / "base_intake_observations.json").write_text(
            json.dumps(observations, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        run_control.write_run_control(
            output_dir / "run_control.json",
            batch="Fixture Batch",
            keys=[("Fixture Batch", "C-FIXTURE-001", "Release Fixture")],
            fingerprint="synthetic-release-fixture",
        )
        if confirmed_hrd if confirmed_hrd is not None else hrd_status == "通过":
            control = run_control.load_run_control(output_dir / "run_control.json")
            run_control.append_operation_log(
                output_dir,
                "hrd_opinions_saved",
                batch_id=control["batch_id"],
                sender_open_id="ou_fixture_reviewer",
                proposal_id="fixture-proposal",
                candidate_count=1,
            )

    def test_metadata_check_reads_full_current_cli_field_list(self):
        tokens, fields, field_by_id, records = self.fixture_base()
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            expected = BASE.metadata_fingerprint(records, field_by_id)
            (output_dir / "base_intake_observations.json").write_text(json.dumps({
                "schema_version": 2, "metadata_fingerprint": expected,
            }), encoding="utf-8")
            current_fields = [
                {"id": field_id, "name": field["field_name"]}
                for field_id, field in field_by_id.items()
            ]
            context = {"base": tokens, "fields": {"HRD审核意见": fields["HRD审核意见"]}}
            with (
                mock.patch.object(CEO.manage_hrd_review, "records", return_value=records),
                mock.patch.object(CEO.manage_hrd_review, "cli_json", return_value={"data": {"fields": current_fields}}),
            ):
                self.assertEqual(CEO.metadata_unchanged(output_dir, context, None), records)

    def test_hrd_completion_requires_a_matching_saved_audit_event(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            run_control.append_operation_log(output_dir, "hrd_opinions_saved", batch_id="D13-OTHER")
            with self.assertRaisesRegex(SystemExit, "缺少同批次的 SAVE 审计记录"):
                CEO.hrd_completion_time(output_dir, "D13-FIXTURE", required=True)
            run_control.append_operation_log(output_dir, "hrd_opinions_saved", batch_id="D13-FIXTURE")
            self.assertNotEqual(
                CEO.hrd_completion_time(output_dir, "D13-FIXTURE", required=True),
                "Not Provided",
            )

    def test_hrd_completion_accepts_a_valid_revalidation_audit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            run_control.append_operation_log(
                output_dir,
                "hrd_opinions_revalidated",
                batch_id="D13-FIXTURE",
                source_batch_id="D13-PRIOR",
                source_save_at="2026-08-26T09:08:37+00:00",
                opinion_fingerprint="fixture-fingerprint",
            )
            self.assertNotEqual(
                CEO.hrd_completion_time(output_dir, "D13-FIXTURE", required=True),
                "Not Provided",
            )

    def test_synthetic_base_builds_formal_ceo_package_and_static_snapshot(self):
        tokens, fields, field_by_id, records = self.fixture_base()
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "fixture-output"
            output_dir.mkdir()
            self.write_handoff(output_dir, field_by_id, records)
            with (
                mock.patch.object(CEO.manage_hrd_review, "resolve_base", return_value=tokens),
                mock.patch.object(CEO.manage_hrd_review, "base_fields", return_value=fields),
                mock.patch.object(CEO.manage_hrd_review, "records", return_value=records),
                mock.patch.object(CEO.manage_hrd_review, "cli_json", return_value={"data": {"fields": list(field_by_id.values())}}),
                mock.patch.object(
                    sys,
                    "argv",
                    [
                        "build_ceo_package.py",
                        "--base-url",
                        "https://example.invalid/base/app_fixture?table=tbl_fixture",
                        "--output-dir",
                        str(output_dir),
                        "--lark-profile",
                        "synthetic-profile",
                    ],
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(CEO.main(), 0)

            process_dir = output_dir / DELIVERY.PROCESS_RECORDS_DIR
            process_dir.mkdir()
            for name in DELIVERY.PROCESS_RECORD_FILES:
                (process_dir / name).write_text("{}\n", encoding="utf-8")

            self.assertEqual(
                {path.name for path in output_dir.iterdir()},
                {
                    "candidate_pack.html",
                    "consolidated_candidate_pack.md",
                    "materials.zip",
                    "missing_evidence_list.md",
                    "source_manifest.md",
                    "ceo_summary_message.md",
                    "operation_log.jsonl",
                    DELIVERY.PROCESS_RECORDS_DIR,
                },
            )
            html = (output_dir / "candidate_pack.html").read_text(encoding="utf-8")
            self.assertIn("HRD 候选人意见包", html)
            self.assertIn("Release Fixture", html)
            self.assertNotIn("{{", html)
            summary = (output_dir / "ceo_summary_message.md").read_text(encoding="utf-8")
            self.assertIn("第Fixture Batch批共 1 位候选人的流程汇总如下", summary)
            self.assertIn("完整通过 1 位", summary)
            self.assertIn("流程汇总如下", summary)
            self.assertIn("附件为候选人审阅页和材料 ZIP；详情请见附件。", summary)
            self.assertNotIn("CEO 最终决策尚未记录", summary)
            self.assertNotIn("建议录用 1 名", summary)
            self.assertNotIn("HRD opinion confirmation: `Not Provided`", (output_dir / "consolidated_candidate_pack.md").read_text(encoding="utf-8"))
            snapshot = DELIVERY.delivery_snapshot(
                output_dir,
                "CEO review fixture",
                "synthetic-profile",
                sender_open_id="ou_fixture_sender",
                recipient={"receive_id_type": "open_id", "receive_id": "ou_fixture_ceo"},
            )
            self.assertRegex(snapshot["batch_id"], r"^D13-[A-F0-9]+$")
            self.assertEqual(snapshot["sender_open_id"], "ou_fixture_sender")
            self.assertEqual(snapshot["recipient"]["receive_id"], "ou_fixture_ceo")

    def test_preview_binds_verified_sender_and_resolved_recipient(self):
        arguments = SimpleNamespace(
            output_dir="/synthetic/output",
            summary="CEO review fixture",
            lark_profile="synthetic-profile",
            recipient="open_id:ou_fixture_ceo",
        )
        snapshot = {
            "batch_id": "Fixture Batch",
            "fingerprint": "fixture-fingerprint",
            "sender_open_id": "ou_fixture_sender",
            "recipient": {"receive_id_type": "open_id", "receive_id": "ou_fixture_ceo"},
        }
        with (
            mock.patch.object(
                DELIVERY,
                "user_cli_ready",
                return_value={"openId": "ou_fixture_sender", "verified": True},
            ),
            mock.patch.object(DELIVERY, "recipient_descriptor", return_value="CEO Fixture"),
            mock.patch.object(DELIVERY, "resolve_recipient", return_value=("open_id", "ou_fixture_ceo")),
            mock.patch.object(DELIVERY, "reviewed_summary", return_value="CEO review fixture"),
            mock.patch.object(DELIVERY, "delivery_snapshot", return_value=snapshot) as build_snapshot,
            mock.patch.object(DELIVERY, "load_receipt", return_value={"components": {}}),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(DELIVERY.preview(arguments), 0)
        build_snapshot.assert_called_once_with(
            Path("/synthetic/output"),
            "CEO review fixture",
            "synthetic-profile",
            sender_open_id="ou_fixture_sender",
            recipient={"receive_id_type": "open_id", "receive_id": "ou_fixture_ceo"},
        )
        preview = json.loads(output.getvalue())
        self.assertEqual(preview["sender_open_id"], "ou_fixture_sender")
        self.assertEqual(preview["recipient"]["receive_id"], "ou_fixture_ceo")

    def test_flat_candidate_pack_keeps_a_manual_reviewed_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            (output_dir / "consolidated_candidate_pack.md").write_text("# Consolidated Candidate Pack\n", encoding="utf-8")
            self.assertEqual(DELIVERY.reviewed_summary(output_dir, "HRD 已审阅的摘要。"), "HRD 已审阅的摘要。")
            with self.assertRaisesRegex(DELIVERY.DeliveryError, "缺少已审阅"):
                DELIVERY.reviewed_summary(output_dir, "")

    def test_zero_hrd_pass_cohort_still_builds_a_formal_summary(self):
        tokens, fields, field_by_id, records = self.fixture_base()
        hrd_status_id = fields["HRD面试流程状态"]["field_id"]
        records[0][1][hrd_status_id] = "Not Reached"
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "fixture-output"
            output_dir.mkdir()
            self.write_handoff(output_dir, field_by_id, records, hrd_status="Not Reached")
            with (
                mock.patch.object(CEO.manage_hrd_review, "resolve_base", return_value=tokens),
                mock.patch.object(CEO.manage_hrd_review, "base_fields", return_value=fields),
                mock.patch.object(CEO.manage_hrd_review, "records", return_value=records),
                mock.patch.object(CEO.manage_hrd_review, "cli_json", return_value={"data": {"fields": list(field_by_id.values())}}),
                mock.patch.object(sys, "argv", [
                    "build_ceo_package.py", "--base-url", "https://example.invalid/base/app_fixture?table=tbl_fixture",
                    "--output-dir", str(output_dir),
                ]),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(CEO.main(), 0)
            self.assertIn("完整通过 0 位", (output_dir / "ceo_summary_message.md").read_text(encoding="utf-8"))
            self.assertEqual(
                DELIVERY.delivery_snapshot(
                    output_dir, "", "synthetic-profile",
                    sender_open_id="ou_fixture_sender",
                    recipient={"receive_id_type": "open_id", "receive_id": "ou_fixture_ceo"},
                )["html_validation"]["static_validation"],
                "passed",
            )


if __name__ == "__main__":
    unittest.main()
