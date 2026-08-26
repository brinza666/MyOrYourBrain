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


run(sys.executable, "-m", "compileall", "-q", "src", "tests")
run(sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v")
run(sys.executable, "-m", "myoryourbrain", "--root", str(ROOT), "validate")
