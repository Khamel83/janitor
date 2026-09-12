# Janitor Gateway2000 Auto-Only Routing Design

**Date:** 2026-09-12  
**Status:** Approved for implementation

## Decision

Janitor uses Gateway2000's `auto` lane for every live model request, including
nightly sweeps and weekly overviews. Janitor never selects or invokes
`g2k-bg`. Gateway2000 owns provider selection, recovery, and fallback inside
the auto route.

The existing direct OpenRouter free-model path remains available only for
environments where no Gateway2000 auto client is available. A Gateway2000
request failure is reported as a Janitor synthesis failure; it is not silently
rerouted through another Janitor-selected lane.

## Context

The repaired systemd transport reached the Mac mini and processed all 80
repositories, but the installed Janitor path selected `g2k-bg`. The live
background route returned HTTP 429 `provider_pool_exhausted`. A fresh bounded
probe through the Gateway2000 `auto` function completed successfully. This
establishes a provider-lane mismatch, not a scheduler or repository-processing
failure.

The installed `/Users/macmini/.local/bin/g2k` file is a symlink to a wrapper
that currently invokes the background function. Therefore, changing only the
binary name is insufficient.

## Architecture

On the Mac mini, the worker invokes the `g2k` shell function from the sourced
Gateway2000 helper (`~/.config/gateway2000/gateway2000.zsh`) and streams the
complete prompt through stdin. This makes the selected lane explicit and
avoids the misleading executable symlink. Where the helper is unavailable,
the worker may use a genuine `g2k` executable if present; it must never search
for or invoke `g2k-bg`.

The worker keeps the existing prompt-size, timeout, usage logging, structured
response parsing, and rate-limit behavior. `sweep` and `overview` continue to
share this one backend policy.

## Failure behavior

- A successful auto completion proceeds through the existing validation and
  document commit gates.
- A Gateway2000 auto failure remains `synthesis_failed` with the provider error
  preserved in the run output and durable state.
- Janitor does not fall back from auto to background, and does not choose a
  provider or model itself.
- The OpenRouter path is used only when no Gateway2000 auto client is present,
  preserving CI and other gateway-less environments.

## Testing and rollout

1. Add unit coverage proving the auto helper command is selected and
   `g2k-bg` is never selected.
2. Preserve coverage for stdin streaming, timeout errors, structured parsing,
   and the gateway-less OpenRouter fallback.
3. Run the full offline suite, lint, and whitespace checks.
4. Deploy the source already used by the Mac mini runner and verify a bounded
   auto completion before the fleet run.
5. Trigger the missed fleet sweep. Verify all 80 repository outcomes, the
   systemd result, the persistent Janitor state, and the next timer schedule.

## Non-goals

- No change to Gateway2000's provider configuration or credentials.
- No global rewrite of the `g2k`/`g2k-bg` installation.
- No automatic branch merge, deletion, reset, checkout, pruning, or push.
- No masking of provider-capacity failures as successful Janitor work.
