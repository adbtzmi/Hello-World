#!/usr/bin/env python3
r"""
model/orchestrators/checkout_orchestrator.py
=============================================
BENTO Checkout Orchestrator — Phase 2 Auto Start Checkout

Runs on the LOCAL PC. Full flow:
1. Read CRT Excel from \\sifsmodtestrep\modtestrep\crab\crt_from_sap.xlsx
2. Generate SLATE XML (correct Profile schema with AutoStart=True)
3. Drop XML to P:\temp\BENTO\CHECKOUT_QUEUE\   <- FIXED: was HOT_DROP
4. Poll .checkout_status sidecar (mirrors wait_for_build() exactly)
5. Loop per test case (PASSING + FORCE FAIL sequentially)
6. Memory collection after all test cases
7. Teams notification with per-test-case summary

KEY FIX:
  run_checkout()     -> saves XML to CHECKOUT_QUEUE (shared P: drive)
  generate_xml_only  -> saves XML directly to hot_folder (playground_queue)
  Both auto-create their target folders with os.makedirs(exist_ok=True)

Mirrors compilation_orchestrator.py pattern exactly. [15]
"""

import os
import sys
import time
import json
import logging
import argparse
import threading
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional, Dict, List

# ── CONFIRMED PATHS ───────────────────────────────────────────────────────────
# N: drive = \\sifsmodtestrep\modtestrep  (confirmed from screenshot)
CAT_CRAB_FOLDER         = r"\\sifsmodtestrep\modtestrep\crab"
CRT_EXCEL_PATH          = r"\\sifsmodtestrep\modtestrep\crab\crt_from_sap.xlsx"
CRT_DB_PATH             = r"\\sifsmodtestrep\modtestrep\crab\closed_crt_jira_info.db"

# ── P: drive — BENTO shared folders ──────────────────────────────────────────
# XML is staged here first, then checkout_watcher.py picks it up [24]
CHECKOUT_QUEUE_FOLDER   = r"P:\temp\BENTO\CHECKOUT_QUEUE"
CHECKOUT_RESULTS_FOLDER = r"P:\temp\BENTO\CHECKOUT_RESULTS"

# ── Tester registry ───────────────────────────────────────────────────────────
TESTER_REGISTRY         = r"P:\temp\BENTO\bento_testers.json"

# ── Default SLATE hot folder on TESTER machine ────────────────────────────────
# NOTE: This path only exists on the tester, not the local PC.
# For "Generate XML Only" mode this is auto-created locally for testing.
# For "Start Checkout" mode the watcher creates it on the tester side. [24]
DEFAULT_HOT_FOLDER      = r"C:\test_program\playground_queue"

# ── Polling ───────────────────────────────────────────────────────────────────
POLL_INTERVAL            = 30      # seconds — matches watcher write cadence
CHECKOUT_TIMEOUT_SECONDS = 3600    # 60 min default

# ── Teams webhook — override via settings.json or env var ────────────────────
TEAMS_WEBHOOK_URL = ""

# ── CONFIRMED COLUMN NAMES from crt_excel_template.json [26] ─────────────────
# ⚠️  "Product  Name" = DOUBLE SPACE — confirmed in CAT.py [33]
_COL_MATERIAL  = "Material description"
_COL_CFGPN     = "CFGPN"
_COL_FW_WAVE   = "FW Wave ID"
_COL_FIDB      = "FIDB_ASIC_FW_REV"
_COL_PRODUCT   = "Product  Name"         # ← DOUBLE SPACE ⚠️
_COL_CUSTOMER  = "CRT Customer"
_COL_DRV_TYPE  = "SSD Drive Type"
_COL_ABIT_REL  = "ABIT Release (Yes/No)"
_COL_SFN2_REL  = "SFN2 Release (Yes/No)"
_COL_CHECKOUT  = "CRT Checkout (Yes/No)"

# ── Recipe file per ENV — confirmed from FullAutoStart.md [24] ───────────────
RECIPE_MAP = {
    "ABIT": r"RECIPE:PEREGRINE\ON_NEOSEM_ABIT.XML",
    "SFN2": r"RECIPE:PEREGRINE\ON_NEOSEM_SFN2.XML",
    "CNFG": r"RECIPE:PEREGRINE\ON_NEOSEM_CNFG.XML",
}


# ── LOGGER ────────────────────────────────────────────────────────────────────
def _get_logger(log_callback=None) -> logging.Logger:
    logger = logging.getLogger("bento_checkout_orchestrator")
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        logger.addHandler(sh)
    return logger


def _log(logger, msg: str, log_callback=None, level: str = "info"):
    getattr(logger, level)(msg)
    if log_callback:
        log_callback(msg)


def _phase(logger, msg: str, log_callback=None, phase_callback=None):
    _log(logger, msg, log_callback)
    if phase_callback:
        phase_callback(msg)


# ── STEP 0 — Load valid ENVs ──────────────────────────────────────────────────
def get_valid_envs() -> set:
    """Load valid environments from shared tester registry. [13]"""
    try:
        if os.path.exists(TESTER_REGISTRY):
            with open(TESTER_REGISTRY, "r") as f:
                data = json.load(f)
            return {
                (v[1].upper() if isinstance(v, list) else v.get("env", "").upper())
                for v in data.values()
            }
    except Exception:
        pass
    return {"ABIT", "SFN2", "CNFG"}


# ── STEP 1 — Read CRT Excel ───────────────────────────────────────────────────
def load_dut_info_from_crt(
    cfgpn_filter: str = "",
    excel_path:   str = "",
    logger              = None,
    log_callback        = None,
) -> list:
    r"""
    Read CRT Excel from \\sifsmodtestrep\modtestrep\crab\crt_from_sap.xlsx
    Confirmed location from Windows Explorer screenshot (N: drive).

    Mirrors CatDB.update_db_with_crt_excel() in CAT.py [33] exactly:
        df = pd.read_excel(filepath, engine="openpyxl", dtype=str)

    Column names confirmed from crt_excel_template.json [26].
    "Product  Name" has DOUBLE SPACE -- do not change.
    """
    import pandas as pd

    if logger is None:
        logger = _get_logger()

    path = excel_path or CRT_EXCEL_PATH

    if not os.path.exists(path):
        msg = (
            f"CRT Excel not found at:\n  {path}\n"
            f"Ensure N: drive (\\\\sifsmodtestrep) is mapped\n"
            f"and C.A.T. has completed a SAP export."
        )
        _log(logger, f"✗ {msg}", log_callback, "error")
        raise FileNotFoundError(msg)

    _log(logger,
         f"Reading CRT Excel: {os.path.basename(path)}", log_callback)

    # Mirrors C.A.T. exactly — openpyxl engine, all columns as str [33]
    df = pd.read_excel(path, engine="openpyxl", dtype=str)

    # Validate critical columns
    missing = [c for c in [_COL_CFGPN, _COL_FW_WAVE, _COL_PRODUCT]
               if c not in df.columns]
    if missing:
        msg = (
            f"CRT Excel missing columns: {missing}\n"
            f"Note: 'Product  Name' requires DOUBLE SPACE."
        )
        _log(logger, f"✗ {msg}", log_callback, "error")
        raise ValueError(msg)

    # Apply CFGPN filter safely (pandas — no SQL injection risk)
    if cfgpn_filter:
        df = df[df[_COL_CFGPN] == str(cfgpn_filter)]

    if df.empty:
        _log(logger,
             "⚠ No CRT rows found"
             + (f" for CFGPN={cfgpn_filter}" if cfgpn_filter else ""),
             log_callback, "warning")
        return []

    # Return with exact column names matching checkout_tab.py [40] grid
    result = []
    for _, row in df.iterrows():
        result.append({
            "Material description":  str(row.get(_COL_MATERIAL, "") or "").strip(),
            "CFGPN":                 str(row.get(_COL_CFGPN,    "") or "").strip(),
            "FW Wave ID":            str(row.get(_COL_FW_WAVE,  "") or "").strip(),
            "FIDB_ASIC_FW_REV":      str(row.get(_COL_FIDB,     "") or "").strip(),
            "Product  Name":         str(row.get(_COL_PRODUCT,  "") or "").strip(),
            "CRT Customer":          str(row.get(_COL_CUSTOMER, "") or "").strip(),
            "SSD Drive Type":        str(row.get(_COL_DRV_TYPE, "") or "").strip(),
            "ABIT Release (Yes/No)": str(row.get(_COL_ABIT_REL, "") or "").strip(),
            "SFN2 Release (Yes/No)": str(row.get(_COL_SFN2_REL, "") or "").strip(),
            "CRT Checkout (Yes/No)": str(row.get(_COL_CHECKOUT,  "") or "").strip(),
        })

    _log(logger,
         f"✓ Loaded {len(result)} DUT record(s) from CRT Excel.",
         log_callback)
    return result


# ── STEP 2 — Generate SLATE XML ───────────────────────────────────────────────
def generate_slate_xml(
    jira_key:      str,
    mid:           str,
    cfgpn:         str,
    fw_ver:        str,
    dut_slots:     int,
    tgz_path:      str,
    env:           str,
    lot_prefix:    str            = "JAANTJB",
    dut_locations: Optional[list] = None,
    label:         str            = "",
    hostname:      str  = "",
    output_dir:    str  = "",
    dry_run:       bool = False,
    logger               = None,
    log_callback         = None,
) -> Optional[str]:
    """
    Generate SLATE XML with correct Profile schema.
    Confirmed from FullAutoStart.md [24] Step 4 & Step 9.
    AutoStart=True eliminates manual "Run Test" click.

    output_dir controls WHERE the XML is saved:
      - run_checkout()    → CHECKOUT_QUEUE (P: shared drive) [39]
      - generate_xml_only → hot_folder (C:\\test_program\\playground_queue) [41]

    Both callers auto-create their target folder before calling this.
    """
    if logger is None:
        logger = _get_logger()

    timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Filename includes hostname + label for tester-specific routing [8]
    parts      = [p for p in [jira_key, hostname, timestamp, label] if p]
    xml_name   = "checkout_" + "_".join(parts) + ".xml"
    out_dir    = output_dir or CHECKOUT_QUEUE_FOLDER
    recipe     = RECIPE_MAP.get(env.upper(),
                                r"RECIPE:PEREGRINE\ON_NEOSEM_ABIT.XML")

    # ── Auto-create output directory ──────────────────────────────────
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception as e:
        _log(logger, f"✗ Cannot create output dir {out_dir}: {e}",
             log_callback, "error")
        return None

    xml_path = os.path.join(out_dir, xml_name)

    _phase(logger,
           f"Generating SLATE XML [{label or 'default'}]...", log_callback)

    try:
        # ── Correct Profile schema from FullAutoStart.md [24] ─────────
        profile = ET.Element("Profile")

        # ── TestJobArchive ────────────────────────────────────────────
        # TGZ path from Phase 1 compile output [7]
        ET.SubElement(profile, "TestJobArchive").text = tgz_path

        # ── RecipeFile ────────────────────────────────────────────────
        ET.SubElement(profile, "RecipeFile").text = recipe

        # ── TempTraveler — confirmed rows from FullAutoStart.md [24] ──
        tt = ET.SubElement(profile, "TempTraveler")
        for section, name, value in [
            ("MAM",       "STEP",          env.upper()),
            ("MAM",       "NAND_OPTION",   "BAD_PLANE"),
            ("CFGPN",     "STEP_ID",       "AMB IB TEST"),
            ("CFGPN",     "SEC_PROCESS",   "ABIT_REQ0"),
            ("EQUIPMENT", "DIB_TYPE",      "MS0052"),
            ("EQUIPMENT", "DIB_TYPE_NAME", "MS0022"),
        ]:
            a = ET.SubElement(tt, "Attribute")
            a.set("Section", section)
            a.set("Name",    name)
            a.set("Value",   value)

        # ── MaterialInfo — user-provided DUT locations or auto-generate
        mat       = ET.SubElement(profile, "MaterialInfo")
        locations = dut_locations or [
            f"{i // 8},{i % 8}" for i in range(dut_slots)
        ]
        for idx, loc in enumerate(locations):
            lot_num = f"{lot_prefix}{str(idx + 1).zfill(3)}"
            a = ET.SubElement(mat, "Attribute")
            a.set("Lot",         lot_num)
            a.set("MID",         mid)
            a.set("DutLocation", loc)

        # ── AutoStart = True — KEY: no manual click needed [24] ───────
        ET.SubElement(profile, "AutoStart").text = "True"

        # ── Write XML ─────────────────────────────────────────────────
        tree = ET.ElementTree(profile)
        ET.indent(tree, space="    ")
        tree.write(xml_path, encoding="unicode", xml_declaration=True)

        size_kb = os.path.getsize(xml_path) / 1024
        _log(logger,
             f"✓ XML: {xml_name} | AutoStart=True | "
             f"{len(locations)} DUT(s) | {size_kb:.1f} KB",
             log_callback)
        return xml_path

    except Exception as e:
        _log(logger, f"✗ XML generation failed: {e}",
             log_callback, "error")
        return None


# ── STATUS FILE HELPERS ───────────────────────────────────────────────────────
def write_checkout_status(xml_path: str, status: str, detail: str = ""):
    """Write .checkout_status sidecar — mirrors watcher_lock.py [9]."""
    status_path = xml_path + ".checkout_status"
    data = {
        "status":    status,
        "detail":    detail,
        "timestamp": datetime.now().isoformat()
    }
    try:
        with open(status_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logging.getLogger("bento_checkout_orchestrator").warning(
            f"Could not write status: {e}"
        )


def read_checkout_status(xml_path: str) -> dict:
    """
    Safely read .checkout_status sidecar JSON.
    Returns {} on any parse or IO error — caller treats as 'not ready'. [9]
    """
    status_path = xml_path + ".checkout_status"
    if not os.path.exists(status_path):
        return {}
    try:
        with open(status_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError, OSError):
        return {}


# ── STEP 3 — Drop XML to hot folder (Generate XML Only mode) ─────────────────
def drop_xml_to_hot_folder(
    xml_path:    str,
    hot_folder:  str,
    logger,
    log_callback = None,
) -> Optional[str]:
    """
    Copy XML directly to SLATE hot folder.

    ✅ AUTO-CREATES the folder if it doesn't exist.

    Used ONLY in generate_xml_only() mode — bypasses CHECKOUT_QUEUE.
    In Start Checkout mode, the watcher handles this copy on the tester. [24][39]
    """
    _phase(logger, "Dropping XML to hot folder…", log_callback)

    # ── AUTO-CREATE hot folder ────────────────────────────────────────
    try:
        os.makedirs(hot_folder, exist_ok=True)
        _log(logger, f"[✓] Hot folder ready: {hot_folder}", log_callback)
    except Exception as e:
        _log(logger,
             f"✗ Cannot create hot folder {hot_folder}: {e}",
             log_callback, "error")
        return None

    dest = os.path.join(hot_folder, os.path.basename(xml_path))
    try:
        shutil.copy2(xml_path, dest)
        _log(logger, f"✓ XML → {dest}", log_callback)
        return dest
    except Exception as e:
        _log(logger, f"✗ Hot folder drop failed: {e}",
             log_callback, "error")
        return None


# ── STEP 4 — Poll for completion ──────────────────────────────────────────────
def wait_for_checkout(
    xml_path:        str,
    logger,
    log_callback     = None,
    phase_callback   = None,
    timeout_seconds: int = CHECKOUT_TIMEOUT_SECONDS,
) -> Dict:
    """
    Poll .checkout_status sidecar every 30s.
    Mirrors wait_for_build() in compilation_orchestrator.py [15].

    The watcher on the tester writes this file after SLATE completes. [24][42]
    """
    start    = time.time()
    deadline = start + timeout_seconds

    _phase(logger,
           f"Waiting for SLATE (timeout={timeout_seconds // 60}min)…",
           log_callback, phase_callback)

    while time.time() < deadline:
        data  = read_checkout_status(xml_path)
        state = data.get("status", "")

        if state == "success":
            elapsed = int(time.time() - start)
            _log(logger, f"✓ Checkout SUCCESS in {elapsed}s", log_callback)
            return {
                "status":  "success",
                "detail":  data.get("detail", ""),
                "elapsed": elapsed
            }

        elif state == "failed":
            elapsed = int(time.time() - start)
            detail  = data.get("detail", "Unknown error")
            _log(logger,
                 f"✗ Checkout FAILED after {elapsed}s: {detail}",
                 log_callback, "error")
            return {
                "status":  "failed",
                "detail":  detail,
                "elapsed": elapsed
            }

        elif state == "in_progress":
            elapsed = int(time.time() - start)
            _phase(logger,
                   f"SLATE running… ({elapsed}s)",
                   log_callback, phase_callback)

        time.sleep(POLL_INTERVAL)

    elapsed = int(time.time() - start)
    _log(logger, f"✗ Checkout TIMEOUT after {elapsed}s",
         log_callback, "error")
    return {
        "status":  "timeout",
        "detail":  f"No SLATE signal within {timeout_seconds}s",
        "elapsed": elapsed
    }


# ── STEP 5 — Memory collection ────────────────────────────────────────────────
def collect_dut_memory(
    hostname:      str,
    jira_key:      str,
    dut_slots:     int,
    output_folder: str,
    logger         = None,
    log_callback   = None,
    phase_callback = None,
) -> Dict:
    """
    Trigger memory collection for all DUT slots after SLATE completes.
    Results saved to P:\\temp\\BENTO\\CHECKOUT_RESULTS\\ [24]

    Replace the placeholder block with your actual tool call.
    """
    if logger is None:
        logger = _get_logger()

    _phase(logger, f"Collecting memory for {dut_slots} DUT(s)…",
           log_callback, phase_callback)

    try:
        os.makedirs(output_folder, exist_ok=True)
    except Exception as e:
        _log(logger,
             f"⚠ Cannot create memory output folder: {e}",
             log_callback, "warning")

    collected = []
    failed    = []

    for slot in range(1, dut_slots + 1):
        try:
            # ── INSERT YOUR REAL MEMORY COLLECTION CALL HERE ──────────
            # Option A: subprocess to memory_collect.exe on tester
            # import subprocess
            # cmd = [r"C:\test_program\tools\memory_collect.exe",
            #        "--slot", str(slot), "--output", output_folder]
            # proc = subprocess.run(cmd, capture_output=True, timeout=300)
            # if proc.returncode != 0:
            #     raise RuntimeError(proc.stderr.decode())
            # ─────────────────────────────────────────────────────────

            # Placeholder — writes a stub file until real tool wired up
            mem_file = os.path.join(
                output_folder, f"{jira_key}_slot{slot}_memory.txt"
            )
            with open(mem_file, 'w') as f:
                f.write(f"Memory collection placeholder\n")
                f.write(f"JIRA Key : {jira_key}\n")
                f.write(f"Hostname : {hostname}\n")
                f.write(f"Slot     : {slot}\n")
                f.write(f"Timestamp: {datetime.now().isoformat()}\n")

            collected.append(slot)
            _log(logger,
                 f"  ✓ Slot {slot} → {os.path.basename(mem_file)}",
                 log_callback)

        except Exception as e:
            failed.append(slot)
            # ✅ Log failure per DUT — don't silently swallow [24]
            _log(logger,
                 f"  ✗ Slot {slot} memory collection failed: {e}",
                 log_callback, "warning")

    status = "success" if not failed else (
        "partial" if collected else "failed"
    )
    detail = f"Collected {len(collected)}/{dut_slots} slots"
    if failed:
        detail += f" (failed slots: {failed})"

    _log(logger, f"Memory collection done: {detail}", log_callback)
    return {
        "status":          status,
        "detail":          detail,
        "collected_slots": collected
    }


# ── STEP 6 — Teams notification ───────────────────────────────────────────────
def send_teams_notification(
    jira_key:    str,
    hostname:    str,
    status:      str,
    detail:      str,
    elapsed:     int,
    webhook_url: str  = "",
    logger             = None,
    log_callback       = None,
) -> bool:
    """
    Send Teams notification on checkout completion. [39]
    Non-fatal — checkout succeeded even if notification fails.
    """
    if logger is None:
        logger = _get_logger()

    url = (webhook_url or TEAMS_WEBHOOK_URL
           or os.environ.get("BENTO_TEAMS_WEBHOOK", ""))

    if not url:
        _log(logger,
             "⚠ Teams webhook URL not configured — skipping.",
             log_callback, "warning")
        return False

    try:
        import urllib.request
        icon    = "✅" if status.lower() == "success" else "❌"
        payload = {
            "@type":      "MessageCard",
            "@context":   "http://schema.org/extensions",
            "themeColor": "0076D7" if status.lower() == "success"
                          else "D40000",
            "summary":    f"BENTO Checkout {status.upper()} — {jira_key}",
            "sections": [{
                "activityTitle":    f"{icon} BENTO Auto Checkout — "
                                    f"{status.upper()}",
                "activitySubtitle": f"Tester: {hostname}  |  "
                                    f"JIRA: {jira_key}",
                "facts": [
                    {"name": "Status",  "value": status.upper()},
                    {"name": "Detail",  "value": detail},
                    {"name": "Elapsed",
                     "value": f"{elapsed}s "
                              f"({elapsed // 60}m {elapsed % 60}s)"},
                    {"name": "Time",
                     "value": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                ],
            }]
        }
        data = json.dumps(payload).encode("utf-8")
        req  = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                _log(logger, "✓ Teams notification sent.", log_callback)
                return True
            else:
                _log(logger,
                     f"⚠ Teams webhook returned HTTP {resp.status}",
                     log_callback, "warning")
                return False
    except Exception as e:
        # Non-fatal — checkout succeeded, notification is best-effort
        _log(logger, f"⚠ Teams notification failed (non-fatal): {e}",
             log_callback, "warning")
        return False


# ── PUBLIC API — called from checkout_controller.py [41] ─────────────────────
def run_checkout(
    jira_key:        str,
    hostname:        str,
    env:             str,
    tgz_path:        str   = "",
    hot_folder:      str   = "",
    mid:             str   = "",
    cfgpn:           str   = "",
    fw_ver:          str   = "",
    dut_slots:       int   = 4,
    lot_prefix:      str   = "JAANTJB",
    dut_locations:   Optional[list] = None,
    test_cases:      Optional[list] = None,
    detect_method:   str   = "AUTO",
    timeout_seconds: int   = CHECKOUT_TIMEOUT_SECONDS,
    notify_teams:    bool  = True,
    webhook_url:     str   = "",
    log_callback           = None,
    phase_callback         = None,
) -> Dict:
    """
    High-level entry point — Start Checkout full flow.

    KEY FIX [39]:
      XML is saved to CHECKOUT_QUEUE (P: shared drive), NOT hot_folder.
      hot_folder (C:\\test_program\\playground_queue) is on the TESTER machine.
      The checkout_watcher.py [42] running on the tester will:
        1. Detect XML in CHECKOUT_QUEUE
        2. Copy XML → C:\\test_program\\playground_queue (auto-creates it)
        3. SLATE picks up XML → AutoStart=True → test begins [24]

    Runs each test case sequentially, collects memory after all done,
    then sends Teams notification with summary. [24]
    """
    logger      = _get_logger(log_callback)
    env         = env.upper()
    start       = time.time()
    _test_cases = test_cases or [{"type": "passing", "label": "passing"}]

    # ── Validate inputs ───────────────────────────────────────────────
    if not hostname:
        msg = "Hostname is required for checkout."
        _log(logger, f"[FAIL] {msg}", log_callback, "error")
        return {"status": "failed", "detail": msg, "elapsed": 0,
                "test_cases": [], "memory": {}}

    valid_envs = get_valid_envs()
    if env not in valid_envs:
        msg = (f"Unknown ENV '{env}'. "
               f"Valid: {', '.join(sorted(valid_envs))}")
        _log(logger, f"[FAIL] {msg}", log_callback, "error")
        return {"status": "failed", "detail": msg, "elapsed": 0,
                "test_cases": [], "memory": {}}

    _log(logger, f"=== BENTO Auto Checkout ===", log_callback)
    _log(logger, f"JIRA     : {jira_key}",  log_callback)
    _log(logger, f"Hostname : {hostname}",   log_callback)
    _log(logger, f"ENV      : {env}",        log_callback)
    _log(logger, f"MID      : {mid}",        log_callback)
    _log(logger, f"CFGPN    : {cfgpn}",      log_callback)
    _log(logger, f"FW       : {fw_ver}",     log_callback)
    _log(logger, f"Slots    : {dut_slots}",  log_callback)
    _log(logger, f"Lot pfx  : {lot_prefix}", log_callback)

    # ── KEY FIX: XML goes to CHECKOUT_QUEUE, not hot_folder ──────────
    # CHECKOUT_QUEUE = P:\temp\BENTO\CHECKOUT_QUEUE  (shared P: drive)
    # The watcher on the tester picks it up and copies to playground_queue
    _queue = CHECKOUT_QUEUE_FOLDER

    # ── Auto-create CHECKOUT_QUEUE on shared drive ────────────────────
    try:
        os.makedirs(_queue, exist_ok=True)
        _log(logger, f"[✓] Queue folder ready: {_queue}", log_callback)
    except Exception as e:
        msg = f"Cannot create CHECKOUT_QUEUE {_queue}: {e}"
        _log(logger, f"[FAIL] {msg}", log_callback, "error")
        return {"status": "failed", "detail": msg, "elapsed": 0,
                "test_cases": [], "memory": {}}

    all_tc_results = []

    # ── Loop per test case (PASSING + FORCE FAIL sequentially) ────────
    for tc in _test_cases:
        label   = tc.get("label", "passing")
        tc_type = tc.get("type",  "passing")
        tc_desc = tc.get("description", "")

        _log(logger,
             f"\n{'=' * 50}\n"
             f"Test case: {label} ({tc_type})\n"
             f"{'=' * 50}",
             log_callback)

        # ── Generate XML → save to CHECKOUT_QUEUE ─────────────────────
        # NOT to C:\test_program\playground_queue
        # (that path only exists on the tester machine) [39]
        xml_path = generate_slate_xml(
            jira_key      = jira_key,
            mid           = mid,
            cfgpn         = cfgpn,
            fw_ver        = fw_ver,
            dut_slots     = dut_slots,
            tgz_path      = tgz_path,
            env           = env,
            lot_prefix    = lot_prefix,
            dut_locations = dut_locations,
            label         = label,
            hostname      = hostname,
            output_dir    = _queue,      # ← P:\temp\BENTO\CHECKOUT_QUEUE
            logger        = logger,
            log_callback  = log_callback,
        )

        if not xml_path:
            result = {
                "status":      "failed",
                "detail":      "XML generation failed.",
                "elapsed":     int(time.time() - start),
                "label":       label,
                "type":        tc_type,
                "description": tc_desc
            }
            all_tc_results.append(result)
            continue

        # ── Write initial queued status ────────────────────────────────
        write_checkout_status(
            xml_path, "queued",
            f"Waiting for tester pickup — {label}"
        )

        _log(logger,
             f"[✓] XML queued: {os.path.basename(xml_path)}\n"
             f"    Waiting for checkout_watcher.py on {hostname} to pick up...",
             log_callback)

        # ── Poll for SLATE completion ──────────────────────────────────
        tc_result = wait_for_checkout(
            xml_path        = xml_path,
            logger          = logger,
            log_callback    = log_callback,
            phase_callback  = phase_callback,
            timeout_seconds = timeout_seconds,
        )
        tc_result["label"]       = label
        tc_result["type"]        = tc_type
        tc_result["description"] = tc_desc
        all_tc_results.append(tc_result)

        icon = "✓" if tc_result["status"] == "success" else "✗"
        _log(logger,
             f"[{icon}] {label}: {tc_result['status']} "
             f"in {tc_result['elapsed']}s",
             log_callback)

    # ── Memory collection after ALL test cases ────────────────────────
    _phase(logger, "Collecting DUT memory…", log_callback, phase_callback)
    mem_output = os.path.join(
        CHECKOUT_RESULTS_FOLDER, f"{jira_key}_{hostname}"
    )
    mem_result = collect_dut_memory(
        hostname       = hostname,
        jira_key       = jira_key,
        dut_slots      = dut_slots,
        output_folder  = mem_output,
        logger         = logger,
        log_callback   = log_callback,
        phase_callback = phase_callback,
    )

    # ── Teams notification ────────────────────────────────────────────
    final_status  = ("success"
                     if all(r["status"] == "success"
                            for r in all_tc_results)
                     else "partial"
                     if any(r["status"] == "success"
                            for r in all_tc_results)
                     else "failed")
    final_detail  = " | ".join(
        f"{r['label']}:{r['status']}" for r in all_tc_results
    )
    final_elapsed = int(time.time() - start)

    if notify_teams:
        send_teams_notification(
            jira_key     = jira_key,
            hostname     = hostname,
            status       = final_status,
            detail       = final_detail,
            elapsed      = final_elapsed,
            webhook_url  = webhook_url,
            logger       = logger,
            log_callback = log_callback,
        )

    _log(logger,
         f"=== Checkout {final_status.upper()} in {final_elapsed}s ===",
         log_callback)

    return {
        "status":     final_status,
        "detail":     final_detail,
        "elapsed":    final_elapsed,
        "test_cases": all_tc_results,
        "memory":     mem_result,
    }


# ── STANDALONE ENTRY POINT ────────────────────────────────────────────────────
def main():
    valid_envs = get_valid_envs()
    parser = argparse.ArgumentParser(
        description="BENTO Checkout Orchestrator (standalone test)"
    )
    parser.add_argument("--jira-key",    required=True)
    parser.add_argument("--hostname",    required=True)
    parser.add_argument("--env",         required=True,
                        help=f"({', '.join(sorted(valid_envs))})")
    parser.add_argument("--tgz-path",    default="")
    parser.add_argument("--mid",         default="")
    parser.add_argument("--cfgpn",       default="")
    parser.add_argument("--fw-ver",      default="")
    parser.add_argument("--dut-slots",   type=int, default=4)
    parser.add_argument("--lot-prefix",  default="JAANTJB")
    parser.add_argument("--timeout-min", type=int, default=60)
    parser.add_argument("--no-notify",   action="store_true")
    args = parser.parse_args()

    result = run_checkout(
        jira_key        = args.jira_key,
        hostname        = args.hostname,
        env             = args.env,
        tgz_path        = args.tgz_path,
        mid             = args.mid,
        cfgpn           = args.cfgpn,
        fw_ver          = args.fw_ver,
        dut_slots       = args.dut_slots,
        lot_prefix      = args.lot_prefix,
        timeout_seconds = args.timeout_min * 60,
        notify_teams    = not args.no_notify,
    )

    print(f"\nResult : {result['status'].upper()}")
    print(f"Detail : {result['detail']}")
    print(f"Elapsed: {result['elapsed']}s")
    sys.exit(0 if result["status"] == "success" else 1)


if __name__ == "__main__":
    main()
    