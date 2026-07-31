"""Serialises the backlog into JSON, with the totals precomputed."""

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from data import BACKLOG_ITEMS, BLOCKED, DONE, EPICS, PARTIAL, POINT_SCALE, TESTING_NOTES  # noqa: E402


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
