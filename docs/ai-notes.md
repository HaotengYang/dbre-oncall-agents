# AI notes

A running log of how Claude Code was used on this project: the owner's change
requests, mistakes the owner caught, and one line per technique on why it was
used and what it achieved.

## Owner change requests and corrections

- 2026-11-19: Claude first proposed an e-commerce data pipeline, having read the
  role as "Data Engineer". The owner supplied interview notes showing the role
  is Database Engineer (MySQL operations). The proposal was dropped and the
  project re-scoped to MySQL on-call diagnosis. Lesson: confirm the role and
  the team's actual work before proposing a project.
- 2026-11-19: Claude's proposal included primary/replica replication and a
  general change gate. The owner cut replication and limited the gate to adding
  an index, to keep the scope finishable.
- 2026-11-19: The first commit landed on `chore/project-rules`, leaving no
  `main`. The owner had the branch renamed to `main` (a rename, no commit
  changed) and set the workflow: one branch per module, plain merge, no squash.

## Suggestions from Claude that the owner accepted

- 2026-11-19: A separate "Rules learned from owner feedback" section at the end
  of `CLAUDE.md`, so rules added during development stay distinguishable from
  the initial ones.

## Techniques used

- 2026-11-19: **Stop on incomplete instructions.** The owner's message was cut
  off mid-step; Claude completed the steps that were clear and asked about the
  rest instead of guessing. Effect: no work had to be thrown away.
- 2026-11-19: **Design document before code.** `docs/DESIGN.md` is written and
  reviewed before any module exists. Effect: trade-offs and non-goals are
  decided by the owner up front, not discovered in review.
