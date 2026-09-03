# Repository audit — 2026-09-03

## Git and validation state

- Branch `codex/council-v2` equals `origin/codex/council-v2` at
  `ebd426fcd2e9798c1da35e96f34bf7eec6f8bbe6` after fetch.
- Worktree was clean before this audit.
- `brain doctor` passes: 41 public files scanned, 1 private note, 0 public notes.
- Repository contains 65 non-Git files in 22 directories.

## Actual usage and evolution

The repository code evolved through five commits. The latest two added inspectable council-v2 run
records and mandatory token-economy policy. Runtime memory did not evolve with the MyLauncher work:
only one private note existed, dated 2026-08-26, and it named the obsolete E: workspace and old
436/0/0 baseline. `memory/public/` and `evolution/` were empty. Two `.local/runs/demo-*.json` files
and one demo promotion approval were fixture/demo output, not real project decisions.

MyOrYourBrain therefore was implemented and validated, but not integrated into normal MyLauncher
checkpoint updates. The authoritative continuity system remained MyLauncher's `.session` tree.

## Cleanup

- Moved demo runtime records and demo approval to
  `.local/not-used/demo-runtime-2026-09-03/`; nothing was deleted.
- Moved the stale private MyLauncher note to `.local/not-used/stale-private-2026-09-03/`; nothing
  was deleted.
- Captured a new compact private MyLauncher continuity note through the `brain` CLI.
- Kept `fixtures/` because tests and offline documentation use it.
- Kept empty `memory/public/` and `evolution/` because they are active storage destinations, not
  obsolete folders.

## Recommended role

Use MyLauncher's compact `.session/SESSION_BRIEF.md` as primary startup context. Mirror only durable,
cross-project decisions into MyOrYourBrain when explicitly valuable. Do not duplicate session
journals, generated evidence, or full handoffs there. Run the council only for consequential
uncertainty; deterministic Git/hash/test checks need no council.
