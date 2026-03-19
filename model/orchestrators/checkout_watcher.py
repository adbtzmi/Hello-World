# -*- coding: utf-8 -*-
"""
model/orchestrators/checkout_watcher.py
=========================================
BENTO Checkout Watcher — Tester Side (Phase 2)

Runs on the TESTER machine. Monitors the hot folder for incoming SLATE
XML files dropped by checkout_orchestrator.py, triggers SLATE execution,
waits for completion, then writes a .bento_status sidecar file so the
orchestrator can detect the result.

Mirrors watcher_main.py EXACTLY in structure:
  - Same poll loop pattern
  - Same .bento_status file protocol
  - Same LockFile / processed-set / retry logic
  - Same MAX_RETRIES / MAX_PROCESSED_SIZE guards
  - Same startup cleanup of stale in_progress status files [P8]
  - Same ZIP integrity check pattern (adapted to XML validation) [P6]
  - Same heartbeat / pruning approach [P2][P4]

Usage:
    python checkout_watcher.py --env ABIT
    python checkout_watcher.py --env SFN2
    python checkout_watcher.py --env CNFG

File structure (deploy alongside other watcher files on tester):
    bento_tester/
        checkout_watcher.py     <- this file
        watcher_config.py       <- shared paths and registry
        watcher_lock.py         <- lock + status file logic
"""
from __future__ import print_function
import os
import sys
import time
import json
import logging
import argparse
import subprocess
from datetime import datetime

# ── Shared infrastructure — same modules as watcher_main.py ──────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# When deployed on the tester, watcher_config.py lives in the same folder.
# Adjust path if deployed in a sub-directory.
try:
    from watcher_config import (
        TESTER_REGISTRY,
        POLL_INTERVAL_SECONDS,
        LOCK_MAX_AGE_SECONDS,
        STATUS_RETENTION_SECONDS,
        LOG_DIR,
        zip_belongs_to_tester,
    )
    from watcher_lock import (
        LockFile,
        write_status,
        cleanup_stale_locks_on_startup,
    )
except ImportError:
    # Fallback for running from model/orchestrators/ during development
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "watcher"))
    from watcher_config import (
        TESTER_REGISTRY,
        POLL_INTERVAL_SECONDS,
        LOCK_MAX_AGE_SECONDS,
        STATUS_RETENTION_SECONDS,
        LOG_DIR,
        zip_belongs_to_tester,
    )
    from watcher_lock import (
        LockFile,
        write_status,
        cleanup_stale_locks_on_startup,
    )


# ================================================================
# CONSTANTS  (mirrors watcher_main.py exactly)
# ================================================================

MAX_RETRIES        = 20    # [P3] max retry attempts per XML before giving up
MAX_PROCESSED_SIZE = 500   # [P4] cap processed set to prevent memory growth

# Hot folder — where checkout_orchestrator drops SLATE XML files
HOT_DROP_FOLDER = r"P:\temp\BENTO\HOT_DROP"
STATUS_FOLDER   = HOT_DROP_FOLDER   # status sidecars live in the same folder


# ================================================================
# [P6] XML INTEGRITY CHECK  (adapted from _is_zip_valid)
# ================================================================

def _is_xml_valid(xml_path, logger):
    """
    [P6] Validate the SLATE XML before attempting to process it.

    Catches:
      - Partially copied / still-being-written files
      - Malformed XML (parse error)
      - Empty files

    Returns:
        True  - XML is well-formed and safe to process
        False - XML is invalid (permanent fail, no retry)
    """
    xml_name = os.path.basename(xml_path)
    try:
        import xml.etree.ElementTree as ET
        tree = ET.parse(xml_path)
        root = tree.getroot()
        if root is None:
            logger.error("XML integrity FAILED: empty root in " + xml_name)
            return False
        logger.info("XML integrity OK: " + xml_name)
        return True
    except ET.ParseError as e:
        logger.error("XML integrity FAILED: parse error in " + xml_name + ": " + str(e))
        return False
    except Exception as e:
        logger.error("XML integrity check error for " + xml_name + ": " + str(e))
        return False


# ================================================================
# [P4] PROCESSED SET PRUNING  (identical to watcher_main.py)
# ================================================================

def _prune_processed(processed, logger):
    """
    [P4] Trim the in-memory processed set to prevent unbounded growth.
    Called once per HEARTBEAT_EVERY polls.
    """
    if len(processed) > MAX_PROCESSED_SIZE:
        before  = len(processed)
        entries = list(processed)
        processed.clear()
        processed.update(entries[-MAX_PROCESSED_SIZE:])
        logger.info(
            "Pruned processed set: "
            + str(before) + " -> " + str(len(processed)) + " entries"
        )
    return processed


# ================================================================
# [P8] STARTUP CLEANUP  (mirrors watcher_main.py exactly)
# ================================================================

def cleanup_stale_status_on_startup(logger, hot_folder):
    """
    [P8] Reset orphaned .bento_status files stuck at 'in_progress'.

    If the watcher is killed mid-checkout, the status file is left at
    'in_progress' forever. This resets them to 'failed' on restart.
    """
    if not os.path.isdir(hot_folder):
        return

    now = time.time()
    for fname in os.listdir(hot_folder):
        if not fname.endswith(".bento_status"):
            continue
        fpath = os.path.join(hot_folder, fname)
        try:
            with open(fpath) as f:
                data = json.load(f)
            if data.get("status") != "in_progress":
                continue
            age = now - os.path.getmtime(fpath)
            if age < LOCK_MAX_AGE_SECONDS:
                continue
            # Stale in_progress — reset to failed
            data["status"] = "failed"
            data["detail"] = (
                "Watcher was killed mid-checkout. "
                "Status file was in_progress for " + str(int(age)) + "s "
                "(threshold=" + str(LOCK_MAX_AGE_SECONDS) + "s). "
                "Reset at " + datetime.now().isoformat()
            )
            with open(fpath, "w") as f:
                json.dump(data, f)
            logger.warning(
                "[P8] Reset stale in_progress status: " + fname
                + " (age=" + str(int(age)) + "s)"
            )
        except Exception as e:
            logger.warning("Could not reset stale status " + fname + ": " + str(e))


def cleanup_old_status_files(logger, hot_folder):
    """Remove completed .bento_status files older than STATUS_RETENTION_SECONDS."""
    if not os.path.isdir(hot_folder):
        return
    now = time.time()
    for fname in os.listdir(hot_folder):
        if not fname.endswith(".bento_status"):
            continue
        fpath = os.path.join(hot_folder, fname)
        try:
            age = now - os.path.getmtime(fpath)
            if age > STATUS_RETENTION_SECONDS:
                os.remove(fpath)
                logger.info("Removed old status file: " + fname)
        except Exception as e:
            logger.warning("Could not remove old status file " + fname + ": " + str(e))


# ================================================================
# SLATE EXECUTION
# ================================================================

def run_slate(xml_path, env, hostname, logger):
    """
    Trigger SLATE execution on the tester with the given XML configuration.

    This is the Phase 2 equivalent of run_build() in watcher_builder.py.
    Adapt the command to match your tester's SLATE launcher CLI.

    Returns (success: bool, pre_run_time: float)
    """
    pre_run_time = time.time()
    xml_name     = os.path.basename(xml_path)

    cfg = TESTER_REGISTRY.get(env, {})
    # SLATE launcher path — adjust to your deployment
    slate_exe = cfg.get("slate_exe", r"C:\SLATE\SLATE.exe")

    logger.info("Starting SLATE: " + xml_name)
    logger.info("SLATE exe : " + slate_exe)
    logger.info("XML file  : " + xml_path)

    build_log = os.path.join(
        LOG_DIR, "checkout_" + os.path.splitext(xml_name)[0] + ".log"
    )

    if not os.path.exists(LOG_DIR):
        try:
            os.makedirs(LOG_DIR)
        except Exception:
            pass

    try:
        cmd = [slate_exe, "--config", xml_path, "--log", build_log]

        with open(build_log, "w") as log_f:
            log_f.write("BENTO Checkout Log\n")
            log_f.write("Started   : " + datetime.now().isoformat() + "\n")
            log_f.write("XML       : " + xml_path + "\n")
            log_f.write("Command   : " + " ".join(cmd) + "\n")
            log_f.write("=" * 60 + "\n\n")
            log_f.flush()

            # ── NOTE: adjust timeout to match your longest expected SLATE run
            from watcher_config import BUILD_TIMEOUT_SECONDS
            proc = subprocess.Popen(cmd, stdout=log_f, stderr=log_f)

            try:
                rc = proc.wait(timeout=BUILD_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                logger.error(
                    "SLATE TIMEOUT after " + str(BUILD_TIMEOUT_SECONDS) + "s"
                    + " | Terminating PID=" + str(proc.pid)
                )
                proc.terminate()
                time.sleep(5)
                if proc.poll() is None:
                    proc.kill()
                log_f.write("\nTIMEOUT: SLATE exceeded " + str(BUILD_TIMEOUT_SECONDS) + "s\n")
                log_f.flush()
                return False, pre_run_time

            log_f.write("\nFinished  : " + datetime.now().isoformat() + "\n")
            log_f.write("Exit code : " + str(rc) + "\n")
            log_f.write("Result    : " + ("SUCCESS" if rc == 0 else "FAILED") + "\n")
            log_f.write("=" * 60 + "\n")

        logger.info("SLATE exit code: " + str(rc) + " | log: " + build_log)

        if rc != 0:
            logger.error("SLATE FAILED rc=" + str(rc))
            return False, pre_run_time

        logger.info("SLATE SUCCESS")
        return True, pre_run_time

    except Exception as e:
        logger.error("SLATE subprocess error: " + str(e))
        return False, pre_run_time


# ================================================================
# PROCESS ONE XML FILE
# (mirrors process_zip() in watcher_main.py exactly)
# ================================================================

def process_xml(xml_path, env, logger):
    """
    Full checkout pipeline for one XML file:
      1. Validate XML integrity [P6]
      2. Write in_progress status
      3. Run SLATE
      4. Write success / failed status

    Returns:
        True  — success (add to processed)
        None  — temporary fail (retry next poll)
        False — permanent fail (add to processed)

    Mirrors process_zip() return semantics exactly.
    """
    xml_name = os.path.basename(xml_path)
    logger.info("Processing: " + xml_name)

    # ── [P6] XML integrity check ──────────────────────────────────────────
    if not _is_xml_valid(xml_path, logger):
        write_status(xml_path, "failed", "XML integrity check failed — malformed or partial file")
        return False   # Permanent fail — no retry

    # ── Write in_progress status ──────────────────────────────────────────
    write_status(xml_path, "in_progress", "Checkout started by watcher")

    # ── Run SLATE ─────────────────────────────────────────────────────────
    success, _ = run_slate(xml_path, env, xml_name, logger)

    if success:
        detail = f"SLATE completed successfully for {xml_name}"
        write_status(xml_path, "success", detail)
        logger.info("[OK] " + xml_name)
        return True
    else:
        detail = f"SLATE failed or timed out for {xml_name}"
        write_status(xml_path, "failed", detail)
        logger.error("[FAIL] " + xml_name)
        return False


# ================================================================
# LOGGER SETUP  (identical to watcher_main.py)
# ================================================================

def setup_logger(env):
    if not os.path.exists(LOG_DIR):
        try:
            os.makedirs(LOG_DIR)
        except Exception:
            pass

    log_file = os.path.join(LOG_DIR, "checkout_watcher_" + env + ".log")
    logger   = logging.getLogger("checkout_watcher_" + env)
    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        fmt = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        logger.addHandler(sh)

        from logging.handlers import RotatingFileHandler
        fh = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=5)
        fh.setFormatter(fmt)
        logger.addHandler(fh)

    return logger


# ================================================================
# MAIN WATCH LOOP  (mirrors watch() in watcher_main.py exactly)
# ================================================================

def watch(env, logger):
    """
    Main poll loop. Monitors HOT_DROP_FOLDER for incoming SLATE XML files.
    Mirrors the watch() function in watcher_main.py line-for-line.
    """

    # ── Startup cleanup ───────────────────────────────────────────────────
    cleanup_stale_locks_on_startup(logger, HOT_DROP_FOLDER, {})
    cleanup_stale_status_on_startup(logger, HOT_DROP_FOLDER)    # [P8]
    cleanup_old_status_files(logger, HOT_DROP_FOLDER)

    logger.info(
        "Watching " + HOT_DROP_FOLDER
        + " every " + str(POLL_INTERVAL_SECONDS) + "s ..."
    )

    # ── State ─────────────────────────────────────────────────────────────
    processed    = set()
    retry_counts = {}   # [P3] xml_path -> int

    # Record XML files already present at startup — skip them
    pre_existing = set()
    if os.path.isdir(HOT_DROP_FOLDER):
        for fname in os.listdir(HOT_DROP_FOLDER):
            if fname.lower().endswith(".xml") and ".bento_" not in fname:
                pre_existing.add(os.path.join(HOT_DROP_FOLDER, fname))
        if pre_existing:
            logger.info(
                "Startup: ignoring " + str(len(pre_existing))
                + " pre-existing XML(s) in HOT_DROP folder."
            )

    cfg             = TESTER_REGISTRY.get(env, {})
    hostname        = cfg.get("hostname", "UNKNOWN")
    heartbeat_count = 0
    HEARTBEAT_EVERY = 60   # log "still watching" every ~1 hour

    # ── Main poll loop ────────────────────────────────────────────────────
    while True:
        try:
            if not os.path.isdir(HOT_DROP_FOLDER):
                logger.warning(
                    "HOT_DROP folder not reachable: " + HOT_DROP_FOLDER
                    + " — will retry in " + str(POLL_INTERVAL_SECONDS) + "s"
                )
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            # Hourly heartbeat + [P4] processed set pruning
            heartbeat_count += 1
            if heartbeat_count >= HEARTBEAT_EVERY:
                heartbeat_count = 0
                logger.info(
                    "Still watching " + HOT_DROP_FOLDER
                    + " | processed=" + str(len(processed))
                    + " | retrying=" + str(len(retry_counts))
                )
                processed = _prune_processed(processed, logger)   # [P4]

            # ── Scan for new XML files ─────────────────────────────────────
            for fname in sorted(os.listdir(HOT_DROP_FOLDER)):
                if not fname.lower().endswith(".xml"):
                    continue
                if ".bento_" in fname:
                    continue   # Skip status sidecars

                xml_path = os.path.join(HOT_DROP_FOLDER, fname)

                if xml_path in processed:
                    continue
                if xml_path in pre_existing:
                    continue   # Silently skip files present at startup

                # Filter: only process XML files addressed to this tester/env
                # XML naming convention: SLATE_<JIRA>_<HOSTNAME>_<ENV>_<TIMESTAMP>.xml
                # Quick check: must contain hostname AND env in filename
                fname_upper = fname.upper()
                if hostname.upper() not in fname_upper:
                    logger.debug("Not for this tester, skipping: " + fname)
                    continue
                if ("_" + env + "_") not in fname_upper and fname_upper.endswith("_" + env + ".XML"):
                    logger.debug("ENV mismatch, skipping: " + fname)
                    continue

                # Per-XML lock — prevents two watcher instances claiming same file
                lock = LockFile(xml_path)
                if not lock.acquire():
                    logger.debug("Locked by another process, skipping: " + fname)
                    continue

                try:
                    result = process_xml(xml_path, env, logger)

                    if result is True:
                        # ── Success ────────────────────────────────────────
                        processed.add(xml_path)
                        retry_counts.pop(xml_path, None)
                        logger.info("[OK] " + fname)

                    elif result is None:
                        # ── Temporary fail — retry ─────────────────────────
                        retry_counts[xml_path] = retry_counts.get(xml_path, 0) + 1
                        count = retry_counts[xml_path]

                        if count >= MAX_RETRIES:
                            logger.error(
                                "[GIVE UP] " + fname
                                + " — exceeded MAX_RETRIES=" + str(MAX_RETRIES)
                            )
                            write_status(
                                xml_path, "failed",
                                "Watcher gave up after " + str(MAX_RETRIES) + " retry attempts"
                            )
                            processed.add(xml_path)
                            retry_counts.pop(xml_path, None)
                        else:
                            logger.info(
                                "[RETRY " + str(count) + "/" + str(MAX_RETRIES) + "] "
                                + fname + " — will retry next poll"
                            )

                    else:
                        # ── Permanent fail ─────────────────────────────────
                        processed.add(xml_path)
                        retry_counts.pop(xml_path, None)
                        logger.error("[FAIL] " + fname)

                finally:
                    lock.release()

        except Exception as e:
            import traceback
            logger.error(
                "Poll loop error: " + str(e)
                + "\n" + traceback.format_exc()
            )

        time.sleep(POLL_INTERVAL_SECONDS)


# ================================================================
# ENTRY POINT  (mirrors watcher_main.py main() exactly)
# ================================================================

def main():
    parser = argparse.ArgumentParser(
        description="BENTO Checkout Watcher — monitors HOT_DROP for incoming SLATE XML files"
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
    logger.info("ENV        : " + env)
    logger.info("Hostname   : " + cfg.get("hostname", "UNKNOWN"))
    logger.info("SLATE exe  : " + cfg.get("slate_exe", "C:\\SLATE\\SLATE.exe"))
    logger.info("HOT_DROP   : " + HOT_DROP_FOLDER)
    logger.info("Poll       : every " + str(POLL_INTERVAL_SECONDS) + "s")
    logger.info("Max retries: " + str(MAX_RETRIES))
    logger.info("Max proc.  : " + str(MAX_PROCESSED_SIZE) + " entries")
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
