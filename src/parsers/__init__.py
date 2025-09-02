# src/parsers/__init__.py
from . import horizon, erasmus

PARSERS = {
    "Horizon Europe": horizon.parse_horizon_pdf,
    "Erasmus+": erasmus.parse_erasmus_pdf,   # add others later
}

PROGRAMMES = list(PARSERS.keys())
