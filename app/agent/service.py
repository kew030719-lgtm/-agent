"""Web 对话服务：复用 LangGraph 和业务工具，跨请求状态由 API 层保存。"""

import os
import sqlite3
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from threading import RLock
from uuid import uuid4

from openai import OpenAI
from pymilvus.exceptions import MilvusException

from app.agent.graph import build_graph
from app.agent.operation_dialogue import interpret_operation
from app.agent.meeting_dialogue import interpret_meeting
from app.agent.dialogue import DialogueContext, QuestionPlan, plan_question
from app.auth import User
from app.errors import ServiceUnavailableError
from app.knowledge.answer import answer_from_knowledge
from app.knowledge.embeddings import LocalEmbedder
from app.knowledge.access import visible_document_ids
from app.knowledge.catalog import answer_catalog, visible_products
from app.knowledge.reranker import LocalReranker
from app.knowledge.vector_store import MilvusVectorStore
from app.tools.expense import create_expense_request
from app.tools.handoff import create_handoff
from app.tools.notifications import query_notifications
from app.tools.work_center import query_todos
from app.tools.ticket_workflow import query_own_tickets
from app.tools.it import create_it_ticket
from app.agent.leave_dialogue import interpret_leave
from app.tools.leave_workflow import create_leave_request, preview_leave, query_own_leaves, leave_balance_summary
from app.tools.leave import get_own_leave_balance
from app.tools.meeting import book_meeting_room, list_available_meeting_rooms, list_meeting_rooms


@dataclass
class TurnResult:
    domain: str
    answer: str
    pending_ticket: str = ""
    knowledge_question: str = ""
    feedback_allowed: bool = False
    meeting_context: str = ""
    source_document_ids: tuple[int, ...] = ()
    dialogue_context: str = ""
    operation_context: str = ""
    confirmation: str = ""


class AssistantService:
    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root
        self.lock = RLock()
        self.embedder: LocalEmbedder | None = None
        self.reranker: LocalReranker | None = None
        self.reranker_checked = False
        self.store: MilvusVectorStore | None = None

    def get_store(self) -> MilvusVectorStore:
        with self.lock:
            if self.store is None:
                self.store = MilvusVectorStore()
            return self.store

    def close(self) -> None:
        with self.lock:
            if self.store is not None:
                self.store.close()
                self.store = None

    def get_embedder(self) -> LocalEmbedder:
        with self.lock:
            if self.embedder is None:
                self.embedder = LocalEmbedder(self.data_root)
            return self.embedder

    def get_reranker(self) -> LocalReranker | None:
        with self.lock:
            if not self.reranker_checked:
                try:
                    self.reranker = LocalReranker(self.data_root)
                except (RuntimeError, ValueError, OSError):
                    self.reranker = None
                self.reranker_checked = True
            return self.reranker

    def respond(
        self, db: sqlite3.Connection, user: User, question: str,
        pending_ticket: str = "", previous_question: str = "",
        meeting_context: str = "", dialogue_context: str = "", operation_context: str = "",
    ) -> TurnResult:
        client: OpenAI | None = None
        knowledge_answer: str | None = None
        anchor = ""
        source_ids: list[int] = []
        status: dict[str, bool] = {}
        planned: QuestionPlan | None = None
        try:
            context = DialogueContext.model_validate_json(dialogue_context) if dialogue_context else DialogueContext(question=previous_question, topic=previous_question)
        except ValueError:
            context = DialogueContext()
        if context.source_ids and not set(context.source_ids) <= set(visible_document_ids(db, user)):
            context = DialogueContext()


        def get_client() -> OpenAI:
            nonlocal client
            if client is None:
                key = os.getenv("DEEPSEEK_API_KEY")
                if not key:
                    raise ServiceUnavailableError("请在服务端 .env 配置 DeepSeek API Key。")
                client = OpenAI(
                    api_key=key,
                    base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                    timeout=float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "30")),
                    max_retries=int(os.getenv("DEEPSEEK_MAX_RETRIES", "1")),
                )
            return client

        def route(text: str) -> str:
            nonlocal planned
            visible = set(visible_document_ids(db, user))
            titles = [row[1] for row in db.execute("SELECT id,title FROM knowledge_documents ORDER BY id")
                      if row[0] in context.source_ids and row[0] in visible][:20]
            companies = sorted({str(p["company"]) for p in visible_products(db, user)})
            planned = plan_question(get_client(), text, context, titles, companies)
            if planned.clarification or planned.needs_human:
                return "human"
            return planned.domain

        def answer_knowledge(actor: User, text: str) -> str | None:
            nonlocal knowledge_answer, anchor
            effective = (planned.standalone_question or text) if planned is not None else text
            if planned is not None and planned.knowledge_action != "search":
                knowledge_answer, ids = answer_catalog(db, actor, planned.knowledge_action, planned.company)
                source_ids.extend(ids)
            else:
                try:
                    with self.lock:
                        knowledge_answer = answer_from_knowledge(
                            get_client(), db, actor, effective,
                            self.get_embedder(), self.get_store(), self.get_reranker(),
                            source_ids=source_ids, answer_status=status,
                        )
                except ServiceUnavailableError:
                    raise
                except (RuntimeError, OSError, MilvusException) as exc:
                    raise ServiceUnavailableError("知识检索服务不可用，请检查模型和 Milvus。") from exc
            anchor = effective
            if knowledge_answer and status.get("incomplete"):
                return knowledge_answer + "\n需要人工确认的话，请回复“转人工”。"
            return knowledge_answer

        graph = build_graph(
            route, partial(create_it_ticket, db), partial(get_own_leave_balance, db),
            partial(create_handoff, db), partial(create_expense_request, db),
            partial(list_meeting_rooms, db), partial(list_available_meeting_rooms, db),
            answer_knowledge,
            lambda text, context: interpret_meeting(get_client(), text, context),
            partial(book_meeting_room, db),
            lambda text, context, kind: interpret_operation(get_client(), text, context, kind),
            partial(query_own_tickets, db),
            lambda text, context: interpret_leave(get_client(), text, context),
            partial(preview_leave, db), partial(create_leave_request, db),
            partial(query_own_leaves, db), partial(leave_balance_summary, db),
            partial(query_todos, db), partial(query_notifications, db),
        )
        try:
            result = graph.invoke(
                {"user": user, "question": question, "pending_ticket": pending_ticket, "meeting_context": meeting_context, "pending_handoff": context.pending_handoff, "operation_context": operation_context},
                config={"configurable": {"thread_id": uuid4().hex}},
            )
            if planned is not None and planned.clarification:
                result["domain"] = "clarify"
                result["answer"] = planned.clarification + " 如需人工协助，请回复“转人工”。"
            if result["domain"] == "knowledge":
                raw_question = question
                if planned is not None and planned.followup and context.question:
                    raw_question = f"补全问题：{anchor or question}\n用户问题：{context.clarification_question or context.question}\n当前追问：{question}"
                pending = raw_question if status.get("incomplete") or knowledge_answer is None else ""
                updated = DialogueContext(question=question, topic=anchor or question,
                                          source_ids=source_ids, pending_handoff=pending[:4000],
                                          catalog_products=[str(p["name"]) for p in visible_products(db, user)
                                                            if not planned.company or p["company"] == planned.company][:50]
                                          if planned is not None and planned.knowledge_action != "search" else [])
            elif result["domain"] == "clarify":
                # 模糊追问仍保留上一主题，方便回答“我指第二个”后继续。
                updated = context.model_copy(update={"pending_handoff": result.get("pending_handoff", ""), "clarification_question": question if planned is not None and planned.clarification else ""}) if context.question else DialogueContext(question=question, topic=question, pending_handoff=result.get("pending_handoff", ""), clarification_question=question if planned is not None and planned.clarification else "")
            elif result.get("pending_handoff"):
                updated = DialogueContext(question=question, topic=question, pending_handoff=result["pending_handoff"])
            else:
                updated = DialogueContext()
            return TurnResult(
                domain=result["domain"], answer=result["answer"],
                pending_ticket=result.get("pending_ticket", ""),
                knowledge_question=anchor, feedback_allowed=knowledge_answer is not None and bool(source_ids),
                meeting_context=result.get("meeting_context", ""),
                source_document_ids=tuple(source_ids), dialogue_context=updated.model_dump_json(),
                operation_context=result.get("operation_context", ""), confirmation=result.get("confirmation", ""),
            )
        finally:
            if client is not None:
                client.close()
