---
id: RB08
title: Database CPU pinned
causes: missing_index_slow_query
---
# Database CPU pinned

Signs: CPU near 100%; latency up across the board; more statements running at once than usual.

1. Do not restart: you lose the evidence and the cause comes back.
2. Find which statement family uses the most time in performance_schema digests.
3. Typical causes: a missing index or a changed plan (RB01), a cache in front of the database failing so traffic hits it directly, a batch job or report on the primary.
4. Contain: kill clearly read-only runaway queries, throttle the source, pause batch jobs.
5. Then fix the cause and add an alert on the statement family.
