# Proposal B: eval system

Status: approved by the owner on 2026-11-20 and implemented on 2026-11-21, with
three deviations recorded in `docs/PROGRESS.md`: case files are JSON, the code is in
`eval_runner/` (the command is `python -m eval_runner`), and the no-admin tier
uses a separate account. Scoring
rules already approved in `docs/DESIGN.md` section 7 are restated here, not
changed.

## What it is

A runner that puts the lab into a known state, asks the diagnosis system what
is wrong, and scores the answer against ground truth. It produces the numbers
by which every later change (a rule, a prompt, a model) is judged.

```
python -m evals run --set quick            # a few cases, used before merging
python -m evals run --set full             # every case
python -m evals run --case <case_id>       # one case
python -m evals report                     # print the last report again
```

## 1. Case format

One YAML file per case under `evals/cases/`. A case says what to do to the lab,
what the system is told, and what a correct answer is.

```yaml
id: cascade-lock-wait-exhausts-connections
tier: hard                 # basic | hard
kind: cascade              # see the table below
scenario: cascade.lock_wait_exhausts_connections   # an injector id, or null
observe_seconds: 45        # how long the scenario runs before diagnosis starts
admin_port_available: true # false exercises the "outside signals only" tier
alert: "Checkout is timing out and the app reports it cannot get a database connection."
expected:
  root_cause: lock_wait
  also_acceptable: []      # for example [undetermined] on incomplete evidence
  symptoms: [connection_exhaustion]
```

| `kind` | What the lab is doing | Expected answer |
|---|---|---|
| `single_fault` | One fault, fully developed. | Its root cause. |
| `healthy` | Nothing. Normal workload only. | `healthy` |
| `healthy_lookalike` | Something that looks alarming and is harmless. | `healthy` |
| `cascade` | A fault whose visible effect is a different anomaly. | The originating fault; the other anomaly belongs in `symptoms`. |
| `incomplete_evidence` | A fault that started seconds ago. | The fault, or `undetermined`. |
| `hostile_input` | A fault, plus a statement carrying text addressed to the model. | Same as without that text. |
| `metrics_gap` | Metrics disappear (the exporter cannot reach MySQL). | Never `healthy`. |

The `alert` is deliberately worded as an application engineer would write it,
not in the vocabulary of the runbooks.

## 2. Ground truth

- `expected` in the case file is the ground truth. It is written by hand and
  reviewed by the owner.
- The injector's own record (`var/injections.jsonl`) is used for two things
  only: the onset time, and a consistency check. If the record's root cause or
  symptoms differ from the case's `expected`, the case is reported as
  **invalid** and is not scored. A mismatch means the case or the injector is
  wrong, not the diagnosis.
- The diagnosis system receives the alert text and its own access to the lab.
  It never receives the case file, the scenario id or the injection record.
  A test enforces that diagnosis-side code imports nothing from `evals` or
  `fault_injector` and reads nothing under `var/` or `evals/`.

## 3. How one case runs

1. Wait for a healthy baseline through the shared gate (`lab_health`).
2. Start the scenario and let it run for `observe_seconds`.
3. Call the diagnosis system with the alert. Record the answer, the time taken
   and, for model backends, tokens and cost.
4. Stop the scenario. Wait for a healthy baseline again.
5. Score.

If step 1 or 4 times out, the run stops and says so. It does not continue on a
lab that is not at baseline, because every later case would be contaminated.

The diagnosis system is called through one function, so the rule engine and
every model backend are scored by exactly the same code:

```
diagnose(alert_text, lab_access) -> Diagnosis   # the contract in DESIGN.md section 3
```

## 4. Scoring

Each answer falls into exactly one outcome:

| Outcome | When |
|---|---|
| `hit` | The answer equals `expected.root_cause`. |
| `acceptable` | The answer is in `also_acceptable` (in practice `undetermined` on incomplete evidence). Counted separately, not as a hit. |
| `symptom_as_cause` | Cascade only: the answer is one of `expected.symptoms`. |
| `false_positive` | The expected answer is `healthy` and the answer names a fault. |
| `undetermined_on_healthy` | The expected answer is `healthy` and the answer is `undetermined`. A mild false alarm. |
| `missing_metric_read_as_healthy` | `metrics_gap` only: the answer is `healthy`. |
| `miss` | Anything else. |

Reported per tier and per backend:

| Number | Definition |
|---|---|
| Hit rate | hits / cases whose expected answer is a fault |
| False-positive rate | false positives / cases whose expected answer is `healthy` |
| Symptom-as-cause rate | symptom_as_cause / cascade cases |
| Undetermined rate | answers of `undetermined` / all cases |
| Undetermined-on-healthy rate | as named / healthy cases |
| Symptoms listed | cascade hits whose `symptoms` contain the expected symptom / cascade hits |
| Onset error | median seconds between the reported onset and the injector's start time |

A system that answers `undetermined` to everything scores a hit rate of 0 and
an undetermined rate of 100%. Both numbers are always printed together.

**Repeats and stability.** A case can be run N times. Accuracy is the mean
over runs. Stability is the share of cases whose N answers are all the same.
The rule engine is deterministic given the same evidence, so end-to-end
variation there comes from the lab, which is itself worth knowing.

## 5. Two layers, reported apart

| Layer | What runs | What it measures |
|---|---|---|
| End-to-end | Steps 1 to 5 above against the live lab. | The whole chain, including evidence collection. |
| Replay | Evidence recorded once per case is handed to each backend. | Model reasoning only. Used for multi-model comparison (step B4). |

The report has one section per layer. A replay number is never printed in the
end-to-end section.

## 6. Proposed case set (18 cases)

| Tier | Cases |
|---|---|
| Basic (8) | The six single faults, fully developed; two healthy cases. |
| Hard (10) | Two cascades; two healthy lookalikes; three incomplete-evidence cases, one per fault class; one hostile input; two metrics gaps. |

The two metrics-gap cases are the idle flood with the admin port available
(expected: `connection_exhaustion`, found by going in through the admin port)
and the same with `admin_port_available: false` (expected:
`connection_exhaustion`, with `undetermined` also acceptable; `healthy` is the
error this case exists to catch).

## 7. Thresholds and protection

- `evals/thresholds.yaml` holds the minimum hit rate and maximum false-positive
  rate per tier. It is created empty of numbers. The owner fills it in after
  seeing the rule-engine baseline (step B3).
- A run that falls below a threshold exits with a failure, which is what makes
  the `verify` skill a gate.
- Cases and thresholds change only with the owner's explicit consent.

## 8. Report

Printed as tables to the terminal and written as JSON to `var/reports/`
(ignored by git). Every case line shows: case id, expected, answered, outcome,
seconds taken. Invalid cases and gate timeouts are listed separately so they
cannot be mistaken for misses.

## Completion criteria

For step B2 (runner, plus rule engine for one fault class):

1. `python -m evals run --case <id>` runs one case end to end and leaves the
   lab at baseline, including when the diagnosis function raises.
2. Each outcome in section 4 is produced by a unit test with a hand-written
   answer, and each rate is checked against a hand-computed value.
3. A case whose `expected` disagrees with the injector's record is reported as
   invalid, not as a miss.
4. A case file with an unknown field, a missing field or an unknown label is
   rejected when loaded, with the file and field named.
5. The report shows end-to-end results only, under an end-to-end heading.
6. A deliberately wrong diagnosis function (always answers `healthy`) scores a
   hit rate of 0 and a false-positive rate of 0 on a real run. An
   always-`connection_exhaustion` function scores a symptom-as-cause rate of
   100% on the cascades. These two runs prove the scoring is wired to reality.

For step B3: every case in section 6 exists, the rule engine runs on all of
them, and the baseline numbers are given to the owner.

## Decisions needed from the owner

1. **Healthy lookalikes need two new injector scenarios that are not faults.**
   Proposed: `healthy.high_load` (three times the normal traffic, all indexed,
   all fast) and `healthy.slow_report` (one harmless report query every ten
   seconds that takes a few hundred milliseconds). They would carry the label
   `healthy`. This extends the fault injector beyond step A5.
2. **A quick set and a full set.** One end-to-end case takes about one and a
   half to two minutes (settle, observe, diagnose, settle). Eighteen cases are
   about 35 minutes. Proposed: `verify` before merging runs a quick set of
   about six cases (one per fault class, one cascade, one healthy, one metrics
   gap; about ten minutes); the full set runs on demand and before submission.
3. **The case count of 18** and the split in section 6.
4. **Onset error is reported, not scored.** It does not turn a hit into a miss.
5. **Repeats.** Proposed default: 1 run per case end-to-end, 5 runs per case in
   replay for model comparison.
