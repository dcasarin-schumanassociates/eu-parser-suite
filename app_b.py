# app_b_echarts.py — ECharts Gantt with monthly shading, dataZoom, persistent view
from __future__ import annotations
import io
import json
import math
import pandas as pd
import streamlit as st
from streamlit_echarts import st_echarts

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

def wrap_label(text: str, width=38, max_lines=3) -> str:
    s = str(text or "")
    parts = [s[i:i+width] for i in range(0, len(s), width)]
    return "\n".join(parts[:max_lines])

def earliest_deadline_row(row: pd.Series):
    vals = [
        pd.to_datetime(row.get("deadline"), errors="coerce"),
        pd.to_datetime(row.get("first_deadline"), errors="coerce"),
        pd.to_datetime(row.get("second_deadline"), errors="coerce"),
    ]
    vals = [x for x in vals if pd.notna(x)]
    return min(vals) if vals else pd.NaT

def month_mark_areas(min_ts: pd.Timestamp, max_ts: pd.Timestamp):
    """Alternating monthly shaded bands as ECharts markArea data."""
    start = pd.Timestamp(min_ts).to_period("M").start_time
    end   = (pd.Timestamp(max_ts).to_period("M") + 1).start_time
    months = pd.date_range(start, end, freq="MS")
    areas = []
    for i in range(len(months)-1):
        if i % 2 == 1:  # shade every other month
            areas.append([
                {"xAxis": months[i].strftime("%Y-%m-%d")},
                {"xAxis": months[i+1].strftime("%Y-%m-%d")},
            ])
    return areas

def build_echarts_option(df: pd.DataFrame, end_col: str, view_start, view_end):
    g = df.copy()

    # keep rows that have opening + selected end date + title
    g = g.dropna(subset=["opening_date", end_col, "title"])
    if g.empty:
        return None

    # y labels: CODE — Title wrapped
    g["y_label"] = (g["code"].fillna("").astype(str) + " — " + g["title"].astype(str))\
                    .apply(lambda s: wrap_label(s, width=38, max_lines=3))

    # sort by earliest deadline (min of any deadline)
    g["earliest_deadline"] = g.apply(earliest_deadline_row, axis=1)
    g = g.sort_values(["earliest_deadline", "opening_date"], ascending=[True, True])

    y_labels = g["y_label"].tolist()
    # map each row to ECharts data item
    def to_ms(ts): return int(pd.Timestamp(ts).timestamp() * 1000)
    data = []
    for _, r in g.iterrows():
        start = pd.to_datetime(r["opening_date"])
        end = pd.to_datetime(r[end_col])
        # Skip inverted bars
        if pd.isna(start) or pd.isna(end) or end < start:
            continue
        item = {
            "name": str(r.get("code") or ""),
            "value": [
                r["y_label"],           # category
                to_ms(start),           # start (ms)
                to_ms(end),             # end   (ms)
            ],
            "title": str(r.get("title") or ""),
            "type_of_action": str(r.get("type_of_action") or ""),
            "budget": float(r.get("budget_per_project_eur") or 0),
            "open_str": start.strftime("%d %b %Y"),
            "close_str": end.strftime("%d %b %Y"),
            "programme": str(r.get("programme") or ""),
        }
        data.append(item)

    if not data:
        return None

    # chart sizing
    row_px = 46
    height_px = max(520, int(len(y_labels) * row_px))

    # calendar span for shading bands
    min_x = min(pd.to_datetime(g["opening_date"]).min(), pd.to_datetime(g[end_col]).min())
    max_x = max(pd.to_datetime(g["opening_date"]).max(), pd.to_datetime(g[end_col]).max())
    mark_areas = month_mark_areas(min_x, max_x)

    # persistent view window (xAxis min/max)
    x_min = pd.to_datetime(view_start).strftime("%Y-%m-%d")
    x_max = pd.to_datetime(view_end).strftime("%Y-%m-%d")

    # custom renderItem to draw duration bars between start/end ms on y category
    render_item = """
    function(params, api) {
      var cat = api.value(0);
      var start = api.value(1);
      var end = api.value(2);
      var yIdx = api.coord([api.value(1), cat])[1];
      var startCoord = api.coord([start, cat]);
      var endCoord   = api.coord([end, cat]);
      va

