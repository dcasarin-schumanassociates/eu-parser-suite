# parsers/horizon.py
from __future__ import annotations
import re
from io import BytesIO
from typing import Dict, Any, List
import fitz  # PyMuPDF
import pandas as pd

# ========== PDF Parsing ==========
def extract_text_from_pdf(file_like: BytesIO) -> str:
    # file_like should be positioned at start
    with fitz.open(stream=file_like.read(), filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)

# ========== Utility ==========
def normalize_text(text: str) -> str:
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r"\xa0", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n+", "\n", text)
    return text.strip()

# ========== Topic Extraction ==========
def extract_topic_blocks(text: str) -> List[Dict[str, Any]]:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    fixed_lines = []
    i = 0
    while i < len(lines):
        if re.match(r"^HORIZON-[A-Z0-9\-]+:?$", lines[i]) and i + 1 < len(lines):
            fixed_lines.append(f"{lines[i]} {lines[i + 1]}")
            i += 2
        else:
            fixed_lines.append(lines[i])
            i += 1

    topic_pattern = r"^(HORIZON-[A-Za-z0-9\-]+):\s*(.*)$"
    candidate_topics = []
    for i, line in enumerate(fixed_lines):
        match = re.match(topic_pattern, line)
        if match:
            lookahead_text = "\n".join(fixed_lines[i+1:i+20]).lower()
            if any(key in lookahead_text for key in ["call:", "type of action"]):
                candidate_topics.append({
                    "code": match.group(1),
                    "title": match.group(2).strip(),
                    "start_line": i
                })

    topic_blocks = []
    for idx, topic in enumerate(candidate_topics):
        start = topic["start_line"]
        end = candidate_topics[idx + 1]["start_line"] if idx + 1 < len(fixed_lines) else len(fixed_lines)
        for j in range(start + 1, end):
            if fixed_lines[j].lower().startswith("this destination"):
                end = j
                break
        topic_blocks.append({
            "code": topic["code"],
            "title": topic["title"],
            "full_text": "\n".join(fixed_lines[start:end]).strip()
        })

    return topic_blocks

# ========== Field Extraction ==========
def extract_data_fields(topic: Dict[str, Any]) -> Dict[str, Any]:
    text = normalize_text(topic["full_text"])

    def extract_budget(txt: str):
        m = re.search(r"around\s+eur\s+([\d.,]+)", txt.lower())
        if m:
            return int(float(m.group(1).replace(",", "")) * 1_000_000)
        m = re.search(r"between\s+eur\s+[\d.,]+\s+and\s+([\d.,]+)", txt.lower())
        if m:
            return int(float(m.group(1).replace(",", "")) * 1_000_000)
        return None

    def extract_total_budget(txt: str):
        m = re.search(r"indicative budget.*?eur\s?([\d.,]+)", txt.lower())
        return int(float(m.group(1).replace(",", "")) * 1_000_000) if m else None

    def get_section(keyword: str, stop_keywords: List[str]):
        lines = text.splitlines()
        collecting = False
        section = []
        for line in lines:
            l = line.lower()
            if not collecting and keyword in l:
                collecting = True
                section.append(line.split(":", 1)[-1].strip())
            elif collecting and any(l.startswith(k) for k in stop_keywords):
                break
            elif collecting:
                section.append(line)
        return "\n".join(section).strip() if section else None

    def extract_type_of_action(txt: str):
        lines = txt.splitlines()
        for i, line in enumerate(lines):
            if "type of action" in line.lower():
                for j in range(i + 1, len(lines)):
                    if lines[j].strip():
                        return lines[j].strip()
        return None

    def extract_topic_title(txt: str):
        lines = txt.strip().splitlines()
        title_lines = []
        found = False
        for line in lines:
            if not found:
                m = re.match(r"^(HORIZON-[A-Za-z0-9-]+):\s*(.*)", line)
                if m:
                    found = True
                    title_lines.append(m.group(2).strip())
            else:
                if re.match(r"^\s*Call[:\-]", line, re.IGNORECASE):
                    break
                elif line.strip():
                    title_lines.append(line.strip())
        return " ".join(title_lines) if title_lines else None

    def extract_call_name_topic(txt: str):
        txt = normalize_text(txt)
        m = re.search(r"(?i)^\s*Call:\s*(.+)$", txt, re.MULTILINE)
        if m:
            return m.group(1).strip()
        return None

    return {
        "title": extract_topic_title(text),
        "budget_per_project": extract_budget(text),
        "indicative_total_budget": extract_total_budget(text),
        "type_of_action": extract_type_of_action(text),
        "expected_outcome": get_section("expected outcome:", ["scope:", "objective:", "expected impact:", "eligibility:", "budget"]),
        "scope": get_section("scope:", ["objective:", "expected outcome:", "expected impact:", "budget"]),
        "call": extract_call_name_topic(text),
        "trl": (m := re.search(r"TRL\s*(\d+)[^\d]*(\d+)?", text, re.IGNORECASE)) and (
            f"{m.group(1)}-{m.group(2)}" if m.group(2) else m.group(1)
        )
    }

# ========== Metadata (Opening / Deadlines / Destination) ==========
def extract_metadata_blocks(text: str) -> Dict[str, Dict[str, Any]]:
    """
    Original behaviour preserved for single deadlines (now mapped to 'deadline1').
    Adds simple support for two-stage lines like:
      'Deadline(s): 23 Sep 2025 (First Stage), 14 Apr 2026 (Second Stage)'
    without turning one-stage into two.
    """
    lines = normalize_text(text).splitlines()

    metadata_map: Dict[str, Dict[str, Any]] = {}
    current_metadata = {
        "opening_date": None,
        "deadline1": None,
        "deadline2": None,
        "destination": None
    }

    topic_pattern = re.compile(r"^(HORIZON-[A-Z0-9\-]+):")
    # Single-format date (keep as your original: '15 March 2026' / '15 Mar 2026')
    date_wordy = re.compile(r"(\d{1,2}\s+\w+\s+\d{4})")

    collecting = False
    for line in lines:
        lower = line.lower().strip()

        # Opening: (unchanged)
        if lower.startswith("opening:"):
            m = date_wordy.search(line)
            current_metadata["opening_date"] = m.group(1) if m else None
            # Reset deadlines on a new header block
            current_metadata["deadline1"] = None
            current_metadata["deadline2"] = None
            collecting = True
            continue

        # Deadline(s): — keep simple & robust
        if collecting and lower.startswith("deadline"):
            # Find ALL dates on the line; then decide 1 vs 2
            dates = date_wordy.findall(line) if "deadline" in lower else []
            if len(dates) >= 2:
                # Two-stage case → put first two into Deadline1/Deadline2
                current_metadata["deadline1"] = dates[0]
                current_metadata["deadline2"] = dates[1]
            elif len(dates) == 1:
                # Single deadline → only Deadline1; do NOT set Deadline2
                current_metadata["deadline1"] = dates[0]
                current_metadata["deadline2"] = None
            else:
                # No date parsed on this line – leave as is
                pass
            continue

        # Destination: (unchanged)
        if collecting and lower.startswith("destination"):
            current_metadata["destination"] = line.split(":", 1)[-1].strip()
            continue

        # When we encounter a topic code after collecting, bind the snapshot
        if collecting:
            match = topic_pattern.match(line)
            if match:
                code = match.group(1)
                metadata_map[code] = current_metadata.copy()

    return metadata_map

# ========== Public API ==========
def parse_pdf(file_like, *, source_filename: str = "", version_label: str = "Unknown", parsed_on_utc: str = "") -> pd.DataFrame:
    """
    Orchestrates your working pipeline and returns a DataFrame that matches your app's output,
    now with Deadline1 and Deadline2 columns.
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
        "Deadline1": t.get("deadline1"),          # ← single or first deadline
        "Deadline2": t.get("deadline2"),          # ← second stage (if any)
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
        # provenance (optional; blank for now – add if you like)
        "Source Filename": source_filename,
        "Version Label": version_label,
        "Parsed On (UTC)": parsed_on_utc,
    } for t in enriched])

    return df
