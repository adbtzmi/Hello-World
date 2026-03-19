# -*- coding: utf-8 -*-
"""
checkout_watcher.py
====================
BENTO Checkout Watcher — Tester Side (Phase 2)

Runs on the TESTER machine. Monitors CHECKOUT_QUEUE for incoming SLATE
XML files dropped by checkout_orchestrator.py [39], then:
  1. Copies XML → C:\\test_program\\playground_queue  (AUTO-CREATED) ← KEY FIX
  2. SLATE picks up XML → AutoStart=True → test begins [24]
  3. SlateCompletionMonitor detects completion (3 parallel methods)
  4. Memory collection for all DUTs
  5. Writes .checkout_status = "success" → orchestrator wakes up [39]

KEY FIX [42]:
  launch_slate_via_hot_folder() now calls os.makedirs(SLATE_HOT_FOLDER,
  exist_ok=True) BEFORE copying the XML, so the folder is auto-created
  if it doesn't exist on the tester machine.

Mirrors watcher_main.py [10] exactly in structure.

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
import concurrent.futures
import xml.etree.ElementTree as ET
from datetime import datetime

# ── Import shared watcher modules (deployed alongside on tester) ──────────────
try:
    from watcher_config import (
        TESTER_REGISTRY,
        POLL_INTERVAL_SECONDS,
        LOCK_MAX_AGE_SECONDS,
        LOG_DIR,
    )
    from watcher_lock import (
        LockFile,
        write_status,
        cleanup_stale_locks_on_startup,
    )
except ImportError:
    # Fallback defaults when running standalone for testing
    TESTER_REGISTRY       = {"ABIT": {"hostname": "IBIR-0383",
                                       "slate_exe": r"C:\SLATE\SLATE.exe"}}
    POLL_INTERVAL_SECONDS = 30
    LOCK_MAX_AGE_SECONDS  = 1800
    LOG_DIR               = r"C:\BENTO\logs"

    class LockFile:
        def __init__(self, path): self.path = path + ".checkout_lock"
        def __enter__(self):
            if os.path.exists(self.path): return False
            open(self.path, 'w').close()
            return True
        def __exit__(self, *a):
            try: os.remove(self.path)
            except: pass

    def write_status(path, status, detail=""):
        sp = path + ".checkout_status"
        with open(sp, "w") as f:
            json.dump({"status": status, "detail": detail,
                       "timestamp": datetime.now().isoformat()}, f)

    def cleanup_stale_locks_on_startup(logger, folder, repo_dirs): pass


# ── CONFIRMED PATHS ───────────────────────────────────────────────────────────
# P: drive — shared folder where orchestrator drops XMLs [39]
CHECKOUT_QUEUE_FOLDER   = r"P:\temp\BENTO\CHECKOUT_QUEUE"
CHECKOUT_RESULTS_FOLDER = r"P:\temp\BENTO\CHECKOUT_RESULTS"

# ── SLATE hot folder on THIS TESTER machine ───────────────────────────────────
# ✅ KEY FIX: auto-created by launch_slate_via_hot_folder() if missing
SLATE_HOT_FOLDER        = r"C:\test_program\playground_queue"

# ── SLATE completion detection paths (local to tester) ───────────────────────
SLATE_LOG_PATH          = r"C:\test_program\logs\slate_system.log"
SLATE_RESULTS_FOLDER    = r"C:\test_program\results"

# ── Watcher behaviour ─────────────────────────────────────────────────────────
MAX_RETRIES         = 20
MAX_PROCESSED_SIZE  = 500
HEARTBEAT_EVERY     = 30    # polls


# ── LOGGER ────────────────────────────────────────────────────────────────────
def setup_logger(env: str) -> logging.Logger:
    logger = logging.getLogger(f"bento_checkout_watcher_{env}")
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)

        # Console handler
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        logger.addHandler(sh)

        # File handler
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            log_file = os.path.join(
                LOG_DIR,
                f"checkout_watcher_{env}_{datetime.now().strftime('%Y%m%d')}.log"
            )
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setFormatter(logging.Formatter(
                "%(asctime)s | %(levelname)-8s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            ))
            logger.addHandler(fh)
        except Exception as e:
            logger.warning(f"Cannot create log file: {e}")

    return logger


# ── PROCESSED SET PRUNING [P4] ────────────────────────────────────────────────
def _prune_processed(processed: set, logger) -> set:
    """Keep processed set bounded to MAX_PROCESSED_SIZE. [P4]"""
    if len(processed) > MAX_PROCESSED_SIZE:
        keep = set(list(processed)[-MAX_PROCESSED_SIZE // 2:])
        removed = len(processed) - len(keep)
        logger.info(f"Pruned {removed} old entries from processed set.")
        return keep
    return processed


# ── XML VALIDATION ────────────────────────────────────────────────────────────
def _is_xml_valid(xml_path: str, logger) -> bool:
    """
    Validate XML is parseable and has required Profile elements.
    Mirrors _is_zip_valid() pattern from watcher_main.py [P6].
    """
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        # Must have TestJobArchive and AutoStart
        tja = root.find("TestJobArchive")
        auto = root.find("AutoStart")
        if tja is None:
            logger.warning(f"XML missing <TestJobArchive>: {xml_path}")
            return False
        if auto is None:
            logger.warning(f"XML missing <AutoStart>: {xml_path}")
            return False
        return True
    except ET.ParseError as e:
        logger.warning(f"XML parse error {xml_path}: {e}")
        return False
    except Exception as e:
        logger.warning(f"XML validation error {xml_path}: {e}")
        return False


# ── STEP 8 — Copy XML to SLATE hot folder ────────────────────────────────────
def launch_slate_via_hot_folder(xml_path: str, logger) -> bool:
    """
    Copy XML to SLATE hot folder.
    SSD Tester Engineering GUI watches this folder and auto-loads the profile.

    ✅ KEY FIX [42]:
      os.makedirs(SLATE_HOT_FOLDER, exist_ok=True) is called FIRST.
      This auto-creates C:\\test_program\\playground_queue if it doesn't exist.
      Previously this would crash if the folder wasn't present.
    """
    # ── AUTO-CREATE SLATE hot folder ──────────────────────────────────
    try:
        os.makedirs(SLATE_HOT_FOLDER, exist_ok=True)
        logger.info(f"[✓] SLATE hot folder ready: {SLATE_HOT_FOLDER}")
    except Exception as e:
        logger.error(
            f"[✗] Cannot create SLATE hot folder {SLATE_HOT_FOLDER}: {e}"
        )
        return False

    dest = os.path.join(SLATE_HOT_FOLDER, os.path.basename(xml_path))
    try:
        shutil.copy2(xml_path, dest)
        logger.info(f"[✓] XML → SLATE hot folder: {dest}")
        return True
    except Exception as e:
        logger.error(f"[✗] Hot folder copy failed: {e}")
        return False


# ── SLATE COMPLETION MONITOR ──────────────────────────────────────────────────
class SlateCompletionMonitor:
    """
    3 parallel detection methods + timeout watchdog.
    Uses threading.Event for thread-safe, race-condition-free completion.
    Mirrors heartbeat pattern from watcher_builder.py [6].
    """

    def __init__(self, xml_path: str, logger, timeout_hours: int = 8):
        self.xml_path          = xml_path
        self.logger            = logger
        self.timeout_seconds   = timeout_hours * 3600
        self.start_time        = time.time()
        self._complete_event   = threading.Event()
        self._error_event      = threading.Event()
        self.completion_method = "unknown"
        self._method_lock      = threading.Lock()

    def _signal_complete(self, method_name: str, is_error: bool = False):
        """Thread-safe, idempotent completion signal."""
        with self._method_lock:
            if not self._complete_event.is_set():
                self.completion_method = method_name
                if is_error:
                    self._error_event.set()
                self._complete_event.set()
                self.logger.info(
                    f"[SlateMonitor] Completion via: {method_name}"
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
                        with open(SLATE_LOG_PATH, 'r', errors='ignore') as lf:
                            lf.seek(last_size)
                            new_text = lf.read()
                        last_size = size

                        for w in error_words:
                            if w in new_text:
                                self._signal_complete("LOG_KEYWORD",
                                                      is_error=True)
                                return

                        for w in success_words:
                            if w in new_text:
                                self._signal_complete("LOG_KEYWORD")
                                return
            except Exception as e:
                self.logger.warning(f"[Monitor1] Log error: {e}")
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
                        if f.endswith(('.csv', '.log', '.bin'))
                    ]
                    if files:
                        sizes_now = {
                            f: os.path.getsize(
                                os.path.join(SLATE_RESULTS_FOLDER, f))
                            for f in files
                        }
                        time.sleep(5)
                        sizes_later = {
                            f: os.path.getsize(
                                os.path.join(SLATE_RESULTS_FOLDER, f))
                            for f in files
                            if os.path.exists(
                                os.path.join(SLATE_RESULTS_FOLDER, f))
                        }
                        if sizes_now == sizes_later:
                            stable_count += 1
                            if stable_count >= required_stable:
                                self._signal_complete("OUTPUT_FILES")
                                return
                        else:
                            stable_count = 0
            except Exception as e:
                self.logger.warning(f"[Monitor2] Folder error: {e}")
            time.sleep(15)

    def _monitor_process_cpu(self):
        """
        METHOD 3: CPU drop detection (secondary confirmation only).
        Not used as standalone trigger — too unreliable during I/O.
        """
        try:
            import psutil
        except ImportError:
            self.logger.warning(
                "[Monitor3] psutil not installed — CPU monitor disabled."
            )
            return

        low_cpu_count = 0
        required_low  = 12
        slate_proc    = None

        while not self._complete_event.is_set():
            try:
                if slate_proc is None:
                    for proc in psutil.process_iter(['name']):
                        if 'SSD_Tester' in proc.info.get('name', ''):
                            slate_proc = proc
                            break

                if slate_proc and slate_proc.is_running():
                    cpu = slate_proc.cpu_percent(interval=10)
                    if cpu < 5.0:
                        low_cpu_count += 1
                        # Only trigger if output files also confirm done
                        if (low_cpu_count >= required_low
                                and self._complete_event.is_set()):
                            self._signal_complete("CPU_DROP")
                            return
                    else:
                        low_cpu_count = 0
                else:
                    slate_proc = None
            except Exception as e:
                self.logger.warning(f"[Monitor3] CPU error: {e}")
            time.sleep(10)

    def _timeout_watchdog(self):
        """SAFETY: Hard timeout watchdog."""
        deadline = self.start_time + self.timeout_seconds
        while not self._complete_event.is_set():
            if time.time() > deadline:
                self.logger.error(
                    f"[TIMEOUT] Checkout exceeded "
                    f"{self.timeout_seconds // 3600}h limit!"
                )
                self._signal_complete("TIMEOUT", is_error=True)
                return
            time.sleep(60)

    def wait_for_completion(self) -> bool:
        """
        Launch all 3 detection methods + watchdog as daemon threads.
        Blocks with heartbeat until any method signals completion.
        Returns True on success, False on error/timeout. [6]
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
                target=self._monitor_process_cpu,
                daemon=True,
                name="checkout-monitor-cpu"
            ),
            threading.Thread(
                target=self._timeout_watchdog,
                daemon=True,
                name="checkout-timeout-watchdog"
            ),
        ]

        for t in threads:
            t.start()

        # Heartbeat loop — every 2 minutes while waiting [6]
        while not self._complete_event.wait(timeout=120):
            elapsed = int(time.time() - self.start_time)
            self.logger.info(
                f"[Heartbeat] Checkout running... "
                f"{elapsed // 3600}h {(elapsed % 3600) // 60}m "
                f"(method={self.completion_method})"
            )

        return not self._error_event.is_set()


# ── MEMORY COLLECTION ─────────────────────────────────────────────────────────
def collect_one_dut(dut_elem, output_dir: str, logger) -> bool:
    """
    Memory collection for one DUT.
    Replace with your actual memory_collect.exe call. [24]
    """
    mid      = dut_elem.get("MID", "unknown")
    lot      = dut_elem.get("Lot", "")
    dut_loc  = dut_elem.get("DutLocation", "")

    try:
        dut_dir = os.path.join(output_dir, mid)
        os.makedirs(dut_dir, exist_ok=True)

        # ── INSERT REAL MEMORY COLLECTION CALL HERE ────────────────────
        # Example:
        # import subprocess
        # cmd = [r"C:\test_program\tools\memory_collect.exe",
        #        "--dut-location", dut_loc,
        #        "--mid", mid,
        #        "--output", dut_dir]
        # proc = subprocess.run(cmd, capture_output=True, timeout=300)
        # if proc.returncode != 0:
        #     raise RuntimeError(proc.stderr.decode())
        # ──────────────────────────────────────────────────────────────

        # Placeholder stub
        info_file = os.path.join(dut_dir, "memory_info.txt")
        with open(info_file, 'w') as f:
            f.write(f"MID        : {mid}\n")
            f.write(f"Lot        : {lot}\n")
            f.write(f"DUT Loc    : {dut_loc}\n")
            f.write(f"Timestamp  : {datetime.now().isoformat()}\n")
            f.write(f"Status     : PLACEHOLDER — wire to real tool\n")

        logger.info(f"  ✓ Memory collected: {mid} @ {dut_loc}")
        return True

    except Exception as e:
        logger.error(f"  ✗ Memory collection failed for {mid}: {e}")
        return False


def trigger_memory_collection(xml_path: str, jira_key: str,
                               logger) -> dict:
    """
    Auto-trigger memory collection for ALL DUTs in parallel. [24]
    Results saved to P:\\temp\\BENTO\\CHECKOUT_RESULTS\\JIRA_KEY\\
    """
    try:
        tree = ET.parse(xml_path)
        duts = tree.getroot().findall(".//MaterialInfo/Attribute")
    except Exception as e:
        logger.error(f"Cannot parse DUT list from XML: {e}")
        return {"status": "failed", "detail": str(e), "collected": 0}

    output_dir = os.path.join(CHECKOUT_RESULTS_FOLDER, jira_key)
    try:
        os.makedirs(output_dir, exist_ok=True)
    except Exception as e:
        logger.warning(f"Cannot create results folder: {e}")

    collected = 0
    failed    = 0

    # ✅ Run ALL DUTs in parallel — log failures, don't swallow [24]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(collect_one_dut, dut, output_dir, logger): dut
            for dut in duts
        }
        for future, dut in futures.items():
            try:
                ok = future.result()
                if ok:
                    collected += 1
                else:
                    failed += 1
            except Exception as e:
                mid = dut.get("MID", "unknown")
                logger.error(
                    f"Memory collection exception for {mid}: {e}"
                )
                failed += 1

    status = "success" if failed == 0 else (
        "partial" if collected > 0 else "failed"
    )
    detail = f"Collected {collected}/{len(duts)} DUTs"
    if failed:
        detail += f" ({failed} failed)"
    logger.info(f"Memory collection done: {detail}")
    return {"status": status, "detail": detail, "collected": collected}


# ── PROCESS ONE XML ───────────────────────────────────────────────────────────
def process_checkout_xml(xml_path: str, env: str, logger):
    """
    Full pipeline for one checkout XML.
    Mirrors process_zip() in watcher_main.py [10].

    Steps:
      1. Validate XML integrity
      2. Write in_progress status
      3. Copy XML → SLATE hot folder (AUTO-CREATED) ← KEY FIX
      4. Wait for SLATE completion (3 parallel methods)
      5. Memory collection for all DUTs
      6. Write success/failed status → orchestrator wakes up [39]
    """
    fname    = os.path.basename(xml_path)
    jira_key = _parse_jira_from_xml_name(fname)

    logger.info(f"[Process] Starting: {fname}")

    # ── Step 1: Validate XML ──────────────────────────────────────────
    if not _is_xml_valid(xml_path, logger):
        write_status(xml_path, "failed", "Invalid XML — cannot parse")
        logger.error(f"[✗] Invalid XML: {fname}")
        return

    # ── Step 2: Write in_progress ─────────────────────────────────────
    write_status(xml_path, "in_progress", "Checkout started by watcher")

    # ── Step 3: Copy XML → SLATE hot folder ───────────────────────────
    # ✅ KEY FIX: launch_slate_via_hot_folder() auto-creates the folder
    ok = launch_slate_via_hot_folder(xml_path, logger)
    if not ok:
        write_status(xml_path, "failed",
                     f"Cannot copy XML to {SLATE_HOT_FOLDER}")
        return

    # ── Step 4: Wait for SLATE completion ────────────────────────────
    logger.info(f"[~] Waiting for SLATE completion...")
    monitor = SlateCompletionMonitor(xml_path, logger, timeout_hours=8)
    success = monitor.wait_for_completion()

    if not success:
        write_status(
            xml_path, "failed",
            f"SLATE did not complete successfully "
            f"(method={monitor.completion_method})"
        )
        logger.error(f"[✗] SLATE failed or timed out: {fname}")
        return

    logger.info(
        f"[✓] SLATE complete via {monitor.completion_method}: {fname}"
    )

    # ── Step 5: Memory collection ─────────────────────────────────────
    logger.info("[~] Starting memory collection for all DUTs...")
    mem_result = trigger_memory_collection(xml_path, jira_key, logger)

    # ── Step 6: Write success status ──────────────────────────────────
    # Orchestrator poll loop detects "success" and wakes up [39]
    write_status(
        xml_path, "success",
        f"Checkout complete via {monitor.completion_method}. "
        f"MemCollect: {mem_result['status']} "
        f"({mem_result['detail']})"
    )
    logger.info(f"[✓] Status written: success — {fname}")


def _parse_jira_from_xml_name(fname: str) -> str:
    """Extract JIRA key from XML filename. e.g. checkout_TSESSD-123_... → TSESSD-123"""
    parts = fname.replace(".xml", "").split("_")
    for p in parts:
        if "-" in p and any(c.isdigit() for c in p):
            return p
    return "UNKNOWN"


# ── MAIN WATCH LOOP ───────────────────────────────────────────────────────────
def watch(env: str, logger):
    """
    Main poll loop. Mirrors watch() in watcher_main.py [10].

    Polls CHECKOUT_QUEUE every 30s for new XMLs.
    Uses file-based locks to prevent duplicate processing. [9]
    Uses pre_existing set to ignore XMLs present at startup. [10]
    """
    processed    = set()
    retry_counts = {}
    pre_existing = set()
    poll_count   = 0

    # ── Startup: ignore pre-existing XMLs ────────────────────────────
    if os.path.isdir(CHECKOUT_QUEUE_FOLDER):
        for fname in os.listdir(CHECKOUT_QUEUE_FOLDER):
            if fname.lower().endswith(".xml"):
                pre_existing.add(
                    os.path.join(CHECKOUT_QUEUE_FOLDER, fname)
                )
        logger.info(
            f"Startup: ignoring {len(pre_existing)} pre-existing XML(s)."
        )

    # ── Startup: cleanup stale locks/status files [P8] ────────────────
    try:
        cleanup_stale_locks_on_startup(logger, CHECKOUT_QUEUE_FOLDER, {})
    except Exception as e:
        logger.warning(f"Startup cleanup error: {e}")

    logger.info(f"Polling {CHECKOUT_QUEUE_FOLDER} every "
                f"{POLL_INTERVAL_SECONDS}s...")

    while True:
        poll_count += 1

        # ── Heartbeat + processed set pruning [P4] ────────────────────
        if poll_count % HEARTBEAT_EVERY == 0:
            logger.info(
                f"[Heartbeat] Watcher alive. "
                f"processed={len(processed)} retry_counts={len(retry_counts)}"
            )
            processed = _prune_processed(processed, logger)

        try:
            if not os.path.isdir(CHECKOUT_QUEUE_FOLDER):
                logger.warning(
                    f"CHECKOUT_QUEUE not accessible: {CHECKOUT_QUEUE_FOLDER}"
                )
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            for fname in os.listdir(CHECKOUT_QUEUE_FOLDER):
                if not fname.lower().endswith(".xml"):
                    continue
                if ".checkout_" in fname:
                    continue    # Skip status/lock sidecars

                xml_path = os.path.join(CHECKOUT_QUEUE_FOLDER, fname)

                # Skip pre-existing files from before watcher started [10]
                if xml_path in pre_existing:
                    continue

                # Skip already processed
                if xml_path in processed:
                    continue

                # Filter by ENV — only pick up XMLs for THIS tester
                # e.g. ABIT watcher only picks up checkout_*_ABIT_*.xml [13]
                env_tag = f"_{env}_"
                if env_tag not in fname.upper():
                    continue

                # Check retry limit [P3]
                retries = retry_counts.get(xml_path, 0)
                if retries >= MAX_RETRIES:
                    logger.error(
                        f"[SKIP] Max retries ({MAX_RETRIES}) reached: {fname}"
                    )
                    write_status(
                        xml_path, "failed",
                        f"Max retries ({MAX_RETRIES}) exceeded."
                    )
                    processed.add(xml_path)
                    continue

                # Acquire per-XML lock [9]
                lock = LockFile(xml_path)
                try:
                    acquired = lock.__enter__()
                except Exception:
                    acquired = False

                if not acquired:
                    retry_counts[xml_path] = retries + 1
                    continue

                logger.info(f"[+] Processing: {fname} (retry={retries})")

                try:
                    process_checkout_xml(xml_path, env, logger)
                    processed.add(xml_path)
                    retry_counts.pop(xml_path, None)
                except Exception as exc:
                    logger.error(f"[!] process_checkout_xml failed: {exc}")
                    retry_counts[xml_path] = retries + 1
                    try:
                        write_status(xml_path, "failed",
                                     f"Watcher exception: {exc}")
                    except Exception:
                        pass
                finally:
                    try:
                        lock.__exit__(None, None, None)
                    except Exception:
                        pass

        except Exception as loop_err:
            logger.error(f"[!] Watch loop error: {loop_err}")

        time.sleep(POLL_INTERVAL_SECONDS)


# ── ENTRY POINT ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description=(
            "BENTO Checkout Watcher — "
            "monitors CHECKOUT_QUEUE for incoming SLATE XML files"
        )
    )
    parser.add_argument(
        "--env",
        required=True,
        choices=list(TESTER_REGISTRY.keys()),
        help="Tester environment (e.g. ABIT, SFN2, CNFG)"
    )
    args   = parser.parse_args()
    env    = args.env.upper()
    logger = setup_logger(env)

    cfg = TESTER_REGISTRY.get(env, {})

    logger.info("=" * 60)
    logger.info("BENTO Checkout Watcher starting up")
    logger.info("ENV          : " + env)
    logger.info("Hostname     : " + (
        cfg.get("hostname", "UNKNOWN") if isinstance(cfg, dict)
        else cfg[0] if isinstance(cfg, list) else "UNKNOWN"
    ))
    logger.info("CHECKOUT_QUEUE: " + CHECKOUT_QUEUE_FOLDER)
    logger.info("HOT_FOLDER   : " + SLATE_HOT_FOLDER +
                " (auto-created if missing)")
    logger.info("Poll interval: " + str(POLL_INTERVAL_SECONDS) + "s")
    logger.info("Max retries  : " + str(MAX_RETRIES))
    logger.info("=" * 60)

    try:
        watch(env, logger)
    except KeyboardInterrupt:
        logger.info("Checkout watcher stopped by user (KeyboardInterrupt).")
        sys.exit(0)
    except Exception as e:
        import traceback
        logger.error(
            "Fatal watcher error: " + str(e)
            + "\n" + traceback.format_exc()
        )
        sys.exit(1)


if __name__ == "__main__":
    main()