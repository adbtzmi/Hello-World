#!/usr/bin/env python3
"""
view/app.py
===========
BENTO Main Application Window (View) — Phase 3C/3D

Tab order:
  1. 🏠 Home               (view/tabs/home_tab.py)
  2. 📋 Fetch Issue         (view/tabs/fetch_issue_tab.py)
  3. 🤖 Analyze JIRA        (view/tabs/analyze_jira_tab.py)
  4. 📦 Repository          (view/tabs/repository_tab.py)
  5. 💻 Implementation      (view/tabs/implementation_tab.py)  ← Phase 3C
  6. 🧪 Test Scenarios      (view/tabs/test_scenarios_tab.py)
  7. 📋 Validation & Risk   (view/tabs/validation_tab.py)

Checkout lives inside the Implementation sub-notebook (injected by main.py).
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import logging
from typing import Any

from view.tabs.home_tab import HomeTab
from view.tabs.fetch_issue_tab import FetchIssueTab
from view.tabs.analyze_jira_tab import AnalyzeJiraTab
from view.tabs.repository_tab import RepositoryTab
from view.tabs.implementation_tab import ImplementationTab
from view.tabs.test_scenarios_tab import TestScenariosTab
from view.tabs.validation_tab import ValidationTab
from context import AppContext

logger = logging.getLogger("bento_app")


class BentoApp:
    """
    Top-level View class.

    Mirrors gui.CrtGui:
      - __init__  : store refs, create window skeleton
      - _build_*  : build all child widgets / tabs
      - Callback methods called BY controllers to update display
    """

    # ── Attributes (declared for IDE support) ─────────────────────────
    paned_window:    ttk.PanedWindow
    notebook:        ttk.Notebook
    log_text:        scrolledtext.ScrolledText
    debug_indicator: ttk.Label
    debug_var:       tk.BooleanVar
    impl_notebook:   ttk.Notebook

    # Tab references
    home_tab:           HomeTab
    fetch_issue_tab:    FetchIssueTab
    analyze_jira_tab:   AnalyzeJiraTab
    repository_tab:     RepositoryTab
    implementation_tab: ImplementationTab
    test_scenarios_tab: TestScenariosTab
    validation_tab:     ValidationTab
    checkout_tab:       Any  # injected by main.py after BentoApp is built

    # ──────────────────────────────────────────────────────────────────────
    def __init__(self, root, controller, config, app_title, app_version):
        self.root        = root
        self.controller  = controller
        self.config      = config
        self.app_title   = app_title
        self.app_version = app_version

        # AppContext — shared state object (ViewModel) passed to every tab
        self.context = AppContext(
            root=root,
            analyzer=None,          # injected by controller after set_view()
            config=config,
            log_callback=self._log_message,
        )

        self._build_window()
        self._build_layout()
        # Wire controller into context BEFORE building tabs.
        # Some tabs (e.g. RepositoryTab) access context.controller during __init__.
        self.context.controller = controller
        self._build_tabs()


    # ──────────────────────────────────────────────────────────────────────
    # WINDOW SETUP
    # ──────────────────────────────────────────────────────────────────────

    def _build_window(self):
        """Configure the root Tk window."""
        self.root.title("BENTO - GUI")
        self.root.geometry("1400x750")
        self.root.minsize(960, 600)

        style = ttk.Style()
        # style.theme_use("clam")  # Disabled to follow original and prevent black treeview
        
        # Use standard styles (no overrides to match default OS button font/size)
        style.configure("Accent.TButton")
        style.configure("Compile.TButton")
        
        style.configure("Success.TLabel", foreground="green")
        style.configure("Error.TLabel",   foreground="red")
        style.configure("Warning.TLabel", foreground="orange")

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_layout(self):
        """Build outer frame: top notebook + side log panel."""
        self.paned_window = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        self.paned_window.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

        # Left side - Main container
        main_frame = ttk.Frame(self.paned_window, padding="2")
        self.paned_window.add(main_frame, weight=3)

        title_frame = ttk.Frame(main_frame)
        title_frame.pack(fill=tk.X, pady=(2, 6))
        
        title = ttk.Label(title_frame, text="BENTO - Build, Evaluate, Navigate, Test & Orchestrate", font=('Arial', 14, 'bold'))
        title.pack(side=tk.LEFT)
        
        self.debug_indicator = ttk.Label(title_frame, text="🐛 DEBUG MODE", 
                                       font=('Arial', 10, 'bold'), 
                                       foreground='red', 
                                       background='yellow',
                                       padding="5")
        # Do not pack initially
        
        self.debug_var = tk.BooleanVar(value=False)
        self.context.set_var('debug_var', self.debug_var)
        self.debug_var.trace_add("write", lambda name, index, mode: self._on_debug_toggled())

        # Centralized Shared Variables (unifies all tabs)
        jira_project = self.config.get('jira', {}).get('project_key', 'TSESSD')
        issue_prefix = f"{jira_project}-"
        
        self.context.set_var('issue_var', tk.StringVar(value=issue_prefix))
        self.context.set_var('repo_var',  tk.StringVar())
        self.context.set_var('branch_var', tk.StringVar())
        self.context.set_var('feature_branch_var', tk.StringVar(value=f"If empty, will automatically be named as 'feature/{jira_project}-XXXX'"))
        self.context.set_var('impl_repo_var', tk.StringVar())

        self.notebook = ttk.Notebook(main_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=(5, 0))

        # Right side - Log container
        log_frame = ttk.Frame(self.paned_window, padding="5")
        self.paned_window.add(log_frame, weight=2)

        log_outer = ttk.LabelFrame(log_frame, text="Progress Log", padding="5")
        log_outer.pack(fill=tk.BOTH, expand=True)

        self.log_text = scrolledtext.ScrolledText(
            log_outer, state=tk.DISABLED,
            height=48, width=45, wrap=tk.WORD
        )
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        ttk.Button(log_outer, text="Clear Log", width=12,
                   command=self._clear_log).pack(side=tk.BOTTOM, pady=5)

    def _on_debug_toggled(self):
        """Show or hide debug indicator on the title bar."""
        if self.debug_var.get():
            self.debug_indicator.pack(side=tk.RIGHT, padx=10)
        else:
            self.debug_indicator.pack_forget()

    def _build_tabs(self):
        """Instantiate and add all tab views to the notebook (Phase 3C/3D order)."""
        # 1. Home
        self.home_tab           = HomeTab(self.notebook, self.context)
        # 2. Fetch Issue
        self.fetch_issue_tab    = FetchIssueTab(self.notebook, self.context)
        # 3. Analyze JIRA
        self.analyze_jira_tab   = AnalyzeJiraTab(self.notebook, self.context)
        # 4. Repository
        self.repository_tab     = RepositoryTab(self.notebook, self.context)
        # 5. Implementation (Phase 3C — contains nested Checkout sub-tab)
        self.implementation_tab = ImplementationTab(self.notebook, self.context)
        # Expose impl_notebook so main.py can inject CheckoutTab
        self.impl_notebook      = self.implementation_tab.impl_notebook
        # 6. Test Scenarios
        self.test_scenarios_tab = TestScenariosTab(self.notebook, self.context)
        # 7. Validation & Risk (Phase 3B)
        self.validation_tab     = ValidationTab(self.notebook, self.context)

    # ──────────────────────────────────────────────────────────────────────
    # LOG PANEL
    # ──────────────────────────────────────────────────────────────────────

    def _log_message(self, message: str):
        """Append a timestamped message to the log panel (thread-safe)."""
        import datetime
        ts   = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {message}\n"

        def _append():
            self.log_text.configure(state=tk.NORMAL)
            self.log_text.insert(tk.END, line)
            self.log_text.see(tk.END)
            self.log_text.configure(state=tk.DISABLED)

        self.root.after(0, _append)

    def _clear_log(self):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

    # ──────────────────────────────────────────────────────────────────────
    # CONTROLLER → VIEW CALLBACKS
    # ──────────────────────────────────────────────────────────────────────

    def compile_started(self, hostname: str, env: str):
        """Called by CompileController when a compile job begins."""
        self._log_message(f"⚙ Compile started → {hostname} ({env})")
        if hasattr(self, "implementation_tab"):
            self.implementation_tab.on_compile_started(hostname, env)

    def compile_completed(self, hostname: str, env: str, result: dict):
        """Called by CompileController when a compile job finishes."""
        status = result.get("status", "unknown").upper()
        self._log_message(
            f"{'✓' if status == 'SUCCESS' else '✗'} "
            f"Compile {status} → {hostname} ({env}): {result.get('detail', '')}")
        if hasattr(self, "implementation_tab"):
            self.implementation_tab.on_compile_completed(hostname, env, result)

    def checkout_started(self, hostname: str):
        """Called by CheckoutController when checkout automation begins."""
        self._log_message(f"🚀 Checkout started → {hostname}")
        # Checkout tab lives inside impl_notebook — relay via the tab itself
        checkout_tab = getattr(self, "checkout_tab", None)
        if checkout_tab:
            checkout_tab.on_checkout_started(hostname)

    def checkout_completed(self, hostname: str, result: dict):
        """Called by CheckoutController when checkout automation finishes."""
        status = result.get("status", "unknown").upper()
        self._log_message(
            f"{'✓' if status == 'SUCCESS' else '✗'} "
            f"Checkout {status} → {hostname}: {result.get('detail', '')}")
        checkout_tab = getattr(self, "checkout_tab", None)
        if checkout_tab:
            checkout_tab.on_checkout_completed(hostname, result)

    def xml_generation_completed(self, hostname: str, result: dict):
        """Called by CheckoutController when XML-only generation finishes."""
        status = result.get("status", "unknown").upper()
        is_ok = status in ("XML_DONE", "XML_PARTIAL")
        self._log_message(
            f"{'✓' if is_ok else '✗'} "
            f"XML Generation {'complete' if is_ok else 'FAILED'} → {hostname}: "
            f"{result.get('detail', '')}")
        checkout_tab = getattr(self, "checkout_tab", None)
        if checkout_tab:
            checkout_tab.on_xml_generation_completed(hostname, result)

    def checkout_progress(self, hostname: str, phase: str):
        """Called by CheckoutController to relay mid-run phase updates."""
        self._log_message(f"   ↳ [{hostname}] {phase}")
        checkout_tab = getattr(self, "checkout_tab", None)
        if checkout_tab:
            checkout_tab.on_checkout_progress(hostname, phase)

    def jira_analysis_completed(self, result: dict):
        """Called by JiraController when ticket analysis finishes."""
        self._log_message(f"✓ JIRA analysis complete: {result.get('issue_key', '')}")
        if hasattr(self, "home_tab"):
            self.home_tab.on_jira_analysis_completed(result)

    def implementation_completed(self, result: dict):
        """Called by ImplementationController when plan generation finishes."""
        self._log_message(f"✓ Implementation: {result.get('issue_key', '')}")
        if hasattr(self, "implementation_tab"):
            self.implementation_tab.on_implementation_completed(result)

    def validation_completed(self, result: dict):
        """Called by ValidationController when validation finishes."""
        self._log_message(f"✓ Validation: {result.get('issue_key', '')}")
        if hasattr(self, "validation_tab"):
            self.validation_tab.on_validation_completed(result)

    # ──────────────────────────────────────────────────────────────────────
    # WINDOW LIFECYCLE
    # ──────────────────────────────────────────────────────────────────────

    def _on_close(self):
        """Guard against closing while background tasks are running."""
        choice = messagebox.askokcancel("Quit", "Do you want to close the application?")
        if not choice:
            return
            
        if self.controller.has_active_tasks():
            choice2 = messagebox.askyesno(
                "Active Tasks",
                "Background tasks are still running.\n"
                "Force close anyway? (data may be lost)",
            )
            if not choice2:
                return
        logger.info("Application closing.")
        self.root.destroy()
