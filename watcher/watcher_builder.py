# -*- coding: utf-8 -*-
"""
watcher_builder.py
==================
Handles ZIP extraction, build execution, and post-build memory cleanup.

WHY CYGWIN BASH instead of cmd.exe:
  make and gcc are Cygwin tools - they are NOT in the Windows PATH.
  cmd.exe cannot find them. We must use Cygwin bash -lc (login shell)
  which loads the full Cygwin environment including /usr/bin.

WHY POST_BUILD_SLEEP:
  After make release exits, the OS needs time to reclaim RAM freed
  by gcc/ld subprocesses. Sleeping before the copy gives it that time.
  Combined with gc.collect() this significantly reduces OOM risk for copy.
"""
from __future__ import print_function
import os
import gc
import time
import zipfile
import subprocess
from datetime import datetime

from watcher_config import (
    BUILD_TIMEOUT_SECONDS,
    POST_BUILD_SLEEP_SECONDS,
    LOG_DIR,
)


# ----------------------------------------------------------------
# ZIP EXTRACTION
# ----------------------------------------------------------------
def extract_zip(zip_path, dest_dir, logger):
    """
    Extract ZIP contents into dest_dir (the repo root).
    Uses binary-safe zipfile module.
    Returns True on success, False on failure.
    """
    zip_name = os.path.basename(zip_path)
    logger.info("Extracting: " + zip_name + " -> " + dest_dir)
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(dest_dir)
        logger.info("Extraction complete.")
        return True
    except zipfile.BadZipFile as e:
        logger.error("Bad ZIP file: " + str(e))
        return False
    except Exception as e:
        logger.error("Extraction failed: " + str(e))
        return False


# ----------------------------------------------------------------
# CYGWIN PATH HELPERS
# ----------------------------------------------------------------
def find_cygwin_bash():
    """Find Cygwin bash.exe on this tester machine."""
    candidates = [
        "C:/cygwin64/bin/bash.exe",
        "C:/cygwin/bin/bash.exe",
        "C:/tools/cygwin/bin/bash.exe",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def win_to_cygwin(win_path):
    """
    Convert a Windows path to a Cygwin path.
    Example: C:/xi/adv_ibir_master -> /cygdrive/c/xi/adv_ibir_master
    """
    # Normalise all backslashes to forward slashes first
    path = win_path.replace("\\", "/")
    # Convert drive letter: C:/foo -> /cygdrive/c/foo
    if len(path) >= 2 and path[1] == ":":
        drive = path[0].lower()
        rest  = path[2:]
        return "/cygdrive/" + drive + rest
    return path


# ----------------------------------------------------------------
# BUILD
# ----------------------------------------------------------------
def run_build(repo_dir, build_cmd, zip_name, logger):
    """
    Run make release using Cygwin bash login shell.

    Cygwin bash -lc is required because make/gcc are Cygwin tools
    and only exist in the Cygwin PATH (/usr/bin), not Windows PATH.
    cmd.exe cannot find them.

    Returns (success: bool, pre_build_time: float)
    pre_build_time lets find_tgz() skip stale artifacts.
    """
    pre_build_time = time.time()

    if not os.path.exists(LOG_DIR):
        try:
            os.makedirs(LOG_DIR)
        except Exception:
            pass

    build_log = os.path.join(
        LOG_DIR, "build_" + os.path.splitext(zip_name)[0] + ".log"
    )

    # Convert repo path to Cygwin format for bash
    cygwin_repo = win_to_cygwin(repo_dir)
    bash_exe    = find_cygwin_bash()

    if bash_exe:
        # -l = login shell (loads /etc/profile, puts /usr/bin in PATH)
        # -c = run this command string
        shell_cmd = "cd " + cygwin_repo + " && " + build_cmd
        cmd_list  = [bash_exe, "-lc", shell_cmd]
        shell_type = "Cygwin bash -lc"
    else:
        # Fallback to cmd.exe - only works if make.exe is in Windows PATH
        shell_cmd = "cd " + repo_dir + " && " + build_cmd
        cmd_list  = ["cmd.exe", "/c", shell_cmd]
        shell_type = "cmd.exe (fallback - make must be in Windows PATH)"
        logger.warning("Cygwin bash not found! Falling back to cmd.exe")
        logger.warning("If make is not in Windows PATH, build will fail")

    logger.info("Shell      : " + shell_type)
    logger.info("Cygwin dir : " + cygwin_repo)
    logger.info("Build cmd  : " + build_cmd)
    logger.info("Build log  : " + build_log)
    logger.info("Timeout    : " + str(BUILD_TIMEOUT_SECONDS) + "s")

    try:
        with open(build_log, "w") as log_f:
            log_f.write("BENTO Build Log\n")
            log_f.write("Started   : " + datetime.now().isoformat() + "\n")
            log_f.write("Shell     : " + shell_type + "\n")
            log_f.write("Repo dir  : " + repo_dir + "\n")
            log_f.write("Cygwin    : " + cygwin_repo + "\n")
            log_f.write("Command   : " + shell_cmd + "\n")
            log_f.write("=" * 60 + "\n\n")
            log_f.flush()

            proc = subprocess.Popen(
                cmd_list,
                cwd=None,      # cwd handled inside the shell command
                stdout=log_f,
                stderr=log_f,
            )

            try:
                rc = proc.wait()
            except KeyboardInterrupt:
                proc.terminate()
                raise

            log_f.write("\n" + "=" * 60 + "\n")
            log_f.write("Finished  : " + datetime.now().isoformat() + "\n")
            log_f.write("Exit code : " + str(rc) + "\n")
            log_f.write("Result    : " + ("SUCCESS" if rc == 0 else "FAILED") + "\n")
            log_f.write("=" * 60 + "\n")

        logger.info("Build exit code: " + str(rc))
        if rc != 0:
            logger.error("Build FAILED rc=" + str(rc) + " | log: " + build_log)
            return False, pre_build_time

        logger.info("Build SUCCESS | log: " + build_log)
        return True, pre_build_time

    except Exception as e:
        logger.error("Build subprocess error: " + str(e))
        return False, pre_build_time


# ----------------------------------------------------------------
# POST-BUILD MEMORY CLEANUP
# ----------------------------------------------------------------
def cleanup_memory(logger):
    """
    Force Python GC and sleep to let OS reclaim RAM after make release.

    Critical: without this, OS is still OOM when copy starts and kills it.
    gc.collect() drops Python references. Sleep gives Windows memory
    manager time to reclaim pages freed by gcc/ld processes.
    """
    logger.info("Post-build cleanup: gc.collect() ...")
    gc.collect()
    logger.info(
        "Post-build cleanup: sleeping " + str(POST_BUILD_SLEEP_SECONDS)
        + "s for OS to reclaim RAM ..."
    )
    time.sleep(POST_BUILD_SLEEP_SECONDS)
    logger.info("Cleanup done. Ready for copy.")


# ----------------------------------------------------------------
# FIND PRODUCED TGZ
# ----------------------------------------------------------------
def find_tgz(repo_dir, pre_build_time, logger):
    """
    Find the newest .tgz under repo_dir/release/ that was produced
    AFTER pre_build_time. Returns full path or None.
    """
    release_root = os.path.join(repo_dir, "release")
    if not os.path.isdir(release_root):
        logger.error("release/ directory not found in: " + repo_dir)
        return None

    logger.info("Searching for new .tgz in: " + release_root)
    best = None
    best_mtime = 0.0

    for root, dirs, files in os.walk(release_root):
        for fname in files:
            if not fname.endswith(".tgz"):
                continue
            full = os.path.join(root, fname)
            try:
                mtime = os.path.getmtime(full)
            except Exception:
                continue
            if mtime < pre_build_time:
                logger.debug("Skipping stale: " + fname)
                continue
            if mtime > best_mtime:
                best_mtime = mtime
                best = full

    if best:
        logger.info("Found TGZ: " + best)
    else:
        logger.error("No new .tgz found under " + release_root)
    return best
