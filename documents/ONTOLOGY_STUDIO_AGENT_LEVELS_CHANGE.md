# Ontology Studio change: publish agent levels

ZeTSA reads each failure class's agent level (collect, propose or act) from the approved ontology release. Ontology Studio's audit-control validator accepted only three families, so it could not publish one. The change below is now applied in the Studio working tree, uncommitted.

Target: `backend/services/audit_controls.py` in the Ontology Studio repository. That file was already untracked there, so commit it together with this change.

## ZeTSA contract (already built)

An `audit_control` business rule whose config is:

```json
{ "family": "agent_level", "exception_type": "DUPLICATE_SALES", "level": "act" }
```

- `level` is one of `collect`, `propose`, `act`.
- `exception_type` is a failure code such as `DUPLICATE_SALES`. One rule per code; a repeat rejects the whole release in ZeTSA.
- A class with no rule keeps the default from the design doc. A class not listed anywhere is `collect`.

## Change (applied)

```diff
-FAMILIES = ("store_day_total", "transaction_field", "policy_limit")
+FAMILIES = ("store_day_total", "transaction_field", "policy_limit", "agent_level")
+AGENT_LEVELS = ("collect", "propose", "act")
```

In the module docstring, add a line to the family list:

```
* ``agent_level``: how far the agent may go on a failure class (collect, propose or act).
```

In `validate_config`, add a branch next to `policy_limit`:

```python
    if family == "agent_level":
        extra = set(config) - {"family", "exception_type", "level"}
        if extra:
            raise ValueError(f"Unknown field {sorted(extra)[0]!r}")
        return {"family": family,
                "exception_type": _text(config.get("exception_type"), "exception_type", 64),
                "level": _choice(config.get("level"), "level", AGENT_LEVELS, "")}
```

## Test (added to `backend/tests/test_audit_controls.py`)

```python
def test_agent_level_accepts_a_known_level_and_rejects_the_rest():
    cfg = ac.validate_config({"family": "agent_level", "exception_type": "DUPLICATE_SALES", "level": "act"})
    assert cfg == {"family": "agent_level", "exception_type": "DUPLICATE_SALES", "level": "act"}
    with pytest.raises(ValueError):
        ac.validate_config({"family": "agent_level", "exception_type": "DUPLICATE_SALES", "level": "autopilot"})
    with pytest.raises(ValueError):
        ac.validate_config({"family": "agent_level", "exception_type": "DUPLICATE_SALES", "level": "act", "sql": "x"})
```

## Checked

- `_choice(value, label, allowed, "")` treats an empty default as required: a missing `level` is not in the allowed list and raises. That is the behaviour wanted.
- The Studio frontend (`frontend/src`, `shared`) has no reference to `audit_control` or its families, so there is no rule form to extend. Rules are authored some other way, and I did not find where.
- A release only takes effect once approved, so a new level changes ZeTSA only after a data steward approves the release.

## Status

- The validator logic was run directly: a valid level passes; a bad level, an extra field and a missing level are rejected; `policy_limit` still validates as before.
- Studio's pytest suite refuses to run against the dev database, so the new test has not been run. Run it on a scratch database or in CI.
- Not tested end to end: ZeTSA reading an approved release that carries an `agent_level` rule.

## Seeding the defaults (added, not run)

`ontology/extend_sales_audit.py` in ZeTSA now has `AUDIT_LEVELS`: 12 draft rules (act for the two exact-duplicate classes, propose for nine classes including timing difference, collect for return without refund). They are created the same way as the limits and wait for a data steward to approve them. ZeTSA works without them, because the same defaults are built in.
