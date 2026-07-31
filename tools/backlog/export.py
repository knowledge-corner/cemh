"""Serialises the backlog into JSON, with the totals precomputed."""

import json
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))

from data import (  # noqa: E402
    BACKLOG_ITEMS, BLOCKED, DONE, EPICS, OPEN_DECISIONS, PARTIAL, POINT_SCALE, TESTING_NOTES,
)


def test_count():
    """
    How many tests actually exist.

    Counted by asking pytest rather than written down, because a hand-maintained
    figure in a document about test coverage is precisely the number that goes
    quietly out of date.

    Returns ``None`` if pytest cannot run — the document then omits the figure
    rather than printing a stale one.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q"],
            cwd=ROOT, capture_output=True, text=True, timeout=300,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    match = re.search(r"(\d+) tests? collected", result.stdout)
    return int(match.group(1)) if match else None


def backlog_dict():
    stories = [s for e in EPICS for s in e["stories"]]

    def pts(status):
        return sum(s["points"] for s in stories if s["status"] == status)

    def count(status):
        return sum(1 for s in stories if s["status"] == status)

    return {
        "epics": EPICS,
        "backlog": BACKLOG_ITEMS,
        "scale": POINT_SCALE,
        "testing": TESTING_NOTES,
        "decisions": OPEN_DECISIONS,
        "testCount": test_count(),
        "totals": {
            "deliveredPts": pts(DONE), "deliveredN": count(DONE),
            "partialPts": pts(PARTIAL), "partialN": count(PARTIAL),
            "blockedPts": pts(BLOCKED), "blockedN": count(BLOCKED),
            "backlogPts": sum(i["points"] for i in BACKLOG_ITEMS),
            "backlogN": len(BACKLOG_ITEMS),
            "gaps": [
                {"id": s["id"], "title": s["title"], "gap": s["gap"]}
                for s in stories if s.get("gap")
            ],
        },
    }


def backlog_json():
    return json.dumps(backlog_dict(), indent=1)


if __name__ == "__main__":
    out = HERE / "backlog.json"
    out.write_text(backlog_json(), encoding="utf-8")
    print(f"written {out}")
