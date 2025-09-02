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
    "Erasmus+": erasmus_parser.parse_pdf,  # stub for now
    # "Digital Europe": digital_europe_parser.parse_pdf,  # add later
}

st.set_page_config(page_title="EU Calls Parser (Programme Selector)", layout="wide")
st.title("EU Calls Parser (Programme Selector)")

st.markdown(
    "Pick a **programme**, upload a **PDF**, and preview the parsed table.  \n"
    "Horizon uses your working extractor; Erasmus+ is a stub so you can test the flow.  \n"
    "Preview shows **Opening Date / Deadline 1 / Deadline 2** side-by-side."
)

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
        # Read once, then pass a fresh buffer to the selected parser
        pdf_bytes = uploaded.read()
        df = PARSERS[programme](
            io.BytesIO(pdf_bytes),
            source_filename=source_filename or "uploaded.pdf",
            version_label=version_label,
            parsed_on_utc=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        )

    # --- Normalise some types for display (safe if columns are missing) ---
    for c in ["Opening Date", "Deadline 1", "Deadline 2"]:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], dayfirst=True, errors="coerce")

    # --- Choose a clean column order for the preview (hide Description) ---
    preferred_order = [
        "Code", "Title",
        "Opening Date", "Deadline 1", "Deadline 2",
        "Destination",
        "Type of Action", "TRL",
        "Budget Per Project", "Total Budget",
        "Call Name",
        "Version Label", "Source Filename", "Parsed On (UTC)",
    ]
    preview_cols = [c for c in preferred_order if c in df.columns]
    other_cols = [c for c in df.columns if c not in preview_cols and c != "Description"]
    display_df = df[preview_cols + other_cols]

    # Optional: render dates in ISO for consistency (comment out if not desired)
    for c in ["Opening Date", "Deadline 1", "Deadline 2"]:
        if c in display_df.columns:
            display_df[c] = display_df[c].dt.strftime("%Y-%m-%d")

    st.subheader("📊 Preview (with Deadline 1 & Deadline 2)")
    st.dataframe(display_df.head(20), use_container_width=True)

    # --- Download Excel (includes full DataFrame with Description) ---
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
else:
    st.info("Select a programme and upload a PDF to begin.")
