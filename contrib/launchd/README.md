# macOS launchd templates

These `.plist.example` files are templates for running `janitor sweep` nightly
and `janitor overview` weekly via `launchd`. They're personal-machine config
(hardcoded paths, your own repo list) — never loaded automatically, and not
meant to be committed with real values filled in.

## Setup

```bash
pip install -e /path/to/janitor         # installs the `janitor` console script
which janitor                            # note this path — goes in ProgramArguments

mkdir -p ~/Library/LaunchAgents
cp contrib/launchd/com.khamel.janitor.sweep.plist.example \
   ~/Library/LaunchAgents/com.khamel.janitor.sweep.plist
cp contrib/launchd/com.khamel.janitor.overview.plist.example \
   ~/Library/LaunchAgents/com.khamel.janitor.overview.plist
```

Edit both copies in `~/Library/LaunchAgents/` and replace:

| Placeholder | With |
|---|---|
| `REPLACE_ME_JANITOR_BIN` | Output of `which janitor` |
| `REPLACE_ME_REPO_1`, `REPLACE_ME_REPO_2`, ... | Absolute paths to the repos you want swept/overviewed (add/remove `<string>` entries as needed) |
| `REPLACE_ME_HOME` | Your `$HOME`, e.g. `/Users/yourname` |
| `REPLACE_ME_DOCS_MIRROR_PATH` (overview only) | Path to a central docs repo, or delete the `JANITOR_DOCS_MIRROR` key entirely to skip mirroring |

If you use `g2k-bg`/`g2k` as your model gateway, make sure its own directory
is on the `PATH` set in the plist — launchd does not inherit your shell's
`PATH`. If it's not on PATH, janitor falls back to `openrouter/free` and
needs `OPENROUTER_API_KEY` added to `EnvironmentVariables` instead.

Then load them:

```bash
launchctl load ~/Library/LaunchAgents/com.khamel.janitor.sweep.plist
launchctl load ~/Library/LaunchAgents/com.khamel.janitor.overview.plist
```

Verify first with a dry run before trusting the schedule:

```bash
janitor sweep --dry-run /path/to/some/repo
janitor overview --dry-run /path/to/some/repo
```

## Uninstall

```bash
launchctl unload ~/Library/LaunchAgents/com.khamel.janitor.sweep.plist
launchctl unload ~/Library/LaunchAgents/com.khamel.janitor.overview.plist
rm ~/Library/LaunchAgents/com.khamel.janitor.{sweep,overview}.plist
```
