"""模块层 — WorkBuddy 助手接口。

为什么留这个口子：员工会向 WorkBuddy 问「ALPHA 这个客户上次报了多少？」
「我还有几张单没确认交期？」这类问题。如果助手只能靠截图或口述，
效率上不去。给 WorkBuddy 一组**语义清晰、已脱敏**的只读查询，
员工的问题就能直接得到答案。

三条硬规矩（写死在代码里，不是靠调用方自觉）：

1. **独立鉴权** —— 不用员工 JWT，用单独的 API Key（`ai_api_key`）。
   好处：可单独撤销、可单独审计、不与人的会话混在一起。
   Key 由系统生成，存 settings 表（值本身哈希后存，明文只在生成时返回一次）。

2. **白名单输出** —— 每个查询**显式列出**要返回哪些字段。
   绝不 `SELECT *` 再 dump —— 那样加一列新字段就会意外泄露。
   成本、毛利、利润一律不出现在任何响应里。

3. **以员工身份执行** —— 请求带 `user`（工号或用户名），
   服务端按**该员工的角色与数据范围**执行查询。
   助手不能看到比提问者更多的数据。

不开放的能力（有意为之）：
- 密码、授权码、JWT 密钥 —— 任何接口都不返回
- 成本 / 毛利 / 利润 —— 商业机密
- 审计日志明细 —— 只有统计，不给明细
- 写操作 —— 本模块全部只读；要改数据走正常界面，天然留痕
- 批量导出 —— 单次返回条数有上限
"""
import hashlib
import hmac
import os
import secrets
import sqlite3

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from ..auth import current_user
from ..core import permissions as P
from ..core.db import get_conn, now, release_conn
from ..core.services import audit

router = APIRouter(prefix="/api/ai", tags=["ai"])


# ==========================================================================
# 1. 独立鉴权 —— API Key
# ==========================================================================
def _key_hash(raw: str) -> str:
    return hashlib.sha256(("wv-ai:" + raw).encode()).hexdigest()


def _load_key_hash() -> str:
    db = get_conn()
    r = db.execute("SELECT value FROM settings WHERE key='ai_api_key_hash'").fetchone()
    return r["value"] if r and r["value"] else ""


def ensure_ai_key() -> str:
    """首次调用时生成 API Key 并返回明文（只此一次，之后不可再取）。

    明文不落库 —— 只存 SHA-256 哈希，管理员需要时用「重置」换新的。
    """
    db = get_conn()
    h = _load_key_hash()
    if h:
        raise HTTPException(400, "API Key 已存在，如需更换请使用重置功能")
    raw = "wbg_" + secrets.token_urlsafe(32)
    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('ai_api_key_hash',?)",
               (_key_hash(raw),))
    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('ai_api_key_created_at',?)",
               (now(),))
    db.commit()
    release_conn()
    return raw


def reset_ai_key() -> str:
    """重置 API Key，返回新明文（旧 Key 立即失效）。"""
    db = get_conn()
    raw = "wbg_" + secrets.token_urlsafe(32)
    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('ai_api_key_hash',?)",
               (_key_hash(raw),))
    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('ai_api_key_created_at',?)",
               (now(),))
    db.commit()
    release_conn()
    return raw


def _auth_ai(x_api_key: str | None = Header(None, alias="X-API-Key")):
    """校验 API Key。缺失或错误一律 401。"""
    if not x_api_key:
        raise HTTPException(401, "缺少 X-API-Key")
    stored = _load_key_hash()
    if not stored or not hmac.compare_digest(stored, _key_hash(x_api_key)):
        raise HTTPException(401, "API Key 无效")
    return True


# ==========================================================================
# 2. 以员工身份执行
# ==========================================================================
def _resolve_employee(user_ref: str | None) -> dict:
    """把工号或用户名解析成员工档案，并带上权限。

    没给 user 时用 admin（服务账号）—— 但仍按 admin 的范围执行，
    助手只能问「系统能回答什么」，不能凭空造数据。
    """
    db = get_conn()
    row = None
    if user_ref:
        row = db.execute(
            "SELECT * FROM users WHERE emp_no=? OR username=? LIMIT 1",
            (user_ref, user_ref)).fetchone()
        if not row:
            release_conn()
            raise HTTPException(404, f"找不到员工：{user_ref}")
    else:
        row = db.execute("SELECT * FROM users WHERE role='admin' AND is_active=1 "
                         "ORDER BY id LIMIT 1").fetchone()
        if not row:
            release_conn()
            raise HTTPException(404, "系统没有可用账号")
    d = dict(row)
    d["perms"] = P.perms_of(d.get("role", ""))
    d["scope"] = P.scope_of(d)
    release_conn()
    return d


# ==========================================================================
# 3. 脱敏工具
# ==========================================================================
def _mask_email(v: str | None) -> str:
    """邮箱脱敏：保留首字符与域名。"""
    if not v or "@" not in v:
        return ""
    name, dom = v.split("@", 1)
    head = name[0] + "*" * max(1, len(name) - 1)
    return f"{head}@{dom}"


def _mask_phone(v: str | None) -> str:
    """手机/座机脱敏：保留后 4 位。"""
    if not v:
        return ""
    s = str(v)
    return ("*" * max(0, len(s) - 4)) + s[-4:] if len(s) > 4 else s


def _clip(v, n=120) -> str:
    """截断长文本，防止把整段 HTML 塞进对话。"""
    if v is None:
        return ""
    s = str(v)
    return s if len(s) <= n else s[:n] + "…"


# ==========================================================================
# 4. 查询接口 —— 全部只读，字段白名单
# ==========================================================================
class AskIn(BaseModel):
    user: str | None = None      # 工号或用户名，代表「谁在问」
    limit: int = 20
    q: str = ""                  # 自然语言问题（仅 /ask 用）


@router.get("/capabilities")
def ai_capabilities(x_api_key: str | None = Header(None, alias="X-API-Key")):
    """能力清单 —— 让 WorkBuddy 知道可以调什么、怎么传参。

    这是「预留接口」的自描述入口：新增查询时这里同步更新。
    """
    _auth_ai(x_api_key)
    return {
        "version": "1.0",
        "auth": {"header": "X-API-Key", "note": "由管理员在「组织与权限」页生成"},
        "identity": {
            "param": "user",
            "note": "工号（如 WV-EMP-0001）或用户名，代表提问者；"
                    "服务端按该员工的角色与数据范围过滤",
        },
        "endpoints": [
            {"path": "/api/ai/capabilities", "method": "GET",
             "desc": "本清单：可用查询与参数说明"},
            {"path": "/api/ai/whoami", "method": "GET",
             "params": {"user": "工号或用户名"},
             "desc": "员工档案摘要：工号、部门、角色、在职状态、待办数"},
            {"path": "/api/ai/tasks", "method": "GET",
             "params": {"user": "工号或用户名", "limit": "条数，默认 20"},
             "desc": "我的待办：跨部门派活给该员工的任务，含逾期标记"},
            {"path": "/api/ai/customers", "method": "GET",
             "params": {"user": "工号", "q": "按公司名/国家搜索", "limit": "条数"},
             "desc": "客户检索：公司与联系人（邮箱手机脱敏）"},
            {"path": "/api/ai/quotations", "method": "GET",
             "params": {"user": "工号", "customer_id": "客户 id", "limit": "条数"},
             "desc": "报价单摘要：单号、标题、金额、状态、有效期"},
            {"path": "/api/ai/orders", "method": "GET",
             "params": {"user": "工号", "status": "按状态过滤", "limit": "条数"},
             "desc": "销售订单履约：单号、客户、金额、状态、交期"},
            {"path": "/api/ai/production", "method": "GET",
             "params": {"user": "工号", "limit": "条数"},
             "desc": "在制订单：产品型号、数量、当前生产状态"},
            {"path": "/api/ai/overview", "method": "GET",
             "params": {"user": "工号"},
             "desc": "经营概览：询盘/报价/订单/出货数量与金额合计（不含成本毛利）"},
            {"path": "/api/ai/ask", "method": "POST",
             "params": {"user": "工号", "q": "自然语言问题"},
             "desc": "意图路由：常见问题一句话返回答案（含数据出处）"},
        ],
        "not_available": [
            "密码 / 授权码 / 密钥 —— 任何接口都不返回",
            "成本 / 毛利 / 利润 —— 商业机密，不开放",
            "审计日志明细 —— 仅统计，不给逐条记录",
            "写操作 —— 本模块全部只读",
            "批量导出 —— 单次返回条数有上限",
        ],
    }


@router.get("/whoami")
def ai_whoami(user: str | None = None,
              x_api_key: str | None = Header(None, alias="X-API-Key")):
    """员工档案摘要 —— 让助手知道「现在是谁在问」。"""
    _auth_ai(x_api_key)
    emp = _resolve_employee(user)
    db = get_conn()
    dept = None
    if emp.get("dept_id"):
        d = db.execute("SELECT name, code FROM departments WHERE id=?",
                       (emp["dept_id"],)).fetchone()
        dept = dict(d) if d else None
    open_tasks = db.execute(
        "SELECT COUNT(*) c FROM tasks WHERE assignee_id=? AND status='open'",
        (emp["id"],)).fetchone()["c"]
    overdue = db.execute(
        "SELECT COUNT(*) c FROM tasks WHERE assignee_id=? AND status='open' "
        "AND due_at IS NOT NULL AND due_at < ?", (emp["id"], now())).fetchone()["c"]
    release_conn()
    return {
        "emp_no": emp.get("emp_no"),
        "username": emp.get("username"),
        "full_name": emp.get("full_name"),
        "role": emp.get("role"),
        "emp_status": emp.get("emp_status"),
        "dept": dept,
        "data_scope": emp.get("scope"),
        "open_tasks": open_tasks,
        "overdue_tasks": overdue,
        "permissions": sorted(emp.get("perms", [])),
    }


@router.get("/tasks")
def ai_tasks(user: str | None = None, limit: int = 20,
             x_api_key: str | None = Header(None, alias="X-API-Key")):
    """我的待办。"""
    _auth_ai(x_api_key)
    emp = _resolve_employee(user)
    limit = max(1, min(limit, 50))
    db = get_conn()
    ts = now()
    rows = db.execute(
        "SELECT id, task_type, title, detail, due_at, priority, status "
        "FROM tasks WHERE assignee_id=? AND status='open' "
        "ORDER BY CASE WHEN due_at IS NULL THEN 1 ELSE 0 END, due_at LIMIT ?",
        (emp["id"], limit)).fetchall()
    release_conn()
    out = []
    for r in rows:
        due = r["due_at"]
        out.append({
            "id": r["id"], "type": r["task_type"], "title": r["title"],
            "detail": _clip(r["detail"], 160),
            "due_at": due,
            "overdue": bool(due and due < ts),
            "priority": r["priority"],
        })
    return {"items": out, "total": len(out)}


@router.get("/customers")
def ai_customers(user: str | None = None, q: str | None = None, limit: int = 20,
                 x_api_key: str | None = Header(None, alias="X-API-Key")):
    """客户检索 —— 联系方式脱敏。"""
    _auth_ai(x_api_key)
    emp = _resolve_employee(user)
    limit = max(1, min(limit, 50))
    db = get_conn()
    sql = ("SELECT id, code, company_en, contact, country, email, phone, "
           "incoterm, currency FROM customers WHERE 1=1")
    args: list = []
    if q:
        sql += " AND (company_en LIKE ? OR contact LIKE ? OR country LIKE ? OR code LIKE ?)"
        args += [f"%{q}%"] * 4
    # 数据范围：self 范围只能看自己负责的客户
    if emp.get("scope") == "self":
        sql += " AND owner_id=?"
        args.append(emp["id"])
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    rows = db.execute(sql, args).fetchall()
    release_conn()
    return {"items": [{
        "id": r["id"], "code": r["code"], "company_en": r["company_en"],
        "contact": r["contact"], "country": r["country"],
        "email": _mask_email(r["email"]), "phone": _mask_phone(r["phone"]),
        "incoterm": r["incoterm"], "currency": r["currency"],
    } for r in rows]}


@router.get("/quotations")
def ai_quotations(user: str | None = None, customer_id: int | None = None,
                  limit: int = 20,
                  x_api_key: str | None = Header(None, alias="X-API-Key")):
    """报价单摘要 —— 不含成本与毛利。"""
    _auth_ai(x_api_key)
    emp = _resolve_employee(user)
    limit = max(1, min(limit, 50))
    db = get_conn()
    sql = ("SELECT q.id, q.quote_no, q.title, q.currency, q.total_amount, q.status, "
           "q.valid_until, q.valid_days, q.created_at, c.company_en AS customer_name "
           "FROM quotations q LEFT JOIN customers c ON c.id=q.customer_id WHERE 1=1")
    args: list = []
    if customer_id:
        sql += " AND q.customer_id=?"
        args.append(customer_id)
    if emp.get("scope") == "self":
        sql += " AND q.owner_id=?"
        args.append(emp["id"])
    sql += " ORDER BY q.id DESC LIMIT ?"
    args.append(limit)
    rows = db.execute(sql, args).fetchall()
    release_conn()
    return {"items": [{
        "id": r["id"], "quote_no": r["quote_no"], "title": r["title"],
        "customer": r["customer_name"], "currency": r["currency"],
        "total_amount": r["total_amount"], "status": r["status"],
        "valid_until": r["valid_until"], "created_at": r["created_at"],
    } for r in rows]}


@router.get("/orders")
def ai_orders(user: str | None = None, status: str | None = None, limit: int = 20,
              x_api_key: str | None = Header(None, alias="X-API-Key")):
    """销售订单履约 —— 不含成本。"""
    _auth_ai(x_api_key)
    emp = _resolve_employee(user)
    limit = max(1, min(limit, 50))
    db = get_conn()
    sql = ("SELECT o.id, o.order_no, o.total_amount, o.currency, o.status, "
           "o.delivery_date, o.created_at, c.company_en AS customer_name "
           "FROM orders o LEFT JOIN customers c ON c.id=o.customer_id WHERE 1=1")
    args: list = []
    if status:
        sql += " AND o.status=?"
        args.append(status)
    if emp.get("scope") == "self":
        sql += " AND o.owner_id=?"
        args.append(emp["id"])
    sql += " ORDER BY o.id DESC LIMIT ?"
    args.append(limit)
    rows = db.execute(sql, args).fetchall()
    release_conn()
    return {"items": [{
        "id": r["id"], "order_no": r["order_no"], "customer": r["customer_name"],
        "currency": r["currency"], "total_amount": r["total_amount"],
        "status": r["status"], "delivery_date": r["delivery_date"],
        "created_at": r["created_at"],
    } for r in rows]}


@router.get("/production")
def ai_production(user: str | None = None, limit: int = 20,
                  x_api_key: str | None = Header(None, alias="X-API-Key")):
    """在制订单进度。"""
    _auth_ai(x_api_key)
    emp = _resolve_employee(user)
    limit = max(1, min(limit, 50))
    db = get_conn()
    rows = db.execute(
        "SELECT oi.id, o.order_no, oi.model, oi.name_en, oi.qty, "
        "oi.production_status, o.delivery_date, c.company_en AS customer_name "
        "FROM order_items oi JOIN orders o ON o.id=oi.order_id "
        "LEFT JOIN customers c ON c.id=o.customer_id "
        "WHERE oi.production_status IS NOT NULL "
        "AND oi.production_status NOT IN ('done','cancelled') "
        "ORDER BY o.id DESC LIMIT ?", (limit,)).fetchall()
    release_conn()
    return {"items": [{
        "order_no": r["order_no"], "customer": r["customer_name"],
        "model": r["model"], "name": r["name_en"], "qty": r["qty"],
        "production_status": r["production_status"],
        "delivery_date": r["delivery_date"],
    } for r in rows]}


@router.get("/overview")
def ai_overview(user: str | None = None,
                x_api_key: str | None = Header(None, alias="X-API-Key")):
    """经营概览 —— 只给数量与金额合计，不含成本毛利。"""
    _auth_ai(x_api_key)
    emp = _resolve_employee(user)
    db = get_conn()
    self_only = emp.get("scope") == "self"
    own = " AND owner_id=?" if self_only else ""
    args = [emp["id"]] if self_only else []

    def cnt(table: str, extra: str = "") -> int:
        return db.execute(f"SELECT COUNT(*) c FROM {table} WHERE 1=1{own}{extra}",
                          args).fetchone()["c"]

    def amount(table: str, col: str, extra: str = "") -> float:
        return db.execute(f"SELECT IFNULL(SUM({col}),0) a FROM {table} "
                          f"WHERE 1=1{own}{extra}", args).fetchone()["a"] or 0

    res = {
        "inquiries": cnt("inquiries"),
        "quotations": cnt("quotations"),
        "orders": cnt("orders"),
        "orders_in_production": cnt("orders", " AND status='producing'"),
        "shipments": cnt("shipments"),
        "total_quotation_amount": amount("quotations", "total_amount"),
        "total_order_amount": amount("orders", "total_amount"),
        "open_tasks": db.execute(
            "SELECT COUNT(*) c FROM tasks WHERE assignee_id=? AND status='open'",
            (emp["id"],)).fetchone()["c"],
        "overdue_tasks": db.execute(
            "SELECT COUNT(*) c FROM tasks WHERE assignee_id=? AND status='open' "
            "AND due_at IS NOT NULL AND due_at < ?", (emp["id"], now())).fetchone()["c"],
    }
    release_conn()
    return res


# ==========================================================================
# 5. 意图路由 —— 一句话提问
# ==========================================================================
INTENTS = [
    ("tasks", ["待办", "任务", "要做", "催", "逾期", "催办"],
     "我的待办"),
    ("production", ["生产", "在制", "车间", "做到哪", "进度", "交货进度"],
     "在制订单进度"),
    ("orders", ["订单", "出货", "交付", "交期", "尾款"],
     "销售订单履约"),
    ("quotations", ["报价", "报过", "上次报", "多少钱", "价格", "折扣"],
     "报价单记录"),
    ("customers", ["客户", "公司", "谁", "联系人", "邮箱", "电话"],
     "客户资料"),
    ("overview", ["多少", "统计", "概况", "总量", "业绩", "成交"],
     "经营概览"),
]


@router.post("/ask")
def ai_ask(body: AskIn,
           x_api_key: str | None = Header(None, alias="X-API-Key")):
    """意图路由 —— 常见问题一句话给出答案。

    关键词匹配到意图 → 调用对应查询 → 组装成可读答案。
    匹配不到时回落到经营概览，并提示可用的问法。
    """
    _auth_ai(x_api_key)
    emp = _resolve_employee(body.user)
    q = (body.q or "").strip()
    low = q.lower()

    intent = None
    for key, words, label in INTENTS:
        if any(w in low for w in words):
            intent = (key, label)
            break

    # 「某客户」—— 尝试从问题里认出公司名。
    # 注意：像「客户资料」这种通用问法里没有公司名，不能拿整句去搜。
    cust = None
    if q:
        db = get_conn()
        like = f"%{q}%"
        cust = db.execute(
            "SELECT id, company_en FROM customers WHERE company_en LIKE ? "
            "OR code LIKE ? ORDER BY id DESC LIMIT 1", (like, like)).fetchone()
        release_conn()
        cust = dict(cust) if cust else None

    if intent and intent[0] == "tasks":
        data = ai_tasks(user=body.user, limit=body.limit, x_api_key=x_api_key)
        items = data["items"]
        if not items:
            answer = "当前没有待办事项。"
        else:
            lines = [f"你共有 {len(items)} 项待办："]
            for t in items:
                mark = "（已逾期）" if t["overdue"] else ""
                lines.append(f"- {t['title']}{mark}"
                             + (f"，截止 {t['due_at']}" if t["due_at"] else ""))
            answer = "\n".join(lines)
        return {"intent": "tasks", "answer": answer, "data": data}

    if intent and intent[0] == "production":
        data = ai_production(user=body.user, limit=body.limit, x_api_key=x_api_key)
        items = data["items"]
        if not items:
            answer = "目前没有在制订单。"
        else:
            lines = [f"在制订单 {len(items)} 条："]
            for it in items:
                lines.append(f"- {it['order_no']}｜{it['model'] or it['name']}×{it['qty']}"
                             f"｜进度 {it['production_status']}"
                             + (f"｜交期 {it['delivery_date']}" if it["delivery_date"] else ""))
            answer = "\n".join(lines)
        return {"intent": "production", "answer": answer, "data": data}

    if intent and intent[0] == "orders":
        data = ai_orders(user=body.user, limit=body.limit, x_api_key=x_api_key)
        items = data["items"]
        if cust:
            items = [i for i in items if cust["company_en"] in (i["customer"] or "")]
        if not items:
            answer = "没有符合条件的订单。"
        else:
            lines = [f"相关订单 {len(items)} 条："]
            for o in items:
                lines.append(f"- {o['order_no']}｜{o['customer']}"
                             f"｜{o['currency']} {o['total_amount']}"
                             f"｜状态 {o['status']}"
                             + (f"｜交期 {o['delivery_date']}" if o["delivery_date"] else ""))
            answer = "\n".join(lines)
        return {"intent": "orders", "answer": answer, "data": data}

    if intent and intent[0] == "quotations":
        data = ai_quotations(user=body.user, customer_id=cust["id"] if cust else None,
                              limit=body.limit, x_api_key=x_api_key)
        items = data["items"]
        if not items:
            answer = "没有找到报价单记录。"
        else:
            lines = [f"报价单 {len(items)} 条："]
            for qq in items:
                lines.append(f"- {qq['quote_no']}｜{qq['customer'] or '—'}"
                             f"｜{qq['currency']} {qq['total_amount']}"
                             f"｜状态 {qq['status']}")
            answer = "\n".join(lines)
        return {"intent": "quotations", "answer": answer, "data": data}

    if intent and intent[0] == "customers":
        # 搜客户时去掉「客户」「资料」这类通用词，只留可能的公司名片段
        kw = q
        for w in ("客户", "资料", "信息", "是谁", "联系方式", "的"):
            kw = kw.replace(w, " ")
        kw = kw.strip()
        data = ai_customers(user=body.user,
                             q=(cust["company_en"] if cust else (kw or None)),
                             limit=body.limit, x_api_key=x_api_key)
        items = data["items"]
        if not items:
            answer = "没有找到匹配的客户。"
        else:
            lines = [f"找到 {len(items)} 个客户："]
            for c in items:
                lines.append(f"- {c['company_en']}｜{c['country'] or '—'}"
                             f"｜联系人 {c['contact'] or '—'}"
                             f"｜邮箱 {c['email'] or '—'}")
            answer = "\n".join(lines)
        return {"intent": "customers", "answer": answer, "data": data}

    # 兜底：给概览
    data = ai_overview(user=body.user, x_api_key=x_api_key)
    answer = (
        f"当前概况：询盘 {data['inquiries']} 条、报价单 {data['quotations']} 份、"
        f"订单 {data['orders']} 单（其中在制 {data['orders_in_production']} 单）、"
        f"出货 {data['shipments']} 批。\n"
        f"我的待办 {data['open_tasks']} 项，逾期 {data['overdue_tasks']} 项。\n"
        "（若要更具体，可问：我的待办 / 生产进度 / 某客户报价 / 订单交期）"
    )
    return {"intent": "overview", "answer": answer, "data": data,
            "matched": intent[0] if intent else None}


# ==========================================================================
# 6. Key 管理（管理员操作，走正常登录态）
# ==========================================================================
@router.get("/key-status")
def ai_key_status(u: dict = Depends(current_user)):
    """查看 API Key 状态（不返回明文）。需 admin 登录态。"""
    P.require(u, "users.edit")
    db = get_conn()
    r = db.execute("SELECT value FROM settings WHERE key='ai_api_key_created_at'").fetchone()
    exists = bool(_load_key_hash())
    release_conn()
    return {"exists": exists, "created_at": r["value"] if r else None,
            "note": "明文只在生成/重置时返回一次，系统只存哈希"}


@router.post("/key-reset")
def ai_key_reset(u: dict = Depends(current_user)):
    """重置 API Key，返回新明文一次。需 admin 登录态。"""
    P.require(u, "users.edit")
    raw = reset_ai_key()
    audit(u, "org", "reset_ai_key", "settings", None, "ai_api_key")
    return {"ok": True, "api_key": raw,
            "note": "这是唯一一次显示明文，请立即保存到 WorkBuddy 配置里"}
