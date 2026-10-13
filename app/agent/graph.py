import re
from collections.abc import Callable
from typing import NotRequired, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.router import DOMAINS
from app.agent.operation_dialogue import (
    CANCEL, CONFIRM, OperationKind, OperationUpdate, answer_operation, read_draft,
)
from app.agent.leave_dialogue import (
    LEAVE_CANCEL, LEAVE_CONFIRM, LeaveUpdate, answer_leave, read_leave_draft,
)
from app.agent.meeting_dialogue import MeetingRequest, answer_meeting
from app.agent.profile import local_profile_domain, profile_answer
from app.auth import User
from app.tools.meeting import MeetingRoom


class AssistantInput(TypedDict):
    user: User
    question: str
    pending_ticket: NotRequired[str]
    meeting_context: NotRequired[str]
    pending_handoff: NotRequired[str]
    operation_context: NotRequired[str]


class AssistantState(AssistantInput, total=False):
    domain: str
    answer: str
    meeting_request: dict[str, object] | None
    operation_update: dict[str, object] | None
    leave_update: dict[str, object] | None
    confirmation: str


HANDOFF_WORDS = frozenset({"转人工", "确认转人工", "帮我转人工", "请转人工", "联系人工", "人工客服"})


def select_domain(state: AssistantState) -> str:
    return state["domain"]


def ticket_description(question: str) -> str | None:
    text = question.strip()
    for prefix in ("报修：", "报修:"):
        if text.startswith(prefix):
            return text[len(prefix):].strip()
    return None

def expense_body(question: str) -> str | None:
    text = question.strip()
    for prefix in ("报销：", "报销:"):
        if text.startswith(prefix):
            return text[len(prefix):].strip()
    return None

def requested_capacity(question: str) -> int | None:
    text = question.strip()
    if text in {"会议室", "查会议室", "查询会议室"}:
        return 1

    for prefix in ("会议室：", "会议室:"):
        if text.startswith(prefix):
            count = text[len(prefix):].strip()
            if count.endswith("人"):
                count = count[:-1].strip()
            try:
                return int(count)
            except ValueError:
                return None

    return None

def availability_request(question: str) -> tuple[int, str, str] | None:
    text = question.strip()
    for prefix in ("空闲会议室：", "空闲会议室:"):
        if text.startswith(prefix):
            body = text[len(prefix):].strip()
            count_text, separator, times = body.partition("；开始：")
            if not separator:
                return None

            start_at, separator, end_at = times.partition("；结束：")
            if not separator or not start_at.strip() or not end_at.strip():
                return None

            count_text = count_text.strip()
            if count_text.endswith("人"):
                count_text = count_text[:-1].strip()
            try:
                capacity = int(count_text)
            except ValueError:
                return None

            return capacity, start_at.strip(), end_at.strip()

    return None

def is_own_leave_query(question: str) -> bool:
    text = question.strip().rstrip("？?。！! ")
    return re.fullmatch(r"(?:查询|查看|查一下|看看|查)?(?:我的|本人)(?:年假|请假|假期)余额(?:还剩多少|是多少|有多少|多少)?", text) is not None


def build_graph(
    route_fn: Callable[[str], str],
    create_ticket_fn: Callable[..., int],
    get_leave_balance_fn: Callable[[User], float | None],
    create_handoff_fn: Callable[[User, str, str], int],
    create_expense_fn: Callable[..., int],
    list_rooms_fn: Callable[[User, int], list[MeetingRoom]],
    list_available_rooms_fn: Callable[
            [User, int, str, str], list[MeetingRoom]
        ],
    answer_knowledge_fn: Callable[[User, str], str | None] | None = None,
    interpret_meeting_fn: Callable[[str, str], MeetingRequest] | None = None,
    book_room_fn: Callable[[User, int, str, str, int], int] | None = None,
    interpret_operation_fn: Callable[[str, str, OperationKind | None], OperationUpdate] | None = None,
    query_ticket_fn: Callable[[User, int | None], str] | None = None,
    interpret_leave_fn: Callable[[str, str], LeaveUpdate] | None = None,
    preview_leave_fn: Callable[..., dict[str, object]] | None = None,
    create_leave_fn: Callable[..., int] | None = None,
    query_leave_fn: Callable[[User, int | None], str] | None = None,
    leave_balance_fn: Callable[[User], dict[str, object]] | None = None,
    query_todos_fn: Callable[[User], str] | None = None,
    query_notifications_fn: Callable[[User], str] | None = None,
) -> CompiledStateGraph:
    def choose_route(state: AssistantState) -> dict[str, object]:
        text = state["question"].strip()
        pending = state.get("pending_ticket", "")

        if text in HANDOFF_WORDS or text.startswith(("转人工：", "转人工:")) or (state.get("pending_handoff") and text == "取消转人工"):
            return {"domain": "human", "pending_ticket": ""}

        profile_domain = local_profile_domain(text)
        if profile_domain is not None:
            return {"domain": profile_domain, "pending_ticket": ""}

        query = text.rstrip("？?。！! ")
        if query in {"我的待办", "查看我的待办", "我还有哪些待办", "我有哪些待办", "待我处理的事项"}:
            return {"domain": "todos"}
        if query in {"我的通知", "未读通知", "我的未读通知", "有哪些未读通知", "哪些申请有新结果", "我的申请有哪些新结果"}:
            return {"domain": "notifications"}

        command = text.rstrip("。！!")
        leave_draft = read_leave_draft(state.get("operation_context", ""))
        if is_own_leave_query(text):
            return {"domain": "hr", "leave_update": LeaveUpdate(mode="balance").model_dump()}
        if command in {"确认请假", "确认年假"} or (leave_draft and command in LEAVE_CONFIRM | LEAVE_CANCEL):
            return {"domain": "hr"}
        if leave_draft and interpret_leave_fn is not None:
            update = interpret_leave_fn(text, state.get("operation_context", ""))
            if update.mode != "other":
                return {"domain": "hr", "leave_update": update.model_dump()}

        draft = read_draft(state.get("operation_context", ""))
        if draft and command in CONFIRM | CANCEL:
            return {"domain": "it" if draft.kind == "ticket" else "finance"}
        if pending and command in CONFIRM | CANCEL:
            # 旧版本未展示故障摘要的待办必须先展示，不能直接执行。
            return {"domain": "it", "operation_context": "", "operation_update": OperationUpdate(kind="ticket", description=pending).model_dump()}
        if command in {"确认报修", "确认报销"}:
            return {"domain": "it" if command == "确认报修" else "finance"}
        if ticket_description(text) is not None:
            return {"domain": "it", "operation_update": OperationUpdate(kind="ticket", description=ticket_description(text)).model_dump()}
        if draft and interpret_operation_fn is not None:
            update = interpret_operation_fn(text, state["operation_context"], None)
            if update.mode != "other":
                kind = update.kind or draft.kind
                return {"domain": "it" if kind == "ticket" else "finance", "operation_update": update.model_dump()}
        if expense_body(text) is not None:
            return {"domain": "finance", "pending_ticket": ""}
        if (
                requested_capacity(text) is not None
                or text.startswith(("会议室：", "会议室:"))
                or text.startswith(("空闲会议室：", "空闲会议室:"))
        ):
            return {"domain": "meeting", "pending_ticket": ""}

        if interpret_meeting_fn is not None and state.get("meeting_context"):
            request = interpret_meeting_fn(text, state["meeting_context"])
            if request.mode != "other":
                return {"domain": "meeting", "pending_ticket": "", "meeting_request": request.model_dump()}

        domain = route_fn(text)
        return {
            "domain": domain if domain in DOMAINS else "human",
            "pending_ticket": "",
        }

    def route_node(state: AssistantState) -> dict[str, object]:
        result = choose_route(state)
        result.setdefault("meeting_request", None)
        result.setdefault("operation_update", None)
        result.setdefault("leave_update", None)
        result["confirmation"] = ""
        if result["domain"] not in {"it", "finance", "hr"}:
            result["operation_context"] = ""
            result["pending_ticket"] = ""
        # 请假和报销/报修共用持久化草稿字段，但不能相互继承确认状态。
        leave_draft = read_leave_draft(state.get("operation_context", ""))
        if (result["domain"] == "hr" and not leave_draft) or (result["domain"] in {"it", "finance"} and leave_draft):
            result["operation_context"] = ""
        if result["domain"] != "human":
            result["pending_handoff"] = ""
        if result["domain"] != "meeting":
            result["meeting_context"] = ""
        return result

    def profile_node(state: AssistantState) -> dict[str, str]:
        return {"answer": profile_answer(state["user"], state["domain"])}

    def work_center_node(state: AssistantState) -> dict[str, str]:
        query_fn = query_todos_fn if state["domain"] == "todos" else query_notifications_fn
        if query_fn is None:
            return {"answer": "待办与通知查询暂未配置，请在对应业务页面查看。"}
        return {"answer": query_fn(state["user"])}

    def operation_node(state: AssistantState, kind: OperationKind) -> dict[str, str]:
        text = state["question"].strip()
        context = state.get("operation_context", "")
        parsed = state.get("operation_update")
        update = OperationUpdate.model_validate(parsed) if parsed is not None else None
        if update is None and text.rstrip("。！!") not in CONFIRM | CANCEL:
            if interpret_operation_fn is not None:
                update = interpret_operation_fn(text, context, kind)
            elif kind == "expense":
                amount, separator, description = (expense_body(text) or "").partition("；事由：")
                update = OperationUpdate(kind=kind, amount=amount if separator else None, description=description if separator else None)
            else:
                update = OperationUpdate(kind=kind, description=ticket_description(text) or text)
        if update is not None and update.mode == "other":
            return {"answer": "你想了解流程，还是提交申请？请说明要办理的事情。", "operation_context": "", "confirmation": ""}
        if update is not None and update.mode == "query":
            answer = query_ticket_fn(state["user"], update.ticket_id) if query_ticket_fn and update.kind == "ticket" else "请到对应业务页面查看申请进度。"
            return {"answer": answer, "operation_context": "", "confirmation": "", "pending_ticket": ""}
        # 迁移旧待报修状态时先展示摘要。
        effective = "补充故障" if parsed is not None and state.get("pending_ticket") and not context and text.rstrip("。！!") in CONFIRM else text
        return answer_operation(effective, context, kind, state["user"], update, create_ticket_fn, create_expense_fn)

    def it_node(state: AssistantState) -> dict[str, str]:
        return operation_node(state, "ticket")

    def hr_node(state: AssistantState) -> dict[str, str]:
        if all(fn is not None for fn in (interpret_leave_fn, preview_leave_fn, create_leave_fn, query_leave_fn, leave_balance_fn)):
            text = state["question"].strip()
            context = state.get("operation_context", "")
            parsed = state.get("leave_update")
            update = LeaveUpdate.model_validate(parsed) if parsed is not None else None
            if update is None and text.rstrip("。！!") not in LEAVE_CONFIRM | LEAVE_CANCEL:
                update = interpret_leave_fn(text, context)
            return answer_leave(text, context, state["user"], update, preview_leave_fn, create_leave_fn, query_leave_fn, leave_balance_fn)
        if not is_own_leave_query(state["question"]):
            return {"answer": "当前只支持查询本人余额，请输入：我的请假余额"}

        try:
            balance = get_leave_balance_fn(state["user"])
        except PermissionError as exc:
            return {"answer": str(exc)}

        if balance is None:
            return {
                "answer": "当前账号尚未录入请假余额。需要人工核对的话，请回复“转人工”。",
                "pending_handoff": state["question"] + "（当前账号尚未录入请假余额）",
            }

        return {"answer": f"当前登录账号的请假余额：{balance:g} 天。"}

    def knowledge_node(state: AssistantState) -> dict[str, str]:
        if answer_knowledge_fn is not None:
            answer = answer_knowledge_fn(state["user"], state["question"])
            if answer:
                return {"answer": answer}
        return {
            "answer": "当前可访问的资料还不足以回答这个问题。你可以补充产品名称或具体问题；需要人工确认的话，请回复“转人工”。",
            "pending_handoff": state["question"],
        }

    def human_node(state: AssistantState) -> dict[str, str]:
        text = state["question"].strip()
        if text == "取消转人工":
            return {"domain": "clarify", "answer": "已取消转人工。", "pending_handoff": ""}
        pending = state.get("pending_handoff", "")
        explicit = text in HANDOFF_WORDS
        if text.startswith(("转人工：", "转人工:")):
            pending = text[4:].strip()
            explicit = True
        if explicit and pending:
            handoff_id = create_handoff_fn(state["user"], pending, "用户明确请求人工协助")
            return {"answer": f"已转人工，待办 #{handoff_id} 已创建。可到“人工协助 → 我的事项”查看进度和回复。", "pending_handoff": ""}
        if explicit:
            return {"domain": "clarify", "answer": "请说明需要人工处理的问题，例如：转人工：需要确认某产品的交付负责人。", "pending_handoff": ""}
        return {"domain": "clarify", "answer": "请补充要查询的对象或需要办理的事情。如果需要人工协助，请回复“转人工”。", "pending_handoff": text}

    def finance_node(state: AssistantState) -> dict[str, str]:
        return operation_node(state, "expense")

    def meeting_node(state: AssistantState) -> dict[str, str]:
        text = state["question"].strip()
        available = availability_request(text)

        if interpret_meeting_fn is not None:
            parsed = state.get("meeting_request")
            if parsed is not None:
                request = MeetingRequest.model_validate(parsed)
            elif available is not None:
                capacity, start_at, end_at = available
                request = MeetingRequest(mode="period", capacity=capacity, start_at=start_at, end_at=end_at)
            elif requested_capacity(text) is not None:
                request = MeetingRequest(mode="list", capacity=requested_capacity(text))
            else:
                request = interpret_meeting_fn(text, state.get("meeting_context", ""))
            return answer_meeting(request, state["user"], list_rooms_fn, list_available_rooms_fn, book_room_fn)

        if available is not None:
            capacity, start_at, end_at = available
            try:
                rooms = list_available_rooms_fn(
                    state["user"], capacity, start_at, end_at
                )
            except (PermissionError, ValueError) as exc:
                return {"answer": str(exc)}

            if not rooms:
                return {
                    "answer": "该时段没有符合条件的空闲会议室（依据本地预订记录）。"
                }
            header = (
                f"{start_at} 至 {end_at}，"
                "根据本地预订记录，空闲会议室："
            )
        else:
            if text.startswith(("空闲会议室：", "空闲会议室:")):
                return {
                    "answer": "请按格式输入：空闲会议室：8人；"
                              "开始：2026-10-09 10:00；结束：2026-10-09 11:00"
                }

            capacity = requested_capacity(text)
            if capacity is None:
                return {"answer": "请按格式输入：会议室：8人；或输入：查会议室"}

            try:
                rooms = list_rooms_fn(state["user"], capacity)
            except (PermissionError, ValueError) as exc:
                return {"answer": str(exc)}

            if not rooms:
                return {"answer": f"没有容纳至少 {capacity} 人的会议室。"}
            header = "会议室清单（未核对空闲时段）："

        lines = [
            f"#{room['id']} {room['name']}，{room['location']}，"
            f"容纳 {room['capacity']} 人"
            for room in rooms
        ]
        return {"answer": header + "\n" + "\n".join(lines)}

    builder = StateGraph(AssistantState, input_schema=AssistantInput)
    builder.add_node("route", route_node)
    builder.add_node("knowledge", knowledge_node)
    builder.add_node("hr", hr_node)
    builder.add_node("it", it_node)
    builder.add_node("finance", finance_node)
    builder.add_node("meeting", meeting_node)
    builder.add_node("human", human_node)
    builder.add_node("identity", profile_node)
    builder.add_node("permissions", profile_node)
    builder.add_node("todos", work_center_node)
    builder.add_node("notifications", work_center_node)

    builder.add_edge(START, "route")
    builder.add_conditional_edges(
        "route", select_domain, {name: name for name in DOMAINS}
    )
    for name in DOMAINS:
        builder.add_edge(name, END)

    return builder.compile(checkpointer=InMemorySaver())
