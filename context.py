import os
import json
import logging
from datetime import datetime
from typing import Any, Callable, Optional


class AppContext:
    """
    Shared state object passed to every new MVC tab in BENTO.

    Holds references to:
      - root      : Tk root window
      - analyzer  : JIRAAnalyzer instance from existing SimpleGUI
      - config    : settings.json dict
      - log       : callable(str) — pipes to existing GUI log panel
      - controller: BentoController — wired by main.py after construction
      - vars      : shared Tkinter variables across tabs
    """

    # Declare types so Pylance accepts later assignments
    analyzer:   Any
    controller: Any

    def __init__(self, root, analyzer, config, log_callback):
        self.root       = root
        self.analyzer:   Any = analyzer
        self.config     = config
        self.log        = log_callback
        self.controller: Any = None     # wired by main.py after BentoController is ready

        self.workflow_file  = None
        self.workflow_state = {}

        # Shared Tkinter variables — tabs store and retrieve via get_var / set_var
        self.vars     = {}
        self.repos    = []
        self.branches = []
        self.branches = []
        self.gui_locked = False
        self.lockable_buttons = []

        
        # Phase 2 additions
        self.chat_window = None  # active chat window reference
        self.current_chat_messages = []  # chat history
        
        # Observers for syncing (replaces trace_add callbacks)
        self._issue_observers = []
        self._repo_observers = []
        self._branch_observers = []
        
        # File logger reference (set by main app)
        self.file_logger = None

    # ── log_callback alias (main.py sets app.context.log_callback) ────────

    @property
    def log_callback(self):
        """Alias for self.log — allows main.py to read/write via log_callback."""
        return self.log

    @log_callback.setter
    def log_callback(self, value):
        self.log = value

    # ── Shared variable store ─────────────────────────────────────────────

    def get_var(self, name):
        """Retrieve a shared Tkinter variable by name."""
        return self.vars.get(name)

    def set_var(self, name, tk_var):
        """Store a shared Tkinter variable by name."""
        self.vars[name] = tk_var
    
    # ── Observer Pattern for Syncing ──────────────────────────────────────
    
    def register_issue_observer(self, callback):
        """Register callback for issue key changes"""
        self._issue_observers.append(callback)
    
    def register_repo_observer(self, callback):
        """Register callback for repo changes"""
        self._repo_observers.append(callback)
    
    def register_branch_observer(self, callback):
        """Register callback for branch changes"""
        self._branch_observers.append(callback)
    
    def get_issue_key(self) -> str:
        """Return current JIRA issue key from vars"""
        issue_var = self.vars.get('issue_key')
        return issue_var.get() if issue_var else ""
    
    def set_issue_key(self, key: str):
        """Set issue key and notify all observers"""
        issue_var = self.vars.get('issue_key')
        if issue_var:
            issue_var.set(key)
        for observer in self._issue_observers:
            observer(key)
    
    def get_repo(self) -> str:
        """Return current repo name"""
        repo_var = self.vars.get('repo')
        return repo_var.get() if repo_var else ""
    
    def set_repo(self, repo: str):
        """Set repo and notify all observers"""
        repo_var = self.vars.get('repo')
        if repo_var:
            repo_var.set(repo)
        for observer in self._repo_observers:
            observer(repo)
    
    def get_branch(self) -> str:
        """Return current branch name"""
        branch_var = self.vars.get('branch')
        return branch_var.get() if branch_var else ""
    
    def set_branch(self, branch: str):
        """Set branch and notify all observers"""
        branch_var = self.vars.get('branch')
        if branch_var:
            branch_var.set(branch)
        for observer in self._branch_observers:
            observer(branch)

    # ── Configuration management ──────────────────────────────────────────

    def save_config(self, ui_vars):
        """Save configuration to settings.json and model configs to ai_modes.json."""
        try:
            with open('settings.json', 'r') as f:
                existing_config = json.load(f)
        except Exception:
            existing_config = {}

        try:
            config = {
                'jira': {
                    'base_url':    ui_vars.get('jira_url', 'https://micron.atlassian.net'),
                    'project_key': ui_vars.get('jira_project', 'TSESSD')
                },
                'bitbucket': {
                    'base_url':    ui_vars.get('bb_url', 'https://bitbucket.micron.com/bbdc/scm'),
                    'project_key': ui_vars.get('bb_project', 'TESTSSD')
                },
                'model_gateway': {
                    'base_url': ui_vars.get('model_url', 'https://model-gateway.gcldgenaigw.gc.micron.com/api/v1')
                },
                'settings': existing_config.get('settings', {'timeout': 300}),
                'compile':  {'last_tgz_label': ui_vars.get('tgz_label', '')}
            }
            with open('settings.json', 'w') as f:
                json.dump(config, f, indent=2)

            # Save AI modes
            try:
                with open('ai_modes.json', 'r') as f:
                    modes_data = json.load(f)
            except Exception:
                modes_data = {}

            modes_data['models'] = {
                'analysis': {
                    'name': ui_vars.get('analysis_model', 'gemini-3.0-pro-preview'),
                    'temperature': modes_data.get('models', {}).get('analysis', {}).get('temperature', 0.7),
                    'max_tokens':  modes_data.get('models', {}).get('analysis', {}).get('max_tokens', 2000)
                },
                'code_generation': {
                    'name': ui_vars.get('code_model', 'claude-sonnet-4-5'),
                    'temperature': modes_data.get('models', {}).get('code_generation', {}).get('temperature', 0.3),
                    'max_tokens':  modes_data.get('models', {}).get('code_generation', {}).get('max_tokens', 4000)
                }
            }
            with open('ai_modes.json', 'w') as f:
                json.dump(modes_data, f, indent=2)

            self.log("✓ Configuration saved to settings.json")
            self.log("✓ Model configuration saved to ai_modes.json")
            return True

        except Exception as e:
            self.log(f"✗ Error saving config: {e}")
            return False

    # ── Workflow state management ─────────────────────────────────────────

    def init_workflow_file(self, issue_key):
        if self.workflow_file:
            basename = os.path.basename(self.workflow_file)
            if basename == f"{issue_key}_workflow.txt":
                self._load_workflow_state()
                return

        workflows_dir = "Workflows"
        if not os.path.exists(workflows_dir):
            os.makedirs(workflows_dir)
            self.log(f"Created directory: {workflows_dir}")

        self.workflow_file = os.path.join(workflows_dir, f"{issue_key}_workflow.txt")
        self._load_workflow_state()

    def _load_workflow_state(self):
        if self.workflow_file and os.path.exists(self.workflow_file):
            try:
                with open(self.workflow_file, 'r', encoding='utf-8') as f:
                    content = f.read()

                self.workflow_state = {}
                current_section = None
                current_content = []

                for line in content.split('\n'):
                    if line.startswith('=== ') and line.endswith(' ==='):
                        if current_section:
                            self.workflow_state[current_section] = '\n'.join(current_content).strip()
                        current_section = line.replace('=== ', '').replace(' ===', '').strip()
                        current_content = []
                    elif current_section:
                        current_content.append(line)

                if current_section:
                    self.workflow_state[current_section] = '\n'.join(current_content).strip()

                self.log(f"✓ Loaded workflow state from {self.workflow_file}")
            except Exception as e:
                self.log(f"⚠ Error loading workflow state: {e}")
                self.workflow_state = {}
        else:
            self.workflow_state = {}

    def get_workflow_step(self, step_name):
        return self.workflow_state.get(step_name)

    def save_workflow_step(self, step_name, content, issue_key=None):
        if issue_key:
            self.init_workflow_file(issue_key)

        if not self.workflow_file:
            self.log("⚠ No workflow file initialized")
            return

        self.workflow_state[step_name] = content

        try:
            with open(self.workflow_file, 'w', encoding='utf-8') as f:
                f.write("JIRA WORKFLOW STATE FILE\n")
                f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"{'='*80}\n\n")
                for section, section_content in self.workflow_state.items():
                    f.write(f"=== {section} ===\n")
                    f.write(f"{section_content}\n\n")

            self.log(f"✓ Saved {step_name} to {self.workflow_file}")
        except Exception as e:
            self.log(f"✗ Error saving workflow step: {e}")
