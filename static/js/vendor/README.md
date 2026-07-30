# Vendored front-end libraries

Served from this repository rather than a CDN, deliberately:

* The clinic's internet connection must never be a prerequisite for opening a
  patient's file.
* No third-party host is contacted while confidential patient data is on screen.
* The exact reviewed version is what ships — a CDN cannot silently change it.

| File | Library | Version | Licence |
|---|---|---|---|
| `htmx.min.js` | [htmx](https://htmx.org) | 2.0.4 | BSD-2-Clause |
| `chart.umd.js` | [Chart.js](https://www.chartjs.org) | 4.4.7 | MIT |

To upgrade, fetch the new release from the npm registry and replace the file,
then update the version in this table.
