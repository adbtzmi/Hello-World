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
import glob
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
        "ABIT": {
            "hostname": "IBIR-0383",
            "env": "ABIT",
            "workspace_folder": r"C:\Tanisys\DNA2\User\Workspace",
            "workspace_mode": "flat",   # subfolders directly under workspace
        },
        "SFN2": {
            "hostname": "MPT3HVM-0156",
            "env": "SFN2",
            "workspace_folder": r"C:\test_program\Rack0",
            "workspace_mode": "primitive_dut",  # Primitive*/DUT* subfolders
        },
        "CNFG": {
            "hostname": "CTOWTST-0031",
            "env": "CNFG",
            "workspace_folder": r"C:\Tanisys\DNA2\User\Workspace",
            "workspace_mode": "flat",
        },
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

    def write_status(path, status, detail="", collected_files=None,
                     output_folder=""):
        sp = path + ".checkout_status"
        try:
            payload = {
                "status":    status,
                "detail":    detail,
                "timestamp": datetime.now().isoformat()
            }
            if collected_files:
                payload["collected_files"] = collected_files
            if output_folder:
                payload["output_folder"] = output_folder
            with open(sp, "w") as f:
                json.dump(payload, f)
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
SLATE_WORKSPACE_FOLDER  = r"C:\Tanisys\DNA2\User\Workspace"
SLATE_LOG_PATH          = r"C:\test_program\logs\slate_system.log"
SLATE_RESULTS_FOLDER    = r"C:\test_program\results"
SLATE_PLAYGROUND_FOLDER = r"C:\test_program\playground"

MAX_RETRIES        = 20
MAX_PROCESSED_SIZE = 500
HEARTBEAT_EVERY    = 30

# ── MANIFEST-BASED FILE SELECTION ─────────────────────────────────────────────
# Allow-list of files that MAY be collected from the tester workspace.
# Each entry defines:
#   key       — unique string identifier (used in manifest & selection JSON)
#   subpath   — relative path inside the workspace folder (supports glob *)
#   required  — if True, always copied regardless of user selection
#   desc      — human-readable description shown in BENTO GUI
#
# The watcher scans the workspace for these files after test completion,
# writes a manifest JSON listing which ones actually exist, and waits for
# BENTO to write back a selection JSON before copying.
# ──────────────────────────────────────────────────────────────────────────────
FILE_ALLOW_LIST = [
    {
        "key":      "results_db",
        "subpath":  "OS/resultsManager.db",
        "required": True,
        "desc":     "Results database (SQLite) — primary test results",
    },
    {
        "key":      "tracefile",
        "subpath":  "Tracefile.txt",
        "required": False,
        "desc":     "Trace log — detailed test execution trace (MPT3 only)",
    },
    {
        "key":      "dispatcher_debug",
        "subpath":  "DispatcherDebug*.txt",
        "required": False,
        "desc":     "Dispatcher debug log — slot dispatch details",
    },
    {
        "key":      "summary_txt",
        "subpath":  "OS/summary.txt",
        "required": False,
        "desc":     "Test summary text — pass/fail overview",
    },
    {
        "key":      "summary_zip",
        "subpath":  "OS/summary.zip",
        "required": True,
        "desc":     "Summary archive — compressed summary package",
    },
    {
        "key":      "update_attrs_xml",
        "subpath":  "OS/*_UPDATE_ATTRS.xml",
        "required": False,
        "desc":     "Update attributes XML — MAM attribute updates",
    },
    {
        "key":      "test_log",
        "subpath":  "OS/test_log.txt",
        "required": False,
        "desc":     "Test log — high-level test execution log",
    },
    {
        "key":      "error_log",
        "subpath":  "OS/error_log.txt",
        "required": False,
        "desc":     "Error log — captured errors during test",
    },
]

# Timeout for BENTO to respond with a selection JSON (seconds).
# If no selection arrives within this window, only required files are copied.
MANIFEST_SELECTION_TIMEOUT = 300   # 5 minutes
MANIFEST_POLL_INTERVAL     = 5    # check every 5 seconds


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
T_LOAD_PROFILE       = 5.0    # Load Profile — GUI processes the XML profile
T_FILE_DIALOG_SETTLE = 2.0    # pause for file dialog to fully render
T_AFTER_MENU_CLICK   = 1.0    # pause after clicking a menu item

SLATE_TITLE_RE  = r".*SSD Tester Engineering GUI.*"
SLATE_BACKEND   = "win32"     # WinForms app — win32 backend confirmed


# ── LOGGER ────────────────────────────────────────────────────────────────────
log = logging.getLogger(__name__)


def setup_logger(env):
    # Configure BOTH root logger and module logger
    logger = logging.getLogger("bento_checkout_watcher_" + env)
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        log.setLevel(logging.DEBUG)  # Ensure __main__ logger is enabled

        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        logger.addHandler(sh)
        log.addHandler(sh)  # Add handler directly to module logger

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
            log.addHandler(fh)
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


# (launch_slate_via_hot_folder removed — XML is loaded directly from
#  CHECKOUT_QUEUE_FOLDER, no intermediate copy to playground_queue needed)


# ════════════════════════════════════════════════════════════════════════
# SLATE GUI AUTOMATION HELPERS
# ════════════════════════════════════════════════════════════════════════

import re as _re


def _safe_desktop_windows(backend="win32"):
    """
    Safely enumerate all top-level desktop windows.

    Desktop(backend=...).windows() can throw
        "Handle XXXXX is not a valid window handle"
    when a window is destroyed between Win32 EnumWindows and pywinauto's
    wrapper creation.  This helper retries up to 3 times, then falls back
    to ctypes EnumWindows + individual HwndWrapper wrapping so that a
    single stale handle doesn't abort the entire scan.

    Returns a list of pywinauto window wrapper objects (may be empty).
    """
    # Fast path: try Desktop().windows() up to 3 times
    for _attempt in range(3):
        try:
            return Desktop(backend=backend).windows()  # type: ignore[operator]
        except Exception:
            time.sleep(0.3)

    # Fallback: enumerate handles via ctypes, wrap individually
    import ctypes
    import ctypes.wintypes

    _handles = []

    @ctypes.WINFUNCTYPE(
        ctypes.wintypes.BOOL,
        ctypes.wintypes.HWND,
        ctypes.wintypes.LPARAM,
    )
    def _enum_cb(hwnd, _lparam):
        _handles.append(hwnd)
        return True

    ctypes.windll.user32.EnumWindows(_enum_cb, 0)

    result = []
    _IsWindow = ctypes.windll.user32.IsWindow
    for hwnd in _handles:
        if not _IsWindow(hwnd):
            continue
        try:
            from pywinauto.controls.hwndwrapper import HwndWrapper  # type: ignore[import]
            result.append(HwndWrapper(hwnd))
        except Exception:
            continue  # stale handle — skip silently

    return result


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
        windows = _safe_desktop_windows(backend=backend)
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


def _parse_xml_for_slate_fields(xml_path, logger=None):
    """
    Parse the checkout XML generated by checkout_orchestrator.generate_slate_xml()
    to extract the values that must be autofilled into SLATE's UI fields.

    XML structure produced by generate_slate_xml():
      <Profile>
        <TestJobArchive>C:\\path\\to\\file.tgz</TestJobArchive>
        <AutoStart>True</AutoStart>
        <MaterialInfo>
          <Attribute Lot="JAANTJB001" MID="XXXXXXX" DutLocation="1,0,32"/>
          ...
        </MaterialInfo>
        <TempTraveler>
          <Attribute section="MAM" attr="STEP" value="ABIT"/>
          <Attribute section="CFGPN" attr="STEP_ID" value="AMB_BI_TEST"/>
          ...
        </TempTraveler>
      </Profile>

    Old XML format (still supported for backward compatibility):
      <TempTraveler>
        <Attribute Section="MAM" Name="STEP" Value="ABIT"/>
      </TempTraveler>

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

        # ── Extract RecipeFile ───────────────────────────────────────────
        recipe_file = ""
        rf_elem = root.find("RecipeFile")
        if rf_elem is not None and rf_elem.text:
            recipe_file = rf_elem.text.strip()

        # ── Extract TempTraveler ───────────────────────────────────────────
        temp_traveler = []
        tt_elem = root.find("TempTraveler")
        if tt_elem is not None:
            for attr in tt_elem.findall("Attribute"):
                temp_traveler.append({
                    "Section": attr.get("section", "") or attr.get("Section", ""),
                    "Name": attr.get("attr", "") or attr.get("Name", ""),
                    "Value": attr.get("value", "") or attr.get("Value", "")
                })

        # ── Ensure MODULE FGPN is present to prevent Lot Cache errors ──────
        has_module_fgpn = any(a.get("Name") == "MODULE FGPN" for a in temp_traveler)
        if not has_module_fgpn:
            temp_traveler.append({
                "Section": "MAM",
                "Name": "MODULE FGPN",
                "Value": "UNKNOWN"
            })


        # ── Extract DutLocations ───────────────────────────────────────────
        dut_locations = []
        mat_info = root.find(".//MaterialInfo")
        if mat_info is not None:
            for attr in mat_info.findall("Attribute"):
                dl = attr.get("DutLocation")
                if dl:
                    dut_locations.append(dl)

        log.info("  \U0001f4cb Parsed XML fields -> LotNo='" + lot_no + "' MID='" + mid + "'")
        return {
            "lot_no":           lot_no,
            "mid":              mid,
            "recipe_file":      recipe_file,
            "temp_traveler":    temp_traveler,
            "dut_locations":    dut_locations,
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


def _set_slate_combobox(win, auto_id, value, required=False):
    """
    Set a WinForms ComboBox field in SLATE by auto_id.
    """
    label = "[" + auto_id + "]"
    if not value:
        log.debug("    \u23ed " + label + " skipped (empty value)")
        return True

    try:
        cb = win.child_window(auto_id=auto_id)
        cb.wait("visible ready", timeout=10)
        cb.set_focus()

        # Try to select by text if possible
        try:
            cb.select(value)
            log.info("    \u2705 " + label + " selected '" + value + "' via combobox.select()")
        except Exception:
            # Fallback to typing it in and hitting enter (if combobox is editable)
            cb.type_keys(value, with_spaces=True, set_foreground=True)
            time.sleep(T_AFTER_FIELD_SET)
            pw_keyboard.send_keys("{ENTER}")  # type: ignore[union-attr]
            log.info("    \u2705 " + label + " typed '" + value + "' via type_keys()")

        time.sleep(T_AFTER_TAB_KEY)
        return True

    except Exception as e:
        if required:
            log.error("    \u274c " + label + " FAILED to set combobox: " + str(e))
            return False
        else:
            log.warning("    \u26a0\ufe0f " + label + " FAILED to set combobox (optional): " + str(e))
            return True

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


# ── Fatal popup sentinel ─────────────────────────────────────────────────────
# When _dismiss_slate_popups() encounters a fatal popup (e.g. "Failed to
# Create Lot Cache"), it stores the full error text here so that callers
# like slate_gui_trigger() can include it in the failure status message.
# This avoids the popup being silently dismissed and the error being lost.
_last_fatal_popup_text = ""


def _dismiss_slate_popups(win):
    """
    Scan all open windows and dismiss any blocking popup/dialog
    that SLATE may show during the automation workflow.

    Dismissible popup title keywords:
      "Error", "Warning", "Confirm", "Overwrite", "Exist",
      "Replace", "Alert", "Question", "Notice",
      "Profile Finished", "Loaded DUT", "Success", "Information",
      "Fail", "Failed"

    Fatal popup keywords (logged as errors, captured for caller inspection):
      "Fail", "Failed", "Fatal", "Error", "Exception", "Abort"

    Dismiss button priority order:
      "Yes" > "OK" > "Overwrite" > "Replace" > "Continue" > "Close"

    Safety: Never dismisses the main SLATE window itself.
    Uses Desktop(backend="win32") to catch dialogs that are
    children of SLATE's process but separate top-level windows.

    Returns
    -------
    str or None
        If a fatal popup was dismissed, returns the full error text
        (title + body). Otherwise returns None.
    """
    global _last_fatal_popup_text
    time.sleep(T_POPUP_SETTLE)

    # "Profile Finished", "Loaded DUT", and "Success" are included here
    # because _wait_for_dut_load_popup() is detect-only — it does NOT
    # dismiss.  After detection, the caller invokes _dismiss_slate_popups()
    # to dismiss the DUT-load popup.
    dismiss_title_keywords = [
        "Error", "Warning", "Confirm", "Overwrite",
        "Exist", "Replace", "Alert", "Question", "Notice",
        "Profile Finished", "Loaded DUT", "Success", "Information",
        "Fail", "Failed",
    ]
    fatal_title_keywords = [
        "Fail", "Fatal", "Error", "Exception", "Abort",
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

                # Capture popup body text from Static controls (error details)
                body_text = ""
                try:
                    for c in w.children():
                        if c.class_name() == "Static":
                            txt = c.window_text()
                            if txt:
                                body_text += txt + "\n"
                except Exception:
                    pass

                full_text = title
                if body_text.strip():
                    full_text += " — " + body_text.strip()

                # Check if this is a FATAL popup (not just informational)
                is_fatal = any(
                    kw.lower() in title.lower() for kw in fatal_title_keywords
                )

                # Special case: "Profile Finished Successfully" with
                # "Loaded DUT:" but NO actual DUT locations listed.
                # This means the profile loaded but no DUTs were assigned —
                # the playground was NOT actually created.  Treat as fatal.
                if ("profile finished" in title.lower()
                        or "loaded dut" in title.lower()):
                    # Extract text after "Loaded DUT:" and check if any
                    # DUT locations are listed (e.g. "R0_P0_D0", "1-1", etc.)
                    loaded_dut_content = ""
                    for line in body_text.splitlines():
                        stripped = line.strip()
                        if stripped.lower().startswith("loaded dut"):
                            # Get everything after "Loaded DUT:"
                            parts = stripped.split(":", 1)
                            if len(parts) > 1:
                                loaded_dut_content = parts[1].strip()
                            break
                    if not loaded_dut_content:
                        is_fatal = True
                        full_text = (
                            title + " — NO DUTs loaded! "
                            "Profile finished but no DUT locations were "
                            "assigned. Playground was NOT created."
                        )
                        log.error(
                            "    \u274c 'Loaded DUT:' is EMPTY — "
                            "no DUTs were loaded into the playground. "
                            "This means the profile/lot failed to load."
                        )

                if is_fatal:
                    log.error(
                        "    \u274c FATAL popup detected: '" + title + "'"
                    )
                    if body_text.strip():
                        log.error(
                            "    \u274c Popup details:\n" + body_text.strip()
                        )
                    _last_fatal_popup_text = full_text
                else:
                    log.warning(
                        "    \u26a0\ufe0f Popup detected: '" + title
                        + "' — dismissing"
                    )

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
                            if is_fatal:
                                return full_text
                            return None   # one popup at a time
                    except Exception:
                        continue

            except Exception:
                continue   # window may have closed between iterations

    except Exception as e:
        log.debug("    _dismiss_slate_popups scan error: " + str(e))

    return None


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
        windows = _safe_desktop_windows(backend="win32")
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
    Drive SLATE (SSD Tester Engineering GUI) through the checkout workflow
    using Profile → LoadProfile menu automation.

    Instead of manually filling fields and clicking buttons one by one
    (Run Recipe Selection → Get Manudata → Create Temp Traveler →
    Create Playground), this function uses the GUI's built-in
    "LoadProfile" feature which handles everything automatically.

    The XML is loaded directly from CHECKOUT_QUEUE_FOLDER — no
    intermediate copy to playground_queue is needed.

    ════════════════════════════════════════════════════════
    WORKFLOW — PROFILE → LOADPROFILE
    ════════════════════════════════════════════════════════
    1. Connect to SLATE window
    2. Use the passed-in xml_path directly from CHECKOUT_QUEUE
    3. Open Profile menu → click "LoadProfile"
    4. Handle the file dialog: type the XML path and confirm
    5. SLATE automatically runs all steps:
       Recipe Selection → Get Manudata → Create Temp Traveler
       → Create Playground

    Args:
        xml_path : str or Path — full path to XML in CHECKOUT_QUEUE_FOLDER
        timeout  : int  — seconds to wait for SLATE window/elements
                          (used for element waits; connect uses CONNECT_TIMEOUT)

    Returns:
        True  — Load Profile succeeded; PlaygroundCompletionMonitor can confirm
        False — a step failed; caller should retry or mark failed

    Raises:
        Nothing — all exceptions are caught and logged; returns False on any error.
    """
    if not _PYWINAUTO_AVAILABLE:
        log.error(
            "\u274c slate_gui_trigger: pywinauto is not installed. "
            "Run: pip install pywinauto"
        )
        return False

    # Clear any previous fatal popup text before starting
    global _last_fatal_popup_text
    _last_fatal_popup_text = ""

    xml_path = str(xml_path)
    xml_name = os.path.basename(xml_path)
    log.info("\U0001f916 slate_gui_trigger (Load Profile) -> " + xml_name)

    # ════════════════════════════════════════════════════════
    # PRE-CONNECT: Dismiss any leftover popups from a previous failed attempt.
    # If a prior slate_gui_trigger() crashed (e.g. TypeError), the "Profile
    # Finished Successfully" popup may still be on screen, blocking SLATE.
    # We scan and dismiss ALL popup-like windows BEFORE connecting to SLATE.
    # ════════════════════════════════════════════════════════
    try:
        _pre_dismiss_keywords = [
            "Error", "Warning", "Confirm", "Overwrite", "Exist", "Replace",
            "Alert", "Question", "Notice", "Success", "Complete", "Done",
            "Information", "Info", "Finished", "Profile", "Loaded",
            "DUT", "Load", "Recipe", "Selection", "Manudata",
            "Traveler", "Playground", "Created", "Result",
            "Manufacturing", "Data",
        ]
        _pre_btn_order = ["OK", "Yes", "Overwrite", "Replace", "Continue", "Close"]
        all_windows = _safe_desktop_windows(backend="win32")
        for w in all_windows:
            try:
                title = w.window_text().strip()
                if not title:
                    continue
                if "SSD Tester Engineering GUI" in title:
                    continue
                if not any(kw.lower() in title.lower() for kw in _pre_dismiss_keywords):
                    continue
                log.info(
                    "  \U0001f9f9 Pre-connect: dismissing leftover popup: '"
                    + title + "'"
                )
                for btn_text in _pre_btn_order:
                    try:
                        btn = w.child_window(title=btn_text, class_name="Button")
                        if btn.exists(timeout=1) and btn.is_enabled():
                            btn.click_input()
                            time.sleep(0.5)
                            log.info(
                                "  \u2705 Pre-connect: dismissed '" + title
                                + "' with '" + btn_text + "'"
                            )
                            break
                    except Exception:
                        continue
                else:
                    # No button found — try Enter
                    try:
                        w.set_focus()
                        time.sleep(0.2)
                        pw_keyboard.send_keys("{ENTER}")  # type: ignore[union-attr]
                        time.sleep(0.5)
                        log.info(
                            "  \u2705 Pre-connect: dismissed '" + title
                            + "' with Enter key"
                        )
                    except Exception:
                        pass
            except Exception:
                continue
    except Exception as pre_err:
        log.debug(
            "  Pre-connect popup scan error (non-fatal): "
            + type(pre_err).__name__ + ": " + str(pre_err)
        )

    # Brief pause to let GUI settle after any pre-connect dismissals
    time.sleep(0.5)

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
            + (matched_title or "") + "' (handle=" + str(handle) + ")"
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
        _log_all_visible_windows(logger=None)

        return False

    # ── Dismiss any startup popups before touching anything ───────────────
    _dismiss_slate_popups(win)

    # ════════════════════════════════════════════════════════
    # RESOLVE XML PATH — use the passed-in xml_path directly
    # from CHECKOUT_QUEUE_FOLDER (no hot folder copy needed)
    # ════════════════════════════════════════════════════════
    log.info("  \U0001f4cb Resolving XML profile path...")

    latest_xml = None

    # PRIORITY 1: Use the passed-in xml_path directly from CHECKOUT_QUEUE
    if os.path.isfile(xml_path):
        latest_xml = xml_path
        log.info("  \U0001f4cb Using XML directly from CHECKOUT_QUEUE: " + latest_xml)

    # PRIORITY 2: Fall back to scanning CHECKOUT_QUEUE_FOLDER for latest XML
    if latest_xml is None:
        try:
            xmls = [
                os.path.join(CHECKOUT_QUEUE_FOLDER, f)
                for f in os.listdir(CHECKOUT_QUEUE_FOLDER)
                if f.lower().endswith(".xml")
                and not f.endswith(".checkout_status")
                and not f.endswith(".checkout_lock")
            ]
            if xmls:
                latest_xml = max(xmls, key=os.path.getmtime)
                log.info(
                    "  \U0001f4cb Fallback: Latest XML in CHECKOUT_QUEUE ("
                    + str(len(xmls)) + " found): "
                    + os.path.basename(latest_xml)
                )
            else:
                log.warning(
                    "  \u26a0\ufe0f No .xml files found in " + CHECKOUT_QUEUE_FOLDER
                )
        except Exception as scan_err:
            log.warning(
                "  \u26a0\ufe0f Could not scan " + CHECKOUT_QUEUE_FOLDER
                + ": " + str(scan_err)
            )

    if latest_xml is None:
        log.error("  \u274c No XML file found to load!")
        return False

    log.info("  \U0001f4cb Selected XML for Load Profile: " + latest_xml)

    # ════════════════════════════════════════════════════════
    # LOAD PROFILE VIA PROFILE MENU
    # Profile → LoadProfile opens a file dialog.
    # We type the XML path into the dialog and confirm.
    # SLATE then automatically runs all checkout steps:
    #   Recipe Selection → Get Manudata → Create Temp Traveler
    #   → Create Playground
    # ════════════════════════════════════════════════════════
    log.info("  \U0001f4cb Opening Profile menu -> LoadProfile...")

    # ── Step 1: Open the Profile menu and click LoadProfile ────────────────
    try:
        # Method A: Use menu_select() for WinForms menu bar
        # SLATE's menu bar has "Profile" (not "File") with item "LoadProfile"
        try:
            win.menu_select("Profile->LoadProfile")
            log.info("  \u2705 Menu selected: Profile -> LoadProfile (menu_select)")
            time.sleep(T_AFTER_MENU_CLICK)
        except Exception as menu_err_a:
            # Method A2: Try with space in menu item name
            try:
                win.menu_select("Profile->Load Profile")
                log.info("  \u2705 Menu selected: Profile -> Load Profile (menu_select)")
                time.sleep(T_AFTER_MENU_CLICK)
            except Exception as menu_err_b:
                log.warning(
                    "  \u26a0\ufe0f menu_select('Profile->LoadProfile') failed: "
                    + str(menu_err_a) + " / " + str(menu_err_b)
                    + " — trying keyboard shortcut"
                )
                # Method B: Use Alt+P keyboard shortcut to open Profile menu,
                # then navigate to LoadProfile
                try:
                    win.set_focus()
                    time.sleep(0.3)
                    pw_keyboard.send_keys("%p")  # type: ignore[union-attr]  # Alt+P for Profile
                    time.sleep(T_AFTER_MENU_CLICK)

                    # Click "LoadProfile" — try 'l' accelerator key first
                    pw_keyboard.send_keys("l")  # type: ignore[union-attr]
                    time.sleep(T_AFTER_MENU_CLICK)
                    log.info("  \u2705 Menu opened via Alt+P -> L keyboard shortcut")
                except Exception as kb_err:
                    log.error(
                        "  \u274c Cannot open Profile -> LoadProfile menu: "
                        + str(kb_err)
                    )
                    return False

    except Exception as e:
        log.error(
            "  \u274c Profile menu interaction failed: "
            + type(e).__name__ + ": " + str(e)
        )
        return False

    # ── Step 2: Handle the file dialog ─────────────────────────────────────
    # The "Load Profile" menu item opens a standard Windows file dialog
    # (Open File dialog). We need to:
    #   a) Wait for the dialog to appear
    #   b) Type the XML file path into the filename field
    #   c) Click Open / press Enter
    log.info("  \U0001f4cb Waiting for file dialog...")
    time.sleep(T_FILE_DIALOG_SETTLE)

    file_dialog_found = False
    try:
        # Look for the Open file dialog — standard Windows dialog titles
        dialog_titles = ["Open", "LoadProfile", "Load Profile", "Select Profile", "Browse"]
        file_dlg = None

        for dlg_title in dialog_titles:
            try:
                file_dlg = app.window(title_re=".*" + dlg_title + ".*")
                if file_dlg.exists(timeout=5):
                    log.info(
                        "  \U0001f50d Found file dialog: '"
                        + file_dlg.window_text() + "'"
                    )
                    file_dialog_found = True
                    break
            except Exception:
                continue

        # Fallback: search all top-level windows for a dialog
        if not file_dialog_found:
            try:
                all_windows = _safe_desktop_windows(backend=SLATE_BACKEND)
                for w in all_windows:
                    try:
                        title = w.window_text()
                        if title and any(
                            kw.lower() in title.lower()
                            for kw in ["Open", "Load", "Profile", "Browse", "Select"]
                        ):
                            # Check if it looks like a file dialog
                            # (has an Edit control for filename)
                            try:
                                edit = w.child_window(class_name="Edit")
                                if edit.exists(timeout=2):
                                    file_dlg = w
                                    file_dialog_found = True
                                    log.info(
                                        "  \U0001f50d Found file dialog (Desktop scan): '"
                                        + title + "'"
                                    )
                                    break
                            except Exception:
                                continue
                    except Exception:
                        continue
            except Exception as scan_err:
                log.warning(
                    "  \u26a0\ufe0f Desktop scan for file dialog failed: "
                    + str(scan_err)
                )

        if not file_dialog_found or file_dlg is None:
            log.error(
                "  \u274c File dialog not found after clicking LoadProfile. "
                "Ensure SLATE has a Profile -> LoadProfile menu item."
            )
            # Try pressing Escape to close any partial menu state
            try:
                pw_keyboard.send_keys("{ESCAPE}")  # type: ignore[union-attr]
            except Exception:
                pass
            return False

        # ── Type the XML path into the filename field ──────────────────────
        # Standard Windows file dialogs have an Edit control with
        # class_name="Edit" for the filename input.
        # The combo box for filename typically has auto_id="1148" or
        # class_name="ComboBoxEx32" with a child Edit.
        log.info(
            "  \U0001f4dd Typing XML path into file dialog: "
            + latest_xml
        )

        filename_set = False

        # Strategy A: Find the Edit control directly in the dialog
        try:
            # Try the standard filename combo box (auto_id 1148 in Windows dialogs)
            try:
                fname_combo = file_dlg.child_window(
                    class_name="ComboBoxEx32"
                )
                if fname_combo.exists(timeout=3):
                    fname_edit = fname_combo.child_window(class_name="Edit")
                    if fname_edit.exists(timeout=2):
                        fname_edit.set_focus()
                        fname_edit.set_edit_text(latest_xml)
                        time.sleep(T_AFTER_FIELD_SET)
                        filename_set = True
                        log.info("  \u2705 Filename set via ComboBoxEx32 -> Edit")
            except Exception:
                pass

            # Strategy B: Find any Edit control in the dialog
            if not filename_set:
                try:
                    fname_edit = file_dlg.child_window(class_name="Edit")
                    if fname_edit.exists(timeout=3):
                        fname_edit.set_focus()
                        fname_edit.set_edit_text(latest_xml)
                        time.sleep(T_AFTER_FIELD_SET)
                        filename_set = True
                        log.info("  \u2705 Filename set via Edit control")
                except Exception:
                    pass

            # Strategy C: Use keyboard to type the path
            if not filename_set:
                try:
                    file_dlg.set_focus()
                    time.sleep(0.3)
                    # Ctrl+A to select all, then type the path
                    pw_keyboard.send_keys("^a")  # type: ignore[union-attr]
                    time.sleep(0.1)
                    pw_keyboard.send_keys(  # type: ignore[union-attr]
                        latest_xml, with_spaces=True
                    )
                    time.sleep(T_AFTER_FIELD_SET)
                    filename_set = True
                    log.info("  \u2705 Filename typed via keyboard")
                except Exception as kb_err:
                    log.error(
                        "  \u274c Cannot type filename: " + str(kb_err)
                    )

            if not filename_set:
                log.error(
                    "  \u274c Could not set filename in file dialog"
                )
                # Close the dialog
                try:
                    pw_keyboard.send_keys("{ESCAPE}")  # type: ignore[union-attr]
                except Exception:
                    pass
                return False

        except Exception as e:
            log.error(
                "  \u274c File dialog filename entry failed: "
                + type(e).__name__ + ": " + str(e)
            )
            try:
                pw_keyboard.send_keys("{ESCAPE}")  # type: ignore[union-attr]
            except Exception:
                pass
            return False

        # ── Click Open / press Enter to confirm ───────────────────────────
        log.info("  \U0001f7e2 Confirming file selection...")
        try:
            # Try clicking the "Open" button first
            open_clicked = False
            for btn_text in ["&Open", "Open", "OK", "Load"]:
                try:
                    open_btn = file_dlg.child_window(
                        title=btn_text, class_name="Button"
                    )
                    if open_btn.exists(timeout=2) and open_btn.is_enabled():
                        open_btn.click_input()
                        open_clicked = True
                        log.info(
                            "  \u2705 Clicked '" + btn_text
                            + "' button in file dialog"
                        )
                        break
                except Exception:
                    continue

            # Fallback: press Enter
            if not open_clicked:
                pw_keyboard.send_keys("{ENTER}")  # type: ignore[union-attr]
                log.info("  \u2705 Pressed Enter to confirm file dialog")

        except Exception as e:
            log.warning(
                "  \u26a0\ufe0f Could not click Open button: " + str(e)
                + " — pressing Enter as fallback"
            )
            try:
                pw_keyboard.send_keys("{ENTER}")  # type: ignore[union-attr]
            except Exception:
                pass

    except Exception as e:
        log.error(
            "  \u274c File dialog handling failed: "
            + type(e).__name__ + ": " + str(e)
        )
        return False

    # ════════════════════════════════════════════════════════
    # LOAD PROFILE COMPLETE
    # Profile loaded via menu + file dialog.
    # SLATE will automatically run all checkout steps:
    #   Recipe Selection → Get Manudata → Create Temp Traveler
    #   → Create Playground
    #
    # IMPORTANT: Do NOT wait for the "DUT load" popup here!
    # The popup detection is handled by _wait_for_dut_load_popup()
    # which is called AFTER slate_gui_trigger() returns.
    # ════════════════════════════════════════════════════════

    # ── Wait for LoadProfile to complete and show the DUT load popup ──
    # The "Profile Finished Successfully" / "Loaded DUT: X,Y,Z" popup
    # signals that SLATE has finished processing the profile and the
    # workspace folder has been created.
    #
    # We wait for it HERE (inside slate_gui_trigger) instead of in
    # process_checkout_xml because the popup appears while the SLATE
    # window is still active and accessible via pywinauto.
    log.info("  \U0001f4cb Waiting for LoadProfile to complete (DUT load popup)...")
    
    # Give SLATE time to process the profile (Recipe Selection, Manudata,
    # Temp Traveler, Playground creation) before checking for the popup.
    # Typical processing time: 30-60 seconds.
    time.sleep(5)  # Initial settle time
    
    popup_ok = _wait_for_dut_load_popup(
        timeout=T_DUT_LOAD_TIMEOUT,
        poll_interval=T_DUT_LOAD_POLL,
    )

    if not popup_ok:
        # popup_ok=False means either timeout OR empty "Loaded DUT:"
        # Both are definitive failures.
        if _last_fatal_popup_text:
            fail_msg = (
                "DUT load popup detected but EMPTY — no DUTs were loaded. "
                "Detail: " + _last_fatal_popup_text[:500]
            )
        else:
            fail_msg = (
                "DUT load popup was NOT detected (timed out after "
                + str(T_DUT_LOAD_TIMEOUT) + "s). "
                "SLATE did not produce a 'Loaded DUT' confirmation."
            )
        log.error("  \u274c " + fail_msg)
        return False

    # ── Check for fatal popups that may have appeared during LoadProfile ──
    # A fatal popup (e.g. "Failed to Create Lot Cache") may have been
    # dismissed by _dismiss_slate_popups() during the LoadProfile process.
    # Check the global sentinel to see if one was captured.
    if _last_fatal_popup_text:
        log.error(
            "  \u274c FATAL popup detected during LoadProfile:\n"
            "  " + _last_fatal_popup_text
        )
        return False

    log.info(
        "  \u2705 \u2705 \u2705 slate_gui_trigger COMPLETE — "
        "Profile loaded + DUT load popup confirmed!"
    )
    log.info("      XML: " + xml_name)
    return True


# ── WAIT FOR DUT LOAD POPUP ──────────────────────────────────────────────────
# After LoadProfile, SLATE processes the XML and eventually shows a
# "Loaded DUT" / "DUT load successful" popup.  This popup signals that
# the workspace folder has been created.
T_DUT_LOAD_POLL    = 10       # poll every 10 seconds
T_DUT_LOAD_TIMEOUT = 600     # give up after 10 minutes


def _wait_for_dut_load_popup(timeout=None, poll_interval=None):
    """
    Poll Desktop windows until a popup whose title contains DUT-load
    keywords appears.  Detection only — dismissal is handled by
    _dismiss_slate_popups() which is called by the caller after this
    function returns True.

    Uses Desktop(backend="win32").windows() directly — the proven working
    approach from the old checkout_watcher.

    Args:
        timeout       : int — max seconds to wait (default: T_DUT_LOAD_TIMEOUT)
        poll_interval : float — seconds between polls (default: T_DUT_LOAD_POLL)

    Returns:
        True  — popup detected (caller must dismiss via _dismiss_slate_popups)
        False — timed out, OR popup had empty "Loaded DUT:" (no playground)
    """
    global _last_fatal_popup_text

    if timeout is None:
        timeout = T_DUT_LOAD_TIMEOUT
    if poll_interval is None:
        poll_interval = T_DUT_LOAD_POLL

    if not _PYWINAUTO_AVAILABLE:
        log.warning("[DUT Popup] pywinauto not available — cannot detect popup")
        return False

    # ── Success keywords — broad match like the old working watcher ────────
    # SLATE may use different casing/phrasing — match broadly.
    success_keywords = [
        "dut load successful",
        "dut loaded",
        "load successful",
        "loaded dut",
        "profile finished",
    ]

    log.info(
        "  \u23f3 Waiting for 'Dut load successful' popup "
        "(timeout=" + str(timeout) + "s, poll=" + str(poll_interval) + "s)..."
    )

    start = time.time()
    while (time.time() - start) < timeout:
        try:
            # Use Desktop(backend="win32").windows() directly — proven working
            all_windows = Desktop(backend="win32").windows()  # type: ignore[operator]
            for w in all_windows:
                try:
                    title = w.window_text().strip()
                    if not title:
                        continue

                    # Check if this window title matches any success keyword
                    title_lower = title.lower()
                    if not any(kw in title_lower for kw in success_keywords):
                        continue

                    # ── Found a matching popup! ──────────────────────────

                    # Extract body text from Static children for logging
                    # and empty-DUT detection.
                    body_text_full = ""
                    try:
                        for child in w.children():
                            try:
                                if child.class_name() == "Static":
                                    ct = child.window_text().strip()
                                    if ct:
                                        body_text_full += ct + "\n"
                            except Exception:
                                continue
                    except Exception:
                        pass

                    # Build a descriptive label for log messages.
                    # Prefer "Loaded DUT: 0,2,5" from body over the
                    # generic title "Profile Finished Successfully".
                    loaded_dut_line = ""
                    loaded_dut_content = ""
                    for bline in body_text_full.splitlines():
                        stripped = bline.strip()
                        if stripped.lower().startswith("loaded dut"):
                            loaded_dut_line = stripped
                            parts = stripped.split(":", 1)
                            if len(parts) > 1:
                                loaded_dut_content = parts[1].strip()
                            break
                    popup_label = loaded_dut_line if loaded_dut_line else title

                    log.info(
                        "  \u2705 'Dut load successful' popup detected: '"
                        + popup_label + "'"
                    )

                    # ── Check for empty "Loaded DUT:" — treat as FAILURE ─
                    # "Profile Finished Successfully" with "Loaded DUT:" but
                    # NO actual DUT locations means the playground was NOT
                    # created.  Return False so process_checkout_xml marks
                    # it as failed instead of proceeding to collect results.
                    try:

                        is_empty_dut = (
                            not loaded_dut_content
                            and (
                                "profile finished" in title_lower
                                or "loaded dut" in title_lower
                                or any(
                                    "loaded dut" in bl.lower()
                                    for bl in body_text_full.splitlines()
                                )
                            )
                        )

                        if is_empty_dut:
                            # Set the fatal popup sentinel so callers know
                            # this was a definitive failure (not a timeout).
                            _last_fatal_popup_text = (
                                title + " — NO DUTs loaded! "
                                "Profile finished but no DUT locations were "
                                "assigned. Playground was NOT created."
                            )
                            log.error(
                                "  \u274c 'Loaded DUT:' is EMPTY — "
                                "no DUTs were loaded into the playground. "
                                "This means the profile/lot failed to load."
                            )
                            log.error(
                                "  Popup title: '" + title + "'"
                            )
                            if body_text_full.strip():
                                log.error(
                                    "  Popup body:\n"
                                    + body_text_full.strip()
                                )
                            return False
                    except Exception as empty_check_err:
                        log.debug(
                            "  Empty DUT check error (non-fatal): "
                            + str(empty_check_err)
                        )

                    # ── Dismiss the popup ─────────────────────────────
                    # The window `w` from Desktop().windows() is an
                    # HwndWrapper — child_window() does NOT work on it.
                    # Use Application().connect(handle=) to get a proper
                    # wrapper, then ctypes as fallback.
                    dismissed = False

                    # Method 1: Re-connect via Application to get a
                    # proper DialogWrapper that supports child_window().
                    try:
                        popup_handle = w.handle
                        popup_app = Application(backend="win32").connect(
                            handle=popup_handle
                        )
                        popup_dlg = popup_app.window(handle=popup_handle)
                        for btn_text in ["OK", "&OK", "Yes", "&Yes",
                                         "Continue", "Close"]:
                            try:
                                btn = popup_dlg.child_window(
                                    title=btn_text,
                                    class_name="Button"
                                )
                                if btn.exists(timeout=2) and btn.is_enabled():
                                    btn.click_input()
                                    time.sleep(0.5)
                                    log.info(
                                        "  \u2705 Dismissed '" + popup_label
                                        + "' via button '" + btn_text
                                        + "' (App.connect)"
                                    )
                                    dismissed = True
                                    break
                            except Exception:
                                continue
                    except Exception as m1_err:
                        log.debug(
                            "  Dismiss Method 1 (App.connect) error: "
                            + str(m1_err)
                        )

                    # Method 2: ctypes Win32 API — FindWindowExW to
                    # locate the Button child, then send BM_CLICK.
                    if not dismissed:
                        try:
                            import ctypes
                            from ctypes import wintypes
                            _user32 = ctypes.windll.user32
                            _FindWindowExW = _user32.FindWindowExW
                            _FindWindowExW.argtypes = [
                                wintypes.HWND, wintypes.HWND,
                                wintypes.LPCWSTR, wintypes.LPCWSTR,
                            ]
                            _FindWindowExW.restype = wintypes.HWND
                            _SendMsg = _user32.SendMessageW
                            _SendMsg.argtypes = [
                                wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM,
                            ]
                            _SendMsg.restype = wintypes.LPARAM
                            BM_CLICK = 0x00F5
                            parent_hwnd = w.handle
                            for btn_text in ["OK", "&OK", "Yes", "&Yes"]:
                                btn_hwnd = _FindWindowExW(
                                    parent_hwnd, None, "Button", btn_text
                                )
                                if btn_hwnd:
                                    _SendMsg(btn_hwnd, BM_CLICK, 0, 0)
                                    time.sleep(0.5)
                                    log.info(
                                        "  \u2705 Dismissed '" + popup_label
                                        + "' via BM_CLICK '" + btn_text
                                        + "' (ctypes)"
                                    )
                                    dismissed = True
                                    break
                            # Try first Button child regardless of text
                            if not dismissed:
                                btn_hwnd = _FindWindowExW(
                                    parent_hwnd, None, "Button", None
                                )
                                if btn_hwnd:
                                    _SendMsg(btn_hwnd, BM_CLICK, 0, 0)
                                    time.sleep(0.5)
                                    log.info(
                                        "  \u2705 Dismissed '" + popup_label
                                        + "' via BM_CLICK first Button"
                                        " (ctypes)"
                                    )
                                    dismissed = True
                        except Exception as m2_err:
                            log.debug(
                                "  Dismiss Method 2 (ctypes) error: "
                                + str(m2_err)
                            )

                    # Method 3: PostMessage WM_CLOSE to the popup
                    if not dismissed:
                        try:
                            import ctypes
                            WM_CLOSE = 0x0010
                            ctypes.windll.user32.PostMessageW(
                                w.handle, WM_CLOSE, 0, 0
                            )
                            time.sleep(0.5)
                            log.info(
                                "  \u2705 Dismissed '" + popup_label
                                + "' via WM_CLOSE"
                            )
                            dismissed = True
                        except Exception as m3_err:
                            log.warning(
                                "  \u26a0\ufe0f Could not dismiss '"
                                + popup_label + "': " + str(m3_err)
                            )

                    return True

                except Exception:
                    continue

        except Exception as e:
            log.debug(
                "  _wait_for_dut_load_popup scan error: " + str(e)
            )

        elapsed = int(time.time() - start)
        if elapsed > 0 and elapsed % 30 == 0:
            log.info(
                "  \u23f3 Still waiting for 'Dut load successful'... "
                + str(elapsed) + "/" + str(timeout) + "s"
            )

        time.sleep(poll_interval)

    log.error(
        "  \u274c Timed out after " + str(timeout)
        + "s waiting for 'Dut load successful' popup"
    )
    return False


# ── WORKSPACE RESULT COLLECTION ───────────────────────────────────────────────
# How long to wait (seconds) for both result files to appear in the workspace.
# The test may take a while after LoadProfile before files are generated.
T_COLLECT_POLL_INTERVAL = 15      # check every 15 seconds
T_COLLECT_TIMEOUT       = 1800    # give up after 30 minutes


# ════════════════════════════════════════════════════════════════════════
# MANIFEST-BASED FILE SELECTION — CORE FUNCTIONS
#
# Flow:
#   1. Watcher calls scan_available_files() after test completion
#      → scans workspace for allow-listed files that actually exist
#   2. Watcher calls write_manifest() → writes JSON to CHECKOUT_RESULTS
#      so BENTO GUI can read it
#   3. BENTO GUI displays available files to user, user selects which
#      optional files to collect, BENTO writes selection JSON back
#   4. Watcher calls read_user_selection() → reads selection JSON
#   5. Watcher calls copy_selected_files() → copies only selected
#      (+ all required) files to the output folder
#
# If BENTO does not respond within MANIFEST_SELECTION_TIMEOUT, only
# required files are copied (safe default).
# ════════════════════════════════════════════════════════════════════════

def scan_available_files(workspace_dir, logger):
    """
    Recursively scan the workspace directory for ALL files.

    Every file found is included in the results.  Files that match an
    entry in FILE_ALLOW_LIST inherit its ``key``, ``required`` flag, and
    friendly ``desc``.  All other files are listed as optional with their
    relative path used as the key.

    Args:
        workspace_dir : str — absolute path to the workspace folder
                        (e.g. C:\\Tanisys\\DNA2\\User\\Workspace\\WS_20260409_...)
        logger        : logging.Logger

    Returns:
        list of dict — each dict contains:
            key      : str   — allow-list key or relative path
            subpath  : str   — relative path from workspace root
            required : bool  — True only for allow-list required entries
            desc     : str   — human-readable description
            found    : bool  — always True (file exists on disk)
            paths    : list  — [absolute path]
            sizes    : list  — [file size in bytes]
            names    : list  — [basename]
    """
    import fnmatch as _fnmatch

    # ── Step 1: Build lookup from FILE_ALLOW_LIST ──────────────────────
    # Map (normalised subpath pattern) -> allow-list entry so we can
    # match discovered files against known entries.
    _allow_map = {}          # subpath_pattern -> entry dict
    _allow_matched = {}      # subpath_pattern -> list of result dicts (filled below)
    for entry in FILE_ALLOW_LIST:
        _allow_map[entry["subpath"]] = entry
        _allow_matched[entry["subpath"]] = []

    # ── Step 2: Walk the entire workspace recursively ──────────────────
    results = []
    seen_paths = set()       # avoid duplicates

    for dirpath, _dirnames, filenames in os.walk(workspace_dir):
        for fname in filenames:
            full_path = os.path.join(dirpath, fname)
            if not os.path.isfile(full_path):
                continue

            # Compute relative path (forward-slash normalised)
            rel_path = os.path.relpath(full_path, workspace_dir).replace("\\", "/")

            if rel_path in seen_paths:
                continue
            seen_paths.add(rel_path)

            # File size
            try:
                fsize = os.path.getsize(full_path)
            except OSError:
                fsize = 0

            # ── Check if this file matches any allow-list entry ────────
            matched_entry = None
            for pattern, entry in _allow_map.items():
                # Normalise pattern separators
                norm_pattern = pattern.replace("\\", "/")
                # Try exact match first (e.g. "OS/resultsManager.db")
                if _fnmatch.fnmatch(rel_path, norm_pattern):
                    matched_entry = entry
                    break
                # Fallback: match basename only (MPT3 stores files at root)
                if "/" in norm_pattern:
                    base_pattern = norm_pattern.rsplit("/", 1)[-1]
                    if _fnmatch.fnmatch(fname, base_pattern):
                        matched_entry = entry
                        break
                else:
                    if _fnmatch.fnmatch(fname, norm_pattern):
                        matched_entry = entry
                        break

            if matched_entry:
                key      = matched_entry["key"]
                required = matched_entry["required"]
                desc     = matched_entry.get("desc", fname)
                # Track that this allow-list entry was matched
                _allow_matched[matched_entry["subpath"]].append(full_path)
            else:
                key      = rel_path
                required = False
                desc     = fname

            results.append({
                "key":      key,
                "subpath":  rel_path,
                "required": required,
                "desc":     desc,
                "found":    True,
                "paths":    [full_path],
                "sizes":    [fsize],
                "names":    [fname],
            })

    # ── Step 3: Add allow-list entries that were NOT found ─────────────
    # So the manifest shows them as "not found" (especially required ones)
    for pattern, entry in _allow_map.items():
        if _allow_matched[pattern]:
            continue   # already matched — skip
        results.append({
            "key":      entry["key"],
            "subpath":  entry["subpath"],
            "required": entry["required"],
            "desc":     entry.get("desc", ""),
            "found":    False,
            "paths":    [],
            "sizes":    [],
            "names":    [],
        })

    # ── Logging summary ───────────────────────────────────────────────
    found_count = sum(1 for e in results if e["found"])
    total_count = len(results)
    total_size  = sum(s for e in results for s in e["sizes"])
    if total_size > 1024 * 1024:
        size_str = "%.1f MB" % (total_size / (1024.0 * 1024.0))
    elif total_size > 1024:
        size_str = "%.1f KB" % (total_size / 1024.0)
    else:
        size_str = str(total_size) + " B"
    logger.info(
        "[Manifest] Recursive scan complete: "
        + str(found_count) + " files found, "
        + str(total_count) + " entries total, "
        + size_str + " on disk"
    )

    return results


def write_manifest(scan_results, job_id, hostname, env, dest_dir, logger):
    """
    Write a JSON manifest listing available files for BENTO to read.

    The manifest is written to:
        <dest_dir>/file_manifest_<hostname>_<job_id>.json

    BENTO GUI polls CHECKOUT_RESULTS for manifest files and displays
    the available files to the user for selection.

    Args:
        scan_results : list — output from scan_available_files()
        job_id       : str  — JIRA key or job identifier
        hostname     : str  — tester hostname
        env          : str  — tester environment (ABIT, SFN2, etc.)
        dest_dir     : str  — output directory (usually CHECKOUT_RESULTS/...)
        logger       : logging.Logger

    Returns:
        str — path to the written manifest file, or "" on error
    """
    manifest = {
        "job_id":    job_id,
        "hostname":  hostname,
        "env":       env,
        "timestamp": datetime.now().isoformat(),
        "files":     [],
    }

    for entry in scan_results:
        manifest["files"].append({
            "key":      entry["key"],
            "subpath":  entry.get("subpath", entry["key"]),
            "desc":     entry["desc"],
            "required": entry["required"],
            "found":    entry["found"],
            "count":    len(entry["paths"]),
            "sizes":    entry["sizes"],
            "names":    entry.get("names", [os.path.basename(p) for p in entry["paths"]]),
        })

    # Ensure destination directory exists
    try:
        if not os.path.isdir(dest_dir):
            os.makedirs(dest_dir)
    except OSError as e:
        logger.error("[Manifest] Cannot create dest dir: " + str(e))
        return ""

    manifest_name = "file_manifest_" + hostname + "_" + job_id + ".json"
    manifest_path = os.path.join(dest_dir, manifest_name)

    try:
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)
        logger.info("[Manifest] Written: " + manifest_path)
        return manifest_path
    except Exception as e:
        logger.error("[Manifest] Write failed: " + str(e))
        return ""


def read_user_selection(job_id, hostname, dest_dir, logger,
                        timeout=MANIFEST_SELECTION_TIMEOUT,
                        poll_interval=MANIFEST_POLL_INTERVAL):
    """
    Wait for BENTO GUI to write back a selection JSON file.

    BENTO writes:
        <dest_dir>/file_selection_<hostname>_<job_id>.json
    containing:
        {
            "job_id": "TSESSD-14270",
            "selected_keys": ["results_db", "tracefile", "dispatcher_debug"]
        }

    Required files are ALWAYS included even if the user omits them.

    Args:
        job_id        : str  — JIRA key or job identifier
        hostname      : str  — tester hostname
        dest_dir      : str  — directory to poll for selection file
        logger        : logging.Logger
        timeout       : int  — max seconds to wait (default: MANIFEST_SELECTION_TIMEOUT)
        poll_interval : int  — seconds between polls (default: MANIFEST_POLL_INTERVAL)

    Returns:
        list of str — selected file keys (always includes required keys)
        None        — on timeout (caller should fall back to required-only)
    """
    selection_name = "file_selection_" + hostname + "_" + job_id + ".json"
    selection_path = os.path.join(dest_dir, selection_name)

    if timeout <= 0:
        logger.info(
            "[Manifest] Waiting for user selection: " + selection_name
            + " (no timeout — waiting until user confirms)"
        )
    else:
        logger.info(
            "[Manifest] Waiting for user selection: " + selection_name
            + " (timeout=" + str(timeout) + "s)"
        )

    start = time.time()
    while True:
        elapsed = time.time() - start

        if os.path.isfile(selection_path):
            try:
                with open(selection_path, "r") as f:
                    data = json.load(f)

                selected_keys = data.get("selected_keys", [])
                logger.info(
                    "[Manifest] User selection received: "
                    + str(selected_keys)
                    + " (after " + str(int(elapsed)) + "s)"
                )

                # Always include required keys (match by allow-list key)
                for entry in FILE_ALLOW_LIST:
                    if entry["required"] and entry["key"] not in selected_keys:
                        selected_keys.append(entry["key"])
                        logger.info(
                            "[Manifest] Auto-added required key: " + entry["key"]
                        )

                # Clean up selection file after reading
                try:
                    os.remove(selection_path)
                except OSError:
                    pass

                return selected_keys

            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.warning(
                    "[Manifest] Invalid selection file: " + str(e)
                    + " — waiting for valid file..."
                )

        # timeout <= 0 means wait indefinitely (no timeout)
        if timeout > 0 and elapsed >= timeout:
            logger.warning(
                "[Manifest] Selection timeout after " + str(int(elapsed))
                + "s — falling back to required-only files"
            )
            return None

        # Periodic heartbeat log so user knows watcher is still alive
        if int(elapsed) > 0 and int(elapsed) % 60 == 0:
            logger.info(
                "[Manifest] Still waiting for user selection... ("
                + str(int(elapsed)) + "s)"
            )

        time.sleep(poll_interval)


def copy_selected_files(scan_results, selected_keys, dest_dir, logger,
                        rename_prefix=""):
    """
    Copy selected (and all required) files from workspace to dest_dir.

    Files are matched by their ``key`` field from scan_available_files().
    For allow-listed files the key is a short name (e.g. "results_db");
    for all other workspace files the key is the relative path
    (e.g. "OS/resultsManager.db", "Traces/debug.log").

    Args:
        scan_results  : list — output from scan_available_files()
        selected_keys : list of str — keys chosen by user (allow-list key or rel path)
        dest_dir      : str  — destination folder
        logger        : logging.Logger
        rename_prefix : str  — e.g. "P2D1_"; when non-empty, DispatcherDebug*.txt
                        files are renamed to <prefix>TraceFile.txt (e.g. P2D1_TraceFile.txt)

    Returns:
        (copied_names, copied_count) — list of copied filenames, total count
    """
    # Build the effective key set: selected + all required
    effective_keys = set(selected_keys) if selected_keys else set()
    for entry in FILE_ALLOW_LIST:
        if entry["required"]:
            effective_keys.add(entry["key"])

    # Ensure destination directory exists
    try:
        if not os.path.isdir(dest_dir):
            os.makedirs(dest_dir)
    except OSError as e:
        logger.error("[Manifest] Cannot create dest dir: " + str(e))
        return [], 0

    copied_names = []
    copied_count = 0

    for entry in scan_results:
        key = entry["key"]
        if key not in effective_keys:
            logger.debug("[Manifest] Skipping (not selected): " + key)
            continue

        if not entry["found"]:
            if entry["required"]:
                logger.warning(
                    "[Manifest] Required file NOT found: " + key
                    + " (" + entry.get("subpath", key) + ")"
                )
            continue

        for src_path in entry["paths"]:
            fname = os.path.basename(src_path)

            # ── Rename with P{x}D{y}_ prefix ────────────────────────────
            # When rename_prefix is set (flat mode / IBIR), rename ALL
            # collected files with the prefix so downstream tools can
            # identify which primitive/DUT each file belongs to:
            #   DispatcherDebug*.txt → P{x}D{y}_TraceFile.txt
            #   resultsManager.db   → P{x}D{y}_resultsManager.db
            #   summary.zip         → P{x}D{y}_summary.zip
            #   Tracefile.txt       → P{x}D{y}_TraceFile.txt
            #   (any other file)    → P{x}D{y}_{original name}
            if rename_prefix:
                if key == "dispatcher_debug":
                    dest_fname = rename_prefix + "TraceFile.txt"
                elif key == "tracefile":
                    dest_fname = rename_prefix + "TraceFile.txt"
                else:
                    dest_fname = rename_prefix + fname
                logger.info(
                    "[Manifest] Renaming " + fname
                    + " -> " + dest_fname
                    + " (prefix=" + rename_prefix + ")"
                )
            else:
                dest_fname = fname

            # Preserve subdirectory structure from the relative path
            subpath = entry.get("subpath", key)
            rel_parts = subpath.replace("\\", "/").split("/")
            if len(rel_parts) > 1:
                # File is in a subdirectory — create it in dest
                sub_dir = os.path.join(dest_dir, *rel_parts[:-1])
                try:
                    if not os.path.isdir(sub_dir):
                        os.makedirs(sub_dir)
                except OSError:
                    sub_dir = dest_dir  # fallback to flat copy
                dest_path = os.path.join(sub_dir, dest_fname)
            else:
                dest_path = os.path.join(dest_dir, dest_fname)

            try:
                shutil.copy2(src_path, dest_path)
                copied_names.append(dest_fname)
                copied_count += 1
                logger.info(
                    "[Manifest] Copied: " + fname
                    + " -> " + dest_fname
                    + " (" + key + ")"
                )
            except Exception as e:
                logger.error(
                    "[Manifest] Failed to copy " + fname + ": " + str(e)
                )

    logger.info(
        "[Manifest] Total copied: " + str(copied_count)
        + " file(s) -> " + dest_dir
    )
    return copied_names, copied_count


def _get_workspace_info(env):
    """
    Return (workspace_folder, workspace_mode) for the given environment.

    Workspace modes:
      "flat"           — subfolders directly under workspace_folder
                         (e.g. C:\\Tanisys\\DNA2\\User\\Workspace\\<lot_folder>)
      "primitive_dut"  — the workspace IS the DUT folder itself; search
                         Primitive*/DUT* for the latest modified DUT folder
                         (e.g. C:\\test_program\\Rack0\\Primitive2\\DUT5)

    Falls back to SLATE_WORKSPACE_FOLDER / "flat" if env not in registry.
    """
    try:
        cfg = TESTER_REGISTRY[env]
        folder = cfg.get("workspace_folder", SLATE_WORKSPACE_FOLDER)
        mode   = cfg.get("workspace_mode", "flat")
        return folder, mode
    except (KeyError, TypeError):
        return SLATE_WORKSPACE_FOLDER, "flat"


def _find_latest_workspace_dir(workspace_folder, workspace_mode, logger):
    """
    Find the most recently modified workspace directory.

    For "flat" mode:
      Scans workspace_folder for the latest modified subfolder.
      Returns e.g. C:\\Tanisys\\DNA2\\User\\Workspace\\<lot_folder>

    For "primitive_dut" mode:
      The workspace IS the DUT folder itself.  Scans all
      workspace_folder/Primitive*/DUT* directories and returns the
      latest modified DUT folder.
      Returns e.g. C:\\test_program\\Rack0\\Primitive2\\DUT5

    Returns the full path to the workspace directory, or None if not found.
    """
    latest_dir  = None
    latest_time = 0

    if workspace_mode == "primitive_dut":
        # The workspace IS the DUT* folder — find the latest one.
        # E.g. C:\test_program\Rack0\Primitive2\DUT5
        dut_pattern = os.path.join(workspace_folder, "Primitive*", "DUT*")
        dut_dirs = glob.glob(dut_pattern)
        if not dut_dirs:
            logger.warning(
                "[Collect] No Primitive*/DUT* folders found in "
                + workspace_folder
            )
            return None

        for dut_dir in dut_dirs:
            if not os.path.isdir(dut_dir):
                continue
            try:
                mtime = os.path.getmtime(dut_dir)
                if mtime > latest_time:
                    latest_time = mtime
                    latest_dir  = dut_dir
            except OSError:
                continue

        if latest_dir is None:
            logger.warning(
                "[Collect] No DUT* folders found in "
                + workspace_folder + "/Primitive*/"
            )
    else:
        # Flat mode — subfolders directly under workspace_folder
        try:
            for entry in os.listdir(workspace_folder):
                full = os.path.join(workspace_folder, entry)
                if not os.path.isdir(full):
                    continue
                mtime = os.path.getmtime(full)
                if mtime > latest_time:
                    latest_time = mtime
                    latest_dir  = full
        except OSError as e:
            logger.error(
                "[Collect] Cannot scan workspace folder: " + str(e)
            )
            return None

        if latest_dir is None:
            logger.warning(
                "[Collect] No workspace subfolders found in "
                + workspace_folder
            )

    return latest_dir


def _collect_workspace_results(jira_key, hostname, env, logger, xml_path=""):
    """
    After a successful LoadProfile, find the most recently modified workspace
    folder, then use the MANIFEST-BASED FILE SELECTION flow to let BENTO
    users choose which files to collect.

    Supports two workspace layouts:
      - "flat" (IBIR-0383/ABIT): C:\\Tanisys\\DNA2\\User\\Workspace\\<lot>
      - "primitive_dut" (MPT3HVM-0156/SFN2): C:\\test_program\\Rack0\\Primitive*\\DUT*\\<lot>

    Flow:
      1. Determine workspace folder + mode from TESTER_REGISTRY
      2. Find latest workspace subfolder
      3. Poll until REQUIRED files appear (resultsManager.db, Tracefile.txt)
      4. Scan ALL allow-listed files via scan_available_files()
      5. Write manifest JSON for BENTO GUI to read
      6. Wait for user selection JSON from BENTO (with timeout)
      7. Copy selected + required files via copy_selected_files()

    If BENTO does not respond within MANIFEST_SELECTION_TIMEOUT, only
    required files are copied (safe default — no data loss).

    Returns (collected_file_names, dest_dir) on success, or (None, None) on
    timeout/error.
    """
    workspace_folder, workspace_mode = _get_workspace_info(env)

    if not os.path.isdir(workspace_folder):
        logger.warning(
            "[Collect] Workspace folder not found: " + workspace_folder
        )
        return None, None

    logger.info(
        "[Collect] Workspace folder: " + workspace_folder
        + " (mode=" + workspace_mode + ")"
    )

    # ── Find the most recently modified workspace subfolder ────────────
    latest_dir = _find_latest_workspace_dir(
        workspace_folder, workspace_mode, logger
    )
    if latest_dir is None:
        return None, None

    workspace_name = os.path.basename(latest_dir)
    logger.info("[Collect] Latest workspace: " + latest_dir)

    # ── Build destination folder ──────────────────────────────────────
    tester_folder = ""
    if hostname and env:
        tester_folder = hostname + "_" + env.upper()
    elif hostname:
        tester_folder = hostname
    elif env:
        tester_folder = env.upper()

    # For primitive_dut mode, use PRIMITIVE{x}/DUT{y} subfolder structure
    # instead of the raw workspace_name (which is just "DUT7").
    # E.g. latest_dir = C:\test_program\Rack0\Primitive2\DUT7
    #   → dest subfolder = PRIMITIVE2\DUT7
    import re as _re
    _prim_num = "0"
    _dut_num  = "0"
    _rename_prefix = ""
    if workspace_mode == "primitive_dut":
        # Extract primitive and DUT folder names from the path
        _prim_match = _re.search(r'(?i)[/\\](primitive(\d+))[/\\]', latest_dir)
        _dut_match  = _re.search(r'(?i)[/\\](DUT(\d+))\s*$', latest_dir)
        _prim_name  = _prim_match.group(1).upper() if _prim_match else "PRIMITIVE0"
        _dut_name   = _dut_match.group(1).upper() if _dut_match else workspace_name.upper()
        if _prim_match:
            _prim_num = _prim_match.group(2)
        if _dut_match:
            _dut_num = _dut_match.group(2)
        _rename_prefix = "P" + _prim_num + "D" + _dut_num + "_"
        dest_subfolder = os.path.join(_prim_name, _dut_name)
    else:
        # ── Flat mode (IBIR): extract Primitive/DUT from checkout XML ──
        # The XML contains DutLocation="tester_flag,primitive,dut" which
        # is the authoritative source for the DUT position.
        # Workspace folder names (e.g. ANSKR20285608083800-2-0) use a
        # DIFFERENT numbering scheme and must NOT be used for the prefix.
        _xml_dut_extracted = False
        if xml_path and os.path.isfile(xml_path):
            try:
                _xtree = ET.parse(xml_path)
                _xroot = _xtree.getroot()
                _mat_info = _xroot.find("MaterialInfo")
                if _mat_info is not None:
                    for _attr_el in _mat_info.findall("Attribute"):
                        _loc_val = _attr_el.get("DutLocation", "").strip()
                        if _loc_val:
                            _loc_parts = _loc_val.split(",")
                            if len(_loc_parts) == 3:
                                _prim_num = _loc_parts[1].strip()
                                _dut_num  = _loc_parts[2].strip()
                                _rename_prefix = "P" + _prim_num + "D" + _dut_num + "_"
                                _xml_dut_extracted = True
                                logger.info(
                                    "[Collect] Flat mode — extracted Primitive="
                                    + _prim_num + ", DUT=" + _dut_num
                                    + " from XML DutLocation: " + _loc_val
                                )
                            break  # use first MaterialInfo entry
            except Exception as _xml_err:
                logger.warning(
                    "[Collect] Could not parse DutLocation from XML: "
                    + str(_xml_err)
                )

        # Fallback: extract from workspace folder name if XML not available
        if not _xml_dut_extracted:
            _flat_match = _re.search(r'-(\d+)-(\d+)$', workspace_name)
            if _flat_match:
                _prim_num = _flat_match.group(1)
                _dut_num  = _flat_match.group(2)
                _rename_prefix = "P" + _prim_num + "D" + _dut_num + "_"
                logger.info(
                    "[Collect] Flat mode — extracted Primitive="
                    + _prim_num + ", DUT=" + _dut_num
                    + " from workspace name (fallback): " + workspace_name
                )
            else:
                logger.info(
                    "[Collect] Flat mode — could not extract Primitive/DUT "
                    "from workspace name: " + workspace_name
                    + " — no rename prefix will be applied"
                )
        dest_subfolder = workspace_name

    if tester_folder:
        dest_dir = os.path.join(
            CHECKOUT_RESULTS_FOLDER, tester_folder, jira_key, dest_subfolder
        )
    else:
        dest_dir = os.path.join(
            CHECKOUT_RESULTS_FOLDER, jira_key, dest_subfolder
        )

    # ── MANIFEST-BASED FILE SELECTION (all workspace modes) ───────────
    # Poll until REQUIRED files appear, then use manifest-based selection
    # so the BENTO user can choose which files to collect.
    #
    # For primitive_dut (MPT/ADV): all required files must appear
    # For flat (IBIR): results_db + dispatcher_debug are required
    if workspace_mode == "primitive_dut":
        required_entries = [e for e in FILE_ALLOW_LIST if e["required"]]
    else:
        # Flat mode (IBIR): results_db + dispatcher_debug are required
        _flat_required_keys = {"results_db", "dispatcher_debug"}
        required_entries = [
            e for e in FILE_ALLOW_LIST
            if e["key"] in _flat_required_keys
        ]
    required_found   = {e["key"]: False for e in required_entries}

    logger.info(
        "[Collect] Waiting for required files to appear in workspace...\n"
        "          Workspace : " + latest_dir + "\n"
        "          Required  : " + ", ".join(
            e["key"] + " (" + e["subpath"] + ")"
            for e in required_entries
        ) + "\n"
        "          Timeout   : " + str(T_COLLECT_TIMEOUT) + "s "
        "(poll every " + str(T_COLLECT_POLL_INTERVAL) + "s)"
    )

    start_time = time.time()

    while True:
        elapsed = time.time() - start_time

        # Check each required file
        for entry in required_entries:
            if required_found[entry["key"]]:
                continue
            pattern = os.path.join(latest_dir, entry["subpath"])
            matched = glob.glob(pattern)
            # Fallback: if subpath has a directory prefix (e.g. "OS/"),
            # also try the filename directly in the workspace root.
            if not matched and "/" in entry["subpath"]:
                root_name = entry["subpath"].rsplit("/", 1)[-1]
                root_pattern = os.path.join(latest_dir, root_name)
                matched = glob.glob(root_pattern)
            if any(os.path.isfile(m) for m in matched):
                required_found[entry["key"]] = True
                logger.info(
                    "[Collect] \u2705 " + entry["key"] + " appeared "
                    "(after " + str(int(elapsed)) + "s)"
                )

        # All required found — proceed
        if all(required_found.values()):
            logger.info(
                "[Collect] All required files found after "
                + str(int(elapsed)) + "s"
            )
            break

        # Timeout check
        if elapsed >= T_COLLECT_TIMEOUT:
            missing = [k for k, v in required_found.items() if not v]
            logger.warning(
                "[Collect] \u26a0\ufe0f Timed out after " + str(int(elapsed))
                + "s. Missing required: " + ", ".join(missing)
            )
            # If NO required files found at all, give up
            if not any(required_found.values()):
                return None, None
            # Otherwise proceed with what we have
            break

        time.sleep(T_COLLECT_POLL_INTERVAL)

    # ── Step 3: Scan ALL allow-listed files ────────────────────────────
    logger.info("[Collect] Scanning workspace for all allow-listed files...")
    scan_results = scan_available_files(latest_dir, logger)

    found_count = sum(1 for e in scan_results if e["found"])
    total_count = len(scan_results)
    logger.info(
        "[Collect] Scan complete: " + str(found_count) + "/"
        + str(total_count) + " allow-listed files found"
    )

    # ── Step 4: Write manifest for BENTO GUI ──────────────────────────
    manifest_path = write_manifest(
        scan_results, jira_key, hostname, env, dest_dir, logger
    )
    if not manifest_path:
        logger.warning(
            "[Collect] Could not write manifest — "
            "falling back to required-only copy"
        )
        # Fall through to copy required files only
        selected_keys = None
    else:
        # ── Step 5: Wait for user selection from BENTO ────────────────
        # timeout=0 means wait indefinitely until user clicks OK
        selected_keys = read_user_selection(
            jira_key, hostname, dest_dir, logger,
            timeout=0,
            poll_interval=MANIFEST_POLL_INTERVAL,
        )

    # ── Step 6: Copy selected + required files ────────────────────────
    if selected_keys is None:
        # Timeout or manifest error — copy only required files
        selected_keys = [e["key"] for e in FILE_ALLOW_LIST if e["required"]]
        logger.info(
            "[Collect] Using required-only fallback: "
            + str(selected_keys)
        )

    # For flat mode (IBIR), always include dispatcher_debug — it is the
    # primary trace file (DispatcherDebug*.txt) and will be renamed to
    # P{x}D{y}_TraceFile.txt by copy_selected_files().
    if workspace_mode != "primitive_dut" and "dispatcher_debug" not in selected_keys:
        selected_keys.append("dispatcher_debug")
        logger.info(
            "[Collect] Auto-added dispatcher_debug for flat mode "
            "(IBIR trace file)"
        )

    copied_names, copied_count = copy_selected_files(
        scan_results, selected_keys, dest_dir, logger,
        rename_prefix=_rename_prefix,
    )

    if copied_count > 0:
        logger.info(
            "[Collect] \u2705 Copied " + str(copied_count)
            + " file(s) to " + dest_dir
        )
        return copied_names, dest_dir
    else:
        logger.warning("[Collect] No files were copied.")
        return None, None


# ── PLAYGROUND COMPLETION MONITOR ─────────────────────────────────────────────
class PlaygroundCompletionMonitor(object):
    """
    Detects playground creation completion (NOT test completion).

    The checkout process ends at "Create Playground" — there is no Load Recipe
    or Run Test step. This monitor confirms the playground was actually created
    by checking:

      METHOD 1 — SLATE log keywords: watches for "Playground Created",
                 "Playground Ready", or "Playground Setup Complete" in the
                 SLATE system log.

      METHOD 2 — Playground folder: watches SLATE_PLAYGROUND_FOLDER for new
                 files/folders appearing after the button click (indicates
                 SLATE has written playground data to disk).

      SAFETY  — Timeout watchdog (default 30 min — playground creation should
                take seconds, not hours).

      SAFETY  — Popup watchdog: catches fatal error popups from SLATE.

    Since slate_gui_trigger() already waits for the btnCreatePlayground click
    to return (pywinauto blocks until the button operation completes), this
    monitor serves as a SECONDARY confirmation that the playground was
    actually created on disk / in SLATE's internal state.

    Uses threading.Event — thread-safe, race-condition free.
    No f-strings — Python 2/3 compatible.
    """

    def __init__(self, xml_path, logger, timeout_minutes=30):
        self.xml_path          = xml_path
        self.logger            = logger
        self.timeout_seconds   = timeout_minutes * 60
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
                    "[PlaygroundMonitor] Completion via: " + method_name
                )

    def _monitor_log(self):
        """METHOD 1: Watch SLATE log for playground creation keywords."""
        success_words = [
            "Playground Created",
            "Playground Ready",
            "Playground Setup Complete",
            "Playground created successfully",
            "CreatePlayground completed",
        ]
        error_words = [
            "FATAL ERROR",
            "ABORTED",
            "Playground creation failed",
            "CreatePlayground failed",
        ]
        last_size = 0

        while not self._complete_event.is_set():
            try:
                if os.path.exists(SLATE_LOG_PATH):
                    size = os.path.getsize(SLATE_LOG_PATH)
                    if size != last_size:
                        with open(SLATE_LOG_PATH, "r") as lf:
                            lf.seek(last_size)
                            new_text = lf.read()
                        last_size = size
                        # Check errors first (higher priority)
                        for w in error_words:
                            if w in new_text:
                                self.logger.error(
                                    "[PlaygroundMonitor] Error keyword in log: " + w
                                )
                                self._signal_complete(
                                    "LOG_KEYWORD_ERROR", is_error=True
                                )
                                return
                        # Check success keywords
                        for w in success_words:
                            if w in new_text:
                                self.logger.info(
                                    "[PlaygroundMonitor] Success keyword in log: " + w
                                )
                                self._signal_complete("LOG_KEYWORD_PLAYGROUND")
                                return
            except Exception as e:
                self.logger.warning(
                    "[PlaygroundMonitor-Log] Error: " + str(e)
                )
            time.sleep(5)

    def _monitor_playground_folder(self):
        """METHOD 2: Watch playground folder for new files appearing."""
        # Snapshot existing files BEFORE playground creation
        initial_files = set()
        try:
            if os.path.isdir(SLATE_PLAYGROUND_FOLDER):
                initial_files = set(os.listdir(SLATE_PLAYGROUND_FOLDER))
        except Exception:
            pass

        stable_count    = 0
        required_stable = 2   # 2 consecutive checks with new stable files

        while not self._complete_event.is_set():
            try:
                if os.path.isdir(SLATE_PLAYGROUND_FOLDER):
                    current_files = set(os.listdir(SLATE_PLAYGROUND_FOLDER))
                    new_files = current_files - initial_files
                    if new_files:
                        # New files appeared — check if they're stable (not still being written)
                        sizes_now = {}
                        for f in new_files:
                            fp = os.path.join(SLATE_PLAYGROUND_FOLDER, f)
                            try:
                                sizes_now[f] = os.path.getsize(fp)
                            except OSError:
                                pass
                        time.sleep(3)
                        sizes_later = {}
                        for f in new_files:
                            fp = os.path.join(SLATE_PLAYGROUND_FOLDER, f)
                            try:
                                if os.path.exists(fp):
                                    sizes_later[f] = os.path.getsize(fp)
                            except OSError:
                                pass
                        if sizes_now == sizes_later and sizes_now:
                            stable_count += 1
                            if stable_count >= required_stable:
                                self.logger.info(
                                    "[PlaygroundMonitor] New stable files in playground: "
                                    + ", ".join(sorted(new_files)[:5])
                                )
                                self._signal_complete("PLAYGROUND_FOLDER")
                                return
                        else:
                            stable_count = 0
            except Exception as e:
                self.logger.warning(
                    "[PlaygroundMonitor-Folder] Error: " + str(e)
                )
            time.sleep(5)

    def _timeout_watchdog(self):
        """SAFETY: Hard timeout watchdog (default 30 min for playground creation)."""
        deadline = self.start_time + self.timeout_seconds
        while not self._complete_event.is_set():
            if time.time() > deadline:
                self.logger.error(
                    "[TIMEOUT] Playground creation exceeded "
                    + str(self.timeout_seconds // 60) + " min limit!"
                )
                self._signal_complete("TIMEOUT", is_error=True)
                return
            time.sleep(30)

    def _popup_watchdog(self):
        """SAFETY: Watch for blocking error popups during playground creation."""
        if not _PYWINAUTO_AVAILABLE:
            return

        dismiss_title_keywords = ["Error", "Fatal", "Abort", "Exception", "Fail"]

        while not self._complete_event.is_set():
            try:
                all_windows = _safe_desktop_windows(backend="win32")
                for w in all_windows:
                    try:
                        title = w.window_text().strip()
                        if not title or "SSD Tester Engineering GUI" in title:
                            continue

                        if any(kw.lower() in title.lower() for kw in dismiss_title_keywords):
                            err_text = title
                            try:
                                for c in w.children():
                                    if c.class_name() == "Static":
                                        txt = c.window_text()
                                        if txt:
                                            err_text += " - " + txt
                            except Exception:
                                pass

                            self.logger.error(
                                "[PopupWatchdog] Fatal popup during playground creation: "
                                + err_text
                            )

                            for btn_text in ["OK", "Close", "Yes", "Continue"]:
                                try:
                                    btn = w.child_window(
                                        title=btn_text, class_name="Button"
                                    )
                                    if btn.exists(timeout=1) and btn.is_enabled():
                                        btn.click_input()
                                        time.sleep(0.3)
                                        break
                                except Exception:
                                    pass

                            self._signal_complete("FATAL_POPUP", is_error=True)
                            return
                    except Exception:
                        continue
            except Exception as e:
                self.logger.debug("[PopupWatchdog] Scan error: " + str(e))

            time.sleep(10)

    def wait_for_completion(self):
        """
        Launch playground detection methods + watchdogs as daemon threads.
        Blocks with heartbeat until any method signals completion.
        Returns True on success, False on error/timeout.
        """
        t_log = threading.Thread(
            target=self._monitor_log,
            name="playground-monitor-log"
        )
        t_log.daemon = True

        t_folder = threading.Thread(
            target=self._monitor_playground_folder,
            name="playground-monitor-folder"
        )
        t_folder.daemon = True

        t_watchdog = threading.Thread(
            target=self._timeout_watchdog,
            name="playground-timeout-watchdog"
        )
        t_watchdog.daemon = True

        t_popup = threading.Thread(
            target=self._popup_watchdog,
            name="playground-popup-watchdog"
        )
        t_popup.daemon = True

        threads = [t_log, t_folder, t_watchdog, t_popup]

        for t in threads:
            t.start()

        # Heartbeat loop every 30 seconds (playground creation is fast)
        while not self._complete_event.wait(timeout=30):
            elapsed = int(time.time() - self.start_time)
            self.logger.info(
                "[Heartbeat] Playground creation running... "
                + str(elapsed) + "s"
                + " (method=" + self.completion_method + ")"
            )

        return not self._error_event.is_set()


# Keep SlateCompletionMonitor as an alias for backward compatibility
SlateCompletionMonitor = PlaygroundCompletionMonitor


# ── PARSE JIRA FROM FILENAME ──────────────────────────────────────────────────
def _parse_jira_from_xml_name(fname):
    """Extract JIRA key from filename.

    Matches JIRA-style keys like TSESSD-14270 (uppercase letters + dash + digits).
    Skips hostname-like parts (e.g. IBIR-0383, MPT3HVM-0156) by requiring
    the prefix to be all-alpha (no digits before the dash).

    Examples:
      checkout_TSESSD-14270_IBIR-0383_ABIT_...  -> TSESSD-14270
      Profile_IBIR-0383_ABIT_T0H074V2E_...      -> UNKNOWN (no JIRA in name)
    """
    import re
    parts = fname.replace(".xml", "").split("_")
    # First pass: strict JIRA pattern — all-alpha prefix + dash + digits
    for p in parts:
        if re.match(r'^[A-Za-z]+-\d+$', p):
            return p
    # Second pass: any part with dash + digits (legacy fallback)
    for p in parts:
        if "-" in p and any(c.isdigit() for c in p):
            return p
    return "UNKNOWN"


# ── PROCESS ONE XML ───────────────────────────────────────────────────────────
def process_checkout_xml(xml_path, env, logger):
    """
    Full pipeline for one checkout XML.

    Uses LoadProfile button — SLATE handles all steps automatically.
    XML is loaded directly from CHECKOUT_QUEUE_FOLDER (no hot folder copy).

    Steps:
      1. Validate XML integrity
      2. Write in_progress status
      3. Click LoadProfile in SLATE GUI via slate_gui_trigger()
         — XML is loaded directly from CHECKOUT_QUEUE_FOLDER
         → SLATE runs all steps internally (Recipe Selection, Manudata,
           Temp Traveler, Playground, Load Recipe, etc.)
      3b. Wait for "DUT load" popup (signals workspace creation)
          → Then poll workspace for resultsManager.db + DispatcherDebug*.txt
          → Copy both files to CHECKOUT_RESULTS folder
      4. Write success/failed status -> orchestrator wakes up
         (success = workspace result files copied to CHECKOUT_RESULTS)
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
        logger.info(
            "[<<] Checkout FAILED for " + fname
            + " — returning to poll loop for next XML"
        )
        return

    # Step 2: Write in_progress
    write_status(xml_path, "in_progress", "Checkout started by watcher")

    # ════════════════════════════════════════════════════════════════════
    # Step 3: Click LoadProfile in SLATE — GUI does everything automatically
    #         until "Dut load successful" popup appears
    #         XML is loaded directly from CHECKOUT_QUEUE (no hot folder copy)
    # ════════════════════════════════════════════════════════════════════

    # Pre-flight: confirm pywinauto is available before attempting GUI automation
    if not _PYWINAUTO_AVAILABLE:
        logger.error(
            "\u274c pywinauto is NOT installed on this machine. "
            "GUI automation cannot run. Install with: pip install pywinauto"
        )
        write_status(xml_path, "failed", "pywinauto not installed")
        logger.info(
            "[<<] Checkout FAILED for " + fname
            + " — returning to poll loop for next XML"
        )
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
            gui_success = slate_gui_trigger(xml_path, timeout=60)
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
        # Include fatal popup text if available — gives the user
        # actionable context (e.g. "Failed to Create Lot Cache —
        # Critical Attribute check failed: PCB_DESIGN_ID ...")
        fail_detail = _last_fatal_popup_text or ""
        fail_msg = (
            "slate_gui_trigger failed after "
            + str(MAX_GUI_ATTEMPTS) + " attempts"
        )
        if fail_detail:
            fail_msg += " — " + fail_detail[:500]  # cap length for status file
        logger.error("\u274c " + fail_msg)
        write_status(xml_path, "failed", fail_msg)
        logger.info(
            "[<<] Checkout FAILED for " + fname
            + " — returning to poll loop for next XML"
        )
        return
    # ════════════════════════════════════════════════════════════════════
    # END Step 3b — slate_gui_trigger() returned True
    # The DUT load popup has been detected and dismissed inside
    # slate_gui_trigger(), confirming that SLATE finished processing
    # and the workspace folder has been created.
    # ════════════════════════════════════════════════════════════════════

    # ── Step 3c: Write "collecting" status so BENTO can start manifest
    #    polling immediately (breaks the deadlock where BENTO waits for
    #    "success" before polling, but watcher waits for BENTO's selection
    #    before writing "success").
    write_status(
        xml_path, "collecting",
        "SLATE completed — collecting workspace results"
    )
    logger.info("[~] Status written: collecting (SLATE done, starting file collection)")

    logger.info("[~] Collecting workspace results (resultsManager.db + DispatcherDebug*.txt)...")
    collected_files, results_folder = _collect_workspace_results(
        jira_key, hostname, env, logger, xml_path=xml_path
    )
    if not collected_files:
        write_status(
            xml_path, "failed",
            "Workspace result collection failed — "
            "resultsManager.db and/or DispatcherDebug*.txt not found"
        )
        logger.error("[FAIL] Workspace result collection failed: " + fname)
        logger.info(
            "[<<] Checkout FAILED for " + fname
            + " — returning to poll loop for next XML"
        )
        return

    logger.info("[OK] Workspace results collected successfully: " + fname)

    # Step 4: Write success status -> orchestrator wakes up
    # Checkout is considered successful once the workspace result files
    # (resultsManager.db + DispatcherDebug*.txt) have been copied to
    # CHECKOUT_RESULTS.
    write_status(
        xml_path, "success",
        "Checkout complete (workspace results collected)",
        collected_files=collected_files,
        output_folder=results_folder or "",
    )
    logger.info("[OK] Status written: success - " + fname)
    logger.info(
        "[<<] Checkout COMPLETE for " + fname
        + " — returning to poll loop for next XML"
    )


def _iter_queue_files(suffix):
    """Yield (filepath, filename) for all files matching *suffix* in the
    CHECKOUT_QUEUE tree (supports both new subfolder layout and legacy flat)."""
    if not os.path.isdir(CHECKOUT_QUEUE_FOLDER):
        return
    for root, _dirs, files in os.walk(CHECKOUT_QUEUE_FOLDER):
        for fname in files:
            if fname.endswith(suffix):
                yield os.path.join(root, fname), fname


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
    removed = 0
    try:
        for lock_path, fname in _iter_queue_files(".checkout_lock"):
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
    now = time.time()
    removed = 0
    try:
        for lock_path, fname in _iter_queue_files(".checkout_lock"):
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
    reset_count = 0
    logger.info("Startup: scanning for orphaned in_progress checkout status files...")

    try:
        for status_path, fname in _iter_queue_files(".checkout_status"):
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


# ── LISTFILES HANDLER (for BENTO GUI remote file browser) ─────────────────────
def _handle_listfiles_requests(logger):
    """
    Process any pending .listfiles request JSON files in CHECKOUT_QUEUE.

    Protocol:
      1. BENTO GUI writes:  CHECKOUT_QUEUE/listfiles_<hostname>_<ts>.json
         containing {"command": "listfiles", "path": "C:\\...", "hostname": "..."}
      2. This handler reads the request, lists the local directory, and writes
         the result to:  CHECKOUT_RESULTS/listfiles_<hostname>_<ts>.json
         containing {"status": "ok", "path": "...", "entries": [...]}
      3. BENTO GUI polls for the result file and displays it.

    This allows the BENTO GUI to browse files on the tester machine without
    needing direct UNC/C$ admin share access (which requires net use auth).
    """
    if not os.path.isdir(CHECKOUT_QUEUE_FOLDER):
        return

    for fname in os.listdir(CHECKOUT_QUEUE_FOLDER):
        if not fname.startswith("listfiles_") or not fname.endswith(".json"):
            continue

        req_path = os.path.join(CHECKOUT_QUEUE_FOLDER, fname)
        resp_path = os.path.join(CHECKOUT_RESULTS_FOLDER, fname)

        try:
            with open(req_path, "r") as f:
                request = json.load(f)
        except Exception as e:
            logger.warning("[listfiles] Cannot read request " + fname + ": " + str(e))
            # Remove bad request
            try:
                os.remove(req_path)
            except OSError:
                pass
            continue

        if request.get("command") != "listfiles":
            continue

        target_path = request.get("path", "")
        logger.info("[listfiles] Request for: " + target_path)

        # Build response
        response = {"status": "ok", "path": target_path, "entries": []}

        try:
            if not os.path.isdir(target_path):
                response["status"] = "error"
                response["error"] = "Directory not found: " + target_path
            else:
                entries = []
                for name in sorted(os.listdir(target_path)):
                    full = os.path.join(target_path, name)
                    try:
                        if os.path.isdir(full):
                            entries.append({
                                "name": name,
                                "type": "folder",
                                "size": 0,
                            })
                        else:
                            try:
                                size = os.path.getsize(full)
                            except OSError:
                                size = 0
                            entries.append({
                                "name": name,
                                "type": "file",
                                "size": size,
                            })
                    except OSError:
                        entries.append({
                            "name": name,
                            "type": "file",
                            "size": 0,
                        })
                response["entries"] = entries
                logger.info(
                    "[listfiles] Listed " + str(len(entries))
                    + " entries in " + target_path
                )
        except OSError as e:
            response["status"] = "error"
            response["error"] = str(e)
            logger.error("[listfiles] Error listing " + target_path + ": " + str(e))

        # Write response
        try:
            if not os.path.isdir(CHECKOUT_RESULTS_FOLDER):
                os.makedirs(CHECKOUT_RESULTS_FOLDER)
            with open(resp_path, "w") as f:
                json.dump(response, f, indent=2)
            logger.info("[listfiles] Response written: " + resp_path)
        except OSError as e:
            logger.error("[listfiles] Cannot write response: " + str(e))

        # Remove request file
        try:
            os.remove(req_path)
        except OSError:
            pass


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

    # ── Determine this watcher's queue subfolder ─────────────────────────────
    # New structure: CHECKOUT_QUEUE/<hostname>/<jira_key>/*.xml
    # The watcher scans its own hostname subfolder for all jira subfolders.
    watcher_queue = os.path.join(CHECKOUT_QUEUE_FOLDER, hostname) if hostname else CHECKOUT_QUEUE_FOLDER

    # ── Snapshot pre-existing XMLs (path-based, clock-skew safe) ─────────────
    # Record the exact set of XML paths that exist RIGHT NOW and skip only
    # those. Any XML that arrives after this snapshot is processed normally.
    # This replaces the broken mtime vs watcher_start_time comparison which
    # fails when the local PC clock and tester clock differ.
    pre_existing = set()
    if os.path.isdir(watcher_queue):
        for _jira_dir in os.listdir(watcher_queue):
            _jira_path = os.path.join(watcher_queue, _jira_dir)
            if not os.path.isdir(_jira_path):
                # Also check root-level XMLs for backward compatibility
                if _jira_dir.lower().endswith(".xml") and ".checkout_" not in _jira_dir:
                    pre_existing.add(os.path.join(watcher_queue, _jira_dir))
                continue
            for _f in os.listdir(_jira_path):
                if _f.lower().endswith(".xml") and ".checkout_" not in _f:
                    pre_existing.add(os.path.join(_jira_path, _f))
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
    skipped_logged  = set()   # XMLs already logged as skipped (avoid log spam)
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
                # Also prune skipped_logged for XMLs that no longer exist
                skipped_logged = {p for p in skipped_logged if os.path.isfile(p)}

            # Self-heal stale locks every poll — clears locks left by a
            # crashed watcher within one poll cycle, not 30 minutes.
            _cleanup_stale_checkout_locks(logger)

            # ── Handle listfiles requests from BENTO GUI ─────────────────
            try:
                _handle_listfiles_requests(logger)
            except Exception as lf_err:
                logger.debug("[listfiles] Error: " + str(lf_err))

            # ── Collect XML paths from subfolder structure ────────────────
            # New layout: CHECKOUT_QUEUE/<hostname>/<jira_key>/*.xml
            # Also supports legacy flat layout for backward compatibility.
            _xml_candidates = []
            if os.path.isdir(watcher_queue):
                for _entry in os.listdir(watcher_queue):
                    _entry_path = os.path.join(watcher_queue, _entry)
                    if os.path.isdir(_entry_path):
                        # Jira subfolder — scan for XMLs inside
                        try:
                            for _xf in os.listdir(_entry_path):
                                if _xf.lower().endswith(".xml") and ".checkout_" not in _xf:
                                    _xml_candidates.append(os.path.join(_entry_path, _xf))
                        except OSError:
                            pass
                    elif _entry.lower().endswith(".xml") and ".checkout_" not in _entry:
                        # Legacy: XML directly in watcher_queue (backward compat)
                        _xml_candidates.append(_entry_path)
            _xml_candidates.sort(key=lambda p: os.path.basename(p))

            # ── Process each XML candidate ───────────────────────────────
            for xml_path in _xml_candidates:
                fname = os.path.basename(xml_path)

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
                    if xml_path not in skipped_logged:
                        logger.debug(
                            "[SKIP] env_tag '" + env_tag
                            + "' not found in: " + fname
                        )
                        skipped_logged.add(xml_path)
                    continue
                if hostname_tag and hostname_tag.upper() not in fname_upper:
                    if xml_path not in skipped_logged:
                        logger.debug(
                            "[SKIP] hostname_tag '" + hostname_tag
                            + "' not found in: " + fname
                        )
                        skipped_logged.add(xml_path)
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
    logger.info("XML source    : " + CHECKOUT_QUEUE_FOLDER
                + " (direct load, no hot folder)")
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
