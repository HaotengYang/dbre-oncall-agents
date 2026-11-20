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

- 2026-11-20: Lab environment merged into `main`.

## Plan

One step at a time; the owner says "继续" before the next one starts.
Schedule target: A and B on Friday 11-20, C on Saturday 11-21, D and E on
Sunday 11-22, Monday 11-23 for rehearsal and submission only.

### A. Fault injector

- [x] A1. Proposal and completion criteria (no code):
      `docs/proposals/A-fault-injector.md`, awaiting owner confirmation
- [ ] A2. Missing-index slow query: one variant, inject and clear
- [ ] A3. Lock wait: one variant
- [ ] A4. Connection exhaustion: one variant; ask the owner how the diagnosis
      account should connect when connections are exhausted
- [ ] A5. Several variants per class, plus cascading faults. The missing-index
      class must include a variant that does not change the schema: a new query
      on a column that never had an index (owner, 2026-11-20)

### B. Eval system

- [ ] B1. Proposal: case format, ground truth, scoring
- [ ] B2. Eval runner plus rule engine for one fault class; first report
- [ ] B3. Rule engine covers all cases; baseline numbers; owner sets thresholds
- [ ] B4. Evidence recording and replay
- [ ] B5. `verify` skill

### C. Agents

- [ ] C1. Evidence collectors (read-only). Add the test that diagnosis-side
      code never reads injection records or imports `fault_injector`
- [ ] C2. SQL agent and metrics agent, in two parallel worktrees
- [ ] C3. Orchestrator and diagnosis report
- [ ] C4. Connect the LLM; owner approves model list and budget first; then
      multi-model comparison
- [ ] C5. SOP retrieval: ten runbooks, keyword and vector, hit-rate comparison
- [ ] C6. Inspection agent

### D. Change safety gate

- [ ] D1. Proposal and completion criteria
- [ ] D2. Allowlist and property-based tests
- [ ] D3. Shadow rehearsal, risk assessment, rollback plan

### E. Wrap-up

- [ ] E1. README (covering both Docker Desktop and Colima)
- [ ] E2. `AI_WORKFLOW.md`
- [ ] E3. Fresh clone in a new directory; follow the README from the start

## Open decisions

- See "Open questions for the owner" in `docs/DESIGN.md`.
- `orders` stays at 200,000 rows until the fault injector gives measured
  numbers (owner, 2026-11-20).
- During connection exhaustion the diagnosis account cannot connect either.
  Options go to the owner in step A4.
- Digest-table rows with a zero count are ignored by the SQL agent; the cause
  of the one sighting stays recorded as not established (owner, 2026-11-20).
- The gate's `INDEX` privilege also allows dropping existing indexes, including
  a primary key. The privilege layer cannot narrow this; the gate's allowlist
  must. To be settled in the change safety gate proposal.
