#!/usr/bin/env python3
"""
BENTO Compilation Orchestrator
================================
Runs on the LOCAL PC (the same machine running main.py / jira_analyzer.py).

Responsibilities:
  1. Packages the TP repository into a ZIP (named with env + JIRA key)
  2. Drops the ZIP into the shared RAW_ZIP folder
  3. Polls the shared folder for a .bento_status file written by the tester
  4. Waits for the compiled .tgz to appear in RELEASE_TGZ
  5. Returns a structured result dict that the BENTO pipeline can log / display

Designed to plug into jira_analyzer.py as a new pipeline step
(call compile_tp_package() from your workflow after code changes are staged).

Usage (standalone test):
    python compilation_orchestrator.py \\
        --zip-source C:\\repos\\IBIR \\
        --env ABIT \\
        --jira-key TSESSD-123
"""

import os
import sys
import time
import json
import logging
import argparse
import zipfile
from datetime import datetime
from typing import Optional, Dict

# ─────────────────────────────────────────────
# CONFIGURATION  (mirror what's in tester_watcher.py)
# ─────────────────────────────────────────────

RAW_ZIP_FOLDER    = r"P:\temp\BENTO\RAW_ZIP"
RELEASE_TGZ_FOLDER = r"P:\temp\BENTO\RELEASE_TGZ"

# How often (seconds) to poll for build completion
POLL_INTERVAL = 15

# Total time to wait before declaring a timeout
# IMPORTANT: Must be LONGER than watcher's BUILD_TIMEOUT_SECONDS (1800s = 30 min)
# so orchestrator never gives up before watcher does
BUILD_TIMEOUT_SECONDS = 2100   # 35 minutes (watcher has 30 min)

# File extensions to exclude when zipping the repo
ZIP_EXCLUDE_PATTERNS = {
    # Version control
    ".git",
    # Python
    "__pycache__", ".pyc", ".pyo",
    # BENTO internals
    ".bento_lock", ".bento_status",
    # Node
    "node_modules",
    # Firmware build artifacts
    "release",          # entire release/ folder
    ".o",               # compiled object files
    ".d",               # dependency files
    ".a",               # static libraries
    ".map",             # linker map files
    ".elf",             # compiled binaries
    ".tgz",             # previous release archives
    ".bin",             # binary firmware images
    ".hex",             # hex firmware images
}

# ─────────────────────────────────────────────
# DYNAMIC VALID_ENVS - loaded from shared registry
# ─────────────────────────────────────────────

def get_valid_envs():
    """
    Load valid environments from the shared tester registry.
    Returns set of ENV tokens that have registered testers.
    """
    registry_path = r"P:\temp\BENTO\bento_testers.json"
    try:
        if os.path.exists(registry_path):
            with open(registry_path, "r") as f:
                data = json.load(f)
            # Extract ENV from each tester entry
            return {val["env"].upper() for val in data.values()}
        else:
            # Fallback to defaults if registry doesn't exist yet
            return {"ABIT", "SFN2", "CNFG"}
    except Exception:
        # Fallback on error
        return {"ABIT", "SFN2", "CNFG"}

# ─────────────────────────────────────────────
# LOGGER  (uses same rotating style as main.py)
# ─────────────────────────────────────────────

def _get_logger(log_callback=None) -> logging.Logger:
    logger = logging.getLogger("bento_orchestrator")
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s",
                                          datefmt="%Y-%m-%d %H:%M:%S"))
        logger.addHandler(sh)
    return logger


def _log(logger, msg: str, log_callback=None, level: str = "info"):
    getattr(logger, level)(msg)
    if log_callback:
        log_callback(msg)   # pipe to BENTO GUI log panel


def _phase(logger, msg: str, log_callback=None):
    """Log a phase update that the GUI can detect and display in status"""
    _log(logger, msg, log_callback)
    if log_callback:
        log_callback("__PHASE__:" + msg)


# ─────────────────────────────────────────────
# STEP 1 – ZIP the TP source directory
# ─────────────────────────────────────────────

def create_tp_zip(source_dir, env, jira_key, hostname, logger, log_callback=None, raw_zip_folder=None, label=""):
    """
    Zip the TP repository at `source_dir` and write it to RAW_ZIP_FOLDER.

    Filename convention:  <JIRA_KEY>_<ENV>_<YYYYMMDD_HHMMSS>.zip
    Example:              TSESSD-123_ABIT_20260310_143502.zip

    Returns the full path to the created ZIP, or None on failure.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")  # Include seconds to prevent collision
    zip_name  = f"{jira_key}_{hostname}_{env}_{timestamp}" + ("_" + label if label else "") + ".zip"
    _raw_zip = raw_zip_folder if raw_zip_folder else RAW_ZIP_FOLDER
    zip_path  = os.path.join(_raw_zip, zip_name)

    _phase(logger, "Zipping repository...", log_callback)

    if not os.path.isdir(source_dir):
        _log(logger, "[FAIL] Source directory not found: " + source_dir, log_callback, "error")
        return None

    try:
        os.makedirs(_raw_zip, exist_ok=True)
    except Exception as e:
        _log(logger, f"✗ Cannot access RAW_ZIP folder: {e}", log_callback, "error")
        return None

    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(source_dir):
                # Skip excluded directories in-place so os.walk skips them
                dirs[:] = [d for d in dirs if d not in ZIP_EXCLUDE_PATTERNS]

                for file in files:
                    if any(file.endswith(ext) for ext in ZIP_EXCLUDE_PATTERNS):
                        continue
                    full_path = os.path.join(root, file)
                    arcname   = os.path.relpath(full_path, source_dir)
                    zf.write(full_path, arcname)

        size_mb = os.path.getsize(zip_path) / (1024 * 1024)
        _log(logger, f"✓ ZIP created: {zip_name} ({size_mb:.1f} MB)", log_callback)
        return zip_path

    except Exception as e:
        _log(logger, f"✗ ZIP creation failed: {e}", log_callback, "error")
        return None


# ─────────────────────────────────────────────
# STEP 2 – Poll for build completion
# ─────────────────────────────────────────────

def _read_status(zip_path: str) -> Optional[Dict]:
    status_file = zip_path + ".bento_status"
    if not os.path.exists(status_file):
        return None
    try:
        with open(status_file) as f:
            return json.load(f)
    except Exception:
        return None


def wait_for_build(zip_path, logger, log_callback=None, release_tgz_folder=None):
    """
    Poll until the tester watcher writes a terminal status
    ('success' or 'failed') or the timeout is reached.

    Returns a result dict:
        {
            "status":   "success" | "failed" | "timeout",
            "tgz_file": "<name>.tgz" | None,
            "detail":   "<message>",
            "elapsed":  <seconds>
        }
    """
    zip_name  = os.path.basename(zip_path)
    start     = time.time()
    deadline  = start + BUILD_TIMEOUT_SECONDS

    _phase(logger, "Waiting for tester to pick up ZIP...", log_callback)
    _log(logger, f"   (timeout = {BUILD_TIMEOUT_SECONDS // 60} min)", log_callback)

    last_phase_update = start
    while time.time() < deadline:
        status_data = _read_status(zip_path)

        if status_data:
            state = status_data.get("status", "")

            if state == "success":
                tgz_name = status_data.get("detail", "")
                _rel = release_tgz_folder if release_tgz_folder else RELEASE_TGZ_FOLDER
                # New structure: RELEASE/<HOSTNAME>_<JIRA>/<file>.tgz - search subfolders too
                tgz_full = os.path.join(_rel, tgz_name)
                if not os.path.exists(tgz_full):
                    # Search one level of subfolders (new folder-per-tester structure)
                    for sub in os.listdir(_rel) if os.path.isdir(_rel) else []:
                        candidate = os.path.join(_rel, sub, tgz_name)
                        if os.path.exists(candidate):
                            tgz_full = candidate
                            break

                # Confirm TGZ actually landed in RELEASE folder
                if os.path.exists(tgz_full):
                    elapsed = int(time.time() - start)
                    _log(logger, f"✓ Build SUCCESS in {elapsed}s → {tgz_name}", log_callback)
                    
                    # Clean up ZIP and status file after successful compile
                    try:
                        os.remove(zip_path)
                        os.remove(zip_path + ".bento_status")
                        _log(logger, f"✓ Cleaned up {os.path.basename(zip_path)}", log_callback)
                    except Exception as e:
                        # Non-fatal — share might not allow delete, or files already gone
                        _log(logger, f"⚠ Could not clean up ZIP: {e}", log_callback, "warning")
                    
                    return {
                        "status":   "success",
                        "tgz_file": tgz_name,
                        "tgz_path": tgz_full,
                        "detail":   f"Compiled in {elapsed}s",
                        "elapsed":  elapsed,
                    }
                # TGZ not there yet – tester is still copying; wait a bit more

            elif state == "failed":
                elapsed = int(time.time() - start)
                detail  = status_data.get("detail", "Unknown error")
                _log(logger, f"✗ Build FAILED after {elapsed}s: {detail}", log_callback, "error")
                return {
                    "status":   "failed",
                    "tgz_file": None,
                    "detail":   detail,
                    "elapsed":  elapsed,
                }

            elif state == "in_progress":
                elapsed_so_far = int(time.time() - start)
                # Update phase every 2 minutes to avoid log spam
                if elapsed_so_far % 120 < POLL_INTERVAL:
                    _phase(logger, f"Building... ({elapsed_so_far}s elapsed)", log_callback)
                    last_phase_update = time.time()

        time.sleep(POLL_INTERVAL)

    # Timed out
    elapsed = int(time.time() - start)
    _log(logger, f"✗ Build TIMEOUT after {elapsed}s", log_callback, "error")
    return {
        "status":   "timeout",
        "tgz_file": None,
        "detail":   f"No response from tester within {BUILD_TIMEOUT_SECONDS}s",
        "elapsed":  elapsed,
    }


# ─────────────────────────────────────────────
# PUBLIC API – called from jira_analyzer.py
# ─────────────────────────────────────────────

def compile_tp_package(
    source_dir,
    env,
    jira_key,
    hostname="",
    log_callback=None,
    raw_zip_folder=None,
    release_tgz_folder=None,
    label="",
):
    """
    High-level entry point called from main.py.

    Args:
        source_dir         : Local path to the TP repository
        env                : Tester environment ABIT / SFN2 / CNFG
        jira_key           : JIRA issue key e.g. TSESSD-123
        hostname           : Tester hostname (required)
        log_callback       : Optional callable(str) for GUI log panel
        raw_zip_folder     : Override RAW_ZIP path from GUI (optional)
        release_tgz_folder : Override RELEASE_TGZ path from GUI (optional)
        label              : Optional label for TGZ filename

    Returns dict: status, tgz_file, tgz_path, detail, elapsed
    """
    _raw_zip = raw_zip_folder if raw_zip_folder else RAW_ZIP_FOLDER
    _release = release_tgz_folder if release_tgz_folder else RELEASE_TGZ_FOLDER

    logger = _get_logger(log_callback)
    env = env.upper()
    
    # Validate hostname is provided
    if not hostname:
        msg = "Hostname is required for compilation"
        _log(logger, "[FAIL] " + msg, log_callback, "error")
        return {"status": "failed", "tgz_file": None, "detail": msg, "elapsed": 0}
    
    # Validate env exists in registry (dynamic check)
    valid_envs = get_valid_envs()
    if env not in valid_envs:
        msg = f"Unknown environment '{env}'. Valid: {', '.join(sorted(valid_envs))}"
        _log(logger, "[FAIL] " + msg, log_callback, "error")
        return {"status": "failed", "tgz_file": None, "detail": msg, "elapsed": 0}

    zip_path = create_tp_zip(source_dir, env, jira_key, hostname, logger, log_callback, _raw_zip, label=label)
    if not zip_path:
        return {
            "status":   "failed",
            "tgz_file": None,
            "detail":   "ZIP creation failed - check source_dir and shared folder access",
            "elapsed":  0,
        }

    result = wait_for_build(zip_path, logger, log_callback, _release)
    result["zip_file"] = os.path.basename(zip_path)
    return result


def compile_tp_package_multi(
    source_dir,
    targets,
    jira_key,
    log_callback=None,
    raw_zip_folder=None,
    release_tgz_folder=None,
    label="",
):
    """
    Parallel multi-tester compilation.
    
    Fans out ZIP creation and polling to multiple testers concurrently,
    without blocking the GUI. Each tester gets its own ZIP and polls
    independently.
    
    Args:
        source_dir         : Local path to the TP repository
        targets            : List of (hostname, env) tuples — one per tester
        jira_key           : JIRA issue key e.g. TSESSD-123
        log_callback       : Optional callable(str) for GUI log panel
        raw_zip_folder     : Override RAW_ZIP path from GUI (optional)
        release_tgz_folder : Override RELEASE_TGZ path from GUI (optional)
        label              : Optional label for TGZ filename
    
    Returns:
        List of result dicts, one per tester, each with 'hostname' and 'env' keys added.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    def _one(hostname, env):
        result = compile_tp_package(
            source_dir=source_dir,
            env=env,
            jira_key=jira_key,
            hostname=hostname,
            log_callback=log_callback,
            raw_zip_folder=raw_zip_folder,
            release_tgz_folder=release_tgz_folder,
            label=label,
        )
        result["hostname"] = hostname
        result["env"] = env
        return result
    
    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        futures = {pool.submit(_one, h, e): (h, e) for h, e in targets}
        results = []
        for future in as_completed(futures):
            results.append(future.result())
    
    return results


# ─────────────────────────────────────────────
# STANDALONE TEST ENTRY POINT
# ─────────────────────────────────────────────

def main():
    # Load valid environments dynamically
    valid_envs = get_valid_envs()
    
    parser = argparse.ArgumentParser(description="BENTO Compilation Orchestrator (standalone test)")
    parser.add_argument("--zip-source", required=True, help="Path to TP repo to package")
    parser.add_argument("--env",        required=True, help=f"Environment ({', '.join(sorted(valid_envs))})")
    parser.add_argument("--hostname",   required=True, help="Tester hostname (e.g. IBIR-0383)")
    parser.add_argument("--jira-key",   required=True, help="e.g. TSESSD-123")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  BENTO Compilation Orchestrator")
    print(f"  JIRA:     {args.jira_key}")
    print(f"  Hostname: {args.hostname}")
    print(f"  Env:      {args.env}")
    print(f"  Source:   {args.zip_source}")
    print(f"{'='*60}\n")

    result = compile_tp_package(
        source_dir=args.zip_source,
        env=args.env,
        jira_key=args.jira_key,
        hostname=args.hostname,
    )
    
    print(f"\n{'='*60}")
    print(f"  Result:  {result['status'].upper()}")
    if result.get("tgz_file"):
        print(f"  TGZ:     {result['tgz_file']}")
    print(f"  Detail:  {result['detail']}")
    print(f"  Elapsed: {result['elapsed']}s")
    print(f"{'='*60}\n")

    sys.exit(0 if result["status"] == "success" else 1)


if __name__ == "__main__":
    main()
    