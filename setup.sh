#!/bin/bash
# Janitor setup — one-command install.
# Installs the package, Claude Code hooks, and system cron.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Janitor Setup ==="

# 1. Install package
echo ""
echo "[1/4] Installing janitor package..."
pip install -e "$REPO_DIR" --quiet 2>&1 | grep -v "already satisfied" || true

# 2. Check for API key
echo ""
echo "[2/4] Checking for OPENROUTER_API_KEY..."
if [ -z "${OPENROUTER_API_KEY:-}" ]; then
    # Try vault (oneshot-style)
    if command -v secrets &>/dev/null; then
        KEY=$(secrets get OPENROUTER_API_KEY 2>/dev/null || true)
        if [ -n "$KEY" ]; then
            export OPENROUTER_API_KEY="$KEY"
            echo "  Found in vault."
        fi
    fi
fi
if [ -z "${OPENROUTER_API_KEY:-}" ]; then
    echo "  WARNING: OPENROUTER_API_KEY not set."
    echo "  Get a free key at https://openrouter.ai/keys"
    echo "  Then: export OPENROUTER_API_KEY=sk-or-..."
    echo "  Janitor will still install but LLM jobs won't work."
fi

# 3. Install Claude Code hooks
echo ""
echo "[3/4] Installing Claude Code hooks..."
SETTINGS="$HOME/.claude/settings.json"

if [ ! -f "$SETTINGS" ]; then
    echo '{"hooks":{}}' > "$SETTINGS"
fi

python3 -c "
import json, os

settings_path = os.path.expanduser('$SETTINGS')
with open(settings_path) as f:
    d = json.load(f)

hooks = d.setdefault('hooks', {})

# PostToolUse — record tool calls
if 'PostToolUse' not in hooks:
    hooks['PostToolUse'] = []
pt_list = hooks['PostToolUse']
found = any('janitor' in str(h.get('hooks', [])) for h in pt_list)
if not found:
    pt_list.append({
        'matcher': '',
        'hooks': [{
            'type': 'command',
            'command': '$REPO_DIR/hooks/record.sh',
            'timeout': 5,
        }]
    })

# SessionStart — inject context
if 'SessionStart' not in hooks:
    hooks['SessionStart'] = []
ss_list = hooks['SessionStart']
found = any('janitor' in str(h.get('hooks', [])) for h in ss_list)
if not found:
    ss_list.append({
        'matcher': '',
        'hooks': [{
            'type': 'command',
            'command': '$REPO_DIR/hooks/context.sh',
            'timeout': 5,
        }]
    })

# SessionEnd — session end marker
if 'SessionEnd' not in hooks:
    hooks['SessionEnd'] = []
se_list = hooks['SessionEnd']
found = any('janitor' in str(h.get('hooks', [])) for h in se_list)
if not found:
    se_list.append({
        'matcher': '',
        'hooks': [{
            'type': 'command',
            'command': '$REPO_DIR/hooks/session-end.sh',
        }]
    })

with open(settings_path, 'w') as f:
    json.dump(d, f, indent=2)
print('  Hooks installed.')
" "$REPO_DIR"

# 4. Install cron
echo ""
echo "[4/4] Installing cron entry..."
CRON_CMD="*/15 * * * * $REPO_DIR/scripts/cron.sh >> /tmp/janitor-cron.log 2>&1"
(crontab -l 2>/dev/null | grep -q "janitor/cron.sh" && echo "  Cron already exists.") || (crontab -l 2>/dev/null; echo "$CRON_CMD"; ) | crontab -
echo "  Cron installed (runs every 15 min)."

echo ""
echo "=== Done ==="
echo "Janitor will start recording events on your next Claude Code session."
echo "Context will appear automatically at session start."
echo ""
echo "To uninstall:"
echo "  pip uninstall janitor"
echo "  crontab -l | grep -v janitor | crontab -"
echo "  # Remove hooks from ~/.claude/settings.json"
