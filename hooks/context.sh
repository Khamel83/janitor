#!/bin/bash
# SessionStart hook: injects recent janitor intelligence into new sessions.
# Fires once at session start — tells Claude about accumulated project data.

project_dir="$PWD"
while [ "$project_dir" != "/" ]; do
  [ -d "$project_dir/.git" ] && break
  project_dir=$(dirname "$project_dir")
done
[ "$project_dir" = "/" ] && exit 0

oneshot_dir="$project_dir/.oneshot"
output=""

# --- Onboarding summary (highest priority, 24h window) ---
onboarding_file="$oneshot_dir/onboarding-summary.md"
if [ -f "$onboarding_file" ]; then
  age=$(find "$onboarding_file" -mmin -1440 2>/dev/null | grep -q "." && echo "found")
  if [ -n "$age" ]; then
    preview=$(head -20 "$onboarding_file" 2>/dev/null | tr '\n' ' ' | head -c 500)
    if [ -n "$preview" ]; then
      output="JANITOR ONBOARDING: ${preview}"
    fi
  fi
fi

# --- Recent session digest (2h window) ---
digest_file="$oneshot_dir/last-digest.md"
if [ -f "$digest_file" ]; then
  age=$(find "$digest_file" -mmin -120 2>/dev/null | grep -q "." && echo "found")
  if [ -n "$age" ]; then
    digest_preview=$(head -5 "$digest_file" 2>/dev/null | tr '\n' ' ' | head -c 300)
    if [ -n "$digest_preview" ]; then
      output="${output} JANITOR: Last digest (within 2h): ${digest_preview}"
    fi
  fi
fi

# --- Unprocessed events ---
events_file="$oneshot_dir/events.jsonl"
if [ -f "$events_file" ]; then
  event_count=$(wc -l < "$events_file" | tr -d ' ')
  if [ "$event_count" -gt 20 ]; then
    decisions=$(grep -c '"type":"decision"' "$events_file" 2>/dev/null) || decisions=0
    blockers=$(grep -c '"type":"blocker"' "$events_file" 2>/dev/null) || blockers=0
    if [ "$decisions" -gt 0 ] || [ "$blockers" -gt 0 ]; then
      output="${output} JANITOR: ${event_count} events (${decisions} decisions, ${blockers} blockers)"
    fi
  fi
fi

# --- Test gaps (2h window) ---
test_gaps_file="$oneshot_dir/test-gaps.json"
if [ -f "$test_gaps_file" ]; then
  age=$(find "$test_gaps_file" -mmin -120 2>/dev/null | grep -q "." && echo "found")
  if [ -n "$age" ]; then
    gap_info=$(python3 -c "
import json
d = json.load(open('$test_gaps_file'))
gc = d.get('gap_count', 0)
if gc > 0:
    files = [g['source_file'] for g in d.get('gaps', [])[:3]]
    print(f'{gc} gaps: ' + ', '.join(files))
" 2>/dev/null)
    if [ -n "$gap_info" ]; then
      output="${output} JANITOR: Test gaps: ${gap_info}"
    fi
  fi
fi

# --- Code smells (2h window) ---
smells_file="$oneshot_dir/code-smells.json"
if [ -f "$smells_file" ]; then
  age=$(find "$smells_file" -mmin -120 2>/dev/null | grep -q "." && echo "found")
  if [ -n "$age" ]; then
    smell_info=$(python3 -c "
import json
d = json.load(open('$smells_file'))
fc = d.get('oversized_file_count', 0)
func = d.get('oversized_function_count', 0)
if fc > 0 or func > 0:
    print(f'{fc} oversized files, {func} long functions')
" 2>/dev/null)
    if [ -n "$smell_info" ]; then
      output="${output} JANITOR: Code smells: ${smell_info}"
    fi
  fi
fi

# --- Config drift (2h window) ---
drift_file="$oneshot_dir/config-drift.json"
if [ -f "$drift_file" ]; then
  age=$(find "$drift_file" -mmin -120 2>/dev/null | grep -q "." && echo "found")
  if [ -n "$age" ]; then
    drift_info=$(python3 -c "
import json
d = json.load(open('$drift_file'))
dc = d.get('drift_count', 0)
if dc > 0:
    print(', '.join(d.get('drifted_file_names', [])[:3]))
" 2>/dev/null)
    if [ -n "$drift_info" ]; then
      output="${output} JANITOR: Config drift: ${drift_info}"
    fi
  fi
fi

# --- Dependency impact (2h window) ---
dep_file="$oneshot_dir/dep-graph.json"
if [ -f "$dep_file" ]; then
  age=$(find "$dep_file" -mmin -120 2>/dev/null | grep -q "." && echo "found")
  if [ -n "$age" ]; then
    impact_info=$(python3 -c "
import json
d = json.load(open('$dep_file'))
top = d.get('impact_ranking', [])[:3]
if top:
    print(', '.join(f'{t[\"file\"]} ({t[\"downstream_count\"]} deps)' for t in top))
" 2>/dev/null)
    if [ -n "$impact_info" ]; then
      output="${output} JANITOR: High-impact files: ${impact_info}"
    fi
  fi
fi

# --- Patterns (24h window) ---
patterns_file="$oneshot_dir/patterns.json"
if [ -f "$patterns_file" ]; then
  age=$(find "$patterns_file" -mmin -1440 2>/dev/null | grep -q "." && echo "found")
  if [ -n "$age" ]; then
    pat_info=$(python3 -c "
import json
d = json.load(open('$patterns_file'))
insights = d.get('insights', [])
if insights:
    print(f'{len(insights)} patterns: ' + '; '.join(p.get('description', '')[:60] for p in insights[:3]))
" 2>/dev/null)
    if [ -n "$pat_info" ]; then
      output="${output} JANITOR: Recurring patterns: ${pat_info}"
    fi
  fi
fi

# --- Hotspot files from change analysis (2h window) ---
changes_file="$oneshot_dir/last-changes.json"
if [ -f "$changes_file" ]; then
  age=$(find "$changes_file" -mmin -120 2>/dev/null | grep -q "." && echo "found")
  if [ -n "$age" ]; then
    hotspots=$(python3 -c "
import json
d = json.load(open('$changes_file'))
h = d.get('hotspot_files', [])
if h: print(', '.join(h[:5]))
" 2>/dev/null)
    if [ -n "$hotspots" ]; then
      output="${output} JANITOR: Hotspot files: ${hotspots}"
    fi
  fi
fi

# Output
if [ -n "$output" ]; then
  echo "{\"hookSpecificOutput\":{\"additionalContext\":\"JANITOR_CONTEXT:${output//\"/\\\"}\"}}"
fi

exit 0
