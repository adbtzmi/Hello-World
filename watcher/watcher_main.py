# -*- coding: utf-8 -*-
"""
watcher_main.py
===============
BENTO Tester Watcher - Entry Point

Runs on the tester machine. Monitors the shared RAW_ZIP folder for
incoming TP packages, compiles them, and places the result TGZ in
the shared RELEASE_TGZ folder.

Usage:
    python watcher_main.py --env ABIT
    python watcher_main.py --env SFN2
    python watcher_main.py --env CNFG

File structure (deploy all files together):
    bento_tester/
        watcher_main.py     <- this file (entry point)
        watcher_config.py   <- all paths and settings
        watcher_lock.py     <- lock + status file logic
        watcher_builder.py  <- cmd.exe build + cleanup
        watcher_copier.py   <- binary copy to shared drive
"""
from __future__ import print_function
import os
import sys
import time
import logging
import argparse
import zipfile
from datetime import datetime

from watcher_config import (
    RAW_ZIP_FOLDER,
    RELEASE_TGZ_FOLDER,
    TESTER_REGISTRY,
    POLL_INTERVAL_SECONDS,
    LOG_DIR,
    parse_jira_key_from_zip,
    zip_belongs_to_tester,
)
from watcher_lock import (
    LockFile,
    RepoBuildLock,
    write_status,
    cleanup_stale_locks_on_startup,
)
from watcher_builder import (
    extract_zip,
    run_build,
    cleanup_memory,
    find_tgz,
)
from watcher_copier import copy_tgz_to_release


# ================================================================
# LOGGER SETUP
# ================================================================
def setup_logger(env):
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR)

    ts = datetime.now().strftime("%d%b%y_%H%M")
    log_path = os.path.join(LOG_DIR, "watcher_" + env + "_" + ts + ".log")

    logger = logging.getLogger("bento_watcher_" + env)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    fh = logging.FileHandler(log_path)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    cfg = TESTER_REGISTRY.get(env, {})
    logger.info("=" * 60)
    logger.info("BENTO Tester Watcher  |  Environment: " + env)
    logger.info("Hostname   : " + cfg.get("hostname", "UNKNOWN"))
    logger.info("RAW_ZIP    : " + RAW_ZIP_FOLDER)
    logger.info("RELEASE    : " + RELEASE_TGZ_FOLDER)
    logger.info("Repo dir   : " + cfg.get("repo_dir", "NOT SET"))
    logger.info("Build cmd  : " + cfg.get("build_cmd", "NOT SET"))
    logger.info("Log file   : " + log_path)
    logger.info("=" * 60)
    return logger


# ================================================================
# WAIT FOR FILE TO FINISH COPYING
# ================================================================
def wait_until_stable(zip_path, logger, checks=3, interval=5):
    """
    Poll file size until it stops changing.
    Prevents extracting a ZIP that is still being written to the shared drive.
    """
    logger.info("Waiting for file to finish copying to shared drive ...")
    last_size = -1
    stable_count = 0
    attempts = 0
    max_attempts = 60   # 5 minutes max wait

    while stable_count < checks and attempts < max_attempts:
        try:
            size = os.path.getsize(zip_path)
        except Exception:
            size = -1

        if size == last_size and size > 0:
            stable_count += 1
            # Only log final confirmation, not every stable check
        else:
            stable_count = 0
            if size != last_size:
                logger.info("  Copying: " + str(size // (1024*1024)) + " MB ...")

        last_size = size
        attempts += 1
        if stable_count < checks:
            time.sleep(interval)

    if stable_count >= checks:
        logger.info("File stable at " + str(last_size) + " bytes. Proceeding.")
        return True
    else:
        logger.error("File never stabilised. Aborting.")
        return False


# ================================================================
# PROCESS ONE ZIP
# ================================================================
def _tail_log(log_path, n=8):
    """Return the last n non-empty lines of a log file as a string."""
    try:
        with open(log_path, "r") as f:
            lines = [l.rstrip() for l in f.readlines() if l.strip()]
        return "\n".join(lines[-n:])
    except Exception:
        return "(log not readable)"


def process_zip(zip_path, env, logger):
    """
    Full pipeline for one incoming ZIP:
      0. Wait for file to finish copying
      1. Acquire repo build lock
      2. Extract ZIP into repo
      3. Run make release via cmd.exe
      4. Post-build memory cleanup
      5. Find produced .tgz
      6. Binary copy to RELEASE_TGZ/HOSTNAME>_<JIRA>\

    Returns:
      True  - success
      False - permanent failure (do not retry)
      None  - temporary failure (retry next poll)
    """
    zip_name = os.path.basename(zip_path)
    cfg      = TESTER_REGISTRY[env]
    hostname = cfg["hostname"]
    repo_dir = cfg["repo_dir"]
    build_cmd = cfg["build_cmd"]
    jira_key = parse_jira_key_from_zip(zip_name)

    logger.info("=" * 60)
    logger.info("Processing : " + zip_name)
    logger.info("JIRA Key   : " + jira_key + "  [matched via hostname+env strict filter]")
    logger.info("Hostname   : " + hostname)
    logger.info("=" * 60)

    write_status(zip_path, "in_progress",
                 hostname + " picked up " + zip_name)

    # -- 0. Wait for file to stabilise --
    if not wait_until_stable(zip_path, logger):
        write_status(zip_path, "failed", "File did not stabilise on shared drive")
        return False

    # Sanity check the ZIP before anything else
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            bad = zf.testzip()
            if bad:
                logger.error("Corrupt ZIP entry: " + bad)
                write_status(zip_path, "failed", "Corrupt ZIP: " + bad)
                return False
    except zipfile.BadZipFile as e:
        logger.error("Not a valid ZIP: " + str(e))
        write_status(zip_path, "failed", "Invalid ZIP file: " + str(e))
        return False

    # -- 1. Acquire repo lock --
    repo_lock = RepoBuildLock(repo_dir)
    if not repo_lock.acquire(logger=logger):
        msg = "Repo already building. Will retry next poll."
        logger.warning(msg)
        write_status(zip_path, "pending", msg)
        return None   # retry

    try:
        # -- 2. Extract ZIP into repo --
        if not extract_zip(zip_path, repo_dir, logger):
            write_status(zip_path, "failed", "ZIP extraction failed")
            return False

        # -- 3. Run build via cmd.exe --
        success, pre_build_time = run_build(repo_dir, build_cmd, zip_name, logger)
        if not success:
            build_log = os.path.join(
                LOG_DIR,
                "build_" + os.path.splitext(zip_name)[0] + ".log"
            )
            # Try to surface the last meaningful error line from build log
            err_snippet = _tail_log(build_log, 8)
            write_status(zip_path, "failed",
                "make release failed (rc=1). Last build output:\n" + err_snippet)
            return False

        # -- 4. Post-build memory cleanup --
        #    CRITICAL: frees RAM before copy runs to prevent OOM kill of copy step
        cleanup_memory(logger)

        # -- 5. Find produced .tgz --
        tgz_path = find_tgz(repo_dir, pre_build_time, logger)
        if tgz_path is None:
            write_status(zip_path, "failed", "No .tgz produced after make release")
            return False

        # -- 6. Binary copy to shared release folder --
        copy_ok, dest_path = copy_tgz_to_release(
            tgz_path, zip_name, hostname, jira_key, env, logger
        )
        if not copy_ok:
            write_status(zip_path, "failed", "Binary copy to RELEASE_TGZ failed")
            return False

        # -- Done --
        result_detail = hostname + "_" + jira_key + "_" + env + "/" + os.path.basename(dest_path if dest_path else tgz_path)
        write_status(zip_path, "success", result_detail)
        logger.info("[OK] Complete: " + zip_name + " -> " + str(dest_path))
        return True

    finally:
        repo_lock.release()


# ================================================================
# MAIN POLL LOOP
# ================================================================
def watch(env, logger):
    repo_dirs = {
        e: cfg["repo_dir"]
        for e, cfg in TESTER_REGISTRY.items()
    }
    cleanup_stale_locks_on_startup(logger, RAW_ZIP_FOLDER, repo_dirs)

    logger.info(
        "Watching " + RAW_ZIP_FOLDER
        + " every " + str(POLL_INTERVAL_SECONDS) + "s ..."
    )
    processed = set()   # completed ZIPs (success or permanent fail)

    # Record ZIPs already in RAW_ZIP at startup so we skip them.
    # We only process ZIPs that ARRIVE after the watcher starts.
    pre_existing = set()
    if os.path.isdir(RAW_ZIP_FOLDER):
        for fname in os.listdir(RAW_ZIP_FOLDER):
            if fname.lower().endswith(".zip") and ".bento_" not in fname:
                pre_existing.add(os.path.join(RAW_ZIP_FOLDER, fname))
        if pre_existing:
            logger.info(
                "Startup: ignoring " + str(len(pre_existing))
                + " pre-existing ZIP(s) in RAW_ZIP folder."
            )

    heartbeat_count = 0
    HEARTBEAT_EVERY  = 60    # log "still watching" every 60 polls = 1 hour (at 60s/poll)

    while True:
        try:
            if not os.path.isdir(RAW_ZIP_FOLDER):
                logger.warning("RAW_ZIP folder not reachable: " + RAW_ZIP_FOLDER)
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            heartbeat_count += 1
            if heartbeat_count >= HEARTBEAT_EVERY:
                heartbeat_count = 0
                logger.info("Still watching " + RAW_ZIP_FOLDER + " ...")

            for fname in sorted(os.listdir(RAW_ZIP_FOLDER)):
                if not fname.lower().endswith(".zip"):
                    continue
                if ".bento_" in fname:
                    continue
                # Strict match: ZIP must encode BOTH this hostname AND this env.
                # Prevents IBIR-0383 from claiming a ZIP intended for IBIR-0999,
                # even when both testers share the same env token (e.g. ABIT).
                if not zip_belongs_to_tester(fname, cfg["hostname"], env):
                    continue

                zip_path = os.path.join(RAW_ZIP_FOLDER, fname)
                if zip_path in processed:
                    continue
                # Skip ZIPs that were already present when watcher started
                if zip_path in pre_existing:
                    continue   # silently skip - already announced at startup

                lock = LockFile(zip_path)
                if not lock.acquire():
                    logger.debug("Locked by another process, skipping: " + fname)
                    continue

                try:
                    result = process_zip(zip_path, env, logger)
                    if result is True:
                        processed.add(zip_path)
                        logger.info("[OK] " + fname)
                    elif result is None:
                        logger.info("[RETRY] " + fname + " - will retry next poll")
                    else:
                        processed.add(zip_path)
                        logger.error("[FAIL] " + fname)
                finally:
                    lock.release()

        except Exception as e:
            import traceback
            logger.error("Poll loop error: " + str(e) + "\n" + traceback.format_exc())

        time.sleep(POLL_INTERVAL_SECONDS)


# ================================================================
# ENTRY POINT
# ================================================================
def main():
    parser = argparse.ArgumentParser(
        description="BENTO Tester Watcher"
    )
    parser.add_argument(
        "--env",
        required=True,
        choices=list(TESTER_REGISTRY.keys()),
        help="Tester environment e.g. ABIT"
    )
    args = parser.parse_args()

    logger = setup_logger(args.env)
    try:
        watch(args.env, logger)
    except KeyboardInterrupt:
        logger.info("Watcher stopped by user.")
        sys.exit(0)


if __name__ == "__main__":
    main()
