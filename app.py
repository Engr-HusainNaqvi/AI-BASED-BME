# app.py
import streamlit as st
import pandas as pd
import PyPDF2
from datetime import date, timedelta
import io
import numpy as np
from typing import List

# ---- model imports (huggingface) ----
from sentence_transformers import SentenceTransformer
from transformers import pipeline, AutoTokenizer, AutoModelForQuestionAnswering

# ---------------- CONFIG ----------------
st.set_page_config(page_title="BioMaint-AI (FREE)", layout="wide")
st.title("🩺 BioMaint-AI — Free (no paid APIs)")

st.markdown("""
This app:
- Parses inventory (CSV/XLSX)
- Detects faulty / no-status items
- Auto-generates PPM schedule (avoids Sundays)
- Semantic SOP search + extractive Q/A using free Hugging Face models (runs locally)
""")

# ---------------- HELPERS ----------------
@st.cache_data
def load_sentence_model(model_name="all-MiniLM-L6-v2"):
    return SentenceTransformer(model_name)

@st.cache_resource
def load_qa_pipeline(model_name="distilbert-base-cased-distilled-squad"):
    # load tokenizer & model then create pipeline
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForQuestionAnswering.from_pretrained(model_name)
    qa = pipeline("question-answering", model=model, tokenizer=tokenizer)
    return qa

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    column_map = {
        "equipment_name": "name_of_equipment",
        "device": "name_of_equipment",
        "device_name": "name_of_equipment",
        "machine": "name_of_equipment",
        "equipment": "name_of_equipment",
        "status_description": "status",
        "functional_status": "status",
        "dept": "department",
        "division": "department"
    }
    for old, new in column_map.items():
        if old in df.columns and new not in df.columns:
            df.rename(columns={old: new}, inplace=True)
    return df

def clean_status_series(s: pd.Series) -> pd.Series:
    s = s.astype(str).fillna("").str.strip().str.lower()
    replace_map = {
        "working": "functional",
        "ok": "functional",
        "good": "functional",
        "non-functional": "faulty",
        "non functional": "faulty",
        "out of order": "faulty",
        "needs repair": "faulty",
        "damaged": "faulty",
        "faulty battery": "faulty",
        "no": "",
        "n/a": ""
    }
    for k, v in replace_map.items():
        s = s.replace(k, v)
    s = s.replace(r'^\s*$', "no status written", regex=True)
    return s

def add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, [31,29 if year%4==0 and (year%100!=0 or year%400==0) else 28,31,30,31,30,31,31,30,31,30,31][month-1])
    return date(year, month, day)

def next_non_sunday(d: date) -> date:
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d

def generate_ppm_for_row(row, start_date: date, ppm_count=4, spacing_months=3, multi_day_depts=None):
    multi_day_depts = multi_day_depts or []
    ppm_entries = []
    for i in range(ppm_count):
        planned = add_months(start_date, i*spacing_months)
        planned = next_non_sunday(planned)
        duration_days = 2 if (str(row.get("department", "")).strip().lower() in multi_day_depts) else 1
        ppm_entries.append({
            "name_of_equipment": row.get("name_of_equipment", ""),
            "department": row.get("department", ""),
            "serial_no": row.get("serial_no", ""),
            "ppm_number": i+1,
            "planned_date": planned.isoformat(),
            "duration_days": duration_days,
            "status_at_planning": row.get("status", "")
        })
    return ppm_entries

# ---------- PDF chunking & embeddings ----------
def chunk_text(text: str, chunk_size=500, overlap=50) -> List[str]:
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = words[i:i+chunk_size]
        chunks.append(" ".join(chunk))
        i += chunk_size - overlap
    return chunks

@st.cache_data(show_spinner=False)
def build_sop_index(pdf_text: str, embed_model_name="all-MiniLM-L6-v2"):
    model = load_sentence_model(embed_model_name)
    chunks = chunk_text(pdf_text, chunk_size=300, overlap=40)
    embeddings = model.encode(chunks, convert_to_numpy=True, show_progress_bar=False)
    return {"chunks": chunks, "embeddings": embeddings, "model_name": embed_model_name}

def semantic_search_index(index, query: str, top_k=5):
    # cosine similarity
    model = load_sentence_model(index["model_name"])
    q_emb = model.encode([query], convert_to_numpy=True)[0]
    embs = index["embeddings"]
    # normalize
    def norm(x): return x / np.linalg.norm(x) if np.linalg.norm(x) != 0 else x
    qn = norm(q_emb)
    emns = np.array([norm(e) for e in embs])
    sims = (emns @ qn)
    top_idx = np.argsort(-sims)[:top_k]
    results = [{"chunk": index["chunks"][i], "score": float(sims[i])} for i in top_idx]
    return results

# ------------------ UI: Upload Inventory ------------------
st.header("1) Upload Inventory (.csv / .xlsx)")
uploaded_file = st.file_uploader("Inventory file", type=["csv","xlsx"], key="inventory")
df = None
if uploaded_file:
    try:
        if uploaded_file.name.endswith(".csv"):
            df = pd.read_csv(uploaded_file)
        else:
            df = pd.read_excel(uploaded_file)
        df = df.dropna(how="all")
        df = normalize_columns(df)
        for col in ["name_of_equipment", "status", "department", "serial_no", "model", "make"]:
            if col not in df.columns:
                df[col] = ""
        df["status"] = clean_status_series(df["status"])
        df["name_lower"] = df["name_of_equipment"].astype(str).str.lower()
        st.success("Inventory loaded")
    except Exception as e:
        st.error(f"Failed to read inventory: {e}")
        df = None

if df is not None:
    st.subheader("Inventory Overview")
    c1, c2, c3 = st.columns(3)
    c1.metric("Total equipment", len(df))
    c2.metric("Faulty equipment", int(df["status"].str.contains("faulty|error|repair|out of order|needs repair", na=False).sum()))
    c3.metric("No status written", int((df["status"] == "no status written").sum()))
    with st.expander("Preview inventory"):
        st.dataframe(df.drop(columns=["name_lower"], errors="ignore"))

# ------------------ UI: Upload SOP PDF ------------------
st.header("2) Upload SOP / Maintenance Manual (PDF)")
uploaded_pdf = st.file_uploader("SOP PDF", type=["pdf"], key="sop")
pdf_text = ""
sop_index = None
if uploaded_pdf:
    try:
        reader = PyPDF2.PdfReader(uploaded_pdf)
        texts = []
        for p in reader.pages:
            txt = p.extract_text() or ""
            texts.append(txt)
        pdf_text = "\n".join(texts)
        st.success("SOP extracted")
        with st.expander("Preview SOP (first 2000 chars)"):
            st.write(pdf_text[:2000] + ("..." if len(pdf_text) > 2000 else ""))
        # build semantic index (cached)
        if pdf_text.strip():
            with st.spinner("Building local semantic index (one-time)..."):
                sop_index = build_sop_index(pdf_text)
            st.success("SOP index ready for semantic search")
    except Exception as e:
        st.error(f"Failed to parse PDF: {e}")

# ------------------ UI: PPM generation ------------------
st.header("3) Generate PPM Schedule")
colA, colB = st.columns([2,1])
with colA:
    start_date = st.date_input("Start date (first PPM)", value=date.today())
    ppm_count = st.number_input("PPM visits per equipment", min_value=1, max_value=12, value=4)
    spacing_months = st.number_input("Spacing (months) between PPMs", min_value=1, max_value=12, value=3)
    multi_day_depts_input = st.text_area("Departments needing 2-day PPM (comma separated)", value="operation theater, radiology")
with colB:
    generate_btn = st.button("Generate PPM")

if generate_btn:
    if df is None:
        st.warning("Upload inventory first.")
    else:
        multi_day_depts = [d.strip().lower() for d in multi_day_depts_input.split(",") if d.strip()]
        all_ppm = []
        for _, row in df.iterrows():
            all_ppm.extend(generate_ppm_for_row(row, start_date, ppm_count=ppm_count, spacing_months=spacing_months, multi_day_depts=multi_day_depts))
        ppm_df = pd.DataFrame(all_ppm)
        st.success(f"Generated {len(ppm_df)} PPM entries")
        with st.expander("Preview PPM (first 200 rows)"):
            st.dataframe(ppm_df.head(200))
        # downloads
        csv = ppm_df.to_csv(index=False).encode("utf-8")
        st.download_button("Download PPM CSV", csv, file_name="ppm_schedule.csv", mime="text/csv")
        # excel
        towrite = io.BytesIO()
        with pd.ExcelWriter(towrite, engine="xlsxwriter") as writer:
            ppm_df.to_excel(writer, index=False, sheet_name="PPM")
            df.to_excel(writer, index=False, sheet_name="Inventory")
        towrite.seek(0)
        st.download_button("Download combined Excel", towrite, file_name="ppm_inventory.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ------------------ UI: Semantic search & QA (free) ------------------
st.header("4) Ask about SOP or Inventory (FREE semantic search + QA)")
query = st.text_input("Ask a question (e.g., 'SOP for defibrillator', 'How many ECGs are faulty?')", key="free_qa")

if st.button("Search (Local AI)"):
    q = (query or "").strip()
    if not q:
        st.warning("Type a question first.")
    else:
        handled = False
        # quick inventory intent checks (simple heuristics)
        if df is not None:
            ql = q.lower()
            if "how many" in ql and ("fault" in ql or "not working" in ql or "broken" in ql):
                # try to detect equipment word in query by matching known names
                candidates = df["name_of_equipment"].astype(str).str.lower().unique()
                found = None
                for cand in candidates:
                    if cand and cand in ql:
                        found = cand
                        break
                if found:
                    mask = df["name_of_equipment"].astype(str).str.lower().str.contains(found, na=False) & \
                           df["status"].str.contains("faulty|error|repair|out of order|needs repair", na=False)
                    res = df[mask]
                    st.success(f"🔍 {len(res)} '{found}' faulty.")
                    if not res.empty:
                        st.dataframe(res)
                    handled = True

        # semantic SOP search + extractive QA
        if not handled and sop_index is not None:
            # semantic match
            with st.spinner("Running semantic search on SOP..."):
                top = semantic_search_index(sop_index, q, top_k=5)
            st.write("Top SOP matches (score):")
            for i, r in enumerate(top):
                st.write(f"{i+1}. (score={r['score']:.3f}) — {r['chunk'][:400]}{'...' if len(r['chunk'])>400 else ''}")

            # run extractive QA on the concatenated top chunks
            try:
                qa = load_qa_pipeline()
                context = "\n\n".join([r["chunk"] for r in top])
                with st.spinner("Running local extractive Q/A..."):
                    answer = qa(question=q, context=context, top_k=1)
                if isinstance(answer, list):
                    ans_text = answer[0]["answer"] if answer else ""
                else:
                    # pipeline returns dict for top_k=1
                    ans_text = answer.get("answer", "")
                st.subheader("Answer (from SOP)")
                st.write(ans_text)
                handled = True
            except Exception as e:
                st.error(f"Local QA failed: {e}")

        # inventory keyword fallback
        if not handled and df is not None:
            res = df[df.apply(lambda r: q.lower() in str(r["name_of_equipment"]).lower() or q.lower() in str(r.get("department","")).lower(), axis=1)]
            if not res.empty:
                st.success(f"Found {len(res)} inventory rows matching the question:")
                st.dataframe(res)
                handled = True

        if not handled:
            st.info("No local answer found. Try a different phrasing or upload a SOP that contains the answer.")

# ------------------ Extras: export faulty / no-status ----------
st.header("Extras")
if df is not None:
    if st.button("Export faulty & no-status lists"):
        faulty = df[df["status"].str.contains("faulty|error|repair|out of order|needs repair", na=False)]
        no_status = df[df["status"] == "no status written"]
        towrite = io.BytesIO()
        with pd.ExcelWriter(towrite, engine="xlsxwriter") as writer:
            faulty.to_excel(writer, index=False, sheet_name="Faulty")
            no_status.to_excel(writer, index=False, sheet_name="NoStatus")
            df.to_excel(writer, index=False, sheet_name="Inventory")
        towrite.seek(0)
        st.download_button("Download lists Excel", data=towrite, file_name="faulty_nostatus_inventory.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
else:
    st.info("Upload inventory to access extras.")
