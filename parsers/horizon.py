# parsers/horizon.py
from __future__ import annotations

# ===== Imports (standard lib) =====
import re
from io import BytesIO
from typing import Dict, Any, List, Optional
from datetime import datetime

# ===== Imports (third-party) =====
import fitz  # PyMuPDF
import pandas as pd


# =============================================================================
# PDF PARSING
# =============================================================================
def extract_text_from_pdf(file_like: BytesIO) -> str:
    """
    Extract text from a PDF-like stream (positioned at start) as a single string.
    """
    with fitz.open(stream=file_like.read(), filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)


# =============================================================================
# TEXT NORMALISATION UTIL
# =============================================================================
def normalize_text(text: str) -> str:
    """
    Normalise whitespace and line breaks; collapse runs of spaces/tabs/newlines.
    """
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r"\xa0", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n+", "\n", text)
    return text.strip()


# =============================================================================
# TOPIC EXTRACTION
#   - Detects HORIZON topic headers and slices out each topic's text block.
# =============================================================================
def extract_topic_blocks(text: str) -> List[Dict[str, Any]]:
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # Join split code/title lines (e.g. code on one line, title on the next)
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
            # Heuristic: only consider as topic if the next lines contain key markers
            lookahead_text = "\n".join(fixed_lines[i + 1:i + 20]).lower()
            if any(key in lookahead_text for key in ["call:", "type of action"]):
                candidate_topics.append({
                    "code": match.group(1),
                    "title": match.group(2).strip(),
                    "start_line": i
                })

    # Slice text blocks between topic headers, stopping early at "This destination"
    topic_blocks = []
    for idx, topic in enumerate(candidate_topics):
        start = topic["start_line"]
        end = candidate_topics[idx + 1]["start_line"] if idx + 1 < len(candidate_topics) else len(fixed_lines)
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


# =============================================================================
# FIELD EXTRACTION (within a topic block)
#   - Budget, total budget, type of action, sections, call name, TRL, etc.
# =============================================================================
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


# =============================================================================
# DEADLINE PARSING HELPERS (two-stage; keeps original single-date detection elsewhere)
# =============================================================================

DATE_RE = re.compile(r"\b\d{1,2}\s+[A-Za-z]{3,9}\.?\s+\d{4}\b")  # 23 Sep 2025 / 23 Sept. 2025 / 23 September 2025
OPENING_TRIGGER_RE  = re.compile(r"^\s*opening(?:\s*date)?\s*:\s*", re.IGNORECASE)
DEADLINE_TRIGGER_RE = re.compile(r"^\s*deadline", re.IGNORECASE)
DEST_TRIGGER_RE     = re.compile(r"^\s*destination\s*:", re.IGNORECASE)

def _find_first_date_in(lines: List[str]) -> str | None:
    """Return first date string found across a list of lines, else None."""
    for ln in lines:
        m = DATE_RE.search(ln)
        if m:
            return m.group(0)
    return None

# Example: "Deadline(s): 23 Sep 2025 (First Stage), 14 Apr 2026 (Second Stage)"
DATE_TOKEN = r"\d{1,2}\s+[A-Za-z]{3,9}\.?\s+\d{4}"
TWO_STAGE_DEADLINES_RE = re.compile(
    rf"deadline\(s\)\s*:\s*"
    rf"({DATE_TOKEN})\s*\(([^)]+?)\)\s*,\s*"
    rf"({DATE_TOKEN})\s*\(([^)]+?)\)",
    re.IGNORECASE
)

def parse_two_stage_deadlines(line: str) -> Dict[str, str]:
    """
    Return {'deadline_stage1': 'DD Mon YYYY', 'deadline_stage2': 'DD Mon YYYY'}
    if a two-stage pattern is present on the given line; otherwise {}.
    """
    m = TWO_STAGE_DEADLINES_RE.search(line)
    if not m:
        return {}
    # Only dates; ignore labels
    return {
        "deadline_stage1": m.group(1),
        "deadline_stage2": m.group(3),
    }


# =============================================================================
# METADATA EXTRACTION (opening date, deadlines, destination) PER TOPIC
#   - Preserves original single-deadline behaviour
#   - Adds optional two-stage dates guarded by code containing '-two-stage'
#   - Adds boolean is_two_stage
# =============================================================================

def extract_metadata_blocks(text: str) -> Dict[str, Dict[str, Any]]:
    lines = normalize_text(text).splitlines()

    metadata_map: Dict[str, Dict[str, Any]] = {}
    current_metadata: Dict[str, Any] = {
        "opening_date": None,
        "deadline": None,          # single-stage (existing behaviour)
        "deadline_stage1": None,   # only for two-stage topics
        "deadline_stage2": None,   # only for two-stage topics
        "destination": None,
        "is_two_stage": False,     # boolean flag
    }

    # Case-insensitive topic code; allow lowercase in codes
    topic_pattern = re.compile(r"^(HORIZON-[A-Z0-9\-]+):", re.IGNORECASE)
    collecting = False

    for idx, raw in enumerate(lines):
        line = raw.strip()
        next1 = lines[idx + 1].strip() if idx + 1 < len(lines) else ""
        next2 = lines[idx + 2].strip() if idx + 2 < len(lines) else ""
        line_plus_next = f"{line} {next1}".strip()

        # --- Opening ---
        if OPENING_TRIGGER_RE.match(line):
            # Try same line after the colon, then look ahead up to two lines
            opening_date = _find_first_date_in([line, next1, next2])
            current_metadata["opening_date"] = opening_date

            # Reset per-call metadata on a new Opening section
            current_metadata["deadline"] = None
            current_metadata["deadline_stage1"] = None
            current_metadata["deadline_stage2"] = None
            current_metadata["destination"] = None
            current_metadata["is_two_stage"] = False
            collecting = True
            continue

        # --- Deadline(s) ---
        if collecting and DEADLINE_TRIGGER_RE.match(line):
            # KEEP: original behaviour (first date), but with next-line fallback
            deadline = _find_first_date_in([line, next1])
            current_metadata["deadline"] = deadline

            # Two-stage parse (handle wrapping by parsing line + next)
            extra = parse_two_stage_deadlines(line_plus_next)
            if extra:
                current_metadata.update(extra)
            continue

        # --- Destination ---
        if collecting and DEST_TRIGGER_RE.match(line):
            current_metadata["destination"] = line.split(":", 1)[-1].strip()
            continue

        # --- Topic boundary: attach snapshot to this code ---
        if collecting:
            match = topic_pattern.match(line)
            if match:
                code = match.group(1)
                to_save = current_metadata.copy()

                # Only attach two-stage data to '-two-stage' topics
                if "-two-stage" in code.lower():
                    to_save["is_two_stage"] = bool(
                        to_save.get("deadline_stage1") and to_save.get("deadline_stage2")
                    )
                else:
                    to_save["is_two_stage"] = False
                    to_save["deadline_stage1"] = None
                    to_save["deadline_stage2"] = None

                # KEY: normalise the key to a consistent case to avoid join misses
                metadata_map[code.upper()] = to_save

    return metadata_map

# =============================================================================
# DATE NORMALISATION (for ISO-only output columns)
# =============================================================================
def _normalise_date_iso(d: Optional[str]) -> Optional[str]:
    """
    Convert 'DD Mon YYYY' / 'DD Month YYYY' (with optional trailing '.' on month)
    to ISO 'YYYY-MM-DD'. Returns None if parsing fails or input is falsey.
    """
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
    return None


# =============================================================================
# PUBLIC API: parse_pdf -> DataFrame
#   - Orchestrates extraction and returns a DF with ISO-only date columns
#   - Preserves all original business logic
# =============================================================================
def parse_pdf(file_like, *, source_filename: str = "", version_label: str = "Unknown", parsed_on_utc: str = "") -> pd.DataFrame:
    """
    Orchestrates the pipeline and returns a DataFrame that matches the app's output.
    Produces only ISO-formatted date columns + boolean 'Two-Stage'.
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
            **metadata_by_code.get(topic["code"].upper(), {})  # <— normalised join key
        }
        for topic in topic_blocks
    ]

    df = pd.DataFrame([{
        "Code": t["code"],
        "Title": t["title"],

        # ISO-only date fields
        "Opening Date": _normalise_date_iso(t.get("opening_date")),
        "Deadline": _normalise_date_iso(t.get("deadline")),
        "First Stage Deadline": _normalise_date_iso(t.get("deadline_stage1")),
        "Second Stage Deadline": _normalise_date_iso(t.get("deadline_stage2")),

        # Boolean flag for single/two-stage logic in downstream visuals
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

        # provenance
        "Source Filename": source_filename,
        "Version Label": version_label,
        "Parsed On (UTC)": parsed_on_utc,
    } for t in enriched])

    return df
