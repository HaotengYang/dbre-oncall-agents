---
id: RB02
title: Sessions blocked on row locks
causes: lock_wait
---
# Sessions blocked on row locks

Signs: updates time out with "Lock wait timeout exceeded"; sessions wait in data_lock_waits; one transaction holds many row locks; reads usually still work.

1. List waiting sessions and the transaction blocking them (data_lock_waits joined with INNODB_TRX).
2. Look at the blocker: how long it has been open, how many rows it locks, and whether its session is idle (Sleep).
3. An idle session holding locks is usually a forgotten transaction or a stalled batch job. Find its owner from the host and user.
4. Ask the owner to commit or roll back. If impossible and impact is severe, a senior engineer may end that one session; a large rollback can take time.
5. Do not restart the server: it rolls back every open transaction and takes longer.
6. Afterwards, add a transaction timeout or idle-in-transaction alert for that application.
