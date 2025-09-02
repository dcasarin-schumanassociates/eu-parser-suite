import fitz  # PyMuPDF

def extract_pdf_text_from_bytes(pdf_bytes: bytes) -> str:
    """Return concatenated text from PDF bytes."""
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)
