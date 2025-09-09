# app_b3_2.py — Streamlit Funding Dashboard
# Two separate filter blocks (Horizon & Erasmus), year "buttons", and stacked Gantts

from __future__ import annotations

import io
import re
from datetime import datetime
from typing import List, Dict, Optional, Tuple

import pandas as pd
import streamlit as st
import altair as alt

# ---------------------- Column mapping (tune if headers differ) ----------------------
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

SEARCHABLE_COLUMNS = (
    "code","title","call_name","expected_outcome","scope","full_text",
    "cluster","destination_or_strand","type_of_action","trl"
)

DISPLAY_COLS = [
    "programme","code","title","opening_date","deadline",
    "first_deadline","second_deadline","two_stage",
    "cluster","destination_or_strand","type_of_action","trl",
    "budget_per_project_eur","total_budget_eur","num_projects",
    "call_name","version_label","source_filename","parsed_on_utc",
]


# --------------------------------- Helpers ---------------------------------
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

    # 2) programme
    df["programme"] = programme_name

    # 3) numerics
    for c in ("budget_per_project_eur","total_budget_eur","trl","num_projects"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # 4) dates
    for c in ("opening_date","deadline","first_deadline","second_deadline"):
        if c in df.columns:
            df[c] = safe_date_series(df[c])

    # 5) two-stage
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

def wrap_label(text: str, width=60, max_lines=3) -> str:
    s = str(text or "")
    chunks = [s[i:i+width] for i in range(0, len(s), width)]
    return "\n".join(chunks[:max_lines]) if chunks else "—"

def unique_y_labels(g: pd.DataFrame) -> pd.Series:
    """Guarantee unique Y labels to prevent Altair stacking."""
    if "code" in g.columns and g["code"].notna().any():
        base = g["code"].fillna("").astype(str)
    elif "title" in g.columns and g["title"].notna().any():
        base = g["title"].fillna("").astype(str)
    else:
        base = pd.Series([f"row-{i}" for i in range(len(g))], index=g.index)
    dup = base.duplicated(keep=False)
    if dup.any():
        base = base + g.groupby(base).cumcount().astype(str).radd("#")
    return base.map(lambda s: wrap_label(s, width=100, max_lines=5))

def build_month_bands(min_x: pd.Timestamp, max_x: pd.Timestamp) -> pd.DataFrame:
    start = pd.Timestamp(min_x).to_period("M").start_time
    end   = (pd.Timestamp(max_x).to_period("M") + 1).start_time
    months = pd.date_range(start, end, freq="MS")
    rows = []
    for i in range(len(months)-1):
        rows.append({"start": months[i], "end": months[i+1], "band": i % 2})
    return pd.DataFrame(rows)

def gantt_singlebar_chart(g: pd.DataFrame, color_field: str = "type_of_action", title: str = ""):
    if g.empty:
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
            alt.Tooltip("opening_date:T", title="Opening", format="%d %b %Y"),
            alt.Tooltip("deadline:T", title="Deadline", format="%d %b %Y"),
        ]
    )

    # optional thin markers for Stage 1/2 (no extra bars)
    rule1 = alt.Chart(g[g.get("first_deadline").notna()]).mark_rule(size=1, color="#00000030").encode(
        x="first_deadline:T", y="y_label:N"
    ) if "first_deadline" in g.columns else None
    rule2 = alt.Chart(g[g.get("second_deadline").notna()]).mark_rule(size=1, color="#00000030").encode(
        x="second_deadline:T", y="y_label:N"
    ) if "second_deadline" in g.columns else None

    start_labels = base.mark_text(align="right", dx=-4, dy=5, fontSize=10, color="#111").encode(
        x="opening_date:T", text=alt.Text("opening_date:T", format="%d %b %Y"))
    end_labels = base.mark_text(align="left", dx=4, dy=5, fontSize=10, color="#111").encode(
        x="deadline:T", text=alt.Text("deadline:T", format="%d %b %Y"))
    inbar = base.mark_text(align="left", baseline="bottom", dx=2, dy=-(int(bar_size/2)+4), color="black").encode(
        x=alt.X("opening_date:T", scale=alt.Scale(domain=[domain_min, domain_max]), axis=None),
        text="title_inbar:N",
        opacity=alt.condition(alt.datum.bar_days >= 10, alt.value(1), alt.value(0))
    )

    layers = [month_shade, month_grid, bars, start_labels, end_labels, inbar, month_labels, today_rule, today_label]
    if rule1 is not None: layers.append(rule1)
    if rule2 is not None: layers.append(rule2)

    chart = alt.layer(*layers).properties(
        height=max(800, len(y_order)*row_h), width='container',
        padding={"top":50,"bottom":30,"left":10,"right":10}
    ).configure_axis(
        grid=False, domain=True, domainWidth=1
    ).configure_view(
        continuousHeight=500, continuousWidth=500, strokeWidth=0, clip=False
    ).interactive(bind_x=True)

    return chart if not title else chart.properties(title=title)


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


# ---------------------------- Filter widgets utils ----------------------------
def year_buttons(label: str, years: List[int], state_key: str) -> List[int]:
    """
    Render a compact row of checkbox 'buttons' for years.
    Returns the list of selected years, stored in st.session_state[state_key].
    """
    years = sorted([int(y) for y in set(years) if pd.notna(y)])
    if state_key not in st.session_state:
        st.session_state[state_key] = years[:]  # default: all selected
    sel = set(st.session_state[state_key])

    st.caption(label)
    cols = st.columns(min(8, max(1, len(years)))) if years else [st.container()]
    # distribute across rows of 8
    for i, y in enumerate(years):
        c = cols[i % len(cols)]
        with c:
            ck = st.checkbox(str(y), value=(y in sel), key=f"{state_key}_{y}")
            if ck: sel.add(y)
            else:
                if y in sel: sel.remove(y)

    st.session_state[state_key] = sorted(sel)
    return sorted(sel)


def apply_local_filters(
    df0: pd.DataFrame,
    kw_terms: List[str],
    kw_mode: str,
    title_code_only: bool,
    clusters: List[str],
    dests: List[str],
    types: List[str],
    trls: List[str],
    budget_range: Tuple[float,float],
    open_years: List[int],
    deadline_years: List[int],
) -> pd.DataFrame:
    df = df0.copy()

    # keywords
    terms = [t.strip().lower() for t in kw_terms if t and str(t).strip()]
    hay = df["_search_title"] if title_code_only else df["_search_all"]
    if terms:
        if kw_mode == "AND":
            pattern = "".join([f"(?=.*{re.escape(t)})" for t in terms]) + ".*"
        else:
            pattern = "(" + "|".join(re.escape(t) for t in terms) + ")"
        df = df[hay.str.contains(pattern, regex=True, na=False)]

    # categorical
    if clusters: df = df[df.get("cluster").isin(clusters)]
    if dests:    df = df[df.get("destination_or_strand").isin(dests)]
    if types:    df = df[df.get("type_of_action").isin(types)]
    if trls:     df = df[df.get("trl").dropna().astype("Int64").astype(str).isin(trls)]

    # budgets
    lo, hi = budget_range
    df = df[df.get("budget_per_project_eur").fillna(0).between(lo, hi)]

    # year buttons
    if open_years:
        df = df[pd.to_datetime(df["opening_date"], errors="coerce").dt.year.isin(open_years)]
    if deadline_years:
        df = df[pd.to_datetime(df["deadline"], errors="coerce").dt.year.isin(deadline_years)]

    return df


def singlebar_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Prep for Gantt: one bar per row, with unique y_label and stage markers."""
    g = df.copy()
    if g.empty:
        return g
    g = g[pd.notna(g["opening_date"]) & pd.notna(g["deadline"]) & (g["opening_date"] <= g["deadline"])].copy()
    g["y_label"] = unique_y_labels(g)
    g["title_inbar"] = g.get("title","").astype(str).map(lambda s: wrap_label(s, width=100, max_lines=3))
    g["bar_days"] = (g["deadline"] - g["opening_date"]).dt.days
    g["mid"] = g["opening_date"] + (g["deadline"] - g["opening_date"]) / 2
    return g.sort_values(["deadline","opening_date"])


# ----------------------------------- UI -----------------------------------
st.set_page_config(page_title="Funding Dashboard – app_b3_2", layout="wide")
st.title("Funding Dashboard — Horizon & Erasmus (app_b3_2)")

upl = st.file_uploader("Upload Excel (.xlsx)", type=["xlsx"])
if not upl:
    st.stop()

# Sheet selectors (explicit, to avoid mis-detection)
sheets = get_sheet_names(upl.getvalue())
c1, c2 = st.columns(2)
with c1:
    hz_sheet = st.selectbox("Horizon sheet", options=sheets, index=0)
with c2:
    er_sheet = st.selectbox("Erasmus sheet", options=sheets, index=min(1, len(sheets)-1))

# Load independently
df_h = load_programme(upl.getvalue(), hz_sheet, "Horizon Europe")
df_e = load_programme(upl.getvalue(), er_sheet, "Erasmus+")

# ---------------------------- Horizon filters block ----------------------------
st.markdown("## 🔵 Horizon Europe — Filters")
prog = "Horizon Europe"
df0 = df_h

# choices from Horizon only
cluster_opts_h = sorted([c for c in df0.get("cluster", pd.Series(dtype=object)).dropna().unique().tolist() if c])
type_opts_h    = sorted([t for t in df0.get("type_of_action", pd.Series(dtype=object)).dropna().unique().tolist() if t])
trl_opts_h     = sorted([str(int(x)) for x in df0.get("trl", pd.Series(dtype=float)).dropna().unique() if pd.notna(x)])
dest_opts_h    = sorted([d for d in df0.get("destination_or_strand", pd.Series(dtype=object)).dropna().unique().tolist() if d])

with st.form("filters_hz"):
    a,b,c = st.columns(3)
    with a: clusters_h = st.multiselect("Cluster (Horizon)", cluster_opts_h)
    with b: dests_h    = st.multiselect("Destination/Strand (Horizon)", dest_opts_h)
    with c: types_h    = st.multiselect("Type of Action (Horizon)", type_opts_h)

    d,e = st.columns(2)
    with d: trls_h = st.multiselect("TRL (Horizon)", trl_opts_h)
    with e:
        title_code_only_h = st.checkbox("Search only Title & Code", value=True)
        kw_mode_h = st.radio("Keyword combine", ["AND","OR"], index=0, horizontal=True)

    r1,r2,r3 = st.columns([2,2,2])
    with r1: kw1_h = st.text_input("Keyword 1 (Horizon)")
    with r2: kw2_h = st.text_input("Keyword 2 (Horizon)")
    with r3: kw3_h = st.text_input("Keyword 3 (Horizon)")

    # budget slider from Horizon only
    bud_series_h = pd.to_numeric(df0.get("budget_per_project_eur"), errors="coerce").dropna()
    if bud_series_h.empty:
        min_bud_h, max_bud_h = 0.0, 1_000_000.0
    else:
        min_bud_h, max_bud_h = float(bud_series_h.min()), float(bud_series_h.max())
        if not (min_bud_h < max_bud_h):
            min_bud_h, max_bud_h = max(min_bud_h, 0.0), min_bud_h + 100000.0
    rng_h = max_bud_h - min_bud_h
    step_h = max(1e4, round(rng_h/50, -3)) if rng_h else 10000.0
    budget_h = st.slider("Budget per project (EUR, Horizon)", min_bud_h, max_bud_h, (min_bud_h, max_bud_h), step=step_h)

    submitted_h = st.form_submit_button("Apply Horizon filters")

# Year buttons (outside form so they react instantly)
open_years_h = year_buttons("Opening years (Horizon)", df_h["opening_date"].dt.year.dropna().astype(int).tolist(), "open_years_h")
deadline_years_h = year_buttons("Deadline years (Horizon)", df_h["deadline"].dt.year.dropna().astype(int).tolist(), "deadline_years_h")

# Apply Horizon filters
fh = apply_local_filters(
    df_h,
    [kw1_h, kw2_h, kw3_h],
    kw_mode_h,
    title_code_only_h,
    clusters_h, dests_h, types_h, trls_h,
    budget_h,
    open_years_h, deadline_years_h
)

st.caption(f"Horizon rows after filters: {len(fh)}")

# ---------------------------- Erasmus filters block ----------------------------
st.markdown("## 🟣 Erasmus+ — Filters")
prog = "Erasmus+"
df0 = df_e

cluster_opts_e = sorted([c for c in df0.get("cluster", pd.Series(dtype=object)).dropna().unique().tolist() if c])
type_opts_e    = sorted([t for t in df0.get("type_of_action", pd.Series(dtype=object)).dropna().unique().tolist() if t])
trl_opts_e     = sorted([str(int(x)) for x in df0.get("trl", pd.Series(dtype=float)).dropna().unique() if pd.notna(x)])
dest_opts_e    = sorted([d for d in df0.get("destination_or_strand", pd.Series(dtype=object)).dropna().unique().tolist() if d])

with st.form("filters_er"):
    a,b,c = st.columns(3)
    with a: clusters_e = st.multiselect("Cluster (Erasmus)", cluster_opts_e)
    with b: dests_e    = st.multiselect("Destination/Strand (Erasmus)", dest_opts_e)
    with c: types_e    = st.multiselect("Type of Action (Erasmus)", type_opts_e)

    d,e = st.columns(2)
    with d: trls_e = st.multiselect("TRL (Erasmus)", trl_opts_e)
    with e:
        title_code_only_e = st.checkbox("Search only Title & Code (Erasmus)", value=True)
        kw_mode_e = st.radio("Keyword combine (Erasmus)", ["AND","OR"], index=0, horizontal=True)

    r1,r2,r3 = st.columns([2,2,2])
    with r1: kw1_e = st.text_input("Keyword 1 (Erasmus)")
    with r2: kw2_e = st.text_input("Keyword 2 (Erasmus)")
    with r3: kw3_e = st.text_input("Keyword 3 (Erasmus)")

    # budget slider from Erasmus only
    bud_series_e = pd.to_numeric(df0.get("budget_per_project_eur"), errors="coerce").dropna()
    if bud_series_e.empty:
        min_bud_e, max_bud_e = 0.0, 1_000_000.0
    else:
        min_bud_e, max_bud_e = float(bud_series_e.min()), float(bud_series_e.max())
        if not (min_bud_e < max_bud_e):
            min_bud_e, max_bud_e = max(min_bud_e, 0.0), min_bud_e + 100000.0
    rng_e = max_bud_e - min_bud_e
    step_e = max(1e4, round(rng_e/50, -3)) if rng_e else 10000.0
    budget_e = st.slider("Budget per project (EUR, Erasmus)", min_bud_e, max_bud_e, (min_bud_e, max_bud_e), step=step_e)

    submitted_e = st.form_submit_button("Apply Erasmus filters")

# Year buttons (outside form so they react instantly)
open_years_e = year_buttons("Opening years (Erasmus)", df_e["opening_date"].dt.year.dropna().astype(int).tolist(), "open_years_e")
deadline_years_e = year_buttons("Deadline years (Erasmus)", df_e["deadline"].dt.year.dropna().astype(int).tolist(), "deadline_years_e")

# Apply Erasmus filters
fe = apply_local_filters(
    df_e,
    [kw1_e, kw2_e, kw3_e],
    kw_mode_e,
    title_code_only_e,
    clusters_e, dests_e, types_e, trls_e,
    budget_e,
    open_years_e, deadline_years_e
)

st.caption(f"Erasmus rows after filters: {len(fe)}")

# ------------------------------ Tabs ------------------------------
tab1, tab2, tab3 = st.tabs(["📅 Gantt(s)", "📋 Tables", "📤 Export"])

with tab1:
    st.subheader("Gantt — one bar per row (Opening → Deadline); thin markers for Stage 1/2")
    # STACKED: Horizon first, then Erasmus
    g_h = singlebar_rows(fh)
    st.markdown("### Horizon Europe")
    if g_h.empty:
        st.info("No valid Horizon rows/dates.")
    else:
        st.altair_chart(gantt_singlebar_chart(g_h, color_field="type_of_action"), use_container_width=True)

    st.markdown("---")
    g_e = singlebar_rows(fe)
    st.markdown("### Erasmus+")
    if g_e.empty:
        st.info("No valid Erasmus rows/dates.")
    else:
        st.altair_chart(gantt_singlebar_chart(g_e, color_field="type_of_action"), use_container_width=True)

with tab2:
    st.subheader("Tables (per programme)")
    show_cols_h = [c for c in DISPLAY_COLS if c in fh.columns]
    show_cols_e = [c for c in DISPLAY_COLS if c in fe.columns]

    with st.expander(f"Horizon Europe — {len(fh)} rows", expanded=True):
        st.dataframe(fh[show_cols_h], use_container_width=True, hide_index=True)
    with st.expander(f"Erasmus+ — {len(fe)} rows", expanded=True):
        st.dataframe(fe[show_cols_e], use_container_width=True, hide_index=True)

with tab3:
    st.subheader("Quick export (filtered)")
    cA, cB = st.columns(2)
    with cA:
        if not fh.empty:
            out_h = io.BytesIO()
            with pd.ExcelWriter(out_h, engine="openpyxl") as xw:
                fh.to_excel(xw, index=False, sheet_name="Horizon")
            out_h.seek(0)
            st.download_button("⬇️ Download Horizon (Excel)", out_h,
                               file_name=f"horizon_filtered_{datetime.utcnow():%Y%m%d}.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        else:
            st.caption("No Horizon rows to export.")
    with cB:
        if not fe.empty:
            out_e = io.BytesIO()
            with pd.ExcelWriter(out_e, engine="openpyxl") as xw:
                fe.to_excel(xw, index=False, sheet_name="Erasmus")
            out_e.seek(0)
            st.download_button("⬇️ Download Erasmus (Excel)", out_e,
                               file_name=f"erasmus_filtered_{datetime.utcnow():%Y%m%d}.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        else:
            st.caption("No Erasmus rows to export.")
