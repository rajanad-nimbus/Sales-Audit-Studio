"""HTTP endpoints for the read-only, grounded assistant."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from apps.agents import llm
from apps.assistant import chat
from apps.security.authentication import require_role
from database import get_db

router = APIRouter()


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatRequest(BaseModel):
    message: str = Field(max_length=4000)
    case_id: str | None = Field(default=None, max_length=36)
    persona: str = Field(default="", max_length=100)
    history: list[ChatMessage] = Field(default_factory=list, max_length=10)


@router.post("/api/chat")
async def chat_endpoint(
    req: ChatRequest,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_role("finance", "analyst", "auditor")),
):
    msg = req.message.strip()
    if not msg:
        raise HTTPException(status_code=400, detail="Empty message")
    ctx = await chat.build_context(db, req.case_id, msg, user)
    base = chat.rule_answer(msg, ctx, req.persona)
    history = [message.model_dump() for message in req.history]
    text = await llm.chat(ctx, history, msg, req.persona)
    if text:
        return {**base, "answer": text, "source": "llm"}
    return base
