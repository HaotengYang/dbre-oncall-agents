# Eval cases

Data only. The code that runs these is in `eval_runner/`.

- `cases/`: one JSON file per case. The format is described in
  `docs/proposals/B-evals.md`, section 1.
- `sets.json`: named subsets. `quick` is what the `verify` skill runs.
- `thresholds.json`: minimum hit rate and maximum false-positive rate per
  tier. `null` means not set.

**Cases and thresholds are changed only with the owner's explicit consent.**
The diagnosis side must never read this directory.
