---
id: RB01
title: Slow queries from a missing index
causes: missing_index_slow_query
---
# Slow queries from a missing index

Signs: a frequently run statement reads far more rows than it returns; EXPLAIN shows access type ALL or a full index scan; CPU and read IO rise; latency of one query family climbs while others stay normal.

1. Find the statement family with the most rows examined per second in performance_schema statement digests.
2. Run plain EXPLAIN on its sample statement (never EXPLAIN ANALYZE in production).
3. Check whether the statement changed in a recent release or an index was dropped by a migration.
4. Propose an index: equality columns first, then one range or ORDER BY column.
5. Send the proposal through the change safety gate: shadow-table rehearsal, risk check, rollback plan.
6. While waiting, reduce load: rate-limit or disable the feature issuing the query.
7. After the index is added, confirm rows examined per execution dropped and latency recovered.
