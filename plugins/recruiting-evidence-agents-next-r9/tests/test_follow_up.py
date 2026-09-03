#!/usr/bin/env python3
"""Offline contract checks for missing-material follow-up control."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from typing import Optional
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = load_module("build_base_materials_for_follow_up_test", "build_base_materials.py")
FOLLOW_UP = load_module("manage_follow_up_for_test", "manage_follow_up.py")
WATCHER = load_module("watch_follow_up_replies_for_test", "watch_follow_up_replies.py")


class FollowUpTests(unittest.TestCase):
    def write_snapshot(self, root: Path, name: str, include_resume: bool, include_hrd_audio: bool = False, include_hrd_transcript: bool = False, owners: Optional[dict[str, dict[str, str]]] = None) -> Path:
        snapshot = root / name
        snapshot.mkdir()
        input_dir = snapshot / "input"
        input_dir.mkdir()
        row = {
            "候选人编号": "S1-KN-001",
            "候选人姓名": "张三",
            "简历材料": "",
            "简历提交时间": "2026-08-20",
            "技术作业评估状态": "Not Provided",
            "技术面试发生时间": "",
            "BP面试发生时间": "",
            "HRD面试发生时间": "2026-08-08" if include_hrd_audio or include_hrd_transcript else "",
            "技术面试流程状态": "Not Reached",
            "BP面试流程状态": "Not Reached",
            "HRD面试流程状态": "Not Reached",
            "CEO最终决策": "Not Provided",
            "当前流程阶段": "技术面试",
            "当前流程状态": "Not Reached",
        }
        headers = list(row)
        BASE.write_register(input_dir / "候选人流程与材料登记表.xlsx", headers, [row])
        attachments = []
        mappings = []
        if include_resume:
            attachments.append({
                "record_id": "rec001", "candidate": "张三", "field": "简历材料", "field_id": "fld_resume",
                "ordinal": 1, "file_token": "file_new", "original_name": "张三_简历.md", "sha256": "a" * 64,
                "snapshot_path": "input/base-source/0001/张三_简历.md",
            })
            mappings.append({"original_path": "input/base-source/0001/张三_简历.md", "resolved_candidate": "张三", "classification": "Candidate Material"})
        if include_hrd_audio:
            attachments.append({
                "record_id": "rec001", "candidate": "张三", "field": "HRD面试材料", "field_id": "fld_hrd",
                "ordinal": 1, "file_token": "file_hrd_audio", "original_name": "张三_HRD面试录音.mp3", "sha256": "b" * 64,
                "snapshot_path": "input/base-source/0002/张三_HRD面试录音.mp3",
            })
            mappings.append({"original_path": "input/base-source/0002/张三_HRD面试录音.mp3", "resolved_candidate": "张三", "classification": "Candidate Material"})
        if include_hrd_transcript:
            attachments.append({
                "record_id": "rec001", "candidate": "张三", "field": "HRD面试材料", "field_id": "fld_hrd",
                "ordinal": 2, "file_token": "file_hrd_transcript", "original_name": "张三_HRD面试转写稿.pdf", "sha256": "c" * 64,
                "snapshot_path": "input/base-source/0003/张三_HRD面试转写稿.pdf",
            })
            mappings.append({"original_path": "input/base-source/0003/张三_HRD面试转写稿.pdf", "resolved_candidate": "张三", "classification": "Candidate Material"})
        manifest = {
            "schema_version": 4,
            "captured_at": "2026-08-20T00:00:00+00:00",
            "attachments": attachments,
            "records": [{"record_id": "rec001", "candidate_id": "S1-KN-001", "candidate": "张三", "owners": owners or {}}],
        }
        (snapshot / "base_snapshot_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        (snapshot / ".material_mapping.jsonl").write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in mappings) + ("\n" if mappings else ""), encoding="utf-8")
        return snapshot

    def test_missing_material_is_drafted_without_send(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            draft = FOLLOW_UP.plan(self.write_snapshot(root, "missing", include_resume=False), root / "control")
        self.assertEqual(draft["new_attachment_events"], 0)
        self.assertEqual(draft["pending_review"][0]["material_field"], "简历材料")
        self.assertEqual(draft["pending_review"][0]["status"], "Pending Review")
        self.assertFalse(draft["send_performed"])
        self.assertFalse(draft["base_written"])

    def test_every_material_follow_up_includes_unavailability_path(self) -> None:
        for item in (
            {"candidate": "张三", "material_field": "简历材料", "material_component": "材料"},
            {"candidate": "杜文博", "kind": "Material Follow-up", "material_field": "BP面试材料", "material_component": "面试转写"},
        ):
            message = FOLLOW_UP.follow_up_message(item)
            self.assertIn(FOLLOW_UP.MISSING_MATERIAL_UNAVAILABLE_HINT, message)
            self.assertIn("已上传", message)

    def test_verified_base_owner_is_mapped_to_its_material_field(self) -> None:
        owners = FOLLOW_UP.load_base_owners({
            "records": [{
                "candidate_id": "S1-KN-001",
                "owners": {"HRD面试材料": {"status": "Verified", "open_id": "ou_owner", "name": "Owner"}},
            }],
        })
        self.assertEqual(owners, {("S1-KN-001", "HRD面试材料"): "open_id:ou_owner"})

    def test_reply_never_resolves_without_verified_base_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            control = root / "control"
            draft = FOLLOW_UP.plan(self.write_snapshot(root, "missing", include_resume=False), control)
            result = FOLLOW_UP.record_reply(control, draft["pending_review"][0]["id"], "已提交")
            state = FOLLOW_UP.load_state(control)
        self.assertEqual(result["status"], "Acknowledged")
        self.assertFalse(result["verified"])
        self.assertEqual(state["items"][0]["status"], "Acknowledged")

    def test_hrd_missing_transcript_is_a_self_service_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            draft = FOLLOW_UP.plan(
                self.write_snapshot(root, "hrd-audio-only", include_resume=True, include_hrd_audio=True),
                root / "control",
            )
        self.assertEqual(draft["pending_review"], [])
        self.assertEqual(len(draft["self_service_actions"]), 1)
        self.assertEqual(draft["self_service_actions"][0]["material_field"], "HRD面试材料")
        self.assertEqual(draft["self_service_actions"][0]["material_component"], "面试转写")

    def test_unmapped_material_is_first_manual_verification_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            owners = {"技术面试材料": {"status": "Verified", "open_id": "ou_owner", "name": "Owner"}}
            snapshot = self.write_snapshot(root, "unmapped", include_resume=True, owners=owners)
            manifest = json.loads((snapshot / "base_snapshot_manifest.json").read_text(encoding="utf-8"))
            manifest["attachments"].append({
                "record_id": "rec001", "candidate": "张三", "field": "技术面试材料", "field_id": "fld_tech",
                "ordinal": 1, "file_token": "file_unmapped", "original_name": "候选人未知_技术面试材料.md",
                "sha256": "u" * 64, "snapshot_path": "input/base-source/0004/候选人未知_技术面试材料.md",
            })
            (snapshot / "base_snapshot_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            with (snapshot / ".material_mapping.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({
                    "original_path": "input/base-source/0004/候选人未知_技术面试材料.md", "resolved_candidate": "Unknown",
                    "classification": "Unmapped", "package_status": "Excluded",
                }, ensure_ascii=False) + "\n")
            draft = FOLLOW_UP.plan(snapshot, root / "control")
        self.assertEqual(len(draft["pending_review"]), 1)
        item = draft["pending_review"][0]
        self.assertEqual(item["kind"], "Manual Verification")
        self.assertEqual(item["categories"], ["Unmapped Material"])
        self.assertEqual(item["recipient"], "open_id:ou_owner")
        self.assertIn("无法确认归属", item["message_draft"])

    def test_new_mapped_attachment_verifies_existing_item_and_is_indexed_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            control = root / "control"
            missing_snapshot = self.write_snapshot(root, "missing", include_resume=False)
            first = FOLLOW_UP.plan(missing_snapshot, control)
            ready_snapshot = self.write_snapshot(root, "ready", include_resume=True)
            second = FOLLOW_UP.plan(ready_snapshot, control)
            third = FOLLOW_UP.plan(ready_snapshot, control)
        self.assertEqual(first["pending_review"][0]["id"], second["verified_item_ids"][0])
        self.assertEqual(second["new_attachment_events"], 1)
        self.assertEqual(second["rebuild_candidate_ids"], ["S1-KN-001"])
        self.assertEqual(third["new_attachment_events"], 0)
        self.assertEqual(third["rebuild_candidate_ids"], [])

    def test_approval_requires_confirmation_and_recipient(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            control = root / "control"
            draft = FOLLOW_UP.plan(self.write_snapshot(root, "missing", include_resume=False), control)
            with self.assertRaises(SystemExit):
                FOLLOW_UP.approve(control, draft["pending_review"][0]["id"], "", "email:self@example.com")
            with self.assertRaises(SystemExit):
                FOLLOW_UP.approve(control, draft["pending_review"][0]["id"], "APPROVE")
            result = FOLLOW_UP.approve(control, draft["pending_review"][0]["id"], "APPROVE", "self@example.com")
            state = FOLLOW_UP.load_state(control)
        self.assertEqual(result["recipient"], "email:self@example.com")
        self.assertEqual(state["items"][0]["recipient"], "email:self@example.com")

    def test_mark_processing_writes_and_reads_back_before_send(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            control = root / "control"
            draft = FOLLOW_UP.plan(self.write_snapshot(root, "missing", include_resume=False), control)
            item_id = draft["pending_review"][0]["id"]
            FOLLOW_UP.approve(control, item_id, "APPROVE", "email:self@example.com")
            fields = {"ok": True, "data": {"fields": [{
                "id": "fld_status", "name": "处理状态", "type": "select", "multiple": False,
                "options": [{"name": "已处理"}, {"name": "待补全"}, {"name": "处理中"}, {"name": "阻塞"}],
            }]}}
            before = {"ok": True, "data": {
                "data": [["S1-KN-001", "张三", None]],
                "field_id_list": ["fld_candidate", "fld_name", "fld_status"],
                "record_id_list": ["rec001"],
                "fields": ["候选人编号", "候选人姓名", "处理状态"],
            }}
            after = {"ok": True, "data": {
                "data": [["S1-KN-001", "张三", ["待补全"]]],
                "field_id_list": ["fld_candidate", "fld_name", "fld_status"],
                "record_id_list": ["rec001"],
                "fields": ["候选人编号", "候选人姓名", "处理状态"],
            }}
            with mock.patch.object(FOLLOW_UP.build_base_materials, "resolve_table_scope", return_value=("base", "tbl")), mock.patch.object(
                FOLLOW_UP, "cli_json", side_effect=[fields, before, {"ok": True, "data": {}}, after],
            ) as call:
                result = FOLLOW_UP.mark_processing(control, "https://tenant/base/base?table=tbl", "tbl", item_id, "MARK", None)
            state = FOLLOW_UP.load_state(control)
        self.assertEqual(result["processing_status"], "待补全")
        self.assertTrue(result["base_written"])
        update_call = call.call_args_list[2].args[0]
        self.assertIn('"fld_status": ["待补全"]', update_call[-1])
        self.assertEqual(state["items"][0]["processing_status"], "待补全")

    def test_processing_targets_cover_every_candidate_and_prioritize_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            snapshot = self.write_snapshot(root, "ready", include_resume=True)
            clean = FOLLOW_UP.processing_targets({"last_snapshot_dir": str(snapshot)})
            with mock.patch.object(FOLLOW_UP.build_base_materials, "material_observations", return_value=[{
                "category": "Unmapped Material", "scope": "张三 / 技术面试材料", "fact": "unknown", "handling": "review",
            }]), mock.patch.object(FOLLOW_UP.build_base_materials, "trace_observations", return_value=[{
                "category": "Invalid Extra Material", "scope": "张三 / BP面试材料", "fact": "invalid", "handling": "review",
            }]):
                blocked = FOLLOW_UP.processing_targets({"last_snapshot_dir": str(snapshot)})
        self.assertEqual(clean[0]["processing_status"], "已处理")
        self.assertEqual(blocked[0]["processing_status"], "阻塞")

    def test_prepare_preview_syncs_statuses_before_returning_draft(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            snapshot = root / "snapshot"
            control = root / "control"
            snapshot.mkdir()
            control.mkdir()
            draft = {"pending_review": [], "base_written": False}
            synced = {"processing_statuses": [{"candidate_id": "S1-KN-001", "processing_status": "待补全"}], "base_written": True}
            with mock.patch.object(FOLLOW_UP, "plan", return_value=draft) as plan_call, mock.patch.object(
                FOLLOW_UP, "synchronize_processing_statuses", return_value=synced,
            ) as sync_call:
                result = FOLLOW_UP.prepare_preview(snapshot, control, "https://tenant/base/base?table=tbl", "tbl", None)
        plan_call.assert_called_once_with(snapshot, control)
        sync_call.assert_called_once()
        self.assertTrue(result["base_written"])
        self.assertEqual(result["processing_statuses"], synced["processing_statuses"])

    def test_dispatch_and_watch_uses_one_confirmation_then_starts_watcher(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            control_root = root / ".招聘材料控制"
            control_root.mkdir()
            control = control_root / "control"
            snapshot = self.write_snapshot(control_root, "missing", include_resume=False)
            output = root / "招聘材料包_第1批_20260820_0000"
            output.mkdir()
            (output / "materials.zip").write_bytes(b"fixture")
            (output / "base_intake_observations.json").write_text("{}", encoding="utf-8")
            draft = FOLLOW_UP.plan(snapshot, control)
            FOLLOW_UP.bind_workspace(control, snapshot, root, output)
            item_id = draft["pending_review"][0]["id"]
            state = FOLLOW_UP.load_state(control)
            state["items"][0]["recipient"] = "open_id:ou_owner"
            FOLLOW_UP.write_json(control / FOLLOW_UP.STATE_FILE, state)
            synced = {"processing_statuses": [{"candidate_id": "S1-KN-001", "processing_status": "待补全"}], "base_written": True}
            dry_run = {"ok": True, "data": {}}
            sent = {"ok": True, "data": {"message_id": "om_request"}}
            watched = {"status": "Started", "pid": 12345}
            launcher = mock.Mock(return_value=watched)
            with mock.patch.object(FOLLOW_UP, "synchronize_processing_statuses", return_value=synced), mock.patch.object(
                FOLLOW_UP, "cli_json", side_effect=[dry_run, sent],
            ) as cli:
                result = FOLLOW_UP.dispatch_and_watch(
                    control, "https://tenant/base/app", "tbl", control_root, root,
                    "profile", "SEND_AND_WATCH", 600, 15, 8, launcher=launcher,
                )
            state = FOLLOW_UP.load_state(control)
            audit_events = [json.loads(line)["event"] for line in (control / FOLLOW_UP.AUDIT_FILE).read_text(encoding="utf-8").splitlines()]
        self.assertTrue(result["send_performed"])
        self.assertFalse(result["task_heartbeat_required"])
        self.assertTrue(result["foreground_wait_required"])
        self.assertEqual(result["sent_items"][0]["message_id"], "om_request")
        self.assertEqual(state["items"][0]["status"], "Sent")
        self.assertEqual(state["items"][0]["recipient_open_id"], "ou_owner")
        self.assertEqual(cli.call_count, 2)
        self.assertIn("--dry-run", cli.call_args_list[0].args[0])
        self.assertIn("reply_watch_launching", audit_events)
        launcher.assert_called_once()

    def test_dispatch_and_wait_returns_only_the_single_terminal_result(self) -> None:
        control = Path("/tmp/control")
        dispatch = {"action": "dispatch-and-watch", "foreground_wait_required": True, "sent_items": [{"item_id": "FU-1"}]}
        terminal = {"action": "wait-for-completion", "notice_ready": True, "message": "补件监听已结束"}
        with mock.patch.object(FOLLOW_UP, "dispatch_and_watch", return_value=dispatch) as send_and_watch, mock.patch.object(
            FOLLOW_UP, "wait_for_completion_notice", return_value=terminal,
        ) as wait:
            result = FOLLOW_UP.dispatch_and_wait(
                control, "https://tenant/base/app", "tbl", Path("/tmp/snapshots"), Path("/tmp/output"),
                "profile", "SEND_AND_WATCH", 600, 15, 8, 1260,
            )
        self.assertEqual(result["action"], "dispatch-and-wait")
        self.assertEqual(result["terminal"], terminal)
        send_and_watch.assert_called_once()
        wait.assert_called_once_with(control, 1260, "https://tenant/base/app", "tbl", "profile")

    def test_resume_watch_only_launches_for_existing_sent_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            control_root = root / ".招聘材料控制"
            control = control_root / "control"
            control.mkdir(parents=True)
            output = root / "招聘材料包_第1批_20260820_0000"
            output.mkdir()
            (output / "materials.zip").write_bytes(b"fixture")
            (output / "base_intake_observations.json").write_text("{}", encoding="utf-8")
            FOLLOW_UP.write_json(control / FOLLOW_UP.STATE_FILE, {
                "schema_version": 4,
                "items": [{"id": "CL-1", "status": "Sent"}],
                "workspace_binding": {"workspace_root": str(root), "output_dir": str(output)},
                "reply_watch": {"status": "Failed", "error_type": "SystemExit", "error": "old failure"},
            })
            with mock.patch.object(FOLLOW_UP, "launch_reply_watch", return_value={"status": "Started", "pid": 12345}) as launch:
                result = FOLLOW_UP.resume_watch(
                    control, "https://tenant/base/app", control_root, root,
                    "profile", 600, 15, 8,
                )
        self.assertFalse(result["send_performed"])
        self.assertFalse(result["task_heartbeat_required"])
        self.assertTrue(result["foreground_wait_required"])
        self.assertEqual(result["watch"]["status"], "Started")
        launch.assert_called_once()

    def test_launch_reply_watch_detaches_a_bounded_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            control = Path(temp_dir) / "control"
            control.mkdir()
            FOLLOW_UP.write_json(control / FOLLOW_UP.STATE_FILE, {
                "schema_version": 3,
                "items": [{"id": "CL-1", "status": "Sent"}],
            })
            process = mock.Mock(pid=12345)
            with mock.patch.object(FOLLOW_UP.subprocess, "Popen", return_value=process) as popen:
                result = FOLLOW_UP.launch_reply_watch(
                    control, "https://tenant/base/app", Path(temp_dir) / "snapshots", Path(temp_dir) / "packages",
                    "profile", 600, 15, 8,
                )
            state = FOLLOW_UP.load_state(control)
        self.assertEqual(result["status"], "Started")
        self.assertEqual(result["pid"], 12345)
        self.assertEqual(state["reply_watch"]["status"], "Running")
        self.assertEqual(state["reply_watch"]["pid"], 12345)
        self.assertIn("deadline_at", state["reply_watch"])
        self.assertNotIn("error", state["reply_watch"])
        self.assertNotIn("error_type", state["reply_watch"])
        self.assertIn("--deadline-at", popen.call_args.args[0])
        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_dispatch_ids_are_unique_across_control_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            first_id = FOLLOW_UP.dispatch_id(first)
            second_id = FOLLOW_UP.dispatch_id(second)
            self.assertEqual(first_id, FOLLOW_UP.dispatch_id(first))
        self.assertNotEqual(first_id, second_id)

    def test_unreadable_pdf_does_not_resolve_a_missing_component(self) -> None:
        attachments = [{"field": "BP面试材料", "snapshot_path": "input/broken.pdf"}]
        mappings = [{"original_path": "input/broken.pdf", "resolved_candidate": "张三", "classification": "Candidate Material", "readability": "PDF Unreadable"}]
        self.assertEqual(FOLLOW_UP.mapped_material_components(attachments, mappings), set())

    def test_duplicate_submission_is_retained_without_a_follow_up_item(self) -> None:
        rows = [{"候选人编号": "S1-KN-001", "候选人姓名": "张三"}]
        attachments = [
            {"candidate": "张三", "field": "简历材料", "snapshot_path": "input/1", "original_name": "张三_简历.pdf"},
            {"candidate": "张三", "field": "简历材料", "snapshot_path": "input/2", "original_name": "张三_简历_副本.pdf"},
        ]
        mappings = [
            {"original_path": "input/1", "package_status": "Included", "resolved_candidate": "张三", "sha256": "same"},
            {"original_path": "input/2", "package_status": "Included", "resolved_candidate": "张三", "sha256": "same"},
        ]
        items = FOLLOW_UP.cleanup_review_items(rows, attachments, mappings)
        self.assertEqual(items, [])

    def test_plan_does_not_draft_a_duplicate_submission(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            owners = {"简历材料": {"status": "Verified", "open_id": "ou_owner", "name": "Owner"}}
            snapshot = self.write_snapshot(root, "duplicate", include_resume=True, owners=owners)
            manifest = json.loads((snapshot / "base_snapshot_manifest.json").read_text(encoding="utf-8"))
            source = dict(manifest["attachments"][0])
            source.update({"ordinal": 2, "file_token": "file_duplicate", "snapshot_path": "input/base-source/0002/张三_简历_副本.md"})
            manifest["attachments"].append(source)
            (snapshot / "base_snapshot_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            mappings = [json.loads(line) for line in (snapshot / ".material_mapping.jsonl").read_text(encoding="utf-8").splitlines()]
            mappings[0].update({"package_status": "Included", "sha256": "same"})
            mappings.append({
                "original_path": source["snapshot_path"], "resolved_candidate": "张三", "classification": "Candidate Material",
                "package_status": "Included", "sha256": "same",
            })
            (snapshot / ".material_mapping.jsonl").write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in mappings) + "\n", encoding="utf-8")
            draft = FOLLOW_UP.plan(snapshot, root / "control")
        self.assertEqual(draft["pending_review"], [])
        self.assertFalse(draft["send_performed"])

    def test_distinct_versions_remain_cleanup_reviews_but_misplaced_material_does_not(self) -> None:
        rows = [{"候选人编号": "S1-KN-001", "候选人姓名": "张三"}]
        attachments = [
            {"candidate": "张三", "field": "简历材料", "snapshot_path": "input/1", "original_name": "李四_简历.pdf"},
            {"candidate": "张三", "field": "技术作业评估", "snapshot_path": "input/2", "original_name": "张三_评估.md"},
            {"candidate": "张三", "field": "技术作业评估", "snapshot_path": "input/3", "original_name": "张三_评估_补充.md"},
        ]
        mappings = [
            {"original_path": "input/1", "package_status": "Included", "resolved_candidate": "李四", "sha256": "other-candidate"},
            {"original_path": "input/2", "package_status": "Included", "resolved_candidate": "张三", "sha256": "version-a"},
            {"original_path": "input/3", "package_status": "Included", "resolved_candidate": "张三", "sha256": "version-b"},
        ]
        items = FOLLOW_UP.cleanup_review_items(rows, attachments, mappings)
        categories = {category for item in items for category in item["categories"]}
        self.assertEqual(categories, {"Multiple Material Versions"})
        messages = [FOLLOW_UP.cleanup_message({**item, "kind": "Cleanup Review"}) for item in items]
        self.assertTrue(any("多个不同版本" in message for message in messages))

    def test_reply_listener_acknowledges_only_the_matching_sent_item(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            control = root / "control"
            draft = FOLLOW_UP.plan(self.write_snapshot(root, "missing", include_resume=False), control)
            item_id = draft["pending_review"][0]["id"]
            state = FOLLOW_UP.load_state(control)
            state["items"][0]["recipient"] = "open_id:ou_owner"
            FOLLOW_UP.write_json(control / FOLLOW_UP.STATE_FILE, state)
            FOLLOW_UP.approve(control, item_id, "APPROVE")
            FOLLOW_UP.record_sent(control, item_id, "ou_owner", "om_request", "2026-08-20T00:00:00+00:00", "SENT")
            replies = WATCHER.poll(
                control,
                "test-profile",
                lambda recipient, sent_at, profile: [
                    {"message_id": "om_request", "sender_id": "ou_owner", "create_time": "2026-08-20T00:00:00+00:00", "content": "请补件"},
                    {"message_id": "om_reply", "sender_id": "ou_owner", "create_time": "2026-08-20T00:01:00+00:00", "reply_to": "om_request", "content": "已上传"},
                ],
            )
            state = FOLLOW_UP.load_state(control)
            duplicate = WATCHER.poll(control, "test-profile", lambda *args: [{"message_id": "om_reply", "sender_id": "ou_owner", "create_time": "2026-08-20T00:01:00+00:00", "reply_to": "om_request", "content": "已上传"}])
        self.assertEqual(replies, [{"item_id": item_id, "message_id": "om_reply", "status": "Acknowledged", "refresh_required": True}])
        self.assertEqual(state["items"][0]["status"], "Acknowledged")
        self.assertEqual(duplicate, [])

    def test_naive_lark_message_time_uses_shanghai_timezone(self) -> None:
        self.assertTrue(WATCHER.message_created_after(
            {"create_time": "2026-08-20T08:01:00"},
            "2026-08-20T08:00:00+08:00",
        ))
        self.assertFalse(WATCHER.message_created_after(
            {"create_time": "2026-08-20T07:59:00"},
            "2026-08-20T08:00:00+08:00",
        ))

    def test_exact_reply_to_original_reminder_confirms_missing_without_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            control = root / "control"
            draft = FOLLOW_UP.plan(self.write_snapshot(root, "missing", include_resume=False), control)
            item_id = draft["pending_review"][0]["id"]
            state = FOLLOW_UP.load_state(control)
            state["items"][0]["recipient"] = "open_id:ou_owner"
            FOLLOW_UP.write_json(control / FOLLOW_UP.STATE_FILE, state)
            FOLLOW_UP.approve(control, item_id, "APPROVE")
            FOLLOW_UP.record_sent(control, item_id, "ou_owner", "om_request", "2026-08-20T00:00:00+00:00", "SENT")
            replies = WATCHER.poll(
                control,
                "test-profile",
                lambda *args: [{
                    "message_id": "om_reply", "sender_id": "ou_owner", "create_time": "2026-08-20T00:01:00+00:00",
                    "reply_to": "om_request", "content": "确认无法提供",
                }],
            )
            state = FOLLOW_UP.load_state(control)
        self.assertEqual(replies, [{"item_id": item_id, "message_id": "om_reply", "status": "Confirmed Missing", "refresh_required": False}])
        self.assertEqual(state["items"][0]["status"], "Confirmed Missing")
        self.assertEqual(state["items"][0]["confirmed_missing_reply_to"], "om_request")

    def test_manual_confirmation_text_cannot_confirm_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            control = root / "control"
            draft = FOLLOW_UP.plan(self.write_snapshot(root, "missing", include_resume=False), control)
            result = FOLLOW_UP.record_reply(control, draft["pending_review"][0]["id"], "确认无法提供")
            state = FOLLOW_UP.load_state(control)
        self.assertEqual(result["status"], "Acknowledged")
        self.assertEqual(state["items"][0]["status"], "Acknowledged")

    def test_follow_up_control_writes_auditable_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            control = root / "control"
            FOLLOW_UP.plan(self.write_snapshot(root, "missing", include_resume=False), control)
            audit = (control / FOLLOW_UP.AUDIT_FILE).read_text(encoding="utf-8")
        self.assertIn('"event": "plan_completed"', audit)
        self.assertFalse(list(control.glob(".follow_up_state.json.*")))

    def test_confirmed_missing_is_exported_only_as_output_bound_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            control = root / "control"
            snapshot = self.write_snapshot(root, "missing", include_resume=False)
            draft = FOLLOW_UP.plan(snapshot, control)
            state = FOLLOW_UP.load_state(control)
            state["items"][0].update({
                "status": "Confirmed Missing", "recipient_open_id": "ou_owner", "sent_message_id": "om_request",
                "confirmed_missing_message_id": "om_reply", "confirmed_missing_reply": "确认无法提供",
                "confirmed_missing_reply_to": "om_request",
            })
            FOLLOW_UP.write_json(control / FOLLOW_UP.STATE_FILE, state)
            output = root / "output"
            output.mkdir()
            (output / "base_intake_observations.json").write_text(json.dumps({
                "schema_version": 2, "captured_at": draft["snapshot_captured_at"], "observations": [], "trace_observations": [],
            }), encoding="utf-8")
            result = FOLLOW_UP.export_resolution(control, output)
            handoff = json.loads((output / "follow_up_resolution.json").read_text(encoding="utf-8"))
        self.assertEqual(result["confirmations"], 1)
        self.assertEqual(handoff["snapshot_captured_at"], draft["snapshot_captured_at"])
        self.assertEqual(handoff["confirmations"][0]["confirmed_missing_reply"], "确认无法提供")

    def test_reply_listener_requires_sender_time_and_message_association(self) -> None:
        item = {
            "id": "FU-EXAMPLE", "recipient_open_id": "ou_owner", "sent_message_id": "om_request",
            "sent_at": "2026-08-20T00:00:00+00:00",
        }
        bare_reply = {
            "message_id": "om_bare", "sender_id": "ou_owner", "create_time": "2026-08-20T00:01:00+00:00", "content": "已上传",
        }
        self.assertFalse(WATCHER.acknowledgement_matches(item, bare_reply))
        self.assertFalse(WATCHER.acknowledgement_matches(item, {**bare_reply, "message_id": "om_wrong", "sender_id": "ou_other", "reply_to": "om_request"}))
        self.assertFalse(WATCHER.acknowledgement_matches(item, {**bare_reply, "message_id": "om_early", "create_time": "2026-08-19T23:59:00+00:00", "reply_to": "om_request"}))
        self.assertTrue(WATCHER.acknowledgement_matches(item, {**bare_reply, "message_id": "om_matched", "reply_to": "om_request"}))
        self.assertTrue(WATCHER.acknowledgement_matches(item, {**bare_reply, "message_id": "om_same_minute", "create_time": "2026-08-20 08:00", "sent_at": "2026-08-20T00:00:27+00:00", "reply_to": "om_request"}))
        self.assertFalse(WATCHER.confirmed_missing_matches(item, {**bare_reply, "message_id": "om_confirm_bare", "content": "确认无法提供"}))
        self.assertTrue(WATCHER.confirmed_missing_matches(item, {**bare_reply, "message_id": "om_confirm", "reply_to": "om_request", "content": "确认无法提供"}))
        self.assertTrue(WATCHER.confirmed_missing_matches(item, {**bare_reply, "message_id": "om_natural", "reply_to": "om_request", "content": "杜文博的BP面试材料缺失，无法提供"}))
        self.assertFalse(WATCHER.confirmed_missing_matches(item, {**bare_reply, "message_id": "om_negative", "reply_to": "om_request", "content": "稍后提供"}))

    def test_cleanup_reply_words_trigger_refresh(self) -> None:
        item = {
            "id": "CL-EXAMPLE", "kind": "Cleanup Review", "recipient_open_id": "ou_owner",
            "sent_message_id": "om_request", "sent_at": "2026-08-20T00:00:00+00:00",
        }
        reply = {"message_id": "om_reply", "sender_id": "ou_owner", "create_time": "2026-08-20T00:01:00+00:00", "reply_to": "om_request"}
        self.assertTrue(WATCHER.acknowledgement_matches(item, {**reply, "content": "已处理"}))
        self.assertTrue(WATCHER.acknowledgement_matches(item, {**reply, "content": "已删除"}))
        self.assertFalse(WATCHER.acknowledgement_matches(item, {**reply, "content": "已上传"}))

    def test_unthreaded_replies_are_matched_in_send_order(self) -> None:
        state = {"items": [
            {
                "id": "RV-1", "kind": "Manual Verification", "status": "Sent", "recipient_open_id": "ou_owner",
                "sent_message_id": "om_request_1", "sent_at": "2026-08-20T00:00:00+00:00",
            },
            {
                "id": "CL-2", "kind": "Cleanup Review", "status": "Sent", "recipient_open_id": "ou_owner",
                "sent_message_id": "om_request_2", "sent_at": "2026-08-20T00:01:00+00:00",
            },
        ]}
        messages = [
            {"message_id": "om_reply_1", "sender_id": "ou_owner", "create_time": "2026-08-20T00:02:00+00:00", "content": "已处理"},
            {"message_id": "om_reply_2", "sender_id": "ou_owner", "create_time": "2026-08-20T00:03:00+00:00", "content": "已处理"},
        ]
        acknowledgements = WATCHER.acknowledge_replies(state, {"ou_owner": messages})
        self.assertEqual([item["item_id"] for item in acknowledgements], ["RV-1", "CL-2"])
        self.assertEqual([item["reply_association"] for item in state["items"]], ["sequential_unthreaded", "sequential_unthreaded"])

    def test_self_p2p_dispatch_messages_are_not_acknowledgements(self) -> None:
        state = {"items": [
            {
                "id": "RV-1", "kind": "Manual Verification", "status": "Sent", "recipient_open_id": "ou_owner",
                "sent_message_id": "om_request_1", "sent_at": "2026-08-20T00:00:27+00:00",
            },
            {
                "id": "CL-2", "kind": "Cleanup Review", "status": "Sent", "recipient_open_id": "ou_owner",
                "sent_message_id": "om_request_2", "sent_at": "2026-08-20T00:00:30+00:00",
            },
        ]}
        dispatched_messages = [
            {"message_id": "om_request_1", "sender_id": "ou_owner", "create_time": "2026-08-20 08:00", "content": "处理后请回复已处理"},
            {"message_id": "om_request_2", "sender_id": "ou_owner", "create_time": "2026-08-20 08:00", "content": "处理后请回复已处理"},
        ]
        self.assertEqual(WATCHER.acknowledge_replies(state, {"ou_owner": dispatched_messages}), [])
        self.assertEqual([item["status"] for item in state["items"]], ["Sent", "Sent"])

    def test_refresh_reuses_selected_table_batch_and_business_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            snapshot_root = root / ".招聘材料控制"
            snapshot = snapshot_root / "previous"
            snapshot.mkdir(parents=True)
            (snapshot / "base_snapshot_manifest.json").write_text(json.dumps({
                "selection": {"table_id": "tbl_fixture", "batch": "Batch 2"},
            }), encoding="utf-8")
            control = snapshot_root / "control"
            control.mkdir()
            output = root / "招聘材料包_第2批_20260820_0000"
            output.mkdir()
            (output / "materials.zip").write_bytes(b"fixture")
            (output / "base_intake_observations.json").write_text("{}", encoding="utf-8")
            FOLLOW_UP.write_json(control / FOLLOW_UP.STATE_FILE, {
                "schema_version": 4, "items": [], "last_snapshot_dir": str(snapshot),
                "workspace_binding": {"workspace_root": str(root), "output_dir": str(output)},
            })
            completed = type("Result", (), {"returncode": 0, "stdout": '{"refresh":{"mode":"Differential"}}\n', "stderr": ""})()
            draft = {"verified_item_ids": [], "rebuild_candidate_ids": [], "processing_statuses": [], "base_written": True}
            with mock.patch.object(WATCHER.subprocess, "run", return_value=completed) as run, mock.patch.object(
                WATCHER.manage_follow_up, "prepare_preview", return_value=draft,
            ), mock.patch.object(
                WATCHER, "replace_business_handoff", return_value=(output, control / "previous_handoffs" / "archived"),
            ):
                result = WATCHER.refresh(control, "https://tenant/base/app", snapshot_root, root, "profile", 8)
        command = run.call_args.args[0]
        self.assertIn("--table-id", command)
        self.assertIn("tbl_fixture", command)
        self.assertIn("--batch", command)
        self.assertIn("Batch 2", command)
        self.assertEqual(result["handoff_dir"], str(output))
        self.assertTrue(result["base_written"])

    def test_refresh_replaces_the_bound_business_handoff_without_a_second_visible_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            control = root / ".招聘材料控制" / "Batch_1"
            control.mkdir(parents=True)
            output = root / "招聘材料包_第1批_20260820_0000"
            staged = root / "招聘材料包_第1批_20260820_0010"
            for directory, payload in ((output, b"old"), (staged, b"new")):
                directory.mkdir()
                (directory / "materials.zip").write_bytes(payload)
                (directory / "base_intake_observations.json").write_text("{}", encoding="utf-8")
            FOLLOW_UP.write_json(control / FOLLOW_UP.STATE_FILE, {
                "schema_version": 4,
                "items": [],
                "workspace_binding": {"workspace_root": str(root), "output_dir": str(output)},
            })
            current, archived = WATCHER.replace_business_handoff(control, staged, "20260820_0010")
            visible = sorted(path.name for path in root.glob("招聘材料包_第1批_*") if path.is_dir())
            current_bytes = (current / "materials.zip").read_bytes()
            archived_bytes = (archived / "materials.zip").read_bytes()
        self.assertEqual(current, output.resolve())
        self.assertEqual(current_bytes, b"new")
        self.assertEqual(archived_bytes, b"old")
        self.assertEqual(visible, [output.name])

    def test_completion_notice_can_be_claimed_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            control = Path(temp_dir) / "control"
            control.mkdir()
            FOLLOW_UP.write_json(control / FOLLOW_UP.STATE_FILE, {"schema_version": 4, "items": []})
            FOLLOW_UP.record_watch_completion(
                control,
                timed_out=False,
                refreshes=1,
                reminders=[],
                acknowledged_count=2,
                unanswered_count=0,
            )
            first = FOLLOW_UP.claim_completion_notice(control)
            second = FOLLOW_UP.claim_completion_notice(control)
        self.assertTrue(first["notice_ready"])
        self.assertIn("2 项回复", first["message"])
        self.assertFalse(second["notice_ready"])
        self.assertEqual(second["reason"], "Already Reported")

    def test_foreground_wait_claims_the_terminal_notice_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            control = Path(temp_dir) / "control"
            control.mkdir()
            FOLLOW_UP.write_json(control / FOLLOW_UP.STATE_FILE, {
                "schema_version": 5,
                "items": [],
            })
            FOLLOW_UP.record_watch_completion(
                control,
                timed_out=True,
                refreshes=0,
                reminders=[{"item_id": "FU-1", "message_id": "om_reminder"}],
                acknowledged_count=0,
                unanswered_count=1,
            )
            with mock.patch.object(FOLLOW_UP, "hrd_queue_after_completion", return_value={}):
                result = FOLLOW_UP.wait_for_completion_notice(control, 1, "https://tenant/base/app", "tbl", "profile")
            with self.assertRaises(SystemExit):
                FOLLOW_UP.wait_for_completion_notice(control, 1, "https://tenant/base/app", "tbl", "profile")
        self.assertEqual(result["action"], "wait-for-completion")
        self.assertTrue(result["notice_ready"])
        self.assertTrue(result["timed_out"])

    def test_foreground_wait_automatically_queues_hrd_after_clean_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            control = root / ".招聘材料控制" / "第1批_20260820_0000" / "follow_up"
            control.mkdir(parents=True)
            output = root / "招聘材料包_第1批_20260820_0000"
            output.mkdir()
            (output / "materials.zip").write_bytes(b"fixture")
            (output / "base_intake_observations.json").write_text("{}", encoding="utf-8")
            FOLLOW_UP.write_json(control / FOLLOW_UP.STATE_FILE, {
                "schema_version": 5,
                "items": [],
                "workspace_binding": {"workspace_root": str(root), "output_dir": str(output)},
            })
            FOLLOW_UP.record_watch_completion(
                control,
                timed_out=False,
                refreshes=1,
                reminders=[],
                acknowledged_count=3,
                unanswered_count=0,
            )
            queue = {"material_gate": "Closed", "pending_opinion_candidates": ["张三"]}
            with mock.patch.object(FOLLOW_UP, "hrd_queue_after_completion", return_value=queue):
                result = FOLLOW_UP.wait_for_completion_notice(control, 1, "https://tenant/base/app", "tbl", "profile")
        self.assertEqual(result["next_action"], "collect_hrd_opinions")
        self.assertEqual(result["hrd_queue"], queue)

    def test_completion_recovery_returns_a_claimed_timeout_result_without_resending(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            control = Path(temp_dir) / "control"
            control.mkdir()
            FOLLOW_UP.write_json(control / FOLLOW_UP.STATE_FILE, {
                "schema_version": 5,
                "items": [{"id": "FU-1", "status": "Sent"}],
            })
            FOLLOW_UP.record_watch_completion(
                control,
                timed_out=True,
                refreshes=0,
                reminders=[{"item_id": "FU-1", "message_id": "om_reminder"}],
                acknowledged_count=0,
                unanswered_count=1,
            )
            FOLLOW_UP.claim_completion_notice(control)
            result = FOLLOW_UP.read_completion_result(control, "https://tenant/base/app", "tbl", "profile")
        self.assertTrue(result["terminal_ready"])
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["notice_status"], "Claimed")
        self.assertNotIn("hrd_queue", result)

    def test_watch_refreshes_immediately_when_all_sent_items_reply(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            control = Path(temp_dir) / "control"
            control.mkdir()
            FOLLOW_UP.write_json(control / FOLLOW_UP.STATE_FILE, {"schema_version": 3, "items": []})
            replies = iter([[
                {"item_id": "CL-1", "message_id": "om_1", "status": "Acknowledged", "refresh_required": True},
                {"item_id": "CL-2", "message_id": "om_2", "status": "Acknowledged", "refresh_required": True},
            ]])
            refreshed = {"base_written": True, "processing_statuses": []}
            with mock.patch.object(WATCHER.time, "monotonic", side_effect=[0, 0]), mock.patch.object(
                WATCHER.time, "sleep",
            ), mock.patch.object(WATCHER, "unanswered_items", return_value=[]):
                result = WATCHER.watch(
                    control, "profile", "https://tenant/base/app", Path(temp_dir) / "snapshots", Path(temp_dir) / "packages",
                    8, 1, 1, poller=lambda *_: next(replies), refresher=lambda *_: refreshed,
                )
        self.assertEqual(len(result["acknowledgements"]), 2)
        self.assertEqual(result["refreshes"], [refreshed])
        self.assertTrue(result["base_written"])

    def test_watch_accepts_a_reminder_reply_in_one_final_bounded_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            control = Path(temp_dir) / "control"
            control.mkdir()
            FOLLOW_UP.write_json(control / FOLLOW_UP.STATE_FILE, {"schema_version": 3, "items": []})
            replies = iter([
                [{"item_id": "CL-1", "message_id": "om_1", "status": "Acknowledged", "refresh_required": True}],
                [],
                [{"item_id": "CL-2", "message_id": "om_reminder_reply", "status": "Acknowledged", "refresh_required": True}],
            ])
            refreshed = {"base_written": True, "processing_statuses": []}
            with mock.patch.object(WATCHER, "wall_deadline_epoch", side_effect=[1, 2]), mock.patch.object(
                WATCHER.time, "time", return_value=0,
            ), mock.patch.object(
                WATCHER.time, "monotonic", side_effect=[0, 0, 1, 1, 1],
            ), mock.patch.object(
                WATCHER.time, "sleep",
            ) as sleep, mock.patch.object(
                WATCHER, "unanswered_items", side_effect=[[{"candidate": "陈晓彤"}], [{"candidate": "陈晓彤"}], [], []],
            ):
                reminder = mock.Mock(return_value=[{"item_id": "CL-2", "message_id": "om_reminder"}])
                result = WATCHER.watch(
                    control, "profile", "https://tenant/base/app", Path(temp_dir) / "snapshots", Path(temp_dir) / "packages",
                    8, 1, 1, poller=lambda *_: next(replies), refresher=lambda *_: refreshed, reminder=reminder,
                )
        self.assertEqual(len(result["acknowledgements"]), 2)
        self.assertEqual(result["refreshes"], [refreshed])
        self.assertEqual(result["timeout_reminders"], [{"item_id": "CL-2", "message_id": "om_reminder"}])
        self.assertEqual(result["next_action"], "collect_hrd_opinions")
        reminder.assert_called_once_with(control, "profile")
        sleep.assert_called_once_with(1)

    def test_reply_to_timeout_reminder_is_a_valid_acknowledgement(self) -> None:
        item = {
            "id": "CL-EXAMPLE", "kind": "Cleanup Review", "status": "Sent", "recipient_open_id": "ou_owner",
            "sent_message_id": "om_original", "timeout_reminder_message_id": "om_reminder",
            "sent_at": "2026-08-20T00:00:00+00:00",
        }
        message = {
            "message_id": "om_reply", "sender_id": "ou_owner", "create_time": "2026-08-20T00:01:00+00:00",
            "reply_to": "om_reminder", "content": "已处理",
        }
        self.assertTrue(WATCHER.acknowledgement_matches(item, message))

    def test_timeout_reminder_is_not_mistaken_for_its_own_reply_in_self_p2p(self) -> None:
        item = {
            "id": "FU-EXAMPLE", "kind": "Material Follow-up", "status": "Sent", "recipient_open_id": "ou_owner",
            "sent_message_id": "om_original", "timeout_reminder_message_id": "om_reminder",
            "sent_at": "2026-08-20T00:00:00+00:00",
        }
        outbound_reminder = {
            "message_id": "om_reminder", "sender_id": "ou_owner", "create_time": "2026-08-20T00:10:00+00:00",
            "reply_to": "om_original", "content": "请在本条消息下回复“已上传”。",
        }
        self.assertFalse(WATCHER.acknowledgement_matches(item, outbound_reminder))

    def test_watch_retries_transient_p2p_failure_inside_same_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            control = Path(temp_dir) / "control"
            control.mkdir()
            FOLLOW_UP.write_json(control / FOLLOW_UP.STATE_FILE, {"schema_version": 3, "items": []})
            retries = iter([WATCHER.RetryablePollError("internal error"), []])
            def poller(*_args: object) -> list[dict[str, object]]:
                result = next(retries)
                if isinstance(result, BaseException):
                    raise result
                return result
            with mock.patch.object(WATCHER.time, "time", side_effect=[0, 0, 0, 1]), mock.patch.object(
                WATCHER.time, "monotonic", side_effect=[0, 0, 1],
            ), mock.patch.object(
                WATCHER.time, "sleep",
            ) as sleep, mock.patch.object(WATCHER, "unanswered_items", return_value=[]):
                reminder = mock.Mock(return_value=[])
                result = WATCHER.watch(
                    control, "profile", "https://tenant/base/app", Path(temp_dir) / "snapshots", Path(temp_dir) / "packages",
                    8, 1, 1, poller=poller, reminder=reminder,
                )
        self.assertEqual(result["timeout_reminders"], [])
        self.assertEqual(result["next_action"], "collect_hrd_opinions")
        sleep.assert_called_once_with(1)
        reminder.assert_called_once_with(control, "profile")

    def test_watch_uses_wall_clock_deadline_after_system_sleep(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            control = Path(temp_dir) / "control"
            control.mkdir()
            FOLLOW_UP.write_json(control / FOLLOW_UP.STATE_FILE, {"schema_version": 3, "items": []})
            reminder = mock.Mock(return_value=[])
            with mock.patch.object(WATCHER.time, "time", side_effect=[0, 0, 2]), mock.patch.object(
                WATCHER.time, "monotonic", side_effect=[0, 0],
            ), mock.patch.object(WATCHER.time, "sleep") as sleep, mock.patch.object(
                WATCHER, "unanswered_items", return_value=[],
            ):
                result = WATCHER.watch(
                    control, "profile", "https://tenant/base/app", Path(temp_dir) / "snapshots", Path(temp_dir) / "packages",
                    8, 1, 1, poller=lambda *_: [], reminder=reminder,
                )
        self.assertEqual(result["timeout_reminders"], [])
        sleep.assert_not_called()
        reminder.assert_called_once_with(control, "profile")

    def test_cli_json_marks_only_retryable_api_errors_as_transient(self) -> None:
        response = type("Result", (), {
            "returncode": 1,
            "stdout": json.dumps({"ok": False, "error": {"message": "internal error", "retryable": True}}),
            "stderr": "",
        })()
        with mock.patch.object(WATCHER.subprocess, "run", return_value=response):
            with self.assertRaises(WATCHER.RetryablePollError):
                WATCHER.cli_json(["im", "+chat-messages-list"], "profile")

    def test_cleanup_review_cannot_be_confirmed_missing(self) -> None:
        item = {
            "id": "CL-EXAMPLE", "kind": "Cleanup Review", "status": "Sent", "recipient_open_id": "ou_owner",
            "sent_message_id": "om_request", "sent_at": "2026-08-20T00:00:00+00:00",
        }
        messages = [{
            "message_id": "om_reply", "sender_id": "ou_owner", "create_time": "2026-08-20T00:01:00+00:00",
            "reply_to": "om_request", "content": "确认无法提供",
        }]
        self.assertEqual(WATCHER.acknowledge_replies({"items": [item]}, {"ou_owner": messages}), [])
        self.assertEqual(item["status"], "Sent")

    def test_timeout_reminder_is_sent_once_and_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            control = Path(temp_dir) / "control"
            control.mkdir()
            FOLLOW_UP.write_json(control / FOLLOW_UP.STATE_FILE, {
                "schema_version": 3,
                "dispatch_id": "0123456789abcdef",
                "items": [{
                    "id": "FU-1", "kind": "Material Follow-up", "status": "Sent",
                    "candidate": "杜文博", "material_field": "BP面试材料", "material_component": "面试转写",
                    "recipient_open_id": "ou_owner", "sent_message_id": "om_original",
                }],
            })
            with mock.patch.object(FOLLOW_UP, "cli_json", return_value={"ok": True, "data": {"message_id": "om_reminder"}}) as cli:
                first = FOLLOW_UP.send_timeout_reminders(control, "profile")
                second = FOLLOW_UP.send_timeout_reminders(control, "profile")
            state = FOLLOW_UP.load_state(control)
        self.assertEqual(first, [{"item_id": "FU-1", "message_id": "om_reminder"}])
        self.assertEqual(second, [])
        self.assertEqual(cli.call_count, 1)
        sent_arguments = cli.call_args.args[0]
        self.assertIn(FOLLOW_UP.MISSING_MATERIAL_UNAVAILABLE_HINT, sent_arguments[sent_arguments.index("--text") + 1])
        self.assertEqual(state["items"][0]["timeout_reminder_message_id"], "om_reminder")


if __name__ == "__main__":
    unittest.main()
