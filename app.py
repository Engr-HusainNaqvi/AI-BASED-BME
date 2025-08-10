# app.py
"""
BioMaint-AI — polished front-end
Features:
- Robust inventory parsing (CSV / XLSX)
- Filters & search (Department, Status, free-text)
- SOP (PDF) upload + search
- 1-year PPM generation (visits/year or spacing), skip Sundays
- Exclude non-functional items option
- Editable PPM table in-app (st.data_editor if available)
- Excel (.xlsx) and CSV export
- Clean, attractive UI
"""

import streamlit as st
import pandas as pd
import PyPDF2
from datetime import date, timedelta
import io
import re
from typing import List, Optional
from rapidfuzz import process, fuzz

# ------------------- Page / CSS -------------------
st.set_page_config(page_title="BioMaint-AI", layout="wide", initial_sidebar_state="auto")
st.markdown(
    """<style>
    .header { display:flex; align-items:center; gap:12px; }
    .app-title { font-size:26px; font-weight:700; margin:0; }
    .app-sub { color:#6b7280; margin:0; }
    .card { background: #ffffff; padding:12px; border-radius:10px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
    .metric { font-size:22px; font-weight:700;}
    .muted { color:#6b7280; }
    </style>""",
    unsafe_allow_html=True,
)

# Header
col_h1, col_h2 = st.columns([0.9, 0.1])
with col_h1:
    st.markdown(
        '<div class="header">'
        '<div style="width:48px;height:48px;border-radius:10px;background:linear-gradient(135deg,#06b6d4,#3b82f6);display:flex;align-items:center;justify-content:center;color:white;font-weight:700;">🩺</div>'
        '<div><p class="app-title">BioMaint-AI</p><p class="app-sub">Smart PPM scheduling & inventory assistant</p></div>'
        '</div>',
        unsafe_allow_html=True,
    )
with col_h2:
    st.write("")  # reserved for future quick action

st.markdown("---")

# ------------------- Utilities -------------------
HEADER_KEYWORDS = [
    "s/n", "s n", "serial", "serial_no", "serial no", "equipment", "equipment name",
    "department", "dept", "model", "make", "status", "supplier"
]
DATE_COL_REGEX = re.compile(r"^\s*(\d{1,4}[\/\-\._]\d{1,4}[\/\-\._]\d{2,4})\s*$")
GENERIC_COL_REGEX = re.compile(r"^(unnamed|column)\b", flags=re.IGNORECASE)


def clean_colname(c: str, idx: int) -> str:
    s = str(c).strip()
    if s == "" or GENERIC_COL_REGEX.match(s):
        return f"col_{idx}"
    if DATE_COL_REGEX.match(c):
        return f"col_{idx}"
    s = s.lower()
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", "_", s).strip("_")
    return s or f"col_{idx}"


def detect_header_row(df_raw: pd.DataFrame, max_rows: int = 12) -> int:
    candidates = min(len(df_raw), max_rows)
    best_idx = 0
    best_score = -1
    for i in range(candidates):
        row = df_raw.iloc[i].astype(str).str.lower().tolist()
        score = 0
        for keyword in HEADER_KEYWORDS:
            for cell in row:
                if keyword in str(cell):
                    score += 1
                    break
        if score > best_score:
            best_score = score
            best_idx = i
    return best_idx if best_score >= 1 else 0


def drop_repeated_header_rows(df: pd.DataFrame) -> pd.DataFrame:
    cols = [str(c).lower() for c in df.columns]
    to_drop = []
    for idx, row in df.iterrows():
        matches = 0
        for col_name, val in zip(cols, row.astype(str).str.lower()):
            if not col_name:
                continue
            if col_name == val or val.strip() in col_name or col_name in val.strip():
                matches += 1
        if matches >= 3:
            to_drop.append(idx)
    if to_drop:
        df = df.drop(index=to_drop)
    return df


def find_best_equipment_name(query: str, equipment_names: List[str]) -> Optional[str]:
    q = query.lower().strip()
    for name in equipment_names:
        if not name:
            continue
        if name.lower() in q or q in name.lower():
            return name
    if equipment_names:
        best = process.extractOne(q, equipment_names, scorer=fuzz.partial_ratio)
        if best and best[1] >= 70:
            return best[0]
    return None


def add_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, [31,
                      29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


def next_non_sunday(d: date) -> date:
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


# ------------------- Upload Inventory -------------------
st.header("Upload inventory")
uploaded_inventory = st.file_uploader("CSV or Excel (.xlsx)", type=["csv", "xlsx"], key="inv_upload")

df = None
parsing_notes: List[str] = []

if uploaded_inventory:
    try:
        if uploaded_inventory.name.lower().endswith(".csv"):
            df_raw = pd.read_csv(uploaded_inventory, header=None, dtype=str, keep_default_na=False)
        else:
            try:
                import openpyxl
                df_raw = pd.read_excel(uploaded_inventory, header=None, dtype=str, engine="openpyxl")
            except Exception:
                st.error("To read .xlsx files install 'openpyxl' or upload CSV.")
                df_raw = None

        if df_raw is not None:
            header_row = detect_header_row(df_raw, max_rows=12)
            parsing_notes.append(f"Detected header row: {header_row}")
            header_vals = df_raw.iloc[header_row].fillna("").astype(str).tolist()
            cleaned_cols = [clean_colname(c, idx) for idx, c in enumerate(header_vals)]
            df = df_raw.iloc[header_row + 1:].copy().reset_index(drop=True)
            df.columns = cleaned_cols

            cols_to_drop = [c for c in df.columns if re.match(r"^col_\d+$", str(c))]
            if cols_to_drop:
                parsing_notes.append(f"Dropped {len(cols_to_drop)} generic columns.")
                df = df.drop(columns=cols_to_drop, errors="ignore")

            df = df.applymap(lambda x: x.strip() if isinstance(x, str) else x)
            df = drop_repeated_header_rows(df)
            df = df.dropna(how="all").reset_index(drop=True)

            def find_col(possible_keywords):
                for k in possible_keywords:
                    for c in df.columns:
                        if k in c.lower():
                            return c
                return None

            equipment_col = find_col(["equipment_name", "equipmentname", "equipment", "device", "item"])
            if not equipment_col:
                lengths = [(c, df[c].astype(str).map(len).median()) for c in df.columns]
                lengths = sorted(lengths, key=lambda x: -x[1])
                equipment_col = lengths[0][0] if lengths else df.columns[0]

            serial_col = find_col(["serial", "s_n", "sn", "serial_no"])
            model_col = find_col(["model"])
            make_col = find_col(["make", "manufacturer"])
            dept_col = find_col(["department", "dept", "division"])
            status_col = find_col(["status"])

            rename_map = {}
            if equipment_col:
                rename_map[equipment_col] = "name_of_equipment"
            if serial_col:
                rename_map[serial_col] = "serial_no"
            if model_col:
                rename_map[model_col] = "model"
            if make_col:
                rename_map[make_col] = "make"
            if dept_col:
                rename_map[dept_col] = "department"
            if status_col:
                rename_map[status_col] = "status"

            if rename_map:
                df = df.rename(columns=rename_map)
                parsing_notes.append(f"Renamed columns: {rename_map}")

            for required in ["name_of_equipment", "serial_no", "model", "make", "department", "status"]:
                if required not in df.columns:
                    df[required] = ""

            df["status"] = df["status"].astype(str).fillna("").str.strip().str.lower()
            df["status"] = df["status"].replace({
                "working": "functional",
                "ok": "functional",
                "good": "functional",
                "non-functional": "faulty",
                "non functional": "faulty",
                "out of order": "faulty",
                "needs repair": "faulty",
                "damaged": "faulty",
                "n/a": ""
            })
            df["status"] = df["status"].replace(r'^\s*$', "no status written", regex=True)

            st.success("Inventory loaded and cleaned.")
    except Exception as e:
        st.error(f"Failed to parse inventory: {e}")
        df = None

if df is not None:
    with st.expander("Parsing notes"):
        st.write("\n".join(parsing_notes) if parsing_notes else "No notes.")
    st.markdown("**Preview**")
    st.dataframe(df.head(150))

st.markdown("---")

# ------------------- Filters & search -------------------
st.subheader("Search & Filters")
col1, col2, col3 = st.columns([2,1,1])
search_text = col1.text_input("Search equipment (name, model, serial, dept...)")
department_list = ["(All)"]
status_list = ["(All)"]
if df is not None:
    department_list += sorted([d for d in df["department"].astype(str).unique() if str(d).strip() != ""])
    status_list += sorted(list(set(df["status"].astype(str).unique())))
department = col2.selectbox("Department", department_list)
status_choice = col3.selectbox("Status", status_list)

filtered_df = pd.DataFrame()
if df is not None:
    filtered_df = df.copy()
    if department != "(All)":
        filtered_df = filtered_df[filtered_df["department"].astype(str).str.lower() == department.lower()]
    if status_choice != "(All)":
        filtered_df = filtered_df[filtered_df["status"].astype(str).str.lower().str.contains(status_choice.lower(), na=False)]
    if search_text and search_text.strip():
        q = search_text.strip().lower()
        mask = pd.Series(False, index=filtered_df.index)
        for c in filtered_df.columns:
            mask |= filtered_df[c].astype(str).str.lower().str.contains(q, na=False)
        filtered_df = filtered_df[mask]
    st.write(f"Items: **{len(filtered_df)}**")
    st.dataframe(filtered_df.head(200))
else:
    st.info("Upload inventory to use search & filters.")

st.markdown("---")

# ------------------- SOP upload & search -------------------
st.subheader("SOP (optional)")
uploaded_pdf = st.file_uploader("Upload SOP PDF", type=["pdf"], key="sop2")
pdf_text = ""
pdf_lines: List[str] = []
if uploaded_pdf:
    try:
        reader = PyPDF2.PdfReader(uploaded_pdf)
        pages = [p.extract_text() or "" for p in reader.pages]
        pdf_text = "\n".join(pages)
        pdf_lines = [ln.strip() for ln in re.split(r'[\r\n]+', pdf_text) if ln.strip()]
        st.success("SOP loaded.")
        with st.expander("SOP preview (first 1500 chars)"):
            st.write(pdf_text[:1500] + ("..." if len(pdf_text) > 1500 else ""))
    except Exception as e:
        st.error(f"Failed to read SOP PDF: {e}")

sop_query = st.text_input("Search SOP text (optional)")
if sop_query and pdf_text:
    hits = [ln for ln in pdf_lines if sop_query.lower() in ln.lower()]
    if hits:
        st.success(f"Found {len(hits)} exact matches:")
        for ln in hits[:30]:
            st.write(ln)
    else:
        fuzzy = process.extract(sop_query, pdf_lines, scorer=fuzz.partial_ratio, limit=5)
        fuzzy = [t for t in fuzzy if t[1] >= 60]
        if fuzzy:
            st.success("SOP fuzzy matches:")
            for text, score, _ in fuzzy:
                st.write(f"[{score}%] {text}")
        else:
            st.info("No SOP matches found.")

st.markdown("---")

# ------------------- Intelligent quick QA -------------------
st.subheader("Quick question")
q = st.text_input("Ask e.g. 'How many cardiac monitors?' or 'How many are faulty?'")
if st.button("Answer"):
    if not q or not str(q).strip():
        st.warning("Type a question.")
    else:
        q_l = q.lower()
        answered = False
        if df is not None and "how many" in q_l:
            equip_names = df["name_of_equipment"].astype(str).str.strip().replace(r'\s+', ' ', regex=True).unique().tolist()
            found = find_best_equipment_name(q_l, equip_names)
            if found:
                original = next((n for n in equip_names if n.lower() == found.lower()), found)
                mask = df["name_of_equipment"].astype(str).str.lower().str.contains(found.lower(), na=False)
                matched = df[mask]
                total_count = len(matched)
                faulty_count = int(matched["status"].astype(str).str.contains("faulty", na=False).sum())
                functional_count = int(matched["status"].astype(str).str.contains("functional|working|ok", na=False).sum())
                no_status_count = int((matched["status"].astype(str) == "no status written").sum())
                st.success(f"'{original}': total={total_count}, faulty={faulty_count}, functional={functional_count}, no_status={no_status_count}")
                if total_count > 0:
                    st.dataframe(matched.head(200))
                answered = True
            else:
                st.info("Could not detect equipment name. Showing totals.")
                if df is not None:
                    st.write(f"Total equipment: {len(df)}")
                    st.write(f"Faulty: {len(df[df['status'].astype(str).str.contains('faulty', na=False)])}")
                answered = True

        if not answered and pdf_text:
            matches = [ln for ln in pdf_lines if q.lower() in ln.lower()]
            if matches:
                st.success("Found in SOP:")
                for ln in matches[:30]:
                    st.write(ln)
                answered = True
            else:
                fuzzy = process.extract(q, pdf_lines, scorer=fuzz.partial_ratio, limit=5)
                fuzzy = [t for t in fuzzy if t[1] >= 60]
                if fuzzy:
                    st.success("SOP fuzzy matches:")
                    for text, score, _ in fuzzy:
                        st.write(f"[{score}%] {text}")
                    answered = True

        if not answered and df is not None:
            mask = pd.Series(False, index=df.index)
            for c in df.columns:
                mask |= df[c].astype(str).str.lower().str.contains(q.lower(), na=False)
            sub = df[mask]
            if not sub.empty:
                st.success(f"Found {len(sub)} matching rows:")
                st.dataframe(sub.head(200))
                answered = True

        if not answered:
            st.info("No local answer. Try rephrasing or upload SOP containing the answer.")

st.markdown("---")

# ------------------- PPM generation (1-year) -------------------
st.subheader("Generate PPM — 1 year schedule")
if df is None:
    st.info("Upload inventory to generate PPM.")
else:
    c1, c2, c3, c4 = st.columns(4)
    visits_per_year = c1.number_input("Visits per equipment / year", min_value=1, max_value=12, value=4)
    spacing_months = c2.number_input("Spacing months (overrides)", min_value=1, max_value=12, value=12 // max(1, visits_per_year))
    ppm_start = c3.date_input("First visit date", value=date.today())
    exclude_nonfunctional = c4.checkbox("Exclude non-functional items", value=True)
    avoid_sundays = st.checkbox("Avoid Sundays (shift to Monday)", value=True)

    if st.button("Generate PPM preview"):
        # choose spacing
        spacing = spacing_months
        schedule_df = df.copy()
        if exclude_nonfunctional:
            schedule_df = schedule_df[~schedule_df["status"].astype(str).str.contains("faulty|repair|out of order|needs repair", na=False)]
        ppm_rows = []
        for _, row in schedule_df.iterrows():
            for i in range(visits_per_year):
                months_offset = i * spacing
                sched = add_months(ppm_start, months_offset)
                if avoid_sundays:
                    sched = next_non_sunday(sched)
                row_copy = row.to_dict()
                for k, v in row_copy.items():
                    if pd.isna(v):
                        row_copy[k] = ""
                    else:
                        row_copy[k] = str(v)
                row_copy["ppm_number"] = i + 1
                row_copy["scheduled_date"] = sched.isoformat()
                ppm_rows.append(row_copy)
        ppm_df = pd.DataFrame(ppm_rows)
        st.success(f"Generated {len(ppm_df)} schedule rows.")
        with st.expander("Preview & edit schedule"):
            try:
                edited = st.data_editor(ppm_df, num_rows="dynamic")
            except Exception:
                st.warning("Inline editor not available — using table view.")
                st.dataframe(ppm_df)
                edited = ppm_df

        # month-wise report
        try:
            tmp = pd.to_datetime((edited["scheduled_date"] if 'edited' in locals() else ppm_df["scheduled_date"]))
            month_counts = tmp.dt.month.value_counts().sort_index()
            month_table = pd.DataFrame({"month": month_counts.index, "ppm_count": month_counts.values})
            month_table["month_name"] = month_table["month"].apply(lambda m: pd.to_datetime(str(m), format='%m').strftime('%B'))
            st.subheader("Monthly workload")
            st.bar_chart(month_table.set_index("month_name")["ppm_count"])
            with st.expander("Monthly table"):
                st.dataframe(month_table)
        except Exception:
            st.info("Could not build monthly report (check schedule format).")

        # downloads
        final_ppm = edited if 'edited' in locals() and edited is not None else ppm_df
        csv_bytes = final_ppm.to_csv(index=False).encode("utf-8")
        st.download_button("Download PPM CSV", csv_bytes, file_name="ppm_schedule.csv", mime="text/csv")

        out = io.BytesIO()
        with pd.ExcelWriter(out, engine="xlsxwriter") as writer:
            final_ppm.to_excel(writer, index=False, sheet_name="PPM")
            df.to_excel(writer, index=False, sheet_name="Inventory")
            # auto column width
            for sheet_name, table_df in [("PPM", final_ppm), ("Inventory", df)]:
                worksheet = writer.sheets[sheet_name]
                for i, col in enumerate(table_df.columns):
                    try:
                        max_len = max(table_df[col].astype(str).map(len).max(), len(str(col))) + 2
                        worksheet.set_column(i, i, max_len)
                    except Exception:
                        pass
        out.seek(0)
        st.download_button("Download PPM + Inventory (Excel)", out, file_name="ppm_inventory.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

st.markdown("---")
st.caption("BioMaint-AI • Simple, fast, and hospital-ready.")
