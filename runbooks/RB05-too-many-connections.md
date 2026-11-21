---
id: RB05
title: New connections refused, server full
causes: connection_exhaustion
---
# New connections refused, server full

Signs: applications cannot get a database connection; error 1040 "Too many connections"; the exporter may lose its connection and dashboards go blank.

1. Connect through the administrative port (admin_port) if available; it is not limited by max_connections.
2. Classify the sessions: idle, running, or waiting on locks. The cause is what most of them are doing.
3. Mostly waiting on locks: follow RB02 or RB03. Mostly running the same slow statement: follow RB01. Mostly idle: follow RB04.
4. If nothing can get in, treat blank dashboards as part of the incident, not as "no data".
5. Reduce incoming load at the application or load balancer while the cause is fixed.
