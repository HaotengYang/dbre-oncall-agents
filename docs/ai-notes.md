# AI notes

A running log of how Claude Code was used on this project: the owner's change
requests, mistakes the owner caught, and one line per technique on why it was
used and what it achieved.

## Owner change requests and corrections

- 2026-11-19: Claude first proposed an e-commerce data pipeline, having read the
  role as "Data Engineer". The owner supplied interview notes showing the role
  is Database Engineer (MySQL operations). The proposal was dropped and the
  project re-scoped to MySQL on-call diagnosis. Lesson: confirm the role and
  the team's actual work before proposing a project.
- 2026-11-19: Claude's proposal included primary/replica replication and a
  general change gate. The owner cut replication and limited the gate to adding
  an index, to keep the scope finishable.
- 2026-11-19: The first commit landed on `chore/project-rules`, leaving no
  `main`. The owner had the branch renamed to `main` (a rename, no commit
  changed) and set the workflow: one branch per module, plain merge, no squash.
- 2026-11-19: Design review. Claude's draft used keyword-only runbook
  retrieval and argued a vector index was unjustified. The owner changed it:
  retrieval becomes an interface with both implementations, runbooks grow to
  about ten, and the evals measure both on wording-mismatch queries. Lesson:
  where a trade-off can be measured cheaply, measure it instead of asserting.
- 2026-11-19: Design review. The owner required a plain-language rationale next
  to the root-cause label, and required end-to-end and replay numbers to be
  reported separately so a replay score is never read as a whole-chain score.
- 2026-11-19: The commit author name was set without a space ("HaotengYang").
  The owner had it corrected to "Haoteng Yang" on the four local commits before
  the first push, with original timestamps kept. Nothing pushed was rewritten.
- 2026-11-19: Second design review. The owner found six gaps in Claude's
  design, each a place where the draft was plausible but unsafe or unmeasurable:
  - The three fault classes are too easy to tell apart, so rule engine and LLM
    could both score 100% and the comparison would mean nothing. Added a hard
    tier (cascading faults, healthy-but-looks-faulty, incomplete evidence).
  - The draft said "run EXPLAIN" without limits. `EXPLAIN ANALYZE` executes the
    statement. Now: plain EXPLAIN, SELECT only, single statement, timeout; and
    captured SQL is untrusted input to prompts.
  - The draft said "read the slow query log" although a read-only account
    cannot read a log file. The source and the minimum grants are now explicit.
  - The shadow-table rehearsal could itself overload the database. Now a capped
    sample, with the limit stated: sample timing does not predict full-table
    timing.
  - Running the full verify before every commit was impractical. Now two levels.
  - The decisions log credited every decision to the owner. It now has a
    "Proposed by" column that separates Claude's ideas from the owner's.
  Lesson: a design that names a tool ("EXPLAIN", "slow log", "shadow table")
  without stating its limits and required privileges is not finished.
- 2026-11-19: Design review closed. The owner approved Claude's four pending
  proposals and added two corrections: `undetermined` on a healthy case is a
  mild false alarm and is counted separately; and the digest table holds
  normalized text, so `EXPLAIN` must use the stored sample statement.

## Suggestions from Claude that the owner accepted

- 2026-11-19: Record evidence once per case and replay it to every model, so
  model comparison is fair, repeatable and affordable.
- 2026-11-19: (approved at close of design review) Statement digests in `performance_schema`
  as the slow-query source; an eval case with hostile text inside a captured
  statement; a redundant-index check and a short lock wait timeout in the gate.
- 2026-11-19: A separate "Rules learned from owner feedback" section at the end
  of `CLAUDE.md`, so rules added during development stay distinguishable from
  the initial ones.

## Techniques used

- 2026-11-19: **Independent second opinion on the design.** During design
  review the owner used a separate Claude session (chat interface, no access
  to this repository or this conversation) to review `docs/DESIGN.md`. The
  comments on eval difficulty, `EXPLAIN` safety and the safety gate's risk
  assessment, among others, came from that review; the owner decided which to
  adopt and passed them to Claude Code as change requests. Why: the session
  that wrote a design is the worst placed to find its gaps. Effect: six gaps
  found before any code existed.
- 2026-11-19: **Stop on incomplete instructions.** The owner's message was cut
  off mid-step; Claude completed the steps that were clear and asked about the
  rest instead of guessing. Effect: no work had to be thrown away.
- 2026-11-19: **Design document before code.** `docs/DESIGN.md` is written and
  reviewed before any module exists. Effect: trade-offs and non-goals are
  decided by the owner up front, not discovered in review.
