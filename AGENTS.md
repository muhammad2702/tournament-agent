# User working agreement and continuation rules

Read docs/CONTEXT.md, docs/ARCHITECTURE.md, README.md, the assignment PDF, and
the actual saved reports first. The implementation is unfinished.

- The user must explain/defend the code in an interview. Teach the MODULE/CLASS
  map first, then trace real data through small actual snippets. Explain unfamiliar
  names, input/output, why a design exists, and its limits. Avoid jargon and big
  unfocused dumps. Do not quiz the user before explaining unfamiliar architecture.
- Before a NEW stage: explain the exact proposed change and wait for explicit
  approval. Execute only that stage, show real evidence, then pause. The user
  explicitly stopped earlier unapproved edits. A question asking how code works
  is permission for read-only teaching, not edits/installations/model calls.
- Current GitHub/cloud migration and bootstrap are approved; no repeated migration
  questions. This does not authorize further features or paid model use afterward.
- No subagents unless explicitly requested.
- Real OpenRouter API only for model testing. Do not add FakeClient/simulated model
  responses. Legacy non-model fixture tests are not live model validation.
- Never print/commit keys. Use cloud secret OPENROUTER_API_KEY, supplied by user
  in settings, not chat. The Windows user-environment key does not travel to cloud.
- Assignment cap $5; gateway local ceiling $4.50. Preserve data/model_calls.json,
  its key identity, actual costs and reservations. At handoff: 2 calls/$0.0017948.
  Never reset/fork the ledger, switch keys to bypass the cap, or blindly retry
  an uncertain charge. Every paid translation/review/repair uses ModelGateway.
- Require explicit approval for the next live model call. Setup/CI must not run
  --test-api, --review-api, or RUN_LIVE_API_TEST=1 automatically.
- English units stop after language inspection: no translation/rebuilding/rendering.
- Four required demonstrations must use the full-corpus workflow, without hand-edited
  PDFs. Preserve math, numbering, scores, authors, diagrams and original sources.
- Keep the package small; no extra orchestration framework/services/database.
- Respect byte integrity: .gitattributes preserves data/prompts. paths.saved_path
  reads historical Windows separators without rewriting reports/caches.
- Download/API-response success is not semantic or visual correctness. Say which
  checks actually ran. Renderer/four final PDFs/complete archive processing are absent.
- Do not run the bare inventory CLI over the saved handoff without approval;
  it currently replaces downstream manifest metadata.

First cloud task: install listed dependencies if needed, run
`python -m tournament_agent.verify` from repo root, report verified state, and wait.
