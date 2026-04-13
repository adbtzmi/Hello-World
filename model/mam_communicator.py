# -*- coding: utf-8 -*-
"""MAM (Material Attribute Management) Communicator.

Adapted from CAT ProfileDump/CATCall/CATCALL.py MAMCommunicator class.
Provides lot attribute query/set operations via PyMIPC (native MIPC DLL).
Also supports MIPC SOAP web service queries via zeep (from blockrun_lot_checker).
Gracefully degrades when PyMIPC and/or zeep are not installed.
"""

import os
import sys
import collections
import socket
import logging
import xml.etree.ElementTree as ET
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# Try to import PyMIPC — the native MIPC DLL wrapper.
# First try the system-installed PyMIPC, then fall back to CAT's bundled copy.
HAS_PYMIPC = False
_PyMIPC = None  # module reference

try:
    import PyMIPC as _PyMIPC
    HAS_PYMIPC = True
    logger.info("PyMIPC loaded from system path")
except ImportError:
    # Try to find CAT's PyMIPC at known locations
    _PYMIPC_SEARCH_PATHS = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "CATCall"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                     "crt_automation_tools", "ProfileDump", "CATCall"),
        # Common CAT install location
        r"C:\CAT\ProfileDump\CATCall",
    ]
    for _search_path in _PYMIPC_SEARCH_PATHS:
        _search_path = os.path.normpath(_search_path)
        _pymipc_file = os.path.join(_search_path, "PyMIPC.py")
        if os.path.isfile(_pymipc_file):
            if _search_path not in sys.path:
                sys.path.insert(0, _search_path)
            try:
                import PyMIPC as _PyMIPC
                HAS_PYMIPC = True
                logger.info("PyMIPC loaded from %s", _search_path)
                break
            except Exception as e:
                logger.debug("PyMIPC found at %s but failed to import: %s",
                             _search_path, e)
    if not HAS_PYMIPC:
        logger.info("PyMIPC not available — MAM queries via native MIPC will be disabled")

# Try to import zeep — SOAP client for MIPC web gateway
try:
    from zeep import Client as ZeepClient
    from zeep.transports import Transport as ZeepTransport
    import requests
    HAS_ZEEP = True
except ImportError:
    ZeepClient = None  # type: ignore[assignment,misc]
    ZeepTransport = None  # type: ignore[assignment,misc]
    HAS_ZEEP = False
    logger.info("zeep not available — MAM SOAP queries will be disabled")


# Per-site MAM MIPC query settings — mirrors CAT CATCALL.py mamQuerySettings.
# Each site has TEST and MODULES sub-configs with MIPC destination addresses.
MAM_QUERY_SETTINGS: Dict[str, Dict[str, Dict[str, str]]] = {
    "SINGAPORE": {
        "TEST": {
            "EXECUTION_FACILITY": "TEST-MSA",
            "SERVER": "/SINGAPORE/MTI/MFG/MAM/PROD/MAMSRV/TST_XML",
        },
        "MODULES": {
            "APP": "MODSI",
            "ACTION": "REPORT",
            "FORMAT": "XML",
            "USERID": "MODULETESTOBJECT",
            "REPORTID": "",
            "CRITERIA": "",
            "EXECUTION_FACILITY": "MODULE-MSA",
            "SERVER": "/SINGAPORE/MTI/MFG/MAM/PROD/MAMSRV/MODSI_XML",
            "REPORT_SERVER": "/SINGAPORE/MTI/MFG/MAM/PROD/MAMRPTSRV/MODSI_XML",
        },
    },
    "PENANG": {
        "TEST": {
            "EXECUTION_FACILITY": "TEST-MMP",
            "SERVER": "/PENANG/MTI/MFG/MAM/PROD/MAMSRV/TSTPG_XML",
        },
        "MODULES": {
            "APP": "MODPG",
            "ACTION": "REPORT",
            "FORMAT": "XML",
            "USERID": "MODULETESTOBJECT",
            "REPORTID": "",
            "CRITERIA": "",
            "EXECUTION_FACILITY": "MODULE-MMP",
            "SERVER": "/PENANG/MTI/MFG/MAM/PROD/MAMSRV/MODPG_XML",
            "REPORT_SERVER": "/PENANG/MTI/MFG/MAM/PROD/MAMRPTSRV/MODPG_XML",
        },
    },
    "BOISE": {
        "TEST": {
            "EXECUTION_FACILITY": "TEST",
            "SERVER": "/BOISE_2/MTI/MFG/MAM/PROD/MAMSRV/TST_XML",
        },
        "MODULES": {
            "APP": "MODASM",
            "ACTION": "REPORT",
            "FORMAT": "XML",
            "USERID": "MODULETESTOBJECT",
            "REPORTID": "",
            "CRITERIA": "",
            "EXECUTION_FACILITY": "MODULE ASSEMBLY",
            "SERVER": "/BOISE_2/MTI/MFG/MAM/PROD/MAMSRV/MOD_XML",
            "REPORT_SERVER": "/BOISE_2/MTI/MFG/MAM/PROD/MAMRPTSRV/MOD_XML",
        },
    },
    "XIAN": {
        "TEST": {
            "EXECUTION_FACILITY": "TEST-XIAN",
            "SERVER": "/XIAN/MTI/MFG/MAM/PROD/MAMSRV/TST_XML",
        },
        "MODULES": {
            "APP": "MODXA",
            "ACTION": "REPORT",
            "FORMAT": "XML",
            "USERID": "MODULETESTOBJECT",
            "REPORTID": "",
            "CRITERIA": "",
            "EXECUTION_FACILITY": "MODULE-XIAN",
            "SERVER": "/XIAN/MTI/MFG/MAM/PROD/MAMSRV/MODXA_XML",
            "REPORT_SERVER": "/XIAN/MTI/MFG/MAM/PROD/MAMRPTSRV/MODXA_XML",
        },
    },
}
# MXA alias
MAM_QUERY_SETTINGS["MXA"] = MAM_QUERY_SETTINGS["XIAN"]

# Legacy config kept for SOAP-based queries (zeep fallback)
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
DEFAULT_SITE = "PENANG"


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

    Uses the native PyMIPC DLL (same approach as CAT's CATCALL.py
    MAMCommunicator) for direct MIPC communication.  When PyMIPC is not
    available, all operations return failure gracefully so the caller can
    fall back to the SOAP web gateway.

    Parameters
    ----------
    site : str
        Site name (SINGAPORE, PENANG, BOISE, XIAN) for server config lookup.
    """

    def __init__(self, site: str = ""):
        self.site = (site or DEFAULT_SITE).upper()
        self._mam_values = MAM_QUERY_SETTINGS.get(
            self.site, MAM_QUERY_SETTINGS[DEFAULT_SITE])
        self._hostname = socket.gethostname()

    @property
    def is_available(self) -> bool:
        """Check if PyMIPC is available for MAM operations."""
        return HAS_PYMIPC

    # ------------------------------------------------------------------
    # GetLotAttributes — mirrors CAT CATCALL.py MAMCommunicator.GetLotAttributes
    # ------------------------------------------------------------------
    def get_lot_attributes(self, lot: str,
                           attributes: Optional[List[str]] = None,
                           tsums_format: bool = True) -> MAMResult:
        """Query lot attributes from MAM via native MIPC.

        Mirrors CAT ``MAMCommunicator.GetLotAttributes()``.  Sends a
        ``GetMAInfo`` SOAP envelope through the MIPC DLL and parses the
        XML response into a dict.

        Parameters
        ----------
        lot : str
            Lot ID to query.
        attributes : list of str, optional
            Ignored (queries all attributes).  Kept for API compat.
        tsums_format : bool
            If True, replace spaces with underscores in keys/values
            (matches CAT's ``tsumsFormat=True``).

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
            sFrom = "/%s/SIG/%s" % (self.site, self._hostname)
            server = self._mam_values["MODULES"]["SERVER"]
            exec_facility = self._mam_values["MODULES"]["EXECUTION_FACILITY"]

            msg = (
                "<SOAP-ENV:Envelope xmlns:SOAP-ENV="
                "'http://schemas.xmlsoap.org/soap/envelope'>"
                "<SOAP-ENV:Header>"
                "<MTXMLMsg MsgType='CMD' />"
                "<Delivery>"
                "<Dest>{dest}</Dest>"
                "<Src>{src}</Src>"
                "<Reply>{src}</Reply>"
                "<TransportType>MIPC</TransportType>"
                "</Delivery>"
                "</SOAP-ENV:Header>"
                "<SOAP-ENV:Body>"
                "<GetMAInfo>"
                "<MAId>{lot}</MAId>"
                "<MAType>MODULE LOT</MAType>"
                "<ExecutionFacility>{facility}</ExecutionFacility>"
                "<MAAttrAll />"
                "<Authorization>"
                "<ClientId>MODULETESTOBJECT</ClientId>"
                "</Authorization>"
                "</GetMAInfo>"
                "</SOAP-ENV:Body>"
                "</SOAP-ENV:Envelope>"
            ).format(dest=server, src=sFrom, lot=lot, facility=exec_facility)

            mipc = _PyMIPC.MIPC()
            status, xml_result = mipc.SendReceive(server, msg)

            if status <= 0:
                err_detail = ""
                if hasattr(xml_result, 'value'):
                    err_detail = xml_result.value.decode('utf-8', errors='replace')
                elif isinstance(xml_result, bytes):
                    err_detail = xml_result.decode('utf-8', errors='replace')
                result.error = (f"MIPC call failed with status={status}. "
                                f"Error: {err_detail}")
                logger.error(result.error)
                return result

            # Decode bytes response
            if isinstance(xml_result, bytes):
                xml_str = xml_result.decode('utf-8', errors='replace')
            elif hasattr(xml_result, 'value'):
                xml_str = xml_result.value.decode('utf-8', errors='replace')
            else:
                xml_str = str(xml_result)

            result.attributes = self._parse_attrs(xml_str, tsums_format)
            result.success = True
            logger.info("MAM PyMIPC query for lot %s: %d attributes",
                        lot, len(result.attributes))

        except Exception as e:
            result.error = f"MAM PyMIPC query exception: {e}"
            logger.exception(result.error)

        return result

    # ------------------------------------------------------------------
    # SetLotAttributes — mirrors CAT CATCALL.py MAMCommunicator.SetLotAttributes
    # ------------------------------------------------------------------
    def set_lot_attributes(self, lot: str, attrs: Dict[str, str]) -> MAMResult:
        """Set lot attributes in MAM via native MIPC.

        Mirrors CAT ``MAMCommunicator.SetLotAttributes()``.

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
            sFrom = "/%s/SIG/%s" % (self.site, self._hostname)
            server = self._mam_values["MODULES"]["SERVER"]
            exec_facility = self._mam_values["MODULES"]["EXECUTION_FACILITY"]

            # Build <Attr> elements
            sb_attrs = ""
            for key, value in attrs.items():
                sb_attrs += (
                    "<Attr><Action>ASSIGN ATTRIBUTE</Action>"
                    "<Id>{k}</Id><Val>{v}</Val></Attr>"
                ).format(k=key, v=value)

            msg = (
                "<SOAP-ENV:Envelope xmlns:SOAP-ENV="
                "'http://schemas.xmlsoap.org/soap/envelope/' "
                "xmlns:MIPC='http://midsysprog.micron.com'>"
                "<SOAP-ENV:Header>"
                "<Delivery>"
                "<Src>{src}</Src>"
                "<Dest>{dest}</Dest>"
                "<Reply>{src}</Reply>"
                "<TransportType>MIPC</TransportType>"
                "</Delivery>"
                "<MTXMLMsg MsgType='CMD'>"
                "<MessageId>1025</MessageId>"
                "</MTXMLMsg>"
                "</SOAP-ENV:Header>"
                "<SOAP-ENV:Body>"
                "<TrackItem>"
                "<Authorization>"
                "<ClientId>MODULETESTOBJECT</ClientId>"
                "</Authorization>"
                "<Keyword>PLUSAMR-MODTESTATTRS</Keyword>"
                "<ExecutionFacility>{facility}</ExecutionFacility>"
                "<MAType>MODULE LOT</MAType>"
                "<MAId>{lot}</MAId>"
                "<MAAttrList>{attrs}</MAAttrList>"
                "</TrackItem>"
                "</SOAP-ENV:Body>"
                "</SOAP-ENV:Envelope>"
            ).format(src=sFrom, dest=server, facility=exec_facility,
                     lot=lot, attrs=sb_attrs)

            mipc = _PyMIPC.MIPC()
            status, reply = mipc.Send(sFrom, server, sFrom, msg)

            if status <= 0:
                err_detail = ""
                if hasattr(reply, 'value'):
                    err_detail = reply.value.decode('utf-8', errors='replace')
                elif isinstance(reply, bytes):
                    err_detail = reply.decode('utf-8', errors='replace')
                result.error = (f"MIPC set failed with status={status}. "
                                f"Error: {err_detail}")
                logger.error(result.error)
                return result

            result.success = True
            result.attributes = attrs
            logger.info("MAM PyMIPC set for lot %s: %s", lot, list(attrs.keys()))

        except Exception as e:
            result.error = f"MAM PyMIPC set exception: {e}"
            logger.exception(result.error)

        return result

    # ------------------------------------------------------------------
    # _parse_attrs — mirrors CAT CATCALL.py MAMCommunicator._parseAttrs
    # ------------------------------------------------------------------
    def _parse_attrs(self, xml_attrs: str,
                     tsums_format: bool = True) -> Dict[str, str]:
        """Parse MAM XML attribute response into a dict.

        Mirrors CAT ``MAMCommunicator._parseAttrs()``.  The MAM response
        contains ``<Attr><Id>…</Id><Val>…</Val></Attr>`` elements.

        Parameters
        ----------
        xml_attrs : str
            Full SOAP XML response string from MAM.
        tsums_format : bool
            If True, replace spaces with underscores in keys and values
            (matches CAT's ``tsumsFormat=True``).

        Returns
        -------
        dict
            Parsed attribute key-value pairs, sorted alphabetically.
        """
        attrs: Dict[str, str] = {}
        try:
            root = ET.fromstring(xml_attrs)
            for attr_elem in root.iter("Attr"):
                id_node = attr_elem.find("Id")
                val_node = attr_elem.find("Val")
                if id_node is not None and id_node.text and \
                   val_node is not None and val_node.text:
                    key = id_node.text.strip()
                    value = val_node.text.strip()
                    if tsums_format:
                        key = key.replace(" ", "_")
                        value = value.replace(" ", "_")
                    attrs[key] = value
        except Exception as e:
            logger.warning("Failed to parse MAM attributes: %s", e)
            # Fallback: try <Attribute name="..." value="..."/> format
            try:
                if not xml_attrs.strip().startswith("<?xml") and \
                   not xml_attrs.strip().startswith("<"):
                    xml_attrs = f"<root>{xml_attrs}</root>"
                root2 = ET.fromstring(xml_attrs)
                for elem in root2.iter("Attribute"):
                    name = elem.get("name", "")
                    value = elem.get("value", "")
                    if name:
                        if tsums_format:
                            name = name.replace(" ", "_")
                            value = value.replace(" ", "_")
                        attrs[name] = value
            except Exception:
                pass
        return collections.OrderedDict(sorted(attrs.items()))

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
            "cfgpn": result.get("BASE_CFGPN", result.get("CFGPN", "")),
            "mcto": result.get("MODULE_FGPN", result.get("MCTO", "")),
        }


# ---------------------------------------------------------------------------
# MIPC SOAP Web Gateway — adapted from blockrun_lot_checker
# (Common_Functions_64.py + checklots.py by mbinsyedaham / JIAJUNLEE)
#
# Uses zeep SOAP client to call the Micron Messaging Gateway, which is
# accessible from any Micron-networked PC (unlike PyMIPC which requires
# specific library installation).
# ---------------------------------------------------------------------------

# MIPC web gateway WSDL
_MIPC_WSDL = "http://web.micron.com/micronmsgateway/micronmessaginggateway.asmx?wsdl"

# Per-facility MAM report destinations (from checklots.py)
_MAM_REPORT_DESTINATIONS: Dict[str, Dict[str, str]] = {
    "SINGAPORE": {
        "dest": "/SINGAPORE/MTI/MFG/MAM/PROD/MAMRPTSRV/MODSI_XML",
        "system": "MODSI",
    },
    "PENANG": {
        "dest": "/PENANG/MTI/MFG/MAM/PROD/MAMRPTSRV/MODPG_XML",
        "system": "MODPG",
    },
}

# Lot prefix → facility mapping (from checklots.py)
_LOT_PREFIX_FACILITY = {
    "J": "PENANG",   # Penang lots start with J
    "C": "SINGAPORE",  # Singapore lots start with C
}

# MAM attributes we want to retrieve for lot lookup.
# Includes the critical attributes that recipe_selection.py's eval_rules()
# compares against CFGPN — without these, BENTO cannot detect mismatches
# early and the tester shows "Critical Attribute check failed".
_LOT_LOOKUP_ATTRS = [
    "STEP OR LOCATION",
    "BASE CFGPN",
    "MODULE FGPN",
    "MODULE FORM FACTOR",
    "MATERIAL DESCRIPTION",
    # Critical attributes for MAM vs CFGPN validation
    "PCB DESIGN ID",
    "DESIGN ID",           # maps to CFGPN COMP1_DESIGN_ID
    "DIE2 DESIGN ID",      # maps to CFGPN COMP2_DESIGN_ID
    "PCB ARTWORK REV",
    "PRODUCT GROUP",
]


def _determine_facility(lot: str) -> str:
    """Determine facility from lot prefix.

    Parameters
    ----------
    lot : str
        Lot ID (e.g. ``"JAATQ95001"`` → Penang, ``"CABC12345"`` → Singapore).

    Returns
    -------
    str
        Facility name (``"PENANG"`` or ``"SINGAPORE"``), or ``""`` if unknown.
    """
    if lot:
        prefix = lot[0].upper()
        return _LOT_PREFIX_FACILITY.get(prefix, "")
    return ""


def _mipc_send_receive(dest: str, msg: str, timeout: int = 180) -> tuple:
    """Send MIPC message via SOAP web gateway and receive response.

    Adapted from ``Common_Functions_64.MIPC_Send_Receive()``.

    Parameters
    ----------
    dest : str
        MIPC destination path.
    msg : str
        SOAP message body.
    timeout : int
        Timeout in seconds (default 180).

    Returns
    -------
    tuple
        ``("success", response_text)`` or ``("error", error_text)``.
    """
    if not HAS_ZEEP:
        return ("error", "zeep not available — cannot send MIPC message")

    try:
        # Disable SSL verification for internal Micron servers
        # (same pattern as jira_analyzer.py ssl._create_unverified_context())
        session = requests.Session()
        session.verify = False
        # Suppress InsecureRequestWarning for internal endpoints
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        transport = ZeepTransport(session=session)

        client = ZeepClient(_MIPC_WSDL, transport=transport)
        client.set_ns_prefix('tns', "http://web.micron.com/MicronMessagingGateway")
        client.bind(port_name='MicronMessagingGatewaySoap')

        with client.settings(raw_response=True):
            mam_webservice = client.get_type("tns:MTMessageWeb")
            msg_content = mam_webservice(
                Destination=dest,
                MsgContents=msg,
                MsgFormat='XML',
                MTXmlMsgType='XMLCMDMSG',
            )
            response = client.service.MipcSendReceive(
                message=msg_content, timeout=timeout
            )

        # Only check for transport-level SOAP faults (<soap:Fault> in the
        # outer envelope body, NOT inside MsgContents). Inner MAM faults
        # (e.g. "invalid lot ID") are handled by _pull_mam_report().
        # The outer envelope wraps MsgContents which may contain its own
        # faultstring — that's a MAM-level error, not a transport error.
        return ("success", response.text)

    except Exception as e:
        logger.exception("MIPC SOAP call failed")
        return ("error", str(e))


def _get_mipc_msg_contents(response_xml: str) -> str:
    """Parse MIPC full response XML and return ReceivedMsgContents.

    Adapted from ``Common_Functions_64.get_MIPC_ReceivedMsgContents()``.

    Parameters
    ----------
    response_xml : str
        Full SOAP response XML.

    Returns
    -------
    str
        The inner ``MsgContents`` XML string.
    """
    ns = {
        'ns0': 'http://schemas.xmlsoap.org/soap/envelope/',
        'ns1': 'http://web.micron.com/MicronMessagingGateway',
    }
    tree = ET.ElementTree(ET.fromstring(response_xml))
    root = tree.getroot()
    elem = root.find(
        "ns0:Body/ns1:MipcSendReceiveResponse/"
        "ns1:MipcSendReceiveResult/ns1:MsgContents",
        ns,
    )
    if elem is not None and elem.text:
        return elem.text
    raise ValueError("MsgContents not found in MIPC response")


def _pull_mam_report(dest: str, msg: str, timeout: int = 360) -> List[Dict[str, str]]:
    """Pull MAM Report and return items as list of dicts.

    Adapted from ``Common_Functions_64.pull_MAM_Report()`` +
    ``checklots.process_mam_data()``.

    Parameters
    ----------
    dest : str
        MIPC destination path.
    msg : str
        SOAP message body.
    timeout : int
        Timeout in seconds (default 360).

    Returns
    -------
    list of dict
        Each dict contains the MAM report row attributes.

    Raises
    ------
    RuntimeError
        If the MIPC call fails.
    """
    result, response_xml = _mipc_send_receive(dest, msg, timeout)
    if result == 'error':
        raise RuntimeError(f"MIPC error: {response_xml}")

    received = _get_mipc_msg_contents(response_xml)

    tree = ET.ElementTree(ET.fromstring(received))
    root = tree.getroot()

    ns_soap = '{http://schemas.xmlsoap.org/soap/envelope}'

    # ------------------------------------------------------------------
    # Detect inner MAM faults -- the MsgContents may itself be a SOAP
    # envelope whose Body contains a <SOAP-ENV:Fault> element instead of
    # a MAMReport.  Extract the faultstring and raise so callers get a
    # clear error message (e.g. "invalid lot ID").
    # ------------------------------------------------------------------
    fault_elem = root.find(f'{ns_soap}Body/{ns_soap}Fault')
    if fault_elem is not None:
        fs = fault_elem.findtext('faultstring') or "Unknown MAM fault"
        raise RuntimeError(f"MAM fault: {fs}")

    num_rows_elem = root.find(f'{ns_soap}Body/MAMReport/NUMROWS')
    if num_rows_elem is None or num_rows_elem.text is None:
        return []

    num_rows = int(num_rows_elem.text)
    if num_rows == 0:
        return []

    all_items: List[Dict[str, str]] = []
    for row in root.findall(f'{ns_soap}Body/MAMReport/ReportData/Row'):
        store_item: Dict[str, str] = {}
        for val in row:
            if val.tag == 'Attrs':
                for attr in val:
                    attr_id = attr.get('ID', '')
                    # Normalise: spaces→underscores, /→_, remove #
                    key = attr_id.replace(" ", "_").replace("/", "_").replace("#", "")
                    store_item[key] = attr.text or ""
            elif val.tag == 'GroupByAttrs':
                for attr in val:
                    attr_id = attr.get('ID', '')
                    key = attr_id.replace(" ", "_").replace("/", "_").replace("#", "")
                    store_item[key] = attr.text or ""
            else:
                store_item[val.tag] = val.text or ""
        all_items.append(store_item)

    return all_items


def _build_lot_report_soap(dest: str, system: str, lot: str) -> str:
    """Build SOAP message for MAM lot report query.

    Adapted from ``checklots.create_soap_message()``.

    Parameters
    ----------
    dest : str
        MIPC destination path.
    system : str
        MAM system identifier (e.g. ``"MODSI"``, ``"MODPG"``).
    lot : str
        Lot ID to query.

    Returns
    -------
    str
        SOAP XML message string.
    """
    attrs_xml = "\n".join(
        f"<Value>{attr}</Value>" for attr in _LOT_LOOKUP_ATTRS
    )
    return f"""
    <SOAP-ENV:Envelope xmlns:SOAP-ENV='http://schemas.xmlsoap.org/soap/envelope'>
        <SOAP-ENV:Header>
            <MTXMLMsg MsgType='CMD'/>
            <Delivery>
                <Dest>{dest}</Dest>
                <Src/>
                <Reply/>
                <TransportType>MIPC</TransportType>
            </Delivery>
        </SOAP-ENV:Header>
        <SOAP-ENV:Body>
            <MAMReport>
                <REPORTID>Module Lots with Attributes</REPORTID>
                <APP>{system}</APP>
                <FORMAT>XML</FORMAT>
                <ACTION>REPORT</ACTION>
                <CRITERIA>
                    <CriteriaList>
                        <Criteria>
                            <Item>MODULE LOT ID</Item>
                            <Rel>in</Rel>
                            <ValueList>
                                <Value>{lot}</Value>
                            </ValueList>
                        </Criteria>
                        <Criteria>
                            <Item>ATTRS TO DISPLAY</Item>
                            <Rel>in</Rel>
                            <ValueList>
                                {attrs_xml}
                            </ValueList>
                        </Criteria>
                    </CriteriaList>
                </CRITERIA>
            </MAMReport>
        </SOAP-ENV:Body>
    </SOAP-ENV:Envelope>"""


def query_lot_cfgpn_mcto(
    lot: str,
    site: str = "",
    timeout: int = 360,
) -> Dict[str, str]:
    """Query MAM via MIPC SOAP to get CFGPN and MCTO for a dummy lot.

    Uses the zeep-based MIPC web gateway (same approach as
    blockrun_lot_checker) to pull a MAM report for the given lot and
    extract ``BASE_CFGPN`` (→ CFGPN) and ``MODULE_FGPN`` (→ MCTO).

    This is the primary method for resolving a dummy lot to its
    CFGPN and MCTO values during BENTO checkout.

    Parameters
    ----------
    lot : str
        Dummy lot ID (e.g. ``"JAATQ95001"``).
    site : str, optional
        Force a specific site (``"SINGAPORE"`` or ``"PENANG"``).
        If empty, auto-detected from lot prefix (J=Penang, C=Singapore).
    timeout : int
        MIPC timeout in seconds (default 360).

    Returns
    -------
    dict
        Dict with keys:
        - ``"cfgpn"`` — BASE CFGPN value (empty string if not found)
        - ``"mcto"`` — MODULE FGPN value (empty string if not found)
        - ``"step"`` — STEP OR LOCATION value (empty string if not found)
        - ``"form_factor"`` — MODULE FORM FACTOR value (empty string if not found)
        - ``"material_desc"`` — MATERIAL DESCRIPTION value (empty string if not found)
        - ``"success"`` — ``"true"`` or ``"false"``
        - ``"error"`` — error message if failed
        - ``"all_attrs"`` — dict of all returned attributes
    """
    result: Dict[str, str] = {
        "cfgpn": "",
        "mcto": "",
        "step": "",
        "form_factor": "",
        "material_desc": "",
        "success": "false",
        "error": "",
    }

    if not lot or not lot.strip():
        result["error"] = "No lot ID provided"
        return result

    lot = lot.strip()

    if not HAS_ZEEP:
        result["error"] = "zeep not available — cannot query MAM via SOAP"
        logger.warning(result["error"])
        return result

    # Determine facility
    facility = site.upper() if site else _determine_facility(lot)
    if not facility or facility not in _MAM_REPORT_DESTINATIONS:
        result["error"] = (
            f"Cannot determine MAM facility for lot '{lot}' "
            f"(prefix '{lot[0] if lot else '?'}', site='{site}'). "
            f"Supported: {list(_MAM_REPORT_DESTINATIONS.keys())}"
        )
        logger.warning(result["error"])
        return result

    config = _MAM_REPORT_DESTINATIONS[facility]
    dest = config["dest"]
    system = config["system"]

    logger.info(
        "Querying MAM report for lot=%s facility=%s system=%s",
        lot, facility, system,
    )

    try:
        soap_msg = _build_lot_report_soap(dest, system, lot)
        items = _pull_mam_report(dest, soap_msg, timeout)

        if not items:
            result["error"] = f"MAM report returned no rows for lot '{lot}'"
            logger.warning(result["error"])
            return result

        # Take the last row (most recent) — matches checklots.py behaviour
        row = items[-1]

        # Extract CFGPN and MCTO using the column mappings from the Teams chat:
        #   DUMMY LOT = CAT COLUMN NAME
        #   MODULE FGPN = MCTO#1
        #   BASE CFGPN = CFGPN
        result["cfgpn"] = row.get("BASE_CFGPN", "").strip()
        result["mcto"] = row.get("MODULE_FGPN", "").strip()
        result["step"] = row.get("STEP_OR_LOCATION", "").strip()
        result["form_factor"] = row.get("MODULE_FORM_FACTOR", "").strip()
        result["material_desc"] = row.get("MATERIAL_DESCRIPTION", "").strip()
        result["success"] = "true"
        result["all_attrs"] = row  # type: ignore[assignment]

        logger.info(
            "MAM lot query OK: lot=%s → CFGPN=%s, MCTO=%s, STEP=%s, "
            "FORM_FACTOR=%s, MATERIAL_DESC=%s",
            lot, result["cfgpn"], result["mcto"], result["step"],
            result["form_factor"], result["material_desc"],
        )

    except Exception as e:
        result["error"] = f"MAM lot query failed: {e}"
        logger.exception(result["error"])

    return result


# ── CFGPN-BASED LOT SEARCH ────────────────────────────────────────────────────


def _build_cfgpn_report_soap(dest: str, system: str, cfgpn: str) -> str:
    """Build SOAP message to query MAM lots by BASE CFGPN.

    Same structure as ``_build_lot_report_soap`` but uses
    ``<Item>BASE CFGPN</Item>`` as the criteria instead of
    ``<Item>MODULE LOT ID</Item>``.

    Parameters
    ----------
    dest : str
        MIPC destination path.
    system : str
        MAM system identifier (e.g. ``"MODSI"``, ``"MODPG"``).
    cfgpn : str
        CFGPN value to search for (e.g. ``"923111"``).

    Returns
    -------
    str
        SOAP XML message string.
    """
    # We only need a subset of attributes for the suggestion display
    suggestion_attrs = [
        "STEP OR LOCATION",
        "BASE CFGPN",
        "MODULE FGPN",
        "MODULE FORM FACTOR",
        "MATERIAL DESCRIPTION",
        "PCB DESIGN ID",
        "DESIGN ID",
        "DIE2 DESIGN ID",
        "PCB ARTWORK REV",
        "PRODUCT GROUP",
    ]
    attrs_xml = "\n".join(
        f"<Value>{attr}</Value>" for attr in suggestion_attrs
    )
    return f"""
    <SOAP-ENV:Envelope xmlns:SOAP-ENV='http://schemas.xmlsoap.org/soap/envelope'>
        <SOAP-ENV:Header>
            <MTXMLMsg MsgType='CMD'/>
            <Delivery>
                <Dest>{dest}</Dest>
                <Src/>
                <Reply/>
                <TransportType>MIPC</TransportType>
            </Delivery>
        </SOAP-ENV:Header>
        <SOAP-ENV:Body>
            <MAMReport>
                <REPORTID>Module Lots with Attributes</REPORTID>
                <APP>{system}</APP>
                <FORMAT>XML</FORMAT>
                <ACTION>REPORT</ACTION>
                <CRITERIA>
                    <CriteriaList>
                        <Criteria>
                            <Item>BASE CFGPN</Item>
                            <Rel>in</Rel>
                            <ValueList>
                                <Value>{cfgpn}</Value>
                            </ValueList>
                        </Criteria>
                        <Criteria>
                            <Item>ATTRS TO DISPLAY</Item>
                            <Rel>in</Rel>
                            <ValueList>
                                {attrs_xml}
                            </ValueList>
                        </Criteria>
                    </CriteriaList>
                </CRITERIA>
            </MAMReport>
        </SOAP-ENV:Body>
    </SOAP-ENV:Envelope>"""


def query_lots_by_cfgpn(
    cfgpn: str,
    site: str = "",
    sap_attrs: Optional[Dict[str, str]] = None,
    timeout: int = 360,
    max_suggestions: int = 5,
) -> List[Dict[str, str]]:
    """Query MAM for lots matching a given CFGPN and filter by SAP attributes.

    Used by the pre-flight mismatch handler to suggest alternative lots
    whose MAM attributes actually match the CFGPN specification in SAP.

    Parameters
    ----------
    cfgpn : str
        CFGPN value to search for (e.g. ``"923111"``).
    site : str, optional
        Force a specific site (``"SINGAPORE"`` or ``"PENANG"``).
        If empty, tries PENANG first (most common for J-prefix lots).
    sap_attrs : dict, optional
        SAP CFGPN attributes to filter against. If provided, only lots
        whose MAM critical attributes match SAP are returned.
        Expected keys: ``PCB_DESIGN_ID``, ``COMP1_DESIGN_ID``,
        ``COMP2_DESIGN_ID``, ``PCB_ARTWORK_REV``.
    timeout : int
        MIPC timeout in seconds (default 360).
    max_suggestions : int
        Maximum number of matching lots to return (default 5).

    Returns
    -------
    list of dict
        Each dict has keys: ``lot_id``, ``step``, ``form_factor``,
        ``pcb``, ``design_id``, ``die2``, ``rev``, ``product_group``,
        ``material_desc``.
        Empty list if no matching lots found or query fails.
    """
    if not cfgpn or not cfgpn.strip():
        return []

    cfgpn = cfgpn.strip()

    if not HAS_ZEEP:
        logger.warning("zeep not available — cannot query MAM for lots by CFGPN")
        return []

    # Determine facility — if site given use it, otherwise try PENANG first
    facility = site.upper() if site else "PENANG"
    if facility not in _MAM_REPORT_DESTINATIONS:
        logger.warning("Unknown facility '%s' for CFGPN lot search", facility)
        return []

    config = _MAM_REPORT_DESTINATIONS[facility]
    dest = config["dest"]
    system = config["system"]

    logger.info(
        "Querying MAM for lots with BASE_CFGPN=%s facility=%s",
        cfgpn, facility,
    )

    # SAP-to-MAM key mapping for filtering
    _SAP_TO_MAM_FILTER = {
        "PCB_DESIGN_ID":   "PCB_DESIGN_ID",
        "COMP1_DESIGN_ID": "DESIGN_ID",
        "COMP2_DESIGN_ID": "DIE2_DESIGN_ID",
        "PCB_ARTWORK_REV": "PCB_ARTWORK_REV",
    }

    try:
        soap_msg = _build_cfgpn_report_soap(dest, system, cfgpn)
        items = _pull_mam_report(dest, soap_msg, timeout)

        if not items:
            logger.info("MAM returned no lots for CFGPN=%s at %s", cfgpn, facility)
            return []

        logger.info("MAM returned %d lot(s) for CFGPN=%s", len(items), cfgpn)

        matching_lots: List[Dict[str, str]] = []
        for row in items:
            lot_id = row.get("ModuleLotID", row.get("MODULE_LOT_ID", "?"))
            lot_info = {
                "lot_id":        lot_id,
                "step":          row.get("STEP_OR_LOCATION", ""),
                "form_factor":   row.get("MODULE_FORM_FACTOR", ""),
                "pcb":           row.get("PCB_DESIGN_ID", ""),
                "design_id":     row.get("DESIGN_ID", ""),
                "die2":          row.get("DIE2_DESIGN_ID", ""),
                "rev":           row.get("PCB_ARTWORK_REV", ""),
                "product_group": row.get("PRODUCT_GROUP", ""),
                "material_desc": row.get("MATERIAL_DESCRIPTION", ""),
            }

            # If SAP attrs provided, filter: only include lots that match
            if sap_attrs:
                is_match = True
                for sap_key, mam_key in _SAP_TO_MAM_FILTER.items():
                    sap_val = sap_attrs.get(sap_key, "")
                    mam_val = row.get(mam_key, "")
                    if sap_val and mam_val and sap_val != mam_val:
                        is_match = False
                        break
                if not is_match:
                    continue

            matching_lots.append(lot_info)
            if len(matching_lots) >= max_suggestions:
                break

        logger.info(
            "CFGPN=%s: %d total lots, %d matching SAP attrs",
            cfgpn, len(items), len(matching_lots),
        )
        return matching_lots

    except Exception as e:
        logger.warning("MAM CFGPN lot search failed: %s", e)
        return []


# ── MID VALIDATION ─────────────────────────────────────────────────────────────


def verify_mid_lot_link(
    mid: str,
    expected_cfgpn: str,
    expected_mcto: str,
    site: str = "",
    lot_hint: str = "",
    timeout: int = 360,
) -> Dict[str, str]:
    """Verify that a MID's lot is correctly linked to the expected CFGPN/MCTO.

    MAM does not support querying by MID directly (``MODULE MID`` is not a
    valid criteria item). Instead, this function queries MAM by the lot
    (``lot_hint``) and verifies that the lot's CFGPN and MCTO match the
    expected values from the profile table.

    This confirms the MID is being checked out against the correct lot
    with the correct CFGPN/MCTO assignment.

    Parameters
    ----------
    mid : str
        Module ID (for logging/display only -- not used as MAM criteria).
    expected_cfgpn : str
        Expected CFGPN value (from the profile table row).
    expected_mcto : str
        Expected MCTO value (from the profile table row).
    site : str, optional
        Force a specific site.
    lot_hint : str, optional
        Lot ID to query in MAM (required for verification).
    timeout : int
        MIPC timeout in seconds.

    Returns
    -------
    dict
        Dict with keys:
        - ``"valid"`` -- ``"true"`` if lot's CFGPN/MCTO match expected
        - ``"lot_cfgpn"`` -- CFGPN found for the lot in MAM
        - ``"lot_mcto"`` -- MCTO found for the lot in MAM
        - ``"cfgpn_match"`` -- ``"true"`` if CFGPN matches
        - ``"mcto_match"`` -- ``"true"`` if MCTO matches
        - ``"error"`` -- error message if query failed
        - ``"message"`` -- human-readable summary
    """
    result: Dict[str, str] = {
        "valid": "false",
        "lot_cfgpn": "",
        "lot_mcto": "",
        "cfgpn_match": "false",
        "mcto_match": "false",
        "error": "",
        "message": "",
    }

    if not mid or not mid.strip():
        result["error"] = "No MID provided"
        result["message"] = "No MID provided for validation"
        return result

    if not lot_hint or not lot_hint.strip():
        result["error"] = "No lot provided -- fill Dummy_Lot first"
        result["message"] = (
            f"Cannot verify MID {mid}: no Dummy_Lot in this row. "
            f"Fill the Dummy_Lot column first."
        )
        return result

    # Query MAM by lot (not by MID -- MODULE MID is not a valid criteria)
    lot_result = query_lot_cfgpn_mcto(
        lot_hint.strip(), site=site, timeout=timeout
    )

    if lot_result.get("success") != "true":
        result["error"] = lot_result.get("error", "Lot query failed")
        result["message"] = (
            f"Cannot verify MID {mid}: lot '{lot_hint}' query failed -- "
            f"{result['error']}"
        )
        return result

    lot_cfgpn = lot_result.get("cfgpn", "")
    lot_mcto = lot_result.get("mcto", "")
    result["lot_cfgpn"] = lot_cfgpn
    result["lot_mcto"] = lot_mcto

    # Compare lot's MAM values against expected (from profile table)
    cfgpn_ok = lot_cfgpn == expected_cfgpn.strip() if expected_cfgpn else True
    mcto_ok = lot_mcto == expected_mcto.strip() if expected_mcto else True

    result["cfgpn_match"] = "true" if cfgpn_ok else "false"
    result["mcto_match"] = "true" if mcto_ok else "false"
    result["valid"] = "true" if (cfgpn_ok and mcto_ok) else "false"

    if cfgpn_ok and mcto_ok:
        result["message"] = (
            f"MID {mid} verified OK: lot '{lot_hint}' has "
            f"CFGPN={lot_cfgpn}, MCTO={lot_mcto} (matches table)"
        )
        logger.info(result["message"])
    else:
        mismatches = []
        if not cfgpn_ok:
            mismatches.append(
                f"CFGPN mismatch (MAM has '{lot_cfgpn}', "
                f"table has '{expected_cfgpn}')"
            )
        if not mcto_ok:
            mismatches.append(
                f"MCTO mismatch (MAM has '{lot_mcto}', "
                f"table has '{expected_mcto}')"
            )
        result["message"] = (
            f"MID {mid} / lot '{lot_hint}' MISMATCH: "
            f"{'; '.join(mismatches)}"
        )
        logger.warning(result["message"])

    return result
