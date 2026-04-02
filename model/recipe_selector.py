"""Recipe Selection Engine — wraps the production recipe_selection.py subprocess.

Mirrors CAT ProfileDump/main.py ProfileRecipe() and Extract() methods.
When recipe_selection.py is not available (e.g., dev environments), falls back
to the static RECIPE_MAP that currently lives in checkout_orchestrator.py.
"""

import os
import sys
import subprocess
import logging
from typing import Optional, Dict, Any

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

    def select_recipe(self, tmptravl_path: str, timeout: int = 120) -> RecipeResult:
        """Run recipe selection subprocess against a tmptravl file.

        Mirrors CAT ProfileRecipe() — runs:
            [python2_exe, recipe_selection.py, tmptravl_path]

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

        cmd = [self.python2_exe, self._script_path, tmptravl_path]
        logger.info(f"Running recipe selection: {' '.join(cmd)}")
        logger.info("DIAG: RecipeSelector - script=%s, python2=%s, tmptravl=%s", self._script_path, self.python2_exe, tmptravl_path)

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self.recipe_folder,
            )
            stdout = proc.stdout
            stderr = proc.stderr
            result.raw_output = stdout
            logger.warning("DIAG: RecipeSelector subprocess returncode=%d", proc.returncode)
            logger.warning("DIAG: RecipeSelector stdout (first 2000 chars): %s", stdout[:2000] if stdout else "EMPTY")
            logger.warning("DIAG: RecipeSelector stderr (first 2000 chars): %s", stderr[:2000] if stderr else "EMPTY")

            # Fix 8: Warn when stdout contains no RECIPE_SEL lines
            if not any(line.strip().startswith("RECIPE_SEL") for line in (stdout or "").splitlines()):
                logger.warning("DIAG: Subprocess stdout contains NO RECIPE_SEL_* lines — will try tmptravl file")

            if proc.returncode != 0:
                result.error = f"recipe_selection.py exited with code {proc.returncode}: {stderr}"
                logger.error(result.error)
                # Don't return yet — try tmptravl file parsing below

            # Parse the stdout output
            if proc.returncode == 0:
                result = self._extract(proc.stdout, result)

            # If stdout parsing failed (no RECIPE_SEL_PROGRAM_RECIPE found),
            # try parsing the tmptravl file itself. recipe_selection.py writes
            # RECIPE_SEL_* results into the $$_BEGIN_SECTION_RECIPE_SELECTION
            # section of the tmptravl file before it may crash on secondary rules
            # (e.g., BOISE_PROGRAM_RECIPE). This recovers the recipe + file_copy_paths.
            if not result.recipe_name and tmptravl_path and os.path.isfile(tmptravl_path):
                logger.info("DIAG: Attempting to extract RECIPE_SEL_* from tmptravl file: %s", tmptravl_path)
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
            logger.warning(result.error)

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
                logger.warning(result.error)

        except Exception as e:
            logger.warning("Failed to parse tmptravl file for recipe data: %s", e)
            result.error = f"Tmptravl parsing failed: {e}"

        return result

    def select_recipe_or_fallback(self, tmptravl_path: str, step: str, timeout: int = 120) -> RecipeResult:
        """Try subprocess recipe selection; fall back to static map if unavailable.

        Parameters
        ----------
        tmptravl_path : str
            Path to the customized .dat tmptravl file.
        step : str
            Step name (e.g., 'ABIT', 'SFN2') for fallback lookup.
        timeout : int
            Subprocess timeout in seconds.

        Returns
        -------
        RecipeResult
            Recipe selection result (from subprocess or fallback).
        """
        logger.info("DIAG: select_recipe_or_fallback called - is_available=%s, tmptravl_path=%s, tmptravl_exists=%s, step=%s",
                     self.is_available, tmptravl_path, os.path.isfile(tmptravl_path) if tmptravl_path else False, step)

        if self.is_available and tmptravl_path and os.path.isfile(tmptravl_path):
            result = self.select_recipe(tmptravl_path, timeout)
            if result.success:
                # Preserve source set by select_recipe() — "subprocess" or "tmptravl"
                if not result.source:
                    result.source = "subprocess"
                logger.info("DIAG: Recipe selection SUCCEEDED - recipe=%s, file_copy_paths_count=%d, source=%s",
                            result.recipe_name, len(result.file_copy_paths), result.source)
                return result
            logger.warning(f"Subprocess recipe selection failed, falling back to static map: {result.error}")
            logger.warning("DIAG: Subprocess recipe selection FAILED - error: %s", result.error)
        else:
            logger.warning("DIAG: Subprocess recipe selection SKIPPED - is_available=%s, tmptravl_path=%r, tmptravl_exists=%s",
                           self.is_available, tmptravl_path, os.path.isfile(tmptravl_path) if tmptravl_path else False)

        # Fallback to static map (now includes file_copy_paths)
        result = RecipeResult()
        step_upper = step.upper() if step else ""
        fallback_entry = _FALLBACK_RECIPE_MAP.get(step_upper)
        if fallback_entry:
            result.recipe_name = fallback_entry["recipe_name"]
            result.file_copy_paths = dict(fallback_entry.get("file_copy_paths", {}))
            result.success = True
            result.source = "fallback"
            logger.info(f"Using fallback recipe for step '{step_upper}': {result.recipe_name}")
            logger.warning("DIAG: Using fallback recipe_name='%s' for step='%s', "
                           "file_copy_paths_count=%d — verify paths match .rul file output",
                           result.recipe_name, step, len(result.file_copy_paths))
        else:
            result.error = f"No fallback recipe found for step: {step_upper}"
            logger.warning(result.error)

        return result
