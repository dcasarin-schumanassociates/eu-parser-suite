# parsers/erasmus.py
from __future__ import annotations
import pandas as pd

BASE_COLUMNS = [
    "programme", "cluster", "code", "title",
    "opening_date", "deadline",
    "budget_per_project_eur", "total_budget_eur",
    "type_of_action", "trl", "destination_or_strand",
    "call_name", "expected_outcome", "scope", "full_text",
    "source_filename", "version_label", "parsed_on_utc"
]

def parse_pdf(file, *, source_filename: str = "", version_label: str = "Unknown", parsed_on_utc: str = "") -> pd.DataFrame:
    _ = file.read()  # consume upload; we don't parse yet
    data = [{
        "programme": "Erasmus+",
        "cluster": None,
        "code": None,
        "title": "Parser not implemented yet",
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
        "full_text": "Erasmus+ parsing to be added in a future version.",
        "source_filename": source_filename,
        "version_label": version_label,
        "parsed_on_utc": parsed_on_utc,
    }]
    return pd.DataFrame(data, columns=BASE_COLUMNS)
