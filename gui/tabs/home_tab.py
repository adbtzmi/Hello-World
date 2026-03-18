import tkinter as tk
from tkinter import ttk, messagebox
from gui.tabs.base_tab import BaseTab

class HomeTab(BaseTab):
    def __init__(self, notebook, context):
        super().__init__(notebook, context, "🏠 Home")
        self.build_ui()

    def build_ui(self):
        # Configuration Section
        config_frame = ttk.LabelFrame(self, text="Configuration", padding="10")
        config_frame.grid(row=0, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=5)
        
        # Row 0: JIRA URL
        ttk.Label(config_frame, text="JIRA URL:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.context.set_var('jira_url', tk.StringVar(value=self.context.config.get('jira', {}).get('base_url', 'https://micron.atlassian.net')))
        ttk.Entry(config_frame, textvariable=self.context.get_var('jira_url'), width=70).grid(row=0, column=1, columnspan=3, pady=2, sticky=tk.W)
        
        # Row 1: Bitbucket URL
        ttk.Label(config_frame, text="Bitbucket URL:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.context.set_var('bb_url', tk.StringVar(value=self.context.config.get('bitbucket', {}).get('base_url', 'https://bitbucket.micron.com/bbdc/scm')))
        ttk.Entry(config_frame, textvariable=self.context.get_var('bb_url'), width=70).grid(row=1, column=1, columnspan=3, pady=2, sticky=tk.W)
        
        # Row 2: Project Keys
        ttk.Label(config_frame, text="Bitbucket Project:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.context.set_var('bb_project', tk.StringVar(value=self.context.config.get('bitbucket', {}).get('project_key', 'TESTSSD')))
        ttk.Entry(config_frame, textvariable=self.context.get_var('bb_project'), width=27).grid(row=2, column=1, pady=2, sticky=tk.W)
        
        ttk.Label(config_frame, text="JIRA Project:").grid(row=2, column=2, sticky=tk.W, pady=2, padx=(20,0))
        self.context.set_var('jira_project', tk.StringVar(value=self.context.config.get('jira', {}).get('project_key', 'TSESSD')))
        ttk.Entry(config_frame, textvariable=self.context.get_var('jira_project'), width=27).grid(row=2, column=3, pady=2, sticky=tk.W)
        
        # Row 3: Model Gateway URL
        ttk.Label(config_frame, text="Model Gateway:").grid(row=3, column=0, sticky=tk.W, pady=2)
        self.context.set_var('model_url', tk.StringVar(value=self.context.config.get('model_gateway', {}).get('base_url', 'https://model-gateway.gcldgenaigw.gc.micron.com/api/v1')))
        ttk.Entry(config_frame, textvariable=self.context.get_var('model_url'), width=70).grid(row=3, column=1, columnspan=3, pady=2, sticky=tk.W)
        
        # Row 4: Debug Checkbox
        self.context.set_var('debug_var', tk.BooleanVar(value=False))
        ttk.Checkbutton(config_frame, text="Enable Debug Mode", variable=self.context.get_var('debug_var')).grid(row=4, column=0, columnspan=2, sticky=tk.W, pady=2)

        # Row 5: Save and Test buttons
        config_btn_frame = ttk.Frame(config_frame)
        config_btn_frame.grid(row=5, column=0, columnspan=4, pady=5)
        ttk.Button(config_btn_frame, text="Save Config", command=self._save_config).pack(side=tk.LEFT, padx=5)
        
        # Task Section (in home tab)
        task_frame = ttk.LabelFrame(self, text="Task Details - Full Workflow", padding="10")
        task_frame.grid(row=2, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=5)
        
        # JIRA Issue with pre-filled prefix
        ttk.Label(task_frame, text="JIRA Issue:").grid(row=0, column=0, sticky=tk.W, pady=2)
        issue_val = f"{self.context.get_var('jira_project').get()}-"
        self.context.set_var('issue_var', tk.StringVar(value=issue_val))
        ttk.Entry(task_frame, textvariable=self.context.get_var('issue_var'), width=70).grid(row=0, column=1, pady=2)
        
        ttk.Label(task_frame, text="Repository:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.context.set_var('repo_var', tk.StringVar())
        self.repo_combo = ttk.Combobox(task_frame, textvariable=self.context.get_var('repo_var'), width=67)
        self.repo_combo.grid(row=1, column=1, pady=2)
        
        ttk.Label(task_frame, text="Base Branch:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.context.set_var('branch_var', tk.StringVar())
        self.branch_combo = ttk.Combobox(task_frame, textvariable=self.context.get_var('branch_var'), width=67)
        self.branch_combo.grid(row=2, column=1, pady=2)
        
        ttk.Label(task_frame, text="Feature Branch:").grid(row=3, column=0, sticky=tk.W, pady=2)
        self.context.set_var('feature_branch_var', tk.StringVar(value="If empty, will automatically be named as 'feature/TSESSD-XXXX'"))
        ttk.Entry(task_frame, textvariable=self.context.get_var('feature_branch_var'), width=70).grid(row=3, column=1, pady=2)
        
        workflow_btn_frame = ttk.Frame(task_frame)
        workflow_btn_frame.grid(row=4, column=0, columnspan=2, pady=10)
        ttk.Button(workflow_btn_frame, text="🚀 Start Full Analysis Workflow", style='Accent.TButton').pack(side=tk.LEFT, padx=5)

    def _save_config(self):
        ui_vars = {k: v.get() for k, v in self.context.vars.items() if hasattr(v, 'get')}
        self.context.save_config(ui_vars)
