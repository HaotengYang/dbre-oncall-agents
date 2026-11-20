# Progress

Updated at the end of every working session.

## Done

- 2026-11-19: Repository created; project rules recorded in `CLAUDE.md`.
- 2026-11-19: `docs/DESIGN.md` drafted (architecture, module responsibilities,
  trade-offs, non-goals).
- 2026-11-19: Private GitHub repository created; `main` and `docs/design` pushed.
- 2026-11-19: `docs/DESIGN.md` revised after owner review (revision 2).
- 2026-11-19: `docs/DESIGN.md` revision 3: hard eval tier and ground-truth
  rules, EXPLAIN safety, slow-query source and least privilege, sampled shadow
  rehearsal and risk checks, two verification levels, "Proposed by" column.

- 2026-11-19: Design review closed; `docs/DESIGN.md` approved and frozen.

- 2026-11-19: Subagent definitions merged into `main`.
- 2026-11-20: Lab environment built on `feat/lab-environment`: MySQL with fixed
  limits, exporter, Prometheus, Python workload, four least-privilege accounts.
  Unit tests 40, lab tests 47, lifecycle tests 2, all passing. Reviewed twice.

## In progress

- Owner to run the lab by hand and confirm the module; then merge into `main`.

## Remaining (in build order)

1. Fault injector (Python first; owner then decides Python vs Java)
2. Eval skeleton and rule-engine baseline
3. Agents: metrics, SQL, SOP, inspection, orchestrator
4. Change safety gate
5. `README.md` and `AI_WORKFLOW.md`

Also remaining: the `verify` skill in `.claude/skills/` (needs the fault
injector and evals to exist).

## Open decisions

- See "Open questions for the owner" in `docs/DESIGN.md`.
- `orders` size: an unindexed scan of 200,000 rows takes about 30 ms. Owner to
  decide whether to enlarge the table, with the fault injector proposal.
- During connection exhaustion the diagnosis account cannot connect either.
  To be settled in the fault injector proposal.
- The gate's `INDEX` privilege also allows dropping existing indexes, including
  a primary key. The privilege layer cannot narrow this; the gate's allowlist
  must. To be settled in the change safety gate proposal.
