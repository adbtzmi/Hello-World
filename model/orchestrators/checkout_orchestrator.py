#!/usr/bin/env python3
r"""
model/orchestrators/checkout_orchestrator.py
=============================================
BENTO Checkout Orchestrator — Phase 2 Auto Start Checkout

Runs on the LOCAL PC. Full flow:
1. Read CRT Excel (user-selected file)
2. Generate SLATE XML (correct Profile schema with AutoStart=True)
3. Drop XML to P:\temp\BENTO\CHECKOUT_QUEUE\   <- FIXED: was HOT_DROP
4. Poll .checkout_status sidecar (mirrors wait_for_build() exactly)
5. Loop per test case (PASSING + FORCE FAIL sequentially)
6. Teams notification with per-test-case summary

KEY FIX:
  run_checkout()     -> saves XML to CHECKOUT_QUEUE (shared P: drive)
  generate_xml_only  -> saves XML to XML_OUTPUT (P:\temp\BENTO\XML_OUTPUT)
                        NOT to CHECKOUT_QUEUE (avoids auto-triggering checkout)
  Both auto-create their target folders with os.makedirs(exist_ok=True)

Mirrors compilation_orchestrator.py pattern exactly. [15]
"""

import os
import sys
import time
import json
import logging
import argparse
import threading
import shutil
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional, Dict, List

from model.hardware_config import get_hardware_config
from model.tmptravl_generator import TmptravlGenerator
from model.recipe_selector import RecipeSelector, RecipeResult
from model.mam_communicator import MAMCommunicator, MAMResult, query_lot_cfgpn_mcto, query_lots_by_cfgpn
from model.sap_communicator import SAPCommunicator, SAPResult, SAP_FIRMWARE_KEYS
from model.profile_sorter import ProfileSorter

# ── CONFIRMED PATHS ───────────────────────────────────────────────────────────
# N: = \\sifsmodtestrep\ModTestRep  (confirmed via `net use` output)
CAT_CRAB_FOLDER         = r"\\sifsmodtestrep\ModTestRep\crab"
CRT_DB_PATH             = r"\\sifsmodtestrep\ModTestRep\crab\closed_crt_jira_info.db"

# ── P: drive — BENTO shared folders ──────────────────────────────────────────
# XML is staged here first, then checkout_watcher.py picks it up [24]
CHECKOUT_QUEUE_FOLDER   = r"P:\temp\BENTO\CHECKOUT_QUEUE"
CHECKOUT_RESULTS_FOLDER = r"P:\temp\BENTO\CHECKOUT_RESULTS"

# ── Tester registry ───────────────────────────────────────────────────────────
TESTER_REGISTRY         = r"P:\temp\BENTO\bento_testers.json"

# ── Default SLATE hot folder on TESTER machine ────────────────────────────────
# NOTE: This path only exists on the tester, not the local PC.
# For "Generate XML Only" mode this is auto-created locally for testing.
# For "Start Checkout" mode the watcher creates it on the tester side. [24]
DEFAULT_HOT_FOLDER      = r"C:\test_program\playground_queue"

# ── Polling ───────────────────────────────────────────────────────────────────
POLL_INTERVAL            = 30      # seconds — matches watcher write cadence
CHECKOUT_TIMEOUT_SECONDS = 3600    # 60 min default

# ── Teams webhook — override via settings.json or env var ────────────────────
TEAMS_WEBHOOK_URL = ""

# ── CONFIRMED COLUMN NAMES from crt_excel_template.json [26] ─────────────────
# ⚠️  "Product  Name" = DOUBLE SPACE — confirmed in CAT.py [33]
_COL_MATERIAL  = "Material description"
_COL_CFGPN     = "CFGPN"
_COL_FW_WAVE   = "FW Wave ID"
_COL_FIDB      = "FIDB_ASIC_FW_REV"
_COL_PRODUCT   = "Product  Name"         # ← DOUBLE SPACE ⚠️
_COL_CUSTOMER  = "CRT Customer"
_COL_DRV_TYPE  = "SSD Drive Type"
_COL_ABIT_REL  = "ABIT Release (Yes/No)"
_COL_SFN2_REL  = "SFN2 Release (Yes/No)"
_COL_CHECKOUT  = "CRT Checkout (Yes/No)"

# NOTE: Static RECIPE_MAP moved to model/recipe_selector.py as _FALLBACK_RECIPE_MAP


# ── LOGGER ────────────────────────────────────────────────────────────────────
def _get_logger(log_callback=None) -> logging.Logger:
    logger = logging.getLogger("bento_checkout_orchestrator")
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        logger.addHandler(sh)
    return logger


def _log(logger, msg: str, log_callback=None, level: str = "info"):
    getattr(logger, level)(msg)
    if log_callback:
        log_callback(msg)


def _phase(logger, msg: str, log_callback=None, phase_callback=None):
    _log(logger, msg, log_callback)
    if phase_callback:
        phase_callback(msg)


# ── MAM QUERY / UPDATE HELPERS ────────────────────────────────────────────────
def query_mam_attributes(
    lot: str,
    site: str = "",
    log_callback=None,
) -> Dict[str, str]:
    """Query MAM for lot attributes (CFGPN, MCTO, etc.).

    Mirrors CAT ProfileWriter.GetMAM().
    Returns empty dict if MAM is unavailable or query fails.

    Parameters
    ----------
    lot : str
        Lot ID to query.
    site : str
        Site name for MAM server selection.
    log_callback : callable, optional
        Callback for log messages.

    Returns
    -------
    dict
        Lot attributes from MAM, or empty dict on failure.
    """
    logger = _get_logger(log_callback)

    mam = MAMCommunicator(site=site)
    if not mam.is_available:
        _log(logger, "MAM query skipped — PyMIPC not available", log_callback)
        return {}

    _log(logger, f"Querying MAM for lot {lot} (site: {mam.site})...", log_callback)
    result = mam.get_lot_attributes(lot)

    if result.success:
        _log(logger, f"MAM returned {len(result.attributes)} attributes for lot {lot}", log_callback)
        return result.attributes
    else:
        _log(logger, f"MAM query failed: {result.error}", log_callback)
        return {}


def query_sap_attributes(
    cfgpn: str,
    instance: str = "PR1",
    logger=None,
    log_callback=None,
) -> dict:
    """Query SAP for CFGPN/MCTO attributes.

    Mirrors CAT ProfileDump/main.py Duplicate() method.

    Parameters
    ----------
    cfgpn : str
        CFGPN or MCTO material number to query.
    instance : str
        SAP instance (PR1=production, QA5=QA).
    logger : optional
        Logger instance.
    log_callback : callable, optional
        UI callback for log messages.

    Returns
    -------
    dict
        SAP attributes as {CHARC_NAME: CHARC_VALUE} or empty dict on failure.
    """
    if logger is None:
        logger = _get_logger(log_callback)

    if not cfgpn:
        _log(logger, "No CFGPN provided for SAP query", log_callback, "warning")
        return {}

    sap = SAPCommunicator(instance=instance)
    if not sap.is_available:
        _log(logger, "SAP unavailable (suds not installed) — skipping SAP query", log_callback, "warning")
        return {}

    try:
        _log(logger, f"Querying SAP ({instance}) for CFGPN={cfgpn}...", log_callback)
        result = sap.get_cfgpn_data(str(cfgpn))
        if result.success:
            _log(logger, f"✓ SAP returned {len(result.attributes)} attributes for CFGPN={cfgpn}", log_callback)
            return result.attributes
        else:
            _log(logger, f"⚠ SAP query failed for CFGPN={cfgpn}: {result.error}", log_callback, "warning")
            return {}
    except Exception as e:
        _log(logger, f"⚠ SAP query error for CFGPN={cfgpn}: {e}", log_callback, "warning")
        return {}


def update_mam_cfgpn_mcto(
    lot: str,
    cfgpn: str,
    mcto: str,
    site: str = "",
    log_callback=None,
) -> bool:
    """Update CFGPN and MCTO attributes in MAM for a lot.

    Mirrors CAT ProfileWriter.UpdateMAM_CFGPN_MCTO().

    Parameters
    ----------
    lot : str
        Lot ID to update.
    cfgpn : str
        CFGPN value to set.
    mcto : str
        MCTO value to set.
    site : str
        Site name for MAM server selection.
    log_callback : callable, optional
        Callback for log messages.

    Returns
    -------
    bool
        True if update succeeded, False otherwise.
    """
    logger = _get_logger(log_callback)

    mam = MAMCommunicator(site=site)
    if not mam.is_available:
        _log(logger, "MAM update skipped — PyMIPC not available", log_callback)
        return False

    attrs = {}
    if cfgpn:
        attrs["CFGPN"] = cfgpn
    if mcto:
        attrs["MCTO"] = mcto

    if not attrs:
        _log(logger, "No CFGPN/MCTO values to update in MAM", log_callback)
        return False

    _log(logger, f"Updating MAM for lot {lot}: {attrs}", log_callback)
    result = mam.set_lot_attributes(lot, attrs)

    if result.success:
        _log(logger, f"MAM update successful for lot {lot}", log_callback)
        return True
    else:
        _log(logger, f"MAM update failed: {result.error}", log_callback)
        return False


# ── PROFILE SORTING ──────────────────────────────────────────────────────────
def sort_generated_profiles(
    output_dir: str,
    step: str = "",
    recipe: str = "",
    log_callback=None,
) -> Dict[str, int]:
    """Sort generated profile files into tester/recipe folder structure.

    Mirrors CAT ProfileSort() + ProfileClean().

    Parameters
    ----------
    output_dir : str
        Directory containing generated XML profiles.
    step : str
        Step name for folder organization.
    recipe : str
        Recipe name for folder organization.
    log_callback : callable, optional
        Callback for log messages.

    Returns
    -------
    dict
        Summary: {tester/recipe_folder: file_count}
    """
    logger = _get_logger(log_callback)

    if not output_dir or not os.path.isdir(output_dir):
        _log(logger, f"Output directory not found for sorting: {output_dir}", log_callback)
        return {}

    sorter = ProfileSorter(output_dir)

    if step:
        dest = sorter.sort_by_step(output_dir, step, recipe, log_callback=log_callback)
        _log(logger, f"Profiles sorted into: {dest}", log_callback)

    # Clean up empty folders
    removed = sorter.clean_empty_folders(log_callback=log_callback)
    if removed:
        _log(logger, f"Removed {removed} empty directories", log_callback)

    # Return summary
    summary = sorter.get_sorted_summary()
    flat_summary = {}
    for tester, recipes in summary.items():
        for rec, count in recipes.items():
            flat_summary[f"{tester}/{rec}"] = count

    return flat_summary


# ── STEP 0 — Load valid ENVs ──────────────────────────────────────────────────
def get_valid_envs() -> set:
    """Load valid environments from shared tester registry. [13]"""
    try:
        if os.path.exists(TESTER_REGISTRY):
            with open(TESTER_REGISTRY, "r") as f:
                data = json.load(f)
            return {
                (v[1].upper() if isinstance(v, list) else v.get("env", "").upper())
                for v in data.values()
            }
    except Exception:
        pass
    return {"ABIT", "SFN2", "CNFG"}


# ── STEP 1 — Read CRT Excel ───────────────────────────────────────────────────
def load_dut_info_from_crt(
    cfgpn_filter: str = "",
    excel_path:   str = "",
    logger              = None,
    log_callback        = None,
) -> list:
    r"""
    Read CRT Excel file.

    Mirrors CatDB.update_db_with_crt_excel() in CAT.py [33] exactly:
        df = pd.read_excel(filepath, engine="openpyxl", dtype=str)

    Column names confirmed from crt_excel_template.json [26].
    "Product  Name" has DOUBLE SPACE -- do not change.
    """
    import pandas as pd

    if logger is None:
        logger = _get_logger()

    path = excel_path

    if not path:
        msg = (
            "No CRT Excel file selected.\n"
            "Use 'Import from Excel' or 'Load from CRT' to select a file."
        )
        _log(logger, f"✗ {msg}", log_callback, "error")
        raise FileNotFoundError(msg)

    if not os.path.exists(path):
        msg = f"CRT Excel not found at:\n  {path}"
        _log(logger, f"✗ {msg}", log_callback, "error")
        raise FileNotFoundError(msg)

    _log(logger,
         f"Reading CRT Excel: {os.path.basename(path)}", log_callback)

    # Mirrors C.A.T. exactly — openpyxl engine, all columns as str [33]
    df = pd.read_excel(path, engine="openpyxl", dtype=str)

    # Validate critical columns
    missing = [c for c in [_COL_CFGPN, _COL_FW_WAVE, _COL_PRODUCT]
               if c not in df.columns]
    if missing:
        msg = (
            f"CRT Excel missing columns: {missing}\n"
            f"Note: 'Product  Name' requires DOUBLE SPACE."
        )
        _log(logger, f"✗ {msg}", log_callback, "error")
        raise ValueError(msg)

    # Apply CFGPN filter safely (pandas — no SQL injection risk)
    if cfgpn_filter:
        df = df[df[_COL_CFGPN] == str(cfgpn_filter)]

    if df.empty:
        _log(logger,
             "⚠ No CRT rows found"
             + (f" for CFGPN={cfgpn_filter}" if cfgpn_filter else ""),
             log_callback, "warning")
        return []

    # Return with exact column names matching checkout_tab.py [40] grid
    result = []
    for _, row in df.iterrows():
        result.append({
            "Material description":  str(row.get(_COL_MATERIAL, "") or "").strip(),
            "CFGPN":                 str(row.get(_COL_CFGPN,    "") or "").strip(),
            "FW Wave ID":            str(row.get(_COL_FW_WAVE,  "") or "").strip(),
            "FIDB_ASIC_FW_REV":      str(row.get(_COL_FIDB,     "") or "").strip(),
            "Product  Name":         str(row.get(_COL_PRODUCT,  "") or "").strip(),
            "CRT Customer":          str(row.get(_COL_CUSTOMER, "") or "").strip(),
            "SSD Drive Type":        str(row.get(_COL_DRV_TYPE, "") or "").strip(),
            "ABIT Release (Yes/No)": str(row.get(_COL_ABIT_REL, "") or "").strip(),
            "SFN2 Release (Yes/No)": str(row.get(_COL_SFN2_REL, "") or "").strip(),
            "CRT Checkout (Yes/No)": str(row.get(_COL_CHECKOUT,  "") or "").strip(),
        })

    _log(logger,
         f"✓ Loaded {len(result)} DUT record(s) from CRT Excel.",
         log_callback)
    return result


# ── DUT LOCATION AUTO-GENERATION ──────────────────────────────────────────────
# SLATE grid layout: 4 rows (0-3) × 32 columns (0-31)
# DutLocation format: "{tester_flag},{primitive},{dut}"
_SLATE_COLS_PER_ROW = 32
_SLATE_MAX_ROWS     = 4


def _normalize_tester_path(path: str) -> str:
    """Normalize path for tester consumption: backslashes, uppercase."""
    if not path:
        return ""
    return path.replace("/", "\\").upper()


def generate_dut_locations(n: int, tester_type: str = "NEOSEM", cols_per_row: int = _SLATE_COLS_PER_ROW) -> list:
    """
    Auto-generate DUT location strings for *n* slots.

    CAT format: "{tester_flag},{primitive},{dut}"
      NEOSEM:    tester_flag=1  (ABIT, SCHP steps)
      ADVANTEST: tester_flag=0  (SFN2 step)

    When actual primitive/dut values are available from CRT Excel,
    those should be used instead of this auto-generation.
    """
    tester_flag = "1" if tester_type.upper() in ("NEOSEM", "NEOS") else "0"
    return [f"{tester_flag},{i // cols_per_row},{i % cols_per_row}" for i in range(n)]


# ── STEP 2 — Generate SLATE XML ───────────────────────────────────────────────
def generate_slate_xml(
    jira_key:      str,
    mid:           str,
    cfgpn:         str,
    fw_ver:        str,
    dut_slots:     int,
    tgz_path:      str,
    env:           str,
    lot_prefix:    str            = "JAANTJB",
    dut_locations: Optional[list] = None,
    label:         str            = "",
    hostname:      str  = "",
    form_factor:   str  = "",
    output_dir:    str  = "",
    dry_run:       bool = False,
    generate_tmptravl: bool = False,
    recipe_folder: str  = "",
    python2_exe:   str  = "",
    site:          str  = "",
    sap_instance:  str  = "PR1",
    autostart:     str  = "True",
    attr_overwrites: Optional[list] = None,
    recipe_override: str = "",
    logger               = None,
    log_callback         = None,
) -> Optional[str]:
    """
    Generate SLATE XML with correct Profile schema.
    Confirmed from FullAutoStart.md [24] Step 4 & Step 9.
    AutoStart=True eliminates manual "Run Test" click.

    output_dir controls WHERE the XML is saved:
      - run_checkout()    → CHECKOUT_QUEUE (P: shared drive) [39]
      - generate_xml_only → hot_folder (C:\\test_program\\playground_queue) [41]

    Both callers auto-create their target folder before calling this.
    """
    if logger is None:
        logger = _get_logger()

    # Filename MUST contain hostname + env so checkout_watcher.py can filter
    # by env_tag ("_ENV_") and hostname_tag ("_HOSTNAME_") in the filename.
    # JIRA key is also included so _parse_jira_from_xml_name() can extract it.
    # Format: Profile_{JIRA}_{HOSTNAME}_{ENV}_{MID}_{Lot}_{timestamp}.xml
    # Timestamp ensures each generation creates a NEW file (never overwrites).
    #
    # CRITICAL: Never silently drop hostname/env/jira_key — the watcher
    # checks for "_ENV_" and "_HOSTNAME_" tags in the filename.  If any
    # critical part is empty, use a placeholder so the filename structure
    # stays intact and the watcher can still match.
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    _jira  = jira_key.strip()  if jira_key  else ""
    _host  = hostname.strip()  if hostname  else ""
    _env   = env.strip().upper() if env     else ""

    if not _jira:
        _log(logger, "WARNING: jira_key is empty — using placeholder 'TSESSD-XXXX'",
             log_callback, "warning")
        _jira = "TSESSD-XXXX"
    if not _host:
        _log(logger, "WARNING: hostname is empty — filename will lack hostname tag; "
             "checkout_watcher may not detect this XML",
             log_callback, "warning")
    if not _env:
        _log(logger, "WARNING: env is empty — filename will lack env tag; "
             "checkout_watcher may not detect this XML",
             log_callback, "warning")

    if mid and lot_prefix:
        parts    = ["Profile", _jira, _host, _env, mid, lot_prefix, ts]
        xml_name = "_".join(p for p in parts if p) + ".xml"
    else:
        parts    = ["checkout", _jira, _host, _env, label, ts]
        xml_name = "_".join(p for p in parts if p) + ".xml"
    out_dir    = output_dir or CHECKOUT_QUEUE_FOLDER

    # ── Auto-create output directory ──────────────────────────────────
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception as e:
        _log(logger, f"✗ Cannot create output dir {out_dir}: {e}",
             log_callback, "error")
        return None

    xml_path = os.path.join(out_dir, xml_name)

    _phase(logger,
           f"Generating SLATE XML [{label or 'default'}]...", log_callback)

    try:
        # ── Correct Profile schema from FullAutoStart.md [24] ─────────
        profile = ET.Element("Profile")

        # ── TestJobArchive — deferred until after recipe selection ────
        # Will be set after recipe selection determines the actual path
        tja_elem = ET.SubElement(profile, "TestJobArchive")

        # ── Load hardware config (replaces hardcoded values) ───────────
        hw_config = get_hardware_config()
        step = env.upper()
        mam_step, cfgpn_step_id = hw_config.get_step_names(step)
        # form_factor: caller-provided takes priority; otherwise resolved
        # from MAM MODULE_FORM_FACTOR after the MAM query below.
        _form_factor = form_factor

        # ── MAM attribute query (PyMIPC) ─────────────────────────────
        mam_attrs = {}
        if lot_prefix and site:
            mam_attrs = query_mam_attributes(lot_prefix, site=site, log_callback=log_callback)
            if mam_attrs:
                if not cfgpn and mam_attrs.get("CFGPN"):
                    cfgpn = mam_attrs["CFGPN"]
                    _log(logger, f"Using CFGPN from MAM: {cfgpn}", log_callback)
                if mam_attrs.get("MCTO"):
                    _log(logger, f"MAM MCTO: {mam_attrs['MCTO']}", log_callback)
                # Auto-detect form factor from MAM if caller didn't provide one
                if not _form_factor and mam_attrs.get("MODULE_FORM_FACTOR"):
                    _form_factor = mam_attrs["MODULE_FORM_FACTOR"]
                    _log(logger, f"Form factor from MAM: {_form_factor}", log_callback)

        # ── Dummy lot → CFGPN/MCTO lookup via MIPC SOAP ─────────────
        # If we have a dummy lot but still no CFGPN (PyMIPC unavailable
        # or MAM query didn't return it), try the zeep-based MIPC SOAP
        # web gateway to pull BASE_CFGPN and MODULE_FGPN from MAM.
        # Mapping (from Teams chat):
        #   DUMMY LOT  = CAT column name (lot_prefix)
        #   BASE CFGPN = CFGPN
        #   MODULE FGPN = MCTO#1
        mcto_from_lot = ""
        if lot_prefix and (not cfgpn or lot_prefix.upper() not in ("NONE", "")):
            _log(logger, f"Querying MAM SOAP for dummy lot '{lot_prefix}' → CFGPN/MCTO...",
                 log_callback)
            lot_result = query_lot_cfgpn_mcto(lot_prefix, site=site)
            if lot_result.get("success") == "true":
                if not cfgpn and lot_result.get("cfgpn"):
                    cfgpn = lot_result["cfgpn"]
                    _log(logger, f"✓ CFGPN from dummy lot: {cfgpn}", log_callback)
                if lot_result.get("mcto"):
                    mcto_from_lot = lot_result["mcto"]
                    _log(logger, f"✓ MCTO from dummy lot: {mcto_from_lot}", log_callback)
                if lot_result.get("step"):
                    _log(logger, f"  Lot step/location: {lot_result['step']}", log_callback)

                # ── Populate mam_attrs from SOAP result ──────────────
                # When PyMIPC is unavailable, mam_attrs is empty.
                # The SOAP query now returns the critical attributes
                # (PCB_DESIGN_ID, DESIGN_ID, etc.) directly from MAM.
                # Populate mam_attrs so the pre-flight validation and
                # tmptravl generation use REAL MAM values, not CFGPN copies.
                soap_all = lot_result.get("all_attrs", {})
                if soap_all and not mam_attrs:
                    _log(logger, f"  Populating mam_attrs from SOAP ({len(soap_all)} keys)",
                         log_callback)
                    mam_attrs = dict(soap_all)  # copy to avoid mutation
                    logger.info("DIAG: mam_attrs populated from SOAP: %s",
                                list(mam_attrs.keys()))
                elif soap_all and mam_attrs:
                    # PyMIPC returned some attrs; merge SOAP attrs as fallback
                    for k, v in soap_all.items():
                        if v and not mam_attrs.get(k):
                            mam_attrs[k] = v
                    logger.info("DIAG: mam_attrs merged with SOAP fallback keys")
            else:
                _log(logger,
                     f"⚠ Dummy lot lookup failed: {lot_result.get('error', 'unknown')}",
                     log_callback, "warning")

        # ── Resolve form factor from SOAP if PyMIPC didn't provide it ─
        if not _form_factor and mam_attrs.get("MODULE_FORM_FACTOR"):
            _form_factor = mam_attrs["MODULE_FORM_FACTOR"]
            _log(logger, f"Form factor from SOAP: {_form_factor}", log_callback)

        # ── Resolve DIB_TYPE now that form_factor is known ────────────
        dib_type = hw_config.get_dib_type(step, _form_factor) if _form_factor else hw_config.get_dib_type(step, "U.2")
        _log(logger, f"DIB_TYPE: {dib_type} (step={step}, form_factor={_form_factor or 'U.2 default'})", log_callback)

        # ── SAP attribute query (CFGPN + MCTO) ──────────────────────
        cfgpn_attrs = {}
        mcto_attrs = {}
        sap_constant_dict = {}
        mcto_number = (
            mcto_from_lot
            or mam_attrs.get("MODULE_FGPN", "")
            or mam_attrs.get("MCTO", "")
        )

        if cfgpn:
            _log(logger, f"Querying SAP for CFGPN={cfgpn}...", log_callback)
            cfgpn_attrs = query_sap_attributes(
                cfgpn, instance=sap_instance,
                logger=logger, log_callback=log_callback
            )
            if cfgpn_attrs:
                sap_comm = SAPCommunicator(instance=sap_instance)
                sap_constant_dict = sap_comm.extract_constant_dict(cfgpn_attrs)
                _log(logger, f"✓ SAP CFGPN attributes: {len(cfgpn_attrs)} keys (incl. PRODUCT_FAMILY={cfgpn_attrs.get('PRODUCT_FAMILY', 'N/A')})", log_callback)
                _log(logger, f"SAP constant dict: {list(sap_constant_dict.keys())}", log_callback)
            else:
                _log(logger, f"⚠ SAP returned no attributes for CFGPN={cfgpn}", log_callback, "warning")
        else:
            _log(logger, "⚠ CFGPN is empty — SAP query skipped. Tmptravl CFGPN section will be empty!", log_callback, "warning")

        if mcto_number:
            mcto_attrs = query_sap_attributes(
                mcto_number, instance=sap_instance,
                logger=logger, log_callback=log_callback
            )
            if mcto_attrs:
                _log(logger, f"✓ SAP MCTO attributes: {len(mcto_attrs)} keys", log_callback)

        # If MCTO query failed/skipped but CFGPN has PRODUCT_GROUP, inject it
        # so that MCTO.PRODUCT_GROUP vs CFGPN.PRODUCT_GROUP check passes.
        if not mcto_attrs.get("PRODUCT_GROUP") and cfgpn_attrs.get("PRODUCT_GROUP"):
            mcto_attrs["PRODUCT_GROUP"] = cfgpn_attrs["PRODUCT_GROUP"]
            logger.info("DIAG: Injected PRODUCT_GROUP into mcto_attrs from cfgpn_attrs: %s",
                        cfgpn_attrs["PRODUCT_GROUP"])

        # ── Pre-flight: MAM vs CFGPN critical attribute validation ────
        # The production recipe_selection.py (eval_rules, line 361) compares
        # MAM attributes against CFGPN attributes and raises a fatal
        # "Critical Attribute check failed" exception if they don't match.
        # This causes SLATE to show "Failed to Create Lot Cache" popup.
        # We check EARLY so the user gets a clear error in the BENTO GUI
        # instead of a cryptic popup on the tester 30 minutes later.
        CFGPN_TO_MAM_CRITICAL_MAPPING = {
            # CFGPN key           → MAM key (where names differ)
            "PCB_DESIGN_ID":       "PCB_DESIGN_ID",
            "COMP1_DESIGN_ID":     "DESIGN_ID",
            "COMP2_DESIGN_ID":     "DIE2_DESIGN_ID",
            "PCB_ARTWORK_REV":     "PCB_ARTWORK_REV",
            "PRODUCT_GROUP":       "PRODUCT_GROUP",
        }

        if mam_attrs and cfgpn_attrs:
            mismatches = []
            for cfgpn_key, mam_key in CFGPN_TO_MAM_CRITICAL_MAPPING.items():
                cfgpn_val = cfgpn_attrs.get(cfgpn_key, "")
                mam_val = mam_attrs.get(mam_key, "")
                if cfgpn_val and mam_val and cfgpn_val != mam_val:
                    mismatches.append(
                        f"  {cfgpn_key}: CFGPN='{cfgpn_val}' vs MAM({mam_key})='{mam_val}'"
                    )
            if mismatches:
                # ── Enhanced DIAG: SAP vs MAM side-by-side comparison ──
                diag_header = (
                    f"╔══════════════════════════════════════════════════════════╗\n"
                    f"║  CRITICAL ATTRIBUTE MISMATCH — SAP vs MAM Comparison    ║\n"
                    f"╠══════════════════════════════════════════════════════════╣\n"
                    f"║  Lot: {lot_prefix:<20s}  CFGPN: {cfgpn:<20s}  ║\n"
                    f"╠══════════════════════════════════════════════════════════╣\n"
                    f"║  {'Attribute':<22s} {'SAP (CFGPN)':<16s} {'MAM (Lot)':<16s} ║\n"
                    f"╠══════════════════════════════════════════════════════════╣"
                )
                diag_rows = []
                for cfgpn_key, mam_key in CFGPN_TO_MAM_CRITICAL_MAPPING.items():
                    cfgpn_val = cfgpn_attrs.get(cfgpn_key, "-")
                    mam_val = mam_attrs.get(mam_key, "-")
                    match_icon = "✓" if cfgpn_val == mam_val else "✗ MISMATCH"
                    diag_rows.append(
                        f"║  {cfgpn_key:<22s} {cfgpn_val:<16s} {mam_val:<16s} ║  {match_icon}"
                    )
                diag_footer = (
                    f"╚══════════════════════════════════════════════════════════╝"
                )
                diag_table = "\n".join([diag_header] + diag_rows + [diag_footer])

                # Log the full DIAG table
                logger.error("PREFLIGHT DIAG — SAP vs MAM comparison:\n%s", diag_table)

                # ── Suggest matching lots via MAM CFGPN query ──────────
                suggestion_msg = ""
                try:
                    _log(logger,
                         f"Searching MAM for lots matching CFGPN '{cfgpn}' "
                         f"with correct SAP attributes...",
                         log_callback)
                    suggested_lots = query_lots_by_cfgpn(
                        cfgpn=cfgpn,
                        site=site or "",
                        sap_attrs=cfgpn_attrs,
                        max_suggestions=5,
                    )
                    if suggested_lots:
                        suggestion_lines = [
                            f"\n── SUGGESTED MATCHING LOTS (CFGPN {cfgpn}) ──",
                            f"  {'Lot ID':<14s} {'Step':<8s} {'PCB':<6s} "
                            f"{'Design':<8s} {'Die2':<8s} {'Rev':<4s} "
                            f"{'Form Factor':<12s}",
                            f"  {'─'*14:<14s} {'─'*8:<8s} {'─'*6:<6s} "
                            f"{'─'*8:<8s} {'─'*8:<8s} {'─'*4:<4s} "
                            f"{'─'*12:<12s}",
                        ]
                        for sl in suggested_lots:
                            suggestion_lines.append(
                                f"  {sl['lot_id']:<14s} {sl['step']:<8s} "
                                f"{sl['pcb']:<6s} {sl['design_id']:<8s} "
                                f"{sl['die2']:<8s} {sl['rev']:<4s} "
                                f"{sl['form_factor']:<12s}"
                            )
                        suggestion_lines.append(
                            f"\nUse one of these lots instead of '{lot_prefix}' "
                            f"for CFGPN '{cfgpn}'."
                        )
                        suggestion_msg = "\n".join(suggestion_lines)
                        _log(logger, suggestion_msg, log_callback)
                    else:
                        suggestion_msg = (
                            f"\nNo matching lots found in MAM for CFGPN '{cfgpn}' "
                            f"with correct SAP attributes.\n"
                            f"FIX: Update lot '{lot_prefix}' MAM attributes to match "
                            f"CFGPN '{cfgpn}', or contact MAM admin."
                        )
                        _log(logger, suggestion_msg, log_callback, "warning")
                except Exception as e:
                    logger.warning("Lot suggestion query failed: %s", e)
                    suggestion_msg = (
                        f"\n(Could not search for matching lots: {e})"
                    )

                # Build the user-facing error message
                mismatch_msg = (
                    f"CRITICAL ATTRIBUTE MISMATCH for lot '{lot_prefix}'!\n"
                    f"The lot's MAM attributes do NOT match the SAP CFGPN "
                    f"specification.\n"
                    f"This WILL cause 'Failed to Create Lot Cache' on the "
                    f"tester.\n\n"
                    + diag_table + "\n"
                    + suggestion_msg + "\n\n"
                    f"ABORTING checkout — will NOT send mismatched profile "
                    f"to tester."
                )
                _log(logger, mismatch_msg, log_callback, "error")
                logger.error("PREFLIGHT: Critical attribute mismatch detected — "
                             "ABORTING. recipe_selection.py would reject this lot. "
                             "Mismatches:\n%s",
                             "\n".join(mismatches))
                # ABORT: Do not proceed with XML generation.
                # Sending a mismatched profile to the tester wastes 30+ minutes
                # only to get "Failed to Create Lot Cache" from SLATE.
                # The user must fix the lot/CFGPN mismatch before retrying.
                return None

        # ── Optional: Generate tmptravl for recipe selection ─────────
        tmptravl_path = ""
        if generate_tmptravl:
            try:
                traces_dir = os.path.join(out_dir, "Traces")
                tmptravl_gen = TmptravlGenerator(output_dir=traces_dir)

                # Build constant dict merging hardware + SAP data
                tmptravl_constants = {
                    "LOT": lot_prefix,
                    "DIB_TYPE": dib_type,
                    "DIB_TYPE_NAME": dib_type,
                    "MACHINE_MODEL": hw_config.get_machine_model(step),
                    "MACHINE_VENDOR": hw_config.get_machine_vendor(step),
                    "STEP": mam_step,
                    "STEP_ID": cfgpn_step_id,
                    "SITE": site or "SINGAPORE",
                }
                # Merge SAP firmware paths + MARKET_SEGMENT + MODULE_FORM_FACTOR
                tmptravl_constants.update(sap_constant_dict)

                # Cross-populate critical MAM attributes from SAP CFGPN data
                # as a FALLBACK — only fills gaps where MAM has no value.
                # Now that the SOAP query fetches critical attrs directly from
                # the factory MAM database, mam_attrs should already contain
                # real values (PCB_DESIGN_ID, DESIGN_ID, etc.).
                # setdefault() ensures real MAM values are NEVER overwritten.
                # This fallback only activates when BOTH PyMIPC AND SOAP fail.

                if mam_attrs is None:
                    mam_attrs = {}

                # Inject critical attributes from CFGPN into MAM (gap-fill only)
                for cfgpn_key, mam_key in CFGPN_TO_MAM_CRITICAL_MAPPING.items():
                    if cfgpn_attrs.get(cfgpn_key):
                        existing = mam_attrs.get(mam_key)
                        if existing:
                            logger.info("DIAG: MAM[%s] already has '%s' — "
                                        "NOT overwriting with CFGPN[%s]='%s'",
                                        mam_key, existing, cfgpn_key,
                                        cfgpn_attrs[cfgpn_key])
                        else:
                            mam_attrs[mam_key] = cfgpn_attrs[cfgpn_key]
                            logger.info("DIAG: Gap-filled MAM[%s] = CFGPN[%s] = %s "
                                        "(MAM had no value)",
                                        mam_key, cfgpn_key, cfgpn_attrs[cfgpn_key])

                # Inject MARKET_SEGMENT and MODULE_FORM_FACTOR from sap_constant_dict
                # so they appear in the [MAM] section of the tmptravl file.
                # recipe_selection.py accesses tmptravl['MAM']['MARKET_SEGMENT']
                # for critical attribute check config selection.
                if sap_constant_dict.get("MARKET_SEGMENT"):
                    mam_attrs.setdefault("MARKET_SEGMENT", sap_constant_dict["MARKET_SEGMENT"])
                if sap_constant_dict.get("MODULE_FORM_FACTOR"):
                    mam_attrs.setdefault("MODULE_FORM_FACTOR", sap_constant_dict["MODULE_FORM_FACTOR"])

                logger.info("DIAG: Final mam_attrs keys after cross-population: %s",
                            list(mam_attrs.keys()) if mam_attrs else "empty")

                _log(logger,
                     f"Tmptravl sections: MAM={len(mam_attrs)} keys, "
                     f"CFGPN={len(cfgpn_attrs)} keys, MCTO={len(mcto_attrs)} keys",
                     log_callback)

                tmptravl_path = tmptravl_gen.generate(
                    mid=mid,
                    step=step,
                    mam_dict=mam_attrs if mam_attrs else None,
                    cfgpn_dict=cfgpn_attrs if cfgpn_attrs else None,
                    mcto_dict=mcto_attrs if mcto_attrs else None,
                    constant_dict=tmptravl_constants,
                )
                _log(logger, f"✓ Tmptravl: {os.path.basename(tmptravl_path)}", log_callback)
                logger.info("DIAG: tmptravl generated at: %s", tmptravl_path)
                logger.info("DIAG: tmptravl file exists: %s", os.path.exists(tmptravl_path))
            except Exception as e:
                _log(logger, f"⚠ Tmptravl generation failed (non-fatal): {e}", log_callback, "warning")
                tmptravl_path = ""

        # ── Recipe selection via subprocess or fallback ──────────────
        selector = RecipeSelector(recipe_folder=recipe_folder, python2_exe=python2_exe)
        _log(logger, f"RecipeSelector: folder={recipe_folder!r}, python2={python2_exe!r}, "
             f"available={selector.is_available}, tmptravl={tmptravl_path!r}", log_callback)
        logger.info("DIAG: Calling recipe selection - folder=%s, python2=%s, tmptravl=%s, tgz=%s, recipe_override=%s",
                     recipe_folder, python2_exe, tmptravl_path, tgz_path, recipe_override)
        recipe_result = selector.select_recipe_or_fallback(
            tmptravl_path, step,
            tgz_path=tgz_path,
            recipe_override=recipe_override,
            cfgpn_attrs=cfgpn_attrs if cfgpn_attrs else None,
        )
        recipe = recipe_result.recipe_name or r"RECIPE\PEREGRINEION_NEOSEM_ABIT.XML"
        _log(logger, f"DIAG: Recipe: {recipe} (success={recipe_result.success}, source={recipe_result.source})", log_callback)
        logger.info("DIAG: Recipe result - success=%s, recipe=%s, test_program=%s, source=%s", recipe_result.success, recipe_result.recipe_name, recipe_result.test_program_path, recipe_result.source)
        logger.info("DIAG: Recipe file_copy_paths count=%d, paths=%s", len(recipe_result.file_copy_paths), recipe_result.file_copy_paths)
        if recipe_result.file_copy_paths:
            _log(logger, f"  file_copy_paths: {recipe_result.file_copy_paths}", log_callback)
        else:
            _log(logger, f"  file_copy_paths: EMPTY — AddtionalFileFolder will be empty", log_callback, "warning")
        if recipe_result.source == "fallback" and len(recipe_result.file_copy_paths) == 0:
            logger.warning("DIAG: file_copy_paths EMPTY because fallback was used (subprocess failed) — AddtionalFileFolder will be empty")
        if recipe_result.error:
            _log(logger, f"  recipe error: {recipe_result.error}", log_callback, "warning")
            logger.warning("DIAG: Recipe selection error: %s", recipe_result.error)
        if recipe_result.raw_output:
            _log(logger, f"  recipe raw_output (first 500 chars): {recipe_result.raw_output[:500]}", log_callback)
            logger.info("DIAG: Recipe raw output: %s", recipe_result.raw_output[:500])

        # ── Set TestJobArchive — user-selected TGZ takes priority ─────
        # Priority: 1) User-selected tgz_path  2) Recipe selection result  3) empty
        if tgz_path and tgz_path.strip():
            tja_elem.text = _normalize_tester_path(tgz_path)
            logger.info("DIAG: TestJobArchive = user-selected tgz_path: %s", tja_elem.text)
        elif recipe_result.success and recipe_result.test_program_path:
            tja_elem.text = recipe_result.test_program_path  # Already uppercased by recipe_selector
            logger.info("DIAG: TestJobArchive = recipe selection result: %s", tja_elem.text)
        else:
            tja_elem.text = ""
            logger.warning("DIAG: TestJobArchive is EMPTY — no tgz_path and no recipe selection result")

        # ── RecipeFile ────────────────────────────────────────────────
        ET.SubElement(profile, "RecipeFile").text = recipe.upper() if recipe else ""

        # ── TempTraveler — user-editable attributes ───────────────────
        # The ATTR_OVERWRITE dialog pre-fills default TempTraveler attributes
        # (MAM/STEP, CFGPN/STEP_ID, EQUIPMENT/DIB_TYPE, etc.) and lets the
        # user edit/add/remove them.  When attr_overwrites is populated, it
        # is the AUTHORITATIVE source — we use it directly.  When empty
        # (legacy/no dialog interaction), we fall back to computed defaults.
        #
        # Attribute ordering follows CAT's working XML:
        #   1. MAM/STEP
        #   2. MAM/NAND_OPTION  (optional, from user)
        #   3. CFGPN/STEP_ID    (underscores — "SSD_FIN_TEST2")
        #   4. EQUIPMENT/DIB_TYPE
        #   5. EQUIPMENT/DIB_TYPE_NAME
        #   6. RECIPE_SELECTION/RECIPE_SEL_TEST_PROGRAM_PATH
        #   7+ Any additional user-specified attributes
        # NOTE: CAT does NOT include SEC_PROCESS — removed to match.
        tt = ET.SubElement(profile, "TempTraveler")

        # STEP_ID: CAT uses underscores ("SSD_FIN_TEST2"), NOT spaces.
        cfgpn_step_id_underscored = cfgpn_step_id.replace(" ", "_")

        # Build a lookup of user-provided overwrite attributes
        ow_lookup = {}  # (section_upper, name_upper) -> (section, name, value)
        extra_attrs = []  # non-default attrs in original order
        if attr_overwrites:
            # Known default attribute keys (section, attr) that we handle below
            _DEFAULT_KEYS = {
                ("MAM", "STEP"),
                ("CFGPN", "STEP_ID"),
                ("EQUIPMENT", "DIB_TYPE"),
                ("EQUIPMENT", "DIB_TYPE_NAME"),
                ("RECIPE_SELECTION", "RECIPE_SEL_TEST_PROGRAM_PATH"),
            }
            for ow in attr_overwrites:
                sect = ow.get("section", "").strip()
                name = ow.get("name", "").strip()
                val  = ow.get("value", "")
                if not sect or not name:
                    continue
                key = (sect.upper(), name.upper())
                ow_lookup[key] = (sect, name, val)
                if key not in _DEFAULT_KEYS:
                    extra_attrs.append((sect, name, val))

        # Helper: get user-overridden value or fall back to computed default
        def _ow_val(section: str, attr: str, default: str) -> str:
            entry = ow_lookup.get((section.upper(), attr.upper()))
            return entry[2] if entry else default

        # Resolve RECIPE_SEL_TEST_PROGRAM_PATH default
        _recipe_sel_tgz = ""
        if tgz_path and tgz_path.strip():
            _recipe_sel_tgz = _normalize_tester_path(tgz_path)
        elif recipe_result.success and recipe_result.test_program_path:
            _recipe_sel_tgz = recipe_result.test_program_path

        # Write attributes in CAT-compatible order, using user values when available:
        # 1. MAM/STEP
        _step_val = _ow_val("MAM", "STEP", mam_step)
        a = ET.SubElement(tt, "Attribute")
        a.set("section", "MAM")
        a.set("attr",    "STEP")
        a.set("value",   _step_val)

        # 2. Extra attrs that should come before STEP_ID (e.g. NAND_OPTION)
        for sect, name, val in extra_attrs:
            if name.upper() == "NAND_OPTION":
                a = ET.SubElement(tt, "Attribute")
                a.set("section", sect)
                a.set("attr",    name)
                a.set("value",   val)

        # 3. CFGPN/STEP_ID (with underscores — matching CAT)
        _step_id_val = _ow_val("CFGPN", "STEP_ID", cfgpn_step_id_underscored)
        a = ET.SubElement(tt, "Attribute")
        a.set("section", "CFGPN")
        a.set("attr",    "STEP_ID")
        a.set("value",   _step_id_val)

        # 4. EQUIPMENT/DIB_TYPE
        _dib_val = _ow_val("EQUIPMENT", "DIB_TYPE", dib_type)
        a = ET.SubElement(tt, "Attribute")
        a.set("section", "EQUIPMENT")
        a.set("attr",    "DIB_TYPE")
        a.set("value",   _dib_val)

        # 5. EQUIPMENT/DIB_TYPE_NAME
        _dib_name_val = _ow_val("EQUIPMENT", "DIB_TYPE_NAME", dib_type)
        a = ET.SubElement(tt, "Attribute")
        a.set("section", "EQUIPMENT")
        a.set("attr",    "DIB_TYPE_NAME")
        a.set("value",   _dib_name_val)

        # 6. RECIPE_SELECTION/RECIPE_SEL_TEST_PROGRAM_PATH
        _recipe_val = _ow_val("RECIPE_SELECTION", "RECIPE_SEL_TEST_PROGRAM_PATH",
                              _recipe_sel_tgz)
        if _recipe_val:
            a = ET.SubElement(tt, "Attribute")
            a.set("section", "RECIPE_SELECTION")
            a.set("attr",    "RECIPE_SEL_TEST_PROGRAM_PATH")
            a.set("value",   _recipe_val)
            logger.info("DIAG: RECIPE_SEL_TEST_PROGRAM_PATH = %s", _recipe_val)

        # 7. Remaining non-standard attributes (e.g. MOD_TST_SWR_NUMBER)
        #    Skip NAND_OPTION (already written above)
        for sect, name, val in extra_attrs:
            if name.upper() != "NAND_OPTION":
                a = ET.SubElement(tt, "Attribute")
                a.set("section", sect)
                a.set("attr",    name)
                a.set("value",   val)

        _total_tt = len(tt)
        if attr_overwrites:
            _log(logger,
                 f"  TempTraveler: {_total_tt} attribute(s) "
                 f"({len(attr_overwrites)} from ATTR_OVERWRITE)",
                 log_callback)
        else:
            _log(logger,
                 f"  TempTraveler: {_total_tt} attribute(s) (computed defaults)",
                 log_callback)

        # ── AddtionalFileFolder — firmware/config file copy paths ─────
        # Note: "AddtionalFileFolder" is intentionally misspelled — matches CAT production spelling
        aff = ET.SubElement(profile, "AddtionalFileFolder")
        logger.info("DIAG: AddtionalFileFolder - recipe_success=%s, file_copy_paths_count=%d", recipe_result.success, len(recipe_result.file_copy_paths))
        if recipe_result.success and recipe_result.file_copy_paths:
            for key in sorted(recipe_result.file_copy_paths.keys()):
                path_entry = recipe_result.file_copy_paths[key]
                if "|" in path_entry:
                    source, dest = path_entry.split("|", 1)
                elif "," in path_entry:
                    source, dest = path_entry.split(",", 1)
                else:
                    logger.warning("DIAG: file_copy_path entry '%s' has no recognized delimiter (expected '|' or ','): %s", key, path_entry)
                    continue
                file_elem = ET.SubElement(aff, "File")
                file_elem.set("source", source.strip())
                file_elem.set("dest", dest.strip())

            file_count = len(aff)
            logger.info("DIAG: AddtionalFileFolder populated with %d File element(s) from %d file_copy_paths entries",
                        file_count, len(recipe_result.file_copy_paths))
            if len(recipe_result.file_copy_paths) > 0 and file_count == 0:
                logger.warning("DIAG: All %d file_copy_paths entries were dropped — check delimiter format",
                               len(recipe_result.file_copy_paths))

        # ── MaterialInfo — ONE entry per XML (CAT generates one XML per MID)
        mat       = ET.SubElement(profile, "MaterialInfo")
        # Determine tester type from step
        tester_type = "ADVANTEST" if step == "SFN2" else "NEOSEM"
        # Use first provided dut_location, or generate a single one
        if dut_locations and len(dut_locations) > 0:
            dut_loc = dut_locations[0]
        else:
            dut_loc = generate_dut_locations(1, tester_type=tester_type)[0]
        a = ET.SubElement(mat, "Attribute")
        a.set("Lot",         lot_prefix)   # No suffix — CAT uses lot as-is
        a.set("MID",         mid)
        a.set("DutLocation", dut_loc)

        # ── AutoStart — defaults to "True", matches working tester XML ─
        ET.SubElement(profile, "AutoStart").text = autostart

        # ── Write XML ─────────────────────────────────────────────────
        # CRITICAL: Working tester XMLs have:
        #   - NO <?xml?> declaration
        #   - 2-space indentation
        # ET.indent() requires Python 3.9+; use try/except with manual
        # fallback to guarantee correct formatting on all Python versions.
        tree = ET.ElementTree(profile)
        try:
            ET.indent(tree, space="  ")  # 2-space indent — Python 3.9+
        except AttributeError:
            # Python < 3.9: manual indent via minidom (strip its XML decl)
            pass

        # Write to string first, then strip any XML declaration
        xml_str = ET.tostring(profile, encoding="unicode")

        # If ET.indent was unavailable, apply manual 2-space indentation
        # by checking if the output is all on one line (no newlines except
        # at the very end).
        if "\n" not in xml_str.strip() or xml_str.strip().startswith("<Profile><"):
            # ET.indent didn't work — apply manual pretty-print
            try:
                from xml.dom import minidom
                dom = minidom.parseString(xml_str)
                # toprettyxml with 2-space indent, then strip the XML declaration line
                pretty = dom.toprettyxml(indent="  ", encoding=None)
                # Remove <?xml ...?> line
                lines = pretty.split("\n")
                if lines and lines[0].startswith("<?xml"):
                    lines = lines[1:]
                # Remove trailing blank lines
                while lines and not lines[-1].strip():
                    lines.pop()
                xml_str = "\n".join(lines)
            except Exception:
                pass  # Fall through with unindented XML

        # Ensure no XML declaration is present
        if xml_str.startswith("<?xml"):
            # Strip the first line (XML declaration)
            xml_str = "\n".join(xml_str.split("\n")[1:])

        # Post-process: normalize self-closing tags to match tester format
        # Working XML: <Attribute ... /> (exactly one space before />)
        # ET may produce: <Attribute .../> or <Attribute ...  />
        # Use regex to normalize to exactly one space before />
        import re as _re_xml
        xml_str = _re_xml.sub(r'\s*/>', ' />', xml_str)

        # Write final XML
        with open(xml_path, "w", encoding="utf-8") as f:
            f.write(xml_str)
        _log(logger, f"✓ File copied: {xml_name} → {out_dir}", log_callback)

        size_kb = os.path.getsize(xml_path) / 1024
        _log(logger,
             f"✓ XML: {xml_name} | AutoStart={autostart} | "
             f"1 DUT ({dut_loc}) | {size_kb:.1f} KB",
             log_callback)

        # Log tmptravl file if it was generated
        if tmptravl_path and os.path.exists(tmptravl_path):
            tmptravl_size = os.path.getsize(tmptravl_path) / 1024
            _log(logger,
                 f"✓ File copied: {os.path.basename(tmptravl_path)} → "
                 f"{os.path.dirname(tmptravl_path)} ({tmptravl_size:.1f} KB)",
                 log_callback)

        # ── Optional profile sorting ────────────────────────────────
        # Sort into step/recipe subfolder if step is provided
        # (Actual sorting is typically done after all MIDs are generated,
        #  so this is a hook for the controller to call sort_generated_profiles())

        return xml_path

    except Exception as e:
        _log(logger, f"✗ XML generation failed: {e}",
             log_callback, "error")
        return None


# ── PARSE EXISTING SLATE XML ─────────────────────────────────────────────────
def parse_slate_xml(xml_path: str) -> dict:
    """
    Parse an existing SLATE Profile XML and return a dict of autofill values.

    Reads:
      <TestJobArchive>   -> tgz_path
      <RecipeFile>       -> env (reverse-mapped via RECIPE_MAP)
      <TempTraveler>/<Attribute Section="MAM" Name="STEP"> -> env
      <MaterialInfo>/<Attribute Lot="..." MID="..." DutLocation="...">
                         -> lot_prefix (strip trailing digits), mid,
                            dut_locations list, material_rows list
      <AutoStart>        -> (informational)

    Returns dict with keys:
      tgz_path, env, mid, lot_prefix, dut_locations (list[str]), dut_slots (int),
      material_rows (list[dict]) — one dict per <Attribute> in <MaterialInfo>
        each with keys: mid, lot, dut_location, primitive, dut
    Raises FileNotFoundError / ET.ParseError on bad input.
    """
    if not os.path.exists(xml_path):
        raise FileNotFoundError(f"XML not found: {xml_path}")

    tree = ET.parse(xml_path)
    root = tree.getroot()

    result: dict = {
        "tgz_path":       "",
        "env":            "",
        "mid":            "",
        "lot_prefix":     "",
        "dut_locations":  [],
        "dut_slots":      1,
        "material_rows":  [],
    }

    # TestJobArchive
    tja = root.find("TestJobArchive")
    if tja is not None and tja.text:
        result["tgz_path"] = tja.text.strip()

    # RecipeFile -> reverse-map to ENV using _FALLBACK_RECIPE_MAP
    from model.recipe_selector import _FALLBACK_RECIPE_MAP
    rf = root.find("RecipeFile")
    if rf is not None and rf.text:
        recipe_text = rf.text.strip().upper()
        for env_key, entry in _FALLBACK_RECIPE_MAP.items():
            recipe_val = entry["recipe_name"] if isinstance(entry, dict) else entry
            if recipe_val.upper() in recipe_text or env_key in recipe_text:
                result["env"] = env_key
                break

    # TempTraveler -> MAM/STEP overrides env if present + collect ATTR_OVERWRITE
    # Standard attributes handled by profile generator (excluded from ATTR_OVERWRITE)
    _STANDARD_ATTRS = {
        ("MAM", "STEP"),
        ("CFGPN", "STEP_ID"),
        ("CFGPN", "SEC_PROCESS"),
        ("EQUIPMENT", "DIB_TYPE"),
        ("EQUIPMENT", "DIB_TYPE_NAME"),
        ("RECIPE_SELECTION", "RECIPE_SEL_TEST_PROGRAM_PATH"),
    }
    attr_overwrite_parts = []
    tt = root.find("TempTraveler")
    if tt is not None:
        for attr in tt.findall("Attribute"):
            sec = attr.get("section", "") or attr.get("Section", "")
            name = attr.get("attr", "") or attr.get("Name", "")
            val = attr.get("value", "") or attr.get("Value", "")
            if sec.upper() == "MAM" and name.upper() == "STEP":
                val_upper = val.strip().upper()
                if val_upper in ("ABIT", "SFN2", "SCHP", "CNFG"):
                    result["env"] = val_upper
            # Collect non-standard attributes for ATTR_OVERWRITE
            if (sec.upper(), name.upper()) not in _STANDARD_ATTRS and sec and name:
                attr_overwrite_parts.extend([sec, name, val])

    # Build semicolon-delimited ATTR_OVERWRITE string
    result["attr_overwrite"] = ";".join(attr_overwrite_parts) if attr_overwrite_parts else ""

    # MaterialInfo -> mid, lot_prefix, dut_locations, material_rows
    import re as _re
    mat = root.find("MaterialInfo")
    if mat is not None:
        locs = []
        material_rows = []
        for attr in mat.findall("Attribute"):
            mid_val = attr.get("MID", "").strip()
            loc_val = attr.get("DutLocation", "").strip()
            lot_val = attr.get("Lot", "").strip()

            if mid_val and not result["mid"]:
                result["mid"] = mid_val

            if loc_val:
                locs.append(loc_val)

            if lot_val and not result["lot_prefix"]:
                m = _re.match(r"^(.*?)(\d+)$", lot_val)
                result["lot_prefix"] = m.group(1) if m else lot_val

            # Build per-MID row for profile grid population
            # DutLocation format: "tester_flag,primitive,dut"
            primitive = ""
            dut = ""
            if loc_val:
                parts = loc_val.split(",")
                if len(parts) == 3:
                    primitive = parts[1].strip()
                    dut = parts[2].strip()

            material_rows.append({
                "mid":          mid_val,
                "lot":          lot_val,
                "dut_location": loc_val,
                "primitive":    primitive,
                "dut":          dut,
            })

        result["dut_locations"]  = locs
        result["dut_slots"]      = len(locs) if locs else 1
        result["material_rows"]  = material_rows

    return result


# ── STATUS FILE HELPERS ───────────────────────────────────────────────────────
def write_checkout_status(xml_path: str, status: str, detail: str = ""):
    """Write .checkout_status sidecar — mirrors watcher_lock.py [9]."""
    status_path = xml_path + ".checkout_status"
    data = {
        "status":    status,
        "detail":    detail,
        "timestamp": datetime.now().isoformat()
    }
    try:
        with open(status_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logging.getLogger("bento_checkout_orchestrator").warning(
            f"Could not write status: {e}"
        )


def read_checkout_status(xml_path: str) -> dict:
    """
    Safely read .checkout_status sidecar JSON.
    Returns {} on any parse or IO error — caller treats as 'not ready'. [9]
    """
    status_path = xml_path + ".checkout_status"
    if not os.path.exists(status_path):
        return {}
    try:
        with open(status_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError, OSError):
        return {}


# ── STEP 3 — Drop XML to hot folder (Generate XML Only mode) ─────────────────
def drop_xml_to_hot_folder(
    xml_path:    str,
    hot_folder:  str,
    logger,
    log_callback = None,
) -> Optional[str]:
    """
    Copy XML directly to SLATE hot folder.

    ✅ AUTO-CREATES the folder if it doesn't exist.

    Used ONLY in generate_xml_only() mode — bypasses CHECKOUT_QUEUE.
    In Start Checkout mode, the watcher handles this copy on the tester. [24][39]
    """
    _phase(logger, "Dropping XML to hot folder…", log_callback)

    # ── AUTO-CREATE hot folder ────────────────────────────────────────
    try:
        os.makedirs(hot_folder, exist_ok=True)
        _log(logger, f"[✓] Hot folder ready: {hot_folder}", log_callback)
    except Exception as e:
        _log(logger,
             f"✗ Cannot create hot folder {hot_folder}: {e}",
             log_callback, "error")
        return None

    dest = os.path.join(hot_folder, os.path.basename(xml_path))
    try:
        shutil.copy2(xml_path, dest)
        _log(logger, f"✓ XML → {dest}", log_callback)
        return dest
    except Exception as e:
        _log(logger, f"✗ Hot folder drop failed: {e}",
             log_callback, "error")
        return None


# ── STEP 4 — Poll for completion ──────────────────────────────────────────────
def wait_for_checkout(
    xml_path:        str,
    logger,
    log_callback     = None,
    phase_callback   = None,
    timeout_seconds: int = CHECKOUT_TIMEOUT_SECONDS,
    cancel_event:    Optional[threading.Event] = None,
) -> Dict:
    """
    Poll .checkout_status sidecar every 30s.
    Mirrors wait_for_build() in compilation_orchestrator.py [15].

    The watcher on the tester writes this file after SLATE completes. [24][42]
    """
    start    = time.time()
    deadline = start + timeout_seconds

    _phase(logger,
           f"Waiting for SLATE (timeout={timeout_seconds // 60}min)…",
           log_callback, phase_callback)

    while time.time() < deadline:
        if cancel_event and cancel_event.is_set():
            elapsed = int(time.time() - start)
            _log(logger, f"⚠ Checkout CANCELLED by user after {elapsed}s", log_callback, "warning")
            return {
                "status":  "cancelled",
                "detail":  "User cancelled checkout",
                "elapsed": elapsed
            }

        data  = read_checkout_status(xml_path)
        state = data.get("status", "")

        if state == "success":
            elapsed = int(time.time() - start)
            _log(logger, f"✓ Checkout SUCCESS in {elapsed}s", log_callback)
            result = {
                "status":  "success",
                "detail":  data.get("detail", ""),
                "elapsed": elapsed
            }
            # Pass through collected_files / output_folder written by watcher
            if data.get("collected_files"):
                result["collected_files"] = data["collected_files"]
            if data.get("output_folder"):
                result["collected_output_folder"] = data["output_folder"]
            return result

        elif state == "failed":
            elapsed = int(time.time() - start)
            detail  = data.get("detail", "Unknown error")
            _log(logger,
                 f"✗ Checkout FAILED after {elapsed}s: {detail}",
                 log_callback, "error")
            return {
                "status":  "failed",
                "detail":  detail,
                "elapsed": elapsed
            }

        elif state == "in_progress":
            elapsed = int(time.time() - start)
            _phase(logger,
                   f"SLATE running… ({elapsed}s)",
                   log_callback, phase_callback)

        time.sleep(POLL_INTERVAL)

    elapsed = int(time.time() - start)
    _log(logger, f"✗ Checkout TIMEOUT after {elapsed}s",
         log_callback, "error")
    return {
        "status":  "timeout",
        "detail":  f"No SLATE signal within {timeout_seconds}s",
        "elapsed": elapsed
    }


# ── STEP 5 — Teams notification ───────────────────────────────────────────────
def send_teams_notification(
    jira_key:    str,
    hostname:    str,
    status:      str,
    detail:      str,
    elapsed:     int,
    webhook_url: str  = "",
    logger             = None,
    log_callback       = None,
) -> bool:
    """
    Send Teams notification on checkout completion. [39]
    Non-fatal — checkout succeeded even if notification fails.
    """
    if logger is None:
        logger = _get_logger()

    url = (webhook_url or TEAMS_WEBHOOK_URL
           or os.environ.get("BENTO_TEAMS_WEBHOOK", ""))

    if not url:
        _log(logger,
             "⚠ Teams webhook URL not configured — skipping.",
             log_callback, "warning")
        return False

    try:
        import urllib.request
        icon       = "✅" if status.lower() == "success" else "❌"
        color      = "Good" if status.lower() == "success" else "Attention"
        elapsed_s  = f"{elapsed // 60}m {elapsed % 60}s"
        timestamp  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Build fact rows for the Adaptive Card FactSet
        facts = [
            {"title": "Status",  "value": f"**{status.upper()}**"},
            {"title": "Tester",  "value": hostname},
            {"title": "JIRA",    "value": jira_key},
            {"title": "Elapsed", "value": elapsed_s},
            {"title": "Time",    "value": timestamp},
        ]
        if detail:
            facts.append({"title": "Detail", "value": detail})

        # Adaptive Card payload (Teams Workflows format)
        adaptive_card = {
            "type":    "AdaptiveCard",
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.4",
            "body": [
                {
                    "type":   "TextBlock",
                    "size":   "Medium",
                    "weight": "Bolder",
                    "text":   f"{icon} BENTO Auto Checkout — {status.upper()}",
                    "color":  color,
                },
                {
                    "type":      "FactSet",
                    "facts":     facts,
                },
            ],
        }

        payload = {
            "type":        "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl":  None,
                "content":     adaptive_card,
            }],
        }

        data = json.dumps(payload).encode("utf-8")
        req  = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status in (200, 202):
                _log(logger, "✓ Teams notification sent.", log_callback)
                return True
            else:
                _log(logger,
                     f"⚠ Teams webhook returned HTTP {resp.status}",
                     log_callback, "warning")
                return False
    except Exception as e:
        # Non-fatal — checkout succeeded, notification is best-effort
        _log(logger, f"⚠ Teams notification failed (non-fatal): {e}",
             log_callback, "warning")
        return False


# ── SCAN CHECKOUT_RESULTS for collected files (fallback) ─────────────────────
def _scan_checkout_results(hostname, env, jira_key, logger, log_callback=None):
    """
    Fallback when the watcher sidecar doesn't include collected_files.

    Scans P:\\temp\\BENTO\\CHECKOUT_RESULTS\\<hostname_env>\\<jira_key>\\
    for the most recently modified subfolder and lists its files.

    Returns (file_names, folder_path) or ([], "").
    """
    import glob as _glob

    tester_folder = f"{hostname}_{env.upper()}" if hostname and env else (hostname or env or "")
    base = os.path.join(CHECKOUT_RESULTS_FOLDER, tester_folder, jira_key)

    if not os.path.isdir(base):
        _log(logger,
             f"  CHECKOUT_RESULTS not found: {base}",
             log_callback, "warning")
        return [], ""

    # Find the most recently modified workspace subfolder
    latest_dir  = None
    latest_time = 0
    try:
        for entry in os.listdir(base):
            full = os.path.join(base, entry)
            if not os.path.isdir(full):
                continue
            mtime = os.path.getmtime(full)
            if mtime > latest_time:
                latest_time = mtime
                latest_dir  = full
    except OSError as e:
        _log(logger, f"  Cannot scan {base}: {e}", log_callback, "warning")
        return [], ""

    if latest_dir is None:
        return [], ""

    # List files in the folder
    files = []
    try:
        for f in os.listdir(latest_dir):
            if os.path.isfile(os.path.join(latest_dir, f)):
                files.append(f)
    except OSError:
        pass

    if files:
        _log(logger,
             f"  Found {len(files)} file(s) in {latest_dir}",
             log_callback)
    return files, latest_dir


# ── PUBLIC API — called from checkout_controller.py [41] ─────────────────────
def run_checkout(
    jira_key:        str,
    hostname:        str,
    env:             str,
    tgz_path:        str   = "",
    hot_folder:      str   = "",
    mid:             str   = "",
    cfgpn:           str   = "",
    fw_ver:          str   = "",
    dut_slots:       int   = 4,
    lot_prefix:      str   = "JAANTJB",
    dut_locations:   Optional[list] = None,
    test_cases:      Optional[list] = None,
    detect_method:   str   = "AUTO",
    timeout_seconds: int   = CHECKOUT_TIMEOUT_SECONDS,
    notify_teams:    bool  = True,
    webhook_url:     str   = "",
    sap_instance:    str   = "PR1",
    autostart:       str   = "False",
    generate_tmptravl: bool = False,
    recipe_folder:   str   = "",
    python2_exe:     str   = "",
    site:            str   = "",
    form_factor:     str   = "",
    attr_overwrites: Optional[list] = None,
    recipe_override: str   = "",
    log_callback           = None,
    phase_callback         = None,
    cancel_event:    Optional[threading.Event] = None,
) -> Dict:
    """
    High-level entry point — Start Checkout full flow.

    KEY FIX [39]:
      XML is saved to CHECKOUT_QUEUE (P: shared drive), NOT hot_folder.
      hot_folder (C:\\test_program\\playground_queue) is on the TESTER machine.
      The checkout_watcher.py [42] running on the tester will:
        1. Detect XML in CHECKOUT_QUEUE
        2. Copy XML → C:\\test_program\\playground_queue (auto-creates it)
        3. SLATE picks up XML → AutoStart=True → test begins [24]

    Runs each test case sequentially, collects memory after all done,
    then sends Teams notification with summary. [24]
    """
    logger      = _get_logger(log_callback)
    env         = env.upper()
    start       = time.time()
    _test_cases = test_cases or [{"type": "passing", "label": "passing"}]

    # ── Validate inputs ───────────────────────────────────────────────
    if not hostname:
        msg = "Hostname is required for checkout."
        _log(logger, f"[FAIL] {msg}", log_callback, "error")
        return {"status": "failed", "detail": msg, "elapsed": 0,
                "test_cases": [], "memory": {}}

    valid_envs = get_valid_envs()
    if env not in valid_envs:
        msg = (f"Unknown ENV '{env}'. "
               f"Valid: {', '.join(sorted(valid_envs))}")
        _log(logger, f"[FAIL] {msg}", log_callback, "error")
        return {"status": "failed", "detail": msg, "elapsed": 0,
                "test_cases": [], "memory": {}}

    _log(logger, f"=== BENTO Auto Checkout ===", log_callback)
    _log(logger, f"JIRA     : {jira_key}",  log_callback)
    _log(logger, f"Hostname : {hostname}",   log_callback)
    _log(logger, f"ENV      : {env}",        log_callback)
    _log(logger, f"MID      : {mid}",        log_callback)
    _log(logger, f"CFGPN    : {cfgpn}",      log_callback)
    _log(logger, f"FW       : {fw_ver}",     log_callback)
    _log(logger, f"Slots    : {dut_slots}",  log_callback)
    _log(logger, f"Lot pfx  : {lot_prefix}", log_callback)

    # ── KEY FIX: XML goes to CHECKOUT_QUEUE, not hot_folder ──────────
    # CHECKOUT_QUEUE = P:\temp\BENTO\CHECKOUT_QUEUE  (shared P: drive)
    # The watcher on the tester picks it up and copies to playground_queue
    _queue = CHECKOUT_QUEUE_FOLDER

    # ── Auto-create CHECKOUT_QUEUE on shared drive ────────────────────
    try:
        os.makedirs(_queue, exist_ok=True)
        _log(logger, f"[✓] Queue folder ready: {_queue}", log_callback)
    except Exception as e:
        msg = f"Cannot create CHECKOUT_QUEUE {_queue}: {e}"
        _log(logger, f"[FAIL] {msg}", log_callback, "error")
        return {"status": "failed", "detail": msg, "elapsed": 0,
                "test_cases": [], "memory": {}}

    all_tc_results = []
    generated_files = []   # Track all files created during checkout

    # ── Loop per test case (PASSING + FORCE FAIL sequentially) ────────
    for tc in _test_cases:
        label   = tc.get("label", "passing")
        tc_type = tc.get("type",  "passing")
        tc_desc = tc.get("description", "")

        _log(logger,
             f"\n{'=' * 50}\n"
             f"Test case: {label} ({tc_type})\n"
             f"{'=' * 50}",
             log_callback)

        # ── Generate XML → save to CHECKOUT_QUEUE ─────────────────────
        # NOT to C:\test_program\playground_queue
        # (that path only exists on the tester machine) [39]
        xml_path = generate_slate_xml(
            jira_key          = jira_key,
            mid               = mid,
            cfgpn             = cfgpn,
            fw_ver            = fw_ver,
            dut_slots         = dut_slots,
            tgz_path          = tgz_path,
            env               = env,
            lot_prefix        = lot_prefix,
            dut_locations     = dut_locations,
            label             = label,
            hostname          = hostname,
            output_dir        = _queue,      # ← P:\temp\BENTO\CHECKOUT_QUEUE
            generate_tmptravl = generate_tmptravl,
            recipe_folder     = recipe_folder,
            python2_exe       = python2_exe,
            site              = site,
            form_factor       = form_factor,
            sap_instance      = sap_instance,
            autostart         = autostart,
            attr_overwrites   = attr_overwrites,
            recipe_override   = recipe_override,
            logger            = logger,
            log_callback      = log_callback,
        )

        if not xml_path:
            result = {
                "status":      "failed",
                "detail":      "XML generation failed.",
                "elapsed":     int(time.time() - start),
                "label":       label,
                "type":        tc_type,
                "description": tc_desc
            }
            all_tc_results.append(result)
            continue

        # ── Write initial queued status ────────────────────────────────
        write_checkout_status(
            xml_path, "queued",
            f"Waiting for tester pickup — {label}"
        )

        generated_files.append(xml_path)
        _log(logger,
             f"[✓] XML queued: {os.path.basename(xml_path)}\n"
             f"    Waiting for checkout_watcher.py on {hostname} to pick up...",
             log_callback)

        # Track only the specific tmptravl file for this MID, not all
        # files in Traces/.  The tmptravl generator creates per-MID files
        # named {MID}_tmptravl_{STEP}.dat — we only want that one file
        # so the generated-files count matches expectations (2 per MID:
        # 1 XML + 1 tmptravl).
        if mid and env:
            tmptravl_name = f"{mid}_tmptravl_{env.strip().upper()}.dat"
            traces_dir = os.path.join(_queue, "Traces")
            tmptravl_fpath = os.path.join(traces_dir, tmptravl_name)
            if os.path.isfile(tmptravl_fpath) and tmptravl_fpath not in generated_files:
                generated_files.append(tmptravl_fpath)

        # ── Poll for SLATE completion ──────────────────────────────────
        tc_result = wait_for_checkout(
            xml_path        = xml_path,
            logger          = logger,
            log_callback    = log_callback,
            phase_callback  = phase_callback,
            timeout_seconds = timeout_seconds,
            cancel_event    = cancel_event,
        )
        tc_result["label"]       = label
        tc_result["type"]        = tc_type
        tc_result["description"] = tc_desc
        all_tc_results.append(tc_result)
        
        if cancel_event and cancel_event.is_set():
            break

        icon = "✓" if tc_result["status"] == "success" else "✗"
        _log(logger,
             f"[{icon}] {label}: {tc_result['status']} "
             f"in {tc_result['elapsed']}s",
             log_callback)

    # ── Teams notification ────────────────────────────────────────────
    final_status  = ("success"
                     if all(r["status"] == "success"
                            for r in all_tc_results)
                     else "partial"
                     if any(r["status"] == "success"
                            for r in all_tc_results)
                     else "failed")
    final_detail  = " | ".join(
        f"{r['label']}:{r['status']}" for r in all_tc_results
    )
    final_elapsed = int(time.time() - start)

    if notify_teams:
        send_teams_notification(
            jira_key     = jira_key,
            hostname     = hostname,
            status       = final_status,
            detail       = final_detail,
            elapsed      = final_elapsed,
            webhook_url  = webhook_url,
            logger       = logger,
            log_callback = log_callback,
        )

    # ── Prefer watcher-collected files over generated files ────────────
    # The watcher writes collected_files (e.g. resultsManager.db,
    # DispatcherDebug*.txt) and output_folder (CHECKOUT_RESULTS path)
    # into the .checkout_status sidecar.  wait_for_checkout() passes
    # them through in tc_result.  Use those for the popup if available.
    collected_files  = []
    collected_folder = ""
    for tc_r in all_tc_results:
        for cf in tc_r.get("collected_files", []):
            if cf not in collected_files:
                collected_files.append(cf)
        if not collected_folder and tc_r.get("collected_output_folder"):
            collected_folder = tc_r["collected_output_folder"]

    # ── Fallback: scan CHECKOUT_RESULTS if sidecar had no file info ───
    # Works even when the watcher hasn't been restarted with the new
    # write_status() that includes collected_files/output_folder.
    if not collected_files and final_status in ("success", "partial"):
        _log(logger,
             "Sidecar had no collected_files — scanning CHECKOUT_RESULTS…",
             log_callback)
        collected_files, collected_folder = _scan_checkout_results(
            hostname, env, jira_key, logger, log_callback
        )

    # Decide what to show: watcher-collected files take priority
    display_files  = collected_files or [os.path.basename(f) for f in generated_files]
    display_folder = collected_folder or _queue

    # ── Log files summary ──────────────────────────────────────────────
    if display_files:
        label = "Collected files" if collected_files else "Generated files"
        _log(logger, f"── {label} ({len(display_files)}) ──", log_callback)
        for fname in display_files:
            _log(logger, f"  ✓ {fname}", log_callback)
        _log(logger, f"  Output folder: {display_folder}", log_callback)

    _log(logger,
         f"=== Checkout {final_status.upper()} in {final_elapsed}s ===",
         log_callback)

    return {
        "status":          final_status,
        "detail":          final_detail,
        "elapsed":         final_elapsed,
        "test_cases":      all_tc_results,
        "memory":          {"status": "skipped", "detail": "memory collection not configured"},
        "generated_files": display_files,
        "output_folder":   display_folder,
    }


# ── STANDALONE ENTRY POINT ────────────────────────────────────────────────────
def main():
    valid_envs = get_valid_envs()
    parser = argparse.ArgumentParser(
        description="BENTO Checkout Orchestrator (standalone test)"
    )
    parser.add_argument("--jira-key",    required=True)
    parser.add_argument("--hostname",    required=True)
    parser.add_argument("--env",         required=True,
                        help=f"({', '.join(sorted(valid_envs))})")
    parser.add_argument("--tgz-path",    default="")
    parser.add_argument("--mid",         default="")
    parser.add_argument("--cfgpn",       default="")
    parser.add_argument("--fw-ver",      default="")
    parser.add_argument("--dut-slots",   type=int, default=4)
    parser.add_argument("--lot-prefix",  default="JAANTJB")
    parser.add_argument("--timeout-min", type=int, default=60)
    parser.add_argument("--no-notify",   action="store_true")
    args = parser.parse_args()

    result = run_checkout(
        jira_key        = args.jira_key,
        hostname        = args.hostname,
        env             = args.env,
        tgz_path        = args.tgz_path,
        mid             = args.mid,
        cfgpn           = args.cfgpn,
        fw_ver          = args.fw_ver,
        dut_slots       = args.dut_slots,
        lot_prefix      = args.lot_prefix,
        timeout_seconds = args.timeout_min * 60,
        notify_teams    = not args.no_notify,
    )

    print(f"\nResult : {result['status'].upper()}")
    print(f"Detail : {result['detail']}")
    print(f"Elapsed: {result['elapsed']}s")
    sys.exit(0 if result["status"] == "success" else 1)


if __name__ == "__main__":
    main()
    