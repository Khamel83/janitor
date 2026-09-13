"""Offline checks for unattended CLI lifecycle boundaries."""
import fcntl
import tempfile
from pathlib import Path
from unittest.mock import patch

from janitor.cli import main


def test_mutating_runs_do_not_overlap():
    with tempfile.TemporaryDirectory() as tmp:
        with (Path(tmp) / "run.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.dict("os.environ", {"JANITOR_STATE_DIR": tmp}), patch("janitor.cli.sweep_repo", return_value={"repo": "fixture", "status": "quiet"}) as sweep:
                assert main(["sweep", tmp]) == 1
                sweep.assert_not_called()
