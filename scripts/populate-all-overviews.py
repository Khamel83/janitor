#!/usr/bin/env python3
import subprocess
import time
from pathlib import Path

workspace = Path("/Volumes/2TB_SSD/GitHub")
docs_repos_dir = workspace / "docs" / "repos"
docs_repos_dir.mkdir(parents=True, exist_ok=True)

all_repos = sorted([p for p in workspace.iterdir() if p.is_dir() and (p / ".git").exists()], key=lambda p: p.name)

print(f"[*] Starting full fleet overview population across {len(all_repos)} repositories...")

for p in all_repos:
    mirror_file = docs_repos_dir / f"{p.name}.md"
    if mirror_file.exists() and mirror_file.stat().st_size > 100:
        print(f"[skip] {p.name} already populated")
        continue
    
    print(f"[*] Synthesizing overview for {p.name}...")
    try:
        res = subprocess.run(["janitor", "overview", str(p)], capture_output=True, text=True, timeout=240)
        out = res.stdout.strip() or res.stderr.strip()
        print(f"[{p.name}] {out}")
    except Exception as e:
        print(f"[error] {p.name}: {e}")
    time.sleep(2)

print("[*] All fleet overviews completed!")
