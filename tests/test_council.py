from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

from tests._support import (
    BrainStore,
    council_iteration,
    make_runner,
    role_payload,
    verified_evidence,
)
from myoryourbrain.council import CouncilRunner
from myoryourbrain.providers import Assignment, CommandProvider, ProviderRegistry, ROLES
from myoryourbrain.types import BrainError, Evidence


class CouncilRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name) / "brain"
        self.store = BrainStore(self.root)
        self.store.initialize()

    def test_accepts_verified_outcome_from_distinct_models(self) -> None:
        runner = make_runner(self.store)

        outcome = runner.run(
            "Adopt the bounded workflow",
            acceptance_criteria=["All hard gates pass"],
            risk="low",
            evidence=[verified_evidence(self.store)],
            run_id="accepted-council",
        )

        self.assertEqual("accepted", outcome.status)
        self.assertEqual(1, outcome.iterations)
        self.assertEqual([], outcome.readiness.hard_gates)
        self.assertGreaterEqual(outcome.readiness.total, outcome.readiness.threshold)
        self.assertEqual(4, len({result.model_id for result in outcome.roles.values()}))
        self.assertEqual("model-chief", outcome.roles["chief"].model_id)
        self.assertTrue(runner.registry.chief_is_strongest_validated())
        stored = self.store.load_run("accepted-council")
        self.assertEqual("accepted", stored["status"])
        self.assertEqual("my-or-your-brain-run-v2", stored["format"])
        self.assertEqual(
            [{"id": "criterion-1", "text": "All hard gates pass"}],
            stored["acceptance_criteria"],
        )
        self.assertEqual("evidence-1", stored["evidence"][0]["id"])
        self.assertTrue(stored["evidence"][0]["verified"])
        self.assertEqual(
            ["positive", "negative", "evaluation", "chief"],
            [entry["role"] for entry in stored["transcript"]],
        )
        self.assertTrue(all(entry["status"] == "succeeded" for entry in stored["transcript"]))
        serialized = json.dumps(stored["transcript"], sort_keys=True)
        self.assertNotIn("chain_of_thought", serialized)
        self.assertNotIn("raw_response", serialized)

    def test_claimed_verified_flag_cannot_bypass_a_mismatched_local_hash(self) -> None:
        evidence = verified_evidence(self.store)
        forged = replace(evidence, sha256="0" * 64, verified=True)
        runner = make_runner(
            self.store,
            iterations=[council_iteration(), council_iteration()],
        )

        outcome = runner.run(
            "Reject forged evidence verification",
            acceptance_criteria=["Evidence hash matches local content"],
            risk="low",
            evidence=[forged],
            run_id="forged-verification",
        )

        self.assertNotEqual("accepted", outcome.status)
        self.assertIn(
            "claims rely on unverified evidence: evidence-1",
            outcome.readiness.hard_gates,
        )

    def test_high_risk_requires_recorded_human_approval(self) -> None:
        runner = make_runner(self.store)

        gated = runner.run(
            "Apply a high-risk change",
            acceptance_criteria=["Human approval is recorded"],
            risk="high",
            evidence=[verified_evidence(self.store)],
            run_id="high-risk-gated",
        )

        self.assertEqual("deferred", gated.status)
        self.assertEqual(1, gated.iterations)
        self.assertIsNone(gated.next_eligible_at)
        self.assertIn(
            "high-risk outcome requires a separate recorded human approval",
            gated.readiness.hard_gates,
        )
        with self.assertRaisesRegex(BrainError, "valid local approval not found"):
            self.store.promote("high-risk-gated", approved_by="owner")

        approval_path = self.store.approve_run(
            "high-risk-gated",
            approved_by="owner",
            scope="promote",
            confirmed=True,
        )
        self.assertTrue(approval_path.exists())
        self.assertEqual(
            "owner",
            self.store.verify_approval("high-risk-gated", scope="promote")["approved_by"],
        )
        self.assertTrue(
            self.store.promote("high-risk-gated", approved_by="owner").exists()
        )

    def test_repeated_strategy_enters_bounded_cooldown(self) -> None:
        repeated = [council_iteration(), council_iteration(), council_iteration()]
        runner = make_runner(
            self.store,
            iterations=repeated,
            max_iterations=10,
            max_no_progress_iterations=10,
        )

        outcome = runner.run(
            "Resolve an evidence gap",
            acceptance_criteria=["Use independently verified evidence"],
            risk="low",
            evidence=[],
            run_id="repeated-strategy",
        )

        self.assertEqual("cooldown", outcome.status)
        self.assertEqual(2, outcome.iterations)
        self.assertIsNotNone(outcome.next_eligible_at)
        events = [
            json.loads(line)
            for line in self.store.events.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        iteration_events = [
            event
            for event in events
            if event["event"] == "council.iteration" and event["run_id"] == "repeated-strategy"
        ]
        self.assertEqual(2, len(iteration_events))
        self.assertEqual(
            iteration_events[0]["strategy_hash"],
            iteration_events[1]["strategy_hash"],
        )
        transcript = self.store.load_run("repeated-strategy")["transcript"]
        self.assertEqual(8, len(transcript))
        self.assertEqual([1] * 4 + [2] * 4, [entry["iteration"] for entry in transcript])

    def test_forbidden_action_is_rejected_as_a_blocked_outcome(self) -> None:
        invalid_iteration = council_iteration()
        invalid_iteration["positive"] = role_payload(
            "positive",
            actions=[{"kind": "delete_files", "description": "Remove the repository."}],
        )
        runner = make_runner(self.store, iterations=[invalid_iteration])

        outcome = runner.run(
            "Attempt a forbidden side effect",
            acceptance_criteria=["Never delete files"],
            risk="high",
            evidence=[verified_evidence(self.store)],
            run_id="forbidden-action",
        )

        self.assertEqual("blocked", outcome.status)
        self.assertTrue(any("not allowed" in gate for gate in outcome.readiness.hard_gates))
        stored = self.store.load_run("forbidden-action")
        self.assertEqual("blocked", stored["status"])
        self.assertEqual(1, len(stored["transcript"]))
        self.assertEqual("positive", stored["transcript"][0]["role"])
        self.assertEqual("failed", stored["transcript"][0]["status"])

    def test_command_provider_is_disabled_by_default_without_execution(self) -> None:
        config = {
            "providers": {
                "command": {
                    "kind": "command",
                    "command": ["this-command-must-never-run"],
                }
            },
            "assignments": {
                role: {
                    "provider": "command",
                    "model_id": f"model-{role}",
                    "model_tier": "test",
                    "rank": 100 if role == "chief" else 10,
                    "validated": True,
                }
                for role in ROLES
            },
        }
        path = self.root / "providers.json"
        path.write_text(json.dumps(config), encoding="utf-8")

        with self.assertRaisesRegex(BrainError, "disabled by default"):
            ProviderRegistry.from_file(path, root=self.root)

    def test_command_provider_does_not_inherit_unlisted_secrets(self) -> None:
        script = self.root / "provider.py"
        script.write_text(
            "import json, os, sys\n"
            "json.load(sys.stdin)\n"
            "json.dump({'secret_seen': 'MYOB_TEST_SECRET' in os.environ}, sys.stdout)\n",
            encoding="utf-8",
        )
        old = os.environ.get("MYOB_TEST_SECRET")
        os.environ["MYOB_TEST_SECRET"] = "not-for-the-provider"
        self.addCleanup(
            lambda: os.environ.pop("MYOB_TEST_SECRET", None)
            if old is None
            else os.environ.__setitem__("MYOB_TEST_SECRET", old)
        )
        provider = CommandProvider([sys.executable, str(script)], root=self.root)

        result = provider.generate("positive", {"task": {}}, 1, timeout_seconds=5)

        self.assertFalse(result["secret_seen"])

    def test_global_deadline_bounds_command_provider_call(self) -> None:
        script = self.root / "slow-provider.py"
        script.write_text("import time\ntime.sleep(5)\n", encoding="utf-8")
        provider = CommandProvider(
            [sys.executable, str(script)], root=self.root, timeout_seconds=30
        )
        assignments = {
            role: Assignment("command", f"model-{role}", "test", 100 if role == "chief" else 10, True)
            for role in ROLES
        }
        registry = ProviderRegistry({"command": provider}, assignments)
        runner = CouncilRunner(
            self.store,
            registry,
            {
                "thresholds": {"low": 0.85, "medium": 0.9, "high": 0.95},
                "limits": {
                    "max_iterations": 1,
                    "max_elapsed_seconds": 1,
                    "max_no_progress_iterations": 1,
                    "minimum_progress": 0.02,
                    "cooldown_hours": 24,
                },
            },
        )
        started = time.monotonic()

        outcome = runner.run(
            "Enforce the deadline",
            acceptance_criteria=["The run stops within its deadline"],
            risk="low",
            evidence=[],
            run_id="deadline-bound",
        )

        self.assertLess(time.monotonic() - started, 2.5)
        self.assertEqual("cooldown", outcome.status)
        self.assertIsNotNone(outcome.next_eligible_at)

    def test_unexpected_provider_failure_is_recorded_and_terminal(self) -> None:
        class UnexpectedProvider:
            def generate(self, role, request, iteration, timeout_seconds):
                raise RuntimeError("synthetic provider crash")

        assignments = {
            role: Assignment("unexpected", f"model-{role}", "test", 100 if role == "chief" else 10, True)
            for role in ROLES
        }
        registry = ProviderRegistry({"unexpected": UnexpectedProvider()}, assignments)
        runner = CouncilRunner(
            self.store,
            registry,
            {
                "thresholds": {"low": 0.85, "medium": 0.9, "high": 0.95},
                "limits": {
                    "max_iterations": 1,
                    "max_elapsed_seconds": 10,
                    "max_no_progress_iterations": 1,
                    "minimum_progress": 0.02,
                    "cooldown_hours": 24,
                },
            },
        )

        outcome = runner.run(
            "Record an unexpected provider failure",
            acceptance_criteria=["The run becomes terminal with a bounded failure record"],
            risk="low",
            evidence=[],
            run_id="unexpected-provider",
        )

        self.assertEqual("blocked", outcome.status)
        stored = self.store.load_run("unexpected-provider")
        self.assertEqual("blocked", stored["status"])
        self.assertEqual("failed", stored["transcript"][0]["status"])
        self.assertIn("RuntimeError", stored["transcript"][0]["error"])
        self.assertNotIn("synthetic provider crash", stored["transcript"][0]["error"])

    def test_strict_boolean_input_and_config_validation(self) -> None:
        with self.assertRaisesRegex(BrainError, "must be a boolean"):
            Evidence.from_dict(
                {
                    "id": "evidence",
                    "source": "fixtures/evidence.txt",
                    "sha256": "0" * 64,
                    "verified": "false",
                }
            )

        assignments = {
            role: Assignment("fixture", f"model-{role}", "test", 1, "false")
            for role in ROLES
        }
        with self.assertRaisesRegex(BrainError, "must be a boolean"):
            ProviderRegistry({"fixture": object()}, assignments)


if __name__ == "__main__":
    unittest.main()
