#!/usr/bin/env python3
"""
model/orchestrators/checkout_orchestrator.py
=============================================
BENTO Checkout Orchestrator — Phase 2 Auto Start Checkout

Runs on the LOCAL PC. Full flow:
  1. Read CRT Excel from \\sifsmodtestrep\modtestrep\crab\crt_from_sap.xlsx
  2. Generate SLATE XML (correct Profile schema with AutoStart=True)
  3. Drop XML to P:\temp\BENTO\CHECKOUT_QUEUE\
  4. Poll .checkout_status sidecar (mirrors wait_for_build() exactly)
  5. Loop per test case (PASSING + FORCE FAIL sequentially)
  6. Memory collection after all test cases
  7. Teams notification with per-test-case summary

Mirrors compilation_orchestrator.py pattern exactly.
"""

import os
import sys
import time
import json
import logging
import argparse
import threading
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional, Dict, List

# ─────────────────────────────────────────────
# CONFIRMED PATHS (from CheckoutPlan.md + sys_config.json)
# ─────────────────────────────────────────────

# CRT Excel on N: drive — confirmed from Windows Explorer screenshot
CRT_EXCEL_PATH          = r"\\sifsmodtestrep\modtestrep\crab\crt_from_sap.xlsx"
CRT_DB_PATH             = r"\\sifsmodtestrep\modtestrep\crab\closed_crt_jira_info.db"

# Phase 2 shared folders on P: drive
CHECKOUT_QUEUE_FOLDER   = r"P:\temp\BENTO\CHECKOUT_QUEUE"
CHECKOUT_RESULTS_FOLDER = r"P:\temp\BENTO\CHECKOUT_RESULTS"

# SLATE hot folder on tester — confirmed from checkout_tab.py
SLATE_HOT_FOLDER_DEFAULT = r"C:\test_program\playground_queue"

# Polling
POLL_INTERVAL            = 30    # seconds — matches watcher write cadence
CHECKOUT_TIMEOUT_SECONDS = 3600  # 60 min default; 8h for overnight runs

# Teams webhook — override in settings.json or env var
TEAMS_WEBHOOK_URL = ""

# ─────────────────────────────────────────────
# RECIPE MAP per ENV
# Confirmed from FullAutoStart.md Step 9 XML→GUI mapping
# ─────────────────────────────────────────────
RECIPE_MAP = {
    "ABIT": r"RECIPE:PEREGRINE\ON_NEOSEM_ABIT.XML",
    "SFN2": r"RECIPE:PEREGRINE\ON_NEOSEM_SFN2.XML",
    "CNFG": r"RECIPE:PEREGRINE\ON_NEOSEM_CNFG.XML",
}

# ─────────────────────────────────────────────
# CRT EXCEL COLUMN NAMES
# Exact names from crt_excel_template.json [24]
# WARNING: "Product  Name" has DOUBLE SPACE — do NOT fix!
# ─────────────────────────────────────────────
_COL_MATERIAL = "Material description"   # lowercase 'd'
_COL_CFGPN    = "CFGPN"
_COL_FW_WAVE  = "FW Wave ID"
_COL_FIDB     = "FIDB_ASIC_FW_REV"
_COL_PRODUCT  = "Product  Name"          # ← DOUBLE SPACE ⚠️
_COL_CUSTOMER = "CRT Customer"
_COL_DRV_TYPE = "SSD Drive Type"
_COL_ABIT_REL = "ABIT Release (Yes/No)"
_COL_SFN2_REL = "SFN2 Release (Yes/No)"
_COL_CHECKOUT = "CRT Checkout (Yes/No)"


# ─────────────────────────────────────────────
# LOGGER (same style as compilation_orchestrator.py)
# ─────────────────────────────────────────────

def _get_logger(log_callback=None) -> logging.Logger:
    logger = logging.getLogger("bento_checkout_orchestrator")
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"))
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


# ─────────────────────────────────────────────
# STEP 1 — Read CRT Excel
# ─────────────────────────────────────────────

def load_dut_info_from_crt(cfgpn_filter=None, excel_path=None,
                            log_callback=None) -> List[dict]:
    """
    Read CRT Excel from \\sifsmodtestrep\modtestrep\crab\crt_from_sap.xlsx
    Confirmed location from Windows Explorer screenshot (N: drive).

    Mirrors CatDB.update_db_with_crt_excel() in CAT.py exactly:
        df = pd.read_excel(filepath, engine="openpyxl", dtype=str)

    Column names confirmed from crt_excel_template.json [24].
    "Product  Name" has DOUBLE SPACE — do not change.
    """
    try:
        import pandas as pd
    except ImportError:
        raise ImportError("pandas required: pip install pandas openpyxl")

    path = excel_path or CRT_EXCEL_PATH

    if not os.path.exists(path):
        msg = (f"CRT Excel not found at:\n  {path}\n"
               f"Ensure N: drive (\\\\sifsmodtestrep) is mapped "
               f"and C.A.T. has completed a SAP export.")
        if log_callback:
            log_callback(f"✗ {msg}")
        raise FileNotFoundError(msg)

    if log_callback:
        log_callback(f"→ Reading CRT Excel: {os.path.basename(path)}")

    # Mirrors C.A.T. exactly — openpyxl engine, all columns as str
    df = pd.read_excel(path, engine="openpyxl", dtype=str)

    # Validate critical columns
    missing = [c for c in [_COL_CFGPN, _COL_FW_WAVE, _COL_PRODUCT]
               if c not in df.columns]
    if missing:
        raise ValueError(
            f"CRT Excel missing columns: {missing}\n"
            f"Note: 'Product  Name' requires DOUBLE SPACE.")

    # Apply CFGPN filter
    if cfgpn_filter:
        df = df[df[_COL_CFGPN] == str(cfgpn_filter)]

    if df.empty:
        if log_callback:
            log_callback("⚠ No CRT rows found after filter.")
        return []

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
            "CRT Checkout (Yes/No)": str(row.get(_COL_CHECKOUT, "") or "").strip(),
        })

    if log_callback:
        log_callback(f"✓ Loaded {len(result)} DUT record(s) from CRT Excel.")
    return result


# ─────────────────────────────────────────────
# STEP 2 — Generate SLATE XML
# Correct Profile schema from FullAutoStart.md [24]
# ─────────────────────────────────────────────

def generate_slate_xml(
    jira_key:      str,
    mid:           str,
    cfgpn:         str,
    fw_ver:        str,
    dut_slots:     int,
    tgz_path:      str,
    env:           str,
    lot_prefix:    str  = "JAANTJB",
    dut_locations: list = None,
    label:         str  = "",
    output_dir:    str  = "",
    dry_run:       bool = False,
    logger                = None,
    log_callback          = None,
) -> Optional[str]:
    """
    Generate SLATE XML with correct Profile schema.
    AutoStart=True eliminates manual "Run Test" click.

    Schema confirmed from FullAutoStart.md Step 4 & Step 9.
    Lot format: JAANTJB001, JAANTJB002... (auto from lot_prefix)
    DUT locations: ["0,0","0,1","0,2","0,3"] or auto-generated.
    """
    if logger is None:
        logger = _get_logger()

    _out  = output_dir or CHECKOUT_QUEUE_FOLDER
    env_u = env.upper()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    label_part = f"_{label}" if label else ""
    xml_name  = f"checkout_{jira_key}_{env_u}_{timestamp}{label_part}.xml"

    try:
        os.makedirs(_out, exist_ok=True)
    except Exception as e:
        _log(logger, f"✗ Cannot create output dir: {e}", log_callback, "error")
        return None

    xml_path = os.path.join(_out, xml_name)
    recipe   = RECIPE_MAP.get(env_u, r"RECIPE:PEREGRINE\ON_NEOSEM_ABIT.XML")

    _phase(logger, f"Generating SLATE XML [{label or 'default'}]...", log_callback)

    try:
        profile = ET.Element("Profile")

        # ── TestJobArchive ───────────────────────────────────────────────
        # TGZ from Phase 1: P:\temp\BENTO\RELEASE_TGZ\...\ibir_release_ABIT.tgz
        ET.SubElement(profile, "TestJobArchive").text = tgz_path

        # ── RecipeFile ───────────────────────────────────────────────────
        ET.SubElement(profile, "RecipeFile").text = recipe

        # ── TempTraveler ─────────────────────────────────────────────────
        # Confirmed from FullAutoStart.md Step 4
        tt = ET.SubElement(profile, "TempTraveler")
        for section, name, value in [
            ("MAM",       "STEP",          env_u),
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

        # ── MaterialInfo ─────────────────────────────────────────────────
        # Use provided DUT locations or auto-generate from slot count
        locations = dut_locations or [
            f"{i // 8},{i % 8}" for i in range(dut_slots)
        ]
        mat = ET.SubElement(profile, "MaterialInfo")
        for idx, loc in enumerate(locations):
            lot_num = f"{lot_prefix}{str(idx + 1).zfill(3)}"
            a = ET.SubElement(mat, "Attribute")
            a.set("Lot",         lot_num)
            a.set("MID",         mid)
            a.set("DutLocation", loc)

        # ── AutoStart = True ─────────────────────────────────────────────
        # KEY: eliminates manual "Run Test" click [24]
        ET.SubElement(profile, "AutoStart").text = "True"

        # Write XML
        tree = ET.ElementTree(profile)
        try:
            ET.indent(tree, space="    ")   # Python 3.9+
        except AttributeError:
            pass  # Python < 3.9 — skip pretty-printing
        tree.write(xml_path, encoding="unicode", xml_declaration=True)

        size_kb = os.path.getsize(xml_path) / 1024
        _log(logger,
             f"✓ XML: {xml_name} | AutoStart=True | {len(locations)} DUT(s) | {size_kb:.1f} KB",
             log_callback)
        return xml_path

    except Exception as e:
        _log(logger, f"✗ XML generation failed: {e}", log_callback, "error")
        return None


# ─────────────────────────────────────────────
# STATUS FILE (mirrors watcher_lock.py pattern)
# ─────────────────────────────────────────────

def write_checkout_status(xml_path: str, status: str, detail: str = ""):
    """Write .checkout_status sidecar. Mirrors write_status() in watcher_lock.py."""
    status_path = xml_path + ".checkout_status"
    data = {
        "status":    status,
        "detail":    detail,
        "timestamp": datetime.now().isoformat(),
    }
    try:
        with open(status_path, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def read_checkout_status(xml_path: str) -> dict:
    """Safe JSON read — returns {} on any error (file mid-write)."""
    status_path = xml_path + ".checkout_status"
    if not os.path.exists(status_path):
        return {}
    try:
        with open(status_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}   # Treat as "not ready yet"


# ─────────────────────────────────────────────
# STEP 3 — Poll for checkout completion
# Mirrors wait_for_build() in compilation_orchestrator.py exactly
# ─────────────────────────────────────────────

def wait_for_checkout(
    xml_path:        str,
    logger,
    log_callback     = None,
    phase_callback   = None,
    timeout_seconds: int = CHECKOUT_TIMEOUT_SECONDS,
) -> Dict:
    """
    Poll .checkout_status sidecar every 30s.
    Mirrors wait_for_build() in compilation_orchestrator.py exactly.
    """
    start    = time.time()
    deadline = start + timeout_seconds

    _phase(logger, "Waiting for SLATE to complete...", log_callback, phase_callback)
    _log(logger, f"   (timeout = {timeout_seconds // 60} min)", log_callback)

    while time.time() < deadline:
        data  = read_checkout_status(xml_path)
        state = data.get("status", "")

        if state == "success":
            elapsed = int(time.time() - start)
            _log(logger, f"✓ Checkout SUCCESS in {elapsed}s", log_callback)
            return {"status": "success", "detail": data.get("detail", ""),
                    "elapsed": elapsed}

        elif state == "failed":
            elapsed = int(time.time() - start)
            detail  = data.get("detail", "Unknown error")
            _log(logger, f"✗ Checkout FAILED: {detail}", log_callback, "error")
            return {"status": "failed", "detail": detail, "elapsed": elapsed}

        elif state == "in_progress":
            elapsed_so_far = int(time.time() - start)
            if elapsed_so_far % 120 < POLL_INTERVAL:
                _phase(logger,
                       f"SLATE running... ({elapsed_so_far}s elapsed)",
                       log_callback, phase_callback)

        time.sleep(POLL_INTERVAL)

    elapsed = int(time.time() - start)
    _log(logger, f"✗ Checkout TIMEOUT after {elapsed}s", log_callback, "error")
    return {"status": "timeout",
            "detail": f"No SLATE signal within {timeout_seconds}s",
            "elapsed": elapsed}


# ─────────────────────────────────────────────
# MEMORY COLLECTION
# ─────────────────────────────────────────────

def collect_dut_memory(
    hostname:      str,
    jira_key:      str,
    dut_slots:     int,
    dut_locations: list = None,
    output_folder: str  = "",
    logger               = None,
    log_callback         = None,
    phase_callback       = None,
) -> Dict:
    """
    Parallel memory collection for all DUTs after SLATE completes.
    Results saved to P:\temp\BENTO\CHECKOUT_RESULTS\<jira_key>\
    """
    import concurrent.futures

    if logger is None:
        logger = _get_logger()

    _phase(logger, f"Collecting memory for {dut_slots} DUT(s)...",
           log_callback, phase_callback)

    _out = output_folder or os.path.join(CHECKOUT_RESULTS_FOLDER, jira_key)
    try:
        os.makedirs(_out, exist_ok=True)
    except Exception as e:
        _log(logger, f"⚠ Cannot create memory output folder: {e}",
             log_callback, "warning")

    locations = dut_locations or [f"{i//8},{i%8}" for i in range(dut_slots)]

    def _collect_one(slot_idx, loc):
        lot_num  = f"DUT_{slot_idx+1}"
        mem_file = os.path.join(_out, f"{jira_key}_{hostname}_slot{slot_idx+1}_memory.txt")
        try:
            # ── INSERT YOUR REAL MEMORY COLLECTION CALL HERE ──────────────
            # e.g. ssh_exec(hostname, f"collect_memory --slot {slot_idx}")
            with open(mem_file, "w") as f:
                f.write(f"Memory collection placeholder\n")
                f.write(f"JIRA Key    : {jira_key}\n")
                f.write(f"Hostname    : {hostname}\n")
                f.write(f"Slot        : {slot_idx+1}\n")
                f.write(f"DUT Location: {loc}\n")
                f.write(f"Timestamp   : {datetime.now().isoformat()}\n")
            return slot_idx + 1, True
        except Exception as e:
            _log(logger, f"  ✗ Slot {slot_idx+1} memory failed: {e}",
                 log_callback, "warning")
            return slot_idx + 1, False

    collected = []
    failed    = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(_collect_one, idx, loc): idx
                   for idx, loc in enumerate(locations)}
        for future in concurrent.futures.as_completed(futures):
            slot, ok = future.result()
            (collected if ok else failed).append(slot)
            if ok:
                _log(logger, f"  ✓ Slot {slot} memory collected", log_callback)

    status = "success" if not failed else ("partial" if collected else "failed")
    detail = f"Collected {len(collected)}/{len(locations)} slots"
    if failed:
        detail += f" (failed: {failed})"
    _log(logger, f"✓ Memory collection done: {detail}", log_callback)

    return {"status": status, "detail": detail,
            "collected_slots": collected, "output_folder": _out}


# ─────────────────────────────────────────────
# TEAMS NOTIFICATION
# ─────────────────────────────────────────────

def send_teams_notification(
    jira_key:    str,
    hostname:    str,
    status:      str,
    detail:      str,
    elapsed:     int,
    test_cases:  list = None,
    webhook_url: str  = "",
    logger             = None,
    log_callback       = None,
) -> bool:
    if logger is None:
        logger = _get_logger()

    _phase(logger, "Sending Teams notification...", log_callback)

    url = (webhook_url or TEAMS_WEBHOOK_URL
           or os.environ.get("BENTO_TEAMS_WEBHOOK", ""))
    if not url:
        _log(logger, "⚠ Teams webhook URL not configured — skipping.",
             log_callback, "warning")
        return False

    try:
        import urllib.request
        icon   = "✅" if status.lower() == "success" else "❌"
        facts  = [
            {"name": "Status",  "value": status.upper()},
            {"name": "Detail",  "value": detail},
            {"name": "Elapsed", "value": f"{elapsed}s ({elapsed//60}m {elapsed%60}s)"},
            {"name": "Time",    "value": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
        ]
        if test_cases:
            for tc in test_cases:
                tc_icon = "✅" if tc.get("status") == "success" else "❌"
                facts.append({
                    "name":  f"  {tc_icon} {tc.get('label','?')}",
                    "value": f"{tc.get('status','?')} in {tc.get('elapsed',0)}s"
                })

        payload = {
            "@type":      "MessageCard",
            "@context":   "http://schema.org/extensions",
            "themeColor": "0076D7" if status.lower() == "success" else "D40000",
            "summary":    f"BENTO Checkout {status.upper()} — {jira_key}",
            "sections": [{
                "activityTitle":    f"{icon} BENTO Auto Checkout — {status.upper()}",
                "activitySubtitle": f"Tester: {hostname}  |  JIRA: {jira_key}",
                "facts": facts,
            }]
        }
        data = json.dumps(payload).encode("utf-8")
        req  = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                _log(logger, "✓ Teams notification sent.", log_callback)
                return True
            _log(logger, f"⚠ Teams webhook HTTP {resp.status}",
                 log_callback, "warning")
            return False
    except Exception as e:
        _log(logger, f"⚠ Teams notification failed: {e}", log_callback, "warning")
        return False


# ─────────────────────────────────────────────
# PUBLIC API — called from checkout_controller.py
# ─────────────────────────────────────────────

def run_checkout(
    jira_key:        str,
    hostname:        str,
    env:             str,
    tgz_path:        str  = "",
    hot_folder:      str  = "",
    mid:             str  = "",
    cfgpn:           str  = "",
    fw_ver:          str  = "",
    dut_slots:       int  = 4,
    lot_prefix:      str  = "JAANTJB",
    dut_locations:   list = None,
    test_cases:      list = None,
    detect_method:   str  = "AUTO",
    timeout_seconds: int  = CHECKOUT_TIMEOUT_SECONDS,
    notify_teams:    bool = True,
    webhook_url:     str  = "",
    log_callback           = None,
    phase_callback         = None,
) -> Dict:
    """
    High-level entry point called from checkout_controller.py.

    Runs checkout for each test case (PASSING + FORCE FAIL) sequentially.
    Each test case generates its own XML with its own label.
    Memory collection runs once after ALL test cases complete.
    Teams notification includes per-test-case summary.

    Returns dict: {status, detail, elapsed, test_cases, memory}
    """
    logger = _get_logger(log_callback)
    env    = env.upper()
    start  = time.time()

    # Validate
    if not hostname:
        msg = "Hostname is required for checkout."
        _log(logger, f"[FAIL] {msg}", log_callback, "error")
        return {"status": "failed", "detail": msg, "elapsed": 0,
                "test_cases": [], "memory": {}}

    _log(logger, f"=== BENTO Auto Checkout ===", log_callback)
    _log(logger, f"JIRA     : {jira_key}", log_callback)
    _log(logger, f"Hostname : {hostname}", log_callback)
    _log(logger, f"ENV      : {env}", log_callback)
    _log(logger, f"MID      : {mid}", log_callback)
    _log(logger, f"CFGPN    : {cfgpn}", log_callback)
    _log(logger, f"FW       : {fw_ver}", log_callback)
    _log(logger, f"Slots    : {dut_slots}", log_callback)
    _log(logger, f"Lot pfx  : {lot_prefix}", log_callback)

    _queue = CHECKOUT_QUEUE_FOLDER
    _test_cases = test_cases or [{"type": "passing", "label": "passing"}]
    all_tc_results = []

    # ── Loop per test case ────────────────────────────────────────────────
    for tc in _test_cases:
        label   = tc.get("label", "passing")
        tc_type = tc.get("type", "passing")
        tc_desc = tc.get("description", "")

        _log(logger,
             f"\n{'='*50}\n"
             f"Test case: {label} ({tc_type})\n"
             f"{'='*50}",
             log_callback)

        if tc_desc:
            _log(logger, f"Description: {tc_desc}", log_callback)

        # Generate XML for this test case
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
            output_dir    = _queue,
            logger        = logger,
            log_callback  = log_callback,
        )

        if not xml_path:
            tc_result = {"status": "failed",
                         "detail": "XML generation failed",
                         "elapsed": 0, "label": label, "type": tc_type}
            all_tc_results.append(tc_result)
            continue

        # Write initial queued status
        write_checkout_status(xml_path, "queued",
                              f"Waiting for tester pickup — {label}")

        _phase(logger, f"Waiting for tester [{label}]...",
               log_callback, phase_callback)

        # Poll for this test case
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
             f"{icon} {label}: {tc_result['status']} in {tc_result['elapsed']}s",
             log_callback)

        # Stop if a test case fails hard (watcher error, not SLATE fail)
        if tc_result["status"] == "failed" and "Watcher" in tc_result.get("detail", ""):
            _log(logger, "⚠ Watcher error — stopping test case loop.",
                 log_callback, "warning")
            break

    # ── Memory collection after ALL test cases ────────────────────────────
    _phase(logger, "Collecting DUT memory...", log_callback, phase_callback)
    mem_output = os.path.join(CHECKOUT_RESULTS_FOLDER, jira_key)
    mem_result = collect_dut_memory(
        hostname      = hostname,
        jira_key      = jira_key,
        dut_slots     = dut_slots,
        dut_locations = dut_locations,
        output_folder = mem_output,
        logger        = logger,
        log_callback  = log_callback,
        phase_callback= phase_callback,
    )

    # ── Overall status ────────────────────────────────────────────────────
    all_ok      = all(r["status"] == "success" for r in all_tc_results)
    any_ok      = any(r["status"] == "success" for r in all_tc_results)
    final_status = "success" if all_ok else ("partial" if any_ok else "failed")
    final_detail = " | ".join(
        f"{r['label']}:{r['status']}" for r in all_tc_results)
    final_elapsed = int(time.time() - start)

    # ── Teams notification ────────────────────────────────────────────────
    if notify_teams:
        send_teams_notification(
            jira_key    = jira_key,
            hostname    = hostname,
            status      = final_status,
            detail      = final_detail,
            elapsed     = final_elapsed,
            test_cases  = all_tc_results,
            webhook_url = webhook_url,
            logger      = logger,
            log_callback= log_callback,
        )

    _log(logger,
         f"\n=== Checkout {final_status.upper()} in {final_elapsed}s ===",
         log_callback)

    return {
        "status":     final_status,
        "detail":     final_detail,
        "elapsed":    final_elapsed,
        "test_cases": all_tc_results,
        "memory":     mem_result,
    }


# ─────────────────────────────────────────────
# STANDALONE ENTRY POINT
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="BENTO Checkout Orchestrator (standalone test)")
    parser.add_argument("--jira-key",   required=True)
    parser.add_argument("--hostname",   required=True)
    parser.add_argument("--env",        required=True)
    parser.add_argument("--tgz-path",   default="")
    parser.add_argument("--mid",        default="")
    parser.add_argument("--cfgpn",      default="")
    parser.add_argument("--fw-ver",     default="")
    parser.add_argument("--dut-slots",  type=int, default=4)
    parser.add_argument("--lot-prefix", default="JAANTJB")
    parser.add_argument("--timeout-min",type=int, default=60)
    parser.add_argument("--no-notify",  action="store_true")
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

    print(f"\n{'='*60}")
    print(f"  Result : {result['status'].upper()}")
    print(f"  Detail : {result['detail']}")
    print(f"  Elapsed: {result['elapsed']}s")
    for tc in result.get("test_cases", []):
        icon = "✓" if tc["status"] == "success" else "✗"
        print(f"  {icon} {tc['label']}: {tc['status']} ({tc['elapsed']}s)")
    print(f"{'='*60}\n")
    sys.exit(0 if result["status"] in ("success", "partial") else 1)


if __name__ == "__main__":
    main()
