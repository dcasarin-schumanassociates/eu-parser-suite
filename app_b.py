# app_b.py — Altair Gantt with monthly shading, start/end labels, top axis, zoom
from __future__ import annotations
import io
import pandas as pd
import streamlit as st
import altair as alt

# ---------- Column mapping tailored to your Excel ----------
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
    # Trim and map headers
    df.columns = [c.strip() for c in df.columns]
    for src, dst in COLUMN_MAP.items():
        if src in df.columns and dst not in df.columns:
            df = df.rename(columns={src: dst})
    # Normalise leftover headers
    df = df.rename(columns={c: c.strip().lower() for c in df.columns})

    # Programme default
    if "programme" not in df.columns:
        df["programme"] = "Horizon Europe"

    # Dates (EU day-first)
    for c in ("opening_date","deadline","first_deadline","second_deadline"):
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce", dayfirst=True)

    # Numerics
    for c in ("budget_per_project_eur","total_budget_eur","trl","num_projects"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # Two-stage to bool-like
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

def wrap_label(text: str, width=38, max_lines=3) -> str:
    """Hard-wrap every `width` chars, cap to `max_lines`."""
    s = str(text or "")
    parts = [s[i:i+width] for i in range(0, len(s), width)]
    parts = parts[:max_lines]
    return "\n".join(parts)

def build_month_bands(min_x: pd.Timestamp, max_x: pd.Timestamp) -> pd.DataFrame:
    """Create alternating month spans for background shading."""
    start = pd.Timestamp(min_x).to_period("M").start_time
    end   = (pd.Timestamp(max_x).to_period("M") + 1).start_time
    months = pd.date_range(start, end, freq="MS")
    rows = []
    for i in range(len(months) - 1):
        rows.append({"start": months[i], "end": months[i+1], "band": i % 2})
    return pd.DataFrame(rows)

def build_altair_chart(df: pd.DataFrame, end_col: str):
    # Sort key: earliest of any deadline
    g = df.copy()
    g["earliest_deadline"] = pd.to_datetime(
        pd.concat(
            [
                pd.to_datetime(g.get("deadline"), errors="coerce"),
                pd.to_datetime(g.get("first_deadline"), errors="coerce"),
                pd.to_datetime(g.get("second_deadline"), errors="coerce"),
            ],
            axis=1,
        ).min(axis=1),
        errors="coerce",
    )

    # Require opening + selected end date + title
    g = g.dropna(subset=["opening_date", end_col, "title"])
    if g.empty:
        return None

    # Wrapped y-label: CODE — Title
    full_label = g["code"].fillna("").astype(str) + " — " + g["title"].astype(str)
    g = g.assign(y_label=[wrap_label(t, width=38, max_lines=3) for t in full_label])

    # Sort earliest first (urgent on top)
    g = g.sort_values(["earliest_deadline", "opening_date"], ascending=[True, True])
    y_order = g["y_label"].tolist()

    # Chart height proportional to rows
    row_height = 42  # extra room for wrapped labels
    chart_height = max(480, len(g) * row_height)

    # Calendar domain and padding (zoomed out a bit)
    min_x = min(g["opening_date"].min(), g[end_col].min())
    max_x = max(g["opening_date"].max(), g[end_col].max())
    pad_days = 30
    domain_min = pd.Timestamp(min_x) - pd.Timedelta(days=pad_days)
    domain_max = pd.Timestamp(max_x) + pd.Timedelta(days=pad_days)

    # Background monthly shading
    bands_df = build_month_bands(min_x, max_x)
    month_bands = (
        alt.Chart(bands_df)
        .mark_rect()
        .encode(
            x=alt.X("start:T", axis=None),
            x2=alt.X2("end:T"),
            opacity=alt.Opacity("band:Q", scale=alt.Scale(domain=[0,1], range=[0.0, 0.08]), legend=None),
            color=alt.value("#000"),
        )
    )

    # Monthly & weekly grid lines
    months = pd.date_range(pd.Timestamp(min_x).to_period("M").start_time,
                           pd.Timestamp(max_x).to_period("M").end_time,
                           freq="MS")
    week_start = pd.Timestamp(min_x).to_period("W-MON").start_time
    week_end   = pd.Timestamp(max_x).to_period("W-MON").start_time
    weeks = pd.date_range(week_start, week_end, freq="W-MON")

    month_grid = (
        alt.Chart(pd.DataFrame({"t": months}))
        .mark_rule(stroke="#9AA0A6", strokeWidth=1.5)
        .encode(x="t:T")
    )
    week_grid = (
        alt.Chart(pd.DataFrame({"t": weeks}))
        .mark_rule(stroke="#E5E7EB", strokeWidth=1)
        .encode(x="t:T")
    )

    # Base encodings
    base = alt.Chart(g).encode(
        y=alt.Y(
            "y_label:N",
            sort=y_order,
            axis=alt.Axis(title=None, labelLimit=1200, labelFontSize=12)  # bigger, don't drop labels
        ),
        color=alt.Color("programme:N", legend=None),
    )

    # Bars
    bars = base.mark_bar(cornerRadius=3).encode(
        x=alt.X(
            "opening_date:T",
            axis=alt.Axis(
                title=None,
                format="%b %Y",
                tickCount="month",
                orient="top",            # << top axis, as requested
                labelFontSize=12,
                tickSize=6
            ),
            scale=alt.Scale(domain=[domain_min, domain_max]),
        ),
        x2=alt.X2(f"{end_col}:T"),
        tooltip=[
            alt.Tooltip("title:N", title="Title"),
            alt.Tooltip("type_of_action:N", title="Type"),
            alt.Tooltip("budget_per_project_eur:Q", title="Budget (€)", format=",.0f"),
            alt.Tooltip("opening_date:T", title="Open", format="%d %b %Y"),
            alt.Tooltip(f"{end_col}:T", title="Close", format="%d %b %Y"),
        ],
    )

    # Start & End date labels on bars (short format)
    start_labels = base.mark_text(
        align="right", dx=-4, dy=-8, fontSize=11, color="#111"
    ).encode(
        x="opening_date:T",
        text=alt.Text("opening_date:T", format="%d %b"),
    )

    end_labels = base.mark_text(
        align="left", dx=4, dy=-8, fontSize=11, color="#111"
    ).encode(
        x=f"{end_col}:T",
        text=alt.Text(f"{end_col}:T", format="%d %b"),
    )

    # Zoom / pan on x
    zoom = alt.selection_interval(bind="scales", encodings=["x"])

    chart = (
        (month_bands + week_grid + month_grid + bars + start_labels + end_labels)
        .add_params(zoom)
        .properties(height=chart_height)
        .configure_axis(grid=False)
        .configure_view(strokeWidth=0)
    )
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

# Dates: compute overall bounds from any deadline
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

# Apply filters
df_kw = keyword_filter(df, keyword)
f = df_kw.copy()
if programmes: f = f[f["programme"].isin(programmes)]
if clusters:   f = f[f["cluster"].isin(clusters)]
if types:      f = f[f["type_of_action"].isin(types)]
if trls:
    trl_str = f["trl"].dropna().astype("Int64").astype(str)
    f = f[trl_str.isin(trls)]
if dests:      f = f[f["destination_or_strand"].isin(dests)]

# Date filters
f = f[(f["opening_date"] >= pd.to_datetime(open_start)) & (f["opening_date"] <= pd.to_datetime(open_end))]
if end_col in f.columns:
    f = f[(pd.to_datetime(f[end_col], errors="coerce") >= pd.to_datetime(dead_start)) &
          (pd.to_datetime(f[end_col], errors="coerce") <= pd.to_datetime(dead_end))]

# Budget filter
f = f[(f["budget_per_project_eur"].fillna(0) >= budget_range[0]) &
      (f["budget_per_project_eur"].fillna(0) <= budget_range[1])]

st.markdown(f"**Showing {len(f)} rows** after filters/search.")

# Tabs
tab1, tab2, tab3 = st.tabs(["📅 Gantt", "📋 Table", "📚 Full Data"])

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
