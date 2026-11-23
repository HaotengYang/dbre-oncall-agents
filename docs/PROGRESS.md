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
- [x] A5. Two variants per class plus two cascading faults (scope set by the
      owner, 2026-11-20): `missing_index.unindexed_query` (no schema change),
      `lock_wait.blocked_schema_change`, `connection_exhaustion.gradual_leak`,
      `cascade.lock_wait_exhausts_connections`,
      `cascade.slow_query_exhausts_connections`

### B. Eval system

- [x] B1. Proposal: `docs/proposals/B-evals.md`, awaiting owner confirmation
- [x] B2. Eval runner plus rule engine; first report (2026-11-21)
- [x] B3. All 18 cases; rule-engine baseline below; thresholds still empty,
      waiting for the owner
- [ ] B4. Evidence recording and replay (moved into step C4 by the owner)
- [x] B5. `verify` skill (`.claude/skills/verify`, `scripts/verify.sh`)

### C. Agents

- [x] C1. Evidence collectors (read-only): safe EXPLAIN, windowed lock sampling.
      Reviewed four rounds until approved. Rule engine frozen and tagged
      `rule-engine-frozen-v1`. Isolation test in place
- [x] C2. SQL agent and metrics agent, built in two parallel worktrees by
      implementer subagents. Reviewed twice
- [x] C3. Orchestrator, report and `python -m diagnosis` CLI. Reviewed twice
- [ ] C4. Connect the LLM; owner approves model list and budget first; then
      multi-model comparison
- [x] C5. SOP retrieval: ten runbooks, keyword retrieval (BM25). Rank-1 hits:
      8/8 for matching wording, 3/8 for reworded alerts. Vector deferred
- [~] C6. Inspection agent: CUT FOR TIME (owner, 2026-11-21). The deadline is
      Monday; the owner chose the holdout set, the model comparison and keyword
      retrieval over a scheduled inspection agent, which no eval case measures

### D. Change safety gate

- [x] D1. Proposal and completion criteria (`docs/proposals/D-change-gate.md`)
- [x] D2. Allowlist and property-based tests
- [x] D3. Shadow rehearsal, risk assessment, rollback plan. Reviewed until approved
      (two rounds)

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

## Steps A5 and B1 (2026-11-20)

Tests: 93 unit, 127 lab (the full lab suite takes about 16 minutes). Reviewed
twice; round 2 approved. Leftover suspected findings are in
`docs/KNOWN_ISSUES.md`.

### Decisions carried out

- **Admin port.** MySQL's administrative listener is on (host port 13307) and
  `shop_diag` may connect to it. Measured during every exhaustion fault: the
  normal port refuses, the admin port works. The two-tier rule is implemented
  by the evidence collectors in step C1.
- **Baseline gate.** `lab_health.wait_for_lab_baseline()` replaces fixed
  sleeps. Signals: reachable, sessions under 30, no lock waits, no statement
  over 1 s, exporter up, workload scraped and completing operations, no recent
  workload errors; all stable for a few seconds; timeout 120 s.
- **Is the slow latency recovery real?** No. Measured after stopping the
  dropped-index fault: a direct query is back to 3 ms within 1 s; the 30 s
  windowed p99 is still 235 ms at +20 s and 9 ms at +60 s. The lag is the
  statistics window.

### What each new fault looks like from inside (through the admin port)

| Fault | After about 30 to 60 s |
|---|---|
| `missing_index.unindexed_query` | 6 extra sessions, each scanning all 400,000 rows of `order_items` per execution; schema identical to baseline |
| `lock_wait.blocked_schema_change` | One `CREATE INDEX` and every workload statement on `orders`, reads included, in "Waiting for table metadata lock"; zero row-lock waits; only 3 extra sessions |
| `connection_exhaustion.gradual_leak` | Sessions 16 at +10 s, 36 at +30 s, 60 at +60 s; all idle |
| `cascade.lock_wait_exhausts_connections` | 60 sessions, 56 of them waiting on row locks held by one idle transaction |
| `cascade.slow_query_exhausts_connections` | 60 sessions, about 50 running the same unindexed scan; no lock waits |

### Open, for the owner

- Step B1 asks five decisions; see the end of `docs/proposals/B-evals.md`.
- `orders` is still 200,000 rows. With the faults now measured, the missing
  index shows as rows examined (about 10 to 200,000) and a p99 of about 230 ms
  for the affected query, not as a query over one second.

## Steps B2, B3 and B5 (2026-11-21)

Tests: 244 unit. Reviewed twice (round 2 was the last allowed); its four real
defects were fixed, its suspected findings are in `docs/KNOWN_ISSUES.md`.

Deviations from the B1 proposal, for the owner's information:

- Case files are JSON, not YAML. No YAML parser was installed and the rule is
  to ask before installing software; JSON needs nothing. Same fields.
- Code is in `eval_runner/` (`python -m eval_runner run`); `evals/` holds data
  only, so "nothing under evals/ changes without consent" stays easy to check.
- After review, the quick set was aligned with the proposal's wording (one per
  fault class, one cascade, one healthy, one metrics gap) and two alerts were
  reworded so they do not use the answer's vocabulary. Both happened before
  the first commit of `evals/`.

### Rule-engine baseline, end-to-end, all 18 cases (final code, 2026-11-21)

One run per case. An earlier full run on the pre-fix code gave the same
answers on 17 cases; the 18th is the one the fixes changed (see below).

Basic tier

| Case | Expected | Answered | Outcome |
|---|---|---|---|
| basic-dropped-index | missing_index_slow_query | missing_index_slow_query | hit |
| basic-unindexed-query | missing_index_slow_query | missing_index_slow_query | hit |
| basic-long-transaction | lock_wait | lock_wait | hit |
| basic-blocked-schema-change | lock_wait | lock_wait | hit |
| basic-idle-flood | connection_exhaustion | connection_exhaustion | hit |
| basic-gradual-leak | connection_exhaustion | connection_exhaustion | hit |
| basic-healthy-quiet | healthy | healthy | hit |
| basic-healthy-vague-alert | healthy | healthy | hit |

Hit rate 100.0% (6/6). False-positive rate 0.0% (0/2). Index suggestion
correct 100.0% (2/2). Median onset error 15 s.

Hard tier

| Case | Kind | Expected | Answered | Outcome |
|---|---|---|---|---|
| hard-cascade-lock-wait | cascade | lock_wait | lock_wait | hit |
| hard-cascade-slow-query | cascade | missing_index_slow_query | missing_index_slow_query | hit |
| hard-lookalike-high-load | healthy lookalike | healthy | healthy | hit |
| hard-lookalike-slow-report | healthy lookalike | healthy | healthy | hit |
| hard-early-dropped-index | incomplete evidence | missing_index_slow_query | missing_index_slow_query | hit |
| hard-early-long-transaction | incomplete evidence | lock_wait | lock_wait | hit |
| hard-early-gradual-leak | incomplete evidence | connection_exhaustion | undetermined | acceptable |
| hard-hostile-comment | hostile input | lock_wait | lock_wait | hit |
| hard-metrics-gap-admin-port | metrics gap | connection_exhaustion | connection_exhaustion | hit |
| hard-metrics-gap-no-admin-port | metrics gap | connection_exhaustion | undetermined | acceptable |

Hit rate 75.0% (6/8). Acceptable, not hits: 2. Misses: 0. False-positive rate
0.0% (0/2). Symptom-as-cause 0.0% (0/2). Symptoms listed 100.0% (2/2).
Missing metric read as healthy 0.0% (0/2). Undetermined rate 20.0% (2/10).
Index suggestion correct 100.0% (2/2). Median onset error 13 s.

All cases: hit rate 85.7% (12/14), false-positive rate 0.0% (0/4), index
suggestion correct 100.0% (4/4).

### Stability: one basic case is not stable (found by the first `verify` run)

`basic-long-transaction` answered `lock_wait` in both full runs, then
`undetermined` (a miss) in the first `verify` run. Repeated five times in a
row: hit, miss, hit, hit, miss. Over all nine runs so far: 6 hits, 3 misses.

Cause, measured: the rule engine reads the number of sessions waiting on row
locks at a single instant and requires at least two. During this fault that
number moves between 1 and 4 (60 samples over 30 s: one waiter 10% of the
time, two 17%, three 20%, four 53%), and a blocked workload session leaves
the count while it reconnects. So the true basic-tier hit rate is not a steady
6/6: it is 6/6 on some runs and 5/6 on others.

Not fixed in this step: the review rounds for it are used up. The fix belongs
in the evidence collectors (step C1): sample lock waits several times across
the window, and count one idle transaction holding many row locks as strong
evidence even with a single waiter.

### How to read these numbers

- **They are high because the rules were written by someone who knew the
  scenarios.** The baseline is not evidence that rules suffice in general. Its
  use is as a regression gate, and as the bar a model must at least match on
  cases where rules have every advantage.
- The two non-hits are both "undetermined" where that is the designed answer:
  a leak eight seconds old, and a database that cannot be entered at all.
- The hostile-input case says nothing about rules (they never read SQL
  comments as instructions). It exists for the model backends.
- One run per case. Small numbers: on the hard tier one case is 12.5 points.

### Suggested thresholds (the owner decides)

| Tier | Minimum hit rate | Maximum false-positive rate |
|---|---|---|
| basic | 0.83 (5/6) now; 1.0 (6/6) once step C1 has made lock-wait evidence stable | 0.0 (0/2) |
| hard | 0.625 (5/8) | 0.0 (0/2) |

- Basic: the right bar is 100%, because these are single, fully developed
  faults and any miss is a regression. But with the instability above, a 100%
  threshold today would fail `verify` about two times in five for a reason
  unrelated to the change being verified, and a gate that cries wolf gets
  ignored. So 5/6 for now, raised to 6/6 when C1 lands. On the quick set
  (three basic fault cases) that same threshold allows no miss at all, so
  `verify` will still fail on the unstable case; see the note under the table.
- Hard at 5/8, one below the baseline of 6/8: the incomplete-evidence cases
  sit near the rules' thresholds by design (a lock wait three seconds old may
  show one waiter or three), so one of them turning "undetermined" on a given
  run is expected variation, not a regression. Two would be.
- False positives at zero on both tiers: a false alarm sends an on-call
  engineer after nothing. With two healthy cases per tier, a single false
  positive is already 50%.
- Not covered by the two thresholds in the file, but worth gating later:
  symptom-as-cause (now 0/2) and missing-metric-read-as-healthy (now 0/2).
- A threshold is a share, and the quick set is small: three basic fault cases
  and two hard ones. One miss there is 67% and 50%. Either the thresholds are
  judged only on the full set, or the quick set gets its own, lower pair.
  This needs the owner's decision together with the numbers.

### What the first baseline run found in the lab itself

1. **The idle flood did not stay full.** The no-admin-port case answered
   "connection_exhaustion", which it could only have learned from inside.
   Other clients leaving opened slots. The fault now retakes freed slots.
2. **`SERVICE_CONNECTION_ADMIN` does more than open the admin port.** Measured
   during a flood: `shop_diag` connected on the normal port as session 61 of
   60, a second attempt was refused, and accounts without the privilege were
   refused every time. So the privilege also grants the one connection MySQL
   keeps in reserve. An earlier note in this file, and the reviewer, said from
   memory that it only allows the admin port; that was wrong. The no-admin
   tier is now modelled by `shop_diag_noadmin`, which lacks the privilege.

## Steps C1 to C3 (2026-11-21)

Tests: 527 unit. Proposals written and awaiting the owner:
`docs/proposals/holdout-cases.md`, `docs/proposals/C4-models-and-budget.md`.

### C1: what EXPLAIN really executes (measured on the lab's MySQL 8.0.40)

| Statement given to plain EXPLAIN | Time |
|---|---|
| `SELECT * FROM (SELECT SLEEP(3) AS x) AS derived` | 3.0 s (executed) |
| `SELECT * FROM (VALUES ROW(SLEEP(3))) AS d` | 3.0 s (executed) |
| `SELECT * FROM orders, LATERAL (SELECT SLEEP(3) AS x) AS d` | 3.0 s (executed) |
| `SELECT id FROM orders WHERE id = 1 AND SLEEP(3) = 0` | 3.0 s (executed) |
| `SELECT id FROM orders WHERE id = (SELECT SLEEP(3))` | 0 s |
| `EXPLAIN ANALYZE SELECT SLEEP(2)` | 2.0 s (executed) |

The design said "plain EXPLAIN only, SELECT only". That is not enough. The
check is now an allowlist of statement shape and functions. Review took four
rounds; rounds 1 and 2 each found statements that got through.

### C3: one deliberate departure from the baseline

The orchestrator takes the frozen rule engine's verdict each round, except
that it never accepts "healthy" while the metrics agent reports missing
metrics or an unreachable exporter. The frozen engine calls a lab without a
`max_connections` metric healthy, which the owner's rule forbids. For those
inputs the orchestrator and the rule engine disagree, on purpose.

### Open for the owner

- Quick set: a healthy case answered "undetermined" counts as neither a miss
  nor a false positive. Should it count as a miss?
- Holdout cases: eight questions at the end of the proposal.
- C4: model list, spending cap, API key.

### Stability after C1 (2026-11-21)

Basic tier, three consecutive runs of all eight cases with the rule engine:
24 of 24 correct. `basic-long-transaction`, which had been right 6 times in
9 before C1, was right 3 of 3. As the owner had ordered, the basic-tier
threshold was then raised from 5/6 to 6/6.

Orchestrator lab tests: 3 of 3 pass (lock wait in one round; locked out
without the admin port escalates after three rounds with the exhaustion
symptom; the CLI's JSON report on a healthy lab).

## Owner decisions, 2026-11-21 (after C3)

- C4: Opus 5.5, Sonnet 5.5, Haiku 5.5; spending cap 40 USD. The Anthropic
  Python SDK was installed into the project's `.venv` with the owner's consent.
- Holdout: only H1, H4, H5, H6. H2 (two unrelated faults) and H3 (read-only
  server) are not built; they go into the README's limitations. H1's answer is
  `connection_exhaustion` with `undetermined` acceptable; H4 is accepted if its
  completion criterion 12 passes; H6 is `healthy`. Holdout cases live in their
  own directory and set, with no thresholds.
- Quick set: a healthy case answered `undetermined` counts toward the "at most
  one miss" allowance, not as a false positive.
- The orchestrator's refusal of "healthy" with missing metrics is accepted; the
  report must say that the orchestrator and the rule engine differ there.
- C6 inspection agent cut for time.

## Holdout set and the rule-engine run on full plus holdout (2026-11-21)

Rule engine, end to end, one run per case (`--set all`):

| Tier | Hit rate | False positives | Other |
|---|---|---|---|
| Basic | 100.0% (6/6) | 0.0% (0/2) | index suggestion 2/2 |
| Hard | 62.5% (5/8) | 0.0% (0/2) | symptom as cause 0/2; 2 acceptable undetermined; 1 miss |
| Holdout | 33.3% (1/3) | 0.0% (0/1) | symptom as cause 1/1; index suggestion 0/1 |

Hard-tier miss: `hard-early-long-transaction` answered `healthy` three
seconds into the fault, before any blocked update had been caught waiting.
The hard tier sits exactly at its threshold of 5/8.

Holdout, case by case:

| Case | Expected | Rule engine | Outcome |
|---|---|---|---|
| holdout-stuck-active-sessions | connection_exhaustion | undetermined | acceptable |
| holdout-unindexed-update-customers | missing_index_slow_query (symptom lock_wait) | lock_wait | symptom as cause |
| holdout-price-editor-lock | lock_wait | lock_wait | hit |
| holdout-lookalike-bigger-pool | healthy | undetermined | undetermined on healthy |

The gap between the basic tier (written by the rules' author) and the holdout
set is what the owner predicted. The rule engine is frozen and was not changed.

The four holdout scenarios also pass the injector's common lab tests (20 of 20).

Second end-to-end rule-engine run (the recording run for C4, same day): basic
6/6, hard 6/8, holdout 1/3, no false positives. The only difference from the
first run is `hard-early-long-transaction`, a hit this time and a miss before:
three seconds into a fault, whether a blocked update has been caught waiting
is chance. Evidence for all 22 cases is recorded in `var/recordings/` (not in
git) and ready for replay.

## Step D, change safety gate (2026-11-21)

`python -m change_gate plan --table orders --columns status,created_at --query "..."`
rehearses on 20,000 of 200,000 rows in `shop_shadow`, reports whether the
target query uses the new index on the sample, the risk level and reasons,
and the rollback statement. `apply` asks for confirmation. Unit tests 650;
gate lab tests 6. Round 1 of review found six defects, the most serious being
that the sample copy took shared row locks on production rows under the
default isolation level, and that the target-query EXPLAIN lacked the
read-only session and timeout the diagnosis side uses.


## Sunday evening plan (owner, 2026-11-22 21:05)

### Step 2: model comparison: not run

No API key of any provider is in `.env`. Per the owner's instruction the step
was skipped: the model path is implemented and unit-tested, the full comparison
was not run for cost control, the recorded evidence is kept locally
(`var/recordings/`), and with a key in `.env` one command reproduces it
(`python -m eval_runner replay`). Spend: 0 USD.

### Step 3: incident replays (`evals/incident-replays/`)

Two cases built from public postmortems, run like the holdout set: no tuning,
no thresholds, a separate report section, not part of ALL CASES. One review
round; its must-fix findings were fixed in their own commit.

Rule engine, end to end (recording run, 2026-11-22 21:13 PST):

| Case | Expected | Answered | Outcome |
|---|---|---|---|
| incident-github-2022-03 | missing_index_slow_query (symptom connection_exhaustion) | missing_index_slow_query, symptom listed, index correct | hit |
| incident-honeycomb-2018-05 | connection_exhaustion (undetermined acceptable) | no answer: `OperationalError 2013 ... (timed out)` | diagnosis_error |

The Honeycomb simulation lowers MySQL's CPU limit to 0.05 while clients keep
opening connections. The diagnosis account's own evidence query timed out and
the error escaped instead of becoming an undetermined round. This is recorded,
not fixed: the replay set is not tuned to (KNOWN_ISSUES.md).

## Step 8b: vector retrieval with a local embedding model (2026-11-22)

Written before anything was installed (owner's condition: model under 500 MB
from a reputable Hugging Face publisher, otherwise skip).

| Item | Value |
|---|---|
| Python packages | `onnxruntime` 1.32.0 (about 22 MB wheel), `tokenizers` 0.23.3 (about 3 MB), `numpy` (already a dependency of nothing else here; installed with them) |
| Model | `sentence-transformers/all-MiniLM-L6-v2`, revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`, Apache-2.0 |
| Files downloaded | `onnx/model.onnx` (90.4 MB), `tokenizer.json` (0.5 MB) |
| Source | https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2 (publisher: the sentence-transformers project) |
| API calls | none; the model runs locally, offline after the one download |

Why this one: 90 MB is well under the limit, it needs no PyTorch, and it is the
most widely used small sentence-embedding model from the library's own
publisher. Packages go in `requirements-vector.txt`, separate from the default
install, so keyword retrieval still runs with no extra dependency.
