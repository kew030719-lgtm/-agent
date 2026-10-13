from app.auth import ROLE_PERMISSIONS, User
from app.organization import JOB_LEVELS


def local_profile_domain(question: str) -> str | None:
    text = question.strip().rstrip("？?！!。 ")
    if text in {"你是谁", "你是什么助手", "介绍一下你自己"}:
        return "identity"
    if text in {
        "我有什么权限", "我的权限", "查看我的权限", "现在有什么权限",
        "当前账号有什么权限", "我是什么角色", "我是谁",
        "我的部门", "我在哪个部门", "我的上级是谁", "我的直属上级", "我的职级",
    }:
        return "permissions"
    return None


def profile_answer(user: User, domain: str) -> str:
    if domain == "identity":
        return (
            "我是企业内部员工助手，可以回答你有权查看的知识问题，"
            "协助提交 IT 工单、查询本人请假余额、发起报销，以及查询和预订会议室。"
            "知识资料不足时会说明；你可以确认转人工，由工作人员继续处理。"
        )
    permissions = sorted(ROLE_PERMISSIONS.get(user["role"], frozenset()))
    return (
        f"当前登录账号：{user['username']}\n姓名：{user.get('display_name') or '未填写'}\n"
        f"部门：{user.get('department_name') or '未分配'}\n"
        f"职级：{JOB_LEVELS.get(user.get('job_level', 'employee'), '普通员工')}\n"
        f"直属上级：{user.get('manager_username') or '未分配'}\n"
        f"权限角色：{user['role']}\n"
        "权限：" + ("、".join(permissions) if permissions else "无")
    )
