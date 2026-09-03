#!/usr/bin/env python3
"""Offline contract checks for the read-only Base snapshot adapter."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from unittest import mock
import zipfile
from pathlib import Path
from types import SimpleNamespace


SCRIPTS = Path(__file__).parents[1] / "scripts"
TEMPLATE = Path(__file__).parents[1] / "assets" / "candidate_pack_ui_template.html"
sys.path.insert(0, str(SCRIPTS))


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = load_module("build_base_materials_for_test", "build_base_materials.py")
CANDIDATE = load_module("build_candidate_pack_for_test", "build_candidate_pack.py")
DELIVERY = load_module("feishu_delivery_for_test", "feishu_delivery.py")
HRD = load_module("manage_hrd_review_for_test", "manage_hrd_review.py")
CEO = load_module("build_ceo_package_for_test", "build_ceo_package.py")


class BaseMaterialsTests(unittest.TestCase):

    def test_process_records_are_fixed_and_strip_source_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            BASE.run_control.append_operation_log(output, "base_intake_completed", batch_id="D13-TEST")
            manifest = {
                "captured_at": "2026-08-26T01:30:00+00:00", "selection": {"batch": "Batch 1"},
                "record_count": 1, "attachments": [{"file_token": "box_secret"}], "refresh": {"mode": "Full"},
            }
            mappings = [{
                "original_name": "张三_简历.pdf", "candidate": "张三", "field": "简历材料",
                "resolved_candidate": "张三", "file_token": "box_secret", "original_path": "input/secret-path",
                "sha256": "abc", "package_status": "Included",
            }]
            observations = {"observations": [], "trace_observations": []}
            BASE.write_process_records(output, manifest, mappings, observations)
            BASE.run_control.refresh_process_records(output)
            process_dir = BASE.run_control.process_records_dir(output)
            self.assertEqual({path.name for path in process_dir.iterdir()}, BASE.run_control.PROCESS_RECORD_FILES)
            content = "\n".join(path.read_text(encoding="utf-8") for path in process_dir.iterdir())
        self.assertNotIn("box_secret", content)
        self.assertNotIn("secret-path", content)

    def test_output_boundary_allows_only_the_fixed_process_records_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            (output / BASE.run_control.PROCESS_RECORDS_DIR).mkdir()
            BASE.run_control.validate_output_boundary(
                output, set(), allowed_directories={BASE.run_control.PROCESS_RECORDS_DIR},
            )
            (output / "unexpected").mkdir()
            with self.assertRaisesRegex(SystemExit, "目录 unexpected"):
                BASE.run_control.validate_output_boundary(
                    output, set(), allowed_directories={BASE.run_control.PROCESS_RECORDS_DIR},
                )

    def test_package_directory_name_requires_business_batch_and_timestamp(self) -> None:
        BASE.run_control.validate_package_directory_name(Path("招聘材料包_第1批_20260826_1030"), "Batch 1")
        with self.assertRaisesRegex(SystemExit, "招聘材料包_第1批"):
            BASE.run_control.validate_package_directory_name(Path("d13_batch1_refresh1"), "Batch 1")
        with self.assertRaisesRegex(SystemExit, "招聘材料包_第1批"):
            BASE.run_control.validate_package_directory_name(Path("招聘材料包_第1批_20260826_103045"), "Batch 1")

    def test_package_location_must_be_a_direct_workspace_child_outside_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            package = workspace / "招聘材料包_第1批_20260826_1030"
            BASE.run_control.validate_package_location(package, "Batch 1", workspace)
            with self.assertRaisesRegex(SystemExit, "直接位于工作目录"):
                BASE.run_control.validate_package_parent(workspace / "04_outputs" / package.name, workspace)
        with self.assertRaisesRegex(SystemExit, "不得写入插件目录"):
            BASE.run_control.validate_package_parent(SCRIPTS.parent / "04_outputs" / "招聘材料包_第1批_20260826_1030")

    def test_package_location_requires_selected_workspace_before_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            package = Path(temp_dir) / "招聘材料包_第1批_20260826_1030"
            with self.assertRaisesRegex(SystemExit, "必须先选择工作目录"):
                BASE.run_control.validate_package_parent(package)
            self.assertFalse(package.exists())

    def test_blocked_material_reply_uses_intake_summary_not_ceo_language(self) -> None:
        skill = (Path(__file__).parents[1] / "skills" / "candidate-pack-agent" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("材料未完成时的业务回复", skill)
        self.assertIn("是否生成催办预览？", skill)
        self.assertNotIn("问题解决后，我将提供 CEO 摘要。", skill)

    def test_follow_up_foreground_wait_returns_one_terminal_notice_then_uses_compact_hrd_prompt(self) -> None:
        root = Path(__file__).parents[1] / "skills"
        base = (root / "base-material-pack-agent" / "SKILL.md").read_text(encoding="utf-8")
        follow_up = (root / "follow-up-incremental-pack-agent" / "SKILL.md").read_text(encoding="utf-8")
        hrd = (root / "hrd-ceo-review-agent" / "SKILL.md").read_text(encoding="utf-8")
        expected = "请逐行填写：姓名+建议录用/不建议录用+意见"
        self.assertIn("wait-for-completion", follow_up)
        self.assertIn("read-completion-result", follow_up)
        self.assertIn("watch_status=Running", follow_up)
        self.assertIn("不是终态", follow_up)
        self.assertIn("--base-url", follow_up)
        self.assertIn("hrd_queue", follow_up)
        self.assertIn("不得创建 Codex heartbeat", follow_up)
        self.assertIn("默认保存到桌面", base)
        self.assertIn("招聘材料包_第N批_YYYYMMDD_HHmm", base)
        self.assertIn("follow_up_completion.json", follow_up)
        self.assertIn(expected, follow_up)
        self.assertIn(expected, hrd)

    def test_hrd_storage_and_ceo_message_contract_are_business_focused(self) -> None:
        hrd = (Path(__file__).parents[1] / "skills" / "hrd-ceo-review-agent" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("一次批量写入", hrd)
        self.assertIn("意见指纹", hrd)
        self.assertIn("第 X 批共 N 位候选人的流程汇总如下", hrd)
        self.assertIn("详情请见附件", hrd)

    def test_owner_snapshot_keeps_only_verified_open_id_and_display_name(self) -> None:
        self.assertEqual(
            BASE.owner_snapshot([{"id": "ou_owner", "name": "Owner", "email": "owner@example.com"}]),
            {"status": "Verified", "open_id": "ou_owner", "name": "Owner"},
        )
        self.assertEqual(BASE.owner_snapshot([]), {"status": "Not Provided"})
        self.assertEqual(BASE.owner_snapshot("agents"), {"status": "Placeholder", "name": "agents"})
        self.assertEqual(BASE.owner_snapshot([{"id": "not-an-open-id"}]), {"status": "Unverified"})
    def test_base_url_requires_table_parameter(self) -> None:
        self.assertEqual(
            BASE.resolve_base_url("https://tenant.feishu.cn/base/app123?table=tbl456"),
            ("app123", "tbl456"),
        )
        with self.assertRaises(SystemExit):
            BASE.resolve_base_url("https://tenant.feishu.cn/base/app123")

    def test_explicit_table_selection_requires_a_single_unambiguous_table(self) -> None:
        self.assertEqual(
            BASE.select_table_id("tbl456", "tbl456"),
            "tbl456",
        )
        self.assertEqual(
            BASE.select_table_id("", "tbl456"),
            "tbl456",
        )
        with self.assertRaisesRegex(SystemExit, "不一致"):
            BASE.select_table_id("tbl456", "tbl999")
        with self.assertRaisesRegex(SystemExit, "未指定数据表"):
            BASE.select_table_id("", "")

    def test_batch_selection_is_exact_and_never_mixes_batches(self) -> None:
        fields = {"batch": {"field_name": "批次"}}
        records = [
            ("rec-a", {"batch": ["Batch A"]}),
            ("rec-b", {"batch": ["Batch B"]}),
        ]
        selected, batch = BASE.select_batch_records(records, fields, "Batch B")
        self.assertEqual(batch, "Batch B")
        self.assertEqual([record_id for record_id, _ in selected], ["rec-b"])
        with self.assertRaisesRegex(SystemExit, "请先确认"):
            BASE.select_batch_records(records, fields, "")
        with self.assertRaisesRegex(SystemExit, "不存在指定批次"):
            BASE.select_batch_records(records, fields, "Batch C")
        with self.assertRaisesRegex(SystemExit, "请先确认"):
            BASE.select_batch_records([("rec-c", {"batch": ["Batch A"]})], fields, "")

    def test_incremental_candidate_scope_selects_only_new_stable_ids(self) -> None:
        fields = {
            "batch": {"field_name": "批次"},
            "candidate_id": {"field_name": "候选人编号"},
            "candidate": {"field_name": "候选人姓名"},
        }
        records = [
            ("rec-a", {"batch": ["Batch 2"], "candidate_id": "S1-KN-001", "candidate": "张三"}),
            ("rec-b", {"batch": ["Batch 2"], "candidate_id": "S1-KN-002", "candidate": "李四"}),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            baseline = Path(temp_dir)
            (baseline / "base_snapshot_manifest.json").write_text(json.dumps({
                "base": {"base_token": "app123", "table_id": "tbl123"},
                "selection": {"table_id": "tbl123", "batch": "Batch 2"},
                "records": [{"record_id": "rec-a", "candidate_id": "S1-KN-001", "candidate": "张三"}],
            }), encoding="utf-8")
            prior = BASE.load_incremental_baseline(baseline, "app123", "tbl123", "Batch 2")
        selected, batch = BASE.select_batch_records(records, fields, "Batch 2")
        incremental, candidate_ids = BASE.select_incremental_candidate_records(selected, fields, prior)
        self.assertEqual(batch, "Batch 2")
        self.assertEqual([record_id for record_id, _ in incremental], ["rec-b"])
        self.assertEqual(candidate_ids, ["S1-KN-002"])
        with self.assertRaisesRegex(SystemExit, "没有新增候选人"):
            BASE.select_incremental_candidate_records([records[0]], fields, prior)
        changed = [("rec-changed", records[0][1])]
        with self.assertRaisesRegex(SystemExit, "更新/更正包"):
            BASE.select_incremental_candidate_records(changed, fields, prior)

    def test_incremental_no_new_candidates_creates_no_output_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            workspace.mkdir()
            baseline = Path(temp_dir) / "baseline"
            baseline.mkdir()
            (baseline / "base_snapshot_manifest.json").write_text(json.dumps({
                "base": {"base_token": "app123", "table_id": "tbl123"},
                "selection": {"table_id": "tbl123", "batch": "Batch 2"},
                "records": [{"record_id": "rec-a", "candidate_id": "S1-KN-001", "candidate": "张三"}],
            }), encoding="utf-8")
            snapshot = workspace / ".招聘材料控制" / "快照_第2批_20260828_1200"
            output = workspace / "招聘材料包_第2批_20260828_1200"
            fields = [{"id": "batch", "name": "批次"}, {"id": "candidate_id", "name": "候选人编号"}, {"id": "candidate", "name": "候选人姓名"}]
            records = {"data": [[ ["Batch 2"], "S1-KN-001", "张三" ]], "field_id_list": ["batch", "candidate_id", "candidate"], "record_id_list": ["rec-a"], "has_more": False}
            args = [
                "build_base_materials.py", "--base-url", "https://tenant/base/app?table=tbl123", "--table-id", "tbl123", "--batch", "Batch 2",
                "--workspace-root", str(workspace), "--snapshot-dir", str(snapshot), "--output-dir", str(output), "--incremental-from", str(baseline),
            ]
            with mock.patch.object(sys, "argv", args), mock.patch.object(BASE, "resolve_table_scope", return_value=("app123", "tbl123")), mock.patch.object(
                BASE, "cli_json", side_effect=[{"data": {"fields": fields}}, {"data": records}],
            ):
                with self.assertRaisesRegex(SystemExit, "没有新增候选人"):
                    BASE.main()
            self.assertFalse(snapshot.exists())
            self.assertFalse(output.exists())

    def test_ceo_metadata_scope_excludes_prior_batch_candidates(self) -> None:
        fields = {
            "batch": {"field_name": "批次"},
            "candidate_id": {"field_name": "候选人编号"},
            "candidate": {"field_name": "候选人姓名"},
        }
        live = [
            ("rec-a", {"batch": ["Batch 2"], "candidate_id": "S1-KN-001", "candidate": "张三"}),
            ("rec-b", {"batch": ["Batch 2"], "candidate_id": "S1-KN-002", "candidate": "李四"}),
        ]
        scope = [{"batch": "Batch 2", "candidate_id": "S1-KN-002", "candidate": "李四"}]
        scoped = CEO.scoped_live_records(live, fields, scope)
        self.assertEqual([record_id for record_id, _ in scoped], ["rec-b"])
        with self.assertRaisesRegex(SystemExit, "候选人范围不一致"):
            CEO.scoped_live_records(live, fields, [{"batch": "Batch 2", "candidate_id": "S1-KN-003", "candidate": "王五"}])

    def test_base_url_resolution_supports_current_block_id_response(self) -> None:
        self.assertEqual(BASE.resolved_table_id({"block_id": "tbl456"}), "tbl456")
        self.assertEqual(BASE.resolved_table_id({"table_id": "tbl456", "block_id": "other"}), "tbl456")

    def test_hrd_root_url_uses_explicit_selected_table(self) -> None:
        selected = ("app123", "tbl456")
        with mock.patch.object(HRD.build_base_materials, "resolve_table_scope", return_value=selected) as resolve:
            self.assertEqual(HRD.resolve_base("https://tenant/base/app123", None, Path("/tmp"), "tbl456"), selected)
        resolve.assert_called_once_with("https://tenant/base/app123", "tbl456", None, Path("/tmp"))

    def test_base_block_list_accepts_current_blocks_shape(self) -> None:
        self.assertEqual(
            BASE.base_block_items({"blocks": [{"id": "tbl456", "type": "table"}]}),
            [{"id": "tbl456", "type": "table"}],
        )
        with self.assertRaisesRegex(SystemExit, "block items"):
            BASE.base_block_items({"blocks": {}})

    def test_table_and_batch_discovery_are_explicit(self) -> None:
        tables = BASE.selectable_tables([
            {"id": "tbl-b", "name": "第二张表", "type": "table", "records_count": 2},
            {"id": "vw-ignored", "name": "视图", "type": "view"},
            {"id": "tbl-a", "name": "第一张表", "type": "table", "records_count": 1},
        ])
        self.assertEqual([item["table_id"] for item in tables], ["tbl-a", "tbl-b"])
        fields = {"batch": {"field_name": "批次"}}
        choices = BASE.batch_choices([
            ("rec-a", {"batch": ["Batch 2"]}),
            ("rec-b", {"batch": ["Batch 1"]}),
            ("rec-c", {"batch": ["Batch 2"]}),
        ], fields)
        self.assertEqual(choices, [{"batch": "Batch 1", "record_count": 1}, {"batch": "Batch 2", "record_count": 2}])
        single = BASE.batch_discovery_payload("tbl-a", [{"batch": "Batch 1", "record_count": 12}])
        self.assertFalse(single["selection_required"])
        self.assertTrue(single["confirmation_required"])
        self.assertIn("Batch 1", str(single["next_action"]))

    def test_base_metadata_fingerprint_is_stable_and_binds_canonical_fields(self) -> None:
        fields = {
            f"field-{index}": {"field_name": name}
            for index, name in enumerate(BASE.CANONICAL_FIELDS, 1)
        }
        values = {field_id: f"value-{index}" for index, field_id in enumerate(fields, 1)}
        first = BASE.metadata_fingerprint([("rec2", values), ("rec1", values)], fields)
        second = BASE.metadata_fingerprint([("rec1", values), ("rec2", values)], fields)
        self.assertEqual(first, second)
        with self.assertRaisesRegex(SystemExit, "missing required fields"):
            BASE.metadata_fingerprint([("rec1", values)], {})

    def test_base_metadata_fingerprint_ignores_plugin_managed_processing_status(self) -> None:
        fields = {
            f"field-{index}": {"field_name": name}
            for index, name in enumerate(BASE.CANONICAL_FIELDS, 1)
        }
        values = {field_id: f"value-{index}" for index, field_id in enumerate(fields, 1)}
        processing_field = next(field_id for field_id, field in fields.items() if field["field_name"] == "处理状态")
        process_changed = dict(values)
        process_changed[processing_field] = "已处理"
        self.assertEqual(
            BASE.metadata_fingerprint([("rec1", values)], fields),
            BASE.metadata_fingerprint([("rec1", process_changed)], fields),
        )
        stage_field = next(field_id for field_id, field in fields.items() if field["field_name"] == "BP面试流程状态")
        stage_changed = dict(values)
        stage_changed[stage_field] = "不同流程状态"
        self.assertNotEqual(
            BASE.metadata_fingerprint([("rec1", values)], fields),
            BASE.metadata_fingerprint([("rec1", stage_changed)], fields),
        )

    def test_base_metadata_fingerprint_accepts_current_cli_name_shape(self) -> None:
        fields = {
            f"field-{index}": {"id": f"field-{index}", "name": name}
            for index, name in enumerate(BASE.CANONICAL_FIELDS, 1)
        }
        values = {field_id: f"value-{index}" for index, field_id in enumerate(fields, 1)}

        self.assertEqual(
            BASE.metadata_fingerprint([("rec1", values)], fields),
            BASE.metadata_fingerprint([("rec1", values)], {
                field_id: {"field_name": field["name"]} for field_id, field in fields.items()
            }),
        )

    def test_base_contract_has_no_people_ops_field(self) -> None:
        self.assertNotIn("People Ops审核状态", BASE.CANONICAL_FIELDS)
        self.assertNotIn("People Ops审核状态", CANDIDATE.REQUIRED)

    def test_date_only_preserves_single_select_values(self) -> None:
        self.assertEqual(BASE.date_only(["通过"]), "通过")
        self.assertEqual(BASE.date_only(["Not Reached"]), "Not Reached")
        self.assertEqual(BASE.date_only(["2026-08-24T10:00:00+08:00"]), "2026-08-24")

    def test_candidate_review_batch_label_uses_business_display_format(self) -> None:
        self.assertEqual(CANDIDATE.batch_label([{"批次": "Batch 1"}]), "第 1 批")
        self.assertEqual(CANDIDATE.batch_label([{"批次": "第Batch 1批"}]), "第 1 批")

    def test_download_worker_selection_is_bounded_and_validated(self) -> None:
        self.assertEqual(BASE.select_download_workers(79, 8), 8)
        self.assertEqual(BASE.select_download_workers(3, 8), 3)
        with self.assertRaises(SystemExit):
            BASE.select_download_workers(0, 8)
        with self.assertRaises(SystemExit):
            BASE.select_download_workers(1, 0)

    def test_verified_previous_attachment_is_reused_only_for_same_base_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            prior = root / "prior"
            selection = {"table_id": "tbl123", "batch": "Batch 1"}
            source = prior / "input" / "base-source" / "0001" / "resume.txt"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"verified source")
            attachment = {
                "record_id": "rec001", "field_id": "fld_resume", "ordinal": 1, "file_token": "file_resume",
                "snapshot_path": "input/base-source/0001/resume.txt", "sha256": BASE.sha256(source), "byte_size": source.stat().st_size,
            }
            (prior / "base_snapshot_manifest.json").write_text(json.dumps({
                "base": {"base_token": "app123", "table_id": "tbl123"}, "selection": selection, "attachments": [attachment],
            }), encoding="utf-8")
            reusable = BASE.load_reusable_attachments(prior, "app123", "tbl123", selection)
            with self.assertRaisesRegex(SystemExit, "批次范围"):
                BASE.load_reusable_attachments(prior, "app123", "tbl123", {"table_id": "tbl123", "batch": "Batch 2"})
            downloads = root / "downloads"
            downloads.mkdir()
            current = {**attachment, "reported_size": source.stat().st_size, "original_name": "resume.txt"}
            self.assertTrue(BASE.reuse_attachment(current, reusable, downloads))
            self.assertEqual((downloads / "file_resume").read_bytes(), b"verified source")
            self.assertFalse(BASE.reuse_attachment({**current, "file_token": "file_replaced"}, reusable, downloads))

    def test_generated_register_is_compatible_with_material_pack_reader(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            register = Path(temp_dir) / "candidate_process_register.xlsx"
            headers = ["候选人编号", "候选人姓名", "简历材料", "简历提交时间", "当前流程状态"]
            BASE.write_register(
                register,
                headers,
                [{"候选人编号": "S1-KN-001", "候选人姓名": "张三", "简历材料": "input/张三_简历.pdf", "简历提交时间": "2026-08-03", "当前流程状态": "通过"}],
            )
            rows = BASE.build_materials.read_xlsx_rows(register)
        self.assertEqual(rows[1], headers)
        self.assertEqual(rows[2][1], "张三")
        self.assertEqual(rows[2][2], "input/张三_简历.pdf")

    def test_non_pdf_bytes_with_a_pdf_extension_are_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "张三_面试转写.pdf"
            path.write_text("not a PDF", encoding="utf-8")
            _, readability = BASE.build_materials.text_for(path)
        self.assertEqual(readability, "PDF Invalid")

    def test_base_source_path_is_directly_under_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            source = input_dir / "base-source" / "0001" / "张三_技术作业评估.md"
            source.parent.mkdir(parents=True)
            source.write_text("李四", encoding="utf-8")
            self.assertEqual(
                BASE.build_materials.source_member_name("李四", source, input_dir),
                "input/李四/张三_技术作业评估.md",
            )

    def test_same_name_sources_use_a_stable_suffix_without_nested_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            first = input_dir / "base-source" / "0001" / "面试记录.md"
            second = input_dir / "base-source" / "0002" / "面试记录.md"
            first.parent.mkdir(parents=True)
            second.parent.mkdir(parents=True)
            first.write_text("张三", encoding="utf-8")
            second.write_text("张三", encoding="utf-8")
            used: set[str] = set()
            self.assertEqual(
                BASE.build_materials.unique_source_member_name("张三", first, input_dir, used),
                "input/张三/面试记录.md",
            )
            self.assertEqual(
                BASE.build_materials.unique_source_member_name("张三", second, input_dir, used),
                "input/张三/面试记录__source-0002.md",
            )

    def test_missing_material_requires_a_recorded_material_event(self) -> None:
        self.assertEqual(
            BASE.expected_material_fields({"技术作业评估状态": "Not Provided", "技术面试发生时间": "", "BP面试发生时间": "", "HRD面试发生时间": ""}),
            ["简历材料"],
        )
        self.assertEqual(
            BASE.expected_material_fields({"技术作业评估状态": "通过", "技术面试发生时间": "2026-08-05", "BP面试发生时间": "Not Reached", "HRD面试发生时间": ""}),
            ["简历材料", "技术作业评估", "技术面试材料"],
        )

    def test_recorded_interview_requires_audio_and_transcript(self) -> None:
        row = {"技术作业评估状态": "Not Provided", "技术面试发生时间": "", "BP面试发生时间": "", "HRD面试发生时间": "2026-08-08"}
        self.assertEqual(
            BASE.expected_material_components(row),
            [("简历材料", "材料"), ("HRD面试材料", "面试录音"), ("HRD面试材料", "面试转写")],
        )
        self.assertEqual(BASE.material_component("HRD面试材料", "杜文博-HRD面试录音文件.mp3"), "面试录音")
        self.assertEqual(BASE.material_component("HRD面试材料", "杜文博-HRD面试转写稿.pdf"), "面试转写")
        self.assertEqual(BASE.material_component("HRD面试材料", "杜文博-HRD面试记录.pdf"), "面试转写")

    def test_hrd_audio_without_transcript_is_missing_transcript(self) -> None:
        row = {
            "候选人姓名": "杜文博", "技术作业评估状态": "Not Provided",
            "技术面试发生时间": "", "BP面试发生时间": "", "HRD面试发生时间": "2026-08-08",
        }
        attachments = [
            {"candidate": "杜文博", "field": "简历材料", "original_name": "杜文博-简历.pdf", "snapshot_path": "input/base-source/1/resume.pdf"},
            {"candidate": "杜文博", "field": "HRD面试材料", "original_name": "杜文博-HRD面试录音.mp3", "snapshot_path": "input/base-source/2/hrd.mp3"},
        ]
        mappings = [
            {"original_path": "input/base-source/1/resume.pdf", "resolved_candidate": "杜文博", "classification": "Candidate Material"},
            {"original_path": "input/base-source/2/hrd.mp3", "resolved_candidate": "杜文博", "classification": "Candidate Material"},
        ]
        observations = BASE.material_observations([row], attachments, mappings)
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["scope"], "杜文博 / HRD面试材料 / 面试转写")

    def test_unreadable_transcript_requires_replacement(self) -> None:
        row = {
            "候选人姓名": "张三", "技术作业评估状态": "Not Provided",
            "技术面试发生时间": "", "BP面试发生时间": "2026-08-06", "HRD面试发生时间": "",
        }
        attachments = [
            {"candidate": "张三", "field": "简历材料", "original_name": "张三_简历.pdf", "snapshot_path": "input/base-source/0/resume.pdf"},
            {"candidate": "张三", "field": "BP面试材料", "original_name": "张三_BP面试录音.mp3", "snapshot_path": "input/base-source/1/audio.mp3"},
            {"candidate": "张三", "field": "BP面试材料", "original_name": "张三_BP面试转写.pdf", "snapshot_path": "input/base-source/2/transcript.pdf"},
        ]
        mappings = [
            {"original_path": "input/base-source/0/resume.pdf", "resolved_candidate": "张三", "classification": "Candidate Material", "readability": "Readable PDF"},
            {"original_path": "input/base-source/1/audio.mp3", "resolved_candidate": "张三", "classification": "Candidate Material", "readability": "Not Readable as Text"},
            {"original_path": "input/base-source/2/transcript.pdf", "resolved_candidate": "张三", "classification": "Candidate Material", "readability": "PDF Unreadable"},
        ]
        observations = BASE.material_observations([row], attachments, mappings)
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]["category"], "Invalid Material")
        self.assertEqual(observations[0]["scope"], "张三 / BP面试材料 / 面试转写")

    def test_invalid_extra_transcript_requires_deletion_not_a_supplement(self) -> None:
        row = {
            "候选人姓名": "孙明远", "技术作业评估状态": "Not Provided",
            "技术面试发生时间": "", "BP面试发生时间": "2026-08-06", "HRD面试发生时间": "",
        }
        attachments = [
            {"candidate": "孙明远", "field": "简历材料", "original_name": "孙明远_简历.pdf", "snapshot_path": "input/0"},
            {"candidate": "孙明远", "field": "BP面试材料", "original_name": "孙明远_BP面试录音.mp3", "snapshot_path": "input/1"},
            {"candidate": "孙明远", "field": "BP面试材料", "original_name": "孙明远_BP面试转写.pdf", "snapshot_path": "input/2"},
            {"candidate": "孙明远", "field": "BP面试材料", "original_name": "张三_BP面试转写_无效.pdf", "snapshot_path": "input/3"},
        ]
        mappings = [
            {"original_path": "input/0", "package_status": "Included", "resolved_candidate": "孙明远", "classification": "Candidate Material", "readability": "PDF Content Unverified", "sha256": "resume"},
            {"original_path": "input/1", "package_status": "Included", "resolved_candidate": "孙明远", "classification": "Candidate Material", "readability": "Not Readable as Text", "sha256": "audio"},
            {"original_path": "input/2", "package_status": "Included", "resolved_candidate": "孙明远", "classification": "Candidate Material", "readability": "PDF Content Unverified", "sha256": "transcript"},
            {"original_path": "input/3", "package_status": "Included", "resolved_candidate": "张三", "classification": "Candidate Material", "readability": "PDF Invalid", "sha256": "invalid"},
        ]
        self.assertEqual(BASE.material_observations([row], attachments, mappings), [])
        trace = BASE.trace_observations(attachments, mappings)
        invalid_extra = next(item for item in trace if item["category"] == "Invalid Extra Material")
        self.assertIn("Delete the invalid extra attachment only", invalid_extra["handling"])
        self.assertNotIn("Misplaced Material", {item["category"] for item in trace})
        self.assertEqual(CANDIDATE.blocking_observations(trace), [])

    def test_trace_observations_report_misplaced_duplicates_and_versions_without_blocking(self) -> None:
        attachments = [
            {"candidate": "张三", "field": "简历材料", "snapshot_path": "input/1", "original_name": "李四-CV.pdf"},
            {"candidate": "张三", "field": "技术作业评估", "snapshot_path": "input/2", "original_name": "张三_评估.md"},
            {"candidate": "张三", "field": "技术作业评估", "snapshot_path": "input/3", "original_name": "张三_评估_副本.md"},
            {"candidate": "张三", "field": "技术作业评估", "snapshot_path": "input/4", "original_name": "张三_评估_补充.md"},
        ]
        mappings = [
            {"original_path": "input/1", "package_status": "Included", "resolved_candidate": "李四", "sha256": "cv"},
            {"original_path": "input/2", "package_status": "Included", "resolved_candidate": "李四", "sha256": "same"},
            {"original_path": "input/3", "package_status": "Included", "resolved_candidate": "李四", "sha256": "same"},
            {"original_path": "input/4", "package_status": "Included", "resolved_candidate": "张三", "sha256": "new"},
        ]
        trace = BASE.trace_observations(attachments, mappings)
        self.assertEqual(
            {item["category"] for item in trace},
            {"Misplaced Material"},
        )
        self.assertEqual(CANDIDATE.blocking_observations(trace), [])

    def test_observations_require_the_control_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "base_intake_observations.json"
            path.write_text(json.dumps({"schema_version": 1, "observations": [{"category": "Unmapped Material", "scope": "张三 / 技术面试材料", "fact": "No unique identity.", "handling": "Retain source."}]}), encoding="utf-8")
            self.assertEqual(CANDIDATE.load_base_observations(path)[0]["category"], "Unmapped Material")
            self.assertEqual(CANDIDATE.load_base_trace_observations(path), [])
            path.write_text(json.dumps({
                "schema_version": 2,
                "observations": [],
                "trace_observations": [{"category": "Misplaced Material", "scope": "张三 / 简历材料", "fact": "mapped to 李四", "handling": "retain"}],
            }), encoding="utf-8")
            self.assertEqual(CANDIDATE.load_base_trace_observations(path)[0]["category"], "Misplaced Material")
            path.write_text(json.dumps({"schema_version": 1, "observations": [{}]}), encoding="utf-8")
            with self.assertRaises(SystemExit):
                CANDIDATE.load_base_observations(path)

    def test_only_output_bound_confirmed_missing_handoff_can_release_missing_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            observations_path = root / "base_intake_observations.json"
            observations_path.write_text(json.dumps({
                "schema_version": 1, "captured_at": "2026-08-20T09:42:00+00:00",
                "observations": [{"category": "Missing Material", "scope": "张三 / 简历材料", "fact": "missing", "handling": "request"}],
            }), encoding="utf-8")
            resolution_path = root / "follow_up_resolution.json"
            resolution_path.write_text(json.dumps({
                "schema_version": 1, "snapshot_captured_at": "2026-08-20T09:42:00+00:00",
                "confirmations": [{
                    "candidate": "张三", "material_field": "简历材料", "material_component": "材料",
                    "recipient_open_id": "ou_owner", "sent_message_id": "om_request", "confirmed_missing_message_id": "om_reply",
                    "confirmed_missing_reply": "确认无法提供", "confirmed_missing_reply_to": "om_request",
                }],
            }), encoding="utf-8")
            observations = CANDIDATE.apply_confirmed_missing_status(
                CANDIDATE.load_base_observations(observations_path), resolution_path, observations_path,
            )
        self.assertEqual(observations[0]["resolution_status"], "Confirmed Missing")
        self.assertEqual(CANDIDATE.blocking_observations(observations), [])
        self.assertEqual(CANDIDATE.ceo_summary_gate(observations)[0], "可提供")
        self.assertIn("已确认缺件", CANDIDATE.ceo_summary_gate(observations)[1])

    def test_confirmed_missing_resolution_accepts_linked_natural_unavailability(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            observations = root / "base_intake_observations.json"
            observations.write_text(json.dumps({
                "schema_version": 2, "captured_at": "2026-08-27T06:46:10+00:00", "observations": [], "trace_observations": [],
            }), encoding="utf-8")
            resolution = root / "follow_up_resolution.json"
            resolution.write_text(json.dumps({
                "schema_version": 1, "snapshot_captured_at": "2026-08-27T06:46:10+00:00", "confirmations": [{
                    "candidate": "杜文博", "material_field": "BP面试材料", "material_component": "面试转写",
                    "recipient_open_id": "ou_owner", "sent_message_id": "om_request", "confirmed_missing_message_id": "om_reply",
                    "confirmed_missing_reply": "杜文博的 BP面试材料缺失，无法提供。", "confirmed_missing_reply_to": "om_request",
                }],
            }), encoding="utf-8")
            scopes = CANDIDATE.confirmed_missing_scopes(resolution, observations)
        self.assertEqual(scopes, {("杜文博", "BP面试材料", "面试转写")})

    def test_output_boundary_rejects_directories_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "output"
            output_dir.mkdir()
            (output_dir / "outside").mkdir()
            with self.assertRaises(SystemExit):
                CANDIDATE.run_control.validate_output_boundary(output_dir, {"materials.zip"})
            (output_dir / "outside").rmdir()
            target = Path(temp_dir) / "target.txt"
            target.write_text("outside", encoding="utf-8")
            (output_dir / "linked.txt").symlink_to(target)
            with self.assertRaises(SystemExit):
                CANDIDATE.run_control.validate_output_boundary(output_dir, {"linked.txt"})

    def test_flat_source_boundary_rejects_nested_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir) / "input"
            input_dir.mkdir()
            (input_dir / "nested").mkdir()
            with self.assertRaises(SystemExit):
                CANDIDATE.run_control.validate_flat_source_boundary(input_dir)

    def test_base_snapshot_boundary_allows_only_generated_nested_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot = Path(temp_dir) / "snapshot"
            input_dir = snapshot / "input"
            source = input_dir / "base-source" / "0001" / "resume.txt"
            source.parent.mkdir(parents=True)
            source.write_text("source", encoding="utf-8")
            (input_dir / "prompt.md").write_text("prompt", encoding="utf-8")
            (snapshot / "base_snapshot_manifest.json").write_text("{}", encoding="utf-8")
            files = CANDIDATE.run_control.validate_base_snapshot_source_boundary(
                input_dir, snapshot / "base_snapshot_manifest.json",
            )
            self.assertEqual({path.name for path in files}, {"resume.txt", "prompt.md"})
            (input_dir / "other").mkdir()
            with self.assertRaises(SystemExit):
                CANDIDATE.run_control.validate_base_snapshot_source_boundary(
                    input_dir, snapshot / "base_snapshot_manifest.json",
                )

    def test_directory_overlap_is_a_boundary_violation(self) -> None:
        root = Path("/private/tmp/d13-boundary")
        self.assertTrue(CANDIDATE.run_control.paths_overlap(root, root / "output"))
        self.assertFalse(CANDIDATE.run_control.paths_overlap(root / "input", root / "output"))

    def test_ceo_summary_gate_blocks_actionable_material_observations(self) -> None:
        self.assertIn("Invalid Material", CANDIDATE.CEO_BLOCKING_OBSERVATIONS)
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            evidence = output_dir / "missing_evidence_list.md"
            evidence.write_text(
                "# 材料问题清单\n\n## CEO 摘要状态\n\n| 项目 | 当前状态 |\n|---|---|\n| 状态 | `暂缓` |\n| 原因 | 张三 / HRD面试材料：缺少材料 |\n",
                encoding="utf-8",
            )
            with self.assertRaises(DELIVERY.DeliveryError):
                DELIVERY.ceo_summary_gate(output_dir)
            evidence.write_text(
                "# 材料问题清单\n\n## CEO 摘要状态\n\n| 项目 | 当前状态 |\n|---|---|\n| 状态 | `可提供` |\n| 原因 | 没有待核实或待补交的材料。 |\n",
                encoding="utf-8",
            )
            self.assertEqual(DELIVERY.ceo_summary_gate(output_dir), "可提供")

    def test_delivery_snapshot_rejects_nested_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "output"
            output_dir.mkdir()
            (output_dir / "unexpected").mkdir()
            with self.assertRaises(DELIVERY.DeliveryError):
                DELIVERY.delivery_snapshot(output_dir, "summary", "test-profile")

    def test_ceo_html_renders_only_saved_hrd_opinions_and_passes_delivery_static_check(self) -> None:
        register = [
            {"候选人姓名": "张三", "技术面试流程状态": "通过", "BP面试流程状态": "通过", "HRD面试流程状态": "通过"},
            {"候选人姓名": "李四", "技术面试流程状态": "通过", "BP面试流程状态": "通过", "HRD面试流程状态": "通过"},
        ]
        selected = [{"candidate": "张三", "current_recommendation": "建议录用", "current_rationale": "岗位经历与协作沟通均匹配。"}]
        rendered = CEO.render_html(
            control={"batch_label": "第一批"}, register=register, selected=selected,
            outcomes=CEO.classify_outcomes(register),
            observations=[{"resolution_status": "Confirmed Missing", "scope": "张三 / HRD面试材料", "fact": "已确认无法提供", "handling": "保留限制"}],
            duplicate_count=1, captured_at="2026-08-24T00:00:00+00:00",
            trace_observations=[{"category": "Multiple Material Versions", "scope": "张三 / 技术作业评估", "fact": "3 versions", "handling": "retain all"}],
        )
        self.assertIn("HRD 候选人意见包", rendered)
        self.assertIn("岗位经历与协作沟通均匹配。", rendered)
        self.assertIn("非阻断材料追溯发现", rendered)
        self.assertIn("funnel-track", rendered)
        self.assertIn("--funnel-width", rendered)
        self.assertNotIn("李四</h4>", rendered)
        with tempfile.TemporaryDirectory() as temp_dir:
            report = Path(temp_dir) / "candidate_pack.html"
            report.write_text(rendered, encoding="utf-8")
            self.assertEqual(DELIVERY.validate_review_html(report)["static_validation"], "passed")

    def test_ceo_selection_and_delivery_receipt_are_bound_to_current_confirmation(self) -> None:
        cohort = [
            {"批次": "Batch 1", "候选人编号": "S1-KN-001", "候选人姓名": "张三"},
            {"批次": "Batch 1", "候选人编号": "S1-KN-002", "候选人姓名": "李四"},
        ]
        context = {"eligible": [
            {"batch": "Batch 1", "candidate_id": "S1-KN-001", "candidate": "张三", "opinion_status": "已填写"},
            {"batch": "Batch 1", "candidate_id": "S1-KN-002", "candidate": "李四", "opinion_status": "未填写"},
        ]}
        selected = CEO.saved_opinions_for_cohort(context, cohort[:1])
        self.assertEqual([item["candidate"] for item in selected], ["张三"])
        with self.assertRaisesRegex(SystemExit, "尚不完整"):
            CEO.saved_opinions_for_cohort(context, cohort)
        with self.assertRaisesRegex(SystemExit, "完整候选人范围"):
            CEO.selected_cohort(cohort, '["张三"]')
        snapshot = {
            "batch_id": "D13-TEST", "delivery_fingerprint": "fingerprint", "sender_open_id": "ou_sender",
            "recipient": {"receive_id_type": "open_id", "receive_id": "ou_ceo"}, "summary_sha256": "summary",
            "attachments": [{"name": "candidate_pack.html", "sha256": "html", "bytes": 1}],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            receipt = DELIVERY.load_receipt(output, snapshot)
            receipt["components"]["message"] = {"message_id": "om_x"}
            DELIVERY.persist_receipt(output, receipt, False)
            self.assertEqual(DELIVERY.load_receipt(output, snapshot)["components"]["message"]["message_id"], "om_x")
            changed = {**snapshot, "sender_open_id": "ou_other"}
            with self.assertRaisesRegex(DELIVERY.DeliveryError, "不属于当前"):
                DELIVERY.load_receipt(output, changed)

    def test_review_html_uses_snapshot_time_and_does_not_imply_an_offer(self) -> None:
        candidate = {
            "候选人姓名": "张三", "批次": "Batch 1", "技术面试流程状态": "通过", "BP面试流程状态": "通过",
            "HRD面试流程状态": "通过", "当前流程状态": "Not Reached", "当前流程阶段": "HRD面试",
        }
        rendered = CANDIDATE.render_html(
            TEMPLATE.read_text(encoding="utf-8"), [candidate], 1, [], "2026-08-20 17:42（Asia/Shanghai）",
        )
        self.assertIn("完整流程已完成", rendered)
        self.assertIn("第 1 批", rendered)
        self.assertIn("2026-08-20 17:42（Asia/Shanghai）", rendered)
        self.assertIn("不构成录用结论", rendered)
        self.assertIn("待补或待核实材料", rendered)
        self.assertIn("材料核验已完成", rendered)
        self.assertIn("另保留 1 组重复提交记录", rendered)
        self.assertNotIn("待运营复核", rendered)
        self.assertNotIn("HRD 拟办", rendered)
        with tempfile.TemporaryDirectory() as temp_dir:
            report = Path(temp_dir) / "candidate_pack.html"
            report.write_text(rendered, encoding="utf-8")
            self.assertEqual(DELIVERY.validate_review_html(report)["static_validation"], "passed")

    def test_review_html_counts_only_active_material_observations_as_pending(self) -> None:
        candidate = {
            "候选人姓名": "张三", "批次": "Batch 1", "技术面试流程状态": "通过", "BP面试流程状态": "通过",
            "HRD面试流程状态": "通过", "当前流程状态": "Not Reached", "当前流程阶段": "HRD面试",
        }
        rendered = CANDIDATE.render_html(
            TEMPLATE.read_text(encoding="utf-8"), [candidate], 2,
            [{"category": "Missing Material", "scope": "张三 / BP面试材料", "fact": "未提交", "handling": "补交"}],
        )
        self.assertIn("材料核验未完成：1 项待补或待核实材料", rendered)
        self.assertIn("另保留 2 组重复提交记录", rendered)
        self.assertNotIn("材料核验未完成：3 项", rendered)
        self.assertNotIn("待录用审批", rendered)
        self.assertNotIn("待 CEO 审批", rendered)

    def test_builder_separates_duplicate_history_from_active_material_observations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "output"
            output_dir.mkdir()
            register = output_dir / "candidate_process_register.xlsx"
            headers = [
                "候选人编号", "候选人姓名", "批次", "技术作业评估状态", "技术面试流程状态",
                "BP面试流程状态", "HRD面试流程状态", "CEO最终决策",
                "当前流程阶段", "当前流程状态",
            ]
            BASE.write_register(register, headers, [{
                "候选人编号": "S1-KN-001", "候选人姓名": "张三", "批次": "Batch 1",
                "技术作业评估状态": "Not Provided", "技术面试流程状态": "Not Reached",
                "BP面试流程状态": "Not Reached", "HRD面试流程状态": "Not Reached",
                "CEO最终决策": "Not Provided",
                "当前流程阶段": "技术面试", "当前流程状态": "Not Reached",
            }])
            with zipfile.ZipFile(output_dir / "materials.zip", "w") as archive:
                archive.writestr("input/技术作业题目.md", "shared prompt")
                archive.writestr("input/张三/张三_简历.pdf", b"same resume")
                archive.writestr("input/张三/张三_简历_副本.pdf", b"same resume")
            CANDIDATE.run_control.write_run_control(
                output_dir / CANDIDATE.run_control.RUN_CONTROL_FILE,
                batch="Batch 1",
                keys=[("Batch 1", "S1-KN-001", "张三")],
                fingerprint="test-fingerprint",
            )
            with mock.patch.object(sys, "argv", [
                "build_candidate_pack.py", "--output-dir", str(output_dir),
            ]):
                self.assertEqual(CANDIDATE.main(), 0)
            consolidated = (output_dir / "consolidated_candidate_pack.md").read_text(encoding="utf-8")
            missing = (output_dir / "missing_evidence_list.md").read_text(encoding="utf-8")
            manifest = (output_dir / "source_manifest.md").read_text(encoding="utf-8")
        self.assertIn("| Active material observations | 0 |", consolidated)
        self.assertIn("| Retained duplicate submission groups | 1 |", consolidated)
        self.assertNotIn("Duplicate Submission", missing)
        self.assertIn("CEO 知情审阅", missing)
        self.assertIn("Duplicate Submission", manifest)

    def test_base_handoff_requires_hrd_review_before_formal_pack(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "output"
            output_dir.mkdir()
            register = output_dir / "candidate_process_register.xlsx"
            headers = [
                "候选人编号", "候选人姓名", "批次", "技术作业评估状态", "技术面试流程状态",
                "BP面试流程状态", "HRD面试流程状态", "CEO最终决策",
                "当前流程阶段", "当前流程状态",
            ]
            BASE.write_register(register, headers, [{
                "候选人编号": "S1-KN-001", "候选人姓名": "张三", "批次": "Batch 1",
                "技术作业评估状态": "Not Provided", "技术面试流程状态": "Not Reached",
                "BP面试流程状态": "Not Reached", "HRD面试流程状态": "通过",
                "CEO最终决策": "Not Provided",
                "当前流程阶段": "HRD面试", "当前流程状态": "进行中",
            }])
            with zipfile.ZipFile(output_dir / "materials.zip", "w") as archive:
                archive.writestr("input/技术作业题目.md", "shared prompt")
                archive.writestr("input/张三/张三_简历.pdf", b"resume")
            (output_dir / "base_intake_observations.json").write_text(
                json.dumps({"schema_version": 1, "captured_at": "2026-08-20T09:42:00+00:00", "observations": []}),
                encoding="utf-8",
            )
            CANDIDATE.run_control.write_run_control(
                output_dir / CANDIDATE.run_control.RUN_CONTROL_FILE,
                batch="Batch 1", keys=[("Batch 1", "S1-KN-001", "张三")], fingerprint="test-fingerprint",
            )
            with mock.patch.object(sys, "argv", ["build_candidate_pack.py", "--output-dir", str(output_dir)]):
                with self.assertRaisesRegex(SystemExit, "HRD 审核意见"):
                    CANDIDATE.main()
            self.assertFalse((output_dir / "candidate_pack.html").exists())

    def test_immutable_handoff_blocks_tampered_material_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            archive = output_dir / "materials.zip"
            register = output_dir / "candidate_process_register.xlsx"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("input/技术作业题目.md", "shared prompt")
            register.write_bytes(b"controlled register")
            CANDIDATE.run_control.write_run_control(
                output_dir / CANDIDATE.run_control.RUN_CONTROL_FILE,
                batch="Batch 1",
                keys=[("Batch 1", "S1-KN-001", "张三")],
                fingerprint="test-fingerprint",
            )
            archive.write_bytes(b"altered archive")
            control = CANDIDATE.run_control.load_run_control(
                output_dir / CANDIDATE.run_control.RUN_CONTROL_FILE,
            )
            with self.assertRaisesRegex(SystemExit, "materials.zip.*交接摘要"):
                CANDIDATE.run_control.validate_handoff(output_dir, control)

    def test_base_observations_must_be_bound_after_base_intake(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            with zipfile.ZipFile(output_dir / "materials.zip", "w") as package:
                package.writestr("input/技术作业题目.md", "shared prompt")
            (output_dir / "candidate_process_register.xlsx").write_bytes(b"controlled register")
            control_path = output_dir / CANDIDATE.run_control.RUN_CONTROL_FILE
            CANDIDATE.run_control.write_run_control(
                control_path,
                batch="Batch 1",
                keys=[("Batch 1", "S1-KN-001", "张三")],
                fingerprint="test-fingerprint",
            )
            observations = output_dir / "base_intake_observations.json"
            observations.write_text('{"observations": []}', encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "Base 材料观察未绑定"):
                CANDIDATE.run_control.validate_handoff(
                    output_dir, CANDIDATE.run_control.load_run_control(control_path),
                )
            CANDIDATE.run_control.bind_base_observations(control_path)
            CANDIDATE.run_control.validate_handoff(
                output_dir, CANDIDATE.run_control.load_run_control(control_path),
            )
            observations.write_text('{"observations": ["altered"]}', encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "base_intake_observations.json.*交接摘要"):
                CANDIDATE.run_control.validate_handoff(
                    output_dir, CANDIDATE.run_control.load_run_control(control_path),
                )

    def test_base_capture_time_uses_shanghai_timezone(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "base_intake_observations.json"
            path.write_text(json.dumps({"schema_version": 1, "captured_at": "2026-08-20T09:42:00+00:00", "observations": []}), encoding="utf-8")
            self.assertEqual(CANDIDATE.base_capture_time(path), "2026-08-20 17:42（Asia/Shanghai）")

    def test_hrd_queue_includes_only_hrd_pass_candidates(self) -> None:
        fields = {
            name: {"field_id": f"fld_{index}", "type": "text"}
            for index, name in enumerate((*HRD.IDENTITY_FIELDS, *HRD.HRD_FIELDS), 1)
        }
        register = [
            {"批次": "Batch 1", "候选人编号": "S1-KN-001", "候选人姓名": "张三", "技术面试流程状态": "通过", "BP面试流程状态": "通过", "HRD面试流程状态": "通过"},
            {"批次": "Batch 1", "候选人编号": "S1-KN-002", "候选人姓名": "李四", "技术面试流程状态": "通过", "BP面试流程状态": "Not Reached", "HRD面试流程状态": "进行中"},
        ]
        row = lambda candidate_id, candidate, status: {
            fields["批次"]["field_id"]: "Batch 1",
            fields["候选人编号"]["field_id"]: candidate_id,
            fields["候选人姓名"]["field_id"]: candidate,
            fields["HRD面试流程状态"]["field_id"]: status,
            fields["HRD审核意见"]["field_id"]: "",
            fields["HRD意见状态"]["field_id"]: "",
            fields["HRD录用建议"]["field_id"]: "",
        }
        with mock.patch.object(HRD, "read_handoff", return_value=({"batch_id": "D13-TEST"}, register)), \
             mock.patch.object(HRD, "resolve_base", return_value=("app123", "tbl123")), \
             mock.patch.object(HRD, "base_fields", return_value=fields), \
             mock.patch.object(HRD, "records", return_value=[("rec1", row("S1-KN-001", "张三", "通过")), ("rec2", row("S1-KN-002", "李四", "进行中"))]):
            context = HRD.queue_context("https://tenant/base/app123?table=tbl123", Path("/tmp"), None)
        self.assertEqual([item["candidate"] for item in context["eligible"]], ["张三"])
        self.assertEqual(context["eligible"][0]["opinion_status"], "未填写")
        self.assertEqual(context["complete_flow_candidates"], ["张三"])
        self.assertEqual(context["pending_opinion_candidates"], ["张三"])
        self.assertFalse(context["ready_for_ceo"])
        self.assertEqual(context["waiting"], [{"candidate": "李四", "hrd_interview_status": "进行中", "complete_flow": False}])

    def test_hrd_opinion_requires_eligible_candidate_direction_and_rationale(self) -> None:
        eligible = [{"record_id": "rec1", "candidate": "张三"}]
        self.assertEqual(
            HRD.validate_opinions([{"candidate": "张三", "recommendation": "建议录用", "rationale": "沟通清晰且岗位匹配。"}], eligible),
            [{"record_id": "rec1", "candidate": "张三", "recommendation": "建议录用", "rationale": "沟通清晰且岗位匹配。"}],
        )
        with self.assertRaisesRegex(SystemExit, "只能是"):
            HRD.validate_opinions([{"candidate": "张三", "recommendation": "暂缓", "rationale": "理由充分。"}], eligible)
        with self.assertRaisesRegex(SystemExit, "简短理由"):
            HRD.validate_opinions([{"candidate": "张三", "recommendation": "建议录用", "rationale": "好"}], eligible)

    def test_hrd_verified_sender_accepts_auth_status_without_api_ok(self) -> None:
        status = {
            "identity": "user",
            "verified": True,
            "identities": {
                "user": {
                    "status": "ready",
                    "available": True,
                    "verified": True,
                    "openId": "ou_reviewer",
                },
            },
        }
        result = SimpleNamespace(returncode=0, stdout=json.dumps(status), stderr="")
        with mock.patch.object(HRD.subprocess, "run", return_value=result):
            self.assertEqual(HRD.verified_sender("test-profile", Path("/tmp")), "ou_reviewer")

    def test_hrd_select_value_accepts_current_cli_options_shape(self) -> None:
        field = {
            "type": "select",
            "options": [{"name": "未填写"}, {"name": "已填写"}],
        }
        self.assertEqual(HRD.controlled_value(field, "已填写"), ["已填写"])

    def test_hrd_confirm_writes_then_reads_back_before_removing_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            fields = {
                "HRD审核意见": {"field_id": "opinion", "type": "text"},
                "HRD意见状态": {"field_id": "state", "type": "text"},
                "HRD录用建议": {"field_id": "recommendation", "type": "text"},
                "HRD面试流程状态": {"field_id": "hrd", "type": "text"},
            }
            proposal = {
                "schema_version": 1, "created_at": "2026-08-24T00:00:00+00:00", "batch_id": "D13-TEST",
                "base": {"base_token": "app123", "table_id": "tbl123"}, "sender_open_id": "ou_reviewer",
                "field_names": list(HRD.HRD_FIELDS),
                "opinions": [{"record_id": "rec1", "candidate": "张三", "recommendation": "建议录用", "rationale": "沟通清晰且岗位匹配。"}],
            }
            proposal["proposal_id"] = HRD.canonical_hash(proposal)
            (output_dir / HRD.PROPOSAL_FILE).write_text(json.dumps(proposal), encoding="utf-8")
            before = {"hrd": "通过", "opinion": "", "state": "", "recommendation": ""}
            after = {"hrd": "通过", "opinion": "沟通清晰且岗位匹配。", "state": "已填写", "recommendation": "建议录用"}
            with mock.patch.object(HRD, "hrd_output_boundary"), \
                 mock.patch.object(HRD, "verified_sender", return_value="ou_reviewer"), \
                 mock.patch.object(HRD, "read_handoff", return_value=({"batch_id": "D13-TEST"}, [])), \
                 mock.patch.object(HRD, "base_fields", return_value=fields), \
                 mock.patch.object(HRD, "records", side_effect=[[("rec1", before)], [("rec1", after)]]), \
                 mock.patch.object(HRD, "cli_json", return_value={"ok": True, "data": {}}) as cli:
                self.assertEqual(HRD.confirm(SimpleNamespace(
                    output_dir=output_dir, proposal_id=proposal["proposal_id"], confirm="SAVE", lark_profile=None,
                )), 0)
            self.assertFalse((output_dir / HRD.PROPOSAL_FILE).exists())
            update_call = cli.call_args.args[0]
            self.assertIn("+record-batch-update", update_call)
            self.assertIn("HRD录用建议", update_call[-1])
            saved = [json.loads(line) for line in (output_dir / BASE.run_control.OPERATION_LOG_FILE).read_text(encoding="utf-8").splitlines()][-1]
            self.assertEqual(saved["event"], "hrd_opinions_saved")
            self.assertEqual(saved["saved_fields"], list(HRD.HRD_FIELDS))
            self.assertEqual(saved["opinions_fingerprint"], HRD.canonical_hash(proposal["opinions"]))


if __name__ == "__main__":
    unittest.main()
