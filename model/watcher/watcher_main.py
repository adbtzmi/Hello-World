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
        watcher_builder.py  <- Cygwin build + cleanup  [P1][P2 applied]
        watcher_copier.py   <- binary copy to shared drive

CHANGES (Recommendations Applied):
    [P3] Retry limit counter added.
         Previously a ZIP returning None (repo busy / temp fail) could
         retry INFINITELY across poll cycles with no upper bound.
         Now: retry_counts dict tracks per-ZIP retry attempts.
         After MAX_RETRIES (20 polls = ~20 min at 60s/poll), the ZIP is
         marked as 'failed' and added to processed — no more retries.
         Counter is cleared on success or permanent failure.

    [P4] processed set pruning added via _prune_processed().
         The processed set is in-memory and was never pruned — it would
         grow unbounded over weeks/months of watcher operation.
         Now: pruned to MAX_PROCESSED_SIZE (500) entries every time the
         hourly heartbeat fires (every HEARTBEAT_EVERY polls).
         Keeps the most recent entries (approximate, sets are unordered).

    [P6] ZIP integrity check added via _is_zip_valid().
         Before extraction, each ZIP is validated using zipfile.testzip()
         which performs a CRC check on every entry in the archive.
         A corrupt or half-copied ZIP is marked 'failed' immediately —
         no build attempt is made, preventing wasted 30-min build cycles.
         Returns False (permanent fail) so it is NOT retried.

    [P8] Orphaned 'in_progress' .bento_status files are now reset on startup.
         Previously cleanup_stale_locks_on_startup() only cleaned lock files.
         If the watcher was killed mid-build, the .bento_status file was left
         stuck at 'in_progress' forever — the orchestrator would wait forever.
         Now: startup scan also resets stale in_progress status files to
         'failed' with a descriptive detail message, so the orchestrator
         can detect and report the failure immediately.
"""
from __future__ import print_function
import os
import sys
import time
import json
import logging
import argparse
import zipfile
from datetime import datetime

from watcher_config import (
    RAW_ZIP_FOLDER,
    RELEASE_TGZ_FOLDER,
    TESTER_REGISTRY,
    POLL_INTERVAL_SECONDS,
    LOCK_MAX_AGE_SECONDS,
    STATUS_RETENTION_SECONDS,
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
# CONSTANTS
# ================================================================

MAX_RETRIES        = 20    # [P3] max retry attempts per ZIP before giving up
                           # 20 polls × 60s = ~20 min, within 30 min build timeout

MAX_PROCESSED_SIZE = 500   # [P4] cap processed set size to prevent memory growth
                           # over long watcher sessions (days/weeks of operation)


# ================================================================
# [P6] ZIP INTEGRITY CHECK
# ================================================================

def _is_zip_valid(zip_path, logger):
    """
    [P6] Validate the ZIP file before attempting extraction or build.

    Performs a full CRC check on every entry in the archive using
    zipfile.testzip(). This catches:
      - Partially copied files (still being written to shared drive)
      - Corrupted archives (bad sectors, network transfer errors)
      - Truncated ZIPs (copy was interrupted mid-transfer)

    WHY THIS MATTERS:
        Without this check, a corrupt ZIP causes extract_zip() to fail,
        but only AFTER acquiring the repo lock and logging misleading errors.
        With this check, corrupt ZIPs are caught early and marked as
        permanent failures (False) — no retry, no wasted build attempt.

    Returns:
        True  - ZIP is structurally valid and safe to extract
        False - ZIP is corrupt, truncated, or unreadable
    """
    zip_name = os.path.basename(zip_path)
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            bad_file = zf.testzip()
            if bad_file is not None:
                logger.error(
                    "ZIP integrity FAILED: first bad entry = '"
                    + bad_file + "' in " + zip_name
                )
                return False
        logger.info("ZIP integrity OK: " + zip_name)
        return True
    except zipfile.BadZipFile:
        logger.error(
            "ZIP integrity FAILED: not a valid ZIP archive: " + zip_name
        )
        return False
    except Exception as e:
        logger.error(
            "ZIP integrity check error for " + zip_name + ": " + str(e)
        )
        return False


# ================================================================
# [P4] PROCESSED SET PRUNING
# ================================================================

def _prune_processed(processed, logger):
    """
    [P4] Trim the in-memory processed set to prevent unbounded growth.

    WHY THIS EXISTS:
        The processed set accumulates every ZIP path ever handled in the
        current watcher session. On a busy tester running 24/7 for months,
        this could grow to thousands of entries — pure memory waste since
        those ZIPs are long gone from the filesystem.

    STRATEGY:
        Keep only the most recent MAX_PROCESSED_SIZE entries.
        Order is approximate (sets are unordered in Python) but this is
        fine — the purpose is deduplication, not strict ordering.
        Called once per HEARTBEAT_EVERY polls (approx. every hour).

    Args:
        processed : the set() of completed ZIP paths
        logger    : standard Python logger

    Returns:
        The (possibly trimmed) processed set.
    """
    if len(processed) > MAX_PROCESSED_SIZE:
        before  = len(processed)
        entries = list(processed)
        processed.clear()
        # Keep the last MAX_PROCESSED_SIZE entries (most recent by insertion order)
        processed.update(entries[-MAX_PROCESSED_SIZE:])
        logger.info(
            "Pruned processed set: "
            + str(before) + " -> " + str(len(processed)) + " entries"
        )
    return processed


# ================================================================
# [P8] EXTENDED STARTUP CLEANUP
# ================================================================

def cleanup_stale_status_on_startup(logger, raw_zip_folder):
    """
    [P8] Reset orphaned .bento_status files stuck at 'in_progress'.

    WHY THIS EXISTS:
        If the watcher is killed mid-build (CTRL+C, power loss, OOM kill),
        the .bento_status sidecar file is left stuck at 'in_progress'.
        The orchestrator (compilation_orchestrator.py) polls this file and
        will wait indefinitely — or until its own 35-minute timeout — even
        though no build is actually running.

        By resetting stale in_progress files to 'failed' on startup, the
        orchestrator immediately detects the failure and can report it to
        the user instead of hanging for 35 minutes.

    STALE THRESHOLD:
        Uses LOCK_MAX_AGE_SECONDS (1800s = 30 min) — same as build timeout.
        A status file older than 30 min that is still 'in_progress' means
        the build process that wrote it is certainly no longer running.

    Args:
        logger         : standard Python logger
        raw_zip_folder : path to scan for .bento_status files
    """
    if not os.path.isdir(raw_zip_folder):
        return

    reset_count = 0
    logger.info("Startup: scanning for orphaned in_progress status files ...")

    try:
        for fname in os.listdir(raw_zip_folder):
            if not fname.endswith(".bento_status"):
                continue

            status_path = os.path.join(raw_zip_folder, fname)
            try:
                # Check file age first (cheap) before reading JSON (expensive)
                age = time.time() - os.path.getmtime(status_path)
                if age <= LOCK_MAX_AGE_SECONDS:
                    continue  # Recent enough — may still be active

                with open(status_path, "r") as sf:
                    data = json.load(sf)

                if data.get("status") != "in_progress":
                    continue  # Not in_progress — leave it alone

                # Stale in_progress — reset to failed
                data["status"] = "failed"
                data["detail"] = (
                    "Reset by watcher startup cleanup: "
                    "previous watcher was killed mid-build "
                    "(status age=" + str(int(age)) + "s, "
                    "threshold=" + str(LOCK_MAX_AGE_SECONDS) + "s)"
                )
                data["reset_timestamp"] = datetime.now().isoformat()

                with open(status_path, "w") as sf:
                    json.dump(data, sf, indent=2)

                logger.warning(
                    "Startup: reset stale in_progress -> failed: "
                    + fname + " (age=" + str(int(age)) + "s)"
                )
                reset_count += 1

            except Exception as e:
                logger.warning(
                    "Startup: could not process status file "
                    + fname + ": " + str(e)
                )

    except OSError as e:
        logger.warning("Startup: could not scan for status files: " + str(e))

    if reset_count > 0:
        logger.warning(
            "Startup: reset " + str(reset_count)
            + " orphaned in_progress status file(s)."
        )
    else:
        logger.info("Startup: no orphaned status files found.")


def cleanup_old_status_files(logger, raw_zip_folder):
    """
    Delete completed .bento_status files older than STATUS_RETENTION_SECONDS.

    WHY THIS EXISTS:
        The orchestrator intentionally keeps .bento_status files after a
        successful build so the BENTO GUI health panel can always read the
        latest result. Without periodic cleanup, these files accumulate
        indefinitely in RAW_ZIP (one per build).

    RETENTION POLICY:
        Files with status 'success', 'failed', or 'timeout' older than
        STATUS_RETENTION_SECONDS (default 7 days) are deleted.
        Files with status 'in_progress' or 'pending' are never deleted here
        — those are handled by cleanup_stale_status_on_startup().

    Args:
        logger         : standard Python logger
        raw_zip_folder : path to scan for .bento_status files
    """
    if STATUS_RETENTION_SECONDS <= 0:
        return   # cleanup disabled
    if not os.path.isdir(raw_zip_folder):
        return

    deleted_count = 0
    now = time.time()

    try:
        for fname in os.listdir(raw_zip_folder):
            if not fname.endswith(".bento_status"):
                continue

            status_path = os.path.join(raw_zip_folder, fname)
            try:
                age = now - os.path.getmtime(status_path)
                if age <= STATUS_RETENTION_SECONDS:
                    continue   # still within retention window

                with open(status_path, "r") as sf:
                    data = json.load(sf)

                state = data.get("status", "unknown")
                if state in ("in_progress", "pending"):
                    continue   # let stale-status cleanup handle these

                os.remove(status_path)
                deleted_count += 1
                logger.info(
                    "Startup: deleted old status file "
                    + fname + " (age=" + str(int(age // 86400)) + "d, status=" + state + ")"
                )

            except Exception as e:
                logger.warning(
                    "Startup: could not process status file "
                    + fname + ": " + str(e)
                )

    except OSError as e:
        logger.warning("Startup: could not scan for old status files: " + str(e))

    if deleted_count > 0:
        logger.info(
            "Startup: deleted " + str(deleted_count)
            + " old status file(s) (retention=" + str(STATUS_RETENTION_SECONDS // 86400) + "d)."
        )


# ================================================================
# LOGGING SETUP
# ================================================================

def setup_logger(env):
    """
    Configure rotating file logger + console output for the watcher.
    Log file: LOG_DIR/watcher_<ENV>.log
    """
    if not os.path.exists(LOG_DIR):
        try:
            os.makedirs(LOG_DIR)
        except Exception:
            pass

    log_path = os.path.join(LOG_DIR, "watcher_" + env + ".log")

    logger = logging.getLogger("bento_watcher_" + env)
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger  # Already configured (re-entrant guard)

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
# WAIT FOR STABLE FILE SIZE
# ================================================================

def wait_until_stable(zip_path, logger, checks=3, interval=5):
    """
    Poll file size until it stops changing.
    Prevents extracting a ZIP that is still being written to the shared drive.

    Args:
        zip_path : full path to the ZIP file
        logger   : standard Python logger
        checks   : number of consecutive identical-size checks required
        interval : seconds between size checks

    Returns:
        True  - file size is stable (safe to read)
        False - file never stabilised within max_attempts
    """
    logger.info("Waiting for file to finish copying to shared drive ...")
    last_size    = -1
    stable_count = 0
    attempts     = 0
    max_attempts = 60  # 5 minutes max wait (60 × 5s)

    while stable_count < checks and attempts < max_attempts:
        try:
            size = os.path.getsize(zip_path)
        except Exception:
            size = -1

        if size == last_size and size > 0:
            stable_count += 1
            logger.debug(
                "Stable check " + str(stable_count) + "/" + str(checks)
                + " size=" + str(size)
            )
        else:
            if stable_count > 0:
                logger.debug("Size changed — resetting stable counter")
            stable_count = 0
            last_size    = size

        if stable_count < checks:
            time.sleep(interval)
        attempts += 1

    if stable_count >= checks:
        logger.info(
            "File stable at " + str(last_size) + " bytes. Proceeding."
        )
        return True
    else:
        logger.error(
            "File did not stabilise after "
            + str(attempts * interval) + "s. Skipping."
        )
        return False


# ================================================================
# TAIL LOG HELPER
# ================================================================

def _tail_log(log_path, n=8):
    """Return the last n non-empty lines of a log file as a string."""
    try:
        with open(log_path, "r") as f:
            lines = [l.rstrip() for l in f.readlines() if l.strip()]
        return "\n".join(lines[-n:])
    except Exception:
        return "(log not readable)"


# ================================================================
# PROCESS A SINGLE ZIP
# ================================================================

def process_zip(zip_path, env, logger):
    """
    Full pipeline for one incoming ZIP:
        0. Wait for file to finish copying (size-stable check)
        1. [P6] ZIP integrity check (CRC validation)
        2. Acquire repo build lock
        3. Extract ZIP into repo
        4. Run make release via Cygwin bash [P1 timeout, P2 heartbeat]
        5. Post-build memory cleanup
        6. Find produced .tgz
        7. Binary copy to RELEASE_TGZ/HOSTNAME_JIRA_ENV/

    Returns:
        True  - success (permanent, do not reprocess)
        False - permanent failure (do not retry)
        None  - temporary failure (retry next poll — repo busy)
    """
    zip_name  = os.path.basename(zip_path)
    cfg       = TESTER_REGISTRY[env]
    hostname  = cfg["hostname"]
    repo_dir  = cfg["repo_dir"]
    build_cmd = cfg["build_cmd"]
    jira_key  = parse_jira_key_from_zip(zip_name)

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

    # -- [P6] 1. ZIP integrity check --
    # Validate CRC of every entry before attempting extraction.
    # A corrupt/partial ZIP is a permanent failure — do NOT retry.
    if not _is_zip_valid(zip_path, logger):
        write_status(
            zip_path, "failed",
            "ZIP failed integrity check (corrupt or incomplete): " + zip_name
        )
        return False  # Permanent fail — corrupt ZIPs won't self-heal

    # -- 2. Acquire repo lock --
    # Prevents two ZIPs from building in the same repo simultaneously.
    # Returns None (retry) if another build is already in progress.
    repo_lock = RepoBuildLock(repo_dir)
    if not repo_lock.acquire(logger=logger):
        msg = "Repo already building. Will retry next poll."
        logger.warning(msg)
        write_status(zip_path, "pending", msg)
        return None  # Temporary — retry on next poll cycle

    try:
        # -- 3. Extract ZIP --
        logger.info("Extracting ZIP into repo: " + repo_dir)
        if not extract_zip(zip_path, repo_dir, logger):
            write_status(zip_path, "failed", "ZIP extraction failed")
            return False

        # -- 4. Run build --
        # [P1] timeout enforced, [P2] heartbeat thread active (in watcher_builder.py)
        logger.info("Starting build: " + build_cmd)
        success, pre_build_time = run_build(repo_dir, build_cmd, zip_name, logger)
        if not success:
            write_status(zip_path, "failed", "Build failed — check build log")
            return False

        # -- 5. Post-build memory cleanup --
        cleanup_memory(logger)

        # -- 6. Find produced .tgz --
        tgz_path = find_tgz(repo_dir, pre_build_time, logger)
        if not tgz_path:
            write_status(zip_path, "failed", "No .tgz produced after build")
            return False

        # -- 7. Binary copy to RELEASE_TGZ --
        copy_ok, dest_path = copy_tgz_to_release(
            tgz_path, zip_name, hostname, jira_key, env, logger
        )
        if not copy_ok:
            write_status(zip_path, "failed", "TGZ copy to release folder failed")
            return False

        # -- Done --
        result_detail = (
            hostname + "_" + jira_key + "_" + env
            + "/" + os.path.basename(dest_path if dest_path else tgz_path)
        )
        write_status(zip_path, "success", result_detail)
        logger.info("[OK] Complete: " + zip_name + " -> " + str(dest_path))
        return True

    finally:
        repo_lock.release()


# ================================================================
# MAIN WATCH LOOP
# ================================================================

def watch(env, logger):
    """
    Main poll loop. Runs forever (while True) watching RAW_ZIP_FOLDER
    for new ZIP files matching this tester's hostname + env.

    Changes applied:
        [P3] retry_counts dict — limits retries to MAX_RETRIES per ZIP
        [P4] _prune_processed() — trims processed set every HEARTBEAT_EVERY polls
        [P6] _is_zip_valid() — called inside process_zip() before extraction
        [P8] cleanup_stale_status_on_startup() — resets orphaned in_progress files
    """
    # Build repo_dirs map for lock cleanup
    repo_dirs = {
        e: cfg["repo_dir"]
        for e, cfg in TESTER_REGISTRY.items()
    }

    # ── Startup cleanup ──────────────────────────────────────────
    # [original] Remove stale per-ZIP and repo-level lock files
    cleanup_stale_locks_on_startup(logger, RAW_ZIP_FOLDER, repo_dirs)

    # [P8] Reset orphaned in_progress status files left by a killed watcher
    cleanup_stale_status_on_startup(logger, RAW_ZIP_FOLDER)

    # Delete completed status files older than STATUS_RETENTION_SECONDS (default 7 days)
    cleanup_old_status_files(logger, RAW_ZIP_FOLDER)

    logger.info(
        "Watching " + RAW_ZIP_FOLDER
        + " every " + str(POLL_INTERVAL_SECONDS) + "s ..."
    )

    # ── State ────────────────────────────────────────────────────
    processed    = set()   # completed ZIPs (success or permanent fail)
    retry_counts = {}      # [P3] zip_path -> int, retry attempt counter

    # Record ZIPs already present at startup — skip them silently
    # (we only process ZIPs that ARRIVE after the watcher starts)
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

    # Resolve this tester's config once — env never changes during watcher lifetime
    cfg             = TESTER_REGISTRY[env]
    heartbeat_count = 0
    HEARTBEAT_EVERY = 60   # log "still watching" every 60 polls ≈ 1 hour at 60s/poll

    # ── Main poll loop ───────────────────────────────────────────
    while True:
        try:
            # Guard: shared drive may become temporarily unreachable
            if not os.path.isdir(RAW_ZIP_FOLDER):
                logger.warning(
                    "RAW_ZIP folder not reachable: " + RAW_ZIP_FOLDER
                    + " — will retry in " + str(POLL_INTERVAL_SECONDS) + "s"
                )
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            # Hourly heartbeat + [P4] processed set pruning
            heartbeat_count += 1
            if heartbeat_count >= HEARTBEAT_EVERY:
                heartbeat_count = 0
                logger.info(
                    "Still watching " + RAW_ZIP_FOLDER
                    + " | processed=" + str(len(processed))
                    + " | retrying=" + str(len(retry_counts))
                )
                # [P4] Prune the processed set to cap memory usage
                processed = _prune_processed(processed, logger)

            # ── Scan for new ZIPs ─────────────────────────────────
            # sorted() gives FIFO order since ZIP names embed timestamps
            for fname in sorted(os.listdir(RAW_ZIP_FOLDER)):
                if not fname.lower().endswith(".zip"):
                    continue
                if ".bento_" in fname:
                    continue  # Skip lock/status sidecars

                zip_path = os.path.join(RAW_ZIP_FOLDER, fname)

                # Skip if already finished (success or permanent fail)
                if zip_path in processed:
                    continue

                # Skip ZIPs that were already present when watcher started
                if zip_path in pre_existing:
                    continue  # Silently skip

                # Skip ZIPs not addressed to this tester/env combination
                if not zip_belongs_to_tester(fname, cfg["hostname"], env):
                    logger.debug(
                        "Not for this tester, skipping: " + fname
                    )
                    continue

                # Per-ZIP file lock — prevents two watcher instances
                # from claiming the same ZIP simultaneously
                lock = LockFile(zip_path)
                if not lock.acquire():
                    logger.debug(
                        "Locked by another process, skipping: " + fname
                    )
                    continue

                try:
                    result = process_zip(zip_path, env, logger)

                    if result is True:
                        # ── Success ───────────────────────────────
                        processed.add(zip_path)
                        retry_counts.pop(zip_path, None)  # [P3] clear counter
                        logger.info("[OK] " + fname)

                    elif result is None:
                        # ── Temporary fail (repo busy) — retry ───
                        # [P3] Increment retry counter and check limit
                        retry_counts[zip_path] = retry_counts.get(zip_path, 0) + 1
                        count = retry_counts[zip_path]

                        if count >= MAX_RETRIES:
                            # Give up — too many retries
                            logger.error(
                                "[GIVE UP] " + fname
                                + " — exceeded MAX_RETRIES=" + str(MAX_RETRIES)
                                + " (" + str(count * POLL_INTERVAL_SECONDS // 60)
                                + " min elapsed). Marking as failed."
                            )
                            write_status(
                                zip_path, "failed",
                                "Watcher gave up after "
                                + str(MAX_RETRIES) + " retry attempts "
                                + "(repo may be permanently locked)"
                            )
                            processed.add(zip_path)
                            retry_counts.pop(zip_path, None)
                        else:
                            logger.info(
                                "[RETRY " + str(count) + "/" + str(MAX_RETRIES) + "] "
                                + fname + " — will retry next poll"
                            )

                    else:
                        # ── Permanent fail ────────────────────────
                        processed.add(zip_path)
                        retry_counts.pop(zip_path, None)  # [P3] clear counter
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
# ENTRY POINT
# ================================================================

def main():
    parser = argparse.ArgumentParser(
        description="BENTO Tester Watcher — monitors RAW_ZIP for incoming TP packages"
    )
    parser.add_argument(
        "--env",
        required=True,
        choices=list(TESTER_REGISTRY.keys()),
        help="Tester environment to watch (e.g. ABIT, SFN2, CNFG)"
    )
    args   = parser.parse_args()
    env    = args.env.upper()
    logger = setup_logger(env)

    logger.info("=" * 60)
    logger.info("BENTO Tester Watcher starting up")
    logger.info("ENV        : " + env)
    logger.info("Hostname   : " + TESTER_REGISTRY[env]["hostname"])
    logger.info("Repo dir   : " + TESTER_REGISTRY[env]["repo_dir"])
    logger.info("Build cmd  : " + TESTER_REGISTRY[env]["build_cmd"])
    logger.info("RAW_ZIP    : " + RAW_ZIP_FOLDER)
    logger.info("RELEASE    : " + RELEASE_TGZ_FOLDER)
    logger.info("Poll       : every " + str(POLL_INTERVAL_SECONDS) + "s")
    logger.info("Max retries: " + str(MAX_RETRIES))
    logger.info("Max proc.  : " + str(MAX_PROCESSED_SIZE) + " entries")
    logger.info("=" * 60)

    try:
        watch(env, logger)
    except KeyboardInterrupt:
        logger.info("Watcher stopped by user (KeyboardInterrupt).")
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
    