import re
import unittest
from pathlib import Path


SYSTEMD_DIR = Path(__file__).parents[1] / "systemd"
MAC_RUNNER = "/Users/macmini/.local/bin/janitor-runner"


class TestSystemdUnits(unittest.TestCase):
    def test_user_services_run_as_the_user_manager_and_use_installed_runner(self):
        for filename in ("janitor-sweep.service", "janitor-overview.service"):
            unit = (SYSTEMD_DIR / filename).read_text(encoding="utf-8")
            self.assertIsNone(
                re.search(r"(?m)^User=", unit),
                msg=f"{filename} must not change identity in a user service",
            )
            self.assertIn(MAC_RUNNER, unit)
            self.assertNotIn("/usr/local/bin/janitor-runner", unit)


if __name__ == "__main__":
    unittest.main()
