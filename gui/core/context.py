import os
import json
import logging
from datetime import datetime

class AppContext:
    """ViewModel holding the application state and core services for the BENTO GUI."""
    def __init__(self, root, analyzer, config, log_callback):
        self.root = root
        self.analyzer = analyzer
        self.config = config
        self.log = log_callback
        
        self.workflow_file = None
        self.workflow_state = {}
        
        # We can store Tkinter variables here so tabs can share them
        self.vars = {}
        
        # State
        self.repos = []
        self.branches = []
        self.gui_locked = False
        
    def get_var(self, name):
        """Retrieve a shared Tkinter variable"""
        return self.vars.get(name)
        
    def set_var(self, name, tk_var):
        """Store a shared Tkinter variable"""
        self.vars[name] = tk_var

    # ---- Configuration Management ----
    def save_config(self, ui_vars):
        """Save configuration to settings.json and model configs to ai_modes.json"""
        try:
            with open('settings.json', 'r') as f:
                existing_config = json.load(f)
        except Exception:
            existing_config = {}
            
        try:
            config = {
                'jira': {
                    'base_url': ui_vars.get('jira_url', 'https://micron.atlassian.net'),
                    'project_key': ui_vars.get('jira_project', 'TSESSD')
                },
                'bitbucket': {
                    'base_url': ui_vars.get('bb_url', 'https://bitbucket.micron.com/bbdc/scm'),
                    'project_key': ui_vars.get('bb_project', 'TESTSSD')
                },
                'model_gateway': {
                    'base_url': ui_vars.get('model_url', 'https://model-gateway.gcldgenaigw.gc.micron.com/api/v1')
                },
                'settings': existing_config.get('settings', {'timeout': 300}),
                'compile': {
                    'last_tgz_label': ui_vars.get('tgz_label', "")
                }
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
                    'max_tokens': modes_data.get('models', {}).get('analysis', {}).get('max_tokens', 2000)
                },
                'code_generation': {
                    'name': ui_vars.get('code_model', 'claude-sonnet-4-5'),
                    'temperature': modes_data.get('models', {}).get('code_generation', {}).get('temperature', 0.3),
                    'max_tokens': modes_data.get('models', {}).get('code_generation', {}).get('max_tokens', 4000)
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

    # ---- Workflow State Management ----
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
                f.write(f"JIRA WORKFLOW STATE FILE\n")
                f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"{'='*80}\n\n")
                
                for section, section_content in self.workflow_state.items():
                    f.write(f"=== {section} ===\n")
                    f.write(f"{section_content}\n\n")
            
            self.log(f"✓ Saved {step_name} to {self.workflow_file}")
        except Exception as e:
            self.log(f"✗ Error saving workflow step: {e}")