# Normalized requirements

## Goal

Create a reusable agentic second brain for this Codex installation and other AI systems without requiring Obsidian or SQLite.

## Required behavior

- Use Git for versioned public knowledge, review, rollback, and collaboration.
- Use Markdown, JSON, and JSONL; keep generated indexes disposable.
- Support exact local search without a database.
- Keep private runtime/memory outside the public Git boundary and export without Git history.
- Expose a stable adapter for different AI systems and model tiers.
- Review material semantic decisions through positive, negative, evaluation, and chief roles.
- Route routine work to cheaper models and material decisions to the strongest locally validated configured model; record actual models and fallback.
- Use evidence-based heuristic readiness at `0.85` low, `0.90` medium, and `0.95` high risk, never self-reported confidence or guaranteed truth.
- Below threshold, identify missing required/optional skills, connections, and knowledge, then try a materially different strategy.
- Bound runs by iterations, elapsed time, output size, and no-progress limits. Default to three iterations and a persisted 24-hour cooldown.
- Require explicit local approval before promotion and human review for high risk.
- Preserve inspectable run/event records and a reversible evolution ledger.

## Non-goals

- Modifying model weights, platform prompts, permissions, or hidden reasoning.
- Unattended destructive or externally visible actions.
- Treating four roles, more agents, or more plugins as automatically better.
- Claiming authenticated identity, complete secret/PII detection, or calibrated probability without external infrastructure and evaluations.
- Shipping a provider credential or requiring one vendor SDK.
