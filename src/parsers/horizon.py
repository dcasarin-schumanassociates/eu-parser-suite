from __future__ import annotations
import re
import pandas as pd

from src.core.schema import ensure_base_schema
from src.core.utils_pdf import extract_pdf_text_from_bytes


# ---------- Utility ----------
def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\xa0", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n+", "\n", text)
    return text.strip()


# ---------- Topic detection ----------
def _extract_topic_blocks(text: str) -> list[dict]:
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
    for idx, line in enumerate(fixed_lines):
        m = re.match(topic_pattern, line)
        if m:
            lookahead = "\n".join(fixed_lines[idx + 1 : idx + 20]).lower()
            if any(k in lookahead for k in ["call:", "type of action"]):
                candidate_topics.append(
                    {"code": m.group(1), "title": m.group(2).strip(), "start_line": idx}
                )

    topic_blocks = []
    for j, topic in enumerate(candidate_topics):
        start = topic["start_line"]
        end = candidate_topics[j + 1]["start_line"] if j + 1 < len(candidate_topics) else len(fixed_lines)
        for k in range(start + 1, end):
            if fixed_lines[k].lower().startswith("this destination"):
                end = k
                break
        topic_blocks.append(
            {"code": topic["code"], "title": topic["title"], "full_text": "\n".join(fixed_lines[start:end]).strip()}
        )
    return topic_blocks


# ---------- Field extraction ----------
def _extract_data_fields(topic: dict) -> dict:
    text = _normalize_text(topic["full_text"])

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

    def get_section(keyword: str, stops: list[str]):
        lines = text.splitlines()
        collecting = False
        buf = []
        for line in lines:
            l = line.lower()
            if not collecting and keyword in l:
                collecting = True
                buf.append(line.split(":", 1)[-1].strip())
            elif collecting and any(l.startswith(k) for k in stops):
                break
            elif collecting:
                buf.append(line)
        return "\n".join(buf).strip() if buf else None

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
        title_lines, found = [], False
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
        t = _normalize_text(txt)
        m = re.search(r"(?i)^\s*Call:\s*(.+)$", t, re.MULTILINE)
        return m.group(1).strip() if m else None

    return {
        "title": extract_topic_title(text),
        "budget_per_project_eur": extract_budget(text),
        "total_budget_eur": extract_total_budget(text),
        "type_of_action": extract_type_of_action(text),
        "expected_outcome": get_section(
            "expected outcome:", ["scope:", "objective:", "expected impact:", "eligibility:", "budget"]
        ),
        "scope": get_section("scope:", ["objective:", "expected outcome:", "expected impact:", "budget"]),
        "call_name": extract_call_name_topic(text),
        "trl": (m := re.search(r"TRL\s*(\d+)[^\d]*(\d+)?", text, re.IGNORECASE))
        and (f"{m.group(1)}-{m.group(2)}" if m.group(2) else m.group(1)),
    }


def _extract_metadata_blocks(text: str) -> dict:
    lines = _normalize_text(text).splitlines()
    metadata_map: dict[str, dict] = {}
    current = {"opening_date": None, "deadline": None, "destination_or_strand": None}

    topic_pattern = re.compile(r"^(HORIZON-[A-Z0-9\-]+):")
    collecting = False

    for line in lines:
        lower = line.lower()

        if lower.startswith("opening:"):
            m = re.search(r"(\d{1,2} \w+ \d{4})", line)
            current["opening_date"] = m.group(1) if m else None
            current["deadline"] = None
            collecting = True

        elif collecting and lower.startswith("deadline"):
            m = re.search(r"(\d{1,2} \w+ \d{4})", line)
            current["deadline"] = m.group(1) if m else None

        elif collecting and lower.startswith("destination"):
            current["destination_or_strand"] = line.split(":", 1)[-1].strip()

        elif collecting:
            m = topic_pattern.match(line)
            if m:
                code = m.group(1)
                metadata_map[code] = current.copy()

    return metadata_map


# ---------- Public API ----------
def parse_horizon_pdf(pdf_bytes: bytes, source_filename: str, version_label: str) -> pd.DataFrame:
    """
    Parse a Horizon Europe work programme PDF into the shared base schema.
    Returns a DataFrame ready to be written to Excel by the caller.
    """
    raw_text = extract_pdf_text_from_bytes(pdf_bytes)

    topic_blocks = _extract_topic_blocks(raw_text)
    metadata_by_code = _extract_metadata_blocks(raw_text)

    enriched = []
    for topic in topic_blocks:
        fields = _extract_data_fields(topic)
        meta = metadata_by_code.get(topic["code"], {})
        enriched.append({**topic, **fields, **meta})

    # Build a DataFrame in (almost) base schema terms
    rows = []
    for t in enriched:
        rows.append(
            {
                # core
                "cluster": None,  # fill if you derive cluster elsewhere
                "code": t.get("code"),
                "title": t.get("title"),
                "opening_date": t.get("opening_date"),
                "deadline": t.get("deadline"),
                "destination_or_strand": t.get("destination_or_strand"),
                "type_of_action": t.get("type_of_action"),
                "trl": t.get("trl"),
                "budget_per_project_eur": t.get("budget_per_project_eur"),
                "total_budget_eur": t.get("total_budget_eur"),
                "call_name": t.get("call_name"),
                "expected_outcome": t.get("expected_outcome"),
                "scope": t.get("scope"),
                "full_text": t.get("full_text"),
            }
        )

    df_raw = pd.DataFrame(rows)

    # Normalise (adds programme/provenance/row_key, coerces types)
    df = ensure_base_schema(
        df_raw,
        programme="Horizon Europe",
        source_filename=source_filename,
        version_label=version_label,
    )

    # Final gentle coercions specific to Horizon (dates are day-first in EC docs)
    for dcol in ("opening_date", "deadline"):
        if dcol in df.columns:
            df[dcol] = pd.to_datetime(df[dcol], dayfirst=True, errors="coerce")

    return df
