---
id: RB03
title: Everything on a table hangs behind a schema change
causes: lock_wait
---
# Everything on a table hangs behind a schema change

Signs: every statement on one table, including simple reads, waits in state "Waiting for table metadata lock"; a migration or ALTER / CREATE INDEX was just started; no row-lock waits.

1. Find the pending DDL statement in the processlist.
2. Find what blocks it: an open transaction that has touched the table (INNODB_TRX, metadata_locks).
3. Cancel the DDL first (end its session). Traffic resumes at once; the migration can be retried later.
4. Then deal with the open transaction that blocked it.
5. Retry the migration with a short lock_wait_timeout, at a quiet time, with ALGORITHM=INPLACE, LOCK=NONE where possible.
