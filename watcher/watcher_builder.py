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

CHANGES (Recommendations Applied):
    [P1] BUILD_TIMEOUT_SECONDS is now ENFORCED in proc.wait(timeout=...).
         Previously BUILD_TIMEOUT_SECONDS was defined in config but never
         passed to proc.wait() — meaning the build could hang FOREVER.
         Now: if the build exceeds the timeout, the process is terminated
         (SIGTERM), then force-killed (SIGKILL) after 5s if still alive,
         and the build is marked as FAILED with a TIMEOUT log entry.

    [P2] Heartbeat thread added via _heartbeat_logger().
         A daemon thread logs "Build still running... elapsed=Xs (PID=Y)"
         every 120 seconds while the build subprocess is alive.
         This prevents false 'hung' assumptions during long builds
         (e.g. make release_supermicro) and gives visibility in the log.
         The thread is daemon=True so it never blocks watcher shutdown.
"""
from __future__ import print_function
import os
import gc
import time
import zipfile
import threading
import subprocess
from datetime import datetime

from watcher_config import (
    BUILD_TIMEOUT_SECONDS,
    POST_BUILD_SLEEP_SECONDS,
    PRE_BUILD_SLEEP_SECONDS,
    LOG_DIR,
)


# ----------------------------------------------------------------
def _heartbeat_logger(proc, logger, interval=120):
    """
    [P2] Background daemon thread: logs a 'still running' heartbeat
    message every `interval` seconds while the build subprocess is alive.

    WHY THIS EXISTS:
        make release / make release_supermicro can run for 20-30 minutes.
        Without heartbeat, a silent log gives no way to distinguish
        "still building normally" from "hung / deadlocked / network frozen".
        This thread makes the watcher log continuously active during builds.

    Exits automatically (and silently) when proc.poll() is not None
    (i.e. the process has finished — success, failure, or timeout-kill).

    Args:
        proc     : subprocess.Popen object of the running build
        logger   : standard Python logger
        interval : seconds between heartbeat log entries (default 120s)
    """
    elapsed = 0
    while proc.poll() is None:
        time.sleep(interval)
        elapsed += interval
        # Double-check after sleep — process may have finished during sleep
        if proc.poll() is None:
            logger.info(
                "Build still running... elapsed=" + str(elapsed) + "s"
                + " (PID=" + str(proc.pid) + ")"
            )


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
def cleanup_memory(logger):
    """
    Force Python GC and sleep to let OS reclaim RAM after make release.

    Called both BEFORE build (in run_build) and AFTER build (in process_zip).
    Critical: without post-build cleanup, OS is still OOM when copy starts.
    gc.collect() drops Python references. Sleep gives Windows memory
    manager time to reclaim pages freed by gcc/ld/tar processes.
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
def run_build(repo_dir, build_cmd, zip_name, logger):
    """
    Run make release using Cygwin bash login shell.

    Cygwin bash -lc is required because make/gcc are Cygwin tools
    and only exist in the Cygwin PATH (/usr/bin), not Windows PATH.
    cmd.exe cannot find them.

    [P1] BUILD_TIMEOUT_SECONDS is now enforced via proc.wait(timeout=...).
         If the build hangs beyond the timeout:
           1. proc.terminate() is called (SIGTERM / graceful)
           2. After 5s, if still alive: proc.kill() (SIGKILL / force)
           3. Log is finalized with TIMEOUT marker
           4. Returns (False, pre_build_time) — treated as permanent fail

    [P2] Heartbeat thread (_heartbeat_logger) is started immediately after
         proc is launched, logging "still running" every 120s.

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

    # ── Resolve shell: prefer Cygwin bash, fallback to cmd.exe ──────────────
    cygwin_repo = repo_dir  # default: same path
    bash_exe    = r"C:\cygwin64\bin\bash.exe"

    if os.path.exists(bash_exe):
        # Convert Windows path to Cygwin path format
        # e.g. C:\BENTO\repo -> /cygdrive/c/BENTO/repo
        drive      = repo_dir[0].lower()
        rest       = repo_dir[2:].replace("\\", "/")
        cygwin_repo = "/cygdrive/" + drive + rest

        # -l = login shell (loads /etc/profile, sets PATH to include /usr/bin)
        # -c = run this command string
        shell_cmd  = "cd " + cygwin_repo + " && " + build_cmd
        cmd_list   = [bash_exe, "-lc", shell_cmd]
        shell_type = "Cygwin bash -lc"
    else:
        # Fallback to cmd.exe - only works if make.exe is in Windows PATH
        shell_cmd  = "cd " + repo_dir + " && " + build_cmd
        cmd_list   = ["cmd.exe", "/c", shell_cmd]
        shell_type = "cmd.exe (fallback - make must be in Windows PATH)"
        logger.warning("Cygwin bash not found! Falling back to cmd.exe")
        logger.warning("If make is not in Windows PATH, build will fail")

    logger.info("Shell      : " + shell_type)
    logger.info("Cygwin dir : " + cygwin_repo)
    logger.info("Build cmd  : " + build_cmd)
    logger.info("Build log  : " + build_log)
    logger.info("Timeout    : " + str(BUILD_TIMEOUT_SECONDS) + "s")

    # ── Pre-build memory cleanup ─────────────────────────────────────────────
    # extraction before the compiler + tar start competing for memory.
    logger.info("Pre-build cleanup: gc.collect() ...")
    gc.collect()
    logger.info(
        "Pre-build cleanup: sleeping " + str(PRE_BUILD_SLEEP_SECONDS)
        + "s to free RAM before build ..."
    )
    time.sleep(PRE_BUILD_SLEEP_SECONDS)
    logger.info("Pre-build cleanup done. Starting build ...")

    # ── BUILD ────────────────────────────────────────────────────────────────
    try:
        with open(build_log, "w") as log_f:
            log_f.write("BENTO Build Log\n")
            log_f.write("Started   : " + datetime.now().isoformat() + "\n")
            log_f.write("Shell     : " + shell_type + "\n")
            log_f.write("Repo dir  : " + repo_dir + "\n")
            log_f.write("Cygwin    : " + cygwin_repo + "\n")
            log_f.write("Command   : " + shell_cmd + "\n")
            log_f.write("Timeout   : " + str(BUILD_TIMEOUT_SECONDS) + "s\n")
            log_f.write("=" * 60 + "\n\n")
            log_f.flush()

            proc = subprocess.Popen(
                cmd_list,
                cwd=None,       # cwd handled inside the shell command
                stdout=log_f,
                stderr=log_f,
            )

            # ── [P2] Start heartbeat thread ──────────────────────────────────
            # Daemon thread: logs "still running" every 120s while proc alive.
            # Exits automatically when proc finishes (any reason).
            # daemon=True ensures it never blocks watcher shutdown.
            heartbeat = threading.Thread(
                target=_heartbeat_logger,
                args=(proc, logger, 120),
                daemon=True,
                name="build-heartbeat-" + zip_name,
            )
            heartbeat.start()
            logger.info(
                "Heartbeat thread started (PID=" + str(proc.pid)
                + ", interval=120s)"
            )

            # ── [P1] Enforced timeout in proc.wait() ─────────────────────────
            # Previously: rc = proc.wait()  ← no timeout, could hang FOREVER
            # Now:        proc.wait(timeout=BUILD_TIMEOUT_SECONDS) is enforced
            try:
                rc = proc.wait(timeout=BUILD_TIMEOUT_SECONDS)

            except subprocess.TimeoutExpired:
                # ── Timeout handling ─────────────────────────────────────────
                logger.error(
                    "Build TIMEOUT after " + str(BUILD_TIMEOUT_SECONDS) + "s"
                    + " | Terminating PID=" + str(proc.pid)
                )

                # Step 1: Graceful terminate (SIGTERM)
                proc.terminate()

                # Step 2: Wait up to 5s for graceful exit
                time.sleep(5)

                # Step 3: Force kill if still alive (SIGKILL)
                if proc.poll() is None:
                    logger.error(
                        "Process did not terminate after SIGTERM — force killing PID="
                        + str(proc.pid)
                    )
                    proc.kill()

                # Step 4: Write timeout marker to build log
                log_f.write("\n" + "=" * 60 + "\n")
                log_f.write("TIMEOUT   : Build exceeded " + str(BUILD_TIMEOUT_SECONDS) + "s\n")
                log_f.write("Killed    : " + datetime.now().isoformat() + "\n")
                log_f.write("Result    : FAILED (TIMEOUT)\n")
                log_f.write("=" * 60 + "\n")
                log_f.flush()

                logger.error(
                    "Build TIMEOUT | log: " + build_log
                )
                return False, pre_build_time

            except KeyboardInterrupt:
                # Watcher is being shut down — terminate child and re-raise
                proc.terminate()
                raise

            # ── Build finished within timeout ─────────────────────────────────
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

    # ── POST-BUILD MEMORY CLEANUP ────────────────────────────────────────────
    # Note: cleanup_memory() is called by process_zip() in watcher_main.py
    # AFTER run_build() returns, not here — so it runs only on success paths.


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
    best       = None
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
                best       = full

    if best:
        logger.info("Found TGZ: " + best)
    else:
        logger.error("No new .tgz found under " + release_root)
    return best
    