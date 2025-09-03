# app_b.py — Scatter Timeline (primary) + Legacy Gantt (optional)
# - Fast: cached transforms, vectorised search
# - Scatter timeline with categories: Opening / Stage 1 / Stage 2 / Final
# - Brush-to-zoom overview
# - Left padding control for roomy y-axis labels
# - Dense-mode simplifications for readability
from __future__ import annotations
import io, re
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

    # Precompute search haystacks (vectorised)
    title = df.get("title", pd.Series("", index=df.index)).astype(str)
    code  = df.get("code", pd.Series("", index=df.index)).astype(str)
    df["_search_title_code"] = (title + " " + code).str.lower()

    # Avoid giant strings if not needed: include a curated set for "all"
    # (modify as needed)
    cols_for_all = [
        "title","code","call_name","cluster","destination_or_strand",
        "type_of_action","programme","expected_outcome","scope","full_text"
    ]
    cols_for_all = [c for c in cols_for_all if c in df.columns]
    if cols_for_all:
        df["_search_all"] = df[cols_for_all].astype(str).agg(" ".join, axis=1).str.lower()
    else:
        df["_search_all"] = df["_search_title_code"]
    return df

def safe_date_bounds(series, start_fb="2000-01-01", end_fb="2100-12-31"):
    s = pd.to_datetime(series, errors="coerce").dropna()
    if s.empty:
        return (pd.to_datetime(start_fb).date(), pd.to_datetime(end_fb).date())
    lo, hi = s.min().date(), s.max().date()
    if lo == hi:
        hi = (pd.to_datetime(hi) + pd.Timedelta(days=1)).date()
    return lo, hi

def multi_keyword_filter_vectorised(df: pd.DataFrame, terms: list[str], mode: str, title_code_only: bool) -> pd.DataFrame:
    terms = [t.strip().lower() for t in terms if t and t.strip()]
    if not terms:
        return df
    hay = df["_search_title_code"] if title_code_only else df["_search_all"]
    if mode == "AND":
        for t in terms:
            df = df[hay.str.contains(re.escape(t), na=False)]
        return df
    # OR
    rx = "|".join(map(re.escape, terms))
    return df[hay.str.contains(rx, na=False)]

def build_events(df: pd.DataFrame) -> pd.DataFrame:
    """Flatten rows into per-date events suitable for scatter timeline."""
    events = []
    for _, r in df.iterrows():
        base = {
            "code": r.get("code"),
            "title": r.get("title"),
            "programme": r.get("programme"),
            "cluster": r.get("cluster"),
            "destination_or_strand": r.get("destination_or_strand"),
            "type_of_action": r.get("type_of_action"),
            "trl": r.get("trl"),
            "budget_per_project_eur": r.get("budget_per_project_eur"),
        }
        if pd.notna(r.get("opening_date")):
            events.append({**base, "date": r["opening_date"], "kind": "Opening"})
        # Preserve all deadline kinds if present
        if pd.notna(r.get("first_deadline")):
            events.append({**base, "date": r["first_deadline"], "kind": "Stage 1"})
        if pd.notna(r.get("second_deadline")):
            events.append({**base, "date": r["second_deadline"], "kind": "Stage 2"})
        if pd.notna(r.get("deadline")):
            events.append({**base, "date": r["deadline"], "kind": "Final"})
    ev = pd.DataFrame(events)
    if not ev.empty:
        ev = ev.sort_values("date").reset_index(drop=True)
        # Helpful categorical order for y
        ev["kind"] = pd.Categorical(ev["kind"], categories=["Opening","Stage 1","Stage 2","Final"], ordered=True)
    return ev

def build_month_overview(ev: pd.DataFrame) -> pd.DataFrame:
    """Aggregate events by month for the overview histogram."""
    if ev.empty:
        return ev
    out = ev.copy()
    out["month"] = out["date"].dt.to_period("M").dt.to_timestamp()
    return out.groupby("month", as_index=False).size()

# ---------- Caching wrappers ----------
@st.cache_data(show_spinner=False)
def load_and_canonicalise(upl_bytes: bytes, sheet: str) -> pd.DataFrame:
    xls = pd.ExcelFile(io.BytesIO(upl_bytes))
    raw = pd.read_excel(xls, sheet_name=sheet)
    return canonicalise(raw)

@st.cache_data(show_spinner=False)
def filter_frame(df: pd.DataFrame, criteria: dict) -> pd.DataFrame:
    f = df.copy()
    f = multi_keyword_filter_vectorised(
        f, [criteria["kw1"], criteria["kw2"], criteria["kw3"]],
        criteria["combine_mode"], criteria["title_code_only"]
    )
    if criteria["programmes"]: f = f[f["programme"].isin(criteria["programmes"])]
    if criteria["clusters"]:   f = f[f["cluster"].isin(criteria["clusters"])]
    if criteria["types"]:      f = f[f["type_of_action"].isin(criteria["types"])]
    if criteria["trls"]:
        trl_str = f["trl"].dropna().astype("Int64").astype(str)
        f = f[trl_str.isin(criteria["trls"])]
    if criteria["dests"]:      f = f[f["destination_or_strand"].isin(criteria["dests"])]

    f = f[(f["opening_date"] >= pd.to_datetime(criteria["open_start"])) &
          (f["opening_date"] <= pd.to_datetime(criteria["open_end"]))]

    any_end_in = (
        (pd.to_datetime(f.get("deadline"), errors="coerce").between(pd.to_datetime(criteria["close_from"]), pd.to_datetime(criteria["close_to"]), inclusive="both")) |
        (pd.to_datetime(f.get("first_deadline"), errors="coerce").between(pd.to_datetime(criteria["close_from"]), pd.to_datetime(criteria["close_to"]), inclusive="both")) |
        (pd.to_datetime(f.get("second_deadline"), errors="coerce").between(pd.to_datetime(criteria["close_from"]), pd.to_datetime(criteria["close_to"]), inclusive="both"))
    )
    f = f[any_end_in.fillna(False)]

    f = f[(f["budget_per_project_eur"].fillna(0) >= criteria["budget_range"][0]) &
          (f["budget_per_project_eur"].fillna(0) <= criteria["budget_range"][1])]
    return f

@st.cache_data(show_spinner=False)
def events_for_frame(f: pd.DataFrame) -> pd.DataFrame:
    return build_events(f)

# ---------- Altair builders ----------
def scatter_timeline(ev: pd.DataFrame, view_start, view_end, point_size: int, left_pad: int, dense_threshold: int = 120):
    if ev.empty:
        return None

    # Dense mode when there are many visible points
    dense = len(ev) > 3_000  # tweak as you wish
    opacity = 0.9 if not dense else 0.6
    size = point_size if not dense else max(24, int(point_size * 0.7))

    brush = alt.selection_interval(encodings=['x'])

    base = alt.Chart(ev).transform_filter(
        (alt.datum.date >= pd.to_datetime(str(view_start))) &
        (alt.datum.date <= pd.to_datetime(str(view_end)))
    )

    points = base.mark_point(filled=True, size=size).encode(
        x=alt.X("date:T",
                axis=alt.Axis(title=None, format="%b %Y", tickCount="month", labelFontSize=12),
                scale=alt.Scale(domain=[pd.to_datetime(str(view_start)), pd.to_datetime(str(view_end))])),
        y=alt.Y("kind:N",
                sort=["Opening","Stage 1","Stage 2","Final"],
                axis=alt.Axis(title=None, labelFontSize=14, labelPadding=8, labelLimit=2000)),
        color=alt.Color("programme:N", legend=alt.Legend(title="Programme")),
        tooltip=[
            alt.Tooltip("title:N", title="Title"),
            alt.Tooltip("code:N", title="Code"),
            alt.Tooltip("programme:N", title="Programme"),
            alt.Tooltip("cluster:N", title="Cluster"),
            alt.Tooltip("destination_or_strand:N", title="Destination/Strand"),
            alt.Tooltip("type_of_action:N", title="Type"),
            alt.Tooltip("trl:Q", title="TRL"),
            alt.Tooltip("budget_per_project_eur:Q", title="Budget (€)", format=",.0f"),
            alt.Tooltip("kind:N", title="Event"),
            alt.Tooltip("date:T", title="Date", format="%d %b %Y"),
        ],
        opacity=alt.value(opacity)
    ).add_params(brush)

    # Overview: monthly histogram of event counts with brush
    ov_src = build_month_overview(ev)
    overview = alt.Chart(ov_src).mark_area(opacity=0.25).encode(
        x=alt.X("month:T", axis=alt.Axis(title=None, format="%b %Y")),
        y=alt.Y("size:Q", axis=alt.Axis(title="Events/month"))
    ).properties(height=64).add_params(brush)

    # Apply generous left padding for y labels
    combined = (points & overview).properties(padding={"left": left_pad, "right": 12, "top": 8, "bottom": 8}) \
        .configure_view(strokeWidth=0)

    return combined

# ---------- Legacy: Gantt (from your original) ----------
def wrap_label(text: str, width=36, max_lines=3) -> str:
    s = str(text or "")
    parts = [s[i:i+width] for i in range(0, len(s), width)]
    return "\n".join(parts[:max_lines])

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

    # (Trimmed grid/shading for speed)
    months = pd.date_range(pd.Timestamp(min_x).to_period("M").start_time,
                           pd.Timestamp(max_x).to_period("M").end_time,
                           freq="MS")
    month_grid = (
        alt.Chart(pd.DataFrame({"t": months}))
        .mark_rule(stroke="#9AA0A6", strokeWidth=1.0)
        .encode(x="t:T")
    )

    base = alt.Chart(seg).encode(
        y=alt.Y("y_label:N",
                sort=y_order,
                axis=alt.Axis(title=None, labelLimit=8000, labelFontSize=14, labelAlign="left", labelPadding=8)),
        color=alt.Color("programme:N", legend=alt.Legend(title="Programme")),
    )

    bars = base.mark_bar(cornerRadius=3).encode(
        x=alt.X("start:T",
                axis=alt.Axis(title=None, format="%b %Y", tickCount="month", orient="top", labelFontSize=12, tickSize=6),
                scale=alt.Scale(domain=[domain_min, domain_max])),
        x2=alt.X2("end:T"),
        tooltip=[
            alt.Tooltip("title:N", title="Title"),
            alt.Tooltip("programme:N", title="Programme"),
            alt.Tooltip("budget_per_project_eur:Q", title="Budget (€)", format=",.0f"),
            alt.Tooltip("start:T", title="Start", format="%d %b %Y"),
            alt.Tooltip("end:T",   title="End",   format="%d %b %Y"),
        ],
    )

    chart = (month_grid + bars).properties(height=chart_height).configure_axis(grid=False).configure_view(strokeWidth=0)
    return chart

# ---------- UI ----------
st.set_page_config(page_title="Calls Explorer — Timeline Scatter", layout="wide")
st.title("Calls Explorer — Timeline Scatter")

upl = st.file_uploader("Upload parsed Excel (.xlsx)", type=["xlsx"])
if not upl:
    st.stop()

upl_bytes = upl.read()
xls = pd.ExcelFile(io.BytesIO(upl_bytes))
sheet = st.selectbox("Sheet", xls.sheet_names, index=0)
df = load_and_canonicalise(upl_bytes, sheet)

# ----- Sidebar: APPLY form -----
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

    st.subheader("Search (multi-keyword)")
    kw1 = st.text_input("Keyword 1")
    kw2 = st.text_input("Keyword 2")
    kw3 = st.text_input("Keyword 3")
    combine_mode = st.radio("Combine", ["AND", "OR"], horizontal=True, index=0)
    title_code_only = st.checkbox("Search only in Title & Code", value=True)

    # Date bounds for defaults
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

    # Budget slider
    bud_series = pd.to_numeric(df.get("budget_per_project_eur"), errors="coerce").dropna()
    if bud_series.empty:
        min_bud, max_bud = 0.0, 1_000_000.0
    else:
        min_bud, max_bud = float(bud_series.min()), float(bud_series.max())
        if not (min_bud < max_bud):
            min_bud, max_bud = max(min_bud, 0.0), min_bud + 100000.0
    budget_range = st.slider("Budget per project (EUR)", min_bud, max_bud, (min_bud, max_bud), step=100000.0)

    # Persistent view window with sensible default (~ past 2 months → next 9 months)
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
        pad_left  = pd.Timedelta(days=60)
        pad_right = pd.Timedelta(days=270)
        st.session_state.view_start = (pd.Timestamp.today().normalize() - pad_left).date() if pd.notna(data_min) else open_lo
        st.session_state.view_end   = (pd.Timestamp.today().normalize() + pad_right).date() if pd.notna(data_max) else open_hi

    view_start = st.date_input("View from", value=st.session_state.view_start)
    view_end   = st.date_input("View to",   value=st.session_state.view_end)

    st.subheader("Chart display")
    left_pad = st.slider("Left margin for y-axis labels (px)", min_value=80, max_value=360, value=220, step=10)
    point_sz = st.slider("Point size", min_value=24, max_value=180, value=60, step=6)

    applied = st.form_submit_button("Apply filters")

# Persist criteria on Apply
if "criteria" not in st.session_state:
    st.session_state.criteria = {}

if applied:
    st.session_state.criteria = dict(
        programmes=programmes, clusters=clusters, types=types, trls=trls, dests=dests,
        kw1=kw1, kw2=kw2, kw3=kw3, combine_mode=combine_mode, title_code_only=title_code_only,
        open_start=open_start, open_end=open_end, close_from=close_from, close_to=close_to,
        budget_range=budget_range, view_start=view_start, view_end=view_end,
        left_pad=left_pad, point_sz=point_sz
    )
    st.session_state.view_start = view_start
    st.session_state.view_end   = view_end

# Defaults before first Apply
open_lo, open_hi = safe_date_bounds(df.get("opening_date"))
dead_all = pd.concat([
    pd.to_datetime(df.get("deadline"), errors="coerce"),
    pd.to_datetime(df.get("first_deadline"), errors="coerce"),
    pd.to_datetime(df.get("second_deadline"), errors="coerce"),
], axis=0)
dead_lo, dead_hi = safe_date_bounds(dead_all)

if not st.session_state.criteria:
    st.session_state.criteria = dict(
        programmes=sorted(df["programme"].dropna().unique().tolist()),
        clusters=[], types=[], trls=[], dests=[],
        kw1="", kw2="", kw3="", combine_mode="AND", title_code_only=True,
        open_start=open_lo, open_end=open_hi, close_from=dead_lo, close_to=dead_hi,
        budget_range=(0.0, 1_000_000.0),
        view_start=st.session_state.view_start, view_end=st.session_state.view_end,
        left_pad=220, point_sz=60
    )

crit = st.session_state.criteria

# ---- Apply filters (cached) ----
f = filter_frame(df, crit)

st.markdown(f"**Showing {len(f)} rows** after last applied filters.")

# Tabs: Scatter (primary), Table, Full Data, Legacy Gantt
tab1, tab2, tab3, tab4 = st.tabs(["📍 Scatter Timeline", "📋 Table", "📚 Full Data", "🧰 Legacy Gantt"])

with tab1:
    st.subheader("Timeline of Openings & Deadlines")
    ev = events_for_frame(f)
    if ev.empty:
        st.info("No events to display.")
    else:
        chart = scatter_timeline(
            ev,
            view_start=crit["view_start"],
            view_end=crit["view_end"],
            point_size=int(crit["point_sz"]),
            left_pad=int(crit["left_pad"]),
        )
        st.altair_chart(chart, use_container_width=True)

with tab2:
    st.subheader("Filtered table")
    show_cols = [c for c in DISPLAY_COLS if c in f.columns]
    st.dataframe(f[show_cols], use_container_width=True, hide_index=True)
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as xw:
        f.to_excel(xw, index=False, sheet_name="filtered")
    out.seek(0)
    st.download_button(
        "⬇️ Download filtered (Excel)", out,
        file_name="calls_filtered.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

with tab3:
    st.subheader("Full data (expand rows)")
    for _, row in f.iterrows():
        title = f"{row.get('code','')} — {row.get('title','')}"
        with st.expander(title):
            st.write(row.to_dict())

with tab4:
    st.subheader("Legacy Gantt (for focused subsets)")
    seg = build_segments(f)
    g_chart = build_altair_chart_from_segments(seg, view_start=crit["view_start"], view_end=crit["view_end"])
    if g_chart is None:
        st.info("No rows with valid dates to display.")
    else:
        st.altair_chart(g_chart, use_container_width=True)
