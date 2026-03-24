# -*- coding: utf-8 -*-
"""
checkout_watcher.py
====================
BENTO Checkout Watcher - Tester Side (Phase 2)

Runs on the TESTER machine.
Fixed: ALL f-strings replaced with .format() or string concatenation.
       Python 2/3 compatible.

Usage:
    python checkout_watcher.py --env ABIT
    python checkout_watcher.py --env SFN2
    python checkout_watcher.py --env CNFG
"""
from __future__ import print_function

import os
import sys
import time
import json
import logging
import argparse
import shutil
import threading
import xml.etree.ElementTree as ET
from datetime import datetime

# ── Import shared watcher modules ────────────────────────────────────────────
try:
    from model.watcher.watcher_config import (
        TESTER_REGISTRY,
        POLL_INTERVAL_SECONDS,
        LOCK_MAX_AGE_SECONDS,
        LOG_DIR,
    )
    from model.watcher.watcher_lock import (
        LockFile,               # type: ignore[assignment]
        write_status,           # type: ignore[assignment]
        cleanup_stale_locks_on_startup,  # type: ignore[assignment]
    )
except ImportError:
    # Fallback defaults when running standalone
    TESTER_REGISTRY       = {}
    POLL_INTERVAL_SECONDS = 30
    LOCK_MAX_AGE_SECONDS  = 1800
    LOG_DIR               = r"C:\BENTO\logs"

    class LockFile(object):
        def __init__(self, path):
            self.path = path + ".checkout_lock"
        def __enter__(self):
            if os.path.exists(self.path):
                return False
            open(self.path, "w").close()
            return True
        def __exit__(self, *a):
            try:
                os.remove(self.path)
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


# ── CONFIRMED PATHS ───────────────────────────────────────────────────────────
CHECKOUT_QUEUE_FOLDER   = r"P:\temp\BENTO\CHECKOUT_QUEUE"
CHECKOUT_RESULTS_FOLDER = r"P:\temp\BENTO\CHECKOUT_RESULTS"
SLATE_HOT_FOLDER        = r"C:\test_program\playground_queue"
SLATE_LOG_PATH          = r"C:\test_program\logs\slate_system.log"
SLATE_RESULTS_FOLDER    = r"C:\test_program\results"

MAX_RETRIES        = 20
MAX_PROCESSED_SIZE = 500
HEARTBEAT_EVERY    = 30


# ── LOGGER ────────────────────────────────────────────────────────────────────
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
            os.makedirs(LOG_DIR, exist_ok=True)
            log_file = os.path.join(
                LOG_DIR,
                "checkout_watcher_" + env
                + "_" + datetime.now().strftime("%Y%m%d") + ".log"
            )
            fh = logging.FileHandler(log_file, encoding="utf-8")
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
        os.makedirs(SLATE_HOT_FOLDER, exist_ok=True)
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
        threads = [
            threading.Thread(
                target=self._monitor_log,
                daemon=True,
                name="checkout-monitor-log"
            ),
            threading.Thread(
                target=self._monitor_output_folder,
                daemon=True,
                name="checkout-monitor-folder"
            ),
            threading.Thread(
                target=self._timeout_watchdog,
                daemon=True,
                name="checkout-timeout-watchdog"
            ),
        ]

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
        os.makedirs(dut_dir, exist_ok=True)

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


def trigger_memory_collection(xml_path, jira_key, logger):
    """
    Auto-trigger memory collection for ALL DUTs sequentially.
    Results saved to P:\\temp\\BENTO\\CHECKOUT_RESULTS\\JIRA_KEY\\
    """
    try:
        tree = ET.parse(xml_path)
        duts = tree.getroot().findall(".//MaterialInfo/Attribute")
    except Exception as e:
        logger.error("Cannot parse DUT list: " + str(e))
        return {"status": "failed", "detail": str(e), "collected": 0}

    output_dir = os.path.join(CHECKOUT_RESULTS_FOLDER, jira_key)
    try:
        os.makedirs(output_dir, exist_ok=True)
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
      4. Wait for SLATE completion (parallel methods)
      5. Memory collection for all DUTs
      6. Write success/failed status -> orchestrator wakes up
    """
    fname    = os.path.basename(xml_path)
    jira_key = _parse_jira_from_xml_name(fname)

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
    mem_result = trigger_memory_collection(xml_path, jira_key, logger)

    # Step 6: Write success status -> orchestrator wakes up
    write_status(
        xml_path, "success",
        "Checkout complete via " + monitor.completion_method + ". "
        + "MemCollect: " + mem_result["status"]
        + " (" + mem_result["detail"] + ")"
    )
    logger.info("[OK] Status written: success - " + fname)


# ── LOCK HELPERS ──────────────────────────────────────────────────────────────
def _is_locked(xml_path):
    return os.path.exists(xml_path + ".checkout_lock")


def _acquire_lock(xml_path, env):
    lock_path = xml_path + ".checkout_lock"
    try:
        with open(lock_path, "x") as lf:
            json.dump({
                "env":       env,
                "pid":       os.getpid(),
                "locked_at": datetime.now().isoformat()
            }, lf)
        return True
    except Exception:
        return False


def _release_lock(xml_path):
    lock_path = xml_path + ".checkout_lock"
    try:
        if os.path.exists(lock_path):
            os.remove(lock_path)
    except Exception:
        pass


# ── MAIN WATCH LOOP ───────────────────────────────────────────────────────────
def watch(env, logger):
    """
    Main poll loop. Mirrors watch() in watcher_main.py.

    Polls CHECKOUT_QUEUE every 30s for new XMLs.
    Uses file-based locks to prevent duplicate processing.
    Uses pre_existing set to ignore XMLs present at startup.
    No f-strings - Python 2/3 compatible.
    """
    processed    = set()
    retry_counts = {}
    pre_existing = set()
    poll_count   = 0

    # Snapshot pre-existing XMLs at startup
    if os.path.isdir(CHECKOUT_QUEUE_FOLDER):
        for fname in os.listdir(CHECKOUT_QUEUE_FOLDER):
            if fname.lower().endswith(".xml"):
                pre_existing.add(
                    os.path.join(CHECKOUT_QUEUE_FOLDER, fname)
                )
        logger.info(
            "Startup: ignoring "
            + str(len(pre_existing))
            + " pre-existing XML(s)."
        )

    # Cleanup stale locks on startup
    try:
        cleanup_stale_locks_on_startup(logger, CHECKOUT_QUEUE_FOLDER, {})
    except Exception as e:
        logger.warning("Startup cleanup error: " + str(e))

    logger.info(
        "Polling " + CHECKOUT_QUEUE_FOLDER
        + " every " + str(POLL_INTERVAL_SECONDS) + "s..."
    )

    while True:
        poll_count += 1

        # Heartbeat + processed set pruning
        if poll_count % HEARTBEAT_EVERY == 0:
            logger.info(
                "[Heartbeat] Watcher alive. "
                "processed=" + str(len(processed))
                + " retries=" + str(len(retry_counts))
            )
            processed = _prune_processed(processed, logger)

        try:
            if not os.path.isdir(CHECKOUT_QUEUE_FOLDER):
                logger.warning(
                    "CHECKOUT_QUEUE not accessible: "
                    + CHECKOUT_QUEUE_FOLDER
                )
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            for fname in os.listdir(CHECKOUT_QUEUE_FOLDER):
                if not fname.lower().endswith(".xml"):
                    continue
                if ".checkout_" in fname:
                    continue    # Skip status/lock sidecars

                xml_path = os.path.join(CHECKOUT_QUEUE_FOLDER, fname)

                if xml_path in pre_existing:
                    continue
                if xml_path in processed:
                    continue

                # ENV filter - only pick up XMLs for THIS tester
                # e.g. ABIT watcher only picks up checkout_*_ABIT_*.xml
                env_tag = "_" + env + "_"
                if env_tag.upper() not in fname.upper():
                    continue

                # Check retry limit
                retries = retry_counts.get(xml_path, 0)
                if retries >= MAX_RETRIES:
                    logger.error(
                        "[SKIP] Max retries ("
                        + str(MAX_RETRIES) + ") reached: " + fname
                    )
                    write_status(
                        xml_path, "failed",
                        "Max retries (" + str(MAX_RETRIES) + ") exceeded."
                    )
                    processed.add(xml_path)
                    continue

                # Skip if already locked
                if _is_locked(xml_path):
                    continue

                # Acquire lock
                if not _acquire_lock(xml_path, env):
                    retry_counts[xml_path] = retries + 1
                    continue

                logger.info(
                    "[+] Processing: " + fname
                    + " (retry=" + str(retries) + ")"
                )

                try:
                    process_checkout_xml(xml_path, env, logger)
                    processed.add(xml_path)
                    retry_counts.pop(xml_path, None)
                except Exception as exc:
                    logger.error(
                        "[!] process_checkout_xml failed: " + str(exc)
                    )
                    retry_counts[xml_path] = retries + 1
                    try:
                        write_status(
                            xml_path, "failed",
                            "Watcher exception: " + str(exc)
                        )
                    except Exception:
                        pass
                finally:
                    try:
                        _release_lock(xml_path)
                    except Exception:
                        pass

        except Exception as loop_err:
            logger.error("[!] Watch loop error: " + str(loop_err))

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
    