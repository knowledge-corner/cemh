/*
 * Builds the requirements document as a .docx from the same backlog data that
 * generates docs/REQUIREMENTS.md, so the two cannot disagree.
 */

const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType,
  Table, TableRow, TableCell, WidthType, ShadingType, BorderStyle,
  TabStopType, PageBreak, Footer, PageNumber, LevelFormat, convertInchesToTwip,
} = require("docx");

const data = JSON.parse(fs.readFileSync(__dirname + "/backlog.json", "utf8"));
// backlog.json is written by export.py — run build_all.py, not this directly.

// ── Clinic palette, from the printed patient file ───────────────────────────
const CHARCOAL = "414E54";
const TEAL = "17A398";
const TEAL_DARK = "0F7A72";
const INK = "222E33";
const MUTED = "6F8189";
const FAINT = "93A5AC";
const RULE = "D5E1E1";
const WASH = "F2F7F7";
const TEAL_WASH = "E2F2F0";
const AMBER = "A8710F";
const AMBER_WASH = "FAF0DD";

const CONTENT_W = 9026; // A4 minus 1" margins each side, in DXA
const FONT = "Calibri";

const STATUS_STYLE = {
  done: { label: "DONE", color: TEAL_DARK, bg: TEAL_WASH },
  partial: { label: "PARTIAL", color: AMBER, bg: AMBER_WASH },
  blocked: { label: "BLOCKED", color: "B03A2E", bg: "FBEAE8" },
  backlog: { label: "BACKLOG", color: MUTED, bg: WASH },
  withdrawn: { label: "WITHDRAWN", color: MUTED, bg: WASH },
};

const today = new Date().toLocaleDateString("en-GB", {
  day: "numeric", month: "long", year: "numeric",
});

// ── Small builders ──────────────────────────────────────────────────────────

const noBorder = { style: BorderStyle.NONE, size: 0, color: "FFFFFF" };
const cellBorders = {
  top: { style: BorderStyle.SINGLE, size: 2, color: RULE },
  bottom: { style: BorderStyle.SINGLE, size: 2, color: RULE },
  left: noBorder, right: noBorder,
};

function text(str, opts = {}) {
  return new TextRun({
    text: str, font: FONT, size: opts.size || 20,
    color: opts.color || INK, bold: opts.bold, italics: opts.italics,
    allCaps: opts.caps,
  });
}

function para(str, opts = {}) {
  return new Paragraph({
    children: Array.isArray(str) ? str : [text(str, opts)],
    spacing: { before: opts.before ?? 0, after: opts.after ?? 120 },
    alignment: opts.align,
    indent: opts.indent,
    border: opts.border,
  });
}

function label(str, color = FAINT) {
  return new Paragraph({
    children: [new TextRun({
      text: str, font: FONT, size: 15, bold: true, color, allCaps: true,
      characterSpacing: 24,
    })],
    spacing: { before: 160, after: 60 },
  });
}

function cell(children, opts = {}) {
  return new TableCell({
    children: Array.isArray(children) ? children : [children],
    width: { size: opts.width, type: WidthType.DXA },
    shading: opts.bg ? { type: ShadingType.CLEAR, fill: opts.bg, color: "auto" } : undefined,
    borders: opts.borders || cellBorders,
    margins: { top: 90, bottom: 90, left: 120, right: 120 },
    columnSpan: opts.span,
  });
}

function table(rows, widths) {
  return new Table({
    rows, columnWidths: widths,
    width: { size: CONTENT_W, type: WidthType.DXA },
  });
}

function headerRow(labels, widths) {
  return new TableRow({
    tableHeader: true,
    children: labels.map((l, i) => cell(
      new Paragraph({
        children: [new TextRun({
          text: l, font: FONT, size: 15, bold: true, color: MUTED,
          allCaps: true, characterSpacing: 20,
        })],
        spacing: { after: 0 },
      }),
      { width: widths[i], bg: WASH }
    )),
  });
}

function statusChip(status) {
  const s = STATUS_STYLE[status] || STATUS_STYLE.backlog;
  return new TextRun({
    text: `  ${s.label}  `, font: FONT, size: 15, bold: true,
    color: s.color, shading: { type: ShadingType.CLEAR, fill: s.bg, color: "auto" },
    characterSpacing: 16,
  });
}

function bullets(items, opts = {}) {
  return items.map((t) => new Paragraph({
    children: [text(t, { size: opts.size || 19, color: opts.color || INK })],
    numbering: { reference: "dash", level: 0 },
    spacing: { after: 40 },
  }));
}

function rule(before = 200, after = 120) {
  return new Paragraph({
    children: [], spacing: { before, after },
    border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: RULE } },
  });
}

// ── Document body ───────────────────────────────────────────────────────────

const body = [];
const T = data.totals;
const totalPts = T.deliveredPts + T.partialPts + T.blockedPts + T.backlogPts;
const totalStories = T.deliveredN + T.partialN + T.blockedN + T.backlogN;

// Title block
body.push(new Paragraph({
  children: [new TextRun({
    text: "Centre for Endocrine & Metabolic Health", font: FONT, size: 18,
    bold: true, color: TEAL_DARK, allCaps: true, characterSpacing: 40,
  })],
  spacing: { after: 160 },
}));
body.push(new Paragraph({
  children: [new TextRun({
    text: "Requirements & Delivery Backlog", font: FONT, size: 52,
    bold: true, color: CHARCOAL,
  })],
  spacing: { after: 80 },
}));
body.push(new Paragraph({
  children: [], spacing: { after: 200 },
  border: { bottom: { style: BorderStyle.SINGLE, size: 18, color: TEAL } },
}));
body.push(para(`Patient management system  ·  ${today}  ·  repository knowledge-corner/Cmeh`,
  { color: MUTED, size: 19, after: 240 }));

body.push(para(
  "This is the agreed scope of the system, broken into stories, each sized in story points " +
  "and each mapped to the automated tests that prove it works. It is meant to be used two " +
  "ways: to agree what is being built, and to see at a glance what is genuinely covered by " +
  "tests and what is not.", { after: 120 }));
body.push(para([
  text("Where a story has ", {}),
  text("no automated cover, it says so", { bold: true }),
  text(". Those gaps are the most useful thing in this document — they are the places where " +
       "a defect could reach the clinic unnoticed.", {}),
], { after: 260 }));

// Status summary
body.push(label("Where the project stands"));
body.push(table([
  headerRow(["", "Stories", "Points"], [5426, 1800, 1800]),
  ...[
    ["Delivered", T.deliveredN, T.deliveredPts, true],
    ["Partially delivered", T.partialN, T.partialPts, false],
    ["Blocked on a clinic decision", T.blockedN, T.blockedPts, false],
    ["Not started", T.backlogN, T.backlogPts, false],
  ].map(([n, s, p, strong]) => new TableRow({
    children: [
      cell(para(n, { size: 19, bold: strong, after: 0 }), { width: 5426 }),
      cell(para(String(s), { size: 19, after: 0, align: AlignmentType.RIGHT }), { width: 1800 }),
      cell(para(String(p), { size: 19, bold: strong, after: 0, align: AlignmentType.RIGHT }), { width: 1800 }),
    ],
  })),
  new TableRow({
    children: [
      cell(para("Total scoped", { size: 19, bold: true, after: 0 }), { width: 5426, bg: WASH }),
      cell(para(String(totalStories), { size: 19, bold: true, after: 0, align: AlignmentType.RIGHT }), { width: 1800, bg: WASH }),
      cell(para(String(totalPts), { size: 19, bold: true, after: 0, align: AlignmentType.RIGHT }), { width: 1800, bg: WASH }),
    ],
  }),
], [5426, 1800, 1800]));

body.push(para([
  text(data.testCount ? `${data.testCount} automated tests` : "", { bold: true }),
  text(`${data.testCount ? " currently pass." : ""} ${T.gaps.length} stories carry no automated cover; each is flagged ` +
       `where it appears and listed again under Testing.`, {}),
], { before: 200, after: 200 }));

// Points scale
body.push(label("How story points are used"));
body.push(para("Points estimate relative effort and risk, not hours.",
  { color: MUTED, size: 19, after: 120 }));
body.push(table([
  headerRow(["Points", "Meaning"], [1200, 7826]),
  ...data.scale.map(([pts, meaning]) => new TableRow({
    children: [
      cell(para(pts, { size: 19, bold: true, after: 0, align: AlignmentType.RIGHT }), { width: 1200 }),
      cell(para(meaning, { size: 19, after: 0 }), { width: 7826 }),
    ],
  })),
], [1200, 7826]));

body.push(para("", { before: 160 }));
body.push(...bullets([
  "Done — built, and covered by tests unless a gap is noted.",
  "Partial — built but not finished; what remains is stated.",
  "Blocked — cannot proceed without a decision or information from the clinic.",
  "Backlog — agreed as wanted, not started.",
], { color: MUTED }));

body.push(new Paragraph({ children: [new PageBreak()] }));

// ── Epics ───────────────────────────────────────────────────────────────────

data.epics.forEach((epic, ei) => {
  const epicPts = epic.stories.reduce((a, s) => a + s.points, 0);
  const donePts = epic.stories.filter(s => s.status === "done")
                              .reduce((a, s) => a + s.points, 0);

  if (ei > 0) body.push(new Paragraph({ children: [new PageBreak()] }));

  body.push(new Paragraph({
    heading: HeadingLevel.HEADING_1,
    children: [new TextRun({
      text: `${epic.id} · ${epic.name}`, font: FONT, size: 32, bold: true, color: CHARCOAL,
    })],
    spacing: { before: 0, after: 80 },
  }));
  body.push(new Paragraph({
    children: [], spacing: { after: 140 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 12, color: TEAL } },
  }));
  body.push(para(epic.goal, { italics: true, color: MUTED, size: 19, after: 60 }));
  body.push(para(`${donePts} of ${epicPts} points delivered.`,
    { bold: true, size: 19, color: TEAL_DARK, after: 220 }));

  epic.stories.forEach((s) => {
    body.push(new Paragraph({
      heading: HeadingLevel.HEADING_2,
      children: [
        new TextRun({ text: `${s.id}  `, font: FONT, size: 22, bold: true, color: TEAL_DARK }),
        new TextRun({ text: s.title, font: FONT, size: 24, bold: true, color: CHARCOAL }),
      ],
      spacing: { before: 200, after: 60 },
    }));

    body.push(new Paragraph({
      children: [
        new TextRun({ text: `${s.points} points`, font: FONT, size: 17, bold: true, color: MUTED }),
        new TextRun({ text: "     ", font: FONT, size: 17 }),
        statusChip(s.status),
      ],
      spacing: { after: 120 },
    }));

    // The user story, set apart with a left rule.
    body.push(new Paragraph({
      children: [text(s.story, { italics: true, size: 19, color: INK })],
      indent: { left: 220 },
      border: { left: { style: BorderStyle.SINGLE, size: 12, color: TEAL, space: 8 } },
      spacing: { after: 140 },
    }));

    body.push(label("Acceptance criteria"));
    body.push(...bullets(s.criteria));

    if (s.note) {
      body.push(para(s.note, { italics: true, size: 18, color: MUTED, before: 100, after: 60 }));
    }

    if (s.tests && s.tests.length) {
      body.push(label("Covered by"));
      body.push(...s.tests.map((t) => new Paragraph({
        children: [new TextRun({ text: t, font: "Consolas", size: 17, color: TEAL_DARK })],
        numbering: { reference: "dash", level: 0 },
        spacing: { after: 30 },
      })));
    }

    if (s.gap) {
      body.push(new Paragraph({
        children: [
          new TextRun({ text: "Test gap.  ", font: FONT, size: 18, bold: true, color: AMBER }),
          new TextRun({ text: s.gap.replace(/\*\*/g, ""), font: FONT, size: 18, color: INK }),
        ],
        shading: { type: ShadingType.CLEAR, fill: AMBER_WASH, color: "auto" },
        indent: { left: 160, right: 160 },
        spacing: { before: 140, after: 140, line: 260 },
        border: { left: { style: BorderStyle.SINGLE, size: 12, color: AMBER, space: 10 } },
      }));
    }
  });
});

// ── Backlog ─────────────────────────────────────────────────────────────────

body.push(new Paragraph({ children: [new PageBreak()] }));
body.push(new Paragraph({
  heading: HeadingLevel.HEADING_1,
  children: [new TextRun({
    text: "Backlog — agreed but not started", font: FONT, size: 32, bold: true, color: CHARCOAL,
  })],
  spacing: { after: 80 },
}));
body.push(new Paragraph({
  children: [], spacing: { after: 160 },
  border: { bottom: { style: BorderStyle.SINGLE, size: 12, color: TEAL } },
}));
body.push(para(`${data.backlog.length} stories · ${T.backlogPts} points`,
  { bold: true, size: 19, color: MUTED, after: 220 }));

data.backlog.forEach((i) => {
  body.push(new Paragraph({
    heading: HeadingLevel.HEADING_2,
    children: [
      new TextRun({ text: `${i.id}  `, font: FONT, size: 22, bold: true, color: TEAL_DARK }),
      new TextRun({ text: i.title, font: FONT, size: 24, bold: true, color: CHARCOAL }),
    ],
    spacing: { before: 200, after: 60 },
  }));
  body.push(new Paragraph({
    children: [
      new TextRun({ text: `${i.points} points`, font: FONT, size: 17, bold: true, color: MUTED }),
      new TextRun({ text: "     ", font: FONT, size: 17 }),
      statusChip("backlog"),
    ],
    spacing: { after: 120 },
  }));
  body.push(new Paragraph({
    children: [text(i.story, { italics: true, size: 19 })],
    indent: { left: 220 },
    border: { left: { style: BorderStyle.SINGLE, size: 12, color: RULE, space: 8 } },
    spacing: { after: 100 },
  }));
  body.push(para(i.note, { size: 19, color: MUTED }));
});

// ── Testing ─────────────────────────────────────────────────────────────────

body.push(new Paragraph({ children: [new PageBreak()] }));
body.push(new Paragraph({
  heading: HeadingLevel.HEADING_1,
  children: [new TextRun({ text: "Testing", font: FONT, size: 32, bold: true, color: CHARCOAL })],
  spacing: { after: 80 },
}));
body.push(new Paragraph({
  children: [], spacing: { after: 180 },
  border: { bottom: { style: BorderStyle.SINGLE, size: 12, color: TEAL } },
}));

data.testing.forEach(([heading, bodyText]) => {
  body.push(new Paragraph({
    children: [
      new TextRun({ text: `${heading}.  `, font: FONT, size: 20, bold: true, color: CHARCOAL }),
      new TextRun({ text: bodyText, font: FONT, size: 20, color: INK }),
    ],
    spacing: { after: 140 },
  }));
});

body.push(label("Stories with no automated cover", AMBER));
body.push(para("These are the places a regression would not be caught.",
  { color: MUTED, size: 19, after: 140 }));
body.push(table([
  headerRow(["Story", "What is missing"], [2900, 6126]),
  ...T.gaps.map((g) => new TableRow({
    children: [
      cell([
        para(g.id, { size: 18, bold: true, after: 20 }),
        para(g.title, { size: 17, color: MUTED, after: 0 }),
      ], { width: 2900 }),
      cell(para(g.gap.replace(/\*\*/g, ""), { size: 18, after: 0 }), { width: 6126 }),
    ],
  })),
], [2900, 6126]));

body.push(label("Running the tests"));
[`pytest                                  # all ${data.testCount || ""}`.trimEnd(),
 "pytest tests/test_workflow.py           # the clinic day, booking to receipt",
 "pytest tests/test_growth_reference.py   # percentile maths vs published tables",
].forEach((line) => body.push(new Paragraph({
  children: [new TextRun({ text: line, font: "Consolas", size: 18, color: INK })],
  shading: { type: ShadingType.CLEAR, fill: WASH, color: "auto" },
  indent: { left: 160, right: 160 },
  spacing: { after: 0, line: 280 },
})));

// ── Decisions ───────────────────────────────────────────────────────────────

body.push(new Paragraph({
  heading: HeadingLevel.HEADING_1,
  children: [new TextRun({
    text: "Open decisions for the clinic", font: FONT, size: 32, bold: true, color: CHARCOAL,
  })],
  spacing: { before: 400, after: 80 },
}));
body.push(new Paragraph({
  children: [], spacing: { after: 180 },
  border: { bottom: { style: BorderStyle.SINGLE, size: 12, color: TEAL } },
}));

data.decisions.forEach(([q, a], n) => {
  body.push(new Paragraph({
    children: [
      new TextRun({ text: `${n + 1}.  `, font: FONT, size: 20, bold: true, color: TEAL_DARK }),
      new TextRun({ text: q, font: FONT, size: 20, bold: true, color: CHARCOAL }),
    ],
    spacing: { before: 140, after: 40 },
  }));
  body.push(para(a, { size: 19, color: INK, indent: { left: 300 } }));
});

// ── Assemble ────────────────────────────────────────────────────────────────

const doc = new Document({
  creator: "Centre for Endocrine & Metabolic Health",
  title: "Requirements & Delivery Backlog",
  description: "Scope, story points and test coverage for the clinic patient management system",
  numbering: {
    config: [{
      reference: "dash",
      levels: [{
        level: 0, format: LevelFormat.BULLET, text: "–", alignment: AlignmentType.LEFT,
        style: { paragraph: { indent: { left: 300, hanging: 180 } } },
      }],
    }],
  },
  sections: [{
    properties: {
      page: { margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } },
    },
    footers: {
      default: new Footer({
        children: [new Paragraph({
          alignment: AlignmentType.RIGHT,
          children: [
            new TextRun({
              text: "Requirements & Delivery Backlog   ·   ",
              font: FONT, size: 16, color: FAINT,
            }),
            new TextRun({ children: [PageNumber.CURRENT], font: FONT, size: 16, color: MUTED }),
          ],
        })],
      }),
    },
    children: body,
  }],
});

Packer.toBuffer(doc).then((buf) => {
  const out = require("path").join(__dirname, "..", "..", "docs", "Requirements-and-Backlog.docx");
  fs.writeFileSync(out, buf);
  console.log("written", out, (buf.length / 1024).toFixed(0) + " KB");
});
