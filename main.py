# main.py
from __future__ import annotations
import io, time
import pandas as pd
import streamlit as st
import os

st.set_page_config(page_title="EU Calls Parser", layout="wide")
st.title("EU Calls Parser (Fixed Excel Loader)")

# ----------------------------------------------------
# 1. Point to your fixed Excel file
FIXED_EXCEL_PATH = "data/calls_fixed.xlsx"  # 👈 change path as needed
# ----------------------------------------------------

if not os.path.exists(FIXED_EXCEL_PATH):
    st.error(f"⚠️ Fixed Excel file not found: {FIXED_EXCEL_PATH}")
    st.stop()

# 2. Load Excel into DataFrame
df = pd.read_excel(FIXED_EXCEL_PATH)

# 3. Gentle normalisation for preview
for c in ["Opening Date", "Deadline 1", "Deadline 2", "Deadline"]:
    if c in df.columns:
        df[c] = pd.to_datetime(df[c], dayfirst=True, errors="coerce")

# 4. Show preview
st.subheader("Preview of fixed Excel")
st.dataframe(df.head(20), use_container_width=True)

# 5. Download button
output = io.BytesIO()
with pd.ExcelWriter(output, engine="openpyxl") as xw:
    df.to_excel(xw, index=False, sheet_name="parsed")
output.seek(0)
st.download_button(
    label="⬇️ Download parsed Excel",
    data=output,
    file_name="calls_fixed.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
