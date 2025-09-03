from __future__ import annotations
import io, json
import pandas as pd
import streamlit as st
from dateutil import tz
import streamlit.components.v1 as components

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
    "code","title","opening_date","deadline","first_deadline","second_deadline","two_stage",
    "cluster","destination_or_strand","type_of_action","trl",
    "budget_per_project_eur","total_budget_eur","num_projects",
    "call_name","version_label","source_filename","parsed_on_utc"
]

LOCAL_TZ = tz.gettz("Europe/Brussels")

# ---------- Helpers ----------
def canonicalise(df: pd.DataFrame) -> pd.DataFrame:
    # Trim headers first
    df.columns = [c.strip() for c in df.columns]

    # Apply direct mapping (Title Case -> snake_case)
    for src, dst in COLUMN_MAP.items():
        if src in df.columns and dst not in df.columns:
            df = df.rename(columns={src: dst})

    # Lowercase any remaining headers for safety
    df = df.rename(columns={c: c.strip().lower() for c in df.columns})

    # Ensure programme exists; cluster already mapped above
    if "programme" not in df.columns:
        df["programme"] = "Horizon Europe"

    # Parse dates (EU day-first)
    for c in ("opening_date","deadline","first_deadline","second_deadline"):
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce", dayfirst=True)

    # Numbers
    for c in ("budget_per_project_eur","total_budget_eur","trl","num_projects"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # Two-stage to bool if present
    if "two_stage" in df.columns:
        # tolerate text like "True"/"FALSE"/"Yes"
        df["two_stage"] = df["two_stage"].astype(str).str.lower().map({"true": True, "false": False, "yes": True, "no": False})
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

def _safe(val):
    return "" if pd.isna(val) else str(val)

def choose_deadline(row, mode: str):
    if mode == "Final deadline":
        return row.get("deadline")
    elif mode == "First stage":
        return row.get("first_deadline")
    elif mode == "Second stage":
        return row.get("second_deadline")
    return row.get("deadline")

def build_frappe_tasks(df: pd.DataFrame, deadline_mode: str = "Final deadline") -> list[dict]:
    tasks = []
    # pick the deadline column based on mode
    for i, r in df.iterrows():
        end_dt = choose_deadline(r, deadline_mode)
        if pd.isna(r.get("opening_date")) or pd.isna(end_dt):
            continue

        tasks.append({
            "id": _safe(r.get("code")) or f"row-{i}",
            "name": f"{_safe(r.get('code'))} — {_safe(r.get('title'))}",
            "start": pd.to_datetime(r.get("opening_date")).date().isoformat(),
            "end":   pd.to_datetime(end_dt).date().isoformat(),
            "progress": 100,
            "custom_class": (_safe(r.get("programme")) or "default").replace(" ", "-").lower(),
            "details": {
                "Title": _safe(r.get("title")),
                "Type": _safe(r.get("type_of_action")),
                "Budget (€)": f"{int(r.get('budget_per_project_eur') or 0):,}",
                "Open": pd.to_datetime(r.get("opening_date")).strftime("%d %b %Y"),
                "Close": pd.to_datetime(end_dt).strftime("%d %b %Y"),
                "Two-stage": "Yes" if bool(r.get("two_stage")) else "No",
                "Cluster": _safe(r.get("cluster")),
                "Destination/Strand": _safe(r.get("destination_or_strand")),
                "Version": _safe(r.get("version_label")),
            }
        })
    return tasks

def render_frappe_gantt(tasks: list[dict], view_mode: str = "Month", height_px: int = 700):
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
      .popup-wrapper {{ max-width: 460px; }}
      .gantt .bar .bar-rect {{ rx: 4px; ry: 4px; }}
    </style>
    <script>
      const tasks = {tasks_json};
      const el = document.getElementById('gantt');
      el.style.height = '{height_px}px';

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
              <div><b>Two-stage:</b> ${{d["Two-stage"] || ""}}</div>
              <div><b>Cluster:</b> ${{d["Cluster"] || ""}}</div>
              <div><b>Destination/Strand:</b> ${{d["Destination/Strand"] || ""}}</div>
              <div><b>Version:</b> ${{d["Version"] || ""}}</div>
            </div>`;
        }}
      }});

      // Apply colors per programme class
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

# ---------- UI ----------
st.set_page_config(page_title="Calls Explorer (Frappe Gantt)", layout="wide")
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
prog_opts    = sorted([p for p in df["programme"].dropna().unique().tolist() if p != ""])
cluster_opts = sorted([c for c in df["cluster"].dropna().unique().tolist() if pd.notna(c)])
type_opts    = sorted([t for t in df["type_of_action"].dropna().unique().tolist() if pd.notna(t)])
trl_opts     = sorted([str(int(x)) for x in df["trl"].dropna().unique() if pd.notna(x)])
dest_opts    = sorted([d for d in df["destination_or_strand"].dropna().unique().tolist() if pd.notna(d)])

programmes = st.sidebar.multiselect("Programme", options=prog_opts, default=prog_opts if prog_opts else [])
clusters   = st.sidebar.multiselect("Cluster", options=cluster_opts)
types      = st.sidebar.multiselect("Type of Action", options=type_opts)
trls       = st.sidebar.multiselect("TRL", options=trl_opts)
dests      = st.sidebar.multiselect("Destination / Strand", options=dest_opts)

# Date bounds from the three date columns (opening & any deadlines)
open_lo, open_hi = safe_date_bounds(df.get("opening_date"))
# For overall allowed range, consider any of the deadline fields
dead_all = pd.concat([
    pd.to_datetime(df.get("deadline"), errors="coerce"),
    pd.to_datetime(df.get("first_deadline"), errors="coerce"),
    pd.to_datetime(df.get("second_deadline"), errors="coerce")
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

# Budget slider (robust)
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

# Deadline mode (what to plot as the bar's end)
st.sidebar.header("Gantt options")
deadline_mode = st.sidebar.selectbox("Deadline to plot", ["Final deadline", "First stage", "Second stage"], index=0)
view_mode = st.sidebar.selectbox("Gantt scale", ["Month","Week","Day"], index=0)
row_px    = st.sidebar.slider("Row height (px)", 20, 60, 28)

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

# Date filters: opening window
f = f[(f["opening_date"] >= pd.to_datetime(open_start)) & (f["opening_date"] <= pd.to_datetime(open_end))]

# Date filters: chosen deadline column for plotting
deadline_col = "deadline" if deadline_mode == "Final deadline" else ("first_deadline" if deadline_mode == "First stage" else "second_deadline")
if deadline_col not in f.columns:
    # if missing, keep all rows (the build will skip rows without both dates)
    pass
else:
    f = f[(pd.to_datetime(f[deadline_col], errors="coerce") >= pd.to_datetime(dead_start)) &
          (pd.to_datetime(f[deadline_col], errors="coerce") <= pd.to_datetime(dead_end))]

# Budget filter
f = f[(f["budget_per_project_eur"].fillna(0) >= budget_range[0]) &
      (f["budget_per_project_eur"].fillna(0) <= budget_range[1])]

st.markdown(f"**Showing {len(f)} rows** after filters/search.")

# Tabs
tab1, tab2, tab3 = st.tabs(["📅 Gantt", "📋 Table", "📚 Full Data"])

with tab1:
    st.subheader(f"Gantt (Opening → {deadline_mode})")
    tasks = build_frappe_tasks(f, deadline_mode=deadline_mode)
    if not tasks:
        st.info("No rows with valid dates for the selected deadline mode.")
    else:
        chart_h = max(360, int(len(tasks) * (row_px + 10)))
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
