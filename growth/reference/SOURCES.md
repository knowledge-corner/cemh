# Growth reference data

These tables are the growth references the charts are plotted against. They are
**real published reference data, not generated values.** Do not edit them by
hand, and do not replace them with approximations.

## What is here

### `who/` — WHO Child Growth Standards (2006), birth to 5 years

| File | Indicator | Range |
|---|---|---|
| `lhfa_{boys,girls}_0_5.json` | Length/height-for-age | 0–60 months |
| `wfa_{boys,girls}_0_5.json` | Weight-for-age | 0–60 months |
| `hcfa_{boys,girls}_0_5.json` | Head circumference-for-age | 0–60 months |
| `bmifa_{boys,girls}_0_2.json` | BMI-for-age | 0–24 months |
| `bmifa_{boys,girls}_2_5.json` | BMI-for-age | 24–60 months |

### `cdc/` — CDC Growth Charts (2000), 2 to 20 years

| File | Indicator | Range |
|---|---|---|
| `lhfa_{boys,girls}_2_20.json` | Stature-for-age | 24–240 months |
| `wfa_{boys,girls}_2_20.json` | Weight-for-age | 24–240 months |
| `bmifa_{boys,girls}_2_20.json` | BMI-for-age | 24–240 months |

Each row carries the **L, M and S** parameters of the LMS distribution for that
age, which is what `growth/reference.py` uses to convert a measurement into a
z-score and a percentile.

## Provenance

Extracted from [pygrowup](https://pypi.org/project/pygrowup/) 0.8.2, which
redistributes the WHO and CDC published tables. Copyright UNICEF and individual
contributors, BSD licence — see `LICENSE.pygrowup`. The underlying standards are
published by the World Health Organization and the US Centers for Disease
Control and Prevention respectively.

## Gaps that must be closed before clinical use

> **Two things are missing, and both are clinical decisions for the doctor —
> not technical ones.**

1. **WHO 5–19 year reference (2007) is not included.** The only data here
   covering school-age children and adolescents is CDC. A clinic that wants WHO
   growth references above 5 years must add those tables.

2. **IAP 2015 charts are not included.** The Indian Academy of Paediatrics
   published revised growth charts for Indian children aged 5–18 in 2015, and
   many Indian paediatric endocrinologists prefer them to WHO or CDC because
   they reflect the Indian population. These tables have to be sourced
   separately.

Adding either is a drop-in: place the files in a new directory here in the same
row format (an `L`, `M`, `S` and `Month` key per row), register it in
`growth/reference.py`, and set `GROWTH_REFERENCE` in `config/clinic.py`.

**Ask the doctor which standard they want to chart against before this goes
into clinical use.** Charting an Indian child against the wrong reference will
misplace them on the percentile curve.
