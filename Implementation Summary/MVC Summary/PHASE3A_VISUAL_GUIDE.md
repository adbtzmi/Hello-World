# Phase 3A: Home Tab — Visual Guide

## Before Phase 3A (Phase 2 State)

```
┌─────────────────────────────────────────────────────────────┐
│ 🏠 Home Tab                                                  │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│ ┌─ Configuration ──────────────────────────────────────┐   │
│ │ JIRA URL:        [https://micron.atlassian.net     ] │   │
│ │ Bitbucket URL:   [https://bitbucket.micron.com/... ] │   │
│ │ Bitbucket Proj:  [TESTSSD]  JIRA Proj: [TSESSD]     │   │
│ │ Model Gateway:   [https://model-gateway...         ] │   │
│ │ ☐ Enable Debug Mode                                  │   │
│ │ [Save Config]                                         │   │
│ └───────────────────────────────────────────────────────┘   │
│                                                              │
│ ┌─ Task Details - Full Workflow ──────────────────────┐   │
│ │ JIRA Issue:      [TSESSD-                          ] │   │
│ │ Repository:      [▼                                ] │   │
│ │ Base Branch:     [▼                                ] │   │
│ │ Feature Branch:  [If empty, will automatically...  ] │   │
│ │ [🚀 Start Full Analysis Workflow]                    │   │
│ └───────────────────────────────────────────────────────┘   │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**Missing:**
- ❌ Credentials section
- ❌ Test Config button
- ❌ Load Workflow button
- ❌ Debug indicator
- ❌ Password visibility toggles

---

## After Phase 3A (Current State)

```
┌─────────────────────────────────────────────────────────────┐
│ 🏠 Home Tab                                                  │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│ ┌─ Configuration ──────────────────────────────────────┐   │
│ │ JIRA URL:        [https://micron.atlassian.net     ] │   │
│ │ Bitbucket URL:   [https://bitbucket.micron.com/... ] │   │
│ │ Bitbucket Proj:  [TESTSSD]  JIRA Proj: [TSESSD]     │   │
│ │ Model Gateway:   [https://model-gateway...         ] │   │
│ │ ☐ Enable Debug Mode                                  │   │
│ │ [Save Config] [Test Config] ← NEW!                   │   │
│ └───────────────────────────────────────────────────────┘   │
│                                                              │
│ ┌─ Credentials (Encrypted) ───────────────────────────┐   │ ← NEW SECTION!
│ │ Email:           [test@micron.com                  ] │   │
│ │ JIRA Token:      [********************] [👁]        │   │ ← NEW!
│ │ Bitbucket Token: [********************] [👁]        │   │ ← NEW!
│ │ Model API Key:   [********************] [👁]        │   │ ← NEW!
│ │ [Load Credentials] [Save]                            │   │ ← NEW!
│ └───────────────────────────────────────────────────────┘   │
│                                                              │
│ ┌─ Task Details - Full Workflow ──────────────────────┐   │
│ │ JIRA Issue:      [TSESSD-1234                      ] │   │
│ │ Repository:      [▼ adv_ibir_master                ] │   │
│ │ Base Branch:     [▼ develop                        ] │   │
│ │ Feature Branch:  [feature/TSESSD-1234              ] │   │
│ │ [📂 Load Workflow] [🚀 Start Full Analysis Workflow] │   │ ← NEW!
│ └───────────────────────────────────────────────────────┘   │
│                                                              │
│ ┌──────────────────────────────────────────────────────┐   │ ← NEW!
│ │ 🐛 DEBUG MODE                                        │   │ ← NEW!
│ └──────────────────────────────────────────────────────┘   │ ← NEW!
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**Added:**
- ✅ Credentials section (4 fields + 2 buttons)
- ✅ Test Config button
- ✅ Load Workflow button
- ✅ Debug indicator (shows when enabled)
- ✅ Password visibility toggles (👁 buttons)

---

## Feature Breakdown

### 1. Configuration Section (Enhanced)

```
┌─ Configuration ──────────────────────────────────────┐
│ JIRA URL:        [https://micron.atlassian.net     ] │
│ Bitbucket URL:   [https://bitbucket.micron.com/... ] │
│ Bitbucket Proj:  [TESTSSD]  JIRA Proj: [TSESSD]     │
│ Model Gateway:   [https://model-gateway...         ] │
│ ☐ Enable Debug Mode                                  │
│ [Save Config] [Test Config] ← NEW!                   │
└───────────────────────────────────────────────────────┘
```

**New Features:**
- `[Test Config]` button tests connectivity to:
  - ✅ JIRA (reachability check)
  - ✅ Bitbucket (reachability check)
  - ✅ Model Gateway (reachability check)

**Behavior:**
- Click → Background thread tests all services
- Shows dialog with results:
  ```
  Configuration Test Results:
  
  ✓ JIRA: Reachable
  ✓ BITBUCKET: Reachable
  ✓ MODEL_GATEWAY: Reachable
  ```

---

### 2. Credentials Section (NEW!)

```
┌─ Credentials (Encrypted) ───────────────────────────┐
│ Email:           [test@micron.com                  ] │
│ JIRA Token:      [********************] [👁]        │
│ Bitbucket Token: [********************] [👁]        │
│ Model API Key:   [********************] [👁]        │
│ [Load Credentials] [Save]                            │
└───────────────────────────────────────────────────────┘
```

**Features:**
- Email input (plain text)
- 3 token inputs (masked with asterisks)
- 3 eye buttons (toggle visibility)
- Load button (from encrypted file)
- Save button (to encrypted file)

**Behavior:**
- `[👁]` button → Shows/hides password
- `[Save]` button → Prompts for password → Encrypts → Saves to `credential` file
- `[Load Credentials]` button → Prompts for password → Decrypts → Populates fields

**Security:**
- Uses Fernet encryption (from cryptography library)
- Password-based key derivation (PBKDF2)
- Credentials never stored in plain text

---

### 3. Task Details Section (Enhanced)

```
┌─ Task Details - Full Workflow ──────────────────────┐
│ JIRA Issue:      [TSESSD-1234                      ] │
│ Repository:      [▼ adv_ibir_master                ] │
│ Base Branch:     [▼ develop                        ] │
│ Feature Branch:  [feature/TSESSD-1234              ] │
│ [📂 Load Workflow] [🚀 Start Full Analysis Workflow] │
└───────────────────────────────────────────────────────┘
```

**New Features:**
- `[📂 Load Workflow]` button loads workflow file

**Behavior:**
- Click → Opens file dialog
- Select workflow file (e.g., `TSESSD-1234_workflow.txt`)
- Populates fields:
  - Issue key
  - Repository
  - Base branch
  - Feature branch
- Shows success dialog

---

### 4. Debug Mode Indicator (NEW!)

```
┌──────────────────────────────────────────────────────┐
│ 🐛 DEBUG MODE                                        │
└──────────────────────────────────────────────────────┘
```

**Features:**
- Yellow background
- Red text
- Bold font
- Initially hidden

**Behavior:**
- Shows when "Enable Debug Mode" is checked
- Hides when "Enable Debug Mode" is unchecked
- Updates `analyzer.ai_client.debug` flag

**Purpose:**
- Visual feedback that debug mode is active
- Prevents accidental debug mode in production
- Logs all AI requests/responses when enabled

---

## User Workflows

### Workflow 1: First-Time Setup

```
1. Launch BENTO
   ↓
2. Go to Home tab
   ↓
3. Enter credentials:
   - Email: john.doe@micron.com
   - JIRA Token: [paste token]
   - Bitbucket Token: [paste token]
   - Model API Key: [paste key]
   ↓
4. Click [Save]
   - Enter password: mypassword123
   - Confirm password: mypassword123
   ↓
5. Click [Test Config]
   - Verify all services reachable
   ↓
6. Ready to use!
```

---

### Workflow 2: Load Existing Credentials

```
1. Launch BENTO
   ↓
2. Go to Home tab
   ↓
3. Click [Load Credentials]
   - Enter password: mypassword123
   ↓
4. Credentials auto-populate
   ↓
5. Ready to use!
```

---

### Workflow 3: Resume Previous Work

```
1. Launch BENTO
   ↓
2. Go to Home tab
   ↓
3. Click [Load Workflow]
   - Select: TSESSD-1234_workflow.txt
   ↓
4. Fields auto-populate:
   - Issue: TSESSD-1234
   - Repo: adv_ibir_master
   - Branch: develop
   ↓
5. Click [🚀 Start Full Analysis Workflow]
   ↓
6. Workflow resumes from last checkpoint
```

---

### Workflow 4: Debug Mode

```
1. Launch BENTO
   ↓
2. Go to Home tab
   ↓
3. Check ☑ Enable Debug Mode
   ↓
4. Yellow indicator appears:
   ┌──────────────────────────────────────┐
   │ 🐛 DEBUG MODE                        │
   └──────────────────────────────────────┘
   ↓
5. All AI calls now logged in detail
   ↓
6. Uncheck ☐ Enable Debug Mode
   ↓
7. Indicator disappears
```

---

## Controller Architecture

```
┌─────────────────────────────────────────────────────────┐
│ view/tabs/home_tab.py                                   │
│ (Presentation Layer)                                    │
├─────────────────────────────────────────────────────────┤
│ • Displays UI elements                                  │
│ • Handles user input                                    │
│ • Delegates to controllers                              │
│ • Thread-safe callbacks                                 │
└─────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│ controller/bento_controller.py                          │
│ (Master Controller)                                     │
├─────────────────────────────────────────────────────────┤
│ • Wires all sub-controllers                             │
│ • Manages controller lifecycle                          │
│ • Provides unified interface                            │
└─────────────────────────────────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ config_      │ │ credential_  │ │ workflow_    │
│ controller   │ │ controller   │ │ controller   │
├──────────────┤ ├──────────────┤ ├──────────────┤
│ • test_      │ │ • load_      │ │ • load_      │
│   config()   │ │   credentials│ │   workflow_  │
│              │ │ • save_      │ │   file()     │
│ • _test_     │ │   credentials│ │              │
│   jira()     │ │ • toggle_    │ │              │
│ • _test_     │ │   debug_mode │ │              │
│   bitbucket()│ │              │ │              │
│ • _test_     │ │              │ │              │
│   model_     │ │              │ │              │
│   gateway()  │ │              │ │              │
└──────────────┘ └──────────────┘ └──────────────┘
        │               │               │
        └───────────────┼───────────────┘
                        ▼
┌─────────────────────────────────────────────────────────┐
│ jira_analyzer.py                                        │
│ (Model Layer)                                           │
├─────────────────────────────────────────────────────────┤
│ • CredentialManager (encryption/decryption)             │
│ • AIGatewayClient (AI API calls)                        │
│ • JIRAAnalyzer (business logic)                         │
└─────────────────────────────────────────────────────────┘
```

---

## Data Flow

### Save Credentials Flow

```
User clicks [Save]
    ↓
home_tab._save_credentials()
    ↓
credential_controller.save_credentials()
    ↓
[Background Thread]
    ↓
Prompt for password (console)
    ↓
CredentialManager.save_credentials()
    ↓
Encrypt with Fernet
    ↓
Write to 'credential' file
    ↓
[Callback to UI Thread]
    ↓
Show success dialog
```

---

### Load Credentials Flow

```
User clicks [Load Credentials]
    ↓
home_tab._load_credentials()
    ↓
credential_controller.load_credentials()
    ↓
[Background Thread]
    ↓
Prompt for password (console)
    ↓
CredentialManager.load_credentials()
    ↓
Decrypt with Fernet
    ↓
[Callback to UI Thread]
    ↓
Populate fields
    ↓
Update analyzer
    ↓
Show success dialog
```

---

### Test Config Flow

```
User clicks [Test Config]
    ↓
home_tab._test_config()
    ↓
config_controller.test_config()
    ↓
[Background Thread]
    ↓
_test_jira() → urllib.request.urlopen()
    ↓
_test_bitbucket() → urllib.request.urlopen()
    ↓
_test_model_gateway() → urllib.request.urlopen()
    ↓
[Callback to UI Thread]
    ↓
Show results dialog
```

---

## Summary

Phase 3A transforms the Home Tab from a basic configuration screen into a comprehensive control center with:

✅ **Secure credential management**
✅ **Configuration testing**
✅ **Workflow resumption**
✅ **Debug mode visualization**
✅ **Enhanced user experience**

All while maintaining:
✅ **Full backward compatibility**
✅ **Clean MVC architecture**
✅ **Thread-safe operations**
✅ **Comprehensive error handling**

**Status:** ✅ COMPLETE — READY FOR TESTING
