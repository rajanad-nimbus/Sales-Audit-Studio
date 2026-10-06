"""Agent settings an admin may change at runtime. Environment variables are the defaults; stored values override.

Deliberately NOT settable here: autonomy limits, policy thresholds, roles. Those remain code/config changes.
"""
import os

from fastapi import HTTPException
from sqlalchemy import select

import nimbus
from agent_tools import TOOL_SPECS
from models import AgentSetting

ALLOWED_MODELS = ["claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1", "claude-haiku-4-5-20251001"]
TOOL_NAMES = [t["name"] for t in TOOL_SPECS]


def defaults() -> dict:
    return {"investigator_enabled": os.getenv("NIMBUS_AGENT_INVESTIGATOR", "1") != "0",  # LLM-driven unless explicitly turned off
            "auto_investigate": os.getenv("NIMBUS_AUTO_INVESTIGATE", "1") == "1",
            "model": os.getenv("NIMBUS_LLM_MODEL", ALLOWED_MODELS[0]),
            "tools": list(TOOL_NAMES)}


async def load(db) -> dict:
    out = defaults()
    for row in (await db.execute(select(AgentSetting))).scalars().all():
        if row.key in out:
            out[row.key] = row.value.get("v")
    return out


def validate(changes: dict) -> dict:
    clean = {}
    for k, v in changes.items():
        if k in ("investigator_enabled", "auto_investigate"):
            if not isinstance(v, bool):
                raise HTTPException(422, f"{k} must be true or false")
        elif k == "model":
            if v not in ALLOWED_MODELS:
                raise HTTPException(422, f"model must be one of {ALLOWED_MODELS}")
        elif k == "tools":
            if not isinstance(v, list) or not v or any(t not in TOOL_NAMES for t in v):
                raise HTTPException(422, f"tools must be a non-empty subset of {TOOL_NAMES}")
            v = [t for t in TOOL_NAMES if t in v]
        else:
            raise HTTPException(422, f"{k} is not an editable setting")
        clean[k] = v
    return clean


async def update(db, changes: dict, user: str) -> dict:
    clean = validate(changes)
    current = await load(db)
    for k, v in clean.items():
        if current[k] == v:
            continue
        row = (await db.execute(select(AgentSetting).where(AgentSetting.key == k))).scalar_one_or_none()
        if row:
            row.value, row.updated_by = {"v": v}, user
        else:
            db.add(AgentSetting(key=k, value={"v": v}, updated_by=user))
        nimbus.audit(db, "AGENT_SETTING_CHANGED", None, "AgentSetting", k, f"{k} changed by {user}",
                     actor=user, after={"setting": k, "before": current[k], "after": v})
    await db.commit()
    return await load(db)
