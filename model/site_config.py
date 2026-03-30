"""Site configuration manager for multi-site support.

Mirrors CAT config/sys_config.json 'Profile Generation' section.
Provides site lists, step lists, form factor lists, and per-site settings.
"""

import os
import json
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

_DEFAULT_SITES = ["SINGAPORE", "PENANG", "BOISE", "XIAN"]
_DEFAULT_STEPS = ["ABIT", "SFN2", "SCHP"]
_DEFAULT_FORM_FACTORS = ["U.2", "U.3", "E3.S", "E1.S", "E1.L", "M.2", "BGA"]
_DEFAULT_SITE = "SINGAPORE"


class SiteConfig:
    """Loads and provides access to site configuration.

    Parameters
    ----------
    config_path : str, optional
        Path to site_config.json. If empty, uses defaults.
    """

    def __init__(self, config_path: str = ""):
        self._config: Dict[str, Any] = {}
        self._config_path = config_path
        self._load(config_path)

    def _load(self, config_path: str):
        """Load configuration from JSON file."""
        if config_path and os.path.isfile(config_path):
            try:
                with open(config_path, 'r') as f:
                    self._config = json.load(f)
                logger.info(f"Site config loaded from {config_path}")
            except Exception as e:
                logger.warning(f"Failed to load site config from {config_path}: {e}")
                self._config = {}
        else:
            if config_path:
                logger.info(f"Site config not found at {config_path}, using defaults")

    @property
    def sites(self) -> List[str]:
        """Available site names."""
        return self._config.get("sites", _DEFAULT_SITES)

    @property
    def steps(self) -> List[str]:
        """Available step names."""
        return self._config.get("steps", _DEFAULT_STEPS)

    @property
    def form_factors(self) -> List[str]:
        """Available form factors."""
        return self._config.get("form_factors", _DEFAULT_FORM_FACTORS)

    @property
    def default_site(self) -> str:
        """Default site name."""
        return self._config.get("default_site", _DEFAULT_SITE)

    def get_site_settings(self, site: str) -> Dict[str, Any]:
        """Get per-site settings.

        Parameters
        ----------
        site : str
            Site name (case-insensitive).

        Returns
        -------
        dict
            Site-specific settings, or empty dict if not found.
        """
        site_upper = site.upper() if site else ""
        site_settings = self._config.get("site_settings", {})
        return site_settings.get(site_upper, {})

    def get_mam_server(self, site: str) -> str:
        """Get MAM server hostname for a site."""
        settings = self.get_site_settings(site)
        return settings.get("mam_server", "")

    def get_mam_port(self, site: str) -> int:
        """Get MAM server port for a site."""
        settings = self.get_site_settings(site)
        return settings.get("mam_port", 1583)

    def is_valid_site(self, site: str) -> bool:
        """Check if a site name is valid."""
        return site.upper() in [s.upper() for s in self.sites] if site else False

    def is_valid_step(self, step: str) -> bool:
        """Check if a step name is valid."""
        return step.upper() in [s.upper() for s in self.steps] if step else False

    def is_valid_form_factor(self, ff: str) -> bool:
        """Check if a form factor is valid."""
        return ff.upper() in [f.upper() for f in self.form_factors] if ff else False


# ── Singleton accessor ──────────────────────────────────────────
_instance: Optional[SiteConfig] = None


def get_site_config(config_path: str = "") -> SiteConfig:
    """Get or create the singleton SiteConfig instance.

    Parameters
    ----------
    config_path : str, optional
        Path to site_config.json. Only used on first call.

    Returns
    -------
    SiteConfig
        The singleton instance.
    """
    global _instance
    if _instance is None:
        _instance = SiteConfig(config_path)
    return _instance


def reset_site_config():
    """Reset the singleton (for testing)."""
    global _instance
    _instance = None
