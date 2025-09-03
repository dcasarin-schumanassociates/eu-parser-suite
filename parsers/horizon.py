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
# DEADLINE & DESTINATION HELPERS
#   - Keeps original single-date detection (deadline)
#   - Adds flexible opening/deadline triggers + nearby destination scans
#   - Adds interstitial destination fallback (text between dates and first topic)
# =============================================================================

# Dates like: 23 Sep 2025 / 23 Sept. 2025 / 23 September 2025
DATE_RE = re.compile(r"\b\d{1,2}\s+[A-Za-z]{3,9}\.?\s+\d{4}\b")

# Accept colon or dash (ASCII '-', en dash '–', em dash '—') after 'Destination'
DEST_SEP = r"[-–—:]"

# Triggers with optional spacing/case variants
OPENING_TRIGGER_RE  = re.compile(r"^\s*opening(?:\s*date)?\s*:\s*", re.IGNORECASE)
DEADLINE_TRIGGER_RE = re.compile(r"^\s*deadline", re.IGNORECASE)
# Updated to accept ':' or any dash after 'Destination' (+ optional number)
DEST_TRIGGER_RE     = re.compile(rf"^\s*destination(?:\s*\d+)?\s*{DEST_SEP}\s*", re.IGNORECASE)

def _find_first_date_in(lines: List[str]) -> str | None:
    """Return first date string found across the given lines; else None."""
    for ln in lines:
        m = DATE_RE.search(ln)
        if m:
            return m.group(0)
    return None

# Two-stage deadlines like:
# "Deadline(s): 23 Sep 2025 (First Stage), 14 Apr 2026 (Second Stage)"
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
    if a two-stage pattern is present in the given string; otherwise {}.
    """
    m = TWO_STAGE_DEADLINES_RE.search(line)
    if not m:
        return {}
    return {"deadline_stage1": m.group(1), "deadline_stage2": m.group(3)}

# Topic header code (used as a boundary for the interstitial fallback)
TOPIC_CODE_RE = re.compile(r"^(HORIZON-[A-Z0-9\-]+):", re.IGNORECASE)

# Explicit "Destination" line (with optional numbering: "Destination 1:")
DEST_LINE_RE = re.compile(rf"^\s*destination(?:\s*\d+)?\s*{DEST_SEP}\s*(.*)$", re.IGNORECASE)

# Section starts that should stop continuation (Opening/Deadline/Destination/Topic)
SECTION_START_RE = re.compile(
    rf"^\s*(opening(?:\s*date)?\s*:|deadline|destination(?:\s*\d+)?\s*{DEST_SEP}\s*|horizon-[a-z0-9\-]+:)",
    re.IGNORECASE
)

def _scan_forward_destination(lines: List[str], start_idx: int, *, max_ahead: int = 8) -> str | None:
    """
    Scan forward up to 'max_ahead' lines for an explicit 'Destination...' line.
    Also capture a single wrapped continuation line if the next line doesn't start a new section.
    """
    for k in range(start_idx, min(start_idx + max_ahead, len(lines))):
        line = lines[k].strip()
        m = DEST_LINE_RE.match(line)
        if not m:
            continue

        dest = m.group(1).strip()

        # Capture one soft-wrapped continuation line
        nxt_idx = k + 1
        if nxt_idx < len(lines):
            nxt = lines[nxt_idx].strip()
            if nxt and not SECTION_START_RE.match(nxt):
                dest = (dest + " " + nxt).strip()

        return dest or None
    return None

def _capture_interstitial_destination(lines: List[str], date_idx: int, *, max_span: int = 12) -> str | None:
    """
    Fallback: capture the free text between the date block (Opening/Deadline) and
    the next topic code ('HORIZON-...:'). Applies several heuristics to avoid false positives.
    """
    # Find the next topic boundary
    end = min(date_idx + 1 + max_span, len(lines))
    for k in range(date_idx + 1, end):
        if TOPIC_CODE_RE.match(lines[k].strip()):
            end = k
            break

    # Collect candidate lines, skipping headers/numeric/action-type lines
    buf: List[str] = []
    for raw in lines[date_idx + 1:end]:
        s = raw.strip()
        if not s:
            continue
        if TOPIC_CODE_RE.match(s) or SECTION_START_RE.match(s):
            break
        if re.match(r"^(topics|type\s+of\s+action|budgets|expected\s+eu\s+contribution|indicative\s+number)", s, re.IGNORECASE):
            continue
        if re.match(r"^(RIA|IA)\b", s):            # action types
            continue
        if re.match(r"^[\d\.\, ]+$", s):           # numeric-only lines
            continue

        buf.append(s)
        if len(" ".join(buf)) > 300:               # conservative bound
            break

    candidate = " ".join(buf).strip()
    if not candidate:
        return None

    # Sanity checks: looks like prose, not a code/table fragment
    letters = sum(ch.isalpha() for ch in candidate)
    digits  = sum(ch.isdigit() for ch in candidate)
    if len(candidate) >= 15 and letters > max(1, 2 * digits) and "HORIZON-" not in candidate.upper():
        return candidate
    return None


# =============================================================================
# METADATA EXTRACTION (opening date, deadlines, destination) PER TOPIC
#   - Preserves original single-deadline behaviour
#   - Adds optional two-stage dates guarded by code containing '-two-stage'
#   - Adds boolean is_two_stage
#   - First-write-wins and case-normalised keys for robust merge
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

    topic_pattern = TOPIC_CODE_RE  # alias for readability
    collecting = False

    for idx, raw in enumerate(lines):
        line = raw.strip()
        next1 = lines[idx + 1].strip() if idx + 1 < len(lines) else ""
        next2 = lines[idx + 2].strip() if idx + 2 < len(lines) else ""
        line_plus_next = f"{line} {next1}".strip()

        # --- Opening ---
        if OPENING_TRIGGER_RE.match(line):
            opening_date = _find_first_date_in([line, next1, next2])
            current_metadata["opening_date"] = opening_date

            # Reset per-call metadata
            current_metadata["deadline"] = None
            current_metadata["deadline_stage1"] = None
            current_metadata["deadline_stage2"] = None
            current_metadata["destination"] = None
            current_metadata["is_two_stage"] = False
            collecting = True

            # (A) explicit Destination scan near Opening
            if current_metadata["destination"] is None:
                maybe_dest = _scan_forward_destination(lines, idx + 1)
                if maybe_dest:
                    current_metadata["destination"] = maybe_dest
            # (B) interstitial fallback if still empty
            if current_metadata["destination"] is None:
                maybe_free = _capture_interstitial_destination(lines, idx)
                if maybe_free:
                    current_metadata["destination"] = maybe_free
            continue

        # --- Deadline(s) ---
        if collecting and DEADLINE_TRIGGER_RE.match(line):
            # Original behaviour (first date), with next-line fallback
            deadline = _find_first_date_in([line, next1])
            current_metadata["deadline"] = deadline

            # Two-stage parse (handle wrapping by parsing line + next)
            extra = parse_two_stage_deadlines(line_plus_next)
            if extra:
                current_metadata.update(extra)

            # Explicit Destination scan near Deadline
            if current_metadata["destination"] is None:
                maybe_dest = _scan_forward_destination(lines, idx + 1)
                if maybe_dest:
                    current_metadata["destination"] = maybe_dest
            # Interstitial fallback (date block → next topic code)
            if current_metadata["destination"] is None:
                maybe_free = _capture_interstitial_destination(lines, idx)
                if maybe_free:
                    current_metadata["destination"] = maybe_free
            continue

        # --- Destination (explicit line) ---
        if collecting and DEST_TRIGGER_RE.match(line):
            # Keep explicit capture consistent with the regex that accepts ':' or dashes
            m = DEST_LINE_RE.match(line)
            if m:
                current_metadata["destination"] = m.group(1).strip()
            else:
                current_metadata["destination"] = line.split(":", 1)[-1].strip()
            continue

        # --- Topic boundary: attach snapshot (first-write-wins) ---
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

                key = code.upper()
                if key not in metadata_map:  # first-write-wins
                    metadata_map[key] = to_save

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
# CLUSTER DETECTION (from the very beginning of the file)
# =============================================================================
_CLUSTER_LINE_RE = re.compile(r"^\s*(\d+)\.\s*(.+?)\s*$")

def extract_cluster_header(text: str) -> Dict[str, Any]:
    """
    Find a leading line like '8. Climate, Energy and Mobility' near the start of the PDF.
    Returns a dict with 'cluster_title', 'cluster_number', 'cluster_index' (number - 3).
    """
    lines = normalize_text(text).splitlines()
    # Look only near the beginning to avoid false matches later in the WP.
    for line in lines[:120]:
        m = _CLUSTER_LINE_RE.match(line)
        if not m:
            continue
        num = int(m.group(1))
        name = m.group(2).strip()
        # Guard against obvious non-cluster lines
        if name.lower().startswith(("part", "page", "annex")):
            continue
        return {
            "cluster_title": f"{num}. {name}",
            "cluster_number": num,
            "cluster_index": num - 3
        }
    return {"cluster_title": None, "cluster_number": None, "cluster_index": None}


# =============================================================================
# PUBLIC API: parse_pdf -> DataFrame
#   - Orchestrates extraction and returns a DF with ISO-only date columns
#   - Preserves all original business logic
# =============================================================================
def parse_pdf(file_like, *, source_filename: str = "", version_label: str = "Unknown", parsed_on_utc: str = "") -> pd.DataFrame:
    """
    Orchestrates the pipeline and returns a DataFrame that matches the app's output.
    Produces only ISO-formatted date columns + boolean 'Two-Stage' + cluster columns.
    """
    # Read bytes once, then work with a new BytesIO so downstream .read() works
    pdf_bytes = file_like.read()
    raw_text = extract_text_from_pdf(BytesIO(pdf_bytes))

    # Detect cluster once from the whole document
    cluster_info = extract_cluster_header(raw_text)

    topic_blocks = extract_topic_blocks(raw_text)
    metadata_by_code = extract_metadata_blocks(raw_text)

    enriched = [
        {
            **topic,
            **extract_data_fields(topic),
            # use uppercased code for the lookup to match how we saved it
            **metadata_by_code.get(topic["code"].upper(), {})
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

        # Cluster columns
        "Cluster": cluster_info["cluster_title"],       # e.g., "8. Climate, Energy and Mobility"
        "Cluster Index": cluster_info["cluster_index"], # e.g., 5 (8 - 3)

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
