# BENTO — Empty `<AddtionalFileFolder />` Fix Summary

## Date Applied
2026-03-31

## Problem
The `<AddtionalFileFolder>` element in generated Slate XML was always empty, even when the upstream recipe selection subprocess returned valid `RECIPE_SEL_FILE_COPY_PATHS_##` entries.

## Root Cause
Delimiter mismatch: The upstream `recipe_selection.py` (Python 2) uses `|` as the source/dest separator, but `checkout_orchestrator.py` only checked for `,`.

## Fixes Applied

### Fix 1 (P1) ✅ — Delimiter Mismatch
**File:** `model/orchestrators/checkout_orchestrator.py` (lines ~676–685)
- Updated delimiter handling to check `|` first, then fall back to `,`
- Added `.strip()` to source and dest values
- Added `logger.warning()` for entries with no recognized delimiter

### Fix 2 (P1) ✅ — Warning Logging for Dropped Entries
**File:** `model/orchestrators/checkout_orchestrator.py` (lines ~687–692)
- Added summary `logger.info()` after the loop showing File element count vs entry count
- Added `logger.warning()` when all entries are dropped (count mismatch)

### Fix 3 (P2) ✅ — Enhanced Diagnostic Logging
**File:** `model/recipe_selector.py` — `select_recipe_or_fallback()` method
- Added entry-point DIAG logging with all condition values
- Added success/failure/skipped DIAG logging for subprocess path
- Added explicit "Using FALLBACK" warning when fallback is triggered

### Fix 4 (P3) ✅ — Fallback Recipe Name Alignment (TODO Added)
**File:** `model/recipe_selector.py` — `_FALLBACK_RECIPE_MAP`
- Added TODO comment block warning about potential recipe name mismatch
- Added `logger.warning()` in fallback path logging the recipe name used
- **Action Required:** Verify fallback recipe names against actual `.rul` file output

Current fallback values:
| Step | Fallback Recipe |
|------|----------------|
| ABIT | `RECIPE\PEREGRINE\ON_NEOSEM_ABIT.XML` |
| SFN2 | `RECIPE\PEREGRINE\ON_NEOSEM_SFN2.XML` |
| CNFG | `RECIPE\PEREGRINE\ON_NEOSEM_CNFG.XML` |

### Fix 5 (P3) ⏸️ — Static Fallback Paths (DEFERRED)
**File:** `model/recipe_selector.py` — `_FALLBACK_RECIPE_MAP`
- **NOT implemented** — too risky without production data
- TODO comment added documenting the approach if needed later
- **Prerequisite:** Capture actual `RECIPE_SEL_FILE_COPY_PATHS_##` values from successful subprocess runs

### Fix 6 (P0) 📋 — Verification Required
**No code changes** — this is a verification step to determine if the empty `<AddtionalFileFolder>` is actually correct behavior for certain steps.

#### Verification Steps:
1. **Check with recipe selection script owner:** Does the `ABIT` step on `PEREGRINE` platform produce file copy paths?
2. **Run subprocess manually:**
   ```
   python recipe_selection.py <known-good-tmptravl-path>
   ```
   Check if `RECIPE_SEL_FILE_COPY_PATHS_##` lines appear in stdout.
3. **Check gate condition in `recipe_selection.py`** (line ~1215):
   Does the platform/CFGPN_DATA combination satisfy `platform in R_FOLDER_PLATFORM and bool(CFGPN_DATA)`?
4. **If no file copy paths are expected:** The empty `<AddtionalFileFolder />` is correct behavior and Fix 1 won't change anything (there are simply no paths to process).
5. **If file copy paths ARE expected:** Fix 1 should resolve the issue by correctly parsing the `|` delimiter.

## Files Modified
| File | Changes |
|------|---------|
| `model/orchestrators/checkout_orchestrator.py` | Fix 1, Fix 2 |
| `model/recipe_selector.py` | Fix 3, Fix 4, Fix 5 (TODO only) |

## Files Referenced (Not Modified)
| File | Purpose |
|------|---------|
| `recipe_selection.py` (upstream, outside repo) | Produces `RECIPE_SEL_FILE_COPY_PATHS_##` with `\|` delimiter |
| `ProfileDump/main.py` (CAT original) | Uses `,` delimiter in `Extract()` function |

## Testing Recommendations
1. Run BENTO with a test case where the subprocess recipe selection succeeds
2. Check logs for new DIAG messages to verify:
   - Subprocess was attempted and succeeded
   - `file_copy_paths_count` is > 0
   - `AddtionalFileFolder populated with N File element(s)`
3. Inspect the generated Slate XML to confirm `<File>` elements appear inside `<AddtionalFileFolder>`
4. Test with a case where fallback is used — verify warning logs appear

---

## Production Testing Results (2026-03-31)

### Test Output
```
Recipe: RECIPE\PEREGRINE\ON_NEOSEM_ABIT.XML (success=True)
  file_copy_paths: EMPTY — AddtionalFileFolder will be empty
```

### Root Cause Confirmed
The delimiter mismatch (Fix 1) was **not** the primary issue. The true root cause is a **three-layer problem**:

1. **Layer 1 — Subprocess stdout is empty/unparseable:** The subprocess (`recipe_selection.py` on network share `\\sifsmodauto\modauto\release\ssd\recipe_selection`) runs with returncode=0 but produces **no `RECIPE_SEL_*` lines** in stdout. Possible causes:
   - The network-share version may write results to a file (`--out_file` option) instead of stdout
   - The tmptravl `.dat` format may not be fully compatible
   - Python 2 (`C:\Python27\python.exe`) may encounter a silent error

2. **Layer 2 — Fallback map has no `file_copy_paths`:** The `_FALLBACK_RECIPE_MAP` is `Dict[str, str]` (step→recipe_name only). When fallback is used, `file_copy_paths` is always empty.

3. **Layer 3 — `success=True` masks the fallback:** The fallback sets `success=True` because a recipe name was found. Downstream code logged `success=True` without distinguishing subprocess-success from fallback-success.

### Additional Fixes Applied

#### Fix 7 ✅ — `source` Field on `RecipeResult`
**File:** `model/recipe_selector.py`
- Added `source` field to `RecipeResult` class (`"subprocess"` or `"fallback"`)
- Set `source = "subprocess"` when subprocess parsing succeeds
- Set `source = "fallback"` when fallback map is used

**File:** `model/orchestrators/checkout_orchestrator.py`
- Updated recipe logging to include `source` field: `Recipe: ... (success=True, source=fallback)`
- Added specific warning when `source == "fallback"` and `file_copy_paths` is empty

#### Fix 8 ✅ — `RECIPE_SEL` Line Detection Warning
**File:** `model/recipe_selector.py`
- Added warning when subprocess stdout contains NO `RECIPE_SEL_*` lines
- Verified existing raw stdout/stderr DIAG logging is present

### Updated Files Summary
| File | All Fixes Applied |
|------|-------------------|
| `model/orchestrators/checkout_orchestrator.py` | Fix 1, Fix 2, Fix 7 (source logging) |
| `model/recipe_selector.py` | Fix 3, Fix 4, Fix 5 (TODO), Fix 7 (source field), Fix 8 |

### Remaining Investigation
The **true fix** requires understanding why the subprocess produces no `RECIPE_SEL_*` output:

1. **Run subprocess manually** to capture raw output:
   ```
   C:\Python27\python.exe \\sifsmodauto\modauto\release\ssd\recipe_selection\recipe_selection.py P:\temp\BENTO\CHECKOUT_QUEUE\Traces\T0H074V2E_tmptravl_ABIT.dat
   ```

2. **Check if `--out_file` is needed:** The upstream `recipe_selection.py` has an `--out_file` option (line 1251). If the network-share version requires this flag, BENTO's `RecipeSelector` needs to pass it and then read the output file instead of stdout.

3. **Check platform gate condition:** File copy paths are only generated when `platform in R_FOLDER_PLATFORM and bool(CFGPN_DATA)`. Verify the test unit's `MACHINE_MODEL` is in the `R_FOLDER_PLATFORM` list.

4. **Compare network-share vs local copy:** The local copy at `C:\Users\NISAZULAIKHA\Documents\tools_production_recipe_selection\recipe_selection.py` may differ from the network-share version at `\\sifsmodauto\modauto\release\ssd\recipe_selection\recipe_selection.py`.

---

## FINAL ROOT CAUSE IDENTIFIED (2026-03-31 12:05)

### The Smoking Gun — Subprocess stderr
```
Traceback (most recent call last):
  File "\\sifsmodauto\modauto\release\ssd\recipe_selection\2050f66d\recipe_selection.py", line 1201, in <module>
    rule_results, platform = eval_rules(...)
  File "\\sifsmodauto\modauto\release\ssd\recipe_selection\2050f66d\recipe_selection.py", line 315, in eval_rules
    if tmptravl['MAM']['MARKET_SEGMENT'] == "BX500":
KeyError: 'MARKET_SEGMENT'
```

### Root Cause
The upstream `recipe_selection.py` reads `MARKET_SEGMENT` from the `[MAM]` section of the tmptravl `.dat` file. BENTO's tmptravl generator was **not writing `MARKET_SEGMENT` into the MAM section**, even though SAP returned it as an attribute.

**Data flow (before fix):**
```
SAP → extract_constant_dict() → sap_constant_dict["MARKET_SEGMENT"]
    → tmptravl_constants (constant_dict)
    → only replaces keys OUTSIDE sections ❌
    → MAM section never gets MARKET_SEGMENT
    → recipe_selection.py crashes: KeyError: 'MARKET_SEGMENT'
    → stdout is EMPTY, returncode=0
    → fallback used → file_copy_paths = {} → <AddtionalFileFolder /> empty
```

### Fix 10 ✅ — Inject MARKET_SEGMENT into MAM Section
**File:** `model/orchestrators/checkout_orchestrator.py` (lines 603-614)

Injected `MARKET_SEGMENT` and `MODULE_FORM_FACTOR` from `sap_constant_dict` into `mam_attrs` using `setdefault()`:
- If MAM server already returned these keys, MAM values take precedence
- SAP values are only used as fallback
- Both keys now appear in the `[MAM]` section of the generated tmptravl

**Data flow (after fix):**
```
SAP → extract_constant_dict() → sap_constant_dict["MARKET_SEGMENT"]
    → injected into mam_attrs via setdefault() ✅
    → mam_dict parameter to generate()
    → written inside [MAM] section of tmptravl
    → recipe_selection.py reads tmptravl['MAM']['MARKET_SEGMENT'] ✅
    → subprocess succeeds → file_copy_paths populated → <AddtionalFileFolder> has <File> elements
```

### Complete Fix Chain
| Fix | Priority | What | Status |
|-----|----------|------|--------|
| Fix 1 | P1 | Delimiter `\|` vs `,` support | ✅ Applied |
| Fix 2 | P1 | Warning on dropped entries | ✅ Applied |
| Fix 3 | P2 | Enhanced DIAG logging in recipe_selector | ✅ Applied |
| Fix 4 | P3 | Fallback recipe name TODO | ✅ Applied |
| Fix 5 | P3 | Static fallback paths TODO | ⏸️ Deferred |
| Fix 6 | P0 | Verification steps | 📋 Documented |
| Fix 7 | P1 | `source` field on RecipeResult | ✅ Applied |
| Fix 8 | P1 | RECIPE_SEL line detection warning | ✅ Applied |
| Fix 9 | P1 | Promote DIAG logs to WARNING level | ✅ Applied |
| **Fix 10** | **P0** | **Inject MARKET_SEGMENT into MAM section** | **✅ THE FIX** |

### All Files Modified (Final)
| File | Fixes |
|------|-------|
| `model/orchestrators/checkout_orchestrator.py` | Fix 1, 2, 7, **10** |
| `model/recipe_selector.py` | Fix 3, 4, 5, 7, 8, 9 |

### Expected Result After Fix 10
On next run, the subprocess should:
1. Successfully read `MARKET_SEGMENT` from the tmptravl `[MAM]` section
2. Complete `eval_rules()` without crashing
3. Output `RECIPE_SEL_PROGRAM_RECIPE` and `RECIPE_SEL_FILE_COPY_PATHS_##` to stdout
4. BENTO parses these → `file_copy_paths` is populated → `<AddtionalFileFolder>` contains `<File>` elements
