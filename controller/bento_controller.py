#!/usr/bin/env python3
"""
controller/bento_controller.py
================================
BENTO Master Controller — Phase 2

Key fix vs agent-generated version:
  - __init__ takes config only (NOT context)
  - context is injected via set_view() AFTER AppContext exists
  - This eliminates the chicken-and-egg problem where
    BentoController was created before BentoApp/AppContext

Sub-controllers that need context receive it in set_view().
Sub-controllers that need config receive it in __init__.
"""

import logging
from singleton_meta import SingletonMeta
from controller.compile_controller  import CompileController
from controller.checkout_controller import CheckoutController

logger = logging.getLogger("bento_app")


class BentoController(metaclass=SingletonMeta):
    """
    Master controller. One instance per application lifetime (Singleton).
    """

    def __init__(self, config: dict, logger_instance=None):
        _logger = logger_instance or logger
        _logger.info("BentoController initialising.")

        self.config  = config
        self._view   = None
        self._context = None

        # Compile and Checkout controllers are config-based
        # (they receive context via set_view)
        self.compile_controller  = CompileController(master=self, config=config)
        self.checkout_controller = CheckoutController(master=self, config=config)

        # Phase 2 controllers — created in set_view() once context exists
        self.workflow_controller = None
        self.chat_controller     = None
        self.jira_controller     = None
        self.repo_controller     = None
        self.test_controller     = None
        self.force_fail_controller = None
        self.result_controller   = None   # Task 2: result collection
        self.pr_review_controller = None  # PR Review pipeline

        _logger.info("BentoController ready (Phase 2 controllers pending set_view).")

    # ──────────────────────────────────────────────────────────────────────
    # WIRING — called by main.py after AppContext exists
    # ──────────────────────────────────────────────────────────────────────

    def set_view(self, view):
        """
        Wire view AND context into all sub-controllers.
        Phase 2 controllers are instantiated here (after context exists).
        """
        self._view    = view
        self._context = view.context

        # Wire context.controller so tabs can reach us
        view.context.controller = self

        # ── Instantiate Phase 2 controllers in dependency order ───────────
        from controller.workflow_controller import WorkflowController
        from controller.chat_controller     import ChatController
        from controller.jira_controller     import JiraController
        from controller.repo_controller     import RepoController
        from controller.test_controller     import TestController
        from controller.config_controller   import ConfigController
        from controller.credential_controller import CredentialController
        from controller.validation_controller import ValidationController

        # No dependencies
        self.workflow_controller = WorkflowController(view.context)
        self.chat_controller     = ChatController(view.context)
        self.config_controller   = ConfigController(view.context)
        self.credential_controller = CredentialController(view.context)

        # Depends on workflow + chat
        self.jira_controller = JiraController(
            view.context,
            self.workflow_controller,
            self.chat_controller
        )

        # Depends on workflow
        self.repo_controller = RepoController(
            view.context,
            self.workflow_controller
        )

        # Depends on workflow + chat
        self.test_controller = TestController(
            view.context,
            self.workflow_controller,
            self.chat_controller
        )

        # Depends on workflow + chat
        self.validation_controller = ValidationController(
            view.context,
            self.workflow_controller,
            self.chat_controller
        )

        # Phase 3C: Implementation controller
        from controller.implementation_controller import ImplementationController
        self.implementation_controller = ImplementationController(
            view.context,
            self.workflow_controller,
            self.chat_controller
        )

        # Force Fail controller (depends on workflow + chat)
        from controller.force_fail_controller import ForceFailController
        self.force_fail_controller = ForceFailController(
            view.context,
            self.workflow_controller,
            self.chat_controller
        )

        # Phase 3D: Full workflow orchestrator
        from controller.full_workflow_controller import FullWorkflowController
        self.full_workflow_controller = FullWorkflowController(
            view.context,
            self.workflow_controller,
            self.chat_controller,
            self.jira_controller,
            self.repo_controller,
            self.test_controller,
            self.implementation_controller,
            self.validation_controller
        )

        # Task 2: Result collection controller
        from controller.result_controller import ResultController
        self.result_controller = ResultController(view.context)

        # PR Review pipeline controller (depends on workflow + chat)
        from controller.pr_review_controller import PRReviewController
        self.pr_review_controller = PRReviewController(
            view.context,
            self.workflow_controller,
            self.chat_controller
        )

        # Wire view into compile/checkout controllers
        self.compile_controller.set_view(view)
        self.checkout_controller.set_view(view)

        logger.info("BentoController: all controllers wired (Phase 3C/3D + Task 2 + PR Review complete).")

    # ──────────────────────────────────────────────────────────────────────
    # ACTIVE TASK GUARD
    # ──────────────────────────────────────────────────────────────────────

    def has_active_tasks(self) -> bool:
        checks = [
            self.compile_controller.is_running(),
            self.checkout_controller.is_running(),
        ]
        if self.jira_controller:
            checks.append(self.jira_controller.is_running())
        if self.repo_controller:
            checks.append(self.repo_controller.is_running())
        if self.test_controller:
            checks.append(self.test_controller.is_running())
        if self.validation_controller:
            checks.append(self.validation_controller.is_running())
        if hasattr(self, 'implementation_controller') and self.implementation_controller:
            checks.append(self.implementation_controller.is_running())
        if hasattr(self, 'force_fail_controller') and self.force_fail_controller:
            checks.append(self.force_fail_controller.is_running())
        if hasattr(self, 'full_workflow_controller') and self.full_workflow_controller:
            checks.append(self.full_workflow_controller.is_running())
        if hasattr(self, 'result_controller') and self.result_controller:
            checks.append(self.result_controller.is_running())
        if hasattr(self, 'pr_review_controller') and self.pr_review_controller:
            checks.append(self.pr_review_controller.is_running())
        return any(checks)

    # ──────────────────────────────────────────────────────────────────────
    # COMPILE CALLBACKS
    # ──────────────────────────────────────────────────────────────────────

    def on_compile_started(self, hostname: str, env: str):
        if self._view:
            self._view.compile_started(hostname, env)

    def on_compile_completed(self, hostname: str, env: str, result: dict):
        if self._view:
            self._view.compile_completed(hostname, env, result)

    # ──────────────────────────────────────────────────────────────────────
    # CHECKOUT CALLBACKS
    # ──────────────────────────────────────────────────────────────────────

    def on_checkout_started(self, hostname: str):
        if self._view:
            self._view.checkout_started(hostname)

    def on_checkout_progress(self, hostname: str, phase: str):
        if self._view:
            self._view.checkout_progress(hostname, phase)

    def on_checkout_completed(self, hostname: str, result: dict):
        if self._view:
            self._view.checkout_completed(hostname, result)

    def on_xml_generation_completed(self, hostname: str, result: dict):
        if self._view:
            self._view.xml_generation_completed(hostname, result)

