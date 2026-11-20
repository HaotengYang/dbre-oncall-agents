# Design

Status: draft, awaiting owner review. Nothing below is implemented yet.

## 1. Problem

An on-call database engineer gets an alert ("API timeouts, MySQL CPU at 95%").
The first ten minutes go to the same manual steps every time: look at metrics,
look at running queries, find the matching runbook, decide what is safe to do.
When the fix is a schema change and there is no staging environment, the change
is applied straight to production.

This system automates the repeatable part of that work and keeps a human in
charge of the risky part:

1. Diagnose the root cause from live evidence.
2. Recommend handling steps from the team's runbooks.
3. Rehearse an index change on a shadow table before it touches the real table.
4. Measure how often the diagnosis is right, so every prompt or model change is
   judged by numbers instead of impressions.

## 2. Architecture

```
                 +--------------------------- lab (docker compose) -----------+
                 |  workload ---> MySQL <--- fault injector                   |
                 |                  |                                         |
                 |          mysqld_exporter ---> Prometheus                   |
                 +------------------|------------------|----------------------+
                                    | read-only SQL    | PromQL
                                    v                  v
alert / schedule --> Orchestrator --+--> SQL agent     Metrics agent
                         |          +--> SOP agent (runbooks/)
                         |          +--> Inspection agent (scheduled)
                         v
                  Diagnosis report ----> human
                         |
                         | proposed index (optional)
                         v
                 Change safety gate --> shadow rehearsal --> risk + rollback plan
                                                             --> human confirms

evals/ cases --> Eval runner --> inject fault --> run diagnosis --> score
```

Two reasoning backends sit behind one interface: a deterministic **rule engine**
and an **LLM**. Every agent can run on either. The rule engine is both the
no-API-key fallback and the baseline the LLM has to beat.

## 3. Modules and responsibilities

| Module | Responsibility | Explicitly not its job |
|---|---|---|
| Lab environment | Start MySQL, exporter, Prometheus and a steady synthetic workload with one command. | Simulating production scale. |
| Fault injector | Create and clear faults on demand. Three classes (missing-index slow query, lock wait, connection exhaustion), several variants each. Each injection carries a ground-truth label. | Diagnosing anything. |
| Evidence collectors | Read-only access to Prometheus and MySQL (processlist, lock waits, slow query log, EXPLAIN). Return structured data. | Interpreting the data. |
| Metrics agent | From metrics, name the anomaly type and when it started. | Looking at SQL. |
| SQL agent | From processlist, slow log and EXPLAIN, name the offending statements and suggest an index. | Applying the index. |
| SOP agent | Retrieve the relevant handling steps from five or six runbooks. | Inventing steps that are not in a runbook. |
| Inspection agent | Run scheduled checks and report risks before they page anyone. | Reacting to alerts. |
| Orchestrator | Receive the alert, dispatch agents, merge findings into one report. Stop after three rounds. | Reasoning about raw evidence itself. |
| Change safety gate | For an "add index" change only: rehearse on a shadow table, assess risk, produce a rollback plan, require human confirmation when risk is high. | Any other kind of change. |
| Eval system | Run labelled cases, report hit rate and false-positive rate, compare rule engine vs LLM, compare models on latency, accuracy, stability and cost. | Being modified to make numbers look better. |

### Diagnosis contract

Every diagnosis is one value from a closed set, plus supporting detail:

- `root_cause`: `missing_index_slow_query` | `lock_wait` |
  `connection_exhaustion` | `healthy` | `undetermined`
- `onset`: when the anomaly started
- `evidence`: the specific metrics and statements the conclusion rests on
- `recommended_steps`: steps quoted from a runbook, with the runbook named
- `rounds_used`: 1 to 3

`undetermined` is a first-class answer. If agents disagree or the evidence does
not support one cause after three rounds, the report says "undetermined,
escalate to a human" and lists what was checked.

## 4. Main trade-offs

**Rule engine and LLM, not LLM alone.** The rule engine costs nothing, is
deterministic, and runs without an API key, so the project is reproducible on
any machine. It also gives the evals a baseline: if the LLM path does not beat
simple rules on hit rate, it is not earning its cost and latency. Cost: every
agent has two implementations to maintain.

**Closed set of root causes, not free text.** A fixed enum makes "was the
diagnosis right" a comparison instead of a judgement call, which is what makes
hit rate and stability measurable. Cost: the system cannot name a cause outside
the set. That is accepted and covered by `undetermined`.

**Agents are read-only.** Diagnosis uses a MySQL account with no write
privileges, and no LLM output is ever executed as SQL. The only component that
can change the database is the change safety gate, and it only runs statements
it builds itself. Cost: the system recommends actions (for example "kill
session 42") but does not perform them.

**Allowlist in the safety gate, not a blocklist.** The gate accepts one
statement shape (add an index) and rejects everything else. A blocklist of
dangerous statements is only as good as the list; an allowlist fails closed.
This is what the property-based tests check: random SQL never gets through
unless it is exactly the permitted shape.

**Keyword retrieval for runbooks, not a vector database.** With five or six
runbooks, lexical scoring is accurate enough, deterministic, and needs no
embedding model or extra service. Embeddings pay off when the corpus is large
or the wording of queries and documents diverges; neither holds here. The
retrieval step sits behind an interface so it can be swapped later.

**Hand-written orchestration loop, no agent framework.** The control flow is
small: dispatch, collect, check agreement, at most three rounds. Writing it
directly keeps every step visible and testable, and matches the project rule of
using only what Claude Code ships with.

**Evals run against a real MySQL, not mocked evidence.** End-to-end cases
inject a real fault and diagnose a real database, because a diagnosis that only
works on hand-written fixtures proves little. Cost: runs take minutes, not
seconds. See open question 3 for how model comparison stays affordable.

## 5. What is deliberately not built, and why

| Not built | Why |
|---|---|
| Primary/replica replication | Owner's decision. The three fault classes do not need it, and it adds setup and failure modes to a four-day project. May be revisited. |
| Automatic remediation | Killing queries or changing configuration on a diagnosis that may be wrong amplifies mistakes. The system recommends; a human acts. |
| Change types other than adding an index | Each change type needs its own rehearsal and rollback logic. One done properly is worth more than several done loosely. |
| Fault classes beyond the three | Depth per class (several variants, healthy cases, false-positive measurement) matters more than breadth. |
| Vector database and embeddings | See trade-offs: the corpus is too small to justify them. |
| Web UI | The output is a report on the command line. A UI adds code without adding evidence of diagnostic quality. |
| Multi-instance or cloud deployment | The lab is one local MySQL. Deployment is not what is being demonstrated. |

## 6. Open questions for the owner

1. **Language.** The fault injector is written in Python first; the owner then
   chooses Python or Java for the rest. This document is language-neutral.
2. **Which models to compare, and the budget.** Multi-model comparison costs
   real API money (cases x repeats x models). The owner approves the model list
   and a spending cap before any paid run.
3. **Recorded evidence for model comparison.** Proposal: live injection for
   end-to-end tests, but for comparing models, record the evidence once per
   case and replay the same evidence to every model. That makes the comparison
   fair (identical input), makes stability measurable (same input, repeated
   runs), and keeps cost and time down. Needs owner approval because it shapes
   the eval design.
4. **Thresholds.** The hit-rate and false-positive thresholds under `evals/`
   are the owner's to set once the rule-engine baseline numbers exist.
