"""模块层 — 电子报销（M071）：无纸化报销 + 重复票据识别 + 分档审批 + A4 打印。

用户原话：「员工不用打印纸质发票/水单，全部提交到系统；系统识别重复票据并标识
清楚、提醒处理；规划好打印排版，审批完由财务或本人打印、签名确认。」

本模块是 Phase 7 财务域的第一块，刻意复用两块已有地基而不是重造：
- **审批**：接 M005 引擎（core/workflow.py），本模块只负责「提交 + 注册回调」，
  流程怎么走、谁批、怎么签全交给引擎（见 EXPENSE_DEFINITION 的金额条件分支）。
- **影像**：接 M109 的 report_slips（data/slips），经 core/services.save_slip
  落盘。modules 之间禁止互相 import，故共享逻辑收敛到 core，物理存储只有一套。

本模块的真正核心是**票据查重**。设计取舍（写在这里是因为将来一定会有人来问
「为什么不直接拦掉」）：

1. 指纹分级而不是一刀切。增值税票有「代码+号码」这种全国唯一的强身份，命中即
   几乎必然是同一张票；水单有交易单号也是强身份；但火车票/机票/出租车票没有
   唯一号，只能退化为「类型+金额+日期+商家」，同日同商家的两笔同额交通费会被
   误报。所以指纹必须分级，弱指纹命中只能叫「疑似」。

2. 单据内硬拦、跨单据软标。自己一张单子裡出现两张一模一样的票，只可能是重复
   录入或拿错图 —— 硬拦，让用户先删掉；但跨单重复存在合理业务场景（两人同行
   各报一半、垫付后归还），硬拦会误伤且剥夺财务裁量权，故只标记不拦截，由财务
   在复核台人工核实后销案（confirmed_ok）或驳回。

3. 软删不参与匹配。已作废的票不该拦住新单据，SQL 里显式带 is_active=1。
"""
import re

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from ..auth import current_user
from ..core import permissions as P
from ..core import workflow as WF
from ..core.db import effective_company, get_conn, jdump, jload, now, release_conn
from ..core.services import audit, next_no, save_slip

router = APIRouter(prefix="/api/expense", tags=["expense"])

# ==========================================================================
# 一、枚举集中定义（前端一律从 /api/expense/types 取，禁止硬编码中文）
# ==========================================================================
EXPENSE_CATEGORIES = {
    "travel": "差旅", "transport": "交通", "hotel": "住宿",
    "meal": "招待", "office": "办公用品", "telecom": "通讯", "other": "其他",
}

TICKET_TYPES = {
    "vat_special": "增值税专票", "vat_normal": "增值税普票",
    "train": "火车票", "flight": "机票", "taxi": "出租车票",
    "wechat": "微信水单", "alipay": "支付宝水单",
    "receipt": "收据", "none": "无票",
}

# 水单/支付类：有交易单号则用单号；没有则退化为「平台 + 金额 + 交易时间」
PAY_TICKET_TYPES = {"wechat", "alipay"}

REPORT_STATUS = {
    "draft": "草稿", "submitted": "待审批", "approving": "审批中",
    "approved": "已通过", "rejected": "已驳回", "paid": "已支付", "void": "已作废",
}

DUP_STATUS = {
    "none": "正常", "suspected": "疑似重复", "confirmed_ok": "已核实非重复",
}

# 金额分档审批档位（与 EXPENSE_DEFINITION 的边条件一致，前端据此提示流向）
APPROVAL_TIERS = [
    {"min_amount": 10000, "label": "≥10000", "flow": "部门主管 → 财务复核 → 总经理批准"},
    {"min_amount": 1000, "label": "≥1000", "flow": "部门主管 → 财务复核"},
    {"min_amount": 0, "label": "<1000", "flow": "部门主管"},
]


# ==========================================================================
# 二、票据查重指纹
# ==========================================================================
def _norm_text(v) -> str:
    """归一化文本：去掉所有空白、英文转大写。

    不归一化会出现「INV 001」与「inv001」被判成两张票的漏检 —— 手输发票号
    时空格与大小写是最常见的输入差异。
    """
    return re.sub(r"\s+", "", str(v or "")).upper()


def _norm_amount(v) -> str:
    """金额统一两位小数：100、100.0、100.00 必须是同一个指纹。"""
    try:
        return "%.2f" % (float(v or 0),)
    except (TypeError, ValueError):
        return "0.00"


def _norm_date(v) -> str:
    """日期统一 YYYY-MM-DD：兼容 2026/1/5、2026.1.5、2026-01-05。"""
    s = str(v or "").strip()
    if not s:
        return ""
    s = s.replace("/", "-").replace(".", "-")
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return "%04d-%02d-%02d" % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return s


def ticket_fingerprint(d: dict) -> str:
    """生成票据查重指纹。三级身份，强者优先。

    1) INV|发票代码|发票号码   增值税票（代码+号码全国唯一，最强）
    2) TRN|交易单号           水单/支付的流水号（平台内唯一）
    3) PLT|票据类型|金额|日期  水单无单号时的降级（平台+金额+交易时间）
    4) GEN|票据类型|金额|日期|商家  兜底（火车票/机票/出租车票等无唯一号票据）

    第 4 级的误报率天生就高（同日同商家同额的两笔出租车票会被判疑似），
    这也是跨单重复「只标记不拦截」的根本原因 —— 系统没资格替财务下结论。
    """
    code = _norm_text(d.get("invoice_code"))
    no = _norm_text(d.get("invoice_no"))
    if code and no:
        return "INV|%s|%s" % (code, no)
    trans = _norm_text(d.get("trans_no"))
    if trans:
        return "TRN|%s" % trans
    amount = _norm_amount(d.get("amount"))
    date = _norm_date(d.get("occur_date"))
    ttype = str(d.get("ticket_type") or "none")
    if ttype in PAY_TICKET_TYPES:
        return "PLT|%s|%s|%s" % (ttype, amount, date)
    return "GEN|%s|%s|%s|%s" % (ttype, amount, date, _norm_text(d.get("merchant")))


# ==========================================================================
# 三、内部工具
# ==========================================================================
def _recalc_total(db, report_id: int) -> float:
    """重算报销单总额（只累加未软删的明细）。任何明细变更后都要调用。"""
    row = db.execute(
        "SELECT IFNULL(SUM(amount),0) t FROM expense_items "
        "WHERE report_id=? AND is_active=1", (report_id,)).fetchone()
    total = float(row["t"] or 0)
    db.execute("UPDATE expense_reports SET total_amount=?,updated_at=? WHERE id=?",
               (total, now(), report_id))
    return total


def _cross_sources(db, report_id: int, fp: str, exclude_item_id: int | None = None) -> list:
    """跨报销单查找同指纹来源。

    两个 is_active=1 是硬要求：软删的明细（用户已删/财务已驳回）绝不能再
    拦住新单据，否则「删掉重录」会被自己拦死。
    """
    rows = db.execute(
        "SELECT i.id, i.report_id, i.occur_date, i.amount, i.ticket_type, "
        "i.merchant, i.invoice_code, i.invoice_no, i.trans_no, "
        "r.code AS report_code, r.applicant_id, r.status AS report_status, "
        "u.full_name AS applicant_name, u.username AS applicant_username "
        "FROM expense_items i "
        "JOIN expense_reports r ON r.id=i.report_id "
        "LEFT JOIN users u ON u.id=r.applicant_id "
        "WHERE i.ticket_fingerprint=? AND i.is_active=1 AND r.is_active=1 "
        "AND i.report_id!=? AND i.id!=? ORDER BY i.id",
        (fp, report_id, exclude_item_id or 0)).fetchall()
    return [dict(r) for r in rows]


def _within_conflicts(db, report_id: int, fp: str,
                      exclude_item_id: int | None = None) -> list:
    """同一报销单内的同指纹明细（用于硬拦截）。"""
    rows = db.execute(
        "SELECT id, occur_date, amount, merchant FROM expense_items "
        "WHERE report_id=? AND ticket_fingerprint=? AND is_active=1 AND id!=? "
        "ORDER BY id", (report_id, fp, exclude_item_id or 0)).fetchall()
    return [dict(r) for r in rows]


def _apply_cross_dup(db, item_id: int, report_id: int, fp: str) -> dict | None:
    """给单条明细打跨单重复标记。返回来源信息（无来源则返回 None）。"""
    srcs = _cross_sources(db, report_id, fp, item_id)
    if not srcs:
        db.execute("UPDATE expense_items SET dup_status='none',dup_of=NULL,"
                   "updated_at=? WHERE id=?", (now(), item_id))
        return None
    s = srcs[0]
    db.execute("UPDATE expense_items SET dup_status='suspected',dup_of=?,"
               "updated_at=? WHERE id=?", (s["id"], now(), item_id))
    return s


def _dup_of_info(src: dict | None) -> dict | None:
    """把来源明细整理成前端可直接展示的结构（单号 / 报销人 / 日期 / 金额）。"""
    if not src:
        return None
    return {
        "item_id": src.get("id"),
        "report_id": src.get("report_id"),
        "report_code": src.get("report_code"),
        "applicant_id": src.get("applicant_id"),
        "applicant_name": src.get("applicant_name") or src.get("applicant_username")
                          or ("#" + str(src.get("applicant_id"))),
        "occur_date": src.get("occur_date"),
        "amount": src.get("amount"),
        "ticket_type": src.get("ticket_type"),
        "merchant": src.get("merchant"),
    }


def _load_items(db, report_id: int) -> list:
    """读取报销单明细，附带重复来源信息（一条 JOIN 拿全，避免 N+1）。"""
    rows = db.execute(
        "SELECT i.*, "
        "src.occur_date AS dup_of_date, src.amount AS dup_of_amount, "
        "src.ticket_type AS dup_of_ticket_type, src.merchant AS dup_of_merchant, "
        "sr.code AS dup_of_report_code, sr.applicant_id AS dup_of_applicant_id, "
        "su.full_name AS dup_of_applicant_name, "
        "su.username AS dup_of_applicant_username "
        "FROM expense_items i "
        "LEFT JOIN expense_items src ON src.id=i.dup_of "
        "LEFT JOIN expense_reports sr ON sr.id=src.report_id "
        "LEFT JOIN users su ON su.id=sr.applicant_id "
        "WHERE i.report_id=? AND i.is_active=1 "
        "ORDER BY i.sort_order, i.id", (report_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        src = None
        if d.get("dup_of"):
            src = {"id": d["dup_of"],
                   "report_code": d.get("dup_of_report_code"),
                   "applicant_id": d.get("dup_of_applicant_id"),
                   "applicant_name": d.get("dup_of_applicant_name")
                                     or d.get("dup_of_applicant_username"),
                   "occur_date": d.get("dup_of_date"),
                   "amount": d.get("dup_of_amount"),
                   "ticket_type": d.get("dup_of_ticket_type"),
                   "merchant": d.get("dup_of_merchant")}
        dup_info = _dup_of_info(src)
        for k in ("dup_of_date", "dup_of_amount", "dup_of_ticket_type",
                  "dup_of_merchant", "dup_of_report_code", "dup_of_applicant_id",
                  "dup_of_applicant_name", "dup_of_applicant_username"):
            d.pop(k, None)
        d["slip_ids"] = jload(d.get("slip_ids"), [])
        d["dup_of_info"] = dup_info
        out.append(d)
    return out


def _scope_filter(u: dict) -> tuple[str, list]:
    """报销单可见范围。

    expense.view 的口径是「本人 + 组织内」：直接复用 M004 的三轴范围
    （scope_clause_org），组织树变化时自动跟随，不在这里另写一套部门判断。
    财务（expense.finance）需要跨组织视野来做复核，故放行全部。
    """
    if P.has_perm(u, "expense.finance") or P.scope_of(u) == "all":
        return "", []
    frag, args = P.scope_clause_org(u, org_unit_col="org_unit_id", alias="r")
    if not frag or frag == " AND 1=0":
        return " AND r.applicant_id=?", [u["id"]]
    return f" AND (r.applicant_id=?{frag})", [u["id"]] + args


def _get_report(db, rid: int, u: dict) -> dict:
    """取单并对可见性把关。看不见一律 404（不泄露单据存在性）。"""
    frag, args = _scope_filter(u)
    r = db.execute(
        f"SELECT r.*, u.full_name AS applicant_name, u.username AS applicant_username, "
        f"o.name AS org_name FROM expense_reports r "
        f"LEFT JOIN users u ON u.id=r.applicant_id "
        f"LEFT JOIN org_units o ON o.id=r.org_unit_id "
        f"WHERE r.id=? AND r.is_active=1{frag}", [rid] + args).fetchone()
    if not r:
        raise HTTPException(404, "报销单不存在或无权查看")
    return dict(r)


def _can_print(u: dict, r: dict) -> bool:
    """打印权限：审批完成后，本人或财务可打印。

    刻意不放开给所有人 —— 报销单是付款凭证，谁都能打印就意味着谁都能拿一张
    A4 去财务要钱，打印留痕（次数/人/时间）也就失去意义。
    """
    if r.get("status") not in ("approved", "paid"):
        return False
    return r.get("applicant_id") == u["id"] or P.has_perm(u, "expense.finance")


# ---- 金额转人民币大写（打印用） ----
_RMB_DIGITS = "零壹贰叁肆伍陆柒捌玖"
_RMB_UNITS = ("", "拾", "佰", "仟")


def _rmb_section(n: int) -> str:
    """0~9999 转大写（不含节权）。"""
    out = ""
    pending_zero = False
    s = str(n)
    for i, ch in enumerate(s):
        v = int(ch)
        pos = len(s) - 1 - i
        if v == 0:
            pending_zero = True
            continue
        if pending_zero and out:
            out += _RMB_DIGITS[0]
        pending_zero = False
        out += _RMB_DIGITS[v] + _RMB_UNITS[pos]
    return out


def rmb_upper(amount) -> str:
    """金额 → 人民币大写（支持到亿，精确到分）。

    纸质报销单必须有大写金额 —— 这是财务的老规矩，也是防篡改手段：
    阿拉伯数字加一笔就能改，大写改不动。
    """
    try:
        val = float(amount or 0)
    except (TypeError, ValueError, OverflowError):
        val = 0.0
    if val != val or val in (float("inf"), float("-inf")):
        val = 0.0
    cents = int(round(val * 100))
    if cents == 0:
        return "零元整"
    sign = "负" if cents < 0 else ""
    cents = abs(cents)
    yuan, jiao, fen = cents // 100, (cents % 100) // 10, cents % 10
    yi, rest = divmod(yuan, 100_000_000)
    wan, ge = divmod(rest, 10_000)

    out = ""
    if yi:
        out += _rmb_section(yi) + "亿"
    if wan:
        if yi and wan < 1000:
            out += _RMB_DIGITS[0]
        out += _rmb_section(wan) + "万"
    if ge:
        if (yi or wan) and ge < 1000:
            out += _RMB_DIGITS[0]
    if ge or not (yi or wan):
        out += (_rmb_section(ge) if ge else _RMB_DIGITS[0]) + "元"
    else:
        out += "元"

    if jiao == 0 and fen == 0:
        out += "整"
    else:
        if jiao:
            out += _RMB_DIGITS[jiao] + "角"
        elif fen:
            out += _RMB_DIGITS[0]
        if fen:
            out += _RMB_DIGITS[fen] + "分"
    return sign + out


# ==========================================================================
# 四、数据模型
# ==========================================================================
class ReportIn(BaseModel):
    period_from: str | None = None
    period_to: str | None = None
    org_unit_id: int | None = None
    reason: str | None = None


class ItemIn(BaseModel):
    category: str = "other"
    occur_date: str | None = None
    amount: float = 0
    ticket_type: str = "none"
    invoice_code: str | None = None
    invoice_no: str | None = None
    trans_no: str | None = None
    merchant: str | None = None
    title: str | None = None
    slip_ids: list[int] | None = None
    remark: str | None = None
    sort_order: int = 0


class DupAction(BaseModel):
    action: str = "confirm"          # confirm 核实为合理 / reject 驳回
    note: str | None = None


# ==========================================================================
# 五、枚举接口（前端唯一的中文来源）
# ==========================================================================
@router.get("/types")
def expense_types(u: dict = Depends(current_user)):
    """枚举字典：费用类型 / 票据类型 / 单据状态 / 重复状态 / 审批档位。

    前端一律从这里取，禁止在 JS 里硬编码中文 —— 否则加一个费用类型要改两处，
    且改了后端忘了改前端就会出现「前端显示 other 后端显示 其他」。
    """
    P.require(u, "expense.view")
    return {
        "categories": [{"value": k, "label": v} for k, v in EXPENSE_CATEGORIES.items()],
        "ticket_types": [{"value": k, "label": v} for k, v in TICKET_TYPES.items()],
        "statuses": [{"value": k, "label": v} for k, v in REPORT_STATUS.items()],
        "dup_statuses": [{"value": k, "label": v} for k, v in DUP_STATUS.items()],
        "approval_tiers": APPROVAL_TIERS,
    }


# ==========================================================================
# 六、报销单
# ==========================================================================
@router.get("/reports")
def list_reports(status: str | None = None, u: dict = Depends(current_user)):
    P.require(u, "expense.view")
    frag, args = _scope_filter(u)
    sql = ("SELECT r.*, u.full_name AS applicant_name, o.name AS org_name, "
           "(SELECT COUNT(1) FROM expense_items i WHERE i.report_id=r.id "
           " AND i.is_active=1 AND i.dup_status='suspected') AS dup_count, "
           "(SELECT COUNT(1) FROM expense_items i WHERE i.report_id=r.id "
           " AND i.is_active=1) AS item_count "
           "FROM expense_reports r LEFT JOIN users u ON u.id=r.applicant_id "
           "LEFT JOIN org_units o ON o.id=r.org_unit_id "
           "WHERE r.is_active=1")
    if status:
        sql += " AND r.status=?"
        args = [status] + args
    sql += " ORDER BY r.id DESC LIMIT 200"
    db = get_conn()
    rows = [dict(r) for r in db.execute(sql, args)]
    release_conn()
    return rows


@router.post("/reports")
def create_report(body: ReportIn, u: dict = Depends(current_user)):
    P.require(u, "expense.edit")
    db = get_conn()
    code = next_no("expense")
    oid = body.org_unit_id or u.get("org_unit_id") or u.get("current_org_unit_id")
    cur = db.execute(
        "INSERT INTO expense_reports(code,applicant_id,org_unit_id,period_from,"
        "period_to,total_amount,status,reason,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (code, u["id"], oid, body.period_from, body.period_to, 0, "draft",
         body.reason, now(), now()))
    rid = cur.lastrowid
    db.commit()
    r = db.execute("SELECT * FROM expense_reports WHERE id=?", (rid,)).fetchone()
    release_conn()
    audit(u, "expense", "create", "expense_report", rid, code,
          after=body.model_dump())
    return dict(r)


@router.get("/reports/{rid}")
def report_detail(rid: int, u: dict = Depends(current_user)):
    P.require(u, "expense.view")
    db = get_conn()
    r = _get_report(db, rid, u)
    r["items"] = _load_items(db, rid)
    release_conn()
    return r


@router.put("/reports/{rid}")
def update_report(rid: int, body: ReportIn, u: dict = Depends(current_user)):
    """编辑报销单（仅草稿、仅本人）。"""
    P.require(u, "expense.edit")
    db = get_conn()
    r = _get_report(db, rid, u)
    if r["applicant_id"] != u["id"] and not P.has_perm(u, "expense.finance"):
        release_conn()
        raise HTTPException(403, "只能编辑自己的报销单")
    if r["status"] != "draft":
        release_conn()
        raise HTTPException(400, "仅草稿状态可编辑")
    db.execute("UPDATE expense_reports SET period_from=?,period_to=?,"
               "org_unit_id=?,reason=?,updated_at=? WHERE id=?",
               (body.period_from, body.period_to,
                body.org_unit_id or r["org_unit_id"], body.reason, now(), rid))
    db.commit()
    audit(u, "expense", "update", "expense_report", rid, r["code"],
          after=body.model_dump())
    out = _get_report(db, rid, u)
    release_conn()
    return out


@router.delete("/reports/{rid}")
def delete_report(rid: int, u: dict = Depends(current_user)):
    """软删报销单（连同明细一起失效，且不参与后续查重）。"""
    P.require(u, "expense.edit")
    db = get_conn()
    r = _get_report(db, rid, u)
    if r["applicant_id"] != u["id"] and not P.has_perm(u, "expense.finance"):
        release_conn()
        raise HTTPException(403, "只能作废自己的报销单")
    if r["status"] in ("approving", "paid"):
        release_conn()
        raise HTTPException(400, "审批中或已支付的报销单不可作废")
    db.execute("UPDATE expense_reports SET is_active=0,status='void',updated_at=? "
               "WHERE id=?", (now(), rid))
    db.execute("UPDATE expense_items SET is_active=0,updated_at=? WHERE report_id=?",
               (now(), rid))
    db.commit()
    release_conn()
    audit(u, "expense", "void", "expense_report", rid, r["code"])
    return {"ok": True, "id": rid}


# ==========================================================================
# 七、费用明细（含「边填边查重」）
# ==========================================================================
@router.post("/reports/{rid}/items")
def add_item(rid: int, body: ItemIn, u: dict = Depends(current_user)):
    """新增明细。单据内重复直接拦截；跨单据重复标记疑似并返回来源信息。

    返回体刻意带上 dup_of_info：前端拿到就能立刻红字提示「这张票已在
    某某的 EX2026xx-0001 里报过」，实现边填边提醒，而不是等提交才发现。
    """
    P.require(u, "expense.edit")
    db = get_conn()
    r = _get_report(db, rid, u)
    if r["applicant_id"] != u["id"]:
        release_conn()
        raise HTTPException(403, "只能编辑自己的报销单")
    if r["status"] != "draft":
        release_conn()
        raise HTTPException(400, "仅草稿状态可添加明细")
    if body.category not in EXPENSE_CATEGORIES:
        release_conn()
        raise HTTPException(400, f"未知费用类型 {body.category}")
    if body.ticket_type not in TICKET_TYPES:
        release_conn()
        raise HTTPException(400, f"未知票据类型 {body.ticket_type}")
    if float(body.amount or 0) <= 0:
        release_conn()
        raise HTTPException(400, "金额必须大于 0")

    fp = ticket_fingerprint(body.model_dump())
    conflicts = _within_conflicts(db, rid, fp)
    if conflicts:
        release_conn()
        ids = "、".join("#%d" % c["id"] for c in conflicts)
        raise HTTPException(
            400, f"该票据在本单内重复（与明细 {ids} 相同），请先删除重复项再保存")

    cur = db.execute(
        "INSERT INTO expense_items(report_id,category,occur_date,amount,ticket_type,"
        "invoice_code,invoice_no,trans_no,merchant,title,slip_ids,"
        "ticket_fingerprint,remark,sort_order,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (rid, body.category, body.occur_date, float(body.amount), body.ticket_type,
         body.invoice_code, body.invoice_no, body.trans_no, body.merchant,
         body.title, jdump(body.slip_ids or []), fp, body.remark,
         body.sort_order, now(), now()))
    iid = cur.lastrowid
    db.commit()
    src = _apply_cross_dup(db, iid, rid, fp)
    _recalc_total(db, rid)
    db.commit()
    row = db.execute("SELECT * FROM expense_items WHERE id=?", (iid,)).fetchone()
    release_conn()
    out = dict(row)
    out["slip_ids"] = jload(out.get("slip_ids"), [])
    out["dup_of_info"] = _dup_of_info(src)
    return out


@router.put("/items/{iid}")
def update_item(iid: int, body: ItemIn, u: dict = Depends(current_user)):
    P.require(u, "expense.edit")
    db = get_conn()
    it = db.execute("SELECT * FROM expense_items WHERE id=? AND is_active=1",
                    (iid,)).fetchone()
    if not it:
        release_conn()
        raise HTTPException(404, "明细不存在")
    r = _get_report(db, it["report_id"], u)
    if r["applicant_id"] != u["id"]:
        release_conn()
        raise HTTPException(403, "只能编辑自己的报销单")
    if r["status"] != "draft":
        release_conn()
        raise HTTPException(400, "仅草稿状态可修改明细")
    fp = ticket_fingerprint(body.model_dump())
    conflicts = _within_conflicts(db, it["report_id"], fp, iid)
    if conflicts:
        release_conn()
        ids = "、".join("#%d" % c["id"] for c in conflicts)
        raise HTTPException(400, f"该票据在本单内重复（与明细 {ids} 相同）")
    db.execute(
        "UPDATE expense_items SET category=?,occur_date=?,amount=?,ticket_type=?,"
        "invoice_code=?,invoice_no=?,trans_no=?,merchant=?,title=?,slip_ids=?,"
        "ticket_fingerprint=?,remark=?,sort_order=?,updated_at=? WHERE id=?",
        (body.category, body.occur_date, float(body.amount), body.ticket_type,
         body.invoice_code, body.invoice_no, body.trans_no, body.merchant,
         body.title, jdump(body.slip_ids or []), fp, body.remark,
         body.sort_order, now(), iid))
    db.commit()
    src = _apply_cross_dup(db, iid, it["report_id"], fp)
    _recalc_total(db, it["report_id"])
    db.commit()
    row = db.execute("SELECT * FROM expense_items WHERE id=?", (iid,)).fetchone()
    release_conn()
    out = dict(row)
    out["slip_ids"] = jload(out.get("slip_ids"), [])
    out["dup_of_info"] = _dup_of_info(src)
    return out


@router.delete("/items/{iid}")
def delete_item(iid: int, u: dict = Depends(current_user)):
    """软删明细并同步总额（软删后不再参与查重匹配）。"""
    P.require(u, "expense.edit")
    db = get_conn()
    it = db.execute("SELECT * FROM expense_items WHERE id=? AND is_active=1",
                    (iid,)).fetchone()
    if not it:
        release_conn()
        raise HTTPException(404, "明细不存在")
    r = _get_report(db, it["report_id"], u)
    if r["applicant_id"] != u["id"]:
        release_conn()
        raise HTTPException(403, "只能删除自己报销单的明细")
    if r["status"] != "draft":
        release_conn()
        raise HTTPException(400, "仅草稿状态可删除明细")
    db.execute("UPDATE expense_items SET is_active=0,updated_at=? WHERE id=?",
               (now(), iid))
    db.commit()
    total = _recalc_total(db, it["report_id"])
    db.commit()
    release_conn()
    return {"ok": True, "id": iid, "total_amount": total}


# ==========================================================================
# 八、提交审批（接 M005 引擎）
# ==========================================================================
@router.post("/reports/{rid}/submit")
def submit_report(rid: int, u: dict = Depends(current_user)):
    """提交审批：单据内查重 → 跨单据标疑似 → 起 EXPENSE 流程实例。

    审批流向由金额决定（见 EXPENSE_DEFINITION），本函数只把 total 塞进
    form 交给引擎，流程怎么分支是流程定义的事 —— 改审批档位不需要改代码。
    """
    P.require(u, "expense.edit")
    db = get_conn()
    r = _get_report(db, rid, u)
    if r["applicant_id"] != u["id"]:
        release_conn()
        raise HTTPException(403, "只能提交自己的报销单")
    if r["status"] != "draft":
        release_conn()
        raise HTTPException(400, "仅草稿状态可提交")

    items = _load_items(db, rid)
    if not items:
        release_conn()
        raise HTTPException(400, "请至少添加一条费用明细")

    # 单据内重复：硬拦截（自己单子里同票必是操作失误）
    seen: dict = {}
    for it in items:
        fp = it.get("ticket_fingerprint")
        if fp in seen:
            release_conn()
            raise HTTPException(
                400, f"明细 #{seen[fp]} 与 #{it['id']} 为同一张票据，"
                     f"请先删除重复项再提交")
        seen[fp] = it["id"]

    # 跨单据重复：只标记，不拦截
    for it in items:
        _apply_cross_dup(db, it["id"], rid, it.get("ticket_fingerprint"))
    total = _recalc_total(db, rid)
    db.commit()
    release_conn()

    db = get_conn()
    d = db.execute("SELECT id FROM workflow_defs WHERE code='EXPENSE' AND is_active=1"
                   ).fetchone()
    release_conn()
    if not d:
        raise HTTPException(500, "报销审批流程未配置，请联系管理员")

    db = get_conn()
    db.execute("UPDATE expense_reports SET status='submitted',submitted_at=?,"
               "updated_at=? WHERE id=?", (now(), now(), rid))
    db.commit()
    release_conn()
    # 必须先置 approving 再起流程：极少数流程（如某些节点无人可审）会在
    # start_instance 内部直接走到终点并触发回调写成 approved，若之后才写
    # approving，就会把审批结果盖回「审批中」，单据永远停在中间态。
    db = get_conn()
    db.execute("UPDATE expense_reports SET status='approving',updated_at=? WHERE id=?",
               (now(), rid))
    db.commit()
    release_conn()
    title = f"报销申请 · {r['code']} · {total:.2f} 元"
    inst = WF.start_instance(d["id"], title, u["id"],
                             {"amount": total}, "expense", rid)
    audit(u, "expense", "submit", "expense_report", rid, r["code"],
          after={"total": total, "instance_id": inst["id"]})
    return {"ok": True, "id": rid, "total_amount": total,
            "instance_id": inst["id"]}


# ==========================================================================
# 九、票据影像（复用 M109 的 slips 存储）
# ==========================================================================
@router.post("/slips/upload")
async def upload_slips(files: list[UploadFile] = File(...),
                       u: dict = Depends(current_user)):
    """上传票据影像。落盘/入库走 core/services.save_slip，与 M109 共用一套
    data/slips + report_slips，图片由 /api/report/slips/{id}/file 提供。
    """
    P.require(u, "expense.edit")
    out = []
    for f in files:
        try:
            out.append(save_slip(f.filename or "", f.file, u["id"], memo="expense"))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    return out


# ==========================================================================
# 十、财务复核台：疑似重复清单 + 人工销案
# ==========================================================================
@router.get("/dups")
def list_dups(status: str = "suspected", u: dict = Depends(current_user)):
    """疑似重复票据清单（财务专用）。

    status=suspected（默认）列待核实；confirmed_ok 列已销案，便于回溯
    「财务当时为什么放行」。
    """
    P.require(u, "expense.finance")
    db = get_conn()
    rows = [dict(r) for r in db.execute(
        "SELECT i.id, i.report_id, i.category, i.occur_date, i.amount, "
        "i.ticket_type, i.merchant, i.invoice_code, i.invoice_no, i.trans_no, "
        "i.ticket_fingerprint, i.dup_status, i.dup_of, "
        "r.code AS report_code, r.status AS report_status, r.applicant_id, "
        "u.full_name AS applicant_name, u.username AS applicant_username, "
        "src.occur_date AS src_date, src.amount AS src_amount, "
        "sr.code AS src_report_code, sr.applicant_id AS src_applicant_id, "
        "su.full_name AS src_applicant_name, su.username AS src_applicant_username "
        "FROM expense_items i "
        "JOIN expense_reports r ON r.id=i.report_id "
        "LEFT JOIN users u ON u.id=r.applicant_id "
        "LEFT JOIN expense_items src ON src.id=i.dup_of "
        "LEFT JOIN expense_reports sr ON sr.id=src.report_id "
        "LEFT JOIN users su ON su.id=sr.applicant_id "
        "WHERE i.is_active=1 AND r.is_active=1 AND i.dup_status=? "
        "ORDER BY i.id DESC LIMIT 200", (status,))]
    release_conn()
    for d in rows:
        d["dup_of_info"] = _dup_of_info({
            "id": d.get("dup_of"),
            "report_code": d.get("src_report_code"),
            "applicant_id": d.get("src_applicant_id"),
            "applicant_name": d.get("src_applicant_name"),
            "applicant_username": d.get("src_applicant_username"),
            "occur_date": d.get("src_date"),
            "amount": d.get("src_amount"),
            "ticket_type": d.get("ticket_type"),
            "merchant": d.get("merchant"),
        }) if d.get("dup_of") else None
        for k in ("src_date", "src_amount", "src_report_code",
                  "src_applicant_id", "src_applicant_name", "src_applicant_username"):
            d.pop(k, None)
    return rows


@router.post("/items/{iid}/dup")
def resolve_dup(iid: int, body: DupAction, u: dict = Depends(current_user)):
    """财务对疑似重复票据销案：confirm 标记已核实非重复；reject 驳回该明细。

    reject 采用软删（而不是留着标红）—— 财务判定这张票不该报，留它在单子里
    只会让总额与后续打印都带上争议项。软删后它也不再参与别人的查重匹配。
    """
    P.require(u, "expense.finance")
    db = get_conn()
    it = db.execute("SELECT * FROM expense_items WHERE id=? AND is_active=1",
                    (iid,)).fetchone()
    if not it:
        release_conn()
        raise HTTPException(404, "明细不存在")
    if body.action == "confirm":
        db.execute("UPDATE expense_items SET dup_status='confirmed_ok',"
                   "updated_at=? WHERE id=?", (now(), iid))
        db.commit()
        audit(u, "expense", "dup_confirm", "expense_item", iid, "",
              after={"note": body.note})
    elif body.action == "reject":
        db.execute("UPDATE expense_items SET is_active=0,updated_at=? WHERE id=?",
                   (now(), iid))
        db.commit()
        total = _recalc_total(db, it["report_id"])
        db.commit()
        audit(u, "expense", "dup_reject", "expense_item", iid, "",
              after={"note": body.note, "total": total})
    else:
        release_conn()
        raise HTTPException(400, "action 只能是 confirm 或 reject")
    row = db.execute("SELECT * FROM expense_items WHERE id=?", (iid,)).fetchone()
    release_conn()
    return dict(row) if row else {"ok": True, "id": iid}


# ==========================================================================
# 十一、打印：数据源 + 打印留痕
# ==========================================================================
@router.get("/reports/{rid}/print-data")
def print_data(rid: int, u: dict = Depends(current_user)):
    """A4 打印数据源（排版全部由前端 @media print 完成，后端只给数据）。"""
    P.require(u, "expense.view")
    db = get_conn()
    r = _get_report(db, rid, u)
    if not _can_print(u, r):
        release_conn()
        raise HTTPException(403, "仅审批完成后本人或财务可打印")
    items = _load_items(db, rid)
    for it in items:
        it["slips"] = [{"id": sid, "url": f"/api/report/slips/{sid}/file"}
                       for sid in (it.get("slip_ids") or [])]
    release_conn()
    r["items"] = items
    r["amount_upper"] = rmb_upper(r["total_amount"])
    r["company"] = effective_company()
    r["applicant_name"] = r.get("applicant_name") or r.get("applicant_username") or ""
    r["printed_at"] = now()
    return r


@router.post("/reports/{rid}/print")
def mark_print(rid: int, u: dict = Depends(current_user)):
    """打印留痕：记录次数/人/时间。

    防舞弊的最后一环 —— 报销单可能被重复打印去多次报账，有次数与最后打印人，
    财务一眼能看出「这张单第 3 次被打印，且不是本人在打」。
    """
    P.require(u, "expense.view")
    db = get_conn()
    r = _get_report(db, rid, u)
    if not _can_print(u, r):
        release_conn()
        raise HTTPException(403, "仅审批完成后本人或财务可打印")
    db.execute("UPDATE expense_reports SET print_count=print_count+1,"
               "last_print_by=?,last_print_at=?,updated_at=? WHERE id=?",
               (u["id"], now(), now(), rid))
    db.commit()
    row = db.execute("SELECT print_count,last_print_by,last_print_at "
                     "FROM expense_reports WHERE id=?", (rid,)).fetchone()
    release_conn()
    audit(u, "expense", "print", "expense_report", rid, r["code"],
          after={"print_count": row["print_count"]})
    return {"ok": True, "print_count": row["print_count"],
            "last_print_by": row["last_print_by"],
            "last_print_at": row["last_print_at"]}


# ==========================================================================
# 十二、支付登记
# ==========================================================================
@router.post("/reports/{rid}/pay")
def pay_report(rid: int, u: dict = Depends(current_user)):
    """支付登记：已通过 → 已支付。"""
    P.require(u, "expense.pay")
    db = get_conn()
    r = _get_report(db, rid, u)
    if r["status"] != "approved":
        release_conn()
        raise HTTPException(400, "仅已通过的报销单可登记支付")
    db.execute("UPDATE expense_reports SET status='paid',paid_at=?,paid_by=?,"
               "updated_at=? WHERE id=?", (now(), u["id"], now(), rid))
    db.commit()
    release_conn()
    audit(u, "expense", "pay", "expense_report", rid, r["code"])
    return {"ok": True, "id": rid, "status": "paid"}


# ==========================================================================
# 十三、审批回调 + 流程定义 + 种子
# ==========================================================================
def _expense_callback(biz_id: int | None, decision: str, inst_code: str) -> None:
    """审批结束后回写报销单状态（M005 引擎在实例结束时调用）。

    签名必须是 (biz_id, decision, inst_code) 且与 _run_callback 的调用一致。

    **幂等**（人工重试 / 引擎重入的前提）：
    - 已是目标状态 → 直接跳过，不写库、不刷 updated_at（否则每次重试都改一次
      时间戳，审计日志看着像有人动过单据）。
    - 已支付 / 已作废是终态，不允许后续回调再把它改回 approved —— 钱都付了
      才发现流程还在跑，是流程配置问题，不是把状态改回去能解决的。
    - 单据不存在则抛错：引擎会记 callback_error，比静默吞掉强 —— 审批流指向
      一张不存在的报销单，说明数据与流程脱节，必须让人看见。
    """
    if biz_id is None:
        return
    mapping = {"approved": "approved", "rejected": "rejected", "canceled": "void"}
    target = mapping.get(decision)
    if not target:
        return
    db = get_conn()
    r = db.execute("SELECT status FROM expense_reports WHERE id=?", (biz_id,)).fetchone()
    if not r:
        release_conn()
        raise ValueError(f"报销单 #{biz_id} 不存在，审批回调无法回写状态")
    cur = r["status"]
    if cur == target or cur in ("paid", "void"):
        # 幂等跳过 / 终态保护：不做无意义的写入
        release_conn()
        return
    db.execute("UPDATE expense_reports SET status=?,updated_at=? WHERE id=?",
               (target, now(), biz_id))
    db.commit()
    release_conn()


# 注册回调：业务模块只负责「声明结束后干什么」，流程怎么走交给引擎
WF.register_callback("expense", _expense_callback)


EXPENSE_DEFINITION = {
    "nodes": [
        {"id": "start", "type": "start"},
        {"id": "mgr", "type": "approval", "name": "部门主管审批", "mode": "or",
         "assignee": {"role": "manager"}},
        {"id": "fin", "type": "approval", "name": "财务复核", "mode": "or",
         "assignee": {"role": "finance"}},
        {"id": "gm", "type": "approval", "name": "总经理批准", "mode": "or",
         "assignee": {"role": "gm"}},
        {"id": "end", "type": "end"},
    ],
    # 边的顺序 = 判定顺序（引擎取第一条条件命中的边），高档必须先写。
    # 三档全部走「或签」：一级审批里任一有权限的人批准即可 —— 报销是高频短流程，
    # 用会签会让「主管休假」直接卡住全部门报销。若某档确需多方共同背书（如大额
    # 招待费），把该节点 mode 改成 and 即可，无需改代码。
    "edges": [
        {"from": "start", "to": "mgr"},
        # ≥1000 先进财务，再由财务节点按 ≥10000 决定是否上总经理 ——
        # 这样 15000 的走向是 主管→财务→总经理，而不是跳过财务直达总经理。
        {"from": "mgr", "to": "fin", "condition": "amount>=1000"},
        {"from": "mgr", "to": "end"},
        {"from": "fin", "to": "gm", "condition": "amount>=10000"},
        {"from": "fin", "to": "end"},
        {"from": "gm", "to": "end"},
    ],
}


def seed_expense() -> dict:
    """灌报销演示数据（幂等）。

    刻意**不**在种子里起审批实例：种子数据会在每次启动时补齐，若顺手起流程
    实例，审批中心会被一堆永不被处理的演示待办淹没。这里改为种 3 张分属
    三个金额档的草稿单（800/5000/15000），用户登录后提交即可逐一看到
    「主管一审 / 主管→财务 / 主管→财务→总经理」三条真实流向。
    """
    # 启动自检：成环的报销流程会让每一笔报销都走到世界尽头，
    # 宁可启动时报错，也不要上线后才发现。
    WF.validate_definition(EXPENSE_DEFINITION)
    db = get_conn()
    db.execute(
        "INSERT OR IGNORE INTO workflow_defs(code,name,description,definition,"
        "is_active,created_at,updated_at) VALUES(?,?,?,?,1,?,?)",
        ("EXPENSE", "报销审批",
         "员工报销：部门主管审批；≥1000 加财务复核；≥10000 再加总经理批准",
         jdump(EXPENSE_DEFINITION), now(), now()))
    db.execute("UPDATE workflow_defs SET definition=?,is_active=1,updated_at=? "
               "WHERE code='EXPENSE'", (jdump(EXPENSE_DEFINITION), now()))
    db.commit()

    users = [dict(r) for r in db.execute(
        "SELECT id, org_unit_id FROM users WHERE is_active=1 AND role!='admin' "
        "ORDER BY id LIMIT 3").fetchall()]
    made = 0
    if len(users) >= 2:
        if db.execute("SELECT 1 FROM expense_reports WHERE code LIKE 'EX-DEMO-%'"
                      ).fetchone():
            release_conn()
            return {"expense_defs": 1, "expense_reports": made}
        demo = [
            ("EX-DEMO-0001", users[0]["id"], users[0]["org_unit_id"],
             "draft", 800.0),
            ("EX-DEMO-0002", users[1]["id"], users[1]["org_unit_id"],
             "draft", 5000.0),
            ("EX-DEMO-0003", users[0]["id"], users[0]["org_unit_id"],
             "draft", 15000.0),
        ]
        for code, uid, oid, st, amt in demo:
            db.execute(
                "INSERT INTO expense_reports(code,applicant_id,org_unit_id,"
                "period_from,period_to,total_amount,status,reason,created_at,"
                "updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (code, uid, oid, None, None, amt, st, "演示数据", now(), now()))
            made += 1
        db.commit()
    release_conn()
    return {"expense_defs": 1, "expense_reports": made}
