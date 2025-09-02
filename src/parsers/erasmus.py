from __future__ import annotations
import pandas as pd
from src.core.schema import ensure_base_schema
from src.core.utils_pdf import extract_pdf_text_from_bytes

def parse_erasmus_pdf(pdf_bytes: bytes, source_filename: str, version_label: str) -> pd.DataFrame:
    _ = extract_pdf_text_from_bytes(pdf_bytes)  # not used yet
    df_raw = pd.DataFrame(columns=[
        "cluster", "code", "title", "opening_date", "deadline",
        "destination_or_strand", "type_of_action", "trl",
        "budget_per_project_eur", "total_budget_eur",
        "call_name", "expected_outcome", "scope", "full_text",
    ])
    return ensure_base_schema(
        df_raw,
        programme="Erasmus+",
        source_filename=source_filename,
        version_label=version_label,
    )
