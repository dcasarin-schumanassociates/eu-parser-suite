# app_b.py — ECharts Gantt (JSON-safe + robust labels)
from __future__ import annotations
import io
from datetime import datetime
import pandas as pd
import streamlit as st
from streamlit_echarts import st_echarts, JsCode

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

def build_segments(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, r in df.iterrows():
        code  = str(r.get("code") or "")
        title = str(r.get("title") or "")
        y_title = wrap_label(title, width=36, max_lines=3)

        prog      = r.get("programme")
        open_dt   = r.get("opening_date")
        final_dt  = r.get("deadline")
        first_dt  = r.get("first_deadline")
        second_dt = r.get("second_deadline")
        two_stage = bool(r.get("two_stage"))

        def add_row(start, end, segment):
            if pd.notna(start) and pd.notna(end) and start <= end:
                rows.append({
                    "y_title": y_title, "code": code, "programme": prog,
                    "start": pd.to_datetime(start), "end": pd.to_datetime(end),
                    "segment": segment,
                    "title": title,
                    "budget_per_project_eur": (
                        float(r.get("budget_per_project_eur"))  # cast to Python float for JSON
                        if pd.notna(r.get("budget_per_project_eur")) else None
                    ),
                })

        if two_stage:
            add_row(open_dt, first_dt, "Stage 1")
            end_b = second_dt if pd.notna(second_dt) else final_dt
            add_row(first_dt, end_b, "Stage 2")
        else:
            add_row(open_dt, final_dt, "Single")

    seg = pd.DataFrame(rows)
    if seg.empty:
        return seg

    seg["earliest_end"] = seg.groupby("y_title")["end"].transform("min")
    seg = seg.sort_values(["earliest_end", "start"]).reset_index(drop=True)
    return seg

def dt_to_ms(x) -> int | None:
    if pd.isna(x):
        return None
    if isinstance(x, pd.Timestamp):
        x = x.to_pydatetime()
    if isinstance(x, datetime):
        return int(x.timestamp() * 1000)
    # fallback
    return int(pd.to_datetime(x).to_pydatetime().timestamp() * 1000)

# ---------- UI ----------
st.set_page_config(page_title="Calls Explorer — Gantt (ECharts)", layout="wide")
st.title("Calls Explorer — Gantt (ECharts)")

upl = st.file_uploader("Upload parsed Excel (.xlsx)", type=["xlsx"])
if not upl:
    st.stop()

xls = pd.ExcelFile(upl)
sheet = st.selectbox("Sheet", xls.sheet_names, index=0)
raw = pd.read_excel(xls, sheet_name=sheet)
df = canonicalise(raw)

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

    # Persistent x-range
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

# ---- Apply filters ----
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
        y_categories = seg["y_title"].drop_duplicates().tolist()
        y_index = {v: i for i, v in enumerate(y_categories)}

        # Each item must be pure JSON types
        series_data = []
        for _, r in seg.iterrows():
            series_data.append([
                int(dt_to_ms(r["start"])),            # 0: start_ms
                int(dt_to_ms(r["end"])),              # 1: end_ms
                int(y_index[r["y_title"]]),           # 2: row index
                str(r.get("code") or ""),             # 3: code (in-bar)
                str(r.get("title") or ""),            # 4: title (tooltip)
                str(r.get("segment") or ""),          # 5: segment (tooltip)
                str(r.get("programme") or ""),        # 6: programme (tooltip)
                (float(r.get("budget_per_project_eur")) if pd.notna(r.get("budget_per_project_eur")) else None),  # 7: budget
            ])

        x_min = int(dt_to_ms(pd.to_datetime(crit["view_start"])))
        x_max = int(dt_to_ms(pd.to_datetime(crit["view_end"])))

        row_height = 36
        chart_height = max(480, len(y_categories) * (row_height + 8))

        render_item = JsCode("""
        function(params, api) {
          var start = api.value(0);
          var end   = api.value(1);
          var yIdx  = api.value(2);
          var code  = api.value(3);

          var startCoord = api.coord([start, yIdx]);
          var endCoord   = api.coord([end,   yIdx]);
          var x0 = startCoord[0], x1 = endCoord[0];

          var band = api.size([0, 1])[1];
          var barH = Math.max(14, Math.min(30, band * 0.70));
          var yC   = startCoord[1];
          var yTop = yC - barH / 2;

          if (isNaN(x0) || isNaN(x1)) return;

          var children = [];
          children.push({
            type: 'rect',
            shape: { x: Math.min(x0,x1), y: yTop, width: Math.abs(x1-x0), height: barH, r: 3 },
            style: { fill: api.visual('color') }
          });

          if (Math.abs(x1-x0) > 72) {
            children.push({
              type: 'text',
              style: {
                x: (x0 + x1) / 2,
                y: yC,
                text: code,
                textAlign: 'center',
                textVerticalAlign: 'middle',
                fontSize: 12,
                textFill: '#FFFFFF',
                fontWeight: 600
              }
            });
          }
          return { type: 'group', children: children };
        }
        """)

        tooltip_fmt = JsCode("""
        function (params) {
          function fmt(ts) {
            var d = new Date(ts);
            var m = d.toLocaleString(undefined, {month:'short'});
            var y = d.getFullYear();
            var day = ('0' + d.getDate()).slice(-2);
            return day + ' ' + m + ' ' + y;
          }
          var v = params.value;
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

        month_axis_fmt = JsCode("""
        function (value) {
          var d = new Date(value);
          var m = d.toLocaleString(undefined, {month:'short'});
          var y = d.getFullYear();
          return m + ' ' + y;
        }
        """)

        option = {
            "animation": False,
            "grid": {"left": 320, "right": 20, "top": 42, "bottom": 56, "containLabel": False},
            "color": ["#4E79A7","#59A14F","#E15759","#F28E2B","#B07AA1","#76B7B2","#EDC948"],
            "tooltip": {"trigger": "item", "borderWidth": 0, "formatter": tooltip_fmt},
            "dataZoom": [
                {"type": "slider", "xAxisIndex": 0, "filterMode": "weakFilter", "height": 18, "bottom": 4},
                {"type": "inside", "xAxisIndex": 0, "filterMode": "weakFilter"}
            ],
            "xAxis": {
                "type": "time",
                "position": "top",
                "min": x_min,
                "max": x_max,
                "axisLabel": {"formatter": month_axis_fmt, "fontSize": 12},
                "splitLine": {"show": True, "lineStyle": {"color": "#E5E7EB"}},
                "axisTick": {"show": True}
            },
            "yAxis": {
                "type": "category",
                "inverse": True,
                "data": y_categories,
                "axisLabel": {
                    "fontSize": 14,
                    "align": "left",
                    "margin": 14,
                    "width": 280,         # allow wrapping
                    "overflow": "break",  # wrap long lines
                    "lineHeight": 18
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
