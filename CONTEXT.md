<!-- janitor:begin:recent -->
Active focus is on final acceptance verification of the Janitor repository caretaker system across local and fleet targets. Core module implementation, CLI suite, systemd units, and git operations are complete.

- Fixed `janitor/git_ops.py` to use absolute ISO-8601 `%cI` timestamps for deterministic input hashing (`3295676`).
- Added root `.gitignore` to un-track compiled bytecode and egg-info build artifacts (`ec95539`).
- Added homelab systemd timers/services and Mac Mini SSH runner script (`a04c03a`).
- Implemented `janitor/cli.py` with fleet discovery, workspace auto-tidy, document sweeps, and structured JSON output (`b77d640`).
- Added sentinel-bounded context reconciler (`janitor/reconciler.py`) with first-run bootstrap and SHA256 hash gating (`b234fd7`).
- Updated model worker (`janitor/worker.py`) to stream payloads over stdin to prevent ARG_MAX limit failures (`8d52783`).
- Implemented butler auto-tidy trash purge and zero-data-loss WIP branch checkpointing in `janitor/hygiene.py` (`aef3665`).
- Created persistent state layer and stable task ID manager in `janitor/state.py` (`3df61ef`).
- Added `janitor/git_ops.py` with preflight guards, atomic staging/committing, and `Janitor-Run` trailer loop prevention (`4c205c2`).
- Authored caretaker design spec, adversarial review hardening, and implementation plan (`8058a4b`, `226b981`, `b296050`, `36a13d1`).

Working tree is currently clean.
<!-- janitor:end:recent -->
