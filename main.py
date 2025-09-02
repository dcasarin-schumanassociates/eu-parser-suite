# main.py
from __future__ import annotations
import io, time
import pandas as pd
import streamlit as st

from parsers import horizon as horizon_parser
from parsers import erasmus as erasmus_parser

PARSERS = {
    "Horizon Europe": horizon_parser.parse_pdf,
    "Erasmus+": erasmus_parser.parse_pdf,  # stub
}

st.set_page_config(page_title="EU Calls Parser", layout="wide")
st.title("EU Calls Parser (Programme Selector)")

st.markdown("Pick a **programme**, upload a **PDF**, and preview the parsed table. "
            "Horizon uses your working logic; Erasmus+ is a stub so you can test selection.")

programme = st.selectbox("Programme", options=list(PARSERS.keys()), index=0)

col1, col2, col3 = st.columns([2,1,1])
with col1:
    uploaded = st.file_uploader("Upload work programme PDF", type=["pdf"])
with col2:
    version_label = st.text_input("Version label", value="Draft v1")
with col3:
    source_filename = uploaded.name if uploaded else ""

if uploaded:
    with st.spinner(f"Parsing with {programme} rules…"):
        pdf_bytes = uploaded.read()
        df = PARSERS[programme](
            io.BytesIO(pdf_bytes),
            source_filename=source_filename or "uploaded.pdf",
            version_label=version_label,
            parsed_on_utc=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        )

    # Gentle normalisation for preview only (won’t break your columns)
    for c in ["Opening Date", "Deadline 1", "Deadline 2", "Deadline"]:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], dayfirst=True, errors="coerce")

    st.subheader("Preview")
    st.dataframe(df.head(20), use_container_width=True)

    # Download
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as xw:
        df.to_excel(xw, index=False, sheet_name="parsed")
    output.seek(0)
    st.download_button(
        label="⬇️ Download parsed Excel",
        data=output,
        file_name=f"{programme.replace(' ','_').lower()}_parsed.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

import plotly.express as px

st.subheader("📅 Gantt (Calls Timeline)")

# Make a copy and ensure dates are proper datetimes
gantt_df = df.copy()
for c in ["Opening Date", "Deadline"]:
    if c in gantt_df.columns:
        gantt_df[c] = pd.to_datetime(gantt_df[c], errors="coerce")

# Keep only rows with both dates
gantt_df = gantt_df.dropna(subset=["Opening Date", "Deadline"])

# Optional: shorten very long titles for the y-axis (keeps tooltip full)
def _shorten(s, n=90):
    s = str(s) if pd.notna(s) else ""
    return (s[:n] + "…") if len(s) > n else s

gantt_df["_TitleShort"] = gantt_df["Title"].apply(_shorten)

# Pick a colour dimension if available; otherwise a single colour
colour_dim = "Type of Action" if "Type of Action" in gantt_df.columns else None

fig = px.timeline(
    gantt_df,
    x_start="Opening Date",
    x_end="Deadline",
    y="_TitleShort",
    color=colour_dim,
    hover_data={
        "_TitleShort": False,
        "Title": True,
        "Code": True if "Code" in gantt_df.columns else False,
        "Opening Date": True,
        "Deadline": True,
        "Destination": True if "Destination" in gantt_df.columns else False,
        "Type of Action": True if "Type of Action" in gantt_df.columns else False,
        "TRL": True if "TRL" in gantt_df.columns else False,
        "Call Name": True if "Call Name" in gantt_df.columns else False,
    },
)

# Gantt charts typically have the first task at the top
fig.update_yaxes(autorange="reversed")

# Make it scroll/zoom friendly
fig.update_xaxes(rangeslider_visible=True)

st.plotly_chart(fig, use_container_width=True)

else:
    st.info("Select a programme and upload a PDF to begin.")
