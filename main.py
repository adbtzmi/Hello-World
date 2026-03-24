#!/usr/bin/env python3
"""
BENTO - Build, Evaluate, Navigate, Test & Orchestrate
Entry Point — Phase 2 MVC

Wiring order (critical):
  1. Load config
  2. Create Tk root
  3. Launch existing SimpleGUI (gui/app.py) — Phase 1 unchanged
  4. Remove legacy top-level Compile/Checkout tabs
  5. Build AppContext using existing analyzer from SimpleGUI
  6. Create BentoController(config) — NO context yet
  7. Create BentoApp view (builds tabs, each tab gets context)
  8. controller.set_view(view) — wires context into all controllers
  9. Inject Checkout into Implementation sub-notebook
"""

import sys
import os
import json
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _remove_top_level_tabs(notebook, names_to_remove):
    """Remove top-level tabs whose label contains any of names_to_remove."""
    removed = []
    for tab_id in reversed(notebook.tabs()):
        tab_text = notebook.tab(tab_id, "text")
        for name in names_to_remove:
            if name.lower() in tab_text.lower():
                notebook.forget(tab_id)
                removed.append(tab_text)
                break
    return removed


def main():
    # ── High-DPI fix ──────────────────────────────────────────────────────
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    root = tk.Tk()

    # ── Step 1: Load config ───────────────────────────────────────────────
    config = {}
    try:
        if os.path.exists('settings.json'):
            with open('settings.json', 'r') as f:
                config = json.load(f)
    except Exception as e:
        print(f"⚠ Could not load settings.json: {e}")

    # ── Step 2: Launch existing SimpleGUI (Phase 1 — completely unchanged) ─
    from gui.app import SimpleGUI
    app = SimpleGUI(root)

    # ── Step 3: Remove legacy top-level Compile and Checkout tabs ─────────
    removed = _remove_top_level_tabs(app.notebook, ["Compile", "Checkout"])
    for name in removed:
        print(f"✓ Removed legacy top-level tab: {name}")

    # ── Step 4: Wire Phase 2 MVC ──────────────────────────────────────────
    try:
        from context import AppContext
        from controller.bento_controller import BentoController
        from view.tabs.checkout_tab import CheckoutTab

        # Shared log callback — pipes into existing GUI log panel
        def log_to_gui(msg):
            try:
                app.log(msg)
            except Exception:
                print(msg)

        # AppContext — uses existing analyzer from SimpleGUI
        context = AppContext(
            root=root,
            analyzer=app.analyzer,
            config=config,
            log_callback=log_to_gui,
        )

        # BentoController — pass config only (context wired via set_view)
        controller = BentoController(config=config)
        context.controller = controller

        # Inject Checkout as sub-tab inside Implementation tab
        impl_notebook = app.impl_notebook
        checkout_tab  = CheckoutTab(impl_notebook, context)

        # Minimal view adapter for checkout callbacks
        class _MinimalView:
            def __init__(self):
                self.root         = root
                self.context      = context
                self.checkout_tab = checkout_tab

            def compile_started(self, hostname, env):
                pass   # handled by existing TP Compilation & Health tab

            def compile_completed(self, hostname, env, result):
                pass   # handled by existing TP Compilation & Health tab

            def checkout_started(self, hostname):
                self.checkout_tab.on_checkout_started(hostname)

            def checkout_progress(self, hostname, phase):
                self.checkout_tab.on_checkout_progress(hostname, phase)

            def checkout_completed(self, hostname, result):
                self.checkout_tab.on_checkout_completed(hostname, result)

            def jira_analysis_completed(self, result):
                pass   # handled by existing SimpleGUI

        view = _MinimalView()
        controller.set_view(view)

        print("✓ Phase 2: 🧪 Checkout tab injected into Implementation")
        print("  Tab order: 🧠 AI Plan Generator | 📦 TP Compilation & Health | 🧪 Checkout")

    except Exception as e:
        print(f"⚠ Phase 2 MVC could not load (existing GUI still works): {e}")
        import traceback
        traceback.print_exc()

    root.mainloop()


if __name__ == "__main__":
    main()

