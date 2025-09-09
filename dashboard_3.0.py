# app_b3_1.py — Streamlit Funding Dashboard (TWO separate Gantts; one per programme)
from __future__ import annotations

import io, base64, re
from datetime import datetime
from typing import List, Dict, Optional, Tuple

import pandas as pd
import streamlit as st
import altair as alt

# Optional DOCX (for shortlist export)
try:
    from docx import Document
    from docx.shared import Pt, Cm
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    DOCX_AVAILABLE = True
except Exception:
    DOCX_AVAILABLE = False

# ---------------------- Column mapping (tune if your headers differ) ----------------------
COLUMN_MAP = {
    "Code": "code",
    "Title": "title",
    "Opening Date": "opening_date",
    "Opening date": "opening_date",
    "Deadline": "deadline",
    "First Stage Deadline": "first_deadline",
    "Second Stage Deadline": "second_deadline",
    "Second Stage deadline": "second_deadline",
    "Two-Stage": "two_stage",
    "Cluster": "cluster",
    "Destination": "destination_or_strand",
    "Strand": "destination_or_strand",
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
    "programme","code","title","opening_date","deadline",
    "first_deadline","second_deadline","two_stage",
    "cluster","destination_or_strand","type_of_action","trl",
    "budget_per_project_eur","total_budget_eur","num_projects",
    "call_name","version_label","source_filename","parsed_on_utc",
]

SEARCHABLE_COLUMNS = (
    "code","title","call_name","expected_outcome","scope","full_text",
    "cluster","destination_or_strand","type_of_action","trl"
)

# --------------------------------- Helpers ---------------------------------
def wrap_label(text: str, width=60, max_lines=3) -> str:
    s = str(text or "")
    chunks = [s[i:i+width] for i in range(0, len(s), width)]
    return "\n".join(chunks[:max_lines]) if chunks else "—"

def safe_date_series(s):
    """Parse dates robustly; try day-first then non-day-first."""
    out = pd.to_datetime(s, errors="coerce", dayfirst=True)
    if out.notna().sum() == 0:
        out = pd.to_datetime(s, errors="coerce", dayfirst=False)
    return out

def canonicalise(df: pd.DataFrame, programme_name: str) -> pd.DataFrame:
    # 1) rename columns
    df.columns = [c.strip() for c in df.columns]
    for src, dst in COLUMN_MAP.items():
        if src in df.columns and dst not in df.columns:
            df = df.rename(columns={src: dst})
    df = df.rename(columns={c: c.strip().lower() for c in df.columns})

    # 2) add programme
    df["programme"] = programme_name

    # 3) parse numerics
    for c in ("budget_per_project_eur","total_budget_eur","trl","num_projects"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # 4) parse dates robustly
    for c in ("opening_date","deadline","first_deadline","second_deadline"):
        if c in df.columns:
            df[c] = safe_date_series(df[c])

    # 5) two-stage flag
    if "two_stage" in df.columns:
        df["two_stage"] = (
            df["two_stage"].astype(str).str.strip().str.lower()
            .map({"true": True, "false": False, "yes": True, "no": False, "1": True, "0": False})
            .fillna(False)
        )
    else:
        df["two_stage"] = False

    # 6) searchable haystacks
    present = [c for c in SEARCHABLE_COLUMNS if c in df.columns]
    df["_search_all"] = df[present].astype(str).agg(" ".join, axis=1).str.lower() if present else ""
    title_cols = [c for c in ["code","title"] if c in df.columns]
    df["_search_title"] = df[title_cols].astype(str).agg(" ".join, axis=1).str.lower() if title_cols else ""

    # 7) convenience "any closing"
    close_cols = [c for c in ["deadline","first_deadline","second_deadline"] if c in df.columns]
    if close_cols:
        df["closing_date_any"] = pd.to_datetime(df[close_cols].stack(), errors="coerce").groupby(level=0).min()
    else:
        df["closing_date_any"] = pd.NaT

    return df

def build_month_bands(min_x: pd.Timestamp, max_x: pd.Timestamp) -> pd.DataFrame:
    start = pd.Timestamp(min_x).to_period("M").start_time
    end   = (pd.Timestamp(max_x).to_period("M") + 1).start_time
    months = pd.date_range(start, end, freq="MS")
    rows = []
    for i in range(len(months)-1):
        rows.append({"start": months[i], "end": months[i+1], "band": i % 2})
    return pd.DataFrame(rows)

def safe_bounds(series, start_fb="2000-01-01", end_fb="2100-12-31"):
    s = pd.to_datetime(series, errors="coerce").dropna()
    if s.empty:
        return (pd.to_datetime(start_fb).date(), pd.to_datetime(end_fb).date())
    lo, hi = s.min().date(), s.max().date()
    if lo == hi:  # keep axis valid
        hi = (pd.to_datetime(hi) + pd.Timedelta(days=1)).date()
    return lo, hi

# ------------------------------- I/O (cached) --------------------------------
@st.cache_data(show_spinner=False)
def get_sheet_names(file_bytes: bytes) -> List[str]:
    xls = pd.ExcelFile(io.BytesIO(file_bytes))
    return xls.sheet_names

@st.cache_data(show_spinner=False)
def load_programme(file_bytes: bytes, sheet_name: str, programme_name: str) -> pd.DataFrame:
    xls = pd.ExcelFile(io.BytesIO(file_bytes))
    raw = pd.read_excel(xls, sheet_name=sheet_name)
    return canonicalise(raw, programme_name)

# --------------------------- Single-bar-per-row prep ---------------------------
def build_singlebar_rows(df: pd.DataFrame) -> pd.DataFrame:
    """One bar per row: Opening → Deadline. Stage 1/2 shown as thin markers on same row."""
    required = {"opening_date","deadline"}
    g = df.copy()
    # robust Y label: code → title → index
    if "code" in g.columns and g["code"].notna().any():
        base_label = g["code"].fillna("").astype(str)
    elif "title" in g.columns and g["title"].notna().any():
        base_label = g["title"].fillna("").astype(str)
    else:
        base_label = pd.Series([f"row-{i}" for i in range(len(g))], index=g.index)

    # ensure uniqueness to prevent stacking
    dup_mask = base_label.duplicated(keep=False)
    if dup_mask.any():
        base_label = base_label + g.groupby(base_label).cumcount().astype(str).radd("#")

    g["y_label"] = base_label.map(lambda s: wrap_label(s, width=100, max_lines=5))
    g["title_inbar"] = g.get("title","").astype(str).map(lambda s: wrap_label(s, width=100, max_lines=3))

    # keep only valid date rows
    g["opening_date"] = safe_date_series(g.get("opening_date"))
    g["deadline"]     = safe_date_series(g.get("deadline"))
    g = g[pd.notna(g["opening_date"]) & pd.notna(g["deadline"]) & (g["opening_date"] <= g["deadline"])].copy()
    if g.empty:
        return g

    g["bar_days"] = (g["deadline"] - g["opening_date"]).dt.days
    g["mid"] = g["opening_date"] + (g["deadline"] - g["opening_date"])/2
    return g.sort_values(["deadline","opening_date"])

def gantt_singlebar_chart(g: pd.DataFrame, color_field: str = "type_of_action", title: str = ""):
    if g is None or g.empty:
        return None

    min_x = min(g["opening_date"].min(), g["deadline"].min())
    max_x = max(g["opening_date"].max(), g["deadline"].max())
    bands_df = build_month_bands(min_x, max_x)

    month_shade = alt.Chart(bands_df).mark_rect(tooltip=False).encode(
        x="start:T", x2="end:T",
        opacity=alt.Opacity("band:Q", scale=alt.Scale(domain=[0,1], range=[0.0,0.15]), legend=None),
        color=alt.value("#00008B")
    )
    months = pd.date_range(pd.Timestamp(min_x).to_period("M").start_time,
                           pd.Timestamp(max_x).to_period("M").end_time, freq="MS")
    month_grid = alt.Chart(pd.DataFrame({"t": months})).mark_rule(stroke="#FFF", strokeWidth=1.5).encode(x="t:T")
    month_labels_df = pd.DataFrame({
        "month": months[:-1], "next_month": months[1:],
        "label": [m.strftime("%b %Y") for m in months[:-1]]
    })
    month_labels_df["mid"] = month_labels_df["month"] + ((month_labels_df["next_month"] - month_labels_df["month"]) / 2)
    month_labels = alt.Chart(month_labels_df).mark_text(
        align="center", baseline="top", dy=0, fontSize=12, fontWeight="bold"
    ).encode(x="mid:T", text="label:N", y=alt.value(0))

    # Today line (Europe/Brussels)
    today_ts = pd.Timestamp.now(tz="Europe/Brussels").normalize().tz_localize(None)
    today_df = pd.DataFrame({"t":[today_ts]})
    today_rule = alt.Chart(today_df).mark_rule(color="#d62728", strokeDash=[6,4], strokeWidth=2).encode(
        x="t:T", tooltip=[alt.Tooltip("t:T", title="Today", format="%d %b %Y")]
    )
    today_label = alt.Chart(today_df).mark_text(
        align="left", baseline="top", dx=4, dy=0, fontSize=11, fontWeight="bold", color="#d62728"
    ).encode(x="t:T", y=alt.value(0), text=alt.Text("t:T", format='Today: "%d %b %Y"'))

    # sizing
    y_order = g["y_label"].drop_duplicates().tolist()
    row_h = 46
    bar_size = int(row_h * 0.38)
    domain_min, domain_max = g["opening_date"].min(), g["deadline"].max()

    base = alt.Chart(g).encode(
        y=alt.Y("y_label:N", sort=y_order,
                axis=alt.Axis(title=None, labelLimit=200, labelFontSize=11, labelAlign="right", labelPadding=50, domain=True),
                scale=alt.Scale(domain=y_order, paddingInner=0.3, paddingOuter=0.8))
    )

    bars = base.mark_bar(cornerRadius=10, size=bar_size).encode(
        x=alt.X("opening_date:T",
                axis=alt.Axis(title=None, format="%b %Y", tickCount="month", orient="top",
                              labelFontSize=11, labelPadding=50, labelOverlap="greedy", tickSize=6),
                scale=alt.Scale(domain=[domain_min, domain_max])),
        x2="deadline:T",
        color=alt.Color(color_field + ":N",
                        legend=alt.Legend(title=color_field.replace("_"," ").title(), orient="top", direction="horizontal", offset=100),
                        scale=alt.Scale(scheme="set2")),
        tooltip=[
            alt.Tooltip("title:N", title="Title"),
            alt.Tooltip("programme:N", title="Programme") if "programme" in g.columns else alt.Tooltip("y_label:N", title="Row"),
            alt.Tooltip("opening_date:T", title="Opening", format="%d %b %Y"),
            alt.Tooltip("deadline:T", title="Deadline", format="%d %b %Y"),
        ]
    )

    # optional thin markers for Stage 1/2 on the same row (no extra bars)
    if "first_deadline" in g.columns:
        rule1 = alt.Chart(g[g["first_deadline"].notna()]).mark_rule(size=1, color="#00000030").encode(
            x="first_deadline:T", y="y_label:N"
        )
    else:
        rule1 = alt.Chart(pd.DataFrame({"x":[None]})).mark_rule().encode()

    if "second_deadline" in g.columns:
        rule2 = alt.Chart(g[g["second_deadline"].notna()]).mark_rule(size=1, color="#00000030").encode(
            x="second_deadline:T", y="y_label:N"
        )
    else:
        rule2 = alt.Chart(pd.DataFrame({"x":[None]})).mark_rule().encode()

    # date labels + in-bar title
    start_labels = base.mark_text(align="right", dx=-4, dy=5, fontSize=10, color="#111").encode(
        x="opening_date:T", text=alt.Text("opening_date:T", format="%d %b %Y"))
    end_labels = base.mark_text(align="left", dx=4, dy=5, fontSize=10, color="#111").encode(
        x="deadline:T", text=alt.Text("deadline:T", format="%d %b %Y"))
    inbar = base.mark_text(align="left", baseline="bottom", dx=2, dy=-(int(bar_size/2)+4), color="black").encode(
        x=alt.X("opening_date:T", scale=alt.Scale(domain=[domain_min, domain_max]), axis=None),
        text="title_inbar:N",
        opacity=alt.condition(alt.datum.bar_days >= 10, alt.value(1), alt.value(0))
    )

    chart = (month_shade + month_grid + bars + rule1 + rule2 + start_labels + end_labels + inbar + month_labels + today_rule + today_label)\
        .properties(height=max(800, len(y_order)*row_h), width='container',
                    padding={"top":50,"bottom":30,"left":10,"right":10})\
        .configure_axis(grid=False, domain=True, domainWidth=1)\
        .configure_view(continuousHeight=500, continuousWidth=500, strokeWidth=0, clip=False)\
        .interactive(bind_x=True)

    return chart if not title else chart.properties(title=title)

# --------------------------------- Reports ---------------------------------
def generate_docx_report(calls_df: pd.DataFrame, notes_by_code: Dict[str,str], title="Funding Report") -> bytes:
    if not DOCX_AVAILABLE:
        raise RuntimeError("python-docx not installed")
    doc = Document()
    h = doc.add_heading(title, level=0); h.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p = doc.add_paragraph(f"Generated on {datetime.utcnow():%d %b %Y, %H:%M UTC}"); p.runs[0].font.size = Pt(9)

    table = doc.add_table(rows=1, cols=5); hdr = table.rows[0].cells
    for i, t in enumerate(["Programme","Code","Title","Opening","Deadline"]): hdr[i].text = t
    for _, r in calls_df.iterrows():
        row = table.add_row().cells
        row[0].text = str(r.get("programme","")); row[1].text = str(r.get("code","")); row[2].text = str(r.get("title",""))
        op, dl = r.get("opening_date"), r.get("deadline")
        row[3].text = op.strftime("%d %b %Y") if pd.notna(op) else "-"
        row[4].text = dl.strftime("%d %b %Y") if pd.notna(dl) else "-"

    for _, r in calls_df.iterrows():
        doc.add_page_break()
        doc.add_heading(f"{r.get('code','')} — {r.get('title','')}", level=1)
        lines = []
        lines.append(f"Programme: {r.get('programme','-')}")
        lines.append(f"Cluster: {r.get('cluster','-')}")
        lines.append(f"Destination: {r.get('destination_or_strand','-')}")
        lines.append(f"Type of Action: {r.get('type_of_action','-')}")
        trl_val = r.get("trl"); lines.append(f"TRL: {int(trl_val) if pd.notna(trl_val) else '-'}")
        op, dl = r.get("opening_date"), r.get("deadline")
        f1, f2 = r.get("first_deadline"), r.get("second_deadline")
        lines.append(f"Opening: {op:%d %b %Y}" if pd.notna(op) else "Opening: -")
        lines.append(f"Deadline: {dl:%d %b %Y}" if pd.notna(dl) else "Deadline: -")
        if r.get("two_stage"):
            lines.append(f"Stage 1: {f1:%d %b %Y}" if pd.notna(f1) else "Stage 1: -")
            lines.append(f"Stage 2: {f2:%d %b %Y}" if pd.notna(f2) else "Stage 2: -")
        bpp, tot, npj = r.get("budget_per_project_eur"), r.get("total_budget_eur"), r.get("num_projects")
        lines.append(f"Budget per project: {bpp:,.0f} EUR" if pd.notna(bpp) else "Budget per project: -")
        lines.append(f"Total budget: {tot:,.0f} EUR" if pd.notna(tot) else "Total budget: -")
        lines.append(f"# Projects: {int(npj) if pd.notna(npj) else '-'}")
        doc.add_paragraph("\n".join(lines))
        notes = (notes_by_code or {}).get(str(r.get("code","")), "")
        doc.add_heading("Notes", level=2); doc.add_paragraph(notes if notes else "-")

    bio = io.BytesIO(); doc.save(bio); bio.seek(0); return bio.getvalue()

# ----------------------------------- UI -----------------------------------
st.set_page_config(page_title="Funding Dashboard – TWO Gantts", layout="wide")

st.title("Funding Dashboard — Horizon & Erasmus (two Gantts)")

upl = st.file_uploader("Upload Excel (.xlsx)", type=["xlsx"])
if not upl:
    st.stop()

# Detect sheets and allow override
sheets = get_sheet_names(upl.getvalue())
c1, c2 = st.columns(2)
with c1:
    hz_sheet = st.selectbox("Horizon sheet", options=sheets, index=0)
with c2:
    er_sheet = st.selectbox("Erasmus sheet", options=sheets, index=min(1, len(sheets)-1))

# Load each programme independently
df_h = load_programme(upl.getvalue(), hz_sheet, "Horizon Europe")
df_e = load_programme(upl.getvalue(), er_sheet, "Erasmus+")

# Build filter options using combined *choices*, but we’ll filter per-programme later
df_all = pd.concat([df_h.assign(programme="Horizon Europe"),
                    df_e.assign(programme="Erasmus+")], ignore_index=True)

open_lo, open_hi = safe_bounds(df_all["opening_date"])
dead_all = pd.concat([
    pd.to_datetime(df_all.get("deadline"), errors="coerce"),
    pd.to_datetime(df_all.get("first_deadline"), errors="coerce"),
    pd.to_datetime(df_all.get("second_deadline"), errors="coerce"),
], axis=0)
dead_lo, dead_hi = safe_bounds(dead_all)

prog_opts = ["Horizon Europe","Erasmus+"]
cluster_opts = sorted([c for c in df_all.get("cluster", pd.Series(dtype=object)).dropna().unique().tolist() if c!=""])
type_opts    = sorted([t for t in df_all.get("type_of_action", pd.Series(dtype=object)).dropna().unique().tolist() if t!=""])
trl_opts     = sorted([str(int(x)) for x in df_all.get("trl", pd.Series(dtype=float)).dropna().unique() if pd.notna(x)])
dest_opts    = sorted([d for d in df_all.get("destination_or_strand", pd.Series(dtype=object)).dropna().unique().tolist() if d!=""])

with st.form("filters", clear_on_submit=False):
    st.header("Filters")
    a,b,c = st.columns(3)
    with a: programmes = st.multiselect("Programme", prog_opts, default=prog_opts)
    with b: clusters   = st.multiselect("Cluster", cluster_opts)
    with c: dests      = st.multiselect("Destination / Strand", dest_opts)
    d,e = st.columns(2)
    with d: types      = st.multiselect("Type of Action", type_opts)
    with e: trls       = st.multiselect("TRL", trl_opts)

    r1,r2,r3,r4 = st.columns([2,2,2,1])
    with r1: kw1 = st.text_input("Keyword 1")
    with r2: kw2 = st.text_input("Keyword 2")
    with r3: kw3 = st.text_input("Keyword 3")
    with r4: combine_mode = st.radio("Combine", ["AND","OR"], index=0, horizontal=True)
    title_code_only = st.checkbox("Search only Title & Code", value=True)

    d1,d2,d3,d4 = st.columns(4)
    with d1: open_start = st.date_input("Open from",  value=open_lo,  min_value=open_lo,  max_value=open_hi)
    with d2: open_end   = st.date_input("Open to",    value=open_hi,  min_value=open_lo,  max_value=open_hi)
    with d3: close_from = st.date_input("Close from", value=dead_lo,  min_value=dead_lo,  max_value=dead_hi)
    with d4: close_to   = st.date_input("Close to",   value=dead_hi,  min_value=dead_lo,  max_value=dead_hi)

    # budget slider uses combined range
    bud_series = pd.to_numeric(df_all.get("budget_per_project_eur"), errors="coerce").dropna()
    if bud_series.empty:
        min_bud, max_bud = 0.0, 1_000_000.0
    else:
        min_bud, max_bud = float(bud_series.min()), float(bud_series.max())
        if not (min_bud < max_bud):
            min_bud, max_bud = max(min_bud, 0.0), min_bud + 100000.0
    rng = max_bud - min_bud
    step = max(1e4, round(rng / 50, -3)) if rng else 10000.0
    budget_range = st.slider("Budget per project (EUR)", min_bud, max_bud, (min_bud, max_bud), step=step)

    applied = st.form_submit_button("Apply filters")

# persist criteria
if "crit" not in st.session_state: st.session_state.crit = {}
if applied:
    st.session_state.crit = dict(
        programmes=programmes, clusters=clusters, dests=dests, types=types, trls=trls,
        kw1=kw1, kw2=kw2, kw3=kw3, combine_mode=combine_mode, title_code_only=title_code_only,
        open_start=open_start, open_end=open_end, close_from=close_from, close_to=close_to,
        budget_range=budget_range
    )
crit = st.session_state.crit or dict(
    programmes=prog_opts, clusters=[], dests=[], types=[], trls=[],
    kw1="", kw2="", kw3="", combine_mode="OR", title_code_only=False,
    open_start=open_lo, open_end=open_hi, close_from=dead_lo, close_to=dead_hi,
    budget_range=(0.0, 1_000_000.0)
)

# filter helper
def apply_filters(df0: pd.DataFrame) -> pd.DataFrame:
    df = df0.copy()
    # keywords
    terms = [crit["kw1"], crit["kw2"], crit["kw3"]]
    terms = [t.strip().lower() for t in terms if t and str(t).strip()]
    hay = df["_search_title"] if crit["title_code_only"] else df["_search_all"]
    if terms:
        if crit["combine_mode"] == "AND":
            pattern = "".join([f"(?=.*{re.escape(t)})" for t in terms]) + ".*"
        else:
            pattern = "(" + "|".join(re.escape(t) for t in terms) + ")"
        df = df[hay.str.contains(pattern, regex=True, na=False)]
    # categorical
    if crit["clusters"]: df = df[df.get("cluster").isin(crit["clusters"])]
    if crit["dests"]:    df = df[df.get("destination_or_strand").isin(crit["dests"])]
    if crit["types"]:    df = df[df.get("type_of_action").isin(crit["types"])]
    if crit["trls"]:     df = df[df.get("trl").dropna().astype("Int64").astype(str).isin(crit["trls"])]
    # dates
    df = df[df["opening_date"].between(pd.to_datetime(crit["open_start"]), pd.to_datetime(crit["open_end"]), inclusive="both")]
    df = df[df["closing_date_any"].between(pd.to_datetime(crit["close_from"]), pd.to_datetime(crit["close_to"]), inclusive="both")]
    # budget
    lo, hi = crit["budget_range"]
    df = df[df.get("budget_per_project_eur").fillna(0).between(lo, hi)]
    return df

# apply per-programme filters (no merge)
show_hz = "Horizon Europe" in crit["programmes"]
show_er = "Erasmus+" in crit["programmes"]

fh = apply_filters(df_h) if show_hz else pd.DataFrame(columns=df_h.columns)
fe = apply_filters(df_e) if show_er else pd.DataFrame(columns=df_e.columns)

st.caption(f"Rows after filters — Horizon: {len(fh)} | Erasmus: {len(fe)}")

# ------------------------------ Tabs ------------------------------
tab1, tab2, tab3, tab4 = st.tabs(["📅 Gantt(s)", "📋 Tables", "📚 Full Data", "📝 Shortlist"])

with tab1:
    st.subheader("Gantt — one bar per row (Opening → Deadline); thin markers for Stage 1/2")
    cols = st.columns(2) if (show_hz and show_er) else [st.container()]
    # Horizon
    if show_hz:
        g_h = build_singlebar_rows(fh)
        with cols[0]:
            st.markdown("### Horizon Europe")
            if g_h.empty: st.info("No valid Horizon rows/dates.")
            else:
                st.altair_chart(gantt_singlebar_chart(g_h, color_field="type_of_action"), use_container_width=True)
    # Erasmus
    if show_er:
        g_e = build_singlebar_rows(fe)
        with (cols[1] if (show_hz and len(cols) > 1) else cols[0]):
            st.markdown("### Erasmus+")
            if g_e.empty: st.info("No valid Erasmus rows/dates.")
            else:
                st.altair_chart(gantt_singlebar_chart(g_e, color_field="type_of_action"), use_container_width=True)

with tab2:
    st.subheader("Tables by Programme")
    show_cols = [c for c in DISPLAY_COLS if c in df_h.columns or c in df_e.columns]
    if show_hz:
        with st.expander(f"Horizon Europe — {len(fh)} rows", expanded=True):
            st.dataframe(fh[show_cols], use_container_width=True, hide_index=True)
    if show_er:
        with st.expander(f"Erasmus+ — {len(fe)} rows", expanded=True):
            st.dataframe(fe[show_cols], use_container_width=True, hide_index=True)

with tab3:
    st.subheader("Full Data (expand rows)")
    def render_rows(df_in: pd.DataFrame, header: str):
        st.markdown(f"### {header}")
        for _, r in df_in.iterrows():
            label = f"{str(r.get('code') or '')} — {str(r.get('title') or '')}".strip(" —")
            with st.expander(label or "(untitled)"):
                c1, c2 = st.columns(2)
                with c1:
                    if pd.notna(r.get("opening_date")):
                        st.markdown(f"📅 **Opening:** {r['opening_date']:%d %b %Y}")
                    if pd.notna(r.get("deadline")):
                        st.markdown(f"⏳ **Deadline:** {r['deadline']:%d %b %Y}")
                    if r.get("two_stage"):
                        if pd.notna(r.get("first_deadline")):
                            st.markdown(f"🔄 **Stage 1:** {r['first_deadline']:%d %b %Y}")
                        if pd.notna(r.get("second_deadline")):
                            st.markdown(f"🔄 **Stage 2:** {r['second_deadline']:%d %b %Y}")
                with c2:
                    if pd.notna(r.get("budget_per_project_eur")):
                        st.markdown(f"💶 **Budget/Project:** {r['budget_per_project_eur']:,.0f} EUR")
                    if pd.notna(r.get("total_budget_eur")):
                        st.markdown(f"📦 **Total:** {r['total_budget_eur']:,.0f} EUR")
                    if pd.notna(r.get("num_projects")):
                        st.markdown(f"📊 **# Projects:** {int(r['num_projects'])}")
                st.caption(
                    f"🏷️ Programme: {r.get('programme','-')} | Cluster: {r.get('cluster','-')} | "
                    f"Destination: {r.get('destination_or_strand','-')} | Type: {r.get('type_of_action','-')} | TRL: {r.get('trl','-')}"
                )

    if show_hz: render_rows(fh, "Horizon Europe")
    if show_er: render_rows(fe, "Erasmus+")

with tab4:
    st.subheader("Shortlist & Notes (DOCX)")
    if "sel" not in st.session_state: st.session_state.sel = set()
    if "notes" not in st.session_state: st.session_state.notes = {}

    combined = []
    if show_hz: combined.append(fh.assign(programme="Horizon Europe"))
    if show_er: combined.append(fe.assign(programme="Erasmus+"))
    ff = pd.concat(combined, ignore_index=True) if combined else pd.DataFrame()

    st.markdown("**Select calls**")
    for _, r in ff.sort_values(["closing_date_any","opening_date"]).iterrows():
        code = str(r.get("code") or ""); title = str(r.get("title") or "")
        label = f"{code} — {title}".strip(" —")
        checked = code in st.session_state.sel
        new = st.checkbox(label or "(untitled)", value=checked, key=f"sel_{code}_{_}")
        if new and not checked: st.session_state.sel.add(code)
        elif (not new) and checked: st.session_state.sel.discard(code)

    selected_df = ff[ff["code"].astype(str).isin(st.session_state.sel)]
    if not selected_df.empty:
        st.markdown("---")
        for _, r in selected_df.iterrows():
            code = str(r.get("code") or "")
            default = st.session_state.notes.get(code, "")
            st.session_state.notes[code] = st.text_area(f"Notes — {code}", value=default, height=100, key=f"note_{code}")

        colA, colB = st.columns(2)
        with colA: title = st.text_input("Report title", value="Funding Report – Shortlist")
        with colB: pass

        if st.button("📄 Generate DOCX"):
            try:
                if DOCX_AVAILABLE:
                    data = generate_docx_report(selected_df, st.session_state.notes, title=title)
                    st.download_button("⬇️ Download .docx", data=data,
                                       file_name="funding_report.docx",
                                       mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
                else:
                    st.error("python-docx not installed in this environment.")
            except Exception as e:
                st.error(f"Failed to generate report: {e}")
    else:
        st.info("Select at least one call to add notes and export.")
