# Tournament Agent

Agent and local harness for the Tournament of Towns archive.

## Goal

Generate English PDFs for written problem sets, preserving mathematical meaning,
metadata and relevant diagrams while excluding solutions.

Source index: https://www.turgor.ru/en/problems/allproblems.php

## Build rules

- Work in small steps; review each step before committing it.
- Use OpenRouter with a hard total spending cap of $5.
- Never put API keys in code, logs, PDFs or Git history.
- No paid calls until budget enforcement is implemented and reviewed.

## Status

First scraper implementation, awaiting review. No model calls.

## Run the scraper

Python 3.10+; no third-party dependencies.

```bash
python scraper.py --limit 1   # Download the index and inventory its links
python scraper.py             # Follow archive links; may download many files
```

Original bytes go in data/raw/ using URL hashes as filenames. data/manifest.json
records source links, labels, section anchors, response types, content hashes and
download statuses. Rerun the command to reuse verified cached files and retry
failures. Progress is saved after each resource.

Current scope: numbered tournament paths under /en/problems/ and /problems/ on
turgor.ru. Other links are recorded but not followed. This is a collection
prototype, not yet a complete archive inventory: image dependencies, content
classification, solution filtering and question extraction are not implemented.
