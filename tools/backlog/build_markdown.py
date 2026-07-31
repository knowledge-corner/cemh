"""Renders the backlog data into docs/REQUIREMENTS.md."""

import pathlib
import sys
from datetime import date

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from data import (  # noqa: E402
    BACKLOG_ITEMS, BLOCKED, DONE, EPICS, PARTIAL, POINT_SCALE, STATUS_LABEL,
    TESTING_NOTES,
)

OUT = HERE.parent.parent / "docs" / "REQUIREMENTS.md"

MARK = {DONE: "Done", PARTIAL: "Partial", BLOCKED: "Blocked"}

lines = []
w = lines.append

# ── Header ───────────────────────────────────────────────────────────────────

w("# Requirements & delivery backlog")
w("")
w("**Centre for Endocrine & Metabolic Health — patient management system**")
w("")
w(f"Generated {date.today():%d %B %Y} · repository `knowledge-corner/Cmeh`")
w("")
w("This document is the agreed scope of the system, broken into stories, each sized and each")
w("mapped to the automated tests that prove it works. It is meant to be used two ways: to agree")
w("what is being built, and to see at a glance what is actually covered by tests and what is not.")
w("")
w("Where a story has **no automated cover, it says so**. Those gaps are the most useful thing")
w("here — they are where a defect would reach the clinic unnoticed.")
w("")

# ── Totals ───────────────────────────────────────────────────────────────────

delivered = sum(
    s["points"] for e in EPICS for s in e["stories"] if s["status"] == DONE
)
partial = sum(
    s["points"] for e in EPICS for s in e["stories"] if s["status"] == PARTIAL
)
blocked = sum(
    s["points"] for e in EPICS for s in e["stories"] if s["status"] == BLOCKED
)
backlog = sum(i["points"] for i in BACKLOG_ITEMS)
story_count = sum(len(e["stories"]) for e in EPICS)
untested = [
    (s["id"], s["title"]) for e in EPICS for s in e["stories"] if s.get("gap")
]

w("## Where the project stands")
w("")
w("| | Stories | Points |")
w("|---|---:|---:|")
w(f"| Delivered | {sum(1 for e in EPICS for s in e['stories'] if s['status'] == DONE)} | **{delivered}** |")
w(f"| Partially delivered | {sum(1 for e in EPICS for s in e['stories'] if s['status'] == PARTIAL)} | {partial} |")
w(f"| Blocked on a decision | {sum(1 for e in EPICS for s in e['stories'] if s['status'] == BLOCKED)} | {blocked} |")
w(f"| Not started | {len(BACKLOG_ITEMS)} | {backlog} |")
w(f"| **Total scoped** | **{story_count + len(BACKLOG_ITEMS)}** | **{delivered + partial + blocked + backlog}** |")
w("")
w(f"**135 automated tests** currently pass. {len(untested)} stories carry no automated cover;")
w("each is flagged in place and listed again under *Testing* at the end.")
w("")

# ── How to read ──────────────────────────────────────────────────────────────

w("## How to read this")
w("")
w("**Story points** estimate relative effort and risk, not hours:")
w("")
w("| Points | Meaning |")
w("|---:|---|")
for pts, meaning in POINT_SCALE:
    w(f"| {pts} | {meaning} |")
w("")
w("**Status:**")
w("")
w("- **Done** — built, and covered by tests unless noted otherwise.")
w("- **Partial** — built but not finished; what remains is stated.")
w("- **Blocked** — cannot proceed without a decision or information from the clinic.")
w("- **Backlog** — agreed as wanted, not started.")
w("")
w("---")
w("")

# ── Epics ────────────────────────────────────────────────────────────────────

for epic in EPICS:
    epic_points = sum(s["points"] for s in epic["stories"])
    epic_done = sum(s["points"] for s in epic["stories"] if s["status"] == DONE)
    w(f"## {epic['id']} · {epic['name']}")
    w("")
    w(f"*{epic['goal']}*")
    w("")
    w(f"**{epic_done} of {epic_points} points delivered.**")
    w("")

    for s in epic["stories"]:
        w(f"### {s['id']} · {s['title']}")
        w("")
        w(f"`{s['points']} points` · **{MARK.get(s['status'], s['status'])}**")
        w("")
        w(f"> {s['story']}")
        w("")
        w("**Acceptance criteria**")
        w("")
        for c in s["criteria"]:
            w(f"- {c}")
        w("")
        if s.get("note"):
            w(f"*{s['note']}*")
            w("")
        if s["tests"]:
            w("**Covered by**")
            w("")
            for t in s["tests"]:
                w(f"- `{t}`")
            w("")
        if s.get("gap"):
            w(f"> ⚠️ **Test gap.** {s['gap']}")
            w("")
    w("---")
    w("")

# ── Backlog ──────────────────────────────────────────────────────────────────

w("## Backlog — agreed but not started")
w("")
w(f"{len(BACKLOG_ITEMS)} stories, {backlog} points.")
w("")
for i in BACKLOG_ITEMS:
    w(f"### {i['id']} · {i['title']}")
    w("")
    w(f"`{i['points']} points` · **Backlog**")
    w("")
    w(f"> {i['story']}")
    w("")
    w(f"{i['note']}")
    w("")
w("---")
w("")

# ── Testing ──────────────────────────────────────────────────────────────────

w("## Testing")
w("")
for heading, body in TESTING_NOTES:
    w(f"**{heading}.** {body}")
    w("")

w("### Stories with no automated cover")
w("")
w("These are the places a regression would not be caught:")
w("")
w("| Story | What is missing |")
w("|---|---|")
for e in EPICS:
    for s in e["stories"]:
        if s.get("gap"):
            gap = s["gap"].replace("**", "").replace("\n", " ")
            w(f"| {s['id']} · {s['title']} | {gap} |")
w("")

w("### Running the tests")
w("")
w("```bash")
w("pytest                      # all 135")
w("pytest tests/test_workflow.py   # the clinic day, booking to receipt")
w("pytest tests/test_growth_reference.py  # percentile maths vs published tables")
w("```")
w("")
w("---")
w("")

# ── Decisions ────────────────────────────────────────────────────────────────

w("## Open decisions for the clinic")
w("")
w("1. **Which growth reference standard?** (S-505) WHO, CDC or IAP 2015. This blocks clinical")
w("   use of the growth chart and is a decision for Dr. Vrushali, not a technical default.")
w("2. **Where is it hosted, and when do we go live?** (S-1005) Recommended: DigitalOcean")
w("   Bangalore with managed PostgreSQL, for India data residency and automated backups.")
w("3. **What else does the receptionist capture at check-in?** The form-definition mechanism")
w("   exists (S-1103) but no fields have been agreed yet.")
w("4. **Do patients get portal logins at all**, and if so who issues them? (S-1107)")
w("")

OUT.parent.mkdir(parents=True, exist_ok=True)
with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines) + "\n")

print(f"written {OUT}")
print(f"{len(lines)} lines · delivered {delivered} pts · backlog {backlog} pts")
print(f"stories without automated cover: {len(untested)}")
