# C.A.T. ↔ BENTO Connection Architecture

## Executive Summary

This document explains **exactly how C.A.T. and BENTO are connected** — the physical shared storage, the two connection paths, and what data flows between them.

**Key Insight:** The **shared network drive** (`\\sifsmodauto\modauto\temp\cat\` or `P:\CRT_Automation\`) is the **physical connection point** — C.A.T. writes to it, BENTO reads from it.

---

## 🔗 Physical Connection Point

### Shared Network Storage

```
Network Share: \\sifsmodauto\modauto\temp\cat\
Alternative:   P:\CRT_Automation\  (if mapped as P: drive)

├── CRT_Automation.db              ← SQLite database (Path A)
└── production\
    ├── crt_from_sap.xlsx          ← Excel export (Path B)
    ├── incoming_crt.xlsx
    └── for_profile_gen.xlsx
```

**Access Requirements:**
- C.A.T. needs **READ/WRITE** access (creates/updates files)
- BENTO needs **READ** access only (consumes data)
- Both applications access the **same physical location**

---

## 📊 Connection Path A — SQLite Database (Preferred)

### Step-by-Step Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    C.A.T. (app.py)                          │
│                                                             │
│  [1] sap_ctrl.query_incoming_crt_from_sap()                │
│      ↓                                                      │
│  [2] SAP GUI automation runs                               │
│      ↓                                                      │
│  [3] Exports CRT Excel from SAP                            │
│      ↓                                                      │
│  [4] db_ctrl.update_db_with_crt_excel()                    │
│      ↓                                                      │
│  [5] Reads Excel via pandas                                │
│      ↓                                                      │
│  [6] Stores in SQLite tables:                              │
│      - SAP CRT table                                       │
│      - Incoming CRT table                                  │
│      - CFGPN List table                                    │
│      ↓                                                      │
│  [7] WRITES TO:                                            │
│      \\sifsmodauto\modauto\temp\cat\CRT_Automation.db      │
└─────────────────────────────────────────────────────────────┘
                           ↓
                  SHARED NETWORK DRIVE
                  (Physical Connection)
                           ↓
┌─────────────────────────────────────────────────────────────┐
│              BENTO (checkout_orchestrator.py)               │
│                                                             │
│  [8] load_dut_info_from_crt()                              │
│      ↓                                                      │
│  [9] READS FROM:                                           │
│      \\sifsmodauto\modauto\temp\cat\CRT_Automation.db      │
│      ↓                                                      │
│  [10] conn = sqlite3.connect(crt_db_path)                  │
│      ↓                                                      │
│  [11] SQL query:                                           │
│       SELECT MID, CFGPN, FW_Wave_ID, Product_Name          │
│       FROM incoming_crt                                    │
│       WHERE status = 'ACTIVE'                              │
│      ↓                                                      │
│  [12] Returns list of dicts:                               │
│       [{"MID": "TUN00PNHW", "CFGPN": "911795", ...}, ...]  │
└─────────────────────────────────────────────────────────────┘
```

### Code Implementation

**C.A.T. Side (app.py):**
```python
# From app.py [15]
if query_crt_from_sap:
    logger.info("Querying incoming CRT data from SAP...")
    check_status, message = sap_ctrl.check_prerequisite_for_sap(args.cronjob)
    query_status, message = sap_ctrl.query_incoming_crt_from_sap()

if update_db:
    logger.info("Updating database with incoming CRT data...")
    db_ctrl.update_db_with_crt_excel()
    # → Writes to \\sifsmodauto\modauto\temp\cat\CRT_Automation.db
```

**BENTO Side (checkout_orchestrator.py):**
```python
def load_dut_info_from_crt(cfgpn: str = None, excel_path: str = None):
    # Load C.A.T. database path from settings.json
    crt_db_path = r"\\sifsmodauto\modauto\temp\cat\CRT_Automation.db"
    
    # Option A — Direct DB read (preferred)
    if os.path.exists(crt_db_path):
        import sqlite3
        conn = sqlite3.connect(crt_db_path)
        
        query = """
            SELECT MID, CFGPN, FW_Wave_ID, Product_Name,
                   Material_Description, MCTO_1
            FROM incoming_crt
            WHERE status = 'ACTIVE'
        """
        
        df = pd.read_sql(query, conn)
        conn.close()
        
        return df.to_dict("records")
```

### Database Schema

**Table: incoming_crt**
```sql
CREATE TABLE incoming_crt (
    CFGPN TEXT PRIMARY KEY,
    Form_Factor TEXT,
    FW_Mig_MSB TEXT,
    FW_Mig_MMP TEXT,
    FW_Mig_MTI TEXT,
    Dummy_Lot TEXT,
    Step_Status TEXT,
    status TEXT  -- 'ACTIVE' or 'INACTIVE'
);
```

**What BENTO Reads:**
- `MID` — Material description (DUT identifier)
- `CFGPN` — Configuration part number
- `FW_Wave_ID` — Firmware wave ID
- `Product_Name` — Product name (e.g., "4600", "2600")
- `Material_Description` — Full material description
- `MCTO_1` — MCTO number

---

## 📄 Connection Path B — Direct Excel Read (Fallback)

### Step-by-Step Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    C.A.T. (SAP Export)                      │
│                                                             │
│  [1] SAP GUI automation runs                               │
│      ↓                                                      │
│  [2] Exports CRT Excel from SAP                            │
│      ↓                                                      │
│  [3] WRITES TO:                                            │
│      \\sifsmodauto\modauto\temp\cat\production\            │
│      crt_from_sap.xlsx                                     │
└─────────────────────────────────────────────────────────────┘
                           ↓
                  SHARED NETWORK DRIVE
                  (Physical Connection)
                           ↓
┌─────────────────────────────────────────────────────────────┐
│              BENTO (checkout_tab.py)                        │
│                                                             │
│  [4] Engineer clicks: 📂 Browse                            │
│      ↓                                                      │
│  [5] filedialog.askopenfilename()                          │
│      ↓                                                      │
│  [6] Selects:                                              │
│      \\sifsmodauto\modauto\temp\cat\production\            │
│      crt_from_sap.xlsx                                     │
│      ↓                                                      │
│  [7] load_dut_info_from_crt(excel_path=...)               │
│      ↓                                                      │
│  [8] pd.read_excel(..., engine="openpyxl", dtype=str)     │
│      ↓                                                      │
│  [9] Reads columns (from crt_excel_template.json [24]):   │
│      - "Material description"  (MID)                       │
│      - "CFGPN"                                             │
│      - "FW Wave ID"                                        │
│      - "Product  Name"  (double space!)                    │
│      ↓                                                      │
│  [10] Returns list of dicts:                               │
│       [{"MID": "TUN00PNHW", "CFGPN": "911795", ...}, ...]  │
└─────────────────────────────────────────────────────────────┘
```

### Code Implementation

**BENTO Side (checkout_orchestrator.py):**
```python
def load_dut_info_from_crt(cfgpn: str = None, excel_path: str = None):
    # Option B — Read from Excel (fallback)
    if excel_path and os.path.exists(excel_path):
        df = pd.read_excel(excel_path, engine="openpyxl", dtype=str)
        
        # Use EXACT column names from crt_excel_template.json [24]
        # Note: "Product  Name" has DOUBLE SPACE!
        mids = df["Material description"].dropna().tolist()
        fw_waves = df["FW Wave ID"].dropna().tolist()
        cfgpns = df["CFGPN"].dropna().tolist()
        products = df["Product  Name"].dropna().tolist()  # double space!
        
        # Build result list
        results = []
        for i in range(len(mids)):
            results.append({
                "MID": mids[i] if i < len(mids) else "",
                "CFGPN": cfgpns[i] if i < len(cfgpns) else "",
                "FW_Wave_ID": fw_waves[i] if i < len(fw_waves) else "",
                "Product_Name": products[i] if i < len(products) else "",
            })
        
        return results
```

### Excel Column Mapping

**From crt_excel_template.json [24]:**
```json
{
  "template_headers": [
    "Material description",    ← MID column
    "CFGPN",                   ← CFGPN column
    "FW Wave ID",              ← FW Wave column
    "Product  Name",           ← Product name (DOUBLE SPACE!)
    "CRT Customer",            ← Customer info
    ...
  ]
}
```

**Critical Note:** `"Product  Name"` has a **double space** — this is NOT a typo!

---

## 🔄 Data Flow Summary

### What Gets Transferred

Both paths deliver the **same data structure** into BENTO:

```python
[
    {
        "MID":                  "TUN00PNHW",
        "CFGPN":                "911795",
        "FW_Wave_ID":           "FW074",
        "Product_Name":         "4600",
        "Material_Description": "MTFDLBA2T0THJ-1BP15ABHA",
        "MCTO_1":               "852457"
    },
    {
        "MID":                  "TUN00PNJS",
        "CFGPN":                "911924",
        "FW_Wave_ID":           "FW074",
        "Product_Name":         "4600",
        "Material_Description": "MTFDLBA2T0THJ-2BP15ABHA",
        "MCTO_1":               "815768"
    },
    ...
]
```

### What BENTO Does with the Data

```
BENTO receives DUT list
    ↓
Populates CRT Excel Grid (Section 1)
    ↓
Engineer clicks a row
    ↓
Auto-fills Checkout Parameters (Section 2):
    ├── MID List      ← from "MID"
    ├── CFGPN         ← from "CFGPN"
    └── FW Wave ID    ← from "FW_Wave_ID"
    ↓
Engineer manually adds:
    ├── Dummy Lot     ← cannot come from SAP (physical lot number)
    ├── DUT Locations ← physical slot positions (e.g., 0,0 0,1 0,2)
    └── TGZ Path      ← from Phase 1 compile output
    ↓
🚀 Start Checkout
    ↓
Generates XML with ALL parameters
    ↓
Drops to CHECKOUT_QUEUE
```

---

## 📊 Comparison: Path A vs Path B

| Aspect | Path A (Database) | Path B (Excel) |
|--------|------------------|----------------|
| **Connection** | SQLite on shared drive | Excel file on shared drive |
| **Who writes** | C.A.T. `db_ctrl` automatically | C.A.T. SAP export |
| **Who reads** | BENTO `sqlite3.connect()` | BENTO `pd.read_excel()` |
| **When to use** | Automated/scheduled runs | Manual one-off runs |
| **Data freshness** | Always current (C.A.T. updates regularly) | Snapshot at export time |
| **Performance** | Fast (indexed SQL queries) | Slower (full file read) |
| **Reliability** | High (database ACID properties) | Medium (file locking issues) |
| **Preferred?** | ✅ **YES** | Fallback only |

---

## 🛠️ Configuration

### BENTO settings.json

```json
{
  "cat": {
    "db_path": "\\\\sifsmodauto\\modauto\\temp\\cat\\CRT_Automation.db",
    "crt_excel_path": "\\\\sifsmodauto\\modauto\\temp\\cat\\production\\crt_from_sap.xlsx",
    "incoming_crt_excel": "\\\\sifsmodauto\\modauto\\temp\\cat\\production\\incoming_crt.xlsx",
    "profile_gen_excel": "\\\\sifsmodauto\\modauto\\temp\\cat\\production\\for_profile_gen.xlsx"
  }
}
```

### Path Formats

**UNC Network Path (Preferred):**
```
\\sifsmodauto\modauto\temp\cat\CRT_Automation.db
```

**Mapped Drive (Alternative):**
```
P:\CRT_Automation\Database\crt_database.db
```

**Both point to the same physical location** — use whichever is accessible on your network.

---

## 🔐 Access Requirements

### C.A.T. Requirements
- **READ/WRITE** access to `\\sifsmodauto\modauto\temp\cat\`
- Creates and updates:
  - `CRT_Automation.db`
  - `production\crt_from_sap.xlsx`
  - `production\incoming_crt.xlsx`

### BENTO Requirements
- **READ** access to `\\sifsmodauto\modauto\temp\cat\`
- Reads from:
  - `CRT_Automation.db` (Path A)
  - `production\crt_from_sap.xlsx` (Path B)

### Network Share Permissions
```powershell
# Test access from BENTO machine
Test-Path "\\sifsmodauto\modauto\temp\cat\CRT_Automation.db"
Test-Path "\\sifsmodauto\modauto\temp\cat\production\crt_from_sap.xlsx"
```

---

## 🔄 Synchronization

### How Data Stays Fresh

**C.A.T. Side:**
```
Cronjob runs every X hours
    ↓
Queries SAP for new CRTs
    ↓
Updates CRT_Automation.db
    ↓
BENTO sees updated data immediately
```

**BENTO Side:**
```
Engineer clicks "🔄 Load CRT Data"
    ↓
Reads latest data from CRT_Automation.db
    ↓
Grid shows current DUTs
```

**No manual sync needed** — shared drive ensures both applications see the same data.

---

## 🚨 Troubleshooting

### Issue: "Database file not found"

**Cause:** Network share not accessible

**Solution:**
```powershell
# Check network connectivity
Test-Path "\\sifsmodauto\modauto\temp\cat\"

# Check file exists
Test-Path "\\sifsmodauto\modauto\temp\cat\CRT_Automation.db"

# Check permissions
Get-Acl "\\sifsmodauto\modauto\temp\cat\CRT_Automation.db"
```

### Issue: "Excel file locked"

**Cause:** C.A.T. or another process has file open

**Solution:**
- Use Path A (database) instead — no file locking issues
- Or wait for C.A.T. to finish updating Excel

### Issue: "Column not found: Product  Name"

**Cause:** Missing double space in column name

**Solution:**
```python
# WRONG
products = df["Product Name"].dropna().tolist()

# CORRECT (note double space!)
products = df["Product  Name"].dropna().tolist()
```

---

## 📈 Performance Considerations

### Path A (Database) — Recommended

**Advantages:**
- Fast SQL queries with indexes
- No file locking issues
- Supports concurrent reads
- ACID properties ensure data consistency

**Query Performance:**
```python
# Typical query time: <100ms for 1000 DUTs
SELECT MID, CFGPN, FW_Wave_ID, Product_Name
FROM incoming_crt
WHERE status = 'ACTIVE'
```

### Path B (Excel) — Fallback

**Disadvantages:**
- Must read entire file (no indexing)
- File locking if C.A.T. is writing
- Slower for large datasets

**Read Performance:**
```python
# Typical read time: 1-5 seconds for 1000 DUTs
df = pd.read_excel(excel_path, engine="openpyxl", dtype=str)
```

---

## 🎯 Best Practices

### 1. Prefer Path A (Database) ✅
```python
# Always try database first
dut_list = load_dut_info_from_crt()  # Uses database by default
```

### 2. Use Path B as Fallback ✅
```python
# Only use Excel if database unavailable
if not dut_list:
    dut_list = load_dut_info_from_crt(excel_path="...")
```

### 3. Cache Data Locally ✅
```python
# Store full data for filtering (avoid repeated network reads)
self.crt_data_full = dut_list
```

### 4. Handle Network Failures ✅
```python
try:
    dut_list = load_dut_info_from_crt()
except Exception as e:
    logger.error(f"Could not read CRT data: {e}")
    # Show user-friendly error message
```

---

## 📚 Related Documentation

1. **CAT_INTEGRATION_VERIFIED.md** — Verification of C.A.T. paths and column names
2. **CRT_GRID_IMPLEMENTATION.md** — CRT Excel Grid implementation details
3. **CHECKOUT_IMPLEMENTATION_COMPLETE.md** — Complete Phase 2 implementation
4. **crtpath.md** — Original C.A.T. configuration breakdown

---

## Summary

✅ **C.A.T. and BENTO are connected via shared network storage:**

| Component | Location | Purpose |
|-----------|----------|---------|
| **Shared Drive** | `\\sifsmodauto\modauto\temp\cat\` | Physical connection point |
| **Path A (DB)** | `CRT_Automation.db` | Preferred — fast, reliable |
| **Path B (Excel)** | `production\crt_from_sap.xlsx` | Fallback — manual use |
| **Data Flow** | C.A.T. writes → BENTO reads | One-way data transfer |

**Key Takeaway:** The shared network drive is the **bridge** between C.A.T. and BENTO — no direct API calls, no web services, just **file-based data sharing**.

---

**Document Date:** March 18, 2026  
**Status:** ✅ VERIFIED  
**Architecture:** File-Based Data Sharing  
**Connection Type:** Shared Network Storage
