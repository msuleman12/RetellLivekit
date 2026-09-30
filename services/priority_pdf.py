"""The priority assessment PDF attached to the intake email, in the same layout
as the old ai-receptionist build:

  Page 1: Header, priority badge, case info, priority case overview
          (urgency banner, 3 I's columns, red flags, counsel recommendation)
  Page 2: Risk assessment, matched keywords, call summary
"""

from datetime import datetime
from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

W = 7.0 * inch  # usable page width

C_NAVY = colors.HexColor("#1e3a8a")
C_BLUE = colors.HexColor("#3b82f6")
C_GREEN = colors.HexColor("#10b981")
C_AMBER = colors.HexColor("#f59e0b")
C_RED = colors.HexColor("#ef4444")
C_SNOW = colors.HexColor("#f8fafc")
C_BORDER = colors.HexColor("#e2e8f0")
C_INK = colors.HexColor("#0f172a")
C_MUTED = colors.HexColor("#64748b")

_styles = getSampleStyleSheet()


def _ps(name: str, **kw: Any) -> ParagraphStyle:
    return ParagraphStyle(name, parent=kw.pop("parent", _styles["Normal"]), **kw)


BODY = _ps("body", fontSize=10, textColor=C_INK, fontName="Helvetica", leading=14, spaceAfter=0)
SMALL = _ps("small", fontSize=8, textColor=C_MUTED, fontName="Helvetica", leading=11, spaceAfter=0)
WHITE_H = _ps("wh", fontSize=10, textColor=colors.white, fontName="Helvetica-Bold", leading=13, spaceAfter=0)
SEC_TITLE = _ps("sec", fontSize=12, textColor=C_NAVY, fontName="Helvetica-Bold", leading=15, spaceAfter=0, spaceBefore=0)


def _sp(h: float) -> Spacer:
    return Spacer(1, h * inch)


def _status_colors(status: str) -> tuple[str, str]:
    """(background, text) for a 3 I's status label."""
    s = status.lower()
    if "strong" in s:
        return ("#ecfdf5", "#065f46")
    if "partial" in s or "moderate" in s:
        return ("#fffbeb", "#92400e")
    return ("#fef2f2", "#991b1b")


def _divider(title: str) -> Table:
    """Section header with a navy accent cap on the left and an underline."""
    cap_w = 0.22 * 72
    row = Table([[Paragraph("", SEC_TITLE), Paragraph(title, SEC_TITLE)]], colWidths=[cap_w, W - cap_w])
    row.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), C_NAVY),
        ("LINEBELOW", (1, 0), (1, -1), 1.5, C_NAVY),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
        ("RIGHTPADDING", (0, 0), (0, -1), 0),
        ("LEFTPADDING", (1, 0), (1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return row


def _header(timestamp: str) -> list[Any]:
    title_p = Paragraph("CASE PRIORITY ASSESSMENT", WHITE_H)
    sub_p = Paragraph(
        '<font size="8" color="#93c5fd">BBLG AI Intake System</font>',
        _ps("hdr_sub", parent=SMALL, textColor=colors.HexColor("#93c5fd"), fontSize=8, leading=11),
    )
    ts_p = Paragraph(
        escape(timestamp),
        _ps("ts", parent=SMALL, textColor=colors.HexColor("#93c5fd"), alignment=TA_RIGHT, fontSize=8),
    )
    left = Table([[title_p], [sub_p]], colWidths=[W * 0.62], style=TableStyle([
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    hdr = Table([[left, ts_p]], colWidths=[W * 0.62, W * 0.38])
    hdr.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), C_NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 16),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 16),
        ("LEFTPADDING", (0, 0), (0, -1), 20),
        ("RIGHTPADDING", (-1, 0), (-1, -1), 20),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
    ]))
    return [hdr, _sp(0.14)]


def _priority_badge(level: str, accent: colors.Color) -> list[Any]:
    label_style = _ps("badge_lbl", fontSize=20, textColor=colors.white, fontName="Helvetica-Bold",
                      leading=24, alignment=TA_CENTER, spaceAfter=0)
    sub_style = _ps("badge_sub", fontSize=8, textColor=colors.white, fontName="Helvetica",
                    leading=11, alignment=TA_CENTER, spaceAfter=0)
    sub_text = (
        "Immediate attorney assignment required" if level == "HIGH"
        else "Review within 24–48 hours" if level == "MEDIUM"
        else "Standard intake protocol"
    )
    badge = Table([
        [Paragraph(f"{escape(level)}  PRIORITY", label_style)],
        [Paragraph(sub_text, sub_style)],
    ], colWidths=[W])
    badge.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), accent),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, 0), 14),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 1), (-1, 1), 6),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 10),
        ("LINEABOVE", (0, 1), (-1, 1), 0.5, colors.HexColor("#ffffff40")),
    ]))
    return [badge, _sp(0.14)]


def _case_info(full_name: str, case_label: str, email: str, phone: str, language: str) -> list[Any]:
    icons = {
        "CLIENT NAME": "■",
        "CASE TYPE": "●",
        "EMAIL ADDRESS": "✉",
        "PHONE NUMBER": "☎",
        "LANGUAGE": "◆",
    }

    def cell(label: str, value: str) -> Paragraph:
        return Paragraph(
            f'<font size="7" color="#64748b"><b>{icons[label]}  {label}</b></font><br/>'
            f'<font size="10" color="#0f172a"><b>{escape(value)}</b></font>',
            BODY,
        )

    rows = [
        [cell("CLIENT NAME", full_name), cell("CASE TYPE", case_label)],
        [cell("EMAIL ADDRESS", email), cell("PHONE NUMBER", phone)],
        [cell("LANGUAGE", language), Paragraph("", BODY)],
    ]
    tbl = Table(rows, colWidths=[W / 2, W / 2])
    tbl.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1, C_BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, C_BORDER),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, C_SNOW]),
        ("LINEBEFORE", (0, 0), (0, -1), 4, C_NAVY),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return [tbl, _sp(0.16)]


def _urgency_banner(level: str, keyword_count: int, accent: colors.Color) -> list[Any]:
    urgency_text = (
        "IMMEDIATE ATTENTION REQUIRED" if level == "HIGH"
        else "PROMPT REVIEW RECOMMENDED" if level == "MEDIUM"
        else "STANDARD REVIEW"
    )
    urg_icon = "●" if level == "HIGH" else "◑" if level == "MEDIUM" else "○"
    urg_icon_color = "#ef4444" if level == "HIGH" else "#f59e0b" if level == "MEDIUM" else "#3b82f6"

    urg_val = _ps("urg_val", fontSize=13, textColor=colors.white, fontName="Helvetica-Bold", leading=16, spaceAfter=0)
    kw_num = _ps("kw_num", fontSize=26, textColor=colors.white, fontName="Helvetica-Bold",
                 leading=30, spaceAfter=0, alignment=TA_CENTER)
    kw_lbl = _ps("kw_lbl", fontSize=8, textColor=colors.HexColor("#bfdbfe"), fontName="Helvetica",
                 leading=10, spaceAfter=0, alignment=TA_CENTER)

    counter = Table(
        [[Paragraph(str(keyword_count), kw_num)], [Paragraph("KEYWORDS MATCHED", kw_lbl)]],
        colWidths=[W * 0.28],
        style=TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]),
    )
    banner = Table([[
        Paragraph(
            f'<font size="7" color="#94a3b8"><b>URGENCY STATUS</b></font><br/>'
            f'<font size="14" color="{urg_icon_color}">{urg_icon}</font>'
            f'<font size="13" color="white">  {urgency_text}</font>',
            urg_val,
        ),
        counter,
    ]], colWidths=[W * 0.72, W * 0.28])
    banner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), C_NAVY),
        ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#1e40af")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, 0), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 14),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
        ("LEFTPADDING", (0, 0), (0, 0), 20),
        ("RIGHTPADDING", (0, 0), (0, 0), 14),
        ("LEFTPADDING", (1, 0), (1, 0), 8),
        ("RIGHTPADDING", (1, 0), (1, 0), 8),
        ("LINEAFTER", (0, 0), (0, 0), 0.5, C_BLUE),
        ("LINEABOVE", (0, 0), (-1, 0), 3, accent),
    ]))
    return [banner, _sp(0.12)]


def _three_is(assessment: dict[str, Any]) -> list[Any]:
    col_w = W / 3
    col_lbl = _ps("col_lbl", fontSize=9, textColor=colors.white, fontName="Helvetica-Bold",
                  leading=11, spaceAfter=0, alignment=TA_CENTER)
    col_st = _ps("col_st", fontSize=10, fontName="Helvetica-Bold", leading=13, spaceAfter=0, alignment=TA_CENTER)
    col_body = _ps("col_body", fontSize=8.5, textColor=C_INK, fontName="Helvetica", leading=12, spaceAfter=0)

    def content(analysis: dict[str, Any]) -> str:
        lines = []
        for s in (analysis.get("strengths") or [])[:2]:
            lines.append(f'<font color="#059669">✔</font>  {escape(s[:75])}')
        for c in (analysis.get("concerns") or [])[:2]:
            lines.append(f'<font color="#d97706">⚠</font>  {escape(c[:75])}')
        if analysis.get("missing"):
            lines.append(f'<font color="#dc2626">✖</font>  Missing: {escape(analysis["missing"][0][:60])}')
        return "<br/>".join(lines) if lines else "Pending further review."

    dims = [assessment.get(k) or {} for k in ("liability", "insurance", "injuries")]
    status_cells, status_bgs = [], []
    for analysis in dims:
        status = analysis.get("status", "Unknown")
        bg_hex, txt_hex = _status_colors(status)
        status_cells.append(Paragraph(f'<font color="{txt_hex}"><b>{escape(status.upper())}</b></font>', col_st))
        status_bgs.append(bg_hex)

    rows = [
        [Paragraph("LIABILITY", col_lbl), Paragraph("INSURANCE", col_lbl), Paragraph("INJURIES / DAMAGES", col_lbl)],
        status_cells,
        [Paragraph(content(a), col_body) for a in dims],
    ]
    tbl = Table(rows, colWidths=[col_w] * 3)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), C_NAVY),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
        *[("BACKGROUND", (i, 1), (i, 1), colors.HexColor(bg)) for i, bg in enumerate(status_bgs)],
        ("ALIGN", (0, 1), (-1, 1), "CENTER"),
        ("TOPPADDING", (0, 1), (-1, 1), 7),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 7),
        ("BACKGROUND", (0, 2), (-1, 2), colors.white),
        ("VALIGN", (0, 2), (-1, 2), "TOP"),
        ("TOPPADDING", (0, 2), (-1, 2), 10),
        ("BOTTOMPADDING", (0, 2), (-1, 2), 10),
        ("LEFTPADDING", (0, 2), (-1, 2), 10),
        ("RIGHTPADDING", (0, 2), (-1, 2), 8),
        ("BOX", (0, 0), (-1, -1), 1, C_BORDER),
        ("LINEAFTER", (0, 0), (1, -1), 0.5, C_BORDER),
        ("LINEBELOW", (0, 1), (-1, 1), 0.5, C_BORDER),
    ]))
    return [tbl, _sp(0.12)]


def _red_flags(red_flags: list[str]) -> list[Any]:
    if not red_flags:
        return []
    rf_hdr = _ps("rf_hdr", fontSize=8, textColor=colors.HexColor("#7f1d1d"), fontName="Helvetica-Bold",
                 leading=10, spaceAfter=0)
    rf_val = _ps("rf_val", fontSize=9.5, textColor=colors.HexColor("#450a0a"), fontName="Helvetica",
                 leading=13, spaceAfter=0)
    rows = [[Paragraph("⚠  RED FLAGS DETECTED", rf_hdr)]]
    rows += [[Paragraph(f"•  {escape(flag)}", rf_val)] for flag in red_flags]

    style = [
        ("LINEBEFORE", (0, 0), (0, -1), 4, C_RED),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#fecaca")),
        ("LEFTPADDING", (0, 0), (-1, -1), 16),
        ("RIGHTPADDING", (0, 0), (-1, -1), 16),
        ("TOPPADDING", (0, 0), (0, 0), 9),
        ("BOTTOMPADDING", (0, 0), (0, 0), 9),
        ("TOPPADDING", (0, 1), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 7),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#fef2f2")),
    ]
    for idx in range(1, len(rows)):
        bg = colors.HexColor("#fff7f7") if (idx - 1) % 2 == 0 else colors.white
        style.append(("BACKGROUND", (0, idx), (-1, idx), bg))
        style.append(("LINEABOVE", (0, idx), (-1, idx), 0.5, colors.HexColor("#fecaca")))

    card = Table(rows, colWidths=[W])
    card.setStyle(TableStyle(style))
    return [card, _sp(0.12)]


def _recommendation(level: str) -> list[Any]:
    if level == "HIGH":
        rec = (
            "This matter warrants immediate assignment to a senior trial attorney for expedited evaluation. "
            "The severity of reported injuries and the liability indicators presented at intake reflect significant "
            "recovery potential. Evidence preservation measures and prompt client retention are strongly advised."
        )
    elif level == "MEDIUM":
        rec = (
            "We recommend attorney review within 24–48 hours to assess case viability and determine a retention "
            "strategy. Preliminary indicators suggest moderate recovery potential, subject to further investigation "
            "of liability, coverage, and damages."
        )
    else:
        rec = (
            "Standard intake protocol applies. Counsel should evaluate this matter at the earliest opportunity "
            "to determine whether sufficient grounds exist for representation and to advise the prospective client "
            "as to available legal remedies."
        )
    rec_val = _ps("rec_val", fontSize=9.5, textColor=C_NAVY, fontName="Helvetica", leading=14, spaceAfter=0)
    card = Table([[Paragraph(
        f'<font size="8" color="#1d4ed8"><b>COUNSEL RECOMMENDATION</b></font><br/>{rec}', rec_val,
    )]], colWidths=[W])
    card.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eff6ff")),
        ("LINEBEFORE", (0, 0), (0, -1), 4, C_BLUE),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#bfdbfe")),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("LEFTPADDING", (0, 0), (-1, -1), 16),
        ("RIGHTPADDING", (0, 0), (-1, -1), 16),
    ]))
    return [card, _sp(0.18)]


def _risk_assessment(reasoning: str) -> list[Any]:
    if not reasoning:
        return []
    val_s = _ps("ra_val", parent=BODY, fontSize=9.5, textColor=C_NAVY, leading=14)
    card = Table([[Paragraph(escape(reasoning), val_s)]], colWidths=[W])
    card.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f0f7ff")),
        ("LINEBEFORE", (0, 0), (0, -1), 3, C_BLUE),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#bfdbfe")),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("LEFTPADDING", (0, 0), (-1, -1), 16),
        ("RIGHTPADDING", (0, 0), (-1, -1), 16),
    ]))
    return [KeepTogether([_divider("RISK ASSESSMENT"), _sp(0.08), card]), _sp(0.16)]


def _keyword_card(label: str, kws: list[str], txt_color: str, bg_color: str, border_color: str) -> Table | None:
    if not kws:
        return None
    grp_lbl = _ps("grp_lbl", fontSize=7.5, textColor=colors.HexColor("#475569"), fontName="Helvetica-Bold",
                  leading=10, spaceAfter=3)
    pill_s = _ps("pill_s", parent=BODY, fontSize=8.5, leading=11, fontName="Helvetica-Bold",
                 textColor=colors.HexColor(txt_color))

    cols = 4
    padded = list(kws) + [""] * (-len(kws) % cols)
    pill_rows = [[Paragraph(escape(kw), pill_s) for kw in padded[i:i + cols]] for i in range(0, len(padded), cols)]

    header = Table([[Paragraph(label, grp_lbl)]], colWidths=[W])
    header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(border_color)),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
    ]))
    pills = Table(pill_rows, colWidths=[W / cols] * cols)
    pills.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(bg_color)),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(border_color)),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor(border_color)),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    wrapper = Table([[header], [pills]], colWidths=[W])
    wrapper.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(border_color)),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return wrapper


def _keywords(matched: list[str]) -> list[Any]:
    if not matched:
        return []
    injury_terms = ("injury", "injured", "hurt", "pain", "fracture", "broken", "brain", "head", "severe", "critical")
    liability_terms = ("fault", "negligence", "responsible", "liable", "report", "police", "witness")
    injury = [kw for kw in matched if any(t in kw.lower() for t in injury_terms)]
    liability = [kw for kw in matched if any(t in kw.lower() for t in liability_terms) and kw not in injury]
    other = [kw for kw in matched if kw not in injury and kw not in liability]

    cards = [c for c in (
        _keyword_card("INJURY-RELATED", injury, "#991b1b", "#fef2f2", "#fecaca"),
        _keyword_card("LIABILITY-RELATED", liability, "#1e40af", "#eff6ff", "#bfdbfe"),
        _keyword_card("OTHER INDICATORS", other, "#065f46", "#f0fdf4", "#bbf7d0"),
    ) if c is not None]

    # Keep the section title with the first keyword card.
    out: list[Any] = [KeepTogether([_divider("MATCHED PRIORITY KEYWORDS"), _sp(0.08), cards[0]])]
    for card in cards[1:]:
        out += [_sp(0.08), card]
    out.append(_sp(0.1))
    return out


def _call_summary(summary: str) -> list[Any]:
    if not summary:
        return []
    val_s = _ps("cs_val", parent=BODY, fontSize=9.5, leading=14, textColor=C_INK)
    conf_s = _ps("cs_conf", parent=BODY, fontSize=7, textColor=colors.HexColor("#94a3b8"),
                 fontName="Helvetica", leading=11, alignment=TA_CENTER)
    dashes = " ".join(["—"] * 30)
    conf_text = (
        '<para alignment="center"><font size="7" color="#94a3b8">'
        f"{dashes}<br/>"
        "<b>CONFIDENTIAL</b>  ·  For internal case evaluation only  ·  BBLG AI Intake System"
        "</font></para>"
    )
    card = Table([
        [Paragraph(escape(summary), val_s)],
        [Paragraph(conf_text, conf_s)],
    ], colWidths=[W])
    card.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), C_SNOW),
        ("BOX", (0, 0), (-1, -1), 0.5, C_BORDER),
        ("LINEABOVE", (0, 1), (-1, 1), 0.5, C_BORDER),
        ("TOPPADDING", (0, 0), (0, 0), 12),
        ("BOTTOMPADDING", (0, 0), (0, 0), 12),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 1), (-1, 1), 10),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 10),
        ("ALIGN", (0, 1), (-1, 1), "CENTER"),
    ]))
    # The section title always stays with at least the opening of the summary.
    return [KeepTogether([_divider("CALL SUMMARY"), _sp(0.08), card]), _sp(0.16)]


def _page_number(canv: Any, doc: Any) -> None:
    canv.saveState()
    canv.setFont("Helvetica", 7)
    canv.setFillColor(colors.HexColor("#94a3b8"))
    canv.drawRightString(letter[0] - 0.55 * inch, 0.28 * inch, f"Page {doc.page}")
    canv.restoreState()


def generate(
    *,
    case_label: str,
    full_name: str,
    email: str,
    phone: str,
    language: str,
    priority: dict[str, Any],
    assessment: dict[str, Any] | None,
    call_summary: str,
    call_time: datetime,
) -> bytes:
    level = priority.get("priority_level", "UNKNOWN")
    matched = priority.get("matched_keywords") or []
    accent = C_RED if level == "HIGH" else C_AMBER if level == "MEDIUM" else C_BLUE
    assessment = assessment or {}

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        topMargin=0.35 * inch, bottomMargin=0.45 * inch,
        leftMargin=0.55 * inch, rightMargin=0.55 * inch,
        title=f"Priority Assessment - {case_label}",
    )
    story: list[Any] = [
        # Page 1
        *_header(call_time.strftime("%B %d, %Y  —  %I:%M %p %Z")),
        *_priority_badge(level, accent),
        *_case_info(full_name or "Unknown Client", case_label, email or "N/A", phone or "N/A", language),
        _divider("PRIORITY CASE OVERVIEW"),
        _sp(0.08),
        *_urgency_banner(level, len(matched), accent),
        *_three_is(assessment),
        *_red_flags(assessment.get("red_flags") or []),
        *_recommendation(level),
        # Page 2
        *_risk_assessment(priority.get("reasoning", "")),
        *_keywords(matched),
        *_call_summary(call_summary),
    ]
    doc.build(story, onFirstPage=_page_number, onLaterPages=_page_number)
    return buf.getvalue()
