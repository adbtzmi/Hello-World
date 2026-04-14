"""
model/site_paths.py
===================
Centralized site-to-path mapping for BENTO application.
Provides global site selection and automatic path resolution.
"""
import os
from typing import Dict, Optional

# Site to base path mapping
SITE_BASE_PATHS: Dict[str, str] = {
    "SINGAPORE": r"\\sisfomodauto\modauto\temp",
    "BOISE": r"\\bofsmodauto\modauto\temp",
    "PENANG": r"P:\temp",  # MUST REMAIN UNCHANGED
    "SANAND": r"\\snfsmodauto\modauto\temp",
}

# Available sites in display order
AVAILABLE_SITES = ["SINGAPORE", "BOISE", "PENANG", "SANAND"]

# Default site
DEFAULT_SITE = "PENANG"


class SitePathResolver:
    """
    Centralized site path resolution.
    Manages global site selection and derives all filesystem paths.
    """
    
    def __init__(self, default_site: str = DEFAULT_SITE):
        self._current_site = default_site
        self._path_override_enabled = False
        self._custom_paths: Dict[str, str] = {}
    
    @property
    def current_site(self) -> str:
        """Get the currently selected site."""
        return self._current_site
    
    @current_site.setter
    def current_site(self, site: str):
        """Set the current site and clear any path overrides."""
        if site not in SITE_BASE_PATHS:
            raise ValueError(f"Invalid site: {site}. Must be one of {AVAILABLE_SITES}")
        self._current_site = site
        # Clear custom paths when site changes
        if not self._path_override_enabled:
            self._custom_paths.clear()
    
    @property
    def base_path(self) -> str:
        """Get the base path for the current site."""
        return SITE_BASE_PATHS[self._current_site]
    
    def get_path(self, path_type: str) -> str:
        """
        Get a derived path for the current site.
        
        Args:
            path_type: Type of path (e.g., 'RAW_ZIP', 'RELEASE_TGZ', 'BENTO_ROOT')
        
        Returns:
            Full path string
        """
        # Return custom path if override is enabled
        if self._path_override_enabled and path_type in self._custom_paths:
            return self._custom_paths[path_type]
        
        # Derive path from base
        base = self.base_path
        
        if path_type == "BENTO_ROOT":
            return os.path.join(base, "BENTO")
        elif path_type == "RAW_ZIP":
            return os.path.join(base, "BENTO", "RAW_ZIP")
        elif path_type == "RELEASE_TGZ":
            return os.path.join(base, "BENTO", "RELEASE_TGZ")
        elif path_type == "CHECKOUT_QUEUE":
            return os.path.join(base, "BENTO", "CHECKOUT_QUEUE")
        elif path_type == "XML_OUTPUT":
            return os.path.join(base, "BENTO", "XML_OUTPUT")
        else:
            # For unknown types, just append to BENTO root
            return os.path.join(base, "BENTO", path_type)
    
    def enable_path_override(self, enabled: bool = True):
        """Enable or disable manual path override."""
        self._path_override_enabled = enabled
        if not enabled:
            self._custom_paths.clear()
    
    def set_custom_path(self, path_type: str, custom_path: str):
        """Set a custom path override for a specific path type."""
        if self._path_override_enabled:
            self._custom_paths[path_type] = custom_path
    
    def is_override_enabled(self) -> bool:
        """Check if path override is enabled."""
        return self._path_override_enabled
    
    def get_all_paths(self) -> Dict[str, str]:
        """Get all standard paths for the current site."""
        return {
            "BENTO_ROOT": self.get_path("BENTO_ROOT"),
            "RAW_ZIP": self.get_path("RAW_ZIP"),
            "RELEASE_TGZ": self.get_path("RELEASE_TGZ"),
            "CHECKOUT_QUEUE": self.get_path("CHECKOUT_QUEUE"),
            "XML_OUTPUT": self.get_path("XML_OUTPUT"),
        }


# Global singleton instance
_global_resolver: Optional[SitePathResolver] = None


def get_site_resolver() -> SitePathResolver:
    """Get the global site path resolver instance."""
    global _global_resolver
    if _global_resolver is None:
        _global_resolver = SitePathResolver()
    return _global_resolver


def set_global_site(site: str):
    """Set the global site selection."""
    resolver = get_site_resolver()
    resolver.current_site = site


def get_global_site() -> str:
    """Get the current global site."""
    resolver = get_site_resolver()
    return resolver.current_site


def get_site_path(path_type: str) -> str:
    """Get a path for the current global site."""
    resolver = get_site_resolver()
    return resolver.get_path(path_type)
