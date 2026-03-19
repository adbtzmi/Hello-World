#!/usr/bin/env python3
"""
BENTO - Build, Evaluate, Navigate, Test & Orchestrate
Entry Point

Phase 1: Launches existing SimpleGUI (gui/app.py) — completely unchanged.
Phase 2: Injects the Checkout sub-tab into the Implementation tab's sub-notebook,
         right after the existing TP Compilation & Health sub-tab.
"""

import sys
import os
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    # ── High-DPI fix for Windows ──────────────────────────────────────────
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    root = tk.Tk()

    # ── Phase 1: Launch existing GUI (completely unchanged) ───────────────
    from gui.app import SimpleGUI
    app = SimpleGUI(root)

    # ── Phase 2: Inject Checkout sub-tab into Implementation tab ──────────
    try:
        import json
        from controller.bento_controller import BentoController
        from context import AppContext
        from view.tabs.checkout_tab import CheckoutTab

        # Load settings.json
        config = {}
        try:
            with open('settings.json', 'r') as f:
                config = json.load(f)
        except Exception:
            pass

        # Shared log callback — pipes into existing GUI log panel
        def log_to_gui(msg):
            try:
                app.log(msg)
            except Exception:
                print(msg)

        # AppContext — shared state for new tabs
        context = AppContext(
            root=root,
            analyzer=app.analyzer,
            config=config,
            log_callback=log_to_gui,
        )

        # Get the Implementation sub-notebook (stored by app.py as self.impl_notebook)
        impl_notebook = app.impl_notebook

        # Master controller
        controller = BentoController(config=config)
        context.controller = controller

        # Add Checkout as SUB-TAB 3 inside Implementation
        checkout_tab = CheckoutTab(impl_notebook, context)

        # Minimal view adapter — lets controller call back into the checkout tab
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

        controller.set_view(_MinimalView())

        print("✓ Phase 2: Checkout sub-tab injected into Implementation tab")

    except Exception as e:
        # If Phase 2 fails, existing GUI still works perfectly
        print(f"⚠ Phase 2 Checkout tab could not load (existing GUI still works): {e}")
        import traceback
        traceback.print_exc()

    root.mainloop()


if __name__ == "__main__":
    main()
