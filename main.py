#!/usr/bin/env python3
"""
JIRA Analyzer - Entry Point
This file serves as the strict entry point for the application.
UI layout components and business logic are separated into the `gui` package
using an MVC/MVVM structural foundation.
"""

import sys
import os
import tkinter as tk

# Ensure the root directory is in the path to import backend modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import the main application GUI from the refactored package
from gui.app import SimpleGUI

def main():
    try:
        # Enable high-DPI scaling on Windows to prevent blurry fonts
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    root = tk.Tk()
    app = SimpleGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()
