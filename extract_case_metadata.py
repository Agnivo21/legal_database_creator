"""
Court Case Metadata Extractor
==============================
Extracts structured metadata from Indian eCourts PDF case detail pages.

"""

import re
import sys
import json
import argparse
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    raise ImportError("Please install pdfplumber: pip install pdfplumber")


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def clean(text) -> str:
    if text is None:
        return ""
    # Replace newlines with space, collapse whitespace
    return re.sub(r'\s+', ' ', str(text)).strip()


def cell(text) -> str:
    """Clean a table cell — strips newlines inside dates too."""
    if text is None:
        return ""
    return re.sub(r'\s+', '', str(text)).strip()   # no spaces at all (good for dates)


def find_field(pattern: str, text: str, group: int = 1, default=None):
    m = re.search(pattern, text, re.IGNORECASE)
    return clean(m.group(group)) if m else default


# ─────────────────────────────────────────────
# Date normaliser → DD-MM-YYYY
# ─────────────────────────────────────────────

MONTH_MAP = {
    "january":"01","february":"02","march":"03","april":"04",
    "may":"05","june":"06","july":"07","august":"08",
    "september":"09","october":"10","november":"11","december":"12",
    "jan":"01","feb":"02","mar":"03","apr":"04",
    "jun":"06","jul":"07","aug":"08",
    "sep":"09","oct":"10","nov":"11","dec":"12",
}

DATE_PATTERN = re.compile(r'\d{1,2}[-/]\d{1,2}[-/]\d{4}')


def normalise_date(raw) -> str | None:
    """
    Accepts:
      '06-03-2026', '06-03-\n2026', '06-03- 2026'  → '06-03-2026'
      '6th March 2026', '28th April 2025'           → '06-03-2026'
      '↑\n05th May 2026'  (arrow icon prefix)       → '05-05-2026'
    """
    if not raw:
        return None

    raw_str = str(raw)

    # ── Strip leading/trailing non-date junk (arrows ↑↓, icons, symbols) ──
    # Remove any characters before the first digit or letter
    raw_str = re.sub(r'^[^a-zA-Z0-9]+', '', raw_str.strip())
    raw_str = re.sub(r'[^a-zA-Z0-9\s\-/]+$', '', raw_str).strip()

    if not raw_str:
        return None

    # Strip ALL internal whitespace (fixes '06-03-\n2026', '06-03- 2026')
    s = re.sub(r'\s+', '', raw_str)

    # DD-MM-YYYY or DD/MM/YYYY
    m = re.match(r'^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$', s)
    if m:
        return f"{int(m.group(1)):02d}-{int(m.group(2)):02d}-{m.group(3)}"

    # "6th March 2026" / "05th May 2026" — needs spaces preserved
    m = re.match(r'^(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})$', raw_str, re.I)
    if m:
        month = MONTH_MAP.get(m.group(2).lower())
        if month:
            return f"{int(m.group(1)):02d}-{month}-{m.group(3)}"

    # "March 6, 2026"
    m = re.match(r'^([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})$', raw_str, re.I)
    if m:
        month = MONTH_MAP.get(m.group(1).lower())
        if month:
            return f"{int(m.group(2)):02d}-{month}-{m.group(3)}"

    return None


# ─────────────────────────────────────────────
# PDF extraction
# ─────────────────────────────────────────────

def extract_text_from_pdf(pdf_path: str) -> str:
    """Flatten all table rows + plain text into a single string."""
    lines = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                for row in table:
                    parts = [clean(c) for c in row if c and clean(c)]
                    if parts:
                        lines.append(" ".join(parts))
            text = page.extract_text()
            if text:
                lines.append(text)
    return "\n".join(lines)


def extract_tables_raw(pdf_path: str) -> list:
    """Return every raw table row from all pages."""
    rows = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                rows.extend(table)
    return rows


# ─────────────────────────────────────────────
# Court level
# ─────────────────────────────────────────────

def detect_court_level(court_name: str) -> str:
    name = court_name.lower()
    if "high court" in name:
        return "High Court"
    if any(k in name for k in ["sessions", "district"]):
        return "Sessions"
    if any(k in name for k in ["magistrate", "judicial", "jmfc", "acjm", "cjm"]):
        return "Magistrate"
    return "Unknown"


# ─────────────────────────────────────────────
# Location from CNR + court name
# ─────────────────────────────────────────────

CNR_DISTRICT_MAP = {
    # Tripura
    "TRWT": ("West Tripura", "Tripura"),
    "TRSE": ("Sepahijala", "Tripura"),
    "TRDH": ("Dhalai", "Tripura"),
    "TRNT": ("North Tripura", "Tripura"),
    "TRST": ("South Tripura", "Tripura"),
    "TRKH": ("Khowai", "Tripura"),
    "TRGT": ("Gomati", "Tripura"),
    "TRUT": ("Unakoti", "Tripura"),
    # Sikkim
    "SKNM": ("Namchi (South Sikkim)", "Sikkim"),
    "SKGT": ("Gangtok (East Sikkim)", "Sikkim"),
    "SKMN": ("Mangan (North Sikkim)", "Sikkim"),
    "SKGZ": ("Gyalshing (West Sikkim)", "Sikkim"),
    "SKSR": ("Soreng", "Sikkim"),
    "SKPK": ("Pakyong", "Sikkim"),
    # Assam
    "ASKM": ("Kamrup Metropolitan", "Assam"),
    "ASKR": ("Kamrup", "Assam"),
    "ASDB": ("Dibrugarh", "Assam"),
    "ASCC": ("Cachar", "Assam"),
    "ASNG": ("Nagaon", "Assam"),
    "ASJR": ("Jorhat", "Assam"),
    "ASGA": ("Goalpara", "Assam"),
    "ASTN": ("Tinsukia", "Assam"),
    "ASKJ": ("Karimganj", "Assam"),
    "ASNL": ("Nalbari", "Assam"),
    "ASDU": ("Dhubri", "Assam"),
    "ASGL": ("Golaghat", "Assam"),
    "ASBR": ("Barpeta/Bajali", "Assam"),
    "ASHI": ("Hailakandi", "Assam"),
    "ASKK": ("Kokrajhar", "Assam"),
    "ASUA": ("Udalguri", "Assam"),
    "ASCH": ("Chirang", "Assam"),
    "ASKA": ("Karbi Anglong", "Assam"),
    "ASDM": ("Dima Hasao", "Assam"),
    "ASCA": ("Charaideo", "Assam"),
    "ASHJ": ("Hojai", "Assam"),
    "ASSS": ("South Salmara-Mankachar", "Assam"),
    "ASSV": ("Sivasagar", "Assam"),
    "ASSN": ("Sonitpur/Biswanath", "Assam"),
    "ASMJ": ("Majuli", "Assam"),
    "ASWP": ("West Karbi Anglong", "Assam"),
    "ASMR": ("Morigaon", "Assam"),
    "ASLK": ("Lakhimpur", "Assam"),
    "ASDR": ("Darrang", "Assam"),
    "ASBN": ("Bongaigaon", "Assam"),
    "ASDM": ("Dhemaji", "Assam"),
    "ASBK": ("Baksa", "Assam"),
    # Manipur
    "MNIW": ("Imphal West", "Manipur"),
    "MNIE": ("Imphal East", "Manipur"),
    "MNBP": ("Bishnupur", "Manipur"),
    "MNNT": ("Thoubal", "Manipur"),
    "MNSP": ("Senapati", "Manipur"),
    "MNUK": ("Ukhrul", "Manipur"),
    "MNCH": ("Churachandpur", "Manipur"),
    
    # Meghalaya
    "MLSH": ("East Khasi Hills", "Meghalaya"),
    # Nagaland
    "NLKH": ("Kohima", "Nagaland"),
    "NLDM": ("Dimapur", "Nagaland"),
    # Arunachal Pradesh
    "ARIT": ("Itanagar", "Arunachal Pradesh"),
    # Mizoram
    "MZAZ": ("Aizawl", "Mizoram"),
    # Delhi
    "DLND": ("New Delhi", "Delhi"),
    # Maharashtra
    "MHMB": ("Mumbai", "Maharashtra"),
    "MHPN": ("Pune", "Maharashtra"),
    "MHNG": ("Nagpur", "Maharashtra"),
    # Tamil Nadu
    "TNCH": ("Chennai", "Tamil Nadu"),
    # Telangana
    "TSHD": ("Hyderabad", "Telangana"),
    # Karnataka
    "KABG": ("Bengaluru", "Karnataka"),
    # West Bengal
    "WBKL": ("Kolkata", "West Bengal"),
    # Bihar
    "BRPT": ("Patna", "Bihar"),
    # Uttar Pradesh
    "UPLK": ("Lucknow", "Uttar Pradesh"),
    "UPLB": ("Prayagraj", "Uttar Pradesh"),
    # Rajasthan
    "RJJP": ("Jaipur", "Rajasthan"),
    # Gujarat
    "GJAH": ("Ahmedabad", "Gujarat"),
    "GJST": ("Surat", "Gujarat"),
    # Kerala
    "KLKC": ("Ernakulam", "Kerala"),
    "KLTV": ("Thiruvananthapuram", "Kerala"),
    # Madhya Pradesh
    "MPBP": ("Bhopal", "Madhya Pradesh"),
    "MPIN": ("Indore", "Madhya Pradesh"),
    # Chhattisgarh
    "CGRP": ("Raipur", "Chhattisgarh"),
    # Jharkhand
    "JHRN": ("Ranchi", "Jharkhand"),
    # Odisha
    "ODBB": ("Bhubaneswar", "Odisha"),
    "ODCT": ("Cuttack", "Odisha"),
    # Punjab
    "PBLD": ("Ludhiana", "Punjab"),
    # Haryana
    "HRGR": ("Gurugram", "Haryana"),
    # Himachal Pradesh
    "HPSM": ("Shimla", "Himachal Pradesh"),
    # Uttarakhand
    "UKDD": ("Dehradun", "Uttarakhand"),
    # Jammu & Kashmir
    "JKJM": ("Jammu", "Jammu & Kashmir"),
    "JKSR": ("Srinagar", "Jammu & Kashmir"),
    # Goa
    "GAPJ": ("Panaji", "Goa"),
}

CNR_STATE_MAP = {
    "TR":"Tripura","SK":"Sikkim","AS":"Assam","MN":"Manipur",
    "ML":"Meghalaya","MZ":"Mizoram","NL":"Nagaland","AR":"Arunachal Pradesh",
    "DL":"Delhi","MH":"Maharashtra","TN":"Tamil Nadu","TS":"Telangana",
    "KA":"Karnataka","WB":"West Bengal","BR":"Bihar","UP":"Uttar Pradesh",
    "RJ":"Rajasthan","PB":"Punjab","HR":"Haryana","MP":"Madhya Pradesh",
    "CG":"Chhattisgarh","JH":"Jharkhand","OD":"Odisha","GJ":"Gujarat",
    "KL":"Kerala","UK":"Uttarakhand","HP":"Himachal Pradesh",
    "JK":"Jammu & Kashmir","GA":"Goa","AN":"Andaman & Nicobar",
    "LD":"Lakshadweep","DN":"Dadra & Nagar Haveli","DD":"Daman & Diu",
    "PY":"Puducherry","CH":"Chandigarh",
}

COURT_NAME_MAP = {
    "bishalgarh":("Sipahijala (Bishalgarh)","Tripura"),
    "agartala":("West Tripura","Tripura"),
    "namchi":("Namchi (South Sikkim)","Sikkim"),
    "gangtok":("East Sikkim","Sikkim"),
    "jorethang":("South Sikkim","Sikkim"),
    "kohima":("Kohima","Nagaland"),
    "imphal":("Imphal","Manipur"),
    "shillong":("East Khasi Hills","Meghalaya"),
    "aizawl":("Aizawl","Mizoram"),
    "itanagar":("Papum Pare","Arunachal Pradesh"),
    "guwahati":("Kamrup Metro","Assam"),
    "dibrugarh":("Dibrugarh","Assam"),
    "silchar":("Cachar","Assam"),
    "delhi":("New Delhi","Delhi"),
    "mumbai":("Mumbai","Maharashtra"),
    "pune":("Pune","Maharashtra"),
    "chennai":("Chennai","Tamil Nadu"),
    "hyderabad":("Hyderabad","Telangana"),
    "bengaluru":("Bengaluru","Karnataka"),
    "bangalore":("Bengaluru","Karnataka"),
    "kolkata":("Kolkata","West Bengal"),
    "patna":("Patna","Bihar"),
    "lucknow":("Lucknow","Uttar Pradesh"),
    "allahabad":("Prayagraj","Uttar Pradesh"),
    "jaipur":("Jaipur","Rajasthan"),
    "chandigarh":("Chandigarh","Punjab & Haryana"),
    "bhopal":("Bhopal","Madhya Pradesh"),
    "indore":("Indore","Madhya Pradesh"),
    "raipur":("Raipur","Chhattisgarh"),
    "ranchi":("Ranchi","Jharkhand"),
    "bhubaneswar":("Bhubaneswar","Odisha"),
    "cuttack":("Cuttack","Odisha"),
    "ahmedabad":("Ahmedabad","Gujarat"),
    "surat":("Surat","Gujarat"),
    "kochi":("Ernakulam","Kerala"),
    "thiruvananthapuram":("Thiruvananthapuram","Kerala"),
    "dehradun":("Dehradun","Uttarakhand"),
    "shimla":("Shimla","Himachal Pradesh"),
    "jammu":("Jammu","Jammu & Kashmir"),
    "srinagar":("Srinagar","Jammu & Kashmir"),
    "panaji":("North Goa","Goa"),
    "puducherry":("Puducherry","Puducherry"),
    "pondicherry":("Puducherry","Puducherry"),
}


def detect_location(court_name: str, cnr: str):
    # 1. CNR 4-char prefix → district + state
    if cnr and len(cnr) >= 4:
        key = cnr[:4].upper()
        if key in CNR_DISTRICT_MAP:
            return CNR_DISTRICT_MAP[key]
    # 2. Court name keyword
    lower = court_name.lower()
    for keyword, (district, state) in COURT_NAME_MAP.items():
        if keyword in lower:
            return district, state
    # 3. CNR 2-char state only
    if cnr and len(cnr) >= 2:
        state = CNR_STATE_MAP.get(cnr[:2].upper(), "Unknown")
        return "Unknown", state
    return "Unknown", "Unknown"


# ─────────────────────────────────────────────
# Acts & Sections extractor  ← FIXED
# ─────────────────────────────────────────────

def extract_acts_sections(raw_rows: list):
    """
    Scans raw table rows for the Acts table which looks like:
        Row: ['Under Act(s)', 'Under Section(s)']   ← header
        Row: ['Limitation Act', '5']                 ← data
        Row: ['IPC', '302, 307']                     ← more data (multiple acts)

    Col 0 = act name, Col 1 = section numbers.
    """
    act_names = []
    sections  = []
    in_acts   = False

    for row in raw_rows:
        # Normalise row cells (strip newlines)
        r = [clean(c) for c in row]

        # Detect the header row
        joined = " ".join(r).lower()
        if "under act" in joined and "under section" in joined:
            in_acts = True
            continue

        if not in_acts:
            continue

        # Stop when we hit another section
        if any(k in joined for k in [
            "fir details", "case history", "petitioner",
            "processes", "process id", "process title",
            "interim order", "police station",
            "field", "details", "order number", "judge"
        ]):
            break

        # Skip empty rows
        non_empty = [c for c in r if c]
        if not non_empty:
            continue

        # Col 0 = act name, Col 1 = section
        act_val = r[0] if len(r) > 0 else ""
        sec_val = r[1] if len(r) > 1 else ""

        if act_val and act_val.lower() not in ("under act(s)", ""):
            act_names.append(act_val)
        if sec_val and sec_val.lower() not in ("under section(s)", ""):
            sections.append(sec_val)

    act_name     = ", ".join(act_names) if act_names else None
    section_str  = ", ".join(sections)  if sections  else None
    num_sections = len(re.findall(r'\b\d+\b', section_str)) if section_str else 0

    return act_name, section_str, num_sections


# ─────────────────────────────────────────────
# Case History date extractor  ← FIXED
# ─────────────────────────────────────────────

def extract_history_dates(raw_rows: list):
    """
    Scans raw table rows for Case History tables.

    Standard 4-col layout:  ['Judge', 'Business on Date', 'Hearing Date', 'Purpose']
    Extended 6-col layout:  ['', 'Judge', 'Business on Date', 'Hearing Date', 'Purpose', '']
    (Some courts add empty border columns — seen in Manipur PDFs)

    Fix: detect col positions dynamically from the header row so both layouts work.
    """
    business_dates = []
    hearing_dates  = []
    in_history     = False
    biz_col        = 1    # default column positions
    hear_col       = 2

    for row in raw_rows:
        r = [clean(c) for c in row]
        joined_lower = " ".join(r).lower()

        # ── Detect history header row ─────────────────────────────────
        if ("business on" in joined_lower or "business" in joined_lower)                 and "hearing" in joined_lower                 and "judge" in joined_lower:
            in_history = True

            # Dynamically find which column contains "business" and "hearing"
            # so we handle both 4-col and 6-col table layouts
            biz_col  = next((i for i, c in enumerate(r) if "business" in c.lower()), 1)
            hear_col = next((i for i, c in enumerate(r) if "hearing" in c.lower()
                             and "purpose" not in c.lower()), biz_col + 1)
            continue

        if not in_history:
            continue

        # ── Stop at orders tables ─────────────────────────────────────
        if any(k in joined_lower for k in [
            "order number", "interim order", "final order",
            "about us", "order date", "order details"
        ]):
            in_history = False   # reset — may be another history table on next page
            continue

        # ── Skip empty rows ───────────────────────────────────────────
        if not any(c for c in r if c):
            continue

        # ── Extract dates using detected column positions ─────────────
        biz_raw  = cell(row[biz_col])  if len(row) > biz_col  else ""
        hear_raw = cell(row[hear_col]) if len(row) > hear_col else ""

        biz_date  = normalise_date(biz_raw)
        hear_date = normalise_date(hear_raw)

        if biz_date:
            business_dates.append(biz_date)
        if hear_date:
            hearing_dates.append(hear_date)

    return sorted(set(business_dates)), sorted(set(hearing_dates))


# ─────────────────────────────────────────────
# Orders extractor (Interim + Final)
# ─────────────────────────────────────────────

def extract_orders(raw_rows: list):
    """
    Extracts ONLY interim order dates as a flat list.
    Final orders completely removed.
    """

    interim_orders = []
    in_interim = False

    for row in raw_rows:
        r = [clean(c) for c in row]
        joined_lower = " ".join(r).lower()

        is_data_row = bool(re.match(r'^\d{1,3}$', r[0].strip())) if r else False

        # ── Detect interim section ─────────────────────────
        if (
            not is_data_row
            and "interim order" in joined_lower
            and "order number" not in joined_lower
        ):
            in_interim = True
            continue

        # ── Column header ─────────────────────────
        if not is_data_row and "order number" in joined_lower and "order date" in joined_lower:
            in_interim = True
            continue

        # Stop if another section starts
        if any(k in joined_lower for k in [
            "final order", "judgement",
            "case history", "fir details",
            "about us", "disclaimer"
        ]):
            in_interim = False
            continue

        if not in_interim:
            continue

        if not any(c for c in r if c):
            continue

        # ── Extract date ─────────────────────────
        order_date = normalise_date(cell(row[1])) if len(row) > 1 else None

        if order_date:
            interim_orders.append(order_date)

    return interim_orders if interim_orders else None

# ─────────────────────────────────────────────
# Hearing purpose extractor
# ─────────────────────────────────────────────

def extract_hearing_purposes(raw_rows: list) -> list:
    """
    Scans Case History rows and builds a list of
    "DD-MM-YYYY: <Purpose of Hearing>" strings, keyed on Business on Date.

    Example output:
        ["09-02-2026: P W S", "17-12-2025: hearing", "10-09-2025: Order", ...]

    Handles both the standard 4-col layout and extended 6-col layout.
    Also captures single-cell "Disposed" rows that appear in some PDFs.
    """
    purposes:   list = []
    in_history: bool = False
    biz_col:    int  = 1
    purp_col:   int  = 3

    for row in raw_rows:
        r            = [clean(c) for c in row]
        joined_lower = " ".join(r).lower()

        # ── Detect history header ─────────────────────────────────────
        if (
            ("business on" in joined_lower or "business" in joined_lower)
            and "hearing" in joined_lower
            and "judge" in joined_lower
        ):
            in_history = True
            biz_col  = next((i for i, c in enumerate(r)
                             if "business" in c.lower()), 1)
            purp_col = next((i for i, c in enumerate(r)
                             if "purpose" in c.lower()), biz_col + 2)
            continue

        if not in_history:
            continue

        # ── Stop at orders section ────────────────────────────────────
        if any(k in joined_lower for k in [
            "order number", "interim order", "final order",
            "about us", "order date", "order details",
        ]):
            in_history = False
            continue

        # Skip blank rows
        if not any(c for c in r if c):
            continue

        # ── Extract business date + purpose ───────────────────────────
        biz_raw  = cell(row[biz_col]) if len(row) > biz_col else ""
        purp_raw = r[purp_col]        if len(r)   > purp_col else ""
        biz_date = normalise_date(biz_raw)

        if biz_date:
            if purp_raw:
                purposes.append(f"{biz_date}: {purp_raw}")
            else:
                # Some PDFs show a lone "Disposed" cell in a merged row
                for cell_val in r:
                    if cell_val and cell_val.lower() in (
                            "disposed", "disposal", "decided"):
                        purposes.append(f"{biz_date}: {cell_val}")
                        break

    return purposes


# ─────────────────────────────────────────────
# Core parser
# ─────────────────────────────────────────────

def parse_case(text: str, raw_rows: list) -> dict:

    # ── Basic identifiers — scan table rows directly ──────────────────
    cnr        = None
    case_type  = None
    filing_num = None
    reg_num    = None
    filing_date_raw   = None
    reg_date_raw      = None

    for row in raw_rows:
        r = [clean(c) for c in row]
        joined = " ".join(r)

        # Case Type row: ['Case Type', 'CRL M CONDO', ...]
        if not case_type and r and r[0].lower() == "case type" and len(r) > 1:
            case_type = r[1]

        # Filing Number row: ['Filing\nNumber', '21/2026', 'Filing Date', '06-03-\n2026']
        if r and "filing" in r[0].lower() and "number" in r[0].lower():
            if not filing_num and len(r) > 1 and r[1]:
                filing_num = r[1]
            if not filing_date_raw and len(r) > 3 and r[3]:
                filing_date_raw = cell(row[3])   # strip \n from date

        # Registration Number row
        if r and "registration" in r[0].lower() and "number" in r[0].lower():
            if not reg_num and len(r) > 1 and r[1]:
                reg_num = r[1]
            if not reg_date_raw and len(r) > 3 and r[3]:
                reg_date_raw = cell(row[3])

        # CNR Number row: ['CNR\nNumber', 'TRSE050000512026\n(Note...)', ...]
        if not cnr and r and "cnr" in r[0].lower():
            if len(r) > 1 and r[1]:
                m = re.search(r'([A-Z]{2,4}\d{8,16})', r[1])
                if m:
                    cnr = m.group(1)

    # Fallback to regex on text if still missing
    if not cnr:
        cnr = find_field(r'([A-Z]{2,4}\d{8,16})', text)
    if not case_type:
        case_type = find_field(r'Case\s*Type\s*[:\-]?\s*(.+?)(?:\n|Filing)', text)
    if not filing_num:
        filing_num = find_field(r'Filing\s*Number\s*[:\-]?\s*([\d/]+)', text)
    if not reg_num:
        reg_num = find_field(r'Registration\s*Number\s*[:\-]?\s*([\d/]+)', text)

    filing_date = normalise_date(filing_date_raw)
    reg_date    = normalise_date(reg_date_raw)

    # ── Court name ────────────────────────────────────────────────────
    court_name = find_field(
        r'(?:Establishment\s+of\s+)?'
        r'((?:Addl\.?\s+)?(?:District\s+and\s+Sessions\s+Judge|'
        r'Sessions\s+Judge|Chief\s+Judicial\s+Magistrate|'
        r'Judicial\s+Magistrate|High\s+Court|Metropolitan\s+Magistrate|'
        r'Civil\s+Judge)[^\n]{0,80})',
        text
    )
    if not court_name:
        for line in text.splitlines():
            line = clean(line)
            if len(line) > 15 and not line.lower().startswith(
                    ("back", "download", "case", "cnr", "filing", "about",
                     "registration", "under", "court number")):
                court_name = line
                break
    court_name  = court_name or "Unknown"
    court_level = detect_court_level(court_name)
    district, state = detect_location(court_name, cnr or "")

    # ── Acts & Sections ───────────────────────────────────────────────
    act_name, section_str, num_sections = extract_acts_sections(raw_rows)

    # ── Status dates — scan Case Status table ─────────────────────────
    first_hearing  = None
    decision_date  = None
    next_hearing   = None

    for row in raw_rows:
        r = [clean(c) for c in row]
        if not r:
            continue

        # Check both (col0→col1) and (col2→col3) label/value pairs
        pairs = []
        if len(r) >= 2:
            pairs.append((r[0].lower(), r[1]))
        if len(r) >= 4:
            pairs.append((r[2].lower(), r[3]))

        for label, value in pairs:
            if not value:
                continue
            d = normalise_date(value)
            if not d:
                continue

            if not first_hearing and any(k in label for k in [
                    "first hearing", "first date"]):
                first_hearing = d

            if not decision_date and any(k in label for k in [
                    "decision", "decided", "disposal date", "date of decision"]):
                decision_date = d

            if not next_hearing and any(k in label for k in [
                    "next hearing", "next date", "next date of hearing",
                    "adjourned", "next"]):
                next_hearing = d

    # ── Regex fallback on full text if table scan missed anything ─────
    DATE_FMTS = (
        r'[\d]{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4}'
        r'|[\d]{1,2}[-/][\d]{1,2}[-/][\d]{4}'
    )
    if not first_hearing:
        first_hearing = normalise_date(
            find_field(rf'First\s*Hearing\s*Date\s*[:\-]?\s*({DATE_FMTS})', text))

    if not decision_date:
        decision_date = normalise_date(
            find_field(rf'Decision\s*Date\s*[:\-]?\s*({DATE_FMTS})', text))

    if not next_hearing:
        next_hearing = normalise_date(
            find_field(
                rf'Next\s*(?:Hearing\s*)?(?:Date\s*(?:of\s*Hearing)?)?\s*[:\-]?\s*({DATE_FMTS})',
                text))

    if not next_hearing:
        next_hearing = normalise_date(
            find_field(
                rf'(?:Next\s+Date|Adjourned\s+(?:to|on))\s*[:\-]?\s*({DATE_FMTS})',
                text))

    # ── Hearing & Business dates from Case History ────────────────────
    business_dates, hearing_dates = extract_history_dates(raw_rows)

    # ── Status ────────────────────────────────────────────────────────
    disposed_keywords = ["disposed", "acquitted", "convicted", "dismissed",
                         "allowed", "withdrawn", "settled", "decided"]
    is_disposed = int(any(k in text.lower() for k in disposed_keywords))
    is_pending  = 1 - is_disposed

    if is_disposed:
        next_hearing  = None
    if is_pending:
        decision_date = None

    # ── Orders (interim always; final only for disposed cases) ────────
    interim_orders = extract_orders(raw_rows)
    # ── Purpose of hearing keyed on business date ─────────────────────
    hearing_purposes = extract_hearing_purposes(raw_rows)

    return {
        "cnr_number":          cnr,
        "case_type":           case_type,
        "filing_number":       filing_num,
        "registration_number": reg_num,
        "court_name":          court_name,
        "court_level":         court_level,
        # "district":            district,
        "state":               state,
        "act_name":            act_name,
        "section":             section_str,
        "number_of_sections":  num_sections,
        "filing_date":         filing_date,
        "hearing_dates":       hearing_dates,
        "business_dates":      business_dates,
        "registration_date":   reg_date,
        "first_hearing_date":  first_hearing,
        "decision_date":       decision_date,
        "next_hearing_date":   next_hearing,
        "is_pending":          is_pending,
        "is_disposed":         is_disposed,
        "interim_orders":      interim_orders,
        "hearing_purposes":    hearing_purposes,
    }


# ─────────────────────────────────────────────
# Main runner
# ─────────────────────────────────────────────

def process_folder(input_dir: str, output_file: str):
    pdf_files = sorted(Path(input_dir).glob("*.pdf"))
    if not pdf_files:
        print(f"[!] No PDF files found in: {input_dir}")
        return

    results = []
    for pdf_path in pdf_files:
        print(f"[+] Processing: {pdf_path.name}")
        try:
            text     = extract_text_from_pdf(str(pdf_path))
            raw_rows = extract_tables_raw(str(pdf_path))
            data     = parse_case(text, raw_rows)
            data["source_file"] = pdf_path.name
            results.append(data)
            print(f"    [OK] CNR: {data.get('cnr_number')} | "
                  f"District: {data.get('district')} | "
                  f"Act: {data.get('act_name')} | "
                  f"Status: {'Pending' if data['is_pending'] else 'Disposed'}")
        except Exception as e:
            import traceback
            print(f"    [ERROR] {pdf_path.name}: {e}")
            traceback.print_exc()
            results.append({"source_file": pdf_path.name, "error": str(e)})

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Done. {len(results)} case(s) written to: {output_file}")


# ─────────────────────────────────────────────
# Entry point — works in Jupyter AND Terminal
# ─────────────────────────────────────────────

if __name__ == "__main__":

    if any("ipykernel" in arg or "jupyter" in arg for arg in sys.argv):
        # ✏️ Change these two paths to match your setup
        input_dir   = "./pdfs"               # ← folder with your PDFs
        output_file = "cases_metadata.json"  # ← output file
        process_folder(input_dir, output_file)

    else:
        parser = argparse.ArgumentParser(
            description="Extract court case metadata from eCourts PDFs"
        )
        parser.add_argument("--input",  "-i", default="./pdfs",
                            help="Folder containing PDF files (default: ./pdfs)")
        parser.add_argument("--output", "-o", default="cases_metadata.json",
                            help="Output JSON file (default: cases_metadata.json)")
        args = parser.parse_args()
        process_folder(args.input, args.output)