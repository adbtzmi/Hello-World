#!/usr/bin/env python3
"""
view/tabs/compile_checkout_tab.py
==================================
Compile & Checkout Tab (View) — Combined tab with nested sub-tabs
Contains: TP Compilation and Checkout as sub-tabs
"""

import tkinter as tk
from tkinter import ttk
import logging

from view.tabs.base_tab import BaseTab

logger = logging.getLogger("bento_app")


class CompileCheckoutTab(BaseTab):
    """
    Combined Compile & Checkout tab with nested notebook.
    
    Layout (nested):
      ├── 📦 TP Compilation
      └── 🧪 Checkout
    """

    def __init__(self, notebook, context):
        super().__init__(notebook, context, "⚙️ Compile & Checkout")
        self.compilation_tab = None
        self.checkout_tab = None
        self._build_ui()

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        # Create nested notebook for sub-tabs
        self.sub_notebook = ttk.Notebook(self)
        self.sub_notebook.grid(row=0, column=0, sticky="nsew")

        # Import here to avoid circular imports
        from view.tabs.compilation_tab import CompilationTab
        from view.tabs.checkout_tab import CheckoutTab

        # Create the sub-tabs directly in the sub-notebook
        self.compilation_tab = CompilationTab(self.sub_notebook, self.context)
        self.checkout_tab = CheckoutTab(self.sub_notebook, self.context)

        # Update the tab text to remove emoji since they're already in the parent tab
        # Get the index of each tab and update its text
        for i in range(self.sub_notebook.index("end")):
            current_text = self.sub_notebook.tab(i, "text")
            if "Compilation" in current_text or "TP Compilation" in current_text:
                self.sub_notebook.tab(i, text="📦 TP Compilation")
            elif "Checkout" in current_text:
                self.sub_notebook.tab(i, text="🧪 Checkout")

    # ──────────────────────────────────────────────────────────────────────
    # RELAY METHODS - Forward calls to the appropriate sub-tab
    # ──────────────────────────────────────────────────────────────────────

    def on_compile_started(self, hostname: str, env: str):
        """Relay to compilation sub-tab."""
        if self.compilation_tab:
            self.compilation_tab.on_compile_started(hostname, env)

    def on_compile_completed(self, hostname: str, env: str, result: dict):
        """Relay to compilation sub-tab."""
        if self.compilation_tab:
            self.compilation_tab.on_compile_completed(hostname, env, result)

    def on_checkout_started(self, hostname: str):
        """Relay to checkout sub-tab."""
        if self.checkout_tab:
            self.checkout_tab.on_checkout_started(hostname)

    def on_checkout_completed(self, hostname: str, result: dict):
        """Relay to checkout sub-tab."""
        if self.checkout_tab:
            self.checkout_tab.on_checkout_completed(hostname, result)

    def on_xml_generation_completed(self, hostname: str, result: dict):
        """Relay to checkout sub-tab."""
        if self.checkout_tab:
            self.checkout_tab.on_xml_generation_completed(hostname, result)

    def on_checkout_progress(self, hostname: str, phase: str):
        """Relay to checkout sub-tab."""
        if self.checkout_tab:
            self.checkout_tab.on_checkout_progress(hostname, phase)

    # ── Additional relays for checkout sub-tab callbacks ──────────────────

    def on_profile_data_loaded(self, profile_rows):
        """Relay to checkout sub-tab."""
        if self.checkout_tab:
            self.checkout_tab.on_profile_data_loaded(profile_rows)

    def on_crt_grid_loaded(self, crt_data):
        """Relay to checkout sub-tab."""
        if self.checkout_tab:
            self.checkout_tab.on_crt_grid_loaded(crt_data)

    def on_lot_lookup_completed(self, row_idx, cfgpn, mcto,
                                form_factor="", material_desc=""):
        """Relay to checkout sub-tab."""
        if self.checkout_tab:
            self.checkout_tab.on_lot_lookup_completed(
                row_idx, cfgpn, mcto, form_factor, material_desc)

    def on_mid_verify_completed(self, row_idx, result):
        """Relay to checkout sub-tab."""
        if self.checkout_tab:
            self.checkout_tab.on_mid_verify_completed(row_idx, result)

    def on_xml_imported(self, data):
        """Relay to checkout sub-tab."""
        if self.checkout_tab:
            self.checkout_tab.on_xml_imported(data)

    def on_autofill_completed(self, mid, cfgpn, fw_ver):
        """Relay to checkout sub-tab."""
        if self.checkout_tab:
            self.checkout_tab.on_autofill_completed(mid, cfgpn, fw_ver)

    def on_rc_progress_update(self, summary, entries):
        """Relay result-collection progress to checkout sub-tab."""
        if self.checkout_tab:
            self.checkout_tab.on_rc_progress_update(summary, entries)

    def on_rc_collection_complete(self, summary):
        """Relay result-collection completion to checkout sub-tab."""
        if self.checkout_tab:
            self.checkout_tab.on_rc_collection_complete(summary)
