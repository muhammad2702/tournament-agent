# Tournament of Towns agent — current workspace

This is the user's current implementation, cached sources, extraction evidence,
and real model-call ledger. It replaces the previous `scraper/` implementation
in this repository. **The assignment is unfinished; four final PDFs do not yet exist.**

Read [AGENTS.md](AGENTS.md), [complete context](docs/CONTEXT.md), and
[architecture](docs/ARCHITECTURE.md) before doing work. The user requires teaching
and approval before each subsequent implementation stage or paid API call.

## Cloud/Linux setup

Use Python 3.11+ and run from the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m tournament_agent.verify
```

Windows: use `.venv\Scripts\python.exe` instead. Verification reads real cached
data and existing cost records without model calls or source downloads. Historical
Windows paths are read through paths.py; Git attributes preserve exact data bytes.
See [cloud bootstrap](docs/CLOUD_START.md).
The [Linux verification run](https://github.com/muhammad2702/tournament-agent/actions/runs/44233715267)
passed. Cloud chat creation was blocked by the migration session's approval
policy; the [continuation prompt](docs/CLOUD_PROMPT.md) is ready for a new chat.

## Saved state

- Index + 38 archive pages = 39 HTML files; 255 webpage references (219 anchors).
- 99 PDF references, 73 unique downloaded PDFs. Completed download run: no failures.
- Inspection: 186 HTML sections, 190 PDF pages, 33 section errors. Some language
  decisions and classifications need review; these are not counts of validated sets.
- Required basic HTML sets: five questions each, scores/authors and original markup retained.
- Required advanced PDF: seven questions on each of two pages, raw spans/crops retained.
  Exponents and a complex underbrace are explicitly flagged for review.
- ONE basic junior question translated and bilingually reviewed through the real API.
- **Two real calls, $0.0017948 spent. Preserve data/model_calls.json across resumes.**

## Review artifacts

- [HTML questions](data/extracted/html_questions_review.html)
- [PDF question crops](data/extracted/pdf_questions_review.html)
- [Real translation](data/live_api_test.json)
- [Real bilingual review](data/live_api_review.json)
- [Assignment PDF](docs/ASE%20Take%20Home%20Assignment.pdf)

## Stage commands and side effects

```bash
# Rewrites inspection metadata and section fragments:
.venv/bin/python -m tournament_agent.cli --inspect
# Rewrites selected HTML question reports:
.venv/bin/python -m tournament_agent.cli --extract-html --anchor turnir31otm --anchor turnir31ots
# Rewrites selected PDF reports and source images:
.venv/bin/python -m tournament_agent.cli --extract-pdf --document-url https://www.turgor.ru/en/problems/31/os31sl.pdf

# Prepares statement inputs and checks the saved probe offline:
.venv/bin/python -m tournament_agent.cli --prepare-translation
```

`--prepare-translation` reads the saved extraction reports and writes
`data/prepared_translation.json` with source evidence, scores, authors,
and the exact translation requests. It also retains the reviewed English
statement for Junior Basic Q1. The source checks for the Advanced PDF are
recorded in `data/pdf_source_checks.json`.

## Preview, execute and inspect a set run

```bash
.venv/bin/python -m tournament_agent.run_sets
.venv/bin/python -m tournament_agent.render_sets  # Uses a saved translation run and XeLaTeX
```

The first command builds `data/run_plan.json` offline. It lists each statement,
translation request, and review template, with a SHA-256 and spending ceiling.
The review template receives the actual English after translation. In GitHub
Actions open **Tournament of Towns PDFs** and select **preview** to see the same
plan. After explicit approval, use **execute** with the preview's SHA-256. The
workflow reads `OpenRouterKey` as `OPENROUTER_API_KEY` and records reservations
and charges in the `openrouter-ledger` branch. Resume with the same plan and
ledger. If a reservation remains unresolved, inspect it before spending again.

The workflow provides `data/translation_run.json`, the reviewed PDFs in
`output/`, `output/manifest.json`, and a PNG for every PDF page in `qa/`. Each
`rendered_needs_visual_qa` PDF has passed the automated page and numbering checks
and still requires a visual check. Unsupported source layouts and unresolved
figures surface in the reports for review.

**Do not run the bare CLI over this handoff without approval:** its download
inventory stage currently recreates manifest.json, dropping later-stage pointers.

Live commands `--test-api --allow-paid` and `--review-api --allow-paid` require
separate approval and the SAME assignment key supplied as OPENROUTER_API_KEY in
cloud secret settings. No key is included in this repository. English units are
skipped after inspection; do not translate, rebuild or render already-English sets.

## Remaining work

Robust inventory reconciliation/solution exclusion, broader format support,
complete math/assets handling, set translation/review/repair, final PDF generation,
all-four visual QA, and final deliverables. The model gateway exists; the full
archive-to-output workflow does not. Read the exact limitations in CONTEXT.md.

## Tests

Simulated MODEL API tests were removed at the user's request. The real integration
test is opt-in via RUN_LIVE_API_TEST=1; leave that unset during setup. Legacy
source-parser/HTTP fixture tests remain and are not proof of full-archive fidelity.
