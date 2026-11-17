# Tournament archive downloads

Download original HTML and non-English/uncertain-language PDFs from the supplied Tournament of Towns archive. No question extraction, translation or PDF generation.

## Run

```bash
python -m pip install -r requirements.txt
python -m scraper --output tests/artifacts/downloads
python -m unittest discover -s tests -v
```

Independent saved-file QA (includes image decoding):

```bash
python -m pip install -r requirements-qa.txt
python -m scraper.audit --output tests/artifacts/downloads
```

## Modules

- `discovery.py`: BeautifulSoup reads links only. Full source URLs keep fragments; download keys strip fragments while retaining queries. Follow the supplied index and directly linked written-archive pages, including the linked Russian archive index.
- `html_downloader.py`: save complete HTML bytes unchanged and collect static assets, including recursive CSS dependencies. It does not download document links.
- `pdf_downloader.py`: independently save original PDF bytes and verify each file opens and has pages. No HTML dependencies or text extraction.
- `downloader.py` and `storage.py`: shared HTTP/retries, logs, hashes, cache and atomic writes.
- `scraper.py`: coordinates the inventory and the two independent downloaders.
- `audit.py`: independently rechecks every saved file.

## Files for review

Under `tests/artifacts/downloads/`:

```text
inventory.json          Every discovered link, selection/exclusion and language evidence
errors.json             Failures, recovered retries and language uncertainties
audit.json              Independent check of every stored file
run.log                 Full collection log
discovery/              Cached index pages and discovery diagnostics
html/raw/               Original HTML and assets with readable host/path names
html/manifest.json      HTML/asset mapping, hashes and statuses
html/download.log       HTML phase log
pdf/raw/                Original PDF files
pdf/manifest.json       PDF mapping, hashes and page counts
pdf/download.log        PDF phase log
```

## Scope and language decisions

Menus, external archives, oral rounds, result lists and solutions are explicitly excluded with reasons. PDFs with explicit English filename markers are excluded. Other PDFs are retained; Cyrillic link labels are evidence for non-English selection, not a full automatic language diagnosis. `/en/` in a URL is never treated as language proof. Two uncertain-language HTML pages and four marking-scheme PDFs are retained for review.

Saving uses HTTP response bytes. BeautifulSoup only discovers links/assets; it does not interpret statements, mathematical notation, page layouts or section anchors. Original HTML references are unchanged; manifests map remote URLs to local files. Tracking is excluded and recorded. Static HTML/CSS dependencies are collected; remote JavaScript is not executed.

## Current verified result

88 HTML pages, 53 assets, 97 PDFs and 2 index snapshots saved. All 240 stored hashes pass; all PDFs open; all 47 images decode. No missing files, pending dependencies or unresolved download errors. 44 explicitly English PDFs excluded. 17 tests pass.

`errors.json` also records the initial HTTP 503 probe, one recovered retry, and the corrected resume-reporting defect. Earlier experimental logs are retained as history. No model calls or paid costs.

## Reproduce the exact source snapshots

`python -m tests.restore_downloads` restores missing raw files directly from their recorded source URLs, accepting only bytes matching the committed size and SHA-256. Existing correct files are reused. It never silently replaces the snapshot with changed source content. GitHub Actions runs the unit tests, this restore step, audits the result and commits restored raw files plus a restore report. The workflow uses GitHub's short-lived repository token; no credential is stored in code or logs.
