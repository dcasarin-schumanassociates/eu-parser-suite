# app_b_scatter.py — Scatter Timeline (Opening & Deadlines as points)
from __future__ import annotations
import io
import pandas as pd
import streamlit as st
import altair as alt

# ---------- Column mapping ----------
COLUMN_MAP = {
    "Code": "code",
    "Title": "title",
    "Opening Date": "opening_date",
    "Deadline": "deadline",
    "First Stage Deadline": "first_deadline",
    "Second Stage Deadline": "second_deadline",
    "Two-Stage": "two_stage",
    "Cluster": "cluster",
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
    "Source Filename": "source_filename",
    "Version Label": "version_label",
    "Parsed On (UTC)": "parsed_on_utc",
}

DISPLAY_COLS = [
    "code","title","opening_date",
    "deadline","first_deadline","second_deadline","two_stage",
    "cluster","destination_or_strand","type_of_action","trl",
    "budget_per_project_eur","total_budget_eur","num_projects",
    "call_name","version_label","source_filename","parsed_on_utc",
]

# ---------- Helpers ----------
def canonicalise(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [c.strip() for c in df.columns]
    for src, dst in COLUMN_MAP.items():
        if src in df.columns and dst not in df.columns:
            df = df.rename(columns={src: dst})
    df = df.rename(columns={c: c.strip().lower() for c in df.columns})
    if "programme" not in df.columns:
        df["programme"] = "Horizon Europe"
    for c in ("opening_date","deadline","first_deadline","second_deadline"):
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce", dayfirst=True)
    for c in ("budget_per_project_eur","total_budget_eur","trl","num_projects"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "two_stage" in df.columns:
        df["two_stage"] = (
            df["two_stage"].astype(str).str.strip().str.lower()
            .map({"true": True, "false": False, "yes": True, "no": False})
            .fillna(False)
        )
    else:
        df["two_stage"] = False
    return df

def safe_date_bounds(series, start_fb="2000-01-01", end_fb="2100-12-31"):
    s = pd.to_datetime(series, errors="coerce").dropna()
    if s.empty:
        return (pd.to_datetime(start_fb).date(), pd.to_datetime(end_fb).date())
    lo, hi = s.min().date(), s.max().date()
    if lo == hi:
        hi = (pd.to_datetime(hi) + pd.Timedelta(days=1)).date()
    return lo, hi

def build_events(df: pd.DataFrame) -> pd.DataFrame:
    """Explode calls into events for scatter timeline"""
    rows = []
    for _, r in df.iterrows():
        if pd.notna(r.get("opening_date")):
            rows.append({"date": r["opening_date"], "kind": "Opening", **r.to_dict()})
        if pd.notna(r.get("first_deadline")):
            rows.append({"date": r["first_deadline"], "kind": "Stage 1", **r.to_dict()})
        if pd.notna(r.get("second_deadline")):
            rows.append({"date": r["second_deadline"], "kind": "Stage 2", **r.to_dict()})
        if pd.notna(r.get("deadline")):
            rows.append({"date": r["deadline"], "kind": "Final", **r.to_dict()})
    return pd.DataFrame(rows)

def build_scatter_chart(ev: pd.DataFrame, view_start, view_end):
    if ev.empty:
        return None

    domain_min = pd.to_datetime(view_start)
    domain_max = pd.to_datetime(view_end)

    base = alt.Chart(ev).encode(
        x=alt.X(
            "date:T",
            scale=alt.Scale(domain=[domain_min, domain_max]),
            axis=alt.Axis(title=None, format="%b %Y", tickCount="month", orient="top")
        ),
        y=alt.Y("kind:N",
                sort=["Opening","Stage 1","Stage 2","Final"],
                axis=alt.Axis(title=None, labelFontSize=14, labelPadding=10)),
        color=alt.Color("programme:N", legend=alt.Legend(title="Programme")),
        tooltip=[
            alt.Tooltip("code:N", title="Code"),
            alt.Tooltip("title:N", title="Title"),
            alt.Tooltip("programme:N", title="Programme"),
            alt.Tooltip("kind:N", title="Kind"),
            alt.Tooltip("date:T", title="Date", format="%d %b %Y"),
        ]
    )

    points = base.mark_circle(size=90)
    return points.properties(height=500).configure_view(strokeWidth=0)

# ---------- UI ----------
st.set_page_config(page_title="Calls Explorer — Scatter Timeline", layout="wide")
st.title("Calls Explorer — Scatter Timeline (Openings & Deadlines)")

upl = st.file_uploader("Upload parsed Excel (.xlsx)", type=["xlsx"])
if not upl:
    st.stop()

xls = pd.ExcelFile(upl)
sheet = st.selectbox("Sheet", xls.sheet_names, index=0)
raw = pd.read_excel(xls, sheet_name=sheet)
df = canonicalise(raw)

# Sidebar filters (same as before) ...
with st.sidebar.form("filters_form", clear_on_submit=False):
    st.header("Filters")

    prog_opts    = sorted([p for p in df["programme"].dropna().unique().tolist() if p != ""])
    cluster_opts = sorted([c for c in df.get("cluster", pd.Series(dtype=object)).dropna().unique().tolist() if c != ""])
    type_opts    = sorted([t for t in df.get("type_of_action", pd.Series(dtype=object)).dropna().unique().tolist() if t != ""])
    trl_opts     = sorted([str(int(x)) for x in df.get("trl", pd.Series(dtype=float)).dropna().unique() if pd.notna(x)])
    dest_opts    = sorted([d for d in df.get("destination_or_strand", pd.Series(dtype=object)).dropna().unique().tolist() if d != ""])

    programmes = st.multiselect("Programme", options=prog_opts, default=prog_opts)
    clusters   = st.multiselect("Cluster", options=cluster_opts)
    types      = st.multiselect("Type of Action", options=type_opts)
    trls       = st.multiselect("TRL", options=trl_opts)
    dests      = st.multiselect("Destination / Strand", options=dest_opts)

    # Date bounds
    open_lo, open_hi = safe_date_bounds(df.get("opening_date"))
    dead_all = pd.concat([
        pd.to_datetime(df.get("deadline"), errors="coerce"),
        pd.to_datetime(df.get("first_deadline"), errors="coerce"),
        pd.to_datetime(df.get("second_deadline"), errors="coerce"),
    ], axis=0)
    dead_lo, dead_hi = safe_date_bounds(dead_all)

    col1, col2 = st.columns(2)
    with col1:
        open_start = st.date_input("Open from", value=open_lo, min_value=open_lo, max_value=open_hi)
    with col2:
        open_end   = st.date_input("Open to",   value=open_hi, min_value=open_lo, max_value=open_hi)

    col3, col4 = st.columns(2)
    with col3:
        close_from = st.date_input("Close from", value=dead_lo, min_value=dead_lo, max_value=dead_hi)
    with col4:
        close_to   = st.date_input("Close to",   value=dead_hi, min_value=dead_lo, max_value=dead_hi)

    st.subheader("View window")
    if "view_start" not in st.session_state or "view_end" not in st.session_state:
        st.session_state.view_start = open_lo
        st.session_state.view_end   = dead_hi

    view_start = st.date_input("View from", value=st.session_state.view_start)
    view_end   = st.date_input("View to",   value=st.session_state.view_end)

    applied = st.form_submit_button("Apply filters")

if applied:
    st.session_state.view_start = view_start
    st.session_state.view_end   = view_end

# ---- Apply filters ----
f = df.copy()
if programmes: f = f[f["programme"].isin(programmes)]
if clusters:   f = f[f["cluster"].isin(clusters)]
if types:      f = f[f["type_of_action"].isin(types)]
if trls:
    trl_str = f["trl"].dropna().astype("Int64").astype(str)
    f = f[trl_str.isin(trls)]
if dests:      f = f[f["destination_or_strand"].isin(dests)]

f = f[(f["opening_date"] >= pd.to_datetime(open_start)) &
      (f["opening_date"] <= pd.to_datetime(open_end))]

any_end_in = (
    (pd.to_datetime(f.get("deadline"), errors="coerce").between(pd.to_datetime(close_from), pd.to_datetime(close_to), inclusive="both")) |
    (pd.to_datetime(f.get("first_deadline"), errors="coerce").between(pd.to_datetime(close_from), pd.to_datetime(close_to), inclusive="both")) |
    (pd.to_datetime(f.get("second_deadline"), errors="coerce").between(pd.to_datetime(close_from), pd.to_datetime(close_to), inclusive="both"))
)
f = f[any_end_in.fillna(False)]

st.markdown(f"**Showing {len(f)} rows** after filters.")

# Tabs
tab1, tab2, tab3 = st.tabs(["📅 Scatter Timeline", "📋 Table", "📚 Full Data"])

with tab1:
    st.subheader("Scatter Timeline of Openings & Deadlines")
    events = build_events(f)
    chart = build_scatter_chart(events, view_start=st.session_state.view_start, view_end=st.session_state.view_end)
    if chart is None:
        st.info("No events to display.")
    else:
        st.altair_chart(chart, use_container_width=True)

with tab2:
    st.subheader("Filtered table")
    show_cols = [c for c in DISPLAY_COLS if c in f.columns]
    st.dataframe(f[show_cols], use_container_width=True, hide_index=True)
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as xw:
        f.to_excel(xw, index=False, sheet_name="filtered")
    out.seek(0)
    st.download_button("⬇️ Download filtered (Excel)", out,
                       file_name="calls_filtered.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

with tab3:
    st.subheader("Full data (expand rows)")
    for _, row in f.iterrows():
        title = f"{row.get('code','')} — {row.get('title','')}"
        with st.expander(title):
            st.write(row.to_dict())
