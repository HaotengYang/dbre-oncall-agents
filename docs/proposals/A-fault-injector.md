# Proposal A: fault injector

Status: proposal, awaiting owner confirmation. No code exists yet.

## What it is

A Python command-line tool that puts the lab MySQL into a known bad state on
demand, and takes it back out. It knows nothing about diagnosis. Its second
job is to record exactly what it did and when, because that record is the
ground truth the evals score against.

```
python -m fault_injector inject <fault_id>     # runs until Ctrl-C or --duration
python -m fault_injector clear                 # return the lab to baseline
python -m fault_injector status                # what differs from baseline now
python -m fault_injector list                  # available faults
```

A fault id is `<class>.<variant>`, for example `missing_index.dropped_index`.

## Design decisions

**1. The injector runs in the foreground while a fault is active.** Holding a
lock or holding connections needs a live process. `inject` therefore stays
running until Ctrl-C or `--duration` and cleans up on the way out. The eval
runner uses the same code as a Python context manager. Rejected: a background
daemon with a state file (more moving parts; a crashed daemon leaves unknown
state).

**2. Fault traffic runs as the application account.** Slow queries, lock
holders and connection floods connect as `shop_app`, because in a real
incident the bad traffic comes from the application. The diagnosis system must
not be able to cheat by looking for a special user name.

**3. One new account, `shop_fault`, for schema faults only.** Dropping an index
needs the `INDEX` privilege, which `shop_app` must not have. `shop_fault` gets
`INDEX` on `shop.*` and nothing else. This changes the lab init scripts, so it
needs one `make reset`.

**4. `clear` restores the baseline from a definition, not from memory.** The
baseline index definitions are kept in one file. `clear` compares the live
schema with it and recreates whatever is missing. It does not depend on
knowing which fault was injected, so it also works after the injector was
killed. Lock holders and held connections end when the injector process ends,
so only schema changes need restoring.

**5. One fault at a time, unless the fault is a declared cascade.** A second
`inject` while one is active is refused. Cascading faults (step A5) are single
fault ids that start two things in a defined order.

**6. The injector refuses to run against anything that is not the lab.** The
lab init creates a marker (`lab_meta.environment` with one row naming the
lab). The injector checks it before every action. A fault injector pointed at
a real database is the worst mistake this tool could make.

**7. Every injection writes a record.** One JSON line per injection in
`var/injections.jsonl` (ignored by git): fault id, root-cause label, start
time, end time, and what was changed. Evals read onset time and label from it.

## Fault classes and the first variant of each

| Step | Fault id | What it does | How it is cleared |
|---|---|---|---|
| A2 | `missing_index.dropped_index` | Drops `idx_orders_customer_created`. The workload's "recent orders for a customer" query (a quarter of all traffic) turns from an index lookup into a scan of 200,000 rows. No extra traffic is added. | Recreate the index. |
| A3 | `lock_wait.long_transaction` | Opens a transaction, locks a quarter of the `orders` rows with `SELECT ... FOR UPDATE`, and holds it. Workload updates to those rows wait and then fail. | Roll back and close. |
| A4 | `connection_exhaustion.idle_flood` | Opens idle connections as `shop_app` until about 55 of 60 are in use, and holds them. | Close them. |

More variants and the cascading faults are listed in step A5 and proposed then.

## Completion criteria

Each is checked by a lab test with a definite pass or fail.

**All faults**

1. `inject` followed by `clear` leaves the schema identical to the baseline
   (`SHOW CREATE TABLE` compared exactly) and the row counts unchanged.
2. `clear` is idempotent: running it twice, or on a healthy lab, changes
   nothing and exits successfully.
3. After `clear`, the lab's healthy-baseline tests pass within 60 seconds.
4. If the injector process is killed without warning, a later `clear` still
   restores the baseline.
5. A second `inject` while a fault is active is refused with a clear message
   and changes nothing.
6. Each injection writes a record with fault id, label, start and end time.
7. Pointed at a MySQL without the lab marker, the injector refuses and makes no
   change.

**A2, missing index**

8. Within 30 seconds of injection, rows examined per execution of the affected
   statement rise by at least 1,000 times (measured from two digest snapshots),
   and its `EXPLAIN` shows a full scan.
9. The measured effect on latency and CPU is reported to the owner as numbers.
   No threshold is asserted until the owner has seen them.

**A3, lock wait**

10. Within 20 seconds, `performance_schema.data_lock_waits` shows sessions
    blocked by the injector's session, and the workload's failed-update count
    rises.
11. Connections do not grow by more than 2: this variant is a lock wait, not a
    cascade.
12. Within 15 seconds of `clear`, no lock waits remain.

**A4, connection exhaustion**

13. Within 20 seconds, connections in use are at least 90% of the limit, and a
    new `shop_app` connection is refused with "too many connections".
14. Within 10 seconds of `clear`, connections are back under 10.

## Decisions needed from the owner

1. Approve adding the `shop_fault` account and the lab marker to the lab init
   (design decisions 3 and 6). Cost: two small init changes and a `make reset`.
2. Approve the foreground process model (design decision 1).
3. Approve the three first variants in the table above.

## Estimated effort

A2, A3 and A4 are each under 40 minutes. A2 is the largest because it also
builds the shared parts: command line, lab guard, baseline restore, records.
