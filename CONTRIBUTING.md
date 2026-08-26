# Contributing

State goal, criteria, risk, evidence, permitted actions, budget, and rollback. Keep changes small, provider-neutral, and compatible with Python 3.11.

```powershell
python scripts/validate.py
git diff --check
```

Inspect the full diff and public boundary. Tests must use fixtures and make no network/API calls. Do not add secrets, `.local/`, hidden reasoning, vendor-required behavior, or claims stronger than the implementation.
