# -*- coding: utf-8 -*-
"""
test_connect.py
===============
Standalone diagnostic script — run this DIRECTLY on IBIR-0383 (or any tester)
to verify that pywinauto can see and connect to the SLATE window.

Usage (from CMD on the tester machine):
    python test_connect.py

What it checks:
  1. Python version
  2. pywinauto installed + version
  3. sys.coinit_flags value (must be 2 before pywinauto import)
  4. All visible top-level windows (title + class)
  5. Direct Application.connect() to SLATE using the exact same
     title regex and backend as checkout_watcher.py
  6. If connected: dumps all child controls of the SLATE window
     so you can verify auto_ids (txtLotNo, btnRunTest, etc.)

CRITICAL: sys.coinit_flags = 2 MUST be the first executable line.
          It is set here before any other import.
"""
# ── MUST be first — before ANY other import ──────────────────────────────────
import sys
sys.coinit_flags = 2   # type: ignore[attr-defined]  # COINIT_APARTMENTTHREADED

# ── Standard library ─────────────────────────────────────────────────────────
import os
import time

# ── Constants (must match checkout_watcher.py exactly) ───────────────────────
SLATE_TITLE_RE = r".*SSD Tester Engineering GUI.*"
SLATE_BACKEND  = "win32"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — Python version
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 60)
print("BENTO SLATE Connection Diagnostic")
print("=" * 60)
print("")
print("[1] Python version : " + sys.version)
print("    Executable     : " + sys.executable)
print("    coinit_flags   : " + str(getattr(sys, "coinit_flags", "NOT SET")))
print("")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — pywinauto import
# ─────────────────────────────────────────────────────────────────────────────
print("[2] Importing pywinauto...")
try:
    from pywinauto import Application, Desktop
    import pywinauto
    print("    OK  pywinauto version: " + pywinauto.__version__)
except ImportError as e:
    print("    FAIL  pywinauto not installed: " + str(e))
    print("")
    print("    Fix: pip install pywinauto")
    print("")
    sys.exit(1)
except Exception as e:
    print("    FAIL  Unexpected import error: " + type(e).__name__ + ": " + str(e))
    sys.exit(1)

print("")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — Enumerate all visible top-level windows
# ─────────────────────────────────────────────────────────────────────────────
print("[3] Enumerating all visible top-level windows (backend=win32)...")
print("    " + "-" * 54)
try:
    all_windows = Desktop(backend=SLATE_BACKEND).windows()
    found = 0
    for w in all_windows:
        try:
            title = w.window_text()
            cls   = w.class_name()
            if title:
                print("    HWND  title='" + title + "'")
                print("          class='" + cls + "'")
                found += 1
        except Exception:
            continue
    if found == 0:
        print("    WARNING: No titled windows found.")
        print("    This may mean Desktop() cannot enumerate windows in this session.")
        print("    Ensure the script runs in the SAME Windows session as SLATE (not SSH).")
    else:
        print("    " + "-" * 54)
        print("    Total titled windows found: " + str(found))
except Exception as e:
    print("    FAIL  Desktop().windows() raised: " + type(e).__name__ + ": " + str(e))

print("")

# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — Try to connect to SLATE
# ─────────────────────────────────────────────────────────────────────────────
print("[4] Connecting to SLATE...")
print("    Title regex : " + SLATE_TITLE_RE)
print("    Backend     : " + SLATE_BACKEND)
print("    Timeout     : 10s")
print("")

import re as _re

def _find_slate_handle(title_re, backend):
    """
    Enumerate all top-level windows and return the handle of the FIRST
    visible window whose title matches title_re.

    This avoids ElementAmbiguousError when multiple windows share the same
    title pattern (e.g. two instances of SSD Tester Engineering GUI).
    Returns (handle, title) or (None, None) if not found.
    """
    pattern = _re.compile(title_re)
    try:
        windows = Desktop(backend=backend).windows()
        for w in windows:
            try:
                title = w.window_text()
                if title and pattern.match(title):
                    return w.handle, title
            except Exception:
                continue
    except Exception as e:
        print("    WARNING: Desktop().windows() failed: " + str(e))
    return None, None

try:
    handle, matched_title = _find_slate_handle(SLATE_TITLE_RE, SLATE_BACKEND)
    if handle is None:
        raise RuntimeError(
            "No window found matching title_re='" + SLATE_TITLE_RE + "'"
        )
    print("    Found SLATE window: '" + matched_title + "' (handle=" + str(handle) + ")")
    app = Application(backend=SLATE_BACKEND).connect(handle=handle, timeout=10)
    win = app.window(handle=handle)
    actual_title = win.window_text()
    print("    SUCCESS  Connected to: '" + actual_title + "'")
    print("")

    # ─────────────────────────────────────────────────────────────────────
    # STEP 5 — Dump child controls to verify auto_ids
    # ─────────────────────────────────────────────────────────────────────
    print("[5] Dumping child controls (auto_id, class, title)...")
    print("    " + "-" * 54)
    print("    {:30s} {:35s} {}".format("auto_id", "class_name", "title"))
    print("    " + "-" * 54)

    IMPORTANT_IDS = {
        "txtLotNo", "txtMID", "txtTestJobArchive",
        "btnRunRecipeSelect", "btnGetManuData", "btnCreateTempTravel",
        "btnCreatePlayground", "btnLoadRecipe", "btnRunTest",
        "tabSetup", "tcFunctions",
    }

    found_ids = set()   # initialise before try so it is always bound
    try:
        children = win.children()
        for child in children:
            try:
                aid   = child.element_info.auto_id or ""
                cls   = child.element_info.class_name or ""
                title = child.window_text() or ""
                marker = " <-- CONFIRMED" if aid in IMPORTANT_IDS else ""
                if aid or title:
                    print("    {:30s} {:35s} {}{}".format(
                        aid[:30], cls[:35], title[:40], marker
                    ))
                if aid in IMPORTANT_IDS:
                    found_ids.add(aid)
            except Exception:
                continue
    except Exception as e:
        print("    FAIL  win.children() raised: " + type(e).__name__ + ": " + str(e))
        print("    Trying win.print_control_identifiers() instead...")
        try:
            win.print_control_identifiers()  # type: ignore[attr-defined]
        except Exception as e2:
            print("    FAIL  print_control_identifiers: " + str(e2))

    print("    " + "-" * 54)
    print("")

    # Report which important auto_ids were found vs missing
    missing = IMPORTANT_IDS - found_ids
    if found_ids:
        print("    Confirmed auto_ids found : " + ", ".join(sorted(found_ids)))
    if missing:
        print("    WARNING — auto_ids NOT found: " + ", ".join(sorted(missing)))
        print("    These controls may be on a different tab or have different IDs.")
    else:
        print("    All expected auto_ids confirmed present.")

    print("")

    # ─────────────────────────────────────────────────────────────────────
    # STEP 6 — Test set_focus() and wait("ready")
    # ─────────────────────────────────────────────────────────────────────
    print("[6] Testing win.set_focus() and win.wait('ready', timeout=5)...")
    try:
        win.set_focus()
        win.wait("ready", timeout=5)
        print("    SUCCESS  Window is ready and focused.")
    except Exception as e:
        print("    FAIL  " + type(e).__name__ + ": " + str(e))

except Exception as e:
    print("    FAIL  Application.connect() raised:")
    print("          " + type(e).__name__ + ": " + str(e))
    print("")
    print("    Possible causes:")
    print("      A) SLATE ('SSD Tester Engineering GUI') is not running")
    print("      B) Title regex does not match — check Step 3 output above")
    print("      C) COM threading conflict — verify sys.coinit_flags=2 is FIRST")
    print("      D) Script is running in a different Windows session than SLATE")
    print("         (e.g. running via SSH while SLATE is on the console session)")

print("")
print("=" * 60)
print("Diagnostic complete.")
print("=" * 60)
