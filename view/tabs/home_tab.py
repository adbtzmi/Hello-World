import tkinter as tk
from tkinter import ttk, messagebox
from view.tabs.base_tab import BaseTab


class HomeTab(BaseTab):
    """
    Home Tab (View)
    ===============
    JIRA / Bitbucket / Model Gateway configuration.
    Also provides the Full Workflow trigger (Step 1: JIRA analysis).

    User actions dispatch to JiraController via AppContext.controller.
    """

    def __init__(self, notebook, context):
        super().__init__(notebook, context, "🏠 Home")
        self.build_ui()

    # ──────────────────────────────────────────────────────────────────────
    # BUILD UI
    # ──────────────────────────────────────────────────────────────────────

    def build_ui(self):
        self.columnconfigure(0, weight=1)

        # ── Configuration Section ─────────────────────────────────────────
        config_frame = ttk.LabelFrame(self, text="Configuration", padding="10")
        config_frame.grid(row=0, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=5)

        # Row 0: JIRA URL
        ttk.Label(config_frame, text="JIRA URL:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.context.set_var('jira_url', tk.StringVar(
            value=self.context.config.get('jira', {}).get('base_url', 'https://micron.atlassian.net')))
        ttk.Entry(config_frame, textvariable=self.context.get_var('jira_url'), width=70).grid(
            row=0, column=1, columnspan=3, pady=2, sticky=tk.W)

        # Row 1: Bitbucket URL
        ttk.Label(config_frame, text="Bitbucket URL:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.context.set_var('bb_url', tk.StringVar(
            value=self.context.config.get('bitbucket', {}).get('base_url', 'https://bitbucket.micron.com/bbdc/scm')))
        ttk.Entry(config_frame, textvariable=self.context.get_var('bb_url'), width=70).grid(
            row=1, column=1, columnspan=3, pady=2, sticky=tk.W)

        # Row 2: Project Keys
        ttk.Label(config_frame, text="Bitbucket Project:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.context.set_var('bb_project', tk.StringVar(
            value=self.context.config.get('bitbucket', {}).get('project_key', 'TESTSSD')))
        ttk.Entry(config_frame, textvariable=self.context.get_var('bb_project'), width=27).grid(
            row=2, column=1, pady=2, sticky=tk.W)

        ttk.Label(config_frame, text="JIRA Project:").grid(row=2, column=2, sticky=tk.W, pady=2, padx=(20, 0))
        self.context.set_var('jira_project', tk.StringVar(
            value=self.context.config.get('jira', {}).get('project_key', 'TSESSD')))
        ttk.Entry(config_frame, textvariable=self.context.get_var('jira_project'), width=27).grid(
            row=2, column=3, pady=2, sticky=tk.W)

        # Row 3: Model Gateway URL
        ttk.Label(config_frame, text="Model Gateway:").grid(row=3, column=0, sticky=tk.W, pady=2)
        self.context.set_var('model_url', tk.StringVar(
            value=self.context.config.get('model_gateway', {}).get(
                'base_url', 'https://model-gateway.gcldgenaigw.gc.micron.com/api/v1')))
        ttk.Entry(config_frame, textvariable=self.context.get_var('model_url'), width=70).grid(
            row=3, column=1, columnspan=3, pady=2, sticky=tk.W)

        # Row 4: Debug toggle
        self.context.set_var('debug_var', tk.BooleanVar(value=False))
        ttk.Checkbutton(config_frame, text="Enable Debug Mode",
                        variable=self.context.get_var('debug_var')).grid(
            row=4, column=0, columnspan=2, sticky=tk.W, pady=2)

        # Row 5: Save Config button
        config_btn_frame = ttk.Frame(config_frame)
        config_btn_frame.grid(row=5, column=0, columnspan=4, pady=5)
        ttk.Button(config_btn_frame, text="Save Config",
                   command=self._save_config).pack(side=tk.LEFT, padx=5)

        # ── Task / Workflow Section ───────────────────────────────────────
        task_frame = ttk.LabelFrame(self, text="Task Details - Full Workflow", padding="10")
        task_frame.grid(row=2, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=5)

        # JIRA Issue
        ttk.Label(task_frame, text="JIRA Issue:").grid(row=0, column=0, sticky=tk.W, pady=2)
        issue_prefix = f"{self.context.config.get('jira', {}).get('project_key', 'TSESSD')}-"
        self.context.set_var('issue_var', tk.StringVar(value=issue_prefix))
        ttk.Entry(task_frame, textvariable=self.context.get_var('issue_var'), width=70).grid(
            row=0, column=1, pady=2)

        # Repository
        ttk.Label(task_frame, text="Repository:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.context.set_var('repo_var', tk.StringVar())
        self.repo_combo = ttk.Combobox(task_frame, textvariable=self.context.get_var('repo_var'), width=67)
        self.repo_combo.grid(row=1, column=1, pady=2)

        # Base Branch
        ttk.Label(task_frame, text="Base Branch:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.context.set_var('branch_var', tk.StringVar())
        self.branch_combo = ttk.Combobox(task_frame, textvariable=self.context.get_var('branch_var'), width=67)
        self.branch_combo.grid(row=2, column=1, pady=2)

        # Feature Branch
        ttk.Label(task_frame, text="Feature Branch:").grid(row=3, column=0, sticky=tk.W, pady=2)
        self.context.set_var('feature_branch_var', tk.StringVar(
            value="If empty, will automatically be named as 'feature/TSESSD-XXXX'"))
        ttk.Entry(task_frame, textvariable=self.context.get_var('feature_branch_var'), width=70).grid(
            row=3, column=1, pady=2)

        # Start Workflow button
        workflow_btn_frame = ttk.Frame(task_frame)
        workflow_btn_frame.grid(row=4, column=0, columnspan=2, pady=10)
        ttk.Button(workflow_btn_frame, text="🚀 Start Full Analysis Workflow",
                   style='Accent.TButton',
                   command=self._start_workflow).pack(side=tk.LEFT, padx=5)

        # ── Status label ──────────────────────────────────────────────────
        self.status_label = ttk.Label(self, text="")
        self.status_label.grid(row=3, column=0, sticky=tk.W, pady=2)

    # ──────────────────────────────────────────────────────────────────────
    # USER ACTIONS → dispatch to controller
    # ──────────────────────────────────────────────────────────────────────

    def _save_config(self):
        ui_vars = {k: v.get() for k, v in self.context.vars.items() if hasattr(v, 'get')}
        self.context.save_config(ui_vars)

    def _start_workflow(self):
        issue_key = self.context.get_var('issue_var').get().strip()
        if not issue_key or issue_key.endswith("-"):
            self.show_error("Input Error", "Please enter a valid JIRA issue key (e.g. TSESSD-1234).")
            return
        # Dispatch to JiraController via master controller
        self.context.controller.jira_controller.start_workflow(issue_key)

    # ──────────────────────────────────────────────────────────────────────
    # VIEW CALLBACKS — called by controller to update display
    # ──────────────────────────────────────────────────────────────────────

    def on_jira_analysis_completed(self, result: dict):
        """Called by BentoApp (relayed from JiraController) when analysis finishes."""
        issue_key = result.get("issue_key", "")
        self.status_label.configure(text=f"✓ Analysis complete: {issue_key}", foreground="green")
