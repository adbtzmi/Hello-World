# -*- coding: utf-8 -*-
"""
watcher_lock.py
===============
Per-ZIP lock files and build status files.

LockFile     : prevents two watcher instances claiming the same ZIP.
RepoBuildLock: prevents two ZIPs building in the same repo simultaneously.
write_status : writes .bento_status JSON sidecar next to the ZIP.
cleanup_stale_locks_on_startup : removes orphaned lock files AND resets
                                  stale in_progress status files on startup.

CHANGES (Recommendations Applied):
    [P8] cleanup_stale_locks_on_startup() extended to also reset orphaned
         .bento_status files stuck at 'in_progress'.

         WHY THIS MATTERS:
             If the watcher is killed mid-build (CTRL+C, power loss, OOM kill,
             machine restart), the .bento_status sidecar file is left stuck
             at 'in_progress' forever. The orchestrator (compilation_orchestrator.py)
             polls this file and will wait indefinitely — up to its full 35-minute
             timeout — even though NO BUILD IS ACTUALLY RUNNING.

             By resetting stale in_progress files to 'failed' on startup, the
             orchestrator immediately detects the failure on its next poll and
             can report it to the user instead of hanging for 35 minutes silently.

         STALE THRESHOLD:
             Uses LOCK_MAX_AGE_SECONDS (1800s = 30 min) — same threshold as
             the build timeout and the lock file expiry. A .bento_status file
             older than 30 minutes that is still 'in_progress' means the build
             process that wrote it is certainly no longer running.

         WHAT IS WRITTEN:
             The status is updated to 'failed' with a detailed message including:
               - Human-readable reason (watcher killed mid-build)
               - File age in seconds
               - Stale threshold used
               - ISO timestamp of when the reset occurred
             This gives the orchestrator and the UI full context on what happened.
"""
from __future__ import print_function
import os
import json
import time
from datetime import datetime

from watcher_config import LOCK_MAX_AGE_SECONDS


# ================================================================
# PER-ZIP LOCK
# ================================================================

class LockFile(object):
    """
    Prevents the same ZIP being processed by two watcher instances.

    Lock file: <zip_path>.bento_lock
    Contents : PID on line 1, unix timestamp on line 2.

    Auto-expires stale locks when either:
      - Lock file is older than LOCK_MAX_AGE_SECONDS (30 min), OR
      - The PID recorded in the lock file is no longer alive (os.kill(pid, 0))

    This ensures that if a watcher crashes mid-processing, the next
    watcher instance (or the same one after restart) can reclaim the ZIP
    without manual intervention.
    """

    def __init__(self, zip_path):
        self.lock_path = zip_path + ".bento_lock"

    def _read_pid(self):
        """Read the PID stored in the lock file. Returns int or None."""
        try:
            with open(self.lock_path, "r") as f:
                line = f.readline().strip()
                return int(line) if line.isdigit() else None
        except Exception:
            return None

    def _is_stale(self):
        """
        Returns True if the lock file should be treated as stale/expired.
        Checks both age (time-based) and process liveness (PID-based).
        """
        try:
            age = time.time() - os.path.getmtime(self.lock_path)
        except OSError:
            return False

        # Age-based expiry: older than LOCK_MAX_AGE_SECONDS = definitely stale
        if age > LOCK_MAX_AGE_SECONDS:
            return True

        # PID-based expiry: lock file is recent but owning process is dead
        pid = self._read_pid()
        if pid is not None:
            try:
                os.kill(pid, 0)   # Signal 0 = check existence only
                return False      # Process alive -> lock is valid
            except OSError:
                return True       # Process dead -> lock is stale
        return False

    def acquire(self):
        """
        Attempt to acquire the per-ZIP lock.

        Returns True  if the lock was successfully acquired.
        Returns False if another live process holds the lock.

        If a stale lock is found it is removed and acquisition proceeds.
        """
        if os.path.exists(self.lock_path):
            if self._is_stale():
                try:
                    os.remove(self.lock_path)
                except OSError:
                    pass
            else:
                return False  # Lock is valid and held by another process

        try:
            with open(self.lock_path, "w") as f:
                f.write(str(os.getpid()) + "\n")
                f.write(str(time.time()) + "\n")
            return True
        except Exception:
            return False

    def release(self):
        """Release the per-ZIP lock by deleting the lock file."""
        try:
            if os.path.exists(self.lock_path):
                os.remove(self.lock_path)
        except Exception:
            pass


# ================================================================
# REPO-LEVEL BUILD LOCK
# ================================================================

class RepoBuildLock(object):
    """
    Ensures only ONE make invocation runs inside a repo at a time.

    Lock file: <repo_dir>/.bento_build_lock
    Contents : PID on line 1, unix timestamp on line 2.

    WHY THIS IS NEEDED:
        If two ZIPs arrive in quick succession for the same tester/env,
        the second ZIP must NOT start building while the first is still
        running make release in the same repo directory. This would cause
        race conditions in the build output and corrupt the produced .tgz.

        The RepoBuildLock serialises builds per repo, while LockFile
        serialises ZIP ownership across watcher instances. They are
        independent layers of protection.
    """

    def __init__(self, repo_dir):
        self.lock_path = os.path.join(repo_dir, ".bento_build_lock")

    def _read_pid(self):
        """Read the PID stored in the lock file. Returns int or None."""
        try:
            with open(self.lock_path, "r") as f:
                line = f.readline().strip()
                return int(line) if line.isdigit() else None
        except Exception:
            return None

    def _is_stale(self):
        """
        Returns True if the repo build lock is stale/expired.
        Checks both age (time-based) and process liveness (PID-based).
        """
        try:
            age = time.time() - os.path.getmtime(self.lock_path)
        except OSError:
            return False

        # Age-based: older than build timeout = build definitely finished/died
        if age > LOCK_MAX_AGE_SECONDS:
            return True

        # PID-based: lock file is recent but process is dead
        pid = self._read_pid()
        if pid is not None:
            try:
                os.kill(pid, 0)
                return False   # Process alive -> build still running
            except OSError:
                return True    # Process dead -> stale lock
        return False

    def acquire(self, logger=None):
        """
        Attempt to acquire the repo-level build lock.

        Returns True  if lock acquired (safe to start build).
        Returns False if another build is actively running in this repo
                      (caller should return None to trigger retry next poll).

        If a stale lock is found it is removed and acquisition proceeds.
        """
        if os.path.exists(self.lock_path):
            if self._is_stale():
                if logger:
                    logger.warning(
                        "Stale repo build lock removed: " + self.lock_path
                    )
                try:
                    os.remove(self.lock_path)
                except OSError:
                    pass
            else:
                return False  # Active build running — do not proceed

        try:
            with open(self.lock_path, "w") as f:
                f.write(str(os.getpid()) + "\n")
                f.write(str(time.time()) + "\n")
            return True
        except Exception:
            return False

    def release(self):
        """Release the repo-level build lock."""
        try:
            if os.path.exists(self.lock_path):
                os.remove(self.lock_path)
        except Exception:
            pass


# ================================================================
# STATUS FILE
# ================================================================

def write_status(zip_path, status, detail=""):
    """
    Write a JSON status sidecar file next to the ZIP.

    File: <zip_path>.bento_status
    Read by: compilation_orchestrator.py (wait_for_build polling loop)
             watcher_main.py (startup cleanup)
             main.py (Watcher Health Monitor panel)

    status values:
        'in_progress' - watcher has picked up the ZIP, build running
        'pending'     - ZIP seen but repo is busy, will retry
        'success'     - build completed and TGZ copied successfully
        'failed'      - permanent failure (build error, corrupt ZIP, etc.)

    Args:
        zip_path : full path to the ZIP file (status file placed alongside)
        status   : one of the status values above
        detail   : human-readable detail message (shown in UI / orchestrator log)
    """
    status_path = zip_path + ".bento_status"
    payload = {
        "zip_file":  os.path.basename(zip_path),
        "status":    status,
        "detail":    detail,
        "timestamp": datetime.now().isoformat(),
    }
    try:
        with open(status_path, "w") as f:
            json.dump(payload, f, indent=2)
    except Exception:
        pass


# ================================================================
# STARTUP CLEANUP
# ================================================================

def cleanup_stale_locks_on_startup(logger, raw_zip_folder, repo_dirs):
    """
    Remove orphaned lock files AND reset stale in_progress status files
    left by a previously killed watcher process.

    Called once at the start of watch() in watcher_main.py before the
    main poll loop begins.

    THREE THINGS ARE CLEANED:

    1. Per-ZIP .bento_lock files (existing behaviour)
       -----------------------------------------------
       Stale per-ZIP lock files are removed so the watcher can
       re-acquire them. A lock is stale if it is older than
       LOCK_MAX_AGE_SECONDS OR the PID it contains is not alive.

    2. Repo-level .bento_build_lock files (existing behaviour)
       ---------------------------------------------------------
       Stale repo-level build locks are removed so builds can
       proceed. Same staleness criteria as per-ZIP locks.

    3. [P8] Orphaned in_progress .bento_status files (NEW)
       -----------------------------------------------------
       If the watcher is killed mid-build (CTRL+C, power loss, OOM kill,
       machine restart), the .bento_status sidecar is left stuck at
       'in_progress'. The orchestrator polls this file and would wait
       indefinitely — up to its full 35-minute timeout.

       Any .bento_status file that:
         - Has status == 'in_progress', AND
         - Is older than LOCK_MAX_AGE_SECONDS (30 min)
       is reset to 'failed' with a descriptive message so the orchestrator
       can detect and report the failure immediately on its next poll.

    Args:
        logger         : standard Python logger
        raw_zip_folder : path to scan for .bento_lock and .bento_status files
        repo_dirs      : dict of {env_name: repo_dir} for repo lock cleanup
    """
    logger.info("Startup: scanning for stale lock and status files ...")
    cleaned = 0

    # ── 1. Per-ZIP .bento_lock files ────────────────────────────
    if os.path.isdir(raw_zip_folder):
        try:
            for fname in os.listdir(raw_zip_folder):

                # ── Clean stale per-ZIP lock files ───────────────
                if fname.endswith(".bento_lock"):
                    lock_path = os.path.join(raw_zip_folder, fname)
                    try:
                        age = time.time() - os.path.getmtime(lock_path)
                        if age > LOCK_MAX_AGE_SECONDS:
                            os.remove(lock_path)
                            logger.warning(
                                "Startup: removed stale ZIP lock: "
                                + fname
                                + " (age=" + str(int(age)) + "s)"
                            )
                            cleaned += 1
                    except Exception as e:
                        logger.warning(
                            "Startup: could not remove lock "
                            + fname + ": " + str(e)
                        )

                # ── [P8] Reset stale in_progress status files ────
                elif fname.endswith(".bento_status"):
                    status_path = os.path.join(raw_zip_folder, fname)
                    try:
                        # Check age first (cheap I/O) before reading JSON
                        age = time.time() - os.path.getmtime(status_path)
                        if age <= LOCK_MAX_AGE_SECONDS:
                            continue  # Recent enough — may still be active

                        # Read the status file
                        with open(status_path, "r") as sf:
                            data = json.load(sf)

                        # Only reset if stuck at in_progress
                        if data.get("status") != "in_progress":
                            continue

                        # Overwrite with 'failed' so the orchestrator
                        # doesn't wait for a build that will never finish
                        data["status"] = "failed"
                        data["detail"] = (
                            "Reset by watcher startup cleanup: "
                            "previous watcher was killed mid-build "
                            "(status file age=" + str(int(age)) + "s, "
                            "threshold=" + str(LOCK_MAX_AGE_SECONDS) + "s). "
                            "Build result is unknown — please resubmit."
                        )
                        data["reset_timestamp"] = datetime.now().isoformat()

                        with open(status_path, "w") as sf:
                            json.dump(data, sf, indent=2)

                        logger.warning(
                            "Startup: reset orphaned in_progress -> failed: "
                            + fname
                            + " (age=" + str(int(age)) + "s)"
                        )
                        cleaned += 1

                    except Exception as e:
                        logger.warning(
                            "Startup: could not process status file "
                            + fname + ": " + str(e)
                        )

        except OSError as e:
            logger.warning(
                "Startup: could not scan RAW_ZIP folder: " + str(e)
            )

    # ── 2. Repo-level .bento_build_lock files ───────────────────
    seen = set()
    for env_name, repo_dir in repo_dirs.items():
        lock_path = os.path.join(repo_dir, ".bento_build_lock")

        # Deduplicate: multiple envs may share the same repo_dir
        if lock_path in seen:
            continue
        seen.add(lock_path)

        if not os.path.exists(lock_path):
            continue

        rbl = RepoBuildLock(repo_dir)
        if rbl._is_stale():
            try:
                os.remove(lock_path)
                logger.warning(
                    "Startup: removed stale repo build lock for env="
                    + env_name
                    + " (" + lock_path + ")"
                )
                cleaned += 1
            except Exception as e:
                logger.warning(
                    "Startup: could not remove repo lock "
                    + lock_path + ": " + str(e)
                )

    # ── Summary ──────────────────────────────────────────────────
    if cleaned > 0:
        logger.warning(
            "Startup cleanup done. Removed/reset " + str(cleaned)
            + " stale file(s)."
        )
    else:
        logger.info(
            "Startup cleanup done. No stale files found."
        )