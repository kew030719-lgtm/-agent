from app.auth import ROLE_PERMISSIONS, User


def local_profile_domain(question: str) -> str | None:
    text = question.strip().rstrip("？?！!。 ")
    if text in {"你是谁", "你是什么助手", "介绍一下你自己"}:
        return "identity"
    if text in {
        "我有什么权限", "我的权限", "查看我的权限", "现在有什么权限",
        "当前账号有什么权限", "我是什么角色", "我是谁",
    }:
        return "permissions"
    return None


def profile_answer(user: User, domain: str) -> str:
    if domain == "identity":
        return (
            "我是企业内部员工助手，可以回答你有权查看的知识问题，"
            "协助提交 IT 工单、查询本人请假余额、发起报销，以及查询和预订会议室。"
            "资料不足或需要人工处理时，会创建人工待办。"
        )
    permissions = sorted(ROLE_PERMISSIONS.get(user["role"], frozenset()))
    return (
        f"当前登录账号：{user['username']}\n角色：{user['role']}\n"
        "权限：" + ("、".join(permissions) if permissions else "无")
    )
