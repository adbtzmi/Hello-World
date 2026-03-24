# Phase 3A Testing Guide

## Quick Start Testing

### Prerequisites
1. Ensure Phase 2 is working (all existing tabs functional)
2. Have valid credentials ready (email, JIRA token, Bitbucket token, Model API key)
3. Have a test JIRA issue key (e.g., TSESSD-1234)

---

## Test Sequence

### Test 1: Configuration Section ✅

**Steps:**
1. Launch BENTO application
2. Navigate to 🏠 Home tab
3. Verify Configuration section displays:
   - JIRA URL field (pre-filled)
   - Bitbucket URL field (pre-filled)
   - Bitbucket Project field (pre-filled)
   - JIRA Project field (pre-filled)
   - Model Gateway field (pre-filled)
   - Enable Debug Mode checkbox
   - Save Config button
   - Test Config button

**Expected Results:**
- All fields are visible and editable
- Pre-filled values match settings.json
- Buttons are clickable

**Test Actions:**
1. Click "Save Config" → should log "✓ Configuration saved"
2. Click "Test Config" → should show dialog with connectivity results
3. Check "Enable Debug Mode" → should show yellow debug indicator below
4. Uncheck "Enable Debug Mode" → should hide debug indicator

---

### Test 2: Credentials Section ✅

**Steps:**
1. Verify Credentials section displays:
   - Email field
   - JIRA Token field (with 👁 button)
   - Bitbucket Token field (with 👁 button)
   - Model API Key field (with 👁 button)
   - Load Credentials button
   - Save button

**Expected Results:**
- All token fields show asterisks (****)
- Eye buttons are visible next to each token field

**Test Actions:**
1. Enter test email: `test@micron.com`
2. Enter test tokens (any text)
3. Click eye button → should show plain text
4. Click eye button again → should hide text (asterisks)
5. Click "Save" button:
   - Should prompt for password in console (temporary limitation)
   - Enter password: `test123`
   - Confirm password: `test123`
   - Should create `credential` file in root directory
   - Should log "✓ Credentials saved successfully"

6. Clear all fields
7. Click "Load Credentials" button:
   - Should prompt for password in console
   - Enter password: `test123`
   - Should populate all fields
   - Should log "✓ Credentials loaded successfully"
   - Should show success dialog

---

### Test 3: Debug Mode Indicator ✅

**Steps:**
1. Ensure Debug Mode checkbox is unchecked
2. Verify no debug indicator is visible
3. Check Debug Mode checkbox

**Expected Results:**
- Yellow label with red text "🐛 DEBUG MODE" appears below Task Details section
- Log shows "🐛 DEBUG MODE ENABLED"

**Test Actions:**
1. Uncheck Debug Mode → indicator should disappear
2. Check Debug Mode → indicator should reappear
3. Verify analyzer.ai_client.debug flag is updated (check in subsequent AI calls)

---

### Test 4: Task Details Section ✅

**Steps:**
1. Verify Task Details section displays:
   - JIRA Issue field (pre-filled with project prefix)
   - Repository combobox
   - Base Branch combobox
   - Feature Branch field (with placeholder text)
   - Load Workflow button
   - Start Full Analysis Workflow button

**Expected Results:**
- JIRA Issue field shows "TSESSD-" prefix
- All fields are editable
- Buttons are clickable

**Test Actions:**
1. Enter issue key: `TSESSD-1234`
2. Click "Load Workflow" button:
   - Should open file dialog
   - Navigate to Workflows folder
   - Select a workflow file (if exists)
   - Should populate fields from workflow
   - Should show success dialog

3. Click "Start Full Analysis Workflow" button:
   - Should validate issue key
   - Should call jira_controller.analyze_issue()
   - Should start workflow (if credentials are valid)

---

### Test 5: Integration with Existing Tabs ✅

**Steps:**
1. Navigate to each Phase 2 tab:
   - 📋 Fetch Issue
   - 🤖 Analyze JIRA
   - 📦 Repository
   - 🧪 Test Scenarios

**Expected Results:**
- All tabs still work correctly
- No errors in log
- No visual glitches

**Test Actions:**
1. Verify issue key syncs from Home tab to other tabs
2. Verify repository syncs from Home tab to other tabs
3. Verify branch syncs from Home tab to other tabs

---

## Error Scenarios

### Test 6: Invalid Input Handling ✅

**Test Cases:**
1. Click "Start Full Analysis Workflow" with empty issue key
   - **Expected:** Error dialog "Please enter a valid JIRA issue key"

2. Click "Start Full Analysis Workflow" with incomplete issue key (e.g., "TSESSD-")
   - **Expected:** Error dialog "Please enter a valid JIRA issue key"

3. Click "Save" credentials with empty fields
   - **Expected:** Error dialog "All credential fields are required"

4. Click "Load Credentials" with wrong password
   - **Expected:** Error dialog "Failed to load credentials"

5. Click "Test Config" with invalid URLs
   - **Expected:** Test results show failures for unreachable services

---

## Performance Testing

### Test 7: Responsiveness ✅

**Test Cases:**
1. Click "Test Config" → should not freeze UI
   - **Expected:** Button remains responsive, test runs in background

2. Click "Load Credentials" → should not freeze UI
   - **Expected:** Prompt appears immediately

3. Click "Load Workflow" → should not freeze UI
   - **Expected:** File dialog opens immediately

---

## Regression Testing

### Test 8: Phase 2 Functionality ✅

**Test Cases:**
1. Navigate to 📋 Fetch Issue tab
   - Enter issue key
   - Click "Fetch Issue"
   - **Expected:** Issue data displays correctly

2. Navigate to 🤖 Analyze JIRA tab
   - Enter issue key
   - Click "Analyze with AI"
   - **Expected:** Analysis runs correctly

3. Navigate to 📦 Repository tab
   - Enter repository details
   - Click "Clone Repository"
   - **Expected:** Repository clones correctly

4. Navigate to 🧪 Test Scenarios tab
   - Enter issue key
   - Click "Generate Test Scenarios"
   - **Expected:** Test scenarios generate correctly

---

## Known Issues & Workarounds

### Issue 1: Console Password Prompt
**Description:** Password prompts appear in console instead of GUI dialog

**Workaround:** 
- Run BENTO from terminal/command prompt
- Enter password when prompted
- Future enhancement: Create Tkinter password dialog

**Impact:** Low (functional but not ideal UX)

---

### Issue 2: Test Config Basic Check
**Description:** Test Config only checks connectivity, not authentication

**Workaround:**
- Use "Start Full Analysis Workflow" to test actual API calls
- Future enhancement: Add authentication test

**Impact:** Low (connectivity test is still useful)

---

## Success Criteria Checklist

- [ ] All Configuration fields are visible and functional
- [ ] Save Config saves to settings.json
- [ ] Test Config tests connectivity to all services
- [ ] Debug Mode checkbox shows/hides indicator
- [ ] All Credentials fields are visible and functional
- [ ] Password visibility toggles work
- [ ] Save Credentials creates encrypted file
- [ ] Load Credentials populates fields
- [ ] Load Workflow opens file dialog and populates fields
- [ ] Start Full Analysis Workflow validates and starts workflow
- [ ] Debug indicator shows/hides correctly
- [ ] No regressions in Phase 2 tabs
- [ ] No syntax errors or exceptions
- [ ] UI remains responsive during background operations

---

## Reporting Issues

If you encounter any issues during testing:

1. **Check the log panel** for error messages
2. **Check the console** for stack traces
3. **Note the exact steps** to reproduce
4. **Capture screenshots** if visual issues
5. **Check file:** `Logs/BentoLog_<date>_<time>hrs.log`

**Report Format:**
```
Issue: [Brief description]
Steps to Reproduce:
1. [Step 1]
2. [Step 2]
Expected: [What should happen]
Actual: [What actually happened]
Log Output: [Relevant log lines]
```

---

## Next Steps After Testing

Once all tests pass:

1. ✅ Mark Phase 3A as COMPLETE
2. 📝 Document any issues found
3. 🔧 Fix critical issues
4. ➡️ Proceed to Sub-Phase 3B (Validation Tab)

---

## Quick Smoke Test (5 minutes)

For rapid verification:

1. Launch BENTO
2. Go to Home tab
3. Check Debug Mode → verify indicator appears
4. Uncheck Debug Mode → verify indicator disappears
5. Click Test Config → verify dialog appears
6. Enter credentials → click Save → verify success
7. Click Load Credentials → verify fields populate
8. Enter issue key → click Start Workflow → verify it starts
9. Navigate to other tabs → verify they still work
10. Check log for errors → should be none

**If all 10 steps pass:** ✅ Phase 3A is working correctly!

---

## Conclusion

This testing guide covers all functionality added in Phase 3A. Follow the test sequence systematically to ensure complete coverage. Report any issues found and proceed to Sub-Phase 3B once all tests pass.

**Happy Testing! 🧪**
