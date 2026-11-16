import asyncio

import pytest
from apps.audit.models import AuditEvent
from apps.transactions.comments import CommentInput, add_comment, list_comments
from apps.transactions.tests.test_transactions import seed
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select


def test_comment_is_persisted_with_authenticated_actor_and_store_scope():
    async def exercise():
        session = await seed()
        async with session() as db:
            user = {
                "user": "auditor@example.com",
                "role": "analyst",
                "store_scoped": True,
                "stores": ["S1"],
            }
            created = await add_comment(
                "t1", CommentInput(text="  Investigated\nwith store  "), db, user
            )
            result = await list_comments("t1", 0, db, user)
            assert result["items"] == [created]
            assert created["text"] == "Investigated\nwith store"
            assert created["actor"] == "auditor@example.com"
            event = (
                await db.execute(select(AuditEvent).where(AuditEvent.id == created["id"]))
            ).scalar_one()
            assert event.event_type == "TRANSACTION_COMMENT_ADDED"
            with pytest.raises(HTTPException) as denied:
                await list_comments("t4", 0, db, user)
            assert denied.value.status_code == 403
            with pytest.raises(HTTPException) as missing:
                await add_comment("missing", CommentInput(text="note"), db, user)
            assert missing.value.status_code == 404

    asyncio.run(exercise())


def test_comment_rejects_blank_and_oversized_text():
    for text in ("   ", "x" * 2001):
        with pytest.raises(ValidationError):
            CommentInput(text=text)
