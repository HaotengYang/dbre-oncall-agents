---
id: RB09
title: Replica lag growing
causes: other
---
# Replica lag growing

Signs: Seconds_Behind_Source rises; reads from replicas return stale data.

1. Check whether the replica's SQL thread is running and what it is applying.
2. A large transaction on the source replays slowly: wait it out or split such jobs in future.
3. Check replica resources (IO, CPU) and parallel replication settings.
4. Route latency-sensitive reads to the source until lag recovers.
