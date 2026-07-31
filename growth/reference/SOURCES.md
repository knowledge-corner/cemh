# Growth reference data

These tables are the growth references the charts are plotted against. They are
**real published reference data, not generated values.** Do not edit them by
hand, and do not replace them with approximations.

## Two kinds of reference

The application distinguishes these, and reports which one produced every
number it shows. The difference is not cosmetic — it changes what can honestly
be said about a child.

| Kind | Sources | What it publishes | What can be computed |
|---|---|---|---|
| `lms` | WHO, CDC | L, M and S per age | Any percentile, exactly; an exact z-score |
| `centiles` | IAP | The centile curves themselves | The band a child falls between, and an SDS interpolated between two printed curves |

A centile reference cannot produce a number for a child outside its outermost
printed curve, and the application does not invent one — see
`growth/centiles.py`. Where that happens, `assess()` supplies a z-score from an
LMS reference alongside, labelled with the source it came from.

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

Each WHO and CDC row carries the **L, M and S** parameters of the LMS
distribution for that age.

### `iap/` — IAP 2015 revised charts, 5 to 18 years

| File | Indicator | Paper's table |
|---|---|---|
| `lhfa_boys_5_18.json` | Height-for-age | Table II |
| `wfa_boys_5_18.json` | Weight-for-age | Table III |
| `lhfa_girls_5_18.json` | Height-for-age | Table IV |
| `wfa_girls_5_18.json` | Weight-for-age | Table V |
| `bmifa_boys_5_18.json` | BMI-for-age | Table VI |
| `bmifa_girls_5_18.json` | BMI-for-age | Table VII |

27 rows each, 5.0 to 18.0 years at half-year steps, stored as months to match
the other tables. Height and weight carry `P3, P10, P25, P50, P75, P90, P97`;
BMI carries `P3, P5, P10, P25, P50, Eq23, Eq27`.

## Provenance

**WHO and CDC** — extracted from [pygrowup](https://pypi.org/project/pygrowup/)
0.8.2, which redistributes the published tables. Copyright UNICEF and individual
contributors, BSD licence — see `LICENSE.pygrowup`. The underlying standards are
published by the World Health Organization and the US Centers for Disease
Control and Prevention respectively.

**IAP** — IAP Growth Chart Committee. *Revised IAP Growth Charts for Height,
Weight and Body Mass Index for 5- to 18-year-old Indian Children.* Indian
Pediatrics 2015;52:47–55 (Khadilkar V, Yadav S, Agrawal KK, et al.). Extracted
from the paper's own text layer with `python manage.py import_iap <paper>.pdf`
rather than transcribed, and validated on the way in.

### Two things to know about the IAP tables

**1. No LMS parameters are published.** The committee built the curves with
Cole's LMS method but printed only the resulting values, so no exact z-score can
be computed from them. Back-fitting L, M and S to seven points would reproduce
the published curves only approximately while reporting a z-score that reads as
exact — so it is not done.

**2. The `SD` column is recorded but never used for scoring.** It is the raw
sample standard deviation, not a parameter consistent with the skewed centiles
printed beside it. Measured against the paper's own numbers, `(value − P50)/SD`
puts a child sitting exactly on the printed 97th centile at **+2.2 to +3.4 SDS**
instead of +1.88 — worst on weight, where the distribution is most skewed. The
SDS the application reports is interpolated between the printed centiles
instead. The column is kept only so the files match the paper.

### BMI: the `Eq23` and `Eq27` columns are not centiles

They are the BMI which, followed along a child's own curve, reaches an adult BMI
of **23** (overweight) or **27** (obesity) at eighteen years — the paper's
recommended cut-offs for Asian children, who carry more adiposity and more
cardio-metabolic risk at a lower BMI than the adult 25 and 30 assume. They are
age- and sex-specific: the 23-equivalent line is 15.7 kg/m² for a five-year-old
boy and 23.2 at eighteen. `growth/bmi.py` uses them; `growth/centiles.py`
deliberately excludes them from percentile placement.

## Coverage, and what happens outside it

| Age | Standard `IAP` | Standard `WHO` | Standard `CDC` |
|---|---|---|---|
| 0–2 y | WHO | WHO | WHO |
| 2–5 y | WHO | WHO | CDC |
| 5–18 y | IAP | CDC | CDC |
| 18–20 y | CDC | CDC | CDC |
| Over 20 y | no chart | no chart | no chart |

A child of exactly 5.0 years falls in both the WHO and the IAP band; IAP
answers, which is where the IAP charts begin. Head circumference is published
only by WHO, to 5 years, so it is never charted above that.

Every fallback is reported in the interface under the chart it produced. A
missing chart is better than a wrong one, so an age no table covers produces no
chart at all rather than an extrapolation.

## Still missing

**WHO 5–19 year reference (2007).** This publishes LMS for school-age children
and adolescents, and would be a better companion than CDC for a child who falls
off the bottom of the IAP scale — it is reference [7] of the IAP paper itself.
Adding it is a drop-in: place the files here in the LMS row format, add the band
to `_BANDS` in `growth/reference.py`, and put it ahead of `cdc` in
`_COMPANION_SOURCES`.
