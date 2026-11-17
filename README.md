# Tournament webpage scraper

Download whole problem webpages and their static assets. No question parsing or section interpretation. Python 3.10+; standard library only.

```bash
python -m scraper --data-dir tests/artifacts/webpages
python -m unittest discover -s tests -v
```

- `scraper/`: download, asset discovery, file storage and CLI.
- `tests/`: offline tests.
- `tests/artifacts/webpages/raw/`: original HTML, images, stylesheets and scripts.
- `tests/artifacts/webpages/manifest.json`: source-to-file mapping, dependency relationships, hashes and statuses.
- `tests/artifacts/webpages/scraper.log`: timestamped history; the last summary is the latest run.
- `tests/artifacts/validation.json`: independent saved-file audit.
- `tests/artifacts/html_structure.json`: inspected HTML structures for all 31 pages.

The supplied index is used only for discovery. Links to problem sections identify the containing webpages; URL fragments are removed for downloading. Each page is saved once, unchanged. Bare tournament landing pages and non-webpage documents are not collected. The first tournament has inline questions without section links and is included explicitly.

Static dependencies include HTML image/script/stylesheet references, srcset, inline CSS, and recursive CSS imports/images/fonts. Embedded assets need no download. Tracking is excluded with a recorded reason. Remote JavaScript is not executed. Original remote references remain unchanged; the manifest maps them to local files.

Verified: 31 webpages, 22 static assets, zero failures or pending downloads. All 53 stored hashes checked and 19 images decoded. No document downloads, model calls or costs.
