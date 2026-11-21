---
id: RB04
title: Connections climbing from a leaking pool
causes: connection_exhaustion
---
# Connections climbing from a leaking pool

Signs: the connection count rises steadily over minutes; most connections are idle; eventually new connections are refused with "Too many connections" (error 1040).

1. Group connections by user and host to find which application holds them.
2. Check that application's recent deploys and its pool settings (maximum size, idle timeout, leak detection).
3. Restart or roll back the leaking application instance; connections are released when it stops.
4. As a stopgap, a senior engineer may end idle connections from that host.
5. Do not simply raise max_connections: a leak fills any limit, and each connection costs memory.
