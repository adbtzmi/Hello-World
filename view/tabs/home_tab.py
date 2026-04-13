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

        import webbrowser

    def _open_url(self, url):
        """Open a URL in the default web browser"""
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception as e:
            self.show_error("Browser Error", f"Failed to open URL: {e}")

    def build_ui(self):
        self.columnconfigure(0, weight=1)

        # ── Configuration Section ──────────────────────────────────────────
        config_frame = ttk.LabelFrame(self, text="Configuration", padding="10")
        config_frame.grid(row=0, column=0, columnspan=3, sticky="we", pady=5)

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
        self.context.get_var('debug_var').trace_add('write', self._toggle_debug_mode)
        ttk.Checkbutton(config_frame, text="Enable Debug Mode",
                        variable=self.context.get_var('debug_var')).grid(
            row=4, column=0, columnspan=2, sticky=tk.W, pady=2)

        # Hidden Model Variables for config saving
        modes_data = self.context.config.get('ai_modes', {})
        models = modes_data.get('models', {})
        self.context.set_var('analysis_model', tk.StringVar(value=models.get('analysis', {}).get('name', 'gemini-3.0-pro-preview')))
        self.context.set_var('code_model', tk.StringVar(value=models.get('code_generation', {}).get('name', 'claude-sonnet-4-5')))

        # Save Config and Test Config buttons
        config_btn_frame = ttk.Frame(config_frame)
        config_btn_frame.grid(row=5, column=0, columnspan=4, pady=5)
        
        save_cfg_btn = ttk.Button(config_btn_frame, text="Save Config", command=self._save_config)
        save_cfg_btn.pack(side=tk.LEFT, padx=5)
        self.context.lockable_buttons.append(save_cfg_btn)
        
        test_cfg_btn = ttk.Button(config_btn_frame, text="Test Config", command=self._test_config)
        test_cfg_btn.pack(side=tk.LEFT, padx=5)
        self.context.lockable_buttons.append(test_cfg_btn)

        # ── Credentials Section ────────────────────────────────────────────
        cred_frame = ttk.LabelFrame(self, text="Credentials (Encrypted)", padding="10")
        cred_frame.grid(row=1, column=0, columnspan=3, sticky="we", pady=5)

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
                   command=lambda: self._open_url("https://id.atlassian.com/manage-profile/security/api-tokens")).grid(
            row=1, column=2, padx=(2, 0), pady=2)

        # Bitbucket Token with show/hide button
        ttk.Label(cred_frame, text="Bitbucket Token:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.context.set_var('bb_token_var', tk.StringVar())
        self.bb_token_entry = ttk.Entry(cred_frame,
                                         textvariable=self.context.get_var('bb_token_var'),
                                         show='*', width=70)
        self.bb_token_entry.grid(row=2, column=1, pady=2, sticky=tk.W)
        ttk.Button(cred_frame, text="👁", width=3,
                   command=lambda: self._open_url("https://bitbucket.micron.com/bbdc/plugins/servlet/access-tokens/manage")).grid(
            row=2, column=2, padx=(2, 0), pady=2)

        # Model API Key with show/hide button
        ttk.Label(cred_frame, text="Model API Key:").grid(row=3, column=0, sticky=tk.W, pady=2)
        self.context.set_var('model_key_var', tk.StringVar())
        self.model_key_entry = ttk.Entry(cred_frame,
                                          textvariable=self.context.get_var('model_key_var'),
                                          show='*', width=70)
        self.model_key_entry.grid(row=3, column=1, pady=2, sticky=tk.W)
        ttk.Button(cred_frame, text="👁", width=3,
                   command=lambda: self._open_url("https://model-gateway.gcldgenaigw.gc.micron.com/")).grid(
            row=3, column=2, padx=(2, 0), pady=2)

        # Credential buttons
        cred_btn_frame = ttk.Frame(cred_frame)
        cred_btn_frame.grid(row=4, column=0, columnspan=3, pady=5)
        
        load_cred_btn = ttk.Button(cred_btn_frame, text="Load Credentials", command=self._load_credentials)
        load_cred_btn.pack(side=tk.LEFT, padx=2)
        self.context.lockable_buttons.append(load_cred_btn)
        
        save_cred_btn = ttk.Button(cred_btn_frame, text="Save", command=self._save_credentials)
        save_cred_btn.pack(side=tk.LEFT, padx=2)
        self.context.lockable_buttons.append(save_cred_btn)

        # ── Notifications Section ──────────────────────────────────────────
        notif_frame = ttk.LabelFrame(self, text="Notifications", padding="10")
        notif_frame.grid(row=3, column=0, columnspan=3, sticky="we", pady=5)
        notif_frame.columnconfigure(1, weight=1)

        # Initialise notification variables here (Home tab is built before Checkout tab)
        if self.context.get_var('checkout_notify_teams') is None:
            self.context.set_var('checkout_notify_teams', tk.BooleanVar(value=True))
        if self.context.get_var('checkout_webhook_url') is None:
            self.context.set_var('checkout_webhook_url', tk.StringVar(
                value=self.context.config.get('notifications', {}).get('teams_webhook_url', '')))

        ttk.Checkbutton(notif_frame, text="Enable Teams Notification",
                        variable=self.context.get_var('checkout_notify_teams')).grid(
            row=0, column=0, columnspan=2, sticky=tk.W, pady=2)

        ttk.Label(notif_frame, text="Webhook URL:").grid(
            row=1, column=0, sticky=tk.W, pady=2)
        webhook_entry = ttk.Entry(notif_frame,
                                  textvariable=self.context.get_var('checkout_webhook_url'),
                                  width=70)
        webhook_entry.grid(row=1, column=1, pady=2, sticky=tk.W)

        # ── Task / Workflow Section ────────────────────────────────────────
        task_frame = ttk.LabelFrame(self, text="Task Details - Full Workflow", padding="10")
        task_frame.grid(row=2, column=0, columnspan=3, sticky="we", pady=5)

        # JIRA Issue
        ttk.Label(task_frame, text="JIRA Issue:").grid(row=0, column=0, sticky=tk.W, pady=2)
        ttk.Entry(task_frame, textvariable=self.context.get_var('issue_var'), width=70).grid(
            row=0, column=1, pady=2)

        # Repository
        ttk.Label(task_frame, text="Repository:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.context.set_var('repo_var', tk.StringVar())
        self.repo_combo = ttk.Combobox(task_frame, textvariable=self.context.get_var('repo_var'), width=67)
        self.repo_combo.grid(row=1, column=1, pady=2)
        self.repo_combo.bind('<<ComboboxSelected>>', self._on_repo_selected)
        self.repo_combo.bind('<KeyRelease>', self._filter_repos)

        # Base Branch
        ttk.Label(task_frame, text="Base Branch:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.context.set_var('branch_var', tk.StringVar())
        self.branch_combo = ttk.Combobox(task_frame, textvariable=self.context.get_var('branch_var'), width=67)
        self.branch_combo.grid(row=2, column=1, pady=2)
        self.branch_combo.bind('<KeyRelease>', self._filter_branches)

        # Feature Branch
        ttk.Label(task_frame, text="Feature Branch:").grid(row=3, column=0, sticky=tk.W, pady=2)
        
        # Use centrally-managed feature_branch_var from context
        feature_branch_var = self.context.get_var('feature_branch_var')
        ttk.Entry(task_frame, textvariable=feature_branch_var, width=70).grid(row=3, column=1, pady=2)

        # Workflow buttons
        workflow_btn_frame = ttk.Frame(task_frame)
        workflow_btn_frame.grid(row=4, column=0, columnspan=2, pady=10)
        
        load_wf_btn = ttk.Button(workflow_btn_frame, text="📂 Load Workflow", command=self._load_workflow)
        load_wf_btn.pack(side=tk.LEFT, padx=5)
        self.context.lockable_buttons.append(load_wf_btn)
        
        start_wf_btn = ttk.Button(workflow_btn_frame, text="🚀 Start Full Analysis Workflow",
                   style='Accent.TButton', command=self._start_workflow)
        start_wf_btn.pack(side=tk.LEFT, padx=5)
        self.context.lockable_buttons.append(start_wf_btn)

        # Status label
        self.status_label = ttk.Label(self, text="")
        self.status_label.grid(row=5, column=0, sticky=tk.W, pady=2)

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

    def _toggle_debug_mode(self, *args):
        # The main title bar already handles mapping/unmapping visually via AppContext variable.
        if self.context.get_var('debug_var').get():
            self.log("🐛 Debug mode enabled")
            if self.context.analyzer:
                self.context.analyzer.debug_mode = True
        else:
            self.log("🐛 Debug mode disabled")
            if self.context.analyzer:
                self.context.analyzer.debug_mode = False

    def _on_repo_selected(self, event=None):
        selected_text = self.context.get_var('repo_var').get()
        if ' - ' in selected_text:
            repo_slug = selected_text.split(' - ')[0]
        else:
            repo_slug = selected_text
        
        project_key = self.context.get_var('bb_project').get()
        self.log(f"Fetching branches for {repo_slug}...")
        
        if hasattr(self.context.controller, 'repo_controller') and self.context.controller.repo_controller:
            def _on_fetched(result):
                if result and result.get('success'):
                    branches = result.get('branches', [])
                    self.context.branches = branches
                    self.branch_combo['values'] = branches
                    self.log(f"✓ Found {len(branches)} branches")
                    if branches:
                        self.branch_combo.current(0)
                        self.context.set_branch(self.context.get_var('branch_var').get())
                else:
                    self.log("✗ Failed to fetch branches")
            
            # Run in background via RepoController
            self.log("Requesting branches via RepoController...")
            self.context.controller.repo_controller.fetch_branches(
                repo_slug, project_key, _on_fetched
            )

    def _filter_repos(self, event=None):
        typed = self.context.get_var('repo_var').get().lower()
        if not hasattr(self.context, 'repos'):
            self.context.repos = []
        if not typed:
            repo_names = [f"{r['slug']} - {r['name']}" for r in self.context.repos]
            self.repo_combo['values'] = repo_names
        else:
            filtered = [f"{r['slug']} - {r['name']}" for r in self.context.repos
                        if typed in r['slug'].lower() or typed in r['name'].lower()]
            self.repo_combo['values'] = filtered

    def _filter_branches(self, event=None):
        typed = self.context.get_var('branch_var').get().lower()
        if not hasattr(self.context, 'branches'):
            self.context.branches = []
        if not typed:
            self.branch_combo['values'] = self.context.branches
        else:
            filtered = [b for b in self.context.branches if typed in b.lower()]
            self.branch_combo['values'] = filtered

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
                
                # Set base URLs from credentials, falling back to settings.json
                self.context.analyzer.jira_base_url = (
                    credentials.get('jira_url')
                    or self.context.config.get('jira', {}).get('base_url', '')
                )
                self.context.analyzer.bitbucket_base_url = (
                    credentials.get('bitbucket_url')
                    or self.context.config.get('bitbucket', {}).get('base_url', '')
                )
                
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

    def _load_workflow(self):
        """Load a workflow file"""
        if not self.context.controller.workflow_controller:
            self.show_error("Error", "Workflow controller not initialized")
            return
        
        self.context.controller.workflow_controller.load_workflow_file(self.root, self._on_workflow_loaded)

    def _on_workflow_loaded(self, workflow_data):
        """Callback when workflow is loaded"""
        if not workflow_data:
            return
        
        # Populate fields from workflow
        issue_key = workflow_data.get('issue_key', '')
        if issue_key:
            self.context.get_var('issue_var').set(issue_key)
        
        repo = workflow_data.get('repository')
        if repo:
            self.context.get_var('repo_var').set(repo)
        
        base_branch = workflow_data.get('base_branch')
        if base_branch:
            self.context.get_var('branch_var').set(base_branch)
            
        feature_branch = workflow_data.get('feature_branch')
        if feature_branch:
            self.context.get_var('feature_branch_var').set(feature_branch)
            
        local_path = workflow_data.get('local_path')
        if local_path:
            self.context.get_var('impl_repo_var').set(local_path)
        
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

