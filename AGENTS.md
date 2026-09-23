# AGENTS.md — SuperLocal Harness

These instructions apply to the whole repository.

## Scope

This repository is the small M0–M2 multi-model Harness, not the complete IRONMAN implementation. Preserve a thin, inspectable control plane. Do not silently add a vector database, distributed queue, browser automation, autonomous trading, public posting, or a large framework.

## Mandatory reads before a significant change

1. `CURRENT_STATE.md`
2. `docs/ARCHITECTURE.md`
3. `docs/SECURITY.md`
4. the relevant configuration/profile and tests

## Invariants

- SQLite mission state and the append-only event stream are authoritative; UI and provider sessions are projections.
- A model tool call is a proposal, never an authorization.
- Explicit user model choice cannot silently change providers.
- `Auto` routing and every fallback must remain observable.
- Local-only must be a hard pre-routing filter.
- Secrets stay in environment variables and never enter public config/API results.
- Every path is resolved under an allowed project root.
- File mutations and shell commands require one-time approval.
- Destructive commands, broker/trading actions and sealed Guanlan paths remain blocked.
- Executor output is not a verified outcome. Preserve planner/executor/verifier separation.
- The same model may be a last-resort verifier only when the audit stream shows reduced independence.
- Provider/model/tool adapters are replaceable; do not encode durable semantics in them.
- No migration may destroy mission history or silently reset event integrity.

## Change discipline

- Prefer a small standard-library implementation until evidence justifies a dependency.
- Add or update tests for policy, recovery, routing, budgets and state transitions.
- Never add a real key, token, user dataset or runtime database to the repository.
- Do not weaken a guardrail to make a model pass. Improve the adapter, decomposition or validation.
- New open-source dependencies require the admission analysis in `docs/OPEN_SOURCE_DECISIONS.md`.

## Acceptance

Run:

```text
python -m compileall -q superlocal_harness tests
node --check superlocal_harness/static/app.js
python -m unittest discover -s tests -v
```

Report changed files, commands, results, unresolved risks and any approval needed.

