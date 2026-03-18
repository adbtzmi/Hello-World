#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
checkout_watcher.py
===================
BENTO Checkout Watcher — mirrors watcher_main.py [5]

Runs on TESTER machine. Monitors CHECKOUT_QUEUE for incoming XML profiles,
launches SLATE via hot folder, monitors completion, triggers memory collection.

Usage:
    python checkout_watcher.py --env ABIT
    python checkout_watcher.py --env SFN2
    python checkout_watcher.py --env CNFG

File structure (deploy alongside watcher_main.py):
    bento_tester/
        watcher_main.py         <- Phase 1 (compilation)
        checkout_watcher.py     <- Phase 2 (checkout) THIS FILE
        watcher_config.py       <- shared config
        watcher_lock.py         <- shared lock/status logic

Mirrors EXACT same pattern as watcher_main.py [5]:
    - Same poll loop structure
    - Same pre_existing set to skip old files
    - Same processed set for deduplication
    - Same retry_counts dict for temporary failures
    - Same heartbeat logging
"""

import os
import sys
import time
import json
import logging
import argparse
import threading
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional, List, Dict, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

# Import shared watcher modules
sys.path.insert(0, os.path.dirname(__file__))
from watcher_config import (
    TESTER_REGISTRY,
    POLL_INTERVAL_SECONDS,
    LOCK_MAX_AGE_SECONDS,
    LOG_DIR,
)
from watcher_lock import LockFile

# ================================================================
# CHECKOUT-SPECIFIC CONFIGURATION
# ================================================================

CHECKOUT_QUEUE_FOLDER = r"P:\temp\BENTO\CHECKOUT_QUEUE"
CHECKOUT_RESULTS_FOLDER = r"P:\temp\BENTO\CHECKOUT_RESULTS"

# SLATE hot folder where SSD Tester Engineering GUI watches
SLATE_HOT_FOLDER = r"C:\test_program\playground_queue"

# SLATE system log for completion detection
SLATE_LOG_PATH = r"C:\test_program\logs\slate_system.log"

# SLATE output folder for result files
SLATE_OUTPUT_FOLDER = r"C:\test_program\results"

# Memory collection executable
MEMORY_COLLECT_EXE = r"C:\tools\memory_collect.exe"

# Checkout timeout (8 hours)
CHECKOUT_TIMEOUT_SECONDS = 8 * 3600

# Max retries before giving up
MAX_RETRIES = 20

# Max processed set size
MAX_PROCESSED_SIZE = 500


# ================================================================
# LOGGING SETUP (mirrors watcher_main.py [5])
# ================================================================

def setup_logger(env):
    """Configure rotating file logger + console output"""
    if not os.path.exists(LOG_DIR):
        try:
            os.makedirs(LOG_DIR)
        except Exception:
            pass
    
    log_path = os.path.join(LOG_DIR, "checkout_watcher_" + env + ".log")
    
    logger = logging.getLogger("bento_checkout_watcher_" + env)
    logger.setLevel(logging.DEBUG)
    
    if logger.handlers:
        return logger  # Already configured
    
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    # File handler
    try:
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:
        pass
    
    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    
    logger.info("Log file: " + log_path)
    return logger


# ================================================================
# XML PARSING
# ================================================================

def parse_xml_env(xml_name: str) -> Optional[str]:
    """
    Extract ENV from XML filename.
    Format: checkout_<JIRA>_<ENV>_<TIMESTAMP>.xml
    Example: checkout_TSESSD-123_ABIT_20260316_152800.xml
    """
    try:
        parts = os.path.splitext(xml_name)[0].split("_")
        if len(parts) >= 3:
            return parts[2].upper()  # ENV is 3rd token
    except Exception:
        pass
    return None


def parse_duts_from_xml(xml_path: str, logger) -> List[Dict]:
    """
    Parse DUT list from XML MaterialInfo section.
    
    Returns list of dicts: [{"lot": "...", "mid": "...", "location": "..."}, ...]
    """
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        
        duts = []
        for attr in root.findall(".//MaterialInfo/Attribute"):
            duts.append({
                "lot": attr.get("Lot", ""),
                "mid": attr.get("MID", ""),
                "location": attr.get("DutLocation", ""),
            })
        
        return duts
    except Exception as e:
        logger.error(f"Could not parse XML: {e}")
        return []


# ================================================================
# SLATE HOT FOLDER LAUNCH
# ================================================================

def launch_slate_via_hot_folder(xml_path: str, logger) -> bool:
    """
    Copy XML to SLATE hot folder.
    SSD Tester Engineering GUI watches this folder and auto-loads profiles.
    """
    import shutil
    
    try:
        os.makedirs(SLATE_HOT_FOLDER, exist_ok=True)
        
        dest = os.path.join(SLATE_HOT_FOLDER, os.path.basename(xml_path))
        shutil.copy2(xml_path, dest)
        
        logger.info(f"[✓] XML dropped to SLATE hot folder: {dest}")
        return True
    except Exception as e:
        logger.error(f"Failed to copy XML to hot folder: {e}")
        return False


# ================================================================
# SLATE COMPLETION MONITOR (3 parallel methods)
# ================================================================

class SlateCompletionMonitor:
    """
    Monitors SLATE test completion using 3 parallel methods.
    First method to detect completion wins.
    
    Mirrors heartbeat pattern from watcher_builder.py [1].
    """
    
    def __init__(self, xml_path: str, dut_count: int, logger):
        self.xml_path = xml_path
        self.dut_count = dut_count
        self.logger = logger
        self.is_complete = False
        self.is_error = False
        self.completion_method = None
        self.start_time = time.time()
        self.lock = threading.Lock()
    
    def _mark_complete(self, method: str, is_error: bool = False):
        """Thread-safe completion marker"""
        with self.lock:
            if not self.is_complete:
                self.is_complete = True
                self.is_error = is_error
                self.completion_method = method
                self.logger.info(f"[✓] Completion detected via {method}")
    
    def wait_for_completion(self) -> Tuple[bool, str]:
        """
        Start all 3 monitoring methods + timeout watchdog.
        Returns (success: bool, method: str)
        """
        threads = [
            threading.Thread(target=self._monitor_log, daemon=True, name="log-monitor"),
            threading.Thread(target=self._monitor_output_folder, daemon=True, name="folder-monitor"),
            threading.Thread(target=self._monitor_process_cpu, daemon=True, name="cpu-monitor"),
            threading.Thread(target=self._timeout_watchdog, daemon=True, name="timeout-watchdog"),
        ]
        
        for t in threads:
            t.start()
        
        # Heartbeat while waiting (mirrors watcher_builder.py [1])
        while not self.is_complete:
            elapsed = int(time.time() - self.start_time)
            self.logger.info(f"[Heartbeat] Checkout running... {elapsed}s")
            time.sleep(120)  # Every 2 minutes
        
        return (not self.is_error, self.completion_method)
    
    def _monitor_log(self):
        """METHOD 1: Watch SLATE log file for completion keywords"""
        if not os.path.exists(SLATE_LOG_PATH):
            self.logger.warning(f"SLATE log not found: {SLATE_LOG_PATH}")
            return
        
        # Get initial file size to start reading from end
        try:
            last_pos = os.path.getsize(SLATE_LOG_PATH)
        except Exception:
            last_pos = 0
        
        while not self.is_complete:
            try:
                with open(SLATE_LOG_PATH, "r") as f:
                    f.seek(last_pos)
                    new_lines = f.readlines()
                    last_pos = f.tell()
                
                for line in new_lines:
                    line_lower = line.lower()
                    
                    # Success keywords
                    if "test complete" in line_lower or "job complete" in line_lower:
                        self._mark_complete("LOG_KEYWORD")
                        return
                    
                    # Error keywords
                    if "fatal error" in line_lower or "aborted" in line_lower:
                        self._mark_complete("LOG_ERROR", is_error=True)
                        return
            
            except Exception as e:
                self.logger.debug(f"Log monitor error: {e}")
            
            time.sleep(10)  # Check every 10 seconds
    
    def _monitor_output_folder(self):
        """METHOD 2: Watch output folder for result files"""
        if not os.path.exists(SLATE_OUTPUT_FOLDER):
            self.logger.warning(f"SLATE output folder not found: {SLATE_OUTPUT_FOLDER}")
            return
        
        expected_files = self.dut_count  # Expect one result file per DUT
        stable_count = 0
        last_file_count = 0
        
        while not self.is_complete:
            try:
                # Count result files (.csv, .log, .bin)
                files = [f for f in os.listdir(SLATE_OUTPUT_FOLDER)
                        if f.endswith((".csv", ".log", ".bin"))]
                file_count = len(files)
                
                # Check if we have expected number of files
                if file_count >= expected_files:
                    # Check if files are stable (not being written)
                    if file_count == last_file_count:
                        stable_count += 1
                        if stable_count >= 3:  # 3 consecutive checks = stable
                            self._mark_complete("OUTPUT_FOLDER")
                            return
                    else:
                        stable_count = 0
                        last_file_count = file_count
                else:
                    stable_count = 0
                    last_file_count = file_count
            
            except Exception as e:
                self.logger.debug(f"Folder monitor error: {e}")
            
            time.sleep(30)  # Check every 30 seconds
    
    def _monitor_process_cpu(self):
        """METHOD 3: Watch SLATE process CPU usage"""
        try:
            import psutil
        except ImportError:
            self.logger.warning("psutil not installed - CPU monitor disabled")
            return
        
        slate_process_name = "slate.exe"  # Adjust to actual SLATE process name
        low_cpu_count = 0
        
        while not self.is_complete:
            try:
                # Find SLATE process
                slate_proc = None
                for proc in psutil.process_iter(['name', 'cpu_percent']):
                    if proc.info['name'].lower() == slate_process_name.lower():
                        slate_proc = proc
                        break
                
                if slate_proc:
                    cpu = slate_proc.cpu_percent(interval=5)
                    
                    # Active test → CPU HIGH
                    # Test complete → CPU drops to <5% for 2 min
                    if cpu < 5.0:
                        low_cpu_count += 1
                        if low_cpu_count >= 24:  # 24 × 5s = 2 minutes
                            self._mark_complete("CPU_IDLE")
                            return
                    else:
                        low_cpu_count = 0
                else:
                    self.logger.debug("SLATE process not found")
            
            except Exception as e:
                self.logger.debug(f"CPU monitor error: {e}")
            
            time.sleep(5)  # Check every 5 seconds
    
    def _timeout_watchdog(self):
        """SAFETY: Timeout watchdog"""
        deadline = self.start_time + CHECKOUT_TIMEOUT_SECONDS
        
        while not self.is_complete and time.time() < deadline:
            time.sleep(60)
        
        if not self.is_complete:
            self._mark_complete("TIMEOUT", is_error=True)


# ================================================================
# MEMORY COLLECTION
# ================================================================

def trigger_memory_collection(xml_path: str, logger) -> bool:
    """
    Auto-trigger memory collection for ALL DUTs in parallel.
    
    Engineer used to run this manually for each DUT:
        memory_collect.exe --dut-location 0,0 --mid TUN00PNHW
        memory_collect.exe --dut-location 0,1 --mid TUN00PNJS
        ...repeat 8 times...
    
    Now done automatically for all DUTs at once.
    """
    duts = parse_duts_from_xml(xml_path, logger)
    if not duts:
        logger.error("No DUTs found in XML")
        return False
    
    logger.info(f"Starting memory collection for {len(duts)} DUTs...")
    
    def collect_one_dut(dut: Dict) -> Tuple[str, bool]:
        """Collect memory for one DUT"""
        mid = dut["mid"]
        loc = dut["location"]
        
        try:
            cmd = [
                MEMORY_COLLECT_EXE,
                "--dut-location", loc,
                "--mid", mid,
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,  # 5 min timeout per DUT
            )
            
            if result.returncode == 0:
                logger.info(f"  [✓] Memory collected: {mid} @ {loc}")
                return (mid, True)
            else:
                logger.error(f"  [✗] Memory collection failed: {mid} @ {loc}")
                logger.error(f"      {result.stderr}")
                return (mid, False)
        
        except Exception as e:
            logger.error(f"  [✗] Memory collection error: {mid} @ {loc}: {e}")
            return (mid, False)
    
    # Run ALL DUTs in parallel
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(collect_one_dut, dut): dut for dut in duts}
        results = []
        for future in as_completed(futures):
            results.append(future.result())
    
    # Check results
    success_count = sum(1 for _, ok in results if ok)
    logger.info(f"Memory collection complete: {success_count}/{len(duts)} successful")
    
    return success_count == len(duts)


# ================================================================
# STATUS FILE MANAGEMENT (mirrors watcher_lock.py [4])
# ================================================================

def write_checkout_status(xml_path: str, status: str, detail: str = ""):
    """Write .checkout_status sidecar file"""
    status_path = xml_path + ".checkout_status"
    payload = {
        "xml_file": os.path.basename(xml_path),
        "status": status,
        "detail": detail,
        "timestamp": datetime.now().isoformat(),
    }
    try:
        with open(status_path, "w") as f:
            json.dump(payload, f, indent=2)
    except Exception:
        pass


# ================================================================
# PROCESS A SINGLE XML
# ================================================================

def process_checkout_xml(xml_path: str, env: str, logger) -> Optional[bool]:
    """
    Full pipeline for one incoming XML:
        1. Acquire per-XML lock
        2. Copy XML to SLATE hot folder
        3. Monitor SLATE completion (3 methods)
        4. Trigger memory collection for all DUTs
        5. Write success/failure status
    
    Returns:
        True  - success (permanent, do not reprocess)
        False - permanent failure (do not retry)
        None  - temporary failure (retry next poll)
    """
    xml_name = os.path.basename(xml_path)
    
    logger.info("=" * 60)
    logger.info("Processing : " + xml_name)
    logger.info("ENV        : " + env)
    logger.info("=" * 60)
    
    write_checkout_status(xml_path, "in_progress", f"Tester {env} picked up {xml_name}")
    
    # STEP 1: Copy XML to SLATE hot folder
    if not launch_slate_via_hot_folder(xml_path, logger):
        write_checkout_status(xml_path, "failed", "Failed to copy XML to SLATE hot folder")
        return False
    
    # STEP 2: Parse DUT count for monitoring
    duts = parse_duts_from_xml(xml_path, logger)
    if not duts:
        write_checkout_status(xml_path, "failed", "No DUTs found in XML")
        return False
    
    logger.info(f"DUT count  : {len(duts)}")
    
    # STEP 3: Monitor SLATE completion
    logger.info("Starting SLATE completion monitor...")
    monitor = SlateCompletionMonitor(xml_path, len(duts), logger)
    success, method = monitor.wait_for_completion()
    
    if not success:
        write_checkout_status(xml_path, "failed", f"Checkout failed or timed out (method: {method})")
        return False
    
    logger.info(f"[✓] Checkout complete via {method}")
    
    # STEP 4: Memory collection
    logger.info("Starting memory collection...")
    mem_ok = trigger_memory_collection(xml_path, logger)
    
    # STEP 5: Write final status
    if mem_ok:
        write_checkout_status(
            xml_path, "success",
            f"Checkout complete via {method}. MemCollect: OK"
        )
        logger.info("[OK] Complete: " + xml_name)
        return True
    else:
        write_checkout_status(
            xml_path, "success",
            f"Checkout complete via {method}. MemCollect: PARTIAL"
        )
        logger.warning("[WARN] Complete but memory collection had errors: " + xml_name)
        return True  # Still mark as success since checkout itself passed


# ================================================================
# STARTUP CLEANUP (mirrors watcher_main.py [5])
# ================================================================

def cleanup_stale_status_on_startup(logger, checkout_queue_folder):
    """Reset orphaned .checkout_status files stuck at 'in_progress'"""
    if not os.path.isdir(checkout_queue_folder):
        return
    
    reset_count = 0
    logger.info("Startup: scanning for orphaned in_progress status files...")
    
    try:
        for fname in os.listdir(checkout_queue_folder):
            if not fname.endswith(".checkout_status"):
                continue
            
            status_path = os.path.join(checkout_queue_folder, fname)
            try:
                age = time.time() - os.path.getmtime(status_path)
                if age <= LOCK_MAX_AGE_SECONDS:
                    continue
                
                with open(status_path, "r") as sf:
                    data = json.load(sf)
                
                if data.get("status") != "in_progress":
                    continue
                
                # Reset to failed
                data["status"] = "failed"
                data["detail"] = (
                    f"Reset by watcher startup cleanup: "
                    f"previous watcher was killed mid-checkout "
                    f"(status age={int(age)}s, threshold={LOCK_MAX_AGE_SECONDS}s)"
                )
                data["reset_timestamp"] = datetime.now().isoformat()
                
                with open(status_path, "w") as sf:
                    json.dump(data, sf, indent=2)
                
                logger.warning(f"Startup: reset stale in_progress -> failed: {fname} (age={int(age)}s)")
                reset_count += 1
            
            except Exception as e:
                logger.warning(f"Startup: could not process status file {fname}: {e}")
    
    except OSError as e:
        logger.warning(f"Startup: could not scan for status files: {e}")
    
    if reset_count > 0:
        logger.warning(f"Startup: reset {reset_count} orphaned in_progress status file(s).")
    else:
        logger.info("Startup: no orphaned status files found.")


def _prune_processed(processed, logger):
    """Trim processed set to prevent unbounded growth"""
    if len(processed) > MAX_PROCESSED_SIZE:
        before = len(processed)
        entries = list(processed)
        processed.clear()
        processed.update(entries[-MAX_PROCESSED_SIZE:])
        logger.info(f"Pruned processed set: {before} -> {len(processed)} entries")
    return processed


# ================================================================
# MAIN WATCH LOOP (mirrors watcher_main.py [5])
# ================================================================

def watch(env: str, logger):
    """
    Main poll loop. Watches CHECKOUT_QUEUE for new XML files.
    Mirrors watch() from watcher_main.py [5] EXACTLY.
    """
    # Startup cleanup
    cleanup_stale_status_on_startup(logger, CHECKOUT_QUEUE_FOLDER)
    
    logger.info(f"Watching {CHECKOUT_QUEUE_FOLDER} every {POLL_INTERVAL_SECONDS}s...")
    
    # State
    processed = set()
    retry_counts = {}
    
    # Record XMLs already present at startup — skip them
    pre_existing = set()
    if os.path.isdir(CHECKOUT_QUEUE_FOLDER):
        for fname in os.listdir(CHECKOUT_QUEUE_FOLDER):
            if fname.lower().endswith(".xml") and ".checkout_" not in fname:
                pre_existing.add(os.path.join(CHECKOUT_QUEUE_FOLDER, fname))
        if pre_existing:
            logger.info(f"Startup: ignoring {len(pre_existing)} pre-existing XML(s)")
    
    heartbeat_count = 0
    HEARTBEAT_EVERY = 60  # Log every 60 polls ≈ 1 hour
    
    # Main poll loop
    while True:
        try:
            # Guard: shared drive may become unreachable
            if not os.path.isdir(CHECKOUT_QUEUE_FOLDER):
                logger.warning(f"CHECKOUT_QUEUE not reachable: {CHECKOUT_QUEUE_FOLDER}")
                time.sleep(POLL_INTERVAL_SECONDS)
                continue
            
            # Hourly heartbeat + processed set pruning
            heartbeat_count += 1
            if heartbeat_count >= HEARTBEAT_EVERY:
                heartbeat_count = 0
                logger.info(
                    f"Still watching {CHECKOUT_QUEUE_FOLDER} | "
                    f"processed={len(processed)} | retrying={len(retry_counts)}"
                )
                processed = _prune_processed(processed, logger)
            
            # Scan for new XMLs
            for fname in sorted(os.listdir(CHECKOUT_QUEUE_FOLDER)):
                if not fname.lower().endswith(".xml"):
                    continue
                if ".checkout_" in fname:
                    continue  # Skip lock/status sidecars
                
                xml_path = os.path.join(CHECKOUT_QUEUE_FOLDER, fname)
                
                # Skip if already finished
                if xml_path in processed:
                    continue
                
                # Skip pre-existing XMLs
                if xml_path in pre_existing:
                    continue
                
                # Skip XMLs not for this ENV
                xml_env = parse_xml_env(fname)
                if xml_env != env:
                    logger.debug(f"Not for this ENV, skipping: {fname}")
                    continue
                
                # Per-XML lock
                lock = LockFile(xml_path)
                if not lock.acquire():
                    logger.debug(f"Locked by another process, skipping: {fname}")
                    continue
                
                try:
                    result = process_checkout_xml(xml_path, env, logger)
                    
                    if result is True:
                        # Success
                        processed.add(xml_path)
                        retry_counts.pop(xml_path, None)
                        logger.info("[OK] " + fname)
                    
                    elif result is None:
                        # Temporary fail — retry
                        retry_counts[xml_path] = retry_counts.get(xml_path, 0) + 1
                        count = retry_counts[xml_path]
                        
                        if count >= MAX_RETRIES:
                            logger.error(
                                f"[GIVE UP] {fname} — exceeded MAX_RETRIES={MAX_RETRIES}"
                            )
                            write_checkout_status(
                                xml_path, "failed",
                                f"Watcher gave up after {MAX_RETRIES} retry attempts"
                            )
                            processed.add(xml_path)
                            retry_counts.pop(xml_path, None)
                        else:
                            logger.info(f"[RETRY {count}/{MAX_RETRIES}] {fname}")
                    
                    else:
                        # Permanent fail
                        processed.add(xml_path)
                        retry_counts.pop(xml_path, None)
                        logger.error("[FAIL] " + fname)
                
                finally:
                    lock.release()
        
        except Exception as e:
            import traceback
            logger.error(f"Poll loop error: {e}\n{traceback.format_exc()}")
        
        time.sleep(POLL_INTERVAL_SECONDS)


# ================================================================
# ENTRY POINT
# ================================================================

def main():
    parser = argparse.ArgumentParser(
        description="BENTO Checkout Watcher — monitors CHECKOUT_QUEUE for XML profiles"
    )
    parser.add_argument(
        "--env",
        required=True,
        choices=list(TESTER_REGISTRY.keys()),
        help="Tester environment (e.g., ABIT, SFN2, CNFG)"
    )
    args = parser.parse_args()
    env = args.env.upper()
    logger = setup_logger(env)
    
    logger.info("=" * 60)
    logger.info("BENTO Checkout Watcher starting up")
    logger.info("ENV             : " + env)
    logger.info("CHECKOUT_QUEUE  : " + CHECKOUT_QUEUE_FOLDER)
    logger.info("SLATE_HOT_FOLDER: " + SLATE_HOT_FOLDER)
    logger.info("Poll            : every " + str(POLL_INTERVAL_SECONDS) + "s")
    logger.info("Max retries     : " + str(MAX_RETRIES))
    logger.info("=" * 60)
    
    try:
        watch(env, logger)
    except KeyboardInterrupt:
        logger.info("Watcher stopped by user (KeyboardInterrupt).")
        sys.exit(0)
    except Exception as e:
        import traceback
        logger.error(f"Fatal error: {e}\n{traceback.format_exc()}")
        sys.exit(1)


if __name__ == "__main__":
    main()
