# app_b.py — Altair Gantt (stable, tidier filters)
from __future__ import annotations
import io
import pandas as pd
import streamlit as st
import altair as alt
import re

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

def nl_to_br(s: str) -> str:
    return "" if not s else s.replace("\n", "<br>")

def clean_footer(text: str) -> str:
    """
    Remove footer lines like:
    '... Work Programme 2026-2027 ... Page xx of yy ...'
    even if embedded in a longer line.
    """
    if not text:
        return ""

    # Match "Work Programme ... Page <num> of <num>"
    footer_pattern = re.compile(
        r"Horizon\s*Europe\s*[-–]?\s*Work Programme.*?Page\s+\d+\s+of\s+\d+",
        re.IGNORECASE | re.DOTALL
    )

    cleaned = footer_pattern.sub("", text)

    # Normalise whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def normalize_bullets(text: str) -> str:
    """Replace odd bullets with clean Markdown dashes and add newlines."""
    if not text:
        return ""

    # Replace common oddball bullets
    for ch in ["▪", "◦", "●", "•", ""]:
        text = text.replace(ch, "- ")

    # Normalise whitespace
    text = re.sub(r"\s+", " ", text)

    # Add line breaks before bullets and numbered lists
    text = re.sub(r"(\s*[-*]\s+)", r"\n\1", text)
    text = re.sub(r"(\s*\d+\.\s+)", r"\n\1", text)

    return text.strip()


def highlight_text(text: str, keywords: list[str], colours=None) -> str:
    """Return text with keywords highlighted using HTML span tags."""
    if not text:
        return ""

    # Filter keywords: only keep non-empty strings
    clean_keywords = [str(k).strip() for k in keywords if k and str(k).strip()]
    if not clean_keywords:
        return text

    if colours is None:
        colours = ["#ffff00", "#a0e7e5", "#ffb3b3"]  # yellow, teal, pink

    highlighted = str(text)
    for i, kw in enumerate(clean_keywords):
        colour = colours[i % len(colours)]
        pattern = re.compile(re.escape(kw), re.IGNORECASE)
        highlighted = pattern.sub(
            lambda m: f"<span style='background-color:{colour}; font-weight:bold;'>{m.group(0)}</span>",
            highlighted
        )
    return highlighted


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

def wrap_label(text: str, width=50, max_lines=3) -> str:
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
        y_label = wrap_label(f"{code}", width=100, max_lines=5)
        type_of_action = r.get("type_of_action"),  # 👈 add this
        prog = r.get("programme")
        open_dt   = r.get("opening_date")
        final_dt  = r.get("deadline")
        first_dt  = r.get("first_deadline")
        second_dt = r.get("second_deadline")
        two_stage = bool(r.get("two_stage"))
        title_inbar = wrap_label(title, width=100, max_lines=3)

        if two_stage:
            if pd.notna(open_dt) and pd.notna(first_dt) and open_dt <= first_dt:
                bar_days = (first_dt - open_dt).days
                rows.append({
                    "y_label": y_label, "programme": prog,
                    "start": open_dt, "end": first_dt,
                    "segment": "Stage 1",
                    "title": title, "title_inbar": title_inbar,
                    "budget_per_project_eur": r.get("budget_per_project_eur"),
                    "type_of_action" : r.get("type_of_action"),  # 👈 add this
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
                    "title": title,"title_inbar": "",
                    "budget_per_project_eur": r.get("budget_per_project_eur"),
                    "type_of_action" : r.get("type_of_action"),  # 👈 add this
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
                    "type_of_action" : r.get("type_of_action"),  # 👈 add this
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
    chart_height = max(1500, unique_rows * row_height)
    domain_min = seg["start"].min()
    domain_max = seg["end"].max()
    min_x = min(seg["start"].min(), seg["end"].min())
    max_x = max(seg["start"].max(), seg["end"].max())
    bands_df = build_month_bands(min_x, max_x)
    month_shade = (
        alt.Chart(bands_df)
        .mark_rect(tooltip=False)
        .encode(
            x=alt.X("start:T"),
            x2=alt.X2("end:T"),
            opacity=alt.Opacity("band:Q",
                                scale=alt.Scale(domain=[0,1], range=[0.0, 0.08]),
                                legend=None),
            color=alt.value("#9AC0CD")
        )
    )
    
    months = pd.date_range(pd.Timestamp(min_x).to_period("M").start_time,
                           pd.Timestamp(max_x).to_period("M").end_time,
                           freq="MS")
    month_grid = alt.Chart(pd.DataFrame({"t": months})).mark_rule(stroke="#FFF", strokeWidth=1.5).encode(x="t:T")

    month_labels_df = pd.DataFrame({
        "month": months[:-1],
        "next_month": months[1:],
        "label": [m.strftime("%b %Y") for m in months[:-1]]
    })
    month_labels_df["mid"] = month_labels_df["month"] + (
        (month_labels_df["next_month"] - month_labels_df["month"]) / 2
    )

    month_labels = alt.Chart(month_labels_df).mark_text(
        align="center",
        baseline="top",
        dy=-20,
        fontSize=12,
        fontWeight="bold",
    ).encode(
        x="mid:T",      # 👈 midpoint, not the start
        text="label:N",
        y=alt.value(-10)  # fixed position at top
    )

        
    base = alt.Chart(seg).encode(
        y=alt.Y(
            "y_label:N",
            sort=y_order,
            axis=alt.Axis(
                title=None,
                labelLimit=200,
                labelFontSize=11,
                labelAlign="right",
                labelPadding=50,
                domain=True                
            ),
            scale=alt.Scale(domain=y_order, paddingInner=0.6, paddingOuter=0.05)
        )
    )   

    bars = alt.Chart(seg).mark_bar(cornerRadius=7, color="#1E90FF").encode(
        y=alt.Y(
            "y_label:N",
            sort=y_order,
            axis=alt.Axis(
                title=None,
                labelLimit=500,
                labelFontSize=13,
                labelAlign="right",
                labelPadding=100,
                domain=True,
                rangeStep=5    # 👈 max thickness per row in pixels
            ),
            scale=alt.Scale(domain=y_order)   # only band, no domain
        ),
        x=alt.X(
            "start:T",
            axis=alt.Axis(
                title=None,
                format="%b %Y",    # 👈 month + year
                tickCount="month",
                orient="top",      # 👈 force top
                labelFontSize=11,
                labelPadding=50,   # 👈 extra room for labels
                labelOverlap="greedy",   # <— allow Vega to hide some labels
                tickSize=6
            ),
            scale=alt.Scale(domain=[domain_min, domain_max])
        ),
        x2="end:T",
        color=alt.Color(
            "type_of_action:N",
            legend=alt.Legend(title="Type of Action",
                              orient="left",
                              offset=100),
            scale=alt.Scale(scheme="set2")   # 👈 bright, categorical palette
        ),
        opacity=alt.condition(
            alt.datum.segment == "Stage 2",
            alt.value(0.7),  # Stage 2 slightly darker
            alt.value(1.0)   # Stage 1/Single full colour
        ),
        tooltip=[
            alt.Tooltip("title:N", title="Title"),
            alt.Tooltip("programme:N", title="Programme"),
            alt.Tooltip("budget_per_project_eur:Q", title="Budget (€)", format=",.0f"),
            alt.Tooltip("start:T", title="Start", format="%d %b %Y"),
            alt.Tooltip("end:T", title="End", format="%d %b %Y")
        ]
    )
   
    start_labels = base.mark_text(align="right",
                                  dx=-4,
                                  dy=5,
                                  fontSize=10,
                                  color="#111")\
                       .encode(x="start:T",
                               text=alt.Text("start:T", format="%d %b %Y"))
    end_labels   = base.mark_text(align="left",
                                  dx=4,
                                  dy=5,
                                  fontSize=10,
                                  color="#111")\
                       .encode(x="end:T",
                               text=alt.Text("end:T", format="%d %b %Y"))
    text_cond = alt.condition(alt.datum.bar_days >= 10, alt.value(1), alt.value(0))
    inbar = base.mark_text(align="left",
                           baseline="middle",
                           dx=2,
                           dy=20,
                           fontSize=12, 
                           fill="black").encode(
        x=alt.X("start:T",
                scale=alt.Scale(domain=[domain_min, domain_max]),
                axis=None),
        text=alt.Text("title_inbar:N"),
        opacity=text_cond
    )

    chart = (
        month_shade + month_grid + bars + start_labels + end_labels + inbar + month_labels
    ).properties(
        height=chart_height + 75,
        width=7000,
        padding={"top": 50, "bottom": 30, "left": 10, "right": 10}  # 👈 correct way
    ).configure_axis(
        grid=False
    ).configure_view(
        continuousHeight=300,
        continuousWidth=500,
        strokeWidth=0,
        clip=False,
    ).resolve_scale(
        x='shared',
        y='shared'
    ).resolve_axis(
        x='shared',
        y='shared'
    )
    
    return chart

# ---------- UI ----------
st.set_page_config(page_title="Funding Dashboard", layout="wide")

import base64

file_path = "logo.png"
with open(file_path, "rb") as f:
    data = base64.b64encode(f.read()).decode("utf-8")

st.markdown(
    f"""
    <div style="text-align: center;">
        <img src="data:image/png;base64,{data}" width="250">
    </div>
    """,
    unsafe_allow_html=True
)

st.title("Funding Dashboard")

st.info(
    "📂 Please upload the latest parsed Excel file.\n\n"
    "➡️ You can find it in the following folder:\n\n"
    "- **3.SA Practices**\n"
    "   - Central Systems and Bid Management\n"
    "      - 1. Central Systems\n"
    "         - 2. CS EU PROGRAMMES Area\n"
    "            - 4. Horizon Europe\n"
    "               - CALENDAR OF CALLS FOR PROPOSALS\n"
    "                  - 4. WPs 2026-2027\n"
    "                     - Coding Davide\n\n"
    "👉 Look for *Horizon Calls - 26_27*.\n"
)

upl = st.file_uploader("Upload parsed Excel (.xlsx)", type=["xlsx"])
if not upl:
    st.stop()

xls = pd.ExcelFile(upl)
sheet = st.selectbox("Sheet", xls.sheet_names, index=0)
raw = pd.read_excel(xls, sheet_name=sheet)
df = canonicalise(raw)

# ----- Top filter form -----
with st.form("filters_form", clear_on_submit=False):
    st.header("Filters")

    prog_opts    = sorted([p for p in df["programme"].dropna().unique().tolist() if p != ""])
    cluster_opts = sorted([c for c in df.get("cluster", pd.Series(dtype=object)).dropna().unique().tolist() if c != ""])
    type_opts    = sorted([t for t in df.get("type_of_action", pd.Series(dtype=object)).dropna().unique().tolist() if t != ""])
    trl_opts     = sorted([str(int(x)) for x in df.get("trl", pd.Series(dtype=float)).dropna().unique() if pd.notna(x)])
    dest_opts    = sorted([d for d in df.get("destination_or_strand", pd.Series(dtype=object)).dropna().unique().tolist() if d != ""])

    col1, col2, col3 = st.columns(3)
    with col1:
        programmes = st.multiselect("Programme", options=prog_opts, default=prog_opts)
    with col2:
        clusters   = st.multiselect("Cluster", options=cluster_opts)
    with col3:
        dests      = st.multiselect("Destination / Strand", options=dest_opts)

    col4, col5 = st.columns(2)
    with col4:
        types      = st.multiselect("Type of Action", options=type_opts)
    with col5:
        trls       = st.multiselect("TRL", options=trl_opts)

    # Keyword row
    col6, col7, col8, col9 = st.columns([2,2,2,1])
    with col6:
        kw1 = st.text_input("Keyword 1")
    with col7:
        kw2 = st.text_input("Keyword 2")
    with col8:
        kw3 = st.text_input("Keyword 3")
    with col9:
        combine_mode = st.radio("Combine", ["AND", "OR"], horizontal=True, index=0)
    title_code_only = st.checkbox("Search only in Title & Code", value=True)

    # Date filters row
    open_lo, open_hi = safe_date_bounds(df.get("opening_date"))
    dead_all = pd.concat([
        pd.to_datetime(df.get("deadline"), errors="coerce"),
        pd.to_datetime(df.get("first_deadline"), errors="coerce"),
        pd.to_datetime(df.get("second_deadline"), errors="coerce"),
    ], axis=0)
    dead_lo, dead_hi = safe_date_bounds(dead_all)

    col10, col11, col12, col13 = st.columns(4)
    with col10:
        open_start = st.date_input("Open from", value=open_lo, min_value=open_lo, max_value=open_hi)
    with col11:
        open_end   = st.date_input("Open to",   value=open_hi, min_value=open_lo, max_value=open_hi)
    with col12:
        close_from = st.date_input("Close from", value=dead_lo, min_value=dead_lo, max_value=dead_hi)
    with col13:
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

    applied = st.form_submit_button("Apply filters")

# Persist criteria
if "criteria" not in st.session_state:
    st.session_state.criteria = {}
if applied:
    st.session_state.criteria = dict(
        programmes=programmes, clusters=clusters, types=types, trls=trls, dests=dests,
        kw1=kw1, kw2=kw2, kw3=kw3, combine_mode=combine_mode, title_code_only=title_code_only,
        open_start=open_start, open_end=open_end, close_from=close_from, close_to=close_to,
        budget_range=budget_range
    )

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
        budget_range=(0.0, 1_000_000.0)
    )

crit = st.session_state.criteria

# ---- Apply filters ----
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

# Tabs
tab1, tab2, tab3 = st.tabs(["📅 Gantt", "📋 Table", "📚 Full Data"])

with tab1:
    st.subheader("Gantt (Opening → Stage 1 → Stage 2 / Final)")
    segments = build_segments(f)
    if segments.empty:
        st.info("No rows with valid dates to display.")
    else:
        chart = build_altair_chart_from_segments(
            segments,
            view_start=crit["open_start"],
            view_end=crit["close_to"]
        )

        # Add scrollable container for chart
        st.markdown(
            """
            <style>
            .scroll-container {
                overflow-x: auto;
                overflow-y: auto;
                max-height: 1600px;   /* allow scroll if chart taller */
                padding: 25px;
            }
            </style>
            """,
            unsafe_allow_html=True
        )

        st.markdown('<div class="scroll-container">', unsafe_allow_html=True)
        st.altair_chart(chart, use_container_width=False)
        st.markdown('</div>', unsafe_allow_html=True)

with tab2:
    st.subheader("Filtered table")

    show_cols = [c for c in DISPLAY_COLS if c in f.columns]
    group_by_dest = st.checkbox("Group by Destination / Strand")

    if group_by_dest and "destination_or_strand" in f.columns:
        for dest, group_df in f.groupby("destination_or_strand"):
            with st.expander(f"Destination: {dest} ({len(group_df)} calls)"):
                st.dataframe(
                    group_df[show_cols],
                    use_container_width=True,
                    hide_index=True,
                    height=400
                )
    else:
        st.dataframe(
            f[show_cols],
            use_container_width=True,
            hide_index=True,
            height=800
        )
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as xw:
        f.to_excel(xw, index=False, sheet_name="filtered")
    out.seek(0)
    st.download_button("⬇️ Download filtered (Excel)", out,
                       file_name="calls_filtered.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

with tab3:
    st.subheader("Full data (expand rows)")

    # Collect keywords from criteria
    kw_list = [crit.get("kw1", ""), crit.get("kw2", ""), crit.get("kw3", "")]

    for _, row in f.iterrows():
        # Title line of the expander
        title = f"{row.get('code','')} — {row.get('title','')}"
        with st.expander(title):

            # --- Top section: Key dates and budgets in two columns
            col1, col2 = st.columns(2)
            with col1:
                if pd.notna(row.get("opening_date")):
                    st.markdown(f"📅 **Opening:** {row.get('opening_date'):%d %b %Y}")
                if pd.notna(row.get("deadline")):
                    st.markdown(f"⏳ **Deadline:** {row.get('deadline'):%d %b %Y}")
                if row.get("two_stage"):
                    if pd.notna(row.get("first_deadline")):
                        st.markdown(f"🔄 **Stage 1:** {row.get('first_deadline'):%d %b %Y}")
                    if pd.notna(row.get("second_deadline")):
                        st.markdown(f"🔄 **Stage 2:** {row.get('second_deadline'):%d %b %Y}")

            with col2:
                if pd.notna(row.get("budget_per_project_eur")):
                    st.markdown(f"💶 **Budget per project:** {row.get('budget_per_project_eur'):,.0f} EUR")
                if pd.notna(row.get("total_budget_eur")):
                    st.markdown(f"📦 **Total budget:** {row.get('total_budget_eur'):,.0f} EUR")
                if pd.notna(row.get("num_projects")):
                    st.markdown(f"📊 **# Projects:** {int(row.get('num_projects'))}")

            # --- Programme / cluster info
            st.markdown(
                f"🏷️ **Programme:** {row.get('programme','-')}  "
                f"| **Cluster:** {row.get('cluster','-')}  "
                f"| **Destination:** {row.get('destination_or_strand','-')}  "
                f"| **Type of Action:** {row.get('type_of_action','-')}  "
                f"| **TRL:** {row.get('trl','-')}"
            )

            # --- Expandable long text sections with bullet normalization + highlights

            if row.get("expected_outcome"):
                with st.expander("🎯 Expected Outcome"):
                    clean_text = normalize_bullets(clean_footer(row.get("expected_outcome")))
                    clean_text = nl_to_br(clean_text)  # turn \n into <br> for HTML rendering
                    st.markdown(
                        highlight_text(clean_text, kw_list),
                        unsafe_allow_html=True
                    )
            
            if row.get("scope"):
                with st.expander("🧭 Scope"):
                    clean_text = normalize_bullets(clean_footer(row.get("scope")))
                    clean_text = nl_to_br(clean_text)
                    st.markdown(
                        highlight_text(clean_text, kw_list),
                        unsafe_allow_html=True
                    )
            
            if row.get("full_text"):
                with st.expander("📖 Full Description"):
                    clean_text = normalize_bullets(clean_footer(row.get("full_text")))
                    clean_text = nl_to_br(clean_text)
                    st.markdown(
                        highlight_text(clean_text, kw_list),
                        unsafe_allow_html=True
                    )


            # --- Meta info at bottom
            st.caption(
                f"📂 Source: {row.get('source_filename','-')} "
                f"| Version: {row.get('version_label','-')} "
                f"| Parsed on: {row.get('parsed_on_utc','-')}"
            )

