"""
model/tmptravl_generator.py
============================
TempTraveler (.dat) file generator for recipe selection compatibility.
Mirrors CAT's ProfileDump/main.py CustomiseTmptravl() (lines 470-539).

The tmptravl file has sections delimited by markers:
  $$_BEGIN_SECTION_MAM / $$_END_SECTION_MAM
  $$_BEGIN_SECTION_CFGPN / $$_END_SECTION_CFGPN
  $$_BEGIN_SECTION_MCTO / $$_END_SECTION_MCTO
  $$_BEGIN_SECTION_EQUIPMENT / $$_END_SECTION_EQUIPMENT
  $$_BEGIN_SECTION_DRIVE_INFO / $$_END_SECTION_DRIVE_INFO
  $$_BEGIN_SECTION_RAW_VALUES / $$_END_SECTION_RAW_VALUES
  $$_BEGIN_SECTION_RECIPE_SELECTION / $$_END_SECTION_RECIPE_SELECTION

Within each section, attributes are written as "KEY: VALUE" lines.
Outside sections, specific keys can be replaced via a constant_dict.

IMPORTANT: The RAW_VALUES section contains nested MCTO and CFGPN sub-sections
with space-separated values (vs underscore-separated in the main sections).
These must be rebuilt from the actual data to avoid stale template data leaking
through. The RECIPE_SELECTION section is cleared so recipe_selection.py can
write fresh results at runtime.
"""
import os
import re
import shutil
import logging
from typing import Dict, Optional, List, Tuple

logger = logging.getLogger("bento_app")

# Keys whose SAP values use underscores but RAW_VALUES needs spaces.
# e.g. SAP returns "MASS_FLASH" but RAW_VALUES needs "MASS FLASH".
_RAW_VALUE_SPACE_KEYS = {
    "ARCHITECTURE", "FAMILY", "I_O_SUPPLY_VOLTAGE", "MFG_PLANNING_GROUP",
    "MICRON_DEVICE_NAME", "PRODUCT_FAMILY", "PRODUCT_GROUP",
    "PRODUCT_TECHNOLOGY", "PROJECT_NAME", "SUPPLY_VOLTAGE",
    "CUST_SERIAL_FORMAT", "FIDB_CERTIFICATION_NUM_2",
    "FIDB_MARKING_ON_TRAY", "FIDB_MODULE_SPEED", "FIDB_MOD_FORM_FACTOR",
    "FIDB_PRODUCT_NAME", "FIDB_SN_BYTE_PAD", "LED_BEHAVIOR",
    "MICRON_SERIAL_FORMAT", "BUILDDAT",
}

# Path to template
_RESOURCE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources")
_TEMPLATE_PATH = os.path.join(_RESOURCE_DIR, "template_tmptravl.dat")


class TmptravlGenerator:
    """
    Generates customized tmptravl .dat files for recipe selection.

    Mirrors CAT's CustomiseTmptravl() flow:
    1. Copy template to per-MID file
    2. Replace section contents with real data
    3. Replace specific keys outside sections with constant values
    """

    def __init__(self, output_dir: str, template_path: str = ""):
        """
        Args:
            output_dir: Directory to write generated tmptravl files
            template_path: Path to template_tmptravl.dat (defaults to bundled template)
        """
        self._template = template_path or _TEMPLATE_PATH
        self._output_dir = output_dir
        os.makedirs(self._output_dir, exist_ok=True)

        if not os.path.exists(self._template):
            logger.warning(f"Template tmptravl not found at {self._template}")

    def generate(
        self,
        mid: str,
        step: str,
        mam_dict: Optional[Dict[str, str]] = None,
        cfgpn_dict: Optional[Dict[str, str]] = None,
        mcto_dict: Optional[Dict[str, str]] = None,
        drive_info_dict: Optional[Dict[str, str]] = None,
        constant_dict: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        Generate a customized tmptravl file for a single MID.

        Mirrors CAT's CustomiseTmptravl() (ProfileDump/main.py:470-539).

        Args:
            mid: The MID identifier
            step: Test step (ABIT, SFN2, SCHP)
            mam_dict: MAM section attributes (from GetMAM or defaults)
            cfgpn_dict: CFGPN section attributes (from SAP or defaults)
            mcto_dict: MCTO section attributes (from SAP or defaults)
            drive_info_dict: DRIVE_INFO attributes (STEP, STEP_ID, LOT, MID)
            constant_dict: Key-value pairs to replace outside sections
                          (LOT, DIB_TYPE, MACHINE_MODEL, etc.)

        Returns:
            Path to the generated tmptravl file
        """
        mam_dict = mam_dict or {}
        cfgpn_dict = cfgpn_dict or {}
        mcto_dict = mcto_dict or {}
        drive_info_dict = drive_info_dict or {}
        constant_dict = constant_dict or {}

        # Inject STEP into MAM section if not already present.
        # The rule engine's PENANG_PROGRAM_RECIPE (and other site-specific
        # recipe tables) use STEP? as a dependency column.  In production
        # CAT, STEP comes from the lot's MAM data.  Since BENTO may not
        # have MAM access, we derive it from the `step` parameter.
        if step and "STEP" not in mam_dict:
            mam_dict["STEP"] = step.upper()

        # Inject LOT into MAM section if not already present.
        # Like STEP, LOT? is a dependency column in site-specific recipe
        # tables (e.g. PENANG_PROGRAM_RECIPE).  The meets_criteria() logic
        # in attributes.py treats '*' as NOT matching UNDEFINED_ATTR — only
        # '~' matches undefined.  So even catch-all rows with '*' for LOT
        # will fail if LOT is missing from the tmptravl.  In production CAT,
        # LOT comes from MAM lot data; here we pull it from constant_dict.
        if constant_dict.get("LOT") and "LOT" not in mam_dict:
            mam_dict["LOT"] = constant_dict["LOT"]

        # Inject CFGPN-only attributes into MAM section.
        # recipe_selection.py pops the entire CFGPN section from the
        # tmptravl dict BEFORE loading attributes into CESI (the central
        # environment system information).  Only SAP_EXCEPTION keys
        # (DENSITY, MFG_STATUS, PROD_CLASSIFICATION) are put back.
        # This means attributes that exist ONLY in the CFGPN section
        # (not duplicated in MAM or MCTO) become UNDEFINED_ATTR in the
        # rule engine's Solutions dict.  Since the rule tables use '*'
        # (requires DEFINED) for these columns, the table lookup fails
        # with "Hit the end of the table".
        #
        # Affected attributes:
        #   BASE_CFGPN  — used by PENANG_PROGRAM_RECIPE and PENANG_JOBPATH
        #   FW_WAVE_ID  — used by PENANG_JOBPATH (some product rows)
        _cfgpn_to_mam = ["BASE_CFGPN", "FW_WAVE_ID"]
        for attr in _cfgpn_to_mam:
            if cfgpn_dict.get(attr) and attr not in mam_dict:
                mam_dict[attr] = cfgpn_dict[attr]

        # Create per-MID filename: {MID}_tmptravl_{STEP}.dat
        filename = f"{mid}_tmptravl_{step}.dat"
        output_path = os.path.join(self._output_dir, filename)

        # Step 1: Copy template
        if not os.path.exists(self._template):
            raise FileNotFoundError(f"Template tmptravl not found: {self._template}")

        shutil.copy(self._template, output_path)
        logger.info(f"Creating customised tmptravl for {filename}")

        # Step 2: Customize sections
        # Mirrors CAT's CustomiseTmptravl line-by-line parsing
        #
        # CRITICAL: The template has a $$_BEGIN_SECTION_RAW_VALUES section that
        # contains NESTED $$_BEGIN_SECTION_MCTO and $$_BEGIN_SECTION_CFGPN
        # sub-sections.  Without tracking RAW_VALUES, the nested sections get
        # replaced with real data while the RAW_VALUES header retains stale
        # template data — causing recipe_selection.py to see contradictory
        # attributes and fail.
        #
        # Fix: Track within_raw_values.  When inside RAW_VALUES, rebuild the
        # entire section from actual CFGPN/MCTO data (with space-separated
        # values matching the RAW_VALUES format).  Also clear the
        # RECIPE_SELECTION section so recipe_selection.py writes fresh results.
        with open(output_path, 'r+') as f:
            content = f.readlines()
            f.seek(0)
            f.truncate()

            within_cfgpn = False
            within_mam = False
            within_mcto = False
            within_equipment = False
            within_drive_info = False
            within_raw_values = False
            within_recipe_selection = False

            for line in content:
                # ── RAW_VALUES Section ──
                # This section contains nested MCTO/CFGPN sub-sections with
                # space-separated values.  We rebuild it entirely from actual
                # data to prevent stale template data from leaking through.
                if "$$_BEGIN_SECTION_RAW_VALUES" in line:
                    within_raw_values = True
                    f.write(line)
                    # Write RAW_VALUES header: space-separated versions of
                    # select CFGPN attributes (mirrors CAT's raw value format)
                    self._write_raw_values_header(f, cfgpn_dict, mam_dict)
                    f.write("\n")
                    # Write nested MCTO sub-section with space-separated values
                    f.write("$$_BEGIN_SECTION_MCTO\n")
                    self._write_raw_values_mcto(f, mcto_dict)
                    f.write("$$_END_SECTION_MCTO\n")
                    f.write("\n")
                    # Write nested CFGPN sub-section with space-separated values
                    f.write("$$_BEGIN_SECTION_CFGPN\n")
                    self._write_raw_values_cfgpn(f, cfgpn_dict, mam_dict)
                    f.write("$$_END_SECTION_CFGPN\n")
                    continue
                elif "$$_END_SECTION_RAW_VALUES" in line:
                    within_raw_values = False
                    f.write(line)
                    continue
                elif within_raw_values:
                    # Skip all template content inside RAW_VALUES — we already
                    # wrote the rebuilt content above.
                    continue

                # ── RECIPE_SELECTION Section ──
                # Clear this section so recipe_selection.py writes fresh
                # results at runtime.  Keep the section markers but remove
                # all stale content between them.
                if "$$_BEGIN_SECTION_RECIPE_SELECTION" in line:
                    within_recipe_selection = True
                    f.write(line)
                    continue
                elif "$$_END_SECTION_RECIPE_SELECTION" in line:
                    within_recipe_selection = False
                    f.write(line)
                    continue
                elif within_recipe_selection:
                    # Skip stale recipe selection content
                    continue

                # ── CFGPN Section (top-level only) ──
                if "$$_BEGIN_SECTION_CFGPN" in line:
                    within_cfgpn = True
                    f.write(line)
                    for key, value in cfgpn_dict.items():
                        if key == "PRODUCT_NAME":
                            f.write(f"{key}: {value.replace(' ', '_')}\n")
                        else:
                            f.write(f"{key}: {value}\n")
                elif "$$_END_SECTION_CFGPN" in line:
                    within_cfgpn = False
                    f.write(line)

                # ── MCTO Section (top-level only) ──
                elif "$$_BEGIN_SECTION_MCTO" in line:
                    within_mcto = True
                    f.write(line)
                    for key, value in mcto_dict.items():
                        if key == "PRODUCT_NAME":
                            f.write(f"{key}: {value.replace(' ', '_')}\n")
                        else:
                            f.write(f"{key}: {value}\n")
                elif "$$_END_SECTION_MCTO" in line:
                    within_mcto = False
                    f.write(line)

                # ── MAM Section ──
                elif "$$_BEGIN_SECTION_MAM" in line:
                    within_mam = True
                    f.write(line)
                    for key, value in mam_dict.items():
                        f.write(f"{key}: {value}\n")
                elif "$$_END_SECTION_MAM" in line:
                    within_mam = False
                    # Write DRIVE_INFO at end of MAM section (before END marker)
                    # Mirrors CAT: drive_info_dict written just before $$_END_SECTION_MAM
                    for key, value in drive_info_dict.items():
                        f.write(f"{key}: {value}\n")
                    f.write(line)

                # ── Outside sections: replace matching keys ──
                elif not within_cfgpn and not within_mam and not within_mcto:
                    attr = re.split(r':\s*', line)[0]
                    if attr in constant_dict:
                        line = f"{attr}: {constant_dict[attr]}\n"
                    f.write(line)

        logger.info(f"Customised tmptravl written: {output_path}")
        return output_path

    @staticmethod
    def _to_raw_value(key: str, value: str) -> str:
        """Convert an underscore-separated SAP value to space-separated RAW_VALUES format.

        RAW_VALUES uses human-readable space-separated values, e.g.:
          SAP:        MASS_FLASH  →  RAW_VALUES: MASS FLASH
          SAP:        7600_MAX    →  RAW_VALUES: 7600 MAX

        Only keys in _RAW_VALUE_SPACE_KEYS are converted; others pass through as-is.
        """
        if key in _RAW_VALUE_SPACE_KEYS:
            return value.replace("_", " ")
        return value

    @staticmethod
    def _write_raw_values_header(
        f,
        cfgpn_dict: Dict[str, str],
        mam_dict: Dict[str, str],
    ):
        """Write the RAW_VALUES header block (non-nested attributes).

        These are space-separated versions of select CFGPN/MAM attributes.
        Mirrors the structure seen in production tmptravl files.
        """
        # Keys to include in the RAW_VALUES header, sourced from CFGPN first,
        # then MAM as fallback.  Order matches production files.
        _HEADER_KEYS = [
            "ARCHITECTURE", "FAMILY", "I_O_SUPPLY_VOLTAGE",
            "MFG_PLANNING_GROUP", "MICRON_DEVICE_NAME", "PRODUCT_FAMILY",
            "PRODUCT_GROUP", "PRODUCT_TECHNOLOGY", "PROJECT_NAME",
            "SUPPLY_VOLTAGE", "CUST_SERIAL_FORMAT",
            "FIDB_CERTIFICATION_NUM_2", "FIDB_MARKING_ON_TRAY",
            "FIDB_MODULE_SPEED", "FIDB_MOD_FORM_FACTOR",
            "FIDB_PRODUCT_NAME", "FIDB_SN_BYTE_PAD", "LED_BEHAVIOR",
            "MICRON_SERIAL_FORMAT", "BUILDDAT",
        ]
        for key in _HEADER_KEYS:
            value = cfgpn_dict.get(key) or mam_dict.get(key, "")
            if value:
                f.write(f"{key}: {TmptravlGenerator._to_raw_value(key, value)}\n")

    @staticmethod
    def _write_raw_values_mcto(f, mcto_dict: Dict[str, str]):
        """Write the nested MCTO sub-section inside RAW_VALUES.

        Uses space-separated values for keys in _RAW_VALUE_SPACE_KEYS.
        Only includes the subset of MCTO keys that appear in RAW_VALUES.
        """
        _MCTO_RAW_KEYS = [
            "ARCHITECTURE", "FAMILY", "I_O_SUPPLY_VOLTAGE",
            "MFG_PLANNING_GROUP", "MICRON_DEVICE_NAME", "PRODUCT_FAMILY",
            "PRODUCT_GROUP", "PRODUCT_TECHNOLOGY", "PROJECT_NAME",
            "SUPPLY_VOLTAGE",
        ]
        for key in _MCTO_RAW_KEYS:
            value = mcto_dict.get(key, "")
            if value:
                f.write(f"{key}: {TmptravlGenerator._to_raw_value(key, value)}\n")

    @staticmethod
    def _write_raw_values_cfgpn(
        f,
        cfgpn_dict: Dict[str, str],
        mam_dict: Dict[str, str],
    ):
        """Write the nested CFGPN sub-section inside RAW_VALUES.

        Uses space-separated values for keys in _RAW_VALUE_SPACE_KEYS.
        Sources from cfgpn_dict first, then mam_dict as fallback.
        """
        _CFGPN_RAW_KEYS = [
            "ARCHITECTURE", "CUST_SERIAL_FORMAT", "FAMILY",
            "FIDB_CERTIFICATION_NUM_2", "FIDB_MARKING_ON_TRAY",
            "FIDB_MODULE_SPEED", "FIDB_MOD_FORM_FACTOR",
            "FIDB_PRODUCT_NAME", "FIDB_SN_BYTE_PAD",
            "I_O_SUPPLY_VOLTAGE", "LED_BEHAVIOR", "MFG_PLANNING_GROUP",
            "MICRON_DEVICE_NAME", "MICRON_SERIAL_FORMAT", "PRODUCT_FAMILY",
            "PRODUCT_GROUP", "PRODUCT_TECHNOLOGY", "PROJECT_NAME",
            "SUPPLY_VOLTAGE", "BUILDDAT",
        ]
        for key in _CFGPN_RAW_KEYS:
            value = cfgpn_dict.get(key) or mam_dict.get(key, "")
            if value:
                f.write(f"{key}: {TmptravlGenerator._to_raw_value(key, value)}\n")

    def generate_for_checkout(
        self,
        mid: str,
        step: str,
        lot: str,
        cfgpn: str,
        site: str,
        dib_type: str,
        machine_model: str,
        machine_vendor: str,
        step_name: str,
        step_id: str,
        mam_dict: Optional[Dict[str, str]] = None,
        cfgpn_dict: Optional[Dict[str, str]] = None,
        mcto_dict: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        High-level convenience method for checkout workflow.
        Builds the constant_dict and drive_info_dict automatically.

        Mirrors CAT's ProfileMass() lines 664-694.
        """
        # Build DRIVE_INFO dict (mirrors CAT ProfileMass lines 665-669)
        drive_info = {
            "STEP": step_name,
            "STEP_ID": step_id,
            "LOT": lot,
            "MID": mid,
        }

        # Build CONSTANT dict (mirrors CAT ProfileMass lines 671-688)
        constants = {
            "LOT": lot,
            "DIB_TYPE": dib_type,
            "DIB_TYPE_NAME": dib_type,
            "MACHINE_MODEL": machine_model,
            "MACHINE_VENDOR": machine_vendor,
            "SITE_NAME": site,
        }

        return self.generate(
            mid=mid,
            step=step,
            mam_dict=mam_dict,
            cfgpn_dict=cfgpn_dict,
            mcto_dict=mcto_dict,
            drive_info_dict=drive_info,
            constant_dict=constants,
        )

    def cleanup(self, tmptravl_path: str):
        """Remove a tmptravl file after successful recipe selection."""
        try:
            if os.path.exists(tmptravl_path):
                os.remove(tmptravl_path)
                logger.debug(f"Deleted tmptravl: {tmptravl_path}")
        except Exception as e:
            logger.warning(f"Failed to delete tmptravl {tmptravl_path}: {e}")

    def move_to_failing(self, tmptravl_path: str, failing_dir: str = ""):
        """
        Move a failed tmptravl to Failing_Tmptravl/ folder.
        Mirrors CAT ProfileMass lines 766-770.
        """
        fail_dir = failing_dir or os.path.join(self._output_dir, "Failing_Tmptravl")
        os.makedirs(fail_dir, exist_ok=True)
        try:
            dest = os.path.join(fail_dir, os.path.basename(tmptravl_path))
            shutil.move(tmptravl_path, dest)
            logger.info(f"Moved failed tmptravl to {dest}")
            return dest
        except Exception as e:
            logger.error(f"Failed to move tmptravl to failing dir: {e}")
            return ""
