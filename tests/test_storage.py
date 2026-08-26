from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tests._support import BrainStore, REPOSITORY_ROOT, role_payload
from myoryourbrain.types import BrainError


def recorded_council_payload(run_id: str, store: BrainStore) -> dict[str, object]:
    evidence_content = b"Locally verified external council evidence.\n"
    evidence_path = store.root / "fixtures" / "import-evidence.txt"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_bytes(evidence_content)
    roles = {}
    for role in ("positive", "negative", "evaluation", "chief"):
        payload = role_payload(role)
        payload["model_id"] = f"external-model-{role}"
        payload["model_tier"] = "external"
        roles[role] = payload
    transcript = [
        {
            "iteration": 1,
            "role": role,
            "phase": {
                "positive": "independent_proposal",
                "negative": "independent_failure_analysis",
                "evaluation": "evaluate",
                "chief": "chief_gate",
            }[role],
            "status": "succeeded",
            "recorded_at": "2026-08-26T12:00:00+00:00",
            "result": payload,
        }
        for role, payload in roles.items()
    ]
    return {
        "format": "my-or-your-brain-run-v2",
        "run_id": run_id,
        "goal": "Record a provider-neutral council outcome",
        "risk": "low",
        "acceptance_criteria": [
            {"id": "criterion-1", "text": "The structured result is locally evidenced."}
        ],
        "evidence": [
            {
                "id": "evidence-1",
                "source": "fixtures/import-evidence.txt",
                "sha256": hashlib.sha256(evidence_content).hexdigest(),
                "quality": 1.0,
                "verified": True,
                "description": "Deterministic local import evidence.",
            }
        ],
        "status": "accepted",
        "iterations": 1,
        "readiness": {
            "total": 1.0,
            "threshold": 0.85,
            "calibrated": False,
            "components": {},
            "penalties": {},
            "hard_gates": [],
        },
        "final_summary": "The structured outcome passed its deterministic checks.",
        "roles": roles,
        "missing_capabilities": {
            "skills": [],
            "connections": [],
            "knowledge": [],
            "optional": [],
        },
        "next_eligible_at": None,
        "transcript": transcript,
        "observations": [],
        "created_at": "2026-08-26T12:00:00+00:00",
    }


class BrainStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.base = Path(self.temporary_directory.name)
        self.root = self.base / "brain"
        self.store = BrainStore(self.root)
        self.store.initialize()

    def test_public_private_capture_search_and_index_boundaries(self) -> None:
        public = self.store.capture(
            "Orion architecture",
            "A shared design note about the council protocol.",
            scope="public",
            tags=["shared", "architecture"],
        )
        private = self.store.capture(
            "Nebula preference",
            "A private preference about the council protocol.",
            scope="private",
            tags=["private", "preference"],
        )

        self.assertEqual(self.store.public, public.path.parent)
        self.assertEqual(self.store.private, private.path.parent)
        self.assertEqual([public.id], [item["id"] for item in self.store.search("council")])
        self.assertEqual([], self.store.search("nebula"))
        self.assertCountEqual(
            [public.id, private.id],
            [item["id"] for item in self.store.search("council", include_private=True)],
        )

        public_index = self.store.build_index()
        self.assertFalse(public_index["includes_private"])
        self.assertEqual({public.id}, set(public_index["documents"]))
        self.assertNotIn("nebula", public_index["terms"])

        complete_index = self.store.build_index(include_private=True)
        self.assertTrue(complete_index["includes_private"])
        self.assertEqual({public.id, private.id}, set(complete_index["documents"]))
        self.assertEqual([private.id], complete_index["terms"]["nebula"])
        self.assertFalse(self.store.validate()["database_required"])

    def test_capture_and_public_export_reject_secret_patterns(self) -> None:
        synthetic_token = "g" + "hp_" + ("A" * 24)
        with self.assertRaisesRegex(BrainError, "possible secret"):
            self.store.capture("Credential", synthetic_token, scope="private")
        self.assertEqual([], self.store.notes(include_private=True))

        (self.root / "README.md").write_text(
            f"Accidentally committed credential: {synthetic_token}\n",
            encoding="utf-8",
        )
        destination = self.base / "secret-export"
        with self.assertRaisesRegex(BrainError, "secret scan failed"):
            self.store.export_public(destination)
        self.assertFalse(destination.exists())

    def test_public_scope_mismatch_and_non_utf8_content_fail_closed(self) -> None:
        note = self.store.capture("Public boundary", "Shareable.", scope="public")
        original = note.path.read_text(encoding="utf-8")
        note.path.write_text(original.replace('scope: "public"', 'scope: "private"'), encoding="utf-8")

        with self.assertRaisesRegex(BrainError, "does not match"):
            self.store.validate()

        note.path.write_text(original, encoding="utf-8")
        binary = self.root / "docs" / "binary.dat"
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(b"\xff\xfe\x00\x01")
        destination = self.base / "binary-export"
        with self.assertRaisesRegex(BrainError, "non-utf8"):
            self.store.export_public(destination)
        self.assertFalse(destination.exists())

    def test_public_memory_symlink_is_rejected_when_supported(self) -> None:
        private = self.store.capture("Private target", "Owner-only text.", scope="private")
        link = self.store.public / "linked-private.md"
        try:
            link.symlink_to(private.path)
        except OSError as exc:
            self.skipTest(f"symlink creation is unavailable: {exc}")

        with self.assertRaisesRegex(BrainError, "symlink or reparse"):
            self.store.notes()

    def test_public_export_is_allowlist_only_and_excludes_git_and_private_data(self) -> None:
        public = self.store.capture("Public note", "Shareable knowledge.", scope="public")
        private = self.store.capture("Private note", "Owner-only knowledge.", scope="private")
        (self.root / "README.md").write_text("# Public repository\n", encoding="utf-8")
        (self.root / ".gitignore").write_text(".local/\n", encoding="utf-8")
        (self.root / "docs").mkdir()
        (self.root / "docs" / "guide.md").write_text("Public guide.\n", encoding="utf-8")
        (self.root / ".git").mkdir()
        (self.root / ".git" / "config").write_text("private git metadata\n", encoding="utf-8")
        (self.root / "not-allowlisted.txt").write_text("not public\n", encoding="utf-8")

        destination = self.base / "public-export"
        manifest = self.store.export_public(destination)
        exported = {record["path"] for record in manifest["files"]}

        self.assertIn("README.md", exported)
        self.assertIn(".gitignore", exported)
        self.assertIn("docs/guide.md", exported)
        self.assertIn(public.path.relative_to(self.store.root).as_posix(), exported)
        self.assertNotIn(private.path.relative_to(self.store.root).as_posix(), exported)
        self.assertNotIn("not-allowlisted.txt", exported)
        self.assertFalse(any(path == ".git" or path.startswith(".git/") for path in exported))
        self.assertFalse(any(path == ".local" or path.startswith(".local/") for path in exported))
        self.assertFalse(manifest["source_history_included"])
        stored_manifest = json.loads((destination / "EXPORT-MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest, stored_manifest)

    def test_reset_archives_checkpoints_without_deleting_memory(self) -> None:
        public = self.store.capture("Durable public", "Keep this public memory.", scope="public")
        private = self.store.capture("Durable private", "Keep this private memory.", scope="private")
        active = self.store.save_run("run-to-reset", {"status": "deferred", "goal": "Retry later"})

        archived = self.store.reset("run-to-reset")

        self.assertEqual(1, len(archived))
        self.assertFalse(active.exists())
        self.assertTrue(archived[0].exists())
        self.assertEqual("deferred", self.store.load_run("run-to-reset")["status"])
        self.assertTrue(public.path.exists())
        self.assertTrue(private.path.exists())
        self.assertCountEqual(
            [public.id, private.id],
            [note.id for note in self.store.notes(include_private=True)],
        )

    def test_promotion_requires_an_accepted_outcome(self) -> None:
        self.store.save_run(
            "rejected-run",
            {"status": "rejected", "goal": "Unsafe change", "final_summary": "Do not promote."},
        )
        self.store.approve_run(
            "rejected-run",
            approved_by="owner",
            scope="promote",
            confirmed=True,
        )
        with self.assertRaisesRegex(BrainError, "only an accepted or solely human-gated"):
            self.store.promote("rejected-run", approved_by="owner")

        self.store.save_run(
            "accepted-run",
            {
                "status": "accepted",
                "goal": "Verified change",
                "final_summary": "All deterministic acceptance criteria passed.",
                "readiness": {"total": 1.0, "calibrated": False},
            },
        )
        with self.assertRaisesRegex(BrainError, "valid local approval not found"):
            self.store.promote("accepted-run", approved_by="owner")
        self.store.approve_run(
            "accepted-run",
            approved_by="owner",
            scope="promote",
            confirmed=True,
        )
        promoted = self.store.promote("accepted-run", approved_by="owner")

        self.assertTrue(promoted.exists())
        content = promoted.read_text(encoding="utf-8")
        self.assertIn("Verified change", content)
        self.assertIn("accepted-run", content)
        self.assertEqual(1, len(list((self.root / "evolution").glob("*.md"))))

    def test_approval_is_invalid_after_run_tampering(self) -> None:
        run = {
            "status": "accepted",
            "goal": "Original goal",
            "final_summary": "Verified result.",
            "readiness": {"total": 1.0, "threshold": 0.9, "calibrated": False, "hard_gates": []},
        }
        self.store.save_run("tamper-check", run)
        self.store.approve_run(
            "tamper-check", approved_by="owner", scope="promote", confirmed=True
        )
        run["final_summary"] = "Changed after approval."
        self.store.save_run("tamper-check", run)

        with self.assertRaisesRegex(BrainError, "does not match"):
            self.store.promote("tamper-check", approved_by="owner")

    def test_record_council_and_append_hash_bound_observation(self) -> None:
        payload = recorded_council_payload("external-council", self.store)
        path = self.store.record_council(payload)
        evidence = self.root / "fixtures" / "observed-result.txt"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text("deterministic result: pass\n", encoding="utf-8")

        observation = self.store.record_observation(
            "external-council",
            status="succeeded",
            evidence_path=evidence,
            note="The approved implementation passed the local deterministic check.",
        )

        self.assertTrue(path.exists())
        self.assertEqual("succeeded", observation["status"])
        self.assertEqual("fixtures/observed-result.txt", observation["evidence"]["source"])
        self.assertEqual(64, len(observation["evidence"]["sha256"]))
        stored = self.store.load_run("external-council")
        self.assertEqual(4, len(stored["transcript"]))
        self.assertEqual([observation], stored["observations"])
        with self.assertRaisesRegex(BrainError, "already exists"):
            self.store.record_council(payload)

    def test_record_council_rejects_hidden_reasoning_fields(self) -> None:
        payload = recorded_council_payload("hidden-reasoning", self.store)
        payload["transcript"][0]["result"]["chain_of_thought"] = "must not be stored"

        with self.assertRaisesRegex(BrainError, "unknown|hidden-reasoning"):
            self.store.record_council(payload)

    def test_record_council_rejects_incoherent_or_high_risk_accepted_runs(self) -> None:
        incomplete = recorded_council_payload("incomplete-council", self.store)
        incomplete["roles"] = {}
        incomplete["transcript"] = []
        with self.assertRaisesRegex(BrainError, "exactly all four"):
            self.store.record_council(incomplete)

        high_risk = recorded_council_payload("high-risk-bypass", self.store)
        high_risk["risk"] = "high"
        high_risk["readiness"]["threshold"] = 0.95
        with self.assertRaisesRegex(BrainError, "human-approval gate|cannot be accepted"):
            self.store.record_council(high_risk)

    def test_strict_import_schema_declares_runtime_safety_boundaries(self) -> None:
        outcome_schema = json.loads(
            (REPOSITORY_ROOT / "schemas" / "council-outcome.schema.json").read_text(encoding="utf-8")
        )
        import_schema = json.loads(
            (REPOSITORY_ROOT / "schemas" / "recorded-council-v2.schema.json").read_text(encoding="utf-8")
        )

        self.assertFalse(outcome_schema["additionalProperties"])
        strict = import_schema["allOf"][1]
        self.assertIn("observations", strict["required"])
        self.assertCountEqual(
            ["positive", "negative", "evaluation", "chief"],
            strict["properties"]["roles"]["required"],
        )
        high_risk_gate = import_schema["allOf"][2]["then"]["properties"]["readiness"]["properties"]["hard_gates"]["contains"]["const"]
        self.assertEqual(
            "high-risk outcome requires a separate recorded human approval",
            high_risk_gate,
        )
        self.assertFalse(import_schema["unevaluatedProperties"])

    def test_observation_requires_a_terminal_run(self) -> None:
        self.store.save_run("running-council", {"status": "running", "observations": []})
        evidence = self.root / "fixtures" / "running.txt"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text("not terminal\n", encoding="utf-8")

        with self.assertRaisesRegex(BrainError, "only after.*terminal"):
            self.store.record_observation(
                "running-council",
                status="mixed",
                evidence_path=evidence,
                note="This outcome is not ready for observation.",
            )

    def test_observation_invalidates_prior_run_hash_approval(self) -> None:
        self.store.record_council(recorded_council_payload("observed-approval", self.store))
        evidence = self.root / "fixtures" / "post-approval.txt"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text("post-approval observation\n", encoding="utf-8")
        self.store.approve_run(
            "observed-approval", approved_by="owner", scope="promote", confirmed=True
        )

        self.store.record_observation(
            "observed-approval",
            status="mixed",
            evidence_path=evidence,
            note="A new observation changes the immutable run content and requires re-approval.",
        )

        with self.assertRaisesRegex(BrainError, "does not match"):
            self.store.verify_approval("observed-approval", scope="promote")


if __name__ == "__main__":
    unittest.main()
