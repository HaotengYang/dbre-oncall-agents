# Eval cases

Data only. The code that runs these is in `eval_runner/`.

- `cases/`: one JSON file per case. The format is described in
  `docs/proposals/B-evals.md`, section 1.
- `sets.json`: named subsets. `quick` is what the `verify` skill runs.
- `thresholds.json`: set by the owner on 2026-11-21.
  - `full`: per tier, minimum hit rate and maximum false-positive rate. Applied
    only to a run of the full set. Basic 0.83 (5 of 6; to be raised to 6 of 6
    once lock-wait evidence is stable), hard 0.625 (5 of 8), false positives 0.
  - `quick`: counts, because the quick set is too small for a rate: no false
    positive, at most one miss.

**Cases and thresholds are changed only with the owner's explicit consent.**
The diagnosis side must never read this directory.
