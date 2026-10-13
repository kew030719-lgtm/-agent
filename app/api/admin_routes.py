from fastapi import APIRouter, HTTPException

from app.api.dependencies import Actor, Database
from app.knowledge.feedback import KnowledgeFeedback, list_recent_feedback
from app.knowledge.usage import UsageSummary, list_usage_summary
from typing import Literal

from fastapi import Query
from pydantic import Field

from app.api.schemas import InputModel
from app.tools.handoff import (
    Handoff, get_handoff, list_handoffs, mark_handoff_read, resolve_handoff, unread_handoffs,
)


router = APIRouter(tags=["人工待办与统计"])


class HandoffReplyInput(InputModel):
    reply: str = Field(min_length=1, max_length=4000)


@router.get("/handoffs")
def handoffs(user: Actor, db: Database, status: Literal["pending", "resolved", "all"] = "pending",
             limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=1000000)) -> list[Handoff]:
    return list_handoffs(db, user, mine=False, status=status, limit=limit, offset=offset)


@router.get("/handoffs/mine")
def my_handoffs(user: Actor, db: Database, status: Literal["pending", "resolved", "all"] = "all",
                limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=1000000)) -> list[Handoff]:
    return list_handoffs(db, user, mine=True, status=status, limit=limit, offset=offset)


@router.get("/handoffs/unread-count")
def unread_count(user: Actor, db: Database) -> dict[str, int]:
    return {"count": unread_handoffs(db, user)}


@router.get("/handoffs/{handoff_id}")
def handoff_detail(handoff_id: int, user: Actor, db: Database) -> Handoff:
    try:
        return get_handoff(db, user, handoff_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/handoffs/{handoff_id}/read", status_code=204)
def read_reply(handoff_id: int, user: Actor, db: Database) -> None:
    if not mark_handoff_read(db, user, handoff_id):
        raise HTTPException(404, "没有可标记为已读的本人回复")


@router.post("/handoffs/{handoff_id}/resolve", status_code=204)
def resolve(handoff_id: int, body: HandoffReplyInput, user: Actor, db: Database) -> None:
    if not resolve_handoff(db, user, handoff_id, body.reply):
        raise HTTPException(404, "待办不存在或已处理，请刷新核对结果")


@router.get("/knowledge-feedback")
def feedback(user: Actor, db: Database) -> list[KnowledgeFeedback]:
    return list_recent_feedback(db, user)


@router.get("/knowledge-stats")
def stats(user: Actor, db: Database) -> list[UsageSummary]:
    return list_usage_summary(db, user)
