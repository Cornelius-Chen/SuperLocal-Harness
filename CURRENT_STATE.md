# Current state — v0.1.0

Date: 2026-09-23 (public source release; original MVP completed 2026-09-17)

## Outcome

The first usable Harness slice is implemented. It provides one local browser UI over a provider-neutral gateway, persistent mission state, explicit static routing, approvals, event integrity, budgets and supervised verification.

## Verified now

- Python syntax compilation passes.
- Browser JavaScript syntax check passes.
- Automated tests cover configuration, local-only routing, event-chain tamper detection, project/sealed-path controls, dangerous/trading command blocks, durable state, offline supervised mission completion, one-time mutation approval, HTTP health/config and UI DOM references.
- The offline model completes `planning → execution → verification → completed` without external calls.
- An approved write is absent before approval and present only after approval.
- Mission event chains verify after both paths.
- Windows test cleanup now closes SQLite connections and waits for active workers; the complete offline test suite passes.

## Runtime entry

```text
scripts/setup.ps1
scripts/run-local.ps1
```

Then open `http://127.0.0.1:8765`.

## Intentional limits

- No MCP client or ACP worker adapter yet.
- No worktree isolation for parallel writers.
- No learned router or model benchmark corpus yet.
- No live browser/computer-use tool.
- No public posting or trading authority.
- UI visual rendering must be checked once on the target Windows machine; the current build environment validated HTTP/DOM/JavaScript but did not expose its localhost to the available screenshot browser.

## Next admitted task

Install on the MSI, set its real project roots and model tags, run `doctor.ps1`, then record a small benchmark across:

1. local fast extraction;
2. local coder bounded edit;
3. DeepSeek Flash ordinary tool task;
4. DeepSeek Reasoner verification;
5. one Guanlan sealed-path denial and clean blind run.

Do not start M3/M4 until this evidence shows where context compilation or an external worker genuinely improves verified quality, cost or human time.
