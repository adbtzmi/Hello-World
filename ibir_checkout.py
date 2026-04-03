#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
IBIR SSD Tester Checkout Automation Script
===========================================
Picks up AFTER playground creation and handles:
  Step 1: Validate playground exists
  Step 2: Copy FW + Flow + Params to workspace
  Step 3: Run test via H4 GUI
  Step 4: Monitor test completion
  Step 5: Collect results + Notify

Platform: Neosem IBIR Tester (Windows 7, Python 2.7.8)
Requires: pywinauto 0.6.3, pyautogui (fallback)

Usage:
  cd C:\\temp\\BENTO
  C:\\Python27\\python.exe ibir_checkout.py
"""

from __future__ import print_function

import os
import sys
import time
import shutil
import glob
import json
import datetime
import subprocess
import traceback
import socket

try:
    import xml.etree.ElementTree as ET
except ImportError:
    ET = None  # type: ignore[assignment]

# ===========================================================================
# CONFIG - Engineer edits this section for each checkout
# ===========================================================================

CONFIG = {
    # Drive identification
    'MID': 'T1234567890',
    'LOT_ID': 'LOT-2025-0710',

    # Tester identification
    'TESTER_ID': 'IBIR-0434',
    'BLADE_SN': 'ANSKR22195608011200',
    'DUT_SLOT': 27,

    # Product and firmware
    'PRODUCT': '3610',
    'FW_SRC': 'VBZZ0007',        # Source folder on Dwelling
    'FW_DEST': 'VBZZ0005',       # Destination folder in workspace
    'FW_SUB': '0002',             # Sub-folder (set to '' if none)
    'REV': 'REV178NH',            # Revision folder for flow file
    'FLOW_FILE': 'JacksonQLC_neosem_ABIT.flow',
    'RECIPE_XML': 'JacksonQLC_neosem_ABIT.xml',

    # Paths
    'DWELLING_DRIVE': 'N',        # N or M (no colon)
    'PARAM_FOLDER': '',            # Set to 'Parameter_File' if needed

    # Monitoring
    'TIMEOUT_HOURS': 8,            # Max hours to wait for test
    'POLL_INTERVAL_SEC': 30,       # How often to check for completion

    # Results collection
    'RESULTS_BASE': r'P:\temp\checkout_results',
    # Set to '' to skip results collection

    # Notification (optional)
    'TEAMS_WEBHOOK_URL': '',       # Set to Teams webhook URL if available
}

# ===========================================================================
# CONSTANTS
# ===========================================================================

SCRIPT_VERSION = '1.1.0'
SCRIPT_NAME = 'IBIR Checkout Automation'

WORKSPACE_BASE = r'C:\Tanisys\DNA2\User\Workspace'
H4_EXE_PATH = r'C:\Program Files (x86)\DNA2\R1.10\Bin\H4.exe'
RPYC_LOG_DIR = r'C:\rpyc_logs'
PROFILE_LOG_DIR = r'C:\test_program\profile_logs'
DIAG_LOG_DIR = r'C:\Tanisys\DNA2\User\diagnostics'
TESTER_CTRL_XML = r'C:\ModAuto\SSDTesterCtrlr\SSDTesterCtrlr.xml'
LAST_CONFIG_PATH = r'C:\temp\BENTO\last_checkout_config.json'

# Engineering checkout result paths (from actual IBIR-0383 tester)
ENG_SUMMARY_PATH = r'C:\test_program\eng_summary_staging'
ENG_CACHE_PATH = r'C:\test_program\eng_cache'
PROD_SUMMARY_PATH = r'D:\test_program\summary_staging'
PLAYGROUND_QUEUE_PATH = r'C:\test_program\playground_queue'

# Blade SN prefixes — used to distinguish blade workspace folders
# from product workspace folders (e.g. 6500_Vintage_ABIT)
BLADE_SN_PREFIXES = ('ANSKR', 'ENSKR', 'CNSKR')

# H4 GUI control auto_ids (from pywinauto inspection)
H4_CONTROLS = {
    'window_title': 'H4: 2.0.1',
    'recipe_textbox': 'tbRecPath',
    'recipe_browse_btn': 'btnRecipe',
    'recipe_edit_btn': 'btRecipeEdit',
    'start_test_btn': 'btnStart',
    'add_sites_btn': 'btnAddSites',
    'remove_sites_btn': 'btnUnSelect',
    'tree_view': 'trVInfo',
    'lot_id_textbox': 'tBLotId',
    'trace_log': 'rTBTrace',
    'new_runs_grid': 'dGVNewRuns',
    'run_info_list': 'lVRunInfo',
    'lot_list_grid': 'dGVLotList',
}

# Error keywords to watch for in logs
ERROR_KEYWORDS = [
    'TCS Crash',
    'ABORT',
    'VerifyRPYCAlive Failure',
    'FATAL',
    'Exception',
    'Unhandled',
]

COMPLETION_KEYWORDS = [
    'Complete',
    'PASS',
    'FAIL',
    'Done',
    'TEST_COMPLETED',
    'Result Received',
]


# ===========================================================================
# HELPER FUNCTIONS
# ===========================================================================

def log(msg, level='INFO'):
    """Print a timestamped log message."""
    ts = datetime.datetime.now().strftime('%H:%M:%S')
    print('[%s] %s: %s' % (ts, level, msg))


def log_step(step_num, msg):
    """Print a step header."""
    ts = datetime.datetime.now().strftime('%H:%M:%S')
    print('')
    print('=' * 60)
    print('[%s] STEP %d: %s' % (ts, step_num, msg))
    print('=' * 60)


def ask_user(prompt, default='y'):
    """Ask user a yes/no question. Returns True for yes."""
    if default.lower() == 'y':
        hint = '(Y/n)'
    else:
        hint = '(y/N)'
    try:
        answer = raw_input('[INPUT] %s %s: ' % (prompt, hint)).strip().lower()
    except EOFError:
        answer = ''
    if answer == '':
        answer = default.lower()
    return answer in ('y', 'yes')


def ask_user_choice(prompt, choices):
    """Ask user to pick from choices. Returns the choice string."""
    print('[INPUT] %s' % prompt)
    for i, choice in enumerate(choices):
        print('  %d) %s' % (i + 1, choice))
    try:
        answer = raw_input('Enter choice (1-%d): ' % len(choices)).strip()
    except EOFError:
        answer = '1'
    try:
        idx = int(answer) - 1
        if 0 <= idx < len(choices):
            return choices[idx]
    except (ValueError, IndexError):
        pass
    return choices[0]


def format_size(size_bytes):
    """Format bytes to human-readable size."""
    if size_bytes < 1024:
        return '%d B' % size_bytes
    elif size_bytes < 1024 * 1024:
        return '%.1f KB' % (size_bytes / 1024.0)
    elif size_bytes < 1024 * 1024 * 1024:
        return '%.1f MB' % (size_bytes / (1024.0 * 1024.0))
    else:
        return '%.2f GB' % (size_bytes / (1024.0 * 1024.0 * 1024.0))


def format_duration(seconds):
    """Format seconds to Xh Ym Zs."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return '%dh %dm %ds' % (hours, minutes, secs)
    elif minutes > 0:
        return '%dm %ds' % (minutes, secs)
    else:
        return '%ds' % secs


def get_workspace_path(config):
    """Build the workspace path from config."""
    folder_name = '%s-x-x' % config['BLADE_SN']
    return os.path.join(WORKSPACE_BASE, folder_name)


def get_recipe_path(config):
    """Build the full recipe XML path."""
    ws = get_workspace_path(config)
    return os.path.join(ws, 'recipe', config['RECIPE_XML'])


def get_dwelling_base(config):
    """Build the Dwelling drive base path."""
    return '%s:\\Dwelling\\ENGOPS\\%s' % (
        config['DWELLING_DRIVE'], config['PRODUCT']
    )


def count_files_in_dir(dirpath):
    """Count files recursively in a directory."""
    count = 0
    total_size = 0
    if not os.path.isdir(dirpath):
        return 0, 0
    for root, dirs, files in os.walk(dirpath):
        for f in files:
            count += 1
            fpath = os.path.join(root, f)
            try:
                total_size += os.path.getsize(fpath)
            except OSError:
                pass
    return count, total_size


def copy_tree_contents(src, dst, overwrite=True):
    """
    Copy all files/folders from src into dst.
    Unlike shutil.copytree, dst can already exist.
    Returns (file_count, total_bytes).
    """
    file_count = 0
    total_bytes = 0

    if not os.path.isdir(src):
        raise OSError('Source directory does not exist: %s' % src)

    if not os.path.isdir(dst):
        os.makedirs(dst)

    for item in os.listdir(src):
        s = os.path.join(src, item)
        d = os.path.join(dst, item)
        if os.path.isdir(s):
            sub_count, sub_bytes = copy_tree_contents(s, d, overwrite)
            file_count += sub_count
            total_bytes += sub_bytes
        else:
            if overwrite or not os.path.exists(d):
                try:
                    shutil.copy2(s, d)
                    fsize = os.path.getsize(s)
                    file_count += 1
                    total_bytes += fsize
                    log('  Copied: %s (%s)' % (item, format_size(fsize)))
                except (IOError, OSError) as e:
                    log('  FAILED to copy %s: %s' % (s, str(e)), 'ERROR')
                    raise

    return file_count, total_bytes


def get_rpyc_log_path():
    """Get today's RPYC log file path."""
    today = datetime.datetime.now().strftime('%Y%m%d')
    return os.path.join(RPYC_LOG_DIR, 'rpyc_log_%s.log' % today)


def read_last_lines(filepath, num_lines=50):
    """Read the last N lines of a file efficiently."""
    lines = []
    try:
        with open(filepath, 'rb') as f:
            # Seek to end
            f.seek(0, 2)
            file_size = f.tell()
            # Read last chunk (estimate ~200 bytes per line)
            chunk_size = min(file_size, num_lines * 200)
            f.seek(max(0, file_size - chunk_size))
            data = f.read()
            lines = data.decode('utf-8', errors='replace').splitlines()
            lines = lines[-num_lines:]
    except (IOError, OSError):
        pass
    return lines


def print_banner():
    """Print the script banner."""
    print('')
    print('=' * 60)
    print('  %s v%s' % (SCRIPT_NAME, SCRIPT_VERSION))
    print('  %s' % datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    print('  Python %s' % sys.version.split()[0])
    print('  Platform: %s' % sys.platform)
    print('=' * 60)
    print('')


def print_config(config):
    """Print the current configuration."""
    print('--- CONFIGURATION ---')
    # Print in logical groups
    groups = [
        ('Drive', ['MID', 'LOT_ID']),
        ('Tester', ['TESTER_ID', 'BLADE_SN', 'DUT_SLOT']),
        ('Product', ['PRODUCT', 'FW_SRC', 'FW_DEST', 'FW_SUB',
                      'REV', 'FLOW_FILE', 'RECIPE_XML']),
        ('Paths', ['DWELLING_DRIVE', 'PARAM_FOLDER']),
        ('Monitoring', ['TIMEOUT_HOURS', 'POLL_INTERVAL_SEC']),
        ('Results', ['RESULTS_BASE']),
        ('Notification', ['TEAMS_WEBHOOK_URL']),
    ]
    for group_name, keys in groups:
        print('  [%s]' % group_name)
        for key in keys:
            val = config.get(key, '')
            if key == 'TEAMS_WEBHOOK_URL' and val:
                # Mask webhook URL
                val = val[:30] + '...'
            print('    %-20s = %s' % (key, val))
    print('--- END CONFIG ---')
    print('')

    # Print derived paths
    print('--- DERIVED PATHS ---')
    ws = get_workspace_path(config)
    recipe = get_recipe_path(config)
    dwelling = get_dwelling_base(config)
    print('  Workspace:    %s' % ws)
    print('  Recipe:       %s' % recipe)
    print('  Dwelling:     %s' % dwelling)
    print('  Eng Summary:  %s' % ENG_SUMMARY_PATH)
    print('  Eng Cache:    %s' % ENG_CACHE_PATH)
    print('  Prod Summary: %s' % PROD_SUMMARY_PATH)
    print('--- END PATHS ---')
    print('')


# ===========================================================================
# AUTO-EXTRACT CONFIG — eliminates manual CONFIG editing
# ===========================================================================

def _detect_tester_model():
    """
    Detect tester model (IBIR vs IBIRHP) from SSDTesterCtrlr.xml.
    Returns dict with 'machine_model', 'hostname', or empty dict on failure.
    """
    result = {}
    try:
        result['hostname'] = socket.gethostname()
    except Exception:
        result['hostname'] = ''

    if ET is None:
        return result

    try:
        if os.path.isfile(TESTER_CTRL_XML):
            tree = ET.parse(TESTER_CTRL_XML)
            root = tree.getroot()
            # Look for machineModel element
            mm = root.find('.//machineModel')
            if mm is not None and mm.text:
                result['machine_model'] = mm.text.strip()
            # Look for clusterHostName
            ch = root.find('.//clusterHostName')
            if ch is not None and ch.text:
                result['cluster_host'] = ch.text.strip()
    except Exception:
        pass

    return result


def _find_newest_workspace():
    """
    Scan WORKSPACE_BASE for the most recently modified blade workspace folder.

    Workspace folder naming (from actual IBIR-0383 tester):
      {BLADE_SN}-{PRIMITIVE}-{SLOT}          e.g. ANSKR20215608072200-1-0
      {BLADE_SN}-{PRIMITIVE}-{SLOT}-{SUFFIX}  e.g. ANSKR20215608072200-1-0-MN2
      {BLADE_SN}-{PRIMITIVE}-{SLOT}_{SUFFIX}  e.g. ANSKR20215608072200-1-0_MUN2

    Blade SN prefixes: ANSKR, ENSKR, CNSKR
    Non-blade folders (skipped): 6500_Vintage_ABIT, 7500_Vintage_SF, etc.

    Returns (workspace_path, blade_sn) or (None, None).
    """
    if not os.path.isdir(WORKSPACE_BASE):
        log('Workspace base not found: %s' % WORKSPACE_BASE, 'WARNING')
        return None, None

    blade_folders = []
    for f in os.listdir(WORKSPACE_BASE):
        full = os.path.join(WORKSPACE_BASE, f)
        if not os.path.isdir(full):
            continue
        # Match blade SN prefixes only (skip product folders)
        starts_with_blade = False
        for prefix in BLADE_SN_PREFIXES:
            if f.startswith(prefix):
                starts_with_blade = True
                break
        if starts_with_blade and '-' in f:
            mtime = os.path.getmtime(full)
            blade_folders.append((f, mtime, full))

    if not blade_folders:
        log('No blade workspace folders found in %s' % WORKSPACE_BASE,
            'WARNING')
        return None, None

    # Sort by modification time, newest first
    blade_folders.sort(key=lambda x: x[1], reverse=True)

    newest_name = blade_folders[0][0]
    ws_path = blade_folders[0][2]

    # Extract blade SN (first part before first dash)
    # ANSKR20285608083800-1-3 -> ANSKR20285608083800
    blade_sn = newest_name.split('-')[0]

    log('Found newest workspace: %s' % newest_name)
    log('  Blade SN: %s' % blade_sn)
    if len(blade_folders) > 1:
        log('  (%d total blade workspaces found)' % len(blade_folders))
        # Show top 5 for reference
        for fname, mt, fp in blade_folders[:5]:
            mt_str = datetime.datetime.fromtimestamp(mt).strftime(
                '%Y-%m-%d %H:%M')
            log('    [%s] %s' % (mt_str, fname))

    return ws_path, blade_sn


def _find_recipe_in_workspace(ws_path):
    """
    Find the recipe XML file in {workspace}/recipe/.
    Returns the filename (not full path) or None.
    """
    recipe_dir = os.path.join(ws_path, 'recipe')
    if not os.path.isdir(recipe_dir):
        return None

    xmls = [f for f in os.listdir(recipe_dir)
            if f.lower().endswith('.xml')]

    if len(xmls) == 1:
        return xmls[0]
    elif len(xmls) > 1:
        log('Multiple recipe XMLs found: %s' % ', '.join(xmls), 'WARNING')
        # Return the most recently modified one
        xmls.sort(
            key=lambda f: os.path.getmtime(
                os.path.join(recipe_dir, f)
            ),
            reverse=True
        )
        return xmls[0]
    return None


def _detect_product_from_workspace(ws_path):
    """
    Detect product code from workspace folder structure.
    Looks in {workspace}/OS/mtfw_files/ for product subfolders.
    Returns product code string or None.
    """
    mtfw_dir = os.path.join(ws_path, 'OS', 'mtfw_files')
    if not os.path.isdir(mtfw_dir):
        return None

    # Product folders are typically numeric codes like '3610', '6550'
    products = [d for d in os.listdir(mtfw_dir)
                if os.path.isdir(os.path.join(mtfw_dir, d))]

    if len(products) == 1:
        return products[0]
    elif len(products) > 1:
        log('Multiple product folders found: %s' % ', '.join(products),
            'WARNING')
        return products[0]
    return None


def _detect_dwelling_drive():
    """
    Detect which drive letter has the Dwelling folder.
    Checks N: and M: drives.
    Returns drive letter (no colon) or None.
    """
    for letter in ['N', 'M']:
        dwelling_path = '%s:\\Dwelling' % letter
        if os.path.isdir(dwelling_path):
            log('Found Dwelling drive: %s:' % letter)
            return letter
    return None


def _list_fw_folders_on_dwelling(dwelling_drive, product):
    """
    List available FW folders on the Dwelling drive for a product.
    Returns list of folder names.
    """
    fw_base = '%s:\\Dwelling\\ENGOPS\\%s' % (dwelling_drive, product)
    if not os.path.isdir(fw_base):
        return []

    folders = []
    for d in os.listdir(fw_base):
        full = os.path.join(fw_base, d)
        if os.path.isdir(full):
            folders.append(d)
    return sorted(folders)


def _list_rev_folders_on_dwelling(dwelling_drive, product):
    """
    List available revision folders on the Dwelling drive.
    Returns list of folder names matching REV* pattern.
    """
    fw_base = '%s:\\Dwelling\\ENGOPS\\%s' % (dwelling_drive, product)
    if not os.path.isdir(fw_base):
        return []

    revs = []
    for d in os.listdir(fw_base):
        full = os.path.join(fw_base, d)
        if os.path.isdir(full) and d.upper().startswith('REV'):
            revs.append(d)
    return sorted(revs)


def _suggest_flow_from_recipe(recipe_xml):
    """
    Suggest a flow filename based on the recipe XML name.
    e.g., 'JacksonQLC_neosem_ABIT.xml' -> 'JacksonQLC_neosem_ABIT.flow'
    """
    if not recipe_xml:
        return ''
    base = recipe_xml.rsplit('.', 1)[0]
    return base + '.flow'


def _parse_checkout_xml(xml_path):
    """
    Parse a checkout profile XML to extract config values.
    Works with the XML format produced by checkout_orchestrator.

    Returns dict with extracted values or empty dict on failure.
    """
    if ET is None:
        log('xml.etree.ElementTree not available', 'WARNING')
        return {}

    if not os.path.isfile(xml_path):
        return {}

    result = {}
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()

        # Extract MID and LOT from MaterialInfo
        mat_info = root.find('.//MaterialInfo')
        if mat_info is not None:
            first_attr = mat_info.find('Attribute')
            if first_attr is not None:
                result['LOT_ID'] = first_attr.get('Lot', '')
                result['MID'] = first_attr.get('MID', '')

            # Extract all DUT locations
            dut_locs = []
            for attr in mat_info.findall('Attribute'):
                dl = attr.get('DutLocation', '')
                if dl:
                    dut_locs.append(dl)
            if dut_locs:
                result['DUT_LOCATIONS'] = dut_locs
                # Parse first DUT location for slot number
                # Format: "chassis,blade,slot" e.g., "1,3,1"
                try:
                    parts = dut_locs[0].split(',')
                    if len(parts) >= 3:
                        result['DUT_SLOT'] = int(parts[2])
                except (ValueError, IndexError):
                    pass

        # Extract RecipeFile
        rf = root.find('RecipeFile')
        if rf is not None and rf.text:
            recipe_text = rf.text.strip()
            # May be relative path like "RECIPE\PEREGRINEION_NEOSEM_ABIT.XML"
            result['RECIPE_XML'] = os.path.basename(recipe_text)

        # Extract TestJobArchive (contains product/revision info)
        tja = root.find('TestJobArchive')
        if tja is not None and tja.text:
            result['TEST_JOB_ARCHIVE'] = tja.text.strip()

        # Extract TempTraveler attributes
        tt = root.find('TempTraveler')
        if tt is not None:
            for attr in tt.findall('Attribute'):
                attr_name = (attr.get('attr') or attr.get('Name', '')).upper()
                attr_val = attr.get('value') or attr.get('Value', '')
                if attr_name == 'STEP':
                    result['STEP'] = attr_val
                elif attr_name == 'MOD_TST_SWR_NUMBER':
                    result['REV'] = attr_val

        # Extract AddtionalFileFolder (file copy mappings)
        aff = root.find('AddtionalFileFolder')
        if aff is not None:
            file_copies = []
            for file_elem in aff.findall('File'):
                src = file_elem.get('source', '')
                dst = file_elem.get('dest', '')
                if src and dst:
                    file_copies.append({'source': src, 'dest': dst})
            if file_copies:
                result['FILE_COPIES'] = file_copies

    except Exception as e:
        log('Error parsing checkout XML: %s' % str(e), 'WARNING')

    return result


def _load_last_config():
    """
    Load last-used config values from disk.
    Returns dict or empty dict if not found.
    """
    try:
        if os.path.isfile(LAST_CONFIG_PATH):
            with open(LAST_CONFIG_PATH, 'r') as f:
                data = json.load(f)
            log('Loaded last-used config from %s' % LAST_CONFIG_PATH)
            return data
    except Exception:
        pass
    return {}


def _save_last_config(config):
    """
    Save config values to disk for next-time defaults.
    Only saves the engineer-entered values (not auto-extracted ones).
    """
    save_keys = [
        'FW_SRC', 'FW_DEST', 'FW_SUB', 'REV', 'FLOW_FILE',
        'DWELLING_DRIVE', 'PARAM_FOLDER', 'PRODUCT',
        'TIMEOUT_HOURS', 'POLL_INTERVAL_SEC',
        'RESULTS_BASE', 'TEAMS_WEBHOOK_URL',
    ]
    data = {}
    for k in save_keys:
        if k in config and config[k]:
            data[k] = config[k]

    try:
        save_dir = os.path.dirname(LAST_CONFIG_PATH)
        if not os.path.isdir(save_dir):
            os.makedirs(save_dir)
        with open(LAST_CONFIG_PATH, 'w') as f:
            json.dump(data, f, indent=2)
        log('Saved config defaults to %s' % LAST_CONFIG_PATH)
    except Exception as e:
        log('Could not save config defaults: %s' % str(e), 'WARNING')


def _prompt_with_default(prompt_text, default=''):
    """
    Prompt engineer for a value with a default.
    Shows [default] hint. Returns entered value or default.
    """
    if default:
        hint = ' [%s]' % default
    else:
        hint = ''
    try:
        answer = raw_input(
            '[INPUT] %s%s: ' % (prompt_text, hint)
        ).strip()
    except EOFError:
        answer = ''
    if not answer:
        return default
    return answer


def auto_extract_config(xml_path=None, mid=None, lot_id=None,
                        dut_slot=None, blade_sn=None):
    """
    Auto-extract CONFIG values from the workspace, checkout XML,
    and tester environment. Prompts engineer for values that cannot
    be auto-detected.

    Args:
        xml_path : str — path to checkout profile XML (optional)
        mid      : str — MID value (if already known from watcher)
        lot_id   : str — LOT ID (if already known from watcher)
        dut_slot : int — DUT slot number (if already known)
        blade_sn : str — Blade serial number (if already known)

    Returns:
        dict — complete CONFIG ready for use by checkout steps
    """
    log_step(0, 'Auto-Extract Configuration')

    config = {}
    last_config = _load_last_config()

    # ---------------------------------------------------------------
    # 0a) Detect tester info
    # ---------------------------------------------------------------
    log('--- 0a) Detect Tester Info ---')
    tester_info = _detect_tester_model()
    config['TESTER_ID'] = tester_info.get('hostname', '')
    if tester_info.get('machine_model'):
        log('  Tester model: %s' % tester_info['machine_model'])
    if config['TESTER_ID']:
        log('  Tester ID: %s' % config['TESTER_ID'])

    # ---------------------------------------------------------------
    # 0b) Parse checkout XML (if provided)
    # ---------------------------------------------------------------
    xml_data = {}
    if xml_path:
        log('--- 0b) Parse Checkout XML ---')
        log('  XML: %s' % xml_path)
        xml_data = _parse_checkout_xml(xml_path)
        if xml_data:
            log('  Extracted %d fields from XML' % len(xml_data))
            for k, v in sorted(xml_data.items()):
                if k == 'FILE_COPIES':
                    log('    %s: %d file copy rules' % (k, len(v)))
                elif k == 'DUT_LOCATIONS':
                    log('    %s: %s' % (k, ', '.join(v)))
                else:
                    log('    %s: %s' % (k, v))
        else:
            log('  No fields extracted from XML', 'WARNING')

    # ---------------------------------------------------------------
    # 0c) Find workspace
    # ---------------------------------------------------------------
    log('--- 0c) Find Workspace ---')
    ws_path = None

    if blade_sn:
        # If blade_sn provided, find the newest matching workspace
        # Pattern: {BLADE_SN}-{PRIMITIVE}-{SLOT}[optional suffix]
        if os.path.isdir(WORKSPACE_BASE):
            matches = []
            for f in os.listdir(WORKSPACE_BASE):
                if f.startswith(blade_sn + '-') and os.path.isdir(
                    os.path.join(WORKSPACE_BASE, f)
                ):
                    fp = os.path.join(WORKSPACE_BASE, f)
                    matches.append((f, os.path.getmtime(fp), fp))
            if matches:
                matches.sort(key=lambda x: x[1], reverse=True)
                ws_path = matches[0][2]
                log('  Workspace found (from blade_sn): %s' % ws_path)
            else:
                log('  No workspace matching blade_sn=%s' % blade_sn,
                    'WARNING')

    if not ws_path:
        ws_path, detected_blade_sn = _find_newest_workspace()
        if ws_path:
            if not blade_sn:
                blade_sn = detected_blade_sn
            log('  Workspace found (newest): %s' % ws_path)
        else:
            log('  No workspace found!', 'ERROR')
            # Prompt for blade SN
            blade_sn = _prompt_with_default(
                'Enter blade serial number',
                blade_sn or ''
            )
            if blade_sn:
                # Try to find any matching folder
                if os.path.isdir(WORKSPACE_BASE):
                    for f in os.listdir(WORKSPACE_BASE):
                        if f.startswith(blade_sn) and os.path.isdir(
                            os.path.join(WORKSPACE_BASE, f)
                        ):
                            ws_path = os.path.join(WORKSPACE_BASE, f)
                            break

    config['BLADE_SN'] = blade_sn or ''

    # ---------------------------------------------------------------
    # 0d) Extract recipe
    # ---------------------------------------------------------------
    log('--- 0d) Find Recipe ---')
    recipe_xml = xml_data.get('RECIPE_XML', '')

    if not recipe_xml and ws_path:
        recipe_xml = _find_recipe_in_workspace(ws_path) or ''

    if recipe_xml:
        log('  Recipe: %s' % recipe_xml)
    else:
        log('  Recipe not found', 'WARNING')
        recipe_xml = _prompt_with_default('Enter recipe XML filename')

    config['RECIPE_XML'] = recipe_xml

    # ---------------------------------------------------------------
    # 0e) Detect product
    # ---------------------------------------------------------------
    log('--- 0e) Detect Product ---')
    product = ''

    if ws_path:
        product = _detect_product_from_workspace(ws_path) or ''

    if not product:
        product = last_config.get('PRODUCT', '')

    if product:
        log('  Product: %s' % product)
    else:
        product = _prompt_with_default(
            'Enter product code (e.g., 3610, 6550)',
            last_config.get('PRODUCT', '')
        )

    config['PRODUCT'] = product

    # ---------------------------------------------------------------
    # 0f) Parse playground queue for MID/LOT/JIRA (FIX 3)
    # ---------------------------------------------------------------
    log('--- 0f) Parse Playground Queue ---')
    pq_mid = ''
    pq_lot = ''
    pq_jira = ''
    pq_tester = ''
    if os.path.isdir(PLAYGROUND_QUEUE_PATH):
        pq_files = os.listdir(PLAYGROUND_QUEUE_PATH)
        # Find Profile_*.xml files (contain MID and LOT in filename)
        profile_files = [
            f for f in pq_files
            if f.startswith('Profile_') and f.lower().endswith('.xml')
        ]
        if profile_files:
            # Sort by modification time, newest first
            profile_files.sort(
                key=lambda f: os.path.getmtime(
                    os.path.join(PLAYGROUND_QUEUE_PATH, f)
                ),
                reverse=True
            )
            newest_profile = profile_files[0]
            log('  Newest playground profile: %s' % newest_profile)

            # Parse filename:
            # Profile_TSESSD-14270_IBIR-0383_ABIT_T1B21FR5T_JAANTJ4001_20260401_104558.xml
            name_no_ext = newest_profile.replace('.xml', '')
            parts = name_no_ext.split('_')
            # parts[0] = 'Profile'
            # parts[1] = 'TSESSD-14270'    (JIRA)
            # parts[2] = 'IBIR-0383'       (TESTER)
            # parts[3] = 'ABIT'            (PLATFORM)
            # parts[4] = 'T1B21FR5T'       (MID)
            # parts[5] = 'JAANTJ4001'      (LOT)
            # parts[6] = '20260401'        (DATE)
            # parts[7] = '104558'          (TIME)
            if len(parts) >= 8:
                pq_jira = parts[1]
                pq_tester = parts[2]
                pq_mid = parts[4]
                pq_lot = parts[5]
                log('  From profile filename:')
                log('    JIRA:    %s' % pq_jira)
                log('    Tester:  %s' % pq_tester)
                log('    MID:     %s' % pq_mid)
                log('    LOT:     %s' % pq_lot)
            elif len(parts) >= 6:
                # Shorter format — try best effort
                pq_jira = parts[1]
                pq_tester = parts[2]
                log('  Partial parse: JIRA=%s Tester=%s' % (
                    pq_jira, pq_tester))
        else:
            log('  No Profile_*.xml files in playground queue')
    else:
        log('  Playground queue not found: %s' % PLAYGROUND_QUEUE_PATH,
            'WARNING')

    # Update tester ID from playground queue if not already set
    if pq_tester and not config.get('TESTER_ID'):
        config['TESTER_ID'] = pq_tester

    # ---------------------------------------------------------------
    # 0g) Set MID, LOT_ID, DUT_SLOT from args, XML, or playground queue
    # ---------------------------------------------------------------
    log('--- 0g) Set MID / LOT / DUT ---')

    config['MID'] = mid or xml_data.get('MID', '') or pq_mid
    config['LOT_ID'] = lot_id or xml_data.get('LOT_ID', '') or pq_lot

    if dut_slot is not None:
        config['DUT_SLOT'] = dut_slot
    elif xml_data.get('DUT_SLOT') is not None:
        config['DUT_SLOT'] = xml_data['DUT_SLOT']
    else:
        config['DUT_SLOT'] = 0

    if not config['MID']:
        config['MID'] = _prompt_with_default('Enter MID')
    if not config['LOT_ID']:
        config['LOT_ID'] = _prompt_with_default('Enter LOT ID')
    if not config['DUT_SLOT']:
        slot_str = _prompt_with_default('Enter DUT slot number', '1')
        try:
            config['DUT_SLOT'] = int(slot_str)
        except ValueError:
            config['DUT_SLOT'] = 1

    log('  MID: %s' % config['MID'])
    log('  LOT ID: %s' % config['LOT_ID'])
    log('  DUT Slot: %s' % config['DUT_SLOT'])

    # ---------------------------------------------------------------
    # 0g) Detect Dwelling drive
    # ---------------------------------------------------------------
    log('--- 0g) Detect Dwelling Drive ---')
    dwelling = _detect_dwelling_drive()
    if not dwelling:
        dwelling = last_config.get('DWELLING_DRIVE', '')
    if not dwelling:
        dwelling = _prompt_with_default(
            'Enter Dwelling drive letter (N or M)', 'N'
        )
    config['DWELLING_DRIVE'] = dwelling

    # ---------------------------------------------------------------
    # 0h) Prompt for FW source/dest (from JIRA ticket)
    # ---------------------------------------------------------------
    log('--- 0h) FW Configuration ---')

    # Show available FW folders if possible
    if config['DWELLING_DRIVE'] and config['PRODUCT']:
        fw_folders = _list_fw_folders_on_dwelling(
            config['DWELLING_DRIVE'], config['PRODUCT']
        )
        if fw_folders:
            log('  Available FW folders on %s:\\Dwelling\\ENGOPS\\%s:' % (
                config['DWELLING_DRIVE'], config['PRODUCT']))
            for folder in fw_folders[:20]:
                log('    %s/' % folder)
            if len(fw_folders) > 20:
                log('    ... and %d more' % (len(fw_folders) - 20))

    # Check if XML has AddtionalFileFolder (auto file copies)
    if xml_data.get('FILE_COPIES'):
        log('  XML defines %d file copy rules (AddtionalFileFolder)' %
            len(xml_data['FILE_COPIES']))
        log('  FW source/dest will be handled by XML file copy rules.')
        config['FW_SRC'] = ''
        config['FW_DEST'] = ''
        config['FW_SUB'] = ''
        config['FILE_COPIES'] = xml_data['FILE_COPIES']
    else:
        # Prompt for FW source/dest
        config['FW_SRC'] = _prompt_with_default(
            'Enter FW source folder (e.g., VBZZ0007)',
            last_config.get('FW_SRC', '')
        )
        config['FW_DEST'] = _prompt_with_default(
            'Enter FW dest folder (e.g., VBZZ0005)',
            last_config.get('FW_DEST', config['FW_SRC'])
        )
        config['FW_SUB'] = _prompt_with_default(
            'Enter FW sub-folder (or Enter to skip)',
            last_config.get('FW_SUB', '')
        )

    # ---------------------------------------------------------------
    # 0i) Prompt for revision and flow file
    # ---------------------------------------------------------------
    log('--- 0i) Revision & Flow File ---')

    rev = xml_data.get('REV', '')
    if not rev:
        # Show available REV folders
        if config['DWELLING_DRIVE'] and config['PRODUCT']:
            rev_folders = _list_rev_folders_on_dwelling(
                config['DWELLING_DRIVE'], config['PRODUCT']
            )
            if rev_folders:
                log('  Available REV folders:')
                for rf in rev_folders[:10]:
                    log('    %s/' % rf)

        rev = _prompt_with_default(
            'Enter revision folder (e.g., REV178NH)',
            last_config.get('REV', '')
        )
    else:
        log('  Revision from XML: %s' % rev)

    config['REV'] = rev

    # Flow file
    flow_default = _suggest_flow_from_recipe(config['RECIPE_XML'])
    if not flow_default:
        flow_default = last_config.get('FLOW_FILE', '')

    config['FLOW_FILE'] = _prompt_with_default(
        'Enter flow file name',
        flow_default
    )

    # ---------------------------------------------------------------
    # 0j) Parameter folder (optional)
    # ---------------------------------------------------------------
    config['PARAM_FOLDER'] = _prompt_with_default(
        'Enter parameter folder (or Enter to skip)',
        last_config.get('PARAM_FOLDER', '')
    )

    # ---------------------------------------------------------------
    # 0k) Monitoring and results settings
    # ---------------------------------------------------------------
    log('--- 0k) Monitoring & Results ---')
    config['TIMEOUT_HOURS'] = int(last_config.get('TIMEOUT_HOURS', 8))
    config['POLL_INTERVAL_SEC'] = int(
        last_config.get('POLL_INTERVAL_SEC', 30)
    )
    config['RESULTS_BASE'] = last_config.get(
        'RESULTS_BASE', r'P:\temp\checkout_results'
    )
    config['TEAMS_WEBHOOK_URL'] = last_config.get('TEAMS_WEBHOOK_URL', '')

    # ---------------------------------------------------------------
    # 0l) Show complete config for confirmation
    # ---------------------------------------------------------------
    print('')
    print('=' * 60)
    print('  EXTRACTED + ENTERED CONFIGURATION')
    print('=' * 60)
    print('  AUTO-EXTRACTED:')
    print('    Workspace:   %s' % (ws_path or 'NOT FOUND'))
    print('    Blade SN:    %s' % config['BLADE_SN'])
    print('    Tester ID:   %s' % config['TESTER_ID'])
    print('    DUT Slot:    %s' % config['DUT_SLOT'])
    print('    MID:         %s' % config['MID'])
    print('    LOT ID:      %s' % config['LOT_ID'])
    print('    Recipe:      %s' % config['RECIPE_XML'])
    print('    Product:     %s' % config['PRODUCT'])
    print('')
    print('  ENGINEER-ENTERED:')
    if config.get('FILE_COPIES'):
        print('    File Copies: %d rules from XML' %
              len(config['FILE_COPIES']))
    else:
        print('    FW Source:   %s' % config.get('FW_SRC', ''))
        print('    FW Dest:     %s' % config.get('FW_DEST', ''))
        print('    FW Sub:      %s' % (config.get('FW_SUB') or 'N/A'))
    print('    Revision:    %s' % config['REV'])
    print('    Flow File:   %s' % config['FLOW_FILE'])
    print('    Dwelling:    %s:' % config['DWELLING_DRIVE'])
    print('    Param Folder:%s' % (config['PARAM_FOLDER'] or 'N/A'))
    print('=' * 60)
    print('')

    if not ask_user('Configuration correct? Proceed?'):
        log('Configuration rejected by user.')
        return None

    # Save for next time
    _save_last_config(config)

    return config


def run_post_playground(xml_path=None, mid=None, lot_id=None,
                        dut_slot=None, blade_sn=None,
                        config_override=None, interactive=True):
    """
    Entry point for running the post-playground checkout steps.
    Called by checkout_watcher2.py after playground creation succeeds.

    This function:
      1. Auto-extracts config (or uses config_override)
      2. Validates playground
      3. Copies test files
      4. Runs test via H4 GUI
      5. Monitors test completion
      6. Collects results and notifies

    Args:
        xml_path        : str — checkout profile XML path (optional)
        mid             : str — MID (from watcher)
        lot_id          : str — LOT ID (from watcher)
        dut_slot        : int — DUT slot (from watcher)
        blade_sn        : str — Blade SN (from watcher)
        config_override : dict — pre-built CONFIG dict (skips auto-extract)
        interactive     : bool — if False, skip user prompts (for watcher mode)

    Returns:
        dict — test result with 'completed', 'result', 'duration_minutes', etc.
    """
    print_banner()

    # Build config
    if config_override:
        config = config_override
        log('Using provided config override')
        print_config(config)
    else:
        config = auto_extract_config(
            xml_path=xml_path,
            mid=mid,
            lot_id=lot_id,
            dut_slot=dut_slot,
            blade_sn=blade_sn,
        )
        if config is None:
            log('Config extraction failed or cancelled.', 'ERROR')
            return {
                'completed': False,
                'result': 'CONFIG_FAILED',
                'duration_minutes': 0,
                'error_message': 'Configuration extraction failed',
            }
        print_config(config)

    # Track overall timing
    overall_start = time.time()

    # Step 1: Validate Playground
    try:
        if not validate_playground(config):
            log('Playground validation failed.', 'ERROR')
            if interactive and not ask_user('Continue anyway?', 'n'):
                return {
                    'completed': False,
                    'result': 'VALIDATION_FAILED',
                    'duration_minutes': 0,
                    'error_message': 'Playground validation failed',
                }
    except Exception as e:
        log('Step 1 error: %s' % str(e), 'ERROR')
        traceback.print_exc()
        if interactive and not ask_user('Continue despite error?', 'n'):
            return {
                'completed': False,
                'result': 'VALIDATION_ERROR',
                'duration_minutes': 0,
                'error_message': str(e),
            }

    # Step 2: Copy Test Files
    try:
        # If XML has FILE_COPIES, use those instead of Dwelling copy
        if config.get('FILE_COPIES'):
            if not _copy_files_from_xml(config):
                log('XML file copy failed.', 'ERROR')
                if interactive and not ask_user(
                    'Continue to test execution anyway?', 'n'
                ):
                    return {
                        'completed': False,
                        'result': 'COPY_FAILED',
                        'duration_minutes': 0,
                        'error_message': 'XML file copy failed',
                    }
        else:
            if not copy_test_files(config):
                log('File copy failed.', 'ERROR')
                if interactive and not ask_user(
                    'Continue to test execution anyway?', 'n'
                ):
                    return {
                        'completed': False,
                        'result': 'COPY_FAILED',
                        'duration_minutes': 0,
                        'error_message': 'File copy failed',
                    }
    except Exception as e:
        log('Step 2 error: %s' % str(e), 'ERROR')
        traceback.print_exc()
        if interactive and not ask_user('Continue despite error?', 'n'):
            return {
                'completed': False,
                'result': 'COPY_ERROR',
                'duration_minutes': 0,
                'error_message': str(e),
            }

    # Step 3: Run Test via H4 GUI
    try:
        if not run_test_h4(config):
            log('Test start failed.', 'ERROR')
            if interactive and ask_user(
                'Skip to monitoring (test started manually)?', 'n'
            ):
                log('Proceeding to monitoring...')
            else:
                return {
                    'completed': False,
                    'result': 'TEST_START_FAILED',
                    'duration_minutes': 0,
                    'error_message': 'Could not start test via H4',
                }
    except Exception as e:
        log('Step 3 error: %s' % str(e), 'ERROR')
        traceback.print_exc()
        if interactive and ask_user(
            'Skip to monitoring (test started manually)?', 'n'
        ):
            log('Proceeding to monitoring...')
        else:
            return {
                'completed': False,
                'result': 'TEST_START_ERROR',
                'duration_minutes': 0,
                'error_message': str(e),
            }

    # Step 4: Monitor Test Completion
    test_result = {
        'completed': False,
        'duration_minutes': 0,
        'tsum_file': None,
        'result_folder': None,
        'detection_method': None,
        'result': 'UNKNOWN',
        'error_message': '',
        'start_time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'end_time': '',
    }

    try:
        test_result = monitor_test_completion(config)
    except Exception as e:
        log('Step 4 error: %s' % str(e), 'ERROR')
        traceback.print_exc()
        test_result['result'] = 'MONITOR_ERROR'
        test_result['error_message'] = str(e)
        test_result['end_time'] = datetime.datetime.now().strftime(
            '%Y-%m-%d %H:%M:%S')

    # Step 5: Collect Results
    results_dir = None
    try:
        results_dir = collect_results(config, test_result)
    except Exception as e:
        log('Step 5 error: %s' % str(e), 'ERROR')
        traceback.print_exc()

    # Step 6: Send Notification
    try:
        send_notification(config, test_result, results_dir)
    except Exception as e:
        log('Step 6 error: %s' % str(e), 'ERROR')
        traceback.print_exc()

    # Final Summary
    overall_elapsed = time.time() - overall_start
    print('')
    print('=' * 60)
    print('  CHECKOUT AUTOMATION COMPLETE')
    print('=' * 60)
    print('  MID:          %s' % config.get('MID', ''))
    print('  Tester:       %s' % config.get('TESTER_ID', ''))
    print('  Test Result:  %s' % test_result.get('result', 'UNKNOWN'))
    print('  Duration:     %s' % format_duration(
        test_result.get('duration_minutes', 0) * 60))
    print('  Total Script: %s' % format_duration(overall_elapsed))
    if results_dir:
        print('  Results:      %s' % results_dir)
    if test_result.get('result_folder'):
        print('  Result Dir:   %s' % test_result['result_folder'])
    if test_result.get('detection_method'):
        print('  Detected By:  %s' % test_result['detection_method'])
    if test_result.get('error_message'):
        print('  Error:        %s' % test_result['error_message'])
    print('=' * 60)
    print('')

    return test_result


def _copy_files_from_xml(config):
    """
    Copy files using the AddtionalFileFolder rules from the checkout XML.
    Each rule has 'source' (network path) and 'dest' (relative to workspace).
    Returns True on success.
    """
    log_step(2, 'Copy Files (from XML AddtionalFileFolder)')

    ws_path = get_workspace_path(config)
    file_copies = config.get('FILE_COPIES', [])
    total_files = 0
    total_bytes = 0
    failed = 0

    for i, rule in enumerate(file_copies):
        src = rule['source']
        dst_rel = rule['dest']
        dst = os.path.join(ws_path, dst_rel)

        log('--- Rule %d/%d ---' % (i + 1, len(file_copies)))
        log('  FROM: %s' % src)
        log('  TO:   %s' % dst)

        if not os.path.isdir(src):
            # Check if it's a file
            if os.path.isfile(src):
                try:
                    dst_dir = os.path.dirname(dst)
                    if not os.path.isdir(dst_dir):
                        os.makedirs(dst_dir)
                    shutil.copy2(src, dst)
                    fsize = os.path.getsize(src)
                    total_files += 1
                    total_bytes += fsize
                    log('  Copied file: %s (%s)' % (
                        os.path.basename(src), format_size(fsize)))
                except (IOError, OSError) as e:
                    log('  FAILED: %s' % str(e), 'ERROR')
                    failed += 1
            else:
                log('  Source NOT FOUND: %s' % src, 'WARNING')
                failed += 1
        else:
            try:
                if not os.path.isdir(dst):
                    os.makedirs(dst)
                fc, fb = copy_tree_contents(src, dst)
                total_files += fc
                total_bytes += fb
                log('  Copied %d files (%s)' % (fc, format_size(fb)))
            except (IOError, OSError) as e:
                log('  FAILED: %s' % str(e), 'ERROR')
                failed += 1

    print('')
    log('XML file copy complete!')
    log('  Total files copied: %d' % total_files)
    log('  Total size:         %s' % format_size(total_bytes))
    if failed:
        log('  Failed rules:       %d' % failed, 'WARNING')

    return failed == 0 or total_files > 0


# ===========================================================================
# STEP 1: VALIDATE PLAYGROUND
# ===========================================================================

def validate_playground(config):
    """
    Validate that the playground was created successfully.
    Checks workspace folder structure and recipe XML existence.
    Returns True if valid, False otherwise.
    """
    log_step(1, 'Validate Playground')

    ws_path = get_workspace_path(config)
    log('Checking workspace: %s' % ws_path)

    all_ok = True
    checks = []

    # Check 1: Workspace folder exists
    if os.path.isdir(ws_path):
        log('  [OK] Workspace folder exists')
        checks.append(('Workspace folder', True))
    else:
        log('  [FAIL] Workspace folder NOT FOUND: %s' % ws_path, 'ERROR')
        checks.append(('Workspace folder', False))
        all_ok = False

    # Check 2: Required subfolders
    subfolders = [
        os.path.join('OS', 'mtfw_files'),
        os.path.join('OS', 'masterflows'),
        os.path.join('OS', 'parameter_files'),
        'recipe',
    ]

    for sf in subfolders:
        sf_path = os.path.join(ws_path, sf)
        if os.path.isdir(sf_path):
            log('  [OK] Subfolder exists: %s' % sf)
            checks.append((sf, True))
        else:
            log('  [FAIL] Subfolder NOT FOUND: %s' % sf_path, 'ERROR')
            checks.append((sf, False))
            all_ok = False

    # Check 3: Recipe XML exists
    recipe_path = get_recipe_path(config)
    if os.path.isfile(recipe_path):
        rsize = os.path.getsize(recipe_path)
        log('  [OK] Recipe XML exists: %s (%s)' % (
            config['RECIPE_XML'], format_size(rsize)))
        checks.append(('Recipe XML', True))
    else:
        log('  [FAIL] Recipe XML NOT FOUND: %s' % recipe_path, 'ERROR')
        checks.append(('Recipe XML', False))
        all_ok = False

    # Print workspace tree (if it exists)
    if os.path.isdir(ws_path):
        log('Workspace folder structure:')
        for root, dirs, files in os.walk(ws_path):
            level = root.replace(ws_path, '').count(os.sep)
            indent = '  ' * (level + 1)
            basename = os.path.basename(root)
            if level == 0:
                basename = os.path.basename(ws_path)
            fcount = len(files)
            if fcount > 0:
                print('%s%s/ (%d files)' % (indent, basename, fcount))
            else:
                print('%s%s/' % (indent, basename))
            # Only go 3 levels deep
            if level >= 3:
                dirs[:] = []

    # Summary
    passed = sum(1 for _, ok in checks if ok)
    total = len(checks)
    print('')
    log('Validation: %d/%d checks passed' % (passed, total))

    if not all_ok:
        log('Playground validation FAILED!', 'WARNING')
        if not ask_user('Continue anyway?', 'n'):
            log('Aborting per user request.', 'ERROR')
            return False
        log('Continuing despite validation failures...')

    return True


# ===========================================================================
# STEP 2: COPY TEST FILES
# ===========================================================================

def copy_test_files(config):
    """
    Copy FW binaries, flow file, and parameter files from Dwelling
    drive into the workspace. Returns True on success.
    """
    log_step(2, 'Copy Test Files to Workspace')

    ws_path = get_workspace_path(config)
    dwelling_base = get_dwelling_base(config)
    total_files = 0
    total_bytes = 0

    # ---------------------------------------------------------------
    # 2a) Copy FW binaries
    # ---------------------------------------------------------------
    log('--- 2a) Copy FW Binaries ---')
    fw_src = os.path.join(dwelling_base, config['FW_SRC'])
    fw_dst = os.path.join(
        ws_path, 'OS', 'mtfw_files',
        config['PRODUCT'], config['FW_DEST']
    )

    log('  FROM: %s' % fw_src)
    log('  TO:   %s' % fw_dst)

    if not os.path.isdir(fw_src):
        log('  FW source folder NOT FOUND: %s' % fw_src, 'ERROR')
        if not ask_user('Skip FW copy and continue?', 'n'):
            return False
    else:
        try:
            if not os.path.isdir(fw_dst):
                os.makedirs(fw_dst)
            fc, fb = copy_tree_contents(fw_src, fw_dst)
            total_files += fc
            total_bytes += fb
            log('  Copied %d FW files (%s)' % (fc, format_size(fb)))
        except (IOError, OSError) as e:
            log('  FW copy FAILED: %s' % str(e), 'ERROR')
            if ask_user('Retry FW copy?'):
                try:
                    fc, fb = copy_tree_contents(fw_src, fw_dst)
                    total_files += fc
                    total_bytes += fb
                    log('  Retry: Copied %d FW files (%s)' % (
                        fc, format_size(fb)))
                except (IOError, OSError) as e2:
                    log('  Retry FAILED: %s' % str(e2), 'ERROR')
                    if not ask_user('Continue without FW files?', 'n'):
                        return False

    # ---------------------------------------------------------------
    # 2b) Copy FW sub-folder (if configured)
    # ---------------------------------------------------------------
    if config['FW_SUB']:
        log('--- 2b) Copy FW Sub-folder ---')
        fw_sub_src = os.path.join(
            dwelling_base,
            '%s_%s' % (config['FW_SRC'], config['FW_SUB'])
        )
        fw_sub_dst = os.path.join(fw_dst, config['FW_SUB'])

        log('  FROM: %s' % fw_sub_src)
        log('  TO:   %s' % fw_sub_dst)

        if not os.path.isdir(fw_sub_src):
            log('  FW sub-folder NOT FOUND: %s' % fw_sub_src, 'WARNING')
            log('  Skipping FW sub-folder copy.')
        else:
            try:
                if not os.path.isdir(fw_sub_dst):
                    os.makedirs(fw_sub_dst)
                fc, fb = copy_tree_contents(fw_sub_src, fw_sub_dst)
                total_files += fc
                total_bytes += fb
                log('  Copied %d FW sub-folder files (%s)' % (
                    fc, format_size(fb)))
            except (IOError, OSError) as e:
                log('  FW sub-folder copy FAILED: %s' % str(e), 'ERROR')
                if not ask_user('Continue without FW sub-folder?'):
                    return False
    else:
        log('--- 2b) FW Sub-folder: SKIPPED (not configured) ---')

    # ---------------------------------------------------------------
    # 2c) Copy flow file
    # ---------------------------------------------------------------
    log('--- 2c) Copy Flow File ---')
    flow_src = os.path.join(
        dwelling_base, config['REV'], config['FLOW_FILE']
    )
    flow_dst_dir = os.path.join(ws_path, 'OS', 'masterflows')
    flow_dst = os.path.join(flow_dst_dir, config['FLOW_FILE'])

    log('  FROM: %s' % flow_src)
    log('  TO:   %s' % flow_dst)

    if not os.path.isfile(flow_src):
        log('  Flow file NOT FOUND: %s' % flow_src, 'ERROR')
        if not ask_user('Continue without flow file?', 'n'):
            return False
    else:
        try:
            if not os.path.isdir(flow_dst_dir):
                os.makedirs(flow_dst_dir)
            shutil.copy2(flow_src, flow_dst)
            fsize = os.path.getsize(flow_src)
            total_files += 1
            total_bytes += fsize
            log('  Copied flow file: %s (%s)' % (
                config['FLOW_FILE'], format_size(fsize)))
        except (IOError, OSError) as e:
            log('  Flow file copy FAILED: %s' % str(e), 'ERROR')
            if not ask_user('Continue without flow file?', 'n'):
                return False

    # ---------------------------------------------------------------
    # 2d) Copy parameter files (if configured)
    # ---------------------------------------------------------------
    if config['PARAM_FOLDER']:
        log('--- 2d) Copy Parameter Files ---')
        param_src = os.path.join(
            dwelling_base, config['FW_SRC'], config['PARAM_FOLDER']
        )
        param_dst = os.path.join(ws_path, 'OS', 'parameter_files')

        log('  FROM: %s' % param_src)
        log('  TO:   %s' % param_dst)

        if not os.path.isdir(param_src):
            log('  Parameter folder NOT FOUND: %s' % param_src, 'WARNING')
            log('  Skipping parameter file copy.')
        else:
            try:
                if not os.path.isdir(param_dst):
                    os.makedirs(param_dst)
                fc, fb = copy_tree_contents(param_src, param_dst)
                total_files += fc
                total_bytes += fb
                log('  Copied %d parameter files (%s)' % (
                    fc, format_size(fb)))
            except (IOError, OSError) as e:
                log('  Parameter file copy FAILED: %s' % str(e), 'ERROR')
                if not ask_user('Continue without parameter files?'):
                    return False
    else:
        log('--- 2d) Parameter Files: SKIPPED (not configured) ---')

    # ---------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------
    print('')
    log('File copy complete!')
    log('  Total files copied: %d' % total_files)
    log('  Total size:         %s' % format_size(total_bytes))

    # Verify destination
    log('Verifying destination workspace...')
    for subdir in ['OS', 'recipe']:
        check_path = os.path.join(ws_path, subdir)
        fc, fb = count_files_in_dir(check_path)
        log('  %s: %d files (%s)' % (subdir, fc, format_size(fb)))

    return True


# ===========================================================================
# STEP 3: RUN TEST VIA H4 GUI
# ===========================================================================

def _try_import_pywinauto():
    """Try to import pywinauto. Returns the module or None."""
    try:
        import pywinauto
        return pywinauto
    except ImportError:
        log('pywinauto is not installed!', 'WARNING')
        log('Install with: pip install pywinauto==0.6.3', 'WARNING')
        return None


def _try_import_pyautogui():
    """Try to import pyautogui. Returns the module or None."""
    try:
        import pyautogui
        return pyautogui
    except ImportError:
        log('pyautogui is not installed (fallback unavailable)', 'WARNING')
        return None


def _find_h4_window(pywinauto_mod):
    """
    Find the H4 GUI window using pywinauto.
    Tries win32 backend first (confirmed working from inspection).
    Returns (app, main_window) or (None, None).
    """
    Application = pywinauto_mod.Application

    # Try win32 backend first (confirmed from inspection JSON)
    for backend in ['win32']:
        try:
            app = Application(backend=backend)
            app.connect(title_re='H4.*', class_name_re='WindowsForms10.*')
            main_win = app.window(title_re='H4.*')
            # Verify window exists
            if main_win.exists():
                log('Connected to H4 GUI via %s backend' % backend)
                log('  Window title: %s' % main_win.window_text())
                return app, main_win
        except Exception as e:
            log('  %s backend failed: %s' % (backend, str(e)))

    return None, None


def _launch_h4():
    """Launch H4.exe if not already running."""
    log('Attempting to launch H4 GUI...')
    if not os.path.isfile(H4_EXE_PATH):
        log('H4.exe not found at: %s' % H4_EXE_PATH, 'ERROR')
        return False

    try:
        os.startfile(H4_EXE_PATH)
        log('H4.exe launched. Waiting for it to load...')
        # H4 can take up to 5 minutes to fully load with blade refresh
        log('  (Blade refresh may take up to 5 minutes)')
        for i in range(60):  # Wait up to 5 minutes (60 * 5s)
            time.sleep(5)
            if i % 6 == 0:  # Every 30 seconds
                log('  Waiting... %d seconds elapsed' % ((i + 1) * 5))
            # Check if H4 window appeared
            try:
                pywinauto_mod = _try_import_pywinauto()
                if pywinauto_mod:
                    app, win = _find_h4_window(pywinauto_mod)
                    if win is not None:
                        log('H4 GUI is now available!')
                        return True
            except Exception:
                pass
        log('H4 GUI did not appear within 5 minutes', 'WARNING')
        return False
    except Exception as e:
        log('Failed to launch H4: %s' % str(e), 'ERROR')
        return False


def _set_recipe_pywinauto(main_win, recipe_path):
    """
    Set the recipe path in H4 GUI using pywinauto.
    Uses the tbRecPath TextBox control directly.
    """
    log('Setting recipe path via pywinauto...')
    log('  Recipe: %s' % recipe_path)

    try:
        # Find the recipe textbox by auto_id
        recipe_edit = main_win.child_window(
            auto_id=H4_CONTROLS['recipe_textbox'],
            control_type='System.Windows.Forms.TextBox'
        )

        if recipe_edit.exists():
            # Click to focus, select all, then type the path
            recipe_edit.click_input()
            time.sleep(0.3)
            recipe_edit.set_edit_text(recipe_path)
            time.sleep(0.5)

            # Verify
            current_text = recipe_edit.window_text()
            if recipe_path in current_text:
                log('  Recipe path set successfully')
                return True
            else:
                log('  Recipe path may not have been set correctly', 'WARNING')
                log('  Current text: %s' % current_text)
                return True  # Continue anyway
        else:
            log('  Recipe textbox not found by auto_id', 'WARNING')
            return False

    except Exception as e:
        log('  Failed to set recipe via pywinauto: %s' % str(e), 'WARNING')
        return False


def _set_recipe_keyboard(recipe_path):
    """
    Fallback: Set recipe path using keyboard simulation.
    Clicks the recipe browse button and types the path.
    """
    pyautogui = _try_import_pyautogui()
    if not pyautogui:
        return False

    log('Setting recipe path via keyboard fallback...')

    try:
        # Click on the recipe textbox area (coordinates from control tree)
        # tbRecPath is at approximately (L67, T498, R518, B518)
        # Center: x=292, y=508
        recipe_x = 292
        recipe_y = 508

        log('  Clicking recipe textbox at (%d, %d)' % (recipe_x, recipe_y))
        pyautogui.click(recipe_x, recipe_y)
        time.sleep(0.5)

        # Select all and type new path
        pyautogui.hotkey('ctrl', 'a')
        time.sleep(0.2)
        pyautogui.typewrite(recipe_path, interval=0.02)
        time.sleep(0.3)
        pyautogui.press('enter')
        time.sleep(0.5)

        log('  Recipe path typed via keyboard')
        return True

    except Exception as e:
        log('  Keyboard fallback failed: %s' % str(e), 'WARNING')
        return False


def _add_sites_pywinauto(main_win):
    """Click the Add Sites button in H4 GUI."""
    log('Adding sites via pywinauto...')
    try:
        add_btn = main_win.child_window(
            auto_id=H4_CONTROLS['add_sites_btn'],
            control_type='System.Windows.Forms.Button'
        )
        if add_btn.exists():
            add_btn.click_input()
            time.sleep(1)
            log('  Add Sites button clicked')
            return True
        else:
            log('  Add Sites button not found', 'WARNING')
            return False
    except Exception as e:
        log('  Failed to click Add Sites: %s' % str(e), 'WARNING')
        return False


def _add_sites_keyboard():
    """Fallback: Click Add Sites button using coordinates."""
    pyautogui = _try_import_pyautogui()
    if not pyautogui:
        return False

    log('Adding sites via keyboard fallback...')
    try:
        # btnAddSites is at (L387, T449, R487, B477)
        # Center: x=437, y=463
        pyautogui.click(437, 463)
        time.sleep(1)
        log('  Add Sites button clicked via coordinates')
        return True
    except Exception as e:
        log('  Keyboard fallback failed: %s' % str(e), 'WARNING')
        return False


def _start_test_pywinauto(main_win):
    """Click the Start Test button in H4 GUI."""
    log('Starting test via pywinauto...')
    try:
        start_btn = main_win.child_window(
            auto_id=H4_CONTROLS['start_test_btn'],
            control_type='System.Windows.Forms.Button'
        )
        if start_btn.exists():
            start_btn.click_input()
            time.sleep(2)
            log('  Start Test button clicked')
            return True
        else:
            log('  Start Test button not found', 'WARNING')
            return False
    except Exception as e:
        log('  Failed to click Start Test: %s' % str(e), 'WARNING')
        return False


def _start_test_keyboard():
    """Fallback: Click Start Test button using coordinates."""
    pyautogui = _try_import_pyautogui()
    if not pyautogui:
        return False

    log('Starting test via keyboard fallback...')
    try:
        # btnStart is at (L487, T449, R587, B477)
        # Center: x=537, y=463
        pyautogui.click(537, 463)
        time.sleep(2)
        log('  Start Test button clicked via coordinates')
        return True
    except Exception as e:
        log('  Keyboard fallback failed: %s' % str(e), 'WARNING')
        return False


def _select_dut_in_tree(main_win, config):
    """
    Select the DUT slot in the System Site Map tree.
    The tree shows: Chassis > Blade > DUT slots.
    We need to find and click the target DUT slot.
    """
    log('Selecting DUT slot %d in tree view...' % config['DUT_SLOT'])

    try:
        tree = main_win.child_window(
            auto_id=H4_CONTROLS['tree_view'],
            control_type='System.Windows.Forms.TreeView'
        )

        if not tree.exists():
            log('  Tree view not found', 'WARNING')
            return False

        # The tree contains blade serial numbers as nodes
        # We need to find the blade matching our BLADE_SN
        # and then select the DUT slot under it
        blade_sn = config['BLADE_SN']
        slot_num = config['DUT_SLOT']

        log('  Looking for blade: %s' % blade_sn)
        log('  Target slot: %d' % slot_num)

        # Try to expand and select via tree item text
        # The blade nodes typically show as "ANSKR..." format
        # Slot format is typically "[XX]" where XX is the slot number
        try:
            # Get tree root items
            root = tree.get_item('\\')
            if root:
                log('  Tree root found, searching for blade...')
                # Try to find blade by iterating children
                # This may not work perfectly with all pywinauto versions
                for child in root.children():
                    child_text = child.text()
                    if blade_sn[:10] in child_text:
                        log('  Found blade node: %s' % child_text)
                        child.click_input()
                        time.sleep(0.5)
                        # Now look for slot
                        slot_text = '[%02d]' % slot_num
                        for slot_child in child.children():
                            if slot_text in slot_child.text():
                                log('  Found slot: %s' % slot_child.text())
                                slot_child.click_input()
                                time.sleep(0.5)
                                return True
        except Exception as e:
            log('  Tree navigation error: %s' % str(e), 'WARNING')

        log('  Could not navigate tree programmatically', 'WARNING')
        return False

    except Exception as e:
        log('  DUT selection failed: %s' % str(e), 'WARNING')
        return False


def _check_h4_running():
    """Check if H4.exe process is running."""
    try:
        # Use tasklist to check for H4.exe
        output = subprocess.check_output(
            'tasklist /FI "IMAGENAME eq H4.exe" /NH',
            shell=True
        )
        return 'H4.exe' in output.decode('utf-8', errors='replace')
    except Exception:
        return False


def run_test_h4(config):
    """
    Run the test via H4 GUI automation.
    Uses pywinauto as primary method, pyautogui as fallback.
    Returns True if test was started successfully.
    """
    log_step(3, 'Run Test via H4 GUI')

    recipe_path = get_recipe_path(config)

    # Verify recipe file exists before trying to set it
    if not os.path.isfile(recipe_path):
        log('Recipe file does not exist: %s' % recipe_path, 'ERROR')
        log('Cannot start test without recipe file.', 'ERROR')
        return False

    # ---------------------------------------------------------------
    # 3a) Check if H4 is running
    # ---------------------------------------------------------------
    log('--- 3a) Check H4 Status ---')
    h4_running = _check_h4_running()

    if h4_running:
        log('H4.exe is running')
    else:
        log('H4.exe is NOT running')
        if ask_user('Launch H4 GUI?'):
            if not _launch_h4():
                log('Could not launch H4 GUI', 'ERROR')
                if ask_user('Continue with manual H4 start?', 'n'):
                    log('Please start H4 manually and press Enter when ready.')
                    raw_input('[INPUT] Press Enter when H4 is ready: ')
                else:
                    return False
        else:
            log('Please start H4 manually and press Enter when ready.')
            raw_input('[INPUT] Press Enter when H4 is ready: ')

    # ---------------------------------------------------------------
    # 3b) Connect to H4 window
    # ---------------------------------------------------------------
    log('--- 3b) Connect to H4 Window ---')
    pywinauto_mod = _try_import_pywinauto()
    app = None
    main_win = None
    use_pywinauto = False

    if pywinauto_mod:
        app, main_win = _find_h4_window(pywinauto_mod)
        if main_win is not None:
            use_pywinauto = True
            log('Connected to H4 GUI successfully')
            try:
                main_win.set_focus()
                time.sleep(0.5)
            except Exception:
                log('  Could not set focus on H4 window', 'WARNING')
        else:
            log('Could not connect to H4 via pywinauto', 'WARNING')
            log('Will attempt keyboard/mouse fallback', 'WARNING')
    else:
        log('pywinauto not available, using fallback methods', 'WARNING')

    # ---------------------------------------------------------------
    # 3c) Lock blades (informational - usually already locked)
    # ---------------------------------------------------------------
    log('--- 3c) Lock Blades ---')
    log('NOTE: Blades should already be locked from playground creation.')
    log('If blades are not locked, right-click the chassis in H4 and')
    log('select "Lock All Blades" manually.')
    # We skip automated blade locking as it requires right-click context
    # menu which is complex and the blades should already be locked.

    # ---------------------------------------------------------------
    # 3d) Select recipe
    # ---------------------------------------------------------------
    log('--- 3d) Select Recipe ---')
    recipe_set = False

    if use_pywinauto:
        recipe_set = _set_recipe_pywinauto(main_win, recipe_path)

    if not recipe_set:
        log('Trying keyboard fallback for recipe selection...')
        recipe_set = _set_recipe_keyboard(recipe_path)

    if not recipe_set:
        log('Could not set recipe automatically.', 'WARNING')
        log('Please set the recipe manually in H4 GUI:')
        log('  Recipe path: %s' % recipe_path)
        raw_input('[INPUT] Press Enter after setting recipe manually: ')
        recipe_set = True

    # ---------------------------------------------------------------
    # 3e) Select DUT slots
    # ---------------------------------------------------------------
    log('--- 3e) Select DUT Slots ---')
    slots_selected = False

    if use_pywinauto:
        # Try to select DUT in tree first
        slots_selected = _select_dut_in_tree(main_win, config)

        if not slots_selected:
            # Try Add Sites button
            log('Tree selection failed, trying Add Sites button...')
            slots_selected = _add_sites_pywinauto(main_win)

    if not slots_selected:
        log('Trying keyboard fallback for site selection...')
        slots_selected = _add_sites_keyboard()

    if not slots_selected:
        log('Could not select DUT slots automatically.', 'WARNING')
        log('Please select DUT slot %d in H4 GUI manually.' %
            config['DUT_SLOT'])
        log('  1. Click on the DUT slot in the chassis view')
        log('  2. Click "Add Sites" button')
        raw_input('[INPUT] Press Enter after selecting DUT slots: ')
        slots_selected = True

    # ---------------------------------------------------------------
    # 3f) Start test
    # ---------------------------------------------------------------
    log('--- 3f) Start Test ---')
    log('About to start the test. Please verify:')
    log('  Recipe: %s' % recipe_path)
    log('  DUT Slot: %d' % config['DUT_SLOT'])

    if not ask_user('Start the test now?'):
        log('Test start cancelled by user.')
        if ask_user('Start test manually and continue to monitoring?'):
            log('Please start the test manually in H4 GUI.')
            raw_input('[INPUT] Press Enter after starting test: ')
            log('Proceeding to monitoring...')
            return True
        return False

    test_started = False

    if use_pywinauto:
        test_started = _start_test_pywinauto(main_win)

    if not test_started:
        log('Trying keyboard fallback for Start Test...')
        test_started = _start_test_keyboard()

    if not test_started:
        log('Could not click Start Test automatically.', 'WARNING')
        log('Please click "Start Test" in H4 GUI manually.')
        raw_input('[INPUT] Press Enter after starting test: ')
        test_started = True

    # ---------------------------------------------------------------
    # 3g) Verify test started
    # ---------------------------------------------------------------
    log('--- 3g) Verify Test Started ---')
    time.sleep(5)  # Wait for test to initialize

    # Check RPYC log for test start indication
    rpyc_log = get_rpyc_log_path()
    test_confirmed = False

    if os.path.isfile(rpyc_log):
        lines = read_last_lines(rpyc_log, 20)
        for line in lines:
            if 'TEST_STARTED' in line or 'Start' in line:
                log('  RPYC log indicates test activity: %s' %
                    line.strip()[:80])
                test_confirmed = True
                break

    if test_confirmed:
        log('Test appears to have started successfully!')
    else:
        log('Could not confirm test start from logs.', 'WARNING')
        log('Check H4 GUI - DUT slots should turn BLUE (running).')

    start_time = datetime.datetime.now().strftime('%H:%M:%S')
    log('Test started at %s' % start_time)

    return True


# ===========================================================================
# STEP 4: MONITOR TEST COMPLETION
# ===========================================================================

def monitor_test_completion(config):
    """
    Monitor for test completion by watching engineering result folders.

    Detection methods (from actual IBIR-0383 tester):
      1. PRIMARY: Watch C:\\test_program\\eng_summary_staging\\ for new
         ENGSUMM-{LOT}_ABIT folders
      2. SECONDARY: Watch C:\\test_program\\eng_cache\\ for new
         {LOT}_ABIT folders
      3. BACKUP: Watch D:\\test_program\\summary_staging\\ for new
         SUMM-{LOT}_ABIT folders (production lots)
      4. RPYC LOG: Watch C:\\rpyc_logs\\rpyc_log_{date}.log for
         completion/error keywords

    NOTE: No .tsum files exist on IBIR testers. Engineering results
    are stored as FOLDERS, not files.

    Returns a result dictionary.
    """
    log_step(4, 'Monitor Test Completion')

    timeout_sec = config['TIMEOUT_HOURS'] * 3600
    poll_sec = config['POLL_INTERVAL_SEC']
    start_time = time.time()
    status_interval = 300  # Print status every 5 minutes
    last_status_time = start_time

    result = {
        'completed': False,
        'duration_minutes': 0,
        'tsum_file': None,
        'result_folder': None,
        'result': 'UNKNOWN',
        'error_message': '',
        'start_time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'end_time': '',
        'detection_method': '',
    }

    # ---------------------------------------------------------------
    # 4a) Snapshot existing result folders
    # ---------------------------------------------------------------
    log('--- 4a) Snapshot Result Folders ---')

    snapshot_eng_summary = set()
    if os.path.isdir(ENG_SUMMARY_PATH):
        snapshot_eng_summary = set(os.listdir(ENG_SUMMARY_PATH))
        log('  eng_summary_staging: %d existing folders' %
            len(snapshot_eng_summary))
    else:
        log('  eng_summary_staging not found: %s' % ENG_SUMMARY_PATH,
            'WARNING')

    snapshot_eng_cache = set()
    if os.path.isdir(ENG_CACHE_PATH):
        snapshot_eng_cache = set(os.listdir(ENG_CACHE_PATH))
        log('  eng_cache: %d existing folders' % len(snapshot_eng_cache))
    else:
        log('  eng_cache not found: %s' % ENG_CACHE_PATH, 'WARNING')

    snapshot_prod_summary = set()
    if os.path.isdir(PROD_SUMMARY_PATH):
        snapshot_prod_summary = set(os.listdir(PROD_SUMMARY_PATH))
        log('  prod_summary_staging: %d existing folders' %
            len(snapshot_prod_summary))
    else:
        log('  prod_summary_staging not found: %s' % PROD_SUMMARY_PATH,
            'WARNING')

    log('--- Monitoring Started ---')
    log('  Timeout: %s' % format_duration(timeout_sec))
    log('  Poll interval: %ds' % poll_sec)
    log('  Watching for LOT: %s' % config.get('LOT_ID', 'ANY'))
    log('  Press Ctrl+C to interrupt monitoring')
    print('')

    try:
        while True:
            elapsed = time.time() - start_time

            # Check timeout
            if elapsed > timeout_sec:
                log('WARNING: Test has exceeded timeout of %s!' %
                    format_duration(timeout_sec), 'WARNING')
                if ask_user('Continue waiting?', 'n'):
                    timeout_sec += 3600
                    log('Timeout extended by 1 hour.')
                else:
                    result['completed'] = False
                    result['result'] = 'TIMEOUT'
                    result['duration_minutes'] = int(elapsed / 60)
                    result['end_time'] = datetime.datetime.now().strftime(
                        '%Y-%m-%d %H:%M:%S')
                    log('Monitoring stopped due to timeout.')
                    return result

            # -------------------------------------------------------
            # 4b) Check for new engineering summary folders
            # -------------------------------------------------------
            if os.path.isdir(ENG_SUMMARY_PATH):
                current = set(os.listdir(ENG_SUMMARY_PATH))
                new_folders = current - snapshot_eng_summary
                if new_folders:
                    # Filter for ENGSUMM-* pattern
                    eng_folders = [
                        f for f in new_folders
                        if f.startswith('ENGSUMM-')
                    ]
                    if eng_folders:
                        result_folder = sorted(eng_folders)[0]
                        result_path = os.path.join(
                            ENG_SUMMARY_PATH, result_folder)
                        log('NEW ENG SUMMARY DETECTED: %s' % result_folder)
                        log('  Path: %s' % result_path)

                        result['completed'] = True
                        result['result_folder'] = result_path
                        result['duration_minutes'] = int(elapsed / 60)
                        result['end_time'] = (
                            datetime.datetime.now().strftime(
                                '%Y-%m-%d %H:%M:%S'))
                        result['result'] = 'COMPLETE'
                        result['detection_method'] = 'eng_summary_staging'

                        log('Test COMPLETED! (eng_summary_staging)')
                        log('Duration: %s' % format_duration(elapsed))
                        return result

            # -------------------------------------------------------
            # 4c) Check for new engineering cache folders
            # -------------------------------------------------------
            if os.path.isdir(ENG_CACHE_PATH):
                current = set(os.listdir(ENG_CACHE_PATH))
                new_folders = current - snapshot_eng_cache
                if new_folders:
                    # Filter for {LOT}_ABIT pattern
                    lot_id = config.get('LOT_ID', '')
                    cache_folders = []
                    for f in new_folders:
                        if lot_id and f.startswith(lot_id):
                            cache_folders.append(f)
                        elif f.endswith('_ABIT'):
                            cache_folders.append(f)
                    if cache_folders:
                        result_folder = sorted(cache_folders)[0]
                        result_path = os.path.join(
                            ENG_CACHE_PATH, result_folder)
                        log('NEW ENG CACHE DETECTED: %s' % result_folder)
                        log('  Path: %s' % result_path)

                        result['completed'] = True
                        result['result_folder'] = result_path
                        result['duration_minutes'] = int(elapsed / 60)
                        result['end_time'] = (
                            datetime.datetime.now().strftime(
                                '%Y-%m-%d %H:%M:%S'))
                        result['result'] = 'COMPLETE'
                        result['detection_method'] = 'eng_cache'

                        log('Test COMPLETED! (eng_cache)')
                        log('Duration: %s' % format_duration(elapsed))
                        return result

            # -------------------------------------------------------
            # 4d) Check for new production summary folders
            # -------------------------------------------------------
            if os.path.isdir(PROD_SUMMARY_PATH):
                current = set(os.listdir(PROD_SUMMARY_PATH))
                new_folders = current - snapshot_prod_summary
                if new_folders:
                    summ_folders = [
                        f for f in new_folders
                        if f.startswith('SUMM-')
                    ]
                    if summ_folders:
                        result_folder = sorted(summ_folders)[0]
                        result_path = os.path.join(
                            PROD_SUMMARY_PATH, result_folder)
                        log('NEW PROD SUMMARY DETECTED: %s' % result_folder)
                        log('  Path: %s' % result_path)

                        result['completed'] = True
                        result['result_folder'] = result_path
                        result['duration_minutes'] = int(elapsed / 60)
                        result['end_time'] = (
                            datetime.datetime.now().strftime(
                                '%Y-%m-%d %H:%M:%S'))
                        result['result'] = 'COMPLETE'
                        result['detection_method'] = 'prod_summary_staging'

                        log('Test COMPLETED! (prod_summary_staging)')
                        log('Duration: %s' % format_duration(elapsed))
                        return result

            # -------------------------------------------------------
            # 4e) Check RPYC log for completion/error keywords
            # -------------------------------------------------------
            rpyc_log = get_rpyc_log_path()
            if os.path.isfile(rpyc_log):
                lines = read_last_lines(rpyc_log, 30)
                for line in lines:
                    line_upper = line.upper()

                    # Check for errors
                    for err_kw in ERROR_KEYWORDS:
                        if err_kw.upper() in line_upper:
                            log('ERROR keyword detected in RPYC log: %s' %
                                err_kw, 'ERROR')
                            log('  Line: %s' % line.strip()[:100])
                            result['error_message'] = (
                                'Error keyword "%s" found in RPYC log' %
                                err_kw
                            )
                            break

                    # Check for completion
                    for comp_kw in COMPLETION_KEYWORDS:
                        if comp_kw.upper() in line_upper:
                            if 'Result Received' in line:
                                log('Completion keyword found: %s' %
                                    line.strip()[:100])
                                break

            # -------------------------------------------------------
            # 4f) Status update
            # -------------------------------------------------------
            now = time.time()
            if now - last_status_time >= status_interval:
                last_status_time = now
                ts = datetime.datetime.now().strftime('%H:%M:%S')
                elapsed_str = format_duration(elapsed)
                timeout_str = format_duration(timeout_sec)
                print('[%s] Monitoring... elapsed: %s (timeout: %s)' % (
                    ts, elapsed_str, timeout_str))

                # Also check if H4 is still running
                if not _check_h4_running():
                    log('WARNING: H4.exe is no longer running!', 'WARNING')
                    result['error_message'] = 'H4.exe process terminated'

            # Sleep until next poll
            time.sleep(poll_sec)

    except KeyboardInterrupt:
        elapsed = time.time() - start_time
        log('Monitoring interrupted by user (Ctrl+C)')
        result['duration_minutes'] = int(elapsed / 60)
        result['end_time'] = datetime.datetime.now().strftime(
            '%Y-%m-%d %H:%M:%S')

        choice = ask_user_choice(
            'What would you like to do?',
            ['Continue monitoring', 'Mark as completed', 'Mark as aborted']
        )

        if choice == 'Continue monitoring':
            log('Resuming monitoring...')
            return monitor_test_completion(config)
        elif choice == 'Mark as completed':
            result['completed'] = True
            result['result'] = 'MANUAL_COMPLETE'
        else:
            result['completed'] = False
            result['result'] = 'ABORTED'

        return result


# ===========================================================================
# STEP 5: COLLECT RESULTS
# ===========================================================================

def collect_results(config, test_result):
    """
    Collect test output files to a central location and generate report.
    Returns the results directory path.
    """
    log_step(5, 'Collect Results')

    # ---------------------------------------------------------------
    # 5a) Create results directory
    # ---------------------------------------------------------------
    log('--- 5a) Create Results Directory ---')
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    results_base = config['RESULTS_BASE']
    if not results_base:
        results_base = r'C:\temp\checkout_results'

    results_dir = os.path.join(results_base, config['MID'], timestamp)

    try:
        if not os.path.isdir(results_dir):
            os.makedirs(results_dir)
        log('Results directory: %s' % results_dir)
    except (IOError, OSError) as e:
        log('Failed to create results directory: %s' % str(e), 'ERROR')
        # Try fallback location
        results_dir = os.path.join(
            r'C:\temp\checkout_results', config['MID'], timestamp
        )
        try:
            if not os.path.isdir(results_dir):
                os.makedirs(results_dir)
            log('Using fallback results directory: %s' % results_dir)
        except (IOError, OSError) as e2:
            log('Cannot create any results directory: %s' % str(e2), 'ERROR')
            return None

    # ---------------------------------------------------------------
    # 5b) Copy result files (engineering folders, not .tsum files)
    # ---------------------------------------------------------------
    log('--- 5b) Copy Result Files ---')
    collected = {}

    # Engineering summary folders (ENGSUMM-* from eng_summary_staging)
    eng_summ_dst = os.path.join(results_dir, 'eng_summary')
    try:
        if not os.path.isdir(eng_summ_dst):
            os.makedirs(eng_summ_dst)
        eng_summ_count = 0
        eng_summ_size = 0
        if os.path.isdir(ENG_SUMMARY_PATH):
            lot_id = config.get('LOT_ID', '')
            for f in os.listdir(ENG_SUMMARY_PATH):
                src = os.path.join(ENG_SUMMARY_PATH, f)
                if not os.path.isdir(src):
                    continue
                # Match ENGSUMM-{LOT}_ABIT or any folder modified recently
                is_match = False
                if lot_id and lot_id in f:
                    is_match = True
                elif f.startswith('ENGSUMM-'):
                    mtime = os.path.getmtime(src)
                    if time.time() - mtime < 86400:
                        is_match = True
                if is_match:
                    dst_folder = os.path.join(eng_summ_dst, f)
                    try:
                        copy_tree_contents(src, dst_folder)
                        fc, fs = count_files_in_dir(dst_folder)
                        eng_summ_count += fc
                        eng_summ_size += fs
                    except (IOError, OSError) as ce:
                        log('  Failed to copy eng_summary folder %s: %s' % (
                            f, str(ce)), 'WARNING')
        collected['eng_summary'] = (eng_summ_count, eng_summ_size)
        log('  eng_summary/: %d files (%s)' % (
            eng_summ_count, format_size(eng_summ_size)))
    except (IOError, OSError) as e:
        log('  Failed to copy eng_summary files: %s' % str(e), 'WARNING')
        collected['eng_summary'] = (0, 0)

    # Engineering cache folders ({LOT}_ABIT from eng_cache)
    eng_cache_dst = os.path.join(results_dir, 'eng_cache')
    try:
        if not os.path.isdir(eng_cache_dst):
            os.makedirs(eng_cache_dst)
        eng_cache_count = 0
        eng_cache_size = 0
        if os.path.isdir(ENG_CACHE_PATH):
            lot_id = config.get('LOT_ID', '')
            for f in os.listdir(ENG_CACHE_PATH):
                src = os.path.join(ENG_CACHE_PATH, f)
                if not os.path.isdir(src):
                    continue
                # Match {LOT}_ABIT folders or recently modified
                is_match = False
                if lot_id and f.startswith(lot_id):
                    is_match = True
                elif '_ABIT' in f:
                    mtime = os.path.getmtime(src)
                    if time.time() - mtime < 86400:
                        is_match = True
                if is_match:
                    dst_folder = os.path.join(eng_cache_dst, f)
                    try:
                        copy_tree_contents(src, dst_folder)
                        fc, fs = count_files_in_dir(dst_folder)
                        eng_cache_count += fc
                        eng_cache_size += fs
                    except (IOError, OSError) as ce:
                        log('  Failed to copy eng_cache folder %s: %s' % (
                            f, str(ce)), 'WARNING')
        collected['eng_cache'] = (eng_cache_count, eng_cache_size)
        log('  eng_cache/: %d files (%s)' % (
            eng_cache_count, format_size(eng_cache_size)))
    except (IOError, OSError) as e:
        log('  Failed to copy eng_cache files: %s' % str(e), 'WARNING')
        collected['eng_cache'] = (0, 0)

    # Production summary folders (SUMM-* from summary_staging, if present)
    prod_summ_dst = os.path.join(results_dir, 'prod_summary')
    try:
        if not os.path.isdir(prod_summ_dst):
            os.makedirs(prod_summ_dst)
        prod_summ_count = 0
        prod_summ_size = 0
        if os.path.isdir(PROD_SUMMARY_PATH):
            lot_id = config.get('LOT_ID', '')
            for f in os.listdir(PROD_SUMMARY_PATH):
                src = os.path.join(PROD_SUMMARY_PATH, f)
                if not os.path.isdir(src):
                    continue
                is_match = False
                if lot_id and lot_id in f:
                    is_match = True
                elif f.startswith('SUMM-'):
                    mtime = os.path.getmtime(src)
                    if time.time() - mtime < 86400:
                        is_match = True
                if is_match:
                    dst_folder = os.path.join(prod_summ_dst, f)
                    try:
                        copy_tree_contents(src, dst_folder)
                        fc, fs = count_files_in_dir(dst_folder)
                        prod_summ_count += fc
                        prod_summ_size += fs
                    except (IOError, OSError) as ce:
                        log('  Failed to copy prod_summary folder %s: %s' % (
                            f, str(ce)), 'WARNING')
        collected['prod_summary'] = (prod_summ_count, prod_summ_size)
        log('  prod_summary/: %d files (%s)' % (
            prod_summ_count, format_size(prod_summ_size)))
    except (IOError, OSError) as e:
        log('  Failed to copy prod_summary files: %s' % str(e), 'WARNING')
        collected['prod_summary'] = (0, 0)

    # Playground queue XMLs (Profile_*.xml)
    pq_dst = os.path.join(results_dir, 'playground_queue')
    try:
        if not os.path.isdir(pq_dst):
            os.makedirs(pq_dst)
        pq_count = 0
        pq_size = 0
        if os.path.isdir(PLAYGROUND_QUEUE_PATH):
            for f in os.listdir(PLAYGROUND_QUEUE_PATH):
                if f.lower().startswith('profile_') and f.lower().endswith('.xml'):
                    src = os.path.join(PLAYGROUND_QUEUE_PATH, f)
                    if os.path.isfile(src):
                        mtime = os.path.getmtime(src)
                        if time.time() - mtime < 86400:
                            shutil.copy2(src, os.path.join(pq_dst, f))
                            pq_count += 1
                            pq_size += os.path.getsize(src)
        collected['playground_queue'] = (pq_count, pq_size)
        log('  playground_queue/: %d files (%s)' % (
            pq_count, format_size(pq_size)))
    except (IOError, OSError) as e:
        log('  Failed to copy playground queue files: %s' % str(e), 'WARNING')
        collected['playground_queue'] = (0, 0)

    # RPYC logs
    rpyc_dst = os.path.join(results_dir, 'rpyc_logs')
    try:
        if not os.path.isdir(rpyc_dst):
            os.makedirs(rpyc_dst)
        rpyc_count = 0
        rpyc_size = 0
        rpyc_log = get_rpyc_log_path()
        if os.path.isfile(rpyc_log):
            fname = os.path.basename(rpyc_log)
            shutil.copy2(rpyc_log, os.path.join(rpyc_dst, fname))
            rpyc_count = 1
            rpyc_size = os.path.getsize(rpyc_log)
        collected['rpyc_logs'] = (rpyc_count, rpyc_size)
        log('  rpyc_logs/: %d files (%s)' % (
            rpyc_count, format_size(rpyc_size)))
    except (IOError, OSError) as e:
        log('  Failed to copy RPYC logs: %s' % str(e), 'WARNING')
        collected['rpyc_logs'] = (0, 0)

    # Profile logs (last 24 hours)
    profile_dst = os.path.join(results_dir, 'profile_logs')
    try:
        if not os.path.isdir(profile_dst):
            os.makedirs(profile_dst)
        profile_count = 0
        profile_size = 0
        if os.path.isdir(PROFILE_LOG_DIR):
            for f in os.listdir(PROFILE_LOG_DIR):
                src = os.path.join(PROFILE_LOG_DIR, f)
                if os.path.isfile(src):
                    mtime = os.path.getmtime(src)
                    if time.time() - mtime < 86400:
                        shutil.copy2(src, os.path.join(profile_dst, f))
                        profile_count += 1
                        profile_size += os.path.getsize(src)
        collected['profile_logs'] = (profile_count, profile_size)
        log('  profile_logs/: %d files (%s)' % (
            profile_count, format_size(profile_size)))
    except (IOError, OSError) as e:
        log('  Failed to copy profile logs: %s' % str(e), 'WARNING')
        collected['profile_logs'] = (0, 0)

    # ---------------------------------------------------------------
    # 5c) Generate checkout report
    # ---------------------------------------------------------------
    log('--- 5c) Generate Checkout Report ---')
    report_path = os.path.join(results_dir, 'checkout_report.txt')

    duration_str = '%dh %dm' % (
        test_result.get('duration_minutes', 0) // 60,
        test_result.get('duration_minutes', 0) % 60
    )

    report_lines = [
        '============================================',
        'IBIR CHECKOUT REPORT',
        '============================================',
        'Date:        %s' % datetime.datetime.now().strftime(
            '%Y-%m-%d %H:%M:%S'),
        'MID:         %s' % config['MID'],
        'LOT ID:      %s' % config['LOT_ID'],
        'Tester:      %s' % config['TESTER_ID'],
        'Blade SN:    %s' % config['BLADE_SN'],
        'DUT Slot:    %s' % config['DUT_SLOT'],
        'Product:     %s' % config['PRODUCT'],
        'FW Source:   %s' % config['FW_SRC'],
        'FW Dest:     %s' % config['FW_DEST'],
        'FW Sub:      %s' % (config['FW_SUB'] or 'N/A'),
        'Flow File:   %s' % config['FLOW_FILE'],
        'Recipe:      %s' % config['RECIPE_XML'],
        '',
        'Test Start:  %s' % test_result.get('start_time', 'N/A'),
        'Test End:    %s' % test_result.get('end_time', 'N/A'),
        'Duration:    %s' % duration_str,
        'Result:      %s' % test_result.get('result', 'UNKNOWN'),
        'Result Folder: %s' % (test_result.get('result_folder') or 'N/A'),
        'Detection:   %s' % (test_result.get('detection_method') or 'N/A'),
        '',
    ]

    if test_result.get('error_message'):
        report_lines.append(
            'Error:       %s' % test_result['error_message']
        )
        report_lines.append('')

    report_lines.append('Files Collected:')
    for folder, (fcount, fsize) in sorted(collected.items()):
        report_lines.append('  %-16s - %d files (%s)' % (
            folder + '/', fcount, format_size(fsize)))

    report_lines.extend([
        '',
        'Results stored at:',
        '  %s' % results_dir,
        '============================================',
    ])

    report_text = '\n'.join(report_lines)

    try:
        with open(report_path, 'w') as f:
            f.write(report_text)
        log('Report saved: %s' % report_path)
    except (IOError, OSError) as e:
        log('Failed to save report: %s' % str(e), 'WARNING')

    # ---------------------------------------------------------------
    # 5d) Print summary to console
    # ---------------------------------------------------------------
    print('')
    print(report_text)
    print('')

    return results_dir


# ===========================================================================
# STEP 6: NOTIFY (OPTIONAL)
# ===========================================================================

def send_notification(config, test_result, report_path):
    """
    Send a Teams notification if webhook URL is configured.
    Uses urllib2 for Python 2.7 compatibility.
    """
    log_step(6, 'Send Notification')

    webhook_url = config.get('TEAMS_WEBHOOK_URL', '')
    if not webhook_url:
        log('Notification skipped (no webhook configured)')
        return

    log('Sending Teams notification...')

    duration_str = '%dh %dm' % (
        test_result.get('duration_minutes', 0) // 60,
        test_result.get('duration_minutes', 0) % 60
    )

    # Build Teams message card (simple text format)
    # Using MessageCard format for older Teams webhooks
    result_emoji = {
        'PASS': '✅',
        'FAIL': '❌',
        'TIMEOUT': '⏰',
        'CRASH': '💥',
        'ABORTED': '🛑',
        'COMPLETE': '✔️',
        'MANUAL_COMPLETE': '✔️',
        'UNKNOWN': '❓',
    }

    emoji = result_emoji.get(
        test_result.get('result', 'UNKNOWN'), '❓'
    )

    card = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": "0076D7",
        "summary": "IBIR Checkout: %s %s" % (
            config['MID'], test_result.get('result', 'UNKNOWN')),
        "sections": [{
            "activityTitle": "%s IBIR Checkout Result: %s" % (
                emoji, test_result.get('result', 'UNKNOWN')),
            "facts": [
                {"name": "MID", "value": config['MID']},
                {"name": "Tester", "value": config['TESTER_ID']},
                {"name": "Product", "value": config['PRODUCT']},
                {"name": "Duration", "value": duration_str},
                {"name": "Result", "value": test_result.get(
                    'result', 'UNKNOWN')},
                {"name": "Results Path",
                 "value": report_path or 'N/A'},
            ],
            "markdown": True
        }]
    }

    payload = json.dumps(card)

    try:
        import urllib2
        req = urllib2.Request(
            webhook_url,
            data=payload,
            headers={'Content-Type': 'application/json'}
        )
        response = urllib2.urlopen(req, timeout=30)
        status = response.getcode()
        if status == 200:
            log('Teams notification sent successfully')
        else:
            log('Teams notification returned status %d' % status, 'WARNING')
    except ImportError:
        # Try requests as fallback
        try:
            import requests
            resp = requests.post(
                webhook_url,
                json=card,
                timeout=30
            )
            if resp.status_code == 200:
                log('Teams notification sent successfully (via requests)')
            else:
                log('Teams notification returned status %d' %
                    resp.status_code, 'WARNING')
        except ImportError:
            log('Neither urllib2 nor requests available for notification',
                'ERROR')
        except Exception as e:
            log('Notification failed (requests): %s' % str(e), 'ERROR')
    except Exception as e:
        log('Notification failed: %s' % str(e), 'ERROR')


# ===========================================================================
# MAIN FUNCTION
# ===========================================================================

def main():
    """
    Main orchestration function.

    When run standalone (python ibir_checkout.py), this function:
      1. Runs auto_extract_config() to detect workspace, blade SN, recipe,
         product, MID, LOT, DUT slot from the tester environment.
      2. Prompts the engineer for any values that cannot be auto-detected
         (FW source/dest, revision, flow file).
      3. Executes the 6-step checkout pipeline.

    The hardcoded CONFIG dict at the top of this file is used ONLY as a
    fallback if auto_extract_config() returns None (user cancelled).
    """
    print_banner()

    # ------------------------------------------------------------------
    # Step 0: Auto-extract config (or fall back to hardcoded CONFIG)
    # ------------------------------------------------------------------
    log_step(0, 'Auto-extracting configuration from tester environment...')

    config = auto_extract_config()

    if config is None:
        # User cancelled auto-extract — offer to use hardcoded CONFIG
        log('Auto-extract cancelled or failed.', 'WARN')
        print('')
        print('  Falling back to hardcoded CONFIG dict.')
        print('  (Edit the CONFIG section at the top of this script to change.)')
        print('')
        print_config(CONFIG)
        if not ask_user('Use hardcoded CONFIG and proceed?'):
            log('Checkout cancelled by user.')
            sys.exit(0)
        config = CONFIG
    else:
        log('Configuration extracted successfully.')

    # Track overall timing
    overall_start = time.time()

    # ------------------------------------------------------------------
    # Step 1: Validate Playground
    # ------------------------------------------------------------------
    try:
        if not validate_playground(config):
            log('Playground validation failed. Aborting.', 'ERROR')
            sys.exit(1)
    except Exception as e:
        log('Step 1 error: %s' % str(e), 'ERROR')
        traceback.print_exc()
        if not ask_user('Continue despite error?', 'n'):
            sys.exit(1)

    # ------------------------------------------------------------------
    # Step 2: Copy Test Files
    # ------------------------------------------------------------------
    try:
        if not copy_test_files(config):
            log('File copy failed.', 'ERROR')
            if not ask_user('Continue to test execution anyway?', 'n'):
                sys.exit(1)
    except Exception as e:
        log('Step 2 error: %s' % str(e), 'ERROR')
        traceback.print_exc()
        if not ask_user('Continue despite error?', 'n'):
            sys.exit(1)

    # ------------------------------------------------------------------
    # Step 3: Run Test via H4 GUI
    # ------------------------------------------------------------------
    try:
        if not run_test_h4(config):
            log('Test start failed.', 'ERROR')
            if ask_user('Skip to monitoring (test started manually)?', 'n'):
                log('Proceeding to monitoring...')
            else:
                sys.exit(1)
    except Exception as e:
        log('Step 3 error: %s' % str(e), 'ERROR')
        traceback.print_exc()
        if ask_user('Skip to monitoring (test started manually)?', 'n'):
            log('Proceeding to monitoring...')
        else:
            sys.exit(1)

    # ------------------------------------------------------------------
    # Step 4: Monitor Test Completion
    # ------------------------------------------------------------------
    test_result = {
        'completed': False,
        'duration_minutes': 0,
        'tsum_file': None,
        'result_folder': None,
        'detection_method': None,
        'result': 'UNKNOWN',
        'error_message': '',
        'start_time': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'end_time': '',
    }

    try:
        test_result = monitor_test_completion(config)
    except Exception as e:
        log('Step 4 error: %s' % str(e), 'ERROR')
        traceback.print_exc()
        test_result['result'] = 'ERROR'
        test_result['error_message'] = str(e)
        test_result['end_time'] = datetime.datetime.now().strftime(
            '%Y-%m-%d %H:%M:%S')

    # ------------------------------------------------------------------
    # Step 5: Collect Results
    # ------------------------------------------------------------------
    results_dir = None
    try:
        results_dir = collect_results(config, test_result)
    except Exception as e:
        log('Step 5 error: %s' % str(e), 'ERROR')
        traceback.print_exc()

    # ------------------------------------------------------------------
    # Step 6: Send Notification
    # ------------------------------------------------------------------
    try:
        send_notification(config, test_result, results_dir)
    except Exception as e:
        log('Step 6 error: %s' % str(e), 'ERROR')
        traceback.print_exc()

    # ------------------------------------------------------------------
    # Final Summary
    # ------------------------------------------------------------------
    overall_elapsed = time.time() - overall_start
    print('')
    print('=' * 60)
    print('  CHECKOUT AUTOMATION COMPLETE')
    print('=' * 60)
    print('  MID:          %s' % config['MID'])
    print('  Tester:       %s' % config['TESTER_ID'])
    print('  Test Result:  %s' % test_result.get('result', 'UNKNOWN'))
    print('  Duration:     %s' % format_duration(
        test_result.get('duration_minutes', 0) * 60))
    print('  Total Script: %s' % format_duration(overall_elapsed))
    if results_dir:
        print('  Results:      %s' % results_dir)
    if test_result.get('result_folder'):
        print('  Result Dir:   %s' % test_result['result_folder'])
    if test_result.get('detection_method'):
        print('  Detected By:  %s' % test_result['detection_method'])
    if test_result.get('error_message'):
        print('  Error:        %s' % test_result['error_message'])
    print('=' * 60)
    print('')


# ===========================================================================
# ENTRY POINT
# ===========================================================================

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('')
        log('Script interrupted by user (Ctrl+C)')
        sys.exit(130)
    except SystemExit:
        raise
    except Exception as e:
        log('Unhandled exception: %s' % str(e), 'FATAL')
        traceback.print_exc()
        sys.exit(1)
