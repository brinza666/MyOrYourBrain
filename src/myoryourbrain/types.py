from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


ALLOWED_RECOMMENDATIONS = {"approve", "revise", "reject", "defer"}
ALLOWED_ACTIONS = {"research", "propose_note", "request_approval", "run_check"}
TERMINAL_STATES = {"accepted", "rejected", "deferred", "blocked", "cooldown"}


class BrainError(RuntimeError):
    """A safe, user-facing workflow error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def require_text(value: Any, name: str, *, maximum: int = 100_000) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BrainError(f"{name} must be non-empty text")
    value = value.strip()
    if len(value) > maximum:
        raise BrainError(f"{name} exceeds {maximum} characters")
    return value


@dataclass(frozen=True)
class Evidence:
    id: str
    source: str
    sha256: str
    quality: float = 0.5
    verified: bool = False
    description: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Evidence":
        quality = float(raw.get("quality", 0.5))
        if not 0 <= quality <= 1:
            raise BrainError("evidence quality must be between 0 and 1")
        verified = raw.get("verified", False)
        if not isinstance(verified, bool):
            raise BrainError("evidence.verified must be a boolean")
        return cls(
            id=require_text(raw.get("id"), "evidence.id", maximum=200),
            source=require_text(raw.get("source"), "evidence.source", maximum=2_000),
            sha256=require_text(raw.get("sha256"), "evidence.sha256", maximum=128),
            quality=quality,
            verified=verified,
            description=str(raw.get("description", "")).strip()[:10_000],
        )


@dataclass(frozen=True)
class Claim:
    id: str
    text: str
    evidence_ids: tuple[str, ...] = ()
    criterion_ids: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Claim":
        refs = raw.get("evidence_ids", [])
        if not isinstance(refs, list) or not all(isinstance(item, str) for item in refs):
            raise BrainError("claim.evidence_ids must be an array of strings")
        criteria = raw.get("criterion_ids", [])
        if not isinstance(criteria, list) or not all(isinstance(item, str) for item in criteria):
            raise BrainError("claim.criterion_ids must be an array of strings")
        return cls(
            id=require_text(raw.get("id"), "claim.id", maximum=200),
            text=require_text(raw.get("text"), "claim.text"),
            evidence_ids=tuple(dict.fromkeys(refs)),
            criterion_ids=tuple(dict.fromkeys(criteria)),
        )


@dataclass(frozen=True)
class ProposedAction:
    kind: str
    description: str
    requires_approval: bool = True

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ProposedAction":
        kind = require_text(raw.get("kind"), "action.kind", maximum=80)
        if kind not in ALLOWED_ACTIONS:
            raise BrainError(
                f"action kind '{kind}' is not allowed; council roles may propose but never execute side effects"
            )
        return cls(
            kind=kind,
            description=require_text(raw.get("description"), "action.description"),
            requires_approval=True,
        )


@dataclass(frozen=True)
class NeedAssessment:
    skills: tuple[str, ...] = ()
    connections: tuple[str, ...] = ()
    knowledge: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "NeedAssessment":
        raw = raw or {}

        def values(name: str) -> tuple[str, ...]:
            items = raw.get(name, [])
            if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
                raise BrainError(f"needs.{name} must be an array of strings")
            return tuple(dict.fromkeys(item.strip() for item in items if item.strip()))

        return cls(
            skills=values("skills"),
            connections=values("connections"),
            knowledge=values("knowledge"),
            optional=values("optional"),
        )


@dataclass(frozen=True)
class RoleResult:
    role: str
    model_id: str
    model_tier: str
    approach_id: str
    summary: str
    claims: tuple[Claim, ...]
    risks: tuple[str, ...]
    contradictions: tuple[str, ...]
    questions: tuple[str, ...]
    recommendation: str
    self_confidence: float
    actions: tuple[ProposedAction, ...] = ()
    needs: NeedAssessment = field(default_factory=NeedAssessment)

    @classmethod
    def from_dict(cls, raw: dict[str, Any], expected_role: str) -> "RoleResult":
        role = require_text(raw.get("role"), "role", maximum=80)
        if role != expected_role:
            raise BrainError(f"provider returned role '{role}', expected '{expected_role}'")
        recommendation = require_text(raw.get("recommendation"), "recommendation", maximum=20)
        if recommendation not in ALLOWED_RECOMMENDATIONS:
            raise BrainError(f"unsupported recommendation: {recommendation}")
        self_confidence = float(raw.get("self_confidence", 0))
        if not 0 <= self_confidence <= 1:
            raise BrainError("self_confidence must be between 0 and 1")

        def strings(name: str) -> tuple[str, ...]:
            items = raw.get(name, [])
            if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
                raise BrainError(f"{name} must be an array of strings")
            return tuple(item.strip() for item in items if item.strip())

        claims_raw = raw.get("claims", [])
        actions_raw = raw.get("actions", [])
        if not isinstance(claims_raw, list) or not all(isinstance(item, dict) for item in claims_raw):
            raise BrainError("claims must be an array of objects")
        if not isinstance(actions_raw, list) or not all(isinstance(item, dict) for item in actions_raw):
            raise BrainError("actions must be an array of objects")
        return cls(
            role=role,
            model_id=require_text(raw.get("model_id"), "model_id", maximum=200),
            model_tier=require_text(raw.get("model_tier"), "model_tier", maximum=80),
            approach_id=require_text(raw.get("approach_id"), "approach_id", maximum=200),
            summary=require_text(raw.get("summary"), "summary"),
            claims=tuple(Claim.from_dict(item) for item in claims_raw),
            risks=strings("risks"),
            contradictions=strings("contradictions"),
            questions=strings("questions"),
            recommendation=recommendation,
            self_confidence=self_confidence,
            actions=tuple(ProposedAction.from_dict(item) for item in actions_raw),
            needs=NeedAssessment.from_dict(raw.get("needs")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReadinessScore:
    total: float
    threshold: float
    calibrated: bool
    components: dict[str, float]
    penalties: dict[str, float]
    hard_gates: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CouncilOutcome:
    run_id: str
    goal: str
    risk: str
    acceptance_criteria: list[dict[str, str]]
    evidence: list[dict[str, Any]]
    status: str
    iterations: int
    readiness: ReadinessScore
    final_summary: str
    roles: dict[str, RoleResult]
    missing_capabilities: dict[str, list[str]]
    next_eligible_at: str | None = None
    transcript: list[dict[str, Any]] = field(default_factory=list)
    observations: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["format"] = "my-or-your-brain-run-v2"
        payload["readiness"] = self.readiness.to_dict()
        payload["roles"] = {name: result.to_dict() for name, result in self.roles.items()}
        return payload

    def assert_terminal(self) -> None:
        if self.status not in TERMINAL_STATES:
            raise BrainError(f"invalid terminal state: {self.status}")
