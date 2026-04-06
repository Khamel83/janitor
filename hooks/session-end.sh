#!/bin/bash
# SessionEnd hook: lightweight session wrap-up.
# Only writes a timestamp marker — heavy processing happens via cron.
#
# The cron job (janitor-cron.sh) is the real processor.
# This hook just marks the session as ended so the cron knows
# there's unprocessed data.

# Find project root
project_dir="$PWD"
while [ "$project_dir" != "/" ]; do
  [ -d "$project_dir/.git" ] && break
  project_dir=$(dirname "$project_dir")
done
[ "$project_dir" = "/" ] && exit 0

events_file="$project_dir/.janitor/events.jsonl"

# Skip if no events
[ ! -f "$events_file" ] && exit 0

line_count=$(wc -l < "$events_file" | tr -d ' ')
[ "$line_count" -lt 3 ] && exit 0

# Just mark session end — cron does the actual processing
timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
session_id="${CLAUDE_CODE_SESSION_ID:-unknown}"

printf '{"ts":"%s","session":"%s","type":"session_end","content":"Session ended","meta":{"auto":true},"files":[]}\n' \
  "$timestamp" "$session_id" >> "$events_file"

exit 0
