# Build report

## Scope correction and rewrite

The earlier implementation incorrectly treated section-anchor problems as download failures. That was unnecessary for whole-page collection. The collector has been rewritten to save full HTML and static assets without interpreting problem sections. Anchor validation and duplicate-anchor diagnostics were removed from application code.

## Source inspection

Inspected all 31 source HTML files before rewriting. Pages 01–03 use the newer maintext/div layout; 04–31 use the older table layout. Text occurs in paragraphs, tables and preformatted blocks. Entire files are saved unchanged, so these layout differences do not require content extraction. Two stylesheets and shared graphics cover the layouts. Page 04 includes a separate problem-diagram image, which is saved and verified. Structure inspection: tests/artifacts/html_structure.json.

## Implementation

- Read the supplied index for discovery, without storing it as a problem page.
- Strip URL fragments, deduplicate webpage URLs, save each whole page once.
- Save directly referenced static assets and recursively follow CSS dependencies.
- Keep original bytes, readable host/path filenames, hashes, dependency references, logs and resume cache.
- Reject empty responses, wrong response types, redirects to a different webpage path, asset error pages and corrupted cache files.
- Retry transient network errors. Up to four download workers; file/manifest writes remain sequential.
- Never follow non-webpage document links. Earlier incorrect artifacts were removed from the current tree; earlier commits preserve history.

## Verified result

- 16 tests passed, no expected failures. Tests include missing assets, wrong response types, retries, cache corruption, CSS dependencies, gzip and exact preservation of HTML containing tables and preformatted maths.
- 31 webpages and 22 static assets saved; zero failures and zero pending downloads.
- Independently verified byte lengths and SHA-256 hashes of all 53 saved files.
- All 19 images decoded and verified with Pillow in the QA environment. Pillow is not an application dependency.
- Final live check fetched the index again and revalidated all previously downloaded pages/assets from cache.
- The final command exits zero and reports complete=true.
- Audit: tests/artifacts/validation.json; tests: tests/artifacts/qa_results.txt.

## Explicit limits

This is static collection, not execution of a remote website. Tracking is excluded and recorded; JavaScript-created resources are not audited. CSS discovery covers standard url() and quoted imports. Original HTML references remain unchanged for later processing through the manifest. The scraper log retains prior diagnostic mistakes; its last summary describes the rewritten collector. No model calls; spend $0.
