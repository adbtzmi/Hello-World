# -*- coding: utf-8 -*-
"""
watcher_config.py
=================
Single source of truth for all BENTO tester watcher configuration.
Edit ONLY this file when paths or tester details change.

Tester file structure:
    bento_tester/
        watcher_config.py   <- this file
        watcher_lock.py     <- lock + status file logic
        watcher_builder.py  <- cmd.exe build + cleanup
        watcher_copier.py   <- binary copy to shared drive
        watcher_main.py     <- entry point
"""
from __future__ import print_function
import os

# ================================================================
# SHARED FOLDER PATHS  (network drive visible from all machines)
# ================================================================
RAW_ZIP_FOLDER      = r"P:\temp\BENTO\RAW_ZIP"
RELEASE_TGZ_FOLDER  = r"P:\temp\BENTO\RELEASE_TGZ"

# ================================================================
# TESTER REGISTRY - LOADED FROM SHARED JSON
# Single source of truth: P:\temp\BENTO\bento_testers.json
# Both GUI and watcher read from this file.
# ================================================================

_REGISTRY_PATH = r"P:\temp\BENTO\bento_testers.json"

def _load_registry():
    """
    Load tester registry from shared JSON file.
    Returns dict mapping ENV -> tester details.
    
    Fallback to hardcoded defaults if file doesn't exist yet.
    """
    import json
    
    # Fallback defaults (used only if JSON doesn't exist)
    defaults = {
        "ABIT": {
            "hostname":  "IBIR-0383",
            "env":       "ABIT",
            "repo_dir":  r"C:\xi\adv_ibir_master",
            "build_cmd": "make release",
        },
        "SFN2": {
            "hostname":  "MPT3HVM-0156",
            "env":       "SFN2",
            "repo_dir":  r"C:\xi\adv_ibir_master",
            "build_cmd": "make release",
        },
        "CNFG": {
            "hostname":  "CTOWTST-0031",
            "env":       "CNFG",
            "repo_dir":  r"C:\xi\adv_ibir_master",
            "build_cmd": "make release_supermicro",
        },
    }
    
    try:
        if os.path.exists(_REGISTRY_PATH):
            with open(_REGISTRY_PATH, "r") as f:
                data = json.load(f)
            # Convert from GUI format (key = "HOSTNAME (ENV)") to watcher format (key = ENV)
            registry = {}
            for key, val in data.items():
                env = val["env"]
                registry[env] = {
                    "hostname":  val["hostname"],
                    "env":       val["env"],
                    "repo_dir":  val["repo_dir"],
                    "build_cmd": val["build_cmd"],
                }
            return registry
        else:
            # File doesn't exist yet - return defaults
            return defaults
    except Exception as e:
        print(f"[WARN] Could not load tester registry from {_REGISTRY_PATH}: {e}")
        print("[WARN] Falling back to hardcoded defaults")
        return defaults

# NOTE: loaded once at startup. Restart the watcher after adding
# a new tester via the BENTO GUI for changes to take effect.
TESTER_REGISTRY = _load_registry()

# ================================================================
# WATCHER BEHAVIOUR
# ================================================================
POLL_INTERVAL_SECONDS   = 60     # how often to scan RAW_ZIP_FOLDER
BUILD_TIMEOUT_SECONDS   = 1800   # 30 min max for make release
COPY_TIMEOUT_SECONDS    = 120    # 2 min max for binary copy
LOCK_MAX_AGE_SECONDS    = 1800   # stale lock expiry (match build timeout)

# ================================================================
# MEMORY MANAGEMENT
# ================================================================
# Seconds to sleep after build exits before starting copy.
# Gives the OS time to reclaim RAM freed by the compiler.
POST_BUILD_SLEEP_SECONDS = 10

# Chunk size for binary TGZ copy (4 MB)
COPY_CHUNK_BYTES = 4 * 1024 * 1024

# ================================================================
# LOGGING
# ================================================================
LOG_DIR = r"C:\BENTO\logs"

# ================================================================
# ZIP FILENAME CONVENTION
# ----------------------------------------------------------------
# Format: <JIRA_KEY>_<HOSTNAME>_<ENV>_<DATE>_<TIME>[_<LABEL>].zip
# Example: TSESSD-14270_IBIR-0383_ABIT_20260312_0832_passing.zip
#
# The watcher matches on BOTH hostname AND env token, so two testers
# with the same env (e.g. two ABIT testers) never steal each other's ZIPs.
# ================================================================

def parse_zip_parts(zip_name):
    """
    Parse a ZIP filename into its components.

    Expected format:
      TSESSD-14270_IBIR-0383_ABIT_20260312_0832[_label...].zip

    Returns dict with keys: jira_key, hostname, env, label
    Returns None if the filename does not match expected format.

    Token layout:
      Index 0       : JIRA key         e.g. TSESSD-14270
      Index 1       : Hostname          e.g. IBIR-0383
      Index 2       : ENV token         e.g. ABIT
      Index 3       : Date (YYYYMMDD)   e.g. 20260312
      Index 4       : Time (HHMM)       e.g. 0832
      Index 5+      : Label (optional)  e.g. passing / force_fail_1
    """
    try:
        base   = os.path.splitext(zip_name)[0]
        parts  = base.split("_")
        # Minimum required: JIRA(1 part, contains '-') + HOSTNAME(1) + ENV(1) + DATE(1) + TIME(1)
        # JIRA key like TSESSD-14270 has a dash, so split("_")[0] = "TSESSD-14270"
        if len(parts) < 5:
            return None
        jira_key = parts[0]            # TSESSD-14270
        hostname = parts[1]            # IBIR-0383
        env      = parts[2].upper()   # ABIT
        # Everything after index 4 (time) is the optional label
        label    = "_".join(parts[5:]) if len(parts) > 5 else ""
        return {
            "jira_key": jira_key,
            "hostname": hostname,
            "env":      env,
            "label":    label,
        }
    except Exception:
        return None


def zip_belongs_to_tester(zip_name, hostname, env):
    """
    Returns True only if the ZIP filename encodes BOTH this hostname AND this env.
    Strict matching: IBIR-0383 watcher will NOT claim a ZIP for IBIR-0999,
    even if both run the same env (ABIT).
    """
    parts = parse_zip_parts(zip_name)
    if parts is None:
        return False
    return (
        parts["hostname"].upper() == hostname.upper() and
        parts["env"].upper()      == env.upper()
    )


def parse_jira_key_from_zip(zip_name):
    """Extract JIRA key from ZIP filename. Returns 'UNKNOWN' if not parseable."""
    parts = parse_zip_parts(zip_name)
    return parts["jira_key"] if parts else "UNKNOWN"


# ================================================================
# OUTPUT FOLDER NAMING
# Format: <HOSTNAME>_<JIRA_KEY>_<ENV>
# Example: IBIR-0383_TSESSD-14270_ABIT
#
# One folder per (tester, ticket, env) combination.
# All compilations for that combination go in the same folder.
# TGZ files inside are differentiated by label.
# ================================================================
def make_output_folder_name(hostname, jira_key, env):
    """
    Return the release output subfolder name.
    e.g. IBIR-0383_TSESSD-14270_ABIT
    """
    return hostname + "_" + jira_key + "_" + env
