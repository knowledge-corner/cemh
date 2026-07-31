"""
Convert supplied IAP 2015 growth tables into the reference format.

    python manage.py import_iap --indicator lhfa --sex boys height-boys.csv

The input is a CSV with a row per month of age and columns for the LMS
parameters. Column names are matched case-insensitively and tolerate the
variations these tables are published with (``Age``/``Month``, ``L``/``lambda``).

**The numbers are checked before they are trusted.** If the file also carries
published centile or SD columns — most do — the LMS values must reproduce them,
or the import is refused. Reference data that silently disagrees with its own
printed curves is worse than no data at all, because nothing downstream would
ever reveal it.
"""

import csv
import json
import math
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from growth.reference import REFERENCE_DIR

INDICATORS = {
    "lhfa": "Height for age",
    "wfa": "Weight for age",
    "bmifa": "BMI for age",
}
SEXES = ("boys", "girls")

#: Accepted spellings for each column we need.
ALIASES = {
    "month": ("month", "months", "age", "age_months", "agemonths", "age (months)"),
    "L": ("l", "lambda"),
    "M": ("m", "mu", "median"),
    "S": ("s", "sigma", "cv"),
}

#: Centile columns used to verify the LMS values, mapped to their z-score.
CENTILE_CHECKS = {
    "p3": -1.8808, "p5": -1.6449, "p10": -1.2816, "p25": -0.6745,
    "p50": 0.0, "sd0": 0.0, "median": 0.0,
    "p75": 0.6745, "p90": 1.2816, "p95": 1.6449, "p97": 1.8808,
    "sd2neg": -2.0, "sd1neg": -1.0, "sd1": 1.0, "sd2": 2.0,
}


def _value_for_z(L, M, S, z):
    if L == 0:
        return M * math.exp(S * z)
    base = 1 + L * S * z
    if base <= 0:
        return None
    return M * (base ** (1 / L))


class Command(BaseCommand):
    help = "Import IAP 2015 LMS tables from a CSV into growth/reference/iap/."

    def add_arguments(self, parser):
        parser.add_argument("source", help="CSV file of LMS values.")
        parser.add_argument("--indicator", required=True, choices=sorted(INDICATORS),
                            help="lhfa (height), wfa (weight) or bmifa (BMI).")
        parser.add_argument("--sex", required=True, choices=SEXES)
        parser.add_argument("--tolerance", type=float, default=0.05,
                            help="Permitted difference when checking LMS against "
                                 "published centiles. Default 0.05.")
        parser.add_argument("--force", action="store_true",
                            help="Write even if verification fails. Only for a source "
                                 "you have checked another way — it defeats the point.")

    def handle(self, *args, **options):
        source = Path(options["source"])
        if not source.exists():
            raise CommandError(f"{source} does not exist.")

        rows, checked, mismatches = self._read(source, options["tolerance"])

        if not rows:
            raise CommandError(
                "No usable rows found. Expected columns for month/age and L, M, S."
            )

        if mismatches and not options["force"]:
            self.stderr.write(self.style.ERROR(
                f"\nRefusing to import: {len(mismatches)} of {checked} checked values "
                f"do not match the published centiles in this file.\n"
            ))
            for line in mismatches[:8]:
                self.stderr.write(f"  {line}")
            if len(mismatches) > 8:
                self.stderr.write(f"  … and {len(mismatches) - 8} more")
            raise CommandError(
                "\nThe LMS values disagree with the curves printed alongside them. "
                "Check the file is the right one and the columns are aligned."
            )

        out_dir = REFERENCE_DIR / "iap"
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"{options['indicator']}_{options['sex']}_5_18.json"
        target.write_text(json.dumps(rows, indent=1), encoding="utf-8")

        span = f"{rows[0]['Month']}–{rows[-1]['Month']} months"
        self.stdout.write(self.style.SUCCESS(
            f"Wrote {len(rows)} rows ({span}) to {target.relative_to(REFERENCE_DIR.parent.parent)}"
        ))
        if checked:
            self.stdout.write(
                f"Verified {checked} values against the published centiles in this file."
            )
        else:
            self.stdout.write(self.style.WARNING(
                "No centile columns found, so the LMS values could not be verified. "
                "Confirm a few points against the printed chart by hand."
            ))

    # ── Reading ───────────────────────────────────────────────────────────

    def _read(self, source, tolerance):
        with source.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            if not reader.fieldnames:
                raise CommandError("The file has no header row.")

            lookup = {name.strip().lower(): name for name in reader.fieldnames}
            cols = {}
            for key, names in ALIASES.items():
                match = next((lookup[n] for n in names if n in lookup), None)
                if match is None:
                    raise CommandError(
                        f"No column found for {key}. Looked for any of: {', '.join(names)}. "
                        f"The file has: {', '.join(reader.fieldnames)}"
                    )
                cols[key] = match

            centile_cols = {
                lookup[name]: z for name, z in CENTILE_CHECKS.items() if name in lookup
            }

            rows, checked, mismatches = [], 0, []
            for line_no, raw in enumerate(reader, start=2):
                try:
                    month = float(raw[cols["month"]])
                    L = float(raw[cols["L"]])
                    M = float(raw[cols["M"]])
                    S = float(raw[cols["S"]])
                except (TypeError, ValueError):
                    continue

                for col, z in centile_cols.items():
                    try:
                        published = float(raw[col])
                    except (TypeError, ValueError):
                        continue
                    computed = _value_for_z(L, M, S, z)
                    if computed is None:
                        continue
                    checked += 1
                    if abs(computed - published) > tolerance:
                        mismatches.append(
                            f"line {line_no}, month {month:g}, {col}: "
                            f"published {published:g} but LMS gives {computed:.3f}"
                        )

                rows.append({"Month": f"{month:g}", "L": L, "M": M, "S": S})

        rows.sort(key=lambda r: float(r["Month"]))
        return rows, checked, mismatches
