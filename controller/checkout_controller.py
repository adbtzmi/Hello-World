# -*- coding: utf-8 -*-
"""
controller/checkout_controller.py
====================================
Checkout Controller - Phase 2

Bridges CheckoutTab (View) <-> checkout_orchestrator.py (Model).

generate_xml_only():
  - Saves XML to CHECKOUT_QUEUE (P: shared drive)
  - NOT to C:\\test_program\\playground_queue (tester-only path)
  - BENTO runs on LOCAL PC — cannot write to tester paths directly
  - checkout_watcher.py on tester picks up from CHECKOUT_QUEUE

start_checkout():
  - Saves XML to CHECKOUT_QUEUE via run_checkout()
  - checkout_watcher.py on tester picks it up and copies to playground_queue
"""

import os
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

logger = logging.getLogger("bento_app")

# ── CONFIRMED PATHS ───────────────────────────────────────────────────────────
_REGISTRY_PATH      = r"P:\temp\BENTO\bento_testers.json"
_CRT_EXCEL_DEFAULT  = r"\\sifsmodtestrep\modtestrep\crab\crt_from_sap.xlsx"
_DEFAULT_HOT_FOLDER = r"C:\test_program\playground_queue"
_CHECKOUT_QUEUE     = r"P:\temp\BENTO\CHECKOUT_QUEUE"

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
      generate_xml_only() - saves to CHECKOUT_QUEUE (shared P: drive)
                            NOT to playground_queue (tester-only path)
      start_checkout()    - full flow via CHECKOUT_QUEUE + watcher
    """

    def __init__(self, master, config):
        self._master        = master
        self._config        = config
        self._view: Any     = None
        self._running       = False
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

    # ── CRT EXCEL READER ──────────────────────────────────────────────────────
    def load_from_crt_excel(self, cfgpn="", excel_path=""):
        r"""
        Read CRT Excel and return structured data for the grid.
        Confirmed path: \\sifsmodtestrep\modtestrep\crab\crt_from_sap.xlsx
        Column names from crt_excel_template.json.
        """
        import pandas as pd

        path = excel_path or self._config.get(
            "cat", {}
        ).get("crt_excel_path", _CRT_EXCEL_DEFAULT)

        if not os.path.exists(path):
            raise FileNotFoundError(
                "CRT Excel not found:\n  " + path + "\n"
                "Ensure N: drive (\\\\sifsmodtestrep) is mapped."
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

    # ── GENERATE XML ONLY ─────────────────────────────────────────────────────
    def generate_xml_only(self, params):
        """
        Generate XML and save to CHECKOUT_QUEUE (shared P: drive).

        WHY NOT C:\\test_program\\playground_queue?
          BENTO runs on LOCAL PC.
          C:\\test_program\\playground_queue only exists on the TESTER machine.
          We CANNOT write there directly from the local PC.

        CORRECT FLOW:
          LOCAL PC -> generates XML
                   -> saves to P:\\temp\\BENTO\\CHECKOUT_QUEUE\\ (shared)
                   -> Engineer inspects XML there
                   -> checkout_watcher.py on tester picks it up automatically
                      and copies it to C:\\test_program\\playground_queue
        """
        def _gen():
            try:
                from model.orchestrators.checkout_orchestrator import (
                    generate_slate_xml,
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

                    # ── Save to CHECKOUT_QUEUE (shared P: drive) ──────
                    # NOT to C:\test_program\playground_queue
                    # (that path is on the TESTER machine, not local PC)
                    output_dir = _CHECKOUT_QUEUE

                    # Auto-create CHECKOUT_QUEUE folder
                    try:
                        os.makedirs(output_dir, exist_ok=True)
                        log_cb(
                            "[OK] Queue folder ready: " + output_dir
                        )
                    except Exception as e:
                        log_cb(
                            "[!] Cannot create queue folder "
                            + output_dir + ": " + str(e)
                        )
                        continue

                    test_cases = params.get("test_cases", [])
                    label = (
                        test_cases[0].get("label", "passing")
                        if test_cases else "passing"
                    )

                    xml_path = generate_slate_xml(
                        jira_key      = params.get("jira_key", "TSESSD-XXXX"),
                        mid           = params.get("mid", ""),
                        cfgpn         = params.get("cfgpn", ""),
                        fw_ver        = params.get("fw_ver", ""),
                        dut_slots     = params.get("dut_slots", 4),
                        tgz_path      = params.get("tgz_path", ""),
                        env           = env,
                        lot_prefix    = params.get("lot_prefix", "JAANTJB"),
                        dut_locations = params.get("dut_locations"),
                        label         = label,
                        hostname      = hostname,
                        output_dir    = output_dir,  # P:\temp\BENTO\CHECKOUT_QUEUE
                        log_callback  = log_cb,
                    )

                    if xml_path:
                        log_cb(
                            "[OK] XML saved to shared queue:\n"
                            "     " + xml_path + "\n"
                            "     checkout_watcher.py on tester will\n"
                            "     pick this up and copy to:\n"
                            "     " + _DEFAULT_HOT_FOLDER
                        )
                    else:
                        log_cb(
                            "[!] XML generation failed for " + hostname
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
        Calls run_checkout() which saves XML to CHECKOUT_QUEUE.
        """
        try:
            from model.orchestrators.checkout_orchestrator import run_checkout

            webhook_url    = self._config.get(
                "notifications", {}
            ).get("teams_webhook_url", "")
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
    