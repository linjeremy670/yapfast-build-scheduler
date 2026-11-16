"""Regression tests for Windows release monitoring and fallback dispatch decisions."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from scripts import builder_health as health
from scripts import yapfast_fallback as fallback

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
SHA = "a" * 40
OLD_SHA = "b" * 40


def windows_release(sha=SHA, exe_size=100):
    return {
        "tag_name": f"windows-{sha}-123-1",
        "draft": False,
        "assets": [
            {"name": "SHA256SUMS", "state": "uploaded", "size": 91},
            {"name": "yapfast-windows-x64.exe", "state": "uploaded", "size": exe_size},
        ],
    }


def run(minutes_ago=5, status="completed", conclusion="success"):
    return {
        "id": 123,
        "name": "Yapfast Windows Scheduler",
        "path": ".github/workflows/windows.yml",
        "event": "schedule",
        "status": status,
        "conclusion": conclusion,
        "created_at": (NOW - timedelta(minutes=minutes_ago)).isoformat(),
        "html_url": "https://github.com/example/actions/runs/123",
    }


def health_snapshot(commit_minutes_ago=120, releases=None, runs=None):
    releases = [windows_release()] if releases is None else releases
    return {
        "now": NOW,
        "upstream": {
            "sha": SHA,
            "commit": {"committer": {
                "date": (NOW - timedelta(minutes=commit_minutes_ago)).isoformat()
            }},
        },
        "main": SHA,
        "releases": releases,
        "scheduler": [run()] if runs is None else runs,
        "yapfast": [],
    }


class BuilderHealthTests(unittest.TestCase):
    def test_complete_release_is_healthy(self):
        self.assertEqual(health.check_problems(health_snapshot()), [])

    def test_missing_release_after_one_hour_alerts(self):
        issues = health.check_problems(health_snapshot(releases=[]))
        self.assertTrue(any("No complete Windows release" in issue for issue in issues))

    def test_missing_release_before_one_hour_does_not_alert(self):
        issues = health.check_problems(
            health_snapshot(commit_minutes_ago=30, releases=[])
        )
        self.assertEqual(issues, [])

    def test_stale_scheduler_alerts(self):
        issues = health.check_problems(health_snapshot(runs=[run(41)]))
        self.assertTrue(any("No Windows scheduler" in issue for issue in issues))

    def test_failed_latest_scheduler_alerts(self):
        issues = health.check_problems(
            health_snapshot(runs=[run(conclusion="failure")])
        )
        self.assertTrue(any("Latest scheduler check failed" in i for i in issues))

    def test_fork_not_synced_alerts_after_hour(self):
        snapshot = health_snapshot()
        snapshot["main"] = OLD_SHA
        self.assertTrue(any("behind upstream" in i
                            for i in health.check_problems(snapshot)))


class FallbackTests(unittest.TestCase):
    def test_complete_release_requires_real_uploaded_assets(self):
        self.assertTrue(fallback.complete_release_exists([windows_release()], SHA))
        self.assertFalse(
            fallback.complete_release_exists([windows_release(exe_size=0)], SHA)
        )
        self.assertFalse(fallback.complete_release_exists([windows_release()], OLD_SHA))

    def test_recent_check_prevents_dispatch(self):
        self.assertFalse(fallback.should_dispatch(NOW, True, [run(5)], None))

    def test_missing_build_triggers_after_twelve_minutes(self):
        self.assertTrue(fallback.should_dispatch(NOW, True, [run(13)], None))

    def test_idle_schedule_triggers_after_twenty_minutes(self):
        self.assertTrue(fallback.should_dispatch(NOW, False, [run(21)], None))

    def test_active_build_prevents_duplicate(self):
        self.assertFalse(fallback.should_dispatch(
            NOW, True, [run(30, status="in_progress")], None
        ))

    def test_dispatch_cooldown_prevents_duplicates(self):
        self.assertFalse(fallback.should_dispatch(
            NOW, True, [run(45)], NOW - timedelta(minutes=10)
        ))


if __name__ == "__main__":
    unittest.main()
