# Guanlan blind-research profile

The profile encodes the methodological boundary already established for Guanlan: teacher/optimal-path information is diagnostic only and must never enter a fresh blind iteration.

## Recommended run package

```text
runs/N2/I1/
  visible/
    data/
    rules/
    task_contract.json
  work/
    hypothesis.md
    ledger.csv
    report.md
  sealed/
    teacher/
    future/
    revealed_outcomes/
```

Point the mission at `runs/N2/I1/visible` or at a higher root with the profile enabled. Prefer an OS-level copy/sandbox that does not mount `sealed`; filename blocking is defense in depth, not perfect isolation.

## Required state before outcome access

Record through `update_state` and durable artifacts:

- hypothesis and economic/behavioral rationale;
- eligible universe and information cutoff;
- exact decision rule;
- position, execution and capital-reuse constraints;
- invalidation/failure conditions;
- primary metric, drawdown and benchmark;
- stop rule and maximum iterations.

## Iteration discipline

- I1/I2/I3 are separate fresh missions or clean worktrees.
- Do not inject prior revealed outcomes into the next worker context.
- The verifier may check reproducibility, leakage, accounting and rule adherence; it must not give the next worker the teacher path.
- Compare each iteration with its frozen baseline and explain the remaining gap after revelation.
- Keep failures. Do not rewrite the pre-registered thesis after seeing the window.
- No live broker authority. Shadow/paper evidence precedes any capital discussion.

## Current enforcement

The profile blocks path strings containing `teacher`, `sealed`, `future`, `answer_key`, `revealed`, `post_window` and `labels_future`. It also blocks broker/trading side-effect command patterns and routes the final result through a separate verifier role.

Future work should add OS/container mount isolation, content hashes for the visible packet and a first-class blind-run object linking I1/I2/I3 without sharing revealed context.

