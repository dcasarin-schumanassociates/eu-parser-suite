from __future__ import annotations
import io
import json
import pandas as pd
import streamlit as st
from dateutil import tz
import altair as alt
import streamlit.components.v1 as components

# ===== Column mapping (same as before) =====
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
    "programme","cluster","code","title",
    "opening_date","deadline",
    "budget_per_project_eur","total_budget_eur",
    "type_of_action","trl","destination_or_strand",
    "call_name","version_label","source_filename",
]
LOCAL_TZ = tz.gettz("Europe/Brussels")

# ===== Helpers =====
def canonicalise(df: pd.DataFrame) -> pd.DataFrame:
    for src, dst in COLUMN_MAP.items():
        if src in df.columns and dst not in df.columns:
            df = df.rename(columns={src: dst})
    df = df.rename(columns={c: c.strip().lower() for c in df.columns})
    if "programme" not in df.columns:
        df["programme"] = "Horizon Europe"
    if "cluster" not in df.columns:
        df["cluster"] = pd.NA
    for c in ("opening_date","deadline"):
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce", dayfirst=True)
    for c in ("budget_per_project_eur","total_budget_eur","trl"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
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

def build_frappe_tasks(df: pd.DataFrame) -> list[dict]:
    tasks = []
    for i, r in df.dropna(subset=["opening_date","deadline"]).iterrows():
        tasks.append({
            "id": str(r.get("code") or f"row-{i}"),
            "name": f"{(r.get('code') or '')} — {(r.get('title') or '')}",
            "start": r["opening_date"].date().isoformat(),
            "end": r["deadline"].date().isoformat(),
            "progress": 100,
            "custom_class": (r.get("programme") or "default").replace(" ", "-").lower(),
            "details": {
                "Title": r.get("title") or "",
                "Type": r.get("type_of_action") or "",
                "Budget (€)": f"{int(r.get('budget_per_project_eur') or 0):,}",
                "Open": r["opening_date"].strftime("%d %b %Y") if pd.notna(r["opening_date"]) else "",
                "Close": r["deadline"].strftime("%d %b %Y") if pd.notna(r["deadline"]) else "",
                "Cluster/Strand": r.get("cluster") or r.get("destination_or_strand") or "",
                "Version": r.get("version_label") or "",
            }
        })
    return tasks

def render_frappe_gantt(tasks: list[dict], view_mode: str = "Month", height_px: int = 600):
    # CDN HTML block. We pass `tasks` JSON and build chart client-side.
    tasks_json = json.dumps(tasks)
    html = f"""
    <div id="gantt" class="gantt-container" style="width:100%; overflow-x:auto;"></div>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/frappe-gantt/dist/frappe-gantt.css">
    <script src="https://cdn.jsdelivr.net/npm/frappe-gantt/dist/frappe-gantt.min.js"></script>
    <style>
      .bar {{ height: 22px; }}
      .bar-wrapper {{ margin-bottom: 10px; }}
      .grid .row.lines .line {{ stroke: #e5e7eb; stroke-width: 1; }}
      .grid .tick {{ stroke: #cbd5e1; }}
      .popup-wrapper {{ max-width: 420px; }}
    </style>
    <script>
      const tasks = {tasks_json};
      const el = document.getElementById('gantt');
      el.style.height = '{height_px}px';

      // Map programme class -> color (set SVG style dynamically)
      const colorMap = {{
        "horizon-europe": "#3b82f6",
        "digital-europe": "#22c55e",
        "erasmus+": "#f59e0b",
        "default": "#64748b"
      }};

      const gantt = new Gantt(el, tasks, {{
        view_mode: "{view_mode}",
        custom_popup_html: function(task) {{
          const d = task.details || {{}};
          return `
            <div class="details-container">
              <h5 style="margin:0 0 6px 0;">${{task.name}}</h5>
              <div><b>Type:</b> ${{d["Type"] || ""}}</div>
              <div><b>Budget (€):</b> ${{d["Budget (€)"] || ""}}</div>
              <div><b>Open → Close:</b> ${{d["Open"] || ""}} → ${{d["Close"] || ""}}</div>
              <div><b>Cluster/Strand:</b> ${{d["Cluster/Strand"] || ""}}</div>
              <div><b>Version:</b> ${{d["Version"] || ""}}</div>
            </div>`;
        }}
      }});

      // Apply colors
      setTimeout(() => {{
        document.querySelectorAll('.bar').forEach(bar => {{
          const klass = Array.from(bar.classList).find(c => colorMap[c]);
          const color = colorMap[klass] || colorMap.default;
          bar.style.fill = color;
        }});
      }}, 0);
    </script>
    """
    components.html(html, height=height_px + 40, scrolling=True)

def make_gantt_altair(df: pd.DataFrame):
    g = df.dropna(subset=["opening_date","deadline","title"]).copy()
    if g.empty:
        return None
    g["title_wrapped"] = (g["code"].fillna("").astype(str) + " — " + g["title"].astype(str))\
                         .str.replace(r"(.{50})", r"\\n\\1", regex=True)
    row_h = 28
    h = max(400, len(g) * row_h)
    base = alt.Chart(g).encode(
        y=alt.Y("title_wrapped:N", sort='-x', axis=alt.Axis(title=None, labelLimit=300)),
        color=alt.Color("programme:N", legend=None)
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
    months = pd.date_range(g["opening_date"].min().floor("D"),
                           g["deadline"].max().ceil("D"),
                           freq="MS")
    grid = alt.Chart(pd.DataFrame({"m": months})).mark_rule(stroke="#DDD").encode(x="m:T")
    return (grid + bars).properties(height=h).configure_axis(grid=False).configure_view(strokeWidth=0)

# ===== UI =====
st.set_page_config(page_title="Calls Explorer (Frappe via CDN)", layout="wide")
st.title("Calls Explorer (Frappe Gantt + Filters)")

upl = st.file_uploader("Upload parsed Excel (.xlsx)", type=["xlsx"])
if not upl:
    st.stop()

xls = pd.ExcelFile(upl)
sheet = st.selectbox("Sheet", xls.sheet_names, index=0)
raw = pd.read_excel(xls, sheet_name=sheet)
df = canonicalise(raw)

# Filters
st.sidebar.header("Filters")
prog_opts   = sorted([p for p in df["programme"].dropna().unique().tolist() if p != ""])
cluster_opts= sorted([c for c in df["cluster"].dropna().unique().tolist() if pd.notna(c)])
type_opts   = sorted([t for t in df["type_of_action"].dropna().unique().tolist() if pd.notna(t)])
trl_opts    = sorted([str(int(x)) for x in df["trl"].dropna().unique() if pd.notna(x)])
dest_opts   = sorted([d for d in df["destination_or_strand"].dropna().unique().tolist() if pd.notna(d)])

programmes = st.sidebar.multiselect("Programme", options=prog_opts, default=prog_opts)
clusters   = st.sidebar.multiselect("Cluster / Strand", options=cluster_opts)
types      = st.sidebar.multiselect("Type of Action", options=type_opts)
trls       = st.sidebar.multiselect("TRL", options=trl_opts)
dests      = st.sidebar.multiselect("Destination / Strand", options=dest_opts)

open_lo, open_hi = safe_date_bounds(df.get("opening_date"))
dead_lo, dead_hi = safe_date_bounds(df.get("deadline"))
col_o1, col_o2 = st.sidebar.columns(2)
with col_o1:
    open_start = st.date_input("Open from", value=open_lo, min_value=open_lo, max_value=open_hi)
with col_o2:
    open_end   = st.date_input("Open to",   value=open_hi, min_value=open_lo, max_value=open_hi)
col_d1, col_d2 = st.sidebar.columns(2)
with col_d1:
    dead_start = st.date_input("Deadline from", value=dead_lo, min_value=dead_lo, max_value=dead_hi)
with col_d2:
    dead_end   = st.date_input("Deadline to",   value=dead_hi, min_value=dead_lo, max_value=dead_hi)

bud_series = pd.to_numeric(df.get("budget_per_project_eur"), errors="coerce").dropna()
if bud_series.empty:
    min_bud, max_bud = 0.0, 1_000_000.0
else:
    min_bud, max_bud = float(bud_series.min()), float(bud_series.max())
    if not (min_bud < max_bud):
        min_bud, max_bud = max(min_bud, 0.0), min_bud + 100000.0
budget_range = st.sidebar.slider("Budget per project (EUR)", min_bud, max_bud, (min_bud, max_bud), step=100000.0)

st.sidebar.header("Search")
keyword = st.sidebar.text_input("Keyword (searches all columns)")

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

f = f[
    (f["opening_date"] >= pd.to_datetime(open_start)) &
    (f["opening_date"] <= pd.to_datetime(open_end)) &
    (f["deadline"] >= pd.to_datetime(dead_start)) &
    (f["deadline"] <= pd.to_datetime(dead_end))
]
f = f[
    (f["budget_per_project_eur"].fillna(0) >= budget_range[0]) &
    (f["budget_per_project_eur"].fillna(0) <= budget_range[1])
]

st.markdown(f"**Showing {len(f)} rows** after filters/search.")

# Tabs
tab1, tab2, tab3 = st.tabs(["📅 Gantt", "📋 Table", "📚 Full Data"])

with tab1:
    st.subheader("Gantt (Opening → Deadline)")
    tasks = build_frappe_tasks(f)
    if not tasks:
        st.info("No rows with valid Opening/Deadline")
    else:
        # Controls
        colA, colB = st.columns([1,1])
        with colA:
            view_mode = st.selectbox("Scale", ["Month","Week","Day"], index=0)
        with colB:
            row_px = st.slider("Row height (px)", 20, 60, 28)
        chart_h = max(360, int(len(tasks) * (row_px + 10)))
        # Render via CDN
        render_frappe_gantt(tasks, view_mode=view_mode, height_px=chart_h)

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
