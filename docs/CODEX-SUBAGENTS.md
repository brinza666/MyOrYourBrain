# Codex-native council

Codex can implement the council with native subagents; no OpenAI API key or provider command is required.

## Default routing

| Role | Suggested tier | Purpose |
|---|---|---|
| Positive | Luna/mini/economy | Smallest viable idea, benefits, evidence, tests |
| Negative | Terra/balanced | Independent failure analysis, regressions, simpler alternatives |
| Evaluation | Strongest available non-chief | Fixed-rubric reconciliation and unresolved gaps |
| Chief | Root agent on strongest available model | Final semantic decision after deterministic gates |

Positive and negative run independently and may run in parallel. Evaluation receives only their concise inspectable outputs. The root agent remains chief and applies schema, evidence, test, permission, budget, and human-approval gates.

Model labels are suggestions, not guarantees. Record actual model IDs, reasoning tier, fallback, and whether different providers/models were genuinely used. Multiple roles on one model are correlated perspectives.

Use at most three subagents in one iteration and at most three iterations by default. A retry must change evidence, decomposition, verification, provider/model, or substantive approach. Skip the council when deterministic checks fully decide the task.
