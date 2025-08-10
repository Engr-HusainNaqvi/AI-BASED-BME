# app.py
import streamlit as st
import pandas as pd
import PyPDF2
from datetime import date, timedelta
import io

# --------------------- Title ---------------------
st.set_page_config(page_title="Biomedical Department AI Assistant", layout="wide")
st.title("🏥 Biomedical Department AI Assistant (Free Version)")
st.write("Upload your equipment inventory & SOP files to get started.")

# --------------------- File Upload ---------------------
uploaded_inventory = st.file_uploader("📤 Upload Equipment Inventory (CSV or Excel)", type=["csv", "xlsx"])
uploaded_sop = st.file_uploader("📤 Upload SOP File (PDF)", type=["pdf"])

df = None
if uploaded_inventory is not None:
    try:
        if uploaded_inventory.name.endswith(".csv"):
            df = pd.read_csv(uploaded_inventory)
        else:
            df = pd.read_excel(uploaded_inventory, engine="openpyxl")
        st.success("✅ Inventory loaded successfully.")
        st.dataframe(df)
    except Exception as e:
        st.error(f"❌ Failed to read inventory: {e}")

# --------------------- Faulty & No-Status Detection ---------------------
faulty, no_status = pd.DataFrame(), pd.DataFrame()
if df is not None:
    if "status" in df.columns:
        faulty = df[df["status"].str.contains("fault", case=False, na=False)]
        no_status = df[df["status"].isna() | (df["status"].str.strip() == "")]
    else:
        st.warning("⚠️ No 'status' column found in inventory.")

# --------------------- SOP Text Extractor ---------------------
sop_text = ""
if uploaded_sop is not None:
    try:
        sop_reader = PyPDF2.PdfReader(uploaded_sop)
        for page in sop_reader.pages:
            sop_text += page.extract_text() + "\n"
        st.success("✅ SOP loaded successfully.")
    except Exception as e:
        st.error(f"❌ Failed to read SOP: {e}")

# --------------------- Question Answering ---------------------
st.header("💬 Ask a Question")
query = st.text_input("Example: 'How many ECG machines are faulty?' or 'SOP for defibrillator'")

if st.button("Get Answer"):
    if not query.strip():
        st.warning("Please enter a valid question.")
    else:
        query_lower = query.lower()
        handled = False

        if df is not None:
            # "How many" questions
            if "how many" in query_lower:
                keywords = query_lower.replace("how many", "").strip().split()
                keywords = [k for k in keywords if k not in ["are", "is", "the", "of", "in", "faulty", "working", "functional"]]
                keyword_phrase = " ".join(keywords)

                if keyword_phrase:
                    matches = df[df["name_of_equipment"].str.lower().str.contains(keyword_phrase, na=False)]
                    if "faulty" in query_lower:
                        matches = matches[matches["status"].str.contains("fault", na=False)]
                        st.success(f"🔍 Found {len(matches)} {keyword_phrase} (faulty)")
                    else:
                        st.success(f"🔍 Found {len(matches)} {keyword_phrase} (total)")
                    if not matches.empty:
                        st.dataframe(matches)
                    handled = True

            # List faulty
            elif "list" in query_lower and "fault" in query_lower:
                st.success("📋 Listing all faulty equipment:")
                st.dataframe(faulty)
                handled = True

            # List no status
            elif "no status" in query_lower or "not written" in query_lower:
                st.success("📋 Equipment with no written status:")
                st.dataframe(no_status)
                handled = True

        # SOP search
        if not handled and sop_text:
            found_lines = [line for line in sop_text.split("\n") if query_lower in line.lower()]
            if found_lines:
                st.success("📄 Found in SOP:")
                for line in found_lines:
                    st.write(line)
                handled = True

        if not handled:
            st.info("No local answer found. Try a different phrasing or ensure inventory/SOP contains the data.")

# --------------------- PPM Plan Generator ---------------------
st.subheader("🗓️ Generate PPM Plan")
interval_months = st.number_input("Maintenance interval (months)", min_value=1, max_value=24, value=6)
start_date = date.today()

ppm_plan = []
if df is not None:
    for _, row in df.iterrows():
        schedule_dates = [start_date + timedelta(days=30 * i) for i in range(0, 13, interval_months)]
        for sched in schedule_dates:
            row_data = row.to_dict()
            row_data["Scheduled Date"] = sched.strftime("%Y-%m-%d")
            ppm_plan.append(row_data)

    ppm_df = pd.DataFrame(ppm_plan)
    st.dataframe(ppm_df)

    csv_ppm = ppm_df.to_csv(index=False).encode("utf-8")
    st.download_button("📥 Download PPM Plan (CSV)", csv_ppm, "ppm_plan.csv", "text/csv")
