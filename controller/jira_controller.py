#!/usr/bin/env python3
"""
controller/jira_controller.py
================================
JIRA Controller

Bridges the HomeTab (View) and jira_analyzer.py (Model).

Responsibilities:
  - Receives the "Start Full Analysis Workflow" action from HomeTab
  - Delegates to JiraAnalyzer (model) in a background thread
  - Relays results back to BentoController → View on completion

Mirrors the threading approach used in CATFWMig.run_fw_mig_thread().
"""

import logging
import threading

logger = logging.getLogger("bento_app")


class JiraController:
    """
    Bridges HomeTab ↔ jira_analyzer.py.

    Constructor args:
        master : BentoController  (for callbacks up to View)
        config : dict             (settings.json contents)
    """

    def __init__(self, master, config: dict):
        self._master   = master
        self._config   = config
        self._view     = None
        self._running  = False
        self.analyzer  = None   # lazy-loaded on first use
        logger.info("JiraController initialised.")

    # ──────────────────────────────────────────────────────────────────────
    # WIRING
    # ──────────────────────────────────────────────────────────────────────

    def set_view(self, view):
        """Receive View reference after two-way wiring in BentoController."""
        self._view = view

    # ──────────────────────────────────────────────────────────────────────
    # ACTIVE TASK GUARD
    # ──────────────────────────────────────────────────────────────────────

    def is_running(self) -> bool:
        """Returns True while a JIRA analysis thread is active."""
        return self._running

    # ──────────────────────────────────────────────────────────────────────
    # WORKFLOW DISPATCH
    # ──────────────────────────────────────────────────────────────────────

    def start_workflow(self, issue_key: str):
        """
        Called by HomeTab._start_workflow().
        Launches analysis in a background thread so the GUI stays responsive.
        Mirrors CATFWMig._run_fw_mig() → threading.Thread pattern.
        """
        if self._running:
            logger.warning("JiraController: analysis already in progress — ignoring request.")
            return

        self._running = True
        if self._view:
            self._view.context.log(f"⚙ Starting JIRA analysis: {issue_key} …")

        threading.Thread(
            target=self._run_analysis,
            args=(issue_key,),
            daemon=True,
            name=f"bento-jira-{issue_key}",
        ).start()
        logger.info(f"JiraController: dispatched analysis for {issue_key}.")

    def _run_analysis(self, issue_key: str):
        """
        Background thread: runs jira_analyzer pipeline for the given issue.
        On completion calls back via BentoController → View.
        """
        try:
            analyzer = self._get_analyzer()
            if analyzer is None:
                raise RuntimeError("JiraAnalyzer could not be initialised — check settings.json.")

            result = analyzer.analyze(issue_key)
            result["issue_key"] = issue_key

            logger.info(f"JiraController: analysis complete for {issue_key}.")
            self._master.on_jira_analysis_completed(result)

        except Exception as e:
            logger.error(f"JiraController._run_analysis [{issue_key}]: {e}")
            if self._view:
                self._view.context.log(f"✗ JIRA analysis failed: {e}")
        finally:
            self._running = False

    # ──────────────────────────────────────────────────────────────────────
    # ANALYZER LAZY INIT
    # ──────────────────────────────────────────────────────────────────────

    def _get_analyzer(self):
        """
        Lazy-load and cache the JiraAnalyzer instance.
        Importing at call time keeps the controller layer decoupled at
        module load (same pattern as compile / checkout controllers).
        """
        if self.analyzer is not None:
            return self.analyzer
        try:
            from model.analyzers.jira_analyzer import JiraAnalyzer
            self.analyzer = JiraAnalyzer(config=self._config)
            logger.info("JiraController: JiraAnalyzer initialised.")
            return self.analyzer
        except Exception as e:
            logger.error(f"JiraController._get_analyzer: {e}")
            return None
