---
name: my-or-your-brain
description: Operate a MyOrYourBrain Git-backed memory repository, including private/public notes, exact search, bounded council review, evidence inspection, approval, promotion, reset, and clean public export. Use when asked to remember project knowledge here, inspect past brain records, run the four-lens council, evaluate a self-evolution proposal, or create a provider-neutral public memory export.
---

# MyOrYourBrain

1. Locate the repository root and read `AGENTS.md` plus only relevant protocols.
2. Keep user/personal context private unless public scope is explicit. Never store credentials.
3. Prefer exact search and existing Markdown/JSON before building an index.
4. Use deterministic operations for exact work and the council only for uncertain/consequential decisions.
5. Treat readiness as heuristic; hard gates and human approval override it.
6. Bound improvement and require a materially different retry.
7. Inspect and validate before approval, promotion, reset, export, commit, or push.

## Codex-native council

When the user requests council review or the task is materially uncertain, Codex may fill the roles with native subagents and no API key:

- positive: an economical Luna/mini-class model;
- negative: a Terra-class model with sufficient reasoning depth;
- evaluation: the strongest available non-chief model;
- chief: the root agent using the strongest available model.

Run positive and negative independently in parallel, then give their inspectable conclusions to evaluation. The root chief applies deterministic gates and makes the final decision. Record actual model IDs and any fallback; never claim independence or "strongest" when availability is unknown. Use at most three subagents per iteration and the configured maximum of three iterations. Stop early when deterministic checks are sufficient.

Use `brain --root REPOSITORY --help`. Command providers are privileged, opt-in, and not sandboxed. Return conclusions, evidence, risks, status, and next action—never hidden reasoning.
