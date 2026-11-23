---
id: RB06
title: Deadlocks between transactions
causes: lock_wait
---
# Deadlocks between transactions

Signs: errors "Deadlock found when trying to get lock"; one transaction is rolled back automatically; often bursts under concurrent writes to the same rows in a different order.

1. Read the latest deadlock section of SHOW ENGINE INNODB STATUS.
2. Identify the two statements and the order in which they lock rows.
3. Application fix: lock rows in a consistent order, keep transactions short, retry on deadlock.
4. Check that the statements use an index: a scan locks many more rows and deadlocks more.
