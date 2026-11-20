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
- 2026-11-20: Pacing. Claude built the whole lab module in one round of over
  an hour and 14 commits, with a long report. The owner could not follow it
  and changed the way of working: one planned step at a time, stop after each,
  reports of at most 15 lines, split anything over 40 minutes, and ask before
  installing software. Recorded as rules in `CLAUDE.md`. Lesson: work the owner
  cannot follow is work the owner cannot defend in an interview.

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
- 2026-11-19: **Subagents with restricted tools.** `explorer` and `reviewer`
  are defined without edit tools, so "the reviewer does not refactor" is
  enforced by what the agent can do, not only by what it is told. Why: an
  instruction can be ignored; a missing tool cannot. Effect: to be recorded
  after first use.
- 2026-11-20: **Tests written before the implementation.** The workload's
  pacing, query-mix and settings tests were written and seen to fail (module
  not found) before any workload code existed; the commit order shows it.
  Effect: the "no burst after a stall" rule was pinned down as a test before
  the pacer was written, instead of being discovered later.
- 2026-11-20: **Reviewer subagent before commit, twice.** The lab module was
  reviewed from a fresh context before its first commit, and the fixes were
  reviewed again before they were committed. Round one: 2 must-fix, 3
  suspected. Round two: approve, with 3 minor points. Effect: two real
  defects were caught that every test had passed over:
  - A first start that failed part-way left a data volume that the next start
    reported as healthy with no accounts in it.
  - The secrets test failed on the documented fresh-clone path; it only passed
    on the machine where it was written.
- 2026-11-20: **Verify a "suspected" finding by experiment before acting.**
  The reviewer suspected a held lock would leak server sessions. A 100 s table
  lock was run against the lab: sessions went 6 -> 10 -> 14 -> 18. The finding
  was real, was fixed, and the experiment became a permanent lab test.

## Mistakes in generated code, found by running it

- 2026-11-20: The seed SQL used `row_number` as a column alias. It is a
  reserved word in MySQL 8. Found on the first real start.
- 2026-11-20: The lab tests kept every database connection open for the whole
  test session. Against the lab's `max_connections = 60` the tests themselves
  pushed connections to 45 and made the "healthy baseline" test fail. The
  failure looked like a lab problem and was a test problem. Connections are now
  closed after each test; the peak during a run is 5.
- 2026-11-20: A digest test matched rows produced by other tests' own queries.
  The filter now matches the workload statement exactly.
- 2026-11-20: Unexplained and recorded as such: once, shortly after a fresh
  server start, the digest table held a duplicate row with a zero count. Not
  reproduced in 1,400 polls. Carried into the SQL agent proposal.

## Measurements

- 2026-11-20, lab on an Apple Silicon laptop, MySQL limited to 1 CPU / 1 GiB:
  first start including image pulls 68 s; reset with images cached 26 s;
  idle MySQL about 5% of one CPU and 630 MiB; workload 20.0 operations/s,
  p99 9 to 14 ms; idle connections 5 of 60. Unindexed scan of `orders`
  (200,000 rows) about 30 ms against 0.5 ms through an index.

