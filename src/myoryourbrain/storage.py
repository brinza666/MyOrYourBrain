from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .security import assert_secret_free, scan_text
from .types import BrainError, CouncilOutcome, utc_now


PUBLIC_ROOT_FILES = {
    ".gitignore",
    "AGENTS.md",
    "CLAUDE.md",
    "CONTRIBUTING.md",
    "GEMINI.md",
    "LICENSE",
    "README.md",
    "SECURITY.md",
    "brain.config.json",
    "providers.example.json",
    "pyproject.toml",
}
PUBLIC_PREFIXES = {
    "decisions",
    "docs",
    "evolution",
    "examples",
    "fixtures",
    "memory/public",
    "protocol",
    "schemas",
    "scripts",
    "skills",
    "src",
    "tests",
}
TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,63}")


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")
    return value[:60] or "note"


def _safe_identifier(value: str, name: str = "identifier") -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise BrainError(f"invalid {name}: {value!r}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class Note:
    id: str
    title: str
    scope: str
    share_status: str
    created_at: str
    tags: tuple[str, ...]
    body: str
    path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "scope": self.scope,
            "share_status": self.share_status,
            "created_at": self.created_at,
            "tags": list(self.tags),
            "body": self.body,
            "path": str(self.path),
        }


class BrainStore:
    """Filesystem and Git-friendly persistence; no database is required."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.local = self.root / ".local"
        self.runs = self.local / "runs"
        self.archive = self.local / "archive"
        self.private = self.local / "private"
        self.approvals = self.local / "approvals"
        self.public = self.root / "memory" / "public"
        self.events = self.local / "events.jsonl"
        self.index_path = self.local / "index.json"

    def initialize(self) -> dict[str, str]:
        for directory in (self.root, self.runs, self.archive, self.private, self.approvals, self.public):
            directory.mkdir(parents=True, exist_ok=True)
        return {
            "root": str(self.root),
            "public_memory": str(self.public),
            "private_memory": str(self.private),
            "runtime": str(self.local),
        }

    @contextmanager
    def _lock(self):
        lock_path = self.local / "state.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def append_event(self, event: str, **details: Any) -> None:
        self.local.mkdir(parents=True, exist_ok=True)
        record = {"at": utc_now(), "event": event, **details}
        line = json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n"
        with self._lock():
            with self.events.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())

    def capture(
        self,
        title: str,
        body: str,
        *,
        scope: str = "private",
        tags: Iterable[str] = (),
    ) -> Note:
        title = title.strip()
        body = body.strip()
        if not title or not body:
            raise BrainError("title and body must be non-empty")
        if scope not in {"public", "private"}:
            raise BrainError("scope must be public or private")
        findings = scan_text(title + "\n" + body)
        if findings:
            raise BrainError(f"memory contains a possible secret: {', '.join(findings)}")

        created_at = utc_now()
        digest = hashlib.sha256(f"{title}\n{body}".encode("utf-8")).hexdigest()[:10]
        note_id = f"{created_at[:10]}-{_slug(title)}-{digest}"
        clean_tags = tuple(dict.fromkeys(item.strip() for item in tags if item.strip()))
        metadata = {
            "id": note_id,
            "title": title,
            "scope": scope,
            "share_status": scope,
            "created_at": created_at,
            "tags": list(clean_tags),
        }
        header = "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in metadata.items())
        content = f"---\n{header}\n---\n\n{body}\n".encode("utf-8")
        directory = self.public if scope == "public" else self.private
        path = directory / f"{note_id}.md"
        if path.exists():
            raise BrainError(f"memory note already exists: {note_id}")
        _atomic_write(path, content)
        self.append_event("memory.captured", id=note_id, scope=scope)
        return Note(note_id, title, scope, scope, created_at, clean_tags, body, path)

    @staticmethod
    def _is_reparse(path: Path) -> bool:
        try:
            info = path.lstat()
        except OSError as exc:
            raise BrainError(f"cannot inspect path {path}: {exc}") from exc
        return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & 0x400)

    def _assert_regular_contained(self, path: Path, base: Path) -> None:
        try:
            path.relative_to(base)
        except ValueError as exc:
            raise BrainError(f"path escapes memory boundary: {path}") from exc
        current = path
        while current != base:
            if self._is_reparse(current):
                raise BrainError(f"symlink or reparse point is not allowed: {current}")
            current = current.parent
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise BrainError(f"cannot resolve memory path {path}: {exc}") from exc
        if resolved != base and base not in resolved.parents:
            raise BrainError(f"resolved path escapes memory boundary: {path}")

    def _parse_note(self, path: Path) -> Note:
        expected_scope: str | None = None
        if path.parent == self.public:
            expected_scope = "public"
            self._assert_regular_contained(path, self.public)
        elif path.parent == self.private:
            expected_scope = "private"
            self._assert_regular_contained(path, self.private)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise BrainError(f"cannot read memory note {path}: {exc}") from exc
        if not text.startswith("---\n") or "\n---\n" not in text[4:]:
            raise BrainError(f"invalid memory note frontmatter: {path}")
        header, body = text[4:].split("\n---\n", 1)
        metadata: dict[str, Any] = {}
        for line in header.splitlines():
            key, separator, value = line.partition(":")
            if not separator:
                raise BrainError(f"invalid frontmatter line in {path}: {line!r}")
            try:
                metadata[key.strip()] = json.loads(value.strip())
            except json.JSONDecodeError as exc:
                raise BrainError(f"invalid frontmatter JSON in {path}: {exc}") from exc
        required = {"id", "title", "scope", "share_status", "created_at", "tags"}
        if not required.issubset(metadata):
            raise BrainError(f"missing memory fields in {path}: {sorted(required - metadata.keys())}")
        tags = metadata["tags"]
        if not isinstance(tags, list) or not all(isinstance(item, str) for item in tags):
            raise BrainError(f"invalid tags in {path}")
        scope = str(metadata["scope"])
        if scope not in {"public", "private"}:
            raise BrainError(f"invalid scope in {path}: {scope}")
        share_status = str(metadata["share_status"])
        if share_status not in {"public", "private", "review"}:
            raise BrainError(f"invalid share_status in {path}: {share_status}")
        if expected_scope and scope != expected_scope:
            raise BrainError(f"note scope {scope!r} does not match its {expected_scope!r} directory: {path}")
        if expected_scope == "public" and share_status != "public":
            raise BrainError(f"public memory requires share_status public: {path}")
        if expected_scope == "private" and share_status == "public":
            raise BrainError(f"private memory cannot declare share_status public: {path}")
        return Note(
            id=_safe_identifier(str(metadata["id"]), "note id"),
            title=str(metadata["title"]),
            scope=scope,
            share_status=share_status,
            created_at=str(metadata["created_at"]),
            tags=tuple(tags),
            body=body.strip(),
            path=path,
        )

    def notes(self, *, include_private: bool = False) -> list[Note]:
        paths = list(self.public.glob("*.md")) if self.public.exists() else []
        if include_private and self.private.exists():
            paths.extend(self.private.glob("*.md"))
        return [self._parse_note(path) for path in sorted(paths)]

    def build_index(self, *, include_private: bool = False) -> dict[str, Any]:
        notes = self.notes(include_private=include_private)
        terms: dict[str, list[str]] = {}
        documents: dict[str, dict[str, Any]] = {}
        for note in notes:
            documents[note.id] = {
                "title": note.title,
                "scope": note.scope,
                "tags": list(note.tags),
                "path": str(note.path.relative_to(self.root)),
            }
            tokens = {token.casefold() for token in TOKEN_RE.findall(" ".join((note.title, *note.tags, note.body)))}
            for token in tokens:
                terms.setdefault(token, []).append(note.id)
        result = {
            "version": 1,
            "generated_at": utc_now(),
            "includes_private": include_private,
            "documents": documents,
            "terms": dict(sorted(terms.items())),
        }
        _atomic_write(self.index_path, _json_bytes(result))
        self.append_event("index.built", documents=len(documents), includes_private=include_private)
        return result

    def search(self, query: str, *, include_private: bool = False, limit: int = 20) -> list[dict[str, Any]]:
        words = [token.casefold() for token in TOKEN_RE.findall(query)]
        if not words:
            raise BrainError("search query has no searchable terms")
        scored: list[tuple[int, Note]] = []
        for note in self.notes(include_private=include_private):
            title = note.title.casefold()
            tags = " ".join(note.tags).casefold()
            body = note.body.casefold()
            score = sum(5 * title.count(word) + 3 * tags.count(word) + body.count(word) for word in words)
            if score:
                scored.append((score, note))
        scored.sort(key=lambda item: (-item[0], item[1].created_at, item[1].id))
        return [{"score": score, **note.to_dict()} for score, note in scored[: max(1, limit)]]

    def save_run(self, run_id: str, payload: dict[str, Any]) -> Path:
        run_id = _safe_identifier(run_id, "run id")
        path = self.runs / f"{run_id}.json"
        _atomic_write(path, _json_bytes(payload))
        return path

    def begin_run(self, run_id: str, payload: dict[str, Any]) -> Path:
        run_id = _safe_identifier(run_id, "run id")
        active = self.runs / f"{run_id}.json"
        archived = list(self.archive.glob(f"*/{run_id}.json"))
        if active.exists() or archived:
            raise BrainError(f"council run id already exists: {run_id}")
        return self.save_run(run_id, payload)

    def load_run(self, run_id: str) -> dict[str, Any]:
        run_id = _safe_identifier(run_id, "run id")
        path = self.runs / f"{run_id}.json"
        if not path.exists():
            archived = sorted(self.archive.glob(f"*/{run_id}.json"), reverse=True)
            if archived:
                path = archived[0]
            else:
                raise BrainError(f"council run not found: {run_id}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BrainError(f"invalid council run {run_id}: {exc}") from exc
        if not isinstance(value, dict):
            raise BrainError(f"invalid council run {run_id}: expected object")
        return value

    def promote(self, run_id: str, *, approved_by: str) -> Path:
        approved_by = approved_by.strip()
        if not approved_by or "\n" in approved_by or len(approved_by) > 200:
            raise BrainError("approved_by must be a single non-empty line of at most 200 characters")
        run = self.load_run(run_id)
        approval = self.verify_approval(run_id, scope="promote")
        if approval.get("approved_by") != approved_by:
            raise BrainError("approved_by does not match the recorded approval")
        gates = run.get("readiness", {}).get("hard_gates", [])
        human_gate = "high-risk outcome requires a separate recorded human approval"
        accepted_or_human_gated = run.get("status") == "accepted" or (
            run.get("status") == "deferred"
            and gates == [human_gate]
            and float(run.get("readiness", {}).get("total", 0))
            >= float(run.get("readiness", {}).get("threshold", 1))
        )
        if not accepted_or_human_gated:
            raise BrainError("only an accepted or solely human-gated council outcome can be promoted")
        goal = str(run.get("goal", "")).strip()
        summary = str(run.get("final_summary", "")).strip()
        readiness = run.get("readiness", {})
        content = (
            f"# Promoted change: {goal}\n\n"
            f"- Run: `{run_id}`\n"
            f"- Approved by: `{approved_by}`\n"
            f"- Promoted at: `{utc_now()}`\n"
            f"- Readiness kind: `{'calibrated_probability' if readiness.get('calibrated') else 'heuristic_readiness'}`\n"
            f"- Readiness: `{float(readiness.get('total', 0)):.3f}`\n\n"
            f"## Verified outcome\n\n{summary}\n"
        )
        findings = scan_text(content)
        if findings:
            raise BrainError(f"promotion contains a possible secret: {', '.join(findings)}")
        path = self.root / "evolution" / f"{datetime.now(timezone.utc):%Y-%m-%d}-{_slug(run_id)}.md"
        if path.exists():
            raise BrainError(f"promotion already exists: {path}")
        _atomic_write(path, content.encode("utf-8"))
        self.append_event("run.promoted", run_id=run_id, approved_by=approved_by, path=str(path))
        return path

    def _run_digest(self, run: dict[str, Any]) -> str:
        return hashlib.sha256(_json_bytes(run)).hexdigest()

    def approve_run(self, run_id: str, *, approved_by: str, scope: str, confirmed: bool) -> Path:
        run_id = _safe_identifier(run_id, "run id")
        approved_by = approved_by.strip()
        if not confirmed:
            raise BrainError("approval requires an explicit confirmation")
        if not approved_by or "\n" in approved_by or len(approved_by) > 200:
            raise BrainError("approved_by must be a single non-empty line of at most 200 characters")
        if scope != "promote":
            raise BrainError("unsupported approval scope")
        run = self.load_run(run_id)
        record = {
            "format": "my-or-your-brain-local-approval-v1",
            "run_id": run_id,
            "run_sha256": self._run_digest(run),
            "scope": scope,
            "approved_by": approved_by,
            "approved_at": utc_now(),
            "identity_authenticated": False,
        }
        path = self.approvals / f"{run_id}.{scope}.json"
        _atomic_write(path, _json_bytes(record))
        self.append_event("run.approved", run_id=run_id, scope=scope, approved_by=approved_by)
        return path

    def verify_approval(self, run_id: str, *, scope: str) -> dict[str, Any]:
        run_id = _safe_identifier(run_id, "run id")
        path = self.approvals / f"{run_id}.{scope}.json"
        try:
            approval = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BrainError(f"valid local approval not found for {run_id}: {exc}") from exc
        run = self.load_run(run_id)
        if (
            not isinstance(approval, dict)
            or approval.get("run_id") != run_id
            or approval.get("scope") != scope
            or approval.get("run_sha256") != self._run_digest(run)
        ):
            raise BrainError("approval does not match the current immutable run content")
        return approval

    def reset(self, run_id: str | None = None) -> list[Path]:
        """Archive runtime checkpoints. Durable public/private memories are never deleted."""
        self.runs.mkdir(parents=True, exist_ok=True)
        candidates = [self.runs / f"{_safe_identifier(run_id, 'run id')}.json"] if run_id else sorted(self.runs.glob("*.json"))
        existing = [path for path in candidates if path.exists()]
        if run_id and not existing:
            raise BrainError(f"active council run not found: {run_id}")
        if not existing:
            return []
        archive_dir = self.archive / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
        moved: list[Path] = []
        with self._lock():
            archive_dir.mkdir(parents=True, exist_ok=False)
            for path in existing:
                destination = archive_dir / path.name
                path.replace(destination)
                moved.append(destination)
        self.append_event("runtime.reset", runs=[path.stem for path in moved])
        return moved

    def _public_candidates(self) -> list[Path]:
        candidates: list[Path] = []
        for path in self.root.rglob("*"):
            relative = path.relative_to(self.root).as_posix()
            if any(part in {"__pycache__", ".git", ".local"} or part.endswith(".egg-info") for part in path.relative_to(self.root).parts):
                continue
            if path.suffix.casefold() in {".pyc", ".pyo"}:
                continue
            if not path.is_file():
                continue
            self._assert_regular_contained(path, self.root)
            first = relative.split("/", 1)[0]
            allowed = relative in PUBLIC_ROOT_FILES or first in PUBLIC_PREFIXES or any(
                relative == prefix or relative.startswith(prefix + "/") for prefix in PUBLIC_PREFIXES
            )
            if allowed:
                if relative.startswith("memory/public/"):
                    note = self._parse_note(path)
                    if note.share_status != "public":
                        continue
                candidates.append(path)
        return sorted(candidates)

    def export_public(self, destination: str | Path) -> dict[str, Any]:
        destination = Path(destination).expanduser().resolve()
        if destination == self.root or self.root in destination.parents:
            raise BrainError("public export destination must be outside the source repository")
        if destination.exists():
            raise BrainError("public export destination must not already exist")
        candidates = self._public_candidates()
        assert_secret_free(candidates)
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent))
        try:
            records: list[dict[str, str]] = []
            for source in candidates:
                relative = source.relative_to(self.root)
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target, follow_symlinks=False)
                records.append({"path": relative.as_posix(), "sha256": _sha256(target)})
            manifest = {
                "format": "my-or-your-brain-public-export-v1",
                "created_at": utc_now(),
                "source_history_included": False,
                "files": records,
            }
            _atomic_write(staging / "EXPORT-MANIFEST.json", _json_bytes(manifest))
            assert_secret_free([path for path in staging.rglob("*") if path.is_file()])
            os.replace(staging, destination)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        self.append_event("public.exported", destination=str(destination), files=len(records))
        return manifest

    def validate(self) -> dict[str, Any]:
        public_notes = self.notes(include_private=False)
        all_notes = self.notes(include_private=True)
        public_files = self._public_candidates()
        assert_secret_free(public_files)
        duplicate_ids: set[str] = set()
        seen: set[str] = set()
        for note in all_notes:
            if note.id in seen:
                duplicate_ids.add(note.id)
            seen.add(note.id)
        if duplicate_ids:
            raise BrainError(f"duplicate memory ids: {sorted(duplicate_ids)}")
        return {
            "ok": True,
            "root": str(self.root),
            "public_notes": len(public_notes),
            "private_notes": len(all_notes) - len(public_notes),
            "public_files_scanned": len(public_files),
            "database_required": False,
        }
