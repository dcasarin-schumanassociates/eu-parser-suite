# parsers/horizon.py
from __future__ import annotations

import re
from typing import BinaryIO, List, Dict, Any
import fitz  # PyMuPDF
import pandas as pd
from io import BytesIO

BASE_COLUMNS = [
    "programme", "cluster", "code", "title",
    "opening_date", "deadline",
    "budget_per_project_eur", "total_budget_eur",
    "type_of_action", "trl", "destination_or_strand",
    "call_name", "expected_outcome", "scope", "full_text",
    "source_filename", "version_label", "parsed_on_utc"
]

def _extract_text(pdf_file: BinaryIO) -> str:
    # IMPORTANT: we must re-open from bytes because Streamlit uploads are IO-like
    data = pdf_file.read()
    with fitz.open(stream=data, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)

def _find_topic_blocks(text: str) -> List[Dict[str, Any]]:
    """
    Minimal heuristic: find lines beginning with HORIZON-... and take the next line as the title.
    Replace with your robust logic (you already have it).
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    blocks = []
    for i, line in enumerate(lines):
        m = re.match(r"^(HORIZON-[A-Za-z0-9\-]+):?\s*(.*)$", line)
        if m:
            code = m.group(1)
            title = m.group(2) if m.group(2) else (lines[i+1] if i+1 < len(lines) else "")
            # naive slice for full text
            full_text = "\n".join(lines[i:i+30])
            blocks.append({"code": code, "title": title, "full_text": full_text})
    return blocks

def parse_pdf(file, *, source_filename: str = "", version_label: str = "Unknown", parsed_on_utc: str = "") -> pd.DataFrame:
    """
    Return a DataFrame with the base schema. Unknown fields left as None.
    Replace the internals with your existing Horizon extractor when ready.
    """
    raw_text = _extract_text(file)

    # Try to infer cluster from text (very rough, safe default to empty)
    cluster = ""
    cluster_match = re.search(r"\bCluster\s*(\d)\b", raw_text, re.IGNORECASE)
    if cluster_match:
        cluster = f"CL{cluster_match.group(1)}"

    topics = _find_topic_blocks(raw_text)
    rows = []
    for t in topics or [{"code": None, "title": "", "full_text": raw_text[:2000]}]:
        rows.append({
            "programme": "Horizon Europe",
            "cluster": cluster,
            "code": t.get("code"),
            "title": t.get("title"),
            "opening_date": None,
            "deadline": None,
            "budget_per_project_eur": None,
            "total_budget_eur": None,
            "type_of_action": None,
            "trl": None,
            "destination_or_strand": None,
            "call_name": None,
            "expected_outcome": None,
            "scope": None,
            "full_text": t.get("full_text"),
            "source_filename": source_filename,
            "version_label": version_label,
            "parsed_on_utc": parsed_on_utc,
        })

    df = pd.DataFrame(rows, columns=BASE_COLUMNS)
    return df
