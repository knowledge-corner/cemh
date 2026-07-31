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

## Strip the source-map comment

npm builds end with a line like:

```js
//# sourceMappingURL=chart.umd.js.map
```

**Delete it.** The `.map` file is a development aid worth about a megabyte and
is not shipped, so the comment points at nothing. In production
`CompressedManifestStaticFilesStorage` rewrites URL references inside JS and
refuses to resolve a file that is missing, so leaving the line in fails
`collectstatic` — and therefore the whole Docker build — with `MissingFileError`.

That strictness is worth keeping rather than switching off: it is the same check
that would catch a stylesheet referencing a font or image nobody committed.
