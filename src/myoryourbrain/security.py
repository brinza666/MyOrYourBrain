from __future__ import annotations

import re
from pathlib import Path

from .types import BrainError


# Split sensitive prefixes so this repository does not contain scanner-triggering example tokens.
SECRET_PATTERNS = {
    "github-classic-token": re.compile(r"g" r"(?:hp|ho|hs|hu|hr)_[A-Za-z0-9]{20,}"),
    "github-fine-grained-token": re.compile(r"github" r"_pat_[A-Za-z0-9_]{20,}"),
    "openai-key": re.compile(r"(?<![A-Za-z0-9])s" r"k-(?:proj-)?[A-Za-z0-9_-]{20,}"),
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "bearer-token": re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{24,}", re.IGNORECASE),
}


def scan_text(text: str) -> list[str]:
    return [name for name, pattern in SECRET_PATTERNS.items() if pattern.search(text)]


def scan_file(path: Path) -> list[str]:
    try:
        if path.stat().st_size > 5_000_000:
            return ["oversized-unscanned-file"]
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ["binary-or-non-utf8-public-file"]
    except OSError:
        return ["unreadable-public-file"]
    return scan_text(text)


def assert_secret_free(files: list[Path]) -> None:
    findings: list[str] = []
    for path in files:
        kinds = scan_file(path)
        if kinds:
            findings.append(f"{path}: {', '.join(kinds)}")
    if findings:
        raise BrainError("secret scan failed:\n" + "\n".join(findings))
