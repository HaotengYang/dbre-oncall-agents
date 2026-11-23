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
- 2026-11-20: **Five review rounds on one module; the fix was to remove a
  capability, not to patch it.** `clear` stopped a running injector by sending
  SIGTERM to a process id read from a file. Rounds 1, 2 and 3 each found a
  different way for that signal to reach the wrong process (reused pid, a
  second `clear` holding the lock, stale identity in the lock file). After the
  third, the signalling was removed: `clear` writes a stop request and the
  injector polls for it. Round 4 found two fail-closed defects in the new
  design; round 5 approved. Lesson: when review keeps finding new holes in the
  same mechanism, the mechanism is wrong.
- 2026-11-20: **A defect that only showed under test load.** The injector set
  a `threading.Event` from its signal handler while the main thread polled
  `Event.wait()`. A Ctrl-C at the wrong instant deadlocked the process, which
  then held the injector lock for ten minutes and hung the test run. Found by
  sampling the stuck process. The handler now sets a plain attribute, with a
  lab test that sends repeated signals.
- 2026-11-20: **Tests that could not fail.** The reviewer found assertions
  that held whatever the code did: timing checks placed after a helper that
  already enforced the same limit, and a clock started after the event it was
  meant to time. Each was rewritten so that reverting the behaviour fails it.
- 2026-11-20: Claude's proposal for the idle-flood fault said "about 55 of 60
  connections", which contradicted its own criterion that a new connection is
  refused. The reviewer caught it; the proposal was corrected.
- 2026-11-20: The owner asked for steps A2, A3 and A4 in one go. It took far
  longer than three 40-minute steps because of the review rounds above.
- 2026-11-20: **Review capped at two rounds (owner's rule).** Step A5 was
  reviewed twice. Round 1 found three defects, among them that the new
  baseline gate read a stopped workload as healthy, the exact mistake the
  owner's rule "a missing metric is evidence of an anomaly" forbids. Round 2
  approved. Two leftover suspected findings went to `docs/KNOWN_ISSUES.md`
  unfixed, as the rule says.
- 2026-11-20: **Measure before explaining.** The owner asked whether the slow
  latency recovery after a fault was real or a statistics lag. A direct query
  timed every few seconds after the fault stopped settled it in two minutes:
  the database recovers in under a second, the windowed percentile lags by its
  window.
- 2026-11-20: **A test of order that the cleanup would hide.** Stopping the
  blocked-schema-change fault must cancel the waiting DDL before releasing the
  transaction. Done in the wrong order the index is really built, but the
  baseline restore removes it, so every end-state test still passed. The
  reviewer saw this; a unit test with recording fakes now pins the order.
- 2026-11-20: Claude's first fix for "do not end sessions that are not orphans"
  was itself found too broad in round 1 and too narrow in round 2. The
  narrower version was kept and the gap recorded.
- 2026-11-21: **The eval's first run tested the lab, not the diagnosis.** The
  baseline came back suspiciously good, and one answer was impossible: the
  case with no way into the database had answered from inside it. Following
  that up, with measurements rather than recall, found two lab defects: the
  connection flood did not stay full, and the privilege granted for the admin
  port also grants MySQL's reserved connection on the normal port. Both
  Claude and the reviewer subagent had stated the opposite from memory.
  Lesson: an eval result that looks too good is a finding about the eval.
- 2026-11-21: **Deliberately wrong backends as a check on the scorer.** Two
  test doubles, "always healthy" and "always connection exhaustion", are run
  through the real runner against the real lab. They must score 0 hits and
  100% symptom-as-cause respectively. Why: a scorer can be wrong in ways unit
  tests of the scorer do not show. Effect: the scoring is known to be wired to
  what actually happened in the lab.
- 2026-11-21: **Review, two rounds, eval system.** Round 1: a refused port
  could be read as healthy; a backend that crashed on healthy cases would pass
  the false-positive gate; one malformed field from a backend ended the whole
  run. Round 2: `make verify` could not return the eval's exit code (make
  turns every failure into 2), the isolation scan missed dynamic imports and
  bare path parts, and an old lab volume would make one case pass for the
  wrong reason. All fixed. The isolation scan is now tested against the very
  snippets the reviewer used to get past it.
- 2026-11-21: Claude twice wrote a file-patching script whose pattern matched
  the wrong number of places, and reported the result of a run that had not
  applied the change. Caught by reading the output (the scenario was "unknown")
  rather than the summary line. The file was then rewritten whole.
- 2026-11-21: The owner asked for an expected index on missing-index cases and
  for every rate to show its fraction. Both are in: "Index suggestion correct
  100.0% (4/4)" is a separate line that never changes a hit into a miss.
- 2026-11-21: **`verify` earned its keep on its first run.** Two full baseline
  runs had the rule engine at 6/6 on the basic tier. The first `verify` run
  got 2/3 on the same cases. Repeating the differing case five times gave
  hit, miss, hit, hit, miss. The engine reads one number at one instant, and
  that number flickers. Had the baseline been reported from the two full runs
  alone, the owner would have been given "100%" for something that is right
  two times in three. Lesson: one run is an anecdote; stability has to be
  measured, which is why the design lists it as its own dimension.
- 2026-11-21: **A holdout set designed blind.** The explorer subagent wrote
  the holdout cases with instructions not to open the rule engine's code.
  Why: the rule engine and the original 18 cases had one author, so its score
  measured little. Effect: six cases covering gaps the 18 did not (another
  table, two unrelated faults, a cause outside the three classes). The
  explorer itself disclosed a leak: the main session had let it read
  `docs/KNOWN_ISSUES.md`, which mentions four rule thresholds.
- 2026-11-21: **Review until approved, for the safety-critical module.** The
  EXPLAIN allowlist went through four review rounds. Round 1 found a
  backtick-quoted `SLEEP` and a `VALUES` derived table that passed; Claude
  measured both on the lab server, and found a third case the reviewer had
  not: a function in WHERE is executed when a table reduces to one row. The
  check became an allowlist of functions. Round 2 found `"SLEEP"(3)` under
  ANSI quotes, `my$IF(3)` and `9IF(3)`. Round 3 found only a wording error.
  Lesson: the design's sentence "plain EXPLAIN only" was true and not enough.
- 2026-11-21: **Two worktrees, two implementer subagents, in parallel.** The
  SQL agent and the metrics agent were built at the same time in separate git
  worktrees from a contract committed first. Effect: both came back with
  passing tests and merged without conflict. Both subagents found their
  worktree branched from `main` instead of the feature branch and reset it
  themselves; worth knowing for next time.
- 2026-11-21: **A project rule overriding a design decision.** Claude designed
  the orchestrator to agree with the frozen rule engine on every input. The
  reviewer showed the engine calls a lab with a missing metric healthy, which
  the owner's rule forbids. The owner's rule was kept; the departure is
  documented and tested.
- 2026-11-21: **An unapproved merge into main.** While starting the change
  safety gate, Claude merged `feat/holdout` into the local `main` without the
  owner's approval for that branch. Claude noticed in the next command, reset
  the local `main` to the pushed `origin/main` (the merge had not been pushed,
  so no published history changed), and told the owner in the next report.
  The owner then added a rule to `CLAUDE.md`: merging into `main` needs the
  owner's explicit approval. Lesson: approval for one merge is not approval
  for the next.

