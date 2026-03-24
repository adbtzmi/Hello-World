#!/usr/bin/env python3
"""
view/tabs/repository_tab.py
============================
Repository Tab (View) - Phase 2

Tab for repository operations (clone & create branch).
Extracted from gui/app.py lines 599-641.
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from view.tabs.base_tab import BaseTab


class RepositoryTab(BaseTab):
    """
    Repository Tab - handles cloning and branch creation.
    
    Layout:
      1. Repository selection (combobox)
      2. Base branch input
      3. Issue key input
      4. Feature branch input (optional)
      5. Clone and Create Branch buttons
      6. Result display
    """
    
    def __init__(self, notebook, context):
        super().__init__(notebook, context, "📦 Repository")
        self._build_ui()
        # Auto-fetch repos on tab creation
        self._fetch_repos()
    
    def _build_ui(self):
        """Build the UI for repository tab."""
        self.columnconfigure(0, weight=1)
        
        # Title
        ttk.Label(self, text="Clone Repository & Create Branch", font=('Arial', 14, 'bold')).grid(
            row=0, column=0, columnspan=2, pady=10)
        
        # Repository selection
        ttk.Label(self, text="Repository:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.context.set_var('repo_tab_repo', tk.StringVar())
        self.repo_combo = ttk.Combobox(self, textvariable=self.context.get_var('repo_tab_repo'), width=37)
        self.repo_combo.grid(row=1, column=1, pady=5)
        self.repo_combo.bind('<KeyRelease>', self._filter_repos)
        
        # Branch selection
        ttk.Label(self, text="Base Branch:").grid(row=2, column=0, sticky=tk.W, pady=5)
        self.context.set_var('repo_tab_branch', tk.StringVar())
        ttk.Entry(self, textvariable=self.context.get_var('repo_tab_branch'), width=40).grid(
            row=2, column=1, pady=5)
        
        # Issue key
        ttk.Label(self, text="JIRA Issue Key:").grid(row=3, column=0, sticky=tk.W, pady=5)
        self.context.set_var('repo_tab_issue', tk.StringVar(value="TSESSD-"))
        ttk.Entry(self, textvariable=self.context.get_var('repo_tab_issue'), width=40).grid(
            row=3, column=1, pady=5)
        
        # Feature branch (optional)
        ttk.Label(self, text="Feature Branch:").grid(row=4, column=0, sticky=tk.W, pady=5)
        self.context.set_var('repo_tab_feature', tk.StringVar(
            value="If empty, will automatically be named as 'feature/TSESSD-XXXX'"))
        ttk.Entry(self, textvariable=self.context.get_var('repo_tab_feature'), width=40).grid(
            row=4, column=1, pady=5)
        
        # Buttons
        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=5, column=0, columnspan=2, pady=10)
        
        self.clone_btn = ttk.Button(btn_frame, text="Clone Repository", command=self._clone_repo)
        self.clone_btn.pack(side=tk.LEFT, padx=5)
        
        self.branch_btn = ttk.Button(btn_frame, text="Create Feature Branch", command=self._create_branch)
        self.branch_btn.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(btn_frame, text="Refresh Repos", command=self._fetch_repos).pack(side=tk.LEFT, padx=5)
        
        # Result display
        result_frame = ttk.LabelFrame(self, text="Repository Results", padding="10")
        result_frame.grid(row=6, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)
        
        self.result_text = scrolledtext.ScrolledText(result_frame, height=15, width=70, wrap=tk.WORD)
        self.result_text.pack(fill=tk.BOTH, expand=True)
        
        self.columnconfigure(1, weight=1)
        self.rowconfigure(6, weight=1)
    
    def _fetch_repos(self):
        """Fetch repository list from Bitbucket."""
        self.log("Fetching repositories...")
        self.context.controller.repo_controller.fetch_repos(callback=self._on_repos_fetched)
    
    def _on_repos_fetched(self, repos):
        """Callback when repos are fetched."""
        if repos:
            self.repo_combo['values'] = repos
            self.log(f"✓ Loaded {len(repos)} repositories")
        else:
            self.log("✗ No repositories found")
    
    def _filter_repos(self, event):
        """Filter repositories as user types."""
        query = self.context.get_var('repo_tab_repo').get()
        filtered = self.context.controller.repo_controller.filter_repos(query)
        self.repo_combo['values'] = filtered
    
    def _clone_repo(self):
        """Clone repository via controller."""
        repo = self.context.get_var('repo_tab_repo').get().strip()
        branch = self.context.get_var('repo_tab_branch').get().strip()
        issue_key = self.context.get_var('repo_tab_issue').get().strip().upper()
        
        if not all([repo, branch, issue_key]) or issue_key.endswith("-"):
            self.show_error("Error", "All fields required (Repository, Base Branch, JIRA Issue Key)")
            return
        
        # Disable buttons during clone
        self.clone_btn.configure(state=tk.DISABLED)
        self.branch_btn.configure(state=tk.DISABLED)
        
        # Call controller
        self.context.controller.repo_controller.clone_repo(
            repo, branch, issue_key,
            callback=self._on_clone_completed
        )
    
    def _on_clone_completed(self, result):
        """Callback when clone completes."""
        # Re-enable buttons
        self.clone_btn.configure(state=tk.NORMAL)
        self.branch_btn.configure(state=tk.NORMAL)
        
        if result.get('success'):
            # Display result
            self.result_text.delete('1.0', tk.END)
            self.result_text.insert('1.0', result.get('result', ''))
            self.log("✓ Repository cloned successfully")
        elif result.get('warning'):
            # Show warning dialog
            messagebox.showwarning("Repository Already Exists", result.get('warning'))
        else:
            error_msg = result.get('error', 'Unknown error')
            self.show_error("Error", f"Failed to clone repository:\n{error_msg}")
            self.log(f"✗ Failed to clone repository: {error_msg}")
    
    def _create_branch(self):
        """Create feature branch via controller."""
        repo = self.context.get_var('repo_tab_repo').get().strip()
        branch = self.context.get_var('repo_tab_branch').get().strip()
        issue_key = self.context.get_var('repo_tab_issue').get().strip().upper()
        feature_branch = self.context.get_var('repo_tab_feature').get().strip()
        
        if not all([repo, branch, issue_key]) or issue_key.endswith("-"):
            self.show_error("Error", "All fields required (Repository, Base Branch, JIRA Issue Key)")
            return
        
        # Disable buttons during branch creation
        self.clone_btn.configure(state=tk.DISABLED)
        self.branch_btn.configure(state=tk.DISABLED)
        
        # Call controller
        self.context.controller.repo_controller.create_feature_branch(
            repo, branch, issue_key, feature_branch,
            callback=self._on_branch_completed
        )
    
    def _on_branch_completed(self, result):
        """Callback when branch creation completes."""
        # Re-enable buttons
        self.clone_btn.configure(state=tk.NORMAL)
        self.branch_btn.configure(state=tk.NORMAL)
        
        if result.get('success'):
            # Display result
            self.result_text.delete('1.0', tk.END)
            self.result_text.insert('1.0', result.get('result', ''))
            
            # Update feature branch field
            feature_branch = result.get('feature_branch', '')
            if feature_branch:
                self.context.get_var('repo_tab_feature').set(feature_branch)
            
            self.log("✓ Feature branch created successfully")
        else:
            error_msg = result.get('error', 'Unknown error')
            self.show_error("Error", f"Failed to create feature branch:\n{error_msg}")
            self.log(f"✗ Failed to create feature branch: {error_msg}")
