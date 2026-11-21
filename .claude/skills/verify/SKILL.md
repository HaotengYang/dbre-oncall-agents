---
name: verify
description: Full verification before merging a branch into main. Starts the lab, runs the unit tests, clears any leftover fault, runs the quick eval set against the live lab and prints the report. Use before every merge into main, and whenever asked whether the system still diagnoses correctly.
---

# verify

Full verification. Fast verification (unit tests only) is `make test` and runs
before every commit; this is the slower check that runs before a merge into
`main`. It takes about ten minutes.

## Run it

```bash
scripts/verify.sh
```

(`make verify` runs the same script, but make reports every failure as exit
code 2. Use the script when the exit code matters.)

It does, in order, and stops at the first failure with that step's exit code:

1. `make up`: start the lab and wait until every service is healthy.
2. `make test`: unit tests, no containers.
3. `python -m fault_injector clear`: return the lab to baseline in case an
   earlier run left a fault behind.
4. `python -m eval_runner run --set quick`: inject faults, diagnose, score,
   print the report.

If Docker is not running, start it first (`colima start`, or open Docker
Desktop) and run it again.

Before any case runs, the eval runner checks that the lab's diagnosis accounts
exist and behave as the cases assume. If it says the data volume predates an
account, run `make reset` once and start again.

## Read the result

The report has one section per tier, a line per case (expected, answered,
outcome), and the rates with their fractions, for example `83.3% (5/6)`.

The script prints which step failed. The exit code is that step's own:

| Failing step | Exit code | Meaning | What to do |
|---|---|---|---|
| none | 0 | Every step ran and no threshold that is set was broken. | Read the report before merging; see the note below. |
| unit tests | 1 (pytest) | A unit test failed. | Fix the code. Do not weaken the test. |
| quick eval set | 1 | A threshold in `evals/thresholds.json` was broken, or a case got no answer because the backend failed. | Do not merge. Find the cause in the code under test. |
| quick eval set | 2 | A case file or the thresholds file is malformed, or the lab's accounts are out of date. | Fix the file, or run `make reset`. |
| quick eval set | 3 | The lab did not return to baseline, or a case could not be run at all. The partial report is still printed and saved. | Run `python -m fault_injector clear`, then `make reset` if that does not help. |
| start the lab, clear | 2 | The lab did not start, or the injector refused. | Read the message. |

**Exit code 0 is not by itself "safe to merge".** If the run ends with "No
thresholds are set in evals/thresholds.json; nothing was checked", then no
number was judged: a hit rate of 0% would also exit 0. In that case report the
numbers and let the owner judge.

Report the numbers exactly as printed, with their fractions. Say which cases
missed and what they answered.

## Rules

- **Never change anything under `evals/` to make verify pass.** Cases and
  thresholds change only with the owner's explicit consent. If a case looks
  wrong, say so and stop.
- Do not weaken, skip or delete a test to get a pass.
- A failing verify is information. Report it as it is; do not rerun until it
  passes and report only the pass. If a rerun gives a different result, report
  both runs.
- To see the last report again without rerunning: `python -m eval_runner report`.
- The full case set (about 25 minutes) is `python -m eval_runner run --set full`.
  Run it on request and before submission, not on every merge.
