# Evolution protocol

Self-evolution is an evaluated, reversible change to repository-owned memory, prompts, adapters, schemas, protocols, or workflows:

```text
observe -> set criteria -> baseline -> propose -> challenge -> evaluate
        -> verify -> chief gate -> approve -> promote -> monitor/rollback
```

Defaults: three iterations, 900 elapsed seconds, two no-progress iterations, 24-hour persisted cooldown. No run sleeps while waiting; a resumed run receives a fresh bounded budget.

A retry must change substantive output or evidence, not only `approach_id`. The engine hashes canonical claims, risks, decisions, models, and evidence digests. Stop at success, joint rejection, missing capability, provider/deadline failure, repeated strategy, no progress, or iteration cap.

Promote only after inspection and local run-hash approval. Core policy, credentials, public export, destructive behavior, and external actions always require human review and rollback.

After implementation, `observe` binds `succeeded`, `failed`, or `mixed` to a repository-local evidence file hash and a concise note. An observation mutates the run record and therefore invalidates any earlier hash-bound approval; inspect and approve the updated record again before promotion.
