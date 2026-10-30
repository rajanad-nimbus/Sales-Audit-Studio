"""Standard Sales Audit totals installed with the system.

Each entry is a Raw header-level total: it filters canonical transactions by source, type and tender, which is all the
calculation supports today. Freight, tax and paid-in/paid-out totals need line, tax or till-movement data and are not here.
"""
STANDARD_TOTALS = [
    {"name": "OVRSHT_S", "description": "Store-level over/short", "category": "Over/Short", "over_short_group": "Accountable For",
     "over_short_operator": "+", "aggregation": "Sum", "source_system": "POS", "transaction_types": ["Sale", "Return"],
     "required_by_system": True, "display_group": 1},
    {"name": "SALES_POS", "description": "Store sales", "category": "Sales", "aggregation": "Sum", "source_system": "POS",
     "transaction_types": ["Sale"], "display_group": 10},
    {"name": "RETURNS_POS", "description": "Store returns", "category": "Returns", "aggregation": "Sum", "source_system": "POS",
     "transaction_types": ["Return"], "display_group": 11},
    {"name": "SALES_ECOM", "description": "E-commerce sales", "category": "Sales", "aggregation": "Sum", "source_system": "Shopify",
     "transaction_types": ["Order"], "display_group": 12},
    {"name": "RETURNS_ECOM", "description": "E-commerce returns", "category": "Returns", "aggregation": "Sum", "source_system": "Shopify",
     "transaction_types": ["Refund"], "display_group": 13},
    *[{"name": f"TEND_{t.upper()}", "description": f"{t} tender", "category": "Tender", "aggregation": "Sum", "source_system": "POS",
       "transaction_types": ["Sale", "Return"], "tender_type": t, "display_group": 20 + i}
      for i, t in enumerate(["Cash", "Card", "Voucher", "GiftCard", "StoreCredit"])],
]

# Reported tax already recorded on stored transaction detail (it does not audit the tax calculation).
TAX_TOTALS = [
    {"name": "TAX_POS", "description": "Tax on store transactions", "category": "Tax", "aggregation": "Sum", "measure": "Tax",
     "source_system": "POS", "transaction_types": ["Sale", "Return"], "display_group": 30},
    {"name": "TAX_ECOM", "description": "Tax on e-commerce orders and refunds", "category": "Tax", "aggregation": "Sum", "measure": "Tax",
     "source_system": "Shopify", "transaction_types": ["Order", "Refund"], "display_group": 31},
]
