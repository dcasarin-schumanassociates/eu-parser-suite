# app_b_echarts.py
import io
import pandas as pd
import streamlit as st
from streamlit_echarts import st_echarts

# ---------- Column mapping ----------
COLUMN_MAP = {
    "Code": "code",
    "Title": "title",
    "Opening Date": "opening_date",
    "Deadline": "deadline",
    "First Stage Deadline": "first_deadline",
    "Second Stage Deadline": "second_deadline",
    "Two-Stage": "two_stage",
    "Programme": "programme",
}

def canonicalise(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns={c: c.strip(): c for c in df.columns})
    for src, dst in COLUMN_MAP.items():
        if src in df.columns and dst not in df.columns:
            df = df.rename(columns={src: dst})
    # Dates
    for c in ["opening_date","deadline","first_deadline","second_deadline"]:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce", dayfirst=True)
    # Boolean for two-stage
    if "two_stage" in df.columns:
        df["two_stage"] = df["two_stage"].astype(str).str.lower().isin(["true","yes","1"])
    else:
        df["two_stage"] = False
    if "programme" not in df.columns:
        df["programme"] = "Programme"
    return df

def build_segments(df: pd.DataFrame) -> list[dict]:
    """Return list of dicts for echarts: {name, value: [start, end]}"""
    rows = []
    for _, r in df.iterrows():
        code = str(r.get("code") or "")
        title = str(r.get("title") or "")
        y_label = f"{title}"  # Y axis shows title
        prog = r.get("programme")

        open_dt = r.get("opening_date")
        final_dt = r.get("deadline")
        first_dt = r.get("first_deadline")
        second_dt = r.get("second_deadline")
        two_stage = r.get("two_stage")

        def fmt(d): return d.strftime("%Y-%m-%d") if pd.notna(d) else None

        if two_stage:
            if pd.notna(open_dt) and pd.notna(first_dt):
                rows.append({
                    "name": y_label,
                    "code": code,
                    "programme": prog,
                    "value": [fmt(open_dt), fmt(first_dt)],
                })
            segB_end = second_dt if pd.notna(second_dt) else final_dt
            if pd.notna(first_dt) and pd.notna(segB_end):
                rows.append({
                    "name": y_label,
                    "code": code,
                    "programme": prog,
                    "value": [fmt(first_dt), fmt(segB_end)],
                })
        else:
            if pd.notna(open_dt) and pd.notna(final_dt):
                rows.append({
                    "name": y_label,
                    "code": code,
                    "programme": prog,
                    "value": [fmt(open_dt), fmt(final_dt)],
                })
    return rows

# ---------- Streamlit App ----------
st.set_page_config(page_title="Calls Explorer — ECharts Gantt", layout="wide")
st.title("Calls Explorer — Gantt (ECharts version)")

upl = st.file_uploader("Upload parsed Excel (.xlsx)", type=["xlsx"])
if not upl:
    st.stop()

df = pd.read_excel(upl, sheet_name=0)
df = canonicalise(df)

segments = build_segments(df)

if not segments:
    st.info("No valid rows with dates.")
    st.stop()

# Group by titles
y_labels = sorted(set([s["name"] for s in segments]))

# ECharts option
option = {
    "tooltip": {
        "formatter": lambda params: (
            f"<b>{params['data']['code']}</b><br/>"
            f"{params['data']['name']}<br/>"
            f"{params['data']['value'][0]} → {params['data']['value'][1]}"
        )
    },
    "dataZoom": [
        {"type": "slider", "xAxisIndex": 0},
        {"type": "inside", "xAxisIndex": 0}
    ],
    "grid": {"left": 220, "right": 40, "top": 50, "bottom": 80},
    "xAxis": {
        "type": "time",
        "axisLabel": {"rotate": 0, "fontSize": 12},
        "splitLine": {"show": True},
    },
    "yAxis": {
        "type": "category",
        "data": y_labels,
        "axisLabel": {"fontSize": 12, "interval": 0},  # always show
    },
    "series": [
        {
            "type": "custom",
            "renderItem": {
                "function": """
                function(params, api) {
                    var categoryIndex = api.value(2);
                    var start = api.coord([api.value(0), categoryIndex]);
                    var end = api.coord([api.value(1), categoryIndex]);
                    var height = api.size([0,1])[1] * 0.6;
                    var rectShape = echarts.graphic.clipRectByRect({
                        x: start[0],
                        y: start[1] - height / 2,
                        width: end[0] - start[0],
                        height: height
                    }, {
                        x: params.coordSys.x,
                        y: params.coordSys.y,
                        width: params.coordSys.width,
                        height: params.coordSys.height
                    });
                    return rectShape && {
                        type: 'rect',
                        shape: rectShape,
                        style: api.style()
                    };
                }
                """
            },
            "encode": {"x": [0,1], "y": 2},
            "data": [
                [s["value"][0], s["value"][1], s["name"], s] for s in segments
            ],
            "itemStyle": {"color": "#1f77b4"}
        }
    ]
}

st_echarts(option, height=600, key="echarts_gantt")
