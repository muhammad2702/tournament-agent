# Tournament of Towns English PDF agent

The four demonstration sets are generated and page-checked. They contain 24
questions and the Senior Basic section note. Every PDF has one page.

| Set | Date | Questions | PDF |
| --- | --- | --- | --- |
| Junior Basic | 18 October 2009 | 5 | [Download](output/junior/basic/2009-10-18.pdf) |
| Senior Basic | 18 October 2009 | 5 + section note | [Download](output/senior/basic/2009-10-18.pdf) |
| Junior Advanced | 25 October 2009 | 7 | [Download](output/junior/advanced/2009-10-25.pdf) |
| Senior Advanced | 25 October 2009 | 7 | [Download](output/senior/advanced/2009-10-25.pdf) |

The [source and output manifest](output/manifest.json) records URLs, hashes,
classifications, counts, and statuses. The [QA note](qa/QA.md) records the
source comparisons, page checks, and unresolved archive layouts.

## Run locally

Use Python 3.11+, XeLaTeX, and DejaVu Serif. Run from the project root.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m tournament_agent.main
```

The main program checks the saved delivery without network or model calls.
On a fresh checkout without a saved run, it inventories and extracts sources,
then writes a translation plan. Paid calls require the approved plan in the
GitHub Actions workflow.
[Architecture](docs/ARCHITECTURE.md) · [Project history](docs/CONTEXT.md)

## Reproduce the four PDFs offline

The saved live responses are in [translation_run.json](data/translation_run.json).
The renderer applies [source-bound QA decisions](data/qa_decisions.json) to
those saved responses and typesets the PDFs with XeLaTeX.

```bash
.venv/bin/python -c "from pathlib import Path; from tournament_agent.verification import render_main; raise SystemExit(render_main(output=Path('reproduced'), qa_dir=Path('reproduced-qa')))"
```

This writes four PDFs and renders a PNG for each page. Fresh exports carry
`rendered_needs_visual_qa` until someone inspects their pages. The checked
submission identifies the exact original PDF and page image hashes in
[visual_checks.json](qa/visual_checks.json).

## Live translation and resume

The manually triggered GitHub Actions workflow has an offline `preview` mode
and a paid `execute` mode. Execute requires the approved plan hash and the
assignment key, supplied as the `OpenRouterKey` repository secret. The runner
passes it to the gateway as `OPENROUTER_API_KEY`. Completed matching requests
can be reused on resume. The `openrouter-ledger` branch is the canonical
accounting record once a live run begins. An unresolved reservation stops
new spending until its accounting can be reconciled.

The demonstration used Gemini 2.5 Flash-Lite for translation and Gemini 2.5
Flash for bilingual review. The live run added **48 calls and $0.0559729**.
Including the saved two-call probe, the ledger totals **50 calls and
$0.0577677**. The provider key cap was $5, with a $4.50 local ceiling.
[Live run](https://github.com/muhammad2702/tournament-agent/actions/runs/44257730151) · [Accounting checkpoint](https://github.com/muhammad2702/tournament-agent/blob/3fdde00532598a14f86c4755ec579173b15514fd/data/model_calls.json)

## Archive scope

The manifest accounts for 255 HTML references and 99 PDF references to 73
distinct document URLs. The offline audit tested the existing extractors on
19 HTML sections and 42 PDF pages classified ready by inspection. Seventeen
HTML results and 27 PDF results still need review. Layouts without a reliable
question boundary, scanned PDFs, cross-page questions, and unresolved figure
placement require additional adapters or source review. Confident English
units are recorded and left in the original form. [Archive audit](qa/archive_audit.json)
