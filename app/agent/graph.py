from collections.abc import Callable
from typing import NotRequired, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.router import DOMAINS
from app.agent.meeting_dialogue import MeetingRequest, answer_meeting
from app.agent.profile import local_profile_domain, profile_answer
from app.auth import User
from app.tools.meeting import MeetingRoom


class AssistantInput(TypedDict):
    user: User
    question: str
    pending_ticket: NotRequired[str]
    meeting_context: NotRequired[str]


class AssistantState(AssistantInput, total=False):
    domain: str
    answer: str
    meeting_request: dict[str, object] | None


CONFIRM_WORDS = frozenset({"确认", "确认报修", "需要", "要"})
CANCEL_WORDS = frozenset({"取消", "取消报修", "不用了"})


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
    text = question.strip()
    return ("我的" in text or "本人" in text) and "假" in text and "余额" in text


def build_graph(
    route_fn: Callable[[str], str],
    create_ticket_fn: Callable[[User, str], int],
    get_leave_balance_fn: Callable[[User], float | None],
    create_handoff_fn: Callable[[User, str, str], int],
    create_expense_fn: Callable[[User, str, str], int],
    list_rooms_fn: Callable[[User, int], list[MeetingRoom]],
    list_available_rooms_fn: Callable[
            [User, int, str, str], list[MeetingRoom]
        ],
    answer_knowledge_fn: Callable[[User, str], str | None] | None = None,
    interpret_meeting_fn: Callable[[str, str], MeetingRequest] | None = None,
    book_room_fn: Callable[[User, int, str, str, int], int] | None = None,
) -> CompiledStateGraph:
    def choose_route(state: AssistantState) -> dict[str, object]:
        text = state["question"].strip()
        pending = state.get("pending_ticket", "")

        profile_domain = local_profile_domain(text)
        if profile_domain is not None:
            return {"domain": profile_domain, "pending_ticket": ""}

        if pending and text in CONFIRM_WORDS | CANCEL_WORDS:
            return {"domain": "it"}
        if ticket_description(text) is not None:
            return {"domain": "it", "pending_ticket": ""}
        if is_own_leave_query(text):
            return {"domain": "hr", "pending_ticket": ""}
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
        if result["domain"] != "meeting":
            result["meeting_context"] = ""
        return result

    def profile_node(state: AssistantState) -> dict[str, str]:
        return {"answer": profile_answer(state["user"], state["domain"])}

    def submit_ticket(user: User, description: str) -> dict[str, str]:
        try:
            ticket_id = create_ticket_fn(user, description)
        except (PermissionError, ValueError) as exc:
            return {"answer": str(exc), "pending_ticket": ""}

        return {
            "answer": f"IT 工单 #{ticket_id} 已创建。",
            "pending_ticket": "",
        }

    def it_node(state: AssistantState) -> dict[str, str]:
        text = state["question"].strip()
        pending = state.get("pending_ticket", "")

        if pending and text in CANCEL_WORDS:
            return {"answer": "已取消报修。", "pending_ticket": ""}
        if pending and text in CONFIRM_WORDS:
            return submit_ticket(state["user"], pending)

        description = ticket_description(text)
        if description is not None:
            if not description:
                return {"answer": "请在“报修：”后面填写具体问题。"}
            return submit_ticket(state["user"], description)

        return {
            "answer": "需要我为你创建 IT 工单吗？回复“确认报修”或“取消”。",
            "pending_ticket": text,
        }

    def hr_node(state: AssistantState) -> dict[str, str]:
        if not is_own_leave_query(state["question"]):
            return {"answer": "当前只支持查询本人余额，请输入：我的请假余额"}

        try:
            balance = get_leave_balance_fn(state["user"])
        except PermissionError as exc:
            return {"answer": str(exc)}

        if balance is None:
            handoff_id = create_handoff_fn(
                state["user"],
                state["question"],
                "请假余额未录入",
            )
            return {
                "answer": f"当前账号尚未录入请假余额，已转人工，待办 #{handoff_id} 已创建。"
            }

        return {"answer": f"当前登录账号的请假余额：{balance:g} 天。"}

    def knowledge_node(state: AssistantState) -> dict[str, str]:
        if answer_knowledge_fn is not None:
            answer = answer_knowledge_fn(state["user"], state["question"])
            if answer:
                return {"answer": answer}
        handoff_id = create_handoff_fn(
            state["user"],
            state["question"],
            "知识库没有足够可靠的依据",
        )
        return {
            "answer": f"知识库没有足够可靠的依据，问题已转人工，待办 #{handoff_id} 已创建。"
        }

    def human_node(state: AssistantState) -> dict[str, str]:
        handoff_id = create_handoff_fn(
            state["user"],
            state["question"],
            "路由判定需人工处理",
        )
        return {"answer": f"已转人工，待办 #{handoff_id} 已创建。"}

    def finance_node(state: AssistantState) -> dict[str, str]:
        body = expense_body(state["question"])
        usage = "请按格式输入：报销：125.50；事由：客户拜访交通费"
        if body is None:
            return {"answer": usage}

        amount, separator, description = body.partition("；事由：")
        if not separator or not amount.strip() or not description.strip():
            return {"answer": usage}

        try:
            request_id = create_expense_fn(
                state["user"], amount.strip(), description.strip()
            )
        except (PermissionError, ValueError) as exc:
            return {"answer": str(exc)}

        return {"answer": f"报销申请 #{request_id} 已提交，等待审核。"}

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

    builder.add_edge(START, "route")
    builder.add_conditional_edges(
        "route", select_domain, {name: name for name in DOMAINS}
    )
    for name in DOMAINS:
        builder.add_edge(name, END)

    return builder.compile(checkpointer=InMemorySaver())
