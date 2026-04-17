#!/usr/bin/env python3
"""
controller/pr_review_controller.py
====================================
PR Review Controller — bridges PRReviewTab (View) ↔ pr_review_orchestrator (Model).

Manages the automated JIRA / PR Approval pipeline:
  1. Attach validation doc to JIRA & tag requestor
  2. Commit & push code changes
  3. Create pull request to reviewers
  4. Poll for PR approval (with stale notifications)
  5. Merge PR on approval
  6. Close/transition JIRA issue

All long-running operations run in background threads.
UI updates are dispatched via root.after() for thread safety.
"""

import logging
import os
import threading
from typing import Callable, Dict, List, Optional

from model.orchestrators.pr_review_orchestrator import (
    add_jira_comment,
    attach_file_to_jira,
    create_pull_request,
    get_jira_reporter,
    get_pr_version,
    get_pull_request_status,
    git_add_commit_push,
    git_get_current_branch,
    git_get_diff_summary,
    merge_pull_request,
    run_full_pr_pipeline,
    send_pr_review_notification,
    transition_jira_issue,
)

logger = logging.getLogger("bento_app")


class PRReviewController:
    """
    Controller for the ✅ PR Review tab.

    Constructor args:
        context:       AppContext
        workflow_ctrl: WorkflowController
        chat_ctrl:     ChatController
    """

    def __init__(self, context, workflow_ctrl, chat_ctrl):
        self.context = context
        self.workflow = workflow_ctrl
        self.chat = chat_ctrl
        self.analyzer = context.analyzer
        self._running = False
        self._cancel_event = threading.Event()
        self._current_pr_id: Optional[int] = None
        self._current_pr_url: Optional[str] = None
        logger.info("PRReviewController initialised.")

    def is_running(self) -> bool:
        return self._running

    def cancel(self):
        """Signal the running pipeline to stop."""
        self._cancel_event.set()
        self._log("⚠ Cancel requested — pipeline will stop after current phase.")

    # ──────────────────────────────────────────────────────────────────────
    # QUICK ACTIONS (individual steps)
    # ──────────────────────────────────────────────────────────────────────

    def get_diff_summary(self, callback: Optional[Callable] = None):
        """Get git diff summary for the current repo (non-blocking)."""
        def _work():
            repo_path = self.workflow.get_workflow_step("REPOSITORY_PATH")
            if not repo_path:
                self._callback(callback, {"success": False, "error": "No repository path in workflow"})
                return
            branch = git_get_current_branch(repo_path, self._log)
            diff = git_get_diff_summary(repo_path, self._log)
            self._callback(callback, {
                "success": True,
                "branch": branch or "unknown",
                "diff": diff,
                "repo_path": repo_path,
            })
        threading.Thread(target=_work, daemon=True, name="bento-pr-diff").start()

    def commit_and_push(self, commit_message: str, callback: Optional[Callable] = None):
        """Commit & push only (Phase 2 standalone)."""
        if self._running:
            self._callback(callback, {"success": False, "error": "Pipeline already running"})
            return

        def _work():
            self._running = True
            try:
                repo_path = self.workflow.get_workflow_step("REPOSITORY_PATH")
                if not repo_path:
                    self._callback(callback, {"success": False, "error": "No repository path in workflow"})
                    return

                branch = git_get_current_branch(repo_path, self._log)
                result = git_add_commit_push(repo_path, commit_message, branch, self._log)
                self._callback(callback, result)
            except Exception as e:
                self._callback(callback, {"success": False, "error": str(e)})
            finally:
                self._running = False

        threading.Thread(target=_work, daemon=True, name="bento-pr-push").start()

    def create_pr_only(
        self,
        issue_key: str,
        target_branch: str,
        reviewers: List[str],
        callback: Optional[Callable] = None,
    ):
        """Create PR only (Phase 3 standalone)."""
        if self._running:
            self._callback(callback, {"success": False, "error": "Pipeline already running"})
            return

        def _work():
            self._running = True
            try:
                repo_path = self.workflow.get_workflow_step("REPOSITORY_PATH")
                if not repo_path:
                    self._callback(callback, {"success": False, "error": "No repository path in workflow"})
                    return

                source_branch = git_get_current_branch(repo_path, self._log) or ""
                repo_slug, project_key = self._resolve_repo_info()

                if not repo_slug:
                    self._callback(callback, {"success": False, "error": "Cannot determine repository slug"})
                    return

                if not source_branch:
                    self._callback(callback, {"success": False, "error": "Cannot determine current branch"})
                    return

                bb_url, bb_user, bb_token = self._get_bitbucket_creds()

                pr_title = f"[{issue_key}] Feature branch merge"
                pr_desc = f"**JIRA:** {issue_key}\n**Branch:** {source_branch} → {target_branch}"

                result = create_pull_request(
                    repo_slug, project_key, source_branch, target_branch,
                    pr_title, pr_desc, reviewers,
                    bb_url, bb_user, bb_token, self._log
                )

                if result.get("success"):
                    self._current_pr_id = result["pr_id"]
                    self._current_pr_url = result["pr_url"]
                    self.workflow.save_workflow_step("PR_ID", str(result["pr_id"]))
                    self.workflow.save_workflow_step("PR_URL", result["pr_url"])

                self._callback(callback, result)
            except Exception as e:
                self._callback(callback, {"success": False, "error": str(e)})
            finally:
                self._running = False

        threading.Thread(target=_work, daemon=True, name="bento-pr-create").start()

    def check_pr_status(self, callback: Optional[Callable] = None):
        """Check current PR approval status."""
        def _work():
            pr_id = self._current_pr_id
            if not pr_id:
                pr_id_str = self.workflow.get_workflow_step("PR_ID")
                if pr_id_str:
                    pr_id = int(pr_id_str)

            if not pr_id:
                self._callback(callback, {"success": False, "error": "No PR ID found"})
                return

            repo_slug, project_key = self._resolve_repo_info()
            bb_url, bb_user, bb_token = self._get_bitbucket_creds()

            result = get_pull_request_status(
                repo_slug, project_key, pr_id,
                bb_url, bb_user, bb_token, self._log
            )
            self._callback(callback, result)

        threading.Thread(target=_work, daemon=True, name="bento-pr-status").start()

    # ──────────────────────────────────────────────────────────────────────
    # FULL PIPELINE
    # ──────────────────────────────────────────────────────────────────────

    def run_full_pipeline(
        self,
        issue_key: str,
        target_branch: str,
        reviewers: List[str],
        commit_message: str,
        validation_doc_path: Optional[str],
        auto_merge: bool,
        auto_close_jira: bool,
        transition_name: str,
        callback: Optional[Callable] = None,
        phase_callback: Optional[Callable] = None,
    ):
        """
        Launch the full PR review pipeline in a background thread.

        Args:
            callback: Called with final result dict on completion.
            phase_callback: Called with (phase_name, detail) for UI progress.
        """
        if self._running:
            self._callback(callback, {"success": False, "error": "Pipeline already running"})
            return

        def _work():
            self._running = True
            self._cancel_event.clear()
            try:
                # Resolve all parameters
                repo_path = self.workflow.get_workflow_step("REPOSITORY_PATH")
                if not repo_path:
                    self._callback(callback, {
                        "success": False,
                        "error": "No repository path in workflow. Clone a repo first."
                    })
                    return

                source_branch = git_get_current_branch(repo_path, self._log)
                if not source_branch:
                    self._callback(callback, {
                        "success": False,
                        "error": "Cannot determine current branch."
                    })
                    return

                repo_slug, project_key = self._resolve_repo_info()
                if not repo_slug:
                    self._callback(callback, {
                        "success": False,
                        "error": "Cannot determine repository slug from workflow."
                    })
                    return

                jira_url, email, jira_token = self._get_jira_creds()
                bb_url, bb_user, bb_token = self._get_bitbucket_creds()
                webhook_url = self._get_webhook_url()

                # Thread-safe phase callback
                def _safe_phase(name, detail=""):
                    _cb = phase_callback
                    if _cb is not None:
                        self.context.root.after(0, lambda n=name, d=detail, cb=_cb: cb(n, d))

                result = run_full_pr_pipeline(
                    issue_key=issue_key,
                    repo_path=repo_path,
                    repo_slug=repo_slug,
                    project_key=project_key,
                    source_branch=source_branch,
                    target_branch=target_branch,
                    reviewers=reviewers,
                    commit_message=commit_message,
                    validation_doc_path=validation_doc_path,
                    jira_base_url=jira_url,
                    email=email,
                    jira_token=jira_token,
                    bitbucket_base_url=bb_url,
                    bitbucket_username=bb_user,
                    bitbucket_token=bb_token,
                    webhook_url=webhook_url,
                    transition_name=transition_name,
                    auto_merge=auto_merge,
                    auto_close_jira=auto_close_jira,
                    log_callback=self._log,
                    phase_callback=_safe_phase,
                    cancel_event=self._cancel_event,
                )

                if result.get("pr_id"):
                    self._current_pr_id = result["pr_id"]
                    self._current_pr_url = result.get("pr_url", "")
                    self.workflow.save_workflow_step("PR_ID", str(result["pr_id"]))
                    self.workflow.save_workflow_step("PR_URL", result.get("pr_url", ""))

                self._callback(callback, result)

            except Exception as e:
                logger.error(f"PR pipeline error: {e}", exc_info=True)
                self._callback(callback, {"success": False, "error": str(e)})
            finally:
                self._running = False

        threading.Thread(target=_work, daemon=True, name="bento-pr-pipeline").start()

    # ──────────────────────────────────────────────────────────────────────
    # VALIDATION DOC GENERATION (reuses existing ValidationController)
    # ──────────────────────────────────────────────────────────────────────

    def generate_validation_doc(self, issue_key: str, callback: Optional[Callable] = None):
        """
        Generate validation document using the existing validation controller,
        then return the path for attachment.
        """
        if self.context.controller.validation_controller:
            self.context.controller.validation_controller.generate_validation(
                issue_key, callback
            )
        else:
            self._callback(callback, {
                "success": False,
                "error": "Validation controller not available"
            })

    # ──────────────────────────────────────────────────────────────────────
    # HELPERS
    # ──────────────────────────────────────────────────────────────────────

    def _log(self, msg: str):
        """Thread-safe log to GUI."""
        self.context.log(msg)

    def _callback(self, callback: Optional[Callable], result: dict):
        """Thread-safe callback dispatch."""
        if callback:
            self.context.root.after(0, lambda: callback(result))

    def _resolve_repo_info(self):
        """
        Extract repo_slug and project_key from workflow state.

        Returns:
            (repo_slug, project_key) tuple.
        """
        repo_info = self.workflow.get_workflow_step("REPOSITORY_INFO") or ""
        repo_path = self.workflow.get_workflow_step("REPOSITORY_PATH") or ""

        repo_slug = ""
        project_key = self.context.config.get("bitbucket", {}).get("project_key", "TESTSSD")

        # Try to extract from REPOSITORY_INFO
        for line in repo_info.split("\n"):
            if line.startswith("Repository:"):
                repo_slug = line.split(":", 1)[1].strip()
                break

        # Fallback: extract from repo_path (format: Repos/ISSUE_reponame)
        if not repo_slug and repo_path:
            basename = os.path.basename(repo_path)
            # Strip issue key prefix (e.g. "TSESSD-12345_reponame" → "reponame")
            parts = basename.split("_", 1)
            if len(parts) > 1:
                repo_slug = parts[1]
            else:
                repo_slug = basename

        return repo_slug, project_key

    def _get_jira_creds(self):
        """Return (jira_base_url, email, jira_token) from analyzer."""
        analyzer = self.analyzer
        jira_url = getattr(analyzer, "jira_base_url", "") or \
                   self.context.config.get("jira", {}).get("base_url", "")
        email = getattr(analyzer, "email", "") or ""
        jira_token = getattr(analyzer, "jira_token", "") or ""
        return jira_url, email, jira_token

    def _get_bitbucket_creds(self):
        """Return (bitbucket_base_url, username, token) from analyzer."""
        analyzer = self.analyzer
        bb_url = getattr(analyzer, "bitbucket_base_url", "") or \
                 self.context.config.get("bitbucket", {}).get("base_url", "")
        bb_user = getattr(analyzer, "bitbucket_username", "") or ""
        bb_token = getattr(analyzer, "bitbucket_token", "") or ""
        return bb_url, bb_user, bb_token

    def _get_webhook_url(self) -> str:
        """Resolve Teams webhook URL from context or config."""
        webhook_var = self.context.get_var("checkout_webhook_url")
        if webhook_var:
            url = webhook_var.get().strip()
            if url:
                return url
        return self.context.config.get("notifications", {}).get("teams_webhook_url", "")
