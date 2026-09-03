#!/usr/bin/env python3
"""Preview and confirmed user-identity Feishu delivery through lark-cli."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path


REQUIRED_OUTPUTS = (
    "materials.zip",
    "consolidated_candidate_pack.md",
    "candidate_pack.html",
    "source_manifest.md",
    "missing_evidence_list.md",
)
CEO_MESSAGE_FILE = "ceo_summary_message.md"
DELIVERY_RECEIPT_FILE = "delivery_receipt.json"
PROCESS_RECORDS_DIR = "过程记录"
PROCESS_RECORD_FILES = {
    "运行摘要.json", "材料映射.jsonl", "材料核验.json", "运行日志.jsonl", "校验和.sha256",
}
FEISHU_ATTACHMENTS = ("candidate_pack.html", "materials.zip")
SYSTEM_METADATA = {".DS_Store"}
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
REQUIRED_USER_SCOPES = {"im:message", "im:message.send_as_user", "im:resource"}
EMAIL_RECIPIENT_SCOPE = "contact:user:search"
REQUIRED_REVIEW_CLASSES = {
    "report-header",
    "batch-bar",
    "executive-summary",
    "metric-grid",
    "funnel",
    "approval",
    "decision-section",
    "table-wrap",
    "operations-note",
}
CEO_REVIEW_CLASSES = {
    "report-header", "batch-bar", "executive-summary", "metric-grid", "funnel",
    "material-limits", "operations-note",
}
FORBIDDEN_REVIEW_TAGS = {"a", "audio", "button", "dialog", "iframe", "script", "video"}
FORBIDDEN_REVIEW_ATTRIBUTES = {"aria-controls", "href", "src"}
INTERNAL_CODE_PATTERN = re.compile(r"\b(?:SK|SRC|BATCH)-[A-Z0-9]{2,}\b")
BATCH_ID_LINE_PATTERN = re.compile(r"^-\s*(?:Batch ID|batch_id):\s*(?:`([^`]+)`|(.+?))\s*$", re.IGNORECASE)
BATCH_ID_TABLE_PATTERN = re.compile(
    r"^\|\s*`?batch_id`?\s*\|\s*`?([^`|]+?)`?\s*\|\s*$", re.IGNORECASE
)
CEO_SUMMARY_STATUS_PATTERN = re.compile(
    r"^\|\s*状态\s*\|\s*`?([^`|]+?)`?\s*\|\s*$"
)


class ReviewHtmlInspector(HTMLParser):
    """Collect the limited structural signals required by the frozen review template."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: Counter[str] = Counter()
        self.classes: Counter[str] = Counter()
        self.forbidden: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.casefold()
        self.tags[normalized_tag] += 1
        if normalized_tag in FORBIDDEN_REVIEW_TAGS:
            self.forbidden.append(f"<{normalized_tag}>")
        for name, value in attrs:
            normalized_name = name.casefold()
            if normalized_name == "class" and value:
                self.classes.update(value.split())
            if normalized_name in FORBIDDEN_REVIEW_ATTRIBUTES or normalized_name.startswith("on"):
                self.forbidden.append(normalized_name)


def validate_review_html(path: Path) -> dict:
    """Reject an unrendered or out-of-contract executive review report."""
    html = path.read_text(encoding="utf-8")
    if "{{" in html or "}}" in html:
        raise DeliveryError("candidate_pack.html 含未替换的模板占位符；停止投递。")
    if INTERNAL_CODE_PATTERN.search(html):
        raise DeliveryError("candidate_pack.html 含不应展示的内部编号；停止投递。")

    inspector = ReviewHtmlInspector()
    try:
        inspector.feed(html)
        inspector.close()
    except Exception as error:
        raise DeliveryError("candidate_pack.html 无法完成静态 HTML 解析；停止投递。") from error

    if inspector.tags["html"] != 1 or inspector.tags["body"] != 1:
        raise DeliveryError("candidate_pack.html 缺少完整 HTML 文档结构；停止投递。")
    required_classes = set(CEO_REVIEW_CLASSES) if "HRD 候选人意见包" in html else set(REQUIRED_REVIEW_CLASSES)
    if inspector.classes["opinion-group"] or inspector.classes["opinion-card"]:
        required_classes.update({"opinion-group", "opinion-card"})
    missing_classes = sorted(name for name in required_classes if not inspector.classes[name])
    if missing_classes:
        raise DeliveryError(f"candidate_pack.html 缺少审阅页面区块：{', '.join(missing_classes)}；停止投递。")
    if inspector.classes["metric"] != 4 or inspector.classes["funnel-step"] != 4:
        raise DeliveryError("candidate_pack.html 的指标卡或流程漏斗数量不符合冻结模板；停止投递。")
    if inspector.forbidden:
        prohibited = ", ".join(sorted(set(inspector.forbidden)))
        raise DeliveryError(f"candidate_pack.html 含不允许的交互或外部资源：{prohibited}；停止投递。")
    return {
        "static_validation": "passed",
        "metric_cards": inspector.classes["metric"],
        "funnel_stages": inspector.classes["funnel-step"],
    }


class DeliveryError(RuntimeError):
    """A blocked or unsuccessful delivery with a safe user-facing reason."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_batch_id(output_dir: Path) -> str:
    pack = output_dir / "consolidated_candidate_pack.md"
    for line in pack.read_text(encoding="utf-8").splitlines():
        match = BATCH_ID_LINE_PATTERN.match(line)
        if match:
            return (match.group(1) or match.group(2)).strip()
        table_match = BATCH_ID_TABLE_PATTERN.match(line)
        if table_match:
            return table_match.group(1).strip()
    raise DeliveryError("无法从 consolidated_candidate_pack.md 读取 Batch ID。")


def is_formal_ceo_package(output_dir: Path) -> bool:
    pack = output_dir / "consolidated_candidate_pack.md"
    try:
        return pack.read_text(encoding="utf-8").startswith("# HRD Candidate Opinion Pack\n")
    except OSError as error:
        raise DeliveryError("无法读取 consolidated_candidate_pack.md；停止投递。") from error


def reviewed_summary(output_dir: Path, supplied: str) -> str:
    """Bind Base-to-CEO delivery text; retain manual review for flat Candidate Packs."""
    path = output_dir / CEO_MESSAGE_FILE
    if path.is_file():
        summary = path.read_text(encoding="utf-8").strip()
        if not summary:
            raise DeliveryError("已生成的 CEO 消息为空；未发出任何消息。")
        if supplied and supplied.strip() != summary:
            raise DeliveryError("待发送 CEO 消息与已生成交付物不一致；请重新预览并确认。")
        return summary
    if is_formal_ceo_package(output_dir):
        raise DeliveryError("缺少已生成的 CEO 消息；请先重新构建正式 CEO 包。")
    summary = supplied.strip()
    if not summary:
        raise DeliveryError("缺少已审阅的 CEO 摘要；未发出任何消息。")
    return summary


def ceo_summary_gate(output_dir: Path) -> str:
    evidence_list = output_dir / "missing_evidence_list.md"
    lines = evidence_list.read_text(encoding="utf-8").splitlines()
    try:
        section_start = lines.index("## CEO 摘要状态")
    except ValueError as error:
        raise DeliveryError("缺少 CEO 摘要状态；请使用当前插件重新生成交付物。") from error
    for line in lines[section_start + 1:]:
        if line.startswith("## "):
            break
        match = CEO_SUMMARY_STATUS_PATTERN.match(line)
        if match:
            status = match.group(1).strip()
            if status != "可提供":
                raise DeliveryError("材料问题尚未处理，暂不提供 CEO 摘要。")
            return status
    raise DeliveryError("CEO 摘要状态不完整；请使用当前插件重新生成交付物。")


def delivery_snapshot(
    output_dir: Path, summary: str, lark_profile: str, *, sender_open_id: str = "", recipient: dict | None = None,
) -> dict:
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise DeliveryError(f"输出目录必须是非符号链接目录：{output_dir}")
    entries = list(output_dir.iterdir())
    symlinks = sorted(path.name for path in entries if path.is_symlink())
    directories = sorted(path.name for path in entries if path.is_dir() and not path.is_symlink() and path.name != PROCESS_RECORDS_DIR)
    special = sorted(path.name for path in entries if not path.is_file() and not path.is_dir() and not path.is_symlink())
    if symlinks or directories or special:
        violations = (
            [f"符号链接 {name}" for name in symlinks]
            + [f"目录 {name}" for name in directories]
            + [f"特殊条目 {name}" for name in special]
        )
        raise DeliveryError("输出目录结构不符合正式投递边界：" + ", ".join(violations))
    process_dir = output_dir / PROCESS_RECORDS_DIR
    if process_dir.exists():
        if process_dir.is_symlink() or not process_dir.is_dir():
            raise DeliveryError("过程记录目录不是普通目录，停止投递。")
        process_entries = list(process_dir.iterdir())
        if any(item.is_symlink() or not item.is_file() for item in process_entries):
            raise DeliveryError("过程记录目录包含非普通文件，停止投递。")
        names = {item.name for item in process_entries}
        if names != PROCESS_RECORD_FILES:
            raise DeliveryError("过程记录目录文件清单不符合约定，停止投递。")
    all_files = {path.name for path in entries if path.is_file()}
    ignored_metadata = sorted(all_files & SYSTEM_METADATA)
    actual = all_files - SYSTEM_METADATA
    required_outputs = set(REQUIRED_OUTPUTS)
    if is_formal_ceo_package(output_dir):
        required_outputs.add(CEO_MESSAGE_FILE)
    expected = required_outputs | {"operation_log.jsonl", DELIVERY_RECEIPT_FILE}
    permitted = actual - {DELIVERY_RECEIPT_FILE}
    required = expected - {DELIVERY_RECEIPT_FILE}
    if permitted != required:
        missing = sorted(required - permitted)
        unexpected = sorted(permitted - required)
        raise DeliveryError(
            "输出目录必须包含当前包类型要求的正式交付物和 operation_log.jsonl，且只能读取当前运行目录；"
            f"缺失：{missing or '无'}；额外：{unexpected or '无'}。"
        )
    ceo_summary_gate(output_dir)
    html_validation = validate_review_html(output_dir / "candidate_pack.html")
    files = []
    for name in FEISHU_ATTACHMENTS:
        path = output_dir / name
        size = path.stat().st_size
        if size == 0:
            raise DeliveryError(f"交付物为空，停止发送：{name}")
        if size > MAX_UPLOAD_BYTES:
            raise DeliveryError(f"交付物超过飞书单文件 30 MB 限制，停止发送：{name}")
        files.append({"name": name, "bytes": size, "sha256": sha256(path)})
    summary_digest = hashlib.sha256(summary.encode("utf-8")).hexdigest()
    canonical = json.dumps(
        {
            "summary_sha256": summary_digest,
            "attachments": files,
            "lark_profile": lark_profile or "default",
            "sender_open_id": sender_open_id or "Not Verified",
            "recipient": recipient or {"status": "Not Resolved"},
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "output_dir": str(output_dir.resolve()),
        "batch_id": read_batch_id(output_dir),
        "attachments": files,
        "summary_sha256": summary_digest,
        "lark_profile": lark_profile or "default",
        "sender_open_id": sender_open_id or "Not Verified",
        "recipient": recipient or {"status": "Not Resolved"},
        "ignored_system_metadata": ignored_metadata,
        "html_validation": html_validation,
        "delivery_fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def parse_aliases() -> dict[str, str]:
    raw = os.environ.get("FEISHU_RECIPIENTS_JSON", "{}")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise DeliveryError("FEISHU_RECIPIENTS_JSON 不是有效 JSON。") from error
    if not isinstance(value, dict) or not all(isinstance(key, str) and isinstance(item, str) for key, item in value.items()):
        raise DeliveryError("FEISHU_RECIPIENTS_JSON 必须是 {\"显示名称\": \"open_id:ou_...\"} 形式。")
    return value


def recipient_descriptor(recipient: str) -> dict:
    aliases = parse_aliases()
    value = aliases.get(recipient.strip(), recipient.strip())
    for prefix, receive_id_type in (("open_id:", "open_id"), ("chat_id:", "chat_id")):
        if value.startswith(prefix) and value[len(prefix):].strip():
            return {"input": recipient, "receive_id_type": receive_id_type, "receive_id": value[len(prefix):].strip()}
    if value.startswith("email:") and value[len("email:"):].strip():
        return {"input": recipient, "receive_id_type": "email", "email": value[len("email:"):].strip()}
    if "@" in value and " " not in value:
        return {"input": recipient, "receive_id_type": "email", "email": value}
    raise DeliveryError(
        "收件人必须是 open_id:ou_...、chat_id:oc_...、email:姓名@域名，"
        "或在 FEISHU_RECIPIENTS_JSON 中配置的唯一显示名称；不会按姓名猜测或搜索收件人。"
    )


def cli_json(arguments: list[str], *, profile: str = "", cwd: Path | None = None) -> dict:
    environment = os.environ.copy()
    environment["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
    environment["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
    try:
        command = ["lark-cli"]
        if profile:
            command.extend(["--profile", profile])
        command.extend(arguments)
        result = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError as error:
        raise DeliveryError("未安装 lark-cli；请先完成飞书 CLI 用户身份配置。") from error
    except subprocess.TimeoutExpired as error:
        raise DeliveryError("lark-cli 请求超时；未继续投递。") from error
    output = result.stdout.strip() or result.stderr.strip()
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as error:
        raise DeliveryError(f"lark-cli 返回无法解析：{output[:500] or '无输出'}") from error
    if result.returncode != 0 or payload.get("ok") is False:
        error = payload.get("error", {})
        detail = error.get("message") or output[:500]
        raise DeliveryError(f"lark-cli 请求失败：{detail}")
    return payload


def required_scopes(descriptor: dict | None = None) -> set[str]:
    scopes = set(REQUIRED_USER_SCOPES)
    if descriptor and descriptor["receive_id_type"] == "email":
        scopes.add(EMAIL_RECIPIENT_SCOPE)
    return scopes


def user_cli_ready(profile: str, descriptor: dict | None = None) -> dict:
    status = cli_json(["auth", "status", "--json", "--verify"], profile=profile)
    user = status.get("identities", {}).get("user", {})
    scopes = set(str(user.get("scope", "")).split())
    missing = sorted(required_scopes(descriptor) - scopes)
    if user.get("status") != "ready" or not user.get("verified") or missing:
        raise DeliveryError(
            "飞书 CLI 用户身份未就绪；缺少权限："
            f"{', '.join(missing) if missing else '无'}。请先执行用户身份授权。"
        )
    return user


def verified_sender_open_id(user: dict) -> str:
    value = user.get("openId", user.get("open_id"))
    if not isinstance(value, str) or not value.startswith("ou_"):
        raise DeliveryError("当前用户身份未返回可验证 open_id；不预览、不发送。")
    return value


def resolve_recipient(descriptor: dict, profile: str) -> tuple[str, str]:
    if descriptor["receive_id_type"] != "email":
        return descriptor["receive_id_type"], descriptor["receive_id"]
    response = cli_json(
        [
            "contact",
            "+search-user",
            "--query",
            descriptor["email"],
            "--as",
            "user",
            "--format",
            "json",
        ],
        profile=profile,
    )
    users = response.get("data", {}).get("users", [])
    exact = [
        user
        for user in users
        if descriptor["email"].casefold() in {
            str(user.get("email", "")).casefold(),
            str(user.get("enterprise_email", "")).casefold(),
        }
        and user.get("open_id")
    ]
    if len(exact) != 1:
        raise DeliveryError(f"邮箱未解析为唯一飞书用户：{descriptor['email']}；未发出任何消息。")
    return "open_id", exact[0]["open_id"]


def cli_message_arguments(receive_id_type: str, receive_id: str, content_flag: str, content: str, idempotency_key: str, dry_run: bool) -> list[str]:
    target_flag = "--chat-id" if receive_id_type == "chat_id" else "--user-id"
    arguments = [
        "im",
        "+messages-send",
        "--as",
        "user",
        target_flag,
        receive_id,
        content_flag,
        content,
        "--idempotency-key",
        idempotency_key,
        "--format",
        "json",
    ]
    if dry_run:
        arguments.append("--dry-run")
    return arguments


def message_id(response: dict, dry_run: bool) -> str:
    if dry_run:
        return "dry-run"
    value = response.get("data", {}).get("message_id")
    if not isinstance(value, str) or not value:
        raise DeliveryError("lark-cli 发送响应缺少 message_id；停止继续投递。")
    return value


def receipt_binding(snapshot: dict) -> dict:
    return {
        "batch_id": snapshot["batch_id"],
        "delivery_fingerprint": snapshot["delivery_fingerprint"],
        "sender_open_id": snapshot["sender_open_id"],
        "recipient": snapshot["recipient"],
        "summary_sha256": snapshot["summary_sha256"],
        "attachments": snapshot["attachments"],
    }


def load_receipt(output_dir: Path, snapshot: dict) -> dict:
    path = output_dir / DELIVERY_RECEIPT_FILE
    binding = receipt_binding(snapshot)
    if not path.exists():
        return {"schema_version": 1, **binding, "components": {}, "status": "Partial"}
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise DeliveryError("投递回执不是有效 JSON；为避免重复投递而停止。") from error
    if (
        receipt.get("schema_version") != 1
        or not isinstance(receipt.get("components"), dict)
        or any(receipt.get(key) != value for key, value in binding.items())
    ):
        raise DeliveryError("现有投递回执不属于当前已确认正文、附件、发送者或收件人；为避免混合投递而停止。")
    return receipt


def persist_receipt(output_dir: Path, receipt: dict, dry_run: bool) -> None:
    if dry_run:
        return
    receipt["updated_at"] = datetime.now(timezone.utc).isoformat()
    path = output_dir / DELIVERY_RECEIPT_FILE
    temporary = output_dir / f".{DELIVERY_RECEIPT_FILE}.tmp"
    temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def preview(arguments: argparse.Namespace) -> int:
    require_delivery_arguments(arguments)
    summary = reviewed_summary(Path(arguments.output_dir), arguments.summary)
    descriptor = recipient_descriptor(arguments.recipient)
    user = user_cli_ready(arguments.lark_profile, descriptor)
    receive_id_type, receive_id = resolve_recipient(descriptor, arguments.lark_profile)
    resolved_recipient = {"receive_id_type": receive_id_type, "receive_id": receive_id}
    snapshot = delivery_snapshot(
        Path(arguments.output_dir), summary, arguments.lark_profile,
        sender_open_id=verified_sender_open_id(user), recipient=resolved_recipient,
    )
    receipt = load_receipt(Path(arguments.output_dir), snapshot)
    print(json.dumps({
        "action": "preview_only", "recipient": resolved_recipient, "message": summary, **snapshot,
        "already_sent_components": sorted(receipt["components"]),
    }, ensure_ascii=False, indent=2))
    return 0


def preflight(arguments: argparse.Namespace) -> int:
    descriptor = recipient_descriptor(arguments.recipient) if arguments.recipient else None
    user = user_cli_ready(arguments.lark_profile, descriptor)
    print(
        json.dumps(
            {
                "action": "preflight",
                "ready": True,
                "identity": "user",
                "profile": arguments.lark_profile or "default",
                "sender": {
                    "name": user.get("userName", "Unknown"),
                    "open_id": user.get("openId", "Unknown"),
                },
                "recipient_type": descriptor["receive_id_type"] if descriptor else "not_checked",
                "required_scopes": sorted(required_scopes(descriptor)),
                "note": "未解析收件人、未上传文件、未发送消息。",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def require_delivery_arguments(arguments: argparse.Namespace) -> None:
    if not arguments.output_dir:
        raise DeliveryError("preview 或 send 必须提供 --output-dir。")
    if not arguments.recipient:
        raise DeliveryError("preview 或 send 必须提供明确的 --recipient。")


def send(arguments: argparse.Namespace) -> int:
    require_delivery_arguments(arguments)
    summary = reviewed_summary(Path(arguments.output_dir), arguments.summary)
    if arguments.confirm != "SEND":
        raise DeliveryError("未收到精确确认 `SEND`；未发出任何消息。")
    descriptor = recipient_descriptor(arguments.recipient)
    user = user_cli_ready(arguments.lark_profile, descriptor)
    receive_id_type, receive_id = resolve_recipient(descriptor, arguments.lark_profile)
    resolved_recipient = {"receive_id_type": receive_id_type, "receive_id": receive_id}
    snapshot = delivery_snapshot(
        Path(arguments.output_dir), summary, arguments.lark_profile,
        sender_open_id=verified_sender_open_id(user), recipient=resolved_recipient,
    )
    if arguments.batch_id != snapshot["batch_id"]:
        raise DeliveryError("确认的 Batch ID 与当前交付物不一致；未发出任何消息。")
    if arguments.delivery_fingerprint != snapshot["delivery_fingerprint"]:
        raise DeliveryError("投递对象、已验证发送者、正文或附件在确认后发生变化；请重新预览并确认。")
    receipt = load_receipt(Path(arguments.output_dir), snapshot)
    sent = []
    components = receipt["components"]
    already_sent = sorted(components)
    try:
        if "message" not in components:
            summary_key = f"{snapshot['batch_id']}-{snapshot['delivery_fingerprint'][:28]}-text"
            summary_response = cli_json(
                cli_message_arguments(receive_id_type, receive_id, "--text", summary, summary_key, arguments.dry_run),
                profile=arguments.lark_profile, cwd=Path(arguments.output_dir),
            )
            component = {"kind": "message", "message_id": message_id(summary_response, arguments.dry_run), "identity": "user"}
            sent.append(component)
            components["message"] = component
            persist_receipt(Path(arguments.output_dir), receipt, arguments.dry_run)
        for item in snapshot["attachments"]:
            if item["name"] in components:
                continue
            file_key = f"{snapshot['batch_id']}-{snapshot['delivery_fingerprint'][:20]}-{item['name'][:12]}"
            file_response = cli_json(
                cli_message_arguments(receive_id_type, receive_id, "--file", f"./{item['name']}", file_key, arguments.dry_run),
                profile=arguments.lark_profile,
                cwd=Path(arguments.output_dir),
            )
            component = {"kind": "file", "name": item["name"], "message_id": message_id(file_response, arguments.dry_run), "sha256": item["sha256"], "identity": "user"}
            sent.append(component)
            components[item["name"]] = component
            persist_receipt(Path(arguments.output_dir), receipt, arguments.dry_run)
    except DeliveryError as error:
        persist_receipt(Path(arguments.output_dir), receipt, arguments.dry_run)
        raise DeliveryError(f"投递部分完成；已发送：{json.dumps(sent, ensure_ascii=False)}；停止继续发送。原因：{error}") from error
    action = "dry_run" if arguments.dry_run else "sent"
    receipt["status"] = "Dry Run" if arguments.dry_run else "Sent"
    persist_receipt(Path(arguments.output_dir), receipt, arguments.dry_run)
    print(json.dumps({"action": action, "identity": "user", "recipient": resolved_recipient, "batch_id": snapshot["batch_id"], "delivery_fingerprint": snapshot["delivery_fingerprint"], "messages": sent, "already_sent": already_sent}, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight, preview, or confirmed user-identity Feishu delivery for CEO message, Candidate Pack HTML, and source ZIP.")
    parser.add_argument("action", choices=("preflight", "preview", "send"))
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--recipient", default="", help="open_id:..., chat_id:..., email:..., or configured alias")
    parser.add_argument("--lark-profile", default="", help="Optional local lark-cli profile. The default profile is used when omitted.")
    parser.add_argument("--summary", default="", help="Optional read-back of ceo_summary_message.md; a different value is rejected.")
    parser.add_argument("--confirm", default="", help="Must be exactly SEND for external delivery.")
    parser.add_argument("--batch-id", default="", help="Batch ID returned by preview.")
    parser.add_argument("--delivery-fingerprint", default="", help="Fingerprint returned by preview.")
    parser.add_argument("--dry-run", action="store_true", help="Validate user-identity CLI sends without transmitting content or files.")
    arguments = parser.parse_args()
    try:
        if arguments.action == "preflight":
            return preflight(arguments)
        return preview(arguments) if arguments.action == "preview" else send(arguments)
    except DeliveryError as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
