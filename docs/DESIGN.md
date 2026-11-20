# Design

Status: revision 2, incorporating the owner's review of 2026-11-19. Awaiting
owner re-review. Nothing below is implemented yet.

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
| SOP agent | Retrieve the relevant handling steps from about ten runbooks, through a retrieval interface with two implementations (keyword, vector). | Inventing steps that are not in a runbook. |
| Inspection agent | Run scheduled checks and report risks before they page anyone. | Reacting to alerts. |
| Orchestrator | Receive the alert, dispatch agents, merge findings into one report. Stop after three rounds. | Reasoning about raw evidence itself. |
| Change safety gate | For an "add index" change only: rehearse on a shadow table, assess risk, produce a rollback plan, require human confirmation when risk is high. | Any other kind of change. |
| Eval system | Run labelled cases, report hit rate and false-positive rate, compare rule engine vs LLM, compare models on latency, accuracy, stability and cost, compare keyword vs vector retrieval. Report end-to-end and replay numbers separately. | Being modified to make numbers look better. |

### Diagnosis contract

Every diagnosis is one value from a closed set, plus supporting detail:

- `root_cause`: `missing_index_slow_query` | `lock_wait` |
  `connection_exhaustion` | `healthy` | `undetermined`
- `rationale`: a short paragraph in plain language explaining why this cause
  was chosen and why the alternatives were ruled out. The label is for scoring;
  the paragraph is for the engineer reading the report.
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

**Runbook retrieval behind an interface: keyword by default, vector optional.**
Keyword scoring is the default because it has no dependencies, is
deterministic, and keeps the no-API-key path fully working. Its known weakness
is vocabulary mismatch: an alert says "requests are piling up" while the
runbook says "connection exhaustion". Vector retrieval handles that case but
needs an embedding model. Rather than argue which is better, the project
measures it: about ten runbooks, eval queries that deliberately include alerts
worded differently from the documents, and a report of retrieval hit rate for
both implementations side by side. Cost: two implementations and an optional
dependency.

**Hand-written orchestration loop, no agent framework.** The control flow is
small: dispatch, collect, check agreement, at most three rounds. Writing it
directly keeps every step visible and testable, and matches the project rule of
using only what Claude Code ships with.

**Evals run against a real MySQL, not mocked evidence.** End-to-end cases
inject a real fault and diagnose a real database, because a diagnosis that only
works on hand-written fixtures proves little. Cost: runs take minutes, not
seconds.

**Two layers of numbers, never mixed.** Model comparison does not re-inject
faults for every model. The evidence for each case is recorded once and
replayed to every model, so all models see identical input, stability can be
measured as agreement across repeated runs, and cost stays bounded. The report
therefore has two separate sections:

- *End-to-end*: inject -> collect -> diagnose -> score. Measures the whole
  chain, including evidence collection and timing.
- *Replay*: recorded evidence -> model -> score. Measures model reasoning only.

A replay number says nothing about whether evidence collection works, so the
two are always labelled and reported apart.

## 5. What is deliberately not built, and why

| Not built | Why |
|---|---|
| Primary/replica replication | Owner's decision. The three fault classes do not need it, and it adds setup and failure modes to a four-day project. May be revisited. |
| Automatic remediation | Killing queries or changing configuration on a diagnosis that may be wrong amplifies mistakes. The system recommends; a human acts. |
| Change types other than adding an index | Each change type needs its own rehearsal and rollback logic. One done properly is worth more than several done loosely. |
| Fault classes beyond the three | Depth per class (several variants, healthy cases, false-positive measurement) matters more than breadth. |
| A vector database service | Vector retrieval is optional and, at about ten runbooks, an in-memory index is enough. A separate database service would add setup without changing the result. |
| Web UI | The output is a report on the command line. A UI adds code without adding evidence of diagnostic quality. |
| Multi-instance or cloud deployment | The lab is one local MySQL. Deployment is not what is being demonstrated. |

## 6. Open questions for the owner

1. **Language.** The fault injector is written in Python first; the owner then
   chooses Python or Java for the rest. This document is language-neutral.
2. **Which models to compare, and the budget.** Multi-model comparison costs
   real API money (cases x repeats x models). The owner approves the model list
   and a spending cap before any paid run.
3. **Embedding source for vector retrieval.** A local embedding model keeps
   the project free to run but adds a large download; an embedding API is small
   to set up but costs money and needs a key. To be proposed with the SOP agent
   module.
4. **Thresholds.** The hit-rate and false-positive thresholds under `evals/`
   are the owner's to set once the rule-engine baseline numbers exist.

## 7. Decisions log

| Date | Decision | Decided by |
|---|---|---|
| 2026-11-19 | Closed set of root causes, plus a plain-language rationale in every report. | Owner |
| 2026-11-19 | Agents are read-only; model output is never executed. | Owner |
| 2026-11-19 | Safety gate uses an allowlist. | Owner |
| 2026-11-19 | Retrieval is an interface with keyword (default) and vector (optional) implementations; about ten runbooks; evals include wording-mismatch queries and compare both. Replaces the draft's keyword-only design. | Owner |
| 2026-11-19 | Evals run against a real MySQL. | Owner |
| 2026-11-19 | Record evidence once and replay it for model comparison; end-to-end and replay numbers reported separately. | Owner |
| 2026-11-19 | Model list and spending cap are approved by the owner before any paid run. | Owner |
