# Janitor Gateway2000 Auto-Only Routing Implementation Plan

> For agentic workers: use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Make every live Janitor model request use Gateway2000 auto and never select g2k-bg, then deploy and rerun the missed fleet sweep.

**Status, 2026-09-12 regroup:** Tasks 1–3 and Task 4 steps 1–3 were completed
according to HANDOFF.md (commits 79c45ba, 8c0d8ad, ca861d8; 161 tests and a
bounded auto probe). Task 4 fleet acceptance failed/interrupted. Historical
step checkboxes below are retained as the original procedure, not open work.
The canonical remaining checklist is now root TODO.md. Do not repeat the
red-test setup or launch another fleet sweep before the bounded diagnosis gate.

**Architecture:** The Mac mini worker invokes the g2k shell function from the sourced Gateway2000 helper through zsh -lc, streaming the full prompt through stdin. The existing OpenRouter free HTTP path remains only for machines without a Gateway2000 auto helper.

**Tech Stack:** Python 3.10+, subprocess, pathlib, zsh, Gateway2000 OMP helper, unittest/pytest, Ruff, systemd user services over SSH.

## Global Constraints

- Janitor uses Gateway2000 auto for sweeps and overviews.
- Janitor never searches for or invokes g2k-bg.
- Prompts remain streamed through stdin, not embedded in argv.
- OpenRouter is used only when no Gateway2000 auto helper is present.
- Provider failures remain visible as synthesis_failed.
- Do not alter Gateway2000 credentials, provider configuration, or global launcher installation.
- Preserve the existing dirty CONTEXT.md and tests/test_git_ops.py files.

---

### Task 1: Add failing auto-lane worker tests

**Files:**
- Modify: tests/test_worker.py

**Interfaces:**
- Consumes the current call_free and subprocess.run contracts.
- Produces _gateway_command() -> tuple[list[str], str] | None and auto-lane streaming coverage.

- [ ] Step 1: Replace the background fixture

Replace the old GATEWAY constant with:

~~~python
AUTO_COMMAND = [
    "/bin/zsh",
    "-lc",
    'source "$HOME/.config/gateway2000/gateway2000.zsh" && g2k -p -',
]
AUTO_LABEL = "gateway2000/auto"
~~~

Update _completed, timeout commands, subprocess assertions, and usage assertions to use AUTO_COMMAND and AUTO_LABEL. Patch janitor.worker._gateway_command with return value (AUTO_COMMAND, AUTO_LABEL).

- [ ] Step 2: Add the selector regression test

Import janitor.worker and add:

~~~python
    def test_gateway_command_sources_auto_helper_without_background_lookup(self):
        helper = self.tmp / "gateway2000.zsh"
        helper.write_text("g2k() { :; }\\n")
        looked_up = []

        def which(name):
            looked_up.append(name)
            return "/bin/zsh" if name == "zsh" else None

        with (
            patch.object(worker, "GATEWAY_HELPER", helper),
            patch.object(worker.shutil, "which", side_effect=which),
        ):
            command = worker._gateway_command()

        self.assertEqual(command, (AUTO_COMMAND, AUTO_LABEL))
        self.assertNotIn("g2k-bg", looked_up)
        self.assertNotIn("g2k-bg", command[0][2])
~~~

- [ ] Step 3: Run the focused tests and confirm red

Run:

~~~bash
PYTHONPATH=. pytest -q tests/test_worker.py
~~~

Expected: FAIL because _gateway_command does not yet exist and the tests still target the old selector.

---

### Task 2: Implement explicit Gateway2000 auto invocation

**Files:**
- Modify: janitor/worker.py
- Test: tests/test_worker.py

**Interfaces:**
- Consumes ~/.config/gateway2000/gateway2000.zsh and zsh on PATH.
- Produces _gateway_command() -> tuple[list[str], str] | None and an auto-only call_free dispatch.

- [ ] Step 1: Add the auto helper constants

Add after _cached_api_key:

~~~python
GATEWAY_HELPER = Path.home() / ".config" / "gateway2000" / "gateway2000.zsh"
AUTO_GATEWAY_SCRIPT = (
    'source "$HOME/.config/gateway2000/gateway2000.zsh" '
    '&& g2k -p -'
)
~~~

Update the module docstring to say Gateway2000 auto is preferred and g2k-bg is never selected.

- [ ] Step 2: Implement the selector

Replace _gateway_cli with:

~~~python
def _gateway_command() -> tuple[list[str], str] | None:
    """Return the explicit Gateway2000 auto command, if installed."""
    zsh = shutil.which("zsh")
    if not zsh or not GATEWAY_HELPER.is_file():
        return None
    return ([zsh, "-lc", AUTO_GATEWAY_SCRIPT], "gateway2000/auto")
~~~

This selector must not call shutil.which("g2k-bg") or execute the misleading PATH wrapper. Gateway-less environments continue to use the existing OpenRouter path.

- [ ] Step 3: Make the gateway call accept argv and a label

Change _call_gateway to:

~~~python
def _call_gateway(
    command: list[str], label: str, prompt: str, system: str | None, timeout: int
) -> str:
    """Call Gateway2000 auto with the payload streamed over stdin."""
    full_prompt = f"{system}\\n\\n{prompt}" if system else prompt
    try:
        res = subprocess.run(
            command,
            input=full_prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{label} timed out after {timeout}s")
    if res.returncode != 0:
        raise RuntimeError(
            f"{label} failed (exit {res.returncode}): {res.stderr.strip()[:200]}"
        )

    raw = res.stdout.strip()
    raw = re.sub(r"^```(?:json|markdown)?\\s*", "", raw)
    raw = re.sub(r"\\s*```$", "", raw)
    _log_usage(label)
    return raw.strip()
~~~

- [ ] Step 4: Update call_free dispatch

Replace the existing gateway block with:

~~~python
    gateway = _gateway_command()
    if gateway:
        command, label = gateway
        return _call_gateway(command, label, prompt, system, timeout)
~~~

- [ ] Step 5: Run the focused worker suite

~~~bash
PYTHONPATH=. pytest -q tests/test_worker.py
~~~

Expected: all worker tests pass and the subprocess argv contains zsh, the sourced helper, and g2k -p -.

- [ ] Step 6: Commit the backend change

~~~bash
git add janitor/worker.py tests/test_worker.py
git commit -m "fix: route Janitor model calls through Gateway2000 auto"
~~~

---

### Task 3: Align active documentation

**Files:**
- Modify: README.md
- Modify: LLM-OVERVIEW.md
- Modify: janitor/reconciler.py
- Modify: tests/test_worker.py

**Interfaces:**
- Consumes the implemented auto-only worker policy.
- Produces truthful operator and agent documentation.

- [ ] Step 1: Update the README topology

Replace the inference line with:

~~~text
│  - Inference: Gateway2000 auto via sourced g2k + stdin │
~~~

Keep the OpenRouter fallback note, but state that it applies only without the Gateway2000 auto helper.

- [ ] Step 2: Update the current overview and reconciler contract

In LLM-OVERVIEW.md, describe the model gateway as the sourced Gateway2000 g2k auto function. In janitor/reconciler.py, change the module contract from g2k-bg/g2k when present to Gateway2000 auto when the helper is present.

- [ ] Step 3: Update the worker test description

Make the no-backend test description refer to the Gateway2000 auto helper rather than naming g2k-bg.

- [ ] Step 4: Check active references

~~~bash
rg -n "g2k-bg|background lane|background route" README.md LLM-OVERVIEW.md janitor tests
~~~

Expected: no active Janitor execution path or current overview claims g2k-bg. Historical design and plan records may retain original wording.

- [ ] Step 5: Commit the documentation change

~~~bash
git add README.md LLM-OVERVIEW.md janitor/reconciler.py tests/test_worker.py
git commit -m "docs: describe Janitor Gateway2000 auto routing"
~~~

---

### Task 4: Verify, deploy, and rerun the missed fleet work

**Files:**
- Verify: systemd/janitor-sweep.service and systemd/janitor-overview.service
- Runtime state: /Users/macmini/.local/state/janitor/state.json
- Live scheduler: Homelab user systemd units

**Interfaces:**
- Consumes committed Janitor source and the repaired systemd transport.
- Produces a successful bounded auto completion, a fleet run through auto, and durable outcome evidence.

- [ ] Step 1: Run local verification

~~~bash
PYTHONPATH=. pytest -q
ruff check janitor tests
git diff --check
~~~

Expected: the full suite passes, Ruff reports no violations, and existing dirty files remain unstaged.

- [ ] Step 2: Verify a live auto completion

~~~bash
ssh macmini 'cd /Volumes/2TB_SSD/GitHub/janitor && python3 -c '\''from janitor.worker import call_free; print(call_free("Reply exactly JANITOR_AUTO_ONLY_PROBE"))'\''
~~~

Expected: JANITOR_AUTO_ONLY_PROBE is returned.

- [ ] Step 3: Confirm the repaired systemd units

~~~bash
ssh homelab 'systemctl --user cat janitor-sweep.service; systemctl --user cat janitor-overview.service'
~~~

Expected: both units omit User= and use /Users/macmini/.local/bin/janitor-runner.

- [ ] Step 4: Run the missed fleet sweep

~~~bash
ssh homelab 'systemctl --user start --wait janitor-sweep.service'
~~~

Expected: exit 0 when required syntheses complete. If a provider failure remains, exit 1 and durable state must identify it; do not call that success.

- [ ] Step 5: Verify all repository outcomes and timer state

~~~bash
ssh macmini 'run_id=$(jq -r .janitor.last_run.run_id /Users/macmini/.local/state/janitor/state.json); jq -r --arg run_id "$run_id" '\''[to_entries[] | select(.key != "janitor" and .value.last_run.run_id == $run_id) | .value.last_run.status] | {repos:length, committed:(map(select(. == "committed"))|length), written:(map(select(. == "written"))|length), quiet:(map(select(. == "quiet"))|length), synthesis_failed:(map(select(. == "synthesis_failed"))|length), error:(map(select(. == "error"))|length)}'\'' /Users/macmini/.local/state/janitor/state.json'
ssh homelab 'systemctl --user show janitor-sweep.timer -p ActiveState -p NextElapseUSecRealtime -p LastTriggerUSec --no-pager'
~~~

Expected: repos is 80, state counts match the run output, the timer is active, and the next daily trigger is present. The command derives the run identifier from Janitor's own durable record so a stale prior run is not used as evidence.

- [ ] Step 6: Review final repository safety

~~~bash
git status --short --branch
git log -4 --oneline
~~~

Expected: the new commits are on local main, pre-existing dirty files remain, and no branch cleanup, push, reset, merge, or unrelated staging occurred.
