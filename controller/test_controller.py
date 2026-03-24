#!/usr/bin/env python3
"""
controller/test_controller.py
==============================
Test Scenarios Controller - Phase 2

Handles test scenario generation.
Extracted from gui/app.py lines 1659-1742.
"""

import logging
import threading

logger = logging.getLogger("bento_app")


class TestController:
    """
    Manages test scenario generation with AI.
    
    Supports:
    - Generating test scenarios from JIRA analysis
    - Interactive chat for test refinement
    """
    
    def __init__(self, context, workflow_ctrl, chat_ctrl):
        self.context = context
        self.workflow = workflow_ctrl
        self.chat = chat_ctrl
        self.analyzer = context.analyzer
        self._running = False
        logger.info("TestController initialized.")
    
    def is_running(self):
        return self._running
    
    def generate_tests(self, issue_key, callback=None):
        """
        Generate test scenarios only.
        
        Args:
            issue_key: JIRA issue key
            callback: Optional callback(result) to call on completion
        """
        def _generate():
            self._running = True
            try:
                self.context.log(f"\n[Generating Test Scenarios for {issue_key}]")
                
                # Initialize workflow file to check for existing data
                self.workflow.init_workflow_file(issue_key)
                
                # Get repository path from workflow
                repo_path = self.workflow.get_workflow_step("REPOSITORY_PATH")
                if not repo_path:
                    error_msg = (
                        "Repository path not found in workflow.\n\n"
                        "Please run the full analysis workflow first or clone the repository."
                    )
                    self.context.log(f"✗ {error_msg}")
                    if callback:
                        self.context.root.after(0, lambda: callback({
                            'success': False,
                            'error': error_msg
                        }))
                    return
                
                # Check if we already have test scenarios in workflow
                existing_test_scenarios = self.workflow.get_workflow_step("TEST_SCENARIOS")
                if existing_test_scenarios:
                    self.context.log(f"✓ Found existing test scenarios in workflow file")
                    self.context.log(f"✓ Test scenarios loaded from workflow")
                    if callback:
                        self.context.root.after(0, lambda: callback({
                            'success': True,
                            'test_scenarios': existing_test_scenarios,
                            'from_cache': True
                        }))
                    return
                
                # Index repository
                repo_index = self.analyzer.index_repository(repo_path)
                
                # Check for existing JIRA analysis in workflow
                jira_analysis_text = self.workflow.get_workflow_step("JIRA_ANALYSIS")
                if jira_analysis_text:
                    self.context.log("✓ Using existing JIRA analysis from workflow file")
                    jira_analysis = {'success': True, 'analysis': jira_analysis_text}
                else:
                    self.context.log("⚠ JIRA analysis not found in workflow, fetching and analyzing...")
                    issue_data = self.analyzer.fetch_jira_issue(issue_key)
                    if not issue_data:
                        error_msg = f"Failed to fetch JIRA issue {issue_key}"
                        self.context.log(f"✗ {error_msg}")
                        if callback:
                            self.context.root.after(0, lambda: callback({
                                'success': False,
                                'error': error_msg
                            }))
                        return
                    
                    jira_analysis = self.analyzer.analyze_jira_request(issue_data)
                    if not jira_analysis.get('success'):
                        error_msg = f"Failed to analyze JIRA issue: {jira_analysis.get('error')}"
                        self.context.log(f"✗ {error_msg}")
                        if callback:
                            self.context.root.after(0, lambda: callback({
                                'success': False,
                                'error': error_msg
                            }))
                        return
                    
                    # Save to workflow for future use
                    self.workflow.save_workflow_step("JIRA_ANALYSIS", jira_analysis.get('analysis', ''))
                
                # Generate test scenarios
                test_scenarios = self.analyzer.generate_test_scenarios(jira_analysis, repo_index)
                if test_scenarios.get('success'):
                    test_scenarios_text = test_scenarios.get('test_scenarios', '')
                    
                    # Open interactive chat for test scenarios review
                    self.context.log(f"✓ Test scenarios generated - opening interactive chat...")
                    self.context.root.after(100, lambda: self.chat.create_interactive_chat(
                        issue_key,
                        "Test Scenarios",
                        test_scenarios_text,
                        lambda: self.finalize_tests(issue_key, test_scenarios_text)
                    ))
                    
                    if callback:
                        self.context.root.after(0, lambda: callback(test_scenarios))
                else:
                    error_msg = f"Test generation failed: {test_scenarios.get('error')}"
                    self.context.log(f"✗ {error_msg}")
                    if callback:
                        self.context.root.after(0, lambda: callback(test_scenarios))
            
            except Exception as e:
                logger.error(f"generate_tests error: {e}")
                self.context.log(f"✗ Error generating tests: {e}")
                if callback:
                    self.context.root.after(0, lambda: callback({
                        'success': False,
                        'error': str(e)
                    }))
            finally:
                self._running = False
        
        threading.Thread(target=_generate, daemon=True).start()
    
    def finalize_tests(self, issue_key, test_scenarios_text):
        """
        Finalize test scenarios after user approval from interactive chat.
        
        Args:
            issue_key: JIRA issue key
            test_scenarios_text: Final approved test scenarios text
        """
        # Save to workflow
        self.workflow.save_workflow_step("TEST_SCENARIOS", test_scenarios_text)
        
        # Log completion
        self.context.log(f"✓ Test scenarios approved and saved to workflow")
        self.context.log(f"✓ Test scenarios for {issue_key} are complete")
