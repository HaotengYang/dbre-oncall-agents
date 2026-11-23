# AI workflow

## What the project is and why

A MySQL on-call diagnosis system: when the database misbehaves it collects
read-only evidence, names a root cause from a closed set (missing index, lock
wait, connection exhaustion, healthy, undetermined), explains it, quotes
runbook steps, and rehearses an index change on a sample copy before any real
table is touched. An eval suite injects real faults into a lab MySQL and
scores the answers. I chose it because the team I applied to runs database
operations with production as its only environment and is building a
multi-agent diagnosis system; this is a small, measurable version of both.

## Tools

- **Claude Code** was the development interface for all code, tests and docs.
- **Three subagents** in `.claude/agents/`: `explorer` (research, proposals; no
  edit tools), `implementer` (builds a confirmed proposal), `reviewer` (fresh
  context, correctness and safety only, no edit tools, so it cannot refactor).
- **Git worktrees**: the SQL agent and the metrics agent were built in parallel
  by two implementers in separate worktrees from a contract committed first.
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

1. The design said "plain EXPLAIN only". Measured on the lab server, plain
   EXPLAIN still executed a function in WHERE (`id = 1 AND SLEEP(3) = 0` took
   3.0 s) and a subquery in FROM. The check became an allowlist.
2. `clear` stopped a running injector by signalling a process id read from a
   file. Three review rounds each found a way to hit the wrong process; the
   signalling was removed and replaced by a stop-request file.
3. The synthetic workload, during a table lock, timed out and reconnected while
   its blocked sessions stayed on the server: sessions grew from 6 to 18 in
   100 s, which would have faked a connection-exhaustion symptom.
4. The change gate's sample copy ran at the default isolation level and took
   shared locks on production rows; it now copies with a lock-free read.

## Results

- Rule engine, end to end: basic tier 6/6, hard tier 5/8 and 6/8 in two runs,
  holdout 1/3, no false positives. The gap between 6/6 and 1/3 is the point:
  rules and the original cases share an author, so the basic score measures
  little. The rule engine serves as a no-key fallback and a regression gate.
- Two incident replays (GitHub 2022-03, Honeycomb 2018-05), not tuned to: the
  GitHub pattern was a hit; on the Honeycomb one the diagnosis crashed on a
  query timeout; fixed as error handling, so it is no longer blind.
- The model path is implemented and unit-tested, but the full model comparison
  was not run, for cost control. Recorded evidence is kept locally; with a key
  in `.env`, `python -m eval_runner replay` reproduces it.

## Mistakes the AI made, and how they were found

- It first misread the role as Data Engineer; I corrected it from interview notes.
- It merged a branch into `main` without my approval, then undid it before
  pushing and told me; merging now needs my explicit approval (`CLAUDE.md`).
- It reported a run whose patch had not applied; caught by reading the output.
- It wrote "degraded RDS hardware" as the Honeycomb cause; a source check
  could not confirm it, and the claim was removed.
- Tests that could not fail were found by the reviewer and rewritten.

## Known limits

One root cause per diagnosis; a small lab (a missing index shows as rows
examined and CPU, not slow queries); keyword retrieval misses most reworded
alerts (3/8); vector retrieval and the inspection agent were not part of the
submittable version; the fault scenarios are synthetic. Full list:
`docs/KNOWN_ISSUES.md`.
