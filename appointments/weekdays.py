"""
The day codes a recurring schedule is written in (KAN-22).

"M-W-F" is how the clinic writes a Monday/Wednesday/Friday clinic, on paper and
in the CSV template, so it is what the form and the importer both read. One
table here rather than one in each, because a code that means Tuesday in the
pop-up and Thursday in the spreadsheet is the kind of difference nobody spots
until a doctor is booked on the wrong day.

Note ``T`` is Tuesday and ``Th`` is Thursday, ``S`` is not a code at all, and
``Sa``/``Su`` are the weekend. That asymmetry is deliberate — it is the
convention the ticket specifies, and guessing "S" as either Saturday or Sunday
would put a clinic on the wrong day of the weekend.
"""

from datetime import timedelta

#: Code to Python weekday number (Monday = 0), in week order.
DAY_CODES = [
    ("M", 0, "Monday"),
    ("T", 1, "Tuesday"),
    ("W", 2, "Wednesday"),
    ("Th", 3, "Thursday"),
    ("F", 4, "Friday"),
    ("Sa", 5, "Saturday"),
    ("Su", 6, "Sunday"),
]

CODE_TO_WEEKDAY = {code: number for code, number, _name in DAY_CODES}
WEEKDAY_TO_CODE = {number: code for code, number, _name in DAY_CODES}
CODE_TO_NAME = {code: name for code, _number, name in DAY_CODES}

#: For a checkbox group, in the order a week is read.
CHOICES = [(code, name) for code, _number, name in DAY_CODES]

SEPARATOR = "-"

#: A range longer than this is a mis-keyed year, not a rota. Two years of a
#: five-day week is around 520 entries; past that something is wrong.
MAX_RANGE_DAYS = 750
MAX_GENERATED = 750


class BadDayCode(ValueError):
    """Raised with a message written for whoever filled the spreadsheet in."""


def parse_codes(raw):
    """
    Turn ``"M-W-F"`` into ``[0, 2, 4]``.

    Case-insensitive and tolerant of spaces, because a column typed by hand
    contains "m-w-f" and "M - W - F" as often as the documented form. Anything
    it cannot read raises rather than being skipped: a day code silently
    dropped is a clinic session that never appears and nobody is told about.
    """
    if raw is None:
        raise BadDayCode("No days given.")

    text = str(raw).strip()
    if not text:
        raise BadDayCode("No days given.")

    # Accept the comma and the slash too. They are what a spreadsheet produces
    # when somebody reformats the column, and refusing them helps nobody.
    for other in (",", "/", "+", " "):
        text = text.replace(other, SEPARATOR)

    wanted = []
    for piece in text.split(SEPARATOR):
        piece = piece.strip()
        if not piece:
            continue
        # Matched case-insensitively against the canonical spelling, so "TH",
        # "th" and "Th" are one day rather than three failures.
        for code in CODE_TO_WEEKDAY:
            if piece.casefold() == code.casefold():
                number = CODE_TO_WEEKDAY[code]
                if number not in wanted:
                    wanted.append(number)
                break
        else:
            raise BadDayCode(
                f"'{piece}' is not a day. Use "
                + SEPARATOR.join(code for code, _n, _d in DAY_CODES)
                + " — for example M-W-F, T-Th or Sa-Su."
            )

    if not wanted:
        raise BadDayCode("No days given.")
    return sorted(wanted)


def format_codes(weekdays):
    """``[0, 2, 4]`` back to ``"M-W-F"``, for showing what was understood."""
    return SEPARATOR.join(
        WEEKDAY_TO_CODE[n] for n in sorted(weekdays) if n in WEEKDAY_TO_CODE
    )


def dates_in_range(start, end, weekdays):
    """
    Every date between ``start`` and ``end`` inclusive falling on ``weekdays``.

    The whole point of the recurrence: reception says "Mondays and Wednesdays
    through September" and the system works out which dates those are, rather
    than somebody adding thirteen entries by hand and missing one.
    """
    wanted = set(weekdays)
    days = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() in wanted:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days
