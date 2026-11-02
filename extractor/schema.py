"""JSON schema the model must fill in for every agreement.

Kept as one place so the app, the database and the prompt all agree on field names.
"""

PARTY = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Full legal name of the party (company or person)."},
        "role": {"type": "string", "description": "Role in the agreement, e.g. 'First Party', 'Client', 'Service Provider', 'Employer', 'Employee'."},
        "party_type": {"type": "string", "enum": ["company", "individual", "government", "other", "unknown"]},
        "address": {"type": ["string", "null"]},
        "registration_or_id_number": {"type": ["string", "null"], "description": "Company registration no., NIC or passport no. as written."},
        "representative_name": {"type": ["string", "null"], "description": "Person signing or acting for this party."},
        "representative_designation": {"type": ["string", "null"], "description": "Job title of that person, e.g. 'Director'."},
        "contact_email": {"type": ["string", "null"]},
    },
    "required": ["name", "role", "party_type"],
}

MONEY_ITEM = {
    "type": "object",
    "properties": {
        "description": {"type": "string", "description": "What the amount is for, e.g. 'Monthly service fee', 'Security deposit', 'Late payment penalty'."},
        "amount": {"type": ["number", "null"], "description": "Numeric amount only, no currency symbol or commas. Null if stated only as a formula/percentage."},
        "currency": {"type": ["string", "null"], "description": "ISO 4217 code if known (LKR, USD, EUR...), otherwise as written."},
        "frequency": {"type": "string", "enum": ["one_time", "monthly", "quarterly", "annually", "per_unit", "milestone", "percentage", "other", "unknown"]},
        "payer": {"type": ["string", "null"], "description": "Name of the party who pays."},
        "payee": {"type": ["string", "null"], "description": "Name of the party who receives."},
        "terms": {"type": ["string", "null"], "description": "Due dates, invoice terms, tax treatment or other conditions, in a short phrase."},
    },
    "required": ["description", "amount", "currency", "frequency"],
}

DATE_FIELD_DESC = "Date in YYYY-MM-DD format if a full date is stated; null if not stated. Do not guess."

AGREEMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "is_agreement": {"type": "boolean", "description": "False if the document is clearly not an agreement/contract."},
        "agreement_title": {"type": ["string", "null"], "description": "Title as written, e.g. 'Service Agreement'."},
        "agreement_type": {
            "type": "string",
            "enum": ["service", "employment", "non_disclosure", "lease_rental", "sales_purchase", "supply_vendor",
                     "partnership", "loan", "consultancy", "licensing", "memorandum_of_understanding", "other"],
        },
        "reference_number": {"type": ["string", "null"]},
        "signing_date": {"type": ["string", "null"], "description": "Date the agreement was made/signed. " + DATE_FIELD_DESC},
        "start_date": {"type": ["string", "null"], "description": "Effective/commencement date. " + DATE_FIELD_DESC},
        "end_date": {"type": ["string", "null"], "description": "Expiry/finishing date if explicitly stated. " + DATE_FIELD_DESC},
        "duration_text": {"type": ["string", "null"], "description": "Term as written, e.g. '12 months', 'two (2) years', 'until completion of the project'."},
        "duration_months": {"type": ["number", "null"], "description": "Term converted to months if it is a fixed period; null otherwise."},
        "renewal_terms": {"type": ["string", "null"]},
        "termination_notice": {"type": ["string", "null"], "description": "Notice period required to terminate, e.g. '30 days written notice'."},
        "purpose": {"type": ["string", "null"], "description": "One or two sentence plain-English summary of what the agreement is for."},
        "parties": {"type": "array", "items": PARTY},
        "financial_terms": {"type": "array", "items": MONEY_ITEM, "description": "Every fee, price, salary, deposit, penalty or other money amount. Empty list if none."},
        "total_contract_value": {"type": ["number", "null"], "description": "Total value only if stated or directly calculable from fixed amounts and term; else null."},
        "total_contract_value_currency": {"type": ["string", "null"]},
        "governing_law": {"type": ["string", "null"]},
        "key_obligations": {"type": "array", "items": {"type": "string"}, "description": "Up to 6 short bullet-style obligations."},
        "is_signed": {"type": ["boolean", "null"], "description": "True if signatures appear to be present, false if blank signature lines, null if unclear."},
        "evidence": {
            "type": "object",
            "description": "Short verbatim snippet (max ~25 words) from the document supporting each main field, for human review.",
            "properties": {
                "parties": {"type": ["string", "null"]},
                "signing_date": {"type": ["string", "null"]},
                "start_date": {"type": ["string", "null"]},
                "end_date": {"type": ["string", "null"]},
                "purpose": {"type": ["string", "null"]},
                "financial_terms": {"type": ["string", "null"]},
            },
        },
        "confidence": {
            "type": "object",
            "description": "high / medium / low confidence for each main field.",
            "properties": {k: {"type": "string", "enum": ["high", "medium", "low"]} for k in
                           ["parties", "signing_date", "start_date", "end_date", "purpose", "financial_terms"]},
        },
        "warnings": {"type": "array", "items": {"type": "string"},
                     "description": "Anything a reviewer should check: unreadable parts, conflicting dates, placeholders like [date], missing pages."},
    },
    "required": ["is_agreement", "agreement_type", "parties", "financial_terms", "warnings"],
}
