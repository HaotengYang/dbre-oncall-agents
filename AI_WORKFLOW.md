# AI workflow

## What the project is and why

A MySQL on-call diagnosis system: it collects read-only evidence, names a root
cause from a closed set, explains it, quotes runbook steps, and rehearses an
index change on a sample copy before any real table is touched. An eval suite
injects real faults into a lab MySQL and scores the answers. The team I applied
to runs databases with production as its only environment and is building a
multi-agent diagnosis system; this is a small, measurable version of both.

## Tools

- **Claude Code** was the development interface for all code, tests and docs.
- **Three subagents** in `.claude/agents/`: `explorer` (research, proposals; no
  edit tools), `implementer` (builds a confirmed proposal), `reviewer` (fresh
  context, correctness and safety only, no edit tools, so it cannot refactor).
- **Git worktrees**: two implementers built the SQL and metrics agents in parallel.
- **`scripts/verify.sh`** (the `verify` skill): lab up, unit tests, quick evals.
- **Outside Claude Code**: during design review and fact-checking I used a
  separate Claude chat session as a second opinion, and Grok to collect public
  incident write-ups, each checked by hand. I decided what was adopted.

## How I worked with the AI

- A proposal with checkable completion criteria first; code only after I approved it.
- Verification before the thing verified: lab, fault injector, evals, then agents.
- Eval cases and thresholds may not be changed by the AI without my consent.
- The rule engine was frozen (tag `rule-engine-frozen-v1`, hash test) before
  the holdout cases were designed by the explorer without sight of its code.
- Review limited to two rounds for ordinary modules; the read-only evidence
  collectors and the change safety gate were reviewed until approved.

## How I checked AI-written code: four real examples

1. "Plain EXPLAIN only" was not safe: measured, it still ran `SLEEP(3)` in WHERE
   (3.0 s) and a subquery in FROM. The check became an allowlist.
2. `clear` stopped a running injector by signalling a process id read from a
   file. Three review rounds each found a way to hit the wrong process; the
   signalling was removed and replaced by a stop-request file.
3. During a table lock the workload reconnected while blocked sessions stayed:
   6 to 18 sessions in 100 s, a fake connection-exhaustion symptom.
4. The change gate's sample copy ran at the default isolation level and took
   shared locks on production rows; it now copies with a lock-free read.

## Results

- Rule engine, end to end: basic 6/6, hard 5/8 and 6/8 in two runs, holdout 1/3,
  no false positives. Rules and original cases share an author: 6/6 measures little.
- Incident replays: GitHub was a hit; Honeycomb exposed a crash on a query
  timeout, fixed as error handling, so it is no longer a blind test.
- Model replay, 24 cases, the same recorded evidence for every backend (all runs
  counted; false positives 0 on every tier for every backend):

| Backend | Basic | Hard | Holdout | Incidents | Stable | Time | Spent |
|---|---|---|---|---|---|---|---|
| Rule engine | 100% (6/6) | 75% (6/8) | 33% (1/3) | 100% (2/2) | 1 run | <0.1 s | 0 |
| Haiku 5.5, 3 runs | 100% (18/18) | 75% (18/24) | 78% (7/9) | 100% (6/6) | 96% (23/24) | 2.8 s | 0.04 USD |
| Sonnet 5.5, 1 run | 100% (6/6) | 75% (6/8) | 67% (2/3) | 100% (2/2) | 1 run | 2.9 s | 0.20 USD |
| Opus 5.5, 1 run | 100% (6/6) | 75% (6/8) | 67% (2/3) | 100% (2/2) | 1 run | 3.6 s | 0.43 USD |

  Gemini 3.8 Flash was skipped (timeouts, then rate limits; no answer). Ledger:
  1.05 of 10 USD, 0.39 of it Gemini calls charged at worst case. The models
  matched the rules everywhere except the holdout set (2 of 3 hits or 7 of 9
  against 1 of 3); with three holdout fault cases that gap is small evidence.

## Mistakes the AI made, and how they were found

- It first misread the role as Data Engineer; I corrected it from interview notes.
- It merged a branch into `main` without my approval, then undid it before
  pushing and told me; merging now needs my explicit approval (`CLAUDE.md`).
- It reported a run whose patch had not applied; caught by reading the output.
- It wrote "degraded RDS hardware" as the Honeycomb cause; a source check
  could not confirm it, and the claim was removed.
- Tests that could not fail were found by the reviewer and rewritten.

## Known limits

One root cause per diagnosis; a small lab; synthetic fault scenarios; holdout
and incident sets of 4 and 2 cases. Full list: `docs/KNOWN_ISSUES.md`.
