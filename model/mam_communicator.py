"""MAM (Material Attribute Management) Communicator.

Adapted from CAT ProfileDump/CATCall/CATCALL.py MAMCommunicator class.
Provides lot attribute query/set operations via PyMIPC.
Gracefully degrades when PyMIPC is not installed.
"""

import os
import socket
import logging
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# Try to import PyMIPC — not available in all environments
try:
    from PyMIPC import clsMIPC
    HAS_PYMIPC = True
except ImportError:
    HAS_PYMIPC = False
    logger.info("PyMIPC not available — MAM queries will be disabled")


# Per-site MAM server configurations (from CAT CATCALL.py)
MAM_SERVER_CONFIGS: Dict[str, Dict[str, Any]] = {
    "SINGAPORE": {
        "mamServer": "intsvr2695.amr.corp.intel.com",
        "mamPort": 1583,
        "mamProtocol": "tcp",
        "mamFormat": "xml",
        "mamFacility": "SORT",
        "mamArea": "SORT",
    },
    "PENANG": {
        "mamServer": "intsvr2695.amr.corp.intel.com",
        "mamPort": 1583,
        "mamProtocol": "tcp",
        "mamFormat": "xml",
        "mamFacility": "SORT",
        "mamArea": "SORT",
    },
    "BOISE": {
        "mamServer": "intsvr2695.amr.corp.intel.com",
        "mamPort": 1583,
        "mamProtocol": "tcp",
        "mamFormat": "xml",
        "mamFacility": "SORT",
        "mamArea": "SORT",
    },
    "XIAN": {
        "mamServer": "intsvr2695.amr.corp.intel.com",
        "mamPort": 1583,
        "mamProtocol": "tcp",
        "mamFormat": "xml",
        "mamFacility": "SORT",
        "mamArea": "SORT",
    },
}

# Default site fallback
DEFAULT_SITE = "SINGAPORE"


class MAMResult:
    """Holds the result of a MAM query operation."""
    def __init__(self):
        self.success: bool = False
        self.attributes: Dict[str, str] = {}
        self.error: str = ""
        self.lot: str = ""

    def get(self, key: str, default: str = "") -> str:
        """Get an attribute value by key."""
        return self.attributes.get(key, default)

    def __repr__(self):
        return (f"MAMResult(lot={self.lot!r}, success={self.success}, "
                f"attrs={len(self.attributes)})")


class MAMCommunicator:
    """Communicates with MAM server to query/set lot attributes.

    Adapted from CAT's MAMCommunicator. Requires PyMIPC library.
    When PyMIPC is not available, all operations return failure gracefully.

    Parameters
    ----------
    site : str
        Site name (SINGAPORE, PENANG, BOISE, XIAN) for server config lookup.
    """

    def __init__(self, site: str = ""):
        self.site = (site or DEFAULT_SITE).upper()
        self._config = MAM_SERVER_CONFIGS.get(self.site, MAM_SERVER_CONFIGS[DEFAULT_SITE])
        self._hostname = socket.gethostname()

    @property
    def is_available(self) -> bool:
        """Check if PyMIPC is available for MAM operations."""
        return HAS_PYMIPC

    def get_lot_attributes(self, lot: str, attributes: Optional[List[str]] = None) -> MAMResult:
        """Query lot attributes from MAM.

        Mirrors CAT MAMCommunicator.GetLotAttributes().

        Parameters
        ----------
        lot : str
            Lot ID to query.
        attributes : list of str, optional
            Specific attributes to query. If None, queries all available.

        Returns
        -------
        MAMResult
            Query result with attributes dict.
        """
        result = MAMResult()
        result.lot = lot

        if not HAS_PYMIPC:
            result.error = "PyMIPC not available — cannot query MAM"
            logger.warning(result.error)
            return result

        if not lot:
            result.error = "No lot ID provided"
            logger.error(result.error)
            return result

        try:
            mipc = clsMIPC.MIPC()
            mipc.set("mamServer", self._config["mamServer"])
            mipc.set("mamPort", str(self._config["mamPort"]))
            mipc.set("mamProtocol", self._config["mamProtocol"])
            mipc.set("mamFormat", self._config["mamFormat"])
            mipc.set("mamFacility", self._config["mamFacility"])
            mipc.set("mamArea", self._config["mamArea"])
            mipc.set("mamHostName", self._hostname)

            mipc.set("mamLot", lot)
            mipc.set("mamOperation", "GetLotAttributes")

            rc = mipc.execute()
            if rc != 0:
                result.error = f"MAM query failed with rc={rc}"
                logger.error(result.error)
                return result

            # Parse response attributes
            xml_attrs = mipc.get("mamAttributes")
            if xml_attrs:
                result.attributes = self._parse_attrs(xml_attrs)
                result.success = True
                logger.info(f"MAM query for lot {lot}: {len(result.attributes)} attributes")
            else:
                result.error = "No attributes returned from MAM"
                logger.warning(result.error)

        except Exception as e:
            result.error = f"MAM query exception: {e}"
            logger.exception(result.error)

        return result

    def set_lot_attributes(self, lot: str, attrs: Dict[str, str]) -> MAMResult:
        """Set lot attributes in MAM.

        Mirrors CAT MAMCommunicator.SetLotAttributes().

        Parameters
        ----------
        lot : str
            Lot ID to update.
        attrs : dict
            Key-value pairs of attributes to set.

        Returns
        -------
        MAMResult
            Operation result.
        """
        result = MAMResult()
        result.lot = lot

        if not HAS_PYMIPC:
            result.error = "PyMIPC not available — cannot set MAM attributes"
            logger.warning(result.error)
            return result

        if not lot or not attrs:
            result.error = "Lot ID and attributes are required"
            logger.error(result.error)
            return result

        try:
            mipc = clsMIPC.MIPC()
            mipc.set("mamServer", self._config["mamServer"])
            mipc.set("mamPort", str(self._config["mamPort"]))
            mipc.set("mamProtocol", self._config["mamProtocol"])
            mipc.set("mamFormat", self._config["mamFormat"])
            mipc.set("mamFacility", self._config["mamFacility"])
            mipc.set("mamArea", self._config["mamArea"])
            mipc.set("mamHostName", self._hostname)

            mipc.set("mamLot", lot)
            mipc.set("mamOperation", "SetLotAttributes")

            # Build attribute XML
            attr_xml = ""
            for key, value in attrs.items():
                attr_xml += f'<Attribute name="{key}" value="{value}"/>'
            mipc.set("mamAttributes", attr_xml)

            rc = mipc.execute()
            if rc != 0:
                result.error = f"MAM set failed with rc={rc}"
                logger.error(result.error)
                return result

            result.success = True
            result.attributes = attrs
            logger.info(f"MAM set for lot {lot}: {list(attrs.keys())}")

        except Exception as e:
            result.error = f"MAM set exception: {e}"
            logger.exception(result.error)

        return result

    def _parse_attrs(self, xml_attrs: str) -> Dict[str, str]:
        """Parse MAM XML attribute response into a dict.

        Mirrors CAT MAMCommunicator._parseAttrs().

        Parameters
        ----------
        xml_attrs : str
            XML string containing attribute elements.

        Returns
        -------
        dict
            Parsed attribute key-value pairs.
        """
        attrs = {}
        try:
            import xml.etree.ElementTree as ET
            # Wrap in root element if needed
            if not xml_attrs.strip().startswith("<?xml") and not xml_attrs.strip().startswith("<root"):
                xml_attrs = f"<root>{xml_attrs}</root>"
            root = ET.fromstring(xml_attrs)
            for elem in root.iter("Attribute"):
                name = elem.get("name", "")
                value = elem.get("value", "")
                if name:
                    attrs[name] = value
        except Exception as e:
            logger.warning(f"Failed to parse MAM attributes: {e}")
            # Try simple line-based parsing as fallback
            for line in xml_attrs.splitlines():
                line = line.strip()
                if 'name="' in line and 'value="' in line:
                    try:
                        name = line.split('name="')[1].split('"')[0]
                        value = line.split('value="')[1].split('"')[0]
                        if name:
                            attrs[name] = value
                    except (IndexError, ValueError):
                        pass
        return attrs

    def get_cfgpn_mcto(self, lot: str) -> Dict[str, str]:
        """Convenience method to get CFGPN and MCTO for a lot.

        Parameters
        ----------
        lot : str
            Lot ID to query.

        Returns
        -------
        dict
            Dict with 'cfgpn' and 'mcto' keys (empty strings if not found).
        """
        result = self.get_lot_attributes(lot)
        return {
            "cfgpn": result.get("CFGPN", ""),
            "mcto": result.get("MCTO", ""),
        }
