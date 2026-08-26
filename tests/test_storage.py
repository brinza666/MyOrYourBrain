from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests._support import BrainStore
from myoryourbrain.types import BrainError


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


if __name__ == "__main__":
    unittest.main()
