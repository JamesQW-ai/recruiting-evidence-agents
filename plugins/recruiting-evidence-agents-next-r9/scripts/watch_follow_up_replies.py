#!/usr/bin/env python3
"""Poll user-identity P2P replies and refresh the D13 read-only snapshot after acknowledgement."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import manage_follow_up
import run_control


class RetryablePollError(RuntimeError):
    """A transient Feishu read failure that may be retried inside this watch window."""


def wall_deadline_epoch(deadline_at: str | None, timeout_seconds: int) -> float:
    if deadline_at is None:
        return time.time() + timeout_seconds
    try:
        deadline = datetime.fromisoformat(deadline_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise SystemExit("监听截止时间无效。") from error
    if deadline.tzinfo is None:
        raise SystemExit("监听截止时间缺少时区。")
    return deadline.timestamp()


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def cli_json(arguments: list[str], profile: str) -> dict[str, Any]:
    command = ["lark-cli", "--profile", profile, *arguments]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise SystemExit(result.stderr.strip() or "lark-cli 未返回有效 JSON。") from error
    if result.returncode != 0 or not payload.get("ok"):
        error = payload.get("error", {})
        message = error.get("message", "未知 lark-cli 错误") if isinstance(error, dict) else "未知 lark-cli 错误"
        if isinstance(error, dict) and error.get("retryable") is True:
            raise RetryablePollError(message)
        raise SystemExit(f"读取补件回复失败：{message}")
    return payload


def list_p2p_messages(recipient_open_id: str, sent_at: str, profile: str) -> list[dict[str, Any]]:
    payload = cli_json(
        [
            "im", "+chat-messages-list", "--user-id", recipient_open_id,
            "--start", sent_at, "--order", "asc", "--page-all",
            "--no-reactions", "--as", "user", "--format", "json",
        ],
        profile,
    )
    data = payload.get("data", {})
    messages = data.get("messages", []) if isinstance(data, dict) else []
    if not isinstance(messages, list) or not all(isinstance(message, dict) for message in messages):
        raise SystemExit("飞书消息列表结构无效。")
    return messages


def message_sender_open_id(message: dict[str, Any]) -> str | None:
    direct = message.get("sender_id")
    if isinstance(direct, str) and direct:
        return direct
    sender = message.get("sender")
    if not isinstance(sender, dict):
        return None
    for key in ("open_id", "id"):
        value = sender.get(key)
        if isinstance(value, str) and value:
            return value
    sender_id = sender.get("sender_id")
    return sender_id if isinstance(sender_id, str) and sender_id else None


def message_created_after(message: dict[str, Any], sent_at: str) -> bool:
    value = message.get("create_time")
    if not isinstance(value, str) or not value:
        return False
    try:
        created = datetime.fromtimestamp(int(value) / 1000, timezone.utc) if value.isdigit() else datetime.fromisoformat(value.replace("Z", "+00:00"))
        sent = datetime.fromisoformat(sent_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if created.tzinfo is None:
        # Lark's message shortcut can return local wall-clock timestamps.
        created = created.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    if sent.tzinfo is None:
        sent = sent.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    # P2P list responses can omit seconds. Treat that timestamp as a one-minute
    # precision window; exact message association is still required by callers.
    minute_precision = isinstance(value, str) and "T" not in value and value.count(":") == 1
    return (created + timedelta(minutes=1) > sent) if minute_precision else created > sent


def outbound_message_ids(item: dict[str, Any]) -> set[str]:
    """Return every message authored by the plugin for one follow-up item."""
    return {
        value
        for value in (item.get("sent_message_id"), item.get("timeout_reminder_message_id"))
        if isinstance(value, str) and value
    }


def reply_matches_sent_item(item: dict[str, Any], message: dict[str, Any]) -> bool:
    if message.get("message_id") in outbound_message_ids(item) or message.get("deleted"):
        return False
    recipient = item.get("recipient_open_id")
    sent_at = item.get("sent_at")
    if not isinstance(recipient, str) or not isinstance(sent_at, str):
        return False
    if message_sender_open_id(message) != recipient or not message_created_after(message, sent_at):
        return False
    content = message.get("content")
    message_ids = outbound_message_ids(item)
    return (
        isinstance(content, str) and item["id"] in content
        or message.get("reply_to") in message_ids
        or message.get("parent_id") in message_ids
        or message.get("root_id") in message_ids
    )


def acknowledgement_matches(item: dict[str, Any], message: dict[str, Any]) -> bool:
    content = message.get("content")
    if not isinstance(content, str) or not reply_matches_sent_item(item, message):
        return False
    return acknowledgement_text_matches(item, content)


def acknowledgement_text_matches(item: dict[str, Any], content: str) -> bool:
    if item.get("kind", "Material Follow-up") == "Material Follow-up":
        return any(value in content for value in ("已上传", "已补交"))
    return any(value in content for value in ("已处理", "已删除"))


def is_unthreaded_acknowledgement(item: dict[str, Any], message: dict[str, Any]) -> bool:
    """Accept a precise acknowledgement without thread metadata as an ordered fallback."""
    if (
        message.get("message_id") in outbound_message_ids(item)
        or message.get("deleted")
        or any(message.get(key) for key in ("reply_to", "parent_id", "root_id"))
    ):
        return False
    recipient = item.get("recipient_open_id")
    sent_at = item.get("sent_at")
    content = message.get("content")
    return (
        isinstance(recipient, str)
        and isinstance(sent_at, str)
        and isinstance(content, str)
        and message_sender_open_id(message) == recipient
        and message_created_after(message, sent_at)
        and acknowledgement_text_matches(item, content)
    )


def confirmed_missing_matches(item: dict[str, Any], message: dict[str, Any]) -> bool:
    content = message.get("content")
    message_ids = outbound_message_ids(item)
    return (
        isinstance(content, str)
        and manage_follow_up.confirmed_missing_reply(content)
        and reply_matches_sent_item(item, message)
        and (message.get("reply_to") in message_ids or message.get("root_id") in message_ids)
    )


def acknowledge_replies(state: dict[str, Any], messages_by_recipient: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    sent_items = [item for item in state["items"] if item.get("status") == "Sent"]
    used_message_ids = {
        item.get("reply_message_id")
        for item in state["items"]
        if isinstance(item.get("reply_message_id"), str)
    }
    # A self-P2P test account is both sender and recipient. Exclude every
    # dispatch message before accepting the unthreaded-reply fallback.
    used_message_ids.update(
        message_id
        for item in state["items"]
        for message_id in outbound_message_ids(item)
    )
    acknowledgements: list[dict[str, Any]] = []
    for item in sent_items:
        recipient = item.get("recipient_open_id")
        if not isinstance(recipient, str):
            continue
        for message in messages_by_recipient.get(recipient, []):
            message_id = message.get("message_id")
            if not isinstance(message_id, str) or not message_id or message_id in used_message_ids:
                continue
            if item.get("kind", "Material Follow-up") == "Material Follow-up" and confirmed_missing_matches(item, message):
                item["status"] = "Confirmed Missing"
                item["confirmed_missing_at"] = str(message["create_time"])
                item["confirmed_missing_message_id"] = message_id
                item["confirmed_missing_reply"] = str(message["content"])
                item["confirmed_missing_reply_to"] = message.get("reply_to")
                item["confirmed_missing_root_id"] = message.get("root_id")
                used_message_ids.add(message_id)
                acknowledgements.append({"item_id": item["id"], "message_id": message_id, "status": "Confirmed Missing", "refresh_required": False})
                break
            if acknowledgement_matches(item, message):
                item["status"] = "Acknowledged"
                item["last_reply"] = str(message["content"])
                item["last_reply_at"] = str(message["create_time"])
                item["reply_message_id"] = message_id
                used_message_ids.add(message_id)
                acknowledgements.append({"item_id": item["id"], "message_id": message_id, "status": "Acknowledged", "refresh_required": True})
                break
    # P2P replies sent from the regular chat composer can lose their thread
    # metadata. For only the remaining sent items, consume qualified replies
    # in send order, never reusing a reply for another item.
    recipients = sorted({str(item.get("recipient_open_id")) for item in state["items"] if item.get("status") == "Sent" and isinstance(item.get("recipient_open_id"), str)})
    for recipient in recipients:
        pending = [
            item for item in state["items"]
            if item.get("status") == "Sent" and item.get("recipient_open_id") == recipient
        ]
        pending.sort(key=lambda item: str(item.get("sent_at", "")))
        for message in messages_by_recipient.get(recipient, []):
            message_id = message.get("message_id")
            if not pending or not isinstance(message_id, str) or not message_id or message_id in used_message_ids:
                continue
            item = pending[0]
            if not is_unthreaded_acknowledgement(item, message):
                continue
            item["status"] = "Acknowledged"
            item["last_reply"] = str(message["content"])
            item["last_reply_at"] = str(message["create_time"])
            item["reply_message_id"] = message_id
            item["reply_association"] = "sequential_unthreaded"
            used_message_ids.add(message_id)
            pending.pop(0)
            acknowledgements.append({
                "item_id": item["id"], "message_id": message_id, "status": "Acknowledged",
                "refresh_required": True, "association": "sequential_unthreaded",
            })
    if acknowledgements:
        state["updated_at"] = manage_follow_up.utc_now()
    return acknowledgements


def poll(control_dir: Path, profile: str, fetch_messages: Callable[[str, str, str], list[dict[str, Any]]] = list_p2p_messages) -> list[dict[str, Any]]:
    with manage_follow_up.control_lock(control_dir):
        state = manage_follow_up.load_state(control_dir)
        messages_by_recipient: dict[str, list[dict[str, Any]]] = {}
        for item in state["items"]:
            if item.get("status") != "Sent":
                continue
            recipient = item.get("recipient_open_id")
            sent_at = item.get("sent_at")
            if not isinstance(recipient, str) or not isinstance(sent_at, str):
                raise SystemExit("已发送补件项缺少监听所需的 recipient_open_id 或 sent_at。")
            if recipient not in messages_by_recipient:
                messages_by_recipient[recipient] = fetch_messages(recipient, sent_at, profile)
        acknowledgements = acknowledge_replies(state, messages_by_recipient)
        if acknowledgements:
            state["updated_at"] = manage_follow_up.utc_now()
            manage_follow_up.write_json(control_dir / manage_follow_up.STATE_FILE, state)
            for acknowledgement in acknowledgements:
                manage_follow_up.append_audit(
                    control_dir,
                    "follow_up_confirmed_missing" if acknowledgement["status"] == "Confirmed Missing" else "reply_acknowledged",
                    item_id=acknowledgement["item_id"],
                    message_id=acknowledgement["message_id"],
                )
        return acknowledgements


def replace_business_handoff(control_dir: Path, staged_handoff_dir: Path, stamp: str) -> tuple[Path, Path]:
    """Keep exactly one visible material package in the originally selected workspace."""
    state = manage_follow_up.load_state(control_dir)
    binding = state.get("workspace_binding")
    if not isinstance(binding, dict) or not isinstance(binding.get("workspace_root"), str) or not isinstance(binding.get("output_dir"), str):
        raise SystemExit("补件控制尚未绑定工作目录和当前材料包；不能替换刷新结果。")
    workspace_root = Path(binding["workspace_root"]).resolve()
    if staged_handoff_dir.resolve().parent != workspace_root:
        raise SystemExit("刷新材料包不在首次选定的工作目录；不能替换当前业务包。")
    output_dir = Path(binding["output_dir"]).resolve()
    if not (staged_handoff_dir / "materials.zip").is_file():
        raise SystemExit("刷新材料包缺少 materials.zip；不能替换当前业务包。")
    if not (output_dir / "materials.zip").is_file():
        raise SystemExit("当前业务材料包已变化；不能自动替换。")
    # A completed HRD/CEO package is no longer a plain material handoff. It must
    # be superseded through a separately reviewed candidate package, not moved.
    if any((output_dir / name).exists() for name in ("candidate_pack.html", "ceo_summary_message.md")):
        raise SystemExit("当前材料包已进入正式审阅或 CEO 阶段；不能自动替换，请新建候选人更新包。")
    archive_root = control_dir / "previous_handoffs"
    archive_root.mkdir(parents=True, exist_ok=True)
    archived = archive_root / f"{output_dir.name}__{stamp}"
    if archived.exists():
        raise SystemExit("本轮旧材料包归档目录已存在；不能覆盖。")
    shutil.move(str(output_dir), str(archived))
    try:
        shutil.move(str(staged_handoff_dir), str(output_dir))
    except BaseException:
        shutil.move(str(archived), str(output_dir))
        raise
    manage_follow_up.append_audit(
        control_dir,
        "business_handoff_replaced",
        output_dir=str(output_dir),
        archived_handoff_dir=str(archived),
    )
    return output_dir, archived


def refresh(control_dir: Path, base_url: str, snapshot_root: Path, handoff_root: Path, profile: str, download_workers: int) -> dict[str, Any]:
    binding = manage_follow_up.require_workspace_binding(control_dir, snapshot_root, handoff_root)
    workspace_root = Path(binding["workspace_root"])
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M")
    snapshot_dir = (snapshot_root / f"快照_{stamp}").resolve()
    state = manage_follow_up.load_state(control_dir)
    previous_snapshot = state.get("last_snapshot_dir")
    if not isinstance(previous_snapshot, str):
        raise SystemExit("补件控制缺少当前快照绑定；不能刷新。")
    previous_manifest = manage_follow_up.read_json(
        Path(previous_snapshot) / "base_snapshot_manifest.json", "base_snapshot_manifest.json",
    )
    selection = previous_manifest.get("selection")
    if not isinstance(selection, dict) or not isinstance(selection.get("table_id"), str) or not isinstance(selection.get("batch"), str):
        raise SystemExit("当前快照缺少数据表或批次范围；不能刷新。")
    table_id = selection["table_id"]
    batch = selection["batch"]
    staged_handoff_dir = (workspace_root / f"招聘材料包_{run_control.business_batch_label(batch)}_{stamp}").resolve()
    if snapshot_dir.exists() or staged_handoff_dir.exists():
        raise SystemExit("刷新目录已存在；请使用新的 snapshot-root 与 handoff-root。")
    command = [
        sys.executable, str(Path(__file__).with_name("build_base_materials.py")),
        "--base-url", base_url, "--snapshot-dir", str(snapshot_dir),
        "--output-dir", str(staged_handoff_dir), "--table-id", table_id, "--batch", batch, "--lark-profile", profile,
        "--workspace-root", str(workspace_root),
        "--download-workers", str(download_workers),
    ]
    if Path(previous_snapshot).is_dir():
        command.extend(["--previous-snapshot", previous_snapshot])
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or result.stdout.strip() or "补件回复后的 Base 快照失败。")
    try:
        snapshot_result = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        snapshot_result = {"refresh": "Not Reported"}
    draft = manage_follow_up.prepare_preview(snapshot_dir, control_dir, base_url, table_id, profile)
    handoff_dir, archived_handoff_dir = replace_business_handoff(control_dir, staged_handoff_dir, stamp)
    return {
        "snapshot_dir": str(snapshot_dir),
        "handoff_dir": str(handoff_dir),
        "archived_handoff_dir": str(archived_handoff_dir),
        "verified_item_ids": draft["verified_item_ids"],
        "rebuild_candidate_ids": draft["rebuild_candidate_ids"],
        "refresh": snapshot_result.get("refresh", "Not Reported"),
        "processing_statuses": draft["processing_statuses"],
        "base_written": draft["base_written"],
    }


def unanswered_items(control_dir: Path) -> list[dict[str, str]]:
    state = manage_follow_up.load_state(control_dir)
    return [
        {
            "candidate": str(item.get("candidate", "Not Provided")),
            "material_field": str(item.get("material_field", "材料")),
            "kind": str(item.get("kind", "Material Follow-up")),
        }
        for item in state["items"]
        if item.get("status") == "Sent"
    ]


def watch(
    control_dir: Path,
    profile: str,
    base_url: str,
    snapshot_root: Path,
    handoff_root: Path,
    download_workers: int,
    timeout_seconds: int,
    interval_seconds: int,
    poller: Callable[[Path, str], list[dict[str, Any]]] = poll,
    refresher: Callable[[Path, str, Path, Path, str, int], dict[str, Any]] = refresh,
    reminder: Callable[[Path, str], list[dict[str, str]]] = manage_follow_up.send_timeout_reminders,
    deadline_at: str | None = None,
) -> dict[str, Any]:
    if timeout_seconds < 1 or interval_seconds < 1:
        raise SystemExit("timeout-seconds 和 interval-seconds 必须至少为 1。")
    wall_deadline = wall_deadline_epoch(deadline_at, timeout_seconds)
    deadline = time.monotonic() + max(0.0, wall_deadline - time.time())
    refreshes: list[dict[str, Any]] = []
    acknowledgements: list[dict[str, Any]] = []
    refresh_required = False
    timed_out = False
    post_reminder_window = False
    reminders: list[dict[str, str]] = []
    state = manage_follow_up.load_state(control_dir)
    previous_watch = state.get("reply_watch")
    if isinstance(previous_watch, dict) and isinstance(previous_watch.get("post_reminder_deadline_at"), str):
        wall_deadline = wall_deadline_epoch(previous_watch["post_reminder_deadline_at"], timeout_seconds)
        deadline = time.monotonic() + max(0.0, wall_deadline - time.time())
        post_reminder_window = True
    retry_attempt = 0
    manage_follow_up.append_audit(
        control_dir, "reply_watch_started", timeout_seconds=timeout_seconds, interval_seconds=interval_seconds,
    )
    manage_follow_up.update_reply_watch(control_dir, "Running")
    try:
        while True:
            try:
                current = poller(control_dir, profile)
            except RetryablePollError as error:
                retry_attempt += 1
                remaining = min(deadline - time.monotonic(), wall_deadline - time.time())
                if remaining <= 0:
                    timed_out = True
                    if not post_reminder_window:
                        reminders = reminder(control_dir, profile)
                        if reminders:
                            post_reminder_window = True
                            wall_deadline = wall_deadline_epoch(
                                manage_follow_up.start_post_reminder_window(control_dir, timeout_seconds), timeout_seconds,
                            )
                            deadline = time.monotonic() + max(0.0, wall_deadline - time.time())
                            retry_attempt = 0
                            continue
                    manage_follow_up.append_audit(
                        control_dir, "reply_watch_poll_retry_exhausted",
                        attempt=retry_attempt, error=str(error),
                    )
                    break
                # Keep retries bounded by the original watch window and avoid
                # retry storms while Feishu marks the error as transient.
                delay = min(remaining, interval_seconds, 2 ** min(retry_attempt - 1, 4))
                manage_follow_up.append_audit(
                    control_dir, "reply_watch_poll_retry",
                    attempt=retry_attempt, retry_delay_seconds=delay, error=str(error),
                )
                time.sleep(delay)
                continue
            retry_attempt = 0
            if current:
                acknowledgements.extend(current)
                if any(item.get("refresh_required") for item in current):
                    refresh_required = True
            remaining = min(deadline - time.monotonic(), wall_deadline - time.time())
            waiting = unanswered_items(control_dir)
            manage_follow_up.append_audit(
                control_dir, "reply_watch_polled", acknowledgements=len(current), unanswered=len(waiting),
            )
            # All dispatched items replied. There is no value in waiting for the
            # rest of the window before taking a fresh Base snapshot.
            if current and not waiting:
                break
            if remaining <= 0:
                timed_out = True
                if not post_reminder_window:
                    reminders = reminder(control_dir, profile)
                    if reminders:
                        post_reminder_window = True
                        wall_deadline = wall_deadline_epoch(
                            manage_follow_up.start_post_reminder_window(control_dir, timeout_seconds), timeout_seconds,
                        )
                        deadline = time.monotonic() + max(0.0, wall_deadline - time.time())
                        continue
                break
            time.sleep(min(interval_seconds, remaining))
        resolutions: list[dict[str, Any]] = []
        if refresh_required:
            refreshes.append(refresher(control_dir, base_url, snapshot_root, handoff_root, profile, download_workers))
            state = manage_follow_up.load_state(control_dir)
            if any(item.get("status") == "Confirmed Missing" for item in state["items"]):
                handoff_dir = refreshes[-1].get("handoff_dir")
                if not isinstance(handoff_dir, str):
                    raise SystemExit("监听刷新缺少受控交接目录；不能导出明确缺件。")
                resolutions.append(manage_follow_up.export_resolution(control_dir, Path(handoff_dir)))
        remaining_items = unanswered_items(control_dir)
        completion = manage_follow_up.record_watch_completion(
            control_dir,
            timed_out=timed_out,
            refreshes=len(refreshes),
            reminders=reminders,
            acknowledged_count=len(acknowledgements),
            unanswered_count=len(remaining_items),
        )
        result = {
            "action": "watch",
            "timeout_seconds": timeout_seconds,
            "acknowledgements": acknowledgements,
            "refreshes": refreshes,
            "unanswered_items": remaining_items,
            "timeout_reminders": reminders,
            "confirmed_missing_resolutions": resolutions,
            "next_action": completion["next_action"],
            "base_written": any(item.get("base_written") for item in refreshes),
        }
        manage_follow_up.append_audit(
            control_dir, "reply_watch_finished", acknowledgements=len(acknowledgements),
            unanswered=len(result["unanswered_items"]), refreshes=len(refreshes),
        )
        manage_follow_up.update_reply_watch(
            control_dir, "Finished", acknowledgements=len(acknowledgements),
            unanswered=len(result["unanswered_items"]), refreshes=len(refreshes),
        )
        return result
    except BaseException as error:
        manage_follow_up.append_audit(
            control_dir, "reply_watch_failed", error_type=type(error).__name__, error=str(error),
        )
        manage_follow_up.update_reply_watch(
            control_dir, "Failed", error_type=type(error).__name__, error=str(error),
        )
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Poll P2P material-follow-up replies as the authorized user; optional refresh remains read-only.")
    subparsers = parser.add_subparsers(dest="action", required=True)
    for name in ("poll", "poll-and-refresh", "watch"):
        command = subparsers.add_parser(name)
        command.add_argument("--control-dir", required=True, type=Path)
        command.add_argument("--lark-profile", required=True)
        if name in {"poll-and-refresh", "watch"}:
            command.add_argument("--base-url", required=True)
            command.add_argument("--snapshot-root", required=True, type=Path)
            command.add_argument("--handoff-root", required=True, type=Path)
            command.add_argument("--download-workers", type=int, default=8)
        if name == "watch":
            command.add_argument("--timeout-seconds", type=int, default=600)
            command.add_argument("--interval-seconds", type=int, default=15)
            command.add_argument("--deadline-at")
    args = parser.parse_args()
    control_dir = args.control_dir.resolve()
    if not control_dir.is_dir():
        raise SystemExit("control-dir 不存在。")
    if args.action == "watch":
        result = watch(
            control_dir, args.lark_profile, args.base_url, args.snapshot_root, args.handoff_root,
            args.download_workers, args.timeout_seconds, args.interval_seconds, deadline_at=args.deadline_at,
        )
    else:
        acknowledgements = poll(control_dir, args.lark_profile)
        result = {"action": args.action, "acknowledgements": acknowledgements, "base_written": False}
    if args.action == "poll-and-refresh" and any(item.get("refresh_required") for item in result["acknowledgements"]):
        result["refresh"] = refresh(control_dir, args.base_url, args.snapshot_root, args.handoff_root, args.lark_profile, args.download_workers)
    elif args.action != "watch":
        result["refresh"] = "Not Triggered"
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
