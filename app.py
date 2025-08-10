# app.py
import streamlit as st
import pandas as pd
import PyPDF2
from datetime import date, timedelta
import io
import re
from typing import List, Optional
from rapidfuzz import process, fuzz

# ---------- Page config ----------
st.set_page_config(page_title="BioMaint-AI (Pro)", layout="wide")
st.title("🏥 BioMaint-AI — Pro: Filters, Editable PPM, Excel Download, Monthly Report")

st.markdown("""
**Features included**
- Robust inventory parsing (CSV / XLSX) — finds real header row, removes repeated headers.
- Filters: Department, Status, Equipment search.
- Full-year PPM generation (visits/yr or spacing months), skips Sundays (shift to Monday).
- Option to exclude non-functional equipment from scheduling.
- Edit PPM table in-app before downloading.
- Export final PPM + Inventory to Excel (.xlsx) and CSV.
- Month-wise PPM workload chart and pivot table.
""")

# ---------- Utilities (header detection / cleaning) ----------
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

# ---------- Upload inventory (robust) ----------
st.header("1) Upload Inventory (CSV or XLSX)")
uploaded_inventory = st.file_uploader("Upload inventory file", type=["csv","xlsx"], key="inv")

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
                st.error("To read .xlsx files you need 'openpyxl'. Add it to requirements or upload CSV.")
                df_raw = None

        if df_raw is not None:
            header_row = detect_header_row(df_raw, max_rows=12)
            parsing_notes.append(f"Detected header row: {header_row} (0-based).")
            header_vals = df_raw.iloc[header_row].fillna("").astype(str).tolist()
            cleaned_cols = [clean_colname(c, idx) for idx, c in enumerate(header_vals)]
            df = df_raw.iloc[header_row + 1:].copy().reset_index(drop=True)
            df.columns = cleaned_cols

            # drop generic columns like col_*
            cols_to_drop = [c for c in df.columns if re.match(r"^col_\d+$", str(c))]
            if cols_to_drop:
                parsing_notes.append(f"Dropped generic columns: {cols_to_drop}")
                df = df.drop(columns=cols_to_drop, errors="ignore")

            df = df.applymap(lambda x: x.strip() if isinstance(x, str) else x)
            df = drop_repeated_header_rows(df)
            df = df.dropna(how="all").reset_index(drop=True)

            # attempt to find columns and rename to canonical
            def find_col(possible_keywords):
                for k in possible_keywords:
                    for c in df.columns:
                        if k in c.lower():
                            return c
                return None

            equipment_col = find_col(["equipment_name","equipmentname","equipment","device","item"])
            if not equipment_col:
                lengths = [(c, df[c].astype(str).map(len).median()) for c in df.columns]
                lengths = sorted(lengths, key=lambda x: -x[1])
                equipment_col = lengths[0][0] if lengths else df.columns[0]

            serial_col = find_col(["serial","s_n","sn","serial_no"])
            model_col = find_col(["model"])
            make_col = find_col(["make","manufacturer"])
            dept_col = find_col(["department","dept","division"])
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

            # ensure required cols exist
            for req in ["name_of_equipment","serial_no","model","make","department","status"]:
                if req not in df.columns:
                    df[req] = ""

            df["status"] = df["status"].astype(str).fillna("").str.strip().str.lower()
            df["status"] = df["status"].replace({
                "working":"functional","ok":"functional","good":"functional",
                "non-functional":"faulty","non functional":"faulty","out of order":"faulty",
                "needs repair":"faulty","damaged":"faulty","n/a":""
            })
            df["status"] = df["status"].replace(r'^\s*$', "no status written", regex=True)
            st.success("Inventory parsed and cleaned.")
    except Exception as e:
        st.error(f"Failed to parse inventory: {e}")
        df = None

if df is not None:
    with st.expander("Parsing notes"):
        st.write("\n".join(parsing_notes) if parsing_notes else "No parsing notes.")
    st.subheader("Inventory preview (cleaned)")
    st.dataframe(df.head(200))

# ---------- Filters & Search ----------
st.header("2) Search & Filters")
col_a, col_b, col_c = st.columns([1,1,2])
search_text = col_a.text_input("Search equipment (free text)")
department_list = ["(All)"] + sorted([d for d in df["department"].astype(str).unique() if str(d).strip() != ""]) if df is not None else ["(All)"]
department = col_b.selectbox("Department", department_list)
status_options = ["(All)","functional","faulty","no status written"]
status_choice = col_c.selectbox("Status", status_options)

filtered_df = pd.DataFrame()
if df is not None:
    filtered_df = df.copy()
    if department and department != "(All)":
        filtered_df = filtered_df[filtered_df["department"].astype(str).str.lower() == str(department).lower()]
    if status_choice and status_choice != "(All)":
        filtered_df = filtered_df[filtered_df["status"].astype(str).str.lower().str.contains(status_choice, na=False)]
    if search_text and search_text.strip():
        q = search_text.strip().lower()
        mask = pd.Series(False, index=filtered_df.index)
        for c in filtered_df.columns:
            mask |= filtered_df[c].astype(str).str.lower().str.contains(q, na=False)
        filtered_df = filtered_df[mask]
    st.write(f"Found {len(filtered_df)} items after filters/search.")
    st.dataframe(filtered_df.head(200))
else:
    st.info("Upload an inventory file to enable filters & search.")

# ---------- SOP upload ----------
st.header("3) SOP upload & search (optional)")
uploaded_pdf = st.file_uploader("Upload SOP PDF (optional)", type=["pdf"], key="sop")
pdf_text = ""
pdf_lines: List[str] = []
if uploaded_pdf:
    try:
        reader = PyPDF2.PdfReader(uploaded_pdf)
        pages = [p.extract_text() or "" for p in reader.pages]
        pdf_text = "\n".join(pages)
        pdf_lines = [ln.strip() for ln in re.split(r'[\r\n]+', pdf_text) if ln.strip()]
        st.success("SOP loaded.")
        with st.expander("SOP preview"):
            st.write(pdf_text[:2000] + ("..." if len(pdf_text) > 2000 else ""))
    except Exception as e:
        st.error(f"Failed to read SOP PDF: {e}")

sop_query = st.text_input("Search SOP lines (optional)")
if sop_query and pdf_text:
    exact = [ln for ln in pdf_lines if sop_query.lower() in ln.lower()]
    if exact:
        st.success(f"Found {len(exact)} exact matches:")
        for ln in exact[:50]:
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

# ---------- PPM Generation UI ----------
st.header("4) PPM generation (1-year schedule)")
if df is None:
    st.info("Upload inventory first to generate PPM.")
else:
    col1, col2, col3, col4 = st.columns(4)
    visits_per_year = col1.number_input("Visits per equipment / year", min_value=1, max_value=12, value=4)
    spacing_months_input = col2.number_input("Or set spacing (months) (optional)", min_value=1, max_value=12, value=int(12//visits_per_year))
    start_date = col3.date_input("Start date", value=date.today())
    exclude_nonfunctional = col4.checkbox("Exclude non-functional items from PPM", value=True)
    avoid_sundays = st.checkbox("Avoid Sundays (shift to Monday)", value=True)

    if st.button("Generate 1-year PPM (preview)"):
        # determine spacing: prefer spacing_months_input
        spacing = spacing_months_input
        # Select which items to schedule
        schedule_df = df.copy()
        if exclude_nonfunctional:
            schedule_df = schedule_df[~schedule_df["status"].astype(str).str.contains("faulty|repair|out of order|needs repair", na=False)]
        ppm_rows = []
        for _, row in schedule_df.iterrows():
            for i in range(visits_per_year):
                months_offset = i * spacing
                sched = add_months(start_date, months_offset)
                if avoid_sundays:
                    sched = next_non_sunday(sched)
                row_copy = row.to_dict()
                for k,v in row_copy.items():
                    if pd.isna(v):
                        row_copy[k] = ""
                    else:
                        row_copy[k] = str(v)
                row_copy["ppm_number"] = i + 1
                row_copy["scheduled_date"] = sched.isoformat()
                ppm_rows.append(row_copy)
        ppm_df = pd.DataFrame(ppm_rows)
        st.success(f"Generated {len(ppm_df)} PPM entries ({visits_per_year} visits per equipment).")
        with st.expander("PPM preview & editable table"):
            # use Streamlit data editor for in-app editing
            try:
                edited = st.data_editor(ppm_df, num_rows="dynamic")
                st.write("Use the table above to edit any scheduled_date or other fields. Click 'Download final PPM' when ready.")
            except Exception:
                # fallback if st.data_editor not available
                st.dataframe(ppm_df)
                edited = ppm_df

        # Month-wise report
        try:
            ppm_df['month'] = pd.to_datetime(ppm_df['scheduled_date']).dt.month
            month_counts = ppm_df['month'].value_counts().sort_index()
            month_table = pd.DataFrame({
                'month': month_counts.index,
                'ppm_count': month_counts.values
            })
            month_table['month_name'] = month_table['month'].apply(lambda m: pd.to_datetime(str(m), format='%m').strftime('%B'))
            st.subheader("Month-wise PPM workload")
            st.bar_chart(month_table.set_index('month_name')['ppm_count'])
            with st.expander("Month-wise pivot table"):
                st.dataframe(month_table)
        except Exception:
            st.info("Could not build month-wise report (check date format).")

        # Download buttons — final excel with edited schedule and original inventory
        final_ppm = edited if 'edited' in locals() and edited is not None else ppm_df
        csv_bytes = final_ppm.to_csv(index=False).encode("utf-8")
        st.download_button("Download final PPM CSV", csv_bytes, file_name="ppm_final.csv", mime="text/csv")

        # Excel
        out = io.BytesIO()
        with pd.ExcelWriter(out, engine="xlsxwriter") as writer:
            final_ppm.to_excel(writer, index=False, sheet_name="PPM")
            df.to_excel(writer, index=False, sheet_name="Inventory")
            # formatting (auto column width)
            for sheet in ['PPM','Inventory']:
                worksheet = writer.sheets[sheet]
                # set column widths
                for i, col in enumerate((final_ppm.columns if sheet=='PPM' else df.columns)):
                    max_len = max(
                        (final_ppm[col].astype(str).map(len).max() if sheet=='PPM' else df[col].astype(str).map(len).max()),
                        len(str(col))
                    ) + 2
                    try:
                        worksheet.set_column(i, i, max_len)
                    except Exception:
                        pass
        out.seek(0)
        st.download_button("Download final PPM + Inventory (Excel)", out, file_name="ppm_inventory_final.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ---------- Extras ----------
st.header("Extras")
if df is not None:
    if st.button("Export Faulty & No-Status (Excel)"):
        faulty_df = df[df["status"].astype(str).str.contains("faulty|repair|out of order|needs repair", na=False)]
        no_status_df = df[df["status"].astype(str).str.contains("no status", na=False)]
        out = io.BytesIO()
        with pd.ExcelWriter(out, engine="xlsxwriter") as writer:
            faulty_df.to_excel(writer, index=False, sheet_name="Faulty")
            no_status_df.to_excel(writer, index=False, sheet_name="NoStatus")
            df.to_excel(writer, index=False, sheet_name="Inventory")
        out.seek(0)
        st.download_button("Download Faulty/NoStatus/Inventory", out, file_name="faulty_nostatus_inventory.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
else:
    st.info("Upload an inventory file to see extras.")
