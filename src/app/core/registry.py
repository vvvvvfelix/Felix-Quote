"""基础设施层 — 模块注册中心。

为什么需要：小公司只想要报价 + 订单，大集团要全模块。硬编码挂载路由意味着
「关不掉也拆不开」，用户被迫接受全部功能，菜单臃肿、权限点多到没人维护。

设计原则（对标 Odoo 的可安装应用，但刻意做轻）：
- 清单只存**元数据**（模块文件名字符串），不 import —— 避免循环依赖
- 启停状态持久化在 settings 表（key = module:<code>），重启后保持
- 依赖在启停时校验，禁止「关掉被依赖的模块」导致运行时崩
- **迁移照常执行**：关模块只隐藏路由与菜单，不删表，随时可再开启且数据不丢

新增模块时只改这里：加一条 MODULES 记录，并在 modules/ 下放同名文件（含 router）。
"""
from typing import Any

from .db import get_conn, now

# ==========================================================================
# 一、模块清单（唯一事实来源）
# ==========================================================================
# code      模块代号（同时是开关 key 与前端菜单标识）
# name      中文名
# pkg       app/modules/ 下的文件名（不含 .py）
# group     分组（前端菜单归类）
# core      是否核心模块（True 则永不可关闭）
# depends   依赖的模块 code 列表（依赖未启用则本模块不可用）
# desc      一句话说明，展示在开关面板
MODULES: list[dict[str, Any]] = [
    {"code": "auth", "name": "登录与认证", "pkg": "auth", "group": "系统", "core": True,
     "depends": [], "desc": "登录、改密、会话与登录失败锁定"},
    {"code": "identity", "name": "组织与身份", "pkg": "identity", "group": "系统", "core": True,
     "depends": ["auth"], "desc": "部门、人员、工号、角色权限、操作日志"},
    {"code": "structure", "name": "组织树", "pkg": "structure", "group": "系统", "core": True,
     "depends": ["identity"],
     "desc": "集团/法人公司/工厂/事业部/部门/车间/班组 多类型无限层级组织主干"},
    {"code": "position", "name": "岗位职级", "pkg": "position", "group": "系统", "core": False,
     "depends": ["structure"],
     "desc": "岗位 / 职级(M·P·T·O 四序列) / 编制(组织×岗位×职级×汇报线) 与 编制 vs 在岗看板"},
    {"code": "employee", "name": "员工档案", "pkg": "employee", "group": "人力", "core": False,
     "depends": ["identity", "structure", "position"],
     "desc": "员工档案(证件/合同/社保/银行卡/紧急联系人) 与 全生命周期异动流水(入职/转正/调岗/晋升/离职/退休)"},

    {"code": "workflow", "name": "审批引擎", "pkg": "workflow", "group": "协同", "core": False,
     "depends": ["identity"],
     "desc": "通用审批引擎：流程定义→实例→任务→回调；会签/或签/条件分支/转办/加签；审批中心(待办/已办/我发起) 与 请假示例"},
    {"code": "expense", "name": "电子报销", "pkg": "expense", "group": "财务",
     "core": False, "depends": ["identity", "workflow", "report"],
     "desc": "无纸化电子报销：报销单/费用明细/票据影像、重复票据识别、金额分档审批、A4 打印与签名确认"},

    {"code": "masterdata", "name": "主数据", "pkg": "masterdata", "group": "基础数据",
     "core": False, "depends": ["identity"],
     "desc": "产品库、客户、供应商、分类与参数"},
    {"code": "quotation", "name": "报价管理", "pkg": "quotation", "group": "销售链",
     "core": False, "depends": ["identity", "masterdata"],
     "desc": "选品制作报价单、历史报价与打印"},
    {"code": "documents", "name": "资料库", "pkg": "documents", "group": "基础数据",
     "core": False, "depends": ["identity"],
     "desc": "合同、证书、图纸等文件归档"},
    {"code": "platform", "name": "平台与系统", "pkg": "platform", "group": "系统", "core": True,
     "depends": ["auth"], "desc": "关于页、看板、用户管理、环境自检、模块开关"},
    {"code": "licensing", "name": "授权与激活", "pkg": "licensing", "group": "系统", "core": True,
     "depends": ["auth"], "desc": "30 天试用管控与作者授权激活（不可关闭）"},
    {"code": "connectors", "name": "连接器（企微/钉钉/飞书）", "pkg": "connectors",
     "group": "系统", "core": False, "depends": ["auth"],
     "desc": "企业微信/钉钉/飞书 SSO 与消息推送接入（M120）；钉钉/飞书本轮为占位"},

    {"code": "inquiry", "name": "询盘管理", "pkg": "inquiry", "group": "销售链",
     "core": False, "depends": ["identity", "masterdata"],
     "desc": "客户询盘登记与跟进"},
    {"code": "order", "name": "销售订单", "pkg": "order", "group": "销售链",
     "core": False, "depends": ["identity", "masterdata"],
     "desc": "成交后的履约与跨部门确认"},
    {"code": "supply", "name": "采购与生产", "pkg": "supply", "group": "供应链",
     "core": False, "depends": ["order"],
     "desc": "采购单、生产进度与外协"},
    {"code": "logistics", "name": "物流出货", "pkg": "logistics", "group": "供应链",
     "core": False, "depends": ["order"],
     "desc": "出货单、运输与关务"},
    {"code": "finance", "name": "财务应收", "pkg": "finance", "group": "财务",
     "core": False, "depends": ["order"],
     "desc": "应收台账与收汇登记"},

    {"code": "task", "name": "待办中心", "pkg": "task", "group": "协同",
     "core": False, "depends": ["identity"],
     "desc": "跨部门派活与我的待办"},
    {"code": "workbench", "name": "工作台", "pkg": "workbench", "group": "协同",
     "core": False, "depends": ["identity"],
     "desc": "个人视图与数据概览"},
    {"code": "mail", "name": "邮件与发件", "pkg": "mail", "group": "协同",
     "core": False, "depends": ["identity"],
     "desc": "SMTP 配置、发报价单与邮件留痕"},
    {"code": "aiapi", "name": "AI 增强", "pkg": "aiapi", "group": "协同",
     "core": False, "depends": ["identity"],
     "desc": "云端模型接入（关闭则为纯本地模式，0 Token）"},
    {"code": "report", "name": "报表与打印", "pkg": "report", "group": "财务",
     "core": False, "depends": ["identity"],
     "desc": "报价单/送货单/行程单/发票/水单报销 的排版与打印"},
    {"code": "toolbox", "name": "岗位工具箱", "pkg": "toolbox", "group": "协同",
     "core": False, "depends": ["identity"],
     "desc": "各岗位常用外部软件（金蝶/税务/CAD/携程等）与系统集成指引"},
]

_BY_CODE: dict[str, dict[str, Any]] = {m["code"]: m for m in MODULES}


# ==========================================================================
# 一之二、模块功能举例（给人看的"这玩意到底干嘛用"）
# ==========================================================================
# desc 是一句话定义，example 是一个**具体场景**，帮人在部署/开关时判断
# "我公司用不用得上"。只做展示，不参与任何逻辑与依赖校验。
# 由 catalog() 随模块一起下发，供部署助手悬停提示与系统内模块开关面板使用。
EXAMPLES: dict[str, str] = {
    "auth": "例：业务员张三用账号密码登录；连续输错 5 次账号自动锁定，管理员在后台解锁。",
    "identity": "例：建「华南大区 / 广州工厂 / 生产部」三级部门，给李四分配「销售」角色，他就只能看自己客户的报价。",
    "structure": "例：集团下 3 家法人公司、每家 2 个工厂、工厂下设车间和班组，可无限层级展开并一键统计各节点人数。",
    "position": "例：定义「高级销售经理」为 M3 职级、编制 5 人；看板显示该岗位编制 5 / 在岗 3 / 缺编 2。",
    "employee": "例：录入王五的身份证、合同到期日、社保与银行卡；转正/调岗/离职全程留痕，合同到期前提醒。",
    "workflow": "例：请假流程「部门主管 → 人事 → 总经理」逐级审批；会签需全部同意，主管休假可转办给副主管。",
    "expense": "例：销售提交差旅报销，上传发票影像，系统识别重复票据并告警，按金额分档走不同审批。",
    "masterdata": "例：维护产品「层炉 CL-3D」的规格/净重/毛重/价格，以及客户、供应商、分类与参数模板。",
    "quotation": "例：选 5 个产品做成英文报价单，套用客户自己的 Excel 模板导出，条款与证书自动填充。",
    "documents": "例：上传供应商的 CE 证书 PDF 与产品图纸归档，做报价时可直接引用附件。",
    "platform": "例：看板显示今日报价数；管理员在模块开关面板关掉公司用不上的模块。",
    "licensing": "例：装好先免费用 30 天；到期前在授权页输入授权码激活为永久版。",
    "connectors": "例：管理员填好企业微信 CorpID/Secret，员工在企微里打开系统即可免密登录。",
    "inquiry": "例：记录阿里国际站来的询盘，登记客户需求并一路跟进到转成正式报价。",
    "order": "例：客户确认报价后转成销售订单，生产、采购、物流各部门在线确认交期。",
    "supply": "例：订单缺料时生成采购单发给供应商，并跟踪生产进度与外协工序。",
    "logistics": "例：安排出货单、录入快递单号与关务信息，并通知客户已发货。",
    "finance": "例：订单发货后生成应收台账，收到客户外汇后登记核销，看还有多少钱没收回。",
    "task": "例：主管派活「周五前提交月度报表」给下属，下属在「我的待办」里看到并处理。",
    "workbench": "例：个人首页汇总我的待办、我负责的报价单与最近单据，不用到处翻菜单。",
    "mail": "例：配好企业邮箱后，选中一张报价单一键发给客户，并留存发送记录备查。",
    "aiapi": "例：开启后可用云端模型自动润色英文产品描述；关闭则是纯本地模式，0 Token。",
    "report": "例：打印 A4 报价单、送货单、发票；或把水单拍照导入、脱敏后直接打印报销。",
    "toolbox": "例：财务岗一键打开金蝶与电子税务局，设计岗打开 CAD，不用自己找网址。",
}


# ==========================================================================
# 二、状态读写
# ==========================================================================
def _key(code: str) -> str:
    return f"module:{code}"


def ensure_states() -> None:
    """首次运行写入默认状态：全部启用（核心模块强制启用）。幂等。"""
    db = get_conn()
    for m in MODULES:
        k = _key(m["code"])
        if db.execute("SELECT 1 FROM settings WHERE key=?", (k,)).fetchone():
            # 核心模块即使被人手动改库也必须拉回启用，否则系统无登录入口
            if m["core"]:
                db.execute("UPDATE settings SET value='1' WHERE key=?", (k,))
            continue
        db.execute("INSERT INTO settings(key,value) VALUES(?,?)", (k, "1"))
    db.commit()


def is_enabled(code: str) -> bool:
    """模块是否启用。未知 code 一律视为 False，核心模块恒 True（防误关）。"""
    m = _BY_CODE.get(code)
    if not m:
        return False
    if m["core"]:
        return True
    try:
        db = get_conn()
        row = db.execute("SELECT value FROM settings WHERE key=?", (_key(code),)).fetchone()
    except Exception:
        # 库或 settings 表尚未建好（首次启动、装配期）——按启用处理最安全：
        # 首装必然是全模块可用，绝不能因为读不到开关就把系统起成空壳。
        return True
    return (row["value"] if row else "1") == "1"


def enabled_codes() -> list[str]:
    return [m["code"] for m in MODULES if is_enabled(m["code"])]


def set_enabled(code: str, on: bool) -> tuple[bool, str]:
    """启停模块。返回 (是否成功, 说明)。依赖校验在这里做，不做就等着运行时崩。"""
    m = _BY_CODE.get(code)
    if not m:
        return False, "模块不存在"
    if m["core"]:
        return False, f"「{m['name']}」是核心模块，不可关闭"
    if on:
        missing = [d for d in m["depends"] if not is_enabled(d)]
        if missing:
            names = "、".join(_BY_CODE[d]["name"] for d in missing if d in _BY_CODE)
            return False, f"需先启用依赖模块：{names}"
    else:
        blockers = [x["name"] for x in MODULES
                    if code in x["depends"] and is_enabled(x["code"])]
        if blockers:
            return False, f"被以下已启用模块依赖，请先关闭：{'、'.join(blockers)}"
    db = get_conn()
    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
               (_key(code), "1" if on else "0"))
    db.commit()
    return True, "已启用" if on else "已关闭"


def catalog() -> list[dict[str, Any]]:
    """模块目录 + 当前状态 + 依赖关系，供开关面板与前端菜单使用。"""
    out = []
    for m in MODULES:
        dependents = [x["code"] for x in MODULES if m["code"] in x["depends"]]
        out.append({**m,
                    "enabled": is_enabled(m["code"]),
                    "example": EXAMPLES.get(m["code"], ""),
                    "dependents": dependents,
                    "can_disable": not m["core"] and not any(
                        is_enabled(d) for d in dependents)})
    return out


def enabled_packages() -> list[str]:
    """返回应当挂载的模块文件名列表（装配层用）。"""
    return [_BY_CODE[c]["pkg"] for c in enabled_codes()]


# ==========================================================================
# 三、装配结果回执
# ==========================================================================
# 挂载失败的模块记在这里（装配层写、平台模块读）。放在 core 而非 main，
# 是为了避免 modules → main 的反向 import 造成循环依赖。
MOUNT_FAILURES: list[dict[str, Any]] = []
