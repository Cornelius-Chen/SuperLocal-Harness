# Evidence-gated roadmap

## M0–M2 — delivered in this MVP

- model registry;
- visible static router and fallbacks;
- OpenAI-compatible DeepSeek/local gateway;
- health, timeout and usage accounting;
- local mission state, events, checkpoints and budgets;
- scoped tools and human approval;
- supervised plan/execute/verify loop.

Acceptance: tests pass; offline mission survives a full lifecycle; HTTP/UI works; event chain verifies.

## M3 — Context compiler

- typed Task Contract;
- token rather than character budgets;
- stable-prefix cache metrics;
- progressive file/evidence retrieval;
- deterministic compaction checkpoints;
- measure quality and cost against full-history baseline.

Do not implement if durable state plus recent context already meets the target cheaply.

## M4 — Worker adapters

- MCP tool client;
- ACP worker interface;
- admit one of Goose, Cline, OpenCode or OpenHands by benchmark;
- isolated git worktrees for parallel writers;
- max three workers and explicit leases.

## M5 — Independent verification

- protected verification packets;
- deterministic test evidence ingestion;
- verifier diversity and correlation tracking;
- retry/escalation policy based on bounded failures;
- rollback and last-known-good artifacts.

## M6 — Eval-based routing

- task-family benchmark corpus from real traces;
- quality, variance, latency, token cost and tool reliability;
- champion/challenger router in shadow mode;
- promote only if verified quality/HMVO improves without privacy regression.

## Guanlan specialization

- first-class BlindRun schema;
- immutable visible-packet hash;
- sealed outcome service unavailable to workers;
- clean I1/I2/I3 context namespaces;
- reproducible ledger validator;
- teacher-gap diagnostic that never feeds the next blind context.

