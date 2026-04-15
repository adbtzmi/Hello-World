#!/usr/bin/env python3
"""
controller/result_controller.py
================================
Result Collection Controller — Task 2

Bridges CheckoutTab Section 6 (View) <-> ResultCollector (Model).

Responsibilities:
  - Start/stop background result collection polling
  - Relay progress updates from model to view (thread-safe via root.after)
  - Trigger manual collect/spool for individual MIDs
  - Send Teams notification on completion
"""

import os
import logging
import threading
from typing import Any

logger = logging.getLogger("bento_app")


class ResultController:
    """
    Controller for test result collection and monitoring.

    Follows the same pattern as CheckoutController / CompileController:
      - __init__ takes (context)
      - Instantiates ResultCollector (model) when user starts monitoring
      - Relays callbacks to view via context.root.after()
    """

    def __init__(self, context):
        self.context    = context
        self._collector: "Any" = None   # ResultCollector instance (set on start)
        self._running   = False
        logger.info("ResultController initialized.")

    def is_running(self) -> bool:
        return self._running

    # ──────────────────────────────────────────────────────────────────────
    # START / STOP
    # ──────────────────────────────────────────────────────────────────────

    def start_monitoring(
        self,
        mids_file:           str,
        target_path:         str   = "",
        site:                str   = "",
        machine_type:        str   = "",
        tester_hostname:     str   = "",
        poll_interval:       int   = 30,
        auto_collect:        bool  = True,
        auto_spool:          bool  = False,
        additional_patterns: list  | None = None,
        webhook_url:         str   = "",
        notify_teams:        bool  = True,
        ai_client            = None,
        jira_context:        dict  | None = None,
        test_scenarios:      str   | None = None,
        code_changes:        str   | None = None,
        impact_analysis:     str   | None = None,
    ):
        """
        Start background result collection monitoring.

        Args:
            mids_file:           Path to MIDs.txt file
            target_path:         Shared folder path for collected files
            site:                Site name (SINGAPORE, PENANG, etc.)
            machine_type:        Tester type (IBIR, MPT) — auto-detected if empty
            tester_hostname:     Remote tester hostname (e.g. "IBIR-0383")
            poll_interval:       Seconds between status polls
            auto_collect:        Auto-collect files when test completes
            auto_spool:          Auto-spool summary when test completes
            additional_patterns: Extra file glob patterns to collect
            webhook_url:         Teams webhook URL for notifications
            notify_teams:        Whether to send Teams notification
            ai_client:           AIGatewayClient for AI-powered validation & risk assessment
            jira_context:        JIRA issue context dict for AI analysis
            test_scenarios:      Test scenarios text for AI checkout validation
            code_changes:        Code changes text for AI checkout validation
            impact_analysis:     Impact analysis text for AI risk assessment
        """
        if self._running:
            self.context.log("⚠ Result collector is already running.")
            return

        if not mids_file or not os.path.exists(mids_file):
            self.context.log(f"✗ MIDs file not found: {mids_file}")
            return

        # Resolve webhook from context if not provided
        if not webhook_url:
            webhook_var = self.context.get_var("checkout_webhook_url")
            if webhook_var:
                webhook_url = webhook_var.get()

        # Resolve notify_teams from context
        notify_var = self.context.get_var("checkout_notify_teams")
        if notify_var is not None:
            notify_teams = notify_var.get()

        # Resolve AI client from context if not provided
        if ai_client is None:
            ai_client = getattr(self.context, 'ai_client', None)

        # Resolve JIRA context from workflow state if not provided
        if jira_context is None:
            workflow = getattr(self.context, 'workflow_state', {})
            if workflow:
                jira_context = workflow.get('jira_analysis', None)
                if test_scenarios is None:
                    test_scenarios = workflow.get('test_scenarios', None)
                if code_changes is None:
                    code_changes = workflow.get('code_changes', None)
                if impact_analysis is None:
                    impact_analysis = workflow.get('impact_analysis', None)

        self._running = True
        self.context.log(f"🚀 Starting result collection monitor...")
        if ai_client:
            self.context.log(f"   🤖 AI-powered validation & risk assessment enabled")

        # Import here to avoid circular imports
        from model.orchestrators.result_collector import ResultCollector

        self._collector = ResultCollector(
            mids_file           = mids_file,
            target_path         = target_path,
            site                = site,
            machine_type        = machine_type,
            tester_hostname     = tester_hostname,
            poll_interval       = poll_interval,
            auto_collect        = auto_collect,
            auto_spool          = auto_spool,
            additional_patterns = additional_patterns or [],
            webhook_url         = webhook_url,
            notify_teams        = notify_teams,
            ai_client           = ai_client,
            jira_context        = jira_context,
            test_scenarios      = test_scenarios,
            code_changes        = code_changes,
            impact_analysis     = impact_analysis,
            log_callback        = self._log_from_thread,
            progress_callback   = self._on_progress,
            completion_callback = self._on_completion,
        )
        self._collector.start()

    def stop_monitoring(self):
        """Stop the background result collection."""
        if self._collector and self._running:
            self._collector.stop()
            self.context.log("⚠ Stop signal sent to result collector.")

    # ──────────────────────────────────────────────────────────────────────
    # MANUAL ACTIONS
    # ──────────────────────────────────────────────────────────────────────

    def collect_single(self, mid: str):
        """Manually collect files for a single MID."""
        if not self._collector:
            self.context.log("✗ No active result collector.")
            return

        def _do():
            self._log_from_thread(f"📁 Manually collecting files for {mid}...")
            success = self._collector.collect_single(mid)
            if success:
                self._log_from_thread(f"✓ Files collected for {mid}")
            else:
                self._log_from_thread(f"✗ Failed to collect files for {mid}")
            # Refresh progress
            self._on_progress(
                self._collector.get_summary(),
                {m: e.to_dict() for m, e in self._collector.entries.items()},
            )

        threading.Thread(target=_do, daemon=True).start()

    def spool_single(self, mid: str):
        """Manually spool summary for a single MID."""
        if not self._collector:
            self.context.log("✗ No active result collector.")
            return

        def _do():
            self._log_from_thread(f"📊 Manually spooling summary for {mid}...")
            success = self._collector.spool_single(mid)
            if success:
                self._log_from_thread(f"✓ Summary spooled for {mid}")
            else:
                self._log_from_thread(f"✗ Failed to spool summary for {mid}")

        threading.Thread(target=_do, daemon=True).start()

    def refresh_status(self):
        """Force a status refresh (re-check all MIDs)."""
        if not self._collector:
            self.context.log("✗ No active result collector.")
            return

        def _do():
            self._log_from_thread("🔄 Refreshing test status...")
            self._collector._check_all_statuses()
            self._on_progress(
                self._collector.get_summary(),
                {m: e.to_dict() for m, e in self._collector.entries.items()},
            )

        threading.Thread(target=_do, daemon=True).start()

    def get_entries(self) -> dict:
        """Return current MID entries as dicts (for GUI)."""
        if not self._collector:
            return {}
        return {m: e.to_dict() for m, e in self._collector.entries.items()}

    def get_summary(self) -> dict:
        """Return current summary dict."""
        if not self._collector:
            return {}
        return self._collector.get_summary()

    # ──────────────────────────────────────────────────────────────────────
    # THREAD-SAFE CALLBACKS
    # ──────────────────────────────────────────────────────────────────────

    def _log_from_thread(self, message: str):
        """Thread-safe log: schedule on Tk main thread."""
        try:
            self.context.root.after(0, lambda: self.context.log(message))
        except Exception:
            logger.info(message)

    def _on_progress(self, summary: dict, entries: dict):
        """
        Called by ResultCollector from background thread.
        Relays to CheckoutTab Section 6 via root.after() for thread safety.
        """
        def _update():
            controller = self.context.controller
            if controller and hasattr(controller, "_view"):
                view = controller._view
                checkout_tab = getattr(view, "checkout_tab", None)
                if checkout_tab and hasattr(checkout_tab, "on_rc_progress_update"):
                    checkout_tab.on_rc_progress_update(summary, entries)

        try:
            self.context.root.after(0, _update)
        except Exception:
            pass

    def _on_completion(self, summary: dict):
        """
        Called by ResultCollector when monitoring ends (all complete or stopped).
        """
        self._running = False

        def _update():
            controller = self.context.controller
            if controller and hasattr(controller, "_view"):
                view = controller._view
                checkout_tab = getattr(view, "checkout_tab", None)
                if checkout_tab and hasattr(checkout_tab, "on_rc_collection_complete"):
                    checkout_tab.on_rc_collection_complete(summary)

                # Show popup notification
                from tkinter import messagebox
                all_done = summary.get("all_done", False)
                passed   = summary.get("passed", 0)
                failed   = summary.get("failed", 0)
                total    = summary.get("total", 0)

                if all_done:
                    msg = (
                        f"All {total} test(s) completed!\n\n"
                        f"Passed: {passed}\n"
                        f"Failed: {failed}\n"
                        f"Machine: {summary.get('machine', '')}\n"
                        f"Site: {summary.get('site', '')}"
                    )
                    if failed > 0:
                        messagebox.showwarning("Test Results", msg)
                    else:
                        messagebox.showinfo("Test Results", msg)
                else:
                    messagebox.showinfo(
                        "Result Collector",
                        "Result collection monitoring stopped."
                    )

        try:
            self.context.root.after(0, _update)
        except Exception:
            pass
