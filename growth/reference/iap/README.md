# IAP 2015 reference tables — not yet installed

This directory is where the Indian Academy of Paediatrics 2015 revised growth
charts go. **It is deliberately empty.** The application runs without them and
falls back to CDC above five years, labelling every chart with the source that
actually produced it.

The tables have not been invented or approximated, and must not be. A wrong
L, M or S value moves a child's centile, and a centile is what a short-stature
diagnosis turns on.

## What is needed

Six files, in the same row format as `../who/` and `../cdc/`:

| File | Indicator |
|---|---|
| `lhfa_boys_5_18.json`, `lhfa_girls_5_18.json` | Height for age |
| `wfa_boys_5_18.json`, `wfa_girls_5_18.json` | Weight for age |
| `bmifa_boys_5_18.json`, `bmifa_girls_5_18.json` | BMI for age |

Each is a JSON list of rows, one per month of age from 60 to 216:

```json
[{"Month": "60", "L": -0.123, "M": 16.4, "S": 0.121}, …]
```

## Installing them

Ask the treating doctor for the IAP 2015 LMS tables — they are published with
the charts (Khadilkar et al., *Indian Pediatrics* 2015). A spreadsheet or CSV
per indicator and sex is ideal.

```bash
python manage.py import_iap --indicator lhfa --sex boys path/to/height-boys.csv
```

The command validates the numbers before writing: if the source includes any
published centile columns, the LMS values must reproduce them, or the import is
refused. That is the same check the WHO tables in this repository already pass.

Once all six are present, nothing else needs changing — `GROWTH_REFERENCE` is
already set to `IAP`, and charts above five years will switch over on the next
request.
