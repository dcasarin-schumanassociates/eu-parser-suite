# app_b.py — Altair Gantt + Filters (tailored to your Excel)
from __future__ import annotations
import io
import pandas as pd
import streamlit as st
import altair as alt

# ---------- Column mapping for your Excel ----------
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
    # trim headers and map to canonical names
    df.columns = [c.strip() for c in df.columns]
    for src, dst in COLUMN_MAP.items():
        if src in df.columns and dst not in df.columns:
            df = df.rename(columns={src: dst})
    # normalise any remaining headers (lowercase)
    df = df.rename(columns={c: c.strip().lower() for c in df.columns})
    # programme default
    if "programme" not in df.columns:
        df["programme"] = "Horizon Europe"

    # coerce dates (EU format)
    for c in ("opening_date","deadline","first_deadline","second_deadline"):
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce", dayfirst=True)

    # numeric
    for c in ("budget_per_project_eur","total_budget_eur","trl","num_projects"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # two_stage to bool-ish
    if "two_stage" in df.columns:
        df["two_stage"] = (
            df["two_stage"].astype(str).str.strip().str.lower()
            .map({"true": True, "false": False, "yes": True, "no": False})
            .fillna(False)
        )
    else:
        df["two_stage"] = False

    return df

def keyword_filter(df: pd.DataFrame, term: str) -> pd.DataFrame:
    term = (term or "").strip().lower()
    if not term:
        return df
    return df[df.apply(lambda r: r.astype(str).str.lower().str.contains(term).any(), axis=1)]

def safe_date_bounds(series, start_fb="2000-01-01", end_fb="2100-12-31"):
    s = pd.to_datetime(series, errors="coerce").dropna()
    if s.empty:
        return (pd.to_datetime(start_fb).date(), pd.to_datetime(end_fb).date())
    lo, hi = s.min().date(), s.max().date()
    if lo == hi:
        hi = (pd.to_datetime(hi) + pd.Timedelta(days=1)).date()
    return lo, hi

def build_altair_chart(df: pd.DataFrame, end_col: str):
    g = df.dropna(subset=["opening_date", end_col, "title"]).copy()
    if g.empty:
        return None

    # tidy y-label: code — title; wrap every ~50 chars
    g["y_label"] = (g["code"].fillna("").astype(str) + " — " + g["title"].astype(str))\
        .str.replace(r"(.{50})", r"\1\n", regex=True)

    row_height = 28
    chart_height = max(400, len(g) * row_height)

    base = alt.Chart(g).encode(
        y=alt.Y("y_label:N", sort='-x', axis=alt.Axis(title=None, labelLimit=360)),
        color=alt.Color("programme:N", legend=None),
    )

    bars = base.mark_bar(cornerRadius=3).encode(
        x=alt.X("opening_date:T",
                axis=alt.Axis(title=None, format="%b %Y", tickCount="month")),
        x2=alt.X2(f"{end_col}:T"),
        tooltip=[
            alt.Tooltip("title:N", title="Title"),
            alt.Tooltip("type_of_action:N", title="Type"),
            alt.Tooltip("budget_per_project_eur:Q", title="Budget (€)", format=",.0f"),
            alt.Tooltip("opening_date:T", title="Open", format="%d %b %Y"),
            alt.Tooltip(f"{end_col}:T", title="Close", format="%d %b %Y"),
            alt.Tooltip("cluster:N", title="Cluster"),
            alt.Tooltip("destination_or_strand:N", title="Destination/Strand"),
            alt.Tooltip("version_label:N", title="Version"),
        ],
    )

    # monthly grid
    months = pd.date_range(g["opening_date"].min().floor("D"),
                           g[end_col].max().ceil("D"),
                           freq="MS")
    grid = alt.Chart(pd.DataFrame({"month": months})).mark_rule(stroke="#E5E7EB").encode(x="month:T")

    chart = (grid + bars).properties(height=chart_height)\
        .configure_axis(grid=False)\
        .configure_view(strokeWidth=0)

    return chart

# ---------- UI ----------
st.set_page_config(page_title="Calls Explorer (Altair)", layout="wide")
st.title("Calls Explorer (Altair Gantt + Filters)")

upl = st.file_uploader("Upload parsed Excel (.xlsx)", type=["xlsx"])
if not upl:
    st.stop()

xls = pd.ExcelFile(upl)
sheet = st.selectbox("Sheet", xls.sheet_names, index=0)
raw = pd.read_excel(xls, sheet_name=sheet)
df = canonicalise(raw)

# Sidebar filters
st.sidebar.header("Filters")
prog_opts    = sorted([p for p in df["programme"].dropna().unique().tolist() if p != ""])
cluster_opts = sorted([c for c in df.get("cluster", pd.Series(dtype=object)).dropna().unique().tolist() if c != ""])
type_opts    = sorted([t for t in df.get("type_of_action", pd.Series(dtype=object)).dropna().unique().tolist() if t != ""])
trl_opts     = sorted([str(int(x)) for x in df.get("trl", pd.Series(dtype=float)).dropna().unique() if pd.notna(x)])
dest_opts    = sorted([d for d in df.get("destination_or_strand", pd.Series(dtype=object)).dropna().unique().tolist() if d != ""])

programmes = st.sidebar.multiselect("Programme", options=prog_opts, default=prog_opts)
clusters   = st.sidebar.multiselect("Cluster", options=cluster_opts)
types      = st.sidebar.multiselect("Type of Action", options=type_opts)
trls       = st.sidebar.multiselect("TRL", options=trl_opts)
dests      = st.sidebar.multiselect("Destination / Strand", options=dest_opts)

# Dates: consider any deadline for overall bounds
open_lo, open_hi = safe_date_bounds(df.get("opening_date"))
dead_all = pd.concat([
    pd.to_datetime(df.get("deadline"), errors="coerce"),
    pd.to_datetime(df.get("first_deadline"), errors="coerce"),
    pd.to_datetime(df.get("second_deadline"), errors="coerce"),
], axis=0)
dead_lo, dead_hi = safe_date_bounds(dead_all)

c1, c2 = st.sidebar.columns(2)
with c1:
    open_start = st.date_input("Open from", value=open_lo, min_value=open_lo, max_value=open_hi)
with c2:
    open_end   = st.date_input("Open to",   value=open_hi, min_value=open_lo, max_value=open_hi)

c3, c4 = st.sidebar.columns(2)
with c3:
    dead_start = st.date_input("Close from", value=dead_lo, min_value=dead_lo, max_value=dead_hi)
with c4:
    dead_end   = st.date_input("Close to",   value=dead_hi, min_value=dead_lo, max_value=dead_hi)

# Budget slider
bud_series = pd.to_numeric(df.get("budget_per_project_eur"), errors="coerce").dropna()
if bud_series.empty:
    min_bud, max_bud = 0.0, 1_000_000.0
else:
    min_bud, max_bud = float(bud_series.min()), float(bud_series.max())
    if not (min_bud < max_bud):
        min_bud, max_bud = max(min_bud, 0.0), min_bud + 100000.0
budget_range = st.sidebar.slider("Budget per project (EUR)", min_bud, max_bud, (min_bud, max_bud), step=100000.0)

# Search
st.sidebar.header("Search")
keyword = st.sidebar.text_input("Keyword (searches all columns)")

# Deadline mode for the bar end
st.sidebar.header("Gantt options")
deadline_mode = st.sidebar.selectbox("Deadline to plot", ["Final deadline", "First stage", "Second stage"], index=0)
end_col = {"Final deadline": "deadline", "First stage": "first_deadline", "Second stage": "second_deadline"}[deadline_mode]

# Apply filters (and collect diagnostics)
diagnostics = {}

df_kw = keyword_filter(df, keyword); diagnostics["after keyword"] = len(df_kw)
f = df_kw.copy()

if programmes:
    f = f[f["programme"].isin(programmes)]; diagnostics["after programme"] = len(f)
if clusters:
    f = f[f["cluster"].isin(clusters)]; diagnostics["after cluster"] = len(f)
if types:
    f = f[f["type_of_action"].isin(types)]; diagnostics["after type"] = len(f)
if trls:
    trl_str = f["trl"].dropna().astype("Int64").astype(str)
    f = f[trl_str.isin(trls)]; diagnostics["after trl"] = len(f)
if dests:
    f = f[f["destination_or_strand"].isin(dests)]; diagnostics["after destination"] = len(f)

# date filters
f = f[(f["opening_date"] >= pd.to_datetime(open_start)) & (f["opening_date"] <= pd.to_datetime(open_end))]
diagnostics["after opening window"] = len(f)

if end_col in f.columns:
    f = f[(pd.to_datetime(f[end_col], errors="coerce") >= pd.to_datetime(dead_start)) &
          (pd.to_datetime(f[end_col], errors="coerce") <= pd.to_datetime(dead_end))]
    diagnostics["after deadline window"] = len(f)

# budget filter
f = f[(f["budget_per_project_eur"].fillna(0) >= budget_range[0]) &
      (f["budget_per_project_eur"].fillna(0) <= budget_range[1])]
diagnostics["after budget"] = len(f)

st.markdown(f"**Showing {len(f)} rows** after filters/search.")

# Tabs
tab1, tab2, tab3, tab4 = st.tabs(["📅 Gantt", "📋 Table", "📚 Full Data", "🛠 Diagnostics"])

with tab1:
    st.subheader(f"Gantt (Opening → {deadline_mode})")
    chart = build_altair_chart(f, end_col=end_col)
    if chart is None:
        st.info("No rows with valid Opening and selected deadline to display.")
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

with tab4:
    st.subheader("Diagnostics")
    st.write(diagnostics)
    st.caption("If the Gantt is empty, check which step dropped rows. The chart needs both `Opening Date` and the selected deadline column.")
