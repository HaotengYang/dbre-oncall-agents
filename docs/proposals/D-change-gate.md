# Proposal D: change safety gate

Status: written for the record; the owner asked for D1 to be followed directly by
D2 and D3 without a stop. Reviewed until the reviewer approves (CLAUDE.md).

## What it does

It takes one proposed change, adding a secondary index, and before anything
touches the real table it:

1. **Accepts or rejects the request** against an allowlist with exactly one
   shape. The gate never executes text it was given; it builds its own
   statement from a parsed, validated request.
2. **Rehearses** the change on a capped sample copy of the table in the
   separate `shop_shadow` database, and checks that the target query would use
   the new index there.
3. **Assesses risk** from live data: table size, long transactions holding a
   metadata lock on the table, online capability, redundancy with an existing
   index.
4. **Writes the rollback plan** before offering the change.
5. **Applies** only after a human confirms; a high-risk change needs a stronger
   confirmation (typing the index name).

```
python -m change_gate plan  --table orders --columns customer_id,created_at \
                            --query "SELECT id FROM orders WHERE customer_id = 7 ORDER BY created_at DESC"
python -m change_gate apply --table orders --columns customer_id,created_at   # asks for confirmation
```

The candidate index from a diagnosis report (`table(columns)`) can be passed
straight in.

## The one allowed shape

A request is: a table name, one to four column names, an optional index name.
Accepted only if:

| Check | Rule |
|---|---|
| Table | Exists in `shop` (read from `information_schema`), plain name. |
| Columns | 1 to 4, each exists in that table, no duplicates, plain names. |
| Index name | `idx_` followed by lower-case letters, digits, underscores; at most 64 characters; not already used on the table. Generated if not given. |
| Not redundant | No existing index starts with the same columns in the same order. |

The gate then renders the only statement it ever runs on `shop`:

```
CREATE INDEX <name> ON shop.<table> (<col>, ...) ALGORITHM = INPLACE LOCK = NONE
```

and its rollback:

```
DROP INDEX <name> ON shop.<table>
```

Free SQL text is accepted only through `parse_index_statement`, which matches
exactly `CREATE INDEX name ON [shop.]table (cols)` (optional trailing
`ALGORITHM`/`LOCK` clauses, optional semicolon) and turns it into a request;
everything else (UNIQUE, FULLTEXT, prefix lengths, expressions, functional
indexes, DESC, ALTER, DROP, other schemas, multiple statements, comments) is
rejected.

## Rehearsal

Run as `shop_gate`, which can only create and drop tables in `shop_shadow` and
add or drop indexes in `shop` (database privileges, tested).

1. `CREATE TABLE shop_shadow.<table> LIKE shop.<table>` (dropped first if left over).
2. Copy at most `SAMPLE_ROW_LIMIT` (20,000) rows, ordered by primary key, in
   chunks of 2,000 with a short pause between chunks. Before each chunk, if more
   than `MAX_RUNNING_SESSIONS` sessions are running on the server, stop and
   report "rehearsal abandoned, server busy".
3. Build the index on the shadow table with the same statement shape. If
   MySQL refuses `ALGORITHM=INPLACE, LOCK=NONE` there, it would refuse on the
   real table too (same structure): reported as "cannot be added online".
4. If a target query is given and passes the EXPLAIN allowlist, explain it in
   `shop_shadow` with and without the new index and report whether the index
   is chosen.
5. Drop the shadow table, whatever happened.

Every report says what the rehearsal cannot show: build time on the sample
does not predict build time on the full table, and the plan on a sample may
differ from the plan on the full table.

## Risk assessment

Read with the read-only diagnosis account.

| Check | High risk when |
|---|---|
| Table size | estimated rows above 1,000,000 or data above 1 GiB |
| Long transaction | a transaction older than 10 s whose session holds a metadata lock on the table |
| Online capability | the rehearsal could not build the index with `INPLACE, LOCK=NONE` (blocks) |
| Redundancy | rejected at the allowlist step |

Low risk: plain confirmation (`yes`). High risk: the operator types the index
name. "Cannot be added online" is blocked, not just high risk: the gate never
issues a table-copying, write-blocking build.

## Apply

As `shop_gate`, with `lock_wait_timeout` of 5 s so that a metadata-lock wait
gives up instead of queueing all traffic behind it. The rollback statement is
printed before the change runs. After it runs, the gate confirms the index
exists.

## Completion criteria

1. Property tests: random statements and random requests (thousands, seeded)
   are never accepted unless they are exactly the allowed shape; anything
   accepted renders to the canonical statement, and that statement re-parses to
   the same request.
2. Every rejection reason is a fixed string that never quotes the input.
3. A lab test rehearses `orders(customer_id, created_at)`-style and new
   indexes: the shadow table holds at most the sample limit, the shadow index
   is built, the target query uses it, and the shadow table is gone afterwards,
   also when the rehearsal fails half-way.
4. A lab test with an idle transaction holding a metadata lock on the table
   reports high risk, and applying then requires the typed index name.
5. A lab test applies a low-risk index and then runs the rollback; the schema
   is back to baseline.
6. The gate never runs anything on `shop` other than the two rendered
   statements (checked by recording every statement sent).
7. `make test` passes, including the frozen-rule and isolation tests.

## Not built

Any change type other than adding an index; unique indexes (a duplicate would
fail the build half-way); index changes on tables outside `shop`; automatic
apply without a human.
