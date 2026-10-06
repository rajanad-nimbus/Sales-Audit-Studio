"""Bindings from the existing enterprise ontology to Nimbus runtime storage.

This is an adapter, not a second ontology. Missing enterprise definitions remain
visible gaps; SQLAlchemy metadata describes only what Nimbus actually stores.
"""
from models import Base

ENTITY_ALIASES = {
    "Exception": "FinancialException", "Case": "SalesAuditCase",
    "Recommendation": "ResolutionRecommendation",
}


def database_bindings(context: dict) -> list[dict]:
    visible = {e.get("name"): e for e in context.get("definitions", {}).get("entities", [])}
    bindings = []
    for mapper in sorted(Base.registry.mappers, key=lambda m: m.local_table.name):
        model, table = mapper.class_.__name__, mapper.local_table
        entity_name = ENTITY_ALIASES.get(model, model)
        entity = visible.get(entity_name)
        bindings.append({
            "model": model, "table": table.name, "ontology_entity": entity_name,
            "ontology_entity_id": entity.get("id") if entity else None,
            "status": "name-matched" if entity else "not-visible-in-release",
            "columns": [{"name": c.name, "type": str(c.type), "nullable": c.nullable,
                         "primary_key": c.primary_key,
                         "references": sorted(fk.target_fullname for fk in c.foreign_keys)}
                        for c in table.columns],
            "note": "Name correspondence only; property mappings require retailer approval.",
        })
    return bindings
