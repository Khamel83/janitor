import re
import unittest
from pathlib import Path


SYSTEMD_DIR = Path(__file__).parents[1] / "systemd"
MAC_RUNNER = "/Users/macmini/.local/bin/janitor-runner"


class TestSystemdUnits(unittest.TestCase):
    def test_user_services_run_as_the_user_manager_and_use_installed_runner(self):
        for filename in ("janitor-sweep.service", "janitor-overview.service",
                         "janitor-publish.service", "janitor-reviews.service"):
            unit = (SYSTEMD_DIR / filename).read_text(encoding="utf-8")
            self.assertIsNone(
                re.search(r"(?m)^User=", unit),
                msg=f"{filename} must not change identity in a user service",
            )
            self.assertIn(MAC_RUNNER, unit)
            self.assertNotIn("/usr/local/bin/janitor-runner", unit)

    def test_pr_units_are_bounded_and_keep_pacific_cadence(self):
        for name, clock, command in (
            ("publish", "03:30:00", "publish --all --limit 20"),
            ("reviews", "06:00:00", "reviews --all"),
        ):
            service = (SYSTEMD_DIR / f"janitor-{name}.service").read_text()
            timer = (SYSTEMD_DIR / f"janitor-{name}.timer").read_text()
            self.assertIn(f"OnCalendar=*-*-* {clock} America/Los_Angeles", timer)
            self.assertIn("Persistent=true", timer)
            self.assertIn(command, service)
            self.assertIn("TimeoutStartSec=55min", service)
            self.assertIn("StartLimitBurst=3", service)
            self.assertIn("RestartSec=10min", service)
            self.assertNotIn("--json", service)


if __name__ == "__main__":
    unittest.main()
