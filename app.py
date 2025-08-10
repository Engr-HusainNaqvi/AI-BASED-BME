# app.py
import streamlit as st
import pandas as pd
import PyPDF2
from datetime import date, timedelta

# Page config
st.set_page_config(page_title="BioMaint-AI (Free)", layout="wide")
st.title("🩺 BioMaint-AI: Free Biomedical Assistant")

st.markdown("""
Upload your inventory and SOPs to:
- Detect faulty and missing-status equipment
- Auto-generate PPM (Planned Preventive Maintenance) schedules
- Search SOP content
- Chatbot for basic inventory Q&A
""")

# --------------------- File Upload ---------------------
st.header("📁 Upload Inventory (.csv or .xlsx)")
uploaded_file = st.file_uploader("Upload your inventory file", type=["csv", "xlsx"])
df = None
faulty = pd.DataFrame()
no_status = pd.DataFrame()

if uploaded_file:
    try:
        if uploaded_file.name.endswith(".csv"):
            df = pd.read_csv(uploaded_file)
        else:
            try:
                import openpyxl
                df = pd.read_excel(uploaded_file, engine="openpyxl")
            except ImportError:
                st.error("❌ Please install `openpyxl` or upload CSV instead.")
                df = None

        if df is not None:
            # Standardize columns
            df.columns = [str(col).strip().lower().replace(" ", "_") for col in df.columns]
            df = df.dropna(how="all")

            column_map = {
                "equipment_name": "name_of_equipment",
                "device": "name_of_equipment",
                "device_name": "name_of_equipment",
                "machine": "name_of_equipment",
                "equipment": "name_of_equipment",
                "status_description": "status",
                "functional_status": "status",
                "dept": "department"
            }
            for old, new in column_map.items():
                if old in df.columns:
                    df.rename(columns={old: new}, inplace=True)

            for col in ["name_of_equipment", "status", "department"]:
                if col not in df.columns:
                    df[col] = "No data"
                df[col] = df[col].astype(str).fillna("No status written").replace(r"^\s*$", "No status written", regex=True)

            df["status"] = df["status"].str.lower().replace({
                "working": "functional",
                "ok": "functional",
                "non-functional": "faulty",
                "out of order": "faulty",
                "needs repair": "faulty",
                "damaged": "faulty",
                "faulty battery": "faulty",
                "no": "no status written"
            })

            faulty = df[df["status"].str.contains("faulty|error|repair|non functional", na=False)]
            no_status = df[df["status"].str.contains("no status", na=False)]

    except Exception as e:
        st.error(f"❌ Failed to read inventory: {e}")

# --------------------- Dashboard ---------------------
if df is not None:
    st.subheader("📊 Equipment Dashboard")

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Equipment", len(df))
    col2.metric("Faulty Equipment", len(faulty))
    col3.metric("No Status Written", len(no_status))

    with st.expander("📋 View Full Inventory"):
        st.dataframe(df)

    if "department" in df.columns:
        st.subheader("🏥 Equipment by Department")
        st.bar_chart(df["department"].value_counts())

    if not faulty.empty:
        st.subheader("⚠️ Faulty Equipment")
        st.dataframe(faulty)

    if not no_status.empty:
        st.subheader("❓ Equipment with No Status")
        st.dataframe(no_status)

# --------------------- PPM Plan Generator ---------------------
    st.subheader("🗓️ Generate PPM Plan")
    interval_months = st.number_input("Maintenance interval (months)", min_value=1, max_value=24, value=6)
    start_date = date.today()

    ppm_plan = []
    for _, row in df.iterrows():
        equip_name = row["name_of_equipment"]
        dept = row["department"]
        schedule_dates = [start_date + timedelta(days=30 * i) for i in range(0, 13, interval_months)]
        for sched in schedule_dates:
            ppm_plan.append({
                "Equipment": equip_name,
                "Department": dept,
                "Scheduled Date": sched.strftime("%Y-%m-%d")
            })

    ppm_df = pd.DataFrame(ppm_plan)
    st.dataframe(ppm_df)

    csv_ppm = ppm_df.to_csv(index=False).encode("utf-8")
    st.download_button("📥 Download PPM Plan (CSV)", csv_ppm, "ppm_plan.csv", "text/csv")

# --------------------- PDF Upload ---------------------
st.header("📄 Upload SOP / Maintenance Manual (PDF)")
uploaded_pdf = st.file_uploader("Upload `.pdf` file", type=["pdf"])
pdf_text = ""

if uploaded_pdf:
    try:
        reader = PyPDF2.PdfReader(uploaded_pdf)
        pdf_text = "\n".join([page.extract_text() for page in reader.pages if page.extract_text()])
        st.success("✅ PDF content extracted.")
        with st.expander("📖 Preview SOP Text"):
            st.write(pdf_text[:2000] + "..." if len(pdf_text) > 2000 else pdf_text)
    except Exception as e:
        st.error(f"❌ Failed to extract PDF: {e}")

# --------------------- Simple Chatbot ---------------------
st.header("💬 Ask the Inventory Chatbot")
query = st.text_input("Ask about your equipment or SOP...")

if st.button("Get Answer"):
    if not query.strip():
        st.warning("Please enter a valid question.")
    else:
        query_lower = query.lower()
        handled = False

        if df is not None:
            if "fault" in query_lower:
                st.success("📋 Faulty Equipment List:")
                st.dataframe(faulty)
                handled = True
            elif "no status" in query_lower:
                st.success("📋 Equipment with no written status:")
                st.dataframe(no_status)
                handled = True
            elif any(word in query_lower for word in ["how many", "count"]):
                st.success(f"📊 Total equipment count: {len(df)}")
                handled = True

        if not handled and pdf_text:
            found = []
            for line in pdf_text.split("\n"):
                if any(word in line.lower() for word in query_lower.split()):
                    found.append(line.strip())
            if found:
                st.success("📄 Found relevant SOP lines:")
                st.write("\n".join(found[:10]))
                handled = True

        if not handled:
            st.info("No direct answer found. Try rephrasing your question.")
