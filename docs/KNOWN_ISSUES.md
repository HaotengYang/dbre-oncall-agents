# Known issues

Problems that are known and deliberately not fixed, with the reason. Suspected
review findings left after the allowed review rounds are recorded here.

## Fault injector

- **Lock scope.** The injector lock is per checkout, not per lab. Two git
  worktrees pointed at the same lab MySQL do not exclude each other. Matters
  when worktrees are used (step C2): only one checkout may inject at a time.
- **Unreported stop.** An injector that starts in the 0.2 s gap while `clear`
  is waiting behind another `clear` is stopped without being named in
  `clear`'s output. Suspected by the reviewer, not reproduced.
- **Stop requests reach only the command line.** An injection run through the
  Python API ends with its with-block. `clear` from another process waits up
  to 60 s and then fails without changing anything.
- **fork.** The lock belongs to the open file, which a `fork()` child would
  share. Nothing forks while holding it today.

- **An orphaned DROP INDEX can run after `clear`.** If the injector is killed
  while its `DROP INDEX` is still waiting for a metadata lock (which needs some
  other long transaction on `orders` at that moment), `clear` reports the
  baseline intact and the drop executes later. The orphan cleanup only ends
  statements creating injector-owned indexes. Suspected in review round 2 of
  step A5, not reproduced. Running `clear` again restores the index.
- **Stale docstring.** `restore_baseline_on_fresh_connection` in
  `fault_injector/engine.py` still says every other session of the schema
  account is ended. Only injector schema changes are.
- **A flaky read in a test.** `test_lock_wait_cascade_...` polls until 40 row
  lock waits exist and then reads the count again; the second read can dip
  under 40 when waits have just timed out. Seen once.

## Baseline gate (`lab_health`)

- **Not proof that no fault is active.** The gate answers "has the lab
  recovered". A dropped index, an unindexed query, and the first seconds of a
  gradual connection leak trip none of its signals. The eval runner must not
  use it to decide whether a fault is present.
- **Detection lag for a dead workload.** Prometheus notices a target is gone
  at its next scrape (5 s interval, 4 s timeout), so for up to about 10 s
  after the workload stops the gate can still pass.
- **A killed session can outlive the 10 s wait.** `end_orphaned_schema_sessions`
  stops waiting after 10 s and the restore proceeds. Suspected, not reproduced.

## Lab

- **Windowed latency lags.** Workload latency in Prometheus is computed over a
  time window. After a fault stops, a direct query is back to about 3 ms
  within 1 s, while the windowed p99 stays high until the window has rolled
  past the fault (measured 2026-11-20: p99 over 30 s still 235 ms at +20 s,
  9 ms at +60 s). Anything that needs "is the lab healthy now" must use
  instantaneous signals, which is what the `lab_health` gate does.
- **p99 per statement over a short window is noisy.** At 20 operations per
  second a 30 s window holds 90 to 180 samples per statement, so p99 is one or
  two samples and a single 100 ms hiccup moves it. The baseline latency test
  failed twice for this reason when the whole lab suite ran in one go. The
  owner decided not to investigate further (2026-11-20).
- **Unexplained digest row.** Once, about 20 s after a fresh server start, the
  statement digest table held a second row with the same digest text, a zero
  count and no sample. Not reproduced in 1,400 polls. Collectors ignore
  zero-count rows.
- **Gate account can drop existing indexes.** MySQL's `INDEX` privilege covers
  both creating and dropping. The privilege layer cannot stop the gate account
  from dropping a baseline index or a primary key; the gate's allowlist must.
- **The admin-port grant exists only on a fresh data volume.** Init scripts run
  on first start. A lab created before 2026-11-20 needs `make reset` once, or
  the diagnosis account is refused on the administrative port.
- **The baseline latency test was loosened.** It asserted the worst per-statement
  p99 under 0.1 s; it now asserts the median under 0.01 s and the p99 of all
  statements pooled under 0.1 s. A single slow low-volume statement type would
  no longer fail it. Reason: p99 of one statement over a short window is one or
  two samples.

## Eval runner and rule engine (suspected findings left after review round 2)

- **`IdleFlood` can leave connections open if its top-up thread does not end
  within 20 s** of stop (a stalled connection handshake). Not reproduced.
- **A freed slot stays open for up to 0.5 s** between top-ups, so an account
  without the administrative privilege can, rarely, still get in during a
  flood. The lab test saw 20 refusals in 20 attempts.
- **The flood test relies on natural client churn** to exercise the top-up; it
  does not free a slot itself.
- **The refused-port rule looks at the error code, not at the path taken.** A
  connection error with no code, reached through the admin port with all quiet
  inside, would still be read as healthy.
- **No test covers** the CLI's exit code 1 on a no-answer case or its
  save-and-exit-3 path on an aborted run. The report text and the underlying
  functions are tested; the CLI wiring is not.
- **Onset can be attributed to the previous case's fault** when two faults are
  close together: the estimate looks back ten minutes. Affects only the onset
  figure, which is reported and not scored.
- **A slow scan from one or two clients may be read as healthy.** The digest
  table counts completed statements; a scan taking over two seconds completes
  fewer than three times in the five-second window, and the rule requires
  three. None of the current scenarios is that shape.
- **The isolation scan is a tripwire, not a sandbox.** It catches static and
  dynamic imports, known path parts, process spawning and filesystem search in
  `diagnosis/`. Code written to get around it could.
- **Review fixes for the eval work are not in commits of their own.** The
  files were new and uncommitted when they were reviewed, and the fixes were
  folded in before the first commit. This departs from the rule that review
  fixes get their own commit; `docs/ai-notes.md` lists what the reviews found.

## Lab accounts and data volumes

- **A lab created before 2026-11-21 lacks `shop_diag_noadmin`.** Accounts are
  created only on a fresh data volume. The eval runner's preflight detects
  this and says to run `make reset`.
- **`SERVICE_CONNECTION_ADMIN` also grants the reserved connection** on the
  normal port (measured). Kept, because diagnosis needs a way in; documented
  because it is more than the privilege's name suggests.

## Rule engine

- **`basic-long-transaction` is unstable: 6 hits in 9 runs.** The engine reads
  the row-lock waiter count at one instant and needs two; during the fault the
  count moves between 1 and 4. When it reads one, it answers `undetermined`.
  Measured 2026-11-21. To be fixed in step C1 by sampling across the window
  and by treating one idle transaction holding many row locks as strong
  evidence. Until then the basic-tier hit rate varies between 5/6 and 6/6.

## Evidence collectors, agents and orchestrator (steps C1 to C3)

- **The frozen rule engine reads a lab without `max_connections` as healthy.**
  The rule engine cannot be changed (frozen). The orchestrator refuses
  "healthy" whenever the metrics agent reports missing metrics or an
  unreachable exporter, and answers "undetermined". Accepted by the owner on
  2026-11-21. Consequence: on such inputs the orchestrator and the rule engine
  give different answers, and reports comparing them say so.
- **After a Prometheus restart, a healthy lab escalates as "undetermined".**
  The one-minute trend is absent until two samples exist, the orchestrator
  refuses "healthy" with a metric missing, and three rounds 5 s apart are too
  short to outlast the gap. Accepted: "not climbing" cannot be claimed without
  the trend.
- **EXPLAIN refuses much ordinary SQL** (backticks, `JOIN ... ON (...)`, index
  hints, `CAST`, any subquery). Those statements are reported "not explained".
  What it cannot catch: a view with a slow definition, or a statement that is
  merely expensive to plan; the 5 s timeout and session kill cover both.
- **The SQL agent's `connection_path == "none"` check is not tested on its own**;
  the collector never produces that path with sessions present.
- **The CLI's handling of a missing setting is not tested.**
- **Static isolation scan limits.** `run = exec; run(...)` or `getattr`
  tricks are not caught. The scan is a tripwire, not a sandbox.
- **Fence near-misses.** Every spelling of "untrusted" and every run of angle
  brackets is removed from fenced text; other invented markers are not.

