# app_b3_0.py — Streamlit Funding Dashboard (Horizon + Erasmus; single-bar Altair Gantt)
from __future__ import annotations

import io
import base64
import re
from datetime import datetime
from typing import List, Dict, Optional, Tuple

import pandas as pd
import streamlit as st
import altair as alt

# Optional DOCX dependency (preferred for export)
try:
    from docx import Document
    from docx.shared import Pt, Cm
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    DOCX_AVAILABLE = True
except Exception:
    DOCX_AVAILABLE = False

# ---------------------- Column mapping (tailored to your Excel) ----------------------
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
    "programme", "code", "title", "opening_date",
    "deadline", "first_deadline", "second_deadline", "two_stage",
    "cluster","destination_or_strand","type_of_action","trl",
    "budget_per_project_eur","total_budget_eur","num_projects",
    "call_name","version_label","source_filename","parsed_on_utc",
]

SEARCHABLE_COLUMNS = (
    "code","title","call_name","expected_outcome","scope","full_text",
    "cluster","destination_or_strand","type_of_action","trl"
)

# --------------------------------- Helpers ---------------------------------
def nl_to_br(s: str) -> str:
    return "" if not s else s.replace("\n", "<br>")

def clean_footer(text: str) -> str:
    """Remove '... Work Programme ... Page xx of yy ...' artifacts commonly found in pasted texts."""
    if not text:
        return ""
    footer_pattern = re.compile(
        r"Horizon\s*Europe\s*[-–]?\s*Work Programme.*?Page\s+\d+\s+of\s+\d+",
        re.IGNORECASE | re.DOTALL
    )
    cleaned = footer_pattern.sub("", text)
    return re.sub(r"\s+", " ", cleaned).strip()

def normalize_bullets(text: str) -> str:
    if not isinstance(text, str) or text == "":
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"(?m)^[ \t]*[▪◦●•]\s*", "- ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"(?<!\n)([ \t]+[-*]\s+)", r"\n- ", text)
    text = re.sub(r"(?<!\n)([ \t]+)(\d+\.\s+)", r"\n\2", text)
    return text.strip()

def highlight_text(text: str, keywords: list[str], colours=None) -> str:
    if not text:
        return ""
    clean_keywords = [str(k).strip() for k in keywords if k and str(k).strip()]
    if not clean_keywords:
        return text
    if colours is None:
        colours = ["#ffff00", "#a0e7e5", "#ffb3b3"]
    highlighted = str(text)
    for i, kw in enumerate(clean_keywords):
        colour = colours[i % len(colours)]
        pattern = re.compile(re.escape(kw), re.IGNORECASE)
        highlighted = pattern.sub(
            lambda m: f"<span style='background-color:{colour}; font-weight:bold;'>{m.group(0)}</span>",
            highlighted
        )
    return highlighted

def canonicalise(df: pd.DataFrame, default_programme: Optional[str]=None) -> pd.DataFrame:
    df.columns = [c.strip() for c in df.columns]
    for src, dst in COLUMN_MAP.items():
        if src in df.columns and dst not in df.columns:
            df = df.rename(columns={src: dst})
    df = df.rename(columns={c: c.strip().lower() for c in df.columns})

    if "programme" not in df.columns:
        df["programme"] = default_programme or "Horizon Europe"

    for c in ("opening_date","deadline","first_deadline","second_deadline"):
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce", dayfirst=True)

    for c in ("budget_per_project_eur","total_budget_eur","trl","num_projects"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    if "two_stage" in df.columns:
        df["two_stage"] = (
            df["two_stage"].astype(str).str.strip().str.lower()
            .map({"true": True, "false": False, "yes": True, "no": False, "1": True, "0": False})
            .fillna(False)
        )
    else:
        df["two_stage"] = False

    return df

def wrap_label(text: str, width=50, max_lines=3) -> str:
    s = str(text or "")
    parts = [s[i:i+width] for i in range(0, len(s), width)]
    return "\n".join(parts[:max_lines])

def safe_date_bounds(series, start_fb="2000-01-01", end_fb="2100-12-31") -> Tuple[pd.Timestamp, pd.Timestamp]:
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

# --------------------------- Search / Filter helpers ---------------------------
def multi_keyword_filter(df: pd.DataFrame, terms: list[str], mode: str, title_code_only: bool) -> pd.DataFrame:
    terms = [t.strip().lower() for t in terms if t and str(t).strip()]
    if not terms:
        return df
    hay = df["_search_title"] if title_code_only else df["_search_all"]
    if mode.upper() == "AND":
        pattern = "".join([f"(?=.*{re.escape(t)})" for t in terms]) + ".*"
    else:
        pattern = "(" + "|".join(re.escape(t) for t in terms) + ")"
    mask = hay.str.contains(pattern, regex=True, na=False)
    return df[mask]

# ------------------------------- I/O (cached) --------------------------------
@st.cache_data(show_spinner=False)
def get_sheet_names(file_bytes: bytes) -> List[str]:
    xls = pd.ExcelFile(io.BytesIO(file_bytes))
    return xls.sheet_names

@st.cache_data(show_spinner=False)
def load_sheet(file_bytes: bytes, sheet_name: str, programme_name: Optional[str]=None) -> pd.DataFrame:
    xls = pd.ExcelFile(io.BytesIO(file_bytes))
    raw = pd.read_excel(xls, sheet_name=sheet_name)
    df = canonicalise(raw, default_programme=programme_name)

    # Build searchable haystacks once
    present = [c for c in SEARCHABLE_COLUMNS if c in df.columns]
    df["_search_all"]   = df[present].astype(str).agg(" ".join, axis=1).str.lower() if present else ""
    title_cols          = [c for c in ["code","title"] if c in df.columns]
    df["_search_title"] = df[title_cols].astype(str).agg(" ".join, axis=1).str.lower() if title_cols else ""

    # Convenience "any closing" column (min of first/second/final)
    close_cols = [c for c in ["deadline","first_deadline","second_deadline"] if c in df.columns]
    if close_cols:
        df["closing_date_any"] = pd.to_datetime(df[close_cols].stack(), errors="coerce").groupby(level=0).min()
    else:
        df["closing_date_any"] = pd.NaT

    return df

@st.cache_data(show_spinner=False)
def load_both_programmes(file_bytes: bytes, hint_horizon: Optional[str], hint_erasmus: Optional[str]) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, List[str]]:
    """Load Horizon + Erasmus if present. Auto-detect by sheet name when hints are None."""
    xls = pd.ExcelFile(io.BytesIO(file_bytes))
    sheets = xls.sheet_names

    # Auto-detect if not provided
    hz_name = hint_horizon or next((s for s in sheets if "horizon" in s.lower()), None)
    er_name = hint_erasmus or next((s for s in sheets if "erasmus" in s.lower()), None)

    df_h = pd.DataFrame()
    df_e = pd.DataFrame()

    if hz_name:
        df_h = load_sheet(file_bytes, hz_name, programme_name="Horizon Europe")
    if er_name:
        df_e = load_sheet(file_bytes, er_name, programme_name="Erasmus+")

    if df_h.empty and df_e.empty:
        raise RuntimeError("Could not find sheets for Horizon or Erasmus in the uploaded file.")

    frames = []
    if not df_h.empty: frames.append(df_h)
    if not df_e.empty: frames.append(df_e)
    df_combined = pd.concat(frames, ignore_index=True)

    return df_h, df_e, df_combined, sheets

# --------------------------- Single-bar Gantt prep ---------------------------
def build_singlebar_rows(df: pd.DataFrame) -> pd.DataFrame:
    """
    One bar per row: Opening -> Deadline (final).
    Stage1 / Stage2 remain as fields for thin markers (no extra bars).
    """
    keep = ["programme","code","title","cluster","type_of_action",
            "opening_date","deadline","first_deadline","second_deadline",
            "budget_per_project_eur"]
    cols = [c for c in keep if c in df.columns]
    g = df[cols].copy()

    g = g[pd.notna(g["opening_date"]) & pd.notna(g["deadline"]) & (g["opening_date"] <= g["deadline"])]
    g["y_label"] = g["code"].astype(str).apply(lambda s: wrap_label(s, width=100, max_lines=5))
    g["title_inbar"] = g["title"].astype(str).apply(lambda s: wrap_label(s, width=100, max_lines=3))
    g["bar_days"] = (g["deadline"] - g["opening_date"]).dt.days
    g["mid"] = g["opening_date"] + (g["deadline"] - g["opening_date"]) / 2
    return g.sort_values(["deadline","opening_date"])

def build_altair_chart_singlebar(g: pd.DataFrame):
    if g.empty:
        return None

    # Month shading + labels like your existing styling
    min_x = min(g["opening_date"].min(), g["deadline"].min())
    max_x = max(g["opening_date"].max(), g["deadline"].max())
    bands_df = build_month_bands(min_x, max_x)

    month_shade = (
        alt.Chart(bands_df).mark_rect(tooltip=False).encode(
            x="start:T", x2="end:T",
            opacity=alt.Opacity("band:Q", scale=alt.Scale(domain=[0,1], range=[0.0, 0.15]), legend=None),
            color=alt.value("#00008B")
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
    month_labels_df["mid"] = month_labels_df["month"] + ((month_labels_df["next_month"] - month_labels_df["month"]) / 2)
    month_labels = alt.Chart(month_labels_df).mark_text(
        align="center", baseline="top", dy=0, fontSize=12, fontWeight="bold",
    ).encode(x="mid:T", text="label:N", y=alt.value(0))

    # Today rule (Europe/Brussels)
    today_ts = pd.Timestamp.now(tz="Europe/Brussels").normalize().tz_localize(None)
    today_df = pd.DataFrame({"t": [today_ts]})
    today_rule = alt.Chart(today_df).mark_rule(color="#d62728", strokeDash=[6,4], strokeWidth=2)\
        .encode(x="t:T", tooltip=[alt.Tooltip("t:T", title="Today", format="%d %b %Y")])
    today_label = alt.Chart(today_df).mark_text(
        align="left", baseline="top", dx=4, dy=0, fontSize=11, fontWeight="bold", color="#d62728"
    ).encode(x="t:T", y=alt.value(0), text=alt.Text("t:T", format='Today: "%d %b %Y"'))

    # Y ordering & sizing
    y_order = g["y_label"].drop_duplicates().tolist()
    row_height = 46
    bar_size   = int(row_height * 0.38)
    domain_min, domain_max = g["opening_date"].min(), g["deadline"].max()

    base = alt.Chart(g).encode(
        y=alt.Y(
            "y_label:N",
            sort=y_order,
            axis=alt.Axis(title=None, labelLimit=200, labelFontSize=11, labelAlign="right", labelPadding=50, domain=True),
            scale=alt.Scale(domain=y_order, paddingInner=0.3, paddingOuter=0.8)
        )
    )

    # Main single bar per row
    bars = base.mark_bar(cornerRadius=10, size=bar_size).encode(
        x=alt.X(
            "opening_date:T",
            axis=alt.Axis(title=None, format="%b %Y", tickCount="month", orient="top",
                          labelFontSize=11, labelPadding=50, labelOverlap="greedy", tickSize=6),
            scale=alt.Scale(domain=[domain_min, domain_max])
        ),
        x2="deadline:T",
        color=alt.Color("programme:N",
                        legend=alt.Legend(title="Programme", orient="top", direction="horizontal", offset=100),
                        scale=alt.Scale(scheme="tableau10")),
        tooltip=[
            alt.Tooltip("title:N", title="Title"),
            alt.Tooltip("programme:N", title="Programme"),
            alt.Tooltip("type_of_action:N", title="Type of Action"),
            alt.Tooltip("budget_per_project_eur:Q", title="Budget (€)", format=",.0f"),
            alt.Tooltip("opening_date:T", title="Opening", format="%d %b %Y"),
            alt.Tooltip("deadline:T", title="Deadline", format="%d %b %Y"),
        ],
    )

    # Optional: thin rules for Stage 1 / Stage 2
    rule_stage1 = alt.Chart(g).transform_filter(alt.datum.first_deadline != None)\
        .mark_rule(size=1, color="#00000030").encode(x="first_deadline:T", y="y_label:N")
    rule_stage2 = alt.Chart(g).transform_filter(alt.datum.second_deadline != None)\
        .mark_rule(size=1, color="#00000030").encode(x="second_deadline:T", y="y_label:N")

    # Date labels and in-bar title
    start_labels = base.mark_text(align="right", dx=-4, dy=5, fontSize=10, color="#111")\
        .encode(x="opening_date:T", text=alt.Text("opening_date:T", format="%d %b %Y"))
    end_labels   = base.mark_text(align="left",  dx= 4, dy=5, fontSize=10, color="#111")\
        .encode(x="deadline:T",      text=alt.Text("deadline:T",      format="%d %b %Y"))
    inbar = base.mark_text(align="left", baseline="bottom", dx=2, dy=-(int(bar_size/2)+4), color="black")\
        .encode(x=alt.X("opening_date:T", scale=alt.Scale(domain=[domain_min, domain_max]), axis=None),
                text="title_inbar:N",
                opacity=alt.condition(alt.datum.bar_days >= 10, alt.value(1), alt.value(0)))

    chart = (month_shade + month_grid + bars + rule_stage1 + rule_stage2 + start_labels + end_labels + inbar + month_labels + today_rule + today_label)\
        .properties(height=max(800, len(y_order) * row_height), width='container',
                    padding={"top": 50, "bottom": 30, "left": 10, "right": 10})\
        .configure_axis(grid=False, domain=True, domainWidth=1)\
        .configure_view(continuousHeight=500, continuousWidth=500, strokeWidth=0, clip=False)\
        .resolve_scale(x='shared', y='shared').resolve_axis(x='shared', y='shared')\
        .interactive(bind_x=True)

    return chart

# ------------------------------ Report Builders ------------------------------
def generate_docx_report(calls_df: pd.DataFrame, notes_by_code: Dict[str, str], title: str="Funding Report") -> bytes:
    if not DOCX_AVAILABLE:
        raise RuntimeError("python-docx is not installed")

    doc = Document()
    h = doc.add_heading(title, level=0)
    h.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p = doc.add_paragraph(f"Generated on {datetime.utcnow():%d %b %Y, %H:%M UTC}")
    p.runs[0].font.size = Pt(9)

    table = doc.add_table(rows=1, cols=5)
    hdr = table.rows[0].cells
    for i, t in enumerate(["Programme", "Code", "Title", "Opening", "Deadline"]):
        hdr[i].text = t

    for _, r in calls_df.iterrows():
        opening = r.get("opening_date"); deadline = r.get("deadline")
        row = table.add_row().cells
        row[0].text = str(r.get("programme",""))
        row[1].text = str(r.get("code",""))
        row[2].text = str(r.get("title",""))
        row[3].text = opening.strftime("%d %b %Y") if pd.notna(opening) else "-"
        row[4].text = deadline.strftime("%d %b %Y") if pd.notna(deadline) else "-"

    for _, r in calls_df.iterrows():
        doc.add_page_break()
        doc.add_heading(f"{r.get('code','')} — {r.get('title','')}", level=1)
        lines = []
        lines.append(f"Programme: {r.get('programme','-')}")
        lines.append(f"Cluster: {r.get('cluster','-')}")
        lines.append(f"Destination: {r.get('destination_or_strand','-')}")
        lines.append(f"Type of Action: {r.get('type_of_action','-')}")
        trl_val = r.get("trl")
        lines.append(f"TRL: {int(trl_val) if pd.notna(trl_val) else '-'}")
        opening = r.get("opening_date"); deadline = r.get("deadline")
        first_deadline = r.get("first_deadline"); second_deadline = r.get("second_deadline")
        lines.append(f"Opening: {opening:%d %b %Y}" if pd.notna(opening) else "Opening: -")
        lines.append(f"Deadline: {deadline:%d %b %Y}" if pd.notna(deadline) else "Deadline: -")
        if r.get("two_stage"):
            lines.append(f"Stage 1: {first_deadline:%d %b %Y}" if pd.notna(first_deadline) else "Stage 1: -")
            lines.append(f"Stage 2: {second_deadline:%d %b %Y}" if pd.notna(second_deadline) else "Stage 2: -")
        bpp = r.get("budget_per_project_eur"); tot = r.get("total_budget_eur"); npj = r.get("num_projects")
        lines.append(f"Budget per project: {bpp:,.0f} EUR" if pd.notna(bpp) else "Budget per project: -")
        lines.append(f"Total budget: {tot:,.0f} EUR" if pd.notna(tot) else "Total budget: -")
        lines.append(f"# Projects: {int(npj) if pd.notna(npj) else '-'}")
        doc.add_paragraph("\n".join(lines))

        notes = (notes_by_code or {}).get(str(r.get("code","")), "")
        doc.add_heading("Notes", level=2)
        doc.add_paragraph(notes if notes else "-")

        eo = clean_footer(str(r.get("expected_outcome") or ""))
        sc = clean_footer(str(r.get("scope") or ""))
        if eo:
            doc.add_heading("Expected Outcome", level=2)
            for line in normalize_bullets(eo).splitlines():
                if line.startswith("- "):
                    par = doc.add_paragraph(line[2:]); par.paragraph_format.left_indent = Cm(0.5)
                else:
                    doc.add_paragraph(line)
        if sc:
            doc.add_heading("Scope", level=2)
            for line in normalize_bullets(sc).splitlines():
                if line.startswith("- "):
                    par = doc.add_paragraph(line[2:]); par.paragraph_format.left_indent = Cm(0.5)
                else:
                    doc.add_paragraph(line)

    bio = io.BytesIO(); doc.save(bio); bio.seek(0)
    return bio.getvalue()

def generate_html_report(calls_df: pd.DataFrame, notes_by_code: Dict[str, str], title: str="Funding Report") -> bytes:
    parts = [f"<h1>{title}</h1><p><em>Generated on {datetime.utcnow():%d %b %Y, %H:%M UTC}</em></p>"]
    parts.append("<table border='1' cellspacing='0' cellpadding='4'><tr><th>Programme</th><th>Code</th><th>Title</th><th>Opening</th><th>Deadline</th></tr>")
    for _, r in calls_df.iterrows():
        opening = r.get("opening_date"); deadline = r.get("deadline")
        parts.append(f"<tr><td>{r.get('programme','')}</td><td>{r.get('code','')}</td><td>{r.get('title','')}</td><td>{opening.strftime('%d %b %Y') if pd.notna(opening) else '-'}</td><td>{deadline.strftime('%d %b %Y') if pd.notna(deadline) else '-'}</td></tr>")
    parts.append("</table>")
    for _, r in calls_df.iterrows():
        parts.append("<hr>")
        parts.append(f"<h2>{r.get('code','')} — {r.get('title','')}</h2>")
        meta = []
        meta.append(f"Programme: {r.get('programme','-')}")
        meta.append(f"Cluster: {r.get('cluster','-')}")
        meta.append(f"Destination: {r.get('destination_or_strand','-')}")
        meta.append(f"Type of Action: {r.get('type_of_action','-')}")
        trl_val = r.get("trl"); meta.append(f"TRL: {int(trl_val) if pd.notna(trl_val) else '-'}")
        opening = r.get("opening_date"); deadline = r.get("deadline")
        first_deadline = r.get("first_deadline"); second_deadline = r.get("second_deadline")
        meta.append(f"Opening: {opening:%d %b %Y}" if pd.notna(opening) else "Opening: -")
        meta.append(f"Deadline: {deadline:%d %b %Y}" if pd.notna(deadline) else "Deadline: -")
        if r.get("two_stage"):
            meta.append(f"Stage 1: {first_deadline:%d %b %Y}" if pd.notna(first_deadline) else "Stage 1: -")
            meta.append(f"Stage 2: {second_deadline:%d %b %Y}" if pd.notna(second_deadline) else "Stage 2: -")
        bpp = r.get("budget_per_project_eur"); tot = r.get("total_budget_eur"); npj = r.get("num_projects")
        meta.append(f"Budget per project: {bpp:,.0f} EUR" if pd.notna(bpp) else "Budget per project: -")
        meta.append(f"Total budget: {tot:,.0f} EUR" if pd.notna(tot) else "Total budget: -")
        meta.append(f"# Projects: {int(npj) if pd.notna(npj) else '-'}")
        parts.append("<p>" + "<br>".join(meta) + "</p>")
    html = "<html><head><meta charset='utf-8'><title>{}</title></head><body>{}</body></html>".format(title, "".join(parts))
    return html.encode("utf-8")

# ----------------------------------- UI -----------------------------------
st.set_page_config(page_title="Funding Dashboard – app_b3.0", layout="wide")

# Global CSS (wider page + scroll container for tall charts)
st.markdown(
    """
    <style>
    .scroll-container { overflow-x: auto; overflow-y: auto; max-height: 900px; padding: 16px; border: 1px solid #eee; border-radius: 8px; }
    .main .block-container { padding-left: 1.5rem; padding-right: 1.5rem; max-width: 95vw; }
    </style>
    """,
    unsafe_allow_html=True
)

# Optional logo
try:
    with open("logo.png", "rb") as f:
        data_b64 = base64.b64encode(f.read()).decode("utf-8")
    st.markdown(f"<div style='text-align:center;'><img src='data:image/png;base64,{data_b64}' width='250'></div>", unsafe_allow_html=True)
except Exception:
    pass

st.title("Funding Dashboard – Horizon & Erasmus (app_b3.0)")

st.info(
    "📂 Upload the latest parsed Excel file that contains **both** Horizon and Erasmus sheets.\n\n"
    "The app auto-detects sheet names containing *horizon* and *erasmus*. You can override this in ⚙️ Advanced."
)

upl = st.file_uploader("Upload Excel (.xlsx)", type=["xlsx"])
if not upl:
    st.stop()

# Advanced: allow overriding auto-detected sheet names
with st.expander("⚙️ Advanced – sheet detection/override"):
    all_sheets = get_sheet_names(upl.getvalue())
    st.caption(f"Detected sheets: {', '.join(all_sheets)}")
    c1, c2 = st.columns(2)
    with c1:
        hint_hz = st.selectbox("Horizon sheet", options=["<auto>"] + all_sheets, index=0)
        hint_hz = None if hint_hz == "<auto>" else hint_hz
    with c2:
        hint_er = st.selectbox("Erasmus sheet", options=["<auto>"] + all_sheets, index=0)
        hint_er = None if hint_er == "<auto>" else hint_er

# Load both programmes (combined + per-programme)
df_h, df_e, df_all, sheet_names = load_both_programmes(upl.getvalue(), hint_hz, hint_er)

# Compute global date bounds
open_lo, open_hi = safe_date_bounds(pd.concat([df_all["opening_date"]], axis=0))
dead_all = pd.concat([
    pd.to_datetime(df_all.get("deadline"), errors="coerce"),
    pd.to_datetime(df_all.get("first_deadline"), errors="coerce"),
    pd.to_datetime(df_all.get("second_deadline"), errors="coerce"),
], axis=0)
dead_lo, dead_hi = safe_date_bounds(dead_all)

# ------------------------------ Filter form ------------------------------
prog_opts    = sorted([p for p in df_all["programme"].dropna().unique().tolist() if p != ""])
cluster_opts = sorted([c for c in df_all.get("cluster", pd.Series(dtype=object)).dropna().unique().tolist() if c != ""])
type_opts    = sorted([t for t in df_all.get("type_of_action", pd.Series(dtype=object)).dropna().unique().tolist() if t != ""])
trl_opts     = sorted([str(int(x)) for x in df_all.get("trl", pd.Series(dtype=float)).dropna().unique() if pd.notna(x)])
dest_opts    = sorted([d for d in df_all.get("destination_or_strand", pd.Series(dtype=object)).dropna().unique().tolist() if d != ""])

with st.form("filters_form", clear_on_submit=False):
    st.header("Filters")
    c1, c2, c3 = st.columns(3)
    with c1: programmes = st.multiselect("Programme", options=prog_opts, default=prog_opts)
    with c2: clusters   = st.multiselect("Cluster", options=cluster_opts)
    with c3: dests      = st.multiselect("Destination / Strand", options=dest_opts)
    c4, c5 = st.columns(2)
    with c4: types      = st.multiselect("Type of Action", options=type_opts)
    with c5: trls       = st.multiselect("TRL", options=trl_opts)

    r1, r2, r3, r4 = st.columns([2,2,2,1])
    with r1: kw1 = st.text_input("Keyword 1")
    with r2: kw2 = st.text_input("Keyword 2")
    with r3: kw3 = st.text_input("Keyword 3")
    with r4: combine_mode = st.radio("Combine", ["AND","OR"], horizontal=True, index=0)
    title_code_only = st.checkbox("Search only in Title & Code", value=True)

    d1, d2, d3, d4 = st.columns(4)
    with d1: open_start = st.date_input("Open from",  value=open_lo,  min_value=open_lo,  max_value=open_hi)
    with d2: open_end   = st.date_input("Open to",    value=open_hi,  min_value=open_lo,  max_value=open_hi)
    with d3: close_from = st.date_input("Close from", value=dead_lo,  min_value=dead_lo,  max_value=dead_hi)
    with d4: close_to   = st.date_input("Close to",   value=dead_hi,  min_value=dead_lo,  max_value=dead_hi)

    bud_series = pd.to_numeric(df_all.get("budget_per_project_eur"), errors="coerce").dropna()
    if bud_series.empty:
        min_bud, max_bud = 0.0, 1_000_000.0
    else:
        min_bud, max_bud = float(bud_series.min()), float(bud_series.max())
        if not (min_bud < max_bud):
            min_bud, max_bud = max(min_bud, 0.0), min_bud + 100000.0
    rng = max_bud - min_bud
    try: step = max(1e4, round(rng / 50, -3))  # ~50 steps, nearest 1k
    except Exception: step = 10000.0
    budget_range = st.slider("Budget per project (EUR)", min_bud, max_bud, (min_bud, max_bud), step=step)

    applied = st.form_submit_button("Apply filters")

# persist criteria
if "criteria" not in st.session_state:
    st.session_state.criteria = {}

if applied:
    st.session_state.criteria = dict(
        programmes=programmes, clusters=clusters, types=types, trls=trls, dests=dests,
        kw1=kw1, kw2=kw2, kw3=kw3, combine_mode=combine_mode, title_code_only=title_code_only,
        open_start=open_start, open_end=open_end, close_from=close_from, close_to=close_to,
        budget_range=budget_range, applied_at=datetime.utcnow().strftime("%H:%M UTC")
    )

if not st.session_state.criteria:
    st.session_state.criteria = dict(
        programmes=sorted(df_all["programme"].dropna().unique().tolist()),
        clusters=[], types=[], trls=[], dests=[],
        kw1="", kw2="", kw3="", combine_mode="OR", title_code_only=False,
        open_start=open_lo, open_end=open_hi, close_from=dead_lo, close_to=dead_hi,
        budget_range=(0.0, 1_000_000.0), applied_at=None
    )

crit = st.session_state.criteria
if crit.get("applied_at"):
    st.caption(f"Filters last applied at {crit['applied_at']}")

# Apply filters
f = df_all.copy()
f = multi_keyword_filter(f, [crit["kw1"], crit["kw2"], crit["kw3"]], crit["combine_mode"], crit["title_code_only"])
if crit["programmes"]: f = f[f["programme"].isin(crit["programmes"])]
if crit["clusters"]:   f = f[f["cluster"].isin(crit["clusters"])]
if crit["types"]:      f = f[f["type_of_action"].isin(crit["types"])]
if crit["trls"]:       f = f[f["trl"].dropna().astype("Int64").astype(str).isin(crit["trls"])]
if crit["dests"]:      f = f[f["destination_or_strand"].isin(crit["dests"])]
f = f[f["opening_date"].between(pd.to_datetime(crit["open_start"]), pd.to_datetime(crit["open_end"]), inclusive="both")]
f = f[f["closing_date_any"].between(pd.to_datetime(crit["close_from"]), pd.to_datetime(crit["close_to"]), inclusive="both")]
low, high = crit["budget_range"]; f = f[f["budget_per_project_eur"].fillna(0).between(low, high)]

st.markdown(f"**Showing {len(f)} rows** after last applied filters.")

# ------------------------------ Tabs ------------------------------
tab1, tab2, tab3, tab4 = st.tabs(["📅 Gantt (All)", "📋 Table (by Programme)", "📚 Full Data (by Programme)", "📝 Shortlist & Notes"])

# Tab 1 — Combined Gantt (single bar per row)
with tab1:
    st.subheader("Combined Timeline – Horizon + Erasmus (Opening → Final; thin markers for stages)")
    g_single = build_singlebar_rows(f)
    if g_single.empty:
        st.info("No rows with valid dates to display.")
    else:
        st.markdown('<div class="scroll-container">', unsafe_allow_html=True)
        st.altair_chart(build_altair_chart_singlebar(g_single), use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)

# Tab 2 — Table, separated by programme
with tab2:
    st.subheader("Filtered Table by Programme")
    show_cols = [c for c in DISPLAY_COLS if c in f.columns]
    for prog, gdf in f.groupby("programme"):
        with st.expander(f"{prog} — {len(gdf)} rows", expanded=True):
            st.dataframe(gdf[show_cols], use_container_width=True, hide_index=True, height=400)

    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as xw:
        for prog, gdf in f.groupby("programme"):
            gdf[show_cols].to_excel(xw, index=False, sheet_name=(prog[:30] or "data"))
    out.seek(0)
    st.download_button("⬇️ Download filtered (Excel, split by programme)", out,
                       file_name=f"calls_filtered_{datetime.utcnow():%Y%m%d}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# Tab 3 — Full Data, separated by programme
with tab3:
    st.subheader("Full Data – Details by Programme")
    kw_list = [crit.get("kw1",""), crit.get("kw2",""), crit.get("kw3","")]

    def render_row(row):
        c1, c2 = st.columns(2)
        with c1:
            if pd.notna(row.get("opening_date")):
                st.markdown(f"📅 **Opening:** {row.get('opening_date'):%d %b %Y}")
            if pd.notna(row.get("deadline")):
                st.markdown(f"⏳ **Deadline:** {row.get('deadline'):%d %b %Y}")
            if row.get("two_stage"):
                if pd.notna(row.get("first_deadline")):
                    st.markdown(f"🔄 **Stage 1:** {row.get('first_deadline'):%d %b %Y}")
                if pd.notna(row.get("second_deadline")):
                    st.markdown(f"🔄 **Stage 2:** {row.get('second_deadline'):%d %b %Y}")
        with c2:
            if pd.notna(row.get("budget_per_project_eur")):
                st.markdown(f"💶 **Budget per project:** {row.get('budget_per_project_eur'):,.0f} EUR")
            if pd.notna(row.get("total_budget_eur")):
                st.markdown(f"📦 **Total budget:** {row.get('total_budget_eur'):,.0f} EUR")
            if pd.notna(row.get("num_projects")):
                st.markdown(f"📊 **# Projects:** {int(row.get('num_projects'))}")

        st.markdown(
            f"🏷️ **Programme:** {row.get('programme','-')}  "
            f"| **Cluster:** {row.get('cluster','-')}  "
            f"| **Destination:** {row.get('destination_or_strand','-')}  "
            f"| **Type of Action:** {row.get('type_of_action','-')}  "
            f"| **TRL:** {row.get('trl','-')}"
        )

        if row.get("expected_outcome"):
            with st.expander("🎯 Expected Outcome"):
                clean_text = nl_to_br(normalize_bullets(clean_footer(row.get("expected_outcome"))))
                st.markdown(highlight_text(clean_text, kw_list), unsafe_allow_html=True)

        if row.get("scope"):
            with st.expander("🧭 Scope"):
                clean_text = nl_to_br(normalize_bullets(clean_footer(row.get("scope"))))
                st.markdown(highlight_text(clean_text, kw_list), unsafe_allow_html=True)

        if row.get("full_text"):
            with st.expander("📖 Full Description"):
                clean_text = nl_to_br(normalize_bullets(clean_footer(row.get("full_text"))))
                st.markdown(highlight_text(clean_text, kw_list), unsafe_allow_html=True)

        st.caption(
            f"📂 Source: {row.get('source_filename','-')} "
            f"| Version: {row.get('version_label','-')} "
            f"| Parsed on: {row.get('parsed_on_utc','-')}"
        )

    for prog, gdf in f.groupby("programme"):
        st.markdown(f"### {prog}")
        for _, row in gdf.iterrows():
            title = f"{row.get('code','')} — {row.get('title','')}"
            with st.expander(title):
                render_row(row)

# Tab 4 — Shortlist & Notes, separated by programme (export DOCX/HTML)
with tab4:
    st.subheader("Shortlist & Notes → Generate Report (DOCX/HTML)")

    if "selection" not in st.session_state: st.session_state.selection = set()
    if "notes" not in st.session_state:     st.session_state.notes = {}

    st.markdown("**Select calls to include in the report**")
    for prog, gdf in f.sort_values(["closing_date_any","opening_date"]).groupby("programme"):
        with st.expander(f"{prog} — {len(gdf)}"):
            for _, r in gdf.iterrows():
                code = str(r.get("code") or ""); title = str(r.get("title") or "")
                label = f"{code} — {title}"
                checked = code in st.session_state.selection
                new_val = st.checkbox(label, value=checked, key=f"chk_{prog}_{code}")
                if new_val and not checked:
                    st.session_state.selection.add(code)
                elif (not new_val) and checked:
                    st.session_state.selection.discard(code)

    if st.session_state.selection:
        st.markdown("---")
        st.markdown("**Enter notes per selected call**")
        selected_df = f[f["code"].astype(str).isin(st.session_state.selection)].copy()
        for _, r in selected_df.iterrows():
            code = str(r.get("code") or ""); title = str(r.get("title") or "")
            default_txt = st.session_state.notes.get(code, "")
            st.session_state.notes[code] = st.text_area(
                f"Notes — {code} — {title}",
                value=default_txt, key=f"note_{code}", height=120
            )

        st.markdown("---")
        colA, colB = st.columns(2)
        with colA: report_title = st.text_input("Report title", value="Funding Report – Shortlist")
        with colB: include_long_text = st.checkbox("Include Expected Outcome / Scope (export)", value=False)

        def prep_df_for_report(df_in: pd.DataFrame) -> pd.DataFrame:
            cols = [
                "programme","code","title","cluster","destination_or_strand",
                "type_of_action","trl","opening_date","deadline",
                "first_deadline","second_deadline","two_stage",
                "budget_per_project_eur","total_budget_eur","num_projects",
                "expected_outcome","scope"
            ]
            keep = [c for c in cols if c in df_in.columns]
            return df_in[keep].copy()

        report_df = prep_df_for_report(selected_df)

        if st.button("📄 Generate report"):
            try:
                df_for_export = report_df.copy()
                if not include_long_text:
                    for col in ("expected_outcome","scope"):
                        if col in df_for_export.columns:
                            df_for_export[col] = ""

                if DOCX_AVAILABLE:
                    docx_bytes = generate_docx_report(df_for_export, st.session_state.notes, title=report_title)
                    st.download_button("⬇️ Download Word (.docx)", data=docx_bytes,
                                       file_name="funding_report.docx",
                                       mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
                else:
                    html_bytes = generate_html_report(df_for_export, st.session_state.notes, title=report_title)
                    st.warning("`python-docx` not found. Generated HTML instead; open it and use your browser’s Print → Save as PDF.")
                    st.download_button("⬇️ Download HTML", data=html_bytes,
                                       file_name="funding_report.html",
                                       mime="text/html")
            except Exception as e:
                st.error(f"Failed to generate report: {e}")
    else:
        st.info("Select at least one call above to add notes and generate a report.")
