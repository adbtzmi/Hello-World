# -*- coding: utf-8 -*-
"""
model/orchestrators/result_collector.py
========================================
BENTO Result Collector — Task 2

Auto-detects test completion on IBIR / MPT (ADV) testers,
collects tracefile.txt + resultsManager.db to a shared folder,
optionally spools summary, and sends Teams notification.

Based on the reference ExtractTrace.py script, refactored into
the BENTO MVC architecture with background polling and GUI callbacks.

Supports:
  - Auto-detect test completion by polling workspace XML status
  - Collect trace/db files to shared network folder
  - Spool summary (optional, per-MID preference)
  - Teams notification on all-complete
  - User-configurable additional files to extract
"""

import os
import ast
import glob
import shutil
import zipfile
import logging
import threading
import traceback
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Dict, List, Optional, Callable, Tuple, Any

# Import auto-consolidator for automated result analysis
try:
    from model.analyzers.auto_consolidator import AutoConsolidator
except ImportError:
    AutoConsolidator = None  # Graceful degradation if module not available

logger = logging.getLogger("bento_app")

# ── Site-specific shared folder paths ─────────────────────────────────────────
SITE_TRACE_PATHS = {
    "PENANG":    r"\\pgfsmodauto\modauto\temp\Dom_Q\0_Trace",
    "SINGAPORE": r"\\sifsmodauto\modauto\release\SSD\temp\Dom_P\0_Trace",
}

# ── Default polling interval (seconds) ───────────────────────────────────────
DEFAULT_POLL_INTERVAL = 30

# ── Local tester paths (used as base for UNC conversion) ─────────────────────
IBIR_WORKSPACE_LOCAL = r"C:\Tanisys\DNA2\User\Workspace"
IBIR_CONFIG_LOCAL    = r"C:\ModAuto\SSDTesterCtrlr\SysState.xml"
ADV_WORKSPACE_LOCAL  = r"C:\test_program\Rack0"

# ── Checkout results folder (watcher writes collected files here) ────────────
CHECKOUT_RESULTS_FOLDER = r"P:\temp\BENTO\CHECKOUT_RESULTS"

# ── Test status constants ────────────────────────────────────────────────────
STATUS_PASS       = "PASS"
STATUS_FAIL       = "FAIL"
STATUS_RUNNING    = "RUNNING"
STATUS_UNKNOWN    = "UNKNOWN"
STATUS_NOT_FOUND  = "NOT_FOUND"
STATUS_ERROR      = "ERROR"
STATUS_COLLECTED  = "COLLECTED"


# ══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ══════════════════════════════════════════════════════════════════════════════

class MIDEntry:
    """Represents a single MID (drive/DUT) entry from MIDs.txt."""

    def __init__(self, mid: str, location: str, name: str, spool: bool = False):
        self.mid      = mid
        self.location = location   # e.g. "P1D1"
        self.name     = name       # e.g. "LBAF" or "6500_U3"
        self.spool    = spool      # whether to spool summary
        self.status   = STATUS_RUNNING
        self.fail_reg  = ""
        self.fail_code = ""
        self.workspace_path = ""   # resolved workspace path
        self.collected = False     # whether files have been collected
        self.error_msg = ""        # any error message
        self.consolidation_results: Optional[Dict] = None  # dict from AutoConsolidator.consolidate()

    def result_line(self) -> str:
        """Format a human-readable result line."""
        if self.status == STATUS_PASS:
            return f" {self.mid:<10} [{self.location:<5}] {self.name:<30}: PASS"
        elif self.status == STATUS_FAIL:
            return (f" {self.mid:<10} [{self.location:<5}] {self.name:<30}: "
                    f"FAIL ({self.fail_reg:<30} - {self.fail_code})")
        elif self.status == STATUS_RUNNING:
            return f" {self.mid:<10} [{self.location:<5}] {self.name:<30}: RUNNING"
        else:
            return (f" {self.mid:<10} [{self.location:<5}] {self.name:<30}: "
                    f"{self.status}")

    def to_dict(self) -> dict:
        """Serialize to dict for GUI display."""
        return {
            "mid":       self.mid,
            "location":  self.location,
            "name":      self.name,
            "spool":     self.spool,
            "status":    self.status,
            "fail_reg":  self.fail_reg,
            "fail_code": self.fail_code,
            "collected": self.collected,
            "error":     self.error_msg,
        }


# ══════════════════════════════════════════════════════════════════════════════
# UNC PATH HELPER
# ══════════════════════════════════════════════════════════════════════════════

def _to_unc(local_path: str, hostname: str) -> str:
    """
    Convert a local Windows path to a UNC admin-share path.

    Examples:
        _to_unc(r"C:\\Tanisys\\DNA2", "IBIR-0383")
        → r"\\\\IBIR-0383\\C$\\Tanisys\\DNA2"

        _to_unc(r"C:\\test_program\\rack0", "MPT3HVM-0156")
        → r"\\\\MPT3HVM-0156\\C$\\test_program\\rack0"

    If hostname is empty, returns the local path unchanged (local mode).
    """
    if not hostname:
        return local_path
    # Normalize to backslashes
    p = local_path.replace("/", "\\")
    # Convert "C:\path" → "\\hostname\C$\path"
    if len(p) >= 2 and p[1] == ":":
        drive = p[0].upper()
        rest  = p[2:]  # e.g. "\Tanisys\DNA2\..."
        return f"\\\\{hostname}\\{drive}${rest}"
    # Already a UNC path or relative — return as-is
    return p


# ══════════════════════════════════════════════════════════════════════════════
# MIDs.txt PARSER
# ══════════════════════════════════════════════════════════════════════════════

def parse_mids_file(filepath: str) -> Dict[str, MIDEntry]:
    """
    Parse a MIDs.txt file into a dict of MIDEntry objects.

    Format per line:
        MID  LOCATION  FILE_NAME  SPOOL(True/False)
    Lines starting with $ or # are skipped (comments / already-collected).
    """
    entries = {}
    with open(filepath, "r") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("$") or stripped.startswith("#"):
                continue
            parts = stripped.split()
            if len(parts) < 3:
                continue
            mid  = parts[0]
            loc  = parts[1]
            name = parts[2]
            try:
                spool = ast.literal_eval(parts[3]) if len(parts) > 3 else False
            except (ValueError, IndexError):
                spool = False
            entries[mid] = MIDEntry(mid, loc, name, spool)
    return entries


def mark_collected_in_mids_file(filepath: str, collected_mids: List[str]):
    """
    Re-read MIDs.txt and prefix collected MIDs with '$ ' to mark them done.
    """
    with open(filepath, "r") as f:
        lines = f.readlines()

    modified = []
    for line in lines:
        parts = line.split()
        if parts and parts[0] in collected_mids:
            modified.append("$ " + line)
        else:
            modified.append(line)

    with open(filepath, "w") as f:
        f.writelines(modified)


# ══════════════════════════════════════════════════════════════════════════════
# WORKSPACE RESOLUTION
# ══════════════════════════════════════════════════════════════════════════════

def resolve_ibir_workspaces(
    mid_entries: Dict[str, MIDEntry],
    log_callback: Optional[Callable] = None,
    tester_hostname: str = "",
) -> Dict[str, MIDEntry]:
    """
    Resolve workspace paths for IBIR testers.
    Reads SysState.xml to find workspace IDs, then verifies MID in tmptravl.dat.

    If tester_hostname is provided, paths are converted to UNC admin shares
    (e.g. \\\\IBIR-0383\\C$\\Tanisys\\...) for remote access.
    """
    path_to_workspace = _to_unc(IBIR_WORKSPACE_LOCAL, tester_hostname)
    path_to_config    = _to_unc(IBIR_CONFIG_LOCAL, tester_hostname)

    try:
        tree  = ET.parse(path_to_config)
        root  = tree.getroot()
        child = root.find("TesterHardware/Rack/Primitives")
    except Exception as e:
        _log(log_callback, f"✗ Cannot parse SysState.xml: {e}")
        return mid_entries

    if child is None:
        _log(log_callback, "✗ Primitives element not found in SysState.xml")
        return mid_entries

    for mid, entry in list(mid_entries.items()):
        try:
            loc = entry.location
            primitive_id = loc[1]       # e.g. "P1D1" -> "1"
            dut_id       = loc[3:]      # e.g. "P1D1" -> "1"

            subchild = child.find(
                f"Primitive[@id='{primitive_id}']/DUTs/DUT[@id='{dut_id}']/BinData/messageHeader"
            )
            if subchild is None:
                entry.status = STATUS_NOT_FOUND
                entry.error_msg = f"DUT {loc} not found in SysState.xml"
                _log(log_callback, f" ⚠ {mid}: DUT {loc} not found in SysState.xml")
                continue

            workspace = subchild.get("workspaceID") or ""
            travl_path = os.path.join(path_to_workspace, workspace, "tmptravl.dat")

            if not os.path.exists(travl_path):
                entry.status = STATUS_NOT_FOUND
                entry.error_msg = f"tmptravl.dat not found at {travl_path}"
                _log(log_callback, f" ⚠ {mid}: tmptravl.dat not found")
                continue

            with open(travl_path, "r") as f:
                for line in f:
                    if "MID: " in line:
                        found_mid = line.split(": ")[1].strip()
                        if found_mid == mid:
                            entry.workspace_path = os.path.join(
                                path_to_workspace, workspace
                            )
                        else:
                            entry.status = STATUS_NOT_FOUND
                            entry.error_msg = (
                                f"MID mismatch at {loc}: "
                                f"expected {mid}, found {found_mid}"
                            )
                            _log(log_callback,
                                 f" ⚠ {mid} not found at {loc} "
                                 f"(found {found_mid})")
                        break
        except Exception as e:
            entry.status = STATUS_ERROR
            entry.error_msg = str(e)
            _log(log_callback, f" ⚠ Error resolving {mid}: {e}")

    return mid_entries


def _find_checkout_results_dut(tester_hostname: str, primitive_id: str,
                                dut_id: str) -> str:
    """
    Search CHECKOUT_RESULTS for a DUT folder that the watcher already
    collected.  Returns the path if found, empty string otherwise.

    Pattern: CHECKOUT_RESULTS/<hostname>_*/<jira>/PRIMITIVE{X}/DUT{Y}/
    """
    if not os.path.isdir(CHECKOUT_RESULTS_FOLDER):
        return ""
    prefix = f"{tester_hostname}_".upper()
    prim_name = f"PRIMITIVE{primitive_id}"
    dut_name  = f"DUT{dut_id}"
    try:
        for tester_dir in os.listdir(CHECKOUT_RESULTS_FOLDER):
            if not tester_dir.upper().startswith(prefix):
                continue
            tester_path = os.path.join(CHECKOUT_RESULTS_FOLDER, tester_dir)
            if not os.path.isdir(tester_path):
                continue
            for jira_dir in os.listdir(tester_path):
                dut_path = os.path.join(
                    tester_path, jira_dir, prim_name, dut_name
                )
                if os.path.isdir(dut_path):
                    return dut_path
    except OSError:
        pass
    return ""


def resolve_adv_workspaces(
    mid_entries: Dict[str, MIDEntry],
    log_callback: Optional[Callable] = None,
    tester_hostname: str = "",
) -> Dict[str, MIDEntry]:
    """
    Resolve workspace paths for ADV (MPT) testers.

    The watcher on the tester already collects results (resultsManager.db,
    TraceFile.txt, summary.zip) into CHECKOUT_RESULTS on the shared drive.
    This function looks there directly — no UNC admin share needed.

    CHECKOUT_RESULTS path pattern:
      P:\\temp\\BENTO\\CHECKOUT_RESULTS\\<hostname>_<env>\\<jira>\\PRIMITIVE{X}\\DUT{Y}
    """
    for mid, entry in list(mid_entries.items()):
        try:
            loc = entry.location
            primitive_id = loc[1]       # e.g. "P2D7" -> "2"
            dut_id       = loc[3:]      # e.g. "P2D7" -> "7"

            # Check CHECKOUT_RESULTS for watcher-collected files
            cr_path = _find_checkout_results_dut(
                tester_hostname, primitive_id, dut_id
            )
            if cr_path:
                entry.workspace_path = cr_path
                entry.status = STATUS_PASS
                entry.collected = True
                _log(log_callback,
                     f" ✓ {mid}: found in CHECKOUT_RESULTS → {cr_path}")
            else:
                # Not yet collected — watcher may still be running
                entry.status = STATUS_NOT_FOUND
                entry.error_msg = "Waiting for watcher to collect results"
                _log(log_callback,
                     f" ⚠ {mid}: waiting for watcher to collect results...")
        except Exception as e:
            entry.status = STATUS_ERROR
            entry.error_msg = str(e)
            _log(log_callback, f" ⚠ Error resolving {mid}: {e}")

    return mid_entries


# ══════════════════════════════════════════════════════════════════════════════
# TEST STATUS CHECKING
# ══════════════════════════════════════════════════════════════════════════════

def check_test_status_ibir(entry: MIDEntry) -> MIDEntry:
    """Check test status for an IBIR MID by reading *_UPDATE_ATTRS.xml in OS subfolder."""
    if not entry.workspace_path:
        return entry
    try:
        xml_files = glob.glob(
            os.path.join(entry.workspace_path, "OS", "*_UPDATE_ATTRS.xml")
        )
        if not xml_files:
            entry.status = STATUS_RUNNING
            return entry

        tree = ET.parse(xml_files[0])
        root = tree.getroot()
        test_status = root.find("testStatus")
        fail_reg    = root.find("failreg")
        fail_code   = root.find("failcode")

        if test_status is not None and test_status.text:
            status_text = test_status.text.upper()
            if status_text == "PASS":
                entry.status = STATUS_PASS
            elif status_text == "FAIL":
                entry.status = STATUS_FAIL
                entry.fail_reg  = (fail_reg.text  or "") if fail_reg  is not None else ""
                entry.fail_code = (fail_code.text or "") if fail_code is not None else ""
            else:
                entry.status = STATUS_UNKNOWN
        else:
            entry.status = STATUS_RUNNING
    except Exception as e:
        entry.error_msg = str(e)
        # Don't change status — might still be running
    return entry


def check_test_status_adv(entry: MIDEntry) -> MIDEntry:
    """Check test status for an ADV (MPT) MID by reading *_UPDATE_ATTRS.xml."""
    if not entry.workspace_path:
        return entry
    try:
        xml_files = glob.glob(
            os.path.join(entry.workspace_path, "*_UPDATE_ATTRS.xml")
        )
        if not xml_files:
            entry.status = STATUS_RUNNING
            return entry

        tree = ET.parse(xml_files[0])
        root = tree.getroot()
        test_status = root.find("testStatus")
        fail_reg    = root.find("failreg")
        fail_code   = root.find("failcode")

        if test_status is not None and test_status.text:
            status_text = test_status.text.upper()
            if status_text == "PASS":
                entry.status = STATUS_PASS
            elif status_text == "FAIL":
                entry.status = STATUS_FAIL
                entry.fail_reg  = (fail_reg.text  or "") if fail_reg  is not None else ""
                entry.fail_code = (fail_code.text or "") if fail_code is not None else ""
            else:
                entry.status = STATUS_UNKNOWN
        else:
            entry.status = STATUS_RUNNING
    except Exception as e:
        entry.error_msg = str(e)
    return entry


# ══════════════════════════════════════════════════════════════════════════════
# FILE COLLECTION
# ══════════════════════════════════════════════════════════════════════════════

def collect_files_ibir(
    entry: MIDEntry,
    target_path: str,
    additional_patterns: Optional[List[str]] = None,
    log_callback: Optional[Callable] = None,
) -> bool:
    """
    Collect trace + DB files for an IBIR MID.
    - DispatcherDebug*.txt  -> {name}.txt
    - OS/resultsManager*.db -> {name}.db
    - Any additional file patterns the user specified
    """
    if not entry.workspace_path:
        return False

    path = entry.workspace_path
    collected_any = False

    try:
        # Trace file (DispatcherDebug*.txt)
        for f in glob.glob(os.path.join(path, "*.txt")):
            if "DispatcherDebug" in os.path.basename(f):
                target = os.path.join(target_path, f"{entry.name}.txt")
                shutil.copy(f, target)
                _log(log_callback, f"    ✓ Copied trace → {entry.name}.txt")
                collected_any = True
                break

        # Results DB (OS/resultsManager*.db)
        for f in glob.glob(os.path.join(path, "OS", "*.db")):
            if "resultsManager" in os.path.basename(f):
                target = os.path.join(target_path, f"{entry.name}.db")
                shutil.copy(f, target)
                _log(log_callback, f"    ✓ Copied DB → {entry.name}.db")
                collected_any = True
                break

        # Additional user-specified file patterns
        if additional_patterns:
            for pattern in additional_patterns:
                for f in glob.glob(os.path.join(path, "**", pattern), recursive=True):
                    basename = os.path.basename(f)
                    target = os.path.join(target_path, f"{entry.name}_{basename}")
                    shutil.copy(f, target)
                    _log(log_callback, f"    ✓ Copied additional → {entry.name}_{basename}")
                    collected_any = True

        entry.collected = collected_any
    except Exception as e:
        entry.error_msg = f"File collection error: {e}"
        _log(log_callback, f"    ✗ Error collecting files for {entry.mid}: {e}")
        return False

    return collected_any


def collect_files_adv(
    entry: MIDEntry,
    target_path: str,
    additional_patterns: Optional[List[str]] = None,
    log_callback: Optional[Callable] = None,
) -> bool:
    """
    Collect trace + DB files for an ADV (MPT) MID.
    - TraceFile*.txt        -> {name}.txt
    - resultsManager*.db    -> {name}.db
    - Any additional file patterns the user specified
    """
    if not entry.workspace_path:
        return False

    path = entry.workspace_path
    collected_any = False

    try:
        # Trace file (TraceFile*.txt)
        for f in glob.glob(os.path.join(path, "*.txt")):
            if "TraceFile" in os.path.basename(f):
                target = os.path.join(target_path, f"{entry.name}.txt")
                shutil.copy(f, target)
                _log(log_callback, f"    ✓ Copied trace → {entry.name}.txt")
                collected_any = True
                break

        # Results DB (resultsManager*.db)
        for f in glob.glob(os.path.join(path, "*.db")):
            if "resultsManager" in os.path.basename(f):
                target = os.path.join(target_path, f"{entry.name}.db")
                shutil.copy(f, target)
                _log(log_callback, f"    ✓ Copied DB → {entry.name}.db")
                collected_any = True
                break

        # Additional user-specified file patterns
        if additional_patterns:
            for pattern in additional_patterns:
                for f in glob.glob(os.path.join(path, "**", pattern), recursive=True):
                    basename = os.path.basename(f)
                    target = os.path.join(target_path, f"{entry.name}_{basename}")
                    shutil.copy(f, target)
                    _log(log_callback, f"    ✓ Copied additional → {entry.name}_{basename}")
                    collected_any = True

        entry.collected = collected_any
    except Exception as e:
        entry.error_msg = f"File collection error: {e}"
        _log(log_callback, f"    ✗ Error collecting files for {entry.mid}: {e}")
        return False

    return collected_any


# ══════════════════════════════════════════════════════════════════════════════
# SPOOL SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

def spool_summary_ibir(
    entry: MIDEntry,
    original_dir: str,
    log_callback: Optional[Callable] = None,
) -> bool:
    """Spool summary for an IBIR MID (extract summary.zip and run SummaryWizard.py)."""
    if not entry.workspace_path or not entry.spool:
        return False

    path = entry.workspace_path
    summary_zip = os.path.join(path, "summary.zip")

    if not os.path.exists(summary_zip):
        _log(log_callback, f"    ⚠ summary.zip not found for {entry.mid}")
        return False

    try:
        _log(log_callback, f"    Spooling summary for MID: {entry.mid}")
        summary_dir = os.path.join(path, "summary")
        with zipfile.ZipFile(summary_zip, "r") as zip_ref:
            zip_ref.extractall(summary_dir)
        os.chdir(summary_dir)
        os.system("python SummaryWizard.py")
        os.chdir(original_dir)
        return True
    except Exception as e:
        _log(log_callback, f"    ✗ Spool summary failed for {entry.mid}: {e}")
        try:
            os.chdir(original_dir)
        except Exception:
            pass
        return False


def spool_summary_adv(
    entry: MIDEntry,
    original_dir: str,
    log_callback: Optional[Callable] = None,
) -> bool:
    """Spool summary for an ADV (MPT) MID (run GenSum.pyc)."""
    if not entry.workspace_path or not entry.spool:
        return False

    path = entry.workspace_path
    loc  = entry.location

    try:
        _log(log_callback, f"    Spooling summary for MID: {entry.mid}")
        os.chdir(path)
        primitive_id = loc[1]
        dut_id       = loc[3:]
        os.system(f"python GenSum.pyc p{primitive_id} d{dut_id}")
        os.chdir(original_dir)
        return True
    except Exception as e:
        _log(log_callback, f"    ✗ Spool summary failed for {entry.mid}: {e}")
        try:
            os.chdir(original_dir)
        except Exception:
            pass
        return False


# ══════════════════════════════════════════════════════════════════════════════
# TEAMS NOTIFICATION
# ══════════════════════════════════════════════════════════════════════════════

def send_result_teams_notification(
    machine:     str,
    site:        str,
    entries:     Dict[str, MIDEntry],
    elapsed:     int,
    webhook_url: str  = "",
    log_callback: Optional[Callable] = None,
) -> bool:
    """
    Send Teams notification with test result summary.
    Non-fatal — collection succeeded even if notification fails.
    """
    url = (webhook_url
           or os.environ.get("BENTO_TEAMS_WEBHOOK", ""))

    if not url:
        _log(log_callback,
             "⚠ Teams webhook URL not configured — skipping result notification.")
        return False

    # Build summary
    total     = len(entries)
    passed    = sum(1 for e in entries.values() if e.status == STATUS_PASS)
    failed    = sum(1 for e in entries.values() if e.status == STATUS_FAIL)
    running   = sum(1 for e in entries.values() if e.status == STATUS_RUNNING)
    collected = sum(1 for e in entries.values() if e.collected)

    all_done = (running == 0)
    icon     = "✅" if failed == 0 and all_done else ("⚠️" if all_done else "🔄")

    # Build facts for each MID
    facts = []
    for mid, entry in entries.items():
        status_icon = {"PASS": "✅", "FAIL": "❌", "RUNNING": "🔄"}.get(
            entry.status, "❓"
        )
        value = f"{status_icon} {entry.status}"
        if entry.status == STATUS_FAIL:
            value += f" ({entry.fail_reg} - {entry.fail_code})"
        if entry.collected:
            value += " [Collected]"
        facts.append({"name": f"{entry.mid} [{entry.location}] {entry.name}", "value": value})

    elapsed_str = f"{elapsed // 60}m {elapsed % 60}s" if elapsed else "N/A"

    try:
        import urllib.request
        import json
        from datetime import datetime

        color     = "Good" if (failed == 0 and all_done) else "Attention"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Summary facts
        summary_facts = [
            {"title": "Total MIDs",  "value": str(total)},
            {"title": "Passed",      "value": str(passed)},
            {"title": "Failed",      "value": str(failed)},
            {"title": "Running",     "value": str(running)},
            {"title": "Collected",   "value": str(collected)},
        ]

        # Per-MID detail rows
        mid_rows = []
        for mid, entry in entries.items():
            status_icon = {"PASS": "✅", "FAIL": "❌", "RUNNING": "🔄"}.get(
                entry.status, "❓"
            )
            value = f"{status_icon} {entry.status}"
            if entry.status == STATUS_FAIL:
                value += f" ({entry.fail_reg} - {entry.fail_code})"
            if entry.collected:
                value += " [Collected]"
            mid_rows.append({
                "title": f"{entry.mid} [{entry.location}] {entry.name}",
                "value": value,
            })

        # Build Adaptive Card body
        body = [
            {
                "type":   "TextBlock",
                "size":   "Medium",
                "weight": "Bolder",
                "text":   f"{icon} BENTO Test Result Collection",
                "color":  color,
            },
            {
                "type":   "TextBlock",
                "text":   f"Tester: **{machine}**  |  Site: **{site}**  |  Elapsed: **{elapsed_str}**  |  {timestamp}",
                "wrap":   True,
                "size":   "Small",
            },
            {
                "type":  "FactSet",
                "facts": summary_facts,
            },
        ]

        # Add per-MID details if any
        if mid_rows:
            body.append({
                "type":      "TextBlock",
                "text":      "─── Per-MID Details ───",
                "weight":    "Bolder",
                "size":      "Small",
                "separator": True,
            })
            body.append({
                "type":  "FactSet",
                "facts": mid_rows,
            })

        adaptive_card = {
            "type":    "AdaptiveCard",
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.4",
            "body":    body,
        }

        payload = {
            "type":        "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl":  None,
                "content":     adaptive_card,
            }],
        }

        data = json.dumps(payload).encode("utf-8")
        req  = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status in (200, 202):
                _log(log_callback, "✓ Teams result notification sent.")
                return True
            else:
                _log(log_callback,
                     f"⚠ Teams webhook returned HTTP {resp.status}")
                return False
    except Exception as e:
        _log(log_callback,
             f"⚠ Teams result notification failed (non-fatal): {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR — BACKGROUND POLLING
# ══════════════════════════════════════════════════════════════════════════════

class ResultCollector:
    """
    Background result collector that polls for test completion,
    collects files, spools summary, and sends notifications.

    Designed to run in a background thread with GUI callbacks.
    """

    def __init__(
        self,
        mids_file:           str,
        target_path:         str   = "",
        site:                str   = "",
        machine_type:        str   = "",   # "IBIR" or "MPT"
        tester_hostname:     str   = "",   # Remote tester hostname (e.g. "IBIR-0383")
        poll_interval:       int   = DEFAULT_POLL_INTERVAL,
        auto_collect:        bool  = True,
        auto_spool:          bool  = False,
        auto_consolidate:    bool  = True,   # Auto-consolidate checkout results
        additional_patterns: Optional[List[str]] = None,
        webhook_url:         str   = "",
        notify_teams:        bool  = True,
        ai_client:           Any   = None,   # AIGatewayClient for AI-powered analysis
        jira_context:        Optional[Dict] = None,   # JIRA context for AI analysis
        test_scenarios:      Optional[str]  = None,   # Test scenarios for AI validation
        code_changes:        Optional[str]  = None,   # Code changes for AI validation
        impact_analysis:     Optional[str]  = None,   # Impact analysis for AI risk assessment
        log_callback:        Optional[Callable] = None,
        progress_callback:   Optional[Callable] = None,
        completion_callback: Optional[Callable] = None,
    ):
        self.mids_file           = mids_file
        self.site                = site
        self.machine_type        = machine_type.upper()
        self.tester_hostname     = tester_hostname   # for UNC path conversion
        self.poll_interval       = poll_interval
        self.auto_collect        = auto_collect
        self.auto_spool          = auto_spool
        self.auto_consolidate    = auto_consolidate
        self.additional_patterns = additional_patterns or []
        self.webhook_url         = webhook_url
        self.notify_teams        = notify_teams
        self.ai_client           = ai_client
        self.jira_context        = jira_context
        self.test_scenarios      = test_scenarios
        self.code_changes        = code_changes
        self.impact_analysis     = impact_analysis
        self.log_callback        = log_callback
        self.progress_callback   = progress_callback
        self.completion_callback = completion_callback
        
        # Initialize auto-consolidator if available
        self._auto_consolidator = None
        if auto_consolidate and AutoConsolidator is not None:
            try:
                self._auto_consolidator = AutoConsolidator(ai_client=ai_client)
            except Exception as e:
                logger.warning(f"Failed to initialize AutoConsolidator: {e}")

        # Resolve target path from site if not provided
        if target_path:
            self.target_path = target_path
        elif site.upper() in SITE_TRACE_PATHS:
            self.target_path = SITE_TRACE_PATHS[site.upper()]
        else:
            self.target_path = ""

        # Detect machine type from tester_hostname or local hostname
        if not self.machine_type:
            detect_name = tester_hostname or os.popen("hostname").read().strip()
            if "IBIR" in detect_name.upper():
                self.machine_type = "IBIR"
            elif "MPT" in detect_name.upper():
                self.machine_type = "MPT"
            elif "CTO" in detect_name.upper():
                self.machine_type = "MPT"   # CTO uses same paths as MPT/ADV

        # Display hostname: prefer tester_hostname, fallback to local
        self._hostname = tester_hostname or os.popen("hostname").read().strip()

        # State
        self._entries: Dict[str, MIDEntry] = {}
        self._cancel_event = threading.Event()
        self._running      = False
        self._thread: Optional[threading.Thread] = None
        self._start_time: Optional[float] = None
        self._original_dir = os.getcwd()

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def entries(self) -> Dict[str, MIDEntry]:
        return self._entries

    def get_summary(self) -> dict:
        """Return a summary dict for GUI display."""
        total     = len(self._entries)
        passed    = sum(1 for e in self._entries.values() if e.status == STATUS_PASS)
        failed    = sum(1 for e in self._entries.values() if e.status == STATUS_FAIL)
        running   = sum(1 for e in self._entries.values() if e.status == STATUS_RUNNING)
        collected = sum(1 for e in self._entries.values() if e.collected)
        # Count NOT_FOUND entries that have no workspace as "unresolved" —
        # these are MIDs whose tmptravl.dat hasn't appeared yet (test may
        # still be starting up).  They should NOT count as "done".
        unresolved = sum(
            1 for e in self._entries.values()
            if e.status == STATUS_NOT_FOUND and not e.workspace_path
        )
        # Aggregate AI consolidation results across all MIDs
        ai_consolidation = []
        for mid, entry in self._entries.items():
            if entry.consolidation_results and entry.consolidation_results.get("success"):
                cr = entry.consolidation_results
                mid_result: Dict[str, Any] = {"mid": mid}
                if "ai_validation" in cr:
                    mid_result["ai_validation"] = cr["ai_validation"]
                if "ai_risk_assessment" in cr:
                    mid_result["ai_risk_assessment"] = cr["ai_risk_assessment"]
                if "outputs" in cr:
                    mid_result["ai_report_paths"] = {
                        k: v for k, v in cr["outputs"].items()
                        if k.startswith("ai_")
                    }
                if len(mid_result) > 1:  # has more than just "mid"
                    ai_consolidation.append(mid_result)

        summary = {
            "total":      total,
            "passed":     passed,
            "failed":     failed,
            "running":    running,
            "collected":  collected,
            "unresolved": unresolved,
            "all_done":   running == 0 and unresolved == 0 and total > 0,
            "machine":    self._hostname,
            "site":       self.site,
            "type":       self.machine_type,
        }
        if ai_consolidation:
            summary["ai_consolidation"] = ai_consolidation
        return summary

    def start(self):
        """Start background polling thread."""
        if self._running:
            _log(self.log_callback, "⚠ Result collector is already running.")
            return

        self._cancel_event.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="ResultCollector"
        )
        self._thread.start()

    def stop(self):
        """Signal the background thread to stop."""
        _log(self.log_callback, "⚠ Stopping result collector...")
        self._cancel_event.set()

    def _run(self):
        """Main polling loop (runs in background thread)."""
        self._running    = True
        self._start_time = time.time()

        try:
            # Parse MIDs file
            _log(self.log_callback, f"📋 Loading MIDs from: {self.mids_file}")
            self._entries = parse_mids_file(self.mids_file)

            if not self._entries:
                _log(self.log_callback, "✗ No MIDs found in file.")
                self._running = False
                return

            _log(self.log_callback,
                 f"   Machine: {self._hostname} ({self.machine_type})")
            _log(self.log_callback,
                 f"   Site: {self.site}")
            _log(self.log_callback,
                 f"   Total MIDs: {len(self._entries)}")
            _log(self.log_callback,
                 f"   Target: {self.target_path}")

            # Resolve workspaces (with UNC conversion if remote)
            if self.tester_hostname:
                _log(self.log_callback,
                     f"   Remote tester: {self.tester_hostname} "
                     f"(UNC: \\\\{self.tester_hostname}\\C$\\...)")
            _log(self.log_callback, "\n🔍 Resolving workspace paths...")
            if self.machine_type == "IBIR":
                self._entries = resolve_ibir_workspaces(
                    self._entries, self.log_callback,
                    tester_hostname=self.tester_hostname,
                )
            elif self.machine_type == "MPT":
                self._entries = resolve_adv_workspaces(
                    self._entries, self.log_callback,
                    tester_hostname=self.tester_hostname,
                )
            else:
                _log(self.log_callback,
                     f"✗ Unknown machine type: {self.machine_type}")
                self._running = False
                return

            # Report initial workspace resolution
            resolved = sum(
                1 for e in self._entries.values() if e.workspace_path
            )
            _log(self.log_callback,
                 f"   Resolved {resolved}/{len(self._entries)} workspaces")

            # Notify GUI of initial state
            self._notify_progress()

            # Polling loop
            _log(self.log_callback,
                 f"\n🔄 Starting auto-detect polling "
                 f"(every {self.poll_interval}s)...")

            while not self._cancel_event.is_set():
                # Check status of all MIDs
                self._check_all_statuses()
                self._notify_progress()

                summary = self.get_summary()
                running_count    = summary["running"]
                total_count      = summary["total"]
                unresolved_count = summary.get("unresolved", 0)

                unresolved_str = (f" U:{unresolved_count}"
                                  if unresolved_count else "")
                _log(self.log_callback,
                     f"   Poll: {total_count - running_count - unresolved_count}"
                     f"/{total_count} "
                     f"completed "
                     f"(P:{summary['passed']} F:{summary['failed']} "
                     f"R:{running_count}{unresolved_str})")

                # All tests complete?
                if summary["all_done"]:
                    _log(self.log_callback,
                         "\n✅ All tests completed!")
                    self._on_all_complete()
                    break

                # Wait for next poll
                self._cancel_event.wait(self.poll_interval)

        except Exception as e:
            _log(self.log_callback, f"✗ Result collector error: {e}")
            logger.error(f"ResultCollector error: {traceback.format_exc()}")
        finally:
            self._running = False
            if self.completion_callback:
                try:
                    self.completion_callback(self.get_summary())
                except Exception:
                    pass

    def _resolve_unresolved_workspaces(self):
        """
        Re-attempt workspace resolution for entries whose tmptravl.dat
        was not found on the previous attempt.  This handles the case
        where the tester hasn't written the file yet when monitoring
        starts (test still booting / initialising).
        """
        unresolved = {
            mid: entry for mid, entry in self._entries.items()
            if entry.status == STATUS_NOT_FOUND and not entry.workspace_path
        }
        if not unresolved:
            return

        _log(self.log_callback, "🔍 Re-resolving unresolved workspace paths...")
        if self.machine_type == "IBIR":
            resolve_ibir_workspaces(
                unresolved, self.log_callback,
                tester_hostname=self.tester_hostname,
            )
        elif self.machine_type == "MPT":
            resolve_adv_workspaces(
                unresolved, self.log_callback,
                tester_hostname=self.tester_hostname,
            )

        # For entries that were successfully resolved, reset status to RUNNING
        # so they get picked up by the normal status-check flow.
        for mid, entry in unresolved.items():
            if entry.workspace_path:
                entry.status = STATUS_RUNNING
                entry.error_msg = ""
                _log(self.log_callback,
                     f"   ✓ Resolved workspace for {mid}: {entry.workspace_path}")

        newly_resolved = sum(1 for e in unresolved.values() if e.workspace_path)
        still_missing  = len(unresolved) - newly_resolved
        if newly_resolved:
            _log(self.log_callback,
                 f"   Resolved {newly_resolved} workspace(s), "
                 f"{still_missing} still unresolved")

    def _check_all_statuses(self):
        """Check test status for all MIDs that are still running or unresolved."""
        # First, re-try workspace resolution for NOT_FOUND entries
        self._resolve_unresolved_workspaces()

        # Then check test status for entries that have a workspace
        for mid, entry in self._entries.items():
            if entry.status == STATUS_RUNNING and entry.workspace_path:
                if self.machine_type == "IBIR":
                    check_test_status_ibir(entry)
                elif self.machine_type == "MPT":
                    check_test_status_adv(entry)

    def _on_all_complete(self):
        """Called when all tests are detected as complete."""
        elapsed = int(time.time() - self._start_time) if self._start_time else 0

        # Print summary
        _log(self.log_callback, "\n>>> TEST SUMMARY <<<")
        for mid, entry in self._entries.items():
            _log(self.log_callback, entry.result_line())

        # Auto-collect files
        if self.auto_collect and self.target_path:
            _log(self.log_callback, f"\n📁 Collecting files to: {self.target_path}")

            # Ensure target directory exists
            os.makedirs(self.target_path, exist_ok=True)

            for mid, entry in self._entries.items():
                if entry.status in (STATUS_PASS, STATUS_FAIL) and entry.workspace_path:
                    _log(self.log_callback,
                         f"  >>> {mid}: Collecting from '{entry.workspace_path}'")
                    if self.machine_type == "IBIR":
                        collect_files_ibir(
                            entry, self.target_path,
                            self.additional_patterns, self.log_callback
                        )
                    elif self.machine_type == "MPT":
                        collect_files_adv(
                            entry, self.target_path,
                            self.additional_patterns, self.log_callback
                        )

            # Mark collected in MIDs file
            collected_mids = [
                mid for mid, e in self._entries.items() if e.collected
            ]
            if collected_mids:
                try:
                    mark_collected_in_mids_file(self.mids_file, collected_mids)
                    _log(self.log_callback,
                         f"   ✓ Marked {len(collected_mids)} MIDs as collected")
                except Exception as e:
                    _log(self.log_callback,
                         f"   ⚠ Could not update MIDs file: {e}")
            
            # Auto-consolidate results (analyze trace files, generate reports)
            if self.auto_consolidate and self._auto_consolidator:
                _log(self.log_callback, "\n📊 Auto-consolidating checkout results...")
                for mid, entry in self._entries.items():
                    if entry.collected and entry.workspace_path:
                        # Find collection directory for this MID
                        collection_dir = os.path.join(self.target_path, mid)
                        if os.path.exists(collection_dir):
                            try:
                                # Find trace file in collection directory
                                trace_files = []
                                for root, dirs, files in os.walk(collection_dir):
                                    for file in files:
                                        if file.startswith("DispatcherDebug") and file.endswith(".txt"):
                                            trace_files.append(os.path.join(root, file))
                                
                                if trace_files:
                                    trace_file = trace_files[0]  # Use first trace file found
                                    _log(self.log_callback, f"  >>> {mid}: Consolidating results...")
                                    
                                    # Run auto-consolidation
                                    results = self._auto_consolidator.consolidate(
                                        collection_dir=collection_dir,
                                        trace_file_path=trace_file,
                                        jira_key=None,  # Could be extracted from MIDs file if available
                                        mid=mid,
                                        sku=entry.name,
                                        generate_validation_doc=True,
                                        generate_spool_summary=True,
                                        generate_manifest=True,
                                        perform_risk_assessment=True,
                                        perform_ai_validation=self.ai_client is not None,
                                        perform_ai_risk_assessment=self.ai_client is not None,
                                        jira_context=self.jira_context,
                                        test_scenarios=self.test_scenarios,
                                        code_changes=self.code_changes,
                                        impact_analysis=self.impact_analysis,
                                        log_callback=self.log_callback,
                                    )
                                    
                                    # Store consolidation results on the entry
                                    entry.consolidation_results = results
                                    
                                    if results.get("success"):
                                        _log(self.log_callback, f"      ✓ Consolidation complete")
                                        if "risk_assessment" in results:
                                            risk_level = results["risk_assessment"].get("risk_level", "UNKNOWN")
                                            risk_score = results["risk_assessment"].get("risk_score", 0)
                                            _log(self.log_callback, f"      ✓ Risk: {risk_level} ({risk_score:.1f}/100)")
                                        if "ai_validation" in results:
                                            val_status = results["ai_validation"].get("validation_status", "N/A")
                                            val_conf = results["ai_validation"].get("confidence", "N/A")
                                            _log(self.log_callback, f"      ✓ AI Validation: {val_status} (confidence: {val_conf})")
                                        if "ai_risk_assessment" in results:
                                            ai_risk = results["ai_risk_assessment"].get("enhanced_risk_level", "N/A")
                                            ai_score = results["ai_risk_assessment"].get("enhanced_risk_score", 0)
                                            _log(self.log_callback, f"      ✓ AI Risk: {ai_risk} ({ai_score:.1f}/100)")
                                    else:
                                        errors = results.get("errors", [])
                                        _log(self.log_callback, f"      ⚠ Consolidation had errors: {', '.join(errors)}")
                                else:
                                    _log(self.log_callback, f"  >>> {mid}: No trace file found, skipping consolidation")
                            except Exception as e:
                                _log(self.log_callback, f"  >>> {mid}: Consolidation failed: {e}")
                                logger.exception(f"Auto-consolidation failed for {mid}")

        # Auto-spool summary
        if self.auto_spool:
            _log(self.log_callback, "\n📊 Spooling summaries...")
            for mid, entry in self._entries.items():
                if entry.spool and entry.workspace_path:
                    if self.machine_type == "IBIR":
                        spool_summary_ibir(
                            entry, self._original_dir, self.log_callback
                        )
                    elif self.machine_type == "MPT":
                        spool_summary_adv(
                            entry, self._original_dir, self.log_callback
                        )

        # Teams notification
        if self.notify_teams:
            _log(self.log_callback, "\n📨 Sending Teams notification...")
            send_result_teams_notification(
                machine      = self._hostname,
                site         = self.site,
                entries      = self._entries,
                elapsed      = elapsed,
                webhook_url  = self.webhook_url,
                log_callback = self.log_callback,
            )

        self._notify_progress()

    def collect_single(self, mid: str) -> bool:
        """Manually collect files for a single MID (called from GUI)."""
        entry = self._entries.get(mid)
        if not entry:
            return False
        if not self.target_path:
            _log(self.log_callback, "✗ No target path configured.")
            return False

        # If workspace was never resolved, try again now
        if not entry.workspace_path:
            _log(self.log_callback,
                 f"   🔍 Attempting to resolve workspace for {mid}...")
            single = {mid: entry}
            if self.machine_type == "IBIR":
                resolve_ibir_workspaces(
                    single, self.log_callback,
                    tester_hostname=self.tester_hostname,
                )
            elif self.machine_type == "MPT":
                resolve_adv_workspaces(
                    single, self.log_callback,
                    tester_hostname=self.tester_hostname,
                )
            if entry.workspace_path:
                entry.status = STATUS_RUNNING
                entry.error_msg = ""
                _log(self.log_callback,
                     f"   ✓ Resolved workspace: {entry.workspace_path}")
            else:
                _log(self.log_callback,
                     f"   ✗ Still cannot resolve workspace for {mid}")
                return False

        os.makedirs(self.target_path, exist_ok=True)

        if self.machine_type == "IBIR":
            return collect_files_ibir(
                entry, self.target_path,
                self.additional_patterns, self.log_callback
            )
        elif self.machine_type == "MPT":
            return collect_files_adv(
                entry, self.target_path,
                self.additional_patterns, self.log_callback
            )
        return False

    def spool_single(self, mid: str) -> bool:
        """Manually spool summary for a single MID (called from GUI)."""
        entry = self._entries.get(mid)
        if not entry:
            return False

        if self.machine_type == "IBIR":
            return spool_summary_ibir(
                entry, self._original_dir, self.log_callback
            )
        elif self.machine_type == "MPT":
            return spool_summary_adv(
                entry, self._original_dir, self.log_callback
            )
        return False

    def _notify_progress(self):
        """Send progress update to GUI callback."""
        if self.progress_callback:
            try:
                self.progress_callback(
                    self.get_summary(),
                    {mid: e.to_dict() for mid, e in self._entries.items()},
                )
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════════════
# UTILITY
# ══════════════════════════════════════════════════════════════════════════════

def _log(callback: Optional[Callable], message: str, level: str = "info"):
    """Log to both logger and optional callback."""
    if level == "warning":
        logger.warning(message)
    elif level == "error":
        logger.error(message)
    else:
        logger.info(message)

    if callback:
        try:
            callback(message)
        except Exception:
            pass
