import json
import logging
import sqlite3
from time import perf_counter
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.dependencies import Actor, Database, owned_conversation
from app.api.schemas import ChatInput, ChatTurn, Conversation, FeedbackInput
from app.errors import ServiceUnavailableError
from app.knowledge.feedback import record_feedback
from app.knowledge.access import access_stamp, sources_visible
from app.knowledge.usage import record_usage


router = APIRouter(tags=["对话"])
logger = logging.getLogger(__name__)


def visible_turn(db: sqlite3.Connection, user: dict[str, object], row: sqlite3.Row) -> dict[str, object]:
    result = dict(row)
    if result["domain"] == "knowledge" and result["feedback_allowed"] and not sources_visible(db, user, result.get("source_ids_json")):
        result.update(answer="此回答的资料权限已变化或旧回答未记录可验证来源，请重新提问。", feedback_allowed=False)
    return result


@router.get("/conversations", response_model=list[Conversation])
def list_conversations(
    user: Actor, db: Database,
    limit: int = Query(default=100, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=(1 << 63) - 1),
) -> list[dict[str, object]]:
    return [dict(row) for row in db.execute(
        "SELECT id,title,created_at,updated_at FROM conversations WHERE username=? "
        "ORDER BY updated_at DESC, rowid DESC LIMIT ? OFFSET ?", (user["username"], limit, offset),
    )]


@router.post("/conversations", response_model=Conversation, status_code=201)
def create_conversation(user: Actor, db: Database) -> dict[str, object]:
    conversation_id = uuid4().hex
    with db:
        db.execute("INSERT INTO conversations(id,username,role) VALUES (?,?,?)", (
            conversation_id, user["username"], user["role"],
        ))
    return dict(owned_conversation(db, user, conversation_id))


@router.get("/conversations/{conversation_id}/messages", response_model=list[ChatTurn])
def list_messages(conversation_id: str, user: Actor, db: Database) -> list[dict[str, object]]:
    owned_conversation(db, user, conversation_id)
    return [visible_turn(db, user, row) for row in db.execute(
        "SELECT * FROM chat_turns WHERE conversation_id=? ORDER BY id", (conversation_id,),
    )]


@router.post("/conversations/{conversation_id}/messages", response_model=ChatTurn)
def send_message(
    conversation_id: str, body: ChatInput, request: Request, user: Actor, db: Database,
) -> dict[str, object]:
    conversation = owned_conversation(db, user, conversation_id)
    previous = db.execute(
        "SELECT * FROM chat_turns WHERE conversation_id=? AND request_id=?",
        (conversation_id, str(body.request_id)),
    ).fetchone()
    if previous is not None:
        if previous["question"] != body.question:
            raise HTTPException(409, "请求编号已被其他问题使用")
        if previous["status"] == "completed":
            return visible_turn(db, user, previous)
        raise HTTPException(409, "请求处理中或已中断，请先刷新对话核对结果")
    try:
        with db:
            cursor = db.execute(
                "INSERT INTO chat_turns(conversation_id,request_id,question,status) VALUES (?,?,?,'processing')",
                (conversation_id, str(body.request_id), body.question),
            )
            turn_id = cursor.lastrowid
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "此对话仍有问题处理中，请稍后刷新") from exc
    conversation = owned_conversation(db, user, conversation_id)
    started = perf_counter()
    same_role = conversation["role"] == user["role"]
    stamp = access_stamp(db, user)
    same_access = conversation["knowledge_access_stamp"] == stamp
    try:
        result = request.app.state.assistant.respond(
            db, user, body.question,
            conversation["pending_ticket"] if same_role else "",
            conversation["knowledge_question"] if same_role and same_access else "",
            conversation["meeting_context"] if same_role else "",
            conversation["dialogue_context"] if same_role and same_access else "",
        )
        with db:
            db.execute(
                "UPDATE chat_turns SET answer=?,domain=?,status='completed',feedback_allowed=?,source_ids_json=? WHERE id=?",
                (result.answer, result.domain, int(result.feedback_allowed), json.dumps(result.source_document_ids), turn_id),
            )
            db.execute(
                "UPDATE conversations SET title=CASE WHEN title='新对话' THEN ? ELSE title END,"
                "pending_ticket=?,knowledge_question=?,meeting_context=?,role=?,knowledge_access_stamp=?,dialogue_context=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (body.question[:36], result.pending_ticket, result.knowledge_question, result.meeting_context, user["role"], stamp, result.dialogue_context, conversation_id),
            )
        if result.domain == "human" or (result.domain == "knowledge" and result.feedback_allowed):
            try:
                record_usage(db, user, "answered" if result.feedback_allowed else "handoff",
                             int((perf_counter() - started) * 1000))
            except sqlite3.Error:
                logger.warning("Knowledge usage could not be recorded")
    except Exception as exc:
        message = str(exc) if isinstance(exc, (ServiceUnavailableError, ValueError)) else "服务处理失败，请稍后重试"
        with db:
            db.execute("UPDATE chat_turns SET answer=?,status='failed' WHERE id=?", (message, turn_id))
            db.execute("UPDATE conversations SET pending_ticket='',knowledge_question='',meeting_context='',dialogue_context='' WHERE id=?", (conversation_id,))
        if isinstance(exc, ValueError):
            raise HTTPException(400, message) from exc
        raise HTTPException(503, message) from exc
    return visible_turn(db, user, db.execute("SELECT * FROM chat_turns WHERE id=?", (turn_id,)).fetchone())


@router.post("/messages/{turn_id}/feedback", response_model=ChatTurn)
def feedback(turn_id: int, body: FeedbackInput, user: Actor, db: Database) -> dict[str, object]:
    row = db.execute(
        "SELECT t.* FROM chat_turns t JOIN conversations c ON c.id=t.conversation_id "
        "WHERE t.id=? AND c.username=?", (turn_id, user["username"]),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "回答不存在")
    if not row["feedback_allowed"] or row["status"] != "completed":
        raise HTTPException(400, "只能评价有依据的知识回答")
    if not sources_visible(db, user, row["source_ids_json"]):
        raise HTTPException(403, "当前无权访问此回答的资料，不能提交反馈")
    # 单条 UPDATE 先占用评价，防止并发重复提交。
    with db:
        changed = db.execute("UPDATE chat_turns SET rating=? WHERE id=? AND rating IS NULL", (body.rating, turn_id))
    if not changed.rowcount:
        raise HTTPException(409, "这条回答已经评价过了")
    try:
        record_feedback(db, user, row["question"], row["answer"], body.rating, json.loads(row["source_ids_json"]))
    except Exception:
        with db:
            db.execute("UPDATE chat_turns SET rating=NULL WHERE id=?", (turn_id,))
        raise
    return visible_turn(db, user, db.execute("SELECT * FROM chat_turns WHERE id=?", (turn_id,)).fetchone())
