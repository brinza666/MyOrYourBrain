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
from .types import BrainError, Evidence, RoleResult, TERMINAL_STATES, require_text, utc_now


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
OBSERVATION_STATUSES = {"succeeded", "failed", "mixed"}
RECORDED_RUN_FIELDS = {
    "format",
    "run_id",
    "goal",
    "risk",
    "acceptance_criteria",
    "evidence",
    "status",
    "iterations",
    "readiness",
    "final_summary",
    "roles",
    "missing_capabilities",
    "next_eligible_at",
    "transcript",
    "observations",
    "created_at",
}
TRANSCRIPT_FIELDS = {"iteration", "role", "phase", "status", "recorded_at", "result", "error"}
FORBIDDEN_TRANSCRIPT_KEYS = {
    "chain_of_thought",
    "hidden_reasoning",
    "reasoning",
    "raw_prompt",
    "raw_request",
    "raw_response",
    "provider_response",
}


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

    def _resolve_inside_root(self, path: str | Path) -> tuple[Path, Path]:
        """Resolve a path inside root, including Windows short/long path aliases."""
        source = Path(path)
        if not source.is_absolute():
            source = self.root / source
        try:
            relative = source.relative_to(self.root)
        except ValueError:
            relative = None
            if os.name == "nt":
                for parent in source.parents:
                    try:
                        if os.path.samefile(parent, self.root):
                            relative = source.relative_to(parent)
                            break
                    except OSError:
                        continue
            if relative is None:
                raise BrainError(f"path escapes repository boundary: {source}")
        canonical = self.root / relative
        self._assert_regular_contained(canonical, self.root)
        try:
            resolved = canonical.resolve(strict=True)
            if not os.path.samefile(source, resolved):
                raise BrainError(f"path alias does not resolve to repository content: {source}")
        except OSError as exc:
            raise BrainError(f"cannot resolve repository path {source}: {exc}") from exc
        return resolved, relative

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
        with self._lock():
            _atomic_write(path, _json_bytes(payload))
        return path

    def begin_run(self, run_id: str, payload: dict[str, Any]) -> Path:
        run_id = _safe_identifier(run_id, "run id")
        active = self.runs / f"{run_id}.json"
        approval_invalidated = False
        with self._lock():
            archived = list(self.archive.glob(f"*/{run_id}.json"))
            if active.exists() or archived:
                raise BrainError(f"council run id already exists: {run_id}")
            _atomic_write(active, _json_bytes(payload))
        return active

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

    @staticmethod
    def _assert_no_hidden_reasoning(value: Any, path: str = "$") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str):
                    raise BrainError(f"recorded council object key at {path} must be text")
                normalized = key.casefold().replace("-", "_")
                if normalized in FORBIDDEN_TRANSCRIPT_KEYS or "chain_of_thought" in normalized:
                    raise BrainError(f"recorded council contains forbidden hidden-reasoning field: {path}.{key}")
                BrainStore._assert_no_hidden_reasoning(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                BrainStore._assert_no_hidden_reasoning(child, f"{path}[{index}]")

    @staticmethod
    def _validate_transcript(transcript: Any) -> None:
        if not isinstance(transcript, list) or len(transcript) > 80:
            raise BrainError("transcript must be an array with at most 80 entries")
        for index, entry in enumerate(transcript):
            if not isinstance(entry, dict):
                raise BrainError(f"transcript[{index}] must be an object")
            unknown = set(entry) - TRANSCRIPT_FIELDS
            if unknown:
                raise BrainError(f"transcript[{index}] has unknown fields: {sorted(unknown)}")
            try:
                iteration = int(entry.get("iteration", 0))
            except (TypeError, ValueError) as exc:
                raise BrainError(f"transcript[{index}].iteration must be an integer") from exc
            if iteration < 1 or iteration > 20:
                raise BrainError(f"transcript[{index}].iteration must be between 1 and 20")
            role = require_text(entry.get("role"), f"transcript[{index}].role", maximum=80)
            if role not in {"positive", "negative", "evaluation", "chief"}:
                raise BrainError(f"transcript[{index}] has unsupported role: {role}")
            require_text(entry.get("phase"), f"transcript[{index}].phase", maximum=100)
            require_text(entry.get("recorded_at"), f"transcript[{index}].recorded_at", maximum=100)
            status = entry.get("status")
            if status == "succeeded":
                if not isinstance(entry.get("result"), dict) or "error" in entry:
                    raise BrainError(f"transcript[{index}] succeeded entry requires result only")
                RoleResult.from_dict(entry["result"], role)
            elif status == "failed":
                if "result" in entry:
                    raise BrainError(f"transcript[{index}] failed entry cannot contain result")
                require_text(entry.get("error"), f"transcript[{index}].error", maximum=2_000)
            else:
                raise BrainError(f"transcript[{index}].status must be succeeded or failed")

    @staticmethod
    def _validate_observations(observations: Any) -> None:
        if not isinstance(observations, list) or len(observations) > 1_000:
            raise BrainError("observations must be an array with at most 1000 entries")
        for index, observation in enumerate(observations):
            if not isinstance(observation, dict):
                raise BrainError(f"observations[{index}] must be an object")
            status = observation.get("status")
            if status not in OBSERVATION_STATUSES:
                raise BrainError(f"observations[{index}] has unsupported status")
            require_text(observation.get("id"), f"observations[{index}].id", maximum=200)
            require_text(observation.get("recorded_at"), f"observations[{index}].recorded_at", maximum=100)
            require_text(observation.get("note"), f"observations[{index}].note", maximum=10_000)
            evidence = observation.get("evidence")
            if not isinstance(evidence, dict):
                raise BrainError(f"observations[{index}].evidence must be an object")
            require_text(evidence.get("source"), f"observations[{index}].evidence.source", maximum=2_000)
            digest = require_text(evidence.get("sha256"), f"observations[{index}].evidence.sha256", maximum=64)
            if not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
                raise BrainError(f"observations[{index}].evidence.sha256 is invalid")
            if not isinstance(evidence.get("bytes"), int) or evidence["bytes"] < 0:
                raise BrainError(f"observations[{index}].evidence.bytes must be a non-negative integer")

    def record_council(self, payload: dict[str, Any]) -> Path:
        """Persist a validated external structured council result without executing it."""
        if not isinstance(payload, dict):
            raise BrainError("recorded council input must be an object")
        unknown = set(payload) - RECORDED_RUN_FIELDS
        if unknown:
            raise BrainError(f"recorded council has unknown top-level fields: {sorted(unknown)}")
        required = RECORDED_RUN_FIELDS - {"next_eligible_at"}
        missing = required - set(payload)
        if missing:
            raise BrainError(f"recorded council is missing fields: {sorted(missing)}")
        if payload.get("format") != "my-or-your-brain-run-v2":
            raise BrainError("recorded council format must be my-or-your-brain-run-v2")
        run_id = _safe_identifier(str(payload.get("run_id", "")), "run id")
        require_text(payload.get("goal"), "goal", maximum=100_000)
        require_text(payload.get("final_summary"), "final_summary", maximum=100_000)
        require_text(payload.get("created_at"), "created_at", maximum=100)
        if payload.get("risk") not in {"low", "medium", "high"}:
            raise BrainError("recorded council risk must be low, medium, or high")
        if payload.get("status") not in TERMINAL_STATES:
            raise BrainError("recorded council status must be terminal")
        criteria = payload.get("acceptance_criteria")
        if not isinstance(criteria, list) or not criteria or len(criteria) > 50:
            raise BrainError("recorded council acceptance_criteria must contain 1 to 50 entries")
        criterion_ids: set[str] = set()
        for index, criterion in enumerate(criteria):
            if not isinstance(criterion, dict) or set(criterion) != {"id", "text"}:
                raise BrainError(f"recorded council acceptance_criteria[{index}] must contain id and text")
            criterion_id = require_text(
                criterion.get("id"), f"acceptance_criteria[{index}].id", maximum=200
            )
            require_text(criterion.get("text"), f"acceptance_criteria[{index}].text", maximum=20_000)
            if criterion_id in criterion_ids:
                raise BrainError("recorded council acceptance criterion ids must be unique")
            criterion_ids.add(criterion_id)
        evidence_raw = payload.get("evidence")
        if not isinstance(evidence_raw, list) or len(evidence_raw) > 100:
            raise BrainError("recorded council evidence must be an array with at most 100 entries")
        evidence_by_id: dict[str, Evidence] = {}
        for index, raw in enumerate(evidence_raw):
            if not isinstance(raw, dict):
                raise BrainError(f"recorded council evidence[{index}] must be an object")
            item = Evidence.from_dict(raw)
            if item.id in evidence_by_id:
                raise BrainError("recorded council evidence ids must be unique")
            if not re.fullmatch(r"[a-fA-F0-9]{64}", item.sha256):
                raise BrainError(f"recorded council evidence {item.id} sha256 is invalid")
            computed_verified = False
            try:
                resolved, _ = self._resolve_inside_root(item.source)
                computed_verified = (
                    resolved.is_file()
                    and resolved.stat().st_size <= 5_000_000
                    and _sha256(resolved) == item.sha256.casefold()
                )
            except BrainError:
                computed_verified = False
            if item.verified != computed_verified:
                raise BrainError(f"recorded council evidence {item.id} verified flag does not match local content")
            evidence_by_id[item.id] = item
        if not isinstance(payload.get("iterations"), int) or not 1 <= payload["iterations"] <= 20:
            raise BrainError("recorded council iterations must be an integer between 1 and 20")
        readiness = payload.get("readiness")
        if not isinstance(readiness, dict):
            raise BrainError("recorded council readiness must be an object")
        readiness_fields = {"total", "threshold", "calibrated", "components", "penalties", "hard_gates"}
        if set(readiness) != readiness_fields:
            raise BrainError("recorded council readiness fields are incomplete or unknown")
        if isinstance(readiness["total"], bool) or not isinstance(readiness["total"], (int, float)):
            raise BrainError("recorded council readiness.total must be numeric")
        if isinstance(readiness["threshold"], bool) or not isinstance(readiness["threshold"], (int, float)):
            raise BrainError("recorded council readiness.threshold must be numeric")
        total = float(readiness["total"])
        threshold = float(readiness["threshold"])
        if not 0 <= total <= 1 or not 0 <= threshold <= 1:
            raise BrainError("recorded council readiness values must be between 0 and 1")
        if not isinstance(readiness["calibrated"], bool):
            raise BrainError("recorded council readiness.calibrated must be a boolean")
        if not isinstance(readiness["components"], dict) or not isinstance(readiness["penalties"], dict):
            raise BrainError("recorded council readiness components and penalties must be objects")
        hard_gates = readiness["hard_gates"]
        if not isinstance(hard_gates, list) or not all(isinstance(item, str) and item.strip() for item in hard_gates):
            raise BrainError("recorded council readiness.hard_gates must be an array of non-empty strings")
        roles = payload.get("roles")
        council_roles = {"positive", "negative", "evaluation", "chief"}
        if not isinstance(roles, dict) or set(roles) != council_roles:
            raise BrainError("recorded council must contain exactly all four council roles")
        parsed_roles: dict[str, RoleResult] = {}
        for role, result in roles.items():
            if not isinstance(result, dict):
                raise BrainError(f"recorded council role {role} must be an object")
            parsed_roles[role] = RoleResult.from_dict(result, role)
        claims = [claim for role in parsed_roles.values() for claim in role.claims]
        referenced_evidence = {reference for claim in claims for reference in claim.evidence_ids}
        referenced_criteria = {reference for claim in claims for reference in claim.criterion_ids}
        if referenced_evidence - set(evidence_by_id):
            raise BrainError("recorded council claims contain unknown evidence references")
        if referenced_criteria - criterion_ids:
            raise BrainError("recorded council claims contain unknown acceptance-criterion references")
        missing_capabilities = payload.get("missing_capabilities")
        capability_fields = {"skills", "connections", "knowledge", "optional"}
        if not isinstance(missing_capabilities, dict) or set(missing_capabilities) != capability_fields:
            raise BrainError("recorded council missing_capabilities fields are incomplete or unknown")
        for name, values in missing_capabilities.items():
            if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
                raise BrainError(f"recorded council missing_capabilities.{name} must be an array of strings")
        computed_missing = {name: [] for name in capability_fields}
        for role in parsed_roles.values():
            for name in capability_fields:
                computed_missing[name].extend(getattr(role.needs, name))
        computed_missing = {
            name: list(dict.fromkeys(values)) for name, values in computed_missing.items()
        }
        if computed_missing != missing_capabilities:
            raise BrainError("recorded council missing_capabilities does not match role results")
        transcript = payload.get("transcript")
        self._validate_transcript(transcript)
        attempts: set[tuple[int, str]] = set()
        latest_success: dict[str, dict[str, Any]] = {}
        for entry in transcript:
            key = (entry["iteration"], entry["role"])
            if key in attempts:
                raise BrainError(f"recorded council transcript repeats an attempted role: {key}")
            attempts.add(key)
            if entry["iteration"] > payload["iterations"]:
                raise BrainError("recorded council transcript iteration exceeds outcome iterations")
            if entry["status"] == "succeeded":
                latest_success[entry["role"]] = entry["result"]
        if not transcript or max(entry["iteration"] for entry in transcript) != payload["iterations"]:
            raise BrainError("recorded council transcript does not reach the declared final iteration")
        if set(latest_success) != council_roles:
            raise BrainError("recorded council transcript lacks a successful result for every role")
        if any(latest_success[role] != roles[role] for role in council_roles):
            raise BrainError("recorded council final roles do not match the transcript")
        status = payload["status"]
        human_gate = "high-risk outcome requires a separate recorded human approval"
        if payload["risk"] == "high" and human_gate not in hard_gates:
            raise BrainError("recorded high-risk council must retain the separate human-approval gate")
        if status == "accepted":
            if hard_gates or total < threshold:
                raise BrainError("accepted recorded council must meet threshold with no hard gates")
            if payload["risk"] == "high":
                raise BrainError("a high-risk recorded council cannot be accepted before human approval")
            if parsed_roles["evaluation"].recommendation != "approve" or parsed_roles["chief"].recommendation != "approve":
                raise BrainError("accepted recorded council requires evaluation and chief approval")
            if parsed_roles["evaluation"].contradictions or parsed_roles["chief"].contradictions:
                raise BrainError("accepted recorded council cannot retain unresolved contradictions")
            if any(missing_capabilities[name] for name in ("skills", "connections", "knowledge")):
                raise BrainError("accepted recorded council cannot retain required missing capabilities")
            chief_covered = {
                criterion
                for claim in parsed_roles["chief"].claims
                for criterion in claim.criterion_ids
                if any(
                    reference in evidence_by_id and evidence_by_id[reference].verified
                    for reference in claim.evidence_ids
                )
            }
            if chief_covered != criterion_ids:
                raise BrainError("accepted recorded council chief lacks verified criterion coverage")
        self._validate_observations(payload.get("observations", []))
        self._assert_no_hidden_reasoning(payload)
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        findings = scan_text(encoded)
        if findings:
            raise BrainError(f"recorded council contains a possible secret: {', '.join(findings)}")
        normalized = json.loads(encoded)
        normalized.setdefault("observations", [])
        path = self.begin_run(run_id, normalized)
        self.append_event("council.recorded", run_id=run_id, format=normalized["format"])
        return path

    def record_observation(
        self,
        run_id: str,
        *,
        status: str,
        evidence_path: str | Path,
        note: str,
    ) -> dict[str, Any]:
        """Append a hash-bound post-run observation while preserving the original run payload."""
        run_id = _safe_identifier(run_id, "run id")
        if status not in OBSERVATION_STATUSES:
            raise BrainError("observation status must be succeeded, failed, or mixed")
        note = require_text(note, "observation note", maximum=10_000)
        findings = scan_text(note)
        if findings:
            raise BrainError(f"observation note contains a possible secret: {', '.join(findings)}")
        try:
            resolved, relative = self._resolve_inside_root(evidence_path)
        except BrainError as exc:
            raise BrainError("observation evidence must be a regular file inside the brain repository") from exc
        if not resolved.is_file() or resolved.stat().st_size > 5_000_000:
            raise BrainError("observation evidence must be a regular file no larger than 5000000 bytes")
        relative_source = relative.as_posix()
        observation = {
            "id": f"observation-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}",
            "recorded_at": utc_now(),
            "status": status,
            "note": note,
            "evidence": {
                "source": relative_source,
                "sha256": _sha256(resolved),
                "bytes": resolved.stat().st_size,
            },
        }
        active = self.runs / f"{run_id}.json"
        with self._lock():
            if not active.exists():
                raise BrainError(f"active council run not found: {run_id}")
            try:
                run = json.loads(active.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise BrainError(f"invalid council run {run_id}: {exc}") from exc
            if not isinstance(run, dict):
                raise BrainError(f"invalid council run {run_id}: expected object")
            if run.get("status") not in TERMINAL_STATES:
                raise BrainError("observations can be added only after a council run is terminal")
            observations = run.setdefault("observations", [])
            if not isinstance(observations, list):
                raise BrainError("council run observations field must be an array")
            if len(observations) >= 1_000:
                raise BrainError("council run has reached the observation limit")
            approval_invalidated = (self.approvals / f"{run_id}.promote.json").exists()
            observations.append(observation)
            _atomic_write(active, _json_bytes(run))
        self.append_event(
            "run.observed",
            run_id=run_id,
            observation_id=observation["id"],
            status=status,
            approval_invalidated=approval_invalidated,
        )
        return observation

    def promote(self, run_id: str, *, approved_by: str) -> Path:
        run_id = _safe_identifier(run_id, "run id")
        approved_by = approved_by.strip()
        if not approved_by or "\n" in approved_by or len(approved_by) > 200:
            raise BrainError("approved_by must be a single non-empty line of at most 200 characters")
        with self._lock():
            run = self.load_run(run_id)
            approval = self._read_approval(run_id, scope="promote")
            self._assert_approval_matches(approval, run_id=run_id, scope="promote", run=run)
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
        path = self.approvals / f"{run_id}.{scope}.json"
        with self._lock():
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
            _atomic_write(path, _json_bytes(record))
        self.append_event("run.approved", run_id=run_id, scope=scope, approved_by=approved_by)
        return path

    def _read_approval(self, run_id: str, *, scope: str) -> dict[str, Any]:
        path = self.approvals / f"{run_id}.{scope}.json"
        try:
            approval = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BrainError(f"valid local approval not found for {run_id}: {exc}") from exc
        if not isinstance(approval, dict):
            raise BrainError(f"valid local approval not found for {run_id}: expected object")
        return approval

    def _assert_approval_matches(
        self,
        approval: dict[str, Any],
        *,
        run_id: str,
        scope: str,
        run: dict[str, Any],
    ) -> None:
        if (
            approval.get("run_id") != run_id
            or approval.get("scope") != scope
            or approval.get("run_sha256") != self._run_digest(run)
        ):
            raise BrainError("approval does not match the current immutable run content")

    def verify_approval(self, run_id: str, *, scope: str) -> dict[str, Any]:
        run_id = _safe_identifier(run_id, "run id")
        with self._lock():
            approval = self._read_approval(run_id, scope=scope)
            run = self.load_run(run_id)
            self._assert_approval_matches(approval, run_id=run_id, scope=scope, run=run)
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
