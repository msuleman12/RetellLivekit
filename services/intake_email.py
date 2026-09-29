"""The intake email to the firm (SendGrid), ported from the old ai-receptionist
build: the call summary, caller details and every extracted field, with the
priority PDF, the full record as JSON and the call recording attached."""

import base64
import html
import json
import logging
from datetime import datetime
from typing import Any

import aiohttp

from agents.user_data import CallData
from config import EMAIL, POST_CALL
from utils.dates import FIRM_TZ

logger = logging.getLogger("intake.email")

SENDGRID_URL = "https://api.sendgrid.com/v3/mail/send"

CASE_LABELS = {
    "accident": "Motor Vehicle Accident",
    "employment": "Employment",
    "premises": "Premises Liability",
    "harassment": "Sexual Harassment",
    "malpractice": "Medical Malpractice",
}

_CONTACT_FIELDS = (
    ("Name", "_full_name"),
    ("Callback number", "user_phone"),
    ("Callback number (unverified)", "user_phone_unverified"),
    ("Caller ID", "_caller_id"),
    ("Email", "user_email"),
    ("Preferred contact", "preferred_contact"),
    ("Best time to call", "best_contact_time"),
    ("Address", "user_address"),
    ("Date of birth", "user_dob"),
    ("City", "incident_city"),
)
_CONTACT_KEYS = {key for _, key in _CONTACT_FIELDS}
_PREFIXES = ("mm_", "sh_", "premises_", "employment_", "user_")
_ACRONYMS = {"Dob": "DOB", "Um": "UM", "Hr": "HR", "Fmla": "FMLA"}


def case_label(case_type: str) -> str:
    return CASE_LABELS.get(case_type, "General Inquiry")


def _label(key: str) -> str:
    for prefix in _PREFIXES:
        if key.startswith(prefix):
            key = key[len(prefix):]
            break
    return " ".join(_ACRONYMS.get(w.capitalize(), w.capitalize()) for w in key.split("_"))


def _value(v: Any) -> str:
    if isinstance(v, bool):
        return "Yes" if v else "No"
    return str(v)


def subject(data: CallData, caller_name: str, priority_level: str, is_test_call: bool) -> str:
    when = datetime.fromtimestamp(data.started_at, FIRM_TZ).strftime("%Y-%m-%d %I:%M %p %Z")
    prefix = "[TEST] " if is_test_call else ""
    if priority_level == "HIGH":
        prefix += "[HIGH PRIORITY] "
    elif priority_level in ("MEDIUM", "LOW"):
        prefix += f"[{priority_level}] "
    name = f" - {caller_name}" if caller_name else ""
    return f"{prefix}[Intake] {case_label(data.case_type)}{name} - {when}"


def _sections(data: CallData, custom: dict[str, Any], recording_url: str) -> list[tuple[str, list[tuple[str, str]]]]:
    values = dict(custom)
    values["_full_name"] = f"{custom.get('user_fname') or ''} {custom.get('user_lname') or ''}".strip()
    values["_caller_id"] = data.from_number
    contact = [(label, _value(values[k])) for label, k in _CONTACT_FIELDS if values.get(k) not in (None, "")]
    case = [
        (_label(k), _value(v))
        for k, v in custom.items()
        if k not in _CONTACT_KEYS and k not in ("user_fname", "user_lname") and v not in (None, "")
    ]
    call = [
        ("Call time", datetime.fromtimestamp(data.started_at, FIRM_TZ).strftime("%B %d, %Y at %I:%M %p %Z")),
        ("Duration", f"{data.duration_ms // 60000}m {data.duration_ms // 1000 % 60}s"),
        ("How the call ended", data.disconnect_reason.replace("_", " ") or "-"),
        ("Call ID", data.call_id),
    ]
    if data.transfer_requested:
        call.append(("Live transfer", data.transfer_outcome.replace("_", " ") or "requested"))
    if recording_url:
        call.append(("Recording", recording_url))
    return [("Caller", contact), ("Case details", case), ("Call", call)]


def bodies(
    data: CallData,
    custom: dict[str, Any],
    summary: str,
    priority: dict[str, Any],
    recording_url: str,
    is_test_call: bool,
) -> tuple[str, str]:
    level = priority.get("priority_level", "")
    sections = _sections(data, custom, recording_url)

    text = [f"LEGAL INTAKE - {case_label(data.case_type).upper()}"]
    if is_test_call:
        text.append("TEST CALL")
    if level:
        text.append(f"PRIORITY: {level} (details in the attached PDF)")
    text += ["", "CALL SUMMARY", summary or "No summary was generated."]
    for title, rows in sections:
        text += ["", title.upper()] + [f"{k}: {v}" for k, v in rows]

    esc = html.escape
    color = {"HIGH": "#b42318", "MEDIUM": "#b54708", "LOW": "#067647"}.get(level, "#6b7280")
    parts = [
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#1f2937;max-width:680px">',
        f'<h2 style="color:#1e2a44;margin:0 0 4px">Legal intake - {esc(case_label(data.case_type))}</h2>',
    ]
    if is_test_call:
        parts.append('<p style="margin:0 0 8px;color:#6b7280"><b>TEST CALL</b></p>')
    if level:
        parts.append(
            f'<p style="display:inline-block;background:{color};color:#fff;padding:4px 10px;'
            f'border-radius:4px;font-weight:bold;margin:4px 0 12px">{esc(level)} PRIORITY</p>'
        )
    parts.append(f'<h3 style="color:#1e2a44">Call summary</h3><p>{esc(summary or "No summary was generated.")}</p>')
    for title, rows in sections:
        if not rows:
            continue
        parts.append(f'<h3 style="color:#1e2a44">{esc(title)}</h3><table style="border-collapse:collapse;width:100%">')
        for k, v in rows:
            cell = f'<a href="{esc(v)}">Listen to the call</a>' if k == "Recording" else esc(v)
            parts.append(
                '<tr><td style="padding:4px 8px;border:1px solid #e5e7eb;background:#f9fafb;width:34%;'
                f'vertical-align:top">{esc(k)}</td><td style="padding:4px 8px;border:1px solid #e5e7eb">{cell}</td></tr>'
            )
        parts.append("</table>")
    parts.append("</div>")
    return "\n".join(text), "".join(parts)


async def send(
    *,
    data: CallData,
    custom: dict[str, Any],
    summary: str,
    priority: dict[str, Any],
    record: dict[str, Any],
    is_test_call: bool,
    pdf: bytes | None,
    recording: dict[str, Any] | None,
    recording_audio: bytes | None,
) -> int:
    """Returns SendGrid's HTTP status. Raises on failure."""
    caller_name = f"{custom.get('user_fname') or ''} {custom.get('user_lname') or ''}".strip()
    recording_url = (recording or {}).get("url") or ""
    text, html_body = bodies(data, custom, summary, priority, recording_url, is_test_call)

    stem = f"{data.case_type or 'intake'}_{data.call_id}"
    attachments = [(f"intake_{stem}.json", json.dumps(record, indent=2, ensure_ascii=False).encode(), "application/json")]
    if pdf:
        attachments.insert(0, (f"Priority_Assessment_{priority.get('priority_level', '')}_{stem}.pdf", pdf, "application/pdf"))
    if recording_audio:
        if len(recording_audio) <= EMAIL.max_recording_attachment_mb * 1024 * 1024:
            attachments.append((f"call_recording_{stem}.mp3", recording_audio, "audio/mpeg"))
        else:
            logger.info("recording is %d bytes, linked instead of attached", len(recording_audio))

    body = {
        "personalizations": [{"to": [{"email": e} for e in EMAIL.recipients_for(data.case_type)]}],
        "from": {"email": EMAIL.from_email, "name": "Bush & Bush Intake"},
        "subject": subject(data, caller_name, priority.get("priority_level", ""), is_test_call),
        "content": [{"type": "text/plain", "value": text}, {"type": "text/html", "value": html_body}],
        "attachments": [
            {"content": base64.b64encode(content).decode(), "filename": name, "type": mime, "disposition": "attachment"}
            for name, content, mime in attachments
        ],
    }
    headers = {"Authorization": f"Bearer {EMAIL.sendgrid_api_key}"}
    # Attachments can be several MB; allow longer than a plain webhook.
    timeout = aiohttp.ClientTimeout(total=max(POST_CALL.webhook_timeout_s, 30.0))
    async with aiohttp.ClientSession(timeout=timeout) as http:
        async with http.post(SENDGRID_URL, json=body, headers=headers) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"SendGrid returned {resp.status}: {(await resp.text())[:500]}")
            return resp.status
