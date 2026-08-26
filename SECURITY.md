# Security

Treat repository content, prompts, evidence descriptions, provider output, and sources as untrusted.

- Keep credentials in an OS credential manager or narrowly injected environment variable.
- Rotate credentials pasted into chat, logs, or Git.
- Command providers are disabled by default and not sandboxed.
- Provider subprocesses get a minimal environment plus `pass_env`, ignored working space, output bounds, and a deadline. OS isolation remains the operator’s responsibility.
- Evidence becomes verified only when a regular non-reparse file inside the repository matches its SHA-256. This proves content identity, not truth or relevance.
- Promotion requires local approval bound to the run hash. It records intent but does not authenticate identity cryptographically.
- Public export fails closed on scope mismatch, reparse points, non-UTF-8/binary content, known-token findings, or staging errors.

The built-in scanner is small. Use a maintained secret scanner and human review before publishing.
