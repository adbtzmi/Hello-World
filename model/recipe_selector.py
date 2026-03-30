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

# Fallback static map — copied from checkout_orchestrator.py lines ~102-106
_FALLBACK_RECIPE_MAP: Dict[str, str] = {
    "ABIT": r"RECIPE:PEREGRINE\ON_NEOSEM_ABIT.XML",
    "SFN2": r"RECIPE:PEREGRINE\ON_NEOSEM_SFN2.XML",
    "CNFG": r"RECIPE:PEREGRINE\ON_NEOSEM_CNFG.XML",
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

    def __repr__(self):
        return (f"RecipeResult(recipe={self.recipe_name!r}, "
                f"program_path={self.test_program_path!r}, "
                f"success={self.success})")


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

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self.recipe_folder,
            )
            result.raw_output = proc.stdout

            if proc.returncode != 0:
                result.error = f"recipe_selection.py exited with code {proc.returncode}: {proc.stderr}"
                logger.error(result.error)
                return result

            # Parse the output
            result = self._extract(proc.stdout, result)

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

        Mirrors CAT Extract() — looks for key=value lines:
        - RECIPE_SEL_PROGRAM_RECIPE=<recipe_name>
        - RECIPE_SEL_TEST_PROGRAM_PATH=<path>
        - RECIPE_SEL_FILE_COPY_PATHS_<n>=<path>

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
            if not line or '=' not in line:
                continue

            key, _, value = line.partition('=')
            key = key.strip()
            value = value.strip()

            if key == "RECIPE_SEL_PROGRAM_RECIPE":
                result.recipe_name = value
                logger.info(f"Recipe selected: {value}")
            elif key == "RECIPE_SEL_TEST_PROGRAM_PATH":
                result.test_program_path = value
                logger.info(f"Test program path: {value}")
            elif key.startswith("RECIPE_SEL_FILE_COPY_PATHS"):
                result.file_copy_paths[key] = value
                logger.debug(f"File copy path: {key}={value}")

        result.success = bool(result.recipe_name)
        if not result.success:
            result.error = "No RECIPE_SEL_PROGRAM_RECIPE found in output"
            logger.warning(result.error)

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
        if self.is_available and tmptravl_path and os.path.isfile(tmptravl_path):
            result = self.select_recipe(tmptravl_path, timeout)
            if result.success:
                return result
            logger.warning(f"Subprocess recipe selection failed, falling back to static map: {result.error}")

        # Fallback to static map
        result = RecipeResult()
        step_upper = step.upper() if step else ""
        recipe = _FALLBACK_RECIPE_MAP.get(step_upper, "")
        if recipe:
            result.recipe_name = recipe
            result.success = True
            logger.info(f"Using fallback recipe for step '{step_upper}': {recipe}")
        else:
            result.error = f"No fallback recipe found for step: {step_upper}"
            logger.warning(result.error)

        return result
