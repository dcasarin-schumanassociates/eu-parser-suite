# app_b.py
from __future__ import annotations
import io
import pandas as pd
import plotly.express as px
import streamlit as st

# ===================
# Column mapping
# ===================
COLUMN_MAP = {
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
    "Source Filename": "source_filename",
    "Version Label": "version_label",
}

DISPLAY_COLS = [
    "programme", "cluster", "code", "title",
    "opening_date", "deadline",
    "budget_per_project_eur", "total_budget_eur",
    "type_of_action", "trl", "destination_or_strand",
    "call_name", "version_label", "source_filename",
]

# ===================
# Helpers
# ===================
def canonicalise(df: pd.DataFrame) -> pd.DataFrame:
    # rename columns according to map
    for src, dst in COLUMN_MAP.items():
        if src in df.columns and dst not in df.columns:
            df = df.rename(columns={src: dst})

    # lower-case for safety
    df = df.rename(columns={c: c.lower() for c in df.columns})

    # ensure programme/cluster exist
    if "programme" not in df.columns:
        df["programme"] = "Horizon Europe"
    if "cluster" not in df.columns:
        df["cluster"] = pd.NA

    # coerce types
    for c in ["opening_date", "deadline"]:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce", dayfirst=True)

    for c in ["budget_per_project_eur", "total_budget_eur", "trl"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    return df


def keyword_filter(df: pd.DataFrame, term: str) -> pd.DataFrame:
    term = (term or "").strip().lower()
    if not term:
        return df
    return df[df.apply(lambda r: r.astype(str).str.lower().str.contains(term).any(), axis=1)]


def make_gantt(df: pd.DataFrame):
    g = df.dropna(subset=["opening_date", "deadline", "title"])
    if g.empty:
        return None

    g = g.assign(
        _label=g["code"].fillna("").astype(str)  # short label for clarity
    )

    fig = px.timeline(
        g,
        x_start="opening_date",
        x_end="deadline",
        y="_label",
        color="programme",
        hover_data=[
            "code", "title", "opening_date", "deadline",
            "budget_per_project_eur", "total_budget_eur",
            "type_of_action", "trl",
            "destination_or_strand", "version_label", "source_filename"
        ],
    )
    fig.update_yaxes(autorange="reversed")

    # Scale height with row count
    row_height = 40
    chart_height = max(600, len(g) * row_height)

    fig.update_layout(
        height=chart_height,
        margin=dict(l=10, r=10, t=10, b=10),
        xaxis=dict(
            rangeslider=dict(visible=True),
            showgrid=True,              # ensure grid lines are shown
            gridcolor="rgba(200,200,200,0.5)",  # light grey with some opacity
            gridwidth=1                 # make them thicker (default is 0.5)
        ),
        plot_bgcolor="white"  # white background makes lines stand out
    )
    return fig


    # Scale height automatically
    row_height = 40
    chart_height = max(600, len(g) * row_height)

    fig.update_layout(
        height=chart_height,
        margin=dict(l=10, r=10, t=10, b=10),
        xaxis=dict(rangeslider=dict(visible=True)),
    )
    return fig


# ===================
# UI
# ===================
st.set_page_config(page_title="Calls Explorer", layout="wide")
st.title("Calls Explorer (Gantt + Filters)")

upl = st.file_uploader("Upload parsed Excel (.xlsx)", type=["xlsx"])
if not upl:
    st.stop()

xls = pd.ExcelFile(upl)
sheet = st.selectbox("Sheet", xls.sheet_names, index=0)
raw = pd.read_excel(xls, sheet_name=sheet)
df = canonicalise(raw)

# Sidebar filters
st.sidebar.header("Filters")

prog_opts = sorted([p for p in df["programme"].dropna().unique().tolist() if p != ""])
cluster_opts = sorted([c for c in df["cluster"].dropna().unique().tolist() if pd.notna(c)])
type_opts = sorted([t for t in df["type_of_action"].dropna().unique().tolist() if pd.notna(t)])
trl_opts = sorted([str(int(x)) for x in df["trl"].dropna().unique() if pd.notna(x)])
dest_opts = sorted([d for d in df["destination_or_strand"].dropna().unique().tolist() if pd.notna(d)])

programmes = st.sidebar.multiselect("Programme", options=prog_opts, default=prog_opts)
clusters = st.sidebar.multiselect("Cluster / Strand", options=cluster_opts)
types = st.sidebar.multiselect("Type of Action", options=type_opts)
trls = st.sidebar.multiselect("TRL", options=trl_opts)
dests = st.sidebar.multiselect("Destination / Strand", options=dest_opts)

# Budget slider (robust)
bud_series = pd.to_numeric(df.get("budget_per_project_eur"), errors="coerce").dropna()
if bud_series.empty:
    min_bud, max_bud = 0.0, 1_000_000.0
else:
    min_bud, max_bud = float(bud_series.min()), float(bud_series.max())
    if not (min_bud < max_bud):
        min_bud, max_bud = max(min_bud, 0.0), min_bud + 100000.0

budget_range = st.sidebar.slider(
    "Budget per project (EUR)", min_bud, max_bud, (min_bud, max_bud), step=100000.0
)

# Dates (robust)
def safe_bounds(series, fallback_start="2000-01-01", fallback_end="2100-12-31"):
    s = pd.to_datetime(series, errors="coerce").dropna()
    if s.empty:
        return (pd.to_datetime(fallback_start).date(), pd.to_datetime(fallback_end).date())
    lo, hi = s.min().date(), s.max().date()
    if lo == hi:
        hi = (pd.to_datetime(hi) + pd.Timedelta(days=1)).date()
    return lo, hi

open_lo, open_hi = safe_bounds(df.get("opening_date"))
dead_lo, dead_hi = safe_bounds(df.get("deadline"))

col_open1, col_open2 = st.sidebar.columns(2)
with col_open1:
    open_start = st.date_input("Open from", value=open_lo, min_value=open_lo, max_value=open_hi)
with col_open2:
    open_end = st.date_input("Open to", value=open_hi, min_value=open_lo, max_value=open_hi)

col_dead1, col_dead2 = st.sidebar.columns(2)
with col_dead1:
    dead_start = st.date_input("Deadline from", value=dead_lo, min_value=dead_lo, max_value=dead_hi)
with col_dead2:
    dead_end = st.date_input("Deadline to", value=dead_hi, min_value=dead_lo, max_value=dead_hi)

# Keyword search
st.sidebar.header("Search")
keyword = st.sidebar.text_input("Keyword (searches all columns)")

# Apply search and filters
df_kw = keyword_filter(df, keyword)

filtered = df_kw.copy()
if programmes:
    filtered = filtered[filtered["programme"].isin(programmes)]
if clusters:
    filtered = filtered[filtered["cluster"].isin(clusters)]
if types:
    filtered = filtered[filtered["type_of_action"].isin(types)]
if trls:
    trl_str = filtered["trl"].dropna().astype("Int64").astype(str)
    filtered = filtered[trl_str.isin(trls)]
if dests:
    filtered = filtered[filtered["destination_or_strand"].isin(dests)]

filtered = filtered[
    (filtered["budget_per_project_eur"].fillna(0) >= budget_range[0]) &
    (filtered["budget_per_project_eur"].fillna(0) <= budget_range[1])
]
filtered = filtered[
    (filtered["opening_date"] >= pd.to_datetime(open_start)) &
    (filtered["opening_date"] <= pd.to_datetime(open_end)) &
    (filtered["deadline"] >= pd.to_datetime(dead_start)) &
    (filtered["deadline"] <= pd.to_datetime(dead_end))
]

st.markdown(f"**Showing {len(filtered)} rows** after filters/search.")

# Tabs
tab1, tab2, tab3 = st.tabs(["📅 Gantt", "📋 Table", "📚 Full Data"])

with tab1:
    st.subheader("Gantt (Opening → Deadline)")
    fig = make_gantt(filtered)
    if fig is None:
        st.info("No rows with valid Opening/Deadline")
    else:
        st.plotly_chart(fig, use_container_width=True)

with tab2:
    st.subheader("Filtered table")
    show_cols = [c for c in DISPLAY_COLS if c in filtered.columns]
    st.dataframe(filtered[show_cols], use_container_width=True, hide_index=True)

    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as xw:
        filtered.to_excel(xw, index=False, sheet_name="filtered")
    out.seek(0)
    st.download_button("⬇️ Download filtered (Excel)", out,
                       file_name="calls_filtered.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

with tab3:
    st.subheader("Full data (expand rows)")
    for _, row in filtered.iterrows():
        title = f"{row.get('code','')} — {row.get('title','')}"
        with st.expander(title):
            st.write(row.to_dict())
