from __future__ import annotations
import hashlib
from datetime import datetime
import pandas as pd

BASE_COLUMNS = [
    "programme", "cluster", "code", "title",
    "opening_date", "deadline",
    "budget_per_project_eur", "total_budget_eur",
    "type_of_action", "trl", "destination_or_strand",
    "call_name", "expected_outcome", "scope", "full_text",
    "source_filename", "version_label", "parsed_on_utc",
    "row_key",
]

def _row_key(programme: str, cluster: str, code: str, title: str) -> str:
    basis = f"{programme}|{cluster}|{code or ''}|{(title or '')[:60]}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()

def ensure_base_schema(df: pd.DataFrame, programme: str, source_filename: str, version_label: str) -> pd.DataFrame:
    out = df.copy()

    # Ensure all base columns exist
    for col in BASE_COLUMNS:
        if col not in out.columns:
            out[col] = None

    # Provenance
    out["programme"] = out["programme"].fillna(programme)
    out["source_filename"] = out["source_filename"].fillna(source_filename)
    out["version_label"] = out["version_label"].fillna(version_label)
    out["parsed_on_utc"] = out["parsed_on_utc"].fillna(datetime.utcnow().isoformat(timespec="seconds"))

    # row_key
    needs_key = out["row_key"].isna() | (out["row_key"] == "")
    if needs_key.any():
        out.loc[needs_key, "row_key"] = [
            _row_key(
                programme=programme,
                cluster=str(row.get("cluster") or ""),
                code=str(row.get("code") or ""),
                title=str(row.get("title") or ""),
            )
            for _, row in out.loc[needs_key].iterrows()
        ]

    # Types
    for dcol in ("opening_date", "deadline"):
        out[dcol] = pd.to_datetime(out[dcol], errors="coerce")

    for ncol in ("budget_per_project_eur", "total_budget_eur", "trl"):
        if ncol in out.columns:
            out[ncol] = pd.to_numeric(out[ncol], errors="coerce")

    # Column order
    out = out[BASE_COLUMNS]
    return out
