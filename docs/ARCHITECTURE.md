# Architecture

## Thesis

The Harness is a thin owner-controlled control plane, not the final IRONMAN system and not a new monolithic agent framework. Its first job is to prove M0–M2 from the existing multi-model specification:

1. model profiles and an inspectable static router;
2. one provider-neutral gateway;
3. token/cost ledger, permissions and resumable mission state.

It adds a deliberately small mission loop so the gateway can be evaluated under realistic use.

## Planes

```text
Browser UI (projection, never source of truth)
                |
Mission service + Context compiler
                |
      Policy / Approval broker
                |
Model router ----+---- Tool executor
      |                    |
OpenAI-compatible APIs     Scoped files/git/shell
      |
DeepSeek / Ollama / vLLM / LiteLLM

Authoritative local state: SQLite + hash-chained events + project Git
```

## Durable state

- `missions`: explicit state machine, budget, stage and selected/actual models.
- `mission_state`: compact plan/facts/decisions/hypotheses/checks outside chat.
- `messages`: replayable provider-neutral transcript and tool results.
- `events`: append-only hash chain for material decisions and transitions.
- `approvals`: proposed side effects and one-time human decisions.
- `usage`: per-call tokens, latency, model and configured cost.
- `checkpoints`: state snapshots at stage, approval and completion boundaries.

On restart, `running` missions become `queued` and resume. Absence of an error is never interpreted as success.

## Mission lifecycle

```text
created
  -> planning
  -> execution <-> waiting_approval
  -> verification
       -> completed
       -> one bounded repair -> verification
       -> needs_review
```

Terminal safety states include `failed`, `cancelled` and `budget_exhausted`.

## Model routing

An explicit user choice never changes provider automatically. `Auto` is a visible deterministic policy:

- routine: local small → DeepSeek Flash → local coder → reasoner;
- coding: local coder → DeepSeek Flash → reasoner;
- hard reasoning, blind research, planning or verification: reasoner → local coder → Flash;
- local-only: candidates outside the local boundary are removed before execution.

Endpoint failures may use the displayed fallback list and emit `ModelFallbackUsed`. A learned router is prohibited until static routing has enough task-family traces and a held-out evaluation.

## Context control

Every model call receives:

- the stable constitutional worker prefix;
- current role and runtime profile;
- the compact durable mission state;
- the most recent messages that fit the profile's character budget.

Older chat turns can be omitted, but mission state is never inferred from omission. `update_state` is the bridge from transient reasoning to durable state.

## Replaceable boundaries

- Model APIs: OpenAI-compatible adapter today; provider-specific adapters later only when required.
- Tool connectivity: internal tools today; MCP adapter next.
- External agents: native loop today; ACP adapters for Goose/Cline/OpenCode/OpenHands later.
- Orchestration: explicit Python state machine today; LangGraph/Temporal only if measured failure/recovery needs justify them.
- Search/memory: SQLite and lexical file tools today; embeddings/graphs remain non-authoritative projections.

