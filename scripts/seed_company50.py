"""创建维科科技50人模拟公司；复用现有解析、索引、权限与密码存储。"""

from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from app.agent.service import AssistantService
from app.api.database import initialize
from app.auth import DB_PATH, connect, insert_user
from app.knowledge.catalog import ProductMetadata, register_product
from app.knowledge.documents import add_knowledge_file
from app.knowledge.embeddings import index_document
from app.tools.leave import set_leave_balance
from app.tools.meeting import add_meeting_room
from scripts.company_simulation import BASE, configure, counts, save_json
from scripts.organize_company50 import assign_organization, write_accounts


ROLE_COUNTS = {"admin": 2, "hr": 3, "it": 5, "finance": 4, "employee": 36}
PASSWORD = "123456789"
POLICIES = {
    "维科公司制度": """## 公司概况
维科科技是一家完全虚构的软件公司，共50人：管理员2人、人事3人、IT支持5人、财务4人、普通员工36人。本文及配套制度仅供员工助手演示，模拟生效日期为2026年1月1日。

## 工作时间与考勤
标准工作日为周一至周五，工作时间09:00至18:00，午休12:00至13:00。员工在考勤系统签到、签退。漏打卡应在2个工作日内提交补卡说明，由直属主管确认；每月最多补卡3次。远程办公须提前1个工作日经主管批准。

## 入职与离职
入职当天由人事创建员工档案，管理员分配账号，IT发放设备并开通必要权限。员工首次登录后核对自己的角色和权限。离职交接需归还设备、移交文档，由人事通知管理员停用账号。

## 信息安全与账号权限
账号限本人使用，不得共享密码。客户名单、源代码和财务原始凭证不得上传到未获批准的外部服务。发现账号异常立即提交IT工单，并通知管理员。普通员工可查询公开制度、本人请假余额、会议室，并提交IT工单和报销申请；管理员可管理账号、知识文档和人工待办。

## 会议与设备
会议室须提前预约，结束后清理白板与桌面。青松会议室可容纳6人，竹影会议室可容纳10人，云帆会议室可容纳16人，星海会议室可容纳24人。网络故障先检查网线或Wi-Fi，再提交含设备编号、发生时间、报错截图的IT工单。

## 制度解释
考勤、年假和请假问题由人事解释，报销凭证与费用归类由财务解释。制度没有覆盖的特殊情况转人工确认，不得自行推定审批结论。
""",
    "维科年假制度": """## 适用范围与年假天数
本制度是维科科技的虚构公司福利规则，供系统测试使用。这里的年假天数为公司统一发放的年度总额度，不与其他年假额度重复叠加。
在维科服务满1年且未满5年的员工，每年享有10天带薪年假；服务满5年且未满10年的员工，每年享有15天带薪年假；服务满10年的员工，每年享有20天带薪年假。服务未满1年的员工，在满6个月后可申请3天预支年假，该额度从下一年度年假中扣除。

## 发放与余额
年假按自然年管理，每年1月1日由人事核定额度。个人剩余年假=本年度额度+有效结转天数-已休天数-已批准待休天数。公司制度说明的是额度规则；个人实际可用天数应通过“我的请假余额”查询，不能仅根据制度推算。

## 申请与审批
年假至少提前3个工作日申请，最小申请单位为0.5天。连续休假不超过3个工作日，由直属主管审批；超过3个工作日，先由直属主管审批，再由部门负责人审批，人事登记后生效。申请时填写起止日期、天数和工作交接人。

## 结转与撤销
当年未休年假最多结转5天，必须在次年3月31日前使用。离职或特殊结转申请由人事人工核算。取消已批准的年假应在休假开始前通知主管，由人事撤销登记并恢复相应余额。审批未完成不得视为申请成功。

## 示例
员工入职维科2年，本年度额度为10天；若已休2天且没有结转或待休记录，则剩余8天。员工在维科服务6年，年度额度为15天。员工服务11年，年度额度为20天。
""",
    "维科报销制度": """## 适用范围和时限
本制度为虚构的维科科技费用报销规则。员工因公发生的交通、住宿、餐饮和办公用品费用可依制度申请报销。应在费用发生后30个自然日内提交；跨期或超标准费用转财务人工审核。

## 交通与住宿标准
市内公务交通据实报销，网约车需附行程单并说明公务事由。城际高铁限二等座，飞机限经济舱。国内差旅住宿每人每晚最高500元；超出部分须事先获得部门负责人书面批准。通勤费用原则上不属于公务交通。

## 餐饮与办公用品
国内差旅餐费每日最高100元，按有效凭证据实报销。客户招待餐饮每人每次最高200元，需注明参与人数、客户名称和业务事由。办公用品单次采购超过1000元，须先完成采购申请。礼品、私人消费及没有业务事由的费用不得报销。

## 申请材料
每笔报销必须提供合法有效的发票、支付凭证、费用日期、金额、币种和业务事由。交通费附行程单，住宿费附酒店明细，餐饮费附消费清单。同一张发票不得重复报销。电子发票上传原始PDF文件，不能只上传聊天截图。

## 审批和付款
金额不超过5000元，由直属主管确认业务真实性，再由财务审核；超过5000元，还需总经理审批。财务审核通过后5个工作日内安排付款。员工助手创建报销申请仅代表提交，实际审批和付款结果以财务处理记录为准。

## 退回处理
凭证缺失、金额不一致或事由不清的申请会退回补充。申请人应在5个工作日内补齐材料。丢失发票、外币费用及预付款抵扣由财务人工确认。
""",
    "维科请假制度": """## 请假类型
本制度为虚构的维科科技请假流程。支持年假、事假、病假等类型。年假的额度和结转规则见《维科年假制度》。个人余额通过“我的请假余额”查询；事假和病假不从年假余额中自动扣除。

## 一般申请流程
员工填写请假类型、开始日期、结束日期、总天数、申请原因和交接人，提交直属主管审批。连续请假不超过3个工作日由主管审批，超过3个工作日加签部门负责人，人事完成登记后生效。请假按工作日计算，最小单位0.5天，周末不计入工作日请假天数。

## 提前申请时间
年假至少提前3个工作日申请；事假至少提前1个工作日申请。突发病假应在当日09:30前通知主管，并在返岗后2个工作日内补交申请和证明。无法自行办理的紧急情况可由交接人联系主管和人事。

## 病假证明和事假材料
连续病假达到2个工作日须提交正规医疗机构的就诊证明。事假说明具体原因和预计返回时间，涉及私人敏感信息时只提交给人事。薪资扣减或特殊假期的计算由人事人工确认。

## 工作交接和销假
休假前向交接人说明进行中的任务、截止日期及必要的文档位置，不得交出个人账号密码。提前返岗须向主管和人事申请销假，批准后更新实际休假天数。延长休假需另行提交申请，不能直接修改已批准的结束日期。

## 系统边界
员工助手目前可查询本人请假余额、解释制度和转人工；查询余额或提出请假意愿不等于已经创建并批准请假单。正式请假请通过公司请假流程办理。
""",
}

# 每份产品资料有独立用途、操作步骤和业务限制，避免仅替换编号的重复文本。
PRODUCTS = [
    ("客户关系管理", "销售部", "管理客户档案、商机阶段和跟进记录", "新建客户并填写公司名称、联系人及联系方式；建立商机后选择负责人；每次沟通后更新跟进记录", "商机金额以人民币记录，赢单前必须上传合同编号", "客户重复时根据统一社会信用代码合并，由销售主管确认"),
    ("项目协作", "产品部", "管理项目任务、里程碑和交付验收", "创建项目并邀请成员；将任务分配给负责人并设置截止日期；交付后提交验收材料", "只有项目负责人可以关闭项目，未完成任务必须先处理", "逾期任务在看板标红，由项目负责人调整排期"),
    ("智能工单", "IT支持部", "接收设备故障和服务请求并跟踪处理状态", "选择故障类型；填写设备编号和故障现象；上传截图后提交；处理结束后由申请人确认", "严重故障指影响全公司网络或核心业务的中断，应同时电话通知IT", "普通工单目标为4个工作小时内响应，复杂故障由IT说明修复计划"),
    ("费用管家", "财务部", "归集费用凭证、检查重复发票和跟踪报销进度", "新建费用单；上传发票与支付凭证；核对金额和费用日期；提交主管和财务审核", "同一发票号码不能重复报销，金额超过5000元需要总经理审批", "识别金额与票面不符时人工更正并重新上传清晰发票"),
    ("人事档案", "人事部", "维护入职、岗位变更和员工档案", "人事创建档案；录入入职日期及所属部门；岗位变更时提交变更记录并留存审批说明", "员工仅可查看本人基础档案，身份证和薪酬字段仅对获授权人事开放", "入职日期错误由人事核验合同后修正，普通员工不能自行修改"),
    ("会议预约", "行政部", "查询会议室空闲时间并提交预约", "选择日期、起止时间和人数；筛选容量足够的会议室；填写会议主题后确认预约", "预约以30分钟为单位，单次不超过4小时，时间冲突时禁止重复占用", "会议取消后应及时释放房间，超过开始时间15分钟未使用可由行政释放"),
    ("知识中心", "运营部", "保存制度文档、产品手册并提供有来源的知识问答", "管理员上传文档并选择可见范围；系统解析切片并建立索引；员工提问后查看回答及引用来源", "可见范围由账号权限控制，没有可靠依据的问题转人工", "文档未完成索引时检查导入状态，扫描PDF需要OCR提取文字"),
    ("库存台账", "仓储部", "记录设备和物料的入库、领用和库存盘点", "创建物料编码；登记入库数量及库位；领用时填写使用人；月底生成盘点单", "出库数量不得超过可用库存，库存调整必须记录原因和经办人", "盘点差异先核对未完成的领用单，再由仓储负责人审批调整"),
    ("合同管理", "商务部", "记录合同审批、签署状态和到期提醒", "填写合同对方、金额和有效期；上传合同正文；通过审批后记录签署日期并归档", "合同到期前30天提醒负责人，合同正文修改后必须重新审批", "签章文件缺页时退回补齐，未签署合同不能标记为已生效"),
    ("经营看板", "管理部", "汇总销售、费用和项目交付指标", "选择统计月份和所属部门；查看收入、费用及交付进度；点击指标查看明细来源；导出汇总报表", "每个工作日08:00刷新上一个工作日的数据，金额默认以人民币元展示", "指标与业务系统不一致时先核对日期范围和部门筛选，再向数据管理员提单"),
]


def make_documents(root: Path) -> list[Path]:
    sources = root / "sources"
    sources.mkdir()
    documents = dict(POLICIES)
    for number, (name, owner, purpose, steps, limits, support) in enumerate(PRODUCTS, 1):
        title = f"维科产品{number:02d}{name}"
        documents[title] = (
            f"## 产品概述\n{title}，产品编号WK-P{number:02d}，版本V1.0，负责部门为{owner}。"
            f"产品用途：{purpose}。本资料为完全虚构的演示产品说明，不代表员工助手已集成该产品的所有操作。\n\n"
            f"## 操作流程\n{steps}。\n\n## 业务规则\n{limits}。\n\n"
            f"## 异常处理\n{support}。无法按文档处理时，说明产品编号及故障现象后转人工。\n"
        )
    paths = []
    for title, body in documents.items():
        path = sources / f"{title}.md"
        path.write_text(f"# {title}\n\n{body}", encoding="utf-8")
        paths.append(path)
    return paths


def seed() -> Path:
    run = "wk50_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    root = BASE / run
    root.mkdir(parents=True, mode=0o700)
    save_json(root / "manifest.json", {"company": "维科科技（完全虚构）", "collection": "company_sim_" + run,
                                      "created_at": datetime.now().isoformat(), "employees": 50, "profile": "wk50"})
    configure(root)
    accounts: list[dict[str, Any]] = []
    for role, total in ROLE_COUNTS.items():
        for number in range(1, total + 1):
            years = (number - 1) % 12 + 1
            entitlement = 10 if years < 5 else 15 if years < 10 else 20
            used = (number - 1) % 5 * 0.5
            accounts.append({"username": f"wk_{role}_{number:02d}", "password": PASSWORD, "role": role,
                             "name": f"维科员工{len(accounts) + 1:02d}", "service_years": years,
                             "annual_entitlement": entitlement, "used_days": used, "leave_days": entitlement - used,
                             "department": {"admin": "管理部", "hr": "人事部", "it": "IT支持部", "finance": "财务部"}.get(role, ("研发部", "销售部", "产品部", "运营部")[(number - 1) % 4])})
    save_json(root / "accounts.json", accounts)
    admin = {"username": accounts[0]["username"], "role": "admin"}
    with closing(connect(root / "company.db")) as db:
        initialize(db)
        for account in accounts:
            insert_user(db, account["username"], account["password"], account["role"])
            set_leave_balance(db, admin, account["username"], account["leave_days"])
        assign_organization(db, accounts)
        write_accounts(root, accounts)
        for name, floor, capacity in [("青松", "1楼", 6), ("竹影", "1楼", 10), ("云帆", "2楼", 16), ("星海", "2楼", 24)]:
            add_meeting_room(db, admin, name + "会议室", floor, capacity)
        print("50个账号、50条余额、4间会议室已创建", flush=True)
        service = AssistantService(DB_PATH.parent)
        try:
            for path in make_documents(root):
                document_id = add_knowledge_file(db, admin, path, "general", root)
                if path.stem.startswith("维科产品"):
                    number = int(path.stem[4:6])
                    with db:
                        register_product(db, admin, document_id, ProductMetadata(company="维科科技", code=f"WK-P{number:02d}", name=path.stem))
                total = index_document(db, document_id, service.get_embedder(), service.get_store())
                print(f"已导入 {path.name}：{total}个切片", flush=True)
        finally:
            service.close()
    save_json(root / "seed_counts.json", counts(root))
    rows = ["# 维科科技模拟公司账号\n", "共50人，统一初始密码：`123456789`。全部账号为虚构数据。\n",
            "| 账号 | 角色 | 部门 | 服务年限 | 年假额度 | 已休 | 余额 |", "| --- | --- | --- | ---: | ---: | ---: | ---: |"]
    rows.extend(f"| {a['username']} | {a['role']} | {a['department']} | {a['service_years']} | {a['annual_entitlement']} | {a['used_days']} | {a['leave_days']} |" for a in accounts)
    (root / "ACCOUNTS.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (root / "ACCOUNTS.md").chmod(0o600)
    print(root, flush=True)
    return root


if __name__ == "__main__":
    seed()
