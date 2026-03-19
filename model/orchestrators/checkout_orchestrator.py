#!/usr/bin/env python3
"""
model/orchestrators/checkout_orchestrator.py
=============================================
BENTO Checkout Orchestrator — Phase 2 Auto Start Checkout
==========================================================
Runs on the LOCAL PC (same machine as main.py / jira_analyzer.py).

Mirrors compilation_orchestrator.py EXACTLY in structure:
  - Same polling pattern (.bento_status file)
  - Same ZIP-drop communication bridge via shared folder
  - Same result dict shape: {status, detail, elapsed}
  - Same _log / _phase / _get_logger helpers
  - Same BUILD_TIMEOUT / POLL_INTERVAL guards

Additional Phase 2 responsibilities:
  1. Auto-generate SLATE XML (replaces manual Notepad++ editing)
  2. Drop generated XML into hot folder watched by SSD Tester Engineering GUI
  3. Poll for SLATE completion via multi-method detection:
       LOG    — scan SLATE log file for completion marker
       FOLDER — watch for result folder / result file to appear
       CPU    — poll remote tester CPU idle via VNC/psutil proxy
       TIMEOUT — hard deadline fallback
  4. Auto memory collection for all DUTs after SLATE completes
  5. Send Teams notification on completion

Usage (standalone test):
    python checkout_orchestrator.py \\
        --jira-key TSESSD-123 \\
        --hostname IBIR-0383 \\
        --env ABIT \\
        --hot-folder P:\\temp\\BENTO\\HOT_DROP \\
        --tgz-path P:\\temp\\BENTO\\RELEASE_TGZ\\IBIR-0383_TSESSD-123_ABIT\\ibir_release_ABIT.tgz \\
        --mid 864479 --cfgpn 864479-001 --fw-ver 2.7.0 --dut-slots 4
"""

import os
import sys
import time
import json
import logging
import argparse
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional, Dict

# ─────────────────────────────────────────────
# CONFIGURATION  (mirrors compilation_orchestrator.py layout)
# ─────────────────────────────────────────────

HOT_DROP_FOLDER   = r"P:\temp\BENTO\HOT_DROP"
STATUS_FOLDER     = r"P:\temp\BENTO\CHECKOUT_STATUS"

# How often (seconds) to poll for SLATE completion
POLL_INTERVAL     = 15

# Total wait time before declaring timeout
# Checkout runs can be long — default 60 min
CHECKOUT_TIMEOUT_SECONDS = 3600   # 60 minutes

# Teams webhook URL — override in settings.json
TEAMS_WEBHOOK_URL = ""


# ─────────────────────────────────────────────
# DYNAMIC VALID_ENVS — same as compile side
# ─────────────────────────────────────────────

def get_valid_envs():
    """Load valid environments from the shared tester registry."""
    registry_path = r"P:\temp\BENTO\bento_testers.json"
    try:
        if os.path.exists(registry_path):
            with open(registry_path, "r") as f:
                data = json.load(f)
            return {val["env"].upper() for val in data.values()}
        else:
            return {"ABIT", "SFN2", "CNFG"}
    except Exception:
        return {"ABIT", "SFN2", "CNFG"}


# ─────────────────────────────────────────────
# LOGGER  (same rotating style as compilation_orchestrator.py)
# ─────────────────────────────────────────────

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
    """Log a phase update — piped to both log panel and badge."""
    _log(logger, msg, log_callback)
    if phase_callback:
        phase_callback(msg)


# ─────────────────────────────────────────────
# STEP 1 — Auto-generate SLATE XML
# ─────────────────────────────────────────────

def generate_slate_xml(
    jira_key:   str,
    mid:        str,
    cfgpn:      str,
    fw_ver:     str,
    dut_slots:  int,
    tgz_path:   str,
    output_dir: str,
    dry_run:    bool = False,
    logger=None,
    log_callback=None,
) -> Optional[str]:
    """
    Auto-generate the SLATE XML test configuration file.

    Replaces the manual Notepad++ editing step performed by engineers.
    The XML schema follows the SSD Tester Engineering GUI hot-folder spec.

    Args:
        jira_key   : JIRA ticket identifier (e.g. TSESSD-1234)
        mid        : Memory ID (device identifier)
        cfgpn      : Configuration Part Number
        fw_ver     : Firmware version string
        dut_slots  : Number of DUT slots to test
        tgz_path   : Path to the compiled TGZ artifact
        output_dir : Directory to write the XML file into
        dry_run    : If True, write to output_dir but do NOT drop to hot folder
        logger     : Python logger instance
        log_callback : Optional callable(str) for GUI log panel

    Returns:
        Full path to generated XML, or None on failure.
    """
    if logger is None:
        logger = _get_logger()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    xml_name  = f"SLATE_{jira_key}_{timestamp}.xml"

    try:
        os.makedirs(output_dir, exist_ok=True)
    except Exception as e:
        _log(logger, f"✗ Cannot create output directory: {e}", log_callback, "error")
        return None

    xml_path = os.path.join(output_dir, xml_name)

    _phase(logger, "Generating SLATE XML…", log_callback)

    try:
        # Build XML tree — adjust element names to match your SSD GUI spec
        root = ET.Element("SLATE_Config")
        root.set("version", "1.0")
        root.set("generated", datetime.now().isoformat())
        root.set("jira_key", jira_key)

        # Test identity block
        identity = ET.SubElement(root, "TestIdentity")
        ET.SubElement(identity, "JiraKey").text      = jira_key
        ET.SubElement(identity, "MID").text          = mid
        ET.SubElement(identity, "CFGPN").text        = cfgpn
        ET.SubElement(identity, "FWVersion").text    = fw_ver
        ET.SubElement(identity, "Timestamp").text    = timestamp

        # Test program block
        test_prog = ET.SubElement(root, "TestProgram")
        ET.SubElement(test_prog, "TGZPath").text     = tgz_path
        ET.SubElement(test_prog, "DUTSlots").text    = str(dut_slots)

        # DUT slots block
        duts = ET.SubElement(root, "DUTs")
        for slot in range(1, dut_slots + 1):
            dut = ET.SubElement(duts, "DUT")
            dut.set("slot", str(slot))
            ET.SubElement(dut, "CFGPN").text   = cfgpn
            ET.SubElement(dut, "FW").text      = fw_ver
            ET.SubElement(dut, "MID").text     = mid

        # Write XML
        tree = ET.ElementTree(root)
        ET.indent(tree, space="    ")   # Python 3.9+
        tree.write(xml_path, encoding="unicode", xml_declaration=True)

        size_kb = os.path.getsize(xml_path) / 1024
        _log(logger, f"✓ SLATE XML created: {xml_name} ({size_kb:.1f} KB)", log_callback)
        return xml_path

    except Exception as e:
        _log(logger, f"✗ SLATE XML generation failed: {e}", log_callback, "error")
        return None


# ─────────────────────────────────────────────
# STEP 2 — Drop XML into hot folder
# ─────────────────────────────────────────────

def drop_xml_to_hot_folder(xml_path: str, hot_folder: str, logger,
                            log_callback=None) -> Optional[str]:
    """
    Copy the generated XML into the hot folder watched by the SSD Tester GUI.

    Returns the destination path, or None on failure.
    """
    _phase(logger, "Dropping XML to hot folder…", log_callback)

    try:
        os.makedirs(hot_folder, exist_ok=True)
    except Exception as e:
        _log(logger, f"✗ Cannot access hot folder: {e}", log_callback, "error")
        return None

    dest = os.path.join(hot_folder, os.path.basename(xml_path))
    try:
        import shutil
        shutil.copy2(xml_path, dest)
        _log(logger, f"✓ XML dropped: {os.path.basename(dest)}", log_callback)
        return dest
    except Exception as e:
        _log(logger, f"✗ Hot folder drop failed: {e}", log_callback, "error")
        return None


# ─────────────────────────────────────────────
# STEP 3 — Poll for SLATE completion
# Multi-method: LOG / FOLDER / CPU / TIMEOUT
# ─────────────────────────────────────────────

def _check_log_completion(log_path: str) -> bool:
    """
    LOG method: scan the SLATE log file for a known completion marker string.
    Adjust marker to match your SSD tester's log format.
    """
    completion_markers = [
        "Test Complete",
        "SLATE_DONE",
        "All tests passed",
        "Test execution complete",
    ]
    try:
        if not os.path.exists(log_path):
            return False
        with open(log_path, "r", errors="ignore") as f:
            content = f.read()
        return any(marker in content for marker in completion_markers)
    except Exception:
        return False


def _check_folder_completion(result_folder: str) -> bool:
    """
    FOLDER method: watch for a result folder or result file to appear.
    Adjust the expected path to match your SSD tester's output convention.
    """
    try:
        return os.path.isdir(result_folder) and bool(os.listdir(result_folder))
    except Exception:
        return False


def _read_checkout_status(status_file: str) -> Optional[Dict]:
    """
    Read .bento_status sidecar file — mirrors _read_status() in
    compilation_orchestrator.py exactly.
    """
    if not os.path.exists(status_file):
        return None
    try:
        with open(status_file) as f:
            return json.load(f)
    except Exception:
        return None


def wait_for_checkout(
    jira_key:        str,
    hostname:        str,
    hot_folder:      str,
    detect_method:   str = "AUTO",
    timeout_seconds: int = CHECKOUT_TIMEOUT_SECONDS,
    slate_log_path:  str = "",
    result_folder:   str = "",
    logger           = None,
    log_callback     = None,
    phase_callback   = None,
) -> Dict:
    """
    Poll until SLATE completes or timeout is reached.

    Detection methods (tried in order when detect_method == "AUTO"):
      LOG    — scan SLATE log file for completion marker
      FOLDER — watch for result folder to populate
      CPU    — future: poll tester CPU idle via VNC/psutil proxy
      TIMEOUT — hard deadline fallback (always last)

    Returns result dict:
        {
            "status":  "success" | "failed" | "timeout",
            "detail":  "<message>",
            "elapsed": <seconds>
        }

    Mirrors wait_for_build() in compilation_orchestrator.py exactly.
    """
    if logger is None:
        logger = _get_logger()

    start    = time.time()
    deadline = start + timeout_seconds

    # Status file mirrors compilation pattern
    status_file = os.path.join(hot_folder, f"{jira_key}_{hostname}_checkout.bento_status")
    method      = detect_method.upper()

    _phase(logger, f"Waiting for SLATE ({method} detection)…", log_callback, phase_callback)
    _log(logger, f"   (timeout = {timeout_seconds // 60} min)", log_callback)

    while time.time() < deadline:
        elapsed_so_far = int(time.time() - start)

        # ── Method 1: .bento_status file (set by checkout_watcher on tester) ──
        status_data = _read_checkout_status(status_file)
        if status_data:
            state = status_data.get("status", "")
            if state == "success":
                elapsed = int(time.time() - start)
                _log(logger, f"✓ Checkout SUCCESS in {elapsed}s", log_callback)
                return {"status": "success", "detail": status_data.get("detail", ""), "elapsed": elapsed}
            elif state == "failed":
                elapsed = int(time.time() - start)
                detail  = status_data.get("detail", "Unknown error")
                _log(logger, f"✗ Checkout FAILED after {elapsed}s: {detail}", log_callback, "error")
                return {"status": "failed", "detail": detail, "elapsed": elapsed}
            elif state == "in_progress":
                if elapsed_so_far % 120 < POLL_INTERVAL:
                    _phase(logger, f"SLATE running… ({elapsed_so_far}s elapsed)",
                           log_callback, phase_callback)

        # ── Method 2: LOG file scan ────────────────────────────────────────
        if method in ("AUTO", "LOG") and slate_log_path:
            if _check_log_completion(slate_log_path):
                elapsed = int(time.time() - start)
                _log(logger, f"✓ SLATE completion detected via LOG in {elapsed}s", log_callback)
                return {"status": "success", "detail": "Detected via log marker", "elapsed": elapsed}

        # ── Method 3: FOLDER watch ─────────────────────────────────────────
        if method in ("AUTO", "FOLDER") and result_folder:
            if _check_folder_completion(result_folder):
                elapsed = int(time.time() - start)
                _log(logger, f"✓ SLATE completion detected via FOLDER in {elapsed}s", log_callback)
                return {"status": "success", "detail": "Detected via result folder", "elapsed": elapsed}

        time.sleep(POLL_INTERVAL)

    # Timed out
    elapsed = int(time.time() - start)
    _log(logger, f"✗ Checkout TIMEOUT after {elapsed}s", log_callback, "error")
    return {
        "status":  "timeout",
        "detail":  f"No SLATE completion signal within {timeout_seconds}s",
        "elapsed": elapsed,
    }


# ─────────────────────────────────────────────
# STEP 4 — Auto memory collection for all DUTs
# ─────────────────────────────────────────────

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

    Placeholder implementation — wire to your actual memory collection
    tool (SSH command, VNC automation, REST call, etc.).

    Returns dict: {status, detail, collected_slots}
    """
    if logger is None:
        logger = _get_logger()

    _phase(logger, f"Collecting memory for {dut_slots} DUT(s)…",
           log_callback, phase_callback)

    try:
        os.makedirs(output_folder, exist_ok=True)
    except Exception as e:
        _log(logger, f"⚠ Cannot create memory output folder: {e}", log_callback, "warning")

    collected = []
    failed    = []

    for slot in range(1, dut_slots + 1):
        try:
            # ── INSERT YOUR MEMORY COLLECTION CALL HERE ──────────────────
            # Example: ssh_exec(hostname, f"collect_memory --slot {slot}")
            # For now we simulate success and write a placeholder file.
            mem_file = os.path.join(output_folder, f"{jira_key}_slot{slot}_memory.txt")
            with open(mem_file, "w") as f:
                f.write(f"Memory collection placeholder\n")
                f.write(f"JIRA Key : {jira_key}\n")
                f.write(f"Hostname : {hostname}\n")
                f.write(f"Slot     : {slot}\n")
                f.write(f"Timestamp: {datetime.now().isoformat()}\n")
            collected.append(slot)
            _log(logger, f"  ✓ Slot {slot} memory collected → {os.path.basename(mem_file)}",
                 log_callback)
        except Exception as e:
            failed.append(slot)
            _log(logger, f"  ✗ Slot {slot} memory collection failed: {e}",
                 log_callback, "warning")

    status = "success" if not failed else ("partial" if collected else "failed")
    detail = f"Collected {len(collected)}/{dut_slots} slots"
    if failed:
        detail += f" (failed slots: {failed})"
    _log(logger, f"✓ Memory collection done: {detail}", log_callback)

    return {"status": status, "detail": detail, "collected_slots": collected}


# ─────────────────────────────────────────────
# STEP 5 — Teams notification
# ─────────────────────────────────────────────

def send_teams_notification(
    jira_key:   str,
    hostname:   str,
    status:     str,
    detail:     str,
    elapsed:    int,
    webhook_url: str = "",
    logger      = None,
    log_callback = None,
) -> bool:
    """
    Send a Microsoft Teams notification via webhook on checkout completion.

    Args:
        webhook_url : Teams incoming webhook URL (from settings.json or env var)

    Returns True if sent successfully, False otherwise.
    """
    if logger is None:
        logger = _get_logger()

    _phase(logger, "Sending Teams notification…", log_callback)

    url = webhook_url or TEAMS_WEBHOOK_URL or os.environ.get("BENTO_TEAMS_WEBHOOK", "")
    if not url:
        _log(logger, "⚠ Teams webhook URL not configured — skipping notification.",
             log_callback, "warning")
        return False

    try:
        import urllib.request
        icon = "✅" if status.lower() == "success" else "❌"
        payload = {
            "@type":      "MessageCard",
            "@context":   "http://schema.org/extensions",
            "themeColor": "0076D7" if status.lower() == "success" else "D40000",
            "summary":    f"BENTO Checkout {status.upper()} — {jira_key}",
            "sections": [{
                "activityTitle":    f"{icon} BENTO Auto Checkout — {status.upper()}",
                "activitySubtitle": f"Tester: {hostname}  |  JIRA: {jira_key}",
                "facts": [
                    {"name": "Status",  "value": status.upper()},
                    {"name": "Detail",  "value": detail},
                    {"name": "Elapsed", "value": f"{elapsed}s ({elapsed // 60}m {elapsed % 60}s)"},
                    {"name": "Time",    "value": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                ],
            }]
        }
        data  = json.dumps(payload).encode("utf-8")
        req   = urllib.request.Request(url, data=data,
                                        headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                _log(logger, "✓ Teams notification sent.", log_callback)
                return True
            else:
                _log(logger, f"⚠ Teams webhook returned HTTP {resp.status}",
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
    detect_method:   str  = "AUTO",
    timeout_seconds: int  = CHECKOUT_TIMEOUT_SECONDS,
    notify_teams:    bool = True,
    webhook_url:     str  = "",
    slate_log_path:  str  = "",
    result_folder:   str  = "",
    log_callback           = None,
    phase_callback         = None,
) -> Dict:
    """
    High-level entry point called from checkout_controller.py.

    Orchestrates the full Phase 2 Auto Checkout flow:
      1. Generate SLATE XML
      2. Drop XML to hot folder
      3. Poll for SLATE completion
      4. Auto memory collection
      5. Teams notification

    Returns dict: {status, detail, elapsed}

    Mirrors compile_tp_package() in compilation_orchestrator.py exactly.
    """
    _hot    = hot_folder if hot_folder else HOT_DROP_FOLDER
    logger  = _get_logger(log_callback)
    env     = env.upper()
    start   = time.time()

    # ── Validate inputs ───────────────────────────────────────────────────
    if not hostname:
        msg = "Hostname is required for checkout."
        _log(logger, f"[FAIL] {msg}", log_callback, "error")
        return {"status": "failed", "detail": msg, "elapsed": 0}

    valid_envs = get_valid_envs()
    if env not in valid_envs:
        msg = f"Unknown environment '{env}'. Valid: {', '.join(sorted(valid_envs))}"
        _log(logger, f"[FAIL] {msg}", log_callback, "error")
        return {"status": "failed", "detail": msg, "elapsed": 0}

    _log(logger, f"=== BENTO Auto Checkout ===", log_callback)
    _log(logger, f"JIRA    : {jira_key}", log_callback)
    _log(logger, f"Hostname: {hostname}", log_callback)
    _log(logger, f"ENV     : {env}", log_callback)
    _log(logger, f"MID     : {mid}", log_callback)
    _log(logger, f"CFGPN   : {cfgpn}", log_callback)
    _log(logger, f"FW      : {fw_ver}", log_callback)
    _log(logger, f"Slots   : {dut_slots}", log_callback)

    # ── Step 1: Generate SLATE XML ────────────────────────────────────────
    xml_path = generate_slate_xml(
        jira_key=jira_key, mid=mid, cfgpn=cfgpn, fw_ver=fw_ver,
        dut_slots=dut_slots, tgz_path=tgz_path,
        output_dir=_hot, logger=logger, log_callback=log_callback,
    )
    if not xml_path:
        elapsed = int(time.time() - start)
        return {"status": "failed", "detail": "SLATE XML generation failed.", "elapsed": elapsed}

    # ── Step 2: Drop XML to hot folder ────────────────────────────────────
    dest_xml = drop_xml_to_hot_folder(xml_path, _hot, logger, log_callback)
    if not dest_xml:
        elapsed = int(time.time() - start)
        return {"status": "failed", "detail": "XML hot-folder drop failed.", "elapsed": elapsed}

    # ── Step 3: Poll for SLATE completion ─────────────────────────────────
    _phase(logger, "Waiting for SLATE to complete…", log_callback, phase_callback)
    checkout_result = wait_for_checkout(
        jira_key=jira_key, hostname=hostname, hot_folder=_hot,
        detect_method=detect_method, timeout_seconds=timeout_seconds,
        slate_log_path=slate_log_path, result_folder=result_folder,
        logger=logger, log_callback=log_callback, phase_callback=phase_callback,
    )

    if checkout_result["status"] not in ("success",):
        # Failed or timed out — still attempt memory collection on best-effort
        _log(logger, f"⚠ SLATE did not complete cleanly: {checkout_result['detail']}",
             log_callback, "warning")

    # ── Step 4: Auto memory collection ───────────────────────────────────
    _phase(logger, "Collecting DUT memory…", log_callback, phase_callback)
    mem_output = os.path.join(_hot, "memory", f"{jira_key}_{hostname}")
    mem_result = collect_dut_memory(
        hostname=hostname, jira_key=jira_key, dut_slots=dut_slots,
        output_folder=mem_output, logger=logger,
        log_callback=log_callback, phase_callback=phase_callback,
    )

    # ── Step 5: Teams notification ────────────────────────────────────────
    final_status  = checkout_result["status"]
    final_detail  = checkout_result["detail"]
    final_elapsed = int(time.time() - start)

    if notify_teams:
        send_teams_notification(
            jira_key=jira_key, hostname=hostname, status=final_status,
            detail=final_detail, elapsed=final_elapsed,
            webhook_url=webhook_url, logger=logger, log_callback=log_callback,
        )

    _log(logger, f"=== Checkout {final_status.upper()} in {final_elapsed}s ===", log_callback)

    return {
        "status":       final_status,
        "detail":       final_detail,
        "elapsed":      final_elapsed,
        "memory":       mem_result,
        "xml_path":     dest_xml,
    }


# ─────────────────────────────────────────────
# STANDALONE TEST ENTRY POINT
# (mirrors compilation_orchestrator.py main() exactly)
# ─────────────────────────────────────────────

def main():
    valid_envs = get_valid_envs()

    parser = argparse.ArgumentParser(description="BENTO Checkout Orchestrator (standalone test)")
    parser.add_argument("--jira-key",       required=True,  help="e.g. TSESSD-123")
    parser.add_argument("--hostname",       required=True,  help="Tester hostname (e.g. IBIR-0383)")
    parser.add_argument("--env",            required=True,  help=f"Environment ({', '.join(sorted(valid_envs))})")
    parser.add_argument("--hot-folder",     required=True,  help="Hot folder path")
    parser.add_argument("--tgz-path",       default="",     help="Path to compiled TGZ")
    parser.add_argument("--mid",            default="",     help="MID (device identifier)")
    parser.add_argument("--cfgpn",          default="",     help="Configuration Part Number")
    parser.add_argument("--fw-ver",         default="",     help="Firmware version string")
    parser.add_argument("--dut-slots",      type=int, default=4, help="Number of DUT slots")
    parser.add_argument("--detect-method",  default="AUTO", help="LOG/FOLDER/CPU/TIMEOUT/AUTO")
    parser.add_argument("--timeout-min",    type=int, default=60, help="Timeout in minutes")
    parser.add_argument("--no-notify",      action="store_true",  help="Skip Teams notification")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  BENTO Checkout Orchestrator (Phase 2)")
    print(f"  JIRA    : {args.jira_key}")
    print(f"  Hostname: {args.hostname}")
    print(f"  Env     : {args.env}")
    print(f"  Slots   : {args.dut_slots}")
    print(f"{'='*60}\n")

    result = run_checkout(
        jira_key        = args.jira_key,
        hostname        = args.hostname,
        env             = args.env,
        tgz_path        = args.tgz_path,
        hot_folder      = args.hot_folder,
        mid             = args.mid,
        cfgpn           = args.cfgpn,
        fw_ver          = args.fw_ver,
        dut_slots       = args.dut_slots,
        detect_method   = args.detect_method,
        timeout_seconds = args.timeout_min * 60,
        notify_teams    = not args.no_notify,
    )

    print(f"\n{'='*60}")
    print(f"  Result : {result['status'].upper()}")
    print(f"  Detail : {result['detail']}")
    print(f"  Elapsed: {result['elapsed']}s")
    print(f"{'='*60}\n")

    sys.exit(0 if result["status"] == "success" else 1)


if __name__ == "__main__":
    main()
