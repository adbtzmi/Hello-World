# -*- coding: utf-8 -*-
"""
checkout_watcher.py
====================
BENTO Checkout Watcher - Tester Side (Phase 2)

Runs on the TESTER machine.

Usage:
    python checkout_watcher.py --env ABIT
    python checkout_watcher.py --env SFN2
    python checkout_watcher.py --env CNFG
"""
from __future__ import print_function

# ════════════════════════════════════════════════════════════════════════
# CRITICAL: sys.coinit_flags must be set BEFORE pywinauto is imported.
# pywinauto reads this flag during its own import to configure COM
# apartment threading. Setting it here (right after __future__ imports,
# which have no runtime effect) ensures it is set before any other
# module can trigger a pywinauto import.
# ════════════════════════════════════════════════════════════════════════
import sys
sys.coinit_flags = 2          # type: ignore[attr-defined]  # COINIT_APARTMENTTHREADED

import os
import time
import json
import logging
import argparse
import shutil
import threading
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime
# pathlib is NOT available on Python 2.7 — use os.path throughout

# ── Third-party: pywinauto GUI automation ───────────────────────────────────
try:
    from pywinauto import Application, Desktop          # type: ignore[import]
    from pywinauto import keyboard as pw_keyboard       # type: ignore[import]
    _PYWINAUTO_AVAILABLE = True
except ImportError:
    _PYWINAUTO_AVAILABLE = False
    Application  = None  # type: ignore[assignment]
    Desktop      = None  # type: ignore[assignment]
    pw_keyboard  = None  # type: ignore[assignment]

# ── Import shared watcher modules ────────────────────────────────────────────
try:
    from model.watcher.watcher_config import (
        TESTER_REGISTRY,
        LOCK_MAX_AGE_SECONDS,
        LOG_DIR,
    )
    from model.watcher.watcher_lock import (
        LockFile,               # type: ignore[assignment]
        write_status,           # type: ignore[assignment]
        cleanup_stale_locks_on_startup,  # type: ignore[assignment]
    )
except ImportError:
    # Fallback defaults when running standalone (tester machine without model package)
    TESTER_REGISTRY = {
        "ABIT": {"hostname": "IBIR-0383",    "env": "ABIT"},
        "SFN2": {"hostname": "MPT3HVM-0156", "env": "SFN2"},
        "CNFG": {"hostname": "CTOWTST-0031", "env": "CNFG"},
    }
    LOCK_MAX_AGE_SECONDS  = 1800
    LOG_DIR               = r"C:\BENTO\logs"

    class LockFile(object):
        """
        Fallback LockFile for standalone mode (tester machine without model package).
        API matches watcher_lock.LockFile exactly: acquire() / release().
        Uses .checkout_lock suffix (vs .bento_lock in the real implementation).
        """
        def __init__(self, path):
            self.lock_path = path + ".checkout_lock"

        def acquire(self):
            """Return True if lock acquired, False if already held."""
            if os.path.exists(self.lock_path):
                # Check age — auto-expire stale locks (same logic as real LockFile)
                try:
                    age = time.time() - os.path.getmtime(self.lock_path)
                    if age <= LOCK_MAX_AGE_SECONDS:
                        return False   # Lock is valid and held
                    # Stale — remove and proceed
                    os.remove(self.lock_path)
                except Exception:
                    return False
            try:
                with open(self.lock_path, "w") as f:
                    f.write(str(os.getpid()) + "\n")
                    f.write(str(time.time()) + "\n")
                return True
            except Exception:
                return False

        def release(self):
            """Release the lock by deleting the lock file."""
            try:
                if os.path.exists(self.lock_path):
                    os.remove(self.lock_path)
            except Exception:
                pass

    def write_status(path, status, detail=""):
        sp = path + ".checkout_status"
        try:
            with open(sp, "w") as f:
                json.dump({
                    "status":    status,
                    "detail":    detail,
                    "timestamp": datetime.now().isoformat()
                }, f)
        except Exception:
            pass

    def cleanup_stale_locks_on_startup(logger, folder, repo_dirs):
        pass

# Checkout watcher uses its own poll interval — independent of the compilation
# watcher (watcher_config.py POLL_INTERVAL_SECONDS = 60s for 30-min builds).
# Checkout queue files should be picked up within 10s of being dropped.
POLL_INTERVAL_SECONDS = 10


# ── CONFIRMED PATHS ───────────────────────────────────────────────────────────
CHECKOUT_QUEUE_FOLDER   = r"P:\temp\BENTO\CHECKOUT_QUEUE"
CHECKOUT_RESULTS_FOLDER = r"P:\temp\BENTO\CHECKOUT_RESULTS"
SLATE_HOT_FOLDER        = r"C:\test_program\playground_queue"
SLATE_LOG_PATH          = r"C:\test_program\logs\slate_system.log"
SLATE_RESULTS_FOLDER    = r"C:\test_program\results"

MAX_RETRIES        = 20
MAX_PROCESSED_SIZE = 500
HEARTBEAT_EVERY    = 30


# ════════════════════════════════════════════════════════════════════════
# SLATE GUI AUTOMATION — TIMING CONSTANTS
# Tune these values if SLATE is slower/faster on specific tester hardware
# ════════════════════════════════════════════════════════════════════════
T_AFTER_FIELD_SET    = 0.8    # pause after set_edit_text() on any field
T_AFTER_TAB_KEY      = 0.3    # pause after sending TAB (field validation)
T_POPUP_SETTLE       = 0.4    # pause before scanning for popups
T_RUN_RECIPE_SEL     = 5.0    # Run Recipe Selection (hits network/DB)
T_GET_MANUDATA       = 5.0    # Get Manudata (hits manufacturing DB)
T_CREATE_TEMP_TRAVEL = 3.0    # Create Temp Traveler
T_CREATE_PLAYGROUND  = 10.0   # Create Playground — allocates test slots
T_LOAD_RECIPE        = 5.0    # Load Recipe — reads recipe from disk
T_RUN_TEST           = 2.0    # Run Test — just a trigger click

SLATE_TITLE_RE  = r".*SSD Tester Engineering GUI.*"
SLATE_BACKEND   = "win32"     # WinForms app — win32 backend confirmed


# ── LOGGER ────────────────────────────────────────────────────────────────────
log = logging.getLogger(__name__)


def setup_logger(env):
    logger = logging.getLogger("bento_checkout_watcher_" + env)
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)

        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        logger.addHandler(sh)

        try:
            if not os.path.isdir(LOG_DIR):
                os.makedirs(LOG_DIR)
            log_file = os.path.join(
                LOG_DIR,
                "checkout_watcher_" + env
                + "_" + datetime.now().strftime("%Y%m%d") + ".log"
            )
            fh = logging.FileHandler(log_file)
            fh.setFormatter(logging.Formatter(
                "%(asctime)s | %(levelname)-8s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            ))
            logger.addHandler(fh)
        except Exception as e:
            logger.warning("Cannot create log file: " + str(e))

    return logger


# ── PROCESSED SET PRUNING ─────────────────────────────────────────────────────
def _prune_processed(processed, logger):
    if len(processed) > MAX_PROCESSED_SIZE:
        keep    = set(list(processed)[-MAX_PROCESSED_SIZE // 2:])
        removed = len(processed) - len(keep)
        logger.info(
            "Pruned " + str(removed) + " old entries from processed set."
        )
        return keep
    return processed


# ── XML VALIDATION ────────────────────────────────────────────────────────────
def _is_xml_valid(xml_path, logger):
    xml_name = os.path.basename(xml_path)
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        if root is None:
            logger.error("XML empty root: " + xml_name)
            return False
        tja  = root.find("TestJobArchive")
        auto = root.find("AutoStart")
        if tja is None:
            logger.warning("XML missing <TestJobArchive>: " + xml_name)
            return False
        if auto is None:
            logger.warning("XML missing <AutoStart>: " + xml_name)
            return False
        logger.info("XML valid: " + xml_name)
        return True
    except ET.ParseError as e:
        logger.error("XML parse error " + xml_name + ": " + str(e))
        return False
    except Exception as e:
        logger.error("XML validation error " + xml_name + ": " + str(e))
        return False


# ── STEP 8 — Copy XML to SLATE hot folder ────────────────────────────────────
def launch_slate_via_hot_folder(xml_path, logger):
    """
    Copy XML to SLATE hot folder.
    AUTO-CREATES C:\\test_program\\playground_queue if it doesn't exist.
    """
    # ── AUTO-CREATE hot folder ────────────────────────────────────────
    try:
        if not os.path.isdir(SLATE_HOT_FOLDER):
            os.makedirs(SLATE_HOT_FOLDER)
        logger.info("[OK] SLATE hot folder ready: " + SLATE_HOT_FOLDER)
    except Exception as e:
        logger.error(
            "[FAIL] Cannot create SLATE hot folder "
            + SLATE_HOT_FOLDER + ": " + str(e)
        )
        return False

    dest = os.path.join(SLATE_HOT_FOLDER, os.path.basename(xml_path))
    try:
        shutil.copy2(xml_path, dest)
        logger.info("[OK] XML -> SLATE hot folder: " + dest)
        return True
    except Exception as e:
        logger.error("[FAIL] Hot folder copy failed: " + str(e))
        return False


# ════════════════════════════════════════════════════════════════════════
# SLATE GUI AUTOMATION HELPERS
# ════════════════════════════════════════════════════════════════════════

import re as _re

def _find_slate_handle(title_re, backend, logger=None):
    """
    Enumerate all top-level windows and return the handle of the FIRST
    visible window whose title matches title_re.

    WHY THIS EXISTS:
        Application.connect(title_re=...) raises ElementAmbiguousError when
        multiple windows share the same title pattern (e.g. two instances of
        'SSD Tester Engineering GUI' — one visible, one a ghost/hidden copy).
        Connecting by handle is unambiguous and always picks the first match.

    Args:
        title_re : str  — regex pattern (same as SLATE_TITLE_RE)
        backend  : str  — pywinauto backend ("win32" or "uia")
        logger   : optional logging.Logger for debug output

    Returns:
        (handle, title) — int handle and matched window title string
        (None, None)    — if no matching window found
    """
    _l = logger if logger is not None else log
    pattern = _re.compile(title_re)
    try:
        windows = Desktop(backend=backend).windows()  # type: ignore[operator]
        for w in windows:
            try:
                title = w.window_text()
                if title and pattern.match(title):
                    _l.info(
                        "  _find_slate_handle: matched '"
                        + title + "' handle=" + str(w.handle)
                    )
                    return w.handle, title
            except Exception:
                continue
    except Exception as e:
        _l.warning(
            "  _find_slate_handle: Desktop().windows() failed: "
            + type(e).__name__ + ": " + str(e)
        )
    return None, None


def _parse_xml_for_slate_fields(xml_path):
    """
    Parse the checkout XML generated by checkout_orchestrator.generate_slate_xml()
    to extract the values that must be autofilled into SLATE's UI fields.

    XML structure produced by generate_slate_xml():
      <Profile>
        <TestJobArchive>C:\\path\\to\\file.tgz</TestJobArchive>
        <AutoStart>True</AutoStart>
        <MaterialInfo>
          <Attribute Lot="JAANTJB001" MID="XXXXXXX" DutLocation="1,1"/>
          ...
        </MaterialInfo>
        <TempTraveler>
          <Attribute name="MAM/STEP">...</Attribute>
        </TempTraveler>
      </Profile>

    Returns:
      {
        "lot_no":            "JAANTJB001",   # first DUT's Lot value
        "mid":               "XXXXXXX",      # first DUT's MID value
        "test_job_archive":  str(xml_path),  # the XML path itself
      }
      OR None if parsing fails.

    NOTE: txtTestJobArchive in SLATE receives the XML file path (not the
    .tgz path). SLATE reads the XML to find the .tgz internally.
    """
    xml_path = str(xml_path)
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()

        # ── Extract LotNo from first MaterialInfo Attribute ──────────────
        lot_no = ""
        mid    = ""
        mat_info = root.find(".//MaterialInfo")
        if mat_info is not None:
            first_attr = mat_info.find("Attribute")
            if first_attr is not None:
                lot_no = first_attr.get("Lot",  "").strip()
                mid    = first_attr.get("MID",  "").strip()

        # ── Fallback: search any element with Lot/MID attributes ─────────
        if not lot_no or not mid:
            for el in root.iter("Attribute"):
                _lot = el.get("Lot")
                _mid = el.get("MID")
                if not lot_no and _lot:
                    lot_no = _lot.strip()
                if not mid and _mid:
                    mid = _mid.strip()
                if lot_no and mid:
                    break

        log.info("  \U0001f4cb Parsed XML fields -> LotNo='" + lot_no + "' MID='" + mid + "'")
        return {
            "lot_no":           lot_no,
            "mid":              mid,
            "test_job_archive": str(xml_path),
        }

    except ET.ParseError as e:
        log.error("  \u274c _parse_xml_for_slate_fields XML error: " + str(e))
        return None
    except Exception as e:
        log.error("  \u274c _parse_xml_for_slate_fields error: " + str(e))
        return None


def _set_slate_field(win, auto_id, value, required=False):
    """
    Set a WinForms TextBox field in SLATE by auto_id.

    Confirmed auto_ids (from slate_full_controls_win32.txt):
      txtLotNo           — Lot No input
      txtMID             — MID input
      txtTestJobArchive  — Test Job Archive path input

    Strategy A: set_edit_text() — no keyboard simulation, most reliable
    Strategy B: triple_click_input() + type_keys() — keyboard fallback

    Args:
        win      : pywinauto window wrapper (already connected)
        auto_id  : WinForms auto_id string (confirmed from discovery)
        value    : String value to set
        required : If True, return False on failure (critical field)
                   If False, log warning and return True (optional field)

    Returns:
        True  — field was set successfully (or failure was non-critical)
        False — field set failed AND required=True
    """
    label = "[" + auto_id + "]"
    if not value:
        log.debug("    \u23ed " + label + " skipped (empty value)")
        return True

    # ── Strategy A: set_edit_text (preferred — no keyboard events) ───────
    try:
        field = win.child_window(auto_id=auto_id)
        field.wait("visible ready", timeout=10)
        field.set_focus()
        field.set_edit_text(value)
        time.sleep(T_AFTER_FIELD_SET)

        # Send TAB to trigger any SLATE-side validation/onChange events
        pw_keyboard.send_keys("{TAB}")  # type: ignore[union-attr]
        time.sleep(T_AFTER_TAB_KEY)

        # ── Verify the value was accepted ─────────────────────────────────
        actual = field.window_text().strip()
        # Accept if full value matches, or if filename portion matches
        # (SLATE may truncate display of long paths)
        value_ok = (
            value.lower() in actual.lower() or
            os.path.basename(value).lower() in actual.lower() or
            actual.lower() in value.lower()   # SLATE may show short form
        )
        if value_ok:
            log.info("    \u2705 " + label + " = '" + actual + "' (set_edit_text)")
            return True

        log.warning(
            "    \u26a0\ufe0f " + label + " Strategy A: got '" + actual
            + "', expected '" + value + "' — trying Strategy B"
        )

    except Exception as e:
        log.warning("    \u26a0\ufe0f " + label + " Strategy A failed: " + str(e) + " — trying B")

    # ── Strategy B: triple-click to select all, then type ────────────────
    try:
        field = win.child_window(auto_id=auto_id)
        field.wait("visible ready", timeout=10)
        field.set_focus()

        # Select all existing text
        field.triple_click_input()
        time.sleep(0.15)
        pw_keyboard.send_keys("^a")    # type: ignore[union-attr]  # Ctrl+A
        time.sleep(0.10)
        pw_keyboard.send_keys("{DELETE}")  # type: ignore[union-attr]
        time.sleep(0.10)

        # Type the new value
        field.type_keys(value, with_spaces=True, set_foreground=True)
        time.sleep(T_AFTER_FIELD_SET)
        pw_keyboard.send_keys("{TAB}")  # type: ignore[union-attr]
        time.sleep(T_AFTER_TAB_KEY)

        actual = field.window_text().strip()
        value_ok = (
            value.lower() in actual.lower() or
            os.path.basename(value).lower() in actual.lower()
        )
        if value_ok:
            log.info("    \u2705 " + label + " = '" + actual + "' (type_keys)")
            return True
        else:
            log.warning("    \u26a0\ufe0f " + label + " Strategy B: got '" + actual + "'")

    except Exception as e:
        log.warning("    \u26a0\ufe0f " + label + " Strategy B failed: " + str(e))

    # ── Both strategies failed ────────────────────────────────────────────
    if required:
        log.error("    \u274c " + label + " FAILED — required field, aborting")
        return False
    else:
        log.warning("    \u26a0\ufe0f " + label + " FAILED — optional field, continuing")
        return True   # non-critical — do not abort the whole workflow


def _click_slate_button(win, auto_id, label, wait_after, required=False):
    """
    Click a WinForms Button in SLATE by auto_id and wait for processing.

    Confirmed auto_ids (from slate_full_controls_win32.txt):
      btnRunRecipeSelect  — "Run Recipe Selection"
      btnGetManuData      — "Get Manudata"
      btnCreateTempTravel — "Create Temp Traveler"
      btnCreatePlayground — "Create Playground"
      btnLoadRecipe       — "Load Recipe"
      btnRunTest          — "Run Test"

    Args:
        win        : pywinauto window wrapper
        auto_id    : WinForms auto_id (confirmed from discovery)
        label      : Human-readable name for logging
        wait_after : Seconds to wait after click (operation-specific)
        required   : If True, caller should return False on failure

    Returns:
        True  — button found, enabled, and clicked
        False — button not found or not enabled
    """
    try:
        btn = win.child_window(auto_id=auto_id)
        # Wait up to 15s for button to become visible and enabled
        # (some buttons enable only after previous step completes)
        btn.wait("visible enabled", timeout=15)

        log.info("    \U0001f7e2 Clicking '" + label + "' (auto_id='" + auto_id + "')...")
        btn.click_input()
        time.sleep(wait_after)
        log.info("    \u2705 '" + label + "' done — waited " + str(wait_after) + "s")
        return True

    except Exception as e:
        if required:
            log.error(
                "    \u274c '" + label + "' (auto_id='" + auto_id + "') FAILED: " + str(e)
            )
        else:
            log.warning(
                "    \u26a0\ufe0f '" + label + "' (auto_id='" + auto_id
                + "') failed — optional: " + str(e)
            )
        return False


def _dismiss_slate_popups(win):
    """
    Scan all open windows and dismiss any blocking popup/dialog
    that SLATE may show during the automation workflow.

    Dismissible popup title keywords:
      "Error", "Warning", "Confirm", "Overwrite", "Exist",
      "Replace", "Alert", "Question", "Notice"

    Dismiss button priority order:
      "Yes" > "OK" > "Overwrite" > "Replace" > "Continue" > "Close"

    Safety: Never dismisses the main SLATE window itself.
    Uses Desktop(backend="win32") to catch dialogs that are
    children of SLATE's process but separate top-level windows.
    """
    time.sleep(T_POPUP_SETTLE)

    dismiss_title_keywords = [
        "Error", "Warning", "Confirm", "Overwrite",
        "Exist", "Replace", "Alert", "Question", "Notice",
    ]
    dismiss_btn_order = [
        "Yes", "OK", "Overwrite", "Replace", "Continue", "Close",
    ]
    slate_title = win.window_text()

    try:
        all_windows = Desktop(backend="win32").windows()  # type: ignore[operator]
        for w in all_windows:
            try:
                title = w.window_text().strip()

                # Skip: empty titles, the main SLATE window, our own process
                if not title:
                    continue
                if title == slate_title:
                    continue
                if "SSD Tester Engineering GUI" in title:
                    continue

                # Check if this looks like a popup we should dismiss
                if not any(kw.lower() in title.lower()
                           for kw in dismiss_title_keywords):
                    continue

                log.warning("    \u26a0\ufe0f Popup detected: '" + title + "' — dismissing")

                for btn_text in dismiss_btn_order:
                    try:
                        # win32 backend: use class_name="Button", NOT control_type=
                        # control_type= is a UIA-only kwarg and raises with win32
                        btn = w.child_window(
                            title=btn_text,
                            class_name="Button"
                        )
                        if btn.exists(timeout=1) and btn.is_enabled():
                            btn.click_input()
                            time.sleep(0.3)
                            log.info(
                                "    \u2705 Dismissed '" + title
                                + "' with '" + btn_text + "'"
                            )
                            return   # one popup at a time
                    except Exception:
                        continue

            except Exception:
                continue   # window may have closed between iterations

    except Exception as e:
        log.debug("    _dismiss_slate_popups scan error: " + str(e))


def _ensure_slate_tab_active(win):
    """
    Ensure the 'Playground Setup / Start Test' tab is the active tab.

    auto_id="tabSetup" confirmed from slate_full_controls_win32.txt.
    Parent TabControl auto_id="tcFunctions" also confirmed.

    IMPORTANT: win32 backend does NOT support control_type= kwarg.
    Use class_name= for WinForms controls with win32 backend:
      TabPage  → class_name="WindowsForms10.SYSTABCONTROL32.app.0.141b42a_r9_ad1"
                 (varies by .NET version — omit class_name, use auto_id only)
      TabControl → same caveat

    Returns True always — tab may already be active, failure is non-fatal.
    """
    try:
        # Method A: click the TabPage directly by auto_id only (no control_type)
        # win32 backend: control_type= is NOT supported — causes ElementNotFoundError
        tab_page = win.child_window(auto_id="tabSetup")
        if tab_page.exists(timeout=5):
            tab_page.click_input()
            time.sleep(0.4)
            log.info("  \u2705 Tab 'Playground Setup / Start Test' activated (auto_id)")
            return True
    except Exception as e:
        log.debug("  _ensure_slate_tab_active Method A: " + type(e).__name__ + ": " + str(e))

    try:
        # Method B: select via TabControl by auto_id only
        tab_ctrl = win.child_window(auto_id="tcFunctions")
        if tab_ctrl.exists(timeout=3):
            tab_ctrl.select("Playground Setup / Start Test")
            time.sleep(0.4)
            log.info("  \u2705 Tab selected via tcFunctions.select()")
            return True
    except Exception as e:
        log.debug("  _ensure_slate_tab_active Method B: " + type(e).__name__ + ": " + str(e))

    log.warning("  \u26a0\ufe0f Could not click tab — assuming already active")
    return True  # Non-fatal: tab may already be on the right page


def _log_all_visible_windows(logger=None):
    """
    Enumerate ALL top-level windows visible to pywinauto (win32 backend)
    and log their titles and class names.

    Called on connect failure to diagnose title mismatches.
    Safe to call even if Desktop() raises — errors are caught.

    Args:
        logger : optional logging.Logger — if None, uses the module-level `log`.
                 Pass the process-local logger from process_checkout_xml() so
                 output appears in the correct log stream.
    """
    _l = logger if logger is not None else log
    if not _PYWINAUTO_AVAILABLE:
        _l.warning("  _log_all_visible_windows: pywinauto not available")
        return
    _l.error("  \u2500\u2500 Visible top-level windows (win32 backend) \u2500\u2500")
    try:
        windows = Desktop(backend="win32").windows()  # type: ignore[operator]
        found = 0
        for w in windows:
            try:
                title = w.window_text()
                cls   = w.class_name()
                if title:   # skip untitled/taskbar entries
                    _l.error(
                        "    HWND title='" + title
                        + "'  class='" + cls + "'"
                    )
                    found += 1
            except Exception:
                continue
        if found == 0:
            _l.error("    (no titled windows found — Desktop() may be restricted)")
    except Exception as e:
        _l.error(
            "    Desktop().windows() failed: "
            + type(e).__name__ + ": " + str(e)
        )
    _l.error("  \u2500\u2500 End window list \u2500\u2500")


def slate_gui_trigger(xml_path, timeout=60):
    """
    Drive SLATE (SSD Tester Engineering GUI) through the complete
    'Playground Setup / Start Test' workflow via pywinauto GUI automation.

    This function is called in process_checkout_xml() AFTER the XML has
    been copied to C:\\test_program\\playground_queue and BEFORE
    SlateCompletionMonitor.wait() is called.

    ════════════════════════════════════════════════════════
    PHASE 1 — AUTOFILL FIELDS FROM XML
    ════════════════════════════════════════════════════════
    Parses the checkout XML to extract:
      • LotNo  → txtLotNo  (auto_id confirmed)
      • MID    → txtMID    (auto_id confirmed)
      • path   → txtTestJobArchive (auto_id confirmed)

    ════════════════════════════════════════════════════════
    PHASE 2 — BUTTON WORKFLOW (exact UI order, left→right)
    ════════════════════════════════════════════════════════
    All auto_ids confirmed from slate_full_controls_win32.txt:
      Step 1: btnRunRecipeSelect  "Run Recipe Selection" (optional)
      Step 2: btnGetManuData      "Get Manudata"          (optional)
      Step 3: btnCreateTempTravel "Create Temp Traveler"  (optional)
      Step 4: btnCreatePlayground "Create Playground"     (REQUIRED)
      Step 5: btnLoadRecipe       "Load Recipe"           (REQUIRED)
      Step 6: btnRunTest          "Run Test"              (REQUIRED)

    Args:
        xml_path : str or Path — full path to XML already in playground_queue
        timeout  : int  — seconds to wait for SLATE window/elements
                          (used for element waits; connect uses CONNECT_TIMEOUT)

    Returns:
        True  — Run Test was clicked; SlateCompletionMonitor can now watch
        False — a REQUIRED step failed; caller should retry or mark failed

    Raises:
        Nothing — all exceptions are caught and logged; returns False on any error.
    """
    if not _PYWINAUTO_AVAILABLE:
        log.error(
            "\u274c slate_gui_trigger: pywinauto is not installed. "
            "Run: pip install pywinauto"
        )
        return False

    xml_path = str(xml_path)
    xml_name = os.path.basename(xml_path)
    log.info("\U0001f916 slate_gui_trigger -> " + xml_name)

    # ════════════════════════════════════════════════════════
    # CONNECT TO SLATE
    # Use _find_slate_handle() to resolve the window handle FIRST, then
    # connect by handle.  This avoids ElementAmbiguousError when multiple
    # windows match the title regex (e.g. a ghost/hidden second instance).
    # ════════════════════════════════════════════════════════
    CONNECT_TIMEOUT = 10   # seconds — SLATE responds instantly if running

    try:
        handle, matched_title = _find_slate_handle(
            SLATE_TITLE_RE, SLATE_BACKEND, logger=None
        )
        if handle is None:
            raise RuntimeError(
                "No window found matching title_re='" + SLATE_TITLE_RE + "'"
            )
        log.info(
            "  \U0001f50d Found SLATE window: '"
            + matched_title + "' (handle=" + str(handle) + ")"
        )
        app = Application(backend=SLATE_BACKEND).connect(  # type: ignore[operator]
            handle=handle,
            timeout=CONNECT_TIMEOUT
        )
        win = app.window(handle=handle)
        win.set_focus()
        win.wait("ready", timeout=CONNECT_TIMEOUT)
        log.info("  \u2705 Connected: '" + win.window_text() + "'")
    except Exception as e:
        # ── Log the FULL exception type + message so we can diagnose ──────
        log.error(
            "  \u274c Cannot connect to SLATE: "
            + type(e).__name__ + ": " + str(e)
        )
        log.error("     Title regex used: " + SLATE_TITLE_RE)
        log.error("     Backend used    : " + SLATE_BACKEND)
        log.error("     Is 'SSD Tester Engineering GUI' running on this machine?")

        # ── Enumerate ALL visible top-level windows for diagnosis ──────────
        # Uses module-level `log` — slate_gui_trigger() has no logger param
        _log_all_visible_windows(logger=None)

        return False

    # ── Dismiss any startup popups before touching anything ───────────────
    _dismiss_slate_popups(win)

    # ── Navigate to the correct tab ───────────────────────────────────────
    _ensure_slate_tab_active(win)

    # ════════════════════════════════════════════════════════
    # PHASE 1 — AUTOFILL FIELDS FROM XML
    # ════════════════════════════════════════════════════════
    log.info("  \U0001f4cb Phase 1: Autofill fields from XML")

    fields = _parse_xml_for_slate_fields(xml_path)
    if fields is None:
        log.error("  \u274c Phase 1: XML parse failed — cannot autofill fields")
        return False

    # 1a. Set Lot No (optional — SLATE may pre-populate)
    _set_slate_field(
        win,
        auto_id  = "txtLotNo",
        value    = fields["lot_no"],
        required = False          # warning only if fails
    )

    # 1b. Set MID (optional — SLATE may pre-populate)
    _set_slate_field(
        win,
        auto_id  = "txtMID",
        value    = fields["mid"],
        required = False
    )

    # 1c. Set Test Job Archive — REQUIRED
    # This is the XML file path. SLATE reads it to find the .tgz internally.
    if not _set_slate_field(
        win,
        auto_id  = "txtTestJobArchive",
        value    = fields["test_job_archive"],
        required = True           # abort if this fails
    ):
        log.error("  \u274c Phase 1: txtTestJobArchive failed — aborting")
        return False

    # Give SLATE a moment to process the archive path
    time.sleep(0.5)
    _dismiss_slate_popups(win)

    # ════════════════════════════════════════════════════════
    # PHASE 2 — BUTTON WORKFLOW
    # Exact left-to-right order as shown in SLATE UI
    # ════════════════════════════════════════════════════════
    log.info("  \U0001f4cb Phase 2: Button workflow")

    # ── Step 1: Run Recipe Selection (optional) ───────────────────────────
    log.info("  \u2500\u2500 Step 1/6: Run Recipe Selection")
    ok = _click_slate_button(
        win,
        auto_id    = "btnRunRecipeSelect",
        label      = "Run Recipe Selection",
        wait_after = T_RUN_RECIPE_SEL,
        required   = False
    )
    if not ok:
        log.warning("    \u26a0\ufe0f Run Recipe Selection failed — continuing")
    _dismiss_slate_popups(win)

    # ── Step 2: Get Manudata (optional) ──────────────────────────────────
    log.info("  \u2500\u2500 Step 2/6: Get Manudata")
    ok = _click_slate_button(
        win,
        auto_id    = "btnGetManuData",
        label      = "Get Manudata",
        wait_after = T_GET_MANUDATA,
        required   = False
    )
    if not ok:
        log.warning("    \u26a0\ufe0f Get Manudata failed — continuing")
    _dismiss_slate_popups(win)

    # ── Step 3: Create Temp Traveler (optional) ───────────────────────────
    log.info("  \u2500\u2500 Step 3/6: Create Temp Traveler")
    ok = _click_slate_button(
        win,
        auto_id    = "btnCreateTempTravel",
        label      = "Create Temp Traveler",
        wait_after = T_CREATE_TEMP_TRAVEL,
        required   = False
    )
    if not ok:
        log.warning("    \u26a0\ufe0f Create Temp Traveler failed — continuing")
    _dismiss_slate_popups(win)

    # ── Step 4: Create Playground (REQUIRED) ─────────────────────────────
    log.info("  \u2500\u2500 Step 4/6: Create Playground  [REQUIRED]")
    if not _click_slate_button(
        win,
        auto_id    = "btnCreatePlayground",
        label      = "Create Playground",
        wait_after = T_CREATE_PLAYGROUND,
        required   = True
    ):
        log.error("  \u274c Create Playground FAILED — aborting workflow")
        return False
    _dismiss_slate_popups(win)

    # ── Step 5: Load Recipe (REQUIRED) ───────────────────────────────────
    log.info("  \u2500\u2500 Step 5/6: Load Recipe  [REQUIRED]")
    if not _click_slate_button(
        win,
        auto_id    = "btnLoadRecipe",
        label      = "Load Recipe",
        wait_after = T_LOAD_RECIPE,
        required   = True
    ):
        log.error("  \u274c Load Recipe FAILED — aborting workflow")
        return False
    _dismiss_slate_popups(win)

    # ── Step 6: Run Test (REQUIRED — FINAL TRIGGER) ───────────────────────
    log.info("  \u2500\u2500 Step 6/6: Run Test  [FINAL TRIGGER]")
    if not _click_slate_button(
        win,
        auto_id    = "btnRunTest",
        label      = "Run Test",
        wait_after = T_RUN_TEST,
        required   = True
    ):
        log.error("  \u274c Run Test FAILED — test did not start")
        return False

    log.info("  \u2705 \u2705 \u2705 slate_gui_trigger COMPLETE — SLATE test is RUNNING!")
    log.info("      XML: " + xml_name)
    return True


# ── SLATE COMPLETION MONITOR ──────────────────────────────────────────────────
class SlateCompletionMonitor(object):
    """
    3 parallel detection methods + timeout watchdog.
    Uses threading.Event - thread-safe, race-condition free.
    No f-strings - Python 2/3 compatible.
    """

    def __init__(self, xml_path, logger, timeout_hours=8):
        self.xml_path          = xml_path
        self.logger            = logger
        self.timeout_seconds   = timeout_hours * 3600
        self.start_time        = time.time()
        self._complete_event   = threading.Event()
        self._error_event      = threading.Event()
        self.completion_method = "unknown"
        self._method_lock      = threading.Lock()

    def _signal_complete(self, method_name, is_error=False):
        """Thread-safe, idempotent completion signal."""
        with self._method_lock:
            if not self._complete_event.is_set():
                self.completion_method = method_name
                if is_error:
                    self._error_event.set()
                self._complete_event.set()
                self.logger.info(
                    "[SlateMonitor] Completion via: " + method_name
                )

    def _monitor_log(self):
        """METHOD 1: Watch SLATE log file for completion keywords."""
        success_words = ["Test Complete", "Job Complete", "ALL PASS"]
        error_words   = ["FATAL ERROR", "ABORTED", "FAILED"]
        last_size     = 0

        while not self._complete_event.is_set():
            try:
                if os.path.exists(SLATE_LOG_PATH):
                    size = os.path.getsize(SLATE_LOG_PATH)
                    if size != last_size:
                        with open(SLATE_LOG_PATH, "r") as lf:
                            lf.seek(last_size)
                            new_text = lf.read()
                        last_size = size
                        for w in error_words:
                            if w in new_text:
                                self._signal_complete(
                                    "LOG_KEYWORD", is_error=True
                                )
                                return
                        for w in success_words:
                            if w in new_text:
                                self._signal_complete("LOG_KEYWORD")
                                return
            except Exception as e:
                self.logger.warning(
                    "[Monitor1] Log error: " + str(e)
                )
            time.sleep(10)

    def _monitor_output_folder(self):
        """METHOD 2: Watch output folder for stable result files."""
        stable_count    = 0
        required_stable = 3

        while not self._complete_event.is_set():
            try:
                if os.path.isdir(SLATE_RESULTS_FOLDER):
                    files = [
                        f for f in os.listdir(SLATE_RESULTS_FOLDER)
                        if f.endswith((".csv", ".log", ".bin"))
                    ]
                    if files:
                        sizes_now = {}
                        for f in files:
                            sizes_now[f] = os.path.getsize(
                                os.path.join(SLATE_RESULTS_FOLDER, f)
                            )
                        time.sleep(5)
                        sizes_later = {}
                        for f in files:
                            fp = os.path.join(SLATE_RESULTS_FOLDER, f)
                            if os.path.exists(fp):
                                sizes_later[f] = os.path.getsize(fp)
                        if sizes_now == sizes_later:
                            stable_count += 1
                            if stable_count >= required_stable:
                                self._signal_complete("OUTPUT_FILES")
                                return
                        else:
                            stable_count = 0
            except Exception as e:
                self.logger.warning(
                    "[Monitor2] Folder error: " + str(e)
                )
            time.sleep(15)

    def _timeout_watchdog(self):
        """SAFETY: Hard timeout watchdog."""
        deadline = self.start_time + self.timeout_seconds
        while not self._complete_event.is_set():
            if time.time() > deadline:
                self.logger.error(
                    "[TIMEOUT] Checkout exceeded "
                    + str(self.timeout_seconds // 3600) + "h limit!"
                )
                self._signal_complete("TIMEOUT", is_error=True)
                return
            time.sleep(60)

    def wait_for_completion(self):
        """
        Launch all detection methods + watchdog as daemon threads.
        Blocks with heartbeat until any method signals completion.
        Returns True on success, False on error/timeout.
        """
        # Python 2/3 compatible: daemon= kwarg was added in Python 3.3.
        # Set .daemon = True after construction for Python 2 compatibility.
        t_log = threading.Thread(
            target=self._monitor_log,
            name="checkout-monitor-log"
        )
        t_log.daemon = True

        t_folder = threading.Thread(
            target=self._monitor_output_folder,
            name="checkout-monitor-folder"
        )
        t_folder.daemon = True

        t_watchdog = threading.Thread(
            target=self._timeout_watchdog,
            name="checkout-timeout-watchdog"
        )
        t_watchdog.daemon = True

        threads = [t_log, t_folder, t_watchdog]

        for t in threads:
            t.start()

        # Heartbeat loop every 2 minutes
        while not self._complete_event.wait(timeout=120):
            elapsed = int(time.time() - self.start_time)
            self.logger.info(
                "[Heartbeat] Checkout running... "
                + str(elapsed // 3600) + "h "
                + str((elapsed % 3600) // 60) + "m"
                + " (method=" + self.completion_method + ")"
            )

        return not self._error_event.is_set()


# ── MEMORY COLLECTION ─────────────────────────────────────────────────────────
def collect_one_dut(dut_elem, output_dir, logger):
    """
    Memory collection for one DUT.
    Replace with your actual memory_collect.exe call.
    """
    mid     = dut_elem.get("MID", "unknown")
    lot     = dut_elem.get("Lot", "")
    dut_loc = dut_elem.get("DutLocation", "")

    try:
        dut_dir = os.path.join(output_dir, mid)
        if not os.path.isdir(dut_dir):
            os.makedirs(dut_dir)

        # ── INSERT REAL MEMORY COLLECTION CALL HERE ───────────────────
        # import subprocess
        # cmd = [r"C:\test_program\tools\memory_collect.exe",
        #        "--dut-location", dut_loc,
        #        "--mid", mid,
        #        "--output", dut_dir]
        # proc = subprocess.run(cmd, capture_output=True, timeout=300)
        # if proc.returncode != 0:
        #     raise RuntimeError(proc.stderr.decode())
        # ─────────────────────────────────────────────────────────────

        info_file = os.path.join(dut_dir, "memory_info.txt")
        with open(info_file, "w") as f:
            f.write("MID        : " + mid + "\n")
            f.write("Lot        : " + lot + "\n")
            f.write("DUT Loc    : " + dut_loc + "\n")
            f.write("Timestamp  : " + datetime.now().isoformat() + "\n")
            f.write("Status     : PLACEHOLDER\n")

        logger.info("  [OK] Memory collected: " + mid + " @ " + dut_loc)
        return True

    except Exception as e:
        logger.error(
            "  [FAIL] Memory collection for " + mid + ": " + str(e)
        )
        return False


def trigger_memory_collection(xml_path, jira_key, logger, hostname="", env=""):
    """
    Auto-trigger memory collection for ALL DUTs sequentially.
    Results saved to P:\\temp\\BENTO\\CHECKOUT_RESULTS\\HOSTNAME_ENV\\JIRA_KEY\\
    e.g. P:\\temp\\BENTO\\CHECKOUT_RESULTS\\IBIR-0383_ABIT\\TSESSD-14270\\
    """
    try:
        tree = ET.parse(xml_path)
        duts = tree.getroot().findall(".//MaterialInfo/Attribute")
    except Exception as e:
        logger.error("Cannot parse DUT list: " + str(e))
        return {"status": "failed", "detail": str(e), "collected": 0}

    # Build tester-specific subfolder: HOSTNAME_ENV (e.g. IBIR-0383_ABIT)
    tester_folder = ""
    if hostname and env:
        tester_folder = hostname + "_" + env.upper()
    elif hostname:
        tester_folder = hostname
    elif env:
        tester_folder = env.upper()

    if tester_folder:
        output_dir = os.path.join(CHECKOUT_RESULTS_FOLDER, tester_folder, jira_key)
    else:
        output_dir = os.path.join(CHECKOUT_RESULTS_FOLDER, jira_key)

    try:
        if not os.path.isdir(output_dir):
            os.makedirs(output_dir)
        logger.info("[OK] Results folder: " + output_dir)
    except Exception as e:
        logger.warning("Cannot create results folder: " + str(e))

    collected = 0
    failed    = 0

    for dut in duts:
        ok = collect_one_dut(dut, output_dir, logger)
        if ok:
            collected += 1
        else:
            failed += 1

    total  = len(duts)
    status = "success" if failed == 0 else (
        "partial" if collected > 0 else "failed"
    )
    detail = "Collected " + str(collected) + "/" + str(total) + " DUTs"
    if failed:
        detail += " (" + str(failed) + " failed)"

    logger.info("Memory collection done: " + detail)
    return {"status": status, "detail": detail, "collected": collected}


# ── PARSE JIRA FROM FILENAME ──────────────────────────────────────────────────
def _parse_jira_from_xml_name(fname):
    """Extract JIRA key from filename. e.g. checkout_TSESSD-123_... -> TSESSD-123"""
    parts = fname.replace(".xml", "").split("_")
    for p in parts:
        if "-" in p and any(c.isdigit() for c in p):
            return p
    return "UNKNOWN"


# ── PROCESS ONE XML ───────────────────────────────────────────────────────────
def process_checkout_xml(xml_path, env, logger):
    """
    Full pipeline for one checkout XML.

    Steps:
      1. Validate XML integrity
      2. Write in_progress status
      3. Copy XML -> SLATE hot folder (AUTO-CREATED)
      3b. Drive SLATE GUI via slate_gui_trigger() [NEW]
      4. Wait for SLATE completion (parallel methods)
      5. Memory collection for all DUTs (saved to CHECKOUT_RESULTS/HOSTNAME_ENV/JIRA_KEY/)
      6. Write success/failed status -> orchestrator wakes up
    """
    fname    = os.path.basename(xml_path)
    jira_key = _parse_jira_from_xml_name(fname)

    # Resolve hostname for this env from registry (used for results folder)
    hostname = ""
    try:
        hostname = TESTER_REGISTRY[env]["hostname"]
    except (KeyError, TypeError):
        pass

    logger.info("[Process] Starting: " + fname)

    # Step 1: Validate XML
    if not _is_xml_valid(xml_path, logger):
        write_status(xml_path, "failed", "Invalid XML - cannot parse")
        logger.error("[FAIL] Invalid XML: " + fname)
        return

    # Step 2: Write in_progress
    write_status(xml_path, "in_progress", "Checkout started by watcher")

    # Step 3: Copy to SLATE hot folder (auto-creates folder)
    ok = launch_slate_via_hot_folder(xml_path, logger)
    if not ok:
        write_status(
            xml_path, "failed",
            "Cannot copy XML to " + SLATE_HOT_FOLDER
        )
        return

    # ════════════════════════════════════════════════════════════════════
    # Step 3b: Drive SLATE GUI to load and start the test  [NEW]
    # ════════════════════════════════════════════════════════════════════
    dest_xml = os.path.join(SLATE_HOT_FOLDER, os.path.basename(xml_path))

    # Pre-flight: confirm pywinauto is available before attempting GUI automation
    if not _PYWINAUTO_AVAILABLE:
        logger.error(
            "\u274c pywinauto is NOT installed on this machine. "
            "GUI automation cannot run. Install with: pip install pywinauto"
        )
        write_status(xml_path, "failed", "pywinauto not installed")
        return

    MAX_GUI_ATTEMPTS = 3
    gui_success      = False

    for attempt in range(1, MAX_GUI_ATTEMPTS + 1):
        logger.info(
            "\U0001f916 GUI automation attempt "
            + str(attempt) + "/" + str(MAX_GUI_ATTEMPTS) + "..."
        )

        # ── Pre-attempt: log all visible windows so we can diagnose title mismatches ──
        # Only log on first attempt (or after a failure) to avoid log spam
        if attempt == 1:
            logger.info("  Pre-flight: enumerating visible windows...")
            _log_all_visible_windows(logger=logger)

        try:
            gui_success = slate_gui_trigger(dest_xml, timeout=60)
        except Exception as e:
            logger.error(
                "  slate_gui_trigger raised unexpectedly: "
                + type(e).__name__ + ": " + str(e)
            )
            gui_success = False

        if gui_success:
            logger.info(
                "  \u2705 GUI automation succeeded on attempt " + str(attempt)
            )
            break

        if attempt < MAX_GUI_ATTEMPTS:
            logger.warning(
                "  \u26a0\ufe0f Attempt " + str(attempt) + " failed — "
                "waiting 10s before retry " + str(attempt + 1)
            )
            time.sleep(10)

    if not gui_success:
        logger.error(
            "\u274c slate_gui_trigger failed after "
            + str(MAX_GUI_ATTEMPTS) + " attempts — marking as failed"
        )
        write_status(
            xml_path, "failed",
            "slate_gui_trigger failed after "
            + str(MAX_GUI_ATTEMPTS) + " attempts"
        )
        return
    # ════════════════════════════════════════════════════════════════════
    # END Step 3b
    # ════════════════════════════════════════════════════════════════════

    # Step 4: Wait for SLATE completion
    logger.info("[~] Waiting for SLATE completion...")
    monitor = SlateCompletionMonitor(xml_path, logger, timeout_hours=8)
    success = monitor.wait_for_completion()

    if not success:
        write_status(
            xml_path, "failed",
            "SLATE did not complete successfully "
            "(method=" + monitor.completion_method + ")"
        )
        logger.error("[FAIL] SLATE failed/timed out: " + fname)
        return

    logger.info(
        "[OK] SLATE complete via " + monitor.completion_method
        + ": " + fname
    )

    # Step 5: Memory collection
    logger.info("[~] Starting memory collection for all DUTs...")
    mem_result = trigger_memory_collection(
        xml_path, jira_key, logger,
        hostname=hostname, env=env
    )

    # Step 6: Write success status -> orchestrator wakes up
    write_status(
        xml_path, "success",
        "Checkout complete via " + monitor.completion_method + ". "
        + "MemCollect: " + mem_result["status"]
        + " (" + mem_result["detail"] + ")"
    )
    logger.info("[OK] Status written: success - " + fname)


def _cleanup_all_checkout_locks_on_startup(logger):
    """
    Remove ALL .checkout_lock files unconditionally at startup.

    WHY UNCONDITIONAL (not age-gated):
        When the watcher process starts, it is the ONLY process that creates
        .checkout_lock files. Any lock that exists at startup was left behind
        by a previous watcher process that is now dead (killed, crashed, or
        restarted). There is NO legitimate reason for a lock to exist before
        this watcher has processed anything.

        The age-gated cleanup (_cleanup_stale_checkout_locks) is correct for
        the POLL LOOP — where a lock created seconds ago by THIS watcher run
        must not be removed. But at startup, age is irrelevant: all locks are
        orphans.

    This fixes the "Could not acquire lock (race condition)" loop where a
    fresh lock (only minutes old) from the previous watcher run survives the
    age-gated cleanup and blocks every subsequent poll.
    """
    if not os.path.isdir(CHECKOUT_QUEUE_FOLDER):
        return
    removed = 0
    try:
        for fname in os.listdir(CHECKOUT_QUEUE_FOLDER):
            if not fname.endswith(".checkout_lock"):
                continue
            lock_path = os.path.join(CHECKOUT_QUEUE_FOLDER, fname)
            try:
                age = int(time.time() - os.path.getmtime(lock_path))
                os.remove(lock_path)
                removed += 1
                logger.warning(
                    "[Startup] Removed orphaned lock (age="
                    + str(age) + "s): " + fname
                )
            except Exception as e:
                logger.warning(
                    "[Startup] Could not remove lock " + fname + ": " + str(e)
                )
    except Exception as e:
        logger.warning("[Startup] Lock scan error: " + str(e))
    if removed:
        logger.info(
            "[Startup] Removed " + str(removed)
            + " orphaned .checkout_lock file(s) from previous watcher run."
        )
    else:
        logger.info("[Startup] No orphaned lock files found.")


def _cleanup_stale_checkout_locks(logger):
    """
    Remove .checkout_lock files older than LOCK_MAX_AGE_SECONDS from CHECKOUT_QUEUE.
    Called every poll cycle to unblock files whose lock was never released due
    to a mid-run crash of the CURRENT watcher process.

    NOTE: This is age-gated (30 min threshold) because it runs while the watcher
    is active — a lock created seconds ago by THIS process must not be removed.
    For startup cleanup (previous process locks), use
    _cleanup_all_checkout_locks_on_startup() instead.
    """
    if not os.path.isdir(CHECKOUT_QUEUE_FOLDER):
        return
    now = time.time()
    removed = 0
    try:
        for fname in os.listdir(CHECKOUT_QUEUE_FOLDER):
            if not fname.endswith(".checkout_lock"):
                continue
            lock_path = os.path.join(CHECKOUT_QUEUE_FOLDER, fname)
            try:
                age = now - os.path.getmtime(lock_path)
                if age > LOCK_MAX_AGE_SECONDS:
                    os.remove(lock_path)
                    removed += 1
                    logger.warning(
                        "[Cleanup] Removed stale lock (age="
                        + str(int(age)) + "s): " + fname
                    )
            except Exception as e:
                logger.warning(
                    "[Cleanup] Could not remove lock " + fname + ": " + str(e)
                )
    except Exception as e:
        logger.warning("[Cleanup] Lock scan error: " + str(e))
    if removed:
        logger.info(
            "[Cleanup] Removed " + str(removed) + " stale .checkout_lock file(s)."
        )


def _cleanup_stale_checkout_status_on_startup(logger):
    """
    [Parity with watcher_main.py P8] Reset orphaned .checkout_status files
    stuck at 'in_progress' after a watcher crash or kill.

    WHY THIS EXISTS:
        If the watcher is killed mid-checkout (CTRL+C, power loss), the
        .checkout_status sidecar is left stuck at 'in_progress'.
        The orchestrator (checkout_orchestrator.py) polls this file and will
        wait indefinitely — or until its own timeout — even though no checkout
        is actually running.

        By resetting stale in_progress files to 'failed' on startup, the
        orchestrator immediately detects the failure and can report it.

    STALE THRESHOLD:
        Uses LOCK_MAX_AGE_SECONDS (1800s = 30 min) — same as the checkout
        timeout. A status file older than 30 min still at 'in_progress' means
        the process that wrote it is certainly no longer running.
    """
    if not os.path.isdir(CHECKOUT_QUEUE_FOLDER):
        return

    reset_count = 0
    logger.info("Startup: scanning for orphaned in_progress checkout status files...")

    try:
        for fname in os.listdir(CHECKOUT_QUEUE_FOLDER):
            if not fname.endswith(".checkout_status"):
                continue

            status_path = os.path.join(CHECKOUT_QUEUE_FOLDER, fname)
            try:
                age = time.time() - os.path.getmtime(status_path)
                if age <= LOCK_MAX_AGE_SECONDS:
                    continue  # Recent enough — may still be active

                with open(status_path, "r") as sf:
                    data = json.load(sf)

                if data.get("status") != "in_progress":
                    continue  # Not in_progress — leave it alone

                # Stale in_progress — reset to failed
                data["status"]          = "failed"
                data["detail"]          = (
                    "Reset by watcher startup cleanup: "
                    "previous watcher was killed mid-checkout "
                    "(status age=" + str(int(age)) + "s, "
                    "threshold=" + str(LOCK_MAX_AGE_SECONDS) + "s)"
                )
                data["reset_timestamp"] = datetime.now().isoformat()

                with open(status_path, "w") as sf:
                    json.dump(data, sf, indent=2)

                logger.warning(
                    "Startup: reset stale in_progress -> failed: "
                    + fname + " (age=" + str(int(age)) + "s)"
                )
                reset_count += 1

            except Exception as e:
                logger.warning(
                    "Startup: could not process status file "
                    + fname + ": " + str(e)
                )

    except OSError as e:
        logger.warning("Startup: could not scan for status files: " + str(e))

    if reset_count > 0:
        logger.warning(
            "Startup: reset " + str(reset_count)
            + " orphaned in_progress checkout status file(s)."
        )
    else:
        logger.info("Startup: no orphaned checkout status files found.")


# ── MAIN WATCH LOOP ───────────────────────────────────────────────────────────
def watch(env, logger):
    """
    Main poll loop — exact structural mirror of watcher_main.py watch().

    Key design decisions copied from watcher_main.py:
      - pre_existing set (path-based) to skip XMLs present at startup
      - LockFile class (not raw open("x")) for per-file locking
      - Lock failure = silent debug skip, ZERO retry penalty
        (only process_checkout_xml failure increments retry_counts)
      - cfg resolved ONCE before the loop
      - sorted(os.listdir()) for FIFO processing order
      - Full traceback in all exception handlers
    """
    # ── Resolve this tester's config once ────────────────────────────────────
    cfg      = TESTER_REGISTRY.get(env, {})
    hostname = cfg.get("hostname", "")

    # ── Startup cleanup ───────────────────────────────────────────────────────
    # [P8-parity] Reset orphaned in_progress .checkout_status files left by a
    # killed watcher — so the orchestrator detects failure immediately.
    _cleanup_stale_checkout_status_on_startup(logger)

    # Remove ALL .checkout_lock files unconditionally at startup.
    # Any lock that exists now was left by a dead previous watcher process.
    # Age-gated cleanup (_cleanup_stale_checkout_locks) is only for the poll loop.
    _cleanup_all_checkout_locks_on_startup(logger)

    # Cleanup stale locks on startup (shared watcher_lock module)
    try:
        cleanup_stale_locks_on_startup(logger, CHECKOUT_QUEUE_FOLDER, {})
    except Exception as e:
        logger.warning("Startup cleanup error: " + str(e))

    # ── Snapshot pre-existing XMLs (path-based, clock-skew safe) ─────────────
    # Record the exact set of XML paths that exist RIGHT NOW and skip only
    # those. Any XML that arrives after this snapshot is processed normally.
    # This replaces the broken mtime vs watcher_start_time comparison which
    # fails when the local PC clock and tester clock differ.
    pre_existing = set()
    if os.path.isdir(CHECKOUT_QUEUE_FOLDER):
        for _f in os.listdir(CHECKOUT_QUEUE_FOLDER):
            if _f.lower().endswith(".xml") and ".checkout_" not in _f:
                pre_existing.add(
                    os.path.join(CHECKOUT_QUEUE_FOLDER, _f)
                )
        logger.info(
            "Startup: ignoring "
            + str(len(pre_existing))
            + " pre-existing XML(s) already in queue."
        )

    logger.info(
        "Polling " + CHECKOUT_QUEUE_FOLDER
        + " every " + str(POLL_INTERVAL_SECONDS) + "s..."
    )

    # ── State ─────────────────────────────────────────────────────────────────
    processed       = set()   # completed XMLs (success or permanent fail)
    retry_counts    = {}      # xml_path -> int, retry attempt counter
    heartbeat_count = 0
    HEARTBEAT_EVERY = 30      # log "still watching" every 30 polls = 5 min at 10s/poll

    # ── Main poll loop ────────────────────────────────────────────────────────
    while True:
        try:
            # Guard: shared drive may become temporarily unreachable
            if not os.path.isdir(CHECKOUT_QUEUE_FOLDER):
                logger.warning(
                    "CHECKOUT_QUEUE not accessible: "
                    + CHECKOUT_QUEUE_FOLDER
                    + " — will retry in " + str(POLL_INTERVAL_SECONDS) + "s"
                )
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            # Heartbeat + processed set pruning
            heartbeat_count += 1
            if heartbeat_count >= HEARTBEAT_EVERY:
                heartbeat_count = 0
                logger.info(
                    "[Heartbeat] Watcher alive. "
                    "processed=" + str(len(processed))
                    + " retrying=" + str(len(retry_counts))
                )
                processed = _prune_processed(processed, logger)

            # Self-heal stale locks every poll — clears locks left by a
            # crashed watcher within one poll cycle, not 30 minutes.
            _cleanup_stale_checkout_locks(logger)

            # ── Scan for new XMLs (sorted = FIFO by timestamp in filename) ──
            for fname in sorted(os.listdir(CHECKOUT_QUEUE_FOLDER)):
                if not fname.lower().endswith(".xml"):
                    continue
                if ".checkout_" in fname:
                    continue    # Skip .checkout_status / .checkout_lock sidecars

                xml_path = os.path.join(CHECKOUT_QUEUE_FOLDER, fname)

                # Skip if already finished (success or permanent fail)
                if xml_path in processed:
                    continue

                # Skip XMLs that were already present when watcher started
                if xml_path in pre_existing:
                    continue   # Silently skip — not a new file

                # Skip XMLs not addressed to this tester/env combination.
                # Orchestrator embeds BOTH hostname AND env in the filename:
                #   checkout_TSESSD-14270_IBIR-0383_ABIT_20260325_094809_passing.xml
                env_tag      = "_" + env + "_"
                hostname_tag = ("_" + hostname + "_") if hostname else ""
                fname_upper  = fname.upper()

                if env_tag.upper() not in fname_upper:
                    logger.debug(
                        "[SKIP] env_tag '" + env_tag
                        + "' not found in: " + fname
                    )
                    continue
                if hostname_tag and hostname_tag.upper() not in fname_upper:
                    logger.debug(
                        "[SKIP] hostname_tag '" + hostname_tag
                        + "' not found in: " + fname
                    )
                    continue

                # Per-XML file lock — prevents two watcher instances from
                # claiming the same XML simultaneously.
                # IMPORTANT: lock failure = silent skip, NO retry penalty.
                # (mirrors watcher_main.py exactly — only process failure retries)
                lock = LockFile(xml_path)
                if not lock.acquire():
                    logger.debug(
                        "[SKIP] Locked by another process: " + fname
                    )
                    continue

                try:
                    # Check retry limit AFTER acquiring lock
                    retries = retry_counts.get(xml_path, 0)
                    if retries >= MAX_RETRIES:
                        logger.error(
                            "[GIVE UP] " + fname
                            + " — exceeded MAX_RETRIES=" + str(MAX_RETRIES)
                            + ". Marking as failed."
                        )
                        write_status(
                            xml_path, "failed",
                            "Watcher gave up after "
                            + str(MAX_RETRIES) + " retry attempts."
                        )
                        processed.add(xml_path)
                        retry_counts.pop(xml_path, None)
                        continue

                    logger.info(
                        "[+] Processing: " + fname
                        + " (attempt=" + str(retries + 1) + ")"
                    )

                    try:
                        process_checkout_xml(xml_path, env, logger)
                        # Success — permanent, do not reprocess
                        processed.add(xml_path)
                        retry_counts.pop(xml_path, None)
                        logger.info("[OK] " + fname)

                    except Exception as exc:
                        # Temporary failure — retry next poll
                        count = retry_counts.get(xml_path, 0) + 1
                        retry_counts[xml_path] = count
                        logger.error(
                            "[RETRY " + str(count) + "/" + str(MAX_RETRIES)
                            + "] " + fname + ": " + str(exc)
                            + "\n" + traceback.format_exc()
                        )
                        try:
                            write_status(
                                xml_path, "failed",
                                "Watcher exception (attempt "
                                + str(count) + "): " + str(exc)
                            )
                        except Exception:
                            pass

                finally:
                    lock.release()

        except Exception as loop_err:
            logger.error(
                "[!] Watch loop error: " + str(loop_err)
                + "\n" + traceback.format_exc()
            )

        time.sleep(POLL_INTERVAL_SECONDS)


# ── ENTRY POINT ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="BENTO Checkout Watcher"
    )
    parser.add_argument(
        "--env",
        required=True,
        help="Tester environment (ABIT, SFN2, CNFG)"
    )
    args   = parser.parse_args()
    env    = args.env.upper()
    logger = setup_logger(env)

    logger.info("=" * 60)
    logger.info("BENTO Checkout Watcher starting up")
    logger.info("ENV           : " + env)
    logger.info("CHECKOUT_QUEUE: " + CHECKOUT_QUEUE_FOLDER)
    logger.info("HOT_FOLDER    : " + SLATE_HOT_FOLDER
                + " (auto-created if missing)")
    logger.info("Poll interval : " + str(POLL_INTERVAL_SECONDS) + "s")
    logger.info("Max retries   : " + str(MAX_RETRIES))
    logger.info("pywinauto     : " + ("available" if _PYWINAUTO_AVAILABLE else "NOT INSTALLED"))
    logger.info("=" * 60)

    try:
        watch(env, logger)
    except KeyboardInterrupt:
        logger.info("Watcher stopped by user.")
        sys.exit(0)
    except Exception as e:
        import traceback
        logger.error(
            "Fatal error: " + str(e)
            + "\n" + traceback.format_exc()
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
