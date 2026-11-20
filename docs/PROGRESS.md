# Progress

Updated at the end of every working session.

## Done

- 2026-11-19: Repository created; project rules recorded in `CLAUDE.md`.
- 2026-11-19: `docs/DESIGN.md` drafted (architecture, module responsibilities,
  trade-offs, non-goals).
- 2026-11-19: Private GitHub repository created; `main` and `docs/design` pushed.
- 2026-11-19: `docs/DESIGN.md` revised after owner review (revision 2).
- 2026-11-19: `docs/DESIGN.md` revision 3: hard eval tier and ground-truth
  rules, EXPLAIN safety, slow-query source and least privilege, sampled shadow
  rehearsal and risk checks, two verification levels, "Proposed by" column.

- 2026-11-19: Design review closed; `docs/DESIGN.md` approved and frozen.

- 2026-11-19: Subagent definitions merged into `main`.
- 2026-11-20: Lab environment built on `feat/lab-environment`: MySQL with fixed
  limits, exporter, Prometheus, Python workload, four least-privilege accounts.
  Unit tests 40, lab tests 47, lifecycle tests 2, all passing. Reviewed twice.

- 2026-11-20: Lab environment merged into `main`.

## Plan

One step at a time; the owner says "继续" before the next one starts.
Schedule target: A and B on Friday 11-20, C on Saturday 11-21, D and E on
Sunday 11-22, Monday 11-23 for rehearsal and submission only.

### A. Fault injector

- [x] A1. Proposal and completion criteria (no code):
      `docs/proposals/A-fault-injector.md`, awaiting owner confirmation
- [x] A2. Missing-index slow query: `missing_index.dropped_index`
- [x] A3. Lock wait: `lock_wait.long_transaction`
- [x] A4. Connection exhaustion: `connection_exhaustion.idle_flood`. Owner's
      decision on how the diagnosis account connects is still open (see below)
- [ ] A5. Several variants per class, plus cascading faults. The missing-index
      class must include a variant that does not change the schema: a new query
      on a column that never had an index (owner, 2026-11-20)

### B. Eval system

- [ ] B1. Proposal: case format, ground truth, scoring
- [ ] B2. Eval runner plus rule engine for one fault class; first report
- [ ] B3. Rule engine covers all cases; baseline numbers; owner sets thresholds
- [ ] B4. Evidence recording and replay
- [ ] B5. `verify` skill

### C. Agents

- [ ] C1. Evidence collectors (read-only). Add the test that diagnosis-side
      code never reads injection records or imports `fault_injector`
- [ ] C2. SQL agent and metrics agent, in two parallel worktrees
- [ ] C3. Orchestrator and diagnosis report
- [ ] C4. Connect the LLM; owner approves model list and budget first; then
      multi-model comparison
- [ ] C5. SOP retrieval: ten runbooks, keyword and vector, hit-rate comparison
- [ ] C6. Inspection agent

### D. Change safety gate

- [ ] D1. Proposal and completion criteria
- [ ] D2. Allowlist and property-based tests
- [ ] D3. Shadow rehearsal, risk assessment, rollback plan

### E. Wrap-up

- [ ] E1. README (covering both Docker Desktop and Colima)
- [ ] E2. `AI_WORKFLOW.md`
- [ ] E3. Fresh clone in a new directory; follow the README from the start

## Open decisions

- See "Open questions for the owner" in `docs/DESIGN.md`.
- `orders` stays at 200,000 rows until the fault injector gives measured
  numbers (owner, 2026-11-20).
- During connection exhaustion the diagnosis account cannot connect either.
  Options go to the owner in step A4.
- Digest-table rows with a zero count are ignored by the SQL agent; the cause
  of the one sighting stays recorded as not established (owner, 2026-11-20).
- The gate's `INDEX` privilege also allows dropping existing indexes, including
  a primary key. The privilege layer cannot narrow this; the gate's allowlist
  must. To be settled in the change safety gate proposal.

## Fault injector, steps A2 to A4 (2026-11-20)

Tests: 68 unit, 89 lab. Reviewed by the reviewer subagent five times.

### Measured effect of each fault (MySQL limited to 1 CPU / 1 GiB)

| Fault | What changes | Measured |
|---|---|---|
| `missing_index.dropped_index` | Customer-orders query scans the table | Rows examined per execution about 10 -> about 200,000; that query's p99 about 10 ms -> about 230 ms; MySQL CPU about 8% -> 16%; other statements unaffected; throughput unchanged at 20/s |
| `lock_wait.long_transaction` | Updates to a quarter of orders block | Lock waits appear within seconds; blocked updates fail after the workload's 10 s lock timeout; connections do not grow |
| `connection_exhaustion.idle_flood` | Every connection slot is taken | New connections refused with error 1040; `shop_diag` cannot connect; the exporter cannot connect, so `mysql_up` is 0 and the connection-count metric disappears; sessions already connected keep working and the workload stays at 20/s; `root` still gets in through the reserved slot |

After a fault ends, workload p99 takes about 45 to 60 s to return to baseline.
The evals will need a settle period between cases.

### Open decision: diagnosis when connections are exhausted

While every slot is taken the diagnosis account cannot open a connection.

1. **Administrative interface (recommended).** Enable MySQL's separate admin
   listener (`admin_address`, `admin_port`) and grant `shop_diag` the
   `SERVICE_CONNECTION_ADMIN` privilege. That listener is not subject to
   `max_connections`. The privilege only allows connecting there; it allows no
   writes. Cost: a config line, a grant, a `make reset`. The exporter is left
   as it is: its failure is itself evidence.
2. **Diagnose from outside only.** Use the refused connection (error 1040),
   `mysql_up = 0` and the connection count before it disappeared. Enough to
   say "connection exhaustion", not enough to see what is behind it, so
   cascading cases would end as `undetermined`.
3. **A connection opened in advance.** The collector keeps one connection
   open at all times. Works only if it was started before the fault.

### Review rounds and what they changed

| Round | Result | Main findings |
|---|---|---|
| 1 (A2) | changes required | `clear` trusted a process id from a file; started record written after the fault took effect; fault account's privileges untested |
| 2 (fixes, A3, A4) | changes required | A3 and A4 sound. Another `clear` holding the lock could still lead to a wrong signal; `clear` on a running injector had no test; stop-time criteria were not really measured |
| 3 | changes required | A killed injector's identity stays in the lock file, so the signal could still go astray |
| 4 (redesign) | changes required | Signalling removed entirely. A stop request could be lost; a message could be wrong |
| 5 | approve | Three suspected points, listed below |

Decision taken after round 3: stop patching the signalling and remove it. The
injector now stops when it sees a stop-request file.

### Known issues, not fixed

- `test_baseline_workload_latency_is_low` failed twice when the whole lab
  suite ran in one go (p99 0.167 s against a 0.1 s limit). It passes alone and
  when run after the fault tests as a pair. Not explained. Likely related to
  the settle time above and to the 1-CPU limit (the container shows CPU
  throttling). Three attempts did not settle it; left for the owner to decide.
- The injector lock is per checkout, not per lab. Two git worktrees pointed at
  the same lab do not exclude each other. Matters from step C2.
- An injector started in the 0.2 s gap while `clear` waits behind another
  `clear` is stopped without being named in `clear`'s output (suspected, not
  reproduced).
- A stop request reaches only a command-line injector. An injection run
  through the Python API ends with its with-block; `clear` from another
  process waits up to 60 s and then fails without changing anything.
