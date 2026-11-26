"""模块层 — 询盘管理。

系统真正的业务入口。当前只有报价单，等于「有单子做但不知道单子从哪来」。
跨部门场景下询盘还要承接技术确认与交期确认，避免销售拍脑袋承诺。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_user
from ..core import permissions as P
from ..core.db import get_conn, jdump, jload, now, release_conn
from ..core.services import (STATUS_LABEL, assert_transition, audit, bump_version,
                             check_version, close_task, create_task, log_activity,
                             next_no, next_states, timeline)

router = APIRouter(prefix="/api/inquiries", tags=["inquiry"])

SOURCE_LABEL = {"alibaba": "阿里国际站", "email": "邮件", "exhibition": "展会",
                "website": "官网表单", "referral": "老客户介绍", "phone": "电话",
                "other": "其他"}
PRIORITY_LABEL = {"low": "低", "normal": "普通", "high": "高", "urgent": "紧急"}


class ItemIn(BaseModel):
    product_id: int | None = None
    category_code: str | None = None
    description: str | None = None
    spec_text: str | None = None
    qty: float = 1
    unit: str = "set"
    target_price: float | None = None
    remark: str | None = None


class InquiryIn(BaseModel):
    customer_id: int | None = None
    customer_name: str | None = None
    source: str = "email"
    contact_name: str | None = None
    contact_email: str | None = None
    country: str | None = None
    subject: str | None = None
    received_at: str | None = None
    currency: str = "USD"
    target_price: float | None = None
    incoterm: str | None = None
    qty_hint: int | None = None
    priority: str = "normal"
    remark: str | None = None
    items: list[ItemIn] = []


def _hydrate(r) -> dict:
    d = dict(r)
    d["source_label"] = SOURCE_LABEL.get(d.get("source"), d.get("source"))
    d["priority_label"] = PRIORITY_LABEL.get(d.get("priority"), d.get("priority"))
    d["status_label"] = STATUS_LABEL.get(d.get("status"), d.get("status"))
    d["next_states"] = next_states("inquiry", d.get("status"))
    d["is_overdue"] = bool(d.get("due_at") and d["due_at"][:10] < now()[:10])
    return d


@router.get("")
def list_inquiries(u: dict = Depends(current_user),
                   status: str | None = None, source: str | None = None,
                   priority: str | None = None, keyword: str | None = None,
                   mine: int = 0, limit: int = 50, offset: int = 0):
    P.require(u, "inquiries.view")
    db = get_conn()
    sql = ("SELECT i.*, c.company_en AS customer_name_db, us.full_name AS owner_name, "
           "d.name AS dept_name FROM inquiries i "
           "LEFT JOIN customers c ON c.id=i.customer_id "
           "LEFT JOIN users us ON us.id=i.owner_id "
           "LEFT JOIN departments d ON d.id=i.dept_id WHERE 1=1")
    args: list = []
    frag, pargs = P.scope_clause(u, alias="i")
    sql += frag
    args += pargs
    if mine:
        sql += " AND i.owner_id=?"
        args.append(u["id"])
    if status:
        sql += " AND i.status=?"
        args.append(status)
    if source:
        sql += " AND i.source=?"
        args.append(source)
    if priority:
        sql += " AND i.priority=?"
        args.append(priority)
    if keyword:
        like = f"%{keyword}%"
        sql += (" AND (i.inquiry_no LIKE ? OR i.subject LIKE ? OR i.customer_name LIKE ? "
                "OR i.contact_name LIKE ? OR i.country LIKE ?)")
        args += [like] * 5
    # 总数：复用同一条件，去掉排序分页后包一层
    count_sql = sql.split(" ORDER BY")[0]
    cnt = db.execute(f"SELECT COUNT(*) c FROM ({count_sql})", args).fetchone()["c"]
    sql += " ORDER BY CASE i.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 ELSE 2 END, i.id DESC LIMIT ? OFFSET ?"
    args += [min(limit, 200), offset]
    rows = [_hydrate(r) for r in db.execute(sql, args)]
    release_conn()
    return {"items": rows, "total": cnt}


@router.get("/stats")
def inquiry_stats(u: dict = Depends(current_user)):
    """漏斗看板：询盘→报价→成交。"""
    P.require(u, "inquiries.view")
    db = get_conn()
    frag, args = P.scope_clause(u, alias="i")
    by_status = {r["status"]: r["n"] for r in db.execute(
        f"SELECT status, COUNT(*) n FROM inquiries WHERE 1=1{frag} GROUP BY status", args)}
    by_source = [dict(r) for r in db.execute(
        f"SELECT source, COUNT(*) n FROM inquiries WHERE 1=1{frag} GROUP BY source "
        "ORDER BY n DESC", args)]
    total = sum(by_status.values())
    won = by_status.get("won", 0)
    quoted = by_status.get("quoted", 0) + by_status.get("negotiating", 0) + won
    rate = round(won * 100.0 / quoted, 1) if quoted else 0
    release_conn()
    return {
        "total": total, "by_status": by_status, "by_source": by_source,
        "quoted": quoted, "won": won, "win_rate": rate,
        "pending": by_status.get("new", 0),
    }


@router.get("/{iid}")
def get_inquiry(iid: int, u: dict = Depends(current_user)):
    P.require(u, "inquiries.view")
    db = get_conn()
    r = db.execute(
        "SELECT i.*, c.company_en AS customer_company, us.full_name AS owner_name, "
        "d.name AS dept_name FROM inquiries i "
        "LEFT JOIN customers c ON c.id=i.customer_id "
        "LEFT JOIN users us ON us.id=i.owner_id "
        "LEFT JOIN departments d ON d.id=i.dept_id WHERE i.id=?", (iid,)).fetchone()
    if not r:
        release_conn()
        raise HTTPException(404, "询盘不存在")
    d = _hydrate(r)
    d["items"] = [dict(x) for x in db.execute(
        "SELECT * FROM inquiry_items WHERE inquiry_id=? ORDER BY id", (iid,))]
    d["timeline"] = timeline("inquiry", iid)
    if not P.visible(u, d):
        release_conn()
        raise HTTPException(403, "没有查看这条询盘的权限")
    release_conn()
    return d


@router.post("")
def create_inquiry(body: InquiryIn, u: dict = Depends(current_user)):
    P.require(u, "inquiries.edit")
    db = get_conn()
    no = next_no("inquiry")
    ts = now()
    cur = db.execute(
        "INSERT INTO inquiries(inquiry_no,source,customer_id,customer_name,"
        "contact_name,contact_email,country,subject,received_at,currency,target_price,"
        "incoterm,qty_hint,status,priority,owner_id,dept_id,remark,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'new',?,?,?,?,?,?)",
        # 19 个占位符 + 字面量 'new' = 20，与列数一一对应
        (no, body.source, body.customer_id, body.customer_name, body.contact_name,
         body.contact_email, body.country, body.subject,
         body.received_at or ts[:10], body.currency, body.target_price,
         body.incoterm, body.qty_hint,                       # ← qty_hint
         body.priority, u["id"], u.get("dept_id"),
         body.remark, ts, ts))
    iid = cur.lastrowid
    for it in body.items:
        db.execute(
            "INSERT INTO inquiry_items(inquiry_id,product_id,category_code,description,"
            "spec_text,qty,unit,target_price,remark) VALUES(?,?,?,?,?,?,?,?,?)",
            (iid, it.product_id, it.category_code, it.description, it.spec_text,
             it.qty, it.unit, it.target_price, it.remark))
    db.commit()
    release_conn()
    log_activity("inquiry", iid, "create", u, f"登记询盘 {no}")
    audit(u, "inquiry", "create", "inquiry", iid, no, after=body.model_dump())
    return {"id": iid, "inquiry_no": no}


@router.put("/{iid}")
def update_inquiry(iid: int, body: InquiryIn, u: dict = Depends(current_user),
                   version: int | None = None):
    P.require(u, "inquiries.edit")
    db = get_conn()
    before = db.execute("SELECT * FROM inquiries WHERE id=?", (iid,)).fetchone()
    if not before:
        release_conn()
        raise HTTPException(404, "询盘不存在")
    try:
        check_version(db, "inquiries", iid, version)
    except Exception as e:
        release_conn()
        if type(e).__name__ == "ConflictError":
            raise HTTPException(409, str(e))
        raise
    d = body.model_dump()
    db.execute(
        "UPDATE inquiries SET customer_id=?,customer_name=?,source=?,contact_name=?,"
        "contact_email=?,country=?,subject=?,received_at=?,currency=?,target_price=?,"
        "incoterm=?,qty_hint=?,priority=?,remark=?,updated_at=? WHERE id=?",
        (d["customer_id"], d["customer_name"], d["source"], d["contact_name"],
         d["contact_email"], d["country"], d["subject"], d["received_at"] or now()[:10],
         d["currency"], d["target_price"], d["incoterm"], d["qty_hint"],
         d["priority"], d["remark"], now(), iid))
    if body.items:
        db.execute("DELETE FROM inquiry_items WHERE inquiry_id=?", (iid,))
        for it in body.items:
            db.execute(
                "INSERT INTO inquiry_items(inquiry_id,product_id,category_code,description,"
                "spec_text,qty,unit,target_price,remark) VALUES(?,?,?,?,?,?,?,?,?)",
                (iid, it.product_id, it.category_code, it.description, it.spec_text,
                 it.qty, it.unit, it.target_price, it.remark))
    bump_version(db, "inquiries", iid, u["id"])
    db.commit()
    release_conn()
    audit(u, "inquiry", "update", "inquiry", iid, before["inquiry_no"],
          before=dict(before), after=d)
    return {"ok": True}


class StatusIn(BaseModel):
    status: str
    lost_reason: str | None = None
    remark: str | None = None


@router.post("/{iid}/status")
def change_status(iid: int, body: StatusIn, u: dict = Depends(current_user)):
    """状态流转。走状态机校验，不允许直接跳级。"""
    P.require(u, "inquiries.edit")
    db = get_conn()
    r = db.execute("SELECT * FROM inquiries WHERE id=?", (iid,)).fetchone()
    if not r:
        release_conn()
        raise HTTPException(404, "询盘不存在")
    try:
        assert_transition("inquiry", r["status"], body.status)
    except HTTPException:
        release_conn()
        raise
    db.execute("UPDATE inquiries SET status=?,lost_reason=?,updated_at=? WHERE id=?",
               (body.status, body.lost_reason or r["lost_reason"], now(), iid))
    bump_version(db, "inquiries", iid, u["id"])
    db.commit()
    release_conn()
    log_activity("inquiry", iid, "status", u, body.remark or "",
                 r["status"], body.status)
    audit(u, "inquiry", "status", "inquiry", iid, r["inquiry_no"],
          before={"status": r["status"]}, after={"status": body.status})
    if body.status == "won":
        close_task("inquiry", iid, None, "询盘成交", u["id"])
    return {"ok": True}


class TaskIn(BaseModel):
    title: str
    detail: str = ""
    task_type: str = "followup"
    assignee_id: int | None = None
    dept_id: int | None = None
    due_at: str | None = None
    priority: str = "normal"


@router.post("/{iid}/tasks")
def add_task(iid: int, body: TaskIn, u: dict = Depends(current_user)):
    """加跟进待办。跨部门场景的核心：销售能直接派活给技术/生产。"""
    P.require(u, "inquiries.edit")
    tid = create_task("inquiry", iid, body.task_type, body.title,
                      body.assignee_id, body.dept_id, body.due_at,
                      body.detail, body.priority, u["id"])
    return {"ok": True, "task_id": tid}


class HandoffIn(BaseModel):
    owner_id: int
    remark: str | None = None


@router.post("/{iid}/handoff")
def handoff(iid: int, body: HandoffIn, u: dict = Depends(current_user)):
    """转交负责人。多站点/分公司之间交接询盘用。"""
    P.require(u, "inquiries.edit")
    db = get_conn()
    r = db.execute("SELECT * FROM inquiries WHERE id=?", (iid,)).fetchone()
    if not r:
        release_conn()
        raise HTTPException(404, "询盘不存在")
    db.execute("UPDATE inquiries SET owner_id=?,updated_at=? WHERE id=?",
               (body.owner_id, now(), iid))
    bump_version(db, "inquiries", iid, u["id"])
    db.commit()
    release_conn()
    log_activity("inquiry", iid, "handoff", u,
                 f"转交给 #{body.owner_id} {body.remark or ''}")
    audit(u, "inquiry", "handoff", "inquiry", iid, r["inquiry_no"],
          before={"owner_id": r["owner_id"]}, after={"owner_id": body.owner_id})
    return {"ok": True}


@router.delete("/{iid}")
def delete_inquiry(iid: int, u: dict = Depends(current_user)):
    P.require(u, "inquiries.edit")
    db = get_conn()
    r = db.execute("SELECT inquiry_no,status FROM inquiries WHERE id=?", (iid,)).fetchone()
    if not r:
        release_conn()
        raise HTTPException(404, "询盘不存在")
    db.execute("DELETE FROM inquiry_items WHERE inquiry_id=?", (iid,))
    db.execute("DELETE FROM activities WHERE biz_type='inquiry' AND biz_id=?", (iid,))
    db.execute("DELETE FROM inquiries WHERE id=?", (iid,))
    db.commit()
    release_conn()
    audit(u, "inquiry", "delete", "inquiry", iid, r["inquiry_no"])
    return {"ok": True}


@router.get("/-meta/options")
def meta_options(u: dict = Depends(current_user)):
    """下拉选项。VPN 延迟高，一次取全比多次往返好。"""
    return {
        "sources": [{"value": k, "label": v} for k, v in SOURCE_LABEL.items()],
        "priorities": [{"value": k, "label": v} for k, v in PRIORITY_LABEL.items()],
        "statuses": [{"value": k, "label": v} for k, v in STATUS_LABEL.items()
                     if k in ("new", "quoted", "negotiating", "won", "lost", "archived")],
    }
