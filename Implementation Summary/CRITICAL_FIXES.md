# BENTO Compilation Pipeline - Critical Fixes

## Overview
Five critical issues identified and fixed in the compilation pipeline that would cause real problems in production use.

## Issue #1: RAW_ZIP Folder Never Gets Cleaned Up (CRITICAL)

### Problem
After successful compilation, ZIP files and `.bento_status` sidecar files remain in RAW_ZIP forever. Over weeks of active use:
- RAW_ZIP fills with stale ZIPs (already processed)
- Watcher's processed set lives only in memory
- Every watcher restart (reboot) rescans RAW_ZIP, skips pre-existing files
- On slow network share, bloated RAW_ZIP makes poll loop sluggish

### Impact
- Disk space waste
- Performance degradation over time
- Operational overhead (manual cleanup required)

### Fix
Orchestrator cleans up after confirmed success:
```python
# In wait_for_build(), after TGZ confirmed in RELEASE folder:
try:
    os.remove(zip_path)
    os.remove(zip_path + ".bento_status")
    _log(logger, f"✓ Cleaned up {os.path.basename(zip_path)}", log_callback)
except Exception as e:
    # Non-fatal — share might not allow delete
    _log(logger, f"⚠ Could not clean up ZIP: {e}", log_callback, "warning")
```

**Why orchestrator, not watcher?**
- Orchestrator knows when compile succeeded (TGZ confirmed)
- Watcher might crash/restart before cleanup
- Orchestrator is the right place for "transaction complete" logic

## Issue #2: Timeout Mismatch (CRITICAL - Silent Wrong Results)

### Problem
- Orchestrator timeout: 900s (15 min)
- Watcher timeout: 1800s (30 min)

**Scenario:**
1. Build takes 20 minutes (legitimately slow)
2. Orchestrator gives up at 15 min → reports TIMEOUT to GUI
3. Watcher keeps going → succeeds at 20 min → writes success status
4. Nobody is polling anymore
5. User sees failure, valid TGZ sits in RELEASE_TGZ unnoticed

### Impact
- Silent wrong results
- User thinks compile failed when it actually succeeded
- Wasted time re-running successful compiles
- Confusion about TGZ files appearing "mysteriously"

### Fix
Orchestrator timeout MUST be longer than watcher timeout:
```python
# compilation_orchestrator.py
BUILD_TIMEOUT_SECONDS = 2100   # 35 minutes (watcher has 30 min)
```

**Rule:** Orchestrator never gives up before watcher does.

## Issue #3: TESTER_MAP Duplicated Across 3 Files (Maintenance Hazard)

### Problem
Same hostname/env mapping exists in three places:
1. `watcher/watcher_config.py` - TESTER_REGISTRY (authoritative)
2. `compilation_orchestrator.py` - TESTER_MAP (dead duplicate)
3. `main.py` - _DEFAULT_TESTERS (GUI defaults)

When adding a 4th tester, must update all three or risk inconsistency.

### Impact
- Maintenance burden
- Risk of desync between files
- Confusion about which is authoritative

### Fix
**Removed from orchestrator:**
- Deleted TESTER_MAP dict
- Deleted get_tester_hostname() function (only used in standalone test)
- Removed hardcoded valid_envs = {"ABIT", "SFN2", "CNFG"}
- Now accepts any env if hostname provided (validated by watcher)

**Single source of truth:** `watcher/watcher_config.py` TESTER_REGISTRY

**GUI defaults:** main.py _DEFAULT_TESTERS remains for initial GUI population only

## Issue #4: in_progress Logs Every 15 Seconds (Log Spam)

### Problem
During build, orchestrator logs "still building (Xs elapsed)" every 15 seconds:
- 15-minute compile = ~60 log lines
- Buries actual meaningful output
- Makes log panel hard to read

### Impact
- Noisy logs
- Important messages buried
- Poor user experience

### Fix
Log only every 2 minutes instead of every 15 seconds:
```python
elif state == "in_progress":
    elapsed_so_far = int(time.time() - start)
    # Update phase every 2 minutes to avoid log spam
    if elapsed_so_far % 120 < POLL_INTERVAL:
        _phase(logger, f"Building... ({elapsed_so_far}s elapsed)", log_callback)
```

**Result:** 15-minute compile = ~7 log lines instead of 60

## Issue #5: No Retry Button on Failure (UX Friction)

### Problem
After compile failure/timeout:
- User must manually click "Compile on Selected Tester(s)" again
- Must re-verify all fields unchanged
- For timeout case (build was just slow), retry is almost always desired

### Impact
- Extra clicks
- Risk of accidentally changing fields between attempts
- Frustration on timeout (user knows it might just need more time)

### Fix
Added "🔄 Retry Compile" button to error dialog:
```python
def _retry():
    dialog.destroy()
    self.trigger_compile_with_lock()

ttk.Button(btn_frame, text="🔄 Retry Compile", command=_retry).pack(side=tk.LEFT, padx=4)
ttk.Button(btn_frame, text="Close", command=dialog.destroy).pack(side=tk.LEFT, padx=4)
```

**Behavior:** Retries with exact same parameters (no field changes)

## Summary Table

| Issue | Severity | Impact | Fix Complexity |
|-------|----------|--------|----------------|
| RAW_ZIP never cleaned | High | Disk space + performance | Low (5 lines) |
| Timeout mismatch | Critical | Silent wrong results | Trivial (1 line) |
| TESTER_MAP duplicated | Medium | Maintenance burden | Low (remove code) |
| in_progress log spam | Low | Noisy logs | Trivial (1 line) |
| No retry button | Low | UX friction | Low (5 lines) |

## Testing Recommendations

### Issue #1: RAW_ZIP Cleanup
1. Run successful compile
2. Check RAW_ZIP folder → ZIP and .bento_status should be gone
3. Check log → should see "✓ Cleaned up <filename>"
4. Run failed compile → ZIP should remain (not cleaned up)

### Issue #2: Timeout
1. Verify orchestrator timeout (2100s) > watcher timeout (1800s)
2. Test with artificially long build (if possible)
3. Confirm orchestrator waits for watcher to finish

### Issue #3: TESTER_MAP
1. Add new tester in watcher_config.py only
2. Verify GUI accepts it without orchestrator changes
3. Confirm no hardcoded env validation errors

### Issue #4: Log Spam
1. Run 10-minute compile
2. Count "still building" messages in log
3. Should see ~5 messages, not ~40

### Issue #5: Retry Button
1. Trigger compile failure (wrong tester, etc.)
2. Error dialog should show "🔄 Retry Compile" button
3. Click retry → should re-run with same parameters
4. Verify no need to re-fill fields

## Files Modified

1. **compilation_orchestrator.py**
   - Added ZIP cleanup after success
   - Changed BUILD_TIMEOUT_SECONDS: 900 → 2100
   - Removed TESTER_MAP and get_tester_hostname()
   - Removed hardcoded valid_envs check
   - Fixed in_progress log frequency: 15s → 2min

2. **main.py**
   - Added retry button to _show_compile_error_dialog()

## Backward Compatibility

All fixes are backward compatible:
- Cleanup is non-fatal (catches exceptions)
- Longer timeout only helps (never hurts)
- Removed code was unused in live pipeline
- Log frequency change is purely cosmetic
- Retry button is additive (doesn't change existing flow)

## Deployment Notes

**No watcher changes required** - all fixes are orchestrator/GUI side.

**No configuration migration** - existing settings work as-is.

**Recommended:** Clear RAW_ZIP folder before deploying (one-time cleanup of accumulated ZIPs).
