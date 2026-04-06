# -*- coding: utf-8 -*-
"""
controller/checkout_controller.py
====================================
Checkout Controller - Phase 2

Bridges CheckoutTab (View) <-> checkout_orchestrator.py (Model).

generate_xml_only():
  - Saves XML to XML_OUTPUT (P:\\temp\\BENTO\\XML_OUTPUT)
  - NOT to CHECKOUT_QUEUE (which is monitored by checkout_watcher.py)
  - Engineer inspects XML, then manually copies to CHECKOUT_QUEUE if needed

start_checkout():
  - Saves XML to CHECKOUT_QUEUE via run_checkout()
  - checkout_watcher.py on tester picks it up and copies to playground_queue
"""

import os
import re
import json
import logging
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

logger = logging.getLogger("bento_app")

# ── CONFIRMED PATHS ───────────────────────────────────────────────────────────
# N: = \\sifsmodtestrep\ModTestRep  (confirmed via `net use`)
_REGISTRY_PATH      = r"P:\temp\BENTO\bento_testers.json"
_CRT_EXCEL_DEFAULT  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Documents", "incoming_crt.xlsx")
_DEFAULT_HOT_FOLDER = r"C:\test_program\playground_queue"
_CHECKOUT_QUEUE     = r"P:\temp\BENTO\CHECKOUT_QUEUE"
# ── XML-only output folder (NOT monitored by checkout_watcher) ────────────────
# generate_xml_only() saves here so the watcher does NOT auto-start checkout.
# Engineers can inspect the XML and manually copy to CHECKOUT_QUEUE when ready.
_XML_OUTPUT_FOLDER  = r"P:\temp\BENTO\XML_OUTPUT"

# ── CONFIRMED COLUMN NAMES from crt_excel_template.json ──────────────────────
# "Product  Name" has DOUBLE SPACE - confirmed in CAT.py
_COL_MATERIAL_DESC  = "Material description"
_COL_CFGPN          = "CFGPN"
_COL_FW_WAVE_ID     = "FW Wave ID"
_COL_FIDB_FW_REV    = "FIDB_ASIC_FW_REV"
_COL_PRODUCT_NAME   = "Product  Name"    # DOUBLE SPACE
_COL_CRT_CUSTOMER   = "CRT Customer"
_COL_SSD_DRIVE_TYPE = "SSD Drive Type"
_COL_ABIT_RELEASE   = "ABIT Release (Yes/No)"
_COL_SFN2_RELEASE   = "SFN2 Release (Yes/No)"


class CheckoutController(object):
    """
    Bridges CheckoutTab <-> checkout_orchestrator.py.

    Two modes:
      generate_xml_only() - saves to XML_OUTPUT (P:\\temp\\BENTO\\XML_OUTPUT)
                            NOT to CHECKOUT_QUEUE (avoids auto-checkout)
      start_checkout()    - full flow via CHECKOUT_QUEUE + watcher
    """

    def __init__(self, master, config):
        self._master        = master
        self._config        = config
        self._view: Any     = None
        self._running       = False
        self._cancel_event  = threading.Event()
        logger.info("CheckoutController initialised.")

    def set_view(self, view):
        self._view = view

    # ── TESTER REGISTRY ───────────────────────────────────────────────────────
    def get_available_testers(self):
        """
        Return list of (hostname, env) from bento_testers.json.
        Supports both list format [hostname, env, ...] and dict format.
        """
        try:
            registry_path = self._config.get("registry_path", _REGISTRY_PATH)
            if not os.path.exists(registry_path):
                logger.debug(
                    "DEBUG_REGISTRY_MISSING: " + registry_path
                )
                return []
            with open(registry_path, "r") as f:
                data = json.load(f)
            result = []
            for val in data.values():
                if isinstance(val, list) and len(val) >= 2:
                    result.append((val[0], val[1]))
                elif isinstance(val, dict):
                    h = val.get("hostname", "")
                    e = val.get("env", "")
                    if h and e:
                        result.append((h, e))
            return result
        except Exception as e:
            logger.error(
                "CheckoutController.get_available_testers: " + str(e)
            )
            return []

    def _get_env_for_hostname(self, hostname):
        """Lookup ENV for a given hostname from bento_testers.json."""
        try:
            registry_path = self._config.get("registry_path", _REGISTRY_PATH)
            if not os.path.exists(registry_path):
                return ""
            with open(registry_path, "r") as f:
                data = json.load(f)
            for val in data.values():
                if isinstance(val, list) and len(val) >= 2:
                    if val[0].upper() == hostname.upper():
                        return val[1].upper()
                elif isinstance(val, dict):
                    if val.get("hostname", "").upper() == hostname.upper():
                        return val.get("env", "").upper()
        except Exception as e:
            logger.error(
                "CheckoutController._get_env_for_hostname: " + str(e)
            )
        return ""

    def is_running(self):
        return self._running

    def stop_checkout(self):
        """Signal all running checkout threads to cancel."""
        if self._running:
            logger.warning("CheckoutController: stopping checkout...")
            if self._view:
                self._view.context.log("⚠ Stop signal sent to checkout orchestrator...")
            self._cancel_event.set()

    # ── CRT EXCEL READER ──────────────────────────────────────────────────────
    def load_from_crt_excel(self, cfgpn="", excel_path=""):
        r"""
        Read CRT Excel and return structured data for the grid.
        Confirmed path: ../Documents/incoming_crt.xlsx
        Column names from crt_excel_template.json.
        """
        import pandas as pd

        path = excel_path or self._config.get(
            "cat", {}
        ).get("crt_excel_path", _CRT_EXCEL_DEFAULT)

        if not os.path.exists(path):
            raise FileNotFoundError(
                "CRT Excel not found:\n  " + path + "\n"
                "Ensure ../Documents/incoming_crt.xlsx exists."
            )

        # Mirrors C.A.T. exactly
        df = pd.read_excel(path, engine="openpyxl", dtype=str)

        # Validate critical columns
        missing = [
            c for c in [_COL_CFGPN, _COL_FW_WAVE_ID, _COL_PRODUCT_NAME]
            if c not in df.columns
        ]
        if missing:
            raise ValueError(
                "CRT Excel missing columns: "
                + str(missing)
                + "\nNote: 'Product  Name' requires DOUBLE SPACE."
            )

        # Apply CFGPN filter
        if cfgpn:
            df = df[df[_COL_CFGPN] == str(cfgpn)]

        rows = []
        for _, row in df.iterrows():
            rows.append({
                _COL_MATERIAL_DESC:  str(row.get(_COL_MATERIAL_DESC,  "") or "").strip(),
                _COL_CFGPN:          str(row.get(_COL_CFGPN,          "") or "").strip(),
                _COL_FW_WAVE_ID:     str(row.get(_COL_FW_WAVE_ID,     "") or "").strip(),
                _COL_FIDB_FW_REV:    str(row.get(_COL_FIDB_FW_REV,    "") or "").strip(),
                _COL_PRODUCT_NAME:   str(row.get(_COL_PRODUCT_NAME,   "") or "").strip(),
                _COL_CRT_CUSTOMER:   str(row.get(_COL_CRT_CUSTOMER,   "") or "").strip(),
                _COL_SSD_DRIVE_TYPE: str(row.get(_COL_SSD_DRIVE_TYPE, "") or "").strip(),
                _COL_ABIT_RELEASE:   str(row.get(_COL_ABIT_RELEASE,   "") or "").strip(),
                _COL_SFN2_RELEASE:   str(row.get(_COL_SFN2_RELEASE,   "") or "").strip(),
                # Profile gen columns (may not exist in CRT Excel)
                "Form_Factor":       str(row.get("Form_Factor",       "") or "").strip(),
                "MCTO_#1":           str(row.get("MCTO_#1",           "") or "").strip(),
                "Dummy_Lot":         str(row.get("Dummy_Lot",         "") or "").strip(),
            })

        fw_wave_id = rows[0].get(_COL_FW_WAVE_ID, "") if rows else ""
        fw_rev     = rows[0].get(_COL_FIDB_FW_REV, "") if rows else ""
        mids       = [r[_COL_MATERIAL_DESC] for r in rows
                      if r[_COL_MATERIAL_DESC]]

        return {
            "rows":       rows,
            "count":      len(rows),
            "cfgpn":      cfgpn,
            "fw_wave_id": fw_wave_id,
            "fw_rev":     fw_rev,
            "mids":       mids,
        }

    def load_crt_grid(self, excel_path="", cfgpn_filter=""):
        """Load CRT Excel in background and push to tab grid."""
        def _run():
            try:
                crt_data = self.load_from_crt_excel(
                    cfgpn=cfgpn_filter,
                    excel_path=excel_path,
                )
                if self._view:
                    self._view.root.after(
                        0,
                        lambda: self._view.checkout_tab.on_crt_grid_loaded(
                            crt_data
                        )
                    )
                if self._view:
                    self._view.context.log(
                        "[OK] CRT grid loaded: "
                        + str(crt_data["count"]) + " row(s)"
                    )
            except FileNotFoundError as e:
                if self._view:
                    self._view.context.log(
                        "[FAIL] CRT Excel not found: " + str(e)
                    )
            except ValueError as e:
                if self._view:
                    self._view.context.log(
                        "[FAIL] CRT column mismatch: " + str(e)
                    )
            except Exception as e:
                logger.error("load_crt_grid: " + str(e))
                if self._view:
                    self._view.context.log(
                        "[FAIL] CRT load error: " + str(e)
                    )

        threading.Thread(
            target=_run, daemon=True, name="bento-crt-load"
        ).start()

    def load_crt_for_profile(self, excel_path="", cfgpn_filter=""):
        """
        Load CRT Excel in background and convert to profile generation rows.

        Reads CRT data, extracts Dummy_Lot (from Material description),
        and creates profile table rows with empty editable columns
        (Step, MID, Tester, Primitive, Dut, ATTR_OVERWRITE).

        Mimics the CRT Automation Tools profile generation process.
        """
        def _run():
            try:
                crt_data = self.load_from_crt_excel(
                    cfgpn=cfgpn_filter,
                    excel_path=excel_path,
                )
                # Convert CRT rows to profile generation format
                # Auto-populate: Form_Factor, Material_Desc, CFGPN, MCTO_#1, Dummy_Lot
                # User-editable: Step, MID, Tester, Primitive, Dut, ATTR_OVERWRITE
                profile_rows = []
                for row_dict in crt_data.get("rows", []):
                    material_desc = str(
                        row_dict.get(_COL_MATERIAL_DESC, "")
                    ).strip()
                    cfgpn = str(
                        row_dict.get(_COL_CFGPN, "")
                    ).strip()
                    form_factor = str(
                        row_dict.get("Form_Factor", "")
                    ).strip()
                    mcto = str(
                        row_dict.get("MCTO_#1", "")
                    ).strip()
                    dummy_lot = str(
                        row_dict.get("Dummy_Lot", "")
                    ).strip() or material_desc or "None"
                    profile_rows.append({
                        "Form_Factor": form_factor,
                        "Material_Desc": material_desc,
                        "CFGPN": cfgpn,
                        "MCTO_#1": mcto,
                        "Dummy_Lot": dummy_lot,
                        "Step": "",
                        "MID": "",
                        "Tester": "",
                        "Primitive": "",
                        "Dut": "",
                        "ATTR_OVERWRITE": "",
                    })

                if not profile_rows:
                    profile_rows.append({
                        "Form_Factor": "",
                        "Material_Desc": "",
                        "CFGPN": "",
                        "MCTO_#1": "",
                        "Dummy_Lot": "None",
                        "Step": "",
                        "MID": "",
                        "Tester": "",
                        "Primitive": "",
                        "Dut": "",
                        "ATTR_OVERWRITE": "",
                    })

                if self._view:
                    self._view.root.after(
                        0,
                        lambda: self._view.checkout_tab.on_profile_data_loaded(
                            profile_rows
                        )
                    )
                if self._view:
                    self._view.context.log(
                        "[OK] Profile table loaded: "
                        + str(len(profile_rows)) + " row(s)"
                    )
            except FileNotFoundError as e:
                if self._view:
                    self._view.context.log(
                        "[FAIL] CRT Excel not found: " + str(e)
                    )
            except ValueError as e:
                if self._view:
                    self._view.context.log(
                        "[FAIL] CRT column mismatch: " + str(e)
                    )
            except Exception as e:
                logger.error("load_crt_for_profile: " + str(e))
                if self._view:
                    self._view.context.log(
                        "[FAIL] CRT load error: " + str(e)
                    )

        threading.Thread(
            target=_run, daemon=True, name="bento-crt-profile-load"
        ).start()

    def load_from_xml(self, xml_path: str):
        """
        Parse an existing SLATE Profile XML and push autofill values to the tab.

        Runs in a background thread so the UI stays responsive.
        Calls checkout_tab.on_xml_imported(data) on the main thread when done.
        """
        def _run():
            try:
                from model.orchestrators.checkout_orchestrator import parse_slate_xml
                data = parse_slate_xml(xml_path)
                logger.info(
                    "load_from_xml: parsed "
                    + xml_path
                    + " -> " + str(data)
                )
                if self._view:
                    self._view.root.after(
                        0,
                        lambda: self._view.checkout_tab.on_xml_imported(data)
                    )
            except FileNotFoundError as e:
                logger.warning("load_from_xml: " + str(e))
                if self._view:
                    self._view.context.log("[WARN] XML not found: " + str(e))
            except Exception as e:
                logger.error("load_from_xml: " + str(e))
                if self._view:
                    self._view.context.log(
                        "[FAIL] XML import error: " + str(e)
                    )

        threading.Thread(
            target=_run, daemon=True, name="bento-xml-import"
        ).start()

    def autofill_from_cat_db(self, cfgpn):
        """Auto-fill DUT fields from CRT Excel by CFGPN."""
        def _lookup():
            try:
                crt_data = self.load_from_crt_excel(cfgpn=cfgpn)
                mid    = crt_data["mids"][0] if crt_data["mids"] else ""
                fw_ver = crt_data["fw_wave_id"] or crt_data["fw_rev"]
                logger.info(
                    "Autofill CFGPN=" + cfgpn
                    + " -> MID=" + mid + " FW=" + fw_ver
                )
                if self._view:
                    self._view.root.after(
                        0,
                        lambda: self._view.checkout_tab.on_autofill_completed(
                            mid, cfgpn, fw_ver
                        )
                    )
            except FileNotFoundError as e:
                logger.warning("Autofill: " + str(e))
                if self._view:
                    self._view.context.log(
                        "[WARN] CRT Excel not found: " + str(e)
                    )
            except Exception as e:
                logger.error("Autofill error: " + str(e))
                if self._view:
                    self._view.context.log(
                        "[FAIL] CRT lookup failed: " + str(e)
                    )

        threading.Thread(
            target=_lookup, daemon=True, name="bento-crt-lookup"
        ).start()

    # ── LOT → CFGPN/MCTO LOOKUP (auto-fill table) ────────────────────────────
    def lookup_lot_cfgpn_mcto(self, lot: str, row_idx: int, site: str = ""):
        """Query MAM SOAP for a dummy lot and auto-fill CFGPN/MCTO in the table.

        Runs in a background thread. On success, calls
        ``checkout_tab.on_lot_lookup_completed(row_idx, cfgpn, mcto)``
        on the main thread to update the profile grid.

        Parameters
        ----------
        lot : str
            Dummy lot ID (e.g. ``"JAATQ95001"``).
        row_idx : int
            Row index in the profile table to update.
        site : str, optional
            Force site (auto-detected from lot prefix if empty).
        """
        def _lookup():
            try:
                from model.mam_communicator import query_lot_cfgpn_mcto
                if self._view:
                    self._view.root.after(
                        0,
                        lambda: self._view.context.log(
                            f"Looking up lot '{lot}' → CFGPN/MCTO..."
                        )
                    )
                result = query_lot_cfgpn_mcto(lot, site=site)
                if result.get("success") == "true":
                    cfgpn = result.get("cfgpn", "")
                    mcto = result.get("mcto", "")
                    step = result.get("step", "")
                    logger.info(
                        "Lot lookup OK: lot=%s → CFGPN=%s, MCTO=%s, STEP=%s",
                        lot, cfgpn, mcto, step,
                    )
                    if self._view:
                        self._view.root.after(
                            0,
                            lambda: self._view.checkout_tab.on_lot_lookup_completed(
                                row_idx, cfgpn, mcto
                            )
                        )
                else:
                    err = result.get("error", "unknown")
                    logger.warning("Lot lookup failed: %s", err)
                    if self._view:
                        self._view.root.after(
                            0,
                            lambda: self._view.context.log(
                                f"[WARN] Lot lookup failed for '{lot}': {err}"
                            )
                        )
            except Exception as e:
                logger.error("Lot lookup error: %s", e)
                if self._view:
                    self._view.root.after(
                        0,
                        lambda: self._view.context.log(
                            f"[FAIL] Lot lookup error: {e}"
                        )
                    )

        threading.Thread(
            target=_lookup, daemon=True, name="bento-lot-lookup"
        ).start()

    # ── MID VALIDATION (verify MID → CFGPN/MCTO link) ─────────────────────────
    def verify_mid_link(self, mid: str, row_idx: int,
                        expected_cfgpn: str = "", expected_mcto: str = "",
                        lot_hint: str = ""):
        """Verify that a MID is correctly linked to the expected CFGPN/MCTO.

        Runs in a background thread. On completion, calls
        ``checkout_tab.on_mid_verify_completed(row_idx, result)``
        on the main thread.

        Parameters
        ----------
        mid : str
            Module ID to verify.
        row_idx : int
            Row index in the profile table.
        expected_cfgpn : str
            Expected CFGPN (from lot lookup).
        expected_mcto : str
            Expected MCTO (from lot lookup).
        lot_hint : str
            Lot ID for facility auto-detection.
        """
        def _verify():
            try:
                from model.mam_communicator import verify_mid_lot_link
                if self._view:
                    self._view.root.after(
                        0,
                        lambda: self._view.context.log(
                            f"Verifying MID '{mid}' link to "
                            f"CFGPN={expected_cfgpn}, MCTO={expected_mcto}..."
                        )
                    )
                result = verify_mid_lot_link(
                    mid,
                    expected_cfgpn=expected_cfgpn,
                    expected_mcto=expected_mcto,
                    lot_hint=lot_hint,
                )
                msg = result.get("message", "")
                logger.info("MID verify: %s", msg)
                if self._view:
                    self._view.root.after(
                        0,
                        lambda: self._view.checkout_tab.on_mid_verify_completed(
                            row_idx, result
                        )
                    )
            except Exception as e:
                logger.error("MID verify error: %s", e)
                if self._view:
                    self._view.root.after(
                        0,
                        lambda: self._view.context.log(
                            f"[FAIL] MID verify error: {e}"
                        )
                    )

        threading.Thread(
            target=_verify, daemon=True, name="bento-mid-verify"
        ).start()

    # ── GENERATE XML ONLY ─────────────────────────────────────────────────────
    def generate_xml_only(self, params):
        """
        Generate XML and save to XML_OUTPUT folder (P:\\temp\\BENTO\\XML_OUTPUT).

        WHY NOT CHECKOUT_QUEUE?
          CHECKOUT_QUEUE is monitored by checkout_watcher.py on the tester.
          Saving there would auto-trigger the checkout process.
          XML_OUTPUT is a separate folder for inspection only.

        CORRECT FLOW:
          LOCAL PC -> generates XML
                   -> saves to P:\\temp\\BENTO\\XML_OUTPUT\\ (not monitored)
                   -> Engineer inspects XML there
                   -> If ready, engineer copies XML to CHECKOUT_QUEUE
                   -> checkout_watcher.py on tester picks it up and starts checkout
        """
        def _gen():
            try:
                from model.orchestrators.checkout_orchestrator import (
                    generate_slate_xml,
                    sort_generated_profiles,
                )

                hostnames = params.get("hostnames", [])
                if not hostnames:
                    if self._view:
                        self._view.context.log(
                            "[!] No tester selected for Generate XML Only."
                        )
                    return

                for hostname in hostnames:
                    log_cb = self._make_log_callback(hostname)

                    # Get ENV for this hostname
                    env = self._get_env_for_hostname(hostname)
                    if not env:
                        log_cb(
                            "[!] Cannot find ENV for '"
                            + hostname
                            + "' in bento_testers.json - skipping."
                        )
                        continue

                    # ── Save to XML_OUTPUT subfolder (NOT CHECKOUT_QUEUE) ─
                    # CHECKOUT_QUEUE is monitored by checkout_watcher.py
                    # which would auto-start the checkout process.
                    # XML_OUTPUT is a separate folder for inspection only.
                    # Each "Generate XML" click creates a new subfolder so
                    # all MIDs from one batch are grouped together.
                    jira_key = params.get("jira_key", "TSESSD-XXXX")
                    tgz_base = os.path.basename(
                        params.get("tgz_path", "")
                    )
                    # Extract IBIR key from TGZ filename
                    # e.g. TSESSD-14270_IBIR-0383_ABIT_passing.tgz
                    ibir_match = re.search(r'(IBIR-\d+)', tgz_base, re.IGNORECASE)
                    ibir_key = ibir_match.group(1).upper() if ibir_match else ""

                    # Build subfolder name: IBIR-0383_TSESSD-14270_20260401_151300
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    folder_parts = [
                        p for p in [ibir_key, jira_key, timestamp] if p
                    ]
                    subfolder_name = "_".join(folder_parts)
                    output_dir = os.path.join(
                        _XML_OUTPUT_FOLDER, subfolder_name
                    )

                    # Auto-create XML_OUTPUT subfolder
                    try:
                        os.makedirs(output_dir, exist_ok=True)
                        log_cb(
                            "[OK] XML output folder ready: " + output_dir
                        )
                    except Exception as e:
                        log_cb(
                            "[!] Cannot create XML output folder "
                            + output_dir + ": " + str(e)
                        )
                        continue

                    test_cases = params.get("test_cases", [])
                    label = (
                        test_cases[0].get("label", "passing")
                        if test_cases else "passing"
                    )

                    profile_table = params.get("profile_table", [])

                    # ── Per-MID tracking ──────────────────────────────
                    mid_results = {}

                    # Read generate_tmptravl from checkout config
                    generate_tmptravl = self._config.get("checkout", {}).get("generate_tmptravl", False)

                    if profile_table:
                        # Multi-MID mode: generate XML per MID
                        for row in profile_table:
                            mid = row.get("mid", params.get("mid", ""))
                            step = str(row.get("step", "ABIT")).strip().upper()

                            # Build per-row dut_locations from Primitive + Dut
                            row_primitive = str(row.get("primitive", "")).strip()
                            row_dut       = str(row.get("dut", "")).strip()
                            tester_flag   = "0" if step == "SFN2" else "1"

                            if row_primitive and row_dut:
                                row_dut_locations = [f"{tester_flag},{row_primitive},{row_dut}"]
                                log_cb(f"  DutLocation for {mid}: {row_dut_locations[0]}")
                            else:
                                row_dut_locations = params.get("dut_locations")

                            # Extract non-standard TempTraveler attributes
                            row_attr_overwrites = row.get("attr_overwrites", [])

                            try:
                                xml_path = generate_slate_xml(
                                    jira_key          = params.get("jira_key", "TSESSD-XXXX"),
                                    mid               = mid,
                                    cfgpn             = row.get("cfgpn", params.get("cfgpn", "")),
                                    fw_ver            = params.get("fw_ver", ""),
                                    dut_slots         = params.get("dut_slots", 4),
                                    tgz_path          = params.get("tgz_path", ""),
                                    env               = env,
                                    lot_prefix        = row.get("lot", params.get("lot_prefix", "JAANTJB")),
                                    dut_locations     = row_dut_locations,
                                    label             = label,
                                    hostname          = hostname,
                                    form_factor       = row.get("form_factor", ""),
                                    output_dir        = output_dir,
                                    generate_tmptravl = generate_tmptravl,
                                    recipe_folder     = self._config.get("checkout", {}).get("recipe_folder", ""),
                                    python2_exe       = self._config.get("checkout", {}).get("python2_exe", ""),
                                    site              = params.get("site", self._config.get("checkout", {}).get("mam_site", "")),
                                    autostart         = params.get("autostart", "False"),
                                    attr_overwrites   = row_attr_overwrites,
                                    recipe_override   = params.get("recipe_override", ""),
                                    log_callback      = log_cb,
                                )
                                if xml_path:
                                    mid_results[mid] = {"status": "success", "xml_path": xml_path, "detail": ""}
                                    log_cb(f"  \u2713 MID {mid}: XML generated")
                                else:
                                    mid_results[mid] = {"status": "error", "xml_path": "", "detail": "XML generation returned None"}
                                    log_cb(f"  \u2717 MID {mid}: XML generation failed")
                            except Exception as e:
                                mid_results[mid] = {"status": "error", "xml_path": "", "detail": str(e)}
                                log_cb(f"  \u2717 MID {mid}: {e}")
                    else:
                        # Single-MID mode (legacy)
                        mid = params.get("mid", "")
                        try:
                            xml_path = generate_slate_xml(
                                jira_key          = params.get("jira_key", "TSESSD-XXXX"),
                                mid               = mid,
                                cfgpn             = params.get("cfgpn", ""),
                                fw_ver            = params.get("fw_ver", ""),
                                dut_slots         = params.get("dut_slots", 4),
                                tgz_path          = params.get("tgz_path", ""),
                                env               = env,
                                lot_prefix        = params.get("lot_prefix", "JAANTJB"),
                                dut_locations     = params.get("dut_locations"),
                                label             = label,
                                hostname          = hostname,
                                output_dir        = output_dir,
                                generate_tmptravl = generate_tmptravl,
                                recipe_folder     = self._config.get("checkout", {}).get("recipe_folder", ""),
                                python2_exe       = self._config.get("checkout", {}).get("python2_exe", ""),
                                site              = params.get("site", self._config.get("checkout", {}).get("mam_site", "")),
                                autostart         = params.get("autostart", "False"),
                                recipe_override   = params.get("recipe_override", ""),
                                log_callback      = log_cb,
                            )
                            if xml_path:
                                mid_results[mid or "default"] = {"status": "success", "xml_path": xml_path, "detail": ""}
                            else:
                                mid_results[mid or "default"] = {"status": "error", "xml_path": "", "detail": "XML generation returned None"}
                        except Exception as e:
                            mid_results[mid or "default"] = {"status": "error", "xml_path": "", "detail": str(e)}

                    # ── Report per-MID summary ────────────────────────
                    success_count = sum(1 for r in mid_results.values() if r["status"] == "success")
                    error_count = sum(1 for r in mid_results.values() if r["status"] == "error")
                    log_cb(f"[SUMMARY] {hostname}: {success_count} success, {error_count} errors out of {len(mid_results)} MID(s)")

                    # Sort generated profiles into step/recipe folders
                    step = params.get("step", "")
                    recipe_name = params.get("recipe", "")
                    if step and output_dir:
                        try:
                            sort_summary = sort_generated_profiles(
                                output_dir=output_dir,
                                step=step,
                                recipe=recipe_name,
                                log_callback=log_cb,
                            )
                            if sort_summary:
                                log_cb(f"Profile sorting complete: {sort_summary}")
                        except Exception as e:
                            log_cb(f"Profile sorting failed (non-fatal): {e}")

                    if success_count > 0 and error_count == 0:
                        log_cb(
                            "[OK] XML saved to output folder:\n"
                            "     " + output_dir + "\n"
                            "     To start checkout, copy XML to:\n"
                            "     " + _CHECKOUT_QUEUE
                        )

                    # Notify UI with per-MID results (XML generation only)
                    if self._view:
                        result = {
                            "status": "xml_done" if error_count == 0 else ("xml_partial" if success_count > 0 else "xml_fail"),
                            "hostname": hostname,
                            "env": env,
                            "mid_results": mid_results,
                            "detail": f"{success_count}/{len(mid_results)} MIDs generated (XML only)",
                            "elapsed": 0,
                            "test_cases": [],
                        }
                        self._view.root.after(
                            0,
                            lambda h=hostname, r=result: self._master.on_xml_generation_completed(h, r)
                        )

            except Exception as e:
                import traceback
                logger.error(
                    "generate_xml_only: " + str(e)
                    + "\n" + traceback.format_exc()
                )
                if self._view:
                    self._view.context.log(
                        "[!] XML generation error: " + str(e)
                    )

        threading.Thread(
            target=_gen, daemon=True, name="bento-xml-gen"
        ).start()

    # ── START CHECKOUT (FULL FLOW) ────────────────────────────────────────────
    def start_checkout(self, params):
        """
        Launch full checkout flow for all selected testers.

        Flow:
          1. run_checkout() saves XML to CHECKOUT_QUEUE (P: shared drive)
          2. checkout_watcher.py on tester detects XML in CHECKOUT_QUEUE
          3. Watcher auto-creates playground_queue and copies XML there
          4. SLATE picks up XML -> AutoStart=True -> test begins
          5. Watcher writes .checkout_status = "success"
          6. wait_for_checkout() wakes up -> Teams notification sent
        """
        if self._running:
            logger.warning("CheckoutController: already running - ignoring.")
            return

        hostnames = params.get("hostnames", [])
        targets   = []
        for hostname in hostnames:
            env = self._get_env_for_hostname(hostname)
            if not env:
                logger.error(
                    "No ENV for hostname '" + hostname
                    + "' in bento_testers.json - skipping."
                )
                if self._view:
                    self._view.context.log(
                        "[!] Hostname '" + hostname
                        + "' not found in registry - skipped."
                    )
                continue
            targets.append((hostname, env))

        if not targets:
            logger.error("No valid targets - aborting checkout.")
            if self._view:
                self._view.context.log(
                    "[!] No valid testers found. "
                    "Check bento_testers.json."
                )
            return

        self._running = True
        self._cancel_event.clear()
        threading.Thread(
            target=self._checkout_all,
            args=(params, targets),
            daemon=True,
            name="bento-checkout-fanout",
        ).start()
        logger.info(
            "CheckoutController: dispatched to "
            + str(len(targets)) + " tester(s)."
        )

    def _checkout_all(self, params, targets):
        """Run checkout for all targets in parallel."""
        def _one(hostname, env):
            if self._view:
                self._view.root.after(
                    0,
                    lambda h=hostname: self._master.on_checkout_started(h)
                )
            result = self._checkout_one(
                hostname=hostname, env=env, params=params
            )
            result["hostname"] = hostname
            result["env"]      = env
            if self._view:
                self._view.root.after(
                    0,
                    lambda h=hostname, r=result:
                        self._master.on_checkout_completed(h, r)
                )
            return result

        try:
            with ThreadPoolExecutor(max_workers=len(targets)) as pool:
                futures = {
                    pool.submit(_one, h, e): (h, e)
                    for h, e in targets
                }
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        hostname, env = futures[future]
                        logger.error(
                            "Thread error [" + hostname + "]: " + str(e)
                        )
                        if self._view:
                            self._view.root.after(
                                0,
                                lambda h=hostname, err=e:
                                    self._master.on_checkout_completed(
                                        h,
                                        {
                                            "status":     "failed",
                                            "detail":     str(err),
                                            "elapsed":    0,
                                            "test_cases": []
                                        }
                                    )
                            )
        finally:
            self._running = False
            logger.info("CheckoutController: all threads complete.")

    def _checkout_one(self, hostname, env, params):
        """
        Run full checkout for one tester.

        Iterates profile_table rows (multi-MID mode) just like
        generate_xml_only() does.  Each MID gets its own run_checkout()
        call which generates XML → saves to CHECKOUT_QUEUE → polls for
        watcher completion → memory collection → Teams notification.

        Falls back to single-MID mode when profile_table is empty
        (backward compatibility with legacy callers).
        """
        try:
            from model.orchestrators.checkout_orchestrator import run_checkout

            webhook_url    = params.get("webhook_url", "") or self._config.get(
                "notifications", {}
            ).get("teams_webhook_url", "")
            log_callback   = self._make_log_callback(hostname)
            phase_callback = self._make_phase_callback(hostname)

            # Read generate_tmptravl from checkout config (same as generate_xml_only)
            generate_tmptravl = self._config.get("checkout", {}).get("generate_tmptravl", False)

            profile_table = params.get("profile_table", [])

            # ── Multi-MID mode: iterate profile_table rows ────────────
            if profile_table:
                mid_results = {}
                for row in profile_table:
                    mid   = row.get("mid", params.get("mid", ""))
                    cfgpn = row.get("cfgpn", params.get("cfgpn", ""))
                    lot   = row.get("lot", params.get("lot_prefix", "JAANTJB"))
                    step  = str(row.get("step", "ABIT")).strip().upper()

                    if not mid:
                        log_callback("[SKIP] Empty MID in profile row — skipping")
                        continue

                    # Build per-row dut_locations from Primitive + Dut
                    row_primitive = str(row.get("primitive", "")).strip()
                    row_dut       = str(row.get("dut", "")).strip()
                    tester_flag   = "0" if step == "SFN2" else "1"

                    if row_primitive and row_dut:
                        row_dut_locations = [f"{tester_flag},{row_primitive},{row_dut}"]
                        log_callback(f"  DutLocation for {mid}: {row_dut_locations[0]}")
                    else:
                        row_dut_locations = params.get("dut_locations")

                    # Extract non-standard TempTraveler attributes
                    row_attr_overwrites = row.get("attr_overwrites", [])

                    log_callback(f"[~] Starting checkout for MID={mid} CFGPN={cfgpn}...")

                    try:
                        result = run_checkout(
                            jira_key          = params["jira_key"],
                            hostname          = hostname,
                            env               = env,
                            tgz_path          = params.get("tgz_path", ""),
                            hot_folder        = params.get("hot_folder", ""),
                            mid               = mid,
                            cfgpn             = cfgpn,
                            fw_ver            = params.get("fw_ver", ""),
                            dut_slots         = params.get("dut_slots", 4),
                            lot_prefix        = lot,
                            dut_locations     = row_dut_locations,
                            test_cases        = params.get("test_cases"),
                            detect_method     = params.get("detect_method", "AUTO"),
                            timeout_seconds   = params.get("timeout_seconds", 3600),
                            notify_teams      = params.get("notify_teams", True),
                            webhook_url       = webhook_url,
                            generate_tmptravl = generate_tmptravl,
                            recipe_folder     = self._config.get("checkout", {}).get("recipe_folder", ""),
                            python2_exe       = self._config.get("checkout", {}).get("python2_exe", ""),
                            site              = params.get("site", self._config.get("checkout", {}).get("mam_site", "")),
                            autostart         = params.get("autostart", "False"),
                            attr_overwrites   = row_attr_overwrites,
                            recipe_override   = params.get("recipe_override", ""),
                            log_callback      = log_callback,
                            phase_callback    = phase_callback,
                            cancel_event      = self._cancel_event,
                        )
                        mid_results[mid] = result
                        icon = "✓" if result.get("status") == "success" else "✗"
                        log_callback(f"  {icon} MID {mid}: {result.get('status', 'unknown')}")
                    except Exception as e:
                        mid_results[mid] = {
                            "status": "failed", "detail": str(e),
                            "elapsed": 0, "test_cases": []
                        }
                        log_callback(f"  ✗ MID {mid}: {e}")

                    # Respect cancellation between MIDs
                    if self._cancel_event and self._cancel_event.is_set():
                        log_callback("[!] Checkout cancelled — stopping remaining MIDs")
                        break

                # Aggregate results
                success_count = sum(1 for r in mid_results.values() if r.get("status") == "success")
                error_count   = sum(1 for r in mid_results.values() if r.get("status") != "success")
                log_callback(
                    f"[SUMMARY] {hostname}: {success_count} success, "
                    f"{error_count} errors out of {len(mid_results)} MID(s)"
                )
                return {
                    "status":      "success" if error_count == 0 else (
                        "partial" if success_count > 0 else "failed"
                    ),
                    "detail":      f"{success_count}/{len(mid_results)} MIDs completed",
                    "elapsed":     sum(r.get("elapsed", 0) for r in mid_results.values()),
                    "test_cases":  [],
                    "mid_results": mid_results,
                }

            # ── Single-MID fallback (legacy / no profile_table) ───────
            else:
                result = run_checkout(
                    jira_key          = params["jira_key"],
                    hostname          = hostname,
                    env               = env,
                    tgz_path          = params.get("tgz_path", ""),
                    hot_folder        = params.get("hot_folder", ""),
                    mid               = params.get("mid", ""),
                    cfgpn             = params.get("cfgpn", ""),
                    fw_ver            = params.get("fw_ver", ""),
                    dut_slots         = params.get("dut_slots", 4),
                    lot_prefix        = params.get("lot_prefix", "JAANTJB"),
                    dut_locations     = params.get("dut_locations"),
                    test_cases        = params.get("test_cases"),
                    detect_method     = params.get("detect_method", "AUTO"),
                    timeout_seconds   = params.get("timeout_seconds", 3600),
                    notify_teams      = params.get("notify_teams", True),
                    webhook_url       = webhook_url,
                    generate_tmptravl = generate_tmptravl,
                    recipe_folder     = self._config.get("checkout", {}).get("recipe_folder", ""),
                    python2_exe       = self._config.get("checkout", {}).get("python2_exe", ""),
                    site              = params.get("site", self._config.get("checkout", {}).get("mam_site", "")),
                    autostart         = params.get("autostart", "False"),
                    recipe_override   = params.get("recipe_override", ""),
                    log_callback      = log_callback,
                    phase_callback    = phase_callback,
                    cancel_event      = self._cancel_event,
                )
                return result

        except Exception as e:
            logger.error("_checkout_one [" + hostname + "]: " + str(e))
            return {
                "status":     "failed",
                "detail":     str(e),
                "elapsed":    0,
                "test_cases": []
            }

    # ── CALLBACK HELPERS ──────────────────────────────────────────────────────
    def _make_log_callback(self, hostname):
        def _cb(msg):
            if self._view:
                self._view.root.after(
                    0,
                    lambda m=msg, h=hostname:
                        self._view.context.log("[" + h + "] " + m)
                )
        return _cb

    def _make_phase_callback(self, hostname):
        def _cb(phase):
            if self._view:
                self._view.root.after(
                    0,
                    lambda p=phase:
                        self._master.on_checkout_progress(hostname, p)
                )
        return _cb
    