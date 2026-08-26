# Architecture

```text
human / any AI
       |
task + criteria + risk ---> local SHA-256 evidence
       |                           |
positive look      negative look  (independent first pass)
       \              /
        evaluation look
              |
       deterministic gates
              |
       chief inspector
              |
accepted / rejected / deferred / blocked / cooldown
              |
     explicit run-hash approval
              |
       Git-visible promotion
```

## Layers

- `storage.py`: Markdown memory, exact search, JSON index, JSON/JSONL runtime, approval binding, atomic export.
- `providers.py`: fixture and opt-in JSON-over-stdin command adapters.
- `council.py`: thresholds, evidence verification, role sequencing, gates, strategy deduplication, bounded states.
- `schemas/`: portable provider/output contracts.
- `protocol/`: provider-independent policy.
- `skills/my-or-your-brain/`: Codex entry point; `AGENTS.md` remains authoritative for every agent.

Positive and negative roles do not see each other’s first response. Evaluation sees both inspectable outputs. The chief sees all three. This reduces direct anchoring but does not make roles statistically independent; a single-model council receives a readiness penalty.

Git tracks shareable source-of-truth artifacts. `.local/` contains replaceable private/runtime artifacts. A branch is not a privacy boundary because Git history can retain removed data.

Command adapters are interoperability, not a sandbox. They are opt-in, run from ignored provider work, receive a minimal environment plus named variables, and have output/deadline limits. The operator must still trust the executable.
