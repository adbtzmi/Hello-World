# -*- coding: utf-8 -*-
"""
controller/force_fail_controller.py
=====================================
Force Fail Controller

Bridges the ImplementationTab (View) and ForceFailGenerator (Model).

Responsibilities:
  - Receives user action from the compile sub-tab's force-fail section
  - Loads workflow data (JIRA analysis, test scenarios, repo index)
  - Calls ForceFailGenerator to produce AI-generated diffs
  - Opens interactive chat for user review/approval
  - On approval, creates patched repo and delegates to CompileController
  - Saves results to workflow file
  - Cleans up temp repo after compilation

Follows the same threading pattern as ImplementationController and TestController.
"""

import logging
import threading
from typing import Optional, List, Dict

logger = logging.getLogger("bento_app")


class ForceFailController:
    """
    Bridges ImplementationTab <-> ForceFailGenerator.

    Constructor args:
        context       : AppContext
        workflow_ctrl : WorkflowController
        chat_ctrl     : ChatController
    """

    def __init__(self, context, workflow_ctrl, chat_ctrl):
        self.context = context
        self.workflow = workflow_ctrl
        self.chat = chat_ctrl
        self.analyzer = context.analyzer
        self._running = False
        self._generator = None       # Lazy-init ForceFailGenerator
        self._current_cases = []     # Last generated force-fail cases
        self._patched_repo = None    # Path to current patched repo (for cleanup)
        logger.info("ForceFailController initialised.")

    def is_running(self):
        return self._running

    @property
    def current_cases(self):
        """Return the last generated force-fail cases."""
        return self._current_cases

    # ──────────────────────────────────────────────────────────────────────
    # LAZY INIT
    # ──────────────────────────────────────────────────────────────────────

    def _ensure_generator(self):
        """Lazy-initialise the ForceFailGenerator."""
        if self._generator is None:
            from model.analyzers.force_fail_generator import ForceFailGenerator
            ff_config = self.context.config.get("force_fail", {})
            self._generator = ForceFailGenerator(
                analyzer=self.analyzer,
                config=ff_config,
            )
            self._generator.set_log_callback(self.context.log)
        return self._generator

    # ──────────────────────────────────────────────────────────────────────
    # GENERATE FORCE-FAIL CASES
    # ──────────────────────────────────────────────────────────────────────

    def generate_force_fail(self, issue_key, repo_path, callback=None):
        """
        Generate force-fail code changes using AI.

        Args:
            issue_key : JIRA issue key (e.g. TSESSD-1234)
            repo_path : Local path to the TP repository
            callback  : Optional callback(result_dict) on completion
        """
        if self._running:
            logger.warning("ForceFailController: already running — ignoring request.")
            return

        self._running = True
        threading.Thread(
            target=self._generate_thread,
            args=(issue_key, repo_path, callback),
            daemon=True,
            name="bento-ff-generate",
        ).start()

    def _generate_thread(self, issue_key, repo_path, callback):
        """Background thread for force-fail generation."""
        try:
            self._log(f"\n[Generating Force Fail Cases for {issue_key}]")

            # Initialize workflow
            self.workflow.init_workflow_file(issue_key)

            # Check for cached force-fail cases
            existing_ff = self.workflow.get_workflow_step("FORCE_FAIL_CASES")
            if existing_ff:
                self._log("✓ Found existing force-fail cases in workflow")
                from model.analyzers.force_fail_generator import ForceFailGenerator
                self._current_cases = ForceFailGenerator.cases_from_json(existing_ff)
                if self._current_cases:
                    self._callback(callback, {
                        "success": True,
                        "cases": self._current_cases,
                        "from_cache": True,
                    })
                    return

            # Load JIRA analysis from workflow
            jira_analysis = self.workflow.get_workflow_step("JIRA_ANALYSIS")
            if not jira_analysis:
                error_msg = (
                    "JIRA analysis not found in workflow.\n\n"
                    "Please run the JIRA analysis step first (Home tab → Analyze)."
                )
                self._log(f"✗ {error_msg}")
                self._callback(callback, {"success": False, "error": error_msg})
                return

            # Load test scenarios from workflow
            test_scenarios = self.workflow.get_workflow_step("TEST_SCENARIOS")
            if not test_scenarios:
                self._log("⚠ Test scenarios not found in workflow — "
                          "AI will generate force-fail cases from JIRA analysis only")
                test_scenarios = ""

            # Generate force-fail diffs
            generator = self._ensure_generator()
            cases = generator.generate_force_fail_diffs(
                jira_analysis=jira_analysis,
                test_scenarios=test_scenarios,
                repo_path=repo_path,
            )

            if not cases:
                self._callback(callback, {
                    "success": False,
                    "error": "AI did not generate any force-fail cases. "
                             "Try running test scenario generation first.",
                })
                return

            self._current_cases = cases

            # Open interactive chat for review
            from model.analyzers.force_fail_generator import ForceFailGenerator as FFG
            display_text = FFG.cases_to_display(cases)

            self._log(f"✓ Generated {len(cases)} force-fail case(s) — opening review chat...")
            self.context.root.after(100, lambda: self._open_review_chat(
                issue_key, display_text, callback
            ))

        except Exception as e:
            logger.error(f"ForceFailController._generate_thread error: {e}")
            self._log(f"✗ Error generating force-fail cases: {e}")
            self._callback(callback, {"success": False, "error": str(e)})
        finally:
            self._running = False

    def _open_review_chat(self, issue_key, display_text, callback):
        """Open interactive chat for user to review force-fail diffs."""
        if self.chat:
            self.chat.open_interactive_chat(
                issue_key=issue_key,
                step_name="Force Fail Cases",
                initial_content=display_text,
                finalize_callback=lambda: self._on_cases_approved(issue_key, callback),
            )
        # Also fire the callback with the generated cases
        if callback:
            self._callback(callback, {
                "success": True,
                "cases": self._current_cases,
                "from_cache": False,
            })

    def _on_cases_approved(self, issue_key, callback=None):
        """Called when user approves force-fail cases in the chat."""
        self._log(f"✓ Force-fail cases approved for {issue_key}")

        # Save to workflow
        from model.analyzers.force_fail_generator import ForceFailGenerator as FFG
        cases_json = FFG.cases_to_json(self._current_cases)
        self.workflow.save_workflow_step("FORCE_FAIL_CASES", cases_json)
        self._log("✓ Force-fail cases saved to workflow")

    # ──────────────────────────────────────────────────────────────────────
    # COMPILE FORCE-FAIL TGZ
    # ──────────────────────────────────────────────────────────────────────

    def compile_force_fail(
        self,
        issue_key: str,
        repo_path: str,
        shared_folder: str,
        hostnames: list,
        label: str = "force_fail_1",
        labels: Optional[Dict] = None,
        callback=None,
    ):
        """
        Create a patched repo and compile it as a force-fail TGZ.

        Args:
            issue_key     : JIRA issue key
            repo_path     : Original TP repo path
            shared_folder : P:\\temp\\BENTO or override
            hostnames     : List of tester hostnames
            label         : TGZ label (default: force_fail_1)
            labels        : Optional per-tester label dict
            callback      : Optional callback(result_dict) on completion
        """
        if self._running:
            logger.warning("ForceFailController: already running — ignoring compile request.")
            return

        if not self._current_cases:
            self._log("✗ No force-fail cases generated — generate first", "error")
            if callback:
                self._callback(callback, {
                    "success": False,
                    "error": "No force-fail cases available. Generate them first.",
                })
            return

        enabled_cases = [c for c in self._current_cases if c.enabled]
        if not enabled_cases:
            self._log("✗ No enabled force-fail cases — enable at least one", "error")
            if callback:
                self._callback(callback, {
                    "success": False,
                    "error": "No enabled force-fail cases. Enable at least one.",
                })
            return

        self._running = True
        threading.Thread(
            target=self._compile_thread,
            args=(issue_key, repo_path, shared_folder, hostnames, label, labels, callback),
            daemon=True,
            name="bento-ff-compile",
        ).start()

    def _compile_thread(
        self, issue_key, repo_path, shared_folder, hostnames, label, labels, callback
    ):
        """Background thread for force-fail compilation."""
        patched_path = None
        try:
            generator = self._ensure_generator()

            # Step 1: Validate patches
            self._log("\n[Force Fail Compilation]")
            enabled_cases = [c for c in self._current_cases if c.enabled]

            validation = generator.validate_patches(repo_path, enabled_cases)
            if not validation.valid:
                error_msg = "Patch validation failed:\n" + "\n".join(validation.errors)
                self._log(f"✗ {error_msg}")
                self._callback(callback, {"success": False, "error": error_msg})
                return

            # Step 2: Check disk space
            ok, msg = generator.check_disk_space(repo_path)
            self._log(f"Disk space check: {msg}")
            if not ok:
                self._callback(callback, {"success": False, "error": msg})
                return

            # Step 3: Create patched repo
            patched_path = generator.create_patched_repo(
                repo_path, enabled_cases, issue_key
            )
            if not patched_path:
                self._callback(callback, {
                    "success": False,
                    "error": "Failed to create patched repository copy.",
                })
                return

            self._patched_repo = patched_path

            # Step 4: Delegate to CompileController
            compile_ctrl = getattr(self.context.controller, "compile_controller", None)
            if compile_ctrl is None:
                self._log("✗ CompileController not available", "error")
                self._callback(callback, {
                    "success": False,
                    "error": "CompileController is not initialised.",
                })
                return

            # Use force_fail label for all testers unless per-tester labels provided
            ff_labels = labels or {}
            if not ff_labels:
                ff_labels = {h: label for h in hostnames}

            self._log(f"Starting force-fail compilation on {len(hostnames)} tester(s)...")
            compile_ctrl.start_compile(
                source_dir=patched_path,
                jira_key=issue_key,
                shared_folder=shared_folder,
                label=label,
                hostnames=hostnames,
                labels=ff_labels,
            )

            # Save force-fail TGZ info to workflow
            self.workflow.save_workflow_step(
                "FORCE_FAIL_TGZ",
                f"Compiled with label '{label}' on testers: {', '.join(hostnames)}"
            )

            self._callback(callback, {
                "success": True,
                "patched_repo": patched_path,
                "hostnames": hostnames,
                "label": label,
            })

        except Exception as e:
            logger.error(f"ForceFailController._compile_thread error: {e}")
            self._log(f"✗ Force-fail compilation error: {e}")
            self._callback(callback, {"success": False, "error": str(e)})
        finally:
            self._running = False
            # Schedule cleanup after compile finishes
            # (CompileController runs async, so we defer cleanup)
            if patched_path:
                self._schedule_cleanup(patched_path)

    def _schedule_cleanup(self, patched_path: str, delay_ms: int = 300000):
        """
        Schedule cleanup of the patched repo after a delay.
        Default 5 minutes — gives compilation time to zip the repo.
        The ZIP is created synchronously before polling starts, so
        5 minutes is more than enough.
        """
        def _cleanup():
            generator = self._ensure_generator()
            if generator._auto_cleanup:
                generator.cleanup_patched_repo(patched_path)
                self._patched_repo = None

        self.context.root.after(delay_ms, _cleanup)

    # ──────────────────────────────────────────────────────────────────────
    # CASE MANAGEMENT (called from View)
    # ──────────────────────────────────────────────────────────────────────

    def toggle_case(self, test_id: str, enabled: bool):
        """Toggle a force-fail case on/off by test_id."""
        for case in self._current_cases:
            if case.test_id == test_id:
                case.enabled = enabled
                self._log(f"{'✓ Enabled' if enabled else '○ Disabled'} force-fail case: {test_id}")
                return True
        return False

    def get_cases_display(self) -> str:
        """Return human-readable display of current force-fail cases."""
        if not self._current_cases:
            return "No force-fail cases generated yet."
        from model.analyzers.force_fail_generator import ForceFailGenerator as FFG
        return FFG.cases_to_display(self._current_cases)

    def get_cases_count(self) -> tuple:
        """Return (total, enabled) count of force-fail cases."""
        total = len(self._current_cases)
        enabled = sum(1 for c in self._current_cases if c.enabled)
        return total, enabled

    def clear_cases(self):
        """Clear all generated force-fail cases."""
        self._current_cases = []
        self._log("Force-fail cases cleared")

    # ──────────────────────────────────────────────────────────────────────
    # HELPERS
    # ──────────────────────────────────────────────────────────────────────

    def _log(self, msg: str, level: str = "info"):
        """Log to both Python logger and GUI."""
        getattr(logger, level)(msg)
        if self.context and self.context.log:
            self.context.log(msg)

    def _callback(self, callback, result: dict):
        """Fire callback on the main thread via root.after."""
        if callback and self.context and self.context.root:
            self.context.root.after(0, lambda: callback(result))
