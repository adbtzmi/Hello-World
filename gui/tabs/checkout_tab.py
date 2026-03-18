#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
checkout_tab.py
===============
BENTO Checkout Tab — Phase 2 Auto Start Checkout

Mirrors compile_tab.py pattern for Phase 1 compilation.

Responsibilities:
    - Load DUT info from CRT database/Excel
    - Configure checkout parameters (MIDs, lot prefix, DUT locations)
    - Trigger checkout orchestration
    - Display real-time progress
    - Show completion status
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import os
import sys
import threading

# Import backend orchestrator
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from model.orchestrators import checkout_orchestrator


class CheckoutTab(ttk.Frame):
    """
    Phase 2 — Auto Start Checkout Tab
    
    Mirrors CompileTab structure from Phase 1.
    """
    
    def __init__(self, parent, controller, log_callback=None):
        """
        Args:
            parent: Parent notebook widget
            controller: BentoController instance (from bento_controller.py)
            log_callback: Callback function for logging to main GUI
        """
        super().__init__(parent, padding="10")
        
        self.controller = controller
        self.log_callback = log_callback
        
        # State variables
        self.current_jira_key = None
        self.current_tgz_path = None
        self.checkout_running = False
        
        self.build_ui()
    
    def log(self, msg: str):
        """Log message to main GUI log panel"""
        if self.log_callback:
            self.log_callback(msg)
        else:
            print(msg)
    
    def build_ui(self):
        """Build the checkout tab UI"""
        
        # Title
        title_frame = ttk.Frame(self)
        title_frame.grid(row=0, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=(0, 10))
        
        ttk.Label(
            title_frame,
            text="Phase 2 — Auto Start Checkout",
            font=('Arial', 14, 'bold')
        ).pack(side=tk.LEFT)
        
        ttk.Label(
            title_frame,
            text="(Requires Phase 1 TGZ output)",
            font=('Arial', 10),
            foreground='gray'
        ).pack(side=tk.LEFT, padx=(10, 0))
        
        # ─────────────────────────────────────────────────────────
        # Section 1: CRT Excel Grid (CRAB mirror)
        # ─────────────────────────────────────────────────────────
        
        grid_frame = ttk.LabelFrame(self, text="1. CRT Excel Grid (Click row to auto-fill)", padding="10")
        grid_frame.grid(row=1, column=0, columnspan=3, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)
        
        # Excel file selection
        excel_select_frame = ttk.Frame(grid_frame)
        excel_select_frame.pack(fill=tk.X, pady=(0, 5))
        
        ttk.Label(excel_select_frame, text="Excel File:").pack(side=tk.LEFT, padx=(0, 5))
        
        self.excel_path_var = tk.StringVar(
            value=r"\\sifsmodauto\modauto\temp\cat\production\crt_from_sap.xlsx"
        )
        ttk.Entry(
            excel_select_frame,
            textvariable=self.excel_path_var,
            width=60
        ).pack(side=tk.LEFT, padx=(0, 5))
        
        ttk.Button(
            excel_select_frame,
            text="📁 Browse",
            command=self._browse_crt_excel,
            width=10
        ).pack(side=tk.LEFT, padx=(0, 5))
        
        ttk.Button(
            excel_select_frame,
            text="🔄 Load CRT Data",
            command=self._load_crt_grid,
            width=15
        ).pack(side=tk.LEFT)
        
        # Filter CFGPN
        filter_frame = ttk.Frame(grid_frame)
        filter_frame.pack(fill=tk.X, pady=(0, 5))
        
        ttk.Label(filter_frame, text="Filter CFGPN:").pack(side=tk.LEFT, padx=(0, 5))
        self.filter_cfgpn_var = tk.StringVar()
        self.filter_cfgpn_var.trace('w', lambda *args: self._apply_filter())
        ttk.Entry(filter_frame, textvariable=self.filter_cfgpn_var, width=20).pack(side=tk.LEFT)
        
        ttk.Label(
            filter_frame,
            text="(Type to filter by CFGPN)",
            foreground='gray',
            font=('Arial', 8)
        ).pack(side=tk.LEFT, padx=(5, 0))
        
        # Treeview for CRT data (mirrors CRAB)
        tree_frame = ttk.Frame(grid_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        
        # Scrollbars
        tree_scroll_y = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL)
        tree_scroll_x = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL)
        
        # Treeview
        self.crt_tree = ttk.Treeview(
            tree_frame,
            columns=("mid", "cfgpn", "fw_wave", "product", "customer"),
            show="headings",
            height=8,
            yscrollcommand=tree_scroll_y.set,
            xscrollcommand=tree_scroll_x.set
        )
        
        # Configure scrollbars
        tree_scroll_y.config(command=self.crt_tree.yview)
        tree_scroll_x.config(command=self.crt_tree.xview)
        
        # Column headings
        self.crt_tree.heading("mid", text="Material Description")
        self.crt_tree.heading("cfgpn", text="CFGPN")
        self.crt_tree.heading("fw_wave", text="FW Wave ID")
        self.crt_tree.heading("product", text="Product Name")
        self.crt_tree.heading("customer", text="CRT Customer")
        
        # Column widths
        self.crt_tree.column("mid", width=200)
        self.crt_tree.column("cfgpn", width=80)
        self.crt_tree.column("fw_wave", width=100)
        self.crt_tree.column("product", width=100)
        self.crt_tree.column("customer", width=150)
        
        # Bind row selection
        self.crt_tree.bind("<<TreeviewSelect>>", self._on_crt_row_select)
        
        # Pack treeview and scrollbars
        self.crt_tree.grid(row=0, column=0, sticky=(tk.N, tk.S, tk.E, tk.W))
        tree_scroll_y.grid(row=0, column=1, sticky=(tk.N, tk.S))
        tree_scroll_x.grid(row=1, column=0, sticky=(tk.E, tk.W))
        
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        
        # Store full CRT data for filtering
        self.crt_data_full = []
        
        # ─────────────────────────────────────────────────────────
        # Section 2: Checkout Parameters
        # ─────────────────────────────────────────────────────────
        
        params_frame = ttk.LabelFrame(self, text="2. Checkout Parameters", padding="10")
        params_frame.grid(row=2, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=5)
        
        # MID List (comma-separated)
        ttk.Label(params_frame, text="MID List:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.mids_var = tk.StringVar()
        mids_entry = ttk.Entry(params_frame, textvariable=self.mids_var, width=60)
        mids_entry.grid(row=0, column=1, columnspan=2, pady=5, sticky=(tk.W, tk.E))
        
        ttk.Label(
            params_frame,
            text="Comma-separated (e.g., TUN00PNHW, TUN00PNJS, TUR0B9SZD)",
            foreground='gray',
            font=('Arial', 8)
        ).grid(row=1, column=1, columnspan=2, sticky=tk.W)
        
        # Dummy Lot Prefix
        ttk.Label(params_frame, text="Dummy Lot Prefix:").grid(row=2, column=0, sticky=tk.W, pady=5)
        self.lot_prefix_var = tk.StringVar(value="JAANTJB")
        ttk.Entry(params_frame, textvariable=self.lot_prefix_var, width=20).grid(row=2, column=1, sticky=tk.W, pady=5)
        
        ttk.Label(
            params_frame,
            text="Will generate: JAANTJB001, JAANTJB002, ...",
            foreground='gray',
            font=('Arial', 8)
        ).grid(row=3, column=1, columnspan=2, sticky=tk.W)
        
        # DUT Locations
        ttk.Label(params_frame, text="DUT Locations:").grid(row=4, column=0, sticky=tk.W, pady=5)
        self.dut_locs_var = tk.StringVar(value="0,0 0,1 0,2 0,3 0,4 0,5 0,6 0,7")
        ttk.Entry(params_frame, textvariable=self.dut_locs_var, width=60).grid(row=4, column=1, columnspan=2, pady=5, sticky=(tk.W, tk.E))
        
        ttk.Label(
            params_frame,
            text="Space-separated row,col pairs (e.g., 0,0 0,1 0,2)",
            foreground='gray',
            font=('Arial', 8)
        ).grid(row=5, column=1, columnspan=2, sticky=tk.W)
        
        # TGZ Path (from Phase 1 compile output)
        ttk.Label(params_frame, text="TGZ Path:").grid(row=6, column=0, sticky=tk.W, pady=5)
        self.tgz_path_var = tk.StringVar()
        ttk.Entry(params_frame, textvariable=self.tgz_path_var, width=50).grid(row=6, column=1, pady=5, sticky=(tk.W, tk.E))
        ttk.Button(
            params_frame,
            text="📁 Browse",
            command=self._browse_tgz,
            width=10
        ).grid(row=6, column=2, pady=5, padx=(5, 0))
        
        ttk.Label(
            params_frame,
            text="Path to compiled TGZ from Phase 1 (e.g., P:\\RELEASE\\SSD\\...\\IBIR_RELEASE.TGZ)",
            foreground='gray',
            font=('Arial', 8)
        ).grid(row=7, column=1, columnspan=2, sticky=tk.W)
        
        # ENV Selector (from bento_testers.json)
        ttk.Label(params_frame, text="Tester ENV:").grid(row=8, column=0, sticky=tk.W, pady=5)
        self.env_var = tk.StringVar(value="ABIT")
        env_combo = ttk.Combobox(
            params_frame,
            textvariable=self.env_var,
            values=["ABIT", "SFN2", "CNFG"],
            state="readonly",
            width=15
        )
        env_combo.grid(row=8, column=1, sticky=tk.W, pady=5)
        
        ttk.Label(
            params_frame,
            text="Tester environment (must match watcher deployment)",
            foreground='gray',
            font=('Arial', 8)
        ).grid(row=9, column=1, columnspan=2, sticky=tk.W)
        
        # JIRA Key (for tracking)
        ttk.Label(params_frame, text="JIRA Key:").grid(row=10, column=0, sticky=tk.W, pady=5)
        self.jira_key_var = tk.StringVar()
        ttk.Entry(params_frame, textvariable=self.jira_key_var, width=20).grid(row=10, column=1, sticky=tk.W, pady=5)
        
        ttk.Label(
            params_frame,
            text="For workflow tracking (e.g., TSESSD-123)",
            foreground='gray',
            font=('Arial', 8)
        ).grid(row=11, column=1, columnspan=2, sticky=tk.W)
        
        # Configure column weights
        params_frame.columnconfigure(1, weight=1)
        
        # ─────────────────────────────────────────────────────────
        # Section 3: Status & Control
        # ─────────────────────────────────────────────────────────
        
        control_frame = ttk.LabelFrame(self, text="3. Start Checkout", padding="10")
        control_frame.grid(row=3, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=5)
        
        # Status label
        self.status_var = tk.StringVar(value="Ready")
        status_label = ttk.Label(
            control_frame,
            textvariable=self.status_var,
            font=('Arial', 11),
            foreground='blue'
        )
        status_label.grid(row=0, column=0, pady=5, sticky=tk.W)
        
        # Start button
        self.start_btn = ttk.Button(
            control_frame,
            text="🚀 Start Checkout",
            command=self._start_checkout,
            style='Accent.TButton'
        )
        self.start_btn.grid(row=1, column=0, pady=10)
        
        # ─────────────────────────────────────────────────────────
        # Section 4: Progress Log (local to this tab)
        # ─────────────────────────────────────────────────────────
        
        log_frame = ttk.LabelFrame(self, text="Checkout Progress", padding="5")
        log_frame.grid(row=4, column=0, columnspan=3, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)
        
        self.checkout_log = scrolledtext.ScrolledText(
            log_frame,
            height=15,
            width=80,
            wrap=tk.WORD,
            state='disabled'
        )
        self.checkout_log.pack(fill=tk.BOTH, expand=True)
        
        # Configure grid weights for resizing
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)  # CRT grid expandable
        self.rowconfigure(4, weight=1)  # Progress log expandable
    
    # ═════════════════════════════════════════════════════════════
    # HELPER METHODS
    # ═════════════════════════════════════════════════════════════
    
    def _log_local(self, msg: str):
        """Log to local checkout log panel"""
        self.checkout_log.config(state='normal')
        self.checkout_log.insert(tk.END, msg + "\n")
        self.checkout_log.see(tk.END)
        self.checkout_log.config(state='disabled')
        
        # Also log to main GUI log
        self.log(msg)
    
    # ─────────────────────────────────────────────────────────────
    # CRT EXCEL GRID METHODS (Section 1)
    # ─────────────────────────────────────────────────────────────
    
    def _browse_crt_excel(self):
        """Browse for CRT Excel file"""
        filename = filedialog.askopenfilename(
            title="Select CRT Export Excel",
            filetypes=[
                ("Excel files", "*.xlsx *.xls"),
                ("All files", "*.*")
            ],
            initialdir=r"\\sifsmodauto\modauto\temp\cat\production" 
                if os.path.exists(r"\\sifsmodauto\modauto\temp\cat\production") 
                else "."
        )
        if filename:
            self.excel_path_var.set(filename)
            self._log_local(f"✓ Selected Excel: {os.path.basename(filename)}")
    
    def _load_crt_grid(self):
        """Load CRT data into grid from Excel or database"""
        excel_path = self.excel_path_var.get().strip()
        
        if not excel_path:
            messagebox.showwarning(
                "No Excel File",
                "Please select a CRT Excel file first."
            )
            return
        
        try:
            self._log_local(f"Loading CRT data from {os.path.basename(excel_path)}...")
            
            # Use orchestrator's CRT integration function
            dut_list = checkout_orchestrator.load_dut_info_from_crt(excel_path=excel_path)
            
            if not dut_list:
                messagebox.showwarning(
                    "No Data",
                    "No DUT information found in the selected file."
                )
                return
            
            # Store full data for filtering
            self.crt_data_full = dut_list
            
            # Populate grid
            self._populate_crt_grid(dut_list)
            
            self._log_local(f"✓ Loaded {len(dut_list)} DUTs from CRT export")
            
            messagebox.showinfo(
                "CRT Data Loaded",
                f"Successfully loaded {len(dut_list)} DUTs.\n\n"
                f"Click any row to auto-fill checkout parameters."
            )
            
        except Exception as e:
            self._log_local(f"✗ Error loading CRT data: {e}")
            messagebox.showerror(
                "Load Error",
                f"Failed to load CRT data:\n\n{e}"
            )
    
    def _populate_crt_grid(self, dut_list):
        """Populate treeview with DUT data"""
        # Clear existing items
        for item in self.crt_tree.get_children():
            self.crt_tree.delete(item)
        
        # Insert new items
        for dut in dut_list:
            mid = dut.get("MID", "")
            cfgpn = dut.get("CFGPN", "")
            fw_wave = dut.get("FW_Wave_ID", "")
            product = dut.get("Product_Name", "")
            customer = dut.get("CRT_Customer", "")
            
            self.crt_tree.insert(
                "",
                tk.END,
                values=(mid, cfgpn, fw_wave, product, customer),
                tags=(cfgpn,)  # Store CFGPN as tag for filtering
            )
    
    def _apply_filter(self):
        """Filter CRT grid by CFGPN"""
        if not self.crt_data_full:
            return
        
        filter_text = self.filter_cfgpn_var.get().strip().lower()
        
        if not filter_text:
            # No filter — show all
            self._populate_crt_grid(self.crt_data_full)
        else:
            # Filter by CFGPN
            filtered = [
                dut for dut in self.crt_data_full
                if filter_text in str(dut.get("CFGPN", "")).lower()
            ]
            self._populate_crt_grid(filtered)
    
    def _on_crt_row_select(self, event):
        """Handle CRT grid row selection — auto-fill Section 2"""
        selection = self.crt_tree.selection()
        if not selection:
            return
        
        # Get selected row data
        item = selection[0]
        values = self.crt_tree.item(item, "values")
        
        if not values:
            return
        
        mid, cfgpn, fw_wave, product, customer = values
        
        # Auto-fill Section 2 manual inputs
        self.mids_var.set(mid)
        
        # Auto-generate dummy lot from CFGPN
        # Format: JAANTJB + last 3 digits of CFGPN
        try:
            cfgpn_suffix = str(cfgpn)[-3:].zfill(3)
            auto_lot = f"JAANTJB{cfgpn_suffix}"
            self.lot_prefix_var.set(auto_lot)
        except Exception:
            pass  # Keep default if auto-generation fails
        
        self._log_local(f"✓ Auto-filled from CRT: MID={mid}, CFGPN={cfgpn}, FW Wave={fw_wave}")
        
        # Show info tooltip
        messagebox.showinfo(
            "Row Selected",
            f"Auto-filled checkout parameters:\n\n"
            f"  MID:      {mid}\n"
            f"  CFGPN:    {cfgpn}\n"
            f"  FW Wave:  {fw_wave}\n"
            f"  Product:  {product}\n\n"
            f"Review and adjust parameters in Section 2 below."
        )
    
    def _browse_tgz(self):
        """Browse for TGZ file"""
        filename = filedialog.askopenfilename(
            title="Select Compiled TGZ",
            filetypes=[
                ("TGZ files", "*.tgz *.TGZ"),
                ("All files", "*.*")
            ],
            initialdir=r"P:\RELEASE\SSD" if os.path.exists(r"P:\RELEASE\SSD") else "."
        )
        if filename:
            self.tgz_path_var.set(filename)
            self._log_local(f"✓ Selected TGZ: {os.path.basename(filename)}")
    
    
    def _validate_inputs(self) -> tuple:
        """
        Validate all checkout inputs.
        
        Returns:
            (success: bool, error_msg: str)
        """
        errors = []
        
        # TGZ path
        tgz_path = self.tgz_path_var.get().strip()
        if not tgz_path:
            errors.append("TGZ path is required")
        elif not os.path.exists(tgz_path):
            errors.append(f"TGZ file not found: {tgz_path}")
        
        # MID list
        mids_raw = self.mids_var.get().strip()
        if not mids_raw:
            errors.append("MID list is empty")
        
        # Lot prefix
        lot_prefix = self.lot_prefix_var.get().strip()
        if not lot_prefix:
            errors.append("Dummy lot prefix is required")
        
        # DUT locations
        dut_locs_raw = self.dut_locs_var.get().strip()
        if not dut_locs_raw:
            errors.append("DUT locations list is empty")
        
        # ENV
        env = self.env_var.get().strip()
        if not env:
            errors.append("Tester ENV is required")
        
        # JIRA key
        jira_key = self.jira_key_var.get().strip()
        if not jira_key:
            errors.append("JIRA key is required for tracking")
        
        # Count validation
        if not errors:
            mids = [m.strip() for m in mids_raw.split(",") if m.strip()]
            dut_locs = dut_locs_raw.split()
            
            if len(mids) != len(dut_locs):
                errors.append(
                    f"MID count ({len(mids)}) must match DUT location count ({len(dut_locs)})"
                )
        
        if errors:
            return False, "\n".join(f"  • {e}" for e in errors)
        
        return True, ""
    
    def _start_checkout(self):
        """Start checkout orchestration"""
        
        if self.checkout_running:
            messagebox.showwarning(
                "Checkout Running",
                "A checkout is already in progress. Please wait for it to complete."
            )
            return
        
        # Validate inputs
        valid, error_msg = self._validate_inputs()
        if not valid:
            messagebox.showerror(
                "Validation Failed",
                f"Please fix the following errors:\n\n{error_msg}"
            )
            return
        
        # Gather inputs
        tgz_path = self.tgz_path_var.get().strip()
        mids_raw = self.mids_var.get().strip()
        lot_prefix = self.lot_prefix_var.get().strip()
        dut_locs_raw = self.dut_locs_var.get().strip()
        env = self.env_var.get().strip()
        jira_key = self.jira_key_var.get().strip()
        
        mids = [m.strip() for m in mids_raw.split(",") if m.strip()]
        dut_locs = dut_locs_raw.split()
        
        # Confirm with user
        confirm_msg = (
            f"Start checkout with the following parameters?\n\n"
            f"  JIRA:      {jira_key}\n"
            f"  ENV:       {env}\n"
            f"  TGZ:       {os.path.basename(tgz_path)}\n"
            f"  DUT Count: {len(mids)}\n"
            f"  Lot:       {lot_prefix}001 - {lot_prefix}{str(len(mids)).zfill(3)}\n\n"
            f"This will:\n"
            f"  1. Generate XML profile\n"
            f"  2. Drop to CHECKOUT_QUEUE\n"
            f"  3. Wait for tester to complete\n"
            f"  4. Auto-collect memory for all DUTs\n\n"
            f"Estimated time: 2-8 hours"
        )
        
        if not messagebox.askyesno("Confirm Checkout", confirm_msg):
            return
        
        # Clear local log
        self.checkout_log.config(state='normal')
        self.checkout_log.delete('1.0', tk.END)
        self.checkout_log.config(state='disabled')
        
        # Update UI state
        self.checkout_running = True
        self.start_btn.config(state='disabled')
        self.status_var.set("Running...")
        
        self._log_local("=" * 60)
        self._log_local("BENTO Phase 2 — Auto Start Checkout")
        self._log_local(f"JIRA:      {jira_key}")
        self._log_local(f"ENV:       {env}")
        self._log_local(f"TGZ:       {os.path.basename(tgz_path)}")
        self._log_local(f"DUT Count: {len(mids)}")
        self._log_local("=" * 60)
        self._log_local("")
        
        # Run in background thread (mirrors compilation pattern)
        def _run():
            result = checkout_orchestrator.run_checkout(
                tgz_path=tgz_path,
                mids=mids,
                lot_prefix=lot_prefix,
                dut_locations=dut_locs,
                env=env,
                jira_key=jira_key,
                log_callback=self._log_local,
            )
            
            # Update GUI on main thread
            self.after(0, lambda: self._on_checkout_done(result))
        
        threading.Thread(target=_run, daemon=True).start()
    
    def _on_checkout_done(self, result: dict):
        """Handle checkout completion"""
        
        self.checkout_running = False
        self.start_btn.config(state='normal')
        
        status = result.get("status", "unknown")
        detail = result.get("detail", "")
        elapsed = result.get("elapsed", 0)
        
        elapsed_str = f"{elapsed // 3600}h {(elapsed % 3600) // 60}m {elapsed % 60}s"
        
        if status == "success":
            self.status_var.set("✓ Checkout Complete!")
            self._log_local("")
            self._log_local("=" * 60)
            self._log_local("✓ CHECKOUT COMPLETE!")
            self._log_local(f"  Elapsed: {elapsed_str}")
            self._log_local(f"  Detail:  {detail}")
            self._log_local("=" * 60)
            
            messagebox.showinfo(
                "Checkout Complete",
                f"Checkout completed successfully!\n\n"
                f"Elapsed time: {elapsed_str}\n\n"
                f"{detail}"
            )
        
        elif status == "failed":
            self.status_var.set("✗ Checkout Failed")
            self._log_local("")
            self._log_local("=" * 60)
            self._log_local("✗ CHECKOUT FAILED")
            self._log_local(f"  Elapsed: {elapsed_str}")
            self._log_local(f"  Error:   {detail}")
            self._log_local("=" * 60)
            
            messagebox.showerror(
                "Checkout Failed",
                f"Checkout failed after {elapsed_str}:\n\n{detail}\n\n"
                f"Check the log for details."
            )
        
        elif status == "timeout":
            self.status_var.set("⏱ Checkout Timeout")
            self._log_local("")
            self._log_local("=" * 60)
            self._log_local("⏱ CHECKOUT TIMEOUT")
            self._log_local(f"  Elapsed: {elapsed_str}")
            self._log_local(f"  Detail:  {detail}")
            self._log_local("=" * 60)
            
            messagebox.showwarning(
                "Checkout Timeout",
                f"Checkout timed out after {elapsed_str}:\n\n{detail}\n\n"
                f"Check tester status manually."
            )
        
        else:
            self.status_var.set(f"? Unknown Status: {status}")
            self._log_local(f"? Unknown checkout status: {status}")
