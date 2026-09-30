"""The intake email to the firm (SendGrid), in the same template as the old
ai-receptionist build: header badges, priority note, call summary, client
contact information, case details and call metadata, with the priority PDF,
the full record as JSON and the call recording attached."""

import base64
import html
import json
import logging
from datetime import datetime
from typing import Any

import aiohttp

from agents.user_data import CallData
from config import ELEVENLABS, EMAIL, POST_CALL
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
    ("Email", "user_email"),
    ("Phone", "_phone"),
    ("Language", "_language"),
    ("Preferred Contact", "preferred_contact"),
    ("Address", "user_address"),
    ("Date of Birth", "user_dob"),
    ("Inbound phone number", "_caller_id"),
    ("Test Call", "_test_call"),
)
# Shown under contact information, so left out of the case details.
_CONTACT_KEYS = {"user_fname", "user_lname", "user_email", "user_phone", "user_phone_unverified",
                 "preferred_contact", "user_address", "user_dob"}
_PREFIXES = ("mm_", "sh_", "premises_", "employment_", "user_")
_ACRONYMS = {"Dob": "DOB", "Um": "UM", "Hr": "HR", "Fmla": "FMLA"}

_PRIORITY_BADGES = {
    "HIGH": '<div class="priority-badge-high">🔴 HIGH PRIORITY - See PDF</div>',
    "MEDIUM": '<div class="priority-badge-medium">🟠 MEDIUM PRIORITY - See PDF</div>',
    "LOW": '<div class="priority-badge-low">🔵 LOW PRIORITY - See PDF</div>',
}
# (accent, background) of the priority note under the header.
_PRIORITY_SECTION_COLORS = {
    "HIGH": ("#e74c3c", "#fff5f5"),
    "MEDIUM": ("#f39c12", "#fffbf0"),
    "LOW": ("#3498db", "#f0f8ff"),
}

_RULE = "───────────────────────────────────────────────────────────────"
_DOUBLE_RULE = "═══════════════════════════════════════════════════════════════"

_CSS = """
          body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            color: #2c3e50;
            line-height: 1.6;
            max-width: 1000px;
            margin: 0 auto;
            padding: 20px;
            background-color: #e8ecf1;
          }
          .container {
            background-color: #f8f9fb;
            border-radius: 12px;
            box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
            overflow: hidden;
            border: 1px solid #e1e8ed;
          }
          .header {
            background-color: #34495e;
            color: #ffffff;
            padding: 28px 30px;
            border-bottom: 3px solid #2c3e50;
          }
          .header h1 {
            margin: 0 0 8px 0;
            font-size: 24px;
            font-weight: 600;
            letter-spacing: 0.5px;
          }
          .agent-badge {
            display: inline-block;
            background-color: #3498db;
            color: #ffffff;
            padding: 6px 16px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 600;
            letter-spacing: 0.3px;
            margin-top: 4px;
          }
          .test-call-badge {
            display: inline-block;
            background-color: #f39c12;
            color: #ffffff;
            padding: 6px 16px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 600;
            letter-spacing: 0.3px;
            margin-top: 4px;
            margin-left: 8px;
          }
          .priority-badge-high {
            display: inline-block;
            background-color: #e74c3c;
            color: #ffffff;
            padding: 6px 16px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 600;
            letter-spacing: 0.3px;
            margin-top: 4px;
            margin-left: 8px;
          }
          .language-badge {
            display: inline-block;
            background-color: #9b59b6;
            color: #ffffff;
            padding: 6px 16px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 600;
            letter-spacing: 0.3px;
            margin-top: 4px;
            margin-left: 8px;
          }
          .priority-badge-medium {
            display: inline-block;
            background-color: #f39c12;
            color: #ffffff;
            padding: 6px 16px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 600;
            letter-spacing: 0.3px;
            margin-top: 4px;
            margin-left: 8px;
          }
          .priority-badge-low {
            display: inline-block;
            background-color: #3498db;
            color: #ffffff;
            padding: 6px 16px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 600;
            letter-spacing: 0.3px;
            margin-top: 4px;
            margin-left: 8px;
          }
          .priority-section {
            background-color: #fff5f5;
            border-left: 4px solid #e74c3c;
            padding: 20px 24px;
            margin: 24px 30px;
            border-radius: 6px;
          }
          .priority-title {
            font-size: 13px;
            font-weight: 600;
            color: #c0392b;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin: 0 0 12px 0;
          }
          .call-summary {
            background-color: #ebf5fb;
            border-left: 4px solid #3498db;
            padding: 20px 24px;
            margin: 24px 30px;
            border-radius: 6px;
          }
          .call-summary-title {
            font-size: 13px;
            font-weight: 600;
            color: #1a5490;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin: 0 0 12px 0;
          }
          .call-summary-content {
            margin: 0;
            font-size: 15px;
            color: #2c3e50;
            line-height: 1.7;
          }
          .call-summary-content strong {
            color: #1a5490;
            font-weight: 600;
          }
          .call-summary-timestamp {
            margin: 8px 0 0 0;
            font-size: 13px;
            color: #34495e;
          }
          .content {
            padding: 30px;
          }
          h2 {
            color: #2c3e50;
            font-size: 16px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-top: 30px;
            margin-bottom: 15px;
            padding-bottom: 8px;
            border-bottom: 2px solid #3498db;
          }
          h2:first-of-type {
            margin-top: 0;
          }
          table {
            border-collapse: collapse;
            width: 100%;
            border: 1px solid #dfe6e9;
            margin-bottom: 25px;
            font-size: 14px;
            font-family: Arial, Helvetica, sans-serif;
          }
          th, td {
            padding: 12px 16px;
            vertical-align: top;
            text-align: left;
            border-bottom: 1px solid #ecf0f1;
          }
          th {
            background-color: #d6eaf8;
            color: #1a5490;
            font-weight: 600;
            width: 280px;
            font-size: 13px;
          }
          td {
            background-color: #ffffff;
            color: #34495e;
            font-size: 14px;
          }
          tr:last-child th,
          tr:last-child td {
            border-bottom: none;
          }
          tr:hover td {
            background-color: #f8f9fa;
          }
          .highlight-row {
            background-color: #ebf5fb !important;
          }
          .highlight-row td {
            font-weight: 600;
            color: #1a5490;
          }
          .metadata-section {
            background-color: #fafbfc;
            padding: 24px;
            margin: 30px -30px -30px -30px;
            border-top: 1px solid #e5e7eb;
          }
          .metadata-section h2 {
            color: #6b7280;
            font-size: 12px;
            margin-top: 0;
            margin-bottom: 12px;
            border-bottom: none;
            padding-bottom: 0;
          }
          .metadata-section table {
            background-color: transparent;
            border: none;
          }
          .metadata-section th {
            background-color: transparent;
            color: #9ca3af;
            font-size: 12px;
            width: 180px;
          }
          .metadata-section td {
            background-color: transparent;
            color: #6b7280;
            font-size: 13px;
            font-family: 'Courier New', monospace;
          }
          .metadata-section tr {
            border-bottom: 1px solid #e5e7eb;
          }
          .metadata-section tr:last-child {
            border-bottom: none;
          }
          .metadata-section tr:hover td {
            background-color: transparent;
          }
          @media only screen and (max-width: 640px) {
            body {
              padding: 10px;
            }
            .header {
              padding: 20px;
            }
            .content {
              padding: 20px;
            }
            .call-summary {
              margin: 20px 20px;
              padding: 16px 18px;
            }
            .priority-section {
              margin: 20px 20px;
              padding: 16px 18px;
            }
            th {
              width: 140px;
              font-size: 12px;
              padding: 10px;
            }
            td {
              font-size: 13px;
              padding: 10px;
            }
            .metadata-section {
              padding: 20px;
              margin: 20px -20px -20px -20px;
            }
          }
"""


def case_label(case_type: str) -> str:
    return CASE_LABELS.get(case_type, "General Inquiry")


def language_display(code: str = ELEVENLABS.language) -> str:
    return {"en": "English", "es": "Spanish"}.get(code.split("-")[0].lower(), code)


def caller_phone(custom: dict[str, Any]) -> str:
    if custom.get("user_phone"):
        return str(custom["user_phone"])
    if custom.get("user_phone_unverified"):
        return f"{custom['user_phone_unverified']} (unverified)"
    return ""


def _label(key: str) -> str:
    for prefix in _PREFIXES:
        if key.startswith(prefix):
            key = key[len(prefix):]
            break
    return " ".join(_ACRONYMS.get(w.capitalize(), w.capitalize()) for w in key.split("_"))


def _value(v: Any) -> str:
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, default=str)
    return str(v)


def _empty(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def subject(data: CallData, caller_name: str, priority_level: str, is_test_call: bool) -> str:
    when = datetime.fromtimestamp(data.started_at, FIRM_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    prefix = "[TEST] " if is_test_call else ""
    if priority_level == "HIGH":
        prefix += "[HIGH PRIORITY] "
    elif priority_level in ("MEDIUM", "LOW"):
        prefix += f"[{priority_level}] "
    if caller_name:
        return f"{prefix}[Intake] {case_label(data.case_type)} — {caller_name} — {when}"
    return f"{prefix}[Intake] {case_label(data.case_type)} — {when}"


def _rows(data: CallData, custom: dict[str, Any], recording_url: str, is_test_call: bool):
    values = dict(custom)
    values["_full_name"] = f"{custom.get('user_fname') or ''} {custom.get('user_lname') or ''}".strip()
    values["_phone"] = caller_phone(custom)
    values["_language"] = language_display()
    values["_caller_id"] = data.from_number
    values["_test_call"] = "Yes (Test Call)" if is_test_call else "No"
    contact = [(label, _value(values[k])) for label, k in _CONTACT_FIELDS if not _empty(values.get(k))]
    case = [(_label(k), _value(v)) for k, v in custom.items() if k not in _CONTACT_KEYS and not _empty(v)]

    metadata = [
        ("Agent Name", data.agent_name or data.case_type or "—"),
        ("Room Identifier", data.room_name or "—"),
        ("Call ID", data.call_id or "—"),
        ("Duration", f"{data.duration_ms // 60000}m {data.duration_ms // 1000 % 60}s"),
        ("Disconnection Reason", data.disconnect_reason.replace("_", " ") or "—"),
    ]
    if data.transfer_requested:
        metadata.append(("Live Transfer", data.transfer_outcome.replace("_", " ") or "requested"))
    if recording_url:
        metadata.append(("Recording", recording_url))
    return contact, case, metadata


def bodies(
    data: CallData,
    custom: dict[str, Any],
    summary: str,
    priority: dict[str, Any],
    recording_url: str,
    is_test_call: bool,
) -> tuple[str, str]:
    level = priority.get("priority_level", "")
    level = level if level in _PRIORITY_BADGES else ""
    label = case_label(data.case_type)
    full_name = f"{custom.get('user_fname') or ''} {custom.get('user_lname') or ''}".strip()
    call_time = datetime.fromtimestamp(data.started_at, FIRM_TZ).strftime("%B %d, %Y at %I:%M %p %Z")
    contact, case, metadata = _rows(data, custom, recording_url, is_test_call)

    # Plain text
    text = [_DOUBLE_RULE, "LEGAL INTAKE REPORT", _DOUBLE_RULE]
    if level:
        text += ["", f"⚠️  PRIORITY: {level}", "(See attached PDF for detailed priority assessment)"]
    text += ["", "CALL SUMMARY", _RULE]
    text.append(summary or f"{full_name or 'Client'} contacted regarding a {label} matter.")
    if summary:
        text.append("")
    text.append(f"Call Time: {call_time}")
    for title, rows in (
        ("CLIENT CONTACT INFORMATION", contact),
        ("CASE DETAILS AND INCIDENT INFORMATION", [("Priority Level", level or "—")] + case),
        ("CALL METADATA", metadata),
    ):
        text += ["", _RULE, title, _RULE] + [f"{k}: {v}" for k, v in rows]

    # HTML
    esc = html.escape

    def table_rows(rows: list[tuple[str, str]]) -> str:
        out = []
        for k, v in rows:
            if k == "Recording":
                cell = f'<a href="{esc(v)}">Listen to the call</a>'
            elif k == "Name":
                cell = f"<strong>{esc(v)}</strong>"
            else:
                cell = esc(v)
            out.append(f"<tr><th>{esc(k)}</th><td>{cell}</td></tr>")
        return "".join(out)

    badges = [
        f'<div class="agent-badge">{esc(label).upper()}</div>',
        f'<div class="language-badge">{esc(language_display())}</div>',
    ]
    if is_test_call:
        badges.append('<div class="test-call-badge">🧪 TEST CALL</div>')
    if level:
        badges.append(_PRIORITY_BADGES[level])

    priority_section = ""
    if level:
        accent, bg = _PRIORITY_SECTION_COLORS[level]
        priority_section = f"""
          <div class="priority-section" style="background-color: {bg}; border-left: 4px solid {accent}; padding: 20px 24px; margin: 24px 30px;">
            <p class="priority-title">CASE PRIORITY: {esc(level)}</p>
            <p style="margin: 0; font-size: 14px; color: #2c3e50;">
              📄 <strong>See attached PDF for detailed priority assessment</strong> including score, matched keywords,
              reasoning, and the 3 I's analysis (Liability, Insurance, Injuries).
            </p>
          </div>"""

    if summary:
        summary_html = "<br>".join(esc(line) for line in summary.split("\n") if line.strip())
    else:
        summary_html = (f"<strong>{esc(full_name or 'Client')}</strong> contacted regarding a "
                        f"<strong>{esc(label)}</strong> matter.")

    # Inline styles on the priority row for email clients that drop <style>.
    priority_row = (
        '<tr><th style="background-color: #d6eaf8; color: #1a5490; padding: 12px 16px;">Priority Level</th>'
        f'<td style="background-color: #ffffff; padding: 12px 16px;">{esc(level or "—")}</td></tr>'
    )

    html_body = f"""
    <html>
      <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <style>{_CSS}        </style>
      </head>
      <body>
        <div class="container">
          <div class="header">
            <h1>Legal Intake Report</h1>
            {"".join(badges)}
          </div>
          {priority_section}
          <div class="call-summary" style="background-color: #ebf5fb; border-left: 4px solid #3498db; padding: 20px 24px;">
            <p class="call-summary-title">Call Summary</p>
            <p class="call-summary-content">{summary_html}</p>
            <p class="call-summary-timestamp">📞 {esc(call_time)}</p>
          </div>

          <div class="content">
            <h2>Client Contact Information</h2>
            <table>{table_rows(contact)}</table>

            <h2>Case Details and Incident Information</h2>
            <table>{priority_row}{table_rows(case)}</table>

            <div class="metadata-section">
              <h2>Call Metadata</h2>
              <table>{table_rows(metadata)}</table>
            </div>
          </div>
        </div>
      </body>
    </html>
    """.strip()
    return "\n".join(text), html_body


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
    attachments = [(f"processed_payload_{stem}.json", json.dumps(record, indent=2, ensure_ascii=False).encode(), "application/json")]
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
