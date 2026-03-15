# BENTO Compilation Pipeline - Final Polish Fixes

## Overview
Five additional issues identified in final review that would cause functional problems in production.

## Issue #1: ZIP Includes Build Artifacts (FUNCTIONAL - High Impact)

### Problem
`ZIP_EXCLUDE_PATTERNS` only filtered Python/web artifacts:
- `.git`, `__pycache__`, `.pyc`, `node_modules`

Missing firmware build artifacts:
- `.o`, `.d`, `.a` (compiled objects, dependencies, libraries)
- `.map`, `.elf` (linker maps, binaries)
- `.tgz` (previous release archives)
- `release/` (entire output directory)

**Impact:**
- Every compile ships previous build's output back to tester
- ZIP unnecessarily large (could be 10x+ bigger)
- Slow ZIP creation and network transfer
- Wasted bandwidth transferring stale `.tgz` files

### Fix
Added firmware-specific exclusions:
```python
ZIP_EXCLUDE_PATTERNS = {
    # Version control
    ".git",
    # Python
    "__pycache__", ".pyc", ".pyo",
    # BENTO internals
    ".bento_lock", ".bento_status",
    # Node
    "node_modules",
    # Firmware build artifacts
    "release",          # entire release/ folder
    ".o",               # compiled object files
    ".d",               # dependency files
    ".a",               # static libraries
    ".map",             # linker map files
    ".elf",             # compiled binaries
    ".tgz",             # previous release archives
    ".bin",             # binary firmware images
    ".hex",             # hex firmware images
}
```

**Expected improvement:** ZIP size reduced by 50-90% depending on repo state

## Issue #2: Unused Imports (Cleanup)

### Problem
```python
import shutil
from pathlib import Path
```
Both imported but never used anywhere in `compilation_orchestrator.py`.

### Impact
- Code noise
- Suggests incomplete implementation
- Minor performance overhead (negligible)

### Fix
Removed both imports.

## Issue #3: Minute-Precision Timestamp (CORRECTNESS RISK)

### Problem
ZIP filename timestamp: `strftime("%Y%m%d_%H%M")` - precision to the minute.

**Collision scenario:**
1. User triggers compile at 14:35:42
2. Quick fix, recompile at 14:35:58 (same minute)
3. Second `create_tp_zip()` overwrites first ZIP
4. First compile's work lost with no error
5. Watcher picks up second ZIP, first compile never happens

**Likelihood:** High during active testing (rapid fix-compile cycles)

### Fix
Added seconds to timestamp:
```python
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")  # Include seconds
```

**Result:** Collision window reduced from 60 seconds to <1 second (effectively eliminated)

## Issue #4: COMPILED_TGZ Written But Never Read (Loose End)

### Problem
After successful compile:
```python
self.save_workflow_step("COMPILED_TGZ", tgz_path)
```

But `get_workflow_step("COMPILED_TGZ")` called exactly zero times in codebase.

**Impact:**
- Misleading workflow file entry
- Suggests downstream usage that doesn't exist
- Confusion about what the workflow file should contain

### Fix
Removed both `save_workflow_step("COMPILED_TGZ", ...)` calls:
- Single-tester success handler
- Multi-tester success handler

**Rationale:** If downstream steps need TGZ path in future, it can be re-added when actually wired up. For now, it's dead code.

## Issue #5: No Pre-flight Check for Shared Folder (UX)

### Problem
Pre-flight checks validated:
- JIRA key non-empty ✓
- Repo path exists ✓
- RAW_ZIP path non-empty ✓
- RELEASE_TGZ path non-empty ✓

But never checked if `P:\temp\BENTO\RAW_ZIP` is actually accessible.

**Failure scenario:**
1. Network share down or P: drive not mapped
2. User clicks "Compile"
3. Background thread starts
4. Failure happens inside `create_tp_zip()` via `os.makedirs()`
5. Generic error: "ZIP creation failed"
6. User confused: "What went wrong?"

### Fix
Added accessibility checks:
```python
elif not os.path.isdir(raw_zip):
    errors.append("RAW_ZIP folder not accessible: " + raw_zip +
                 "\n    Check that the P: drive is mapped on this machine")

elif not os.path.isdir(release):
    errors.append("RELEASE_TGZ folder not accessible: " + release +
                 "\n    Check that the P: drive is mapped on this machine")
```

**Result:** Clear immediate error before background thread starts, not confusing generic failure

## Summary Table

| Issue | Severity | Impact | Fix Complexity |
|-------|----------|--------|----------------|
| ZIP includes build artifacts | High | 10x+ larger ZIPs, slow transfer | Low (add patterns) |
| Unused imports | Low | Code noise | Trivial (delete 2 lines) |
| Minute-precision timestamp | Medium | Silent collision risk | Trivial (add 1 char) |
| COMPILED_TGZ never read | Low | Misleading workflow | Low (delete 2 lines) |
| No shared folder check | Medium | Confusing error messages | Low (add 2 checks) |

## Testing Recommendations

### Issue #1: ZIP Exclusions
1. Create a test repo with build artifacts:
   - Add dummy `.o`, `.a`, `.elf` files
   - Create `release/` folder with dummy `.tgz`
2. Compile → check ZIP contents
3. Verify artifacts excluded
4. Compare ZIP size before/after fix

### Issue #2: Unused Imports
No testing needed - purely cleanup.

### Issue #3: Timestamp Collision
1. Trigger compile
2. Immediately trigger second compile (within same minute)
3. Check RAW_ZIP folder → should see TWO ZIPs with different timestamps
4. Both should have unique filenames (no overwrite)

### Issue #4: COMPILED_TGZ
No testing needed - removed dead code.

### Issue #5: Shared Folder Check
1. Disconnect P: drive (or use invalid path)
2. Try to compile
3. Should see immediate error: "RAW_ZIP folder not accessible"
4. Error should mention checking P: drive mapping
5. Should NOT see generic "ZIP creation failed"

## Files Modified

1. **compilation_orchestrator.py**
   - Expanded ZIP_EXCLUDE_PATTERNS with firmware artifacts
   - Removed unused imports (shutil, Path)
   - Added seconds to timestamp format

2. **main.py**
   - Removed COMPILED_TGZ workflow saves (2 locations)
   - Added pre-flight checks for RAW_ZIP and RELEASE_TGZ accessibility

## Impact on Existing Deployments

**ZIP Exclusions:**
- Immediate benefit: smaller ZIPs, faster transfers
- No breaking changes
- Existing ZIPs unaffected (only new compiles benefit)

**Timestamp Seconds:**
- Prevents future collisions
- Existing ZIPs unaffected
- Watcher already handles any timestamp format (parses by position)

**Shared Folder Check:**
- Better error messages
- No functional change to success path
- Only affects error reporting

## Deployment Notes

**Recommended:** Test with a repo that has build artifacts to verify exclusions work correctly.

**No watcher changes required** - all fixes are orchestrator/GUI side.

**No configuration migration** - existing settings work as-is.
