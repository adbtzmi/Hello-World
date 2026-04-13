#!/usr/bin/env python3
"""
BENTO - Build, Evaluate, Navigate, Test & Orchestrate
Entry Point — Phase 3D (Clean MVC, no gui/app.py)

Wiring order:
  1. Load config from settings.json
  2. Create Tk root (with High-DPI fix)
  3. Create JIRAAnalyzer (model layer)
  4. Create AppContext (shared state)
  5. Create BentoController(config)  — no context yet
  6. Create BentoApp(root, controller, config, ...) — builds all tabs
  7. controller.set_view(app)         — wires context into all controllers
  8. root.mainloop()

gui/app.py (SimpleGUI) is no longer launched.
Compilation and Checkout tabs are now main-level tabs (no injection needed).
"""

import sys
import os
import json
import logging
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Logging setup ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s  %(name)s  %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bento_app")

APP_TITLE   = "BENTO"
APP_VERSION = "- GUI"


def main():
    # ── High-DPI fix (Windows) ─────────────────────────────────────────────
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    # ── Step 1: Load config ────────────────────────────────────────────────
    config = {}
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
    try:
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                config = json.load(f)
        else:
            logger.warning(f"settings.json not found at {config_path} — using defaults")
    except Exception as e:
        logger.error(f"Could not load settings.json: {e}")

    # ── Step 2: Create Tk root ─────────────────────────────────────────────
    root = tk.Tk()

    # ── Step 3: Create JIRAAnalyzer (Model) ────────────────────────────────
    analyzer = None
    try:
        from jira_analyzer import JIRAAnalyzer
        analyzer = JIRAAnalyzer()   # takes no constructor args; credentials loaded later via UI
    except Exception as e:
        logger.error(f"Could not initialise JIRAAnalyzer: {e}")

    # ── Step 4: Create AppContext ───────────────────────────────────────────
    try:
        from context import AppContext
        context = AppContext(
            root=root,
            analyzer=analyzer,
            config=config,
            log_callback=None,   # overwritten by BentoApp._log_message after set_view
        )
    except Exception as e:
        logger.critical(f"Cannot create AppContext: {e}")
        root.destroy()
        return

    # ── Step 5: Create BentoController ─────────────────────────────────────
    try:
        from controller.bento_controller import BentoController
        controller = BentoController(config=config)
        # Wire controller into context NOW so tabs can access it during __init__
        context.controller = controller
    except Exception as e:
        logger.critical(f"Cannot create BentoController: {e}")
        root.destroy()
        return

    # ── Step 6: Create BentoApp (View — builds all tabs) ───────────────────
    try:
        from view.app import BentoApp
        app = BentoApp(
            root=root,
            controller=controller,
            config=config,
            app_title=APP_TITLE,
            app_version=APP_VERSION,
        )
    except Exception as e:
        logger.critical(f"Cannot create BentoApp: {e}")
        import traceback
        traceback.print_exc()
        root.destroy()
        return

    # ── Step 7: Wire context + analyzer into all controllers ────────────────
    try:
        # Inject analyzer into the app's AppContext
        app.context.analyzer = analyzer
        # set_view: wires context into every sub-controller (Phase 2 + 3C/3D)
        controller.set_view(app)
        # Also update the log_callback now that BentoApp owns the log panel
        app.context.log_callback = app._log_message
    except Exception as e:
        logger.error(f"set_view() failed: {e}")
        import traceback
        traceback.print_exc()

    # ── Step 8: Enter Tk event loop ─────────────────────────────────────────
    # Note: Compilation and Checkout tabs are now created directly in BentoApp._build_tabs()
    root.mainloop()


if __name__ == "__main__":
    main()
