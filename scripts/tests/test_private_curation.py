"""Regression tests for accidental public annotation commits."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_private_curation import private_curation_paths


class PrivateCurationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("commit", "-q", "--allow-empty", "-m", "Initial")

    def git(self, *arguments):
        return subprocess.check_output(["git", "-C", str(self.root), *arguments])

    def stage(self, path):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("synthetic fixture\n", encoding="utf-8")
        self.git("add", "--", path)

    def test_public_documentation_code_and_demo_fixtures_are_allowed(self):
        for path in (
            "imagery_model/annotations/README.md",
            "imagery_model/src/training.py",
            "reviewer/fixtures/tiles/demo.json",
        ):
            self.stage(path)
        self.git("commit", "-qm", "Public files")
        self.assertEqual(private_curation_paths(self.root), set())

    def test_staged_payloads_and_generated_review_data_are_rejected(self):
        paths = {
            "imagery_model/annotations/city/scene with spaces/reviews.json",
            "imagery_model/artifacts/reviews/state.json",
            "reviewer/tiles/live.json",
        }
        for path in paths:
            self.stage(path)
        self.assertEqual(private_curation_paths(self.root), paths)

    def test_deleted_payload_in_history_is_still_rejected(self):
        path = "imagery_model/annotations/city/reviews.json"
        self.stage(path)
        self.git("commit", "-qm", "Accidental payload")
        self.git("rm", "--", path)
        self.git("commit", "-qm", "Remove current payload")
        self.assertEqual(private_curation_paths(self.root), {path})

    def test_payload_in_separate_branch_is_outside_checked_history(self):
        self.git("branch", "private-backup")
        path = "imagery_model/annotations/city/reviews.json"
        self.stage(path)
        self.git("commit", "-qm", "Private branch payload")
        self.git("checkout", "-q", "private-backup")
        self.assertEqual(private_curation_paths(self.root), set())

    def test_renamed_then_deleted_payload_is_rejected(self):
        path = "imagery_model/annotations/city/reviews.json"
        self.stage("fixture.json")
        self.git("commit", "-qm", "Fixture")
        (self.root / path).parent.mkdir(parents=True)
        self.git("mv", "fixture.json", path)
        self.git("commit", "-qm", "Move into private directory")
        self.git("rm", "--", path)
        self.git("commit", "-qm", "Delete payload")
        self.assertEqual(private_curation_paths(self.root), {path})


if __name__ == "__main__":
    unittest.main()
