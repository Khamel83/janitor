#!/bin/bash
# PostToolUse hook: records tool calls to .janitor/events.jsonl
# Fires AFTER every tool call during a Claude Code session.
# Optimized: uses printf instead of jq (one process instead of 3-4).
#
# INSTALL: Add to ~/.claude/settings.json under PostToolUse hooks

input=$(cat)

# Fast field extraction with parameter expansion (no jq needed for simple fields)
tool_name="${input#*\"tool_name\":\"}"
tool_name="${tool_name%%\"*}"

# Skip noise early — before any further processing
case "$tool_name" in
  Glob|Grep|WebFetch|WebSearch|mcp__*|TaskCreate|TaskUpdate|TaskList|TaskGet|CronCreate|CronDelete|CronList|AskUserQuestion)
    exit 0
    ;;
esac

# Find project root (directory with .git)
project_dir="$PWD"
while [ "$project_dir" != "/" ]; do
  [ -d "$project_dir/.git" ] && break
  project_dir=$(dirname "$project_dir")
done
[ "$project_dir" = "/" ] && exit 0

oneshot_dir="$project_dir/.oneshot"
events_file="$oneshot_dir/events.jsonl"
mkdir -p "$oneshot_dir"

timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
session_id="${CLAUDE_CODE_SESSION_ID:-$(date +%Y%m%d-%H%M%S)}"

# Extract file_path from tool_input (works for Read/Write/Edit which have file_path field)
# Simple string extraction: find "file_path":"..." and take value up to next "
file_path=""
case "$tool_name" in
  Read|Write|Edit)
    file_path="${input#*\"file_path\":\"}"
    file_path="${file_path%%\"*}"
    ;;
esac

# Build event based on tool type
case "$tool_name" in
  Read)
    [ -z "$file_path" ] && exit 0
    printf '{"ts":"%s","session":"%s","type":"file_read","content":"Read %s","meta":{"tool":"Read","auto":true},"files":["%s"]}\n' \
      "$timestamp" "$session_id" "$file_path" "$file_path" >> "$events_file"
    ;;
  Write)
    [ -z "$file_path" ] && exit 0
    printf '{"ts":"%s","session":"%s","type":"file_written","content":"Created %s","meta":{"tool":"Write","auto":true},"files":["%s"]}\n' \
      "$timestamp" "$session_id" "$file_path" "$file_path" >> "$events_file"
    ;;
  Edit)
    [ -z "$file_path" ] && exit 0
    printf '{"ts":"%s","session":"%s","type":"file_written","content":"Edited %s","meta":{"tool":"Edit","auto":true},"files":["%s"]}\n' \
      "$timestamp" "$session_id" "$file_path" "$file_path" >> "$events_file"
    ;;
  Bash)
    # Extract command from tool_input
    cmd="${input#*\"command\":\"}"
    cmd="${cmd%%\"*}"
    cmd="${cmd:0:200}"
    case "$cmd" in
      git\ commit*|git\ push*)
        printf '{"ts":"%s","session":"%s","type":"commit","content":"Git: %s","meta":{"tool":"Bash","auto":true},"files":[]}\n' \
          "$timestamp" "$session_id" "${cmd:0:100}" >> "$events_file"
        ;;
      git\ *)
        exit 0  # skip routine git
        ;;
      *)
        printf '{"ts":"%s","session":"%s","type":"action_taken","content":"Ran: %s","meta":{"tool":"Bash","auto":true},"files":[]}\n' \
          "$timestamp" "$session_id" "${cmd:0:100}" >> "$events_file"
        ;;
    esac
    ;;
  *)
    exit 0  # skip everything else
    ;;
esac

exit 0
