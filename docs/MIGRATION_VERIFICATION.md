# Migration verification

Local real-data verification on 18 November 2026:

```text
Original source hashes verified: 112
Manifest report hashes verified: 3
HTML question source ranges verified: 10
PDF question spans and source images verified: 14
Real model calls: 2; recorded spend USD: 0.0017948
Unresolved charges: 0
```

No source download, model request or report rewrite was performed by verification.
All Python module syntax was checked. Code modifications for the migration only
interpret old backslash paths portably and serialize new paths with forward slashes.
Original cached source, inspection, extraction, translation/review and cost records
were not rewritten. Git attributes preserve their bytes across platforms.

Linux verification runs in the attached cloud chat and the GitHub verification
workflow. This file records the local result; it does not pre-claim remote success.
The former simulated model API suite remains removed. Setup/CI must never set
RUN_LIVE_API_TEST=1 or supply a model key to run paid calls automatically.
