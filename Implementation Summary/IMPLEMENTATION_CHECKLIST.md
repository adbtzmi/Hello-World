# BENTO GUI - Implementation Checklist

## ✅ All Features Implemented

### Core Feature: Parallel Multi-Tester Compilation
- [x] Added `compile_tp_package_multi()` to compilation_orchestrator.py
- [x] Replaced Combobox with multi-select Listbox in GUI
- [x] Updated `_resolve_tester()` to return list of targets
- [x] Updated `_refresh_tester_dropdown()` for listbox
- [x] Updated `_refresh_tester_mode()` with multi-tester badge
- [x] Updated `_run_compile()` to route single vs. multi
- [x] Added `_handle_single_compile_result()` method
- [x] Added `_handle_multi_compile_results()` method
- [x] Updated compile button text to "Compile on Selected Tester(s)"

### UX Improvement #1: Preflight Checklist Dialog
- [x] Redesigned "Add Tester" dialog with checklist-first approach
- [x] Added three mandatory checkboxes at top of dialog
- [x] "Add Tester" button disabled until all checkboxes ticked
- [x] Setup guide button prominently placed in checklist section
- [x] Visual hierarchy: Checklist → Details → Button
- [x] Updated dialog size to 560×480 to accommodate checklist

### UX Improvement #2: Browse Buttons for Paths
- [x] Added `_browse_directory()` helper method
- [x] Added 📁 button for Local Repo Path
- [x] Added 📁 button for RAW_ZIP Path
- [x] Added 📁 button for RELEASE_TGZ Path

### UX Improvement #3: Remove Tester Button
- [x] Added 🗑 Remove button to GUI
- [x] Added `_remove_selected_tester()` method
- [x] Supports removing multiple selected testers
- [x] Shows confirmation dialog with list
- [x] Updates registry and saves to JSON

### UX Improvement #4: Live Progress Indication
- [x] Added `_phase()` helper to compilation_orchestrator.py
- [x] Updated `create_tp_zip()` to send phase updates
- [x] Updated `wait_for_build()` to send phase updates every 15s
- [x] Wrapped log callback in `_run_compile()` to detect `__PHASE__:` prefix
- [x] Status label updates: "Zipping..." → "Waiting..." → "Building... (Xs)"

### UX Improvement #5: TGZ Label Persistence
- [x] Added `compile.last_tgz_label` to settings.json structure
- [x] Updated `save_config()` to save label
- [x] Updated implementation tab to load last label on startup
- [x] Auto-saves label when compile runs

### UX Improvement #6: Centred Dialog Positioning
- [x] Added `_centre_dialog()` helper method
- [x] Fixed "Add New Tester" dialog positioning (520×380)
- [x] Fixed compile error dialog positioning (600×420)
- [x] Fixed interactive chat window positioning (900×700)
- [x] Fixed chat continuation window positioning (800×600)
- [x] Added `transient(self.root)` to all dialogs

## Testing Checklist

### Parallel Compilation
- [ ] Select single tester → compile → verify works as before
- [ ] Select two testers (Shift+Click) → compile → verify both complete
- [ ] Select three testers → compile → verify all complete
- [ ] Test with one valid + one invalid tester → verify partial success handling
- [ ] Search for tester → verify multi-selection preserved during filter

### Preflight Checklist
- [ ] Open "Add Tester" dialog → verify checklist appears at top
- [ ] Verify "Add Tester" button is disabled initially
- [ ] Try to click "Add Tester" → verify nothing happens (disabled)
- [ ] Tick first checkbox → verify button still disabled
- [ ] Tick second checkbox → verify button still disabled
- [ ] Tick all three checkboxes → verify button becomes enabled
- [ ] Untick one checkbox → verify button becomes disabled again
- [ ] Tick all three → fill in hostname/env → add tester → verify success
- [ ] Click "Open Setup Guide" → verify PDF opens (if exists)
- [ ] Click 📁 for Local Repo Path → verify folder picker opens
- [ ] Click 📁 for RAW_ZIP Path → verify folder picker opens
- [ ] Click 📁 for RELEASE_TGZ Path → verify folder picker opens
- [ ] Select folder → verify path updates in text field

### Remove Tester
- [ ] Add test tester → verify appears in list
- [ ] Select tester → click 🗑 Remove → verify confirmation dialog
- [ ] Confirm removal → verify tester removed from list
- [ ] Verify bento_testers.json updated
- [ ] Select multiple testers → remove → verify batch removal

### Live Progress
- [ ] Start compile → verify status shows "Zipping repository..."
- [ ] Wait → verify status shows "Waiting for tester..."
- [ ] Wait → verify status shows "Building... (Xs elapsed)"
- [ ] Complete → verify status shows "Done: filename.tgz"

### Label Persistence
- [ ] Set TGZ label to "test_label"
- [ ] Restart application
- [ ] Verify label field shows "test_label"

### Dialog Positioning
- [ ] Open "Add Tester" dialog → verify centred on parent
- [ ] Move main window → open dialog → verify still centred
- [ ] Click parent window → verify dialog stays on top
- [ ] Test on multi-monitor setup → verify dialog appears on correct screen
- [ ] Trigger compile error → verify error dialog centred
- [ ] Open chat window → verify centred

## Files Modified

1. **compilation_orchestrator.py**
   - Added `_phase()` helper
   - Added `compile_tp_package_multi()`
   - Updated `create_tp_zip()` with phase updates
   - Updated `wait_for_build()` with phase updates

2. **main.py**
   - Replaced Combobox with Listbox (multi-select)
   - Added 📁 browse buttons for paths
   - Added 🗑 Remove button
   - Added `_browse_directory()` helper
   - Added `_remove_selected_tester()` method
   - Added `_centre_dialog()` helper
   - Updated `_resolve_tester()` to return list
   - Updated `_refresh_tester_dropdown()` for listbox
   - Updated `_refresh_tester_mode()` with multi-tester badge
   - Updated `_run_compile()` with phase detection
   - Added `_handle_single_compile_result()`
   - Added `_handle_multi_compile_results()`
   - Updated all dialog positioning
   - Updated TGZ label persistence

3. **settings.json** (auto-updated)
   - Added `compile.last_tgz_label` field

## No Changes Required

- **tester_watcher.py**: Works as-is (natural routing)
- **bento_testers.json**: Format unchanged
- **Workflow files**: No migration needed

## Verification

Run diagnostics:
```bash
# No syntax errors
python -m py_compile compilation_orchestrator.py
python -m py_compile main.py
```

All checks passed: ✅
