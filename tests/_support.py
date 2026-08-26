from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from myoryourbrain.council import CouncilRunner
from myoryourbrain.providers import Assignment, FixtureProvider, ProviderRegistry, ROLES
from myoryourbrain.storage import BrainStore
from myoryourbrain.types import Evidence


def verified_evidence(store: BrainStore) -> Evidence:
    content = b"Deterministic local evidence for the offline council.\n"
    source = store.root / "fixtures" / "test-evidence.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(content)
    return Evidence(
        id="evidence-1",
        source=source.relative_to(store.root).as_posix(),
        sha256=hashlib.sha256(content).hexdigest(),
        quality=1.0,
        verified=False,
        description="Verification must be derived from this local file, not trusted input.",
    )


def role_payload(
    role: str,
    *,
    recommendation: str = "approve",
    approach_id: str | None = None,
    evidence_ids: list[str] | None = None,
    criterion_ids: list[str] | None = None,
    contradictions: list[str] | None = None,
    actions: list[dict[str, Any]] | None = None,
    needs: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    references = ["evidence-1"] if evidence_ids is None else evidence_ids
    return {
        "role": role,
        "approach_id": approach_id or f"{role}-approach",
        "summary": f"{role} completed its bounded review.",
        "claims": [
            {
                "id": f"{role}-claim",
                "text": f"{role} conclusion is supported.",
                "evidence_ids": references,
                "criterion_ids": ["criterion-1"] if criterion_ids is None else criterion_ids,
            }
        ],
        "risks": [],
        "contradictions": contradictions or [],
        "questions": [],
        "recommendation": recommendation,
        "self_confidence": 0.99,
        "actions": actions or [],
        "needs": needs or {},
    }


def council_iteration(**role_overrides: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        role: role_payload(role, **role_overrides.get(role, {}))
        for role in ROLES
    }


def make_runner(
    store: BrainStore,
    *,
    iterations: list[dict[str, dict[str, Any]]] | None = None,
    max_iterations: int = 3,
    max_no_progress_iterations: int = 2,
) -> CouncilRunner:
    provider = FixtureProvider({"iterations": iterations or [council_iteration()]})
    assignments = {
        "positive": Assignment("fixture", "model-positive", "economy", 10, True),
        "negative": Assignment("fixture", "model-negative", "economy", 20, True),
        "evaluation": Assignment("fixture", "model-evaluation", "capable", 30, True),
        "chief": Assignment("fixture", "model-chief", "decision", 100, True),
    }
    registry = ProviderRegistry({"fixture": provider}, assignments)
    config = {
        "thresholds": {"low": 0.85, "medium": 0.90, "high": 0.95},
        "limits": {
            "max_iterations": max_iterations,
            "max_elapsed_seconds": 900,
            "max_no_progress_iterations": max_no_progress_iterations,
            "minimum_progress": 0.02,
            "cooldown_hours": 24,
        },
    }
    return CouncilRunner(store, registry, config)
