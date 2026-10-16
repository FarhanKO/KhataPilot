"""Ledger categories from the words owners actually use (English, Banglish, Bangla)."""

from __future__ import annotations

import re

KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("Rice, lentils & flour", ("rice", "chal", "chaal", "dal", "daal", "lentil", "atta", "aata", "flour", "moida",
                               "চাল", "ডাল", "আটা", "ময়দা")),
    ("Oil & sugar", ("oil", "tel", "soybean", "sugar", "chini", "তেল", "চিনি")),
    ("Dairy, eggs & bread", ("milk", "dudh", "egg", "eggs", "dim", "bread", "pauruti", "butter", "দুধ", "ডিম", "পাউরুটি")),
    ("Toiletries & snacks", ("soap", "saban", "shampoo", "biscuit", "biscuits", "chips", "noodles", "toothpaste",
                             "সাবান", "বিস্কুট")),
    ("Spices & vegetables", ("onion", "peyaj", "piaj", "garlic", "rosun", "potato", "alu", "aloo", "chili", "morich",
                             "spice", "masala", "পেঁয়াজ", "আলু", "মরিচ")),
    ("Rent", ("rent", "bhara", "vara", "ভাড়া")),
    ("Utilities", ("electricity", "current bill", "desco", "dpdc", "gas bill", "water bill", "internet", "বিদ্যুৎ")),
    ("Wages", ("salary", "beton", "wage", "wages", "helper", "বেতন")),
    ("Transport & misc", ("van", "rickshaw", "transport", "bhara gari", "packaging", "polythene", "tea", "cha")),
]

DEFAULT_BY_KIND = {
    "sale": "Sales",
    "baki_sale": "Sales on credit (baki)",
    "baki_repayment": "Baki collected",
    "purchase": "Stock purchases",
    "supplier_payment": "Stock purchases",
    "expense": "Transport & misc",
    "owner_drawing": "Owner withdrawal",
    "other_in": "Other income",
    "other_out": "Payments",
}


def categorize(kind: str, *texts: str | None) -> str:
    """Sales stay "Sales"; purchases and expenses get a category from the item words."""
    if kind in ("sale", "baki_sale", "baki_repayment", "owner_drawing"):
        return DEFAULT_BY_KIND[kind]
    words = " ".join(t for t in texts if t).lower()
    for category, keys in KEYWORDS:
        if any(re.search(rf"(?<![\wঀ-৿]){re.escape(k)}(?![\wঀ-৿])", words) for k in keys):
            return category
    return DEFAULT_BY_KIND.get(kind, "Other")
