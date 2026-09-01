from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV = dict(os.environ)
ENV["PYTHONPATH"] = str(ROOT / "src")


def run(*arguments: str) -> None:
    completed = subprocess.run(arguments, cwd=ROOT, env=ENV, check=False)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def validate_agent_contract() -> None:
    required_text = {
        "AGENTS.md": (
            "## Token economy: mandatory Caveman Ultra",
            "docs/TOKEN-EFFICIENCY.md",
        ),
        "CLAUDE.md": ("mandatory Caveman Ultra token economy",),
        "GEMINI.md": ("mandatory Caveman Ultra token economy",),
        "README.md": ("## Token-efficient agent operation", "docs/TOKEN-EFFICIENCY.md"),
        "CONTRIBUTING.md": ("Caveman Ultra token-efficiency contract",),
        "docs/TOKEN-EFFICIENCY.md": ("# Token-efficiency contract", "## Caveman Ultra rules"),
    }
    missing: list[str] = []
    for relative_path, markers in required_text.items():
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        for marker in markers:
            if marker not in text:
                missing.append(f"{relative_path}: {marker}")
    if missing:
        details = "\n".join(f"- {item}" for item in missing)
        raise SystemExit(f"Agent token-efficiency contract is incomplete:\n{details}")


validate_agent_contract()
run(sys.executable, "-m", "compileall", "-q", "src", "tests")
run(sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v")
run(sys.executable, "-m", "myoryourbrain", "--root", str(ROOT), "validate")
