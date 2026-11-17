# Build Report

## Step 1 — Repository setup (awaiting review)

- Read the two-page ASE take-home assignment.
- Verified GitHub account muhammad2702 has write access to tournament-agent.
- Repository is public; user accepted public visibility.
- This workspace had no previous local repository; initialized main and origin.
- Prepared README.md, .gitignore and this report. No application code added.
- Model calls: 0. OpenRouter spend: $0.00 / $5.00.

## Assignment requirements

- Use a reusable agent with a local harness supporting filesystem access, code
  execution, package installation, model access, caching, checkpoints,
  validation and cost tracking; version prompts or instruction files.
- Inventory the full written-problem archive and automatically reconcile it.
- Map 8–9 classes / O-level to junior and 10–11 / A-level to senior;
  map basic / базовый вариант to basic and advanced / сложный вариант
  to advanced, checking classifications against sources.
- Demonstrate four sets: 18 October 2009 basic (junior and senior), and
  25 October 2009 advanced (junior and senior).
- Output PDFs beneath output/{junior,senior}/{basic,advanced}/.
- Preserve meaning, notation, numbers, conditions, numbering, points, authors
  and relevant diagrams; exclude solutions.
- Record ambiguous, duplicate and out-of-scope items.
- Disclose English editions used as controls and demonstrate model translation
  on at least one non-English source or controlled excerpt.
- Deliver source/output manifest with URLs, classifications, problem counts,
  hashes and status; document running, resuming, models and costs.
- Render every generated PDF; inspect all four examples page by page; compare
  at least one problem per source format against its original.
- Record model, call count and spend; enforce the $5 cap and protect the key.

## Next proposed step

Inspect the source index and identify the four demonstration sets, their formats
and source languages. Make no paid model calls.

## Scraper step — awaiting review

- Added downloader.py (HTTP), storage.py (original bytes and manifest), and
  scraper.py (archive link discovery and coordination). Standard library only.
- Downloads each URL without its fragment once per run; references retain anchors.
- Three attempts for transient HTTP/network failures, 30-second request timeout,
  one-second pacing for uncached downloads; hashes checked before cache reuse.
- Saves progress atomically after each resource and records download failures.
- Live index run: 1 downloaded resource, 106 additional archive URLs queued,
  347 link references recorded, 0 failures. Full crawl has not been run.
- Verified commented links are ignored, anchors retained, external/oral paths
  excluded from traversal, cache persists, and corrupted cache is rejected.
- Scope is numbered tournament directories; full inventory coverage is not yet
  established. Image dependencies and content extraction remain future work.
- Original downloads stay in ignored data/. No model calls; spend $0.00 / $5.00.

## Scraper QA — 17 November 2026

Ran `python -m unittest -v test_scraper`: 10 offline tests. Five passed;
five expected failures reproduce missing protections. Expected failures are
known defects, not a clean reliability result. No production code changed.

Verified: network failure recorded with nonzero failure count; transient network
errors attempted three times; 404 not retried; fragments retained with shared
resource deduplication; damaged cached bytes rejected.

Reproduced defects: empty index accepted; HTML error page with HTTP 200 accepted;
image dependencies omitted; Windows-1251 HTML without matching HTTP charset fails
despite its meta declaration; missing target anchors not detected.

Logging review: console prints and latest failure string in manifest only.
No persistent event log, timestamps, attempt history or traceback. A recovered
retry is invisible. A later success replaces the previous failure record.
HTML decode/discovery failures overwrite successful download metadata as failed.
Checkpoint and manifest-load errors are outside per-resource error handling and
terminate the run with a traceback.

Other limitations: scope excludes non-numbered archive paths; excluded links lack
reasons; redirects are not checked against traversal scope; no freshness checks
on cached pages; no PDF signature/content validation; no full-crawl completeness
reconciliation; no Retry-After handling; raw writes are not atomic; concurrent
runs can overwrite the manifest. --limit runs can exit successfully with pending
resources. Existing live verification covers only the index, not the full archive.

Priority fixes: response/discovery validation; persistent event logging and
separate download/discovery statuses; encoding detection; explicit pending and
exclusion reporting; dependency/anchor checks. Full crawl QA remains outstanding.

## Major fixes — awaiting review

- Reject empty responses and index pages without any archive links.
- Check PDF signature when the server declares application/pdf.
- Persist timestamped request attempts, retries, cache hits, failures with
  tracebacks and final counts in data/scraper.log.
- Read HTML charset declarations when HTTP charset is absent.
- Preserve download metadata on failure; explicitly show pending resource count.
- QA: 8 tests pass; 2 known gaps remain (images and missing anchors).
  Confirmed cached index run: 0 failures, 106 pending. No full crawl run.
- Error-page detection is limited to the index; arbitrary HTML responses still
  need content validation later. No paid calls or commits.

## Live download demonstration

Ran `python scraper.py --data-dir data/demo --limit 2` with an empty cache.
Downloaded the supplied index and /en/problems/48/index.php: 2 downloads,
0 cache hits, 0 failures; 105 other resources remain pending. Verified stored
bytes against manifest SHA-256 hashes. Originals and the run log are in data/demo/.

## Collection scope correction — awaiting review

Index is read for discovery only; no index HTML is saved to raw/. Select problem
HTML through section-anchor links, skipping bare tournament landing pages and
direct index PDF links for this HTML collection stage. Follow document links
from selected problem pages. Collect same-site img src and stylesheet href assets.
This does not yet handle CSS url(), srcset, or script-generated assets.
Save readable host/path filenames, with PHP pages stored as .html.
Live limited run in data/problem_html saved /en/problems/31/index.html and
/i/footer_mbg2.gif; other discovered assets remain pending due to the limit.
QA: 9 tests pass, one known missing-anchor check remains. Earlier demo directories
remain historical runs, not evidence of the revised collection scope.

## Review snapshot uploaded

All current source files and data directories are included for user review.
Historical demo runs are retained unchanged, including erroneous landing-page
and solution-PDF downloads. They are evidence of earlier mistakes, not accepted
outputs. data/problem_html is the latest limited HTML demonstration; it is not
a complete crawl and most related assets are still pending. No secrets are
included. Current QA has nine passing tests and one known expected failure.

## Repository organization

Application modules moved to scraper/; QA moved to tests/. All historical
downloads, manifests and logs moved unchanged to tests/artifacts/data/. Earlier
paths and commands in this report describe historical runs. Current commands:
python -m scraper --limit 2; python -m unittest discover -s tests -v.
