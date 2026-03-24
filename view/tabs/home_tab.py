import tkinter as tk
from tkinter import ttk, messagebox
from view.tabs.base_tab import BaseTab


class HomeTab(BaseTab):
    """
    Home Tab (View) — Phase 2
    Configuration + Full Workflow trigger.
    Dispatches to JiraController via context.controller.
    """

    def __init__(self, notebook, context):
        super().__init__(notebook, context, "🏠 Home")
        self.build_ui()

    def build_ui(self):
        self.columnconfigure(0, weight=1)

        # ── Configuration Section ──────────────────────────────────────────
        config_frame = ttk.LabelFrame(self, text="Configuration", padding="10")
        config_frame.grid(row=0, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=5)

        # JIRA URL
        ttk.Label(config_frame, text="JIRA URL:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.context.set_var('jira_url', tk.StringVar(
            value=self.context.config.get('jira', {}).get('base_url', 'https://micron.atlassian.net')))
        ttk.Entry(config_frame, textvariable=self.context.get_var('jira_url'), width=70).grid(
            row=0, column=1, columnspan=3, pady=2, sticky=tk.W)

        # Bitbucket URL
        ttk.Label(config_frame, text="Bitbucket URL:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.context.set_var('bb_url', tk.StringVar(
            value=self.context.config.get('bitbucket', {}).get('base_url', 'https://bitbucket.micron.com/bbdc/scm')))
        ttk.Entry(config_frame, textvariable=self.context.get_var('bb_url'), width=70).grid(
            row=1, column=1, columnspan=3, pady=2, sticky=tk.W)

        # Project Keys
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

        # Model Gateway
        ttk.Label(config_frame, text="Model Gateway:").grid(row=3, column=0, sticky=tk.W, pady=2)
        self.context.set_var('model_url', tk.StringVar(
            value=self.context.config.get('model_gateway', {}).get(
                'base_url', 'https://model-gateway.gcldgenaigw.gc.micron.com/api/v1')))
        ttk.Entry(config_frame, textvariable=self.context.get_var('model_url'), width=70).grid(
            row=3, column=1, columnspan=3, pady=2, sticky=tk.W)

        # Debug toggle with trace to update indicator
        self.context.set_var('debug_var', tk.BooleanVar(value=False))
        self.context.get_var('debug_var').trace_add('write', self._toggle_debug_mode)
        ttk.Checkbutton(config_frame, text="Enable Debug Mode",
                        variable=self.context.get_var('debug_var')).grid(
            row=4, column=0, columnspan=2, sticky=tk.W, pady=2)

        # Save Config and Test Config buttons
        config_btn_frame = ttk.Frame(config_frame)
        config_btn_frame.grid(row=5, column=0, columnspan=4, pady=5)
        ttk.Button(config_btn_frame, text="Save Config",
                   command=self._save_config).pack(side=tk.LEFT, padx=5)
        ttk.Button(config_btn_frame, text="Test Config",
                   command=self._test_config).pack(side=tk.LEFT, padx=5)

        # ── Credentials Section ────────────────────────────────────────────
        cred_frame = ttk.LabelFrame(self, text="Credentials (Encrypted)", padding="10")
        cred_frame.grid(row=1, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=5)

        # Email
        ttk.Label(cred_frame, text="Email:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.context.set_var('email_var', tk.StringVar())
        ttk.Entry(cred_frame, textvariable=self.context.get_var('email_var'), width=70).grid(
            row=0, column=1, columnspan=2, pady=2, sticky=tk.W)

        # JIRA Token with show/hide button
        ttk.Label(cred_frame, text="JIRA Token:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.context.set_var('jira_token_var', tk.StringVar())
        self.jira_token_entry = ttk.Entry(cred_frame,
                                           textvariable=self.context.get_var('jira_token_var'),
                                           show='*', width=70)
        self.jira_token_entry.grid(row=1, column=1, pady=2, sticky=tk.W)
        ttk.Button(cred_frame, text="👁", width=3,
                   command=lambda: self._toggle_password_visibility(self.jira_token_entry)).grid(
            row=1, column=2, padx=(2, 0), pady=2)

        # Bitbucket Token with show/hide button
        ttk.Label(cred_frame, text="Bitbucket Token:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.context.set_var('bb_token_var', tk.StringVar())
        self.bb_token_entry = ttk.Entry(cred_frame,
                                         textvariable=self.context.get_var('bb_token_var'),
                                         show='*', width=70)
        self.bb_token_entry.grid(row=2, column=1, pady=2, sticky=tk.W)
        ttk.Button(cred_frame, text="👁", width=3,
                   command=lambda: self._toggle_password_visibility(self.bb_token_entry)).grid(
            row=2, column=2, padx=(2, 0), pady=2)

        # Model API Key with show/hide button
        ttk.Label(cred_frame, text="Model API Key:").grid(row=3, column=0, sticky=tk.W, pady=2)
        self.context.set_var('model_key_var', tk.StringVar())
        self.model_key_entry = ttk.Entry(cred_frame,
                                          textvariable=self.context.get_var('model_key_var'),
                                          show='*', width=70)
        self.model_key_entry.grid(row=3, column=1, pady=2, sticky=tk.W)
        ttk.Button(cred_frame, text="👁", width=3,
                   command=lambda: self._toggle_password_visibility(self.model_key_entry)).grid(
            row=3, column=2, padx=(2, 0), pady=2)

        # Credential buttons
        cred_btn_frame = ttk.Frame(cred_frame)
        cred_btn_frame.grid(row=4, column=0, columnspan=3, pady=5)
        ttk.Button(cred_btn_frame, text="Load Credentials",
                   command=self._load_credentials).pack(side=tk.LEFT, padx=2)
        ttk.Button(cred_btn_frame, text="Save",
                   command=self._save_credentials).pack(side=tk.LEFT, padx=2)

        # ── Task / Workflow Section ────────────────────────────────────────
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
        self.repo_combo = ttk.Combobox(task_frame,
                                        textvariable=self.context.get_var('repo_var'), width=67)
        self.repo_combo.grid(row=1, column=1, pady=2)

        # Base Branch
        ttk.Label(task_frame, text="Base Branch:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.context.set_var('branch_var', tk.StringVar())
        self.branch_combo = ttk.Combobox(task_frame,
                                          textvariable=self.context.get_var('branch_var'), width=67)
        self.branch_combo.grid(row=2, column=1, pady=2)

        # Feature Branch
        ttk.Label(task_frame, text="Feature Branch:").grid(row=3, column=0, sticky=tk.W, pady=2)
        self.context.set_var('feature_branch_var', tk.StringVar(
            value="If empty, will automatically be named as 'feature/TSESSD-XXXX'"))
        ttk.Entry(task_frame, textvariable=self.context.get_var('feature_branch_var'), width=70).grid(
            row=3, column=1, pady=2)

        # Workflow buttons
        workflow_btn_frame = ttk.Frame(task_frame)
        workflow_btn_frame.grid(row=4, column=0, columnspan=2, pady=10)
        ttk.Button(workflow_btn_frame, text="📂 Load Workflow",
                   command=self._load_workflow).pack(side=tk.LEFT, padx=5)
        ttk.Button(workflow_btn_frame, text="🚀 Start Full Analysis Workflow",
                   style='Accent.TButton',
                   command=self._start_workflow).pack(side=tk.LEFT, padx=5)

        # ── Debug Mode Indicator (initially hidden) ────────────────────────
        self.debug_indicator = ttk.Label(self, text="🐛 DEBUG MODE",
                                         font=('Arial', 10, 'bold'),
                                         foreground='red',
                                         background='yellow',
                                         padding="5")
        # Don't grid it yet - will be shown when debug mode is enabled

        # Status label
        self.status_label = ttk.Label(self, text="")
        self.status_label.grid(row=4, column=0, sticky=tk.W, pady=2)

    # ──────────────────────────────────────────────────────────────────────
    # USER ACTIONS
    # ──────────────────────────────────────────────────────────────────────

    def _save_config(self):
        """Save configuration to settings.json"""
        ui_vars = {k: v.get() for k, v in self.context.vars.items() if hasattr(v, 'get')}
        self.context.save_config(ui_vars)
        self.log("✓ Configuration saved")

    def _test_config(self):
        """Test configuration connectivity"""
        if not self.context.controller.config_controller:
            self.show_error("Error", "Config controller not initialized")
            return

        self.log("Testing configuration...")
        
        def _on_result(results):
            if 'error' in results:
                self.show_error("Test Failed", f"Configuration test failed:\n{results['error']}")
                return
            
            # Display results
            msg = "Configuration Test Results:\n\n"
            for service, result in results.items():
                if result.get('success'):
                    msg += f"✓ {service.upper()}: {result.get('message', 'OK')}\n"
                else:
                    msg += f"✗ {service.upper()}: {result.get('error', 'Failed')}\n"
            
            self.show_info("Test Results", msg)
            self.log("✓ Configuration test complete")

        self.context.controller.config_controller.test_config(_on_result)

    def _toggle_password_visibility(self, entry_widget):
        """Toggle between showing and hiding password in an entry widget"""
        if entry_widget.cget('show') == '*':
            entry_widget.configure(show='')
        else:
            entry_widget.configure(show='*')

    def _load_credentials(self):
        """Load credentials from encrypted file"""
        if not self.context.controller.credential_controller:
            self.show_error("Error", "Credential controller not initialized")
            return

        self.log("Loading credentials...")
        
        def _on_loaded(credentials):
            if not credentials:
                self.show_error("Load Failed", "Failed to load credentials.\nCheck password or file integrity.")
                return
            
            # Populate fields
            self.context.get_var('email_var').set(credentials.get('email', ''))
            self.context.get_var('jira_token_var').set(credentials.get('jira_token', ''))
            self.context.get_var('bb_token_var').set(credentials.get('bitbucket_token', ''))
            self.context.get_var('model_key_var').set(credentials.get('model_api_key', ''))
            
            # Update analyzer credentials
            if self.context.analyzer:
                self.context.analyzer.email = credentials.get('email', '')
                self.context.analyzer.bitbucket_username = credentials.get('bitbucket_username', '')
                self.context.analyzer.jira_token = credentials.get('jira_token', '')
                self.context.analyzer.bitbucket_token = credentials.get('bitbucket_token', '')
                self.context.analyzer.model_api_key = credentials.get('model_api_key', '')
                
                # Reinitialize AI client with new key
                if credentials.get('model_api_key'):
                    from jira_analyzer import AIGatewayClient
                    self.context.analyzer.ai_client = AIGatewayClient(credentials['model_api_key'])
            
            self.log("✓ Credentials loaded successfully")
            self.show_info("Success", "Credentials loaded successfully!")

        self.context.controller.credential_controller.load_credentials(_on_loaded)

    def _save_credentials(self):
        """Save credentials to encrypted file"""
        if not self.context.controller.credential_controller:
            self.show_error("Error", "Credential controller not initialized")
            return

        email = self.context.get_var('email_var').get().strip()
        jira_token = self.context.get_var('jira_token_var').get().strip()
        bb_token = self.context.get_var('bb_token_var').get().strip()
        model_key = self.context.get_var('model_key_var').get().strip()

        if not all([email, jira_token, bb_token, model_key]):
            self.show_error("Input Error", "All credential fields are required")
            return

        self.log("Saving credentials...")
        
        def _on_saved(success):
            if success:
                self.log("✓ Credentials saved successfully")
                self.show_info("Success", "Credentials saved to encrypted file!")
            else:
                self.show_error("Save Failed", "Failed to save credentials.\nCheck the log for details.")

        self.context.controller.credential_controller.save_credentials(
            email, jira_token, bb_token, model_key, _on_saved)

    def _toggle_debug_mode(self, *args):
        """Toggle debug mode indicator and update analyzer"""
        enabled = self.context.get_var('debug_var').get()
        
        if enabled:
            # Show debug indicator
            self.debug_indicator.grid(row=3, column=0, sticky=tk.W, pady=2)
            self.log("🐛 DEBUG MODE ENABLED")
        else:
            # Hide debug indicator
            self.debug_indicator.grid_forget()
            self.log("DEBUG MODE DISABLED")
        
        # Update analyzer debug mode
        if self.context.controller.credential_controller:
            self.context.controller.credential_controller.toggle_debug_mode(enabled)

    def _load_workflow(self):
        """Load a workflow file"""
        if not self.context.controller.workflow_controller:
            self.show_error("Error", "Workflow controller not initialized")
            return
        
        self.context.controller.workflow_controller.load_workflow_file(self._on_workflow_loaded)

    def _on_workflow_loaded(self, workflow_data):
        """Callback when workflow is loaded"""
        if not workflow_data:
            return
        
        # Populate fields from workflow
        issue_key = workflow_data.get('issue_key', '')
        if issue_key:
            self.context.get_var('issue_var').set(issue_key)
        
        repo_info = workflow_data.get('repository_info', {})
        if repo_info.get('repository'):
            self.context.get_var('repo_var').set(repo_info['repository'])
        if repo_info.get('base_branch'):
            self.context.get_var('branch_var').set(repo_info['base_branch'])
        if repo_info.get('feature_branch'):
            self.context.get_var('feature_branch_var').set(repo_info['feature_branch'])
        
        self.log(f"✓ Workflow loaded for {issue_key}")
        self.show_info("Workflow Loaded", 
                      f"Loaded workflow for {issue_key}\n\n"
                      f"Available sections: {len(workflow_data.get('sections', []))}")

    def _start_workflow(self):
        issue_key = self.context.get_var('issue_var').get().strip()
        repo = self.context.get_var('repo_var').get().strip()
        branch = self.context.get_var('branch_var').get().strip()
        feature_branch = self.context.get_var('feature_branch_var').get().strip()
        
        if not issue_key or issue_key.endswith("-"):
            self.show_error("Input Error", "Please enter a valid JIRA issue key (e.g. TSESSD-1234).")
            return
        
        if not all([repo, branch]):
            self.show_error("Input Error", "Please select repository and branch.")
            return
        
        # Extract repo slug if needed
        if ' - ' in repo:
            repo_slug = repo.split(' - ')[0].strip()
        else:
            repo_slug = repo
        
        # Determine feature branch display name
        if feature_branch and "if empty," not in feature_branch.lower():
            fb_display = feature_branch
        else:
            fb_display = f"feature/{issue_key}"
        
        # Confirm before starting
        from tkinter import messagebox
        response = messagebox.askyesno("Confirm Full Workflow",
            f"Start full analysis workflow for {issue_key}?\n\n"
            f"This will:\n"
            f"1. Fetch JIRA issue\n"
            f"2. Analyze with AI\n"
            f"3. Clone {repo_slug}\n"
            f"4. Create branch '{fb_display}'\n"
            f"5. Generate implementation plan\n"
            f"6. Generate test scenarios\n"
            f"7. Generate validation & risk assessment\n\n"
            f"Continue?")
        
        if response:
            if not self.context.controller.full_workflow_controller:
                self.show_error("Error", "Full workflow controller not initialized")
                return
            
            self.log("Starting full BENTO analysis workflow...")
            self.context.controller.full_workflow_controller.start_full_workflow(
                issue_key, repo_slug, branch, feature_branch
            )

    # ──────────────────────────────────────────────────────────────────────
    # VIEW CALLBACKS
    # ──────────────────────────────────────────────────────────────────────

    def on_jira_analysis_completed(self, result: dict):
        issue_key = result.get("issue_key", "")
        self.status_label.configure(
            text=f"✓ Analysis complete: {issue_key}", foreground="green")

