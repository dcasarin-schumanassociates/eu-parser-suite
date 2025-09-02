# EU Calls Parser (Modular)

A minimal Streamlit app that lets you pick a **programme** (Horizon Europe, Erasmus, etc.),
upload a PDF, and parse it into a tabular preview (downloadable as Excel).

## Quick start
1. `pip install -r requirements.txt`
2. `streamlit run main.py`

## Add a new programme
- Create `parsers/<programme>.py` with a `parse_pdf(file)` function that returns a pandas DataFrame.
- Register it in `main.py` under `PARSERS`.
