---
id: RB10
title: Long-running transaction holding the undo history
causes: lock_wait
---
# Long-running transaction holding the undo history

Signs: History list length keeps growing; an old transaction appears in INNODB_TRX; purge falls behind; queries slow down gradually; other sessions may wait on its locks.

1. Find the oldest transaction in INNODB_TRX and its session.
2. Find the owner; ask them to finish it. A transaction idle for hours is usually a forgotten session in a client tool.
3. If it blocks others, follow RB02.
4. Afterwards set an idle transaction timeout for interactive clients.
