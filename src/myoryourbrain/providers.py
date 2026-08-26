from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .types import BrainError, RoleResult


ROLES = ("positive", "negative", "evaluation", "chief")


class Provider(Protocol):
    def generate(
        self,
        role: str,
        request: dict[str, Any],
        iteration: int,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]: ...


class FixtureProvider:
    """Deterministic provider used by tests and offline examples."""

    def __init__(self, payload: dict[str, Any]):
        iterations = payload.get("iterations")
        if not isinstance(iterations, list) or not iterations:
            raise BrainError("fixture provider requires a non-empty iterations array")
        self.iterations = iterations

    @classmethod
    def from_file(cls, path: Path) -> "FixtureProvider":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BrainError(f"cannot load fixture provider {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise BrainError("fixture provider root must be an object")
        return cls(payload)

    def generate(
        self,
        role: str,
        request: dict[str, Any],
        iteration: int,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        del timeout_seconds
        del request
        try:
            current = self.iterations[iteration - 1]
            raw = current[role]
        except (IndexError, KeyError, TypeError) as exc:
            raise BrainError(f"fixture has no {role} result for iteration {iteration}") from exc
        if not isinstance(raw, dict):
            raise BrainError(f"fixture result for {role} must be an object")
        return dict(raw)


class CommandProvider:
    """JSON-over-stdin adapter for any local AI CLI. It never invokes a shell."""

    def __init__(
        self,
        command: list[str],
        *,
        root: Path,
        timeout_seconds: int = 120,
        max_output_bytes: int = 1_000_000,
        pass_env: list[str] | None = None,
    ):
        if not command or not all(isinstance(item, str) and item for item in command):
            raise BrainError("command provider requires a non-empty string array")
        if not 1 <= timeout_seconds <= 3600:
            raise BrainError("provider timeout must be between 1 and 3600 seconds")
        if not 1024 <= max_output_bytes <= 10_000_000:
            raise BrainError("provider max_output_bytes must be between 1024 and 10000000")
        self.command = command
        self.root = root
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.pass_env = tuple(pass_env or ())
        if not all(name.isidentifier() or (name.replace("_", "").isalnum() and not name[0].isdigit()) for name in self.pass_env):
            raise BrainError("provider pass_env names must be ordinary environment variable names")
        self.work_directory = root / ".local" / "provider-work"
        self.work_directory.mkdir(parents=True, exist_ok=True)

    def generate(
        self,
        role: str,
        request: dict[str, Any],
        iteration: int,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        envelope = {"protocol": "my-or-your-brain-provider-v1", "role": role, "iteration": iteration, **request}
        safe_names = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL"}
        child_env = {name: value for name, value in os.environ.items() if name.upper() in safe_names}
        for name in self.pass_env:
            if name in os.environ:
                child_env[name] = os.environ[name]
        child_env["MYORYOURBRAIN_ROLE"] = role
        timeout = min(self.timeout_seconds, timeout_seconds) if timeout_seconds is not None else self.timeout_seconds
        if timeout <= 0:
            raise BrainError("global council deadline expired before provider invocation")
        try:
            with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                completed = subprocess.run(
                    self.command,
                    input=json.dumps(envelope, ensure_ascii=False).encode("utf-8"),
                    cwd=self.work_directory,
                    env=child_env,
                    stdout=stdout,
                    stderr=stderr,
                    timeout=timeout,
                    shell=False,
                    check=False,
                )
                stdout_size = stdout.tell()
                stderr_size = stderr.tell()
                stdout.seek(0)
                stderr.seek(max(0, stderr_size - 2000))
                output = stdout.read(self.max_output_bytes + 1)
                error_output = stderr.read(2000).decode("utf-8", errors="replace").strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BrainError(f"provider command failed for {role}: {exc}") from exc
        if completed.returncode != 0:
            raise BrainError(f"provider command exited {completed.returncode} for {role}: {error_output}")
        if stdout_size > self.max_output_bytes:
            raise BrainError(f"provider output exceeds {self.max_output_bytes} bytes")
        try:
            raw = json.loads(output.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BrainError(f"provider returned invalid JSON for {role}: {exc}") from exc
        if not isinstance(raw, dict):
            raise BrainError(f"provider result for {role} must be an object")
        return raw


@dataclass(frozen=True)
class Assignment:
    provider: str
    model_id: str
    model_tier: str
    rank: int
    validated: bool


class ProviderRegistry:
    def __init__(self, providers: dict[str, Provider], assignments: dict[str, Assignment]):
        missing = set(ROLES) - assignments.keys()
        if missing:
            raise BrainError(f"provider assignments missing roles: {sorted(missing)}")
        unknown = {assignment.provider for assignment in assignments.values()} - providers.keys()
        if unknown:
            raise BrainError(f"assignments reference unknown providers: {sorted(unknown)}")
        for role, assignment in assignments.items():
            if not isinstance(assignment.validated, bool):
                raise BrainError(f"assignment {role} validated must be a boolean")
            if not isinstance(assignment.rank, int) or isinstance(assignment.rank, bool) or not 0 <= assignment.rank <= 100:
                raise BrainError(f"assignment {role} rank must be an integer between 0 and 100")
        self.providers = providers
        self.assignments = assignments

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        *,
        root: str | Path,
        allow_command_providers: bool = False,
    ) -> "ProviderRegistry":
        path = Path(path).expanduser().resolve()
        root = Path(root).expanduser().resolve()
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BrainError(f"cannot load provider config {path}: {exc}") from exc
        if not isinstance(config, dict):
            raise BrainError("provider config root must be an object")
        provider_objects: dict[str, Provider] = {}
        raw_providers = config.get("providers", {})
        if not isinstance(raw_providers, dict):
            raise BrainError("providers must be an object")
        for name, raw in raw_providers.items():
            if not isinstance(raw, dict):
                raise BrainError(f"provider {name} must be an object")
            kind = raw.get("kind")
            if kind == "fixture":
                fixture_path = Path(str(raw.get("path", "")))
                if not fixture_path.is_absolute():
                    fixture_path = path.parent / fixture_path
                provider_objects[name] = FixtureProvider.from_file(fixture_path.resolve())
            elif kind == "command":
                if not allow_command_providers:
                    raise BrainError(
                        "command providers are privileged and disabled by default; explicitly authorize them"
                    )
                command = raw.get("command")
                if not isinstance(command, list):
                    raise BrainError(f"command for provider {name} must be an array")
                pass_env = raw.get("pass_env", [])
                if not isinstance(pass_env, list) or not all(isinstance(item, str) for item in pass_env):
                    raise BrainError(f"pass_env for provider {name} must be an array of strings")
                provider_objects[name] = CommandProvider(
                    command,
                    root=root,
                    timeout_seconds=int(raw.get("timeout_seconds", 120)),
                    max_output_bytes=int(raw.get("max_output_bytes", 1_000_000)),
                    pass_env=pass_env,
                )
            else:
                raise BrainError(f"unsupported provider kind for {name}: {kind!r}")
        raw_assignments = config.get("assignments", {})
        if not isinstance(raw_assignments, dict):
            raise BrainError("assignments must be an object")
        assignments: dict[str, Assignment] = {}
        for role, raw in raw_assignments.items():
            if role not in ROLES or not isinstance(raw, dict):
                raise BrainError(f"invalid assignment role: {role}")
            assignments[role] = Assignment(
                provider=str(raw.get("provider", "")),
                model_id=str(raw.get("model_id", "")).strip(),
                model_tier=str(raw.get("model_tier", "")).strip(),
                rank=int(raw.get("rank", 0)),
                validated=raw.get("validated", False),
            )
            if not isinstance(assignments[role].validated, bool):
                raise BrainError(f"assignment {role} validated must be a boolean")
            if not 0 <= assignments[role].rank <= 100:
                raise BrainError(f"assignment {role} rank must be between 0 and 100")
            if not assignments[role].model_id or not assignments[role].model_tier:
                raise BrainError(f"assignment {role} needs model_id and model_tier")
        return cls(provider_objects, assignments)

    def generate(
        self,
        role: str,
        request: dict[str, Any],
        iteration: int,
        *,
        timeout_seconds: float | None = None,
    ) -> RoleResult:
        assignment = self.assignments[role]
        raw = self.providers[assignment.provider].generate(
            role, request, iteration, timeout_seconds=timeout_seconds
        )
        raw = dict(raw)
        raw["role"] = role
        raw["model_id"] = assignment.model_id
        raw["model_tier"] = assignment.model_tier
        return RoleResult.from_dict(raw, role)

    def chief_is_strongest_validated(self) -> bool:
        validated_ranks = [item.rank for item in self.assignments.values() if item.validated]
        if not validated_ranks or not self.assignments["chief"].validated:
            return False
        return self.assignments["chief"].rank >= max(validated_ranks)

    def model_ids(self) -> set[str]:
        return {item.model_id for item in self.assignments.values()}
