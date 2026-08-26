# MyOrYourBrain

MyOrYourBrain is a provider-neutral, Git-backed second brain for humans and AI agents. It uses Markdown, JSON, JSONL, exact text search, and disposable JSON indexes—no Obsidian, SQLite, vector database, or hosted memory service is required.

It provides private/public memory plus a bounded review council: positive idea, negative, evaluation, chief inspector, deterministic gates, and explicit human promotion.

“Self-evolution” means versioned improvements to repository-owned prompts, protocols, adapters, schemas, and memory. It does not mean changing model weights, hidden instructions, permissions, or Codex itself.

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

## Public export

```powershell
brain --root . export-public --dest ..\MyOrYourBrain-public
```

The export is allowlisted, secret-scanned, excludes `.git` history and `.local/`, and is published only after staging passes. The destination must not exist. Human review is still required; the scanner is not a complete PII or credential detector.

## Validate

```powershell
python scripts/validate.py
```

Read [AGENTS.md](AGENTS.md), [architecture](docs/ARCHITECTURE.md), [requirements](docs/REQUIREMENTS.md), and [design evidence](docs/DESIGN-EVIDENCE.md).
