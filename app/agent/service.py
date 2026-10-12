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
from app.agent.meeting_dialogue import interpret_meeting
from app.agent.router import route_question
from app.auth import User
from app.errors import ServiceUnavailableError
from app.knowledge.answer import answer_from_knowledge
from app.knowledge.embeddings import LocalEmbedder
from app.knowledge.query import expand_followup, is_followup
from app.knowledge.reranker import LocalReranker
from app.knowledge.vector_store import MilvusVectorStore
from app.tools.expense import create_expense_request
from app.tools.handoff import create_handoff
from app.tools.it import create_it_ticket
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
        meeting_context: str = "",
    ) -> TurnResult:
        client: OpenAI | None = None
        knowledge_answer: str | None = None
        anchor = ""

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
            if previous_question and is_followup(text):
                return "knowledge"
            return route_question(get_client(), text)

        def answer_knowledge(actor: User, text: str) -> str | None:
            nonlocal knowledge_answer, anchor
            followup = bool(previous_question and is_followup(text))
            effective = expand_followup(previous_question, text) if followup else text
            try:
                with self.lock:
                    knowledge_answer = answer_from_knowledge(
                        get_client(), db, actor, effective,
                        self.get_embedder(), self.get_store(), self.get_reranker(),
                    )
            except ServiceUnavailableError:
                raise
            except (RuntimeError, OSError, MilvusException) as exc:
                raise ServiceUnavailableError("知识检索服务不可用，请检查模型和 Milvus。") from exc
            if knowledge_answer:
                anchor = previous_question if followup else text
            return knowledge_answer

        graph = build_graph(
            route, partial(create_it_ticket, db), partial(get_own_leave_balance, db),
            partial(create_handoff, db), partial(create_expense_request, db),
            partial(list_meeting_rooms, db), partial(list_available_meeting_rooms, db),
            answer_knowledge,
            lambda text, context: interpret_meeting(get_client(), text, context),
            partial(book_meeting_room, db),
        )
        try:
            result = graph.invoke(
                {"user": user, "question": question, "pending_ticket": pending_ticket, "meeting_context": meeting_context},
                config={"configurable": {"thread_id": uuid4().hex}},
            )
            return TurnResult(
                domain=result["domain"], answer=result["answer"],
                pending_ticket=result.get("pending_ticket", ""),
                knowledge_question=anchor, feedback_allowed=knowledge_answer is not None,
                meeting_context=result.get("meeting_context", ""),
            )
        finally:
            if client is not None:
                client.close()
