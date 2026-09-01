# MyOrYourBrain

MyOrYourBrain is a provider-neutral, Git-backed second brain for humans and AI agents. It uses Markdown, JSON, JSONL, exact text search, and disposable JSON indexes—no Obsidian, SQLite, vector database, or hosted memory service is required.

It provides private/public memory plus a bounded review council: positive idea, negative, evaluation, chief inspector, deterministic gates, and explicit human promotion.

“Self-evolution” means versioned improvements to repository-owned prompts, protocols, adapters, schemas, and memory. It does not mean changing model weights, hidden instructions, permissions, or Codex itself.

## Token-efficient agent operation

Interactive agents must use the repository's Caveman Ultra policy: retain technical meaning while removing filler, repetition, unnecessary tool narration, and avoidable context reads. This is an operating rule, not a request to distort code or durable documentation.

Compression never removes negation, conditions, exceptions, paths, commands, identifiers, exact errors, numbers, security warnings, or approval boundaries. Agents temporarily use full wording when compression could make a warning, destructive action, ordered procedure, or technical statement ambiguous. Durable artifacts remain concise professional prose.

See [the token-efficiency contract](docs/TOKEN-EFFICIENCY.md). `python scripts/validate.py` verifies that the canonical and compatibility-agent files retain this policy.

## Install

Python 3.11 or newer is the only runtime requirement.

```powershell
python -m pip install -e .
brain --root . init
brain --root . validate
```

Without installation, set `PYTHONPATH=src` and run `python -m myoryourbrain`.

## Memory without a database

Private is the default:

```powershell
brain --root . capture --title "Local preference" --text "Keep this on this machine" --scope private
brain --root . capture --title "Shared decision" --text "The public protocol uses JSON." --scope public --tag architecture
brain --root . search "protocol JSON" --include-private
brain --root . index --include-private
```

Public notes live in `memory/public/`. Private notes, indexes, events, approvals, and runs live in ignored `.local/`. That is a source-control boundary, not encryption or OS access control.

## Council

The included fixture proves the workflow offline and makes zero API calls:

```powershell
brain --root . council `
  --goal "Run the named offline demo" `
  --criterion "The deterministic demo passes" `
  --risk medium `
  --evidence fixtures/evidence.demo.json `
  --providers fixtures/providers.demo.json `
  --config brain.config.json
```

The fixture is scripted and must not be reused for real decisions. Connect any AI through [the JSON-over-stdin adapter](docs/PROVIDER-ADAPTER.md), or use [Codex-native subagents](docs/CODEX-SUBAGENTS.md) without an API key. Command providers are disabled unless `--allow-command-providers` is supplied because configured executables are privileged and not sandboxed.

Every local run is stored as `my-or-your-brain-run-v2`. It retains acceptance-criterion text, locally verified evidence metadata, and the structured result or bounded failure for every role in every iteration. Raw prompts, provider responses, hidden reasoning, and chain-of-thought are neither requested nor stored. A council run created by another provider-neutral orchestrator can be imported without execution:

```powershell
brain --root . record-council --input .local\incoming\council-result.json
```

The input must satisfy the strict [external council import schema](schemas/recorded-council-v2.schema.json) and the stronger runtime checks for typed roles, transcript/final-role coherence, terminal/high-risk invariants, and secrets. Unknown fields and hidden-reasoning fields fail closed. The broader [council outcome schema](schemas/council-outcome.schema.json) also describes partial blocked/cooldown results produced when a provider fails before all four roles complete.

The `0.85`, `0.90`, and `0.95` values are heuristic readiness thresholds for low, medium, and high risk. They are not model self-confidence or guaranteed correctness. Hard gates always override the score.

## Approval, promotion, and reset

Council output proposes actions but executes none. Promotion requires a separate local attestation bound to the exact run hash:

```powershell
brain --root . inspect RUN_ID
brain --root . approve RUN_ID --approved-by "owner" --confirm
brain --root . promote RUN_ID --approved-by "owner"
brain --root . reset --run-id RUN_ID
```

`reset` archives runtime checkpoints; it never deletes durable memory. A local approval records intent but does not authenticate identity.

After an approved change is implemented and checked, attach the evidence artifact and observed outcome:

```powershell
brain --root . observe RUN_ID `
  --status succeeded `
  --evidence fixtures\observed-result.txt `
  --note "The deterministic implementation check passed."
```

The evidence must be a regular file of at most 5 MB inside the repository. It is recorded by relative path, byte count, and SHA-256; its contents are not copied into the run. Because observations change the run hash, an existing promotion approval becomes invalid and must be recorded again after inspection.

## Public export

```powershell
brain --root . export-public --dest ..\MyOrYourBrain-public
```

The export is allowlisted, secret-scanned, excludes `.git` history and `.local/`, and is published only after staging passes. The destination must not exist. Human review is still required; the scanner is not a complete PII or credential detector.

## Validate

```powershell
python scripts/validate.py
```

Read [AGENTS.md](AGENTS.md), [token efficiency](docs/TOKEN-EFFICIENCY.md), [architecture](docs/ARCHITECTURE.md), [requirements](docs/REQUIREMENTS.md), and [design evidence](docs/DESIGN-EVIDENCE.md).
