#!/usr/bin/env python3
"""
controller/checkout_controller.py
====================================
Checkout Controller — Phase 2

Bridges the CheckoutTab (View) and checkout_orchestrator.py (Model).

Responsibilities:
  - Receives user action from CheckoutTab._start_checkout()
  - Reads MID / CFGPN / FW data from CRT Excel
    (\\sifsmodauto\modauto\temp\cat\production\crt_from_sap.xlsx)
    using EXACT column names from crt_excel_template.json [24]
  - Auto-generates SLATE XML (replaces manual Notepad++ editing)
  - Drops generated XML into the configured hot folder
  - Fans out checkout jobs to multiple testers via threads
  - Relays phase updates and completion events back to the View

CRT Excel column names — EXACT match required [24]:
  "Material description"  <- MID
  "CFGPN"                 <- Config Part Number
  "FW Wave ID"            <- FW wave identifier
  "FIDB_ASIC_FW_REV"      <- FW revision string
  "Product  Name"         <- NOTE: DOUBLE SPACE — not a typo! [24]
  "CRT Customer"          <- Customer
  "SSD Drive Type"
  "ABIT Release (Yes/No)"
  "SFN2 Release (Yes/No)"

Mirrors CompileController's threading pattern exactly, per MVC methodology.
"""

import os
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger("bento_app")

# ── Shared registry path — same source of truth as all other modules
_REGISTRY_PATH = r"P:\temp\BENTO\bento_testers.json"

# ── CRT Excel paths from sys_config.json [25]
_CRT_EXCEL_DEFAULT    = r"\\sifsmodauto\modauto\temp\cat\production\crt_from_sap.xlsx"
_INCOMING_CRT_DEFAULT = r"\\sifsmodauto\modauto\temp\cat\production\incoming_crt.xlsx"

# ── Exact CRT Excel column names from crt_excel_template.json [24]
# WARNING: "Product  Name" has a DOUBLE SPACE — do NOT fix the "typo"!
_COL_MATERIAL_DESC  = "Material description"
_COL_CFGPN          = "CFGPN"
_COL_FW_WAVE_ID     = "FW Wave ID"
_COL_FIDB_FW_REV    = "FIDB_ASIC_FW_REV"
_COL_PRODUCT_NAME   = "Product  Name"    # DOUBLE SPACE — intentional [24]
_COL_CRT_CUSTOMER   = "CRT Customer"
_COL_SSD_DRIVE_TYPE = "SSD Drive Type"
_COL_ABIT_RELEASE   = "ABIT Release (Yes/No)"
_COL_SFN2_RELEASE   = "SFN2 Release (Yes/No)"
_COL_ABIT_STATUS    = "ABIT Status"
_COL_SFN2_STATUS    = "SFN2 Status"
_COL_OWNER          = "Owner"
_COL_DATE_RECEIVED  = "Date Received"


class CheckoutController:
    """
    Bridges CheckoutTab <-> checkout_orchestrator.py + CRT Excel integration.

    Constructor args:
        master : BentoController  (for callbacks up to View)
        config : dict             (settings.json contents)
    """

    def __init__(self, master, config: dict):
        self._master  = master
        self._config  = config
        self._view    = None
        self._running = False   # True while any checkout thread is active
        logger.info("CheckoutController initialised.")

    # ──────────────────────────────────────────────────────────────────────
    # WIRING
    # ──────────────────────────────────────────────────────────────────────

    def set_view(self, view):
        """Receive View reference after two-way wiring in BentoController."""
        self._view = view

    # ──────────────────────────────────────────────────────────────────────
    # REGISTRY HELPERS  (mirrors CompileController exactly)
    # ──────────────────────────────────────────────────────────────────────

    def get_available_testers(self):
        """
        Return a list of (hostname, env) tuples from the tester registry.
        Used by CheckoutTab to populate the tester checkbox list.
        """
        try:
            registry_path = self._config.get("registry_path", _REGISTRY_PATH)
            if not os.path.exists(registry_path):
                logger.warning(f"Tester registry not found: {registry_path}")
                return []
            with open(registry_path, "r") as f:
                data = json.load(f)
            result = []
            for val in data.values():
                hostname = val.get("hostname", "")
                env      = val.get("env", "")
                if hostname and env:
                    result.append((hostname, env))
            return result
        except Exception as e:
            logger.error(f"CheckoutController.get_available_testers: {e}")
            return []

    def _get_env_for_hostname(self, hostname: str) -> str:
        """Look up the ENV token for a given hostname."""
        try:
            registry_path = self._config.get("registry_path", _REGISTRY_PATH)
            if not os.path.exists(registry_path):
                return ""
            with open(registry_path, "r") as f:
                data = json.load(f)
            for val in data.values():
                if val.get("hostname", "").upper() == hostname.upper():
                    return val.get("env", "")
        except Exception as e:
            logger.error(f"CheckoutController._get_env_for_hostname: {e}")
        return ""

    # ──────────────────────────────────────────────────────────────────────
    # ACTIVE TASK GUARD
    # ──────────────────────────────────────────────────────────────────────

    def is_running(self) -> bool:
        """Returns True while any checkout thread is active."""
        return self._running

    # ──────────────────────────────────────────────────────────────────────
    # CRT EXCEL READER
    # Reads from \\sifsmodauto\modauto\temp\cat\production\crt_from_sap.xlsx
    # Uses EXACT column names from crt_excel_template.json [24]
    # ──────────────────────────────────────────────────────────────────────

    def load_from_crt_excel(self, cfgpn: str = "", excel_path: str = "") -> dict:
        """
        Read DUT identity fields from the CRT Excel file.

        Column names are taken EXACTLY from crt_excel_template.json [24].
        WARNING: "Product  Name" has a DOUBLE SPACE — not a typo!

        Args:
            cfgpn      : Optional CFGPN to filter rows. If empty, returns all rows.
            excel_path : Override path. If empty, reads from settings.json cat.crt_excel_path.

        Returns dict:
            {
                "mids"       : list of Material description strings,
                "cfgpn"      : first CFGPN found (or the filtered one),
                "fw_wave_id" : FW Wave ID string,
                "fw_rev"     : FIDB_ASIC_FW_REV string,
                "product"    : Product Name string,
                "customer"   : CRT Customer string,
                "drive_type" : SSD Drive Type string,
                "count"      : total row count,
                "rows"       : full list of row dicts for grid display
            }

        Raises:
            ImportError       if pandas / openpyxl are not installed
            FileNotFoundError if the Excel file does not exist
            ValueError        if required columns are missing
        """
        try:
            import pandas as pd
        except ImportError:
            raise ImportError(
                "pandas is required for CRT Excel reading.\n"
                "Install: pip install pandas openpyxl"
            )

        # ── Resolve path ──────────────────────────────────────────────────
        path = (
            excel_path
            or self._config.get("cat", {}).get("crt_excel_path", "")
            or _CRT_EXCEL_DEFAULT
        )

        if not os.path.exists(path):
            raise FileNotFoundError(
                f"CRT Excel not found: {path}\n"
                f"Check settings.json -> cat.crt_excel_path\n"
                f"Expected: {_CRT_EXCEL_DEFAULT}"
            )

        logger.info(f"CheckoutController: reading CRT Excel from: {path}")

        # ── Read Excel — dtype=str preserves leading zeros in CFGPN etc. ─
        df = pd.read_excel(path, engine="openpyxl", dtype=str)

        # ── Validate required columns ─────────────────────────────────────
        required = [_COL_MATERIAL_DESC, _COL_CFGPN, _COL_FW_WAVE_ID, _COL_FIDB_FW_REV]
        missing  = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(
                f"CRT Excel is missing required columns: {missing}\n"
                f"Available columns: {list(df.columns)}\n"
                f"Note: 'Product  Name' requires DOUBLE SPACE per crt_excel_template.json [24]"
            )

        # ── Filter by CFGPN if provided ───────────────────────────────────
        if cfgpn:
            filtered = df[df[_COL_CFGPN].str.strip() == cfgpn.strip()]
            if filtered.empty:
                logger.warning(
                    f"CheckoutController: CFGPN '{cfgpn}' not found in CRT Excel "
                    f"— returning all {len(df)} rows."
                )
                filtered = df
        else:
            filtered = df

        # ── Helper: first non-null value from a column ────────────────────
        def _first(col):
            if col not in filtered.columns:
                return ""
            vals = filtered[col].dropna().str.strip()
            return vals.iloc[0] if not vals.empty else ""

        # ── Extract fields ────────────────────────────────────────────────
        mids      = filtered[_COL_MATERIAL_DESC].dropna().str.strip().tolist()
        fw_wave   = _first(_COL_FW_WAVE_ID)
        fw_rev    = _first(_COL_FIDB_FW_REV)
        cfgpn_out = _first(_COL_CFGPN)

        # "Product  Name" has DOUBLE SPACE [24] — try that first, then single space
        product = _first(_COL_PRODUCT_NAME)
        if not product:
            product = _first("Product Name")   # single-space fallback

        customer   = _first(_COL_CRT_CUSTOMER)
        drive_type = _first(_COL_SSD_DRIVE_TYPE)

        # ── Build rows for grid display (mirrors CRAB Excel Preview Grid) ─
        display_cols = [
            _COL_MATERIAL_DESC,
            _COL_CFGPN,
            _COL_FW_WAVE_ID,
            _COL_FIDB_FW_REV,
            _COL_PRODUCT_NAME,
            _COL_CRT_CUSTOMER,
            _COL_SSD_DRIVE_TYPE,
            _COL_ABIT_RELEASE,
            _COL_SFN2_RELEASE,
        ]
        available_display = [c for c in display_cols if c in filtered.columns]
        rows = filtered[available_display].fillna("").to_dict(orient="records")

        result = {
            "mids":       mids,
            "cfgpn":      cfgpn_out,
            "fw_wave_id": fw_wave,
            "fw_rev":     fw_rev,
            "product":    product,
            "customer":   customer,
            "drive_type": drive_type,
            "count":      len(mids),
            "rows":       rows,
        }

        logger.info(
            f"CheckoutController: CRT Excel loaded — "
            f"{len(mids)} row(s) | CFGPN={cfgpn_out} | FW={fw_wave}"
        )
        return result

    # ──────────────────────────────────────────────────────────────────────
    # CRT AUTOFILL — called by CheckoutTab._autofill_from_cat_db()
    # Reads CRT Excel directly [24][25] — no CAT SAP API required
    # ──────────────────────────────────────────────────────────────────────

    def autofill_from_cat_db(self, cfgpn: str):
        """
        Look up MID / FW Wave / FW Rev from the CRT Excel file.
        Non-blocking — result relayed to View via on_autofill_completed().

        Uses exact column names from crt_excel_template.json [24]:
          Material description -> MID
          FW Wave ID           -> fw_ver (primary)
          FIDB_ASIC_FW_REV     -> fw_ver (fallback)
        """
        def _lookup():
            try:
                crt_data = self.load_from_crt_excel(cfgpn=cfgpn)

                mid    = crt_data["mids"][0] if crt_data["mids"] else ""
                fw_ver = crt_data["fw_wave_id"] or crt_data["fw_rev"]

                logger.info(
                    f"CheckoutController: autofill CFGPN={cfgpn} -> "
                    f"MID={mid}, FW={fw_ver}"
                )

                if self._view:
                    self._view.root.after(
                        0,
                        lambda: self._view.checkout_tab.on_autofill_completed(
                            mid, cfgpn, fw_ver
                        )
                    )

            except FileNotFoundError as e:
                logger.warning(f"CheckoutController.autofill: {e}")
                if self._view:
                    self._view.context.log(
                        f"⚠ CRT Excel not found. "
                        f"Check settings.json -> cat.crt_excel_path\n  {e}"
                    )
            except ValueError as e:
                logger.error(f"CheckoutController.autofill column error: {e}")
                if self._view:
                    self._view.context.log(f"✗ CRT column error: {e}")
            except Exception as e:
                logger.error(f"CheckoutController.autofill_from_cat_db: {e}")
                if self._view:
                    self._view.context.log(f"✗ CRT lookup failed: {e}")

        threading.Thread(target=_lookup, daemon=True, name="bento-crt-lookup").start()

    # ──────────────────────────────────────────────────────────────────────
    # CRT GRID LOAD — populates the preview grid in CheckoutTab
    # Mirrors the CRAB "Excel Data Preview Grid" (screenshot)
    # ──────────────────────────────────────────────────────────────────────

    def load_crt_grid(self, excel_path: str = "", cfgpn_filter: str = ""):
        """
        Load CRT Excel data into the checkout tab's preview grid.
        Called by CheckoutTab "Load CRT Data" button.
        Relays result to View via on_crt_grid_loaded().
        """
        def _load():
            try:
                crt_data = self.load_from_crt_excel(
                    cfgpn=cfgpn_filter,
                    excel_path=excel_path,
                )
                if self._view:
                    self._view.root.after(
                        0,
                        lambda: self._view.checkout_tab.on_crt_grid_loaded(crt_data)
                    )
            except Exception as e:
                logger.error(f"CheckoutController.load_crt_grid: {e}")
                if self._view:
                    self._view.context.log(f"✗ CRT grid load failed: {e}")

        threading.Thread(target=_load, daemon=True, name="bento-crt-grid").start()

    # ──────────────────────────────────────────────────────────────────────
    # XML GENERATION (standalone — no full checkout run)
    # ──────────────────────────────────────────────────────────────────────

    def generate_xml_only(self, params: dict):
        """
        Generate the SLATE XML file without running the full checkout.
        Called by CheckoutTab._generate_xml_only().
        """
        def _gen():
            try:
                from model.orchestrators.checkout_orchestrator import generate_slate_xml
                xml_path = generate_slate_xml(
                    jira_key   = params["jira_key"],
                    mid        = params["mid"],
                    cfgpn      = params["cfgpn"],
                    fw_ver     = params["fw_ver"],
                    dut_slots  = params["dut_slots"],
                    tgz_path   = params.get("tgz_path", ""),
                    output_dir = params.get("hot_folder", "."),
                    dry_run    = True,
                )
                if self._view:
                    self._view.context.log(f"✓ SLATE XML generated: {xml_path}")
            except Exception as e:
                logger.error(f"CheckoutController.generate_xml_only: {e}")
                if self._view:
                    self._view.context.log(f"✗ XML generation failed: {e}")

        threading.Thread(target=_gen, daemon=True, name="bento-xml-gen").start()

    # ──────────────────────────────────────────────────────────────────────
    # CHECKOUT DISPATCH
    # ──────────────────────────────────────────────────────────────────────

    def start_checkout(self, params: dict):
        """
        Called by CheckoutTab._start_checkout().
        Fans out one checkout thread per selected hostname.

        params keys:
            jira_key, tgz_path, hot_folder, mid, cfgpn, fw_ver,
            dut_slots, detect_method, timeout_seconds, notify_teams,
            hostnames
        """
        if self._running:
            logger.warning("CheckoutController: checkout already in progress — ignoring.")
            return

        hostnames = params.get("hostnames", [])
        targets   = []
        for hostname in hostnames:
            env = self._get_env_for_hostname(hostname)
            if not env:
                logger.error(
                    f"CheckoutController: no ENV for hostname '{hostname}' — skipping."
                )
                continue
            targets.append((hostname, env))

        if not targets:
            logger.error("CheckoutController: no valid targets — aborting.")
            return

        self._running = True
        threading.Thread(
            target=self._checkout_all,
            args=(params, targets),
            daemon=True,
            name="bento-checkout-fanout",
        ).start()
        logger.info(
            f"CheckoutController: dispatched checkout for {len(targets)} tester(s)."
        )

    def _checkout_all(self, params: dict, targets: list):
        """
        Background fanout thread — one _checkout_one() per tester in parallel.
        Mirrors _compile_all() in compile_controller.py exactly.
        """
        def _one(hostname, env):
            self._master.on_checkout_started(hostname)
            result = self._checkout_one(hostname=hostname, env=env, params=params)
            result["hostname"] = hostname
            result["env"]      = env
            self._master.on_checkout_completed(hostname, result)
            return result

        try:
            with ThreadPoolExecutor(max_workers=len(targets)) as pool:
                futures = {pool.submit(_one, h, e): (h, e) for h, e in targets}
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        hostname, env = futures[future]
                        logger.error(
                            f"CheckoutController: thread error [{hostname}]: {e}"
                        )
                        self._master.on_checkout_completed(
                            hostname,
                            {"status": "failed", "detail": str(e), "elapsed": 0}
                        )
        finally:
            self._running = False
            logger.info("CheckoutController: all checkout threads complete.")

    def _checkout_one(self, hostname: str, env: str, params: dict) -> dict:
        """
        Single-tester checkout. Delegates to checkout_orchestrator (Model).
        Returns the result dict from the orchestrator.
        """
        try:
            from model.orchestrators.checkout_orchestrator import run_checkout

            # Resolve Teams webhook and paths from settings.json
            webhook_url    = self._config.get("notifications", {}).get("teams_webhook_url", "")
            slate_log_path = self._config.get("checkout", {}).get("slate_log_path", "")
            result_folder  = self._config.get("checkout", {}).get("output_folder", "")

            log_callback   = self._make_log_callback(hostname)
            phase_callback = self._make_phase_callback(hostname)

            result = run_checkout(
                jira_key        = params["jira_key"],
                hostname        = hostname,
                env             = env,
                tgz_path        = params.get("tgz_path", ""),
                hot_folder      = params["hot_folder"],
                mid             = params.get("mid", ""),
                cfgpn           = params.get("cfgpn", ""),
                fw_ver          = params.get("fw_ver", ""),
                dut_slots       = params.get("dut_slots", 4),
                detect_method   = params.get("detect_method", "AUTO"),
                timeout_seconds = params.get("timeout_seconds", 3600),
                notify_teams    = params.get("notify_teams", True),
                webhook_url     = webhook_url,
                slate_log_path  = slate_log_path,
                result_folder   = result_folder,
                log_callback    = log_callback,
                phase_callback  = phase_callback,
            )
            return result
        except Exception as e:
            logger.error(f"CheckoutController._checkout_one [{hostname}]: {e}")
            return {"status": "failed", "detail": str(e), "elapsed": 0}

    # ──────────────────────────────────────────────────────────────────────
    # HELPERS
    # ──────────────────────────────────────────────────────────────────────

    def _make_log_callback(self, hostname: str):
        """Returns a callable(str) that prefixes log lines with hostname."""
        def _cb(msg: str):
            if self._view:
                self._view.context.log(f"[{hostname}] {msg}")
        return _cb

    def _make_phase_callback(self, hostname: str):
        """
        Returns a callable(str) that relays phase updates to the View badge.
        The orchestrator calls phase_callback("Waiting for SLATE...") etc.
        """
        def _cb(phase: str):
            self._master.on_checkout_progress(hostname, phase)
        return _cb
