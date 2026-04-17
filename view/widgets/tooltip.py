#!/usr/bin/env python3
"""
view/widgets/tooltip.py
========================
Shared lightweight balloon tooltip for any Tkinter widget.

Usage:
    from view.widgets.tooltip import ToolTip, tip
    tip(my_button, "Click to compile")
"""

import tkinter as tk


class ToolTip:
    """Lightweight balloon tooltip for any widget."""

    def __init__(self, widget, text: str, delay: int = 500):
        self._widget = widget
        self._text   = text
        self._delay  = delay
        self._id     = None
        self._tw     = None
        widget.bind("<Enter>",  self._schedule, add="+")
        widget.bind("<Leave>",  self._cancel,   add="+")
        widget.bind("<Button>", self._cancel,   add="+")

    def _schedule(self, _=None):
        self._cancel()
        self._id = self._widget.after(self._delay, self._show)

    def _cancel(self, _=None):
        if self._id:
            self._widget.after_cancel(self._id)
            self._id = None
        if self._tw:
            self._tw.destroy()
            self._tw = None

    def _show(self):
        x = self._widget.winfo_rootx() + 20
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        self._tw = tw = tk.Toplevel(self._widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.attributes("-topmost", True)
        tk.Label(
            tw, text=self._text, justify=tk.LEFT,
            relief=tk.SOLID, borderwidth=1,
            font=("Segoe UI", 8),
            padx=6, pady=4, wraplength=340,
        ).pack()


def tip(widget, text: str):
    """Convenience wrapper — attach a tooltip to *widget*."""
    ToolTip(widget, text)
