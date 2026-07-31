# IAP 2015 reference tables

The Indian Academy of Paediatrics 2015 revised growth charts for Indian
children aged 5–18, from:

> IAP Growth Chart Committee. *Revised IAP Growth Charts for Height, Weight and
> Body Mass Index for 5- to 18-year-old Indian Children.*
> **Indian Pediatrics 2015;52:47–55.** Khadilkar V, Yadav S, Agrawal KK, et al.

Six files, one per indicator and sex, each 27 rows from 5.0 to 18.0 years at
half-year steps. Ages are stored as **months** so they share an x-axis with the
WHO and CDC tables.

| File | Indicator | Paper's table |
|---|---|---|
| `lhfa_boys_5_18.json` | Height for age | Table II |
| `wfa_boys_5_18.json` | Weight for age | Table III |
| `lhfa_girls_5_18.json` | Height for age | Table IV |
| `wfa_girls_5_18.json` | Weight for age | Table V |
| `bmifa_boys_5_18.json` | BMI for age | Table VI |
| `bmifa_girls_5_18.json` | BMI for age | Table VII |

## These are centiles, not LMS

Unlike the WHO and CDC tables, these carry **no L, M or S**. The committee
fitted LMS curves but published only the values, so a row looks like this:

```json
{"Month": 60.0, "P3": 99.0, "P10": 102.3, "P25": 105.6, "P50": 108.9,
 "P75": 112.4, "P90": 115.9, "P97": 119.4, "SD": 5.7}
```

and a BMI row substitutes the adult-equivalent cut-offs for the top columns:

```json
{"Month": 60.0, "P3": 12.1, "P5": 12.4, "P10": 12.8, "P25": 13.6,
 "P50": 14.7, "Eq23": 15.7, "Eq27": 17.5, "SD": 1.6}
```

`Eq23` and `Eq27` are **not centiles**. They are the BMI reaching an adult BMI
of 23 or 27 at eighteen years, and they define overweight and obesity. `SD` is
recorded to match the paper but is never used for scoring — see
`../SOURCES.md` for why, with the numbers.

## Reinstalling them

Point the command at the paper itself; it reads all six tables from the PDF's
text layer, so nothing is transcribed by hand:

```bash
python manage.py import_iap path/to/iap-2015.pdf            # writes all six
python manage.py import_iap path/to/iap-2015.pdf --dry-run  # checks only
```

A single table can also come from a CSV:

```bash
python manage.py import_iap height-boys.csv --indicator lhfa --sex boys
```

The command refuses to write anything it cannot vouch for, naming the offending
rows. It checks that the values rise across every row, that each column rises
smoothly with age, that there are 27 rows spanning 5.0–18.0, and — for BMI —
that `P50 < Eq23 < Eq27` throughout and that the two cut-off lines have
converged on the adult 23 and 27 by eighteen years. That last check is the one
that proves the columns were read correctly.

Do not edit these files by hand. `tests/test_iap_import.py` re-runs the same
validation against whatever is installed here, so an edit that breaks a curve
will fail the suite rather than quietly misplace a child on a chart.
