#!/bin/bash
set -euo pipefail
export PATH="/opt/homebrew/bin:/Users/macmini/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
if [ -f "/etc/janitor/env" ]; then
    source /etc/janitor/env
fi
exec python3 -m janitor.cli "$@"
