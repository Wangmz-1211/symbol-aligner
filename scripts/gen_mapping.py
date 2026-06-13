#!/usr/bin/env python3
"""Generate the compound-word finance mapping for symbol-aligner.

Both legacy and canonical identifiers use fully-spelled English words (camelCase).

Non-linearity comes from context-sensitive noun mappings:
  • verb+Noun  compounds  →  NOUN_ACTION_MAP  (operational / service view)
  • noun+Attr  compounds  →  NOUN_DATA_MAP    (domain model / data view)

The same legacy noun maps to different canonicals depending on context:
  "account" in setAccount  → Portfolio  (something you operate on as a service)
  "account" in accountStatus → ledgerState  (a domain entity with attributes)

This means knowing "account→Portfolio" from verb-context tells you nothing
about what "accountLimit" will become — that's the non-linearity.
"""

from __future__ import annotations
import json
import pathlib

# ── Verb pool ────────────────────────────────────────────────────────────────
# Legacy verb prefix → canonical verb prefix.
# Chosen to be plausible synonyms but NOT the obvious first choice,
# reflecting a real shift in engineering vocabulary between two codebases.

VERB_MAP: dict[str, str] = {
    "get":      "fetch",
    "set":      "configure",
    "compute":  "evaluate",
    "check":    "validate",
    "send":     "transmit",
    "receive":  "accept",
    "load":     "import",
    "save":     "persist",
    "create":   "allocate",
    "update":   "modify",
    "delete":   "purge",
    "find":     "locate",
    "list":     "enumerate",
    "process":  "execute",
    "apply":    "invoke",
}

# ── Noun pool — context-sensitive ────────────────────────────────────────────
# The same business noun maps to DIFFERENT canonical terms depending on
# whether it is the direct object of an action (verb+Noun) or owns
# an attribute (noun+Attr).  This breaks component-wise predictability.

# Used in verb+Noun compounds: noun is the target of a service operation.
NOUN_ACTION_MAP: dict[str, str] = {
    "User":     "Client",
    "Account":  "Portfolio",
    "Order":    "Transaction",
    "Payment":  "Settlement",
    "Stock":    "Equity",
    "Market":   "Exchange",
    "Fund":     "Capital",
    "Loan":     "Facility",
    "Rate":     "Spread",
    "Asset":    "Holding",
    "Trade":    "Deal",
    "Risk":     "Exposure",
    "Report":   "Disclosure",
    "Tax":      "Levy",
    "Audit":    "Review",
    "Invoice":  "Billing",
    "Budget":   "Allocation",
    "Contract": "Mandate",
    "Balance":  "Position",
    "Record":   "Ledger",
}

# Used in noun+Attr compounds: noun is a domain-model entity owning data.
NOUN_DATA_MAP: dict[str, str] = {
    "user":     "principal",
    "account":  "ledger",
    "order":    "instruction",
    "payment":  "obligation",
    "stock":    "security",
    "market":   "venue",
    "fund":     "portfolio",
    "loan":     "credit",
    "rate":     "tenor",
    "asset":    "instrument",
    "trade":    "position",
    "risk":     "constraint",
    "report":   "statement",
    "tax":      "withholding",
    "audit":    "reconciliation",
    "invoice":  "receivable",
    "budget":   "provision",
    "contract": "agreement",
    "balance":  "equity",
    "record":   "entry",
}

# ── Attribute pool ────────────────────────────────────────────────────────────
# Noun+Attr suffix: legacy attribute → canonical attribute.
ATTR_MAP: dict[str, str] = {
    "Amount":   "Value",
    "Date":     "Timestamp",
    "Status":   "State",
    "Type":     "Category",
    "Name":     "Label",
    "Count":    "Quantity",
    "Limit":    "Threshold",
    "Info":     "Metadata",
    "Summary":  "Overview",
    "Total":    "Aggregate",
}


# ── Generators ────────────────────────────────────────────────────────────────

def gen_verb_noun() -> dict[str, str]:
    """15 verbs × 20 nouns = 300 pairs: verb+Noun → canonicalVerb+CanonicalNoun"""
    out: dict[str, str] = {}
    for v_leg, v_can in VERB_MAP.items():
        for n_leg, n_can in NOUN_ACTION_MAP.items():
            out[v_leg + n_leg] = v_can + n_can
    return out


def gen_noun_attr() -> dict[str, str]:
    """20 nouns × 10 attrs = 200 pairs: noun+Attr → canonicalNoun+CanonicalAttr"""
    out: dict[str, str] = {}
    for n_leg, n_can in NOUN_DATA_MAP.items():
        for a_leg, a_can in ATTR_MAP.items():
            out[n_leg + a_leg] = n_can + a_can
    return out


# ── Validation ────────────────────────────────────────────────────────────────

def validate(mapping: dict[str, str]) -> None:
    """Enforce 1-to-1: raise ValueError on any duplicate canonical value."""
    seen: dict[str, str] = {}
    for k, v in mapping.items():
        if v in seen:
            raise ValueError(
                f"Duplicate canonical '{v}': both '{seen[v]}' and '{k}' map to it"
            )
        seen[v] = k


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    mapping: dict[str, str] = {}
    mapping.update(gen_verb_noun())   # 300 entries
    mapping.update(gen_noun_attr())   # 200 entries

    validate(mapping)

    out = pathlib.Path(__file__).parent.parent / "mappings" / "example.json"
    out.write_text(json.dumps(mapping, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Written {len(mapping)} entries → {out}")


if __name__ == "__main__":
    main()
