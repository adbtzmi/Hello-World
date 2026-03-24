#!/usr/bin/env python3
"""
controller/workflow_controller.py
==================================
Workflow Controller - Phase 2

Manages workflow file state for JIRA issues.
Extracted from gui/app.py lines 370-524.

Responsibilities:
  - Initialize workflow files for issues
  - Load/save workflow steps
  - Parse workflow file format
  - Load workflow files via file dialog
"""

import os
import logging
from datetime import datetime
from tkinter import filedialog, messagebox

logger = logging.getLogger("bento_app")


class WorkflowController:
    """
    Manages workflow file state.
    
    Constructor args:
        context: AppContext - Application context
    """
    
    def __init__(self, context):
        self.context = context
        logger.info("WorkflowController initialized")
    
    def init_workflow_file(self, issue_key):
        """
        Initialize workflow file for an issue.
        Extracted from gui/app.py lines 370-389.
        """
        # Check if we already have a loaded workflow file for this issue
        if self.context.workflow_file:
            basename = os.path.basename(self.context.workflow_file)
            if basename == f"{issue_key}_workflow.txt":
                # Already initialized for this issue, keep using existing file path
                # This supports legacy files in root directory
                self.load_workflow_state()
                return

        # Create Workflows directory if not exists
        workflows_dir = "Workflows"
        if not os.path.exists(workflows_dir):
            os.makedirs(workflows_dir)
            self.context.log(f"Created directory: {workflows_dir}")
            logger.info(f"Created workflows directory: {workflows_dir}")
            
        self.context.workflow_file = os.path.join(workflows_dir, f"{issue_key}_workflow.txt")
        self.load_workflow_state()
        logger.info(f"Initialized workflow file for {issue_key}")
    
    def load_workflow_state(self):
        """
        Load workflow state from file.
        Extracted from gui/app.py lines 390-424.
        """
        if self.context.workflow_file and os.path.exists(self.context.workflow_file):
            try:
                with open(self.context.workflow_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    
                # Parse the workflow file
                self.context.workflow_state = {}
                current_section = None
                current_content = []
                
                for line in content.split('\n'):
                    if line.startswith('=== ') and line.endswith(' ==='):
                        # Save previous section
                        if current_section:
                            self.context.workflow_state[current_section] = '\n'.join(current_content).strip()
                        # Start new section
                        current_section = line.replace('=== ', '').replace(' ===', '').strip()
                        current_content = []
                    elif current_section:
                        current_content.append(line)
                
                # Save last section
                if current_section:
                    self.context.workflow_state[current_section] = '\n'.join(current_content).strip()
                
                self.context.log(f"✓ Loaded workflow state from {self.context.workflow_file}")
                self.context.log(f"  Available sections: {', '.join(self.context.workflow_state.keys())}")
                logger.info(f"Loaded workflow state: {len(self.context.workflow_state)} sections")
            except Exception as e:
                self.context.log(f"⚠ Error loading workflow state: {e}")
                logger.error(f"Error loading workflow state: {e}")
                self.context.workflow_state = {}
        else:
            self.context.workflow_state = {}
    
    def save_workflow_step(self, step_name, content, issue_key=None):
        """
        Save a workflow step to the consolidated file.
        Extracted from gui/app.py lines 425-451.
        """
        if issue_key:
            self.init_workflow_file(issue_key)
        
        if not self.context.workflow_file:
            self.context.log("⚠ No workflow file initialized")
            logger.warning("Attempted to save workflow step without initialized file")
            return
        
        # Update state
        self.context.workflow_state[step_name] = content
        
        # Write entire workflow file
        try:
            with open(self.context.workflow_file, 'w', encoding='utf-8') as f:
                f.write(f"JIRA WORKFLOW STATE FILE\n")
                f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"{'='*80}\n\n")
                
                for section, section_content in self.context.workflow_state.items():
                    f.write(f"=== {section} ===\n")
                    f.write(f"{section_content}\n\n")
            
            self.context.log(f"✓ Saved {step_name} to {self.context.workflow_file}")
            logger.info(f"Saved workflow step: {step_name}")
        except Exception as e:
            self.context.log(f"✗ Error saving workflow step: {e}")
            logger.error(f"Error saving workflow step: {e}")
    
    def get_workflow_step(self, step_name):
        """
        Get a workflow step from state.
        Extracted from gui/app.py lines 525-528.
        """
        return self.context.workflow_state.get(step_name)
    
    def load_workflow_file(self, root_window, callback=None):
        """
        Load a workflow file via file dialog and populate fields.
        Extracted from gui/app.py lines 452-524.
        
        Args:
            root_window: tk.Tk - Root window for file dialog
        
        Returns:
            dict: Workflow data or None if cancelled
        """
        # Determine initial directory
        initial_dir = os.path.join(os.getcwd(), "Workflows")
        if not os.path.exists(initial_dir):
            initial_dir = os.getcwd()
            
        # Open file dialog filtered for workflow files
        filename = filedialog.askopenfilename(
            title="Select Workflow File",
            filetypes=[("Workflow files", "*_workflow.txt"), ("All files", "*.*")],
            initialdir=initial_dir
        )
        
        if not filename:
            return None
        
        try:
            # Set the workflow file
            self.context.workflow_file = filename
            self.load_workflow_state()
            
            # Extract issue key from filename
            issue_key = self.get_workflow_step("ISSUE_KEY") or self.get_workflow_step("ISSUE KEY")
            if not issue_key:
                # Try to extract from filename
                basename = os.path.basename(filename)
                if basename.endswith("_workflow.txt"):
                    issue_key = basename.replace("_workflow.txt", "")
            
            # Get repository info
            repo_info = self.get_workflow_step("REPOSITORY_INFO")
            repo_path = None
            repo_name = None
            base_branch = None
            feature_branch = None
            
            if repo_info:
                # Parse repository info
                for line in repo_info.split('\n'):
                    if line.startswith("Repository:"):
                        repo_name = line.split(":", 1)[1].strip()
                    elif line.startswith("Base branch:"):
                        base_branch = line.split(":", 1)[1].strip()
                    elif line.startswith("Feature branch:"):
                        feature_branch = line.split(":", 1)[1].strip()
                    elif line.startswith("Local path:"):
                        repo_path = line.split(":", 1)[1].strip()
            
            # Log success
            if issue_key:
                self.context.log(f"✓ Loaded workflow for {issue_key}")
            
            if repo_path:
                self.context.log(f"  Auto-populated repo path: {repo_path}")
            
            # Show summary
            sections = list(self.context.workflow_state.keys())
            messagebox.showinfo(
                "Workflow Loaded",
                f"Loaded workflow for {issue_key}\n\n"
                f"Available sections:\n" + "\n".join(f"  • {s}" for s in sections)
            )
            
            logger.info(f"Loaded workflow file: {filename}")
            
            # Return parsed data for caller to populate UI
            result = {
                "issue_key": issue_key,
                "repository": repo_name,
                "base_branch": base_branch,
                "feature_branch": feature_branch,
                "local_path": repo_path,
                "sections": sections
            }
            
            if callback:
                # Use root_window if it's the root, otherwise fallback to result
                if hasattr(root_window, 'after'):
                    root_window.after(0, lambda: callback(result))
                else:
                    callback(result)
                    
            return result
            
        except Exception as e:
            self.context.log(f"✗ Error loading workflow file: {e}")
            logger.error(f"Error loading workflow file: {e}")
            messagebox.showerror("Error", f"Failed to load workflow file:\n{e}")
            return None
