# -*- coding: utf-8 -*-
"""
view/dialogs/hardware_config_dialog.py
=======================================
Modal dialog for viewing and editing the hardware configuration
(DIB_TYPE, MACHINE_MODEL, MACHINE_VENDOR) used by profile generation.

Mirrors the original CAT's "View/Edit Hardware" panel in
GUI/panels/profile_generate_panel.py:200-280.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox
from typing import Dict, Optional, Any

from model.hardware_config import (
    HardwareConfig,
    get_hardware_config,
    VALID_STEPS,
    VALID_FORM_FACTORS,
)


class HardwareConfigDialog(tk.Toplevel):
    """
    A modal dialog that displays the hardware configuration in an editable
    grid.  Layout:

        ┌─────────────────────────────────────────────────────────┐
        │  DIB_TYPE                                               │
        │  ──────────────────────────────────────────────────────  │
        │           U.2     U.3     E3.S    E1.S    E1.L    M.2   │
        │  ABIT   [MS0022] [     ] [MS0032] [MS0028] [    ] [   ] │
        │  SFN2   [MS0050] [     ] [MS0049] [MS0083] [    ] [   ] │
        │  SCHP   [MS0055] [     ] [MS0053] [MS0054] [    ] [   ] │
        ├─────────────────────────────────────────────────────────┤
        │  MACHINE_MODEL / MACHINE_VENDOR                         │
        │  ──────────────────────────────────────────────────────  │
        │           Model          Vendor                         │
        │  ABIT   [IBIR         ] [NEOS          ]                │
        │  SFN2   [MPT3000HVM3 ] [ADVT          ]                │
        │  SCHP   [FX7          ] [NEOSEM        ]                │
        ├─────────────────────────────────────────────────────────┤
        │                          [ Save ]  [ Cancel ]           │
        └─────────────────────────────────────────────────────────┘
    """

    def __init__(self, parent: Any, hw_config: Optional[HardwareConfig] = None):
        super().__init__(parent)
        self.title("View / Edit Hardware Configuration")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._hw = hw_config or get_hardware_config()
        self._dib_vars: Dict[str, Dict[str, tk.StringVar]] = {}
        self._model_vars: Dict[str, tk.StringVar] = {}
        self._vendor_vars: Dict[str, tk.StringVar] = {}

        self._build_ui()
        self._center_on_parent(parent)

    # ──────────────────────────────────────────────────────────────────
    # UI Construction
    # ──────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        pad: Dict[str, int] = dict(padx=4, pady=2)

        # ── DIB_TYPE section ─────────────────────────────────────────
        dib_frame = ttk.LabelFrame(self, text="  DIB_TYPE  ", padding=(10, 6, 10, 10))
        dib_frame.grid(row=0, column=0, sticky="we", padx=12, pady=(12, 6))

        # Column headers
        ttk.Label(dib_frame, text="Step", font=("", 9, "bold")).grid(
            row=0, column=0, sticky=tk.W, padx=pad["padx"], pady=pad["pady"])
        for col_idx, ff in enumerate(VALID_FORM_FACTORS):
            ttk.Label(dib_frame, text=ff, font=("", 9, "bold")).grid(
                row=0, column=col_idx + 1, padx=pad["padx"], pady=pad["pady"])

        dib_dict = self._hw.get_dib_dict()
        for row_idx, step in enumerate(VALID_STEPS):
            ttk.Label(dib_frame, text=step, font=("", 9, "bold")).grid(
                row=row_idx + 1, column=0, sticky=tk.W,
                padx=pad["padx"], pady=pad["pady"])
            self._dib_vars[step] = {}
            step_dibs = dib_dict.get(step, {})
            for col_idx, ff in enumerate(VALID_FORM_FACTORS):
                var = tk.StringVar(value=step_dibs.get(ff, ""))
                self._dib_vars[step][ff] = var
                entry = ttk.Entry(dib_frame, textvariable=var, width=10,
                                  justify=tk.CENTER)
                entry.grid(row=row_idx + 1, column=col_idx + 1,
                           padx=pad["padx"], pady=pad["pady"])

        # ── MACHINE_MODEL / MACHINE_VENDOR section ───────────────────
        machine_frame = ttk.LabelFrame(
            self, text="  MACHINE_MODEL / MACHINE_VENDOR  ",
            padding=(10, 6, 10, 10))
        machine_frame.grid(row=1, column=0, sticky="we", padx=12, pady=(6, 6))

        ttk.Label(machine_frame, text="Step", font=("", 9, "bold")).grid(
            row=0, column=0, sticky=tk.W, padx=pad["padx"], pady=pad["pady"])
        ttk.Label(machine_frame, text="Model", font=("", 9, "bold")).grid(
            row=0, column=1, padx=pad["padx"], pady=pad["pady"])
        ttk.Label(machine_frame, text="Vendor", font=("", 9, "bold")).grid(
            row=0, column=2, padx=pad["padx"], pady=pad["pady"])

        model_dict = self._hw.get_machine_model_dict()
        vendor_dict = self._hw.get_machine_vendor_dict()

        for row_idx, step in enumerate(VALID_STEPS):
            ttk.Label(machine_frame, text=step, font=("", 9, "bold")).grid(
                row=row_idx + 1, column=0, sticky=tk.W,
                padx=pad["padx"], pady=pad["pady"])

            model_var = tk.StringVar(value=model_dict.get(step, ""))
            self._model_vars[step] = model_var
            ttk.Entry(machine_frame, textvariable=model_var, width=18).grid(
                row=row_idx + 1, column=1,
                padx=pad["padx"], pady=pad["pady"])

            vendor_var = tk.StringVar(value=vendor_dict.get(step, ""))
            self._vendor_vars[step] = vendor_var
            ttk.Entry(machine_frame, textvariable=vendor_var, width=18).grid(
                row=row_idx + 1, column=2,
                padx=pad["padx"], pady=pad["pady"])

        # ── Buttons ──────────────────────────────────────────────────
        btn_frame = ttk.Frame(self)
        btn_frame.grid(row=2, column=0, sticky="e", padx=12, pady=(6, 12))

        ttk.Button(btn_frame, text="Save", command=self._on_save).pack(
            side=tk.LEFT, padx=(0, 6))
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(
            side=tk.LEFT)

    # ──────────────────────────────────────────────────────────────────
    # Save handler
    # ──────────────────────────────────────────────────────────────────

    def _on_save(self) -> None:
        """Validate and save all entries back to HardwareConfig."""
        # Validate DIB_TYPE format (MSxxxx)
        errors = []
        for step in VALID_STEPS:
            for ff in VALID_FORM_FACTORS:
                val = self._dib_vars[step][ff].get().strip().upper()
                if val and not HardwareConfig.validate_dib_type_format(val):
                    errors.append(
                        f"DIB_TYPE [{step}][{ff}] = '{val}' — "
                        f"expected format MSxxxx (e.g. MS0022)")

        if errors:
            messagebox.showerror(
                "Validation Error",
                "Please fix the following:\n\n" + "\n".join(errors),
                parent=self,
            )
            return

        # Apply DIB_TYPE values
        for step in VALID_STEPS:
            for ff in VALID_FORM_FACTORS:
                val = self._dib_vars[step][ff].get().strip().upper()
                self._hw.update_dib_type(step, ff, val)

        # Apply MACHINE_MODEL values
        for step in VALID_STEPS:
            val = self._model_vars[step].get().strip()
            self._hw.update_machine_model(step, val)

        # Apply MACHINE_VENDOR values (use internal _data since no
        # dedicated update method exists for vendor)
        for step in VALID_STEPS:
            val = self._vendor_vars[step].get().strip()
            if "MACHINE_VENDOR" not in self._hw._data:
                self._hw._data["MACHINE_VENDOR"] = {}
            self._hw._data["MACHINE_VENDOR"][step] = val

        # Persist to disk
        try:
            self._hw.save()
            messagebox.showinfo(
                "Saved",
                "Hardware configuration saved successfully.",
                parent=self,
            )
            self.destroy()
        except Exception as e:
            messagebox.showerror(
                "Save Error",
                f"Failed to save hardware config:\n{e}",
                parent=self,
            )

    # ──────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────

    def _center_on_parent(self, parent: Any) -> None:
        """Center the dialog over its parent window."""
        self.update_idletasks()
        w = self.winfo_width()
        h = self.winfo_height()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f"+{x}+{y}")
