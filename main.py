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

# --- Gantt chart ---
st.subheader("📅 Gantt (Calls Timeline)")

gantt_df = df.copy()
for c in ["Opening Date", "Deadline"]:
    if c in gantt_df.columns:
        gantt_df[c] = pd.to_datetime(gantt_df[c], errors="coerce")

gantt_df = gantt_df.dropna(subset=["Opening Date", "Deadline"])

# Shorten or wrap long titles with line breaks
def _wrap_title(s: str, n: int = 60) -> str:
    """
    Insert a line break every ~n characters (on spaces if possible).
    """
    if not isinstance(s, str):
        return ""
    words = s.split()
    lines, current = [], ""
    for w in words:
        if len(current) + len(w) + 1 <= n:
            current += (" " if current else "") + w
        else:
            lines.append(current)
            current = w
    if current:
        lines.append(current)
    # join with <br> so Plotly will render it as multi-line
    return "<br>".join(lines)

gantt_df["_TitleWrapped"] = gantt_df["Title"].apply(lambda x: _wrap_title(str(x), n=60))

colour_dim = "Type of Action" if "Type of Action" in gantt_df.columns else None

fig = px.timeline(
    gantt_df,
    x_start="Opening Date",
    x_end="Deadline",
    y="_TitleWrapped",
    color=colour_dim,
    hover_data={
        "Title": True,
        "Code": "Code" in gantt_df.columns,
        "Opening Date": True,
        "Deadline": True,
        "Destination": "Destination" in gantt_df.columns,
        "Type of Action": "Type of Action" in gantt_df.columns,
        "TRL": "TRL" in gantt_df.columns,
        "Call Name": "Call Name" in gantt_df.columns,
    },
)

fig.update_yaxes(autorange="reversed")

# increase row height
n_rows = len(gantt_df)
fig.update_layout(
    yaxis=dict(tickfont=dict(size=10)),   # smaller font for labels
    margin=dict(l=250),                   # more space for wrapped labels
    height=200 + n_rows * 40              # scale row height
)

fig.update_xaxes(rangeslider_visible=True)

st.plotly_chart(fig, use_container_width=True)

