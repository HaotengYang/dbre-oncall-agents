# Proposal: holdout eval cases

Status: proposal for the owner. Nothing is implemented and nothing was run.

Provenance: written by the `explorer` subagent on 2026-11-21, instructed not to
open anything under `diagnosis/` or any test describing the rules. The text
below is its report, saved by the main session without changing its content.
The main session wrote the rule engine and therefore did not design or adjust
these cases. One process flaw, disclosed by the explorer in section 1.2: the
main session allowed it to read `docs/KNOWN_ISSUES.md`, which mentions four
details of the rule engine.

Six holdout cases are proposed, ranked so the owner can cut from the bottom.
Three of them cannot be scored honestly under the current contract without an
owner decision; those decisions are in section 5.

## 1. Findings

### 1.1 What was read and what was not

Read: `CLAUDE.md`, `docs/DESIGN.md`, `docs/proposals/A-fault-injector.md`,
`docs/proposals/B-evals.md`, `evals/README.md` and all 18 files in
`evals/cases/`, all of `fault_injector/faults/*.py`,
`fault_injector/background.py`, `lab/mysql/init/01-schema.sql`,
`lab/mysql/conf.d/lab.cnf`, `lab/workload/shop_workload/query_mix.py`,
`docs/KNOWN_ISSUES.md`.

Not opened or searched: anything under `diagnosis/`, and any test file. Also
not read because they were outside the allowed list: `fault_injector/lab.py`,
`baseline.py`, `engine.py`, `eval_runner/`, `lab/mysql/init/02-seed.sql`, the
account grants and the workload runner. Every claim that depends on them is
marked unverified.

### 1.2 Blindness disclosure

`docs/KNOWN_ISSUES.md` was on the allowed list, but it describes four
rule-engine behaviours. The explorer saw them, so this holdout is not fully
blind:

| What it reveals |
|---|
| The refused-port rule looks at the error code, not the path taken. |
| The onset estimate looks back ten minutes. |
| The slow-scan rule needs three completed executions in a five-second digest window. |
| The lock rule reads the row-lock waiter count at one instant and needs two. |

Client counts and timings were chosen from each scenario's own logic and from
the existing injector's values (0.4 s between clients, 6 clients, 40 to 45 s
observation). No parameter was moved toward or away from those thresholds. The
owner should still treat H1 and H4 (lock waiters) as "designed by someone who
had seen one lock threshold".

### 1.3 What the existing 18 cases cover

- Every lock scenario is on `orders`. Every missing-index scenario is on
  `orders` or `order_items`.
- Every cascade has `connection_exhaustion` as its symptom. No case has
  `lock_wait` as a symptom.
- No case expects `undetermined` as the correct answer; it appears only in
  `also_acceptable`.
- No case has two independent faults.
- Connection exhaustion by active sessions exists only as copies of an
  unindexed scan (`hard-cascade-slow-query`).
- The healthy lookalikes are high query volume and a slow report. None is
  about connection count.

### 1.4 Facts from MySQL documentation

Verified against the MySQL 8.0 Reference Manual:

- Unindexed locking statements lock every row: "If you have no indexes
  suitable for your statement and MySQL must scan the entire table to process
  the statement, every row of the table becomes locked." ("Locks Set by
  Different SQL Statements in InnoDB")
- `UPDATE ... WHERE` "sets an exclusive next-key lock on every record the
  search encounters." (same page)
- Default isolation is REPEATABLE READ, under which an unindexed `UPDATE`
  "acquires an x-lock on each row that it reads and does not release any of
  them". ("Transaction Isolation Levels")
- `SLEEP(n)` pauses for n seconds and returns 0. ("Miscellaneous Functions")
- `SET GLOBAL` needs `SYSTEM_VARIABLES_ADMIN` or `SUPER`. (from a
  search-result summary, not read in full)

Unverified, from memory or inferred: the default `innodb_lock_wait_timeout`
(50 s); the exact `read_only` behaviour (error 1290 for ordinary accounts,
reads unaffected, whether enabling it blocks behind open transactions); that a
plain `SELECT` is not blocked by row locks; that `SLEEP()` in the select list
of a one-row primary-key lookup runs once; that the digest table's
rows-examined and no-index-used counters are populated for `UPDATE`; that
`shop_app` may update `products` and `customers`; that the control connection
may set global variables; that the workload survives error 1290; that the
`lab_health` gate would notice a server left read-only; how `expected.index`
is scored; whether the case loader accepts a new `kind`.

## 2. Proposal

### 2.1 Summary

| Rank | Id | Expected `root_cause` | Scorable today? |
|---|---|---|---|
| 1 | `holdout-stuck-active-sessions` | `connection_exhaustion`, `undetermined` acceptable | Yes, if the owner accepts the label |
| 2 | `holdout-two-unrelated-faults` | None single; report separately | No |
| 3 | `holdout-writes-refused` | `undetermined` | Partly: outcome yes, rates no |
| 4 | `holdout-unindexed-update-customers` | `missing_index_slow_query`, symptom `lock_wait` | Yes |
| 5 | `holdout-price-editor-lock` | `lock_wait` | Yes |
| 6 | `holdout-lookalike-bigger-pool` | `healthy` | Yes |

Each case costs about two minutes to run, so the set is about twelve minutes.

### 2.2 H1 `holdout-stuck-active-sessions`

**Description.** Every connection is taken by sessions that are actively
running a statement that is slow for a reason no index can fix.

**Scenario** (`connection_exhaustion.active_slow_calls`).
- A new `shop_app` connection every 0.4 s, safety cap 500, through
  `BackgroundClients`: the same arrival pattern as
  `SlowQueryExhaustsConnections`.
- Each client loops on
  `SELECT id, name, price_cents, SLEEP(4) FROM shop.products WHERE id = %s`
  with a random id from 1 to 1,000.
- Errors are caught with a 0.5 s pause.
- `observe_seconds: 45`, `admin_port_available: true`. Sixty slots fill in
  about 25 s.
- No schema change and no data change. Undo is `BackgroundClients.stop()`;
  each thread returns within 4 s of the stop request.

**Expected answer.**
- `root_cause: connection_exhaustion`, `also_acceptable: [undetermined]`,
  `symptoms: []`, no index.
- Reason: the statement uses the primary key and examines one row. There is no
  lock. The one anomaly in the closed set is that connections have run out.
- `missing_index_slow_query` is a miss: it would send the engineer and the
  safety gate to build an index that fixes nothing. `lock_wait` and `healthy`
  are misses.
- Debatable: by the cascade rule the origin is "a slow call", which is outside
  the set, so `undetermined` could be the hit instead. `connection_exhaustion`
  is recommended as the hit because `basic-idle-flood` already uses that label
  when the reason behind the connections is outside the database.

**Closest existing case.** `hard-cascade-slow-query`. There the active
statements are full scans and the answer is the missing index. Here the same
symptom has a different cause, so "active slow statements plus full
connections" must not be mapped straight to a missing index.

**Alert.** "The app is out of database sessions. Database CPU is flat and
product pages take several seconds to load."

**Risks.** No damage: the sessions use no CPU and hold no locks. Connections
drop within about 5 s of stop. `SLEEP` stands in for a slow stored function or
an external wait, and a language model may recognise it as synthetic. Check:
the processlist shows at least 40 sessions in `Command = Query` running this
statement, `data_lock_waits` is empty, and a new `shop_app` connection gets
error 1040.

### 2.3 H2 `holdout-two-unrelated-faults`

**Description.** A missing-index query and a lock wait are active at once, and
neither causes the other.

**Scenario** (`compound.unindexed_query_and_long_transaction`).
- Start `LongTransaction` (row locks on `orders` ids 1 to 50,000) and
  `UnindexedQuery` (6 clients scanning `order_items`) within one second of
  each other, composed the way `LockWaitWithHostileComment` composes its two
  parts.
- `observe_seconds: 45`, `admin_port_available: true`.
- No schema change. Undo is each part's own `stop()`, lock holder first.
- Both parts are existing basic cases on their own, so a failure here is
  attributable to the combination.

**Expected answer.**
- There is no single correct `root_cause`. Removing either fault leaves the
  other.
- By the letter of the contract, `undetermined` is the conforming answer,
  because the evidence does not support one cause.
- Naming one fault is half right and half actionable. Putting the other in
  `symptoms` surfaces it but mislabels it as a consequence.
- **Recommendation: do not score this case in any hit rate.** Report it in its
  own section with three facts: the label given, whether both labels appear
  anywhere in `root_cause` plus `symptoms`, and whether the answer was
  `healthy` or `connection_exhaustion` (clearly wrong).
- If the owner wants it in the current format anyway: `root_cause:
  undetermined`, `also_acceptable: [missing_index_slow_query, lock_wait]`.

**Closest existing case.** `hard-cascade-lock-wait`, which also shows two
anomalies. There one causes the other. Here a system that always picks an
"origin" and demotes the rest to symptoms produces a confident but wrong
causal story.

**Alert.** "Since the release the product report page crawls, and some order
status changes hang and then fail."

**Risks.** No damage beyond the two existing scenarios. `Fault` has one
`root_cause`, and a mismatch with the case makes the case invalid, so this
case cannot be recorded without an owner decision. Check: rows examined up
with a full scan, and lock waits present, in the same window.

### 2.4 H3 `holdout-writes-refused`

**Description.** The server has been switched to read-only, so every
application write fails at once while reads stay fast.

**Scenario** (`outside.read_only_server`).
- `start()`: the control connection runs `SET GLOBAL read_only = ON`.
  `stop()`: `SET GLOBAL read_only = OFF`.
- No extra clients. The normal workload's `update_order_status` (15% of
  traffic) is the victim.
- `observe_seconds: 30`, `admin_port_available: true`.
- No schema change. The only proposed case that changes global server state.

**Expected answer.**
- `root_cause: undetermined`, `also_acceptable: []`, `symptoms: []`.
- Reason: something is clearly wrong, and it is none of the three classes. The
  design says `undetermined` covers causes outside the set.
- `healthy` is the worst miss: all writes are failing. `lock_wait` is the
  tempting miss, because the alert is about failing updates.

**Closest existing case.** `basic-long-transaction`, whose alert is also about
failing order updates. There updates wait and time out. Here they fail
instantly and no lock exists.

**Alert.** "Every order status change has failed instantly for the last
minute. Order pages still load normally."

**Risks.**
- If the injector is killed, the server stays read-only. `clear` restores only
  schema, so it would have to be extended. That touches a reviewed module.
- If the `lab_health` gate does not notice a read-only server, every later
  case runs on a broken lab. Unverified either way.
- Privileges and workload tolerance are unverified.
- Scoring: an expected `undetermined` is in neither the hit-rate nor the
  false-positive denominator, and it inflates the undetermined rate that
  exists to expose hiding.
- Check: a `shop_app` `UPDATE` fails with the read-only error, a `shop_app`
  `SELECT` succeeds, and `data_lock_waits` is empty.

### 2.5 H4 `holdout-unindexed-update-customers`

**Description.** An update that filters on an unindexed column of `customers`
locks the whole table each time, so concurrent copies queue behind each other.

**Scenario** (`cascade.unindexed_update_blocks_writers`).
- Six `shop_app` clients, `autocommit=False`, started 0.1 s apart.
- Each loops: `UPDATE shop.customers SET country_code = country_code WHERE
  full_name = %s`, wait 0.3 s, `COMMIT`. The parameter is a random name
  string; it need not match a row, because every scanned row is locked.
- Errors (lock wait timeout, deadlock) are caught with a 0.5 s pause.
- `observe_seconds: 45`, `admin_port_available: true`.
- No schema change and no data change (self-assignment).

**Expected answer.**
- `root_cause: missing_index_slow_query`, `symptoms: [lock_wait]`,
  `also_acceptable: []`, index `customers (full_name)`.
- Reason: with an index on `full_name`, each transaction locks only its own
  rows and the queue disappears. The origin is the fault whose removal makes
  the other go away. `lock_wait` scores as `symptom_as_cause`.
- The most debatable ground truth in the set: the 0.3 s hold invites the
  reading "transactions are too long". The check below settles it.

**Closest existing case.** `basic-unindexed-query`. Differences: a table that
normal traffic never touches, an `UPDATE` instead of a `SELECT`, and
`lock_wait` as the symptom, which no existing cascade has.

**Alert.** "The profile-sync job that normally finishes in seconds has been
stuck for minutes. The storefront is unaffected."

**Risks.** No damage (10,000 rows, no data change). The design explains only
`SELECT`, so the system cannot `EXPLAIN` this statement; the index must be
inferred from digest counters, unverified for `UPDATE`. Check, which settles
the ground truth: run the scenario, create `idx_fault_customers_full_name`,
and confirm lock waits on `customers` fall to zero.

### 2.6 H5 `holdout-price-editor-lock`

**Description.** A stalled transaction holds row locks on `products`, and only
a few back-office writers are stuck behind it.

**Scenario** (`lock_wait.products_row_lock`).
- Holder: one `shop_app` connection, `autocommit=False`, runs `SELECT id FROM
  shop.products WHERE id BETWEEN 1 AND 1000 FOR UPDATE` and stays open.
- Victims: four `shop_app` clients, each looping on `UPDATE shop.products SET
  price_cents = price_cents WHERE id = %s`, with a 0.5 s pause on error.
- `observe_seconds: 40`, `admin_port_available: true`.
- No schema change and no data change. Undo: roll back the holder first, then
  stop the clients.

**Expected answer.** `root_cause: lock_wait`, `symptoms: []`,
`also_acceptable: []`. Not debatable.

**Closest existing case.** `basic-long-transaction`. Differences: the table is
`products`, and the normal workload shows nothing: its reads of `products` are
not blocked and it never writes there, so workload latency and failed-update
counts stay flat. Only the database's own lock evidence shows the fault.

**Alert.** "Saving a price in the back-office tool spins and then errors out.
Customers on the storefront see nothing wrong."

**Risks.** No damage. Four victims was chosen after the explorer had seen the
waiter threshold in `KNOWN_ISSUES.md`. Check: `data_lock_waits` shows waits on
`products` and none on `orders`.

### 2.7 H6 `holdout-lookalike-bigger-pool`

**Description.** The connection count jumps once and then stays flat at about
half the limit; nothing is refused.

**Scenario** (`healthy.steady_idle_pool`).
- Twenty idle `shop_app` connections appear in two seconds and are held.
- `observe_seconds: 60`, `admin_port_available: true`.
- No schema change.

**Expected answer.** `root_cause: healthy`. `connection_exhaustion` is a false
positive; `undetermined` is the mild false alarm. Mildly debatable: half the
limit deserves a note from the inspection agent, but it is not a fault.

**Closest existing case.** `hard-early-gradual-leak`, which is also idle
connections. There the count climbs steadily. Here it steps up and stays, so
the distinction is trend, not level.

**Alert.** "Database sessions roughly tripled after we scaled out the app this
afternoon and have stayed there. Should we worry?"

**Risks.** No damage. Check: `Threads_connected` stays within two of its value
at +5 s for the whole window, and a new `shop_app` connection is accepted.

### 2.8 Holdout protocol

1. Store the cases apart from the 18, in their own set, with no thresholds.
2. Implement the scenarios and pass the state checks in section 3 before any
   diagnosis backend is run on them.
3. Run each backend on the holdout once, and record the result with the commit
   hash.
4. Print holdout results in their own report section. Never add them to the
   basic or hard tier numbers.
5. Once a rule or prompt is changed in response to a holdout result, the case
   is no longer holdout. Say so in the report.

### 2.9 Alternatives rejected

- Deadlock storm as the out-of-scope case: arguably a lock problem, so
  `undetermined` versus `lock_wait` is unscorable.
- `GET_LOCK` pile-up for H1: a lock wait in plain language, same ambiguity.
- Cartesian-join runaway query for H1: pins CPU on sixty sessions and risks
  the lab.
- Application sending failing statements for H3: the database is fine, so
  `healthy` versus `undetermined` is unclear.
- Stopping the workload as a "traffic vanished" case: the `lab_health` gate
  treats a dead workload as unhealthy.

## 3. Completion criteria

Each is a lab test with a definite pass or fail. None runs a diagnosis backend.

1. For every new scenario, inject then clear leaves `SHOW CREATE TABLE`
   identical for all four tables and row counts unchanged.
2. For every new scenario, the `lab_health` gate passes within 60 s of stop.
3. H1: within 40 s a new `shop_app` connection is refused with error 1040.
4. H1: at that moment at least 40 processlist rows have `Command = Query` and
   the `SLEEP` statement, and `data_lock_waits` has zero rows.
5. H1: within 15 s of stop, connections are back under 10.
6. H2: within 30 s, the unindexed statement's `EXPLAIN` shows a full scan and
   `data_lock_waits` shows a wait on `orders`, in the same sample.
7. H3: within 5 s a `shop_app` `UPDATE` is rejected with the read-only error
   and a `shop_app` `SELECT` on the same table succeeds.
8. H3: after stop, `SELECT @@global.read_only` returns 0.
9. H3: after the injector is killed without warning, `clear` leaves
   `@@global.read_only` at 0.
10. H3: with `read_only` on and no injector running, the `lab_health` gate
    reports unhealthy.
11. H4: within 20 s, `data_lock_waits` has at least one row on `customers` in
    at least 15 of 20 samples taken 0.5 s apart.
12. H4: with a temporary index on `customers (full_name)`, the same 20 samples
    show zero lock waits on `customers`.
13. H5: within 20 s, `data_lock_waits` has rows on `products` and none on
    `orders`, and the workload's failed-update count does not rise.
14. H6: `Threads_connected` stays within 2 of its value at +5 s until +60 s,
    and a new `shop_app` connection succeeds at +30 s.
15. Criteria 3 to 14 pass in three consecutive runs.
16. Each holdout case file loads without error, and its `expected` matches the
    injector record.
17. The eval report prints holdout results under their own heading, and the
    basic and hard tier numbers are identical with and without the holdout set.
18. The existing isolation test still passes: diagnosis-side code reads
    nothing from the holdout location.

## 4. Open questions for the owner

1. **H1 label.** Is the hit `connection_exhaustion` with `undetermined`
   acceptable (recommended), or the reverse?
2. **H2 scoring.** Report it separately and unscored (recommended), or force
   it into the current format with `undetermined` as the hit?
3. **H2 and H3 need a new `kind`** and, for H2, an injector record that can
   hold two labels. Approve, or drop the cases?
4. **H3 extends the injector beyond the three fault classes** and makes
   `clear` reset a server setting. Approve, given the scope in `CLAUDE.md`?
5. **Where holdout cases live.** A separate directory and set, with no
   thresholds?
6. **H4 ground truth.** Accept "missing index, with lock wait as the symptom"
   if criterion 12 passes?
7. **H6 level.** Is about half the connection limit, flat, `healthy`?
8. **Schedule.** Six scenarios are about six small steps. The suggested cut
   order if time is short is H6, H5, then H3.

## 5. Limitations of the current contract and scoring

Separate findings; no change to the frozen design is proposed here.

1. **Single root cause.** Two independent faults cannot be reported correctly;
   the only conforming answer is `undetermined`, which discards what the
   system found.
2. **A correct `undetermined` has no rate.** Hit rate counts only cases whose
   expected answer is a fault. A case whose right answer is `undetermined` is
   in no denominator, and its correct answer raises the undetermined rate that
   exists to expose hiding.
3. **The closed set has no "slow for another reason".** A system that sees
   slow active statements has only `missing_index_slow_query` to reach for. H1
   measures how often it does so wrongly.
4. **Only `SELECT` is explained.** A missing index behind an `UPDATE` cannot be
   confirmed by `EXPLAIN` under the design. H4 may be unanswerable at the
   index level for that reason.
5. **One label per injector record**, so compound scenarios cannot be recorded
   as they are.
6. **The holdout is only as blind as the process.** The same assistant
   sessions that write the rule engine can read these cases, and the explorer
   had already seen four rule details in `KNOWN_ISSUES.md`.

Sources cited by the explorer (MySQL 8.0 Reference Manual): "Locks Set by
Different SQL Statements in InnoDB", "Transaction Isolation Levels",
"Miscellaneous Functions", "System Variable Privileges", "Privileges Provided
by MySQL".
