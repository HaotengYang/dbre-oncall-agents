---
id: RB07
title: Disk filling up
causes: other
---
# Disk filling up

Signs: free space on the data volume falls; writes fail with "No space left on device" or "table is full"; binary logs or temporary files grow.

1. Find what grows: data files, binary logs, temporary tables, the slow log.
2. Purge binary logs older than the retention you need (PURGE BINARY LOGS), never by deleting files.
3. Look for a runaway query creating large temporary tables.
4. Add space or move logs before writes stop.
