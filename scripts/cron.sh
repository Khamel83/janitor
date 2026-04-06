#!/bin/bash
# Janitor cron — runs background intelligence jobs across all projects.
# Designed to run from system crontab, independent of any Claude session.
#
# INSTALL: crontab -e
#   */15 * * * * /home/ubuntu/github/oneshot/scripts/janitor-cron.sh >> /tmp/janitor-cron.log 2>&1
#
# Rate budget: 3-8 LLM calls/day + unlimited pure-compute jobs.
# Well within openrouter/free limits (1000/day, 20/min).

set -euo pipefail

REPO_BASE="${HOME}/github"
JANITOR_LOG="${REPO_BASE}/oneshot/.janitor/cron-runs.log"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

log() {
  echo "[$TIMESTAMP] $*" >> "$JANITOR_LOG"
}

# Find all projects with .janitor/events.jsonl
find_projects() {
  find "$REPO_BASE" -maxdepth 3 -name "events.jsonl" -path "*/.janitor/*" 2>/dev/null | while read events_file; do
    project_dir=$(dirname "$(dirname "$events_file")")
    line_count=$(wc -l < "$events_file" | tr -d ' ')
    if [ "$line_count" -ge 3 ]; then
      echo "$project_dir"
    fi
  done
}

# Process a single project — all jobs
process_project() {
  local project_dir="$1"
  local last_processed="$project_dir/.janitor/last-processed"

  # Skip if processed within last 10 minutes
  if [ -f "$last_processed" ]; then
    last_time=$(stat -c %Y "$last_processed" 2>/dev/null || echo 0)
    now=$(date +%s)
    age=$((now - last_time))
    if [ "$age" -lt 600 ]; then
      return 0
    fi
  fi

  log "Processing: $project_dir"
  cd "$project_dir" 2>/dev/null || return 0

  python3 -c "
import sys, os, time, json
from pathlib import Path
sys.path.insert(0, os.getcwd())
try:
    from core.janitor.recorder import SessionRecorder
    from core.janitor.worker import get_usage_stats
    from core.janitor.jobs import (
        summarize_recent_turns,
        detect_test_gaps,
        scan_code_smells,
        detect_config_drift,
        build_dependency_map,
        enrich_commit_messages,
    )

    stats = get_usage_stats()
    print(f'Usage: {stats[\"today\"]}/1000 today')

    recorder = SessionRecorder()

    # --- Pure-compute jobs (no LLM, always run) ---
    tg = detect_test_gaps()
    print(f'Test gaps: {tg.get(\"gap_count\", 0)} found')

    cs = scan_code_smells()
    print(f'Code smells: {cs.get(\"oversized_file_count\", 0)} oversized files, {cs.get(\"oversized_function_count\", 0)} long funcs')

    cd_result = detect_config_drift()
    print(f'Config drift: {cd_result.get(\"drift_count\", 0)} drifted')

    dg = build_dependency_map()
    print(f'Dep graph: {dg.get(\"files_with_imports\", 0)} files with imports')

    # --- LLM job: turn summarizer (always, rate-limited) ---
    if stats['today'] < 950:
        result = summarize_recent_turns(recorder, n=20)
        decisions = len(result.get('decisions', []))
        blockers = len(result.get('blockers', []))
        print(f'Extracted: {decisions} decisions, {blockers} blockers')

    # --- LLM job: commit enricher (only if new commits) ---
    if stats['today'] < 940:
        ce = enrich_commit_messages(recorder)
        print(f'Commit enrichments: {ce.get(\"enriched_count\", 0)} new')

    # --- Daily-only jobs (pattern miner + onboarding) ---
    daily_gate = Path('.janitor/last-daily-run')
    skip_daily = False
    if daily_gate.exists():
        age = time.time() - daily_gate.stat().st_mtime
        if age < 86400:
            skip_daily = True

    if not skip_daily and stats['today'] < 930:
        from core.janitor.jobs import mine_patterns, generate_onboarding_summary

        pm = mine_patterns(recorder)
        insights = len(pm.get('insights', []))
        print(f'Patterns: {insights} found')

        ob = generate_onboarding_summary(recorder)
        print(f'Onboarding: {ob.get(\"summary_length\", 0)} chars')

        daily_gate.write_text(str(time.time()))

    # Mark processed
    with open('.janitor/last-processed', 'w') as f:
        f.write('$TIMESTAMP')

except ImportError as e:
    print(f'SKIP: {e}')
except Exception as e:
    print(f'ERROR: {e}')
" 2>&1 | while read line; do
    log "  $line"
  done
}

# Run memory hygiene across all projects (once per run)
run_memory_hygiene() {
  log "Running memory hygiene"
  python3 -c "
import sys, os
sys.path.insert(0, os.path.expanduser('~/github/oneshot'))
try:
    from core.janitor.jobs import memory_hygiene
    result = memory_hygiene()
    overlaps = len(result.get('overlaps', []))
    print(f'Memory hygiene: {result.get(\"total_files\", 0)} files, {overlaps} overlaps')
except Exception as e:
    print(f'ERROR: {e}')
" 2>&1 | while read line; do
    log "  $line"
  done
}

# Main
log "--- Janitor cron start ---"
log "Rate limit: $(python3 -c "from core.janitor.worker import get_usage_stats; import json; print(json.dumps(get_usage_stats()))" 2>/dev/null || echo 'unavailable')"

projects=$(find_projects)
if [ -z "$projects" ]; then
  log "No projects with unprocessed events"
else
  for project in $projects; do
    process_project "$project"
  done
fi

run_memory_hygiene

log "--- Janitor cron end ---"
