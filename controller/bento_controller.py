#!/usr/bin/env python3
"""
controller/bento_controller.py
================================
BENTO Master Controller

Mirrors the top-level wiring role of CAT.py in the C.A.T. project:
  - Owns all sub-controllers (CompileController, CheckoutController, JiraController)
  - Holds a reference to the View (set by main.py after construction)
  - Provides the two-way bridge between View and Model layers
  - Exposes has_active_tasks() for the View's smart-close guard

Sub-controllers are instantiated here so main.py stays a clean entry point
(exactly as CatSAP / CatDB / CATFWMig are instantiated in app.py then passed
to CatGUI.SetController()).
"""

import logging
from singleton_meta import SingletonMeta
from controller.compile_controller  import CompileController
from controller.checkout_controller import CheckoutController
from controller.jira_controller     import JiraController

logger = logging.getLogger("bento_app")


class BentoController(metaclass=SingletonMeta):
    """
    Master controller.  One instance per application lifetime (Singleton).

    Attributes:
        compile_controller  : CompileController
        checkout_controller : CheckoutController
        jira_controller     : JiraController
        _view               : BentoApp  (set after construction via set_view())
    """

    def __init__(self, config: dict, logger_instance=None):
        _logger = logger_instance or logger
        _logger.info("BentoController initialising.")

        self.config = config
        self._view  = None   # wired in main.py after BentoApp is constructed

        # ── Sub-controllers (mirrors CAT.py class instantiation pattern) ──
        self.compile_controller  = CompileController(master=self, config=config)
        self.checkout_controller = CheckoutController(master=self, config=config)
        self.jira_controller     = JiraController(master=self, config=config)

        _logger.info("BentoController ready.")

    # ──────────────────────────────────────────────────────────────────────
    # WIRING
    # ──────────────────────────────────────────────────────────────────────

    def set_view(self, view):
        """
        Two-way wire: give every sub-controller a reference to the View.
        Mirrors GUI_ctrl.SetController() in C.A.T.
        """
        self._view = view

        # Inject view reference + controller reference into AppContext
        # so tabs can call self.context.controller.<sub_controller>.<method>()
        view.context.controller = self
        view.context.analyzer   = self.jira_controller.analyzer

        # Propagate view down to sub-controllers
        self.compile_controller.set_view(view)
        self.checkout_controller.set_view(view)
        self.jira_controller.set_view(view)

        logger.info("BentoController: View wired.")

    # ──────────────────────────────────────────────────────────────────────
    # ACTIVE TASK GUARD (used by BentoApp._on_close)
    # ──────────────────────────────────────────────────────────────────────

    def has_active_tasks(self) -> bool:
        """
        Returns True if any background task is currently running.
        Checked by the View before allowing the window to close.
        Mirrors C.A.T.'s background-task-management guard.
        """
        return (
            self.compile_controller.is_running()
            or self.checkout_controller.is_running()
            or self.jira_controller.is_running()
        )

    # ──────────────────────────────────────────────────────────────────────
    # COMPILE CALLBACKS (called by CompileController → forwarded to View)
    # ──────────────────────────────────────────────────────────────────────

    def on_compile_started(self, hostname: str, env: str):
        """Relay compile-started event from model up to the View."""
        if self._view:
            self._view.compile_started(hostname, env)

    def on_compile_completed(self, hostname: str, env: str, result: dict):
        """Relay compile-completed event from model up to the View."""
        if self._view:
            self._view.compile_completed(hostname, env, result)

    # ──────────────────────────────────────────────────────────────────────
    # CHECKOUT CALLBACKS (called by CheckoutController → forwarded to View)
    # ──────────────────────────────────────────────────────────────────────

    def on_checkout_started(self, hostname: str):
        """Relay checkout-started event from model up to the View."""
        if self._view:
            self._view.checkout_started(hostname)

    def on_checkout_progress(self, hostname: str, phase: str):
        """Relay mid-run phase update from model up to the View."""
        if self._view:
            self._view.checkout_progress(hostname, phase)

    def on_checkout_completed(self, hostname: str, result: dict):
        """Relay checkout-completed event from model up to the View."""
        if self._view:
            self._view.checkout_completed(hostname, result)

    # ──────────────────────────────────────────────────────────────────────
    # JIRA CALLBACKS (called by JiraController → forwarded to View)
    # ──────────────────────────────────────────────────────────────────────

    def on_jira_analysis_completed(self, result: dict):
        """Relay JIRA analysis result from model up to the View."""
        if self._view:
            self._view.jira_analysis_completed(result)
