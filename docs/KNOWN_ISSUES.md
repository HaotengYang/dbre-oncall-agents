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
