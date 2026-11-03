"""Streamlit app: upload agreements, extract details with Claude, review and search them.

Run:  streamlit run app.py
"""
from __future__ import annotations

import io
import json
import logging
import os
from datetime import date

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from extractor import ExtractionError, UnsupportedFileError, process_file, storage  # noqa: E402
from extractor.llm import DEFAULT_PROVIDER, api_key_configuration_error  # noqa: E402
from extractor.postprocess import postprocess  # noqa: E402
from extractor.schema import AGREEMENT_SCHEMA  # noqa: E402

st.set_page_config(page_title="Agreement Extractor", page_icon="📄", layout="wide")

STATUS_LABEL = {
    "active": "🟢 Active", "expiring_soon": "🟠 Expiring soon", "expired": "🔴 Expired",
    "not_started": "🔵 Not started", "open_ended_or_unknown": "⚪ No end date",
    "not_an_agreement": "⚫ Not an agreement",
}
AGREEMENT_TYPES = AGREEMENT_SCHEMA["properties"]["agreement_type"]["enum"]
FILE_TYPES = ["pdf", "docx", "txt", "jpg", "jpeg", "png", "webp", "tif", "tiff", "bmp"]
logger = logging.getLogger(__name__)


# ---------------- access control ----------------
def check_password() -> bool:
    pw = os.getenv("APP_PASSWORD")
    if not pw:
        return True
    if st.session_state.get("authed"):
        return True
    st.title("📄 Agreement Extractor")
    entered = st.text_input("Password", type="password")
    if entered and entered == pw:
        st.session_state.authed = True
        st.rerun()
    elif entered:
        st.error("Wrong password.")
    return False


if not check_password():
    st.stop()


# ---------------- helpers ----------------
def money(v, cur):
    if v is None:
        return "—"
    return f"{cur or ''} {v:,.2f}".strip()


def show_record(data: dict, key: str):
    der = data.get("derived", {})
    if not data.get("is_agreement", True):
        st.error("This document doesn't look like an agreement.")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Start date", der.get("effective_start") or "—",
              help="Taken from the signing date" if der.get("start_date_assumed_from_signing_date") else None)
    c2.metric("End date", der.get("effective_end") or "—",
              help="Calculated from the term" if der.get("end_date_calculated") else None)
    c3.metric("Total value", money(data.get("total_contract_value"), der.get("primary_currency")))
    c4.metric("Status", STATUS_LABEL.get(der.get("status"), der.get("status") or "—"))

    st.markdown(f"**{data.get('agreement_title') or 'Untitled agreement'}** · "
                f"{(data.get('agreement_type') or '').replace('_', ' ').title()}"
                + (f" · Ref {data['reference_number']}" if data.get("reference_number") else ""))
    st.markdown("**Brief summary**")
    st.write(data.get("purpose") or "No summary could be extracted from this document.")

    meta = {
        "Signing date": data.get("signing_date"), "Term": data.get("duration_text"),
        "Renewal": data.get("renewal_terms"), "Termination notice": data.get("termination_notice"),
        "Governing law": data.get("governing_law"),
        "Signed": {True: "Yes", False: "No (blank signature lines)", None: "Unclear"}.get(data.get("is_signed")),
    }
    st.table(pd.DataFrame([(k, v or "—") for k, v in meta.items()], columns=["Field", "Value"]).set_index("Field"))

    st.markdown("**Parties**")
    if data.get("parties"):
        st.dataframe(pd.DataFrame(data["parties"]), hide_index=True, width="stretch")
    else:
        st.caption("No parties found.")

    st.markdown("**Money / financial terms**")
    if data.get("financial_terms"):
        st.dataframe(pd.DataFrame(data["financial_terms"]), hide_index=True, width="stretch")
    else:
        st.caption("No money-related terms found.")

    if data.get("key_obligations"):
        with st.expander("Key obligations"):
            for o in data["key_obligations"]:
                st.markdown(f"- {o}")
    if data.get("evidence"):
        with st.expander("Evidence from the document"):
            for k, v in data["evidence"].items():
                if v:
                    conf = (data.get("confidence") or {}).get(k, "")
                    st.markdown(f"**{k.replace('_', ' ').title()}** ({conf}): “{v}”")
    review_items = list(data.get("warnings") or [])
    review_items.extend(
        f"Missing or unclear: {field.replace('_', ' ')}."
        for field in der.get("missing_fields", [])
    )
    review_items.extend(
        f"Low confidence: {field.replace('_', ' ')}."
        for field in der.get("low_confidence_fields", [])
    )
    review_items = list(dict.fromkeys(review_items))
    st.markdown("**Potential issues to verify**")
    if review_items:
        st.warning("\n".join(f"- {item}" for item in review_items))
    else:
        st.info("No extraction issues were flagged. Check these details against the original; this is not a legal review.")
    st.download_button("Download JSON", json.dumps(data, indent=2, ensure_ascii=False),
                       file_name="agreement.json", mime="application/json", key=f"dl_{key}")


def review_form(row: dict):
    data = json.loads(row["data_json"])
    with st.form(f"edit_{row['id']}"):
        c1, c2 = st.columns(2)
        title = c1.text_input("Title", data.get("agreement_title") or "")
        a_type = c2.selectbox("Type", AGREEMENT_TYPES,
                              index=AGREEMENT_TYPES.index(data.get("agreement_type")) if data.get("agreement_type") in AGREEMENT_TYPES else len(AGREEMENT_TYPES) - 1)
        c1, c2, c3 = st.columns(3)
        signing = c1.text_input("Signing date (YYYY-MM-DD)", data.get("signing_date") or "")
        start = c2.text_input("Start date (YYYY-MM-DD)", data.get("start_date") or "")
        end = c3.text_input("End date (YYYY-MM-DD)", data.get("end_date") or data["derived"].get("effective_end") or "")
        purpose = st.text_area("Purpose", data.get("purpose") or "")
        c1, c2 = st.columns(2)
        total = c1.number_input("Total contract value", value=float(data.get("total_contract_value") or 0), min_value=0.0, step=1000.0)
        cur = c2.text_input("Currency", data.get("total_contract_value_currency") or data["derived"].get("primary_currency") or "")
        st.markdown("Parties")
        parties = st.data_editor(pd.DataFrame(data.get("parties") or [{"name": "", "role": "", "party_type": "unknown"}]),
                                 num_rows="dynamic", width="stretch", key=f"p_{row['id']}")
        st.markdown("Financial terms")
        fin = st.data_editor(pd.DataFrame(data.get("financial_terms") or [{"description": "", "amount": None, "currency": "", "frequency": "unknown"}]),
                             num_rows="dynamic", width="stretch", key=f"f_{row['id']}")
        if st.form_submit_button("Save corrections", type="primary"):
            data.update({
                "agreement_title": title, "agreement_type": a_type, "signing_date": signing or None,
                "start_date": start or None, "end_date": end or None, "purpose": purpose,
                "total_contract_value": total or None, "total_contract_value_currency": cur or None,
                "parties": [p for p in parties.astype(object).where(pd.notna(parties), None).to_dict("records") if p.get("name")],
                "financial_terms": [m for m in fin.astype(object).where(pd.notna(fin), None).to_dict("records") if m.get("description")],
                "warnings": [],  # reviewer has checked them
            })
            data.pop("derived", None)
            cleaned = postprocess(data)
            cleaned["derived"]["reviewed_by_human"] = True
            storage.update_reviewed(row["id"], cleaned)
            st.success("Saved.")
            st.rerun()


# ---------------- pages ----------------
st.sidebar.title("📄 Agreement Extractor")
page = st.sidebar.radio("Go to", ["Upload", "All agreements", "Review"])
user = st.sidebar.text_input("Your name (for the record)", key="user")
provider = os.getenv("LLM_PROVIDER", DEFAULT_PROVIDER).strip().lower()
key_variable = "GOOGLE_API_KEY" if provider == "google" else "ANTHROPIC_API_KEY"
provider_label = "Google Gemini" if provider == "google" else "Anthropic Claude" if provider == "anthropic" else provider
st.sidebar.caption(f"Extraction provider: {provider_label}")
api_key_error = api_key_configuration_error(provider, os.getenv(key_variable))
if api_key_error:
    st.sidebar.error(api_key_error)

if page == "Upload":
    st.header("Upload agreements")
    st.caption("PDF (typed or scanned), Word (.docx), text or photos of pages. Several files at once is fine.")
    files = st.file_uploader("Agreement files", type=FILE_TYPES, accept_multiple_files=True)
    reprocess = st.checkbox("Re-extract files that were uploaded before")
    if files and st.button("Extract details", type="primary"):
        for f in files:
            with st.status(f"Reading {f.name}…", expanded=True) as s:
                try:
                    res = process_file(f.getvalue(), f.name, uploaded_by=user or None, reprocess=reprocess)
                except (UnsupportedFileError, ExtractionError) as e:
                    s.update(label=f"{f.name}: failed — {str(e)[:200]}", state="error", expanded=True)
                    st.error(str(e))
                    continue
                except Exception as e:
                    logger.exception("Unexpected error processing uploaded file %s", f.name)
                    s.update(
                        label=f"{f.name}: failed — {type(e).__name__}: {str(e)[:200]}",
                        state="error",
                        expanded=True,
                    )
                    st.error(f"Could not process {f.name} ({type(e).__name__}): {e}")
                    continue
                label = f"{f.name}: " + ("already in the system" if res.duplicate else "done")
                s.update(label=label, state="complete")
                if res.duplicate:
                    st.info("This exact file was uploaded before; showing the saved result.")
                show_record(res.data, key=f"{res.id}_{f.name}")

elif page == "All agreements":
    st.header("All agreements")
    rows = storage.list_all()
    if not rows:
        st.info("Nothing uploaded yet.")
        st.stop()
    df = pd.DataFrame(rows)
    df["status_label"] = df["status"].map(STATUS_LABEL)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Agreements", len(df))
    c2.metric("Active", int((df["status"] == "active").sum()))
    c3.metric("Expiring ≤ 60 days", int((df["status"] == "expiring_soon").sum()))
    c4.metric("Need review", int(((df["needs_review"] == 1) & (df["reviewed"] == 0)).sum()))

    c1, c2, c3 = st.columns([2, 1, 1])
    q = c1.text_input("Search title, party, purpose or file name")
    status_f = c2.multiselect("Status", list(STATUS_LABEL), format_func=STATUS_LABEL.get)
    type_f = c3.multiselect("Type", sorted(df["agreement_type"].dropna().unique()))
    view = df
    if q:
        mask = pd.Series(False, index=df.index)
        for col in ["title", "parties", "purpose", "filename", "reference_number"]:
            mask |= df[col].fillna("").str.contains(q, case=False, regex=False)
        view = view[mask]
    if status_f:
        view = view[view["status"].isin(status_f)]
    if type_f:
        view = view[view["agreement_type"].isin(type_f)]

    cols = ["id", "title", "agreement_type", "parties", "start_date", "end_date", "total_value", "currency",
            "status_label", "reviewed", "filename", "uploaded_at"]
    st.dataframe(view[cols], hide_index=True, width="stretch",
                 column_config={"total_value": st.column_config.NumberColumn(format="%.2f"),
                                "reviewed": st.column_config.CheckboxColumn()})

    export = view[cols[:-1] + ["purpose"]].rename(columns={"status_label": "status"})
    buf = io.BytesIO()
    export.to_excel(buf, index=False, sheet_name="Agreements")
    c1, c2 = st.columns(2)
    c1.download_button("Export to Excel", buf.getvalue(), f"agreements_{date.today()}.xlsx")
    c2.download_button("Export to CSV", export.to_csv(index=False), f"agreements_{date.today()}.csv")

    sel = st.selectbox("Open an agreement", view["id"], format_func=lambda i: f"#{i} – {df.set_index('id').loc[i, 'title'] or df.set_index('id').loc[i, 'filename']}")
    if sel:
        row = storage.get(int(sel))
        show_record(json.loads(row["data_json"]), key=f"all_{sel}")
        if st.button("Delete this record", key=f"del_{sel}"):
            storage.delete(int(sel))
            st.rerun()

elif page == "Review":
    st.header("Review and correct")
    st.caption("Records with warnings, missing fields or low confidence. Corrections are saved and marked as reviewed.")
    rows = [r for r in storage.list_all() if r["needs_review"] and not r["reviewed"]]
    show_all = st.checkbox("Show all records, not just ones needing review")
    if show_all:
        rows = storage.list_all()
    if not rows:
        st.success("Nothing waiting for review.")
        st.stop()
    sel = st.selectbox("Agreement", [r["id"] for r in rows],
                       format_func=lambda i: next(f"#{r['id']} – {r['title'] or r['filename']}" for r in rows if r["id"] == i))
    row = storage.get(int(sel))
    data = json.loads(row["data_json"])
    if data.get("warnings"):
        st.warning("\n".join(f"- {w}" for w in data["warnings"]))
    if row.get("stored_file") and os.path.exists(row["stored_file"]):
        with open(row["stored_file"], "rb") as fh:
            st.download_button("Open original file", fh.read(), file_name=row["filename"])
    review_form(row)
