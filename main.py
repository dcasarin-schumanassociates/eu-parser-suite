# main.py
from __future__ import annotations

import io
import time
import pandas as pd
import streamlit as st

# Import parser modules
from parsers import horizon as horizon_parser
from parsers import erasmus as erasmus_parser

PARSERS = {
    "Horizon Europe": horizon_parser.parse_pdf,
    "Erasmus+": erasmus_parser.parse_pdf,
    # "Digital Europe": digital_europe_parser.parse_pdf,  # add later
}

st.set_page_config(page_title="EU Calls Parser", layout="wide")
st.title("EU Calls Parser (Modular)")

st.markdown("Select a **programme**, upload a **PDF**, and preview the parsed table. "
            "🎯 This is a minimal, modular setup to grow with new programmes.")

programme = st.selectbox(
    "Programme",
    options=list(PARSERS.keys()),
    index=0,
)

col1, col2, col3 = st.columns([2, 1, 1])
with col1:
    uploaded = st.file_uploader("Upload work programme PDF", type=["pdf"])
with col2:
    version_label = st.text_input("Version label", value="Draft v1")
with col3:
    source_filename = uploaded.name if uploaded else ""

if uploaded:
    with st.spinner(f"Parsing with {programme} rules…"):
        # Important: pass a new buffer because parser may .read()
        pdf_bytes = uploaded.read()
        df = PARSERS[programme](
            io.BytesIO(pdf_bytes),
            source_filename=source_filename or "uploaded.pdf",
            version_label=version_label,
            parsed_on_utc=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        )

    # Basic coercions (safe no-ops if columns are empty)
    date_cols = ["opening_date", "deadline"]
    for c in date_cols:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce")

    num_cols = ["budget_per_project_eur", "total_budget_eur", "trl"]
    for c in num_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    st.subheader("Preview")
    st.dataframe(df.head(20), use_container_width=True)

    # Download as Excel
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="parsed")
    output.seek(0)
    st.download_button(
        label="⬇️ Download parsed Excel",
        data=output,
        file_name=f"{programme.replace(' ','_').lower()}_parsed.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
else:
    st.info("Select a programme and upload a PDF to begin.")
