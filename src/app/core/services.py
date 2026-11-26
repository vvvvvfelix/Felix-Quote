"""通用领域服务 — 审计、时间线、待办、状态机、单号。

所有模块共用，避免各写各的。多站点协作里，「谁改了什么」和
「这张单现在在谁手上」是刚需，不是加分项。
"""
import os
import sys
import threading
from datetime import datetime, timedelta

from ..database import BASE_DIR
from .db import get_conn, jdump, jload, now, release_conn

# ==========================================================================
# 一、单号服务
# ==========================================================================
EMP_NO_PREFIX = "WV-EMP-"


def next_emp_no() -> str:
    """生成下一个员工工号 —— 系统强制生成，用户不可自选、不可编辑。

    序号取已存在的最大工号 +1，因此离职员工的工号不会被回收复用
    （人还在 users 表里，序号自然不倒退）。
    """
    db = get_conn()
    n = db.execute("SELECT IFNULL(MAX(CAST(SUBSTR(emp_no,8) AS INTEGER)),0) m "
                   "FROM users WHERE emp_no LIKE ?",
                   (EMP_NO_PREFIX + "%",)).fetchone()["m"] or 0
    return f"{EMP_NO_PREFIX}{int(n) + 1:04d}"


# 配置化规则：新增单据类型只改这里
NUMBER_RULES = {
    "inquiry":     {"prefix": "INQ", "mode": "ymd", "seq": 3, "desc": "询盘单"},
    "task":        {"prefix": "TK",  "mode": "ymd", "seq": 4, "desc": "待办"},
    "order":       {"prefix": "SO",  "mode": "y",   "seq": 4, "desc": "销售订单"},
    "purchase":    {"prefix": "PO",  "mode": "y",   "seq": 4, "desc": "采购单"},
    "shipment":    {"prefix": "SH",  "mode": "y",   "seq": 4, "desc": "出货单"},
    "receivable":  {"prefix": "AR",  "mode": "ym",  "seq": 4, "desc": "应收单"},
    "quotation":   {"prefix": "WGQ", "mode": "y",   "seq": 4, "desc": "报价单"},
    "workflow":    {"prefix": "WF",  "mode": "ymd", "seq": 3, "desc": "审批实例"},
    "leave":       {"prefix": "LV",  "mode": "ymd", "seq": 3, "desc": "请假单"},
    "expense":     {"prefix": "EX",  "mode": "ym",  "seq": 4, "desc": "报销单"},
}

_seq_lock = threading.Lock()


def next_no(kind: str, when: str | None = None) -> str:
    """生成单号。并发安全：进程内锁 + 库内唯一约束兜底。

    INQ20260720-001 / SO2026-0001 / AR202607-0001
    """
    rule = NUMBER_RULES.get(kind)
    if not rule:
        raise ValueError(f"未知单号类型 {kind}")
    if when:
        try:
            d = datetime.strptime(when, "%Y-%m-%d")
        except (TypeError, ValueError):
            d = datetime.now()
    else:
        d = datetime.now()
    ymd = d.strftime("%Y%m%d")
    ym = d.strftime("%Y%m")
    y = d.strftime("%Y")
    stem = {"ymd": ymd, "ym": ym, "y": y}[rule["mode"]]
    prefix, seq = rule["prefix"], rule["seq"]
    base = f"{prefix}{stem}-"

    table = {"inquiry": "inquiries", "order": "orders", "purchase": "purchase_orders",
             "shipment": "shipments", "receivable": "receivables",
             "quotation": "quotations"}.get(kind)
    col = {"inquiry": "inquiry_no", "order": "order_no", "purchase": "po_no",
           "shipment": "shipment_no", "receivable": "receivable_no",
           "quotation": "quote_no"}.get(kind)

    with _seq_lock:
        db = get_conn()
        last = 0
        if table and col:
            row = db.execute(
                f"SELECT IFNULL(MAX(CAST(SUBSTR({col},? ) AS INTEGER)),0) m "
                f"FROM {table} WHERE {col} LIKE ?",
                (len(base) + 1, base + "%")).fetchone()
            last = int(row["m"] or 0)
        else:
            # 无独立主表的单号（如待办）用 settings 计数，避免与业务表耦合
            key = f"seq:{kind}:{stem}"
            r2 = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            last = int(r2["value"]) if r2 and r2["value"] else 0
        n = last + 1
        no = base + str(n).zfill(seq)
        if not (table and col):
            db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
                       (f"seq:{kind}:{stem}", str(n)))
            db.commit()
    return no


# ==========================================================================
# 一之二、票据影像归档（M109 建立，M071 复用同一套）
# ==========================================================================
# 票据影像（水单/发票拍照）是全系统唯一的「附件」资产：M109 用来排版打印，
# M071 用来挂在费用明细上做凭证。若各模块自建目录与表，同一张水单会被存两遍、
# 归档时找不到北。故把落盘 + 入库收敛到这里，两个模块的上传端点都调用它，
# 物理存储（data/slips）与元数据表（report_slips）始终只有一套。
SLIPS_DIR = os.path.join(BASE_DIR, "data", "slips")


def save_slip(filename: str, src, created_by: int | None = None,
              memo: str = "", ext_allow: tuple = (".jpg", ".jpeg", ".png", ".webp")
              ) -> dict:
    """把一个上传的票据影像写入 data/slips 与 report_slips 表。

    src 是拥有 .read() 的文件对象（UploadFile.file 或已打开的二进制流）。
    返回 {"id","filename","url","size"}；url 指向 M109 的图片服务端点，
    因此调用方（如 M071）不需要自己再实现一套文件下载。

    刻意不做图片压缩/缩放：报销凭证必须保留原始像素以便税务查验，
    压缩属于「为了省空间牺牲凭证效力」，这里不换。
    """
    import shutil
    from datetime import datetime as _dt

    os.makedirs(SLIPS_DIR, exist_ok=True)
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ext_allow:
        raise ValueError(f"仅支持图片：{filename}")
    name = f"{_dt.now().strftime('%Y%m%d%H%M%S')}_{uuid_hex8()}{ext}"
    dest = os.path.join(SLIPS_DIR, name)
    with open(dest, "wb") as fh:
        shutil.copyfileobj(src, fh)
    size = os.path.getsize(dest)
    db = get_conn()
    db.execute(
        "INSERT INTO report_slips(filename,stored_path,size,memo,mask_json,"
        "created_by,created_at) VALUES(?,?,?,?,?,?,?)",
        (filename or name, dest, size, memo, "[]", created_by, now()))
    db.commit()
    sid = db.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    return {"id": sid, "filename": filename or name, "size": size,
            "url": f"/api/report/slips/{sid}/file"}


def uuid_hex8() -> str:
    """短随机串，用于上传文件名去重（避免同名文件互相覆盖）。"""
    import uuid
    return uuid.uuid4().hex[:8]


# ==========================================================================
# 二、审计日志
# ==========================================================================
def _log_exc(where: str) -> None:
    """把被吞掉的异常写到服务日志。

    曾经 create_task 里用 `except Exception: pass`，结果一个 KeyError
    让所有跨部门待办静默失效 —— 派活功能看着正常，实则一条没存。
    审计/时间线这类「不能影响主业务」的地方可以吞异常，但必须留痕。
    """
    try:
        import traceback
        import sys
        msg = f"[{where}] {traceback.format_exc()}"
        with open(os.path.join(BASE_DIR, "data", "service.log"), "a",
                  encoding="utf-8") as f:
            f.write(msg + "\n")
        print(msg, file=sys.stderr)
    except Exception:
        pass


def log_warn(tag: str, msg: str) -> None:
    """把「健康/告警信号」写到服务日志（data/service.log）。

    与 _log_exc 的区别：_log_exc 记的是「被吞掉的异常栈」，这里记的是
    「系统仍可运行但可能需要人介入」的信号（如流程某节点无人可审 → 自动通过、
    启动期体检发现流程定义缺审批人）。

    刻意的两条边界：
    - 不写审计日志（audit_logs）：健康信号不是操作留痕，写进去会淹没真正的审计。
    - 不抛异常：告警绝不能影响主业务（例如体检时 settings 表尚未就绪也应静默跳过）。
    """
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(os.path.join(BASE_DIR, "data", "service.log"), "a", encoding="utf-8") as f:
            f.write(f"[{ts}] [WARN] [{tag}] {msg}\n")
        print(f"[WARN] [{tag}] {msg}", file=sys.stderr)
    except Exception:
        pass


def audit(user: dict | None, module: str, action: str,
          target_type: str = "", target_id: int | None = None,
          target_label: str = "", before: dict | None = None,
          after: dict | None = None, ip: str = "") -> None:
    """记录操作留痕。多站点协作时用于追责与对账。

    审计失败绝不能影响主业务，故吞掉异常（但会写日志）。
    """
    try:
        db = get_conn()
        db.execute(
            "INSERT INTO audit_logs(user_id,username,module,action,target_type,"
            "target_id,target_label,before_json,after_json,ip,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (user.get("id") if user else None, (user or {}).get("username", ""),
             module, action, target_type, target_id, target_label,
             jdump(before) if before else None,
             jdump(after) if after else None, ip, now()))
        db.commit()
    except Exception:
        _log_exc("audit")


def diff_of(before: dict | None, after: dict | None, fields: list[str]) -> dict:
    """只记录真正变化的字段，避免审计日志被无意义内容淹没。"""
    if not before or not after:
        return after or {}
    return {k: after.get(k) for k in fields
            if before.get(k) != after.get(k)}


# ==========================================================================
# 三、活动时间线
# ==========================================================================
def log_activity(biz_type: str, biz_id: int, action: str,
                 user: dict | None = None, content: str = "",
                 from_status: str | None = None, to_status: str | None = None) -> None:
    """写入单据时间线。回答「这张单现在在谁手上 / 发生过什么」。"""
    try:
        db = get_conn()
        db.execute(
            "INSERT INTO activities(biz_type,biz_id,action,from_status,to_status,"
            "content,user_id,username,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (biz_type, biz_id, action, from_status, to_status, content,
             (user or {}).get("id"), (user or {}).get("username", ""), now()))
        db.commit()
    except Exception:
        _log_exc("log_activity")


def timeline(biz_type: str, biz_id: int, limit: int = 100) -> list:
    db = get_conn()
    rows = [dict(r) for r in db.execute(
        "SELECT * FROM activities WHERE biz_type=? AND biz_id=? "
        "ORDER BY id DESC LIMIT ?", (biz_type, biz_id, limit))]
    return rows


# ==========================================================================
# 四、待办任务
# ==========================================================================
TASK_LABEL = {
    "followup": "跟进客户",
    "tech_confirm": "技术参数确认",
    "approve": "价格审批",
    "purchase": "采购下单",
    "production": "生产进度",
    "ship": "安排出货",
    "receive": "确认收款",
    "tech_delivery": "确认交期",
}


def create_task(biz_type: str, biz_id: int, task_type: str, title: str,
                assignee_id: int | None = None, dept_id: int | None = None,
                due_at: str | None = None, detail: str = "",
                priority: str = "normal", created_by: int | None = None) -> int | None:
    """创建待办。同类型同单据未完成的只保留一条，避免刷屏。"""
    try:
        db = get_conn()
        exist = db.execute(
            "SELECT id FROM tasks WHERE biz_type=? AND biz_id=? AND task_type=? "
            "AND status='open'", (biz_type, biz_id, task_type)).fetchone()
        if exist:
            db.execute("UPDATE tasks SET title=?,detail=?,due_at=?,priority=? "
                       "WHERE id=?",
                       (title, detail, due_at, priority, exist["id"]))
            db.commit()
            return exist["id"]
        no = next_no("task")
        cur = db.execute(
            "INSERT INTO tasks(task_no,biz_type,biz_id,task_type,title,detail,"
            "assignee_id,dept_id,due_at,priority,status,created_by,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,'open',?,?)",
            (no, biz_type, biz_id, task_type, title, detail, assignee_id, dept_id,
             due_at, priority, created_by, now()))
        db.commit()
        return cur.lastrowid
    except Exception:
        _log_exc("create_task")
        return None


def close_task(biz_type: str, biz_id: int, task_type: str | None = None,
               result: str = "", done_by: int | None = None) -> int:
    db = get_conn()
    sql = ("UPDATE tasks SET status='done',result=?,done_at=?,done_by=? "
           "WHERE biz_type=? AND biz_id=? AND status='open'")
    args: list = [result, now(), done_by, biz_type, biz_id]
    if task_type:
        sql += " AND task_type=?"
        args.append(task_type)
    cur = db.execute(sql, args)
    db.commit()
    return cur.rowcount


def my_tasks(user: dict, limit: int = 50) -> list:
    """首页「今日待办」。跨部门场景下这是打开系统第一眼要看的东西。"""
    db = get_conn()
    scope = user.get("role") == "admin"
    sql = ("SELECT t.*, d.name AS dept_name FROM tasks t "
           "LEFT JOIN departments d ON d.id=t.dept_id "
           "WHERE t.status='open'")
    args: list = []
    if not scope:
        sql += " AND (t.assignee_id=? OR t.dept_id=?)"
        args += [user.get("id"), user.get("dept_id")]
    sql += " ORDER BY CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 "
    sql += "WHEN 'normal' THEN 2 ELSE 3 END, COALESCE(t.due_at,'9999') LIMIT ?"
    args.append(limit)
    rows = [dict(r) for r in db.execute(sql, args)]
    today = now()[:10]
    for r in rows:
        r["task_label"] = TASK_LABEL.get(r["task_type"], r["task_type"])
        r["is_overdue"] = bool(r.get("due_at") and r["due_at"][:10] < today)
        r["is_today"] = bool(r.get("due_at") and r["due_at"][:10] == today)
    return rows


def pending_workflow_tasks(user: dict, limit: int = 50) -> list:
    """审批待办 —— 首页「今日待办」的审批维度入口。

    与 my_tasks（跨部门派活）并列，二者合起来才是「打开系统第一眼看到的全部待办」。
    审批引擎表由 M005 建立，旧库若未迁移则静默返回空，绝不拖垮首页。
    """
    try:
        db = get_conn()
        rows = [dict(r) for r in db.execute(
            "SELECT t.id, t.instance_id, t.node_name, t.mode, t.status, "
            "i.code AS inst_code, i.title AS inst_title, i.biz_type, i.biz_id, "
            "i.applicant_id, i.status AS inst_status, i.started_at "
            "FROM workflow_tasks t JOIN workflow_instances i "
            "ON i.id=t.instance_id "
            "WHERE t.assignee_id=? AND t.status='pending' "
            "AND i.status='running' ORDER BY i.started_at, t.id LIMIT ?",
            (user.get("id"), limit))]
        for r in rows:
            r["kind"] = "approval"
            r["is_overdue"] = False
        return rows
    except Exception:
        return []


# ==========================================================================
# 五、状态机
# ==========================================================================
# 各单据的状态流转声明：from -> 允许到的 to
STATE_MACHINES = {
    "inquiry": {
        "new":          ["quoted", "archived"],
        "quoted":       ["negotiating", "won", "lost", "archived"],
        "negotiating":  ["won", "lost", "archived"],
        "won":          [],
        "lost":         ["archived"],
        "archived":     [],
    },
    "order": {
        "draft":        ["confirming", "canceled"],
        "confirming":   ["confirmed", "canceled"],
        "confirmed":    ["producing", "canceled"],
        "producing":    ["shipped", "canceled"],
        "shipped":      ["completed"],
        "completed":    [],
        "canceled":     [],
    },
    "purchase": {
        "draft":        ["sent", "closed"],
        "sent":         ["producing", "received", "closed"],
        "producing":    ["received", "closed"],
        "received":     ["closed"],
        "closed":       [],
    },
    "shipment": {
        "planned":      ["shipped"],
        "shipped":      ["arrived"],
        "arrived":      ["customs", "delivered"],
        "customs":      ["delivered"],
        "delivered":    [],
    },
    "receivable": {
        "open":         ["partial", "settled"],
        "partial":      ["settled"],
        "settled":      [],
        "overdue":      ["partial", "settled"],
    },
}

STATUS_LABEL = {
    "draft": "草稿", "new": "新询盘", "confirming": "待确认", "confirmed": "已确认",
    "producing": "生产中", "shipped": "已出货", "completed": "已完成",
    "canceled": "已取消", "quoted": "已报价", "negotiating": "洽谈中",
    "won": "已成交", "lost": "已流失", "archived": "已归档",
    "sent": "已发出", "received": "已收货", "closed": "已关闭",
    "planned": "计划中", "arrived": "已到港", "customs": "报关中",
    "delivered": "已送达", "open": "未收款", "partial": "部分收款",
    "settled": "已结清", "overdue": "已逾期",
}


def can_transition(biz: str, frm: str, to: str) -> bool:
    return to in STATE_MACHINES.get(biz, {}).get(frm, [])


def assert_transition(biz: str, frm: str, to: str) -> None:
    from fastapi import HTTPException
    if frm == to:
        return
    if not can_transition(biz, frm, to):
        allowed = STATE_MACHINES.get(biz, {}).get(frm, [])
        names = "、".join(STATUS_LABEL.get(x, x) for x in allowed) or "无"
        raise HTTPException(400, f"不能从「{STATUS_LABEL.get(frm, frm)}」"
                                f"直接变更为「{STATUS_LABEL.get(to, to)}」。"
                                f"当前允许：{names}")


def next_states(biz: str, frm: str) -> list:
    return [{"value": s, "label": STATUS_LABEL.get(s, s)}
            for s in STATE_MACHINES.get(biz, {}).get(frm, [])]


# ==========================================================================
# 六、乐观锁（并发编辑冲突）
# ==========================================================================
class ConflictError(Exception):
    def __init__(self, who: str, when: str):
        self.who = who
        self.when = when
        super().__init__(f"该单据已被 {who} 于 {when} 修改，请刷新后重试")


def check_version(db, table: str, row_id: int, expect: int | None) -> None:
    """版本号校验。不传 version 则跳过（单人使用时不受影响）。"""
    if expect is None:
        return
    row = db.execute(f"SELECT version FROM {table} WHERE id=?", (row_id,)).fetchone()
    if not row:
        raise ValueError("记录不存在")
    if int(row["version"] or 1) != int(expect):
        who = "他人"
        when = ""
        try:
            u = db.execute(
                f"SELECT username FROM users WHERE id=("
                f"SELECT updated_by FROM {table} WHERE id=?)", (row_id,)).fetchone()
            if u:
                who = u["username"]
        except Exception:
            pass
        r2 = db.execute(f"SELECT updated_at FROM {table} WHERE id=?", (row_id,)).fetchone()
        if r2:
            when = r2["updated_at"]
        raise ConflictError(who, when)


def bump_version(db, table: str, row_id: int, user_id: int | None = None) -> None:
    db.execute(f"UPDATE {table} SET version=version+1, updated_by=? WHERE id=?",
               (user_id, row_id))


# ==========================================================================
# 七、逾期检查（首页提醒用）
# ==========================================================================
def overdue_alerts(user: dict) -> list:
    """三类逾期：报价即将失效、应收未到账、待办超期。"""
    from .permissions import scope_clause
    out = []
    db = get_conn()
    today = now()[:10]
    try:
        frag, args = scope_clause(user, alias="q")
        rows = db.execute(
            "SELECT id,quote_no,customer_id,valid_until FROM quotations "
            "WHERE status IN ('draft','sent') AND valid_until IS NOT NULL "
            f"AND valid_until<=date('+7 day','localtime'){frag} LIMIT 20", args)
        for r in rows:
            out.append({"kind": "quote_expiring", "label": "报价即将到期",
                        "ref": r["quote_no"], "id": r["id"],
                        "due": r["valid_until"]})
    except Exception:
        pass
    try:
        frag, args = scope_clause(user, "owner_id", "dept_id", alias="r")
        rows = db.execute(
            "SELECT id,receivable_no,due_date,amount,received_amount,currency "
            "FROM receivables WHERE status IN ('open','partial','overdue') "
            f"AND due_date IS NOT NULL AND due_date<date('localtime'){frag} LIMIT 20",
            args)
        for r in rows:
            left = r["amount"] - (r["received_amount"] or 0)
            out.append({"kind": "receivable_overdue", "label": "应收逾期",
                        "ref": r["receivable_no"], "id": r["id"],
                        "due": r["due_date"],
                        "amount": round(left, 2), "currency": r["currency"]})
    except Exception:
        pass
    for t in my_tasks(user, 50):
        if t.get("is_overdue"):
            out.append({"kind": "task_overdue", "label": "待办超期",
                        "ref": t["title"], "id": t["id"], "due": t.get("due_at")})
    return out
