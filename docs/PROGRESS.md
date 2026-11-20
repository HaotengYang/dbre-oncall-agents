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

## In progress

- Owner re-review of `docs/DESIGN.md`, then merge `docs/design` into `main`.

## Remaining (in build order)

1. Lab environment (blocked until Docker Desktop is installed)
2. Fault injector (Python first; owner then decides Python vs Java)
3. Eval skeleton and rule-engine baseline
4. Agents: metrics, SQL, SOP, inspection, orchestrator
5. Change safety gate
6. `README.md` and `AI_WORKFLOW.md`

Next after the merge: `.claude/agents/` (explorer, implementer, reviewer) and the
`verify` skill in `.claude/skills/`; then the lab environment proposal.

## Open decisions

- See "Open questions for the owner" in `docs/DESIGN.md`.
