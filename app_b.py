# app_b.py — Altair Gantt with two-stage dual bars, multi-keyword search, bigger wrapped labels
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
    # Trim + map headers
    df.columns = [c.strip() for c in df.columns]
    for src, dst in COLUMN_MAP.items():
        if src in df.columns and dst not in df.columns:
            df = df.rename(columns={src: dst})
    # Normalise leftovers
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

def wrap_label(text: str, width=38, max_lines=3) -> str:
    """Hard-wrap every `width` chars, cap to `max_lines`."""
    s = str(text or "")
    parts = [s[i:i+width] for i in range(0, len(s), width)]
    parts = parts[:max_lines]
    return "\n".join(parts)

def safe_date_bounds(series, start_fb="2000-01-01", end_fb="2100-12-31"):
    s = pd.to_datetime(series, errors="coerce").dropna()
    if s.empty:
        return (pd.to_datetime(start_fb).date(), pd.to_datetime(end_fb).date())
    lo, hi = s.min().date(), s.max().date()
    if lo == hi:
        hi = (pd.to_datetime(hi) + pd.Timedelta(days=1)).date()
    return lo, hi

def build_month_bands(min_x: pd.Timestamp, max_x: pd.Timestamp) -> pd.DataFrame:
    """Create alternating month spans for background shading."""
    start = pd.Timestamp(min_x).to_period("M").start_time
    end   = (pd.Timestamp(max_x).to_period("M") + 1).start_time
    months = pd.date_range(start, end, freq="MS")
    rows = []
    for i in range(len(months) - 1):
        rows.append({"start": months[i], "end": months[i+1], "band": i % 2})
    return pd.DataFrame(rows)

# -------- Multi-keyword search (3 fields, AND/OR) --------
def multi_keyword_filter(df: pd.DataFrame, terms: list[str], mode: str, title_code_only: bool) -> pd.DataFrame:
    terms = [t.strip().lower() for t in terms if t and t.strip()]
    if not terms:
        return df

    if title_code_only:
        # Search only in title + code
        hay = df[["title","code"]].astype(str).apply(lambda s: s.str.lower())
        masks = []
        for t in terms:
            masks.append(hay.apply(lambda s: s.str.contains(t, na=False)).any(axis=1))
    else:
        # Search across all columns (stringified)
        lower_all = df.apply(lambda r: r.astype(str).str.lower(), axis=1)
        masks = []
        for t in terms:
            masks.append(lower_all.apply(lambda s: s.str.contains(t, na=False)).any(axis=1))

    if mode == "AND":
        combined = masks[0]
        for m in masks[1:]:
            combined = combined & m
    else:  # "OR"
        combined = masks[0]
        for m in masks[1:]:
            combined = combined | m

    return df[combined]

# -------- Build long-form segments (one or two bars per row) --------
def build_segments(df: pd.DataFrame) -> pd.DataFrame:
    """
    Returns a long-form DataFrame with (start, end, y_label, segment_type, etc.)
    - Single-stage: one segment Opening -> Deadline
    - Two-stage: two segments: Opening -> First, First -> Second (if present)
    Rows lacking necessary dates are skipped.
    """
    rows = []
    for _, r in df.iterrows():
        code = str(r.get("code") or "")
        title = str(r.get("title") or "")
        y_label = wrap_label(f"{code} — {title}", width=38, max_lines=3)

        prog = r.get("programme")
        open_dt = r.get("opening_date")
        final_dt = r.get("deadline")
        first_dt = r.get("first_deadline")
        second_dt = r.get("second_deadline")
        two_stage = bool(r.get("two_stage"))

        # Two-stage → two segments if dates exist
        if two_stage:
            # Segment A: Opening -> First
            if pd.notna(open_dt) and pd.notna(first_dt) and open_dt <= first_dt:
                rows.append({
                    "y_label": y_label,
                    "programme": prog,
                    "start": open_dt,
                    "end": first_dt,
                    "segment": "Stage 1",
                    "title": title,
                    "type_of_action": r.get("type_of_action"),
                    "budget_per_project_eur": r.get("budget_per_project_eur"),
                })
            # Segment B: First -> Second (fallback to Final if second missing but final exists and after first)
            segB_end = second_dt if pd.notna(second_dt) else (final_dt if pd.notna(final_dt) else None)
            if pd.notna(first_dt) and pd.notna(segB_end) and first_dt <= segB_end:
                rows.append({
                    "y_label": y_label,
                    "programme": prog,
                    "start": first_dt,
                    "end": segB_end,
                    "segment": "Stage 2",
                    "title": title,
                    "type_of_action": r.get("type_of_action"),
                    "budget_per_project_eur": r.get("budget_per_project_eur"),
                })
        else:
            # Single-stage → Opening -> Final
            if pd.notna(open_dt) and pd.notna(final_dt) and open_dt <= final_dt:
                rows.append({
                    "y_label": y_label,
                    "programme": prog,
                    "start": open_dt,
                    "end": final_dt,
                    "segment": "Single",
                    "title": title,
                    "type_of_action": r.get("type_of_action"),
                    "budget_per_project_eur": r.get("budget_per_project_eur"),
                })

    seg = pd.DataFrame(rows)
    if seg.empty:
        return seg

    # Compute sorting key using earliest deadline across the original df row;
    # since we expanded rows, approximate with segment end (min per y_label)
    seg["earliest_end"] = seg.groupby("y_label")["end"].transform("min")
    # Order rows by earliest_end, then by start
    seg = seg.sort_values(["earliest_end", "start"]).reset_index(drop=True)
    return seg

def build_altair_chart_from_segments(seg: pd.DataFrame, view_start, view_end):
    if seg.empty:
        return None

    # Stable y order
    y_order = seg["y_label"].drop_duplicates().tolist()

    # Chart height proportional to unique y rows
    unique_rows = len(y_order)
    row_height = 46  # more room for wrapped labels, bigger font
    chart_height = max(520, unique_rows * row_height)

    # Calendar domain from persistent view window
    domain_min = pd.to_datetime(view_start)
    domain_max = pd.to_datetime(view_end)

    # Background monthly shading based on actual data span
    min_x = min(seg["start"].min(), seg["end"].min())
    max_x = max(seg["start"].max(), seg["end"].max())
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

    # Base encodings (bigger label font; prevent dropping)
    base = alt.Chart(seg).encode(
        y=alt.Y(
            "y_label:N",
            sort=y_order,
            axis=alt.Axis(title=None, labelLimit=4000, labelFontSize=13)  # bigger
        ),
        color=alt.Color("programme:N", legend=None),
    )

    # Bars — use segment to vary opacity so Stage 1 / Stage 2 are distinguishable
    bars = base.mark_bar(cornerRadius=3).encode(
        x=alt.X(
            "start:T",
            axis=alt.Axis(
                title=None,
                format="%b %Y",
                tickCount="month",
                orient="top",
                labelFontSize=12,
                tickSize=6
            ),
            scale=alt.Scale(domain=[domain_min, domain_max]),
        ),
        x2=alt.X2("end:T"),
        opacity=alt.Opacity(
            "segment:N",
            scale=alt.Scale(domain=["Single", "Stage 1", "Stage 2"], range=[1.0, 0.95, 0.75]),
            legend=None
        ),
        tooltip=[
            alt.Tooltip("title:N", title="Title"),
            alt.Tooltip("segment:N", title="Segment"),
            alt.Tooltip("type_of_action:N", title="Type"),
            alt.Tooltip("budget_per_project_eur:Q", title="Budget (€)", format=",.0f"),
            alt.Tooltip("start:T", title="Start", format="%d %b %Y"),
            alt.Tooltip("end:T", title="End",   format="%d %b %Y"),
        ],
    )

    # Start/End date labels per segment
    start_labels = base.mark_text(
        align="right", dx=-4, dy=-8, fontSize=11, color="#111"
    ).encode(
        x="start:T",
        text=alt.Text("start:T", format="%d %b"),
    )
    end_labels = base.mark_text(
        align="left", dx=4, dy=-8, fontSize=11, color="#111"
    ).encode(
        x="end:T",
        text=alt.Text("end:T", format="%d %b"),
    )

    chart = (
        (month_bands + week_grid + month_grid + bars + start_labels + end_labels)
        .properties(height=chart_height)
        .configure_axis(grid=False)
        .configure_view(strokeWidth=0)
    )
    return chart

# ---------- UI ----------
st.set_page_config(page_title="Calls Explorer (Altair)", layout="wide")
st.title("Calls Explorer — Gantt with two-stage segments")

# Upload
upl = st.file_uploader("Upload parsed Excel (.xlsx)", type=["xlsx"])
if not upl:
    st.stop()

# Read
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

# Multi-keyword search (Title/Code only or all columns)
st.sidebar.header("Search")
kw1 = st.sidebar.text_input("Keyword 1")
kw2 = st.sidebar.text_input("Keyword 2")
kw3 = st.sidebar.text_input("Keyword 3")
combine_mode = st.sidebar.radio("Combine", ["AND", "OR"], horizontal=True, index=0)
title_code_only = st.sidebar.checkbox("Search only in Title & Code", value=True)

# Dates: compute overall bounds (for view window defaults)
open_lo, open_hi = safe_date_bounds(df.get("opening_date"))
dead_all = pd.concat([
    pd.to_datetime(df.get("deadline"), errors="coerce"),
    pd.to_datetime(df.get("first_deadline"), errors="coerce"),
    pd.to_datetime(df.get("second_deadline"), errors="coerce"),
], axis=0)
dead_lo, dead_hi = safe_date_bounds(dead_all)

# Filter date windows for data inclusion (not the persistent view)
c1, c2 = st.sidebar.columns(2)
with c1:
    open_start = st.date_input("Open from", value=open_lo, min_value=open_lo, max_value=open_hi)
with c2:
    open_end   = st.date_input("Open to",   value=open_hi, min_value=open_lo, max_value=open_hi)

# For two-stage we still want an overall "close" window, so use min(end across segments)
c3, c4 = st.sidebar.columns(2)
with c3:
    close_from = st.date_input("Close from", value=dead_lo, min_value=dead_lo, max_value=dead_hi)
with c4:
    close_to   = st.date_input("Close to",   value=dead_hi, min_value=dead_lo, max_value=dead_hi)

# Budget slider
bud_series = pd.to_numeric(df.get("budget_per_project_eur"), errors="coerce").dropna()
if bud_series.empty:
    min_bud, max_bud = 0.0, 1_000_000.0
else:
    min_bud, max_bud = float(bud_series.min()), float(bud_series.max())
    if not (min_bud < max_bud):
        min_bud, max_bud = max(min_bud, 0.0), min_bud + 100000.0
budget_range = st.sidebar.slider("Budget per project (EUR)", min_bud, max_bud, (min_bud, max_bud), step=100000.0)

# Persistent VIEW WINDOW (prevents reset on reruns/tab switches)
st.sidebar.header("View window (persistent)")
if "view_start" not in st.session_state or "view_end" not in st.session_state:
    data_min = min(
        pd.to_datetime(df.get("opening_date"), errors="coerce").min(),
        pd.to_datetime(df.get("deadline"), errors="coerce").min(),
        pd.to_datetime(df.get("first_deadline"), errors="coerce").min(),
        pd.to_datetime(df.get("second_deadline"), errors="coerce").min(),
    )
    data_max = max(
        pd.to_datetime(df.get("opening_date"), errors="coerce").max(),
        pd.to_datetime(df.get("deadline"), errors="coerce").max(),
        pd.to_datetime(df.get("first_deadline"), errors="coerce").max(),
        pd.to_datetime(df.get("second_deadline"), errors="coerce").max(),
    )
    pad = pd.Timedelta(days=30)
    st.session_state.view_start = (data_min - pad).date() if pd.notna(data_min) else open_lo
    st.session_state.view_end   = (data_max + pad).date() if pd.notna(data_max) else open_hi

view_start = st.sidebar.date_input("View from", value=st.session_state.view_start)
view_end   = st.sidebar.date_input("View to",   value=st.session_state.view_end)
st.session_state.view_start = view_start
st.session_state.view_end   = view_end

# ---- Apply filters ----
f = df.copy()

# Multi-keyword first (so downstream filters act on a smaller set)
f = multi_keyword_filter(f, [kw1, kw2, kw3], combine_mode, title_code_only)

if programmes: f = f[f["programme"].isin(programmes)]
if clusters:   f = f[f["cluster"].isin(clusters)]
if types:      f = f[f["type_of_action"].isin(types)]
if trls:
    trl_str = f["trl"].dropna().astype("Int64").astype(str)
    f = f[trl_str.isin(trls)]
if dests:      f = f[f["destination_or_strand"].isin(dests)]

# Opening window filter
f = f[(f["opening_date"] >= pd.to_datetime(open_start)) & (f["opening_date"] <= pd.to_datetime(open_end))]

# Close window filter: keep rows where ANY potential end (final/first/second) falls in range
any_end_in = (
    (pd.to_datetime(f.get("deadline"), errors="coerce")       .between(pd.to_datetime(close_from), pd.to_datetime(close_to), inclusive="both")) |
    (pd.to_datetime(f.get("first_deadline"), errors="coerce") .between(pd.to_datetime(close_from), pd.to_datetime(close_to), inclusive="both")) |
    (pd.to_datetime(f.get("second_deadline"), errors="coerce").between(pd.to_datetime(close_from), pd.to_datetime(close_to), inclusive="both"))
)
f = f[any_end_in.fillna(False)]

# Budget filter
f = f[(f["budget_per_project_eur"].fillna(0) >= budget_range[0]) &
      (f["budget_per_project_eur"].fillna(0) <= budget_range[1])]

st.markdown(f"**Showing {len(f)} rows** after filters/search.")

# Tabs
tab1, tab2, tab3 = st.tabs(["📅 Gantt", "📋 Table", "📚 Full Data"])

with tab1:
    st.subheader("Gantt (Opening → Stage 1 → Stage 2 / Final)")
    segments = build_segments(f)
    chart = build_altair_chart_from_segments(segments, view_start=view_start, view_end=view_end)
    if chart is None:
        st.info("No rows with valid dates to display.")
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
