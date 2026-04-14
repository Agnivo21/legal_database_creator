import streamlit as st
import pytesseract
from pdf2image import convert_from_bytes
import cv2
import numpy as np
import re
import json
import tempfile
import hashlib
from pathlib import Path

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# IMPORT YOUR METADATA SCRIPT
from extract_case_metadata import (
    extract_text_from_pdf,
    extract_tables_raw,
    parse_case,
)

# ==========================
# 🎨 PAGE CONFIG
# ==========================
st.set_page_config(
    page_title="Court Case AI System",
    page_icon="⚖️",
    layout="wide",
)

# ==========================
# 💅 CUSTOM CSS
# ==========================
st.markdown(
    """
<style>
.main {
    background-color: #0f172a;
}
h1, h2, h3 {
    color: #38bdf8;
}
.card {
    background-color: #1e293b;
    padding: 20px;
    border-radius: 15px;
    margin-bottom: 20px;
}
.upload-box {
    border: 2px dashed #38bdf8;
    padding: 30px;
    border-radius: 15px;
    text-align: center;
    color: #94a3b8;
}
.json-box {
    background-color: #020617;
    padding: 20px;
    border-radius: 10px;
    color: #f87171;
    font-family: monospace;
}
</style>
""",
    unsafe_allow_html=True,
)

# ==========================
# 📋 SHEET SCHEMA
# ==========================
# These headers match the JSON keys in final_case.json.
# List values are converted to comma-separated strings before upload.
HEADERS = [
    "cnr_number",
    "case_type",
    "filing_number",
    "registration_number",
    "court_name",
    "court_level",
    # "district",
    "state",
    "act_name",
    "section",
    "number_of_sections",
    "filing_date",
    "hearing_dates",
    "business_dates",
    "registration_date",
    "first_hearing_date",
    "decision_date",
    "next_hearing_date",
    "is_pending",
    "is_disposed",
    "interim_orders",
    "hearing_purposes",
    "full_text",
]

# ==========================
# 🧹 CLEAN TEXT
# ==========================
def clean_text(text: str) -> str:
    lines = text.split("\n")
    return "\n".join([re.sub(r"\s+", " ", l.strip()) for l in lines])


# ==========================
# 📄 OCR FUNCTION
# ==========================
def ocr_pdf(file_bytes, lang, psm_mode):
    images = convert_from_bytes(file_bytes, dpi=300)
    full_text = ""

    for img in images:
        img = np.array(img)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        config = f"--oem 3 --psm {psm_mode}"
        text = pytesseract.image_to_string(thresh, lang=lang, config=config)
        full_text += text + "\n"

    return clean_text(full_text)


# ==========================
# 🧾 GOOGLE SHEETS HELPERS
# ==========================
@st.cache_resource
def get_sheet():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]

    creds_path = Path("credentials.json")
    if not creds_path.exists():
        raise FileNotFoundError(
            "credentials.json not found. Place your Google service account JSON in the app folder."
        )

    creds = ServiceAccountCredentials.from_json_keyfile_name(str(creds_path), scope)
    client = gspread.authorize(creds)

    spreadsheet = client.open_by_url(
        "https://docs.google.com/spreadsheets/d/17n58eSjdraBOVfhs2b2NGI0haxebjqVcoF7vKGw5DEQ/edit?usp=sharing"
    )

    try:
        sheet = spreadsheet.worksheet("Sheet1")
    except gspread.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(title="Sheet1", rows=1000, cols=len(HEADERS))

    return sheet


def ensure_headers(sheet):
    first_row = sheet.row_values(1)
    if first_row != HEADERS:
        sheet.update("1:1", [HEADERS])


def serialize_value(value):
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(v) for v in value)
    return str(value)


def build_row_from_json(data: dict):
    return [serialize_value(data.get(header)) for header in HEADERS]


def upload_json_to_sheet(data: dict):
    sheet = get_sheet()
    ensure_headers(sheet)
    row = build_row_from_json(data)
    sheet.append_row(row, value_input_option="USER_ENTERED")


# ==========================
# SIDEBAR
# ==========================
st.sidebar.title("⚙️ OCR Settings")

language = st.sidebar.selectbox("Language", ["eng", "eng+hin"])
psm_mode = st.sidebar.selectbox("PSM Mode", [3, 4, 6])

# ==========================
# HEADER
# ==========================
st.markdown(
    """
<div class="card">
<h1>⚖️ Court Case Processing System</h1>
<p>OCR + Metadata Extraction + JSON Merge + Google Sheets Upload</p>
</div>
""",
    unsafe_allow_html=True,
)

# ==========================================================
# 🔹 SECTION 1 — OCR PDF
# ==========================================================
st.markdown('<div class="card">', unsafe_allow_html=True)

st.subheader("📄 Upload Interim Order PDFs")

ocr_files = st.file_uploader(
    "Upload PDF(s)",
    type=["pdf"],
    accept_multiple_files=True,
)

ocr_text_output = ""
ocr_file_names = []

if ocr_files:
    # Maintain serial order like interimorder1, 2, 3...
    def natural_sort_key(s):
        return [int(text) if text.isdigit() else text.lower() for text in re.split(r"(\d+)", s)]

    ocr_files = sorted(ocr_files, key=lambda x: natural_sort_key(x.name))
    ocr_file_names = [file.name for file in ocr_files]

    for file in ocr_files:
        st.info(f"Processing {file.name}")
        text = ocr_pdf(file.read(), language, psm_mode)
        ocr_text_output += f"\n--- {file.name} ---\n{text}\n"

    st.success("OCR Completed")
    st.text_area("Preview OCR Output", ocr_text_output, height=250)

st.markdown("</div>", unsafe_allow_html=True)

# ==========================================================
# 🔹 SECTION 2 — METADATA EXTRACTION
# ==========================================================
st.markdown('<div class="card">', unsafe_allow_html=True)

st.subheader("📊 Upload Metadata PDF")

metadata_file = st.file_uploader(
    "Upload metadata PDF",
    type=["pdf"],
    key="metadata",
)

metadata_json = None

if metadata_file:
    st.info("Extracting metadata...")

    # Save temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(metadata_file.read())
        temp_path = tmp.name

    try:
        text = extract_text_from_pdf(temp_path)
        raw = extract_tables_raw(temp_path)
        metadata_json = parse_case(text, raw)
        st.success("Metadata Extracted")
        st.markdown("### JSON Output")
        st.json(metadata_json)
    finally:
        try:
            Path(temp_path).unlink(missing_ok=True)
        except Exception:
            pass

st.markdown("</div>", unsafe_allow_html=True)

# ==========================================================
# 🔹 SECTION 3 — AUTO MERGE JSON + AUTO SAVE TO SHEET
# ==========================================================
st.markdown('<div class="card">', unsafe_allow_html=True)

st.subheader("🔗 Merged Output (Auto)")

if metadata_json and ocr_text_output:
    final_json = metadata_json.copy()
    final_json["full_text"] = ocr_text_output

    # Optional traceability field; keep it only if you want it in the sheet.
    # If you do not want an extra column, comment the next line out.
    # final_json["source_file"] = ", ".join(ocr_file_names)

    st.success("✅ Automatically Merged")
    st.markdown("### Final JSON")
    st.json(final_json)

    # Manual save button instead of auto upload
    if st.button("💾 Save to Google Sheet"):
        try:
            upload_json_to_sheet(final_json)
            st.success("✅ Merged JSON saved to Google Sheet")
        except Exception as e:
            st.error(f"❌ Google Sheet upload failed: {e}")

    st.download_button(
        "📥 Download JSON",
        data=json.dumps(final_json, indent=2, ensure_ascii=False),
        file_name="final_case.json",
        mime="application/json",
    )
else:
    st.info("📌 Upload both OCR PDF(s) and Metadata PDF to auto-merge")

st.markdown("</div>", unsafe_allow_html=True)