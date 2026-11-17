# Tournament Agent

Scraper prototype for the Tournament of Towns assignment. Python 3.10+; no external dependencies. No model calls yet; OpenRouter spending remains $0 of the $5 cap. Never put keys in code or logs.

## Layout

- `scraper/`: HTTP downloading, storage and archive collection.
- `tests/`: offline QA tests.
- `tests/artifacts/data/`: original live-run samples, manifests and logs for review.
- `BUILD_REPORT.md`: implementation history, defects and limitations.

## Run

```bash
python -m scraper --limit 2
python -m unittest discover -s tests -v
```

Runtime output defaults to `data/`. The index is used for discovery; selected problem HTML and related files are saved with readable host/path filenames. Progress and failures are recorded in the manifest and log.

## Review artifacts

Under `tests/artifacts/data/`:
- Root: earliest index-only run.
- `demo/`: earlier index and landing-page run (wrong scope).
- `problem_demo/`: historical PDF run, including an unwanted solutions PDF.
- `problem_html/`: limited 2009 HTML demonstration and one asset; most dependencies remain pending.

These incomplete historical snapshots expose earlier mistakes. They are not finished assignment outputs. Remaining limitations include missing-anchor validation, CSS-embedded dependencies, and incomplete archive coverage.
