# Eval cases

Data only. The code that runs these is in `eval_runner/`.

- `cases/`: one JSON file per case. The format is described in
  `docs/proposals/B-evals.md`, section 1.
- `holdout/`: four holdout cases, designed without sight of the rule engine
  (`docs/proposals/holdout-cases.md`; owner chose H1, H4, H5, H6). Run with
  `--set holdout`, or `--set all` for full plus holdout. No thresholds apply.
  Nothing may be tuned to them.
- `sets.json`: named subsets. `quick` is what the `verify` skill runs.
- `thresholds.json`: set by the owner on 2026-11-21.
  - `full`: per tier, minimum hit rate and maximum false-positive rate. Applied
    only to a run of the full set. Basic 1.0 (6 of 6; raised from 5 of 6 on
    2026-11-21 after the basic tier scored 24 of 24 in three consecutive runs,
    as the owner had ordered), hard 0.625 (5 of 8), false positives 0.
  - `quick`: counts, because the quick set is too small for a rate: no false
    positive, at most one miss.

**Cases and thresholds are changed only with the owner's explicit consent.**
The diagnosis side must never read this directory.
