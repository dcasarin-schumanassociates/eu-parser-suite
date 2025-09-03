import pandas as pd
from io import BytesIO
from typing import Any, Dict, Optional
from datetime import datetime

# Small helper: normalise "23 Sep 2025", "23 September 2025", "23 Sept. 2025" → "2025-09-23"
def _normalise_date_iso(d: Optional[str]) -> Optional[str]:
    if not d:
        return None
    s = " ".join(d.strip().split())  # collapse spaces
    # remove trailing dot on month (e.g. "Sept.")
    parts = s.split()
    if len(parts) == 3 and parts[1].endswith("."):
        parts[1] = parts[1].rstrip(".")
        s = " ".join(parts)
    # try common formats
    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return None  # keep strict; avoids silent misparses

def parse_pdf(file_like, *, source_filename: str = "", version_label: str = "Unknown", parsed_on_utc: str = "") -> pd.DataFrame:
    """
    Orchestrates your working pipeline and returns a DataFrame that matches your current app's output.
    No Streamlit here; just pure parsing.
    """
    # Read bytes once, then work with a new BytesIO so downstream .read() works
    pdf_bytes = file_like.read()
    raw_text = extract_text_from_pdf(BytesIO(pdf_bytes))

    topic_blocks = extract_topic_blocks(raw_text)
    metadata_by_code = extract_metadata_blocks(raw_text)

    enriched = [
        {
            **topic,
            **extract_data_fields(topic),
            **metadata_by_code.get(topic["code"], {})
        }
        for topic in topic_blocks
    ]

    df = pd.DataFrame([{
        "Code": t["code"],
        "Title": t["title"],

        "Opening Date": t.get("opening_date"),
        "Deadline": t.get("deadline"),
        "First Stage Deadline": t.get("deadline_stage1"),
        "Second Stage Deadline": t.get("deadline_stage2"),

        # ISO-normalised mirrors (safe for calendar/Gantt)
        "Opening Date (ISO)": _normalise_date_iso(t.get("opening_date")),
        "Deadline (ISO)": _normalise_date_iso(t.get("deadline")),
        "First Stage Deadline (ISO)": _normalise_date_iso(t.get("deadline_stage1")),
        "Second Stage Deadline (ISO)": _normalise_date_iso(t.get("deadline_stage2")),

        # Boolean flag for branching in visualisations/logic
        "Two-Stage": bool(t.get("is_two_stage")),

        "Destination": t.get("destination"),
        "Budget Per Project": t.get("budget_per_project"),
        "Total Budget": t.get("indicative_total_budget"),
        "Number of Projects": int(t["indicative_total_budget"] / t["budget_per_project"])
            if t.get("budget_per_project") and t.get("indicative_total_budget") else None,
        "Type of Action": t.get("type_of_action"),
        "TRL": t.get("trl"),
        "Call Name": t.get("call"),
        "Expected Outcome": t.get("expected_outcome"),
        "Scope": t.get("scope"),
        "Description": t.get("full_text"),
        # provenance (optional)
        "Source Filename": source_filename,
        "Version Label": version_label,
        "Parsed On (UTC)": parsed_on_utc,
    } for t in enriched])

    return df
