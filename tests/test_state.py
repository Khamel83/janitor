import json
import tempfile
import time
import unittest
from pathlib import Path

from janitor.state import StateManager


class TestStateManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.temp_dir.name)
        self.sm = StateManager(self.state_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    def state_file(self):
        return self.state_dir / "state.json"

    def test_task_id_stability(self):
        id1 = self.sm.get_or_create_task_id("maya", "Deploy Postgres schema")
        id2 = self.sm.get_or_create_task_id("maya", "Deploy Postgres schema")
        self.assertEqual(id1, id2)
        self.assertTrue(id1.startswith("tk_"))

    def test_task_id_stable_across_restarts(self):
        first = self.sm.get_or_create_task_id("maya", "Deploy Postgres schema")
        reloaded = StateManager(self.state_dir)
        self.assertEqual(
            reloaded.get_or_create_task_id("maya", "Deploy Postgres schema"), first
        )

    def test_task_id_normalizes_case_and_whitespace(self):
        id1 = self.sm.get_or_create_task_id("maya", "  Deploy   Postgres SCHEMA\n")
        id2 = self.sm.get_or_create_task_id("maya", "deploy postgres schema")
        self.assertEqual(id1, id2)

    def test_input_hash_tracking(self):
        self.assertIsNone(self.sm.get_last_input_hash("maya"))
        self.sm.set_last_input_hash("maya", "hash_abc123")
        self.assertEqual(self.sm.get_last_input_hash("maya"), "hash_abc123")

    def test_input_hash_is_per_repo(self):
        self.sm.set_last_input_hash("maya", "hash_abc123")
        self.assertIsNone(self.sm.get_last_input_hash("homelab"))

    def test_input_hash_persists_across_restarts(self):
        self.sm.set_last_input_hash("maya", "hash_abc123")
        reloaded = StateManager(self.state_dir)
        self.assertEqual(reloaded.get_last_input_hash("maya"), "hash_abc123")

    def test_record_run_stores_status_run_id_timestamp(self):
        before = int(time.time())
        self.sm.record_run("maya", "committed", "20260908-0300")
        after = int(time.time())
        last_run = json.loads(self.state_file().read_text(encoding="utf-8"))["maya"][
            "last_run"
        ]
        self.assertEqual(last_run["status"], "committed")
        self.assertEqual(last_run["run_id"], "20260908-0300")
        self.assertIsInstance(last_run["ts"], int)
        self.assertGreaterEqual(last_run["ts"], before)
        self.assertLessEqual(last_run["ts"], after)

    def test_record_run_overwrites_previous_run(self):
        self.sm.record_run("maya", "committed", "run_1")
        self.sm.record_run("maya", "skipped", "run_2")
        last_run = json.loads(self.state_file().read_text(encoding="utf-8"))["maya"][
            "last_run"
        ]
        self.assertEqual(last_run["status"], "skipped")
        self.assertEqual(last_run["run_id"], "run_2")

    def test_wip_branch_expiry_filters_by_max_age_days(self):
        # now = 2_000_000; cutoff for 1 day = 2_000_000 - 86_400 = 1_913_600
        self.sm.track_wip_branch(
            "maya", "auto-wip/20260101-0300", "sha123", timestamp=1_000_000
        )
        self.sm.track_wip_branch(
            "maya", "auto-wip/20260102-0300", "sha456", timestamp=1_950_000
        )
        expired = self.sm.get_expired_wip_branches(
            "maya", max_age_days=1, current_timestamp=2_000_000
        )
        self.assertIn("auto-wip/20260101-0300", expired)
        self.assertNotIn("auto-wip/20260102-0300", expired)

    def test_wip_branch_records_sha_and_defaults_timestamp(self):
        before = int(time.time())
        self.sm.track_wip_branch("maya", "auto-wip/20260908-0300", "sha456")
        after = int(time.time())
        branch = json.loads(self.state_file().read_text(encoding="utf-8"))["maya"][
            "wip_branches"
        ][0]
        self.assertEqual(branch["branch"], "auto-wip/20260908-0300")
        self.assertEqual(branch["sha"], "sha456")
        self.assertGreaterEqual(branch["created_at"], before)
        self.assertLessEqual(branch["created_at"], after)

    def test_expired_wip_branches_unknown_repo_is_empty(self):
        self.assertEqual(
            self.sm.get_expired_wip_branches(
                "missing", max_age_days=1, current_timestamp=2_000_000
            ),
            [],
        )

    def test_branch_observation_records_compact_continuity(self):
        observed_at = 1_800_000_000
        rows = [
            {
                "name": "main",
                "tip": {"sha": "sha-main"},
                "classification": "active",
            }
        ]

        self.sm.record_branch_observation("maya", rows, "report-1", observed_at)

        self.assertEqual(
            self.sm.get_branch_review("maya"),
            {
                "last_report_hash": "report-1",
                "last_semantic_change_date": "2027-01-15",
                "branches": {
                    "main": {
                        "first_seen": observed_at,
                        "last_seen": observed_at,
                        "last_sha": "sha-main",
                        "classification": "active",
                        "present": True,
                        "missing_since": None,
                    }
                },
            },
        )

    def test_branch_observation_preserves_first_seen_and_updates_current_fields(self):
        first_seen = 1_800_000_000
        self.sm.record_branch_observation(
            "maya",
            [{"name": "feature", "tip": {"sha": "sha-1"}, "classification": "aging"}],
            "report-1",
            first_seen,
        )

        last_seen = first_seen + 86400
        self.sm.record_branch_observation(
            "maya",
            [{"name": "feature", "tip": {"sha": "sha-2"}, "classification": "stale"}],
            "report-2",
            last_seen,
        )

        branch = self.sm.get_branch_review("maya")["branches"]["feature"]
        self.assertEqual(branch["first_seen"], first_seen)
        self.assertEqual(branch["last_seen"], last_seen)
        self.assertEqual(branch["last_sha"], "sha-2")
        self.assertEqual(branch["classification"], "stale")
        self.assertTrue(branch["present"])
        self.assertIsNone(branch["missing_since"])
        self.assertEqual(
            self.sm.get_branch_review("maya")["last_semantic_change_date"],
            "2027-01-16",
        )

    def test_branch_observation_keeps_missing_branch_tombstone_until_after_90_days(self):
        first_seen = 1_800_000_000
        self.sm.record_branch_observation(
            "maya",
            [{"name": "feature", "tip": {"sha": "sha-1"}, "classification": "active"}],
            "report-1",
            first_seen,
        )
        missing_since = first_seen + 86400
        self.sm.record_branch_observation("maya", [], "report-2", missing_since)

        tombstone = self.sm.get_branch_review("maya")["branches"]["feature"]
        self.assertFalse(tombstone["present"])
        self.assertEqual(tombstone["missing_since"], missing_since)
        self.assertEqual(tombstone["last_seen"], first_seen)

        at_expiry = missing_since + (90 * 86400)
        self.sm.record_branch_observation("maya", [], "report-2", at_expiry)
        self.assertIn("feature", self.sm.get_branch_review("maya")["branches"])

        after_expiry = at_expiry + 1
        self.sm.record_branch_observation("maya", [], "report-2", after_expiry)
        self.assertNotIn("feature", self.sm.get_branch_review("maya")["branches"])

    def test_branch_observation_persists_across_restarts(self):
        self.sm.record_branch_observation(
            "maya",
            [{"name": "main", "tip": {"sha": "sha-main"}, "classification": "active"}],
            "report-1",
            1_800_000_000,
        )

        reloaded = StateManager(self.state_dir)

        self.assertEqual(reloaded.get_branch_review("maya"), self.sm.get_branch_review("maya"))

    def test_state_dir_created_recursively(self):
        # Default state dir (~/.local/state/janitor) may not exist yet, so a
        # nested path must be created on construction.
        nested = self.state_dir / "nested" / "dir"
        sm2 = StateManager(nested)
        self.assertTrue(nested.is_dir())
        sm2.set_last_input_hash("repo_a", "h1")
        reloaded = StateManager(nested)
        self.assertEqual(reloaded.get_last_input_hash("repo_a"), "h1")


if __name__ == "__main__":
    unittest.main()
