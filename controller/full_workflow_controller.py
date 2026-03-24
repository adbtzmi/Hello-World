# -*- coding: utf-8 -*-
"""
controller/full_workflow_controller.py
=======================================
Full Workflow Controller - Phase 3D

Orchestrates the complete BENTO analysis workflow:
1. Fetch JIRA issue
2. Analyze with AI
3. Clone repository
4. Create feature branch
5. Generate implementation plan
6. Generate test scenarios
7. Generate validation & risk assessment
"""

import logging
import threading

logger = logging.getLogger("bento_app")


class FullWorkflowController:
    """
    Orchestrates the complete BENTO analysis workflow.
    Coordinates all sub-controllers in sequence.
    """

    def __init__(self, context, workflow_ctrl, chat_ctrl, jira_ctrl,
                 repo_ctrl, test_ctrl, implementation_ctrl, validation_ctrl):
        self.context = context
        self.workflow = workflow_ctrl
        self.chat = chat_ctrl
        self.jira = jira_ctrl
        self.repo = repo_ctrl
        self.test = test_ctrl
        self.implementation = implementation_ctrl
        self.validation = validation_ctrl
        self._running = False
        logger.info("FullWorkflowController initialised.")

    def is_running(self):
        return self._running

    # ──────────────────────────────────────────────────────────────────────
    # START FULL WORKFLOW
    # ──────────────────────────────────────────────────────────────────────

    def start_full_workflow(self, issue_key, repo, branch, feature_branch):
        """
        Start the complete analysis workflow.
        
        Args:
            issue_key: JIRA issue key
            repo: Repository name/slug
            branch: Base branch name
            feature_branch: Feature branch name (or empty for auto)
        """
        if self._running:
            logger.warning("FullWorkflowController: already running")
            return

        self._running = True
        self._log("="*60)
        self._log("Starting Full BENTO Analysis Workflow")
        self._log(f"Issue: {issue_key}")
        self._log(f"Repository: {repo}")
        self._log(f"Branch: {branch}")
        self._log("="*60)

        threading.Thread(
            target=self._run_workflow_thread,
            args=(issue_key, repo, branch, feature_branch),
            daemon=True,
            name="bento-full-workflow"
        ).start()

    def _run_workflow_thread(self, issue_key, repo, branch, feature_branch):
        """Background thread for full workflow execution."""
        try:
            # Step 1: Fetch JIRA issue
            self._log("\n[Step 1/7] Fetching JIRA issue...")
            if not self._fetch_issue(issue_key):
                self._log("✗ Workflow aborted: Failed to fetch JIRA issue")
                return

            # Step 2: Analyze JIRA with AI
            self._log("\n[Step 2/7] Analyzing JIRA with AI...")
            if not self._analyze_jira(issue_key):
                self._log("✗ Workflow aborted: Failed to analyze JIRA")
                return

            # Step 3: Clone repository
            self._log("\n[Step 3/7] Cloning repository...")
            repo_path = self._clone_repo(issue_key, repo, branch)
            if not repo_path:
                self._log("✗ Workflow aborted: Failed to clone repository")
                return

            # Step 4: Create feature branch
            self._log("\n[Step 4/7] Creating feature branch...")
            if not self._create_branch(issue_key, repo_path, branch, feature_branch):
                self._log("✗ Workflow aborted: Failed to create feature branch")
                return

            # Step 5: Generate implementation plan
            self._log("\n[Step 5/7] Generating implementation plan...")
            if not self._generate_implementation(issue_key, repo_path):
                self._log("⚠ Implementation plan generation failed (continuing)")

            # Step 6: Generate test scenarios
            self._log("\n[Step 6/7] Generating test scenarios...")
            if not self._generate_tests(issue_key):
                self._log("⚠ Test scenario generation failed (continuing)")

            # Step 7: Generate validation & risk assessment
            self._log("\n[Step 7/7] Generating validation & risk assessment...")
            if not self._generate_validation(issue_key):
                self._log("⚠ Validation generation failed (continuing)")

            # Workflow complete
            self._log("\n" + "="*60)
            self._log("✓ Full BENTO Analysis Workflow Complete!")
            self._log("="*60)
            self._log(f"\nWorkflow file: Workflows/{issue_key}_workflow.txt")
            self._log("Review the workflow file for all generated artifacts.")

        except Exception as e:
            logger.error(f"Workflow error: {e}")
            self._log(f"\n✗ Workflow error: {e}")
        finally:
            self._running = False

    # ──────────────────────────────────────────────────────────────────────
    # WORKFLOW STEPS
    # ──────────────────────────────────────────────────────────────────────

    def _fetch_issue(self, issue_key):
        """Step 1: Fetch JIRA issue."""
        try:
            issue_data = self.context.analyzer.fetch_jira_issue(issue_key)
            if issue_data:
                # Save to workflow
                fields = issue_data.get('fields', {})
                summary = fields.get('summary', 'N/A')
                extracted = issue_data.get('extracted_fields', {})
                
                issue_text = f"Issue: {issue_key}\nSummary: {summary}\n\n"
                issue_text += f"Description:\n{extracted.get('description', 'N/A')}\n"
                
                self.workflow.init_workflow_file(issue_key)
                self.workflow.save_workflow_step("ISSUE_KEY", issue_key)
                self.workflow.save_workflow_step("JIRA_ISSUE_DATA", issue_text)
                
                self._log(f"✓ Issue fetched: {issue_key}")
                return True
            else:
                self._log(f"✗ Failed to fetch issue: {issue_key}")
                return False
        except Exception as e:
            logger.error(f"Fetch issue error: {e}")
            return False

    def _analyze_jira(self, issue_key):
        """Step 2: Analyze JIRA with AI."""
        try:
            issue_data = self.context.analyzer.fetch_jira_issue(issue_key)
            if not issue_data:
                return False

            jira_analysis = self.context.analyzer.analyze_jira_request(issue_data)
            if jira_analysis.get('success'):
                analysis_text = jira_analysis.get('analysis', '')
                self.workflow.save_workflow_step("JIRA_ANALYSIS", analysis_text)
                self._log("✓ JIRA analysis complete")
                return True
            else:
                self._log(f"✗ Analysis failed: {jira_analysis.get('error')}")
                return False
        except Exception as e:
            logger.error(f"Analyze JIRA error: {e}")
            return False

    def _clone_repo(self, issue_key, repo, branch):
        """Step 3: Clone repository."""
        try:
            repo_path = self.context.analyzer.clone_repository(repo, branch, issue_key)
            if repo_path:
                result = f"Repository cloned successfully!\n\n"
                result += f"Local path: {repo_path}\n"
                result += f"Repository: {repo}\n"
                result += f"Base branch: {branch}\n"
                
                self.workflow.save_workflow_step("REPOSITORY_PATH", repo_path)
                self.workflow.save_workflow_step("REPOSITORY_INFO", result)
                
                self._log(f"✓ Repository cloned: {repo_path}")
                return repo_path
            else:
                self._log("✗ Failed to clone repository")
                return None
        except Exception as e:
            logger.error(f"Clone repo error: {e}")
            return None

    def _create_branch(self, issue_key, repo_path, base_branch, feature_branch):
        """Step 4: Create feature branch."""
        try:
            # Determine feature branch name
            if feature_branch and "if empty," not in feature_branch.lower():
                fb_name = feature_branch
            else:
                fb_name = f"feature/{issue_key}"

            success = self.context.analyzer.create_feature_branch(
                repo_path, issue_key, base_branch, feature_branch
            )
            
            if success:
                self._log(f"✓ Feature branch created: {fb_name}")
                return True
            else:
                self._log("✗ Failed to create feature branch")
                return False
        except Exception as e:
            logger.error(f"Create branch error: {e}")
            return False

    def _generate_implementation(self, issue_key, repo_path):
        """Step 5: Generate implementation plan."""
        try:
            # Use implementation controller
            result = {'success': False}
            
            def callback(res):
                result.update(res)
            
            self.implementation.generate_implementation_plan(issue_key, repo_path, callback)
            
            # Wait for completion (simple polling)
            import time
            timeout = 300  # 5 minutes
            elapsed = 0
            while self.implementation.is_running() and elapsed < timeout:
                time.sleep(1)
                elapsed += 1
            
            if result.get('success'):
                # Auto-finalize (no interactive chat in full workflow)
                plan_text = result.get('plan', '')
                self.implementation.finalize_plan(issue_key, plan_text)
                self._log("✓ Implementation plan generated")
                return True
            else:
                self._log(f"✗ Implementation failed: {result.get('error')}")
                return False
        except Exception as e:
            logger.error(f"Generate implementation error: {e}")
            return False

    def _generate_tests(self, issue_key):
        """Step 6: Generate test scenarios."""
        try:
            # Use test controller
            result = {'success': False}
            
            def callback(res):
                result.update(res)
            
            self.test.generate_test_scenarios(issue_key, callback)
            
            # Wait for completion
            import time
            timeout = 300
            elapsed = 0
            while self.test.is_running() and elapsed < timeout:
                time.sleep(1)
                elapsed += 1
            
            if result.get('success'):
                # Auto-finalize
                test_text = result.get('test_scenarios', '')
                self.test.finalize_test_scenarios(issue_key, test_text)
                self._log("✓ Test scenarios generated")
                return True
            else:
                self._log(f"✗ Test generation failed: {result.get('error')}")
                return False
        except Exception as e:
            logger.error(f"Generate tests error: {e}")
            return False

    def _generate_validation(self, issue_key):
        """Step 7: Generate validation & risk assessment."""
        try:
            # Use validation controller
            result = {'success': False}
            
            def callback(res):
                result.update(res)
            
            self.validation.assess_risks(issue_key, callback)
            
            # Wait for completion
            import time
            timeout = 300
            elapsed = 0
            while self.validation.is_running() and elapsed < timeout:
                time.sleep(1)
                elapsed += 1
            
            if result.get('success'):
                # Auto-finalize
                assessment_text = result.get('assessment', '')
                self.validation.finalize_assessment(issue_key, assessment_text)
                self._log("✓ Validation & risk assessment generated")
                return True
            else:
                self._log(f"✗ Validation failed: {result.get('error')}")
                return False
        except Exception as e:
            logger.error(f"Generate validation error: {e}")
            return False

    # ──────────────────────────────────────────────────────────────────────
    # HELPERS
    # ──────────────────────────────────────────────────────────────────────

    def _log(self, message):
        """Thread-safe logging."""
        if self.context.log_callback:
            self.context.root.after(0, lambda: self.context.log_callback(message))
