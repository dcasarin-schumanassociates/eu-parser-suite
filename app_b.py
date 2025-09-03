# app_b_frappe.py
from __future__ import annotations
import io
import pandas as pd
import streamlit as st
from dateutil import tz

# Frappe Gantt component
try:
    from streamlit_frappe_gantt import gantt
except Exception as e:
    st.error("Missing dependency: install with `pip install streamlit-frappe-gantt`")
    raise

# ========== Config / column mapping ==========
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
LOCAL_TZ = tz.gettz("Europe/Brussels")

# Nice colours per programme (used via CSS classes)
PROGRAMME_COLOURS = {
    "Horizon Europe": "#3b82f6",  # blue-500
    "Digital Europe": "#22c55e",  # green-500
    "Erasmus+": "#f59e0b",        # amber-500
}

# ========== Helpers ==========
def canonicalise(df: pd.DataFrame) -> pd.DataFrame:
    # rename known headers
    for src, dst in COLUMN_MAP.items():
        if src in df.columns and dst not in df.columns:
            df = df.rename(columns={src: dst})
    # lower-case remaining
    df = df.rename(columns={c: c.strip().lower() for c in df.columns})

    if "programme" not in df.columns:
        df["programme"] = "Horizon Europe"
    if "cluster" not in df.columns:
        df["cluster"] = pd.NA

    # types
    for c in ("opening_date", "deadline"):
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce", dayfirst=True)
    for c in ("budget_per_project_eur", "total_budget_eur", "trl"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df

def keyword_filter(df: pd.DataFrame, term: str) -> pd.DataFrame:
    term = (term or "").strip().lower()
    if not term:
        return df
    return df[df.apply(lambda r: r.astype(str).str.lower().str.contains(term).any(), axis=1)]

def filtered_df(df: pd.DataFrame,
                programmes, clusters, types, trls, dests,
                open_start, open_end, dead_start, dead_end,
                budget_range) -> pd.DataFrame:
    out = df.copy()
    if programmes:
        out = out[out["programme"].isin(programmes)]
    if clusters:
        out = out[out["cluster"].isin(clusters)]
    if types:
        out = out[out["type_of_action"].isin(types)]
    if trls:
        trl_str = out["trl"].dropna().astype("Int64").astype(str)
        out = out[trl_str.isin(trls)]
    if dests:
        out = out[out["destination_or_strand"].isin(dests)]

    # budgets
    lo, hi = budget_range
    if "budget_per_project_eur" in out.columns:
        out = out[
            (out["budget_per_project_eur"].fillna(0) >= lo) &
            (out["budget_per_project_eur"].fillna(0) <= hi)
        ]

    # dates (guard NaT)
    if pd.notna(open_start) and pd.notna(open_end):
        out = out[(out["opening_date"] >= pd.to_datetime(open_start)) &
                  (out["opening_date"] <= pd.to_datetime(open_end))]
    if pd.notna(dead_start) and pd.notna(dead_end):
        out = out[(out["deadline"] >= pd.to_datetime(dead_start)) &
                  (out["deadline"] <= pd.to_datetime(dead_end))]
    return out

def safe_date_bounds(series, start_fb="2000-01-01", end_fb="2100-12-31"):
    s = pd.to_datetime(series, errors="coerce").dropna()
    if s.empty:
        return (pd.to_datetime(start_fb).date(), pd.to_datetime(end_fb).date())
    lo, hi = s.min().date(), s.max().date()
    if lo == hi:
        hi = (pd.to_datetime(hi) + pd.Timedelta(days=1)).date()
    return lo, hi

def build_tasks(df: pd.DataFrame) -> list[dict]:
    """Convert rows to Frappe Gantt tasks."""
    tasks = []
    for i, r in df.dropna(subset=["opening_date", "deadline"]).iterrows():
        pid = (r.get("programme") or "").strip()
        css = (pid if pid in PROGRAMME_COLOURS else "default").replace(" ", "-").lower()
        tasks.append({
            "id": str(r.get("code") or f"row-{i}"),
            "name": f"{(r.get('code') or '')} — {(r.get('title') or '')}",
            "start": r["opening_date"].date().isoformat(),
            "end": r["deadline"].date().isoformat(),
            "progress": 100,  # static; treat as published window
            "custom_class": css,
            "details": {  # show in popup
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

def tasks_to_html(tasks: list[dict]) -> list[dict]:
    """Frappe’s built-in popup is basic. We’ll render a nicer HTML in the name."""
    out = []
    for t in tasks:
        name_html = (
            f"<b>{t['name']}</b>"
        )
        t2 = dict(t)
        t2["name"] = name_html
        out.append(t2)
    return out

# Inject CSS for nicer visuals (row height, colours, grid)
def inject_css():
    palette = []
    for prog, color in PROGRAMME_COLOURS.items():
        klass = prog.replace(" ", "-").lower()
        palette.append(f".bar-wrapper .bar.{klass} {{ fill: {color}; }}")
        palette.append(f".handle.{klass} {{ stroke: {color}; }}")
    css = f"""
    <style>
      .gantt-container {{ overflow-x: auto; }}
      .gantt .bar .bar-rect {{ rx: 4px; ry: 4px; }}
      .gantt .bar {{ height: 22px; }}             /* bar height (row thickness) */
      .gantt .bar-group {{ margin-bottom: 10px; }}/* vertical spacing */
      .gantt .grid .row.lines .line {{ stroke: #e5e7eb; stroke-width: 1; }} /* grid lines */
      .gantt .grid .tick {{ stroke: #cbd5e1; }}   /* calendar ticks */
      .gantt text {{ font-size: 12px; fill: #111827; }}
      .popup-wrapper {{ max-width: 420px; }}
      .popup-wrapper .pointer.month, .popup-wrapper .pointer {{ display: none; }}
      {''.join(palette)}
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)

# ========== UI ==========
st.set_page_config(page_title="Calls Explorer (Frappe Gantt)", layout="wide")
st.title("Calls Explorer (Frappe Gantt + Filters)")

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
clusters   = st.sidebar.multiselect("Cluster / Strand", options=cluster_opts)
types      = st.sidebar.multiselect("Type of Action", options=type_opts)
trls       = st.sidebar.multiselect("TRL", options=trl_opts)
dests      = st.sidebar.multiselect("Destination / Strand", options=dest_opts)

# Date bounds
open_lo, open_hi = safe_date_bounds(df.get("opening_date"))
dead_lo, dead_hi = safe_date_bounds(df.get("deadline"))

col_open1, col_open2 = st.sidebar.columns(2)
with col_open1:
    open_start = st.date_input("Open from", value=open_lo, min_value=open_lo, max_value=open_hi)
with col_open2:
    open_end   = st.date_input("Open to",   value=open_hi, min_value=open_lo, max_value=open_hi)

col_dead1, col_dead2 = st.sidebar.columns(2)
with col_dead1:
    dead_start = st.date_input("Deadline from", value=dead_lo, min_value=dead_lo, max_value=dead_hi)
with col_dead2:
    dead_end   = st.date_input("Deadline to",   value=dead_hi, min_value=dead_lo, max_value=dead_hi)

# Budget slider (robust)
bud_series = pd.to_numeric(df.get("budget_per_project_eur"), errors="coerce").dropna()
if bud_series.empty:
    min_bud, max_bud = 0.0, 1_00_
