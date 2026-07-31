"""
Builds the requirements document as a PDF, from the same backlog data as the
markdown and the .docx.

A PDF as well as the Word file because this is the copy the doctors are likely
to be sent, and it renders identically everywhere.
"""

import json
from datetime import date
from html import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, KeepTogether, PageBreak, PageTemplate, Paragraph,
    Spacer, Table, TableStyle,
)

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

OUT = str(HERE.parent.parent / "docs" / "Requirements-and-Backlog.pdf")

from export import backlog_json  # noqa: E402

data = json.loads(backlog_json())
T = data["totals"]

# ── Clinic palette ──────────────────────────────────────────────────────────
CHARCOAL = colors.HexColor("#414E54")
TEAL = colors.HexColor("#17A398")
TEAL_DARK = colors.HexColor("#0F7A72")
TEAL_WASH = colors.HexColor("#E2F2F0")
INK = colors.HexColor("#222E33")
MUTED = colors.HexColor("#6F8189")
FAINT = colors.HexColor("#93A5AC")
RULE = colors.HexColor("#D5E1E1")
WASH = colors.HexColor("#F2F7F7")
AMBER = colors.HexColor("#A8710F")
AMBER_WASH = colors.HexColor("#FAF0DD")
RED = colors.HexColor("#B03A2E")
RED_WASH = colors.HexColor("#FBEAE8")

STATUS = {
    "done": ("DONE", TEAL_DARK, TEAL_WASH),
    "partial": ("PARTIAL", AMBER, AMBER_WASH),
    "blocked": ("BLOCKED", RED, RED_WASH),
    "backlog": ("BACKLOG", MUTED, WASH),
    "withdrawn": ("WITHDRAWN", MUTED, WASH),
}

PAGE_W, PAGE_H = A4
MARGIN = 20 * mm
CONTENT_W = PAGE_W - 2 * MARGIN

ss = getSampleStyleSheet()


def hx(c):
    """reportlab inline markup needs a leading # on colours."""
    return "#" + c.hexval()[2:]


def style(name, **kw):
    kw.setdefault("fontName", "Helvetica")
    kw.setdefault("textColor", INK)
    kw.setdefault("fontSize", 9.5)
    kw.setdefault("leading", 14)
    return ParagraphStyle(name, parent=ss["BodyText"], **kw)


S = {
    "eyebrow": style("eyebrow", fontName="Helvetica-Bold", fontSize=7.5, leading=11,
                     textColor=TEAL_DARK, spaceAfter=8),
    "title": style("title", fontName="Helvetica-Bold", fontSize=26, leading=30,
                   textColor=CHARCOAL, spaceAfter=4),
    "sub": style("sub", fontSize=9, leading=13, textColor=MUTED, spaceAfter=14),
    "body": style("body", spaceAfter=7),
    "epic": style("epic", fontName="Helvetica-Bold", fontSize=15, leading=19,
                  textColor=CHARCOAL, spaceBefore=0, spaceAfter=3),
    "epicgoal": style("epicgoal", fontName="Helvetica-Oblique", fontSize=9,
                      leading=13, textColor=MUTED, spaceAfter=2),
    "epicpts": style("epicpts", fontName="Helvetica-Bold", fontSize=9,
                     textColor=TEAL_DARK, spaceAfter=12),
    "story": style("story", fontName="Helvetica-Bold", fontSize=11, leading=14,
                   textColor=CHARCOAL, spaceBefore=10, spaceAfter=3),
    "quote": style("quote", fontName="Helvetica-Oblique", fontSize=9, leading=13,
                   leftIndent=9, borderPadding=0, spaceAfter=7),
    "label": style("label", fontName="Helvetica-Bold", fontSize=7, leading=10,
                   textColor=FAINT, spaceBefore=5, spaceAfter=3),
    "bullet": style("bullet", fontSize=9, leading=12.5, leftIndent=11,
                    bulletIndent=2, spaceAfter=1.5),
    "test": style("test", fontName="Courier", fontSize=7.8, leading=11,
                  leftIndent=11, bulletIndent=2, textColor=TEAL_DARK, spaceAfter=1),
    "gap": style("gap", fontSize=8.5, leading=12, leftIndent=8, rightIndent=6,
                 spaceBefore=3, spaceAfter=3),
    "note": style("note", fontName="Helvetica-Oblique", fontSize=8.5, leading=12,
                  textColor=MUTED, spaceBefore=3, spaceAfter=4),
    "cell": style("cell", fontSize=8.5, leading=12, spaceAfter=0),
    "cellb": style("cellb", fontName="Helvetica-Bold", fontSize=8.5, leading=12, spaceAfter=0),
    "cellh": style("cellh", fontName="Helvetica-Bold", fontSize=7, leading=10,
                   textColor=MUTED, spaceAfter=0),
    "cellr": style("cellr", fontSize=8.5, leading=12, alignment=TA_RIGHT, spaceAfter=0),
    "cellrb": style("cellrb", fontName="Helvetica-Bold", fontSize=8.5, leading=12,
                    alignment=TA_RIGHT, spaceAfter=0),
    "code": style("code", fontName="Courier", fontSize=8, leading=13, leftIndent=6),
}


def chip_and_points(points, status):
    """Points and a coloured status chip on one line."""
    lbl, fg, bg = STATUS[status]
    t = Table(
        [[Paragraph(f"{points} points", S["cellh"]),
          Paragraph(f'<font color="{hx(fg)}"><b>{lbl}</b></font>',
                    style("chip", fontSize=7, leading=10, alignment=TA_LEFT))]],
        colWidths=[16 * mm, 22 * mm], hAlign="LEFT",
    )
    t.setStyle(TableStyle([
        ("BACKGROUND", (1, 0), (1, 0), bg),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("LEFTPADDING", (1, 0), (1, 0), 5),
        ("RIGHTPADDING", (1, 0), (1, 0), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return t


def data_table(rows, widths, header=True):
    t = Table(rows, colWidths=widths, hAlign="LEFT", repeatRows=1 if header else 0)
    cmds = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, RULE),
    ]
    if header:
        cmds += [("BACKGROUND", (0, 0), (-1, 0), WASH),
                 ("LINEBELOW", (0, 0), (-1, 0), 0.7, RULE)]
    t.setStyle(TableStyle(cmds))
    return t


def gap_box(txt):
    p = Paragraph(
        f'<font color="{hx(AMBER)}"><b>Test gap.</b></font> '
        f'{escape(txt.replace("**", ""))}', S["gap"])
    t = Table([[p]], colWidths=[CONTENT_W], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), AMBER_WASH),
        ("LINEBEFORE", (0, 0), (0, -1), 2, AMBER),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def quote(txt):
    p = Paragraph(escape(txt), S["quote"])
    t = Table([[p]], colWidths=[CONTENT_W], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("LINEBEFORE", (0, 0), (0, -1), 2, TEAL),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def hr(color=TEAL, width=1.2, space_after=8):
    t = Table([[""]], colWidths=[CONTENT_W], rowHeights=[0.1])
    t.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (-1, 0), width, color),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), space_after),
    ]))
    return t


# ── Story flow ──────────────────────────────────────────────────────────────

flow = []

flow.append(Paragraph("CENTRE FOR ENDOCRINE &amp; METABOLIC HEALTH", S["eyebrow"]))
flow.append(Paragraph("Requirements &amp; Delivery Backlog", S["title"]))
flow.append(hr(TEAL, 2.5, 6))
flow.append(Paragraph(
    f"Patient management system &nbsp;·&nbsp; {date.today():%d %B %Y} "
    f"&nbsp;·&nbsp; repository knowledge-corner/Cmeh", S["sub"]))

flow.append(Paragraph(
    "This is the agreed scope of the system, broken into stories, each sized in story points and "
    "each mapped to the automated tests that prove it works. It is meant to be used two ways: to "
    "agree what is being built, and to see at a glance what is genuinely covered by tests and "
    "what is not.", S["body"]))
flow.append(Paragraph(
    "Where a story has <b>no automated cover, it says so</b>. Those gaps are the most useful "
    "thing in this document — they are the places where a defect could reach the clinic "
    "unnoticed.", S["body"]))
flow.append(Spacer(1, 8))

flow.append(Paragraph("WHERE THE PROJECT STANDS", S["label"]))
total_pts = T["deliveredPts"] + T["partialPts"] + T["blockedPts"] + T["backlogPts"]
total_n = T["deliveredN"] + T["partialN"] + T["blockedN"] + T["backlogN"]
rows = [[Paragraph("", S["cellh"]), Paragraph("STORIES", S["cellh"]), Paragraph("POINTS", S["cellh"])]]
for name, n, p, strong in [
    ("Delivered", T["deliveredN"], T["deliveredPts"], True),
    ("Partially delivered", T["partialN"], T["partialPts"], False),
    ("Blocked on a clinic decision", T["blockedN"], T["blockedPts"], False),
    ("Not started", T["backlogN"], T["backlogPts"], False),
]:
    st = S["cellb"] if strong else S["cell"]
    rows.append([Paragraph(name, st), Paragraph(str(n), S["cellr"]),
                 Paragraph(str(p), S["cellrb"] if strong else S["cellr"])])
rows.append([Paragraph("Total scoped", S["cellb"]), Paragraph(str(total_n), S["cellrb"]),
             Paragraph(str(total_pts), S["cellrb"])])
tbl = data_table(rows, [CONTENT_W - 60 * mm, 30 * mm, 30 * mm])
tbl.setStyle(TableStyle([("BACKGROUND", (0, len(rows) - 1), (-1, len(rows) - 1), WASH)]))
flow.append(tbl)

flow.append(Spacer(1, 10))
TESTS = data.get("testCount")
flow.append(Paragraph(
    (f"<b>{TESTS} automated tests</b> currently pass. " if TESTS else "")
    + f"{len(T['gaps'])} stories carry no automated cover; each is flagged where it "
      f"appears and listed again under Testing.", S["body"]))

flow.append(Spacer(1, 6))
flow.append(Paragraph("HOW STORY POINTS ARE USED", S["label"]))
flow.append(Paragraph("Points estimate relative effort and risk, not hours.", S["note"]))
srows = [[Paragraph("PTS", S["cellh"]), Paragraph("MEANING", S["cellh"])]]
for pts, meaning in data["scale"]:
    srows.append([Paragraph(pts, S["cellrb"]), Paragraph(escape(meaning), S["cell"])])
flow.append(data_table(srows, [14 * mm, CONTENT_W - 14 * mm]))

flow.append(Spacer(1, 8))
for b in ["<b>Done</b> — built, and covered by tests unless a gap is noted.",
          "<b>Partial</b> — built but not finished; what remains is stated.",
          "<b>Blocked</b> — cannot proceed without a decision from the clinic.",
          "<b>Backlog</b> — agreed as wanted, not started."]:
    flow.append(Paragraph(b, S["bullet"], bulletText="–"))

flow.append(PageBreak())

# ── Epics ───────────────────────────────────────────────────────────────────

for ei, epic in enumerate(data["epics"]):
    epic_pts = sum(s["points"] for s in epic["stories"])
    done_pts = sum(s["points"] for s in epic["stories"] if s["status"] == "done")

    if ei > 0:
        flow.append(Spacer(1, 14))

    head = [
        Paragraph(f"{epic['id']} &nbsp;·&nbsp; {escape(epic['name'])}", S["epic"]),
        hr(TEAL, 1.2, 5),
        Paragraph(escape(epic["goal"]), S["epicgoal"]),
        Paragraph(f"{done_pts} of {epic_pts} points delivered.", S["epicpts"]),
    ]
    flow.append(KeepTogether(head))

    for s in epic["stories"]:
        block = [
            Paragraph(
                f'<font color="{hx(TEAL_DARK)}">{s["id"]}</font> &nbsp; '
                f'{escape(s["title"])}', S["story"]),
            chip_and_points(s["points"], s["status"]),
            Spacer(1, 4),
            quote(s["story"]),
            Paragraph("ACCEPTANCE CRITERIA", S["label"]),
        ]
        for c in s["criteria"][:2]:
            block.append(Paragraph(escape(c), S["bullet"], bulletText="–"))
        flow.append(KeepTogether(block))

        for c in s["criteria"][2:]:
            flow.append(Paragraph(escape(c), S["bullet"], bulletText="–"))

        if s.get("note"):
            flow.append(Paragraph(escape(s["note"]), S["note"]))

        if s.get("tests"):
            flow.append(Paragraph("COVERED BY", S["label"]))
            for t in s["tests"]:
                flow.append(Paragraph(escape(t), S["test"], bulletText="–"))

        if s.get("gap"):
            flow.append(Spacer(1, 3))
            flow.append(gap_box(s["gap"]))

    flow.append(PageBreak())

# ── Backlog ─────────────────────────────────────────────────────────────────

flow.append(Paragraph("Backlog — agreed but not started", S["epic"]))
flow.append(hr(TEAL, 1.2, 5))
flow.append(Paragraph(f"{len(data['backlog'])} stories &nbsp;·&nbsp; {T['backlogPts']} points",
                      S["epicpts"]))

for i in data["backlog"]:
    flow.append(KeepTogether([
        Paragraph(f'<font color="{hx(TEAL_DARK)}">{i["id"]}</font> &nbsp; '
                  f'{escape(i["title"])}', S["story"]),
        chip_and_points(i["points"], "backlog"),
        Spacer(1, 4),
        quote(i["story"]),
        Paragraph(escape(i["note"]), S["body"]),
    ]))

flow.append(PageBreak())

# ── Testing ─────────────────────────────────────────────────────────────────

flow.append(Paragraph("Testing", S["epic"]))
flow.append(hr(TEAL, 1.2, 8))
for heading, txt in data["testing"]:
    flow.append(Paragraph(f"<b>{escape(heading)}.</b> {escape(txt)}", S["body"]))

flow.append(Spacer(1, 6))
flow.append(Paragraph("STORIES WITH NO AUTOMATED COVER", S["label"]))
flow.append(Paragraph("These are the places a regression would not be caught.", S["note"]))
grows = [[Paragraph("STORY", S["cellh"]), Paragraph("WHAT IS MISSING", S["cellh"])]]
for g in T["gaps"]:
    grows.append([
        Paragraph(f'<b>{g["id"]}</b><br/><font color="{hx(MUTED)}" size="7">'
                  f'{escape(g["title"])}</font>', S["cell"]),
        Paragraph(escape(g["gap"].replace("**", "")), S["cell"]),
    ])
flow.append(data_table(grows, [42 * mm, CONTENT_W - 42 * mm]))

flow.append(Spacer(1, 10))
flow.append(Paragraph("RUNNING THE TESTS", S["label"]))
code_rows = [[Paragraph(c, S["code"])] for c in [
    "pytest&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"
    "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; # all " + str(TESTS or ""),
    "pytest tests/test_workflow.py&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; "
    "# the clinic day, booking to receipt",
    "pytest tests/test_growth_reference.py # percentile maths vs published tables",
]]
ct = Table(code_rows, colWidths=[CONTENT_W], hAlign="LEFT")
ct.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, -1), WASH),
    ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ("TOPPADDING", (0, 0), (-1, -1), 2),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
]))
flow.append(ct)

# ── Decisions ───────────────────────────────────────────────────────────────

flow.append(Spacer(1, 16))
flow.append(Paragraph("Open decisions for the clinic", S["epic"]))
flow.append(hr(TEAL, 1.2, 8))

for n, (q, a) in enumerate([
    ("Which growth reference standard?",
     "WHO, CDC or IAP 2015. This blocks clinical use of the growth chart (S-505) and is a "
     "decision for Dr. Vrushali, not a technical default."),
    ("Where is it hosted, and when do we go live?",
     "Recommended: DigitalOcean Bangalore with managed PostgreSQL, for India data residency "
     "and automated backups (S-1005)."),
    ("What else does the receptionist capture at check-in?",
     "The mechanism for clinic-specific fields exists, but no fields have been agreed (S-1103)."),
    ("Do patients get portal logins, and who issues them?",
     "Today they are created one at a time in the admin (S-1107)."),
], start=1):
    # Keep the question with its answer — a decision split across a page break
    # is exactly the thing a reader skims past.
    flow.append(KeepTogether([
        Paragraph(
            f'<font color="{hx(TEAL_DARK)}"><b>{n}.</b></font> &nbsp;<b>{escape(q)}</b>',
            S["body"]),
        Paragraph(escape(a), style("dec", fontSize=9, leading=13, leftIndent=14,
                                  textColor=INK, spaceAfter=8)),
    ]))


# ── Page furniture ──────────────────────────────────────────────────────────

def decorate(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(FAINT)
    canvas.drawString(MARGIN, 12 * mm,
                      "Centre for Endocrine & Metabolic Health — Requirements & Delivery Backlog")
    canvas.setFillColor(MUTED)
    canvas.drawRightString(PAGE_W - MARGIN, 12 * mm, str(doc.page))
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.4)
    canvas.line(MARGIN, 15 * mm, PAGE_W - MARGIN, 15 * mm)
    canvas.restoreState()


doc = BaseDocTemplate(
    OUT, pagesize=A4,
    leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=22 * mm,
    title="Requirements & Delivery Backlog",
    author="Centre for Endocrine & Metabolic Health",
    subject="Scope, story points and test coverage for the clinic patient management system",
)
frame = Frame(MARGIN, 22 * mm, CONTENT_W, PAGE_H - MARGIN - 22 * mm, id="body")
doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=decorate)])
doc.build(flow)

print(f"written {OUT}")
