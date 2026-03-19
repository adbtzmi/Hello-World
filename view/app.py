#!/usr/bin/env python3
"""
view/app.py
===========
BENTO Main Application Window (View)

Responsibilities:
  - Builds the Tk root window, notebook, and all tabs
  - Owns the log panel (shared by all tabs via AppContext)
  - Exposes GUI-callback methods that controllers call to update display
    (mirrors gui.CrtGui in the C.A.T. project)

Phase 2 tabs:
  - HomeTab        : JIRA / Bitbucket configuration
  - CompileTab     : Phase 1 compilation trigger + status badges
  - CheckoutTab    : Phase 2 Auto Start Checkout automation  [NEW]
"""

import tkinter as tk
from tkinter import ttk
import logging

from view.tabs.base_tab import BaseTab
from view.tabs.home_tab import HomeTab
from view.tabs.compile_tab import CompileTab
from view.tabs.checkout_tab import CheckoutTab
from context import AppContext

logger = logging.getLogger("bento_app")


class BentoApp:
    """
    Top-level View class.

    Mirrors gui.CrtGui:
      - __init__  : store refs, create window skeleton
      - init_GUI  : build all child widgets / tabs
      - Callback methods called BY controllers to update display
    """

    # ──────────────────────────────────────────────────────────────────────
    def __init__(self, root, controller, config, app_title, app_version):
        self.root        = root
        self.controller  = controller
        self.config      = config
        self.app_title   = app_title
        self.app_version = app_version

        # AppContext is the shared state object (ViewModel) passed to every tab
        self.context = AppContext(
            root=root,
            analyzer=None,          # set by controller after wiring
            config=config,
            log_callback=self._log_message,
        )

        self._build_window()
        self._build_layout()
        self._build_tabs()

        logger.info("BentoApp (View) initialised.")

    # ──────────────────────────────────────────────────────────────────────
    # WINDOW SETUP
    # ──────────────────────────────────────────────────────────────────────

    def _build_window(self):
        """Configure the root Tk window."""
        self.root.title(f"{self.app_title}  {self.app_version}")
        self.root.geometry("1280x800")
        self.root.minsize(960, 600)

        # Style
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Accent.TButton", foreground="white", background="#0078d4")
        style.configure("Success.TLabel", foreground="green")
        style.configure("Error.TLabel",   foreground="red")
        style.configure("Warning.TLabel", foreground="orange")

        # Intercept close
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_layout(self):
        """Build the outer frame: top notebook + bottom log panel."""
        # ── Top notebook fills most of the window ──
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=(5, 0))

        # ── Bottom log panel ──────────────────────────────────────────────
        log_outer = ttk.LabelFrame(self.root, text="Log", padding="5")
        log_outer.pack(fill=tk.X, padx=5, pady=5)

        self.log_text = tk.Text(
            log_outer, height=8, state=tk.DISABLED,
            font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4",
            relief=tk.FLAT, wrap=tk.WORD,
        )
        log_scroll = ttk.Scrollbar(log_outer, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Clear button
        ttk.Button(log_outer, text="Clear", width=6,
                   command=self._clear_log).pack(side=tk.RIGHT, padx=(5, 0))

    def _build_tabs(self):
        """Instantiate and add all tab views to the notebook."""
        self.home_tab     = HomeTab(self.notebook, self.context)
        self.compile_tab  = CompileTab(self.notebook, self.context)
        self.checkout_tab = CheckoutTab(self.notebook, self.context)

    # ──────────────────────────────────────────────────────────────────────
    # LOG PANEL
    # ──────────────────────────────────────────────────────────────────────

    def _log_message(self, message: str):
        """
        Append a timestamped message to the log panel.
        Thread-safe via root.after().
        """
        import datetime
        ts  = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {message}\n"

        def _append():
            self.log_text.configure(state=tk.NORMAL)
            self.log_text.insert(tk.END, line)
            self.log_text.see(tk.END)
            self.log_text.configure(state=tk.DISABLED)

        # Schedule on the main thread — safe to call from worker threads
        self.root.after(0, _append)

    def _clear_log(self):
        """Clear all messages from the log panel."""
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

    # ──────────────────────────────────────────────────────────────────────
    # CONTROLLER → VIEW CALLBACKS
    # (mirrors CatGUI.fw_mig_started / fw_mig_completed etc.)
    # ──────────────────────────────────────────────────────────────────────

    def compile_started(self, hostname: str, env: str):
        """Called by CompileController when a compile job begins."""
        self._log_message(f"⚙ Compile started → {hostname} ({env})")
        self.compile_tab.on_compile_started(hostname, env)

    def compile_completed(self, hostname: str, env: str, result: dict):
        """Called by CompileController when a compile job finishes."""
        status = result.get("status", "unknown").upper()
        self._log_message(f"{'✓' if status == 'SUCCESS' else '✗'} Compile {status} → {hostname} ({env}): {result.get('detail', '')}")
        self.compile_tab.on_compile_completed(hostname, env, result)

    def checkout_started(self, hostname: str):
        """Called by CheckoutController when checkout automation begins."""
        self._log_message(f"🚀 Checkout started → {hostname}")
        self.checkout_tab.on_checkout_started(hostname)

    def checkout_completed(self, hostname: str, result: dict):
        """Called by CheckoutController when checkout automation finishes."""
        status = result.get("status", "unknown").upper()
        self._log_message(f"{'✓' if status == 'SUCCESS' else '✗'} Checkout {status} → {hostname}: {result.get('detail', '')}")
        self.checkout_tab.on_checkout_completed(hostname, result)

    def checkout_progress(self, hostname: str, phase: str):
        """Called by CheckoutController to relay mid-run phase updates."""
        self._log_message(f"   ↳ [{hostname}] {phase}")
        self.checkout_tab.on_checkout_progress(hostname, phase)

    def jira_analysis_completed(self, result: dict):
        """Called by JiraController when ticket analysis finishes."""
        self._log_message(f"✓ JIRA analysis complete: {result.get('issue_key', '')}")
        self.home_tab.on_jira_analysis_completed(result)

    # ──────────────────────────────────────────────────────────────────────
    # WINDOW LIFECYCLE
    # ──────────────────────────────────────────────────────────────────────

    def _on_close(self):
        """
        Guard against closing while background tasks are running.
        Mirrors C.A.T.'s smart-close behaviour for profile generation / FW mig.
        """
        if self.controller.has_active_tasks():
            from tkinter import messagebox
            choice = messagebox.askyesno(
                "Active Tasks",
                "Background tasks are still running.\n"
                "Force close anyway? (data may be lost)",
            )
            if not choice:
                return
        logger.info("Application closing.")
        self.root.destroy()
