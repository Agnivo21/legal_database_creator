import streamlit as st
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# -------------------------
# Google Sheets Connection
# -------------------------
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]

creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
client = gspread.authorize(creds)

# Open using your link
spreadsheet = client.open_by_url(
    "https://docs.google.com/spreadsheets/d/17n58eSjdraBOVfhs2b2NGI0haxebjqVcoF7vKGw5DEQ/edit?usp=sharing"
)

# Select sheet/tab
sheet = spreadsheet.worksheet("Sheet1")

# -------------------------
# Define Headers
# -------------------------
HEADERS = [
    "cnr_number",
    "case_type",
    "filing_number",
    "registration_number",
    "court_name",
    "court_level",
    "district",
    "state",
    "act_name",
    "section",
    "number_of_sections",
    "filing_date",
    "registration_date",
    "first_hearing_date",
    "decision_date",
    "next_hearing_date",
    "is_pending",
    "is_disposed",
    "source_file",
    "hearing_dates",
    "business_dates"
]


# -------------------------
# Ensure Headers Exist
# -------------------------
def ensure_headers():
    first_row = sheet.row_values(1)

    if not first_row:
        sheet.append_row(HEADERS)
        st.info("🆕 Headers created automatically!")


# -------------------------
# Streamlit UI
# -------------------------
st.title("📄 Case JSON Uploader")

json_input = st.text_area("Paste your JSON here")


# -------------------------
# Button Logic
# -------------------------
if st.button("Save to Google Sheet"):

    try:
        data = json.loads(json_input)

        # Ensure headers exist
        ensure_headers()

        # Convert lists to string
        hearing_dates = ",".join(data.get("hearing_dates", []))
        business_dates = ",".join(data.get("business_dates", []))

        # Create row (order must match headers)
        row = [
        data.get("cnr_number"),
        data.get("case_type"),
        data.get("filing_number"),
        data.get("registration_number"),
        data.get("court_name"),
        data.get("court_level"),
        data.get("district"),
        data.get("state"),
        data.get("act_name"),
        data.get("section"),
        data.get("number_of_sections"),
        data.get("filing_date"),
        data.get("registration_date"),
        data.get("first_hearing_date"),
        data.get("decision_date"),
        data.get("next_hearing_date"),
        data.get("is_pending"),
        data.get("is_disposed"),
        data.get("source_file"),
        ",".join(data.get("hearing_dates", [])),
        ",".join(data.get("business_dates", []))
        ]

        # Insert into sheet
        sheet.append_row(row)

        st.success("✅ Data saved successfully!")

    except Exception as e:
        st.error(f"❌ Error: {str(e)}")