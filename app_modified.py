import streamlit as st
import pytesseract
from pdf2image import convert_from_bytes
import cv2
import numpy as np
import re
import json
import tempfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import gspread
from oauth2client.service_account import ServiceAccountCredentials

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
.main { background-color: #0f172a; }
h1, h2, h3 { color: #38bdf8; }
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
</style>
""",
    unsafe_allow_html=True,
)

# ==========================
# 📋 SHEET SCHEMA
# ==========================
HEADERS = [
    "cnr_number", "case_type", "filing_number", "registration_number",
    "court_name", "court_level", "state", "act_name", "section",
    "number_of_sections", "filing_date", "hearing_dates", "business_dates",
    "registration_date", "first_hearing_date", "decision_date",
    "next_hearing_date", "is_pending", "is_disposed", "interim_orders",
    "hearing_purposes", "full_text",
]

# ==========================
# 🔑 CREDENTIALS RESOLVER
# ==========================
def _find_credentials_path() -> Path:
    render_path = Path("/etc/secrets/credentials.json")
    local_path  = Path("credentials.json")
    if render_path.exists():
        return render_path
    if local_path.exists():
        return local_path
    raise FileNotFoundError(
        "credentials.json not found.\n"
        "• On Render: add it as a Secret File named credentials.json\n"
        "• Locally: place credentials.json in the project root folder"
    )


# ==========================
# 🧹 HELPERS
# ==========================
def clean_text(text: str) -> str:
    lines = text.split("\n")
    return "\n".join([re.sub(r"\s+", " ", l.strip()) for l in lines])


# ─────────────────────────────────────────────
# ⚡ FAST OCR — three optimisations applied:
#
#  1. ThreadPoolExecutor parallelises pages.
#     Tesseract releases the GIL during its C call,
#     so threads give real speedup (2–4x on multi-page PDFs).
#
#  2. Results stored by index so page order is preserved
#     even though futures complete out-of-order.
#
#  3. DPI fixed at 150 — quarter the memory of 300 DPI,
#     still sufficient for printed court documents.
# ─────────────────────────────────────────────

def _ocr_single_page(args):
    """OCR one PIL image page. Runs inside a thread."""
    img_pil, lang, psm_mode = args
    img_np = np.array(img_pil)
    gray   = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    config = f"--oem 3 --psm {psm_mode}"
    return pytesseract.image_to_string(thresh, lang=lang, config=config)


def ocr_pdf_fast(file_bytes: bytes, lang: str, psm_mode: int,
                 max_workers: int = 4) -> str:
    """
    Parallel page OCR.
    max_workers=4 is safe for Render free tier (1 vCPU, shared).
    Raise to 8 on paid instances.
    """
    images     = convert_from_bytes(file_bytes, dpi=150)
    args_list  = [(img, lang, psm_mode) for img in images]
    page_texts = [""] * len(images)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {
            executor.submit(_ocr_single_page, args): idx
            for idx, args in enumerate(args_list)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                page_texts[idx] = future.result()
            except Exception as e:
                page_texts[idx] = f"[OCR error on page {idx+1}: {e}]"

    return clean_text("\n".join(page_texts))


# ==========================
# 🧾 GOOGLE SHEETS HELPERS
# ==========================
@st.cache_resource
def get_sheet():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds_path = _find_credentials_path()
    creds      = ServiceAccountCredentials.from_json_keyfile_name(str(creds_path), scope)
    client     = gspread.authorize(creds)
    spreadsheet = client.open_by_url(
        "https://docs.google.com/spreadsheets/d/17n58eSjdraBOVfhs2b2NGI0haxebjqVcoF7vKGw5DEQ/edit?usp=sharing"
    )
    try:
        sheet = spreadsheet.worksheet("Sheet1")
    except gspread.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(title="Sheet1", rows=1000, cols=len(HEADERS))
    return sheet


def ensure_headers(sheet):
    if sheet.row_values(1) != HEADERS:
        sheet.update("1:1", [HEADERS])


def serialize_value(value):
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(v) for v in value)
    return str(value)


def build_row_from_json(data: dict):
    return [serialize_value(data.get(h)) for h in HEADERS]


def upload_json_to_sheet(data: dict):
    sheet = get_sheet()
    ensure_headers(sheet)
    sheet.append_row(build_row_from_json(data), value_input_option="USER_ENTERED")


# ==========================
# SIDEBAR
# ==========================
st.sidebar.title("⚙️ OCR Settings")
language    = st.sidebar.selectbox("Language", ["eng", "eng+hin"])
psm_mode    = st.sidebar.selectbox("PSM Mode", [3, 4, 6])
max_workers = st.sidebar.slider(
    "Parallel OCR workers",
    min_value=1, max_value=8, value=4,
    help="Higher = faster on multi-page PDFs. Keep ≤4 on Render free tier."
)

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

ocr_files = st.file_uploader("Upload PDF(s)", type=["pdf"], accept_multiple_files=True)
ocr_text_output = ""
ocr_file_names  = []

if ocr_files:
    def natural_sort_key(s):
        return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]

    ocr_files      = sorted(ocr_files, key=lambda x: natural_sort_key(x.name))
    ocr_file_names = [f.name for f in ocr_files]

    progress = st.progress(0, text="Starting OCR...")

    for i, file in enumerate(ocr_files):
        progress.progress(
            int((i / len(ocr_files)) * 100),
            text=f"OCR: {file.name}  ({i+1}/{len(ocr_files)})"
        )
        text = ocr_pdf_fast(file.read(), language, psm_mode, max_workers)
        ocr_text_output += f"\n--- {file.name} ---\n{text}\n"

    progress.progress(100, text="✅ OCR complete")
    st.success(f"OCR completed for {len(ocr_files)} file(s)")
    st.text_area("Preview OCR Output", ocr_text_output, height=250)

st.markdown("</div>", unsafe_allow_html=True)

# ==========================================================
# 🔹 SECTION 2 — METADATA EXTRACTION
# ==========================================================
st.markdown('<div class="card">', unsafe_allow_html=True)
st.subheader("📊 Upload Metadata PDF")

metadata_file = st.file_uploader("Upload metadata PDF", type=["pdf"], key="metadata")
metadata_json = None

if metadata_file:
    with st.spinner("Extracting metadata..."):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(metadata_file.read())
            temp_path = tmp.name
        try:
            text          = extract_text_from_pdf(temp_path)
            raw           = extract_tables_raw(temp_path)
            metadata_json = parse_case(text, raw)
            st.success("Metadata Extracted")
            st.markdown("### JSON Output")
            st.json(metadata_json)
        finally:
            Path(temp_path).unlink(missing_ok=True)

st.markdown("</div>", unsafe_allow_html=True)

# ==========================================================
# 🔹 SECTION 3 — MERGE + SAVE
# ==========================================================
st.markdown('<div class="card">', unsafe_allow_html=True)
st.subheader("🔗 Merged Output (Auto)")

if metadata_json and ocr_text_output:
    final_json = metadata_json.copy()
    final_json["full_text"] = ocr_text_output

    st.success("✅ Automatically Merged")
    st.markdown("### Final JSON")
    st.json(final_json)

    if st.button("💾 Save to Google Sheet"):
        with st.spinner("Uploading to Google Sheets..."):
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