from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .providers import ProviderRegistry, ROLES
from .security import scan_text
from .storage import BrainStore
from .types import BrainError, CouncilOutcome, Evidence, ReadinessScore, RoleResult, require_text, utc_now


DEFAULT_CONFIG: dict[str, Any] = {
    "thresholds": {"low": 0.85, "medium": 0.90, "high": 0.95},
    "limits": {
        "max_iterations": 3,
        "max_elapsed_seconds": 900,
        "max_no_progress_iterations": 2,
        "minimum_progress": 0.02,
        "cooldown_hours": 24,
    },
}
SHA256_RE = re.compile(r"^[a-fA-F0-9]{64}$")


def _read_config(path: str | Path | None) -> dict[str, Any]:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    if path is None:
        return config
    try:
        incoming = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BrainError(f"cannot load brain config {path}: {exc}") from exc
    if not isinstance(incoming, dict):
        raise BrainError("brain config root must be an object")
    unknown_sections = set(incoming) - {"thresholds", "limits"}
    if unknown_sections:
        raise BrainError(f"unknown brain config sections: {sorted(unknown_sections)}")
    for section in ("thresholds", "limits"):
        value = incoming.get(section)
        if value is not None:
            if not isinstance(value, dict):
                raise BrainError(f"brain config {section} must be an object")
            unknown = set(value) - set(config[section])
            if unknown:
                raise BrainError(f"unknown brain config {section} keys: {sorted(unknown)}")
            config[section].update(value)
    return config


def _role_view(result: RoleResult) -> dict[str, Any]:
    """Concise external rationale only; hidden reasoning is neither requested nor stored."""
    return {
        "role": result.role,
        "model_id": result.model_id,
        "approach_id": result.approach_id,
        "summary": result.summary,
        "claims": [claim.__dict__ for claim in result.claims],
        "risks": list(result.risks),
        "contradictions": list(result.contradictions),
        "questions": list(result.questions),
        "recommendation": result.recommendation,
        "actions": [action.__dict__ for action in result.actions],
        "needs": result.needs.__dict__,
    }


def _safe_failure_message(error: BrainError) -> str:
    """Keep bounded operational diagnostics, never provider secrets or hidden reasoning."""
    message = str(error).strip()[:2_000] or "provider failed without a diagnostic"
    if scan_text(message):
        return "provider failed; diagnostic omitted by secret scanner"
    return message


class CouncilRunner:
    """Bounded, evidence-gated orchestration. Role output can never execute actions."""

    def __init__(self, store: BrainStore, registry: ProviderRegistry, config: dict[str, Any] | None = None):
        self.store = store
        self.registry = registry
        self.config = config or json.loads(json.dumps(DEFAULT_CONFIG))
        self._validate_config()

    @classmethod
    def from_files(
        cls,
        root: str | Path,
        *,
        providers_path: str | Path,
        config_path: str | Path | None = None,
        allow_command_providers: bool = False,
    ) -> "CouncilRunner":
        store = BrainStore(root)
        store.initialize()
        registry = ProviderRegistry.from_file(
            providers_path,
            root=store.root,
            allow_command_providers=allow_command_providers,
        )
        return cls(store, registry, _read_config(config_path))

    def _validate_config(self) -> None:
        try:
            thresholds = self.config.get("thresholds", {})
            for risk in ("low", "medium", "high"):
                value = float(thresholds.get(risk, -1))
                if not 0 <= value <= 1:
                    raise BrainError(f"invalid {risk} readiness threshold")
            limits = self.config.get("limits", {})
            if not 1 <= int(limits.get("max_iterations", 0)) <= 20:
                raise BrainError("max_iterations must be between 1 and 20")
            if not 1 <= int(limits.get("max_elapsed_seconds", 0)) <= 86_400:
                raise BrainError("max_elapsed_seconds must be between 1 and 86400")
            if not 0 <= float(limits.get("minimum_progress", -1)) <= 1:
                raise BrainError("minimum_progress must be between 0 and 1")
            max_iterations = int(limits.get("max_iterations", 0))
            if not 1 <= int(limits.get("max_no_progress_iterations", 0)) <= max_iterations:
                raise BrainError("max_no_progress_iterations must be between 1 and max_iterations")
            if not 1 <= int(limits.get("cooldown_hours", 0)) <= 24 * 30:
                raise BrainError("cooldown_hours must be between 1 and 720")
        except (TypeError, ValueError) as exc:
            raise BrainError(f"brain config contains an invalid numeric value: {exc}") from exc

    def _hard_gates(
        self,
        roles: dict[str, RoleResult],
        evidence: dict[str, Evidence],
        criterion_ids: set[str],
        *,
        risk: str,
    ) -> list[str]:
        gates: list[str] = []
        if set(roles) != set(ROLES):
            gates.append("all four council roles must complete")
        if not evidence or not any(item.verified for item in evidence.values()):
            gates.append("at least one independently verified evidence record is required")
        referenced = {reference for result in roles.values() for claim in result.claims for reference in claim.evidence_ids}
        dangling = sorted(referenced - evidence.keys())
        if dangling:
            gates.append(f"unresolved evidence references: {', '.join(dangling)}")
        unverified = sorted(reference for reference in referenced if reference in evidence and not evidence[reference].verified)
        if unverified:
            gates.append(f"claims rely on unverified evidence: {', '.join(unverified)}")
        referenced_criteria = {
            criterion
            for result in roles.values()
            for claim in result.claims
            for criterion in claim.criterion_ids
        }
        unknown_criteria = sorted(referenced_criteria - criterion_ids)
        if unknown_criteria:
            gates.append(f"claims reference unknown acceptance criteria: {', '.join(unknown_criteria)}")
        chief_claims = roles["chief"].claims if "chief" in roles else ()
        chief_covered = {
            criterion
            for claim in chief_claims
            for criterion in claim.criterion_ids
            if any(ref in evidence and evidence[ref].verified for ref in claim.evidence_ids)
        }
        missing_criteria = sorted(criterion_ids - chief_covered)
        if missing_criteria:
            gates.append(f"chief lacks verified coverage for acceptance criteria: {', '.join(missing_criteria)}")
        if any(roles[name].contradictions for name in ("evaluation", "chief") if name in roles):
            gates.append("evaluation or chief reports unresolved contradictions")
        if roles.get("evaluation") and roles["evaluation"].recommendation in {"reject", "defer"}:
            gates.append("evaluation did not approve or request a revision")
        if roles.get("chief") and roles["chief"].recommendation != "approve":
            gates.append("chief inspector did not approve")
        if not self.registry.chief_is_strongest_validated():
            gates.append("chief is not the strongest locally validated configured model")
        missing_required = self._missing_capabilities(roles)
        if any(missing_required[name] for name in ("skills", "connections", "knowledge")):
            gates.append("required skill, connection, or knowledge is unavailable")
        if risk == "high":
            gates.append("high-risk outcome requires a separate recorded human approval")
        return gates

    def _readiness(
        self,
        roles: dict[str, RoleResult],
        evidence: dict[str, Evidence],
        criterion_ids: set[str],
        *,
        risk: str,
    ) -> ReadinessScore:
        claims = [claim for result in roles.values() for claim in result.claims]
        references = [reference for claim in claims for reference in claim.evidence_ids]
        verified = [item for item in evidence.values() if item.verified]
        valid_references = [reference for reference in references if reference in evidence and evidence[reference].verified]
        covered = [claim for claim in claims if any(ref in evidence and evidence[ref].verified for ref in claim.evidence_ids)]
        chief_claims = roles["chief"].claims if "chief" in roles else ()
        covered_criteria = {
            criterion
            for claim in chief_claims
            for criterion in claim.criterion_ids
            if any(ref in evidence and evidence[ref].verified for ref in claim.evidence_ids)
        }
        reproducible = [item for item in evidence.values() if SHA256_RE.fullmatch(item.sha256)]
        contradiction_clear = not any(
            roles[name].contradictions for name in ("evaluation", "chief") if name in roles
        )
        hard_gates = self._hard_gates(
            roles, evidence, criterion_ids, risk=risk
        )
        non_human_gates = [gate for gate in hard_gates if "separate recorded human approval" not in gate]
        components = {
            "evidence_quality": sum(item.quality for item in verified) / len(verified) if verified else 0.0,
            "claim_coverage": len(covered) / len(claims) if claims else 0.0,
            "criterion_coverage": len(covered_criteria) / len(criterion_ids) if criterion_ids else 0.0,
            "reference_validity": len(valid_references) / len(references) if references else 0.0,
            "contradiction_clearance": 1.0 if contradiction_clear else 0.0,
            "deterministic_checks": 1.0 if not non_human_gates else 0.0,
            "reproducibility": len(reproducible) / len(evidence) if evidence else 0.0,
        }
        weights = {
            "evidence_quality": 0.20,
            "claim_coverage": 0.15,
            "criterion_coverage": 0.20,
            "reference_validity": 0.10,
            "contradiction_clearance": 0.15,
            "deterministic_checks": 0.10,
            "reproducibility": 0.10,
        }
        penalties: dict[str, float] = {}
        if len(self.registry.model_ids()) < 2:
            penalties["correlated_single_model_council"] = 0.05
        total = sum(components[name] * weight for name, weight in weights.items()) - sum(penalties.values())
        return ReadinessScore(
            total=round(max(0.0, min(1.0, total)), 4),
            threshold=float(self.config["thresholds"][risk]),
            calibrated=False,
            components={name: round(value, 4) for name, value in components.items()},
            penalties=penalties,
            hard_gates=hard_gates,
        )

    @staticmethod
    def _missing_capabilities(roles: dict[str, RoleResult]) -> dict[str, list[str]]:
        result = {"skills": [], "connections": [], "knowledge": [], "optional": []}
        for role in roles.values():
            for name in result:
                result[name].extend(getattr(role.needs, name))
        return {name: list(dict.fromkeys(items)) for name, items in result.items()}

    @staticmethod
    def _strategy_hash(roles: dict[str, RoleResult], evidence: dict[str, Evidence]) -> str:
        role_content: dict[str, Any] = {}
        for name, role in sorted(roles.items()):
            value = role.to_dict()
            value.pop("approach_id", None)
            value.pop("self_confidence", None)
            role_content[name] = value
        value = {
            "role_content": role_content,
            "evidence": {name: item.sha256 for name, item in sorted(evidence.items())},
        }
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()

    def _verify_evidence(self, evidence: list[Evidence]) -> list[Evidence]:
        verified: list[Evidence] = []
        for item in evidence:
            if not SHA256_RE.fullmatch(item.sha256):
                raise BrainError(f"evidence {item.id} sha256 must be 64 hexadecimal characters")
            source = Path(item.source)
            if not source.is_absolute():
                source = self.store.root / source
            try:
                source.relative_to(self.store.root)
                self.store._assert_regular_contained(source, self.store.root)
                resolved = source.resolve(strict=True)
            except (BrainError, OSError, ValueError):
                verified.append(
                    Evidence(item.id, item.source, item.sha256.lower(), item.quality, False, item.description)
                )
                continue
            if not resolved.is_file() or resolved.stat().st_size > 5_000_000:
                is_verified = False
            else:
                digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
                is_verified = digest == item.sha256.casefold()
            verified.append(
                Evidence(item.id, item.source, item.sha256.lower(), item.quality, is_verified, item.description)
            )
        return verified

    def run(
        self,
        goal: str,
        *,
        acceptance_criteria: list[str],
        risk: str,
        evidence: list[Evidence],
        run_id: str | None = None,
    ) -> CouncilOutcome:
        goal = require_text(goal, "goal", maximum=100_000)
        if risk not in {"low", "medium", "high"}:
            raise BrainError("risk must be low, medium, or high")
        if len(acceptance_criteria) > 50:
            raise BrainError("at most 50 acceptance criteria are allowed")
        criteria = [require_text(item, "acceptance criterion", maximum=20_000) for item in acceptance_criteria if item.strip()]
        if not criteria:
            raise BrainError("at least one acceptance criterion is required")
        if len(evidence) > 100:
            raise BrainError("at most 100 evidence records are allowed")
        sensitive = scan_text("\n".join([goal, *criteria, *(item.source + "\n" + item.description for item in evidence)]))
        if sensitive:
            raise BrainError(f"task input contains a possible secret: {', '.join(sensitive)}")
        evidence = self._verify_evidence(evidence)
        evidence_by_id = {item.id: item for item in evidence}
        if len(evidence_by_id) != len(evidence):
            raise BrainError("evidence ids must be unique")
        run_id = run_id or f"run-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
        criteria_records = [
            {"id": f"criterion-{index}", "text": text}
            for index, text in enumerate(criteria, start=1)
        ]
        evidence_records = [dict(item.__dict__) for item in evidence]
        transcript: list[dict[str, Any]] = []
        self.store.begin_run(
            run_id,
            {
                "format": "my-or-your-brain-run-v2",
                "run_id": run_id,
                "goal": goal,
                "risk": risk,
                "acceptance_criteria": criteria_records,
                "evidence": evidence_records,
                "status": "starting",
                "transcript": transcript,
                "observations": [],
                "updated_at": utc_now(),
            },
        )
        limits = self.config["limits"]
        max_iterations = int(limits["max_iterations"])
        elapsed_limit = int(limits["max_elapsed_seconds"])
        minimum_progress = float(limits["minimum_progress"])
        max_no_progress = int(limits["max_no_progress_iterations"])
        cooldown_hours = int(limits["cooldown_hours"])
        started = time.monotonic()
        deadline = started + elapsed_limit
        seen_strategies: set[str] = set()
        no_progress = 0
        previous_score = -1.0
        final_roles: dict[str, RoleResult] = {}
        readiness = ReadinessScore(0, float(self.config["thresholds"][risk]), False, {}, {}, ["not run"])
        status = "deferred"
        next_eligible_at: str | None = None

        base_request = {
            "task": {
                "goal": goal,
                "acceptance_criteria": criteria_records,
                "risk": risk,
                "readiness_target": float(self.config["thresholds"][risk]),
                "permitted_actions": ["research", "propose_note", "request_approval", "run_check"],
            },
            "evidence": evidence_records,
            "rules": {
                "output_only": True,
                "no_hidden_reasoning_requested": True,
                "recommendation_is_not_execution": True,
            },
        }
        self.store.append_event("council.started", run_id=run_id, risk=risk)
        iterations_completed = 0
        criterion_ids = {f"criterion-{index}" for index in range(1, len(criteria) + 1)}

        def save_checkpoint(
            *,
            iteration: int,
            completed_role: str | None = None,
            failed_role: str | None = None,
        ) -> None:
            payload: dict[str, Any] = {
                "format": "my-or-your-brain-run-v2",
                "run_id": run_id,
                "goal": goal,
                "risk": risk,
                "acceptance_criteria": criteria_records,
                "evidence": evidence_records,
                "status": "running",
                "iteration": iteration,
                "transcript": list(transcript),
                "observations": [],
                "updated_at": utc_now(),
            }
            if completed_role is not None:
                payload["completed_role"] = completed_role
            if failed_role is not None:
                payload["failed_role"] = failed_role
            self.store.save_run(run_id, payload)

        def generate(role: str, request: dict[str, Any], iteration: int, phase: str) -> RoleResult:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                error = BrainError("global council deadline expired")
                transcript.append(
                    {
                        "iteration": iteration,
                        "role": role,
                        "phase": phase,
                        "status": "failed",
                        "recorded_at": utc_now(),
                        "error": _safe_failure_message(error),
                    }
                )
                save_checkpoint(iteration=iteration, failed_role=role)
                raise error
            try:
                result = self.registry.generate(role, request, iteration, timeout_seconds=remaining)
            except BrainError as error:
                transcript.append(
                    {
                        "iteration": iteration,
                        "role": role,
                        "phase": phase,
                        "status": "failed",
                        "recorded_at": utc_now(),
                        "error": _safe_failure_message(error),
                    }
                )
                save_checkpoint(iteration=iteration, failed_role=role)
                raise
            except Exception as unexpected:
                error = BrainError(
                    f"provider failed unexpectedly ({type(unexpected).__name__}); diagnostic omitted"
                )
                transcript.append(
                    {
                        "iteration": iteration,
                        "role": role,
                        "phase": phase,
                        "status": "failed",
                        "recorded_at": utc_now(),
                        "error": _safe_failure_message(error),
                    }
                )
                save_checkpoint(iteration=iteration, failed_role=role)
                raise error from unexpected
            transcript.append(
                {
                    "iteration": iteration,
                    "role": role,
                    "phase": phase,
                    "status": "succeeded",
                    "recorded_at": utc_now(),
                    "result": result.to_dict(),
                }
            )
            save_checkpoint(iteration=iteration, completed_role=role)
            return result

        for iteration in range(1, max_iterations + 1):
            if time.monotonic() >= deadline:
                status = "cooldown"
                next_eligible_at = (datetime.now(timezone.utc) + timedelta(hours=cooldown_hours)).replace(microsecond=0).isoformat()
                break
            try:
                positive = generate(
                    "positive",
                    {**base_request, "phase": "independent_proposal"},
                    iteration,
                    "independent_proposal",
                )
                final_roles = {"positive": positive}
                negative = generate(
                    "negative",
                    {**base_request, "phase": "independent_failure_analysis"},
                    iteration,
                    "independent_failure_analysis",
                )
                final_roles["negative"] = negative
                evaluation_request = {
                    **base_request,
                    "phase": "evaluate",
                    "candidate_outputs": [_role_view(positive), _role_view(negative)],
                }
                evaluation = generate("evaluation", evaluation_request, iteration, "evaluate")
                final_roles["evaluation"] = evaluation
                chief_request = {
                    **base_request,
                    "phase": "chief_gate",
                    "candidate_outputs": [_role_view(positive), _role_view(negative), _role_view(evaluation)],
                }
                chief = generate("chief", chief_request, iteration, "chief_gate")
                final_roles["chief"] = chief
            except BrainError as exc:
                iterations_completed = iteration
                transient = "deadline" in str(exc).casefold() or "timed out" in str(exc).casefold()
                status = "cooldown" if transient else "blocked"
                if transient:
                    next_eligible_at = (
                        datetime.now(timezone.utc) + timedelta(hours=cooldown_hours)
                    ).replace(microsecond=0).isoformat()
                readiness = ReadinessScore(
                    total=0.0,
                    threshold=float(self.config["thresholds"][risk]),
                    calibrated=False,
                    components={},
                    penalties={},
                    hard_gates=[f"provider or deadline failure: {_safe_failure_message(exc)}"],
                )
                break
            final_roles = {
                "positive": positive,
                "negative": negative,
                "evaluation": evaluation,
                "chief": chief,
            }
            iterations_completed = iteration
            strategy = self._strategy_hash(final_roles, evidence_by_id)
            readiness = self._readiness(
                final_roles,
                evidence_by_id,
                criterion_ids,
                risk=risk,
            )
            repeated = strategy in seen_strategies
            seen_strategies.add(strategy)
            self.store.append_event(
                "council.iteration",
                run_id=run_id,
                iteration=iteration,
                strategy_hash=strategy,
                readiness=readiness.total,
                hard_gates=readiness.hard_gates,
            )
            if not readiness.hard_gates and readiness.total >= readiness.threshold:
                status = "accepted"
                break
            if readiness.hard_gates == ["high-risk outcome requires a separate recorded human approval"] and readiness.total >= readiness.threshold:
                status = "deferred"
                break
            if chief.recommendation == "reject" and evaluation.recommendation == "reject":
                status = "rejected"
                break
            if repeated:
                status = "cooldown"
                next_eligible_at = (datetime.now(timezone.utc) + timedelta(hours=cooldown_hours)).replace(microsecond=0).isoformat()
                break
            progress = readiness.total - previous_score if previous_score >= 0 else readiness.total
            no_progress = no_progress + 1 if progress < minimum_progress else 0
            previous_score = readiness.total
            if no_progress >= max_no_progress:
                status = "cooldown"
                next_eligible_at = (datetime.now(timezone.utc) + timedelta(hours=cooldown_hours)).replace(microsecond=0).isoformat()
                break

        missing = self._missing_capabilities(final_roles)
        if status == "deferred" and any(missing[name] for name in ("skills", "connections", "knowledge")):
            status = "blocked"
        if status in {"deferred", "blocked"} and iterations_completed >= max_iterations:
            next_eligible_at = (datetime.now(timezone.utc) + timedelta(hours=cooldown_hours)).replace(microsecond=0).isoformat()
        summary = (
            final_roles["chief"].summary
            if "chief" in final_roles
            else "Council stopped before the chief inspector completed."
        )
        outcome = CouncilOutcome(
            run_id=run_id,
            goal=goal,
            risk=risk,
            acceptance_criteria=criteria_records,
            evidence=evidence_records,
            status=status,
            iterations=iterations_completed,
            readiness=readiness,
            final_summary=summary,
            roles=final_roles,
            missing_capabilities=missing,
            next_eligible_at=next_eligible_at,
            transcript=list(transcript),
            observations=[],
        )
        outcome.assert_terminal()
        self.store.save_run(run_id, outcome.to_dict())
        self.store.append_event("council.finished", run_id=run_id, status=status, readiness=readiness.total)
        return outcome
