# Provider adapter contract

A command provider lets any model CLI participate without changing the core. It reads one UTF-8 JSON object from stdin and writes one UTF-8 JSON object to stdout with no surrounding commentary.

The request protocol is `my-or-your-brain-provider-v1` and contains role, iteration, phase, task, verified evidence metadata, and prior inspectable role outputs where applicable. Criteria have IDs such as `criterion-1`.

The response must match `schemas/provider-response.schema.json`:

```json
{
  "approach_id": "method-v1",
  "summary": "Concise conclusion, not hidden reasoning.",
  "claims": [{"id": "claim-1", "text": "A checkable claim.", "evidence_ids": ["evidence-1"], "criterion_ids": ["criterion-1"]}],
  "risks": [],
  "contradictions": [],
  "questions": [],
  "recommendation": "approve",
  "self_confidence": 0.5,
  "actions": [{"kind": "run_check", "description": "Proposed only."}],
  "needs": {"skills": [], "connections": [], "knowledge": [], "optional": []}
}
```

`self_confidence` is diagnostic and excluded from readiness. The core overwrites role/model identity from configuration and rejects unknown actions.

Command providers are privileged and disabled unless `--allow-command-providers` is given. The core does not provide container isolation, network denial, or authenticated provider identity. Prefer a pinned absolute executable and expose only exact credential variables through `pass_env`.

Codex can alternatively run its native subagents with different available model tiers and normalize their outputs to this schema; that path needs no OpenAI API key.
