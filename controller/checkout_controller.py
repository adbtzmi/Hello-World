#!/usr/bin/env python3
"""
controller/checkout_controller.py
====================================
Checkout Controller — Phase 2

Bridges CheckoutTab (View) <-> checkout_orchestrator.py (Model).

KEY FIXES [41]:
  generate_xml_only():
    - AUTO-CREATES C:\\test_program\\playground_queue with os.makedirs
    - Saves XML DIRECTLY to hot_folder (bypass CHECKOUT_QUEUE)
    - Used for local testing on tester machine
    - Gets ENV from bento_testers.json via _get_env_for_hostname()

  start_checkout():
    - Saves XML to CHECKOUT_QUEUE (P: shared drive) via run_checkout()
    - checkout_watcher.py on tester picks it up and copies to playground_queue
    - Dispatches to multiple testers in parallel via ThreadPoolExecutor
"""

import os
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger("bento_app")

# ── CONFIRMED PATHS ───────────────────────────────────────────────────────────
_REGISTRY_PATH     = r"P:\temp\BENTO\bento_testers.json"
_CRT_EXCEL_DEFAULT = r"\\sifsmodtestrep\modtestrep\crab\crt_from_sap.xlsx"
_DEFAULT_HOT_FOLDER = r"C:\test_program\playground_queue"

# ── CONFIRMED COLUMN NAMES — "Product  Name" has DOUBLE SPACE [26] ───────────
_COL_MATERIAL_DESC  = "Material description"
_COL_CFGPN          = "CFGPN"
_COL_FW_WAVE_ID     = "FW Wave ID"
_COL_FIDB_FW_REV    = "FIDB_ASIC_FW_REV"
_COL_PRODUCT_NAME   = "Product  Name"    # ← DOUBLE SPACE [24]
_COL_CRT_CUSTOMER   = "CRT Customer"
_COL_SSD_DRIVE_TYPE = "SSD Drive Type"
_COL_ABIT_RELEASE   = "ABIT Release (Yes/No)"
_COL_SFN2_RELEASE   = "SFN2 Release (Yes/No)"


class CheckoutController:
    """
    Bridges CheckoutTab [40] <-> checkout_orchestrator.py [39].

    Two modes:
      generate_xml_only() — local test, direct to playground_queue
      start_checkout()    — full flow via CHECKOUT_QUEUE + watcher [42]
    """

    def __init__(self, master, config: dict):
        self._master  = master   # BentoApp or view root with on_checkout_* callbacks
        self._config  = config
        self._view    = None
        self._running = False
        logger.info("CheckoutController initialised.")

    def set_view(self, view):
        self._view = view

    # ── TESTER REGISTRY ───────────────────────────────────────────────────────
    def get_available_testers(self) -> list:
        """
        Return list of (hostname, env) from bento_testers.json. [13]
        Supports both old list format and new dict format.
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
                if isinstance(val, list) and len(val) >= 2:
                    result.append((val[0], val[1]))   # [hostname, env, ...]
                elif isinstance(val, dict):
                    h = val.get("hostname", "")
                    e = val.get("env", "")
                    if h and e:
                        result.append((h, e))
            return result
        except Exception as e:
            logger.error(f"CheckoutController.get_available_testers: {e}")
            return []

    def _get_env_for_hostname(self, hostname: str) -> str:
        """Lookup ENV for a given hostname from bento_testers.json. [13]"""
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
            logger.error(f"CheckoutController._get_env_for_hostname: {e}")
        return ""

    def is_running(self) -> bool:
        return self._running

    # ── CRT EXCEL READER ──────────────────────────────────────────────────────
    def load_from_crt_excel(
        self,
        cfgpn:      str = "",
        excel_path: str = "",
    ) -> dict:
        """
        Read CRT Excel and return structured data for the grid.
        Uses confirmed N: drive path [screenshot confirmed].
        Column names from crt_excel_template.json [26].
        """
        import pandas as pd

        path = excel_path or self._config.get(
            "cat", {}
        ).get("crt_excel_path", _CRT_EXCEL_DEFAULT)

        if not os.path.exists(path):
            raise FileNotFoundError(
                f"CRT Excel not found:\n  {path}\n"
                f"Ensure N: drive (\\\\sifsmodtestrep) is mapped."
            )

        # Mirrors C.A.T. exactly [33]
        df = pd.read_excel(path, engine="openpyxl", dtype=str)

        # Validate critical columns
        missing = [c for c in [_COL_CFGPN, _COL_FW_WAVE_ID, _COL_PRODUCT_NAME]
                   if c not in df.columns]
        if missing:
            raise ValueError(
                f"CRT Excel missing columns: {missing}\n"
                f"Note: 'Product  Name' requires DOUBLE SPACE."
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
        mids       = [r[_COL_MATERIAL_DESC] for r in rows if r[_COL_MATERIAL_DESC]]

        return {
            "rows":       rows,
            "count":      len(rows),
            "cfgpn":      cfgpn,
            "fw_wave_id": fw_wave_id,
            "fw_rev":     fw_rev,
            "mids":       mids,
        }

    def load_crt_grid(self, excel_path: str = "", cfgpn_filter: str = ""):
        """Load CRT Excel in background and push to tab grid. [40]"""
        def _run():
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
                if self._view:
                    self._view.context.log(
                        f"✓ CRT grid loaded: {crt_data['count']} row(s)"
                    )
            except FileNotFoundError as e:
                if self._view:
                    self._view.context.log(f"✗ CRT Excel not found: {e}")
            except ValueError as e:
                if self._view:
                    self._view.context.log(f"✗ CRT column mismatch: {e}")
            except Exception as e:
                logger.error(f"load_crt_grid: {e}")
                if self._view:
                    self._view.context.log(f"✗ CRT load error: {e}")

        threading.Thread(
            target=_run, daemon=True, name="bento-crt-load"
        ).start()

    def autofill_from_cat_db(self, cfgpn: str):
        """Auto-fill DUT fields from CRT Excel by CFGPN. [40]"""
        def _lookup():
            try:
                crt_data = self.load_from_crt_excel(cfgpn=cfgpn)
                mid    = crt_data["mids"][0] if crt_data["mids"] else ""
                fw_ver = crt_data["fw_wave_id"] or crt_data["fw_rev"]
                logger.info(f"Autofill CFGPN={cfgpn} -> MID={mid} FW={fw_ver}")
                if self._view:
                    self._view.root.after(
                        0,
                        lambda: self._view.checkout_tab.on_autofill_completed(
                            mid, cfgpn, fw_ver
                        )
                    )
            except FileNotFoundError as e:
                logger.warning(f"Autofill: {e}")
                if self._view:
                    self._view.context.log(f"⚠ CRT Excel not found: {e}")
            except Exception as e:
                logger.error(f"Autofill error: {e}")
                if self._view:
                    self._view.context.log(f"✗ CRT lookup failed: {e}")

        threading.Thread(
            target=_lookup, daemon=True, name="bento-crt-lookup"
        ).start()

    # ── GENERATE XML ONLY ─────────────────────────────────────────────────────
    def generate_xml_only(self, params: dict):
        """
        Generate XML and save DIRECTLY to SLATE hot folder.

        KEY FIX [41]:
          - Auto-creates C:\\test_program\\playground_queue with os.makedirs
          - Saves directly to hot_folder — bypasses CHECKOUT_QUEUE
          - Used for local testing on tester machine, not full checkout flow
          - Gets correct ENV from bento_testers.json for each hostname
        """
        def _gen():
            from model.orchestrators.checkout_orchestrator import (
                generate_slate_xml
            )

            hostnames = params.get("hostnames", [])
            if not hostnames:
                log_cb = self._make_log_callback("LOCAL")
                log_cb("[✗] No tester selected for Generate XML Only.")
                return

            for hostname in hostnames:
                log_cb = self._make_log_callback(hostname)

                # Get ENV for this hostname from registry [13]
                env = self._get_env_for_hostname(hostname)
                if not env:
                    log_cb(
                        f"[✗] Cannot find ENV for '{hostname}' "
                        f"in bento_testers.json — skipping."
                    )
                    continue

                # hot_folder from params or default
                hot_folder = params.get("hot_folder", "").strip()
                if not hot_folder:
                    hot_folder = _DEFAULT_HOT_FOLDER

                # ── AUTO-CREATE playground_queue if missing ────────────
                try:
                    os.makedirs(hot_folder, exist_ok=True)
                    log_cb(f"[✓] Hot folder ready: {hot_folder}")
                except Exception as e:
                    log_cb(f"[✗] Cannot create hot folder {hot_folder}: {e}")
                    continue

                # ── Get label from first test case ────────────────────
                test_cases = params.get("test_cases", [])
                label = test_cases[0].get("label", "passing") if test_cases else "passing"

                # ── Generate XML directly to hot_folder ───────────────
                # NOT to CHECKOUT_QUEUE — this is test/preview mode
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
                    output_dir    = hot_folder,    # ← Direct to playground_queue
                    dry_run       = True,
                    log_callback  = log_cb,
                )

                if xml_path:
                    log_cb(
                        f"[✓] XML saved to: {xml_path}\n"
                        f"    SLATE should auto-pick this up if running.\n"
                        f"    (This bypasses CHECKOUT_QUEUE — test mode only)"
                    )
                else:
                    log_cb(f"[✗] XML generation failed for {hostname}")

        threading.Thread(
            target=_gen, daemon=True, name="bento-xml-gen"
        ).start()

    # ── START CHECKOUT (FULL FLOW) ────────────────────────────────────────────
    def start_checkout(self, params: dict):
        """
        Launch full checkout flow for all selected testers.

        KEY FLOW [39][42]:
          1. run_checkout() saves XML to CHECKOUT_QUEUE (P: shared drive)
          2. checkout_watcher.py on tester detects XML in CHECKOUT_QUEUE
          3. Watcher copies XML → C:\\test_program\\playground_queue
          4. SLATE picks up XML → AutoStart=True → test begins [24]
          5. Watcher writes .checkout_status = "success"
          6. wait_for_checkout() wakes up → Teams notification sent
        """
        if self._running:
            logger.warning("CheckoutController: already running — ignoring.")
            return

        hostnames = params.get("hostnames", [])
        targets   = []
        for hostname in hostnames:
            env = self._get_env_for_hostname(hostname)
            if not env:
                logger.error(
                    f"No ENV for hostname '{hostname}' "
                    f"in bento_testers.json — skipping."
                )
                if self._view:
                    self._view.context.log(
                        f"[✗] Hostname '{hostname}' not found in registry — skipped."
                    )
                continue
            targets.append((hostname, env))

        if not targets:
            logger.error("No valid targets — aborting checkout.")
            if self._view:
                self._view.context.log(
                    "[✗] No valid testers found. Check bento_testers.json."
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
            f"CheckoutController: dispatched to {len(targets)} tester(s)."
        )

    def _checkout_all(self, params: dict, targets: list):
        """Run checkout for all targets in parallel."""
        def _one(hostname, env):
            # Notify view: checkout started
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
            # Notify view: checkout completed
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
                        logger.error(f"Thread error [{hostname}]: {e}")
                        if self._view:
                            self._view.root.after(
                                0,
                                lambda h=hostname, err=e:
                                    self._master.on_checkout_completed(
                                        h,
                                        {"status": "failed",
                                         "detail": str(err),
                                         "elapsed": 0,
                                         "test_cases": []}
                                    )
                            )
        finally:
            self._running = False
            logger.info("CheckoutController: all threads complete.")

    def _checkout_one(self, hostname: str, env: str, params: dict) -> dict:
        """
        Run full checkout for one tester.
        Calls run_checkout() which saves XML to CHECKOUT_QUEUE. [39]
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
            logger.error(f"_checkout_one [{hostname}]: {e}")
            return {
                "status":     "failed",
                "detail":     str(e),
                "elapsed":    0,
                "test_cases": []
            }

    # ── CALLBACK HELPERS ──────────────────────────────────────────────────────
    def _make_log_callback(self, hostname: str):
        def _cb(msg: str):
            if self._view:
                self._view.root.after(
                    0,
                    lambda m=msg: self._view.context.log(
                        f"[{hostname}] {m}"
                    )
                )
        return _cb

    def _make_phase_callback(self, hostname: str):
        def _cb(phase: str):
            if self._view:
                self._view.root.after(
                    0,
                    lambda p=phase:
                        self._master.on_checkout_progress(hostname, p)
                )
        return _cb
    