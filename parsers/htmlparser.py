import streamlit as st
import fitz  # PyMuPDF
from io import BytesIO

st.set_page_config("PDF HTML Viewer", layout="wide")

st.title("📄 PDF → HTML Extractor (PyMuPDF)")

# File uploader
pdf_file = st.file_uploader("Upload a PDF", type=["pdf"])

def extract_pdf_as_html(file_bytes: bytes) -> str:
    """Extract PDF text with formatting using PyMuPDF's 'html' mode."""
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        html_pages = []
        for i, page in enumerate(doc, start=1):
            html_pages.append(f"<h3>Page {i}</h3>")
            html_pages.append(page.get_text("html"))
        return "\n".join(html_pages)

if pdf_file:
    file_bytes = pdf_file.read()

    with st.spinner("Extracting PDF as HTML..."):
        html_content = extract_pdf_as_html(file_bytes)

    st.success("Extraction complete ✅")

    # Display inside an iframe for proper HTML rendering
    st.markdown("### Preview (rendered HTML)")
    st.components.v1.html(
        f"<div style='padding:1rem; max-height:600px; overflow:auto;'>{html_content}</div>",
        height=600,
        scrolling=True
    )

    # Optionally also show raw HTML text for inspection
    with st.expander("🔎 Raw HTML output"):
        st.text_area("Raw HTML", value=html_content, height=400)
