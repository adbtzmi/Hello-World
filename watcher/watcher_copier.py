# -*- coding: utf-8 -*-
"""
watcher_copier.py
=================
Handles binary copy of the compiled .tgz to the shared RELEASE_TGZ folder.

OUTPUT FOLDER + FILE NAMING:
  RELEASE_TGZ/
      IBIR-0383_TSESSD-14270_ABIT/    <- HOSTNAME_JIRA_ENV folder
          ibir_release_passing.tgz        <- label comes from ZIP filename
          ibir_release_force_fail_1.tgz   <- different label = different run
          build_info_passing.txt

ZIP filename encodes the label:
  TSESSD-14270_ABIT_20260312_0832_passing.zip    -> label = "passing"
  TSESSD-14270_ABIT_20260312_0832_force_fail_1.zip -> label = "force_fail_1"
  TSESSD-14270_ABIT_20260312_0832.zip            -> label = "" (default)

The compiled artifact on the tester is always named ibir_release.tgz.
We rename it to ibir_release_<label>.tgz (or ibir_release.tgz if no label)
before copying to RELEASE_TGZ.
"""
from __future__ import print_function
import os
from datetime import datetime

from watcher_config import (
    RELEASE_TGZ_FOLDER,
    COPY_CHUNK_BYTES,
    make_output_folder_name,
    parse_zip_parts,
)


# ----------------------------------------------------------------
# PARSE LABEL FROM ZIP FILENAME
# ----------------------------------------------------------------
def parse_label_from_zip(zip_name):
    """
    Extract optional label from ZIP filename using parse_zip_parts.
    New format: JIRA_HOSTNAME_ENV_DATE_TIME[_LABEL].zip
    Examples:
      TSESSD-14270_IBIR-0383_ABIT_20260312_0832.zip           -> ""
      TSESSD-14270_IBIR-0383_ABIT_20260312_0832_passing.zip   -> "passing"
      TSESSD-14270_IBIR-0383_ABIT_20260312_0832_force_fail_1.zip -> "force_fail_1"
    """
    parts = parse_zip_parts(zip_name)
    if parts:
        return parts.get("label", "")
    return ""


def make_tgz_filename(base_tgz_name, label, env):
    """
    Rename the tgz with label and env.
    ibir_release.tgz + label="passing" + env="ABIT"
      -> ibir_release_ABIT_passing.tgz
    ibir_release.tgz + label="" + env="ABIT"
      -> ibir_release_ABIT.tgz
    """
    stem = os.path.splitext(base_tgz_name)[0]   # e.g. "ibir_release"
    if label:
        label = label.replace(" ", "_")          # spaces → underscores in filename
        return stem + "_" + env + "_" + label + ".tgz"
    else:
        return stem + "_" + env + ".tgz"


# ----------------------------------------------------------------
# BINARY COPY
# ----------------------------------------------------------------
def binary_copy(src_path, dest_path, logger):
    """
    Copy src_path to dest_path using binary chunked read/write.
    Verifies file size after copy.
    Returns True on success, False on failure.
    """
    try:
        src_size = os.path.getsize(src_path)
    except Exception:
        src_size = 0

    logger.info(
        "Binary copy: " + os.path.basename(src_path)
        + " (" + str(src_size // (1024 * 1024)) + " MB)"
        + " -> " + dest_path
    )

    try:
        with open(src_path, "rb") as src_f:
            with open(dest_path, "wb") as dst_f:
                copied    = 0
                chunk_num = 0
                while True:
                    chunk = src_f.read(COPY_CHUNK_BYTES)
                    if not chunk:
                        break
                    dst_f.write(chunk)
                    copied    += len(chunk)
                    chunk_num += 1
                    if chunk_num % 10 == 0:
                        pct = int(100 * copied / src_size) if src_size > 0 else 0
                        logger.info(
                            "  Copy progress: "
                            + str(copied // (1024 * 1024)) + " MB / "
                            + str(src_size // (1024 * 1024)) + " MB ("
                            + str(pct) + "%)"
                        )
                dst_f.flush()
                try:
                    os.fsync(dst_f.fileno())
                except Exception:
                    pass   # fsync may not be supported on all network shares

        dest_size = os.path.getsize(dest_path)
        if dest_size != src_size:
            logger.error(
                "Size mismatch after copy! src=" + str(src_size)
                + " dest=" + str(dest_size)
            )
            return False

        logger.info(
            "[OK] Binary copy complete. "
            + str(dest_size // (1024 * 1024)) + " MB verified."
        )
        return True

    except Exception as e:
        logger.error("Binary copy failed: " + str(e))
        return False


# ----------------------------------------------------------------
# WRITE BUILD INFO
# ----------------------------------------------------------------
def write_build_info(dest_folder, zip_name, tgz_filename,
                     hostname, jira_key, env, label, logger):
    safe_label = label.replace(" ", "_") if label else ""
    info_name = "build_info_" + (safe_label if safe_label else "default") + ".txt"
    info_path = os.path.join(dest_folder, info_name)
    try:
        with open(info_path, "w") as f:
            f.write("BENTO Build Info\n")
            f.write("=" * 40 + "\n")
            f.write("Tester    : " + hostname + "\n")
            f.write("JIRA Key  : " + jira_key + "\n")
            f.write("Env       : " + env + "\n")
            f.write("Label     : " + (label if label else "(none)") + "\n")
            f.write("Source ZIP: " + zip_name + "\n")
            f.write("Output TGZ: " + tgz_filename + "\n")
            f.write("Timestamp : " + datetime.now().isoformat() + "\n")
            f.write("=" * 40 + "\n")
        logger.info(info_name + " written.")
    except Exception as e:
        logger.warning("Could not write build info: " + str(e))


# ----------------------------------------------------------------
# MAIN COPY ENTRY POINT
# ----------------------------------------------------------------
def copy_tgz_to_release(tgz_path, zip_name, hostname, jira_key, env, logger,
                         release_tgz_folder=None):
    """
    Copy the compiled TGZ to the release folder.

    Output folder  : RELEASE_TGZ/IBIR-0383_TSESSD-14270_ABIT/
    Output filename: ibir_release_ABIT_passing.tgz  (or ibir_release_ABIT.tgz)

    Returns (success: bool, dest_path: str or None)
    """
    _release = release_tgz_folder if release_tgz_folder else RELEASE_TGZ_FOLDER

    label    = parse_label_from_zip(zip_name)
    tgz_base = os.path.basename(tgz_path)
    tgz_out  = make_tgz_filename(tgz_base, label, env)

    # Folder: HOSTNAME_JIRA_ENV  e.g. IBIR-0383_TSESSD-14270_ABIT
    # Each tester gets its own folder - even if two testers share the same env.
    folder_name = make_output_folder_name(hostname, jira_key, env)
    dest_folder = os.path.join(_release, folder_name)

    logger.info("Release folder  : " + dest_folder)
    logger.info("Output filename : " + tgz_out)
    if label:
        logger.info("Label           : " + label)

    try:
        if not os.path.exists(dest_folder):
            os.makedirs(dest_folder)
            logger.info("Created release folder: " + dest_folder)
    except Exception as e:
        logger.error("Cannot create release folder: " + str(e))
        return False, None

    dest_path = os.path.join(dest_folder, tgz_out)

    success = binary_copy(tgz_path, dest_path, logger)

    if success:
        write_build_info(
            dest_folder, zip_name, tgz_out,
            hostname, jira_key, env, label, logger
        )
        return True, dest_path
    else:
        try:
            if os.path.exists(dest_path):
                os.remove(dest_path)
        except Exception:
            pass
        return False, None
