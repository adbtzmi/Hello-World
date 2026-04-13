#!/usr/bin/env python3
"""
view/tabs/implementation_tab.py
================================
Implementation Tab (View) — Simplified to AI Plan Generator only
Compilation functionality moved to compilation_tab.py
"""

import os
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog
import logging

from view.tabs.base_tab import BaseTab

logger = logging.getLogger("bento_app")


class ImplementationTab(BaseTab):
    """
    Implementation tab — AI Plan Generator only.
    
    Layout:
      - AI Plan Generator (JIRA issue, repo path, generate button, plan display)
    """

    def __init__(self, notebook, context):
        super().__init__(notebook, context, "💻 Implementation")
        self._build_ui()

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        # Main frame with padding
        main_frame = ttk.Frame(self, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(4, weight=1)
        
        ttk.Label(main_frame, text="Generate Implementation Plan", font=('Arial', 14, 'bold')).grid(
            row=0, column=0, columnspan=3, pady=10)
        
        ttk.Label(main_frame, text="JIRA Issue Key:").grid(row=1, column=0, sticky=tk.W, pady=5)
        ttk.Entry(main_frame, textvariable=self.context.get_var("issue_var"), width=50).grid(
            row=1, column=1, pady=5, sticky="we")
        ttk.Label(main_frame, text="(Auto-populated from Home tab)", font=('Arial', 8), foreground='gray').grid(
            row=1, column=2, sticky=tk.W, padx=5)

        ttk.Label(main_frame, text="Local Repo Path:").grid(row=2, column=0, sticky=tk.W, pady=5)
        repo_path_frame = ttk.Frame(main_frame)
        repo_path_frame.grid(row=2, column=1, pady=5, sticky="we")
        self.context.set_var("impl_repo_var", tk.StringVar())
        ttk.Entry(repo_path_frame, textvariable=self.context.get_var("impl_repo_var"), width=47).pack(side=tk.LEFT)
        ttk.Button(repo_path_frame, text="📁", width=3, command=self._browse_repo).pack(side=tk.LEFT, padx=(2, 0))
        ttk.Label(main_frame, text="(Path to cloned repository for AI indexing)", font=('Arial', 8), foreground='gray').grid(
            row=2, column=2, sticky=tk.W, padx=5)

        self.generate_btn = ttk.Button(main_frame, text="Generate Implementation Plan", command=self._generate_plan)
        self.generate_btn.grid(row=3, column=0, columnspan=3, pady=10)
        self.context.lockable_buttons.append(self.generate_btn)

        result_frame = ttk.LabelFrame(main_frame, text="Implementation Plan", padding="10")
        result_frame.grid(row=4, column=0, columnspan=3, sticky="nsew", pady=5)

        self.plan_text = scrolledtext.ScrolledText(result_frame, height=20, width=80, wrap=tk.WORD)
        self.plan_text.pack(fill=tk.BOTH, expand=True)

    # ──────────────────────────────────────────────────────────────────────
    # AI PLAN GENERATOR — USER ACTIONS
    # ──────────────────────────────────────────────────────────────────────

    def _browse_repo(self):
        path = filedialog.askdirectory(title="Select Repository Folder")
        if path:
            self.context.get_var("impl_repo_var").set(path)

    def _generate_plan(self):
        issue_key = self.context.get_var("issue_var").get().strip().upper()
        repo_path = self.context.get_var("impl_repo_var").get().strip()

        jira_project = self.context.config.get("jira", {}).get("project_key", "TSESSD")
        if not issue_key or issue_key == f"{jira_project}-":
            self.show_error("Input Error", "Enter a valid JIRA issue key.")
            return
        if not repo_path:
            self.show_error("Input Error", "Select the local repository path.")
            return

        ctrl = getattr(self.context.controller, "implementation_controller", None)
        if ctrl is None:
            self.show_error("Error", "ImplementationController is not initialised.")
            return

        self.lock_gui()
        self.plan_text.delete("1.0", tk.END)
        self.plan_text.insert(tk.END, "⏳ Generating implementation plan…\n")
        self.log(f"[Implementation] Generating plan for {issue_key}")

        ctrl.generate_implementation_plan(issue_key, repo_path, self._on_plan_generated)

    def _on_plan_generated(self, result):
        """Callback — runs on main thread via root.after in controller."""
        self.unlock_gui()

        if not result.get("success"):
            error = result.get("error", "Unknown error")
            self.plan_text.delete("1.0", tk.END)
            self.plan_text.insert(tk.END, f"✗ Generation failed:\n{error}")
            self.show_error("Generation Failed", error)
            self.log(f"✗ Implementation plan generation failed: {error}")
            return

        plan = result.get("plan", "")
        from_cache = result.get("from_cache", False)

        self.plan_text.delete("1.0", tk.END)
        self.plan_text.insert(tk.END, plan)
        self.log(f"✓ Implementation plan generated{' (from cache)' if from_cache else ''}")

        # Open interactive chat for review/refinement
        issue_key = self.context.get_var("issue_var").get().strip().upper()
        chat_ctrl = getattr(self.context.controller, "chat_controller", None)
        if chat_ctrl:
            chat_ctrl.open_interactive_chat(
                issue_key=issue_key,
                step_name="Implementation Plan",
                initial_content=plan,
                finalize_callback=lambda: self._finalize_plan()
            )

    def _finalize_plan(self):
        issue_key = self.context.get_var("issue_var").get().strip().upper()
        plan_text = self.plan_text.get("1.0", tk.END).strip()
        if not plan_text:
            self.show_error("Error", "No plan to finalize.")
            return

        ctrl = getattr(self.context.controller, "implementation_controller", None)
        if ctrl:
            success = ctrl.finalize_plan(issue_key, plan_text)
            if success:
                self.log(f"✓ Implementation plan finalized for {issue_key}")
            else:
                self.show_error("Error", "Failed to save plan to workflow.")

    # ──────────────────────────────────────────────────────────────────────
    # VIEW CALLBACKS
    # ──────────────────────────────────────────────────────────────────────

    def on_implementation_completed(self, result: dict):
        issue_key = result.get("issue_key", "")
        self.log(f"✓ Implementation step complete: {issue_key}")
