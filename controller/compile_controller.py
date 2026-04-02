#!/usr/bin/env python3
"""
controller/compile_controller.py
==================================
Compile Controller

Bridges the CompileTab (View) and compilation_orchestrator.py (Model).

Responsibilities:
  - Receives user action from CompileTab._start_compile()
  - Resolves tester hostnames → (hostname, env) pairs via the tester registry
  - Fans out compile jobs in parallel threads (one per tester)
  - Calls back into BentoController → View on start / completion per tester

Mirrors the threading pattern used in CATFWMig._run_fw_mig():
  one Thread per tester, result relayed via GUI_ctrl callback.
"""

import os
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger("bento_app")

# ── Shared registry path (same as compilation_orchestrator.py / watcher_config.py)
_REGISTRY_PATH = r"P:\temp\BENTO\bento_testers.json"


class CompileController:
    """
    Bridges CompileTab ↔ compilation_orchestrator.py.

    Constructor args:
        master : BentoController  (for callbacks up to View)
        config : dict             (settings.json contents)
    """

    def __init__(self, master, config: dict):
        self._master  = master
        self._config  = config
        self._view    = None
        self._running = False   # True while any compile thread is alive
        logger.info("CompileController initialised.")

    # ──────────────────────────────────────────────────────────────────────
    # WIRING
    # ──────────────────────────────────────────────────────────────────────

    def set_view(self, view):
        """Receive View reference after two-way wiring in BentoController."""
        self._view = view

    # ──────────────────────────────────────────────────────────────────────
    # REGISTRY HELPERS
    # ──────────────────────────────────────────────────────────────────────

    def get_available_testers(self):
        """
        Load the tester registry and return a list of (hostname, env) tuples.
        Returns an empty list if the registry file is missing or unreadable.
        Used by CompileTab to populate the tester checkbox list.
        """
        try:
            registry_path = self._config.get("registry_path", _REGISTRY_PATH)
            if not os.path.exists(registry_path):
                logger.debug(f"DEBUG_REGISTRY_MISSING: {registry_path}")
                return []
            with open(registry_path, "r") as f:
                data = json.load(f)
            result = []
            for val in data.values():
                hostname = val.get("hostname", "")
                env      = val.get("env", "")
                if hostname and env:
                    result.append((hostname, env))
            return result
        except Exception as e:
            logger.error(f"CompileController.get_available_testers: {e}")
            return []

    def _get_env_for_hostname(self, hostname: str) -> str:
        """Look up the ENV token for a given hostname from the registry."""
        try:
            registry_path = self._config.get("registry_path", _REGISTRY_PATH)
            if not os.path.exists(registry_path):
                return ""
            with open(registry_path, "r") as f:
                data = json.load(f)
            for val in data.values():
                if val.get("hostname", "").upper() == hostname.upper():
                    return val.get("env", "")
        except Exception as e:
            logger.error(f"CompileController._get_env_for_hostname: {e}")
        return ""

    # ──────────────────────────────────────────────────────────────────────
    # ACTIVE TASK GUARD
    # ──────────────────────────────────────────────────────────────────────

    def is_running(self) -> bool:
        """Returns True while any compile thread is active."""
        return self._running

    # ──────────────────────────────────────────────────────────────────────
    # COMPILE DISPATCH
    # ──────────────────────────────────────────────────────────────────────

    def start_compile(self, source_dir: str, jira_key: str, shared_folder: str,
                      label: str, hostnames: list, labels: dict | None = None):
        """
        Called by CompileTab._start_compile().

        Fans out one compile thread per selected hostname, mirroring the
        ThreadPoolExecutor pattern already used in compilation_orchestrator
        .compile_tp_package_multi().

        Args:
            source_dir    : local path to TP repo
            jira_key      : e.g. TSESSD-1234
            shared_folder : P:\\temp\\BENTO or override
            label         : default TGZ label (passing / force_fail_1 / …)
            hostnames     : list of selected hostname strings
            labels        : optional dict mapping hostname → custom TGZ label
                            (used for multi-tester compiles with per-tester labels)
        """
        if self._running:
            logger.warning("CompileController: compile already in progress — ignoring request.")
            return

        # Resolve hostname → (hostname, env) targets
        targets = []
        for hostname in hostnames:
            env = self._get_env_for_hostname(hostname)
            if not env:
                logger.error(f"CompileController: no ENV found for hostname '{hostname}' — skipping.")
                continue
            targets.append((hostname, env))

        if not targets:
            logger.error("CompileController: no valid targets resolved — aborting.")
            return

        raw_zip_folder    = os.path.join(shared_folder, "RAW_ZIP")
        release_tgz_folder = os.path.join(shared_folder, "RELEASE_TGZ")

        self._running = True
        # Fire a background thread that fans out per-tester work
        threading.Thread(
            target=self._compile_all,
            args=(source_dir, jira_key, raw_zip_folder, release_tgz_folder, label, targets, labels),
            daemon=True,
            name="bento-compile-fanout",
        ).start()
        logger.info(f"CompileController: dispatched compile for {len(targets)} tester(s).")

    def _compile_all(self, source_dir, jira_key, raw_zip_folder,
                     release_tgz_folder, label, targets, labels=None):
        """
        Background thread — fans out one _compile_one() per tester in parallel.
        Called on completion of all testers to reset self._running.
        """
        # Resolve webhook URL for Teams notification
        webhook_url = self._config.get(
            "notifications", {}
        ).get("teams_webhook_url", "")

        def _one(hostname, env):
            # Use per-tester label if provided, otherwise fall back to default
            tester_label = (labels or {}).get(hostname, label)
            # Notify View: compile starting for this tester
            self._master.on_compile_started(hostname, env)
            result = self._compile_one(
                source_dir=source_dir,
                env=env,
                jira_key=jira_key,
                hostname=hostname,
                raw_zip_folder=raw_zip_folder,
                release_tgz_folder=release_tgz_folder,
                label=tester_label,
            )
            result["hostname"] = hostname
            result["env"]      = env
            # Notify View: compile finished for this tester
            self._master.on_compile_completed(hostname, env, result)

            # ── Teams notification for compilation result ──────────────
            try:
                from model.orchestrators.compilation_orchestrator import (
                    send_compile_teams_notification,
                )
                notify_enabled = self._config.get(
                    "notifications", {}
                ).get("notify_on_complete", True)
                notify_on_fail = self._config.get(
                    "notifications", {}
                ).get("notify_on_failure", True)

                should_notify = (
                    (result.get("status") == "success" and notify_enabled)
                    or (result.get("status") != "success" and notify_on_fail)
                )
                if should_notify:
                    send_compile_teams_notification(
                        jira_key    = jira_key,
                        hostname    = hostname,
                        env         = env,
                        status      = result.get("status", "unknown"),
                        detail      = result.get("detail", ""),
                        elapsed     = result.get("elapsed", 0),
                        tgz_file    = result.get("tgz_file", "") or "",
                        webhook_url = webhook_url,
                        log_callback = self._make_log_callback(hostname),
                    )
            except Exception as e:
                logger.warning(
                    f"CompileController: Teams notification failed "
                    f"(non-fatal) [{hostname}]: {e}"
                )

            return result

        try:
            with ThreadPoolExecutor(max_workers=len(targets)) as pool:
                futures = {pool.submit(_one, h, e): (h, e) for h, e in targets}
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        hostname, env = futures[future]
                        logger.error(f"CompileController: thread error [{hostname}]: {e}")
                        self._master.on_compile_completed(
                            hostname, env,
                            {"status": "failed", "detail": str(e), "elapsed": 0, "tgz_file": None}
                        )
        finally:
            self._running = False
            logger.info("CompileController: all compile threads complete.")

    def _compile_one(self, source_dir, env, jira_key, hostname,
                     raw_zip_folder, release_tgz_folder, label):
        """
        Single-tester compile.  Delegates entirely to compilation_orchestrator
        .compile_tp_package() — the Model layer.

        Returns the result dict from the orchestrator.
        """
        try:
            # Import here (not at top-level) so the controller layer never
            # directly depends on model internals at import time.
            from model.orchestrators.compilation_orchestrator import compile_tp_package

            log_callback = self._make_log_callback(hostname)

            result = compile_tp_package(
                source_dir=source_dir,
                env=env,
                jira_key=jira_key,
                hostname=hostname,
                log_callback=log_callback,
                raw_zip_folder=raw_zip_folder,
                release_tgz_folder=release_tgz_folder,
                label=label,
            )
            return result
        except Exception as e:
            logger.error(f"CompileController._compile_one [{hostname}]: {e}")
            return {"status": "failed", "detail": str(e), "elapsed": 0, "tgz_file": None}

    # ──────────────────────────────────────────────────────────────────────
    # HELPERS
    # ──────────────────────────────────────────────────────────────────────

    def _make_log_callback(self, hostname: str):
        """
        Returns a callable(str) that prefixes every log line with the
        hostname and pipes it to the View's log panel.
        """
        def _cb(msg: str):
            if self._view:
                self._view.context.log(f"[{hostname}] {msg}")
        return _cb
