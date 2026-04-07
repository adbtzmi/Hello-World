"""SAP Communicator for BENTO.

Ported from CAT SAPCommunicator1 class (crt_automation_tools/SAP/SAPCommunicator/).
Provides CFGPN/MCTO attribute queries via SAP SOAP web services (Z_VCTO_GET_MID_DATA_2).
Gracefully degrades when suds-community is not installed.
"""

import os
import collections
import logging
from typing import Dict, Optional
from urllib.request import pathname2url

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Graceful degradation — same pattern as mam_communicator.py
# ---------------------------------------------------------------------------
HAS_SUDS = False
try:
    from suds.client import Client  # type: ignore[import-untyped]
    HAS_SUDS = True
except ImportError:
    Client = None  # type: ignore[assignment,misc]

if not HAS_SUDS:
    logger.info("suds-community not available — SAP queries will be disabled")

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------
WHERE_AM_I = os.path.split(os.path.realpath(__file__))[0]
WSDL_PATH = os.path.join(WHERE_AM_I, "resources")

# ---------------------------------------------------------------------------
# SAP connection credentials (from CAT SAPCommunicator.py lines 24-26)
# ---------------------------------------------------------------------------
_SAP_USER = "MANU-RFC"
_SAP_PWD = "Micron123"
_SAP_CLIENT_ID = "100"

# ---------------------------------------------------------------------------
# Firmware-related SAP attribute keys
# (from CAT ProfileDump/main.py _replacelist, line 214)
# ---------------------------------------------------------------------------
SAP_FIRMWARE_KEYS = [
    "MFG_TST_VERSION",
    "TST_PARAM_PATH",
    "CUST_FW_PATH",
    "MFG_FW_PATH",
    "RELEASE_NOTES_PATH",
    "BASE_CFGPN",
]


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------
class SAPResult:
    """Holds the result of a SAP query operation."""

    def __init__(self):
        self.success: bool = False
        self.attributes: Dict[str, str] = collections.OrderedDict()
        self.error: str = ""
        self.cfgpn: str = ""

    def get(self, key: str, default: str = "") -> str:
        """Get an attribute value by key."""
        return self.attributes.get(key, default)

    def __repr__(self) -> str:
        return (
            f"SAPResult(cfgpn={self.cfgpn!r}, success={self.success}, "
            f"attrs={len(self.attributes)})"
        )


# ---------------------------------------------------------------------------
# SAP Communicator
# ---------------------------------------------------------------------------
class SAPCommunicator:
    """Communicates with SAP via SOAP to query CFGPN/MCTO attributes.

    Ported from CAT's ``SAPCommunicator1`` class.  Requires the
    ``suds-community`` library.  When suds is not available, all operations
    return failure gracefully (same pattern as
    :class:`~model.mam_communicator.MAMCommunicator`).

    Parameters
    ----------
    instance : str
        SAP instance identifier (e.g. ``"PR1"`` for production,
        ``"QA5"`` for QA).  Used to build the SOAP endpoint URL.
    """

    def __init__(self, instance: str = "PR1"):
        self.instance = instance
        self.url = (
            f"http://sap{instance}ms.micron.com/sap/bc/soap/rfc"
            f"?sap-language=EN&sap-client={_SAP_CLIENT_ID}"
        )

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------
    @property
    def is_available(self) -> bool:
        """Check if suds-community is available for SAP operations."""
        return HAS_SUDS

    # ------------------------------------------------------------------
    # Path helpers (from CAT SAPCommunicator.py)
    # ------------------------------------------------------------------
    @staticmethod
    def _wsdlpath(filename: str) -> str:
        """Return the full filesystem path to a WSDL resource file."""
        return os.path.join(WSDL_PATH, filename)

    @staticmethod
    def _path2url(path: str) -> str:
        """Convert a filesystem path to a ``file:///`` URL for suds."""
        return "file:///" + pathname2url(path)

    # ------------------------------------------------------------------
    # Core SOAP call — ported from CAT GetMIDData (lines 60-82)
    # ------------------------------------------------------------------
    def get_mid_data(
        self,
        serial_no: str = "",
        cfgpn_mm: str = "",
        cfgpn_flag: str = "",
        sn_flag: str = "",
        mcto_flag: str = "",
        batch_flag: str = "",
    ) -> SAPResult:
        """Execute the ``Z_VCTO_GET_MID_DATA_2`` SOAP call.

        This is the core SAP query — all other convenience methods delegate
        to this one.

        Parameters
        ----------
        serial_no : str
            Serial number / MID to query.
        cfgpn_mm : str
            CFGPN material number.
        cfgpn_flag : str
            Set to ``'X'`` to request CFGPN data.
        sn_flag : str
            Set to ``'X'`` to request serial-number data.
        mcto_flag : str
            Set to ``'X'`` to request MCTO data.
        batch_flag : str
            Set to ``'X'`` to request batch data.

        Returns
        -------
        SAPResult
            Query result with ordered attributes dict.
        """
        result = SAPResult()

        if not HAS_SUDS:
            result.error = "suds-community not available — cannot query SAP"
            logger.warning(result.error)
            return result

        try:
            wsdl_file = self._wsdlpath("Z_VCTO_GET_MID_DATA_2.wsdl")
            wsdl_url = self._path2url(wsdl_file)

            assert Client is not None  # guaranteed by HAS_SUDS check above
            client = Client(
                wsdl_url,
                location=self.url,
                username=_SAP_USER,
                password=_SAP_PWD,
            )

            # Build request parameters (mirrors CAT exactly)
            params = {
                "SERIAL_NO": serial_no,
                "CFGPN_MM": cfgpn_mm,
                "CFGPN": cfgpn_flag,
                "SN": sn_flag,
                "MCTO": mcto_flag,
                "BATCH": batch_flag,
            }

            # Create factory object to get default RETURN / CHAR_OUTPUT
            it = client.factory.create("Z_VCTO_GET_MID_DATA_2")  # type: ignore[union-attr]
            params["RETURN"] = it.RETURN  # type: ignore[attr-defined]
            params["CHAR_OUTPUT"] = it.CHAR_OUTPUT  # type: ignore[attr-defined]

            response = client.service.Z_VCTO_GET_MID_DATA_2(**params)

            # Parse CHAR_OUTPUT items into ordered dict
            ret_val: Dict[str, str] = {}
            last_matnr = ""
            if len(response.CHAR_OUTPUT) > 0:
                for item in response.CHAR_OUTPUT.item:
                    key = str(item.CHARC_NAME).strip().upper()
                    ret_val[key] = str(item.CHARC_VALUE).strip()
                    last_matnr = str(item.MATNR).strip()

            ret_val["CFGPN"] = last_matnr
            result.cfgpn = last_matnr

            # Check for SAP error responses
            self._check_for_error(response)

            # Sort alphabetically (matches CAT behaviour)
            result.attributes = collections.OrderedDict(
                sorted(ret_val.items(), key=lambda t: str(t[0]))
            )
            result.success = True
            logger.info(
                "SAP query OK: cfgpn_mm=%s, %d attributes returned",
                cfgpn_mm,
                len(result.attributes),
            )

        except SAPError:
            raise
        except Exception as e:
            result.error = f"SAP query exception: {e}"
            logger.exception(result.error)

        return result

    # ------------------------------------------------------------------
    # Convenience wrappers
    # ------------------------------------------------------------------
    def get_cfgpn_data(self, cfgpn_mm: str) -> SAPResult:
        """Query all SAP attributes for a CFGPN material number.

        Mirrors CAT ``GetCFGPNData`` (lines 85-90).

        Parameters
        ----------
        cfgpn_mm : str
            The CFGPN material number to look up (e.g. ``"MTFDKBA51QBK-1B"``).

        Returns
        -------
        SAPResult
            Query result.  ``result.attributes`` contains an ordered dict of
            all characteristic name/value pairs from SAP.
        """
        result = self.get_mid_data(
            serial_no="",
            cfgpn_mm=cfgpn_mm,
            cfgpn_flag="X",
            sn_flag="",
            mcto_flag="",
            batch_flag="",
        )

        # Ensure all values are plain strings (CAT compatibility)
        if result.success:
            cleaned: Dict[str, str] = collections.OrderedDict()
            for key, val in result.attributes.items():
                cleaned[str(key)] = str(val)
            result.attributes = cleaned

        return result

    # ------------------------------------------------------------------
    # Firmware / constant extraction helper
    # (mirrors CAT ProfileMass lines 661-684)
    # ------------------------------------------------------------------
    @staticmethod
    def extract_constant_dict(cfgpn_attrs: Dict[str, str]) -> Dict[str, str]:
        """Extract firmware paths and key constants from SAP CFGPN attributes.

        Pulls the firmware-related keys defined in :data:`SAP_FIRMWARE_KEYS`,
        plus ``PRODUCT_NAME`` → ``MARKET_SEGMENT`` and ``MODULE_FORM_FACTOR``.

        Parameters
        ----------
        cfgpn_attrs : dict
            The ``attributes`` dict from a :class:`SAPResult` (typically from
            :meth:`get_cfgpn_data`).

        Returns
        -------
        dict
            Subset of attributes relevant for profile generation constants.
        """
        constants: Dict[str, str] = {}

        # Firmware / path keys
        for key in SAP_FIRMWARE_KEYS:
            if key in cfgpn_attrs:
                constants[key] = cfgpn_attrs[key]

        # PRODUCT_NAME → MARKET_SEGMENT mapping (CAT ProfileMass convention)
        # Replace spaces with underscores — recipe_selection.py rule tables
        # use underscore-separated values (e.g. "6550_ION" not "6550 ION").
        # Mirrors CAT ProfileDump/main.py line 689.
        if "PRODUCT_NAME" in cfgpn_attrs:
            constants["MARKET_SEGMENT"] = cfgpn_attrs["PRODUCT_NAME"].replace(" ", "_")

        # MODULE_FORM_FACTOR
        if "MODULE_FORM_FACTOR" in cfgpn_attrs:
            constants["MODULE_FORM_FACTOR"] = cfgpn_attrs["MODULE_FORM_FACTOR"]

        return constants

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------
    @staticmethod
    def _check_for_error(response) -> None:
        """Raise :class:`SAPError` if the SAP response contains error items.

        Parameters
        ----------
        response
            The raw suds response object from ``Z_VCTO_GET_MID_DATA_2``.

        Raises
        ------
        SAPError
            If any RETURN item has TYPE ``'E'`` (error) or ``'A'`` (abort).
        """
        try:
            if hasattr(response, "RETURN") and len(response.RETURN) > 0:
                for item in response.RETURN.item:
                    msg_type = str(getattr(item, "TYPE", "")).strip().upper()
                    if msg_type in ("E", "A"):
                        message = str(getattr(item, "MESSAGE", "Unknown SAP error"))
                        raise SAPError(message)
        except SAPError:
            raise
        except Exception:
            # If RETURN structure is empty or unexpected, ignore
            pass


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------
class SAPError(Exception):
    """Raised when SAP returns an error response."""
    pass
