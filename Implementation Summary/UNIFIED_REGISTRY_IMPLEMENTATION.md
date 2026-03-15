# Unified Tester Registry Implementation

## Problem Solved

Previously, BENTO had two separate tester registries that never synchronized:

1. `watcher/watcher_config.py` → `TESTER_REGISTRY` (hostname + env + repo_dir + build_cmd)
   - Read by: watcher_main, watcher_builder, watcher_copier
   - Edited by: developer manually in code

2. `bento_testers.json` → `_TESTER_REGISTRY` (hostname + env only)
   - Read by: main.py GUI only
   - Edited by: Add Tester dialog

When a user clicked "+ Add Tester", they updated `bento_testers.json` — but the watcher on that tester machine was still reading `watcher_config.py`, which knew nothing about it. The two were permanently out of sync.

## Solution: Single Source of Truth

Merged both registries into one JSON file on the shared folder that both the GUI and watcher read:

**Location:** `P:\temp\BENTO\bento_testers.json`

**Format:**
```json
{
  "IBIR-0383 (ABIT)": {
    "hostname":  "IBIR-0383",
    "env":       "ABIT",
    "repo_dir":  "C:\\xi\\adv_ibir_master",
    "build_cmd": "make release"
  },
  "CTOWTST-0031 (CNFG)": {
    "hostname":  "CTOWTST-0031",
    "env":       "CNFG",
    "repo_dir":  "C:\\xi\\adv_ibir_master",
    "build_cmd": "make release_supermicro"
  }
}
```

## Changes Made

### 1. watcher/watcher_config.py
- Replaced hardcoded `TESTER_REGISTRY` with `_load_registry()` function
- Loads from `P:\temp\BENTO\bento_testers.json` at startup
- Falls back to hardcoded defaults if file doesn't exist
- No other watcher files need changes — they all import `TESTER_REGISTRY`

### 2. main.py - Add Tester Dialog
- Expanded to collect 4 fields instead of 2:
  - Tester Hostname (existing)
  - Environment (existing)
  - Repo Path (NEW) — with 📁 browse button
  - Build Command (NEW) — dropdown with `make release` / `make release_supermicro`

- Dialog size increased: 560×480 → 560×580
- Preflight checklist remains at top (unchanged)

### 3. main.py - Registry Save
- `_save_tester_registry()` now writes to TWO locations:
  1. Local `bento_testers.json` (for GUI persistence)
  2. Shared `P:\temp\BENTO\bento_testers.json` (for watcher access)

### 4. compilation_orchestrator.py
- Added `get_valid_envs()` function that loads from shared registry
- `valid_envs` is now dynamic — no hardcoded list
- Automatically includes any ENV added via Add Tester dialog

## User Workflow (After Implementation)

1. User sets up watcher on tester machine (shared folder, Task Scheduler)
2. User clicks "+ Add Tester" in GUI
3. User fills in:
   - Hostname: `IBIR-0999`
   - Environment: `ABIT`
   - Repo Path: `C:\xi\adv_ibir_master` (browse button available)
   - Build Command: `make release` (dropdown)
4. GUI writes to `P:\temp\BENTO\bento_testers.json`
5. Next time watcher starts on that tester, it loads the JSON and knows about itself
6. `valid_envs` in orchestrator automatically includes the new ENV
7. Compile works end-to-end

## Migration

For existing installations, run:
```bash
python migrate_registry.py
```

This will:
- Read existing `bento_testers.json` (2-tuple format)
- Add default `repo_dir` and `build_cmd` to each entry
- Write to shared folder in new format
- Update local file to 4-tuple format

## Backward Compatibility

- Old 2-tuple format is auto-migrated on load
- Defaults added: `repo_dir = C:\xi\adv_ibir_master`, `build_cmd = make release`
- No manual editing of `watcher_config.py` required anymore

## Benefits

✓ One dialog, one file, one source of truth
✓ No developer needs to manually edit `watcher_config.py` ever again
✓ `valid_envs` is fully dynamic — add tester in GUI, it works immediately
✓ Watcher auto-loads latest registry on startup
✓ GUI and watcher always in sync
