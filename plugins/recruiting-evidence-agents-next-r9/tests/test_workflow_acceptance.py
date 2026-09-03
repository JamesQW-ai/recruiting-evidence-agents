#!/usr/bin/env python3
"""Offline end-to-end acceptance for the business-facing recruiting workflow."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import build_ceo_package as CEO  # noqa: E402
import manage_follow_up as FOLLOW_UP  # noqa: E402
import run_control  # noqa: E402
import watch_follow_up_replies as WATCHER  # noqa: E402


class WorkflowAcceptanceTests(unittest.TestCase):
    def test_workspace_to_follow_up_to_ceo_review_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            package = workspace / "招聘材料包_第1批_20260826_1711"
            run_control.validate_package_location(package, "Batch 1", workspace)
            self.assertEqual(run_control.safe_batch_label("Batch 1"), "第1批")
            self.assertEqual(run_control.business_batch_label("Batch 1"), "第1批")
            self.assertEqual(run_control.display_batch_label("第Batch 1批"), "第 1 批")
            with self.assertRaisesRegex(SystemExit, "直接位于工作目录"):
                run_control.validate_package_parent(workspace / "04_outputs" / package.name, workspace)

        state = {"items": [
            {
                "id": "RV-CHECK", "kind": "Manual Verification", "status": "Sent",
                "recipient_open_id": "ou_owner", "sent_message_id": "om_request_1",
                "sent_at": "2026-08-26T09:00:00+00:00",
            },
            {
                "id": "CL-CLEAN", "kind": "Cleanup Review", "status": "Sent",
                "recipient_open_id": "ou_owner", "sent_message_id": "om_request_2",
                "sent_at": "2026-08-26T09:01:00+00:00",
            },
        ]}
        replies = [
            {"message_id": "om_reply_1", "sender_id": "ou_owner", "create_time": "2026-08-26T09:02:00+00:00", "content": "候选人材料已处理"},
            {"message_id": "om_reply_2", "sender_id": "ou_owner", "create_time": "2026-08-26T09:03:00+00:00", "content": "损坏文件已删除"},
        ]
        acknowledgements = WATCHER.acknowledge_replies(state, {"ou_owner": replies})
        self.assertEqual([item["item_id"] for item in acknowledgements], ["RV-CHECK", "CL-CLEAN"])
        self.assertTrue(all(item["association"] == "sequential_unthreaded" for item in acknowledgements))

        rows = [
            {"候选人姓名": name, "技术面试流程状态": "通过", "BP面试流程状态": "通过", "HRD面试流程状态": "通过"}
            for name in ("张三", "孙明远", "杜文博")
        ]
        rows.extend(
            {"候选人姓名": f"结束候选人{index}", "技术面试流程状态": "Stopped", "BP面试流程状态": "Not Reached", "HRD面试流程状态": "Not Reached"}
            for index in range(1, 9)
        )
        rows.append({"候选人姓名": "状态待确认", "技术面试流程状态": "Not Provided", "BP面试流程状态": "Not Provided", "HRD面试流程状态": "Not Provided"})
        outcomes = CEO.classify_outcomes(rows)
        summary = CEO.ceo_summary_message({"batch_label": "第1批"}, outcomes, [])
        self.assertEqual(
            summary,
            "第 1 批共 12 位候选人的流程汇总如下：\n"
            "完整通过 3 位：张三、孙明远、杜文博；\n"
            "明确未通过/退出 8 位；\n"
            "状态未能确认 1 位。\n"
            "其中存在异常 0 位。\n"
            "附件为候选人审阅页和材料 ZIP；详情请见附件。",
        )
        rendered = CEO.render_html(
            control={"batch": "Batch 1", "batch_label": "第Batch 1批"}, register=rows,
            selected=[{"candidate": "张三", "current_recommendation": "建议录用", "current_rationale": "岗位经历与协作沟通均符合本轮要求。"}],
            outcomes=outcomes, observations=[], duplicate_count=0, captured_at="2026-08-26T09:00:00+08:00",
        )
        self.assertEqual(rendered.count('class="funnel-step'), 4)
        self.assertEqual(rendered.count('class="funnel-track"'), 4)
        self.assertNotIn("{{", rendered)
        self.assertIn("批次：第 1 批", rendered)
        self.assertNotIn("第Batch 1批", rendered)


if __name__ == "__main__":
    unittest.main()
