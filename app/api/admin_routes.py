from fastapi import APIRouter, HTTPException

from app.api.dependencies import Actor, Database
from app.knowledge.feedback import KnowledgeFeedback, list_recent_feedback
from app.knowledge.usage import UsageSummary, list_usage_summary
from app.tools.handoff import Handoff, list_pending_handoffs, resolve_handoff


router = APIRouter(tags=["人工待办与统计"])


@router.get("/handoffs")
def handoffs(user: Actor, db: Database) -> list[Handoff]:
    return list_pending_handoffs(db, user)


@router.post("/handoffs/{handoff_id}/resolve", status_code=204)
def resolve(handoff_id: int, user: Actor, db: Database) -> None:
    if not resolve_handoff(db, user, handoff_id):
        raise HTTPException(404, "待办不存在或已处理")


@router.get("/knowledge-feedback")
def feedback(user: Actor, db: Database) -> list[KnowledgeFeedback]:
    return list_recent_feedback(db, user)


@router.get("/knowledge-stats")
def stats(user: Actor, db: Database) -> list[UsageSummary]:
    return list_usage_summary(db, user)
