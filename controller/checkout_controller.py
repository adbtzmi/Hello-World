#!/usr/bin/env python3
"""
controller/checkout_controller.py
====================================
Checkout Controller — Phase 2

Bridges the CheckoutTab (View) and checkout_orchestrator.py (Model).
Updated to pass new fields: lot_prefix, dut_locations, test_cases.
"""

import os
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger("bento_app")

_REGISTRY_PATH     = r"P:\temp\BENTO\bento_testers.json"
_CRT_EXCEL_DEFAULT = r"\\sifsmodtestrep\modtestrep\crab\crt_from_sap.xlsx"

# Exact CRT column names from crt_excel_template.json [24]
# "Product  Name" has DOUBLE SPACE — intentional
_COL_MATERIAL_DESC  = "Material description"
_COL_CFGPN          = "CFGPN"
_COL_FW_WAVE_ID     = "FW Wave ID"
_COL_FIDB_FW_REV    = "FIDB_ASIC_FW_REV"
_COL_PRODUCT_NAME   = "Product  Name"    # DOUBLE SPACE [24]
_COL_CRT_CUSTOMER   = "CRT Customer"
_COL_SSD_DRIVE_TYPE = "SSD Drive Type"
_COL_ABIT_RELEASE   = "ABIT Release (Yes/No)"
_COL_SFN2_RELEASE   = "SFN2 Release (Yes/No)"


class CheckoutController:
    """
    Bridges CheckoutTab <-> checkout_orchestrator.py.
    """

    def __init__(self, master, config: dict):
        self._master  = master
        self._config  = config
        self._view    = None
        self._running = False
        logger.info("CheckoutController initialised.")

    def set_view(self, view):
        self._view = view

    # ──────────────────────────────────────────────────────────────────────
    # REGISTRY
    # ──────────────────────────────────────────────────────────────────────

    def get_available_testers(self):
        try:
            registry_path = self._config.get("registry_path", _REGISTRY_PATH)
            if not os.path.exists(registry_path):
                logger.warning(f"Tester registry not found: {registry_path}")
                return []
            with open(registry_path, "r") as f:
                data = json.load(f)
            return [(v.get("hostname",""), v.get("env",""))
                    for v in data.values()
                    if v.get("hostname") and v.get("env")]
        except Exception as e:
            logger.error(f"CheckoutController.get_available_testers: {e}")
            return []

    def _get_env_for_hostname(self, hostname: str) -> str:
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

    def is_running(self) -> bool:
        return self._running

    # ──────────────────────────────────────────────────────────────────────
    # CRT EXCEL READER
    # Reads from \\sifsmodtestrep\modtestrep\crab\crt_from_sap.xlsx
    # ──────────────────────────────────────────────────────────────────────

    def load_from_crt_excel(self, cfgpn: str = "", excel_path: str = "") -> dict:
        """
        Read DUT identity from CRT Excel.
        Confirmed path: \\sifsmodtestrep\modtestrep\crab\crt_from_sap.xlsx
        Column names from crt_excel_template.json [24].
        "Product  Name" has DOUBLE SPACE — intentional.
        """
        try:
            import pandas as pd
        except ImportError:
            raise ImportError("pandas required: pip install pandas openpyxl")

        path = (excel_path
                or self._config.get("cat", {}).get("crt_excel_path", "")
                or _CRT_EXCEL_DEFAULT)

        if not os.path.exists(path):
            raise FileNotFoundError(
                f"CRT Excel not found: {path}\n"
                f"Ensure N: drive (\\\\sifsmodtestrep) is mapped.")

        logger.info(f"CheckoutController: reading CRT Excel: {path}")
        df = pd.read_excel(path, engine="openpyxl", dtype=str)

        required = [_COL_MATERIAL_DESC, _COL_CFGPN, _COL_FW_WAVE_ID, _COL_FIDB_FW_REV]
        missing  = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(
                f"CRT Excel missing columns: {missing}\n"
                f"Note: 'Product  Name' requires DOUBLE SPACE.")

        if cfgpn:
            filtered = df[df[_COL_CFGPN].str.strip() == cfgpn.strip()]
            if filtered.empty:
                logger.warning(f"CFGPN '{cfgpn}' not found — returning all rows.")
                filtered = df
        else:
            filtered = df

        def _first(col):
            if col not in filtered.columns:
                return ""
            vals = filtered[col].dropna().str.strip()
            return vals.iloc[0] if not vals.empty else ""

        mids      = filtered[_COL_MATERIAL_DESC].dropna().str.strip().tolist()
        fw_wave   = _first(_COL_FW_WAVE_ID)
        fw_rev    = _first(_COL_FIDB_FW_REV)
        cfgpn_out = _first(_COL_CFGPN)
        product   = _first(_COL_PRODUCT_NAME) or _first("Product Name")
        customer  = _first(_COL_CRT_CUSTOMER)
        drive_type= _first(_COL_SSD_DRIVE_TYPE)

        display_cols = [
            _COL_MATERIAL_DESC, _COL_CFGPN, _COL_FW_WAVE_ID,
            _COL_FIDB_FW_REV, _COL_PRODUCT_NAME, _COL_CRT_CUSTOMER,
            _COL_SSD_DRIVE_TYPE, _COL_ABIT_RELEASE, _COL_SFN2_RELEASE,
        ]
        available = [c for c in display_cols if c in filtered.columns]
        rows      = filtered[available].fillna("").to_dict(orient="records")

        result = {
            "mids": mids, "cfgpn": cfgpn_out,
            "fw_wave_id": fw_wave, "fw_rev": fw_rev,
            "product": product, "customer": customer,
            "drive_type": drive_type,
            "count": len(mids), "rows": rows,
        }
        logger.info(
            f"CRT Excel loaded — {len(mids)} row(s) | "
            f"CFGPN={cfgpn_out} | FW={fw_wave}")
        return result

    # ──────────────────────────────────────────────────────────────────────
    # AUTOFILL
    # ──────────────────────────────────────────────────────────────────────

    def autofill_from_cat_db(self, cfgpn: str):
        def _lookup():
            try:
                crt_data = self.load_from_crt_excel(cfgpn=cfgpn)
                mid    = crt_data["mids"][0] if crt_data["mids"] else ""
                fw_ver = crt_data["fw_wave_id"] or crt_data["fw_rev"]
                logger.info(f"Autofill CFGPN={cfgpn} -> MID={mid} FW={fw_ver}")
                if self._view:
                    self._view.root.after(
                        0, lambda: self._view.checkout_tab.on_autofill_completed(
                            mid, cfgpn, fw_ver))
            except FileNotFoundError as e:
                logger.warning(f"Autofill: {e}")
                if self._view:
                    self._view.context.log(f"⚠ CRT Excel not found: {e}")
            except Exception as e:
                logger.error(f"Autofill error: {e}")
                if self._view:
                    self._view.context.log(f"✗ CRT lookup failed: {e}")

        threading.Thread(target=_lookup, daemon=True, name="bento-crt-lookup").start()

    # ──────────────────────────────────────────────────────────────────────
    # CRT GRID LOAD
    # ──────────────────────────────────────────────────────────────────────

    def load_crt_grid(self, excel_path: str = "", cfgpn_filter: str = ""):
        def _load():
            try:
                crt_data = self.load_from_crt_excel(
                    cfgpn=cfgpn_filter, excel_path=excel_path)
                if self._view:
                    self._view.root.after(
                        0, lambda: self._view.checkout_tab.on_crt_grid_loaded(crt_data))
            except Exception as e:
                logger.error(f"load_crt_grid: {e}")
                if self._view:
                    self._view.context.log(f"✗ CRT grid load failed: {e}")

        threading.Thread(target=_load, daemon=True, name="bento-crt-grid").start()

    # ──────────────────────────────────────────────────────────────────────
    # XML GENERATION ONLY
    # ──────────────────────────────────────────────────────────────────────

    def generate_xml_only(self, params: dict):
        def _gen():
            try:
                from model.orchestrators.checkout_orchestrator import generate_slate_xml
                xml_path = generate_slate_xml(
                    jira_key      = params["jira_key"],
                    mid           = params.get("mid", ""),
                    cfgpn         = params.get("cfgpn", ""),
                    fw_ver        = params.get("fw_ver", ""),
                    dut_slots     = params.get("dut_slots", 4),
                    tgz_path      = params.get("tgz_path", ""),
                    env           = params.get("hostnames", [""])[0],  # first tester env
                    lot_prefix    = params.get("lot_prefix", "JAANTJB"),
                    dut_locations = params.get("dut_locations"),
                    label         = params["test_cases"][0]["label"] if params.get("test_cases") else "",
                    output_dir    = params.get("hot_folder", "."),
                    dry_run       = True,
                )
                if self._view:
                    self._view.context.log(f"✓ SLATE XML generated: {xml_path}")
            except Exception as e:
                logger.error(f"generate_xml_only: {e}")
                if self._view:
                    self._view.context.log(f"✗ XML generation failed: {e}")

        threading.Thread(target=_gen, daemon=True, name="bento-xml-gen").start()

    # ──────────────────────────────────────────────────────────────────────
    # CHECKOUT DISPATCH
    # ──────────────────────────────────────────────────────────────────────

    def start_checkout(self, params: dict):
        if self._running:
            logger.warning("CheckoutController: already running — ignoring.")
            return

        hostnames = params.get("hostnames", [])
        targets   = []
        for hostname in hostnames:
            env = self._get_env_for_hostname(hostname)
            if not env:
                logger.error(f"No ENV for hostname '{hostname}' — skipping.")
                continue
            targets.append((hostname, env))

        if not targets:
            logger.error("No valid targets — aborting.")
            return

        self._running = True
        threading.Thread(
            target=self._checkout_all,
            args=(params, targets),
            daemon=True,
            name="bento-checkout-fanout",
        ).start()
        logger.info(f"CheckoutController: dispatched to {len(targets)} tester(s).")

    def _checkout_all(self, params: dict, targets: list):
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
                        logger.error(f"Thread error [{hostname}]: {e}")
                        self._master.on_checkout_completed(
                            hostname,
                            {"status": "failed", "detail": str(e), "elapsed": 0,
                             "test_cases": []})
        finally:
            self._running = False
            logger.info("CheckoutController: all threads complete.")

    def _checkout_one(self, hostname: str, env: str, params: dict) -> dict:
        try:
            from model.orchestrators.checkout_orchestrator import run_checkout

            webhook_url    = self._config.get("notifications", {}).get("teams_webhook_url", "")
            log_callback   = self._make_log_callback(hostname)
            phase_callback = self._make_phase_callback(hostname)

            result = run_checkout(
                jira_key        = params["jira_key"],
                hostname        = hostname,
                env             = env,
                tgz_path        = params.get("tgz_path", ""),
                hot_folder      = params.get("hot_folder", ""),
                mid             = params.get("mid", ""),
                cfgpn           = params.get("cfgpn", ""),
                fw_ver          = params.get("fw_ver", ""),
                dut_slots       = params.get("dut_slots", 4),
                lot_prefix      = params.get("lot_prefix", "JAANTJB"),
                dut_locations   = params.get("dut_locations"),
                test_cases      = params.get("test_cases"),
                detect_method   = params.get("detect_method", "AUTO"),
                timeout_seconds = params.get("timeout_seconds", 3600),
                notify_teams    = params.get("notify_teams", True),
                webhook_url     = webhook_url,
                log_callback    = log_callback,
                phase_callback  = phase_callback,
            )
            return result
        except Exception as e:
            logger.error(f"_checkout_one [{hostname}]: {e}")
            return {"status": "failed", "detail": str(e), "elapsed": 0,
                    "test_cases": []}

    # ──────────────────────────────────────────────────────────────────────
    # HELPERS
    # ──────────────────────────────────────────────────────────────────────

    def _make_log_callback(self, hostname: str):
        def _cb(msg: str):
            if self._view:
                self._view.context.log(f"[{hostname}] {msg}")
        return _cb

    def _make_phase_callback(self, hostname: str):
        def _cb(phase: str):
            self._master.on_checkout_progress(hostname, phase)
        return _cb
