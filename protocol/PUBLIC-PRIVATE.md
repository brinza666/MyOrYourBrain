# Public/private protocol

`memory/public/` is the only exportable memory path. Every note must reside in the directory matching `scope`; a public note must declare `share_status: public`. Scope mismatch, malformed note, symlink, Windows reparse point, unreadable file, or non-UTF-8 file fails closed.

`.local/` stores private notes, indexes, provider work, events, runs, and approvals. It is ignored by Git but readable to the same OS account. Use separate OS identities, ACLs, encryption, or a private repository for stronger separation.

`brain export-public` uses an allowlist, stages into a new directory, scans known secrets, hashes files, and publishes only after staging passes. It excludes `.git` and `.local/`; destination must not exist.

The scanner is defense in depth, not complete PII/secret detection. Review every export. Never store API keys, access tokens, cookies, credential URLs, or raw private transcripts in brain notes.
