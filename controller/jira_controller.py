#!/usr/bin/env python3
"""
controller/jira_controller.py
==============================
JIRA Controller - Phase 2

Handles JIRA-related business logic.
Extracted from gui/app.py lines 1310-1468.
"""

import logging
import threading

logger = logging.getLogger("bento_app")


class JiraController:
    """
    Manages JIRA issue fetching and AI-powered analysis.
    
    Supports:
    - Fetching JIRA issues with field extraction
    - AI-powered JIRA analysis
    - Interactive chat for analysis refinement
    """
    
    def __init__(self, context, workflow_ctrl, chat_ctrl):
        self.context = context
        self.workflow = workflow_ctrl
        self.chat = chat_ctrl
        self.analyzer = context.analyzer
        self._running = False
        logger.info("JiraController initialized.")
    
    def is_running(self):
        return self._running
    
    def fetch_issue(self, issue_key, callback=None):
        """
        Fetch JIRA issue only.
        
        Args:
            issue_key: JIRA issue key
            callback: Optional callback(result) to call on completion
        """
        def _fetch():
            self._running = True
            try:
                # Initialize workflow file
                self.workflow.init_workflow_file(issue_key)
                
                self.context.log(f"\n[Fetching JIRA Issue: {issue_key}]")
                issue_data = self.analyzer.fetch_jira_issue(issue_key)
                
                if issue_data:
                    fields = issue_data.get('fields', {})
                    summary = fields.get('summary', 'N/A')
                    
                    # Get extracted fields
                    extracted = issue_data.get('extracted_fields', {})
                    
                    # Build structured display
                    result = f"{'='*50}\n"
                    result += f"JIRA ISSUE: {issue_key}\n"
                    result += f"{'='*50}\n\n"
                    result += f"SUMMARY:\n{summary}\n\n"
                    result += f"{'─'*50}\n"
                    result += f"WORK TYPE:\n{extracted.get('work_type', 'N/A')}\n\n"
                    result += f"{'─'*50}\n"
                    result += f"REPORTER:\n{extracted.get('reporter', 'N/A')}\n\n"
                    result += f"{'─'*50}\n"
                    result += f"ASSIGNEE:\n{extracted.get('assignee', 'Unassigned')}\n\n"
                    result += f"{'─'*50}\n"
                    result += f"DESCRIPTION:\n{extracted.get('description', 'N/A')}\n\n"
                    result += f"{'─'*50}\n"
                    result += f"COMPONENTS:\n{extracted.get('components_str', 'None')}\n\n"
                    result += f"{'─'*50}\n"
                    result += f"ACCEPTANCE CRITERIA:\n{extracted.get('acceptance_criteria', 'N/A')}\n\n"
                    result += f"{'─'*50}\n"
                    result += f"CHANGE CATEGORY:\n{extracted.get('change_category', 'N/A')}\n\n"
                    result += f"{'─'*50}\n"
                    result += f"ISSUE LINKS:\n{extracted.get('issue_links_count', 0)} linked issue(s)\n\n"
                    result += f"{'─'*50}\n"
                    result += f"ATTACHMENTS:\n{extracted.get('attachments_count', 0)} file(s)\n\n"
                    result += f"{'─'*50}\n"
                    result += f"RECENT COMMENTS (with roles):\n{extracted.get('comments_text', 'None')}\n"
                    result += f"{'='*50}\n"
                    
                    # Save to workflow file
                    self.workflow.save_workflow_step("ISSUE_KEY", issue_key)
                    self.workflow.save_workflow_step("JIRA_ISSUE_DATA", result)
                    
                    self.context.log(f"✓ Issue fetched and fields extracted successfully")
                    
                    if callback:
                        self.context.root.after(0, lambda: callback({'success': True, 'result': result, 'issue_data': issue_data}))
                else:
                    self.context.log(f"✗ Failed to fetch issue")
                    if callback:
                        self.context.root.after(0, lambda: callback({'success': False, 'error': f"Failed to fetch issue {issue_key}"}))
            
            except Exception as e:
                logger.error(f"fetch_issue error: {e}")
                self.context.log(f"✗ Error fetching issue: {e}")
                if callback:
                    self.context.root.after(0, lambda: callback({'success': False, 'error': str(e)}))
            finally:
                self._running = False
        
        threading.Thread(target=_fetch, daemon=True).start()
    
    def analyze_issue(self, issue_key, callback=None):
        """
        Analyze JIRA with AI only.
        
        Args:
            issue_key: JIRA issue key
            callback: Optional callback(result) to call on completion
        """
        def _analyze():
            self._running = True
            try:
                # Initialize workflow file
                self.workflow.init_workflow_file(issue_key)
                
                # Check if we already have JIRA analysis in workflow
                existing_analysis = self.workflow.get_workflow_step("JIRA_ANALYSIS")
                if existing_analysis:
                    self.context.log(f"✓ Found existing JIRA analysis in workflow file")
                    self.context.log(f"  Opening interactive chat with existing analysis...")
                    
                    # Open chat with existing analysis
                    self.context.root.after(100, lambda: self.chat.create_interactive_chat(
                        issue_key,
                        "JIRA Analysis",
                        existing_analysis,
                        lambda: self.finalize_analysis(issue_key, existing_analysis)
                    ))
                    
                    if callback:
                        self.context.root.after(0, lambda: callback({'success': True, 'analysis': existing_analysis}))
                    return
                
                # Check if we have issue data in workflow, if not fetch it
                issue_data_text = self.workflow.get_workflow_step("JIRA_ISSUE_DATA")
                if not issue_data_text:
                    self.context.log("⚠ JIRA issue data not found in workflow, fetching from JIRA...")
                    issue_data = self.analyzer.fetch_jira_issue(issue_key)
                    if not issue_data:
                        if callback:
                            self.context.root.after(0, lambda: callback({'success': False, 'error': 'Failed to fetch issue'}))
                        return
                else:
                    self.context.log("✓ Found JIRA issue data in workflow file")
                    # Re-fetch to get full data object
                    issue_data = self.analyzer.fetch_jira_issue(issue_key)
                
                self.context.log(f"\n[Analyzing JIRA Issue with AI: {issue_key}]")
                
                if issue_data:
                    jira_analysis = self.analyzer.analyze_jira_request(issue_data)
                    if jira_analysis.get('success'):
                        analysis_text = jira_analysis.get('analysis', '')
                        
                        # Open interactive chat for user to refine the analysis
                        self.context.log(f"✓ Initial analysis complete - opening interactive chat...")
                        self.context.root.after(100, lambda: self.chat.create_interactive_chat(
                            issue_key,
                            "JIRA Analysis",
                            analysis_text,
                            lambda: self.finalize_analysis(issue_key, analysis_text)
                        ))
                        
                        if callback:
                            self.context.root.after(0, lambda: callback(jira_analysis))
                    else:
                        self.context.log(f"✗ Analysis failed: {jira_analysis.get('error')}")
                        if callback:
                            self.context.root.after(0, lambda: callback(jira_analysis))
                else:
                    self.context.log(f"✗ Failed to fetch issue")
                    if callback:
                        self.context.root.after(0, lambda: callback({'success': False, 'error': 'Failed to fetch issue'}))
            
            except Exception as e:
                logger.error(f"analyze_issue error: {e}")
                self.context.log(f"✗ Error analyzing issue: {e}")
                if callback:
                    self.context.root.after(0, lambda: callback({'success': False, 'error': str(e)}))
            finally:
                self._running = False
        
        threading.Thread(target=_analyze, daemon=True).start()
    
    def finalize_analysis(self, issue_key, analysis_text):
        """
        Finalize JIRA analysis after user approval from interactive chat.
        
        Args:
            issue_key: JIRA issue key
            analysis_text: Final approved analysis text
        """
        # Save to workflow file
        self.workflow.save_workflow_step("JIRA_ANALYSIS", analysis_text)
        
        # Log completion
        self.context.log(f"✓ JIRA analysis approved and saved")
        self.context.log(f"✓ Analysis for {issue_key} is complete")
