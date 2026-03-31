# SAP Integration Implementation Summary

## Problem Statement

BENTO's profile generation pipeline previously relied solely on **MAM (Manufacturing Attribute Management)** for retrieving product configuration data during checkout. This meant that SAP-specific attributes—particularly firmware paths, market segment, and module form factor—were unavailable to the TempTraveler XML and downstream recipe selection logic. Without SAP data, BENTO could not populate the `CFGPN`, `MCTO`, or `CONSTANT` sections of the TempTraveler, limiting the completeness and accuracy of generated profiles.

## What Was Implemented

### SAP vs MAM: What Each Provides

| Source | Data Provided | Usage |
|--------|--------------|-------|
| **MAM** | Product attributes, configuration metadata | Existing profile generation attributes |
| **SAP** | Firmware paths, `MARKET_SEGMENT`, `MODULE_FORM_FACTOR`, CFGPN/MCTO characteristics | TempTraveler CFGPN/MCTO/CONSTANT sections, recipe selection inputs |

SAP exposes its data via the `Z_VCTO_GET_MID_DATA_2` RFC, which returns `ZVCTO_MID_CHAR` records—key/value pairs of `CHARC_NAME` → `CHARC_VALUE`.

---

## Files Created

### 1. `model/sap_communicator.py` — SAP SOAP Web Service Client

The [`SAPCommunicator`](model/sap_communicator.py) class provides a clean interface to SAP's SOAP/RFC endpoint:

- **`get_cfgpn_data(cfgpn)`** — Queries SAP for a given CFGPN and returns a `SAPResult` object containing an attributes dictionary.
- **`extract_constant_dict(cfgpn_attrs)`** — Extracts firmware paths, `MARKET_SEGMENT`, and `MODULE_FORM_FACTOR` from the raw SAP attribute map into a structured constant dictionary.
- **`SAP_FIRMWARE_KEYS`** — Defines the canonical list of firmware-related keys extracted from SAP:
  ```python
  SAP_FIRMWARE_KEYS = [
      'MFG_TST_VERSION',
      'TST_PARAM_PATH',
      'CUST_FW_PATH',
      'MFG_FW_PATH',
      'RELEASE_NOTES_PATH',
      'BASE_CFGPN'
  ]
  ```

**Connection details:**
- Endpoint: `http://sap{instance}ms.micron.com/sap/bc/soap/rfc`
- Credentials: `MANU-RFC` / `Micron123`
- SOAP library: `suds-community` (WSDL-based SOAP client)

**Graceful degradation pattern:** The module follows the same pattern established by [`model/mam_communicator.py`](model/mam_communicator.py)—if `suds-community` is not installed, the import is caught and SAP functionality degrades gracefully rather than crashing the application. This allows BENTO to run in environments where SAP connectivity is not required or the dependency is unavailable.

### 2. `model/resources/Z_VCTO_GET_MID_DATA_2.wsdl` — SAP WSDL Service Definition

The [`Z_VCTO_GET_MID_DATA_2.wsdl`](model/resources/Z_VCTO_GET_MID_DATA_2.wsdl) file defines the SOAP service contract for the SAP RFC:

- **Source:** Copied from the CAT (CRT Automation Tools) repository at `C:\Users\NISAZULAIKHA\Documents\crt_automation_tools\SAP\SAPCommunicator\resources\`
- **RFC:** `Z_VCTO_GET_MID_DATA_2`
- **Response type:** `ZVCTO_MID_CHAR` — an array of characteristic records, each containing a `CHARC_NAME` and `CHARC_VALUE` pair

---

## Files Modified

### 3. `requirements.txt` — New Dependency

Added `suds-community>=1.1.2` to support SOAP/WSDL communication with SAP.

### 4. `settings.json` — SAP Configuration

Added two new fields to the `checkout` section:

```json
{
  "checkout": {
    "sap_instance": "PR1",
    "sap_enabled": true
  }
}
```

- **`sap_instance`** — Determines which SAP system to connect to (e.g., `PR1` for production, `QA1` for QA). This value is interpolated into the endpoint URL.
- **`sap_enabled`** — Feature flag to enable/disable SAP integration without removing the code.

### 5. `model/orchestrators/checkout_orchestrator.py` — Core Integration

Major changes to [`checkout_orchestrator.py`](model/orchestrators/checkout_orchestrator.py):

- **New import:** `from model.sap_communicator import SAPCommunicator, SAPResult, SAP_FIRMWARE_KEYS`
- **New helper:** `query_sap_attributes()` — encapsulates the SAP query logic, returning structured CFGPN attributes, MCTO data, and constant dictionaries.
- **Restructured `generate_slate_xml()` flow** (see Architecture section below).
- **Enhanced TempTraveler XML generation** — now populates full `CFGPN`, `MCTO`, and `CONSTANT` sections using SAP data.
- **Updated function signatures:** Both `generate_slate_xml()` and `run_checkout()` now accept an `sap_instance` parameter.

### 6. `model/tmptravl_generator.py` — No Changes Needed

The [`tmptravl_generator.py`](model/tmptravl_generator.py) module already supported `cfgpn_dict`, `mcto_dict`, and `constant_dict` parameters in its `generate()` method, so no modifications were required. The SAP integration simply provides the data these parameters expect.

---

## Architecture

### Data Flow

```
CFGPN (input)
    │
    ▼
┌─────────┐     ┌─────────┐
│   MAM   │     │   SAP   │
│ (attrs) │     │ (SOAP)  │
└────┬────┘     └────┬────┘
     │               │
     │               ├── cfgpn_attrs (CHARC_NAME → CHARC_VALUE)
     │               ├── mcto_dict
     │               └── constant_dict (firmware paths, MARKET_SEGMENT, MODULE_FORM_FACTOR)
     │               │
     ▼               ▼
┌──────────────────────────┐
│   TempTraveler Generator │
│   (tmptravl_generator)   │
│                          │
│   Sections populated:    │
│   • CFGPN section        │
│   • MCTO section         │
│   • CONSTANT section     │
└────────────┬─────────────┘
             │
             ▼
┌──────────────────────────┐
│    Recipe Selection      │
│  (uses SAP constants +   │
│   MAM attrs to select)   │
└──────────────────────────┘
```

### Restructured `generate_slate_xml()` Flow

The previous flow was:

```
MAM → tmptravl → recipe selection
```

The new flow is:

```
MAM → SAP → tmptravl → recipe selection
```

1. **MAM query** — Retrieve product attributes (unchanged).
2. **SAP query** — Call `query_sap_attributes()` to get CFGPN characteristics, MCTO data, and firmware constants from SAP.
3. **TempTraveler generation** — Pass all three dictionaries (`cfgpn_dict`, `mcto_dict`, `constant_dict`) to the TempTraveler generator.
4. **Recipe selection** — Use the enriched attribute set (MAM + SAP) for recipe matching.

### Graceful Degradation Pattern

```python
try:
    from suds.client import Client
    SUDS_AVAILABLE = True
except ImportError:
    SUDS_AVAILABLE = False
```

When `suds-community` is not installed:
- `SUDS_AVAILABLE` is `False`
- SAP queries return empty/default results
- The rest of the pipeline continues with MAM-only data
- No exceptions are raised; warnings are logged

This mirrors the pattern used in [`model/mam_communicator.py`](model/mam_communicator.py) and ensures BENTO remains functional in development or test environments without SAP access.

---

## SAP_FIRMWARE_KEYS and the Replacement List

The `SAP_FIRMWARE_KEYS` constant defines the six firmware-related attributes extracted from SAP:

| Key | Purpose |
|-----|---------|
| `MFG_TST_VERSION` | Manufacturing test version identifier |
| `TST_PARAM_PATH` | Path to test parameter files |
| `CUST_FW_PATH` | Customer firmware file path |
| `MFG_FW_PATH` | Manufacturing firmware file path |
| `RELEASE_NOTES_PATH` | Path to release notes |
| `BASE_CFGPN` | Base configuration part number |

These keys serve as the **replacement list** (`_replacelist`) — they are the specific SAP characteristics that get extracted from the full CFGPN attribute set and placed into the `CONSTANT` section of the TempTraveler. The `extract_constant_dict()` function filters the raw SAP response to include only these keys (plus `MARKET_SEGMENT` and `MODULE_FORM_FACTOR`), ensuring the TempTraveler contains exactly the data needed for downstream firmware loading and recipe selection.

---

## How to Test

### Prerequisites

1. **Install the SOAP dependency:**
   ```bash
   pip install suds-community>=1.1.2
   ```

2. **Configure SAP in `settings.json`:**
   ```json
   {
     "checkout": {
       "sap_instance": "PR1",
       "sap_enabled": true
     }
   }
   ```

### Verification Steps

1. **Unit test SAP connectivity:**
   - Instantiate `SAPCommunicator` with the desired instance (e.g., `"PR1"`).
   - Call `get_cfgpn_data()` with a known CFGPN.
   - Verify the returned `SAPResult` contains expected `CHARC_NAME` → `CHARC_VALUE` pairs.

2. **Test constant extraction:**
   - Pass the SAP result attributes to `extract_constant_dict()`.
   - Verify the output dictionary contains keys from `SAP_FIRMWARE_KEYS` plus `MARKET_SEGMENT` and `MODULE_FORM_FACTOR`.

3. **End-to-end checkout test:**
   - Run a full checkout via `run_checkout()`.
   - Inspect the generated TempTraveler XML for populated `CFGPN`, `MCTO`, and `CONSTANT` sections.
   - Verify recipe selection uses the enriched attribute set.

4. **Graceful degradation test:**
   - Uninstall `suds-community` (`pip uninstall suds-community`).
   - Run checkout — should complete successfully with MAM-only data and log a warning about SAP unavailability.

---

## Summary

| Aspect | Detail |
|--------|--------|
| **Files created** | `model/sap_communicator.py`, `model/resources/Z_VCTO_GET_MID_DATA_2.wsdl` |
| **Files modified** | `requirements.txt`, `settings.json`, `model/orchestrators/checkout_orchestrator.py` |
| **Files unchanged** | `model/tmptravl_generator.py` (already supported the required parameters) |
| **New dependency** | `suds-community>=1.1.2` |
| **Pattern** | Graceful degradation (same as MAM communicator) |
| **SAP endpoint** | `http://sap{instance}ms.micron.com/sap/bc/soap/rfc` |
| **RFC** | `Z_VCTO_GET_MID_DATA_2` |
| **Config keys** | `sap_instance`, `sap_enabled` in `settings.json` checkout section |
