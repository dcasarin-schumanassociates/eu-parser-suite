# app_b.py
from __future__ import annotations
import io, os, time
from typing import List, Dict
import pandas as pd
import plotly.express as px
import streamlit as st
from dateutil import tz

# ========= Configuration =========
LOCAL_TZ = tz.gettz("Europe/Brussels")

# Map your uploaded Excel headers → canonical names the app uses
COLUMN_MAP = {
    # From your Horizon export
    "Code": "code",
    "Title": "title",
    "Opening Date": "opening_date",
    "Deadline": "deadline",
    "Destination": "destination_or_strand",
    "Budget Per Project": "budget_per_project_eur",
    "Total Budget": "total_budget_eur",
    "Number of Projects": "num_projects",
    "Type of Action": "type_of_action",
    "TRL": "trl",
    "Call Name": "call_name",
    "Expected Outcome": "expected_outcome",
    "Scope": "scope",
    "Description": "full_text",
    # Optional provenance if you have it
    "Source Filename": "source_filename",
    "Version Label": "version_label",
}

# Columns we want for filters/Gantt (if missing, we’ll create blanks)
REQUIRED = [
    "programme", "cluster", "code", "title",
    "opening_date", "deadline",
    "budget_per_project_eur", "total_budget_eur",
    "type_of_action", "trl", "destination_or_strand",
    "call_name", "expected_outcome", "scope", "full_text",
    "version_label", "source_filename",
]

DISPLAY = [
    "programme", "cluster", "code", "title",
    "opening_date", "deadline",
    "budget_per_project_eur", "total_budget_eur",
    "type_of_action", "trl", "destination_or_strand",
    "call_name", "version_label", "source_filename",
]

# ========= Helpers =========
def _canonicalise(df: pd.DataFrame) -> pd.DataFrame:
    # 1) Rename known columns
    for src, dst in COLUMN_MAP.items():
        if src in df.columns and dst not in df.columns:
            df = df.rename(columns={src: dst})

    # 2) Lowercase any remaining headers so we’re case-tolerant
    df = df.rename(columns={c: c.strip().lower() for c in df.columns})

    # 3) Ensure required columns exist
    for col in REQUIRED:
        if col not in df.columns:
            df[col] = pd.NA

    # 4) If programme/cluster are blank, provide sensible defaults
    if df["programme"].isna().all():
        df["programme"] = "Horizon Europe"
    if df["cluster"].isna().all():
        # Keep blank; many Horizon sheets don’t carry a “cluster” column. It’s optional in filters.
        df["cluster"] = pd.NA

    # 5) Parse dates (EU format tolerated); parse numbers
    for c in ["opening_date", "deadline"]:
        df[c] = pd.to_datetime(df[c], errors="coerce", dayfirst=True)

    for c in ["budget_per_project_eur", "total_budget_eur", "trl"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    return df

def _keyword_filter(df: pd.DataFrame, term: str) -> pd.DataFrame:
    term = (term or "").strip().lower()
    if not term:
        return df
    return df[df.apply(lambda r: r.astype(str).str.lower().str.contains(term).any(), axis=1)]

def _apply_filters(df: pd.DataFrame,
                   programmes, clusters, types, trls, dests,
                   bud_range, open_range, dead_range) -> pd.DataFrame:
    out = df.copy()

    if programmes:
        out = out[out["programme"].isin(programmes)]
    if clusters:
        out = out[out["cluster"].isin(clusters)]
    if types:
        out = out[out["type_of_action"].isin(types)]
    if trls:
        # Make TRL comparable as strings of ints (e.g., "5", "6")
        trl_str = out["trl"].dropna().astype("Int64").astype(str)
        out = out[trl_str.isin(trls)]

    if dests:
        out = out[out["destination_or_strand"].isin(dests)]

    # Budget range
    lo, hi = bud_range
    if "budget_per_project_eur" in out.columns:
        out = out[
            (out["budget_per_project_eur"].fillna(0) >= lo) &
            (out["budget_per_project_eur"].fillna(0) <= hi)
        ]

    # Date windows
    if all(pd.notna(open_range)):
        out = out[
            (out["opening_date"] >= pd.to_datetime(open_range[0])) &
            (out["opening_date"] <= pd.to_datetime(open_range[1]))
        ]
    if all(pd.notna(dead_range)):
        out = out[
            (out["deadline"] >= pd.to_datetime(dead_range[0])) &
            (out["deadline"] <= pd.to_datetime(dead_range[1]))
        ]

    return out

def _make_gantt(df: pd.DataFrame):
    g = df.dropna(subset=["opening_date", "deadline", "title"])
    if g.empty:
        return None

    # Short label for hover
    g = g.assign(
        _label=g["code"].fillna("").astype(str).str[:25] + " — " + g["title"].astype(str).str[:60]
    )
    fig = px.timeline(
        g,
        x_start="opening_date",
        x_end="deadline",
        y="cluster" if "cluster" in g.columns else "programme",
        color="programme",
        hover_data={
            "code": True, "title": True,
            "opening_date": True, "deadline": True,
            "budget_per_project_eur": True, "total_budget_eur": True,
            "type_of_action": True, "trl": True,
            "destination_or_strand": True,
            "version_label": True, "source_filename": True
        },
    )
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(
        height=600,
        margin=dict(l=10, r=10, t=10, b=10),
        xaxis=dict(rangeslider=dict(visible=True)),
        hoverlabel=dict(namelength=-1),
    )
    return fig

# ========= UI =========
st.set_page_config(page_title="Calls Explorer (Gantt + Filters)", layout="wide")
st.title("Calls Explorer (Gantt + Filters)")

st.caption("Upload the **Excel** from your parser (e.g., with columns like "
           "`Code`, `Title`, `Opening Date`, `Deadline`, `Destination`, `Budget Per Project`, …).")

upl = st.file_uploader("Upload parsed Excel (.xlsx)", type=["xlsx"])
if not upl:
    st.stop()

# Load workbook (all sheets, or pick one)
xls = pd.ExcelFile(upl)
sheet = st.selectbox("Sheet", xls.sheet_names, index=0)

raw = pd.read_excel(xls, sheet_name=sheet)
df = _canonicalise(raw)

# Quick sanity: show what we mapped
with st.expander("Show column mapping & missing fields"):
    mapped = [dst for dst in COLUMN_MAP.values() if dst in df.columns]
    missing = [c for c in ["code", "title", "opening_date", "deadline"] if c not in df.columns]
    st.write("**Mapped columns:**", ", ".join(mapped) or "—")
    if missing:
        st.warning(f"Missing (will appear blank): {', '.join(missing)}")

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

# Budget range
bud_series = pd.to_numeric(df["budget_per_project_eur"], errors="coerce")
min_bud = float(bud_series.min() if pd.notna(bud_series.min()) else 0)
max_bud = float(bud_series.max() if pd.notna(bud_series.max()) else 10_000_000)
min_bud = max(min_bud, 0.0)
budget_range = st.sidebar.slider(
    "Budget per project (EUR)", 0.0, max_bud if max_bud > 0 else 1_000_000.0,
    (min_bud, max_bud if max_bud > 0 else 1_000_000.0), step=100000.0
)

# Date ranges
open_min = pd.to_datetime(df["opening_date"], errors="coerce").min()
open_max = pd.to_datetime(df["opening_date"], errors="coerce").max()
dead_min = pd.to_datetime(df["deadline"], errors="coerce").min()
dead_max = pd.to_datetime(df["deadline"], errors="coerce").max()

col_open1, col_open2 = st.sidebar.columns(2)
with col_open1:
    open_start = st.date_input("Open from", value=(open_min.date() if pd.notna(open_min) else None))
with col_open2:
    open_end = st.date_input("Open to", value=(open_max.date() if pd.notna(open_max) else None))

col_dead1, col_dead2 = st.sidebar.columns(2)
with col_dead1:
    dead_start = st.date_input("Deadline from", value=(dead_min.date() if pd.notna(dead_min) else None))
with col_dead2:
    dead_end = st.date_input("Deadline to", value=(dead_max.date() if pd.notna(dead_max) else None))

# Keyword search
st.sidebar.header("Search")
keyword = st.sidebar.text_input("Keyword (searches all columns)")

# Apply search + filters
df_kw = _keyword_filter(df, keyword)
filtered = _apply_filters(
    df_kw, programmes, clusters, types, trls, dests,
    budget_range, (open_start, open_end), (dead_start, dead_end)
)

st.markdown(f"**Showing {len(filtered)} rows** after filters/search.")

# Tabs
tab1, tab2, tab3 = st.tabs(["📅 Gantt", "📋 Table", "📚 Full Data"])

with tab1:
    st.subheader("Gantt (Opening → Deadline)")
    fig = _make_gantt(filtered)
    if fig is None:
        st.info("No rows with valid `Opening Date` and `Deadline` to display.")
    else:
        st.plotly_chart(fig, use_container_width=True)

with tab2:
    st.subheader("Filtered table")
    show_cols = [c for c in DISPLAY if c in filtered.columns]
    if not show_cols:  # fallback for very minimal files
        show_cols = [c for c in filtered.columns if c not in ("full_text",)]
    st.dataframe(filtered[show_cols], use_container_width=True, hide_index=True)

    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as xw:
        filtered.to_excel(xw, index=False, sheet_name="filtered")
    out.seek(0)
    st.download_button("⬇️ Download filtered (Excel)", out,
                       file_name="calls_filtered.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

with tab3:
    st.subheader("Full data (expand rows for details)")
    key_full = st.text_input("Search within Full Data tab", value=keyword or "")
    full = _keyword_filter(filtered, key_full)

    for _, row in full.iterrows():
        title = f"{(row.get('code') or '')} — {(row.get('title') or '')}"
        with st.expander(title):
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
            with st.expander("Full description"):
                st.write(row.get("full_text",""))
