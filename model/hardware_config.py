"""
model/hardware_config.py
========================
Hardware configuration loader for checkout profile generation.
Replaces hardcoded DIB_TYPE/MACHINE_MODEL in checkout_orchestrator.py.
Modeled after CAT's configManager.py:220-247 and ProfileDump/main.py:632-676.
"""
import os
import json
import logging
import re
from typing import Dict, Optional, Tuple

logger = logging.getLogger("bento_app")

# Default config path
_CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config")
_HW_CONFIG_FILE = os.path.join(_CONFIG_DIR, "profile_gen_hardware.json")

# Valid steps and form factors (mirrors CAT sys_config.json)
VALID_STEPS = ["ABIT", "SFN2", "SCHP"]
VALID_FORM_FACTORS = ["U.2", "U.3", "E3.S", "E1.S", "E1.L", "M.2"]

# Fallback defaults if config file is missing
_FALLBACK_DIB = "MS0052"
_FALLBACK_MACHINE_MODEL = "IBIR"
_FALLBACK_MACHINE_VENDOR = "NEOS"


class HardwareConfig:
    """
    Loads and provides hardware configuration for profile generation.

    Mirrors CAT's approach:
    - configManager.py creates profile_gen_hardware.json
    - ProfileDump/main.py uses dib_dict[step][form_factor] for lookup
    """

    def __init__(self, config_path: str = ""):
        self._config_path = config_path or _HW_CONFIG_FILE
        self._data: Dict = {}
        self._load()

    def _load(self):
        """Load hardware config from JSON file."""
        try:
            if os.path.exists(self._config_path):
                with open(self._config_path, "r") as f:
                    self._data = json.load(f)
                logger.debug(f"Hardware config loaded from {self._config_path}")
            else:
                logger.warning(f"Hardware config not found at {self._config_path}, using defaults")
                self._data = self._create_defaults()
        except Exception as e:
            logger.error(f"Error loading hardware config: {e}")
            self._data = self._create_defaults()

    def _create_defaults(self) -> Dict:
        """Create default hardware config (mirrors CAT unit test values)."""
        return {
            "DIB_TYPE": {
                "ABIT": {"U.2": "MS0022", "U.3": "", "E3.S": "MS0032", "E1.S": "MS0028", "E1.L": "", "M.2": ""},
                "SFN2": {"U.2": "MS0050", "U.3": "", "E3.S": "MS0049", "E1.S": "MS0083", "E1.L": "", "M.2": ""},
                "SCHP": {"U.2": "MS0055", "U.3": "", "E3.S": "MS0053", "E1.S": "MS0054", "E1.L": "", "M.2": ""},
            },
            "MACHINE_MODEL": {"ABIT": "IBIR", "SFN2": "MPT3000HVM3", "SCHP": "FX7"},
            "MACHINE_VENDOR": {"ABIT": "NEOS", "SFN2": "ADVT", "SCHP": "NEOSEM"},
            "STEP_NAME": {
                "ABIT": ["ABIT", "AMB_BI_TEST"],
                "SFN2": ["SFN2", "SSD_FIN_TEST2"],
                "SCHP": ["SCHP", "SSD_CHAMBER_PERF"],
            },
        }

    def save(self):
        """Save current config to JSON file."""
        os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
        with open(self._config_path, "w") as f:
            json.dump(self._data, f, indent=4)
        logger.info(f"Hardware config saved to {self._config_path}")

    def reload(self):
        """Reload config from disk."""
        self._load()

    def get_dib_type(self, step: str, form_factor: str) -> str:
        """
        Look up DIB_TYPE by step and form factor.
        Mirrors CAT: dibs = self.dib_dict[step][form]
        """
        step = step.upper()
        dib_types = self._data.get("DIB_TYPE", {})
        step_dibs = dib_types.get(step, {})
        result = step_dibs.get(form_factor, "")
        if not result:
            logger.warning(f"No DIB_TYPE for step={step}, form_factor={form_factor}, using fallback {_FALLBACK_DIB}")
            return _FALLBACK_DIB
        return result

    def get_machine_model(self, step: str) -> str:
        """Look up MACHINE_MODEL by step."""
        step = step.upper()
        result = self._data.get("MACHINE_MODEL", {}).get(step, "")
        if not result:
            logger.warning(f"No MACHINE_MODEL for step={step}, using fallback {_FALLBACK_MACHINE_MODEL}")
            return _FALLBACK_MACHINE_MODEL
        return result

    def get_machine_vendor(self, step: str) -> str:
        """Look up MACHINE_VENDOR by step."""
        step = step.upper()
        result = self._data.get("MACHINE_VENDOR", {}).get(step, "")
        if not result:
            logger.warning(f"No MACHINE_VENDOR for step={step}, using fallback {_FALLBACK_MACHINE_VENDOR}")
            return _FALLBACK_MACHINE_VENDOR
        return result

    def get_step_names(self, step: str) -> Tuple[str, str]:
        """
        Get (MAM_STEP_NAME, CFGPN_STEP_ID) for a step.
        Mirrors CAT: self.step_name_dict[step] -> ["ABIT", "AMB_BI_TEST"]
        """
        step = step.upper()
        names = self._data.get("STEP_NAME", {}).get(step, [step, step])
        if len(names) >= 2:
            return (names[0], names[1])
        return (step, step)

    def get_dib_dict(self) -> Dict:
        """Return full DIB_TYPE dict for passing to ProfileWriter-style code."""
        return self._data.get("DIB_TYPE", {})

    def get_machine_model_dict(self) -> Dict:
        """Return full MACHINE_MODEL dict."""
        return self._data.get("MACHINE_MODEL", {})

    def get_machine_vendor_dict(self) -> Dict:
        """Return full MACHINE_VENDOR dict."""
        return self._data.get("MACHINE_VENDOR", {})

    def get_step_name_dict(self) -> Dict:
        """Return full STEP_NAME dict."""
        return self._data.get("STEP_NAME", {})

    def update_dib_type(self, step: str, form_factor: str, value: str):
        """Update a single DIB_TYPE entry."""
        step = step.upper()
        if "DIB_TYPE" not in self._data:
            self._data["DIB_TYPE"] = {}
        if step not in self._data["DIB_TYPE"]:
            self._data["DIB_TYPE"][step] = {}
        self._data["DIB_TYPE"][step][form_factor] = value.strip().upper()

    def update_machine_model(self, step: str, value: str):
        """Update MACHINE_MODEL for a step."""
        step = step.upper()
        if "MACHINE_MODEL" not in self._data:
            self._data["MACHINE_MODEL"] = {}
        self._data["MACHINE_MODEL"][step] = value.strip()

    @staticmethod
    def validate_dib_type_format(value: str) -> bool:
        """Validate DIB_TYPE format (MS followed by 4 digits). Mirrors CAT on_save() regex."""
        if not value:
            return True  # Empty is allowed
        return bool(re.match(r"^MS\d{4}$", value.strip().upper()))


# Module-level singleton
_hw_config: Optional[HardwareConfig] = None

def get_hardware_config(config_path: str = "") -> HardwareConfig:
    """Get or create the hardware config singleton."""
    global _hw_config
    if _hw_config is None:
        _hw_config = HardwareConfig(config_path)
    return _hw_config
