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

Published workspace commit: `674c9db886b973e505c6eef8a35080f5fc622b7f`.
All 477 repository files matched the local Git blob hashes before publication.
The previous implementation was replaced on master with a fast-forward commit;
its history was retained. No other repositories were changed.

Linux/Python 3.12 verification passed in GitHub Actions, including dependency
installation and the saved-data verifier:
https://github.com/muhammad2702/tournament-agent/actions/runs/44233715267

Creating the requested Codex Cloud chat was attempted but blocked by the tool:
"MCP tool call requires approval, but approval policy is never". No cloud chat
was created, so interactive Cloud bootstrap has not been verified. This is a
session permission blocker, not a verifier failure. See CLOUD_PROMPT.md for the
ready-to-use continuation prompt; do not claim the chat already exists.

The former simulated model API suite remains removed. Setup/CI must never set
RUN_LIVE_API_TEST=1 or supply a model key to run paid calls automatically.
