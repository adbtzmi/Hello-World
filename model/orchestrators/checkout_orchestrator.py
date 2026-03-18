#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
checkout_orchestrator.py
========================
BENTO Checkout Orchestrator — mirrors compilation_orchestrator.py [9]

Runs on LOCAL PC (same machine as main.py).

Responsibilities:
  1. Generate XML profile with ALL checkout parameters
  2. Drop XML to shared CHECKOUT_QUEUE folder
  3. Poll .checkout_status file written by checkout_watcher.py
  4. Wait for checkout completion
  5. Return structured result dict

Mirrors EXACT same pattern as compilation_orchestrator.py [9]:
    - XML generation instead of ZIP creation
    - Same polling pattern with .checkout_status
    - Same timeout handling
    - Same result dict structure
"""

import os
import sys
import time
import json
import logging
from datetime import datetime
from typing import Optional, Dict, List
import xml.etree.ElementTree as ET
import xml.dom.minidom as minidom

# ─────────────────────────────────────────────
# CONFIGURATION (mirrors compilation_orchestrator.py)
# ─────────────────────────────────────────────

CHECKOUT_QUEUE_FOLDER = r"P:\temp\BENTO\CHECKOUT_QUEUE"
CHECKOUT_RESULTS_FOLDER = r"P:\temp\BENTO\CHECKOUT_RESULTS"

# Poll interval for status file
POLL_INTERVAL = 30  # seconds

# Total timeout for checkout (8 hours)
CHECKOUT_TIMEOUT_SECONDS = 8 * 3600  # 8 hours

# ─────────────────────────────────────────────
# LOGGER (mirrors compilation_orchestrator.py)
# ─────────────────────────────────────────────

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


def _phase(logger, msg: str, log_callback=None):
    """Log a phase update that GUI can detect"""
    _log(logger, msg, log_callback)
    if log_callback:
        log_callback("__PHASE__:" + msg)


# ─────────────────────────────────────────────
# STEP 1 – Generate XML Profile
# ─────────────────────────────────────────────

class CheckoutXMLGenerator:
    """
    Auto-generates XML profile that fills ALL fields in
    SSD Tester Engineering GUI.
    
    Replaces manual Notepad++ editing.
    """
    
    def __init__(self, inputs: Dict):
        """
        Args:
            inputs: Dict with keys:
                - tgz_path: Path to compiled TGZ
                - mids: List of MID strings
                - lot_prefix: Dummy lot prefix
                - dut_locations: List of "row,col" strings
                - env: ABIT/SFN2/CNFG
                - jira_key: JIRA issue key
                - recipe_file: Recipe file path
        """
        self.inputs = inputs
    
    def build_xml(self) -> str:
        """Generate complete XML profile"""
        profile = ET.Element("Profile")
        
        # ── TestJobArchive ─────────────────────────────
        tja = ET.SubElement(profile, "TestJobArchive")
        tja.text = self.inputs["tgz_path"]
        
        # ── RecipeFile ─────────────────────────────────
        rf = ET.SubElement(profile, "RecipeFile")
        rf.text = self.inputs.get("recipe_file", "RECIPE:PEREGRINE\\ON_NEOSEM_ABIT.XML")
        
        # ── TempTraveler ───────────────────────────────
        profile.append(self._build_temp_traveler())
        
        # ── AdditionalFileFolder ───────────────────────
        profile.append(self._build_additional_files())
        
        # ── MaterialInfo ───────────────────────────────
        profile.append(self._build_material_info())
        
        # ── AutoStart = True ───────────────────────────
        auto = ET.SubElement(profile, "AutoStart")
        auto.text = "True"
        
        return self._prettify(profile)
    
    def _build_temp_traveler(self) -> ET.Element:
        """Build TempTraveler section"""
        tt = ET.Element("TempTraveler")
        
        # Standard attributes for ABIT checkout
        attrs = [
            ("MAM", "STEP", "ABIT"),
            ("MAM", "NAND_OPTION", "BAD_PLANE"),
            ("CFGPN", "STEP_ID", "AMB IB TEST"),
            ("CFGPN", "SEC_PROCESS", "ABIT_REQ0"),
            ("EQUIPMENT", "DIB_TYPE", "MS0052"),
            ("EQUIPMENT", "DIB_TYPE_NAME", "MS0022"),
        ]
        
        for section, name, value in attrs:
            attr = ET.SubElement(tt, "Attribute")
            attr.set("Section", section)
            attr.set("Name", name)
            attr.set("Value", value)
        
        return tt
    
    def _build_additional_files(self) -> ET.Element:
        """Build AdditionalFileFolder section"""
        aff = ET.Element("AdditionalFileFolder")
        
        # Standard firmware/config paths
        folders = [
            (r"\\pgfsmodauto\modauto\release\asd\config", r"OS\config"),
            (r"\\pgfsmodauto\modauto\release\asd\firmware", r"OS\net_files"),
        ]
        
        for source, dest in folders:
            folder = ET.SubElement(aff, "Folder")
            folder.set("Source", source)
            folder.set("Destination", dest)
        
        return aff
    
    def _build_material_info(self) -> ET.Element:
        """Build MaterialInfo section with all DUTs"""
        mi = ET.Element("MaterialInfo")
        
        mids = self.inputs["mids"]
        locs = self.inputs["dut_locations"]
        lot_prefix = self.inputs["lot_prefix"]
        
        # Generate dummy lot for each DUT
        for i, (mid, loc) in enumerate(zip(mids, locs)):
            lot = f"{lot_prefix}{str(i+1).zfill(3)}"
            
            attr = ET.SubElement(mi, "Attribute")
            attr.set("Lot", lot)
            attr.set("MID", mid)
            attr.set("DutLocation", loc)
        
        return mi
    
    def _prettify(self, elem: ET.Element) -> str:
        """Pretty-print XML with indentation"""
        rough = ET.tostring(elem, encoding="unicode")
        reparsed = minidom.parseString(rough)
        return reparsed.toprettyxml(indent="  ")
    
    def save(self, output_folder: str) -> str:
        """
        Generate XML and save to output folder.
        
        Returns full path to saved XML file.
        """
        xml_content = self.build_xml()
        
        # Filename: checkout_<JIRA>_<ENV>_<TIMESTAMP>.xml
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        jira_key = self.inputs["jira_key"]
        env = self.inputs["env"]
        filename = f"checkout_{jira_key}_{env}_{timestamp}.xml"
        
        os.makedirs(output_folder, exist_ok=True)
        xml_path = os.path.join(output_folder, filename)
        
        with open(xml_path, "w", encoding="utf-8") as f:
            f.write(xml_content)
        
        return xml_path


# ─────────────────────────────────────────────
# STEP 2 – Status File Management
# ─────────────────────────────────────────────

def write_checkout_status(xml_path: str, status: str, detail: str = ""):
    """
    Write .checkout_status sidecar file.
    Mirrors write_status() from watcher_lock.py [4].
    
    Args:
        xml_path: Full path to XML file
        status: 'queued', 'in_progress', 'success', 'failed', 'timeout'
        detail: Human-readable detail message
    """
    status_path = xml_path + ".checkout_status"
    payload = {
        "xml_file": os.path.basename(xml_path),
        "status": status,
        "detail": detail,
        "timestamp": datetime.now().isoformat(),
    }
    try:
        with open(status_path, "w") as f:
            json.dump(payload, f, indent=2)
    except Exception as e:
        logger.warning(f"Could not write checkout status: {e}")


def read_checkout_status(xml_path: str) -> Optional[Dict]:
    """
    Read .checkout_status sidecar file.
    Mirrors _read_status() from compilation_orchestrator.py [9].
    """
    status_file = xml_path + ".checkout_status"
    if not os.path.exists(status_file):
        return None
    try:
        with open(status_file) as f:
            return json.load(f)
    except Exception:
        return None


# ─────────────────────────────────────────────
# STEP 3 – Poll for Checkout Completion
# ─────────────────────────────────────────────

def wait_for_checkout(xml_path: str, logger, log_callback=None) -> Dict:
    """
    Poll .checkout_status until terminal state or timeout.
    Mirrors wait_for_build() from compilation_orchestrator.py [9].
    
    Returns result dict:
        {
            "status": "success" | "failed" | "timeout",
            "detail": "<message>",
            "elapsed": <seconds>
        }
    """
    xml_name = os.path.basename(xml_path)
    start = time.time()
    deadline = start + CHECKOUT_TIMEOUT_SECONDS
    
    _phase(logger, "Waiting for tester to pick up XML...", log_callback)
    _log(logger, f"   (timeout = {CHECKOUT_TIMEOUT_SECONDS // 3600} hours)", log_callback)
    
    last_phase_update = start
    
    while time.time() < deadline:
        status_data = read_checkout_status(xml_path)
        
        if status_data:
            state = status_data.get("status", "")
            
            if state == "success":
                elapsed = int(time.time() - start)
                detail = status_data.get("detail", "Checkout complete")
                _log(logger, f"✓ Checkout SUCCESS in {elapsed}s", log_callback)
                
                # Clean up XML and status file after successful checkout
                try:
                    os.remove(xml_path)
                    status_file = xml_path + ".checkout_status"
                    if os.path.exists(status_file):
                        os.remove(status_file)
                    _log(logger, f"✓ Cleaned up {os.path.basename(xml_path)}", log_callback)
                except Exception as e:
                    _log(logger, f"⚠ Could not clean up XML: {e}", log_callback, "warning")
                
                return {
                    "status": "success",
                    "detail": detail,
                    "elapsed": elapsed,
                }
            
            elif state == "failed":
                elapsed = int(time.time() - start)
                detail = status_data.get("detail", "Unknown error")
                _log(logger, f"✗ Checkout FAILED after {elapsed}s: {detail}", log_callback, "error")
                return {
                    "status": "failed",
                    "detail": detail,
                    "elapsed": elapsed,
                }
            
            elif state == "in_progress":
                elapsed_so_far = int(time.time() - start)
                # Update phase every 2 minutes to avoid log spam
                if elapsed_so_far % 120 < POLL_INTERVAL:
                    _phase(logger, f"Checkout running... ({elapsed_so_far}s elapsed)", log_callback)
                    last_phase_update = time.time()
        
        time.sleep(POLL_INTERVAL)
    
    # Timeout
    elapsed = int(time.time() - start)
    _log(logger, f"✗ Checkout TIMEOUT after {elapsed}s", log_callback, "error")
    return {
        "status": "timeout",
        "detail": f"No response from tester within {CHECKOUT_TIMEOUT_SECONDS}s",
        "elapsed": elapsed,
    }


# ─────────────────────────────────────────────
# PUBLIC API – called from controller
# ─────────────────────────────────────────────

def run_checkout(
    tgz_path: str,
    mids: List[str],
    lot_prefix: str,
    dut_locations: List[str],
    env: str,
    jira_key: str,
    log_callback=None,
    recipe_file: str = None,
) -> Dict:
    """
    High-level entry point called from BentoCheckout controller.
    
    Args:
        tgz_path: Path to compiled TGZ from Phase 1
        mids: List of MID strings
        lot_prefix: Dummy lot prefix (e.g., "JAANTJB")
        dut_locations: List of "row,col" strings (e.g., ["0,0", "0,1"])
        env: ABIT/SFN2/CNFG
        jira_key: JIRA issue key
        log_callback: Optional callback for GUI logging
        recipe_file: Optional recipe file path override
    
    Returns dict: status, detail, elapsed
    """
    logger = _get_logger(log_callback)
    env = env.upper()
    
    # Validate inputs
    errors = []
    if not tgz_path or not os.path.exists(tgz_path):
        errors.append(f"TGZ file not found: {tgz_path}")
    if not mids:
        errors.append("MID list is empty")
    if not lot_prefix:
        errors.append("Lot prefix is required")
    if not dut_locations:
        errors.append("DUT locations list is empty")
    if len(mids) != len(dut_locations):
        errors.append(f"MID count ({len(mids)}) != DUT location count ({len(dut_locations)})")
    
    if errors:
        error_msg = "Checkout validation failed:\n" + "\n".join(f"  • {e}" for e in errors)
        _log(logger, error_msg, log_callback, "error")
        return {
            "status": "failed",
            "detail": error_msg,
            "elapsed": 0,
        }
    
    # Build inputs dict for XML generator
    xml_inputs = {
        "tgz_path": tgz_path,
        "mids": mids,
        "lot_prefix": lot_prefix,
        "dut_locations": dut_locations,
        "env": env,
        "jira_key": jira_key,
        "recipe_file": recipe_file or "RECIPE:PEREGRINE\\ON_NEOSEM_ABIT.XML",
    }
    
    # STEP 1: Generate XML
    _phase(logger, "Generating XML profile...", log_callback)
    gen = CheckoutXMLGenerator(xml_inputs)
    xml_path = gen.save(CHECKOUT_QUEUE_FOLDER)
    _log(logger, f"✓ XML created: {os.path.basename(xml_path)}", log_callback)
    
    # STEP 2: Write initial status (mirrors compilation_orchestrator.py [9])
    write_checkout_status(xml_path, "queued", "Waiting for tester pickup")
    
    # STEP 3: Poll for completion (mirrors wait_for_build() [9])
    result = wait_for_checkout(xml_path, logger, log_callback)
    
    return result


# ─────────────────────────────────────────────
# CRT DATABASE INTEGRATION
# ─────────────────────────────────────────────

def load_dut_info_from_crt(cfgpn: str = None, excel_path: str = None) -> List[Dict]:
    """
    Load DUT info from C.A.T. CRT database or Excel.
    Mirrors update_db_with_crt_excel() from CAT.py [17].
    
    Option A: Read from C.A.T. SQLite database (preferred)
    Option B: Read from exported Excel file
    
    Returns list of dicts with keys: MID, CFGPN, FW_Wave_ID, Product_Name
    """
    import pandas as pd
    
    # Load C.A.T. database path from settings.json
    crt_db_path = r"\\sifsmodauto\modauto\temp\cat\CRT_Automation.db"
    try:
        if os.path.exists("settings.json"):
            with open("settings.json", "r") as f:
                settings = json.load(f)
                crt_db_path = settings.get("cat", {}).get("db_path", crt_db_path)
    except Exception:
        pass  # Use default path if settings.json not found
    
    # Option A — Direct DB read (preferred)
    
    if os.path.exists(crt_db_path):
        try:
            import sqlite3
            conn = sqlite3.connect(crt_db_path)
            
            query = """
                SELECT MID, CFGPN, FW_Wave_ID, Product_Name,
                       Material_Description, MCTO_1
                FROM incoming_crt
                WHERE status = 'ACTIVE'
            """
            if cfgpn:
                query += f" AND CFGPN = '{cfgpn}'"
            
            df = pd.read_sql(query, conn)
            conn.close()
            
            return df.to_dict("records")
        except Exception as e:
            logger.warning(f"Could not read CRT database: {e}")
    
    # Option B — Read from Excel (fallback)
    if excel_path and os.path.exists(excel_path):
        try:
            df = pd.read_excel(excel_path, engine="openpyxl", dtype=str)
            
            # Use EXACT column names from crt_excel_template.json [24]
            # Note: "Product  Name" has DOUBLE SPACE!
            mids = df["Material description"].dropna().tolist()
            fw_waves = df["FW Wave ID"].dropna().tolist()
            cfgpns = df["CFGPN"].dropna().tolist()
            products = df["Product  Name"].dropna().tolist()  # double space!
            
            # Build result list
            results = []
            for i in range(len(mids)):
                results.append({
                    "MID": mids[i] if i < len(mids) else "",
                    "CFGPN": cfgpns[i] if i < len(cfgpns) else "",
                    "FW_Wave_ID": fw_waves[i] if i < len(fw_waves) else "",
                    "Product_Name": products[i] if i < len(products) else "",
                })
            
            return results
        except Exception as e:
            logger.warning(f"Could not read CRT Excel: {e}")
    
    return []


# ─────────────────────────────────────────────
# STANDALONE TEST ENTRY POINT
# ─────────────────────────────────────────────

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="BENTO Checkout Orchestrator (standalone test)")
    parser.add_argument("--tgz-path", required=True, help="Path to compiled TGZ")
    parser.add_argument("--mids", required=True, help="Comma-separated MID list")
    parser.add_argument("--lot-prefix", required=True, help="Dummy lot prefix")
    parser.add_argument("--dut-locs", required=True, help="Space-separated DUT locations")
    parser.add_argument("--env", required=True, help="ABIT/SFN2/CNFG")
    parser.add_argument("--jira-key", required=True, help="JIRA issue key")
    args = parser.parse_args()
    
    mids = [m.strip() for m in args.mids.split(",")]
    dut_locs = args.dut_locs.split()
    
    print(f"\n{'='*60}")
    print(f"  BENTO Checkout Orchestrator")
    print(f"  JIRA:     {args.jira_key}")
    print(f"  Env:      {args.env}")
    print(f"  TGZ:      {args.tgz_path}")
    print(f"  MIDs:     {len(mids)} DUTs")
    print(f"{'='*60}\n")
    
    result = run_checkout(
        tgz_path=args.tgz_path,
        mids=mids,
        lot_prefix=args.lot_prefix,
        dut_locations=dut_locs,
        env=args.env,
        jira_key=args.jira_key,
    )
    
    print(f"\n{'='*60}")
    print(f"  Result:  {result['status'].upper()}")
    print(f"  Detail:  {result['detail']}")
    print(f"  Elapsed: {result['elapsed']}s")
    print(f"{'='*60}\n")
    
    sys.exit(0 if result["status"] == "success" else 1)


if __name__ == "__main__":
    main()
