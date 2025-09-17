import streamlit as st
import fitz  # PyMuPDF

st.set_page_config("PDF Extractor Viewer", layout="wide")
st.title("📄 PDF Extractor (compare modes)")

# Upload PDF
pdf_file = st.file_uploader("Upload a PDF", type=["pdf"])

# Mode selector
mode = st.radio(
    "Choose extraction mode",
    ["text", "html", "blocks", "dict"],
    horizontal=True,
    index=1
)

def extract_pdf(file_bytes: bytes, mode: str) -> str:
    """Extract PDF text using different PyMuPDF modes."""
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        if mode == "blocks":
            parts = []
            for i, page in enumerate(doc, start=1):
                parts.append(f"\n=== Page {i} ===\n")
                for block in page.get_text("blocks"):
                    if block[4].strip():
                        parts.append(block[4])
            return "\n".join(parts)

        elif mode == "html":
            return "\n".join(
                f"<h3>Page {i+1}</h3>" + page.get_text("html")
                for i, page in enumerate(doc)
            )

        elif mode == "dict":
            import json
            return "\n".join(
                f"\n=== Page {i+1} ===\n" + json.dumps(page.get_text("dict"), indent=2)
                for i, page in enumerate(doc)
            )

        else:  # default plain text
            return "\n".join(
                f"\n=== Page {i+1} ===\n" + page.get_text("text")
                for i, page in enumerate(doc)
            )

if pdf_file:
    file_bytes = pdf_file.read()
    with st.spinner(f"Extracting with mode '{mode}'..."):
        content = extract_pdf(file_bytes, mode)

    st.success("Extraction complete ✅")

    if mode == "html":
        st.markdown("### Rendered HTML")
        st.components.v1.html(
            f"<div style='padding:1rem; max-height:600px; overflow:auto;'>{content}</div>",
            height=600,
            scrolling=True
        )

    st.markdown("### Raw Output")
    st.text_area("Output", value=content, height=400)
