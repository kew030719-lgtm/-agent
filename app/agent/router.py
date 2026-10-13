import json
import math

from openai import OpenAI, OpenAIError

from app.errors import ServiceUnavailableError
from app.config import deepseek_model


DOMAINS: frozenset[str] = frozenset({
    "knowledge", "hr", "it", "finance", "meeting", "human",
    "identity", "permissions", "todos", "notifications",
})

MIN_ROUTE_CONFIDENCE = 0.75

ROUTER_PROMPT = """
你是企业内部员工助手的意图路由器。只返回 JSON，不回答问题。
格式：{"domain": "hr", "confidence": 0.93, "needs_human": false}
confidence 是 0 到 1 的数字，表示你对问题所属领域的把握，不表示你是否知道答案。
公司名称、业务编号或文档内容陌生，不应降低明确知识查询的分类置信度；资料是否存在由后续检索判断。
查询内部业务信息、制度、流程和文档属于 knowledge，知识可见权限由后端根据登录账号校验。
涉及财务、人事或内部资料的只读查询不等于高风险操作，不要仅因这些领域词设置 needs_human。
请求付款、删除数据、变更权限、索取密码或密钥等认证凭据，以及明确要求人工处理时，设置 needs_human 为 true。
只有无法识别意图或区分领域时才降低置信度。

领域：
identity：询问助手是谁或能做什么
permissions：询问当前登录账号、角色或已有权限；不是修改权限
todos：查询当前登录人有哪些待办、需要审批或处理的事项（跨请假、报销、IT工单和人工协助）
notifications：查询当前登录人的站内通知、未读提醒、申请有哪些新结果；查询单笔请假或工单进度仍使用对应业务领域
knowledge：查询公司制度、流程、产品文档、FAQ，包括请假流程、报销材料、VPN 使用说明
hr：本人年假申请、修改未提交的请假草稿、查询本人请假余额或已提交请假审批进度；请假制度和流程咨询属于knowledge。年假申请是正常业务操作，不直接转人工
it：报修、创建 IT 工单、处理电脑或网络故障，查询本人已提交工单的进度、处理人、处理结果
finance：提交报销等财务操作
meeting：查询会议室、预订会议室；普通员工预订是正常业务操作，不应仅因预订就转人工
human：意图不清、无法分类，或涉及高风险操作

每次只能选择一个领域。
"""


def parse_domain(content: str | None) -> str:
    if not content:
        return "human"

    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        return "human"

    if not isinstance(result, dict):
        return "human"

    domain = result.get("domain")
    confidence = result.get("confidence")
    needs_human = result.get("needs_human")

    if not isinstance(domain, str) or domain not in DOMAINS:
        return "human"
    if isinstance(confidence, bool) or not isinstance(
        confidence, (int, float)
    ):
        return "human"
    if not 0 <= confidence <= 1 or not math.isfinite(confidence):
        return "human"
    if not isinstance(needs_human, bool):
        return "human"
    if needs_human or confidence < MIN_ROUTE_CONFIDENCE:
        return "human"

    return domain


def route_question(client: OpenAI, question: str) -> str:
    if not question.strip():
        return "human"

    try:
        response = client.chat.completions.create(
            model=deepseek_model(),
            messages=[
                {"role": "system", "content": ROUTER_PROMPT},
                {"role": "user", "content": question},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=100,
            extra_body={"thinking": {"type": "disabled"}},
        )
    except OpenAIError as exc:
        raise ServiceUnavailableError("DeepSeek 路由服务调用失败，请检查 API 配置或稍后重试。") from exc
    if not response.choices:
        raise ServiceUnavailableError("DeepSeek 路由服务返回空结果，请重试。")
    return parse_domain(response.choices[0].message.content)
