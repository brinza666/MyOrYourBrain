# Token-efficiency contract

This repository requires Caveman Ultra compression for interactive AI-agent communication. The goal is lower token use without losing technical meaning, evidence, safety, or authority boundaries. [`AGENTS.md`](../AGENTS.md) is canonical; provider compatibility files inherit it.

## Caveman Ultra rules

1. State each fact once. Remove filler, pleasantries, repeated plans, repeated conclusions, decorative formatting, and routine tool narration.
2. Use the shortest unambiguous wording. Fragments are acceptable. Strip conjunctions only when cause, sequence, and scope remain clear.
3. Never remove or weaken `not`, `never`, `no`, `only`, `except`, conditions, qualifications, failure states, or uncertainty that changes meaning.
4. Preserve exact code, commands, paths, identifiers, API names, technical terms, URLs, errors, numbers, versions, dates, units, and quoted evidence.
5. Do not invent prose abbreviations such as `cfg`, `impl`, `req`, `res`, or `fn`. Do not replace causal language with arrows. These forms reduce readability without dependable token savings.
6. Read the minimum relevant context. Prefer exact search, bounded output, deterministic checks, and existing structured data. Do not invoke the council when deterministic evidence fully decides the task.
7. Report the outcome, decisive evidence, risks, and next action. Omit raw logs unless the user requests them; quote the shortest decisive error when needed.

## Clarity and safety override

Use full normal wording for security warnings, irreversible actions, authorization requests, ordered recovery procedures, or any statement whose compressed form could be misread. Compression never overrides privacy, approval, validation, rollback, or public/private storage rules. Resume Caveman Ultra after the sensitive section.

## Durable artifacts

Caveman Ultra applies to agent conversation and temporary working context. Committed documentation, code comments, schemas, commit messages, public notes, and user-facing artifacts use concise normal professional language. Never compress code blocks or inline code. File compression is a separate explicit operation that requires preservation and validation.

## Provider behavior

Codex, Claude, Gemini, command-provider adapters, and future agents must follow the same semantics. If a runtime lacks a named Caveman feature, it must implement these rules directly; no plugin or hosted service is required.

## Validation

Run:

```powershell
python scripts/validate.py
git diff --check
```

Validation fails if canonical or compatibility-agent policy markers disappear. Human review still verifies that later edits preserve meaning, not only marker text.
