# -*- coding: utf-8 -*-
"""
controller/implementation_controller.py
========================================
Implementation Controller - Phase 3C

Bridges ImplementationTab (View) <-> jira_analyzer.py (Model).
Handles AI-powered implementation plan generation.
"""

import os
import json
import logging
import threading

logger = logging.getLogger("bento_app")


class ImplementationController:
    """
    Bridges ImplementationTab <-> jira_analyzer.py.
    Handles implementation plan generation with AI.
    """

    def __init__(self, context, workflow_ctrl, chat_ctrl):
        self.context = context
        self.workflow = workflow_ctrl
        self.chat = chat_ctrl
        self.analyzer = context.analyzer
        self._running = False
        logger.info("ImplementationController initialised.")

    def is_running(self):
        return self._running

    # ──────────────────────────────────────────────────────────────────────
    # GENERATE IMPLEMENTATION PLAN
    # ──────────────────────────────────────────────────────────────────────

    def generate_implementation_plan(self, issue_key, repo_path, callback):
        """
        Generate implementation plan using AI.
        
        Args:
            issue_key: JIRA issue key
            repo_path: Local repository path
            callback: Function to call with result dict
        """
        if self._running:
            logger.warning("ImplementationController: already running")
            return

        self._running = True
        threading.Thread(
            target=self._generate_impl_thread,
            args=(issue_key, repo_path, callback),
            daemon=True,
            name="bento-impl-gen"
        ).start()

    def _generate_impl_thread(self, issue_key, repo_path, callback):
        """Background thread for implementation plan generation."""
        try:
            self._log(f"[Generating Implementation Plan for {issue_key}]")

            # Initialize workflow
            self.workflow.init_workflow_file(issue_key)

            # Check for existing plan
            existing_plan = self.workflow.get_workflow_step("IMPLEMENTATION_PLAN")
            if existing_plan:
                self._log("✓ Found existing implementation plan in workflow")
                # Extract content (skip header lines)
                plan_lines = existing_plan.split('\n')
                plan_content = '\n'.join([
                    line for line in plan_lines
                    if not line.startswith('#') and not line.startswith('**')
                ])
                self._callback(callback, {
                    'success': True,
                    'plan': plan_content.strip(),
                    'from_cache': True
                })
                return

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

            # Generate implementation plan
            self._log("Generating implementation plan with AI...")
            plan = self._generate_plan_with_ai(
                repo_path, jira_analysis, impact_analysis, repo_index
            )

            if plan:
                self._log("✓ Implementation plan generated")
                self._callback(callback, {
                    'success': True,
                    'plan': plan,
                    'from_cache': False
                })
            else:
                self._callback(callback, {
                    'success': False,
                    'error': "Failed to generate implementation plan"
                })

        except Exception as e:
            logger.error(f"Implementation generation error: {e}")
            self._callback(callback, {
                'success': False,
                'error': str(e)
            })
        finally:
            self._running = False

    def _generate_plan_with_ai(self, repo_path, jira_analysis, impact_analysis, repo_index):
        """Generate implementation plan using AI."""
        # Get key files
        key_files = []
        for file_info in repo_index['file_index'][:20]:
            file_path = os.path.join(repo_path, file_info['path'])
            if os.path.isfile(file_path) and file_info['size'] < 100000:
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                        key_files.append({
                            'path': file_info['path'],
                            'content': content[:2000]
                        })
                except Exception:
                    pass

        # Build prompt
        impl_prompt = f"""Based on the JIRA analysis and code impact assessment, provide specific code changes:

**JIRA Analysis:**
{jira_analysis.get('analysis', 'N/A')}

**Impact Analysis:**
{impact_analysis.get('impact_analysis', 'N/A')}

**Sample Code Files:**
{json.dumps(key_files[:5], indent=2)}

Provide implementation in this EXACT format:

## FILES_TO_MODIFY
- path/to/file1.py: Brief description of changes
- path/to/file2.js: Brief description of changes

## CODE_CHANGES
### path/to/file1.py
```python
# Complete updated code for this file
```

### path/to/file2.js
```javascript
// Complete updated code for this file
```

## NEW_FILES
### path/to/newfile.py
```python
# Complete code for new file
```

## IMPLEMENTATION_GUIDE
Step-by-step guide for manual review and additional changes.
"""

        messages = [{"role": "user", "content": impl_prompt}]
        result = self.analyzer.ai_client.chat_completion(
            messages, task_type="code_generation", mode="code_implementation"
        )

        if result['success']:
            return result['response']['choices'][0]['message']['content']
        else:
            logger.error(f"AI generation failed: {result.get('error')}")
            return None

    # ──────────────────────────────────────────────────────────────────────
    # FINALIZE PLAN
    # ──────────────────────────────────────────────────────────────────────

    def finalize_plan(self, issue_key, plan_text):
        """Save finalized implementation plan to workflow."""
        try:
            # Format with header
            formatted_plan = f"""# Implementation Plan

**JIRA Issue:** {issue_key}
**Generated:** {self._get_timestamp()}

{plan_text}
"""
            self.workflow.save_workflow_step("IMPLEMENTATION_PLAN", formatted_plan)
            self._log(f"✓ Implementation plan saved to workflow")
            return True
        except Exception as e:
            logger.error(f"Failed to finalize plan: {e}")
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

    def _get_timestamp(self):
        """Get formatted timestamp."""
        from datetime import datetime
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
