# Requirements document generator

`data.py` is the single source of truth for the requirements backlog. Everything
in `docs/` is generated from it, so the markdown, PDF and Word versions can never
disagree.

```bash
python tools/backlog/build_all.py
```

Produces in `docs/`:

| File | For |
|---|---|
| `REQUIREMENTS.md` | Reading in the repository, and diffing in pull requests |
| `Requirements-and-Backlog.pdf` | Sending to the clinic |
| `Requirements-and-Backlog.docx` | Marking up and commenting |

**Edit `data.py`, never the generated files.** When a story is finished, change
its `status` to `DONE`, list the tests that cover it in `tests`, and remove its
`gap` if it now has automated cover. Then re-run the command above and commit
all four files together.

The Word step needs Node and the `docx` npm package (`npm install docx` in this
directory). It is skipped with a warning if unavailable; the markdown and PDF
always build.
