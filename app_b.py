# app_b_topfilters.py — Altair Gantt (filters on top)
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

def wrap_label(text: str, width=36, max_lines=3) -> str:
    s = str(text or "")
    parts = [s[i:i+width] for i in range(0, len(s), width)]
    return "\n".join(parts[:max_lines])

def safe_date_bounds(series, start_fb="2000-01-01", end_fb="2100-12-31"):
    s = pd.to_datetime(series, errors="coerce").dropna()
    if s.empty:
        return (pd.to_datetime(start_fb).date(), pd.to_datetime(end_fb).date())
    lo, hi = s.min().date(), s.max().date()
    if lo == hi:
        hi = (pd.to_datetime(hi) + pd.Timedelta(days=1)).date()
    return lo, hi

def build_month_bands(min_x: pd.Timestamp, max_x: pd.Timestamp) -> pd.DataFrame:
    start = pd.Timestamp(min_x).to_period("M").start_time
    end   = (pd.Timestamp(max_x).to_period("M") + 1).start_time
    months = pd.date_range(start, end, freq="MS")
    rows = []
    for i in range(len(months) - 1):
        rows.append({"start": months[i], "end": months[i+1], "band": i % 2})
    return pd.DataFrame(rows)

# -------- Multi-keyword search --------
def multi_keyword_filter(df: pd.DataFrame, terms: list[str], mode: str, title_code_only: bool) -> pd.DataFrame:
    terms = [t.strip().lower() for t in terms if t and t.strip()]
    if not terms:
        return df
    if title_code_only:
        hay = df[["title","code"]].astype(str).apply(lambda s: s.str.lower())
        masks = [hay.apply(lambda s: s.str.contains(t, na=False)).any(axis=1) for t in terms]
    else:
        lower_all = df.apply(lambda r: r.astype(str).str.lower(), axis=1)
        masks = [lower_all.apply(lambda s: s.str.contains(t, na=False)).any(axis=1) for t in terms]
    combined = masks[0]
    for m in masks[1:]:
        combined = (combined & m) if mode == "AND" else (combined | m)
    return df[combined]

# -------- Build long-form segments --------
def build_segments(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, r in df.iterrows():
        code = str(r.get("code") or "")
        title = str(r.get("title") or "")
        y_label = wrap_label(f"{code} — {title}", width=36, max_lines=3)

        prog = r.get("programme")
        open_dt   = r.get("opening_date")
        final_dt  = r.get("deadline")
        first_dt  = r.get("first_deadline")
        second_dt = r.get("second_deadline")
        two_stage = bool(r.get("two_stage"))
        title_inbar = wrap_label(title, width=26, max_lines=3)

        if two_stage:
            if pd.notna(open_dt) and pd.notna(first_dt) and open_dt <= first_dt:
                bar_days = (first_dt - open_dt).days
                rows.append({
                    "y_label": y_label, "programme": prog,
                    "start": open_dt, "end": first_dt,
                    "segment": "Stage 1",
                    "title": title, "title_inbar": title_inbar,
                    "budget_per_project_eur": r.get("budget_per_project_eur"),
                    "bar_days": bar_days,
                    "mid": open_dt + (first_dt - open_dt)/2,
                })
            segB_end = second_dt if pd.notna(second_dt) else (final_dt if pd.notna(final_dt) else None)
            if pd.notna(first_dt) and pd.notna(segB_end) and first_dt <= segB_end:
                bar_days = (segB_end - first_dt).days
                rows.append({
                    "y_label": y_label, "programme": prog,
                    "start": first_dt, "end": segB_end,
                    "segment": "Stage 2",
                    "title": title,
                    "budget_per_project_eur": r.get("budget_per_project_eur"),
                    "bar_days": bar_days,
                    "mid": first_dt + (segB_end - first_dt)/2,
                })
        else:
            if pd.notna(open_dt) and pd.notna(final_dt) and open_dt <= final_dt:
                bar_days = (final_dt - open_dt).days
                rows.append({
                    "y_label": y_label, "programme": prog,
                    "start": open_dt, "end": final_dt,
                    "segment": "Single",
                    "title": title, "title_inbar": title_inbar,
                    "budget_per_project_eur": r.get("budget_per_project_eur"),
                    "bar_days": bar_days,
                    "mid": open_dt + (final_dt - open_dt)/2,
                })
    seg = pd.DataFrame(rows)
    if seg.empty:
        return seg
    seg["earliest_end"] = seg.groupby("y_label")["end"].transform("min")
    seg = seg.sort_values(["earliest_end", "start"]).reset_index(drop=True)
    return seg

def build_altair_chart_from_segments(seg: pd.DataFrame, view_start, view_end):
    if seg.empty:
        return None
    y_order = seg["y_label"].drop_duplicates().tolist()
    unique_rows = len(y_order)
    row_height = 50
    chart_height = max(560, unique_rows * row_height)
    domain_min = pd.to_datetime(view_start)
    domain_max = pd.to_datetime(view_end)
    min_x = min(seg["start"].min(), seg["end"].min())
    max_x = max(seg["start"].max(), seg["end"].max())

    bands_df = build_month_bands(min_x, max_x)
    month_shade = (
        alt.Chart(bands_df)
        .mark_rect()
        .encode(
            x=alt.X("start:T", axis=None),
            x2=alt.X2("end:T"),
            opacity=alt.Opacity("band:Q", scale=alt.Scale(domain=[0,1], range=[0.0, 0.08]), legend=None),
            color=alt.value("#000"),
        )
    )
    months = pd.date_range(pd.Timestamp(min_x).to_period("M").start_time,
                           pd.Timestamp(max_x).to_period("M").end_time,
                           freq="MS")
    weeks = pd.date_range(pd.Timestamp(min_x).to_period("W-MON").start_time,
                          pd.Timestamp(max_x).to_period("W-MON").start_time,
                          freq="W-MON")
    month_grid = alt.Chart(pd.DataFrame({"t": months})).mark_rule(stroke="#9AA0A6", strokeWidth=1.5).encode(x="t:T")
    week_grid = alt.Chart(pd.DataFrame({"t": weeks})).mark_rule(stroke="#E5E7EB", strokeWidth=1).encode(x="t:T")

    base = alt.Chart(seg).encode(
        y=alt.Y("y_label:N", sort=y_order,
            axis=alt.Axis(title=None, labelLimit=8000, labelFontSize=14, labelAlign="left", labelPadding=8)),
        color=alt.Color("programme:N", legend=alt.Legend(title="Programme")),
    )
    bars = base.mark_bar(cornerRadius=3).encode(
        x=alt.X("start:T", axis=alt.Axis(title=None, format="%b %Y", tickCount="month",
                orient="top", labelFontSize=12, tickSize=6),
                scale=alt.Scale(domain=[domain_min, domain_max])),
        x2=alt.X2("end:T"),
        tooltip=[alt.Tooltip("title:N", title="Title"),
                 alt.Tooltip("programme:N", title="Programme"),
                 alt.Tooltip("budget_per_project_eur:Q", title="Budget (€)", format=",.0f"),
                 alt.Tooltip("start:T", title="Start", format="%d %b %Y"),
                 alt.Tooltip("end:T", title="End", format="%d %b %Y")]
    )
    start_labels = base.mark_text(align="right", dx=-4, dy=-8, fontSize=11, color="#111").encode(
        x="start:T", text=alt.Text("start:T", format="%d %b"))
    end_labels = base.mark_text(align="left", dx=4, dy=-8, fontSize=11, color="#111").encode(
        x="end:T", text=alt.Text("end:T", format="%d %b"))
    text_cond = alt.condition(alt.datum.bar_days >= 10, alt.value(1), alt.value(0))
    inbar = base.mark_text(align="center", baseline="middle", fontSize=12,
                           fill="white", stroke=None).encode(
        x=alt.X("mid:T", scale=alt.Scale(domain=[domain_min, domain_max]), axis=None),
        text=alt.Text("title_inbar:N"), opacity=text_cond
    )
    chart = (month_shade + week_grid + month_grid + bars + start_labels + end_labels + inbar)\
        .properties(height=chart_height).configure_axis(grid=False).configure_view(strokeWidth=0)
    return chart

# ---------- UI ----------
st.set_page_config(page_title="Calls Explorer — Gantt", layout="wide")
st.title("Calls Explorer — Gantt (two-stage + in-bar titles)")

upl = st.file_uploader("Upload parsed Excel (.xlsx)", type=["xlsx"])
if not upl:
    st.stop()

xls = pd.ExcelFile(upl)
sheet = st.selectbox("Sheet", xls.sheet_names, index=0)
raw = pd.read_excel(xls, sheet_name=sheet)
df = canonicalise(raw)

# ----- Filters FORM on top -----
with st.form("filters_form", clear_on_submit=False):
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

    st.subheader("Search (multi-keyword)")
    kw1 = st.text_input("Keyword 1")
    kw2 = st.text_input("Keyword 2")
    kw3 = st.text_input("Keyword 3")
    combine_mode = st.radio("Combine", ["AND", "OR"], horizontal=True, index=0)
    title_code_only = st.checkbox("Search only in Title & Code", value=True)

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

    bud_series = pd.to_numeric(df.get("budget_per_project_eur"), errors="coerce").dropna()
    if bud_series.empty:
        min_bud, max_bud = 0.0, 1_000_000.0
    else:
        min_bud, max_bud = float(bud_series.min()), float(bud_series.max())
        if not (min_bud < max_bud):
            min_bud, max_bud = max(min_bud, 0.0), min_bud + 100000.0
    budget_range = st.slider("Budget per project (EUR)", min_bud, max_bud, (min_bud, max_bud), step=100000.0)

    st.subheader("View window (persistent)")
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

    view_start = st.date_input("View from", value=st.session_state.view_start)
    view_end   = st.date_input("View to",   value=st.session_state.view_end)

    applied = st.form_submit_button("Apply filters")

# ---- Persist criteria ----
if "criteria" not in st.session_state:
    st.session_state.criteria = {}

if applied:
    st.session_state.criteria = dict(
        programmes=programmes, clusters=clusters, types=types, trls=trls, dests=dests,
        kw1=kw1, kw2=kw2, kw3=kw3, combine_mode=combine_mode, title_code_only=title_code_only,
        open_start=open_start, open_end=open_end, close_from=close_from, close_to=close_to,
        budget_range=budget_range, view_start=view_start, view_end=view_end
    )
    st.session_state.view_start = view_start
    st.session_state.view_end   = view_end

# ---- Defaults if first run ----
open_lo, open
