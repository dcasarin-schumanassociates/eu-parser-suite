# app.py — Streamlit Funding Dashboard (Schuman-branded, 2-file version)
from __future__ import annotations
import io
from typing import List
import pandas as pd
import streamlit as st

# Local helpers
from utils import (
    # branding
    inject_brand_css, brand_header,

    # load/canonicalise
    get_sheet_names, load_programme, canonicalise,

    # cleaning & text utils
    normalize_bullets, clean_footer, strip_and_collect_footnotes,
    nl_to_br, highlight_text,

    # charts
    build_singlebar_rows, gantt_singlebar_chart, prepare_dates_for_chart,
    shortlist_gantt_png,

    # shortlist / state
    ensure_shortlist_state, render_shortlist_row, merge_edits_into_df,
    guard_large_render, DISPLAY_COLS,

    # report
    generate_docx_report,DOCX_AVAILABLE,
)

def editable_text(field_label: str, field_name: str, raw_text: str, code: str, kw_list: list[str], height: int = 180):
    """
    Renders an expander with an editable text area for a field.
    - Initializes the widget state only ONCE (before instantiation).
    - Uses a unique key per (field, code).
    - Shows a highlighted preview of the edited text under the editor.
    """
    # Clean before first render
    clean_text, _ = strip_and_collect_footnotes(clean_footer(raw_text or ""))
    clean_text = normalize_bullets(clean_text)

    widget_key = f"edit_{field_name}_{code}"
    # IMPORTANT: initialize default BEFORE the widget is created
    if widget_key not in st.session_state:
        st.session_state[widget_key] = clean_text

    with st.expander(field_label):
        st.text_area(f"Edit {field_label.split(' ', 1)[-1]}", key=widget_key, height=height)
        # Preview with highlighting below the editor (optional)
        preview = nl_to_br(st.session_state[widget_key])
        st.markdown(highlight_text(preview, kw_list), unsafe_allow_html=True)


# -------------- Page Setup --------------
st.set_page_config(
    page_title="Schuman · Funding Dashboard",
    page_icon="assets/iconSA.png",  # path to your PNG file
    layout="wide"
)
inject_brand_css()
brand_header()

st.info(
    "📂 Please upload the latest parsed Excel file.\n\n"
    "➡️ Location hint:\n\n"
    "- **3.SA Practices** → Central Systems and Bid Management → 1. Central Systems\n\n"
    "👉 Look for *Central System Funding Compass Database*.\n"
)

upl = st.file_uploader("Upload Excel (.xlsx)", type=["xlsx"])
if not upl:
    st.stop()

# Detect sheets and allow override
sheets = get_sheet_names(upl.getvalue())
c1, c2 = st.columns(2)
with c1:
    hz_sheet = st.selectbox("Horizon Database", options=sheets, index=0)
with c2:
    er_sheet = st.selectbox("Erasmus Database", options=sheets, index=min(1, len(sheets)-1))

# Load each programme independently
df_h = load_programme(upl.getvalue(), hz_sheet, "Horizon Europe", _ver=1)
df_e = load_programme(upl.getvalue(), er_sheet, "Erasmus+",      _ver=1)

# ------- Build filter choices -------
all_df = pd.concat([df_h, df_e], ignore_index=True)  # programme already set
opening_years  = sorted([int(y) for y in all_df["opening_year"].dropna().unique()])
deadline_years = sorted([int(y) for y in all_df["deadline_year"].dropna().unique()])
type_opts      = sorted([t for t in all_df.get("type_of_action", pd.Series(dtype=object)).dropna().unique().tolist() if t!=""])

cluster_opts   = sorted([c for c in df_h.get("cluster", pd.Series(dtype=object)).dropna().unique().tolist() if c!=""])
dest_opts      = sorted([d for d in df_h.get("destination", pd.Series(dtype=object)).dropna().unique().tolist() if d!=""])
trl_opts       = sorted([str(int(x)) for x in df_h.get("trl", pd.Series(dtype=float)).dropna().unique() if pd.notna(x)])

ma_opts        = sorted([m for m in df_e.get("managing_authority", pd.Series(dtype=object)).dropna().unique().tolist() if m!=""])
ka_opts        = sorted([k for k in df_e.get("key_action", pd.Series(dtype=object)).dropna().unique().tolist() if k!=""])

# Budget slider (use combined range)
bud_series = pd.to_numeric(all_df.get("budget_per_project_eur"), errors="coerce").dropna()
if bud_series.empty:
    min_bud, max_bud = 0.0, 1_000_000.0
else:
    min_bud, max_bud = float(bud_series.min()), float(bud_series.max())
    if not (min_bud < max_bud):
        min_bud, max_bud = max(min_bud, 0.0), min_bud + 100000.0
rng = max_bud - min_bud
step = max(1e4, round(rng / 50, -3)) if rng else 10000.0

with st.form("filters", clear_on_submit=False):
    st.header("Filters")

    # ----------------
    # Row 1: opening, deadline, open calls, budget, type of action
    # ----------------
    col_oy, col_dy, col_open, col_bud, col_type = st.columns([1,1,1,2,2])
    
    with col_oy:
        open_year_sel = st.multiselect("Opening year(s)", opening_years)
    
    with col_dy:
        deadline_year_sel = st.multiselect("Deadline year(s)", deadline_years)
    
    with col_open:
        open_calls_only = st.checkbox(
            "Open calls only",
            value=False,
            help="Keep only entries whose final Deadline is strictly after today (Europe/Brussels)."
        )
   
    with col_bud:
        budget_range = st.slider(
            "Budget per project (EUR)",
            min_bud, max_bud,
            (min_bud, max_bud),
            step=step
        )
    
    with col_type:
        types_sel = st.multiselect("Type of Action", type_opts)
        
    # ----------------
    # Row 2: keywords + combine + title/code toggle
    # ----------------
    k1, k2, k3, kcomb, ktit, kmatch = st.columns([2,2,2,1,1.2,1.2])
    
    with k1:
        kw1 = st.text_input("Keyword 1")    
    with k2:
        kw2 = st.text_input("Keyword 2")    
    with k3:
        kw3 = st.text_input("Keyword 3")    
    with kmatch:
        match_case = st.checkbox("Match case", value=False)    
    with kcomb:
        kw_mode = st.radio("Combine", ["AND","OR"], index=1, horizontal=True)  # default OR    
    with ktit:
        title_code_only = st.checkbox("Title/Code only", value=False)          # default off

    # Horizon-specific
    st.subheader("Horizon-specific")
    h1, h2, h3 = st.columns(3)
    with h1:
        clusters_sel = st.multiselect("Cluster", cluster_opts)
    with h2:
        dests_sel = st.multiselect("Destination", dest_opts)
    with h3:
        trls_sel = st.multiselect("TRL", trl_opts)
    
    # Erasmus-specific
    st.subheader("Erasmus+-specific")
    e1, e2 = st.columns(2)
    with e1:
        ma_sel = st.multiselect("Managing Authority", ma_opts)
    with e2:
        ka_sel = st.multiselect("Key Action", ka_opts)
    
    applied = st.form_submit_button("Apply filters")

# Welcome/empty state
if "has_applied" not in st.session_state:
    st.session_state.has_applied = False
if applied:
    st.session_state.has_applied = True

# Persist criteria
if "crit35" not in st.session_state:
    st.session_state.crit35 = {}
if applied or not st.session_state.crit35:
    st.session_state.crit35 = dict(
        open_years=open_year_sel, deadline_years=deadline_year_sel,
        types=types_sel, kws=[kw1,kw2,kw3], kw_mode=kw_mode, title_code_only=title_code_only, match_case=match_case,
        budget_range=budget_range,
        clusters=clusters_sel, dests=dests_sel, trls=trls_sel,
        managing_authority=ma_sel, key_action=ka_sel,
        open_calls_only=open_calls_only
    )
crit = st.session_state.crit35

# Shortlist/session state
ensure_shortlist_state()

# Apply filters
def multi_keyword_filter(df: pd.DataFrame, terms, mode, title_code_only, match_case: bool = False):
    import re
    terms = [t.strip() for t in terms if t and str(t).strip()]
    if not terms:
        return df
    # Split positive and negative terms
    pos_terms = [t.lstrip("+") for t in terms if not t.startswith("-")]
    neg_terms = [t[1:] for t in terms if t.startswith("-")]
    # Pick haystack
    if title_code_only:
        hay = df["_search_title_raw"] if match_case and "_search_title_raw" in df else df.get("_search_title", "")
    else:
        hay = df["_search_all_raw"] if match_case and "_search_all_raw" in df else df.get("_search_all", "")
    # Normalize for case-insensitive search
    if not match_case:
        pos_terms = [t.lower() for t in pos_terms]
        neg_terms = [t.lower() for t in neg_terms]
        hay = hay.str.lower()
    # Build regex for positives
    if pos_terms:
        if mode.upper() == "AND":
            pos_pattern = "".join([f"(?=.*{re.escape(t)})" for t in pos_terms]) + ".*"
        else:  # OR
            pos_pattern = "(" + "|".join(re.escape(t) for t in pos_terms) + ")"
        mask = hay.str.contains(pos_pattern, regex=True, na=False)
    else:
        mask = pd.Series(True, index=df.index)
    # Apply negatives
    for nt in neg_terms:
        mask &= ~hay.str.contains(re.escape(nt), regex=True, na=False)
    return df[mask]



def apply_common_filters(df0: pd.DataFrame) -> pd.DataFrame:
    df = df0.copy()
    if crit["open_years"]:
        df = df[df["opening_year"].isin(crit["open_years"])]
    if crit["deadline_years"]:
        df = df[df["deadline_year"].isin(crit["deadline_years"])]
    if crit.get("open_calls_only"):
        today = pd.Timestamp.now(tz="Europe/Brussels").normalize().tz_localize(None)
        df = df[df["deadline"].notna() & (df["deadline"] > today)]
    if crit["types"]:
        df = df[df.get("type_of_action").isin(crit["types"])]
    lo, hi = crit["budget_range"]
    df = df[df.get("budget_per_project_eur").fillna(0).between(lo, hi)]
    df = multi_keyword_filter(
        df, crit["kws"],
        crit["kw_mode"],
        crit["title_code_only"],
        crit.get("match_case", False)
    )
    return df

def apply_horizon_filters(df0: pd.DataFrame) -> pd.DataFrame:
    df = apply_common_filters(df0)
    if crit["clusters"]: df = df[df.get("cluster").isin(crit["clusters"])]
    if crit["dests"]:    df = df[df.get("destination").isin(crit["dests"])]
    if crit["trls"]:
        df = df[df.get("trl").dropna().astype("Int64").astype(str).isin(crit["trls"])]
    return df

def apply_erasmus_filters(df0: pd.DataFrame) -> pd.DataFrame:
    df = apply_common_filters(df0)
    if crit["managing_authority"]: df = df[df.get("managing_authority").isin(crit["managing_authority"])]
    if crit["key_action"]:         df = df[df.get("key_action").isin(crit["key_action"])]
    return df

fh = apply_horizon_filters(df_h)
fe = apply_erasmus_filters(df_e)
st.caption(f"Rows after filters — Horizon: {len(fh)} | Erasmus: {len(fe)}")

# Early exit if not applied yet
if not st.session_state.has_applied:
    st.markdown(
        """
        ### 👋 Welcome
        Use the **Filters** above and click **Apply filters** to load matching calls.
        - Tip: start with *Opening/Deadline year* or turn on **Open calls only**.
        """
    )
    st.stop()

# ------------------------------ Tabs ------------------------------
tab_hz, tab_er, tab_tbl, tab_full, tab_short = st.tabs([
    "📅 Gantt — Horizon", "📅 Gantt — Erasmus", "📋 Tables", "📚 Full Data", "📝 Shortlist"
])

with tab_hz:
    st.subheader("Gantt — Horizon Europe (Opening → Deadline)")
    group_by_cluster = st.checkbox(
        "Group by cluster (render one Gantt per cluster)",
        value=False,
        help="When enabled, the Horizon chart is split into one dropdown per Cluster."
    )
    if not group_by_cluster:
        if guard_large_render(fh, "Horizon Gantt"):
            g_h = build_singlebar_rows(fh)
            if g_h.empty:
                st.info("No valid Horizon rows/dates.")
            else:
                st.markdown('<div class="scroll-container">', unsafe_allow_html=True)
                st.altair_chart(gantt_singlebar_chart(g_h, color_field="type_of_action"), use_container_width=True)
                st.markdown('</div>', unsafe_allow_html=True)
    else:
        if "cluster" not in fh.columns:
            st.warning("Column 'cluster' not available in Horizon dataset.")
        else:
            tmp = fh.copy()
            tmp["cluster"] = tmp["cluster"].fillna("— Unspecified —").replace({"": "— Unspecified —"})
            groups = list(tmp.groupby("cluster", dropna=False))
            groups.sort(key=lambda kv: len(kv[1]), reverse=True)
            st.caption(f"Clusters found: {len(groups)}")
            for clu_name, gdf in groups:
                if not guard_large_render(gdf, f"Horizon Gantt · {clu_name}"):
                    continue
                g_clu = build_singlebar_rows(gdf)
                with st.expander(f"Cluster: {clu_name}  ({len(g_clu)} calls)", expanded=False):
                    if g_clu.empty:
                        st.info("No valid rows/dates for this cluster after filters.")
                    else:
                        st.markdown('<div class="scroll-container">', unsafe_allow_html=True)
                        st.altair_chart(gantt_singlebar_chart(g_clu, color_field="type_of_action"), use_container_width=True)
                        st.markdown('</div>', unsafe_allow_html=True)

with tab_er:
    st.subheader("Gantt — Erasmus+ (Opening → Deadline)")
    if guard_large_render(fe, "Erasmus Gantt"):
        g_e = build_singlebar_rows(fe)
        if g_e.empty: st.info("No valid Erasmus rows/dates.")
        else:
            st.markdown('<div class="scroll-container">', unsafe_allow_html=True)
            st.altair_chart(gantt_singlebar_chart(g_e, color_field="type_of_action"), use_container_width=True)
            st.markdown('</div>', unsafe_allow_html=True)

with tab_tbl:
    st.subheader("Tables")

    def show_paged_df(df: pd.DataFrame, label: str, cols: list[str], page_size_default: int = 50):
        if df.empty:
            st.caption("— no rows —")
            return
        with st.expander(f"{label} — options", expanded=False):
            page_size = st.number_input("Rows per page", 10, 500, page_size_default, 10, key=f"ps_{label}")
            max_page = max(1, (len(df)-1)//page_size + 1)
            page = st.number_input("Page", 1, max_page, 1, 1, key=f"pg_{label}")
        start = (page-1)*page_size
        end = start + page_size
        st.dataframe(df[cols].iloc[start:end], use_container_width=True, hide_index=True)
        st.caption(f"Showing {min(end, len(df))}/{len(df)}")

    show_cols_h = [c for c in DISPLAY_COLS if c in fh.columns]
    show_cols_e = [c for c in DISPLAY_COLS if c in fe.columns]

    st.markdown(f"### Horizon Europe ({len(fh)})")
    show_paged_df(fh, "Horizon", show_cols_h)

    st.markdown(f"### Erasmus+ ({len(fe)})")
    show_paged_df(fe, "Erasmus", show_cols_e)

with tab_full:
    st.subheader("Full Data — Expand rows for details")

    # get active keywords from filters for highlighting
    kw_list = crit.get("kws", [])
                       
    def render_row(row, programme: str):
        c1, c2 = st.columns(2)
        with c1:
            if pd.notna(row.get("opening_date")):
                st.markdown(f"📅 **Opening:** {row['opening_date']:%d %b %Y}")
            if pd.notna(row.get("deadline")):
                st.markdown(f"⏳ **Deadline:** {row['deadline']:%d %b %Y}")
        with c2:
            if pd.notna(row.get("budget_per_project_eur")):
                st.markdown(f"💶 **Budget/Project:** {row['budget_per_project_eur']:,.0f} EUR")
            if pd.notna(row.get("total_budget_eur")):
                st.markdown(f"📦 **Total:** {row['total_budget_eur']:,.0f} EUR")
            if pd.notna(row.get("num_projects")):
                st.markdown(f"📊 **# Projects:** {int(row['num_projects'])}")
    
        meta_bits = [
            f"🏷️ **Programme:** {programme}",
            f"**Type of Action:** {row.get('type_of_action','-')}",
        ]
        if programme == "Horizon Europe":
            meta_bits += [
                f"**Cluster:** {row.get('cluster','-')}",
                f"**Destination:** {row.get('destination','-')}",
                f"**TRL:** {row.get('trl','-')}",
            ]
        else:
            meta_bits += [
                f"**Managing Authority:** {row.get('managing_authority','-')}",
                f"**Key Action:** {row.get('key_action','-')}",
            ]
        st.markdown(" | ".join(meta_bits))
    
        if row.get("expected_outcome"):
            with st.expander("🎯 Expected Outcome"):
                clean_text = nl_to_br(normalize_bullets(clean_footer(str(row.get("expected_outcome")))))
                st.markdown(highlight_text(clean_text, kw_list, match_case=crit.get("match_case", False)), unsafe_allow_html=True)
    
        if row.get("scope"):
            with st.expander("🧭 Scope"):
                clean_text = nl_to_br(normalize_bullets(clean_footer(str(row.get("scope")))))
                st.markdown(highlight_text(clean_text, kw_list, match_case=crit.get("match_case", False)), unsafe_allow_html=True)
    
        if row.get("full_text"):
            with st.expander("📖 Full Description"):
                clean_text = nl_to_br(normalize_bullets(clean_footer(str(row.get("full_text")))))
                st.markdown(highlight_text(clean_text, kw_list, match_case=crit.get("match_case", False)), unsafe_allow_html=True)
    
        st.caption(
            f"📂 Source: {row.get('source_filename','-')} "
            f"| Version: {row.get('version_label','-')} "
            f"| Parsed on: {row.get('parsed_on_utc','-')}"
        )


    # ----- HORIZON -----
    st.markdown(f"### Horizon Europe ({len(fh)})")
    if not guard_large_render(fh, "Full Data — Horizon"):
        st.stop()
    group_hz = st.checkbox(
        "Group Horizon by Cluster (dropdowns)",
        value=False,
        help="Show one expander per Cluster; inside each, expand rows for details."
    )
    if len(fh) == 0:
        st.caption("— no Horizon rows after filters —")
    else:
        if not group_hz:
            for i, r in fh.iterrows():
                label = f"{str(r.get('code') or '')} — {str(r.get('title') or '')}".strip(" —")
                code = str(r.get("code") or f"id-{i}")
                render_shortlist_row(
                    label, code,
                    lambda rr=r: render_row(rr, "Horizon Europe")
                )
        else:
            tmp = fh.copy()
            tmp["cluster"] = tmp.get("cluster").fillna("— Unspecified —").replace({"": "— Unspecified —"})
            groups = list(tmp.groupby("cluster", dropna=False))
            groups.sort(key=lambda kv: len(kv[1]), reverse=True)
            st.caption(f"Clusters found: {len(groups)}")
            for clu_name, gdf in groups:
                disp = str(clu_name)
                with st.expander(f"Cluster: {disp}  ({len(gdf)} calls)", expanded=False):
                    for i, r in gdf.iterrows():
                        label = f"{str(r.get('code') or '')} — {str(r.get('title') or '')}".strip(" —")
                        code = str(r.get("code") or f"id-{i}")
                        render_shortlist_row(
                            label, code,
                            lambda rr=r: render_row(rr, "Horizon Europe")
                        )

    # ----- ERASMUS -----
    st.markdown(f"### Erasmus+ ({len(fe)})")
    if not guard_large_render(fe, "Full Data — Erasmus"):
        st.stop()
    if len(fe) == 0:
        st.caption("— no Erasmus rows after filters —")
    else:
        for i, r in fe.iterrows():
            label = f"{str(r.get('code') or '')} — {str(r.get('title') or '')}".strip(" —")
            code = str(r.get("code") or f"id-{i}")
            render_shortlist_row(
                label, code,
                lambda rr=r: render_row(rr, "Erasmus+")
            )

with tab_short:
    st.subheader("Shortlist & Notes (DOCX)")

    # Build combined filtered set
    combined = []
    if len(fh): combined.append(fh)
    if len(fe): combined.append(fe)
    ff = pd.concat(combined, ignore_index=True) if combined else pd.DataFrame()

    shortlisted_codes = set(st.session_state.sel35)
    if ff.empty or not shortlisted_codes:
        st.info("No shortlisted calls yet. Use the **Shortlist** checkbox in the Full Data tab to add items.")
        st.stop()

    # closing_date_any safeguard
    if "closing_date_any" not in ff.columns:
        close_cols = [c for c in ["deadline","first_deadline","second_deadline"] if c in ff.columns]
        if close_cols:
            ff["closing_date_any"] = pd.to_datetime(ff[close_cols].stack(), errors="coerce").groupby(level=0).min()
        else:
            ff["closing_date_any"] = pd.NaT

    ff = ff.sort_values([c for c in ["closing_date_any","opening_date"] if c in ff.columns])
    selected_df = ff[ff["code"].astype(str).isin(shortlisted_codes)].copy()

    st.markdown("**Your shortlisted calls** (you can uncheck to remove):")
    for idx, r in selected_df.iterrows():
        code = str(r.get("code") or "")
        title = str(r.get("title") or "")
        label = f"{code} — {title}".strip(" —")
        checked = code in st.session_state.sel35
        new_val = st.checkbox(label or "(untitled)", value=checked, key=f"sel35_mirror_{code}_{idx}")
        if new_val and not checked: st.session_state.sel35.add(code)
        elif (not new_val) and checked: st.session_state.sel35.discard(code)

    # Recompute after toggles
    shortlisted_codes = set(st.session_state.sel35)
    selected_df = ff[ff["code"].astype(str).isin(shortlisted_codes)].copy()

    # In-app Gantt + PNG for report
    if guard_large_render(selected_df, "Shortlist Gantt"):
        st.markdown("### 📅 Gantt — Shortlisted Calls")
        gsrc = prepare_dates_for_chart(selected_df)
        color_by = st.selectbox(
            "Colour bars by",
            options=[opt for opt in ["type_of_action", "programme", "cluster"] if opt in gsrc.columns],
            index=0
        )
        g_all = build_singlebar_rows(gsrc)
        if g_all.empty:
            total = len(selected_df)
            with_dates = selected_df["deadline"].notna().sum()
            st.info(
                f"No valid date ranges for the current shortlist. "
                f"(Selected: {total}; with any deadline: {with_dates})"
            )
            st.session_state.shortlist_chart_png = None
        else:
            chart = gantt_singlebar_chart(g_all, color_field=color_by)
            st.markdown('<div class="scroll-container">', unsafe_allow_html=True)
            st.altair_chart(chart, use_container_width=True)
            st.markdown('</div>', unsafe_allow_html=True)
    
          
            # Save Altair chart to PNG (try Altair → fallback to matplotlib)
            try:
                import io
                buf = io.BytesIO()
                chart.save(buf, format="png")   # requires vl-convert-python
                st.session_state.shortlist_chart_png = buf.getvalue()
            except Exception as e:
                st.warning(f"Altair export failed ({e}), using matplotlib fallback.")
                st.session_state.shortlist_chart_png = shortlist_gantt_png(gsrc, color_by=color_by)
            

    # Notes + Title + DOCX
    if not selected_df.empty:
        st.markdown("---")

        # Merge edits from session state into the selected_df for export
        merge_edits_into_df(selected_df, st.session_state)

        for _, r in selected_df.iterrows():
            code = str(r.get("code") or "")
            default = st.session_state.notes35.get(code, "")
            st.session_state.notes35[code] = st.text_area(f"Notes — {code}", value=default, height=110, key=f"note35_{code}")

        colA, _colB = st.columns(2)
        with colA: title = st.text_input("Report title", value="Funding Report – Shortlist")

        if st.button("📄 Generate DOCX"):
            try:
                if DOCX_AVAILABLE:
                    export_df = selected_df.copy()
                    merge_edits_into_df(export_df, st.session_state)  # <-- apply text edits
                    data = generate_docx_report(
                        export_df,
                        st.session_state.notes35,
                        title=title,
                        shortlist_gantt_png=st.session_state.shortlist_chart_png
                    )
                    st.download_button("⬇️ Download .docx", data=data,
                                       file_name="funding_report.docx",
                                       mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
                else:
                    st.error("python-docx not installed in this environment.")
            except Exception as e:
                st.error(f"Failed to generate report: {e}")
    else:
        st.info("Your shortlist is now empty.")
