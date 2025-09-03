# app_b.py — ECharts Gantt (scalable) + existing filters/table/downloads
from __future__ import annotations
import io
import math
from datetime import datetime
import pandas as pd
import streamlit as st
from streamlit_echarts import st_echarts, JsCode

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

def wrap_label(text: str, width=36, max_lines=3) -> str:
    """Hard-wrap to `width` chars per line, up to `max_lines`."""
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

# -------- Multi-keyword search (3 fields, AND/OR, Title/Code only or All) --------
def multi_keyword_filter(df: pd.DataFrame, terms: list[str], mode: str, title_code_only: bool) -> pd.DataFrame:
    terms = [t.strip().lower() for t in terms if t and t.strip()]
    if not terms:
        return df
    if title_code_only and set(["title","code"]).issubset(df.columns):
        hay = df[["title","code"]].astype(str).apply(lambda s: s.str.lower())
        masks = [hay.apply(lambda s: s.str.contains(t, na=False)).any(axis=1) for t in terms]
    else:
        lower_all = df.apply(lambda r: r.astype(str).str.lower(), axis=1)
        masks = [lower_all.apply(lambda s: s.str.contains(t, na=False)).any(axis=1) for t in terms]
    combined = masks[0]
    for m in masks[1:]:
        combined = (combined & m) if mode == "AND" else (combined | m)
    return df[combined]

# -------- Build long-form segments (one or two bars per row) --------
def build_segments(df: pd.DataFrame) -> pd.DataFrame:
    """
    Returns DataFrame with columns:
      y_title (wrapped title for left axis),
      code, programme, start, end, segment, title, budget_per_project_eur
    Two-stage rows -> two segments; single-stage -> one segment.
    """
    rows = []
    for _, r in df.iterrows():
        code = str(r.get("code") or "")
        title = str(r.get("title") or "")
        y_title = wrap_label(title, width=36, max_lines=3)  # axis uses TITLE only
        prog = r.get("programme")
        open_dt   = r.get("opening_date")
        final_dt  = r.get("deadline")
        first_dt  = r.get("first_deadline")
        second_dt = r.get("second_deadline")
        two_stage = bool(r.get("two_stage"))

        if two_stage:
            if pd.notna(open_dt) and pd.notna(first_dt) and open_dt <= first_dt:
                rows.append({
                    "y_title": y_title, "code": code, "programme": prog,
                    "start": open_dt, "end": first_dt, "segment": "Stage 1",
                    "title": title, "budget_per_project_eur": r.get("budget_per_project_eur"),
                })
            segB_end = second_dt if pd.notna(second_dt) else (final_dt if pd.notna(final_dt) else None)
            if pd.notna(first_dt) and pd.notna(segB_end) and first_dt <= segB_end:
                rows.append({
                    "y_title": y_title, "code": code, "programme": prog,
                    "start": first_dt, "end": segB_end, "segment": "Stage 2",
                    "title": title, "budget_per_project_eur": r.get("budget_per_project_eur"),
                })
        else:
            if pd.notna(open_dt) and pd.notna(final_dt) and open_dt <= final_dt:
                rows.append({
                    "y_title": y_title, "code": code, "programme": prog,
                    "start": open_dt, "end": final_dt, "segment": "Single",
                    "title": title, "budget_per_project_eur": r.get("budget_per_project_eur"),
                })

    seg = pd.DataFrame(rows)
    if seg.empty:
        return seg

    # Sort rows by earliest end per y_title, then by start
    seg["earliest_end"] = seg.groupby("y_title")["end"].transform("min")
    seg = seg.sort_values(["earliest_end", "start"]).reset_index(drop=True)
    return seg

def dt_to_ms(x):
    """pandas.Timestamp/datetime/date -> JS milliseconds since epoch."""
    if pd.isna(x):
        return None
    if isinstance(x, pd.Timestamp):
        ts = x.to_pydatetime()
    elif isinstance(x, datetime):
        ts = x
    else:
        ts = pd.to_datetime(x).to_pydatetime()
    return int(ts.timestamp() * 1000)

# ---------- UI ----------
st.set_page_config(page_title="Calls Explorer — Gantt (ECharts)", layout="wide")
st.title("Calls Explorer — Gantt (ECharts)")

# Upload
upl = st.file_uploader("Upload parsed Excel (.xlsx)", type=["xlsx"])
if not upl:
    st.stop()

# Read
xls = pd.ExcelFile(upl)
sheet = st.selectbox("Sheet", xls.sheet_names, index=0)
raw = pd.read_excel(xls, sheet_name=sheet)
df = canonicalise(raw)

# ----- Sidebar: APPLY form (so chart updates only on Apply) -----
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

    # Persistent view window (x-axis)
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

# Persist criteria on Apply
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
        view_start=st.session_state.view_start, view_end=st.session_state.view_end
    )

crit = st.session_state.criteria

# ---- Apply filters after Apply ----
f = df.copy()
f = multi_keyword_filter(f, [crit["kw1"], crit["kw2"], crit["kw3"]], crit["combine_mode"], crit["title_code_only"])
if crit["programmes"]: f = f[f["programme"].isin(crit["programmes"])]
if crit["clusters"]:   f = f[f["cluster"].isin(crit["clusters"])]
if crit["types"]:      f = f[f["type_of_action"].isin(crit["types"])]
if crit["trls"]:
    trl_str = f["trl"].dropna().astype("Int64").astype(str)
    f = f[trl_str.isin(crit["trls"])]
if crit["dests"]:      f = f[f["destination_or_strand"].isin(crit["dests"])]

f = f[(f["opening_date"] >= pd.to_datetime(crit["open_start"])) &
      (f["opening_date"] <= pd.to_datetime(crit["open_end"]))]

any_end_in = (
    (pd.to_datetime(f.get("deadline"), errors="coerce").between(pd.to_datetime(crit["close_from"]), pd.to_datetime(crit["close_to"]), inclusive="both")) |
    (pd.to_datetime(f.get("first_deadline"), errors="coerce").between(pd.to_datetime(crit["close_from"]), pd.to_datetime(crit["close_to"]), inclusive="both")) |
    (pd.to_datetime(f.get("second_deadline"), errors="coerce").between(pd.to_datetime(crit["close_from"]), pd.to_datetime(crit["close_to"]), inclusive="both"))
)
f = f[any_end_in.fillna(False)]

f = f[(f["budget_per_project_eur"].fillna(0) >= crit["budget_range"][0]) &
      (f["budget_per_project_eur"].fillna(0) <= crit["budget_range"][1])]

st.markdown(f"**Showing {len(f)} rows** after last applied filters.")

# ---------- Gantt (ECharts) ----------
tab1, tab2, tab3 = st.tabs(["📅 Gantt", "📋 Table", "📚 Full Data"])

with tab1:
    st.subheader("Gantt (Opening → Stage 1 → Stage 2 / Final)")
    seg = build_segments(f)
    if seg.empty:
        st.info("No rows with valid dates to display.")
    else:
        # Build y-axis categories (stable order)
        y_categories = seg["y_title"].drop_duplicates().tolist()
        y_index = {v: i for i, v in enumerate(y_categories)}

        # Build series data for custom renderItem
        # Each item: [start_ms, end_ms, yIndex, code, title, segment, programme]
        series_data = []
        for _, r in seg.iterrows():
            series_data.append([
                dt_to_ms(r["start"]),
                dt_to_ms(r["end"]),
                y_index[r["y_title"]],
                r.get("code") or "",
                r.get("title") or "",
                r.get("segment") or "",
                r.get("programme") or "",
                r.get("budget_per_project_eur") if pd.notna(r.get("budget_per_project_eur")) else None,
            ])

        # View window
        x_min = dt_to_ms(pd.to_datetime(crit["view_start"]))
        x_max = dt_to_ms(pd.to_datetime(crit["view_end"]))

        # Row height scaling (px)
        row_height = 34  # adjust if you want taller rows
        chart_height = max(480, len(y_categories) * (row_height + 8))

        # JS: renderItem draws rectangles per row + in-bar code label (white)
        render_item = JsCode("""
        function(params, api) {
          var start = api.value(0);
          var end = api.value(1);
          var yIdx = api.value(2);
          var code = api.value(3);
          var title = api.value(4);
          var segment = api.value(5);
          var catIndex = yIdx;

          var startCoord = api.coord([start, catIndex]);
          var endCoord   = api.coord([end, catIndex]);
          var x0 = startCoord[0];
          var x1 = endCoord[0];

          // bar height: a fraction of category interval
          var band = api.size([0, 1])[1];
          var barHeight = Math.max(12, Math.min(28, band * 0.65));
          var yCenter = startCoord[1];
          var yTop = yCenter - barHeight / 2;

          if (isNaN(x0) || isNaN(x1)) { return; }
          if (x1 < x0) { var tmp = x0; x0 = x1; x1 = tmp; }

          var groupChildren = [];

          // main rect
          groupChildren.push({
            type: 'rect',
            shape: { x: x0, y: yTop, width: (x1 - x0), height: barHeight, r: 3 },
            style: { fill: api.visual('color') }
          });

          // in-bar code label (white). Only if bar wide enough (~70px)
          if ((x1 - x0) > 70) {
            groupChildren.push({
              type: 'text',
              style: {
                x: (x0 + x1) / 2,
                y: yCenter,
                text: code,
                textAlign: 'center',
                textVerticalAlign: 'middle',
                fontSize: 12,
                textFill: '#FFFFFF',
                fontWeight: 600
              }
            });
          }

          return { type: 'group', children: groupChildren };
        }
        """)

        # Tooltip formatter
        tooltip_fmt = JsCode("""
        function (params) {
          var v = params.value;
          function fmt(ts) {
            var d = new Date(ts);
            var opts = { day:'2-digit', month:'short', year:'numeric' };
            return d.toLocaleDateString(undefined, opts);
          }
          var start = fmt(v[0]);
          var end   = fmt(v[1]);
          var code  = v[3];
          var title = v[4];
          var seg   = v[5];
          var prog  = v[6];
          var bud   = v[7];
          var budStr = (bud != null) ? new Intl.NumberFormat().format(bud) : '—';
          return [
            '<b>' + code + '</b>',
            title,
            'Segment: ' + seg,
            'Programme: ' + prog,
            'Budget (€): ' + budStr,
            'Start: ' + start,
            'End: ' + end
          ].join('<br/>');
        }
        """)

        option = {
            "animation": False,
            "grid": {"left": 260, "right": 20, "top": 36, "bottom": 50, "containLabel": False},
            "color": ["#4E79A7", "#59A14F", "#E15759", "#F28E2B", "#B07AA1", "#76B7B2", "#EDC948"],
            "tooltip": {"trigger": "item", "borderWidth": 0, "formatter": tooltip_fmt},
            "dataZoom": [
                {"type": "slider", "xAxisIndex": 0, "filterMode": "weakFilter", "height": 18, "bottom": 0},
                {"type": "inside", "xAxisIndex": 0, "filterMode": "weakFilter"}
            ],
            "xAxis": {
                "type": "time",
                "position": "top",
                "min": x_min,
                "max": x_max,
                "axisLabel": {"formatter": "{MMM} {yyyy}", "fontSize": 12},
                "splitLine": { "show": True, "lineStyle": {"color": "#E5E7EB"} },  # weekly split lines via minorTick not ideal; this gives regular grid
                "axisTick": { "show": True }
            },
            "yAxis": {
                "type": "category",
                "inverse": True,
                "data": y_categories,
                "axisLabel": {
                    "fontSize": 14,
                    "align": "left",
                    "margin": 12,
                    "width": 240,           # wrap by width + \n already present
                    "overflow": "break"     # allows breaking long lines
                }
            },
            "series": [{
                "type": "custom",
                "name": "Calls",
                "renderItem": render_item,
                "encode": {"x": [0,1], "y": 2},
                "data": series_data,
                "z": 10
            }]
        }

        st_echarts(options=option, height=chart_height, renderer="canvas")

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
