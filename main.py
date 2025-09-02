# main.py
from __future__ import annotations
import io, time
import pandas as pd
import streamlit as st
import plotly.express as px

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

col1, col2, col3 = st.columns([2, 1, 1])
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
        file_name=f"{programme.replace(' ', '_').lower()}_parsed.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
