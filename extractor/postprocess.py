"""Clean and check what the model returned, so stored data is consistent and reviewers see problems."""
from __future__ import annotations

import re
from datetime import date, datetime

from dateutil.relativedelta import relativedelta

DATE_FIELDS = ("signing_date", "start_date", "end_date")
PLACEHOLDER = re.compile(r"^\s*(\[.*\]|_{3,}|\.{3,}|n/?a|tbd|null|none|-)\s*$", re.I)
CURRENCY_ALIASES = {"RS": "LKR", "RS.": "LKR", "SLR": "LKR", "RUPEES": "LKR", "$": "USD", "US$": "USD",
                    "€": "EUR", "£": "GBP", "₹": "INR", "INR.": "INR"}
EXPIRY_WARNING_DAYS = 60


def _clean_str(v):
    if isinstance(v, float) and v != v:  # NaN from tables/spreadsheets
        return None
    if isinstance(v, str):
        v = v.strip()
        return None if not v or PLACEHOLDER.match(v) else v
    return v


def parse_date(v) -> date | None:
    v = _clean_str(v)
    if not v:
        return None
    try:
        return datetime.strptime(v[:10], "%Y-%m-%d").date()
    except ValueError:
        pass
    try:
        from dateutil import parser
        return parser.parse(v, dayfirst=True).date()
    except (ValueError, OverflowError):
        return None


def norm_currency(c):
    c = _clean_str(c)
    if not c or not isinstance(c, str):
        return None
    c = c.upper()
    return CURRENCY_ALIASES.get(c, c)


def to_number(v):
    if v is None or isinstance(v, (int, float)):
        return v
    s = re.sub(r"[^\d.\-]", "", str(v))
    try:
        return float(s) if s else None
    except ValueError:
        return None


def postprocess(raw: dict, today: date | None = None) -> dict:
    """Return a cleaned copy of the model output plus derived fields under 'derived' and extra 'warnings'."""
    today = today or date.today()
    d = {k: (_clean_str(v) if isinstance(v, str) else v) for k, v in raw.items()}
    warnings = list(d.get("warnings") or [])
    derived: dict = {}

    # Dates -> ISO strings
    parsed: dict[str, date | None] = {}
    for f in DATE_FIELDS:
        parsed[f] = parse_date(d.get(f))
        if d.get(f) and parsed[f] is None:
            warnings.append(f"Could not read {f.replace('_', ' ')} '{d.get(f)}'.")
        d[f] = parsed[f].isoformat() if parsed[f] else None

    # Start date falls back to signing date ("effective from the date of this agreement" is common)
    start = parsed["start_date"] or parsed["signing_date"]
    if not parsed["start_date"] and parsed["signing_date"]:
        derived["start_date_assumed_from_signing_date"] = True

    # Calculate end date from a fixed term when not written explicitly
    end = parsed["end_date"]
    months = to_number(d.get("duration_months"))
    if end is None and start and months:
        end = start + relativedelta(months=int(months)) - relativedelta(days=1) if float(months).is_integer() \
            else start + relativedelta(days=round(months * 30.44))
        derived["end_date_calculated"] = True
    derived["effective_start"] = start.isoformat() if start else None
    derived["effective_end"] = end.isoformat() if end else None

    if start and end and end < start:
        warnings.append("End date is before the start date — please check.")

    # Status
    if not d.get("is_agreement", True):
        status = "not_an_agreement"
    elif end is None:
        status = "open_ended_or_unknown"
    elif end < today:
        status = "expired"
    elif (end - today).days <= EXPIRY_WARNING_DAYS:
        status = "expiring_soon"
    elif start and start > today:
        status = "not_started"
    else:
        status = "active"
    derived["status"] = status
    derived["days_to_expiry"] = (end - today).days if end else None

    # Parties
    parties = []
    for p in d.get("parties") or []:
        p = {k: _clean_str(v) for k, v in p.items()}
        if p.get("name"):
            parties.append(p)
    d["parties"] = parties
    if len(parties) < 2 and d.get("is_agreement", True):
        warnings.append(f"Only {len(parties)} party found; agreements normally have at least two.")

    # Money
    money = []
    for m in d.get("financial_terms") or []:
        m = {k: _clean_str(v) for k, v in m.items()}
        m["amount"] = to_number(m.get("amount"))
        m["currency"] = norm_currency(m.get("currency"))
        if m.get("amount") is not None and m["amount"] < 0:
            warnings.append(f"Negative amount for '{m.get('description')}'.")
        money.append(m)
    d["financial_terms"] = money
    d["total_contract_value"] = to_number(d.get("total_contract_value"))
    d["total_contract_value_currency"] = norm_currency(d.get("total_contract_value_currency"))
    currencies = {m["currency"] for m in money if m.get("currency")}
    if len(currencies) > 1:
        warnings.append(f"More than one currency used: {', '.join(sorted(currencies))}.")
    derived["has_financial_terms"] = bool(money)
    derived["primary_currency"] = d["total_contract_value_currency"] or (sorted(currencies)[0] if currencies else None)

    # Fields a reviewer must look at
    missing = [f for f in ("agreement_title", "purpose") if not d.get(f)]
    if not start:
        missing.append("start_date")
    derived["missing_fields"] = missing
    low = [k for k, v in (d.get("confidence") or {}).items() if v == "low"]
    derived["low_confidence_fields"] = low
    derived["needs_review"] = bool(warnings or missing or low)

    d["warnings"] = list(dict.fromkeys(warnings))  # dedupe, keep order
    d["derived"] = derived
    return d
