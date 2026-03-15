# -*- coding: utf-8 -*-
"""
watcher_lock.py
===============
Per-ZIP lock files and build status files.

LockFile     : prevents two watcher instances claiming the same ZIP.
RepoBuildLock: prevents two ZIPs building in the same repo simultaneously.
write_status : writes .bento_status JSON sidecar next to the ZIP.
"""
from __future__ import print_function
import os
import json
import time
from datetime import datetime

from watcher_config import LOCK_MAX_AGE_SECONDS


# ----------------------------------------------------------------
# STATUS FILE
# ----------------------------------------------------------------
def write_status(zip_path, status, detail=""):
    """
    Write a JSON status sidecar file next to the ZIP.
    status: 'in_progress' | 'success' | 'failed' | 'pending'
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


# ----------------------------------------------------------------
# PER-ZIP LOCK
# ----------------------------------------------------------------
class LockFile(object):
    """
    Prevents the same ZIP being processed by two watcher instances.
    Lock file: <zip>.bento_lock  containing PID on line 1, timestamp on line 2.
    Auto-expires stale locks (dead PID or older than LOCK_MAX_AGE_SECONDS).
    """

    def __init__(self, zip_path):
        self.lock_path = zip_path + ".bento_lock"

    def _read_pid(self):
        try:
            with open(self.lock_path, "r") as f:
                line = f.readline().strip()
            return int(line) if line.isdigit() else None
        except Exception:
            return None

    def _is_stale(self):
        try:
            age = time.time() - os.path.getmtime(self.lock_path)
        except OSError:
            return False
        if age > LOCK_MAX_AGE_SECONDS:
            return True
        pid = self._read_pid()
        if pid is not None:
            try:
                os.kill(pid, 0)
                return False   # process alive -> lock valid
            except OSError:
                return True    # process dead -> stale
        return False

    def acquire(self):
        if os.path.exists(self.lock_path):
            if self._is_stale():
                try:
                    os.remove(self.lock_path)
                except OSError:
                    pass
            else:
                return False
        try:
            with open(self.lock_path, "w") as f:
                f.write(str(os.getpid()) + "\n")
                f.write(str(time.time()) + "\n")
            return True
        except Exception:
            return False

    def release(self):
        try:
            if os.path.exists(self.lock_path):
                os.remove(self.lock_path)
        except Exception:
            pass


# ----------------------------------------------------------------
# REPO-LEVEL BUILD LOCK
# ----------------------------------------------------------------
class RepoBuildLock(object):
    """
    Ensures only ONE make invocation runs inside a repo at a time.
    Lock file: <repo_dir>/.bento_build_lock
    """

    def __init__(self, repo_dir):
        self.lock_path = os.path.join(repo_dir, ".bento_build_lock")

    def _read_pid(self):
        try:
            with open(self.lock_path, "r") as f:
                line = f.readline().strip()
            return int(line) if line.isdigit() else None
        except Exception:
            return None

    def _is_stale(self):
        try:
            age = time.time() - os.path.getmtime(self.lock_path)
        except OSError:
            return False
        if age > LOCK_MAX_AGE_SECONDS:
            return True
        pid = self._read_pid()
        if pid is not None:
            try:
                os.kill(pid, 0)
                return False
            except OSError:
                return True
        return False

    def acquire(self, logger=None):
        if os.path.exists(self.lock_path):
            if self._is_stale():
                if logger:
                    logger.warning("Stale repo build lock removed: " + self.lock_path)
                try:
                    os.remove(self.lock_path)
                except OSError:
                    pass
            else:
                return False
        try:
            with open(self.lock_path, "w") as f:
                f.write(str(os.getpid()) + "\n")
                f.write(str(time.time()) + "\n")
            return True
        except Exception:
            return False

    def release(self):
        try:
            if os.path.exists(self.lock_path):
                os.remove(self.lock_path)
        except Exception:
            pass


# ----------------------------------------------------------------
# STARTUP CLEANUP
# ----------------------------------------------------------------
def cleanup_stale_locks_on_startup(logger, raw_zip_folder, repo_dirs):
    """Remove orphaned lock files left by a previously killed watcher."""
    logger.info("Startup: scanning for stale lock files ...")
    cleaned = 0

    # Repo-level locks
    seen = set()
    for env_name, repo_dir in repo_dirs.items():
        lock_path = os.path.join(repo_dir, ".bento_build_lock")
        if lock_path in seen or not os.path.exists(lock_path):
            continue
        seen.add(lock_path)
        rbl = RepoBuildLock(repo_dir)
        if rbl._is_stale():
            try:
                os.remove(lock_path)
                logger.warning("Startup: removed stale repo lock for env=" + env_name)
                cleaned += 1
            except Exception as e:
                logger.warning("Startup: could not remove " + lock_path + ": " + str(e))

    # Per-ZIP locks
    if os.path.isdir(raw_zip_folder):
        try:
            for fname in os.listdir(raw_zip_folder):
                if not fname.endswith(".bento_lock"):
                    continue
                lock_path = os.path.join(raw_zip_folder, fname)
                try:
                    age = time.time() - os.path.getmtime(lock_path)
                    if age > LOCK_MAX_AGE_SECONDS:
                        os.remove(lock_path)
                        logger.warning("Startup: removed stale ZIP lock: " + fname)
                        cleaned += 1
                except Exception as e:
                    logger.warning("Startup: could not remove " + lock_path + ": " + str(e))
        except OSError:
            pass

    logger.info("Startup lock cleanup done. Removed: " + str(cleaned))
