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

st.title("EU Calls Parser (Programme Selector)")

st.markdown("Pick a **programme**, upload one or more **PDFs**, and preview the parsed table. "
            "Horizon uses your working logic; Erasmus+ is a stub so you can test selection.")

programme = st.selectbox("Programme", options=list(PARSERS.keys()), index=0)

col1, col2 = st.columns([2, 1])
with col1:
    uploaded_files = st.file_uploader("Upload work programme PDFs", type=["pdf"], accept_multiple_files=True)
with col2:
    version_label = st.text_input("Version label", value="Draft v1")

if uploaded_files:
    all_dfs = []
    with st.spinner(f"Parsing {len(uploaded_files)} file(s) with {programme} rules…"):
        for uploaded in uploaded_files:
            pdf_bytes = uploaded.read()
            df = PARSERS[programme](
                io.BytesIO(pdf_bytes),
                source_filename=uploaded.name,
                version_label=version_label,
                parsed_on_utc=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            )
            all_dfs.append(df)

    # Combine results
    combined_df = pd.concat(all_dfs, ignore_index=True)

    # Gentle normalisation for preview
    for c in ["Opening Date", "Deadline 1", "Deadline 2", "Deadline"]:
        if c in combined_df.columns:
            combined_df[c] = pd.to_datetime(combined_df[c], dayfirst=True, errors="coerce")

    st.subheader("Preview")
    st.dataframe(combined_df.head(20), use_container_width=True)

    # Download
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as xw:
        combined_df.to_excel(xw, index=False, sheet_name="parsed")
    output.seek(0)
    st.download_button(
        label="⬇️ Download parsed Excel",
        data=output,
        file_name=f"{programme.replace(' ', '_').lower()}_parsed.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
