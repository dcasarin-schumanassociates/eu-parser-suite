# app_b.py (Altair version)
from __future__ import annotations
import io
import pandas as pd
import streamlit as st
import altair as alt

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


def make_gantt_altair(df: pd.DataFrame):
    g = df.dropna(subset=["opening_date", "deadline", "title"]).copy()
    if g.empty:
        return None

    # Wrap long titles for y-axis
    g["title_wrapped"] = g["code"].fillna("").astype(str) + " — " + g["title"].astype(str)
    g["title_wrapped"] = g["title_wrapped"].str.replace(r"(.{50})", r"\1\n", regex=True)

    row_height = 28
    chart_height = max(400, len(g) * row_height)

    base = alt.Chart(g).encode(
        y=alt.Y("title_wrapped:N",
                sort="-x",
                axis=alt.Axis(title=None, labelLimit=300)),
        color=alt.Color("programme:N", legend=None),
    )

    bars = base.mark_bar(cornerRadius=3).encode(
        x=alt.X("opening_date:T", axis=alt.Axis(title=None, format="%b %Y", tickCount="month")),
        x2="deadline:T",
        tooltip=[
            alt.Tooltip("title:N", title="Title"),
            alt.Tooltip("budget_per_project_eur:Q", title="Budget (€)", format=",.0f"),
            alt.Tooltip("type_of_action:N", title="Type"),
            alt.Tooltip("opening_date:T", title="Open", format="%d %b %Y"),
            alt.Tooltip("deadline:T", title="Close", format="%d %b %Y"),
        ],
    )

    # Monthly grid lines
    months = pd.date_range(g["opening_date"].min().floor("D"),
                           g["deadline"].max().ceil("D"),
                           freq="MS")
    grid = alt.Chart(pd.DataFrame({"month": months}))\
        .mark_rule(stroke="#DDD").encode(x="month:T")

    chart = (grid + bars).properties(height=chart_height)\
        .configure_axis(grid=False)\
        .configure_view(strokeWidth=0)

    return chart


# ===================
# UI
# ===================
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

st.sidebar.header("Search")
keyword = st.sidebar.text_input("Keyword (searches all columns)")

# Apply filters
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

st.markdown(f"**Showing {len(filtered)} rows** after filters/search.")

# Tabs
tab1, tab2, tab3 = st.tabs(["📅 Gantt", "📋 Table", "📚 Full Data"])

with tab1:
    st.subheader("Gantt (Opening → Deadline)")
    chart = make_gantt_altair(filtered)
    if chart is None:
        st.info("No rows with valid Opening/Deadline")
    else:
        st.altair_chart(chart, use_container_width=True)

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
