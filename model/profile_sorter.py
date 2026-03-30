"""Profile Sorter — organizes generated XML profiles into tester/recipe folders.

Mirrors CAT ProfileDump/main.py ProfileSort() and ProfileClean() methods.
After XML generation, sorts output files into structured directories:
    output_dir/
        TESTER_NAME/
            RECIPE_NAME/
                profile_MID.xml
                profile_MID.dat  (tmptravl)
"""

import os
import shutil
import logging
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class ProfileSorter:
    """Sorts generated profile files into tester/recipe folder structure.

    Parameters
    ----------
    output_dir : str
        Base output directory containing generated profiles.
    """

    def __init__(self, output_dir: str):
        self.output_dir = output_dir

    def sort_profiles(
        self,
        file_list: List[Dict[str, str]],
        log_callback=None,
    ) -> Dict[str, List[str]]:
        """Sort profile files into tester/recipe subfolders.

        Parameters
        ----------
        file_list : list of dict
            Each dict has keys:
            - 'xml_path': path to generated XML file
            - 'tester': tester hostname or name
            - 'recipe': recipe name
            - 'mid': MID identifier
            - 'tmptravl_path': optional path to tmptravl .dat file
        log_callback : callable, optional
            Callback for log messages.

        Returns
        -------
        dict
            Mapping of tester_name -> list of sorted file paths.
        """
        sorted_files: Dict[str, List[str]] = {}

        for entry in file_list:
            xml_path = entry.get("xml_path", "")
            tester = entry.get("tester", "UNKNOWN")
            recipe = entry.get("recipe", "DEFAULT")
            mid = entry.get("mid", "")
            tmptravl_path = entry.get("tmptravl_path", "")

            if not xml_path or not os.path.isfile(xml_path):
                msg = f"Skipping missing file: {xml_path}"
                logger.warning(msg)
                if log_callback:
                    log_callback(msg)
                continue

            # Create tester/recipe subfolder
            tester_folder = self._sanitize_folder_name(tester)
            recipe_folder = self._sanitize_folder_name(recipe)
            dest_dir = os.path.join(self.output_dir, tester_folder, recipe_folder)

            try:
                os.makedirs(dest_dir, exist_ok=True)
            except OSError as e:
                msg = f"Failed to create directory {dest_dir}: {e}"
                logger.error(msg)
                if log_callback:
                    log_callback(msg)
                continue

            # Move XML file
            dest_xml = os.path.join(dest_dir, os.path.basename(xml_path))
            try:
                shutil.move(xml_path, dest_xml)
                msg = f"Sorted: {os.path.basename(xml_path)} -> {tester_folder}/{recipe_folder}/"
                logger.info(msg)
                if log_callback:
                    log_callback(msg)

                if tester_folder not in sorted_files:
                    sorted_files[tester_folder] = []
                sorted_files[tester_folder].append(dest_xml)
            except (shutil.Error, OSError) as e:
                msg = f"Failed to move {xml_path}: {e}"
                logger.error(msg)
                if log_callback:
                    log_callback(msg)
                continue

            # Move tmptravl file if present
            if tmptravl_path and os.path.isfile(tmptravl_path):
                dest_dat = os.path.join(dest_dir, os.path.basename(tmptravl_path))
                try:
                    shutil.move(tmptravl_path, dest_dat)
                except (shutil.Error, OSError) as e:
                    logger.warning(f"Failed to move tmptravl {tmptravl_path}: {e}")

        return sorted_files

    def sort_by_step(
        self,
        xml_dir: str,
        step: str,
        recipe: str = "",
        log_callback=None,
    ) -> str:
        """Sort all XML files in a directory into a step/recipe subfolder.

        Simpler interface for when all files belong to the same step.

        Parameters
        ----------
        xml_dir : str
            Directory containing XML files to sort.
        step : str
            Step name (e.g., 'ABIT', 'SFN2').
        recipe : str
            Recipe name. If empty, uses step as recipe.
        log_callback : callable, optional
            Callback for log messages.

        Returns
        -------
        str
            Path to the destination directory.
        """
        if not recipe:
            recipe = step

        step_folder = self._sanitize_folder_name(step)
        recipe_folder = self._sanitize_folder_name(recipe)
        dest_dir = os.path.join(self.output_dir, step_folder, recipe_folder)

        try:
            os.makedirs(dest_dir, exist_ok=True)
        except OSError as e:
            logger.error(f"Failed to create {dest_dir}: {e}")
            return ""

        if not os.path.isdir(xml_dir):
            return dest_dir

        moved = 0
        for fname in os.listdir(xml_dir):
            if fname.lower().endswith(('.xml', '.dat')):
                src = os.path.join(xml_dir, fname)
                dst = os.path.join(dest_dir, fname)
                try:
                    shutil.move(src, dst)
                    moved += 1
                except (shutil.Error, OSError) as e:
                    logger.warning(f"Failed to move {fname}: {e}")

        msg = f"Sorted {moved} files into {step_folder}/{recipe_folder}/"
        logger.info(msg)
        if log_callback:
            log_callback(msg)

        return dest_dir

    def clean_empty_folders(self, log_callback=None) -> int:
        """Remove empty subdirectories from output_dir.

        Mirrors CAT ProfileClean().

        Parameters
        ----------
        log_callback : callable, optional
            Callback for log messages.

        Returns
        -------
        int
            Number of empty directories removed.
        """
        removed = 0
        if not os.path.isdir(self.output_dir):
            return removed

        # Walk bottom-up to remove empty leaf directories first
        for dirpath, dirnames, filenames in os.walk(self.output_dir, topdown=False):
            # Don't remove the root output_dir itself
            if dirpath == self.output_dir:
                continue
            if not filenames and not dirnames:
                try:
                    os.rmdir(dirpath)
                    removed += 1
                    logger.debug(f"Removed empty directory: {dirpath}")
                except OSError:
                    pass

        if removed > 0:
            msg = f"Cleaned up {removed} empty directories"
            logger.info(msg)
            if log_callback:
                log_callback(msg)

        return removed

    def get_sorted_summary(self) -> Dict[str, Dict[str, int]]:
        """Get a summary of sorted profiles by tester/recipe.

        Returns
        -------
        dict
            Nested dict: {tester: {recipe: file_count}}
        """
        summary: Dict[str, Dict[str, int]] = {}

        if not os.path.isdir(self.output_dir):
            return summary

        for tester_name in os.listdir(self.output_dir):
            tester_path = os.path.join(self.output_dir, tester_name)
            if not os.path.isdir(tester_path):
                continue

            summary[tester_name] = {}
            for recipe_name in os.listdir(tester_path):
                recipe_path = os.path.join(tester_path, recipe_name)
                if not os.path.isdir(recipe_path):
                    continue

                xml_count = sum(1 for f in os.listdir(recipe_path)
                                if f.lower().endswith('.xml'))
                summary[tester_name][recipe_name] = xml_count

        return summary

    @staticmethod
    def _sanitize_folder_name(name: str) -> str:
        """Sanitize a string for use as a folder name.

        Parameters
        ----------
        name : str
            Raw name to sanitize.

        Returns
        -------
        str
            Sanitized folder name.
        """
        if not name:
            return "UNKNOWN"
        # Replace characters that are invalid in folder names
        invalid_chars = '<>:"/\\|?*'
        sanitized = name.strip()
        for ch in invalid_chars:
            sanitized = sanitized.replace(ch, '_')
        return sanitized or "UNKNOWN"

    @staticmethod
    def match_latest_path(base_path: str, folder_prefix: str) -> str:
        """Find the latest versioned folder matching a prefix.

        Mirrors CAT MatchLatestPath() — looks for folders like:
            base_path/PREFIX_v1/
            base_path/PREFIX_v2/
        Returns the one with the highest version number.

        Parameters
        ----------
        base_path : str
            Directory to search in.
        folder_prefix : str
            Prefix to match folder names against.

        Returns
        -------
        str
            Path to the latest matching folder, or empty string.
        """
        if not os.path.isdir(base_path):
            return ""

        matches = []
        for name in os.listdir(base_path):
            full_path = os.path.join(base_path, name)
            if os.path.isdir(full_path) and name.startswith(folder_prefix):
                matches.append(full_path)

        if not matches:
            return ""

        # Sort by modification time (newest last) and return latest
        matches.sort(key=lambda p: os.path.getmtime(p))
        return matches[-1]
