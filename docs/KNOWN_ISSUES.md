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
