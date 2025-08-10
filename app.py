# app.py
import streamlit as st
import pandas as pd
import PyPDF2
from datetime import date, timedelta
import io
import re
from typing import List, Optional
from rapidfuzz import process, fuzz

# ---------------- Page config ----------------
st.set_page_config(page_title="BioMaint-AI (Final)", layout="wide")
st.title("🏥 BioMaint-AI — Final: Full-year PPM, Robust Parsing, Local QA")

st.write(
    "Upload inventory (CSV/XLSX) and optional SOP (PDF). "
    "The app will auto-detect headers, clean the table, provide QA, and generate a 1-year PPM schedule that avoids Sundays."
)

# ---------------- Helper functions ----------------
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
    # direct substring match
    for name in equipment_names:
        if not name:
            continue
        if name.lower() in q or q in name.lower():
            return name
    # fuzzy match
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


# ---------------- Upload & robust parse ----------------
st.header("1) Upload inventory (CSV or XLSX)")
uploaded_inventory = st.file_uploader("Inventory file", type=["csv", "xlsx"], key="inv")

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
                st.error("To read .xlsx upload, 'openpyxl' must be installed. Add it to requirements. Or upload CSV.")
                df_raw = None

        if df_raw is not None:
            header_row = detect_header_row(df_raw, max_rows=12)
            parsing_notes.append(f"Detected header row at index {header_row} (0-based).")
            header_vals = df_raw.iloc[header_row].fillna("").astype(str).tolist()
            cleaned_cols = [clean_colname(c, idx) for idx, c in enumerate(header_vals)]
            df = df_raw.iloc[header_row + 1:].copy().reset_index(drop=True)
            df.columns = cleaned_cols

            # drop generic columns (col_*)
            cols_to_drop = [c for c in df.columns if re.match(r"^col_\d+$", str(c))]
            if cols_to_drop:
                parsing_notes.append(f"Dropping {len(cols_to_drop)} generic columns.")
                df = df.drop(columns=cols_to_drop, errors="ignore")

            df = df.applymap(lambda x: x.strip() if isinstance(x, str) else x)
            df = drop_repeated_header_rows(df)
            df = df.dropna(how="all").reset_index(drop=True)

            # find likely columns and rename
            def find_col(possible_keywords):
                for k in possible_keywords:
                    for c in df.columns:
                        if k in c.lower():
                            return c
                return None

            equipment_col = find_col(["equipment_name", "equipmentname", "equipment", "device", "item", "equipment_name"])
            if not equipment_col:
                # fallback: longest median length column
                lengths = [(c, df[c].astype(str).map(len).median()) for c in df.columns]
                lengths = sorted(lengths, key=lambda x: -x[1])
                equipment_col = lengths[0][0] if lengths else df.columns[0]

            serial_col = find_col(["serial", "s_n", "sn", "serial_no", "serialno"])
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

            # ensure required columns exist
            for required in ["name_of_equipment", "serial_no", "model", "make", "department", "status"]:
                if required not in df.columns:
                    df[required] = ""

            # normalize status
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
                "faulty battery": "faulty",
                "n/a": ""
            })
            df["status"] = df["status"].replace(r'^\s*$', "no status written", regex=True)

            st.success("Inventory parsed and cleaned.")
    except Exception as e:
        st.error(f"Failed to parse inventory: {e}")
        df = None

if df is not None:
    with st.expander("Parsing notes"):
        st.write("\n".join(parsing_notes) if parsing_notes else "No notes.")
    st.subheader("Inventory (cleaned preview)")
    st.dataframe(df.head(200))

# ---------------- Faulty / no-status ----------------
faulty_df = pd.DataFrame()
no_status_df = pd.DataFrame()
if df is not None:
    faulty_df = df[df["status"].astype(str).str.contains("faulty|error|repair|out of order|needs repair", na=False)]
    no_status_df = df[df["status"].astype(str).str.contains("no status", na=False)]
    c1, c2, c3 = st.columns(3)
    c1.metric("Total", len(df))
    c2.metric("Faulty", len(faulty_df))
    c3.metric("No status", len(no_status_df))

# ---------------- SOP upload ----------------
st.header("2) Upload SOP (optional)")
uploaded_pdf = st.file_uploader("SOP PDF (optional)", type=["pdf"], key="sop")
pdf_text = ""
pdf_lines: List[str] = []
if uploaded_pdf:
    try:
        reader = PyPDF2.PdfReader(uploaded_pdf)
        pages = [p.extract_text() or "" for p in reader.pages]
        pdf_text = "\n".join(pages)
        pdf_lines = [ln.strip() for ln in re.split(r'[\r\n]+', pdf_text) if ln.strip()]
        st.success("SOP extracted.")
        with st.expander("SOP preview"):
            st.write(pdf_text[:2000] + ("..." if len(pdf_text) > 2000 else ""))
    except Exception as e:
        st.error(f"Failed to extract PDF text: {e}")

# ---------------- Q/A ----------------
st.header("3) Ask (inventory or SOP)")
query = st.text_input("E.g. 'How many cardiac monitors?', 'How many are faulty?', 'SOP for defibrillator'")

if st.button("Get Answer"):
    if not query or not str(query).strip():
        st.warning("Type a question first.")
    else:
        q = str(query).strip()
        q_l = q.lower()
        answered = False

        if df is not None and "how many" in q_l:
            equip_names = df["name_of_equipment"].astype(str).str.strip().replace(r'\s+', ' ', regex=True).unique().tolist()
            found = find_best_equipment_name(q_l, equip_names)
            if found:
                # map to actual casing
                original = next((n for n in equip_names if n.lower() == found.lower()), found)
                mask = df["name_of_equipment"].astype(str).str.lower().str.contains(found.lower(), na=False)
                matched = df[mask]
                total_count = len(matched)
                faulty_count = int(matched["status"].astype(str).str.contains("faulty", na=False).sum())
                functional_count = int(matched["status"].astype(str).str.contains("functional|working|ok", na=False).sum())
                no_status_count = int((matched["status"].astype(str) == "no status written").sum())
                st.success(f"Results for '{original}': total={total_count}, faulty={faulty_count}, functional={functional_count}, no_status={no_status_count}")
                if total_count > 0:
                    st.dataframe(matched.head(200))
                answered = True
            else:
                st.info("Couldn't detect specific equipment name. Showing overall counts.")
                st.write(f"Total equipment: {len(df)}")
                st.write(f"Faulty: {len(faulty_df)}")
                st.write(f"No status: {len(no_status_df)}")
                answered = True

        if not answered and df is not None and ("list" in q_l and "fault" in q_l or "show faulty" in q_l or "faulty list" in q_l):
            st.success("Listing faulty equipment:")
            st.dataframe(faulty_df)
            answered = True

        if not answered and df is not None and ("no status" in q_l or "not written" in q_l or "no status written" in q_l):
            st.success("Listing equipment with no status written:")
            st.dataframe(no_status_df)
            answered = True

        if not answered and pdf_text:
            matches = [ln for ln in pdf_lines if q_l in ln.lower()]
            if matches:
                st.success("Found in SOP (exact matches):")
                for ln in matches[:50]:
                    st.write(ln)
                answered = True
            else:
                fuzzy = process.extract(q, pdf_lines, scorer=fuzz.partial_ratio, limit=5)
                fuzzy = [t for t in fuzzy if t[1] >= 60]
                if fuzzy:
                    st.success("SOP fuzzy matches (score, line):")
                    for text, score, _ in fuzzy:
                        st.write(f"[{score}%] {text}")
                    answered = True

        if not answered and df is not None:
            mask = pd.Series(False, index=df.index)
            for c in df.columns:
                mask |= df[c].astype(str).str.lower().str.contains(q_l, na=False)
            sub = df[mask]
            if not sub.empty:
                st.success(f"Found {len(sub)} matching inventory rows:")
                st.dataframe(sub.head(200))
                answered = True

        if not answered:
            st.info("No local answer found. Try rephrasing or provide the SOP with the required info.")

# ---------------- PPM generation: full-year, skip Sundays ----------------
st.header("4) Generate PPM schedule (1-year, skip Sundays)")
ppm_enabled = st.checkbox("Enable PPM generation", value=True)
ppm_visits_per_year = st.number_input("Number of visits per equipment per year", min_value=1, max_value=12, value=4)
ppm_spacing_months = st.number_input("Spacing between visits (months)", min_value=1, max_value=12, value=12 // max(1, int(4)))
ppm_start_date = st.date_input("PPM first visit start date", value=date.today())
avoid_sundays = st.checkbox("Avoid Sundays (shift to Monday)", value=True)

if ppm_enabled and df is not None and st.button("Generate 1-year PPM"):
    ppm_rows = []
    # compute effective spacing: if user provided visits per year, we can compute spacing automatically
    spacing = ppm_spacing_months
    # but if user put spacing inconsistent with visits_per_year, we respect spacing input
    for _, row in df.iterrows():
        for i in range(ppm_visits_per_year):
            months_offset = i * spacing
            sched = add_months(ppm_start_date, months_offset)
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
    st.success(f"Generated {len(ppm_df)} PPM entries ({ppm_visits_per_year} visits/equipment).")
    with st.expander("PPM preview (first 200 rows)"):
        st.dataframe(ppm_df.head(200))

    # downloads
    csv_bytes = ppm_df.to_csv(index=False).encode("utf-8")
    st.download_button("Download PPM CSV", csv_bytes, file_name="ppm_schedule.csv", mime="text/csv")

    towrite = io.BytesIO()
    with pd.ExcelWriter(towrite, engine="xlsxwriter") as writer:
        ppm_df.to_excel(writer, index=False, sheet_name="PPM")
        df.to_excel(writer, index=False, sheet_name="Inventory")
    towrite.seek(0)
    st.download_button("Download PPM+Inventory Excel", towrite, file_name="ppm_inventory.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# ---------------- Extras ----------------
st.header("Extras")
if df is not None:
    if st.button("Export Faulty & No-Status (Excel)"):
        out = io.BytesIO()
        with pd.ExcelWriter(out, engine="xlsxwriter") as writer:
            faulty_df.to_excel(writer, index=False, sheet_name="Faulty")
            no_status_df.to_excel(writer, index=False, sheet_name="NoStatus")
            df.to_excel(writer, index=False, sheet_name="Inventory")
        out.seek(0)
        st.download_button("Download lists", out, file_name="faulty_nostatus_inventory.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
else:
    st.info("Upload an inventory file to enable features.")
