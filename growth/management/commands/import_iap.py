"""
Install the IAP 2015 growth references.

    python manage.py import_iap path/to/iap-2015.pdf
    python manage.py import_iap table.csv --indicator lhfa --sex boys

The IAP charts are published in Khadilkar et al., *Indian Pediatrics*
2015;52:47-55, as six tables of **centiles** — not as LMS parameters. The paper
built its curves with Cole's LMS method but printed only the resulting values,
so there is no L, M or S to import. See ``growth/centiles.py`` for what the
application does with a reference of this shape.

Given the PDF, this command reads all six tables from its text layer. Given a
CSV, it reads one table and needs to be told which. Either way the numbers are
checked before they are written, because a reference table that disagrees with
its own printed curves is worse than no table at all — nothing downstream would
ever reveal it.
"""

import csv
import json
import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from growth.reference import REFERENCE_DIR

INDICATORS = {
    "lhfa": "Height for age",
    "wfa": "Weight for age",
    "bmifa": "BMI for age",
}
SEXES = ("boys", "girls")

#: Columns each kind of table carries, in printed order. Height and weight use
#: seven centiles; BMI substitutes the adult-equivalent cut-offs for the top two
#: because that is what the paper recommends for defining overweight and obesity
#: in Asian children (adult BMI 23 and 27), and they are not centiles at all.
CENTILE_COLUMNS = ["P3", "P10", "P25", "P50", "P75", "P90", "P97"]
BMI_COLUMNS = ["P3", "P5", "P10", "P25", "P50", "Eq23", "Eq27"]

#: Where each table lives in the paper, so the PDF can be read unattended.
PAPER_TABLES = {
    "II": ("lhfa", "boys"),
    "III": ("wfa", "boys"),
    "IV": ("lhfa", "girls"),
    "V": ("wfa", "girls"),
    "VI": ("bmifa", "boys"),
    "VII": ("bmifa", "girls"),
}

#: Accepted spellings, matched case-insensitively after stripping punctuation.
ALIASES = {
    "Age": ("age", "ageyears", "ageyrs", "years", "month", "months", "agemonths"),
    "P3": ("p3", "3", "3rd"),
    "P5": ("p5", "5", "5th"),
    "P10": ("p10", "10", "10th"),
    "P25": ("p25", "25", "25th"),
    "P50": ("p50", "50", "50th", "median"),
    "P75": ("p75", "75", "75th"),
    "P90": ("p90", "90", "90th"),
    "P97": ("p97", "97", "97th"),
    "Eq23": ("eq23", "23eq", "23", "23adulteq", "23adultequivalent", "eq71", "eq75"),
    "Eq27": ("eq27", "27eq", "27", "27adulteq", "27adultequivalent", "eq90", "eq95"),
    "SD": ("sd", "standarddeviation", "sigma"),
}

#: The paper's tables run 5.0 to 18.0 years in half-year steps.
EXPECTED_ROWS = 27
FIRST_AGE_YEARS, LAST_AGE_YEARS = 5.0, 18.0

#: Biggest credible change in one half-year step, per indicator. A dropped digit
#: during extraction produces a jump far larger than any of these; real growth,
#: even through the pubertal spurt, does not. Observed maxima in the paper are
#: 3.3 cm, 3.8 kg and 0.6 kg/m², so these leave generous headroom.
MAX_STEP = {"lhfa": 8.0, "wfa": 8.0, "bmifa": 2.0}

#: A row of the paper's tables: an age, then eight numbers.
ROW_RE = re.compile(r"^(\d+\.\d)\s+((?:\d+\.\d+\s+){7}\d+\.\d+)$", re.M)

#: "TABLE VII BODY MASS INDEX …". The paper prints "TABLE IVHEIGHT" with no
#: space after the numeral, so the lookahead accepts a letter as well as a space.
#: Longer numerals come first so "III" is not matched as "II".
TABLE_RE = re.compile(r"TABLE\s+(III|II|IV|VII|VI|V)(?=[A-Z ])")


class Command(BaseCommand):
    help = "Install the IAP 2015 centile tables into growth/reference/iap/."

    def add_arguments(self, parser):
        parser.add_argument(
            "source",
            help="The IAP 2015 paper as a PDF (reads all six tables), or a CSV "
                 "of one table.",
        )
        parser.add_argument("--indicator", choices=sorted(INDICATORS),
                            help="CSV only: lhfa (height), wfa (weight) or bmifa.")
        parser.add_argument("--sex", choices=SEXES, help="CSV only.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Validate and report, but write nothing.")

    def handle(self, *args, **options):
        source = Path(options["source"])
        if not source.exists():
            raise CommandError(f"{source} does not exist.")

        if source.suffix.lower() == ".pdf":
            tables = self._read_pdf(source)
        else:
            if not (options["indicator"] and options["sex"]):
                raise CommandError(
                    "A CSV holds one table, so --indicator and --sex are needed. "
                    "Give the paper's PDF instead to import all six at once."
                )
            tables = {
                (options["indicator"], options["sex"]): self._read_csv(
                    source, options["indicator"]
                )
            }

        failures = {}
        for (indicator, sex), rows in sorted(tables.items()):
            problems = self._validate(indicator, rows)
            if problems:
                failures[(indicator, sex)] = problems

        if failures:
            self.stderr.write(self.style.ERROR("\nRefusing to import.\n"))
            for (indicator, sex), problems in failures.items():
                self.stderr.write(f"  {INDICATORS[indicator]}, {sex}:")
                for line in problems[:6]:
                    self.stderr.write(f"    {line}")
                if len(problems) > 6:
                    self.stderr.write(f"    … and {len(problems) - 6} more")
            raise CommandError(
                "\nThese values disagree with the curves they are printed as. "
                "Check the source is the right one and the columns are aligned."
            )

        if options["dry_run"]:
            for (indicator, sex), rows in sorted(tables.items()):
                self.stdout.write(
                    f"{INDICATORS[indicator]}, {sex}: {len(rows)} rows validated"
                )
            self.stdout.write(self.style.SUCCESS("\nAll checks passed. Nothing written."))
            return

        out_dir = REFERENCE_DIR / "iap"
        out_dir.mkdir(parents=True, exist_ok=True)
        for (indicator, sex), rows in sorted(tables.items()):
            target = out_dir / f"{indicator}_{sex}_5_18.json"
            target.write_text(json.dumps(rows, indent=1), encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(
                f"Wrote {len(rows)} rows to {target.relative_to(REFERENCE_DIR.parent.parent)}"
            ))
        self.stdout.write(
            f"\n{len(tables)} table(s) checked for row and age monotonicity"
            + (", and against the adult 23/27 cut-offs at 18 years"
               if any(i == "bmifa" for i, _ in tables) else "")
            + "."
        )

    # ── Reading ───────────────────────────────────────────────────────────

    def _read_pdf(self, source):
        """All six tables, keyed by (indicator, sex), from the paper's text layer."""
        try:
            import pypdfium2
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise CommandError(
                "Reading the PDF needs pypdfium2 (pip install pypdfium2). "
                "Alternatively transcribe each table to CSV and import them one "
                "at a time with --indicator and --sex."
            ) from exc

        document = pypdfium2.PdfDocument(str(source))
        # The text layer uses CRLF; the row pattern is line-anchored, so the
        # carriage returns have to go or nothing matches.
        text = "\n".join(
            page.get_textpage().get_text_range() for page in document
        ).replace("\r\n", "\n").replace("\r", "\n")

        marks = sorted((m.start(), m.group(1)) for m in TABLE_RE.finditer(text))
        if not marks:
            raise CommandError(
                f"No tables found in {source.name}. Is this the IAP 2015 paper "
                "(Indian Pediatrics 2015;52:47-55)?"
            )
        bounds = [start for start, _ in marks] + [len(text)]

        tables = {}
        for (start, numeral), end in zip(marks, bounds[1:]):
            if numeral not in PAPER_TABLES:
                continue
            indicator, sex = PAPER_TABLES[numeral]
            columns = BMI_COLUMNS if indicator == "bmifa" else CENTILE_COLUMNS
            rows = [
                self._row(age, values.split(), columns)
                for age, values in ROW_RE.findall(text[start:end])
            ]
            if rows:
                # Tables II-VII appear once each; a second match would mean the
                # regex has caught a cross-reference in the prose.
                tables.setdefault((indicator, sex), rows)

        missing = set(PAPER_TABLES.values()) - set(tables)
        if missing:
            names = ", ".join(f"{INDICATORS[i]} {s}" for i, s in sorted(missing))
            raise CommandError(f"Could not read these tables from the PDF: {names}.")
        return tables

    def _read_csv(self, source, indicator):
        """One table from a CSV, columns matched by name."""
        columns = BMI_COLUMNS if indicator == "bmifa" else CENTILE_COLUMNS
        wanted = ["Age"] + columns + ["SD"]

        with source.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            if not reader.fieldnames:
                raise CommandError("The file has no header row.")

            lookup = {_normalise(name): name for name in reader.fieldnames}
            found = {}
            for key in wanted:
                match = next(
                    (lookup[alias] for alias in ALIASES[key] if alias in lookup), None
                )
                if match is None:
                    if key == "SD":
                        continue  # The SD column is recorded but never scored against.
                    raise CommandError(
                        f"No column found for {key}. Looked for any of: "
                        f"{', '.join(ALIASES[key])}. "
                        f"The file has: {', '.join(reader.fieldnames)}"
                    )
                found[key] = match

            rows = []
            for raw in reader:
                try:
                    values = [float(raw[found[key]]) for key in columns]
                    age = float(raw[found["Age"]])
                except (KeyError, TypeError, ValueError):
                    continue
                sd = None
                if "SD" in found:
                    try:
                        sd = float(raw[found["SD"]])
                    except (TypeError, ValueError):
                        sd = None
                rows.append(self._row(age, values, columns, sd=sd))

        if not rows:
            raise CommandError(
                f"No usable rows found. Expected an age column and {', '.join(columns)}."
            )
        rows.sort(key=lambda r: r["Month"])
        return rows

    def _row(self, age, values, columns, sd=None):
        """
        One reference row.

        Ages are stored as months to match the WHO and CDC tables, so the whole
        module can keep a single x-axis. The paper prints years.
        """
        age = float(age)
        months = age * 12 if age <= LAST_AGE_YEARS else age
        row = {"Month": round(months, 1)}
        row.update({name: float(value) for name, value in zip(columns, values)})
        if sd is None and len(values) > len(columns):
            sd = float(values[len(columns)])
        if sd is not None:
            # Recorded as published, never used for scoring: it is the sample SD,
            # not a parameter of the skewed distribution these centiles describe.
            # See growth/reference/SOURCES.md.
            row["SD"] = float(sd)
        return row

    # ── Checking ──────────────────────────────────────────────────────────

    def _validate(self, indicator, rows):
        """Every reason to refuse this table, as readable lines."""
        columns = BMI_COLUMNS if indicator == "bmifa" else CENTILE_COLUMNS
        problems = []

        if len(rows) != EXPECTED_ROWS:
            problems.append(
                f"expected {EXPECTED_ROWS} rows for 5.0-18.0 years, found {len(rows)}"
            )
        if rows:
            first, last = rows[0]["Month"] / 12, rows[-1]["Month"] / 12
            if (first, last) != (FIRST_AGE_YEARS, LAST_AGE_YEARS):
                problems.append(
                    f"expected ages 5.0-18.0 years, found {first:g}-{last:g}"
                )

        # 1. Each row must rise across the columns. Catches a transposed column.
        for row in rows:
            values = [row[name] for name in columns]
            if values != sorted(values):
                problems.append(
                    f"age {row['Month'] / 12:g}y: values do not rise across the row "
                    f"({', '.join(f'{c} {row[c]:g}' for c in columns)})"
                )

        # 2. Each column must rise smoothly with age. Catches a dropped digit.
        limit = MAX_STEP[indicator]
        for name in columns:
            for lower, upper in zip(rows, rows[1:]):
                step = upper[name] - lower[name]
                if step < 0:
                    problems.append(
                        f"{name} falls from {lower[name]:g} to {upper[name]:g} between "
                        f"{lower['Month'] / 12:g}y and {upper['Month'] / 12:g}y"
                    )
                elif step > limit:
                    problems.append(
                        f"{name} jumps {step:g} between {lower['Month'] / 12:g}y and "
                        f"{upper['Month'] / 12:g}y, more than the {limit:g} expected in "
                        f"half a year"
                    )

        # 3. BMI only: the adult-equivalent lines must sit above the median, and
        #    at 18 years must have converged on the adult 23 and 27 they are
        #    defined from. This is the check that actually proves the columns
        #    have not been misread.
        if indicator == "bmifa" and rows:
            for row in rows:
                if not row["P50"] < row["Eq23"] < row["Eq27"]:
                    problems.append(
                        f"age {row['Month'] / 12:g}y: expected P50 < 23-Eq < 27-Eq, "
                        f"found {row['P50']:g}, {row['Eq23']:g}, {row['Eq27']:g}"
                    )
            adult = rows[-1]
            for name, expected in (("Eq23", 23.0), ("Eq27", 27.0)):
                if abs(adult[name] - expected) > 0.6:
                    problems.append(
                        f"at 18 years {name} is {adult[name]:g}, which should have "
                        f"converged on the adult cut-off of {expected:g}"
                    )

        return problems


def _normalise(name):
    """Column names, stripped to letters and digits for matching."""
    return re.sub(r"[^a-z0-9]", "", name.strip().lower())
