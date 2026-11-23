# MySQL on-call diagnosis agents

When MySQL misbehaves, this system collects read-only evidence, names the root
cause from a closed set, explains why, quotes handling steps from the team's
runbooks, and rehearses an index change on a sample copy before anything
touches the real table. An eval suite injects real faults into a lab MySQL and
measures how often the diagnosis is right.

## What is in the box

| Part | What it does | Code |
|---|---|---|
| Lab | Docker Compose: MySQL 8.0 (1 CPU, 1 GiB), Prometheus, mysqld_exporter, a steady synthetic workload | `docker-compose.yml`, `lab/` |
| Fault injector | Puts the lab into a known bad state and takes it back out: 3 fault classes, 2 to 3 variants each, cascades, healthy look-alikes, a hostile-input case | `fault_injector/` |
| Diagnosis | Read-only evidence collection, a frozen rule engine, an SQL agent, a metrics agent, an orchestrator (up to three rounds, then "undetermined, escalate"), runbook retrieval | `diagnosis/` |
| Model backend | The same evidence given to Claude models or any OpenAI-compatible API (Gemini), answers constrained to a JSON schema | `diagnosis/llm.py`, `diagnosis/llm_openai_compatible.py` |
| Change safety gate | Adds an index only: allowlist, sample rehearsal, risk check, rollback plan, human confirmation | `change_gate/` |
| Evals | 18 cases in two tiers, 4 holdout cases, 2 incident replays, end-to-end and replay layers, thresholds | `evals/` (data), `eval_runner/` (code) |

Design and decisions: `docs/DESIGN.md`, `docs/proposals/`. What is known not to
work: `docs/KNOWN_ISSUES.md`. How Claude Code was used: `AI_WORKFLOW.md`.

## Requirements

- macOS or Linux, Python 3.11 or newer, `make`, `git`.
- A Docker engine with Compose v2, either of:
  - **Docker Desktop**: install it, start it once, and `docker compose version` works.
  - **Colima** (no desktop app): `brew install colima docker docker-compose`, then
    `colima start --cpu 4 --memory 4`. Colima must be started again after a reboot.
    Colima shares only your home directory with containers by default, so clone
    the repository somewhere under `~` (a clone under `/tmp` starts MySQL with
    empty configuration and seed folders, and `make up` never becomes healthy).
    If `docker compose` is not found, add
    `{"cliPluginsExtraDirs": ["/opt/homebrew/lib/docker/cli-plugins"]}` to
    `~/.docker/config.json`.
- About 2 GB of disk for images. Ports 13306, 13307 and 19090 free on localhost.
- Optional: an Anthropic and/or Gemini API key, only for the model comparison.
  Everything else runs without one; the system then uses the rule engine.

## Set up

```bash
git clone <repository-url> dbre-oncall-agents
cd dbre-oncall-agents
cp .env.example .env          # then replace every replace-with-... value
make venv                     # creates .venv with pinned test and SDK dependencies
make up                       # starts the lab and waits until it is healthy (about a minute)
```

Passwords in `.env` may only contain letters, digits, `-` and `_`. `.env` is
ignored by git; never commit it. For the model comparison, add
`ANTHROPIC_API_KEY=...` and/or `GEMINI_API_KEY=...` to `.env`.

If the lab ever refuses to become healthy after a failed first start, run
`make reset` (it deletes the lab's data volume and rebuilds it from the seed).

## Try it

The whole story in one command, about a minute and a half: a startup check, a
cascading fault (an unindexed query piles up until connections run out), the
diagnosis, a change-gate rehearsal of the suggested index, and cleanup.

```bash
make demo
```

Or step by step: break the lab and diagnose it, in two terminals:

```bash
.venv/bin/python -m fault_injector list
.venv/bin/python -m fault_injector inject lock_wait.long_transaction      # terminal 1; Ctrl-C stops it
.venv/bin/python -m diagnosis "Order status updates are timing out."     # terminal 2
```

The report names the root cause, explains why, lists the evidence (captured SQL
is labelled untrusted), each agent's findings, and steps quoted from a runbook.
`python -m fault_injector clear` returns the lab to its baseline in any state.

Rehearse an index change (nothing is changed by `plan`):

```bash
.venv/bin/python -m change_gate plan --table orders --columns status,created_at \
  --query "SELECT id FROM orders WHERE status = 'paid' ORDER BY created_at DESC LIMIT 10"
```

`apply` takes the same arguments and asks for confirmation: `yes` for low
risk, the index name for high risk; a change that cannot be built online is
refused.

Scheduled inspection (risks only, no diagnosis): connection trend, transactions
open longer than a minute, and frequent statements that use no index.

```bash
.venv/bin/python -m diagnosis.inspection              # once
.venv/bin/python -m diagnosis.inspection --every 300  # every five minutes, until Ctrl-C
```

## Tests and evals

| Command | What it runs | Time |
|---|---|---|
| `make test` | Unit tests, no containers | seconds |
| `make test-lab` | Tests against the running lab | about 20 minutes |
| `make test-lifecycle` | Stop, start, reset, failed first start | about 4 minutes |
| `python -m eval_runner run --set quick` | The quick eval set, judged by count | about 6 minutes |
| `scripts/verify.sh` | Lab up, unit tests, clear, quick eval set; exits with the failing step's code | about 10 minutes |
| `python -m eval_runner run --set full` | All 18 cases, judged against `evals/thresholds.json` | about 35 minutes |
| `python -m eval_runner run --set holdout` | The 4 holdout cases (no thresholds) | about 8 minutes |
| `python -m eval_runner run --set incidents` | The 2 incident replays (no thresholds) | about 4 minutes |
| `python -m eval_runner record --set all` | Runs all 22 cases and saves the evidence for replay | about 40 minutes |
| `python -m eval_runner replay --set everything` | Gives the recorded evidence of all 24 cases to the approved models; costs money, capped at 10 USD in total | about 15 minutes |
| `python -m eval_runner retrieval` | Runbook retrieval hit rates (keyword) | seconds |
| `python -m eval_runner retrieval --retriever compare` | Keyword and vector retrieval side by side (needs the optional install below) | about 10 seconds |

Thresholds (owner's decision): basic tier 6/6, hard tier at least 5/8, no
false positives; the quick set allows no false positive and at most one miss.

## Results so far (rule engine, end to end)

| Tier | Hit rate | False positives |
|---|---|---|
| Basic (8 cases) | 6/6 | 0/2 |
| Hard (10 cases) | 5/8 and 6/8 in two runs | 0/2 |
| Holdout (4 cases) | 1/3 | 0/1 |
| Incident replays (2 cases), after the crash fix | GitHub: hit; Honeycomb: acceptable (`run`) and hit (`record`) | none in the set |

**Read the gap between the basic tier and the holdout set first.** The rule
engine and the 18 original cases were written by the same author, and it shows:
6/6 on the basic tier, 1/3 on holdout cases designed without sight of the rules.
The rule engine was frozen before the holdout set existed and was not changed
for it. Its value is as a no-API-key fallback and a regression gate, not as
evidence that rules generalise.

The Honeycomb replay is **no longer a blind test**: its first run exposed a
crash, the crash was fixed, and the later results come after that fix (see
Incident replays below).

Runbook retrieval, same 16 queries, nothing tuned:

| Queries | Keyword (BM25), hit@1 / hit@3 | Vector (all-MiniLM-L6-v2, local), hit@1 / hit@3 |
|---|---|---|
| Worded like the runbooks (8) | 8/8 / 8/8 | 8/8 / 8/8 |
| Worded differently (8) | 3/8 / 6/8 | 5/8 / 8/8 |

Vector retrieval is optional and runs locally, with no API call:
`.venv/bin/pip install -r requirements-vector.txt`, then
`.venv/bin/python scripts/fetch_embedding_model.py` (90 MB, pinned revision and
hash). Keyword retrieval stays the default for the SOP step.

**`make demo`** runs the whole chain live in about a minute and a half (82 s
measured): the cascade fault was diagnosed as `missing_index_slow_query` with
`connection_exhaustion` as its symptom, and the change gate rehearsed the
suggested `order_items(product_id, quantity)` at low risk, with the target
query using the new index on the sample.

**Inspection agent** (`python -m diagnosis.inspection`): lists risks only, no
root cause. Lab tests: an idle transaction holding about 50,000 row locks is
listed as high risk once it is a minute old, a frequent unindexed query on
`order_items` is listed, and the quiet baseline shows neither. A connection
metric that stops is reported as a risk, never as a clear.

### Model comparison (replay layer)

Every backend got the same recorded evidence for all 24 cases (basic, hard,
holdout, incident replays), so these numbers measure reasoning only, not
evidence collection; the end-to-end numbers above are a separate layer. Hit
rate and false-positive rate per tier, every run counted; stability is the
share of cases whose repeated answers agreed.

| Backend | Basic hit | Basic FP | Hard hit | Hard FP | Holdout hit | Holdout FP | Incidents hit | Incidents FP | Stability | Mean time | No answer | Spent |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Rule engine | 100% (6/6) | 0% (0/2) | 75% (6/8) | 0% (0/2) | 33% (1/3) | 0% (0/1) | 100% (2/2) | n/a | 1 run | <0.1 s | 0 | 0.00 USD |
| claude-haiku-5-5, 3 runs | 100% (18/18) | 0% (0/6) | 75% (18/24) | 0% (0/6) | 78% (7/9) | 0% (0/3) | 100% (6/6) | n/a | 96% (23/24) | 2.8 s | 0 | 0.04 USD |
| claude-sonnet-5-5, 1 run | 100% (6/6) | 0% (0/2) | 75% (6/8) | 0% (0/2) | 67% (2/3) | 0% (0/1) | 100% (2/2) | n/a | 1 run | 2.9 s | 0 | 0.20 USD |
| claude-opus-5-5, 1 run | 100% (6/6) | 0% (0/2) | 75% (6/8) | 0% (0/2) | 67% (2/3) | 0% (0/1) | 100% (2/2) | n/a | 1 run | 3.6 s | 0 | 0.43 USD |

- Order run, cheapest first: Gemini 3.8 Flash and Claude Haiku 5.5 three times
  per case, then Sonnet 5.5 and Opus 5.5 once. Cap 10 USD for everything.
- **Gemini 3.8 Flash did not run**: every call timed out (180 s) or was refused
  as overloaded or rate-limited, in two attempts. It has no answers to score.
- Spent: 1.05 USD by the ledger, an upper bound. 0.66 USD is measured Claude
  usage; 0.39 USD is six Gemini calls that returned nothing, charged at their
  worst case because their real cost is unknown.
- The incident rows are replays of the recordings made after the crash fix, so
  the Honeycomb case is not blind here either.

**What the data supports:** on the same evidence, the three Claude models
matched the rule engine on the basic, hard and incident cases and made no false
positive. They did better only on the holdout set (Haiku 7/9 over three runs,
Sonnet and Opus 2/3 each, the rule engine 1/3), which has three fault cases, so
the difference rests on very few cases. Haiku was the cheapest and fastest.

Reproduce: `python -m eval_runner replay --set everything --models <model> --repeats <n>`,
then `python scripts/replay_table.py <reports>`.

### Incident replays

Two cases in `evals/incident-replays/` reproduce the pattern of a public
incident in the lab. Like the holdout set, they have no thresholds, are
reported in their own section, and nothing was tuned to them. The alerts are
reworded, not quoted.

| Case | Source | What the lab does | Expected | Rule engine |
|---|---|---|---|---|
| `incident-github-2022-03` | [GitHub, 2022-03](https://github.blog/news-insights/company-news/an-update-on-recent-service-disruptions/) | The index behind the order-history query is gone; at peak, each new request opens a connection and runs the now-scanning query until connections run out | `missing_index_slow_query`, symptom `connection_exhaustion` | hit, symptom and index named |
| `incident-honeycomb-2018-05` | [Honeycomb, 2018-05](https://honeycomb.io/blog/rds-performance-degradation-postmortem) | MySQL's CPU limit drops to 0.05 while clients keep opening connections | `connection_exhaustion` (`undetermined` acceptable) | first run: crash, an evidence query timed out (`diagnosis_error`); after the fix: `undetermined` (acceptable) in `run`, hit in `record`. **Not blind any more.** |

How the Honeycomb case differs from the incident: there, the managed RDS
MySQL instance itself degraded suddenly (P95 query time from 11 ms to over
1,000 ms, per the postmortem), load went past `max_connections`, and new
clients got `Error 1040: Too many connections`. The lab cannot degrade a
managed instance, so it starves the server of CPU, the closest way to make
every query slow at once without changing any query or index. The expected answer is `connection_exhaustion`
because that is what the evidence shows from inside: no missing index, no lock
wait, the server full. `undetermined` is also accepted, since the real cause
lies outside the closed label set (the same rule as holdout case H1). On its
first run the diagnosis crashed: at 0.05 CPU an evidence query timed out and
the error escaped. None of the 22 synthetic cases had reached that path. It
was fixed as error handling, not as a rule change (the frozen rules are
untouched): a failed read now answers `undetermined`, names the read that
failed and why, and asks for a human. Because this case prompted the fix, its
later results do not count as a blind test.

## Real-world grounding

The eval scenarios are synthetic. Each fault class is mapped below to public
material that shows the same or a similar pattern. Evidence level: *real
incident* (a company's own postmortem), *customer case* (a consultancy's
anonymised case), *mechanism demo* (a reproduction of the mechanism),
*troubleshooting guide*.

| Fault type (lab) | Source | Link | Evidence level | Correspondence |
|---|---|---|---|---|
| Slow query exhausts connections (`cascade.slow_query_exhausts_connections`) | GitHub, 2022-03-16: poor query performance exhausted the database proxy's connections; an index was added the next day | [github.blog](https://github.blog/news-insights/company-news/an-update-on-recent-service-disruptions/) | real incident | same pattern |
| Slow query exhausts connections | Honeycomb, 2018-05-03: the database slowed down, then the application got `Error 1040` | [honeycomb.io](https://honeycomb.io/blog/rds-performance-degradation-postmortem) | real incident | same pattern |
| Slow query overloads the primary (`missing_index.*`) | GitHub, 2021-08-10: a poorly performing query plus application retries overwhelmed the primary | [github.blog](https://github.blog/news-insights/company-news/github-availability-report-august-2021/) | real incident | similar |
| Schema change stuck on a metadata lock, later queries queue (`lock_wait.blocked_schema_change`) | GitHub, 2021-11-27: officially a "semaphore deadlock"; the final rename of a large-table migration broke many read replicas | [github.blog](https://github.blog/2021-12-01-github-availability-report-november-2021) | real incident | similar |
| Schema change stuck on a metadata lock | Percona, 2019: in an unnamed customer's load test, an ALTER on a child table waited for hours for the parent table's metadata lock through a foreign key, blocking reads of the child table | [percona.com](https://www.percona.com/blog/alters-foreign-keys-and-metadata-locks-oh-my/) | customer case | similar |
| Schema change stuck on a metadata lock | Percona, 2022: a long query holds the lock, the ALTER waits, later SELECTs queue behind the ALTER; every online DDL tool faces this | [percona.com](https://www.percona.com/blog/online-ddl-tools-and-metadata-locks/) | mechanism demo | same pattern |
| Long transaction causes row-lock waits (`lock_wait.long_transaction`) | No public postmortem found. Percona, 2011: uncommitted modifying transactions block others until the lock wait timeout | [percona.com](https://www.percona.com/blog/how-to-debug-long-running-transactions-in-mysql/) | troubleshooting guide | same pattern |

## Limitations

- **Two holdout cases were not built** (owner's decision for time): two
  unrelated faults at once, which the single-root-cause contract cannot score
  fairly, and a read-only server, which would need the injector to change
  server settings and could leave the lab broken if interrupted.
- **One root cause per diagnosis.** Two independent faults can only be reported
  as `undetermined` or as one of them.
- **The inspection agent has no eval cases**; it is covered by unit tests and three lab tests only.
- **Vector retrieval is optional and not the SOP default**; it still misses 3 of 8 reworded queries at rank 1.
- **Primary/replica replication is out of scope.**
- **The lab is small** (200,000 orders); a missing index shows as rows examined
  and CPU, not as multi-second queries.
- Everything else known: `docs/KNOWN_ISSUES.md`.

## Layout

```
lab/                 lab MySQL config, schema, seed, accounts; Prometheus; workload
fault_injector/      faults and scenarios, the injector engine and CLI
lab_health/          the shared "wait for a healthy baseline" gate
diagnosis/           evidence, EXPLAIN safety, rule engine (frozen), agents, orchestrator, model backend, retrieval
change_gate/         allowlist, rehearsal, risk, apply
eval_runner/         case loading, runner, scoring, reports, thresholds, record and replay
evals/               cases/, holdout/, retrieval/, sets.json, thresholds.json (data only)
runbooks/            ten runbooks used by the SOP step
tests/unit, tests/lab
docs/                design, proposals, progress, known issues, AI notes
.claude/             subagents (explorer, implementer, reviewer) and the verify skill
```
