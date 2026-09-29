"""The one-page-ish priority assessment PDF attached to the intake email:
priority badge, caller details, the 3 I's, red flags, matched keywords and the
call summary."""

from datetime import datetime
from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from xml.sax.saxutils import escape

NAVY = colors.HexColor("#1e2a44")
MUTED = colors.HexColor("#6b7280")
BORDER = colors.HexColor("#d9dee7")

PRIORITY_COLORS = {
    "HIGH": colors.HexColor("#b42318"),
    "MEDIUM": colors.HexColor("#b54708"),
    "LOW": colors.HexColor("#067647"),
}
STATUS_COLORS = {  # (background, text)
    "Strong": ("#ecfdf5", "#065f46"),
    "Partial": ("#fffbeb", "#92400e"),
    "Weak": ("#fef2f2", "#991b1b"),
    "Unknown": ("#f3f4f6", "#374151"),
}

_styles = getSampleStyleSheet()
TITLE = ParagraphStyle("title", parent=_styles["Title"], textColor=NAVY, fontSize=18, spaceAfter=2)
SUBTITLE = ParagraphStyle("subtitle", parent=_styles["Normal"], textColor=MUTED, alignment=TA_CENTER, fontSize=9)
HEADING = ParagraphStyle("heading", parent=_styles["Heading3"], textColor=NAVY, spaceBefore=12, spaceAfter=4)
BODY = ParagraphStyle("body", parent=_styles["Normal"], fontSize=9.5, leading=13)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8.5, leading=11)
BADGE = ParagraphStyle("badge", parent=BODY, fontSize=14, leading=18, textColor=colors.white, alignment=TA_CENTER)


def _p(text: Any, style: ParagraphStyle = BODY) -> Paragraph:
    return Paragraph(escape(str(text)), style)


def _bullet_markup(items: list[str], empty: str = "None noted") -> str:
    return "<br/>".join(f"&bull; {escape(i)}" for i in items) if items else escape(empty)


def _bullets(items: list[str], empty: str = "None noted") -> Paragraph:
    return Paragraph(_bullet_markup(items, empty), SMALL)


def generate(
    *,
    case_label: str,
    caller: dict[str, str],
    priority: dict[str, Any],
    assessment: dict[str, Any] | None,
    call_summary: str,
    call_time: datetime,
    is_test_call: bool,
) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER, leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch, title=f"Priority Assessment - {case_label}",
    )
    width = doc.width
    level = priority.get("priority_level", "UNKNOWN")
    story: list[Any] = [
        Paragraph("Bush &amp; Bush Law Group", TITLE),
        _p(f"Intake Priority Assessment  |  {call_time.strftime('%B %d, %Y at %I:%M %p %Z')}"
           + ("  |  TEST CALL" if is_test_call else ""), SUBTITLE),
        Spacer(1, 10),
    ]

    badge = Table([[_p(f"{level} PRIORITY  -  {case_label}", BADGE)]], colWidths=[width])
    badge.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PRIORITY_COLORS.get(level, MUTED)),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story += [badge, Spacer(1, 4), _p(priority.get("reasoning", ""), SMALL)]

    story.append(_p("Caller", HEADING))
    rows = [[_p(k, SMALL), _p(v or "-", BODY)] for k, v in caller.items()]
    info = Table(rows, colWidths=[1.6 * inch, width - 1.6 * inch])
    info.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f5f7fa")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(info)

    if assessment:
        story.append(_p("The 3 I's", HEADING))
        dims = [("Liability", "liability"), ("Insurance", "insurance"), ("Injuries / Damages", "injuries")]
        header = [Paragraph(f"<b>{t}</b>", BODY) for t, _ in dims]
        status = [_p(assessment[k]["status"], BODY) for _, k in dims]
        detail = [
            Paragraph(
                "<b>Strengths</b><br/>" + _bullet_markup(assessment[k]["strengths"])
                + "<br/><b>Concerns</b><br/>" + _bullet_markup(assessment[k]["concerns"])
                + "<br/><b>Missing</b><br/>" + _bullet_markup(assessment[k]["missing"]),
                SMALL,
            )
            for _, k in dims
        ]
        grid = Table([header, status, detail], colWidths=[width / 3] * 3)
        style = [
            ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f5f7fa")),
        ]
        for col, (_, k) in enumerate(dims):
            bg, fg = STATUS_COLORS.get(assessment[k]["status"], STATUS_COLORS["Unknown"])
            style.append(("BACKGROUND", (col, 1), (col, 1), colors.HexColor(bg)))
            style.append(("TEXTCOLOR", (col, 1), (col, 1), colors.HexColor(fg)))
        grid.setStyle(TableStyle(style))
        story.append(grid)

        story.append(_p("Red flags", HEADING))
        story.append(_bullets(assessment["red_flags"], "No red flags raised on the call."))

    story.append(_p("Matched keywords", HEADING))
    story.append(_p(", ".join(priority.get("matched_keywords") or []) or "None", SMALL))

    story.append(_p("Call summary", HEADING))
    story.append(_p(call_summary or "No summary was generated.", BODY))

    story += [
        Spacer(1, 16),
        _p("Automated keyword-based triage from the AI intake call. It is not a legal assessment "
           "of the matter and must be reviewed by an attorney.", SMALL),
    ]
    doc.build(story)
    return buf.getvalue()
