# src/apps/parser_app.py
from __future__ import annotations

# --- Robust import bootstrap (works locally & on Streamlit Cloud) ---
import sys
from pathlib import Path

# Project root = two levels up from this file (eu-parser-suite/)
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# -------------------------------------------------------------------

import io
import pandas as pd
import streamlit as st

from src.parsers import PARSERS, PROGRAMMES

st.set_page_config(page_title="EU Programme Parser", layout="wide")
st.title("EU Programme Parser")

st.markdown(
    "Select a **programme**, upload a **PDF**, and provide a **version label** "
    "(e.g., `Draft v1`, `Draft v2`, `Final`)."
)

with st.sidebar:
    programme = st.selectbox("Programme", PROGRAMMES, index=0)
    version_label = st.text_input("Version label", value="Draft v1", help="Shown in the Excel output")
    st.caption("Tip: keep labels consistent (Draft v1, Draft v2, Final)")

uploaded = st.file_uploader("Upload work programme PDF", type=["pdf"])

if uploaded and programme:
    parser_fn = PARSERS[programme]
    with st.spinner(f"Parsing as {programme}…"):
        pdf_bytes = uploaded.read()
        df = parser_fn(pdf_bytes=pdf_bytes, source_filename=uploaded.name, version_label=version_label)

    st.success(f"Parsed {len(df)} rows for {programme}.")
    st.dataframe(df.head(50), use_container_width=True)

    # Download Excel
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, index=False, sheet_name="parsed")
    st.download_button(
        "⬇️ Download parsed Excel",
        data=buf.getvalue(),
        file_name=f"{programme.replace(' ','_')}_parsed.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
else:
    st.info("Choose a programme on the left and upload a PDF to begin.")
