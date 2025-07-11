import streamlit as st
import pandas as pd
import PyPDF2
import openai
import os

# Load API key (Streamlit secrets or env)
openai.api_key = st.secrets.get("openai_api_key") or os.getenv("OPENAI_API_KEY")

st.set_page_config(page_title="BioMaint-AI", layout="wide")
st.title("🩺 BioMaint-AI: Smart Biomedical Assistant + ChatGPT")

st.markdown("""
Upload your equipment inventory and SOPs to:
- 🔍 Track faulty equipment
- ✅ Detect missing status
- 📖 Search SOP/manual content
- 🤖 Ask ChatGPT if logic fails
""")

# --------------------- File Upload ---------------------
st.header("📁 Upload Inventory (.csv or .xlsx)")
uploaded_file = st.file_uploader("Upload your inventory file", type=["csv", "xlsx"])
df = None
pdf_text = ""
faulty = pd.DataFrame()
no_status = pd.DataFrame()

if uploaded_file:
    try:
        if uploaded_file.name.endswith(".csv"):
            df = pd.read_csv(uploaded_file)
        else:
            df = pd.read_excel(uploaded_file)

        df.columns = [col.strip().lower().replace(" ", "_") for col in df.columns]

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
        st.error(f"❌ Failed to read file: {e}")
        df = None

# --------------------- Dashboard ---------------------
if df is not None:
    st.subheader("📊 Inventory Dashboard")

    total = len(df)

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Equipment", total)
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

# --------------------- PDF Upload ---------------------
st.header("📄 Upload SOP / Maintenance Manual (PDF)")
uploaded_pdf = st.file_uploader("Upload `.pdf` file", type=["pdf"])

if uploaded_pdf:
    try:
        reader = PyPDF2.PdfReader(uploaded_pdf)
        pdf_text = "\n".join([page.extract_text() for page in reader.pages if page.extract_text()])
        st.success("✅ PDF content extracted.")
        with st.expander("📖 Preview SOP Text"):
            st.write(pdf_text[:2000] + "..." if len(pdf_text) > 2000 else pdf_text)
    except Exception as e:
        st.error(f"❌ Failed to extract PDF: {e}")

# --------------------- Question Answering ---------------------
st.header("💬 Ask a Question")

query = st.text_input("E.g. 'How many ECG machines are faulty?' or 'SOP for defibrillator'")

if st.button("Get Answer"):
    if not query.strip():
        st.warning("Please enter a valid question.")
    else:
        query_lower = query.lower()
        matched = pd.DataFrame()
        handled = False

        if df is not None:
            if "how many" in query_lower and "fault" in query_lower:
                for keyword in ["ecg", "monitor", "defibrillator", "suction", "pump", "ventilator"]:
                    if keyword in query_lower:
                        matched = df[
                            df["name_of_equipment"].str.lower().str.replace(" ", "", regex=False).str.contains(keyword.replace(" ", ""), na=False) &
                            df["status"].str.contains("faulty|error|repair|non functional", na=False)
                        ]
                        st.success(f"🔍 {len(matched)} {keyword.upper()} machine(s) are faulty.")
                        if not matched.empty:
                            st.dataframe(matched)
                        handled = True
                        break

            elif "list" in query_lower and "fault" in query_lower:
                st.success("📋 Listing all faulty equipment:")
                st.dataframe(faulty)
                handled = True

            elif "no status" in query_lower or "not written" in query_lower:
                st.success("📋 Equipment with no written status:")
                st.dataframe(no_status)
                handled = True

        if not handled and pdf_text:
            st.subheader("📄 SOP Lookup in PDF")
            found = []
            for line in pdf_text.split('\n'):
                if any(word in line.lower() for word in query_lower.split()):
                    found.append(line.strip())

            if found:
                st.success("Found relevant SOP/line:")
                st.write("\n\n".join(found[:10]))
                handled = True

        # ---------------- GPT Fallback ----------------
        if not handled and openai.api_key:
            try:
                st.subheader("🤖 ChatGPT's Answer (Fallback)")
                response = openai.ChatCompletion.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": "You are a biomedical engineer assistant. Use inventory and SOP data if provided."},
                        {"role": "user", "content": f"Inventory:\n{df.to_csv(index=False)[:1500] if df is not None else ''}\n\nPDF SOP:\n{pdf_text[:1500]}\n\nQuestion:\n{query}"}
                    ],
                    temperature=0.3,
                    max_tokens=300
                )
                answer = response['choices'][0]['message']['content'].strip()
                st.success("ChatGPT says:")
                st.write(answer)
                handled = True

            except Exception as e:
                st.error(f"GPT Error: {e}")
                st.info("Please check if your OpenAI API key is correct.")

        elif not handled:
            st.info("No relevant data found in file or SOP.")
