# Cloud bootstrap

Repository: https://github.com/muhammad2702/tournament-agent
Branch: master

Read AGENTS.md and docs/CONTEXT.md. Cached sources and paid ledger are part of
the handoff; do not recreate the project or reset costs.

If no checkout is attached:

```bash
git clone https://github.com/muhammad2702/tournament-agent.git
cd tournament-agent
git log -1 --oneline
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m tournament_agent.verify
```

The user approved cloning, installing these dependencies and offline verification
for this migration. Do not run live model commands or new feature stages. Leave
RUN_LIVE_API_TEST unset. After verification, report state and wait for the user.

No API key is included. For a later approved call, user supplies the SAME assignment
key as OPENROUTER_API_KEY through Cloud runtime secret settings. Do not ask for it
in chat or create a new key to bypass the cap. Windows user environment is local.

Later source/API access needs www.turgor.ru and openrouter.ai; setup needs GitHub
and the configured Python package registry. Respect proxy/TLS/network policies.
