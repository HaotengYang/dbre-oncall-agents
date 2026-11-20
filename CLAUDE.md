# CLAUDE.md

Project rules for Claude Code. The owner sets these rules; follow them exactly.
When the owner points out a problem, fix it AND add the resulting rule to this file.

## Project

MySQL on-call multi-agent diagnosis system.

When MySQL misbehaves, the system diagnoses the root cause, recommends handling
steps, rehearses a change safely before it touches production, and measures its
own diagnostic accuracy with an eval suite.

Deadline: Monday 2026-11-23, submit before 15:00 (hard cutoff 18:00).

## Scope

1. **Lab environment** - Docker Compose runs MySQL, Prometheus and a synthetic
   workload. One command starts everything.
2. **Fault injector** - missing-index slow queries, lock waits, connection
   exhaustion. Every fault class has several variants.
3. **Eval system**
   - Cases cover fault scenarios and "system is healthy" scenarios.
   - Reports hit rate and false-positive rate.
   - Compares the rule-engine path against the LLM path.
   - Supports multi-model comparison on four dimensions: response time,
     accuracy, stability (consistency across repeated runs of one case), cost.
   - Model comparison replays recorded evidence. Reports keep two layers of
     numbers apart: end-to-end (whole chain) and replay (model reasoning only).
     Never mix them.
   - Includes queries where the alert wording differs from the runbook wording,
     and reports retrieval hit rate for keyword vs vector.
4. **Agents**
   - Orchestrator: receives the alert, dispatches, merges a diagnosis report.
   - Metrics agent: reads Prometheus metrics, identifies anomaly type and onset.
   - SQL agent: inspects processlist, slow query log and EXPLAIN; suggests indexes.
   - SOP agent: retrieves handling steps from about ten runbooks. Retrieval is
     an interface with two implementations: keyword (default, no dependencies)
     and vector (optional).
   - Inspection agent: scheduled checks that surface risks early.
   - If the root cause is not settled within three rounds, output
     "undetermined, escalate to a human". Never force a conclusion.
   - Every report carries the root-cause label and a plain-language paragraph
     explaining the basis for it.
5. **Change safety gate** - supports exactly one change type: adding an index.
   Shadow-table rehearsal, risk assessment, generated rollback plan; high-risk
   operations require human confirmation.
6. **Degradation and secrets** - without an API key the system falls back to the
   rule engine, so it runs on any machine. API keys live only in `.env`, and
   `.env` must be listed in `.gitignore`.

**Out of scope:** primary/replica replication. Do not add it unless the owner
decides to.

## Build order

Build the means of verification before the thing being verified:

lab environment -> fault injector -> eval skeleton and rule-engine baseline ->
agents -> change safety gate -> docs.

## Claude Code setup

1. Three subagents defined in `.claude/agents/`, committed to the repo:
   - `explorer`: research and proposals.
   - `implementer`: implementation.
   - `reviewer`: reviews from a fresh context; correctness and safety only.
     No drive-by refactoring, no style changes.
2. Verification has two levels:
   - **Fast, before every commit:** run the unit tests. No containers.
   - **Full, before merging a branch into `main`:** run the `verify` skill in
     `.claude/skills/`: start the environment, inject faults, run the evals,
     print a report.
3. Independent modules are developed in parallel with git worktrees (for
   example the SQL agent and the metrics agent).
4. No third-party orchestration tools. Use only what Claude Code ships with.

## Working agreement

1. For each module, first give the owner a proposal and verifiable completion
   criteria. Write code only after the owner confirms, then iterate until the
   criteria are met.
2. Three consecutive rounds without progress: stop and ask the owner.
3. After finishing a module, tell the owner how to run it by hand.
4. The reviewer reviews before commit. Changes made in response to review go in
   their own commit.
5. Cases and thresholds under `evals/` must not be modified without the owner's
   explicit consent. The model list and spending cap for any paid
   model run also need the owner's approval first.
6. Problems the owner points out are fixed and recorded as rules in this file.
7. Build the minimum usable version first. Do not over-engineer.

## Testing

1. For key modules, write the tests before the implementation.
2. End-to-end tests (inject a fault -> diagnose -> check the conclusion) are the
   most important kind.
3. The change safety gate has property-based tests: generate random SQL and
   verify that high-risk statements are always blocked.
4. Tests must check real effects, not merely the absence of errors.

## Git and docs

1. Small commits, one concern per commit, conventional commits format.
2. No squashing, no amending pushed commits, no force push. Push every day.
3. Maintain `docs/PROGRESS.md`: what is done and what remains. Update it at the
   end of every working session.
4. Maintain `docs/ai-notes.md`: the owner's change requests and the mistakes
   the owner caught; and each time a technique is used (subagent, worktree,
   property-based test, ...), one line on why it was used and what it achieved.
5. Code, comments, docs and commit messages are all in English. Variable and
   function names carry specific meaning; no `a`, `b`, `tmp`.
6. The final deliverables include `README.md` (run and test instructions) and
   `AI_WORKFLOW.md` (40 to 80 lines). Write these two last.

## Language

Write the fault injector in Python first. After reading that code, the owner
decides whether to continue in Python or switch to Java. Do not assume the
language for later modules before that decision.

## Rules learned from owner feedback

(None yet. Append here, newest last, with the date.)
