"""
The import command must refuse data it cannot vouch for.

A reference table that disagrees with the curves it was printed as is worse than
no table at all, because nothing downstream would ever reveal it — the chart
would simply place children in the wrong place, confidently. So the tests here
are mostly about the refusals, and about the refusal naming the row.
"""

import csv
import io
import json
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from growth import reference as ref


def _sound_table():
    """27 well-formed rows, shaped like the paper's height table."""
    rows = []
    for step in range(27):
        age = 5.0 + step * 0.5
        median = 108.9 + step * 2.9
        rows.append({
            "Age": f"{age:g}",
            "P3": round(median - 9.9, 1), "P10": round(median - 6.6, 1),
            "P25": round(median - 3.3, 1), "P50": round(median, 1),
            "P75": round(median + 3.5, 1), "P90": round(median + 7.0, 1),
            "P97": round(median + 10.5, 1), "SD": 5.7,
        })
    return rows


def _write(rows, directory):
    """These rows as a CSV, taking the column names from the rows themselves."""
    path = Path(directory) / "table.csv"
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


class ImportCase(SimpleTestCase):
    INDICATOR = "lhfa"

    def _run(self, rows):
        """
        Run the command over these rows, returning stdout. Whatever it wrote to
        stderr is left on ``self._stderr`` so a refusal can be inspected.
        """
        with tempfile.TemporaryDirectory() as directory:
            path = _write(rows, directory)
            out, errors = io.StringIO(), io.StringIO()
            try:
                call_command(
                    "import_iap", str(path), indicator=self.INDICATOR,
                    sex="boys", dry_run=True, stdout=out, stderr=errors,
                )
            finally:
                self._stderr = errors.getvalue()
            return out.getvalue()

    def _import(self, rows):
        return self._run(rows)

    def _refusal(self, rows):
        """
        The explanation printed when these rows are refused.

        The command raises to set a non-zero exit status and writes the detail —
        which row, which column, what was wrong — to stderr. It is the detail
        that makes a refusal actionable, so that is what these tests read.
        """
        with self.assertRaises(CommandError):
            self._run(rows)
        return self._stderr


class TestASoundTableIsAccepted(ImportCase):
    def test_a_well_formed_table_passes(self):
        self.assertIn("checks passed", self._import(_sound_table()))


class TestTransposedColumnsAreRefused(ImportCase):
    def test_two_swapped_columns_are_caught(self):
        rows = _sound_table()
        rows[8]["P25"], rows[8]["P75"] = rows[8]["P75"], rows[8]["P25"]
        self.assertIn("do not rise across the row", self._refusal(rows))

    def test_the_refusal_names_the_offending_age(self):
        rows = _sound_table()
        # Row 8 is the ninth half-year step from 5.0, so 9 years.
        rows[8]["P25"], rows[8]["P75"] = rows[8]["P75"], rows[8]["P25"]
        self.assertIn("age 9y", self._refusal(rows))


class TestADroppedDigitIsRefused(ImportCase):
    def test_a_digit_lost_mid_column_is_caught(self):
        rows = _sound_table()
        rows[10]["P50"] = 3.6  # was 136.9
        refusal = self._refusal(rows)
        self.assertIn("P50", refusal)
        self.assertIn("10y", refusal)

    def test_a_value_that_goes_backwards_with_age_is_caught(self):
        rows = _sound_table()
        rows[12]["P90"] = rows[11]["P90"] - 5
        self.assertIn("P90 falls", self._refusal(rows))


class TestTheShapeOfTheTableIsChecked(ImportCase):
    def test_a_short_table_is_refused(self):
        self.assertIn("expected 27 rows", self._refusal(_sound_table()[:20]))

    def test_a_table_starting_at_the_wrong_age_is_refused(self):
        rows = _sound_table()
        for row in rows:
            row["Age"] = f"{float(row['Age']) + 1:g}"
        self.assertIn("expected ages 5.0-18.0", self._refusal(rows))


class TestTheBmiCutoffCheck(ImportCase):
    """
    The strongest check available: the adult-equivalent lines must converge on
    23 and 27 by eighteen years, because that is how they are defined.
    """

    INDICATOR = "bmifa"

    def _bmi_rows(self, eq23_at_18=23.2, eq27_at_18=26.6):
        rows = []
        for step in range(27):
            fraction = step / 26
            rows.append({
                "Age": f"{5.0 + step * 0.5:g}",
                "P3": round(12.1 + fraction * 3.5, 1),
                "P5": round(12.4 + fraction * 3.8, 1),
                "P10": round(12.8 + fraction * 4.3, 1),
                "P25": round(13.6 + fraction * 5.3, 1),
                "P50": round(14.7 + fraction * 6.4, 1),
                "Eq23": round(15.7 + fraction * (eq23_at_18 - 15.7), 1),
                "Eq27": round(17.5 + fraction * (eq27_at_18 - 17.5), 1),
                "SD": 1.6,
            })
        return rows

    def test_a_sound_bmi_table_passes(self):
        self.assertIn("checks passed", self._import(self._bmi_rows()))

    def test_eq_lines_that_do_not_reach_the_adult_values_are_refused(self):
        # Still above the median at every age, so only the convergence check
        # can catch these — which is the check under test.
        refusal = self._refusal(self._bmi_rows(eq23_at_18=21.8, eq27_at_18=24.0))
        self.assertIn("converged on the adult cut-off", refusal)

    def test_an_eq27_below_eq23_is_refused(self):
        rows = self._bmi_rows()
        rows[5]["Eq27"] = rows[5]["Eq23"] - 1
        self.assertIn("P50 < 23-Eq < 27-Eq", self._refusal(rows))


class TestTheInstalledTablesStillPassTheirOwnChecks(SimpleTestCase):
    """
    The tables in the repository must satisfy the validation that admitted them.
    This is what would catch an edit made to them by hand.
    """

    def test_every_installed_table_revalidates(self):
        from growth.management.commands.import_iap import Command

        command = Command()
        for indicator in ("lhfa", "wfa", "bmifa"):
            for sex in ("boys", "girls"):
                path = ref.REFERENCE_DIR / "iap" / f"{indicator}_{sex}_5_18.json"
                with path.open() as fh:
                    rows = json.load(fh)
                problems = command._validate(indicator, rows)
                self.assertEqual(problems, [], msg=f"{indicator}_{sex}: {problems}")
