from __future__ import annotations
import pandas as pd
from src.core.schema import ensure_base_schema, BASE_COLUMNS

def test_ensure_base_schema_minimal():
    df = pd.DataFrame([{"code": "HORIZON-CL5-2025-01-01", "title": "Dummy"}])
    out = ensure_base_schema(df, programme="Horizon Europe", source_filename="x.pdf", version_label="Draft v1")
    for col in BASE_COLUMNS:
        assert col in out.columns
    assert len(out) == 1
