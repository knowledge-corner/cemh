"""
Regenerates every form of the requirements document from tools/backlog/data.py.

    python tools/backlog/build_all.py

Produces, in docs/:
    REQUIREMENTS.md                markdown, for reading in the repository
    Requirements-and-Backlog.pdf   for sending to the clinic
    Requirements-and-Backlog.docx  editable, for marking up

Edit data.py — never the generated files — then re-run this so the three
cannot disagree with each other.

Needs `reportlab` (in requirements/dev.txt) for the PDF, and Node with the
`docx` package for the Word file. The Word step is skipped with a warning if
Node or the package is unavailable; the other two always run.
"""

import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DOCS = ROOT / "docs"

sys.path.insert(0, str(HERE))

from export import backlog_json  # noqa: E402


def run(step, argv):
    print(f"→ {step}")
    result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout or "", result.stderr or "", sep="\n")
        return False
    print(f"  {result.stdout.strip()}")
    return True


def main():
    DOCS.mkdir(parents=True, exist_ok=True)

    # The Node step reads this; the Python steps import the data directly.
    (HERE / "backlog.json").write_text(backlog_json(), encoding="utf-8")

    ok = run("markdown", [sys.executable, str(HERE / "build_markdown.py")])
    ok &= run("pdf", [sys.executable, str(HERE / "build_pdf.py")])

    if not run("docx", ["node", str(HERE / "build_docx.js")]):
        print("  skipped — needs Node and `npm install docx` in tools/backlog/")

    print("\nDone." if ok else "\nSome steps failed.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
