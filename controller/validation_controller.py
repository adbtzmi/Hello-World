# -*- coding: utf-8 -*-
"""
controller/validation_controller.py
====================================
Validation Controller - Phase 3C

Bridges ValidationTab (View) <-> jira_analyzer.py (Model).
Handles validation document generation and risk assessment.
"""

import os
import logging
import threading

logger = logging.getLogger("bento_app")


class ValidationController:
    """
    Bridges ValidationTab <-> jira_analyzer.py.
    Handles validation document and risk assessment generation.
    """

    def __init__(self, context, workflow_ctrl, chat_ctrl):
        self.context = context
        self.workflow = workflow_ctrl
        self.chat = chat_ctrl
        self.analyzer = context.analyzer
        self._running = False
        logger.info("ValidationController initialised.")

    def is_running(self):
        return self._running

    # ──────────────────────────────────────────────────────────────────────
    # ASSESS RISKS
    # ──────────────────────────────────────────────────────────────────────

    def assess_risks(self, issue_key, callback):
        """
        Generate validation document and risk assessment.
        
        Args:
            issue_key: JIRA issue key
            callback: Function to call with result dict
        """
        if self._running:
            logger.warning("ValidationController: already running")
            return

        self._running = True
        threading.Thread(
            target=self._assess_risks_thread,
            args=(issue_key, callback),
            daemon=True,
            name="bento-validation"
        ).start()

    def _assess_risks_thread(self, issue_key, callback):
        """Background thread for risk assessment."""
        try:
            self._log(f"[Generating Validation Document for {issue_key}]")

            # Initialize workflow
            self.workflow.init_workflow_file(issue_key)

            # Get repository path
            repo_path = self.workflow.get_workflow_step("REPOSITORY_PATH")
            if not repo_path:
                self._callback(callback, {
                    'success': False,
                    'error': "Repository path not found in workflow.\n"
                             "Please run the full analysis workflow first."
                })
                return

            # Check for template
            template_file = "template_validation.docx"
            if os.path.exists(template_file):
                self._log(f"✓ Found template: {template_file}")
                self._populate_template(issue_key, template_file, repo_path)

            # Index repository
            self._log("Indexing repository...")
            repo_index = self.analyzer.index_repository(repo_path)

            # Get JIRA analysis
            jira_analysis_text = self.workflow.get_workflow_step("JIRA_ANALYSIS")
            if jira_analysis_text:
                self._log("✓ Using existing JIRA analysis from workflow")
                jira_analysis = {'success': True, 'analysis': jira_analysis_text}
            else:
                self._log("⚠ JIRA analysis not found, fetching...")
                issue_data = self.analyzer.fetch_jira_issue(issue_key)
                if not issue_data:
                    self._callback(callback, {
                        'success': False,
                        'error': f"Failed to fetch JIRA issue {issue_key}"
                    })
                    return

                jira_analysis = self.analyzer.analyze_jira_request(issue_data)
                if not jira_analysis.get('success'):
                    self._callback(callback, {
                        'success': False,
                        'error': f"Failed to analyze JIRA: {jira_analysis.get('error')}"
                    })
                    return

                self.workflow.save_workflow_step("JIRA_ANALYSIS", jira_analysis['analysis'])

            # Get impact analysis
            impact_analysis_text = self.workflow.get_workflow_step("IMPACT_ANALYSIS")
            if impact_analysis_text:
                self._log("✓ Using existing impact analysis from workflow")
                impact_analysis = {'success': True, 'impact_analysis': impact_analysis_text}
            else:
                self._log("⚠ Impact analysis not found, generating...")
                impact_analysis = self.analyzer.analyze_code_impact(
                    repo_path, repo_index, jira_analysis
                )
                if not impact_analysis.get('success'):
                    self._callback(callback, {
                        'success': False,
                        'error': f"Failed to analyze impact: {impact_analysis.get('error')}"
                    })
                    return

                self.workflow.save_workflow_step("IMPACT_ANALYSIS", impact_analysis['impact_analysis'])

            # Generate risk assessment
            self._log("Generating risk assessment with AI...")
            risk_assessment = self.analyzer.assess_risks(jira_analysis, impact_analysis)

            if risk_assessment.get('success'):
                assessment_text = risk_assessment.get('risk_assessment', '')
                self._log("✓ Risk assessment generated")
                self._callback(callback, {
                    'success': True,
                    'assessment': assessment_text
                })
            else:
                self._callback(callback, {
                    'success': False,
                    'error': f"Risk assessment failed: {risk_assessment.get('error')}"
                })

        except Exception as e:
            logger.error(f"Validation error: {e}")
            self._callback(callback, {
                'success': False,
                'error': str(e)
            })
        finally:
            self._running = False

    def _populate_template(self, issue_key, template_file, repo_path):
        """Populate validation template with workflow data."""
        try:
            from docx import Document
            
            doc = Document(template_file)
            
            # Get workflow data
            jira_data = self.workflow.get_workflow_step("JIRA_ISSUE_DATA") or "N/A"
            jira_analysis = self.workflow.get_workflow_step("JIRA_ANALYSIS") or "N/A"
            impact_analysis = self.workflow.get_workflow_step("IMPACT_ANALYSIS") or "N/A"
            test_scenarios = self.workflow.get_workflow_step("TEST_SCENARIOS") or "N/A"
            
            # Replace placeholders
            replacements = {
                '{{ISSUE_KEY}}': issue_key,
                '{{JIRA_DATA}}': jira_data[:500],  # Truncate for template
                '{{JIRA_ANALYSIS}}': jira_analysis[:500],
                '{{IMPACT_ANALYSIS}}': impact_analysis[:500],
                '{{TEST_SCENARIOS}}': test_scenarios[:500],
                '{{REPO_PATH}}': repo_path,
            }
            
            for paragraph in doc.paragraphs:
                for key, value in replacements.items():
                    if key in paragraph.text:
                        paragraph.text = paragraph.text.replace(key, value)
            
            # Save populated template
            output_file = f"Validation_{issue_key}.docx"
            doc.save(output_file)
            self._log(f"✓ Validation document saved: {output_file}")
            
        except ImportError:
            self._log("⚠ python-docx not installed, skipping template population")
        except Exception as e:
            logger.error(f"Template population error: {e}")
            self._log(f"⚠ Template population failed: {e}")

    # ──────────────────────────────────────────────────────────────────────
    # FINALIZE ASSESSMENT
    # ──────────────────────────────────────────────────────────────────────

    def finalize_assessment(self, issue_key, assessment_text):
        """Save finalized risk assessment to workflow."""
        try:
            self.workflow.save_workflow_step("RISK_ASSESSMENT", assessment_text)
            self._log(f"✓ Risk assessment saved to workflow")
            return True
        except Exception as e:
            logger.error(f"Failed to finalize assessment: {e}")
            return False

    # ──────────────────────────────────────────────────────────────────────
    # HELPERS
    # ──────────────────────────────────────────────────────────────────────

    def _log(self, message):
        """Thread-safe logging."""
        if self.context.log_callback:
            self.context.root.after(0, lambda: self.context.log_callback(message))

    def _callback(self, callback, result):
        """Thread-safe callback."""
        if callback:
            self.context.root.after(0, lambda: callback(result))
