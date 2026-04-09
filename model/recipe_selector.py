"""Recipe Selection Engine — wraps the production recipe_selection.py subprocess.

Mirrors CAT ProfileDump/main.py ProfileRecipe() and Extract() methods.

**Primary approach**: Uses a fault-tolerant wrapper (``recipe_fault_tolerant.py``)
that monkey-patches the rule engine's ``calc_all_rules()`` to skip non-critical
site-specific rules (e.g., BOISE_PROGRAM_RECIPE when SITE_NAME=PENANG).

**Fallback chain**: wrapper → vanilla subprocess → TGZ scan → static RECIPE_MAP.
"""

import os
import re
import sys
import subprocess
import logging
import tarfile
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# Fallback static map — used when recipe_selection.py subprocess is unavailable.
# IMPORTANT: Recipe names MUST match the actual file paths on the tester.
# Verified against working tester XML (PION_U2_172E.xml):
#   RecipeFile = "RECIPE\PEREGRINEION_NEOSEM_ABIT.XML"
# The product name is "PEREGRINEION" (one word), NOT "PEREGRINE\ON" (subfolder).
# If upstream recipe_selection.py changes its output, update these entries.
_FALLBACK_RECIPE_MAP: Dict[str, dict] = {
    "ABIT": {
        "recipe_name": r"RECIPE\PEREGRINEION_NEOSEM_ABIT.XML",
        "file_copy_paths": {
            "RECIPE_SEL_FILE_COPY_PATHS_01": r"\\pgfsmodauto\modauto\release\ssd\config|OS\config",
        },
    },
    "SFN2": {
        "recipe_name": r"RECIPE\PEREGRINEION_NEOSEM_SFN2.XML",
        "file_copy_paths": {
            "RECIPE_SEL_FILE_COPY_PATHS_01": r"\\pgfsmodauto\modauto\release\ssd\config|OS\config",
        },
    },
    "CNFG": {
        "recipe_name": r"RECIPE\PEREGRINEION_NEOSEM_CNFG.XML",
        "file_copy_paths": {
            "RECIPE_SEL_FILE_COPY_PATHS_01": r"\\pgfsmodauto\modauto\release\ssd\config|OS\config",
        },
    },
    "SCHP": {
        "recipe_name": r"RECIPE\PEREGRINEION_NEOSEM_SCHP.XML",
        "file_copy_paths": {
            "RECIPE_SEL_FILE_COPY_PATHS_01": r"\\pgfsmodauto\modauto\release\ssd\config|OS\config",
        },
    },
}

# ---------------------------------------------------------------------------
# UNC base path for file copy operations (mirrors CAT recipe_selection.py)
# ---------------------------------------------------------------------------
_UNC_BASE = r"\\pgfsmodauto\modauto\release\ssd"


def build_file_copy_paths_from_cfgpn(cfgpn_attrs: Dict[str, str]) -> Dict[str, str]:
    """Build file_copy_paths from SAP CFGPN attributes.

    When recipe_selection.py subprocess is unavailable, this function
    constructs the same file_copy_paths that recipe_selection.py would
    produce by reading firmware paths from SAP CFGPN data.

    The CAT reference XML (production) has 6 AddtionalFileFolder entries:
      1. config (with CONFIG_HASH)     → OS\\config
      2. CHECKLIST_FILES               → OS\\checklist_files
      3. TAMT_FILES                    → OS\\tamt_files
      4. MFG_FW_FILE_PATH             → OS\\mfg_fw_files
      5. CUST_FW release package      → OS\\net_files
      6. vs_parser                     → OS\\vs_parser

    Parameters
    ----------
    cfgpn_attrs : dict
        SAP CFGPN attributes dict (from SAP query).

    Returns
    -------
    dict
        file_copy_paths dict with RECIPE_SEL_FILE_COPY_PATHS_01..06 keys.
    """
    paths: Dict[str, str] = {}

    # Helper: convert artifactory URL to UNC path
    # e.g. "https://boartifactory.micron.com/artifactory/ssdmfgtstparmperegrineion-generic-dev-local"
    #   → extract the repo name for path construction
    def _extract_artifactory_path(url: str) -> str:
        """Extract the path portion after /artifactory/ from a URL.

        Converts forward slashes to backslashes for UNC path compatibility.
        """
        if not url:
            return ""
        m = re.search(r'/artifactory/(.+)', url)
        if not m:
            return ""
        return m.group(1).rstrip('/').replace('/', '\\')

    # 1. config — always present, with CONFIG_HASH if available
    config_hash = cfgpn_attrs.get("CONFIG_HASH", "")
    if config_hash:
        paths["RECIPE_SEL_FILE_COPY_PATHS_01"] = (
            rf"{_UNC_BASE}\config\{config_hash}|OS\config"
        )
    else:
        paths["RECIPE_SEL_FILE_COPY_PATHS_01"] = (
            rf"{_UNC_BASE}\config|OS\config"
        )

    # 2. CHECKLIST_FILES — from TST_PARAM_PATH + MFG_TST_VERSION
    tst_param_path = _extract_artifactory_path(cfgpn_attrs.get("TST_PARAM_PATH", ""))
    mfg_tst_version = cfgpn_attrs.get("MFG_TST_VERSION", "")
    product_name = cfgpn_attrs.get("PRODUCT_NAME", "").replace(" ", "").replace("_", "")
    if tst_param_path and mfg_tst_version:
        # Build: \\pgfsmodauto\modauto\release\ssd\firmware\Released\artifactory\<repo>\<version>\<product>_mfg_tst_params-<version>\CHECKLIST_FILES
        checklist_path = (
            rf"{_UNC_BASE}\firmware\Released\artifactory\{tst_param_path}"
            rf"\{mfg_tst_version}\{product_name}_mfg_tst_params-{mfg_tst_version}\CHECKLIST_FILES"
        )
        paths["RECIPE_SEL_FILE_COPY_PATHS_02"] = f"{checklist_path}|OS\\checklist_files"

        # 3. TAMT_FILES — same base path, different subfolder
        tamt_path = (
            rf"{_UNC_BASE}\firmware\Released\artifactory\{tst_param_path}"
            rf"\{mfg_tst_version}\{product_name}_mfg_tst_params-{mfg_tst_version}\TAMT_FILES"
        )
        paths["RECIPE_SEL_FILE_COPY_PATHS_03"] = f"{tamt_path}|OS\\tamt_files"

    # 4. MFG_FW_FILE_PATH — from MFG_FW_PATH + MFG_FW_VERSION
    mfg_fw_path = _extract_artifactory_path(cfgpn_attrs.get("MFG_FW_PATH", ""))
    mfg_fw_version = cfgpn_attrs.get("MFG_FW_VERSION", "")
    if mfg_fw_path and mfg_fw_version:
        mfg_fw_full = (
            rf"{_UNC_BASE}\firmware\Released\artifactory\{mfg_fw_path}\{mfg_fw_version}"
        )
        paths["RECIPE_SEL_FILE_COPY_PATHS_04"] = f"{mfg_fw_full}|OS\\mfg_fw_files"

        # 6. vs_parser — same MFG_FW base + vs_parser subfolder
        vs_parser_path = rf"{mfg_fw_full}\vs_parser"
        paths["RECIPE_SEL_FILE_COPY_PATHS_06"] = f"{vs_parser_path}|OS\\vs_parser"

    # 5. net_files — from CUST_FW_PATH (release package directory)
    cust_fw_path = _extract_artifactory_path(cfgpn_attrs.get("CUST_FW_PATH", ""))
    if cust_fw_path:
        # Remove the .tar.bz2 extension to get the directory path
        cust_fw_dir = re.sub(r'\.tar\.bz2$', '', cust_fw_path, flags=re.IGNORECASE)
        net_files_path = (
            rf"{_UNC_BASE}\firmware\Released\artifactory\{cust_fw_dir}"
        )
        paths["RECIPE_SEL_FILE_COPY_PATHS_05"] = f"{net_files_path}|OS\\net_files"

    if paths:
        logger.info("Built %d file_copy_paths from CFGPN attributes", len(paths))
        for k, v in sorted(paths.items()):
            logger.debug("  %s = %s", k, v)
    else:
        logger.warning("Could not build file_copy_paths from CFGPN — missing firmware attributes")

    return paths


class RecipeResult:
    """Holds parsed recipe selection output."""
    def __init__(self):
        self.recipe_name: str = ""
        self.test_program_path: str = ""
        self.file_copy_paths: Dict[str, str] = {}
        self.raw_output: str = ""
        self.success: bool = False
        self.error: str = ""
        self.source: str = ""  # "subprocess" or "fallback" — indicates where the result came from

    def __repr__(self):
        return (f"RecipeResult(recipe={self.recipe_name!r}, "
                f"program_path={self.test_program_path!r}, "
                f"success={self.success}, source={self.source!r})")


class RecipeSelector:
    """Runs recipe_selection.py subprocess and parses results.

    Parameters
    ----------
    recipe_folder : str
        Path to the folder containing recipe_selection.py and CSV rule files.
    python2_exe : str, optional
        Path to Python 2 executable. Defaults to 'python2' on PATH.
    """

    def __init__(self, recipe_folder: str = "", python2_exe: str = ""):
        self.recipe_folder = recipe_folder
        self.python2_exe = python2_exe or "python2"
        self._script_path = os.path.join(recipe_folder, "recipe_selection.py") if recipe_folder else ""

    @property
    def is_available(self) -> bool:
        """Check if recipe_selection.py exists and is accessible."""
        return bool(self._script_path) and os.path.isfile(self._script_path)

    @property
    def _wrapper_script_path(self) -> str:
        """Path to the fault-tolerant wrapper script bundled with BENTO."""
        return os.path.join(os.path.dirname(__file__), "resources", "recipe_fault_tolerant.py")

    @property
    def _wrapper_available(self) -> bool:
        """Check if the fault-tolerant wrapper script exists."""
        return os.path.isfile(self._wrapper_script_path)

    # ------------------------------------------------------------------
    # Primary entry point — fault-tolerant wrapper
    # ------------------------------------------------------------------

    def select_recipe(self, tmptravl_path: str, timeout: int = 120) -> RecipeResult:
        """Run recipe selection subprocess against a tmptravl file.

        **Primary approach**: Uses the fault-tolerant wrapper script
        (``recipe_fault_tolerant.py``) which monkey-patches the rule engine
        to skip non-critical site-specific rules (e.g., BOISE_PROGRAM_RECIPE)
        that would otherwise crash the subprocess.

        **Fallback**: If the wrapper is unavailable, falls back to running
        ``recipe_selection.py`` directly (vanilla mode).

        Parameters
        ----------
        tmptravl_path : str
            Path to the customized .dat tmptravl file.
        timeout : int
            Subprocess timeout in seconds.

        Returns
        -------
        RecipeResult
            Parsed recipe selection output.
        """
        result = RecipeResult()

        if not self.is_available:
            result.error = f"recipe_selection.py not found at: {self._script_path}"
            logger.warning(result.error)
            return result

        if not os.path.isfile(tmptravl_path):
            result.error = f"Tmptravl file not found: {tmptravl_path}"
            logger.error(result.error)
            return result

        # ── Primary: fault-tolerant wrapper ────────────────────────────
        if self._wrapper_available:
            result = self._run_with_wrapper(tmptravl_path, timeout)
            if result.success:
                return result
            # Wrapper ran but failed — fall through to vanilla attempt
            wrapper_error = result.error
            logger.warning("Fault-tolerant wrapper did not produce a recipe — "
                           "falling back to vanilla subprocess: %s", wrapper_error)
        else:
            logger.info("Fault-tolerant wrapper not found at %s — using vanilla subprocess",
                        self._wrapper_script_path)

        # ── Fallback: vanilla subprocess (direct recipe_selection.py) ──
        result = self._run_vanilla(tmptravl_path, timeout)
        return result

    def _run_with_wrapper(self, tmptravl_path: str, timeout: int = 120) -> RecipeResult:
        """Run recipe selection via the fault-tolerant wrapper.

        The wrapper monkey-patches ``Solutions.calc_all_rules()`` to skip
        non-critical site-specific rules, then executes the real
        ``recipe_selection.py`` in the same process.

        Parameters
        ----------
        tmptravl_path : str
            Path to the customized .dat tmptravl file.
        timeout : int
            Subprocess timeout in seconds.

        Returns
        -------
        RecipeResult
            Parsed recipe selection output.
        """
        result = RecipeResult()

        # The wrapper expects: python <wrapper> <recipe_dir> <tmptravl> [--tt_format dat]
        cmd = [
            self.python2_exe,
            self._wrapper_script_path,
            self.recipe_folder,
            tmptravl_path,
            "--tt_format", "dat",
        ]
        logger.info("Running recipe selection (fault-tolerant wrapper): %s", " ".join(cmd))

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            result.raw_output = stdout

            # Log wrapper diagnostics from stderr
            for line in stderr.splitlines():
                if line.startswith("RECIPE_WRAPPER_INFO:"):
                    logger.info("Wrapper: %s", line)
                elif line.startswith("RECIPE_WRAPPER_WARNING:"):
                    logger.warning("Wrapper: %s", line)
                elif line.startswith("RECIPE_WRAPPER_ERROR:"):
                    logger.error("Wrapper: %s", line)

            logger.info("DIAG: Wrapper subprocess returncode=%d", proc.returncode)
            if stdout:
                logger.debug("DIAG: Wrapper stdout (first 2000 chars): %s", stdout[:2000])
            if stderr:
                logger.debug("DIAG: Wrapper stderr (first 2000 chars): %s", stderr[:2000])

            # Check for RECIPE_SEL lines in stdout
            has_recipe_sel_lines = any(
                line.strip().startswith("RECIPE_SEL") for line in stdout.splitlines()
            )

            if proc.returncode != 0:
                # Even with non-zero exit, check if we got recipe data
                if not has_recipe_sel_lines:
                    result.error = (f"Wrapper exited with code {proc.returncode}: "
                                    f"{stderr[:500] if stderr else 'no stderr'}")
                    logger.warning(result.error)
                else:
                    logger.info("Wrapper exited with code %d but produced RECIPE_SEL output — parsing",
                                proc.returncode)

            # Parse stdout output
            if has_recipe_sel_lines:
                result = self._extract(stdout, result)

            # If stdout parsing didn't find recipe, try tmptravl file
            if not result.recipe_name and os.path.isfile(tmptravl_path):
                logger.debug("DIAG: Wrapper stdout had no recipe — trying tmptravl file: %s", tmptravl_path)
                result = self._extract_from_tmptravl(tmptravl_path, result)
                if result.recipe_name:
                    result.source = "tmptravl"
                    logger.info("Recovered recipe from tmptravl after wrapper run: %s (file_copy_paths=%d)",
                                result.recipe_name, len(result.file_copy_paths))

            if result.success and not result.source:
                result.source = "subprocess"
                logger.info("✓ Recipe selection succeeded via fault-tolerant wrapper: %s", result.recipe_name)

        except subprocess.TimeoutExpired:
            result.error = f"Wrapper timed out after {timeout}s"
            logger.error(result.error)
        except FileNotFoundError:
            result.error = f"Python 2 executable not found: {self.python2_exe}"
            logger.error(result.error)
        except Exception as e:
            result.error = f"Wrapper execution failed: {e}"
            logger.exception(result.error)

        return result

    def _run_vanilla(self, tmptravl_path: str, timeout: int = 120) -> RecipeResult:
        """Run recipe selection directly (without fault-tolerant wrapper).

        This is the fallback path when the wrapper script is unavailable.
        Runs ``recipe_selection.py`` directly, which may fail on non-critical
        site-specific rules.

        Parameters
        ----------
        tmptravl_path : str
            Path to the customized .dat tmptravl file.
        timeout : int
            Subprocess timeout in seconds.

        Returns
        -------
        RecipeResult
            Parsed recipe selection output.
        """
        result = RecipeResult()

        cmd = [self.python2_exe, self._script_path, tmptravl_path]
        logger.info("Running recipe selection (vanilla): %s", " ".join(cmd))

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self.recipe_folder,
            )
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            result.raw_output = stdout

            # Detect known rule-lookup failures (e.g., BOISE_PROGRAM_RECIPE).
            _KNOWN_RULE_FAILURE_RE = re.compile(
                r"Hit the end of the table when looking up the value for rule (\S+)",
                re.IGNORECASE,
            )
            known_rule_match = _KNOWN_RULE_FAILURE_RE.search(stdout) or _KNOWN_RULE_FAILURE_RE.search(stderr)
            is_known_rule_failure = bool(known_rule_match)

            if is_known_rule_failure:
                failed_rule = known_rule_match.group(1)
                logger.info("Vanilla subprocess: rule '%s' not found in lookup table "
                            "(expected for this site/product)", failed_rule)
                logger.debug("DIAG: Vanilla subprocess returncode=%d", proc.returncode)
                logger.debug("DIAG: Vanilla stdout (first 2000 chars): %s", stdout[:2000] if stdout else "EMPTY")
                logger.debug("DIAG: Vanilla stderr (first 2000 chars): %s", stderr[:2000] if stderr else "EMPTY")
                result.error = f"Rule '{failed_rule}' not in lookup table (expected — using fallback)"
                result._known_rule_failure = True  # type: ignore[attr-defined]
            else:
                logger.info("DIAG: Vanilla subprocess returncode=%d", proc.returncode)
                if stdout:
                    logger.debug("DIAG: Vanilla stdout (first 2000 chars): %s", stdout[:2000])
                if stderr:
                    logger.warning("DIAG: Vanilla stderr (first 2000 chars): %s", stderr[:2000])

            has_recipe_sel_lines = any(
                line.strip().startswith("RECIPE_SEL") for line in stdout.splitlines()
            )

            if not has_recipe_sel_lines and not is_known_rule_failure:
                logger.warning("DIAG: Vanilla stdout contains NO RECIPE_SEL_* lines — will try tmptravl file")

            if proc.returncode != 0 and not is_known_rule_failure:
                result.error = f"recipe_selection.py exited with code {proc.returncode}: {stderr}"
                logger.error(result.error)

            # Parse stdout output
            if has_recipe_sel_lines:
                result = self._extract(stdout, result)

            # Try tmptravl file if stdout parsing didn't find recipe
            if not result.recipe_name and tmptravl_path and os.path.isfile(tmptravl_path):
                logger.debug("DIAG: Attempting to extract RECIPE_SEL_* from tmptravl file: %s", tmptravl_path)
                result = self._extract_from_tmptravl(tmptravl_path, result)
                if result.recipe_name:
                    result.source = "tmptravl"
                    logger.info("DIAG: Successfully recovered recipe from tmptravl file: %s (file_copy_paths=%d)",
                                result.recipe_name, len(result.file_copy_paths))

        except subprocess.TimeoutExpired:
            result.error = f"recipe_selection.py timed out after {timeout}s"
            logger.error(result.error)
        except FileNotFoundError:
            result.error = f"Python 2 executable not found: {self.python2_exe}"
            logger.error(result.error)
        except Exception as e:
            result.error = f"Recipe selection failed: {e}"
            logger.exception(result.error)

        return result

    def _extract(self, output: str, result: RecipeResult) -> RecipeResult:
        """Parse recipe selection stdout output.

        Mirrors CAT Extract() — looks for key:value lines (colon separator):
        - RECIPE_SEL_PROGRAM_RECIPE:<recipe_name>
        - RECIPE_SEL_TEST_PROGRAM_PATH:<path>
        - RECIPE_SEL_FILE_COPY_PATHS_<n>:<path>

        Also supports equals separator for backward compatibility.

        Parameters
        ----------
        output : str
            Raw stdout from recipe_selection.py.
        result : RecipeResult
            Result object to populate.

        Returns
        -------
        RecipeResult
            Populated result object.
        """
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue

            # CAT uses colon separator, but support both for flexibility
            if ':' in line:
                key, _, value = line.partition(':')
            elif '=' in line:
                key, _, value = line.partition('=')
            else:
                continue

            key = key.strip()
            value = value.strip()

            if key == "RECIPE_SEL_PROGRAM_RECIPE":
                result.recipe_name = value.upper()  # CAT uppercases
                logger.info(f"Recipe selected: {result.recipe_name}")
            elif key == "RECIPE_SEL_TEST_PROGRAM_PATH":
                result.test_program_path = value.upper()  # CAT uppercases
                logger.info(f"Test program path: {result.test_program_path}")
            elif key.startswith("RECIPE_SEL_FILE_COPY_PATHS"):
                result.file_copy_paths[key] = value  # NOT uppercased per CAT
                logger.debug(f"File copy path: {key}={value}")

        result.success = bool(result.recipe_name)
        if result.success:
            result.source = "subprocess"
        else:
            result.error = "No RECIPE_SEL_PROGRAM_RECIPE found in output"
            # Log at debug level — the caller handles user-facing messaging
            # and this is expected when the subprocess crashes on a known rule
            logger.debug(result.error)

        return result

    def _extract_from_tmptravl(self, tmptravl_path: str, result: RecipeResult) -> RecipeResult:
        """Parse RECIPE_SEL_* lines from the tmptravl file's RECIPE_SELECTION section.

        recipe_selection.py writes its results into the tmptravl file under
        $$_BEGIN_SECTION_RECIPE_SELECTION / $$_END_SECTION_RECIPE_SELECTION.
        This method extracts those results as a fallback when stdout parsing fails
        (e.g., when the subprocess crashes on a secondary rule after writing results).

        Parameters
        ----------
        tmptravl_path : str
            Path to the tmptravl .dat file.
        result : RecipeResult
            Result object to populate.

        Returns
        -------
        RecipeResult
            Populated result object.
        """
        try:
            with open(tmptravl_path, "r", encoding="utf-8", errors="replace") as f:
                in_recipe_section = False
                for line in f:
                    line = line.strip()
                    if line == "$$_BEGIN_SECTION_RECIPE_SELECTION":
                        in_recipe_section = True
                        continue
                    if line == "$$_END_SECTION_RECIPE_SELECTION":
                        in_recipe_section = False
                        continue
                    if not in_recipe_section or not line:
                        continue

                    # Parse key: value lines within the section
                    if ':' in line:
                        key, _, value = line.partition(':')
                    elif '=' in line:
                        key, _, value = line.partition('=')
                    else:
                        continue

                    key = key.strip()
                    value = value.strip()

                    if key == "RECIPE_SEL_PROGRAM_RECIPE":
                        result.recipe_name = value.upper()
                        logger.info("Tmptravl recipe: %s", result.recipe_name)
                    elif key == "RECIPE_SEL_TEST_PROGRAM_PATH":
                        result.test_program_path = value.upper()
                        logger.info("Tmptravl test program path: %s", result.test_program_path)
                    elif key.startswith("RECIPE_SEL_FILE_COPY_PATHS"):
                        result.file_copy_paths[key] = value
                        logger.debug("Tmptravl file copy path: %s=%s", key, value)
                    elif key.startswith("RECIPE_SEL_"):
                        # Capture other RECIPE_SEL_* keys (e.g., BINS, BIN_DEFS, etc.)
                        logger.debug("Tmptravl recipe key: %s=%s", key, value)

            result.success = bool(result.recipe_name)
            if not result.success:
                result.error = "No RECIPE_SEL_PROGRAM_RECIPE found in tmptravl file"
                # Log at debug level — the caller handles user-facing messaging
                logger.debug(result.error)

        except Exception as e:
            logger.warning("Failed to parse tmptravl file for recipe data: %s", e)
            result.error = f"Tmptravl parsing failed: {e}"

        return result

    def select_recipe_or_fallback(self, tmptravl_path: str, step: str,
                                  timeout: int = 120,
                                  tgz_path: str = "",
                                  recipe_override: str = "",
                                  cfgpn_attrs: Optional[Dict[str, str]] = None) -> RecipeResult:
        """Try subprocess recipe selection; fall back to TGZ scan, then static map.

        When subprocess fails, builds file_copy_paths from SAP CFGPN attributes
        so that AddtionalFileFolder entries match production output.

        Parameters
        ----------
        tmptravl_path : str
            Path to the customized .dat tmptravl file.
        step : str
            Step name (e.g., 'ABIT', 'SFN2') for fallback lookup.
        timeout : int
            Subprocess timeout in seconds.
        tgz_path : str
            Path to the TGZ archive. Used for TGZ recipe scanning when
            subprocess recipe selection is unavailable.
        recipe_override : str
            User-specified recipe override (e.g., "RECIPE\\Condor_neosem_ABIT.XML").
            When provided, skips all recipe selection logic and uses this directly.
        cfgpn_attrs : dict, optional
            SAP CFGPN attributes dict. When provided and subprocess fails,
            used to build file_copy_paths dynamically from firmware paths.

        Returns
        -------
        RecipeResult
            Recipe selection result (from override, subprocess, TGZ scan, or fallback).
        """
        logger.info("DIAG: select_recipe_or_fallback called - is_available=%s, tmptravl_path=%s, "
                     "tmptravl_exists=%s, step=%s, tgz_path=%s, recipe_override=%s, "
                     "cfgpn_attrs_count=%d",
                     self.is_available, tmptravl_path,
                     os.path.isfile(tmptravl_path) if tmptravl_path else False,
                     step, tgz_path, recipe_override,
                     len(cfgpn_attrs) if cfgpn_attrs else 0)

        # ── Priority 0: User-specified recipe override ─────────────────
        if recipe_override and recipe_override.strip():
            result = RecipeResult()
            result.recipe_name = recipe_override.strip().upper()
            # Build file_copy_paths from CFGPN if available, else minimal config-only
            if cfgpn_attrs:
                result.file_copy_paths = build_file_copy_paths_from_cfgpn(cfgpn_attrs)
            if not result.file_copy_paths:
                result.file_copy_paths = {
                    "RECIPE_SEL_FILE_COPY_PATHS_01": r"\\pgfsmodauto\modauto\release\ssd\config|OS\config",
                }
            # Set test_program_path from tgz_path when available
            if tgz_path and tgz_path.strip():
                result.test_program_path = tgz_path.strip()
            result.success = True
            result.source = "user_override"
            logger.info("Using user-specified recipe override: %s (file_copy_paths=%d)",
                        result.recipe_name, len(result.file_copy_paths))
            return result

        # ── Priority 1: Subprocess recipe selection (wrapper → vanilla) ─
        is_known_rule_failure = False
        if self.is_available and tmptravl_path and os.path.isfile(tmptravl_path):
            result = self.select_recipe(tmptravl_path, timeout)
            if result.success:
                # Preserve source set by select_recipe() — "subprocess" or "tmptravl"
                if not result.source:
                    result.source = "subprocess"
                logger.info("✓ Recipe selection SUCCEEDED - recipe=%s, file_copy_paths_count=%d, source=%s",
                            result.recipe_name, len(result.file_copy_paths), result.source)
                return result

            # Check if this was a known rule-lookup failure (e.g., BOISE_PROGRAM_RECIPE
            # not in the CSV table for this site/product). This is expected for certain
            # configurations — the TGZ scan will resolve the recipe correctly.
            is_known_rule_failure = getattr(result, '_known_rule_failure', False)
            if is_known_rule_failure:
                logger.info("Subprocess recipe table incomplete for this config — "
                            "resolving recipe from TGZ archive instead")
            else:
                logger.warning(f"Subprocess recipe selection failed, falling back: {result.error}")
                logger.warning("DIAG: Subprocess recipe selection FAILED - error: %s", result.error)
        else:
            logger.info("DIAG: Subprocess recipe selection SKIPPED - is_available=%s, tmptravl_path=%r, tmptravl_exists=%s",
                        self.is_available, tmptravl_path, os.path.isfile(tmptravl_path) if tmptravl_path else False)

        # ── Build SAP-based file_copy_paths for fallback scenarios ──────
        # When subprocess fails, use CFGPN attributes to build the same
        # file_copy_paths that recipe_selection.py would have produced.
        sap_file_copy_paths: Dict[str, str] = {}
        if cfgpn_attrs:
            sap_file_copy_paths = build_file_copy_paths_from_cfgpn(cfgpn_attrs)
            logger.info("DIAG: Built %d SAP-based file_copy_paths from CFGPN attributes",
                        len(sap_file_copy_paths))

        # ── Priority 2: TGZ recipe scan ────────────────────────────────
        step_upper = step.upper() if step else ""
        if tgz_path and tgz_path.strip():
            tgz_recipe = scan_tgz_for_recipe(tgz_path.strip(), step_upper)
            if tgz_recipe:
                result = RecipeResult()
                result.recipe_name = tgz_recipe
                # Use SAP-based paths if available, else minimal config-only
                result.file_copy_paths = sap_file_copy_paths or {
                    "RECIPE_SEL_FILE_COPY_PATHS_01": r"\\pgfsmodauto\modauto\release\ssd\config|OS\config",
                }
                # Set test_program_path from tgz_path
                result.test_program_path = tgz_path.strip()
                result.success = True
                result.source = "tgz_scan"
                if is_known_rule_failure:
                    logger.info("✓ Recipe resolved via TGZ scan for step '%s': %s "
                                "(file_copy_paths=%d, SAP-enriched=%s)",
                                step_upper, result.recipe_name,
                                len(result.file_copy_paths), bool(sap_file_copy_paths))
                else:
                    logger.info("Using TGZ-scanned recipe for step '%s': %s (file_copy_paths=%d, from %s)",
                                step_upper, result.recipe_name, len(result.file_copy_paths), tgz_path)
                return result
            else:
                logger.warning("DIAG: TGZ scan found no matching recipe for step '%s' in %s",
                               step_upper, tgz_path)

        # ── Priority 3: Static fallback map ────────────────────────────
        result = RecipeResult()
        fallback_entry = _FALLBACK_RECIPE_MAP.get(step_upper)
        if fallback_entry:
            result.recipe_name = fallback_entry["recipe_name"]
            # Prefer SAP-based paths over static fallback paths
            result.file_copy_paths = sap_file_copy_paths or dict(fallback_entry.get("file_copy_paths", {}))
            # Set test_program_path from tgz_path if available
            if tgz_path and tgz_path.strip():
                result.test_program_path = tgz_path.strip()
            result.success = True
            result.source = "fallback"
            logger.info("Using fallback recipe for step '%s': %s (file_copy_paths=%d, sap_enriched=%s)",
                        step_upper, result.recipe_name, len(result.file_copy_paths),
                        bool(sap_file_copy_paths))
            if not sap_file_copy_paths:
                logger.warning("DIAG: Using static fallback file_copy_paths (no CFGPN attrs) — "
                               "AddtionalFileFolder will have only config entry")
        else:
            result.error = f"No fallback recipe found for step: {step_upper}"
            logger.warning(result.error)

        return result


# ═══════════════════════════════════════════════════════════════════════════
# TGZ RECIPE SCANNER
# ═══════════════════════════════════════════════════════════════════════════

def scan_tgz_recipes(tgz_path: str) -> List[str]:
    """Scan a TGZ archive and return all recipe XML filenames in the recipe/ folder.

    Parameters
    ----------
    tgz_path : str
        Path to the .tgz archive file.

    Returns
    -------
    list of str
        Recipe filenames (e.g., ["Condor_neosem_ABIT.xml", "Raven_neosem_ABIT.xml"]).
        Returns empty list if TGZ is not accessible or has no recipe/ folder.
    """
    recipes = []
    if not tgz_path or not os.path.isfile(tgz_path):
        logger.warning("scan_tgz_recipes: TGZ not found: %s", tgz_path)
        return recipes

    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            for member in tar.getmembers():
                name_lower = member.name.lower()
                # Match files in recipe/ folder that end with .xml
                if (name_lower.startswith("recipe/") and
                        name_lower.endswith(".xml") and
                        "/" not in name_lower[7:]):  # No subdirectories
                    # Extract just the filename
                    fname = os.path.basename(member.name)
                    recipes.append(fname)
    except Exception as e:
        logger.warning("scan_tgz_recipes: Failed to read TGZ %s: %s", tgz_path, e)

    logger.info("scan_tgz_recipes: Found %d recipe(s) in %s", len(recipes), tgz_path)
    return sorted(recipes)


def scan_tgz_for_recipe(tgz_path: str, step: str) -> str:
    """Scan a TGZ and find the best matching recipe for the given step.

    Matching logic:
      1. Look for recipes ending with _{step}.xml (case-insensitive)
      2. Among matches, prefer *_neosem_* recipes (NEOSEM tester)
      3. If multiple matches, pick the first alphabetically

    Parameters
    ----------
    tgz_path : str
        Path to the .tgz archive file.
    step : str
        Step name (e.g., 'ABIT', 'SFN2', 'CNFG').

    Returns
    -------
    str
        Recipe path in RECIPE\\<filename>.XML format (uppercased),
        or empty string if no match found.
    """
    if not step:
        return ""

    recipes = scan_tgz_recipes(tgz_path)
    if not recipes:
        return ""

    step_upper = step.upper()
    step_suffix = f"_{step_upper}.XML"

    # Find all recipes matching the step
    matches = [r for r in recipes if r.upper().endswith(step_suffix)]

    if not matches:
        logger.warning("scan_tgz_for_recipe: No recipe matching step '%s' in %s. "
                        "Available: %s", step, tgz_path, recipes[:10])
        return ""

    # Prefer *_neosem_* recipes
    neosem_matches = [r for r in matches if "_neosem_" in r.lower()]
    if neosem_matches:
        matches = neosem_matches

    # Pick first alphabetically for deterministic results
    best = sorted(matches)[0]
    result = f"RECIPE\\{best}".upper()
    logger.info("scan_tgz_for_recipe: Selected '%s' for step '%s' from %d candidate(s)",
                result, step, len(matches))
    return result
