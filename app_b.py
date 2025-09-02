# app_b.py
from __future__ import annotations
import io, os, time
from typing import List, Dict
from dateutil import tz
import pandas as pd
import plotly.express as px
import streamlit as st

# =========================================
# Config — change these to match your Excel
# =========================================
DEFAULT_SOURCE_PATH = "Calls_Catalog.xlsx"  # put your central Excel here (or leave blank to use uploader)
DEFAULT_SHEETS = [
    "Horizon_AllClusters_Current",   # your Horizon current view
    # "DEP_Current",
    # "Erasmus_Current",
]

# If your Excel has slightly different headers, map them here: incoming -> canonical
COLUMN_MAP = {
    # "Opening Date": "opening_date",
    # "Deadline Date": "deadline",
    # "Destination": "destination_or_strand",
}

REQUIRED_COLUMNS = [
    "programme", "cluster", "code", "title",
    "opening_date", "deadline",
    "budget_per_project_eur", "total_budget_eur",
    "type_of_action", "trl", "destination_or_strand",
    "call_name", "expected_outcome", "scope", "full_text",
    "version_label", "source_filename",
]

DISPLAY_COLUMNS = [
    "programme", "cluster", "code", "title",
    "opening_date", "deadline",
    "budget_per_project_eur", "total_budget_eur",
    "type_of_action", "trl", "destination_or_strand",
    "call_name", "version_label", "source_filename",
]

LOCAL_TZ = tz.gettz("Europe/Brussels")

# =========================================
# Helpers
# =========================================
def _file_signature(path: str) -> str:
    try:
        stt = os.stat(path)
        return f"{stt.st_size}-{int(stt.st_mtime)}"
    except FileNotFoundError:
        return "missing"

def _canonicalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    # apply optional COLUMN_MAP
    for k, v in COLUMN_MAP.items():
        if k in df.columns and v not in df.columns:
            df = df.rename(columns={k: v})
    # lowercase canonical columns if needed
    lower_map = {c: c.lower() for c in df.columns}
    df = df.rename(columns=lower_map)
    return df

@st.cache_data(show_spinner=False)
def load_excel_bytes(xlsx_bytes: bytes, sheets: List[str]) -> pd.DataFrame:
    xls = pd.ExcelFile(io.BytesIO(xlsx_bytes))
    dfs = []
    for sh in sheets or xls.sheet_names:
        if sh in xls.sheet_names:
            d = pd.read_excel(xls, sheet_name=sh)
            d["__sheet__"] = sh
            dfs.append(d)
    if not dfs:
        return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True)
    df = _canonicalise_columns(df)

    # coerce types
    for c in ["opening_date", "deadline"]:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    for c in ["budget_per_project_eur", "total_budget_eur", "trl"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # fill missing non-critical columns
    for c in REQUIRED_COLUMNS:
        if c not in df.columns:
            df[c] = pd.NA

    return df

@st.cache_data(show_spinner=False)
def load_from_path(path: str, sheets: List[str], sig: str) -> pd.DataFrame:
    # sig included solely to bust cache if file changes
    with open(path, "rb") as f:
        data = f.read()
    return load_excel_bytes(data, sheets)

def check_required_columns(df: pd.DataFrame) -> List[str]:
    return [c for c in REQUIRED_COLUMNS if c not in df.columns]

def df_filter_keyword(df: pd.DataFrame, term: str) -> pd.DataFrame:
    term = term.strip().lower()
    if not term:
        return df
    return df[df.apply(lambda r: r.astype(str).str.lower().str.contains(term).any(), axis=1)]

def df_apply_filters(df: pd.DataFrame,
                     programmes: List[str], clusters: List[str], types: List[str], trls: List[str],
                     dests: List[str], min_budget: float, max_budget: float,
                     start_open: pd.Timestamp|None, end_open: pd.Timestamp|None,
                     start_dead: pd.Timestamp|None, end_dead: pd.Timestamp|None) -> pd.DataFrame:
    out = df.copy()
    if programmes:
        out = out[out["programme"].isin(programmes)]
    if clusters:
        out = out[out["cluster"].isin(clusters)]
    if types:
        out = out[out["type_of_action"].isin(types)]
    if trls:
        out = out[out["trl"].astype("Int64").astype(str).isin(trls)]
    if dests:
        out = out[out["destination_or_strand"].isin(dests)]
    if "budget_per_project_eur" in out.columns:
        out = out[
            (out["budget_per_project_eur"].fillna(0) >= min_budget) &
            (out["budget_per_project_eur"].fillna(0) <= max_budget)
        ]
    if start_open is not None and end_open is not None:
        out = out[(out["opening_date"] >= pd.to_datetime(start_open)) &
                  (out["opening_date"] <= pd.to_datetime(end_open))]
    if start_dead is not None and end_dead is not None:
        out = out[(out["deadline"] >= pd.to_datetime(start_dead)) &
                  (out["deadline"] <= pd.to_datetime(end_dead))]
    return out

def make_gantt(df: pd.DataFrame):
    # Need these columns present and not null
    g = df.dropna(subset=["opening_date", "deadline", "title"])
    if g.empty:
        return None
    # Build a short label
    g = g.assign(
        _label=g["code"].fillna("").astype(str).str[:25] + " — " + g["title"].astype(str).str[:60]
    )
    fig = px.timeline(
        g,
        x_start="opening_date",
        x_end="deadline",
        y="cluster",  # or "programme" or "destination_or_strand"
        color="programme",
        hover_data={
            "code": True, "title": True, "opening_date": True, "deadline": True,
            "budget_per_project_eur": True, "total_budget_eur": True,
            "type_of_action": True, "trl": True, "destination_or_strand": True,
            "version_label": True, "source_filename": True
        },
        title=None,
    )
    fig.update_yaxes(autorange="reversed")  # Gantt style
    fig.update_layout(
        height=600,
        margin=dict(l=10, r=10, t=10, b=10),
        xaxis=dict(rangeslider=dict(visible=True)),
        hoverlabel=dict(namelength=-1)
    )
    return fig

# =========================================
# UI
# =========================================
st.set_page_config(page_title="Calls Explorer & Gantt", layout="wide")
st.title("Calls Explorer & Gantt")

# Data source picker
with st.expander("📁 Data source"):
    c1, c2 = st.columns([2,1])
    with c1:
        source_mode = st.radio("Choose source", ["Use path on server", "Upload Excel"], horizontal=True)
    if source_mode == "Use path on server":
        path = st.text_input("Excel path", value=DEFAULT_SOURCE_PATH)
        sheets = st.text_input("Sheet names (comma-separated)", value=",".join(DEFAULT_SHEETS)).split(",")
        sig = _file_signature(path)
        load_btn = st.button("🔄 Load / Refresh")
        if load_btn:
            load_from_path.clear()
        if sig == "missing":
            st.error("File not found. Switch to 'Upload Excel' or fix the path.")
            st.stop()
        df = load_from_path(path, [s.strip() for s in sheets if s.strip()], sig)
        last_mod = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.stat(path).st_mtime))
        st.caption(f"Loaded: **{os.path.basename(path)}** — Last modified: {last_mod}")
    else:
        upl = st.file_uploader("Upload central Excel (.xlsx)", type=["xlsx"])
        sheets = st.text_input("Sheet names (comma-separated, leave blank to read all found)", value="")
        sheets_list = [s.strip() for s in sheets.split(",") if s.strip()]
        if upl is None:
            st.stop()
        df = load_excel_bytes(upl.read(), sheets_list)
        st.caption(f"Loaded: **{upl.name}**")

# Basic checks
missing = check_required_columns(df)
if missing:
    st.warning("The following expected columns are missing (they will appear blank in the UI): " + ", ".join(missing))

# Sidebar filters
st.sidebar.header("Filters")
prog_opts = sorted([p for p in df["programme"].dropna().unique().tolist() if p != ""])
cluster_opts = sorted([c for c in df["cluster"].dropna().unique().tolist() if c != ""])
type_opts = sorted([t for t in df["type_of_action"].dropna().unique().tolist() if t != ""])
trl_opts = sorted([str(int(x)) for x in df["trl"].dropna().unique() if pd.notna(x)])
dest_opts = sorted([d for d in df["destination_or_strand"].dropna().unique().tolist() if d != ""])

programmes = st.sidebar.multiselect("Programme", options=prog_opts, default=prog_opts)
clusters = st.sidebar.multiselect("Cluster / Strand", options=cluster_opts)
types = st.sidebar.multiselect("Type of Action", options=type_opts)
trls = st.sidebar.multiselect("TRL", options=trl_opts)
dests = st.sidebar.multiselect("Destination / Strand", options=dest_opts)

min_bud = float(pd.to_numeric(df["budget_per_project_eur"], errors="coerce").min(skipna=True) or 0)
max_bud = float(pd.to_numeric(df["budget_per_project_eur"], errors="coerce").max(skipna=True) or 10_000_000)
min_bud = max(min_bud, 0.0)
budget_range = st.sidebar.slider("Budget per project (EUR)", 0.0, max_bud if max_bud>0 else 1_000_000.0, (min_bud, max_bud if max_bud>0 else 1_000_000.0), step=100000.0)

# Date filters
open_min = pd.to_datetime(df["opening_date"], errors="coerce").min()
open_max = pd.to_datetime(df["opening_date"], errors="coerce").max()
dead_min = pd.to_datetime(df["deadline"], errors="coerce").min()
dead_max = pd.to_datetime(df["deadline"], errors="coerce").max()

col_open1, col_open2 = st.sidebar.columns(2)
with col_open1:
    open_start = st.date_input("Open from", value=open_min.date() if pd.notna(open_min) else None)
with col_open2:
    open_end = st.date_input("Open to", value=open_max.date() if pd.notna(open_max) else None)

col_dead1, col_dead2 = st.sidebar.columns(2)
with col_dead1:
    dead_start = st.date_input("Deadline from", value=dead_min.date() if pd.notna(dead_min) else None)
with col_dead2:
    dead_end = st.date_input("Deadline to", value=dead_max.date() if pd.notna(dead_max) else None)

# Keyword search
st.sidebar.header("Search")
keyword = st.sidebar.text_input("Keyword (searches all columns)")

# Apply filters
filtered = df_apply_filters(
    df=df_filter_keyword(df, keyword),
    programmes=programmes, clusters=clusters, types=types, trls=trls, dests=dests,
    min_budget=budget_range[0], max_budget=budget_range[1],
    start_open=open_start, end_open=open_end,
    start_dead=dead_start, end_dead=dead_end
)

st.markdown(f"**Showing {len(filtered)} rows** after filters/search.")

# =========================
# Tabs: Gantt | Table | Full Data
# =========================
tab1, tab2, tab3 = st.tabs(["📅 Gantt", "📋 Table", "📚 Full Data"])

with tab1:
    st.subheader("Gantt view (Opening → Deadline)")
    fig = make_gantt(filtered)
    if fig is None:
        st.info("No rows with valid Opening/Deadline to display.")
    else:
        st.plotly_chart(fig, use_container_width=True)

with tab2:
    st.subheader("Filtered table")
    show_cols = [c for c in DISPLAY_COLUMNS if c in filtered.columns]
    st.dataframe(filtered[show_cols], use_container_width=True, hide_index=True)

    # Download current view
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as xw:
        filtered.to_excel(xw, index=False, sheet_name="filtered")
    out.seek(0)
    st.download_button("⬇️ Download filtered (Excel)", out, file_name="calls_filtered.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

with tab3:
    st.subheader("Full data (expand for details)")
    # optional sub-search within full data tab
    key_full = st.text_input("Search within Full Data tab", value=keyword or "")
    full = df_filter_keyword(filtered if key_full else filtered, key_full or "")

    for _, row in full.iterrows():
        title = f"{(row.get('code') or '')} — {(row.get('title') or '')}"
        with st.expander(title):
            # summary header line
            st.markdown(
                f"**Programme:** {row.get('programme','')} &nbsp;&nbsp; "
                f"**Cluster/Strand:** {row.get('cluster','')} &nbsp;&nbsp; "
                f"**Type:** {row.get('type_of_action','')} &nbsp;&nbsp; "
                f"**TRL:** {'' if pd.isna(row.get('trl')) else int(row.get('trl'))}"
            )
            st.markdown(
                f"**Opening:** {row.get('opening_date')} &nbsp;&nbsp; "
                f"**Deadline:** {row.get('deadline')} &nbsp;&nbsp; "
                f"**Destination/Strand:** {row.get('destination_or_strand','')}"
            )
            st.markdown(
                f"**Budget per project (EUR):** {row.get('budget_per_project_eur')} &nbsp;&nbsp; "
                f"**Total budget (EUR):** {row.get('total_budget_eur')}"
            )
            st.markdown(f"**Call name:** {row.get('call_name','')}")
            st.markdown(f"**Version:** {row.get('version_label','')} — **Source:** {row.get('source_filename','')}")

            st.markdown("**Expected Outcome**")
            st.write(row.get("expected_outcome",""))

            st.markdown("**Scope**")
            st.write(row.get("scope",""))

            with st.expander("Show full description"):
                st.write(row.get("full_text",""))
