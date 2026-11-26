"""模块层 — 财务：应收、收汇、对账。

报价单是营销动作，收汇才是生意。跨部门场景下财务只该看到
与自己相关的数据（scope 过滤自动生效），销售看不到别人的账。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_user
from ..core import permissions as P
from ..core.db import get_conn, jdump, jload, now, release_conn
from ..core.services import (STATUS_LABEL, assert_transition, audit, close_task,
                             create_task, log_activity, next_no, next_states,
                             timeline)

router = APIRouter(prefix="/api/finance", tags=["finance"])

PAY_METHODS = {"T/T": "电汇", "L/C": "信用证", "D/P": "付款交单",
               "D/A": "承兑交单", "P": "即期支票", "cash": "现金"}


class ReceivableIn(BaseModel):
    order_id: int | None = None
    invoice_no: str | None = None
    amount: float
    currency: str = "USD"
    due_date: str | None = None
    remark: str | None = None


@router.get("/receivables")
def list_receivables(u: dict = Depends(current_user), status: str | None = None,
                     keyword: str | None = None, limit: int = 50, offset: int = 0):
    P.require(u, "finance.view")
    db = get_conn()
    sql = ("SELECT r.*, o.order_no, c.company_en AS customer_name, "
           "us.full_name AS owner_name FROM receivables r "
           "LEFT JOIN orders o ON o.id=r.order_id "
           "LEFT JOIN customers c ON c.id=o.customer_id "
           "LEFT JOIN users us ON us.id=r.owner_id WHERE 1=1")
    args: list = []
    frag, pargs = P.scope_clause(u, alias="r")
    sql += frag
    args += pargs
    if status:
        sql += " AND r.status=?"
        args.append(status)
    if keyword:
        like = f"%{keyword}%"
        sql += (" AND (r.receivable_no LIKE ? OR r.invoice_no LIKE ? "
                "OR o.order_no LIKE ? OR c.company_en LIKE ?)")
        args += [like] * 4
    # 逾期判定写回，便于列表筛选
    rows = [dict(r) for r in db.execute(
        sql + " ORDER BY CASE WHEN r.due_date<date('localtime') "
              "AND r.status IN ('open','partial') THEN 0 ELSE 1 END, "
              "r.due_date LIMIT ? OFFSET ?", args + [min(limit, 200), offset])]
    cnt = db.execute(f"SELECT COUNT(*) c FROM ({sql})", args).fetchone()["c"]
    release_conn()
    today = now()[:10]
    for r in rows:
        r["outstanding"] = round(r["amount"] - (r["received_amount"] or 0), 2)
        r["status_label"] = STATUS_LABEL.get(r["status"], r["status"])
        r["is_overdue"] = bool(r["due_date"] and r["due_date"] < today
                               and r["status"] in ("open", "partial"))
        r["days_overdue"] = 0
        if r["is_overdue"]:
            try:
                y1, m1, d1 = map(int, today.split("-"))
                y2, m2, d2 = map(int, r["due_date"][:10].split("-"))
                r["days_overdue"] = (y1 - y2) * 372 + (m1 - m2) * 31 + (d1 - d2)
            except Exception:
                pass
    return {"items": rows, "total": cnt}


@router.get("/receivables/summary")
def summary(u: dict = Depends(current_user)):
    """账龄与汇总。财务最常看的一个接口。"""
    P.require(u, "finance.view")
    db = get_conn()
    frag, args = P.scope_clause(u, alias="r")
    rows = [dict(r) for r in db.execute(
        f"SELECT currency, status, IFNULL(SUM(amount),0) amount, "
        f"IFNULL(SUM(received_amount),0) received, COUNT(*) n "
        f"FROM receivables WHERE 1=1{frag} GROUP BY currency, status", args)]
    by_cur: dict = {}
    for r in rows:
        c = by_cur.setdefault(r["currency"], {"amount": 0, "received": 0, "count": 0})
        c["amount"] += r["amount"]
        c["received"] += r["received"]
        c["count"] += r["n"]
        c["outstanding"] = c["amount"] - c["received"]
    overdue = db.execute(
        f"SELECT IFNULL(SUM(amount-received_amount),0) v FROM receivables "
        f"WHERE status IN ('open','partial') AND due_date<date('localtime'){frag}",
        args).fetchone()["v"]
    release_conn()
    return {"by_currency": by_cur, "overdue_amount": round(overdue or 0, 2)}


@router.get("/receivables/{rid}")
def get_receivable(rid: int, u: dict = Depends(current_user)):
    P.require(u, "finance.view")
    db = get_conn()
    r = db.execute(
        "SELECT r.*, o.order_no, c.company_en AS customer_name FROM receivables r "
        "LEFT JOIN orders o ON o.id=r.order_id "
        "LEFT JOIN customers c ON c.id=o.customer_id WHERE r.id=?", (rid,)).fetchone()
    if not r:
        release_conn()
        raise HTTPException(404, "应收单不存在")
    d = dict(r)
    d["outstanding"] = round(r["amount"] - (r["received_amount"] or 0), 2)
    d["status_label"] = STATUS_LABEL.get(r["status"], r["status"])
    d["payments"] = [dict(x) for x in db.execute(
        "SELECT * FROM payments WHERE receivable_id=? ORDER BY id", (rid,))]
    d["timeline"] = timeline("receivable", rid)
    release_conn()
    return d


@router.post("/receivables")
def create_receivable(body: ReceivableIn, u: dict = Depends(current_user)):
    P.require(u, "finance.edit")
    if body.amount <= 0:
        raise HTTPException(400, "金额必须大于 0")
    db = get_conn()
    no = next_no("receivable")
    ts = now()
    owner = dept = None
    if body.order_id:
        o = db.execute("SELECT owner_id,dept_id FROM orders WHERE id=?",
                       (body.order_id,)).fetchone()
        if o:
            owner, dept = o["owner_id"], o["dept_id"]
    cur = db.execute(
        "INSERT INTO receivables(receivable_no,order_id,invoice_no,amount,"
        "received_amount,currency,due_date,status,owner_id,dept_id,remark,"
        "created_at,updated_at) VALUES(?,?,?,?,0,?,?,'open',?,?,?,?,?)",
        (no, body.order_id, body.invoice_no, body.amount, body.currency,
         body.due_date, owner, dept, body.remark, ts, ts))
    rid = cur.lastrowid
    db.commit()
    release_conn()
    log_activity("receivable", rid, "create", u, f"创建应收 {no} {body.amount} {body.currency}")
    audit(u, "finance", "create", "receivable", rid, no, after=body.model_dump())
    return {"id": rid, "receivable_no": no}


class PaymentIn(BaseModel):
    amount: float
    currency: str = "USD"
    received_at: str | None = None
    method: str = "T/T"
    remark: str | None = None


@router.post("/receivables/{rid}/payments")
def add_payment(rid: int, body: PaymentIn, u: dict = Depends(current_user)):
    """登记收汇。金额校验防止多收；全额到账自动结清。"""
    P.require(u, "finance.edit")
    if body.amount <= 0:
        raise HTTPException(400, "收款金额必须大于 0")
    db = get_conn()
    r = db.execute("SELECT * FROM receivables WHERE id=?", (rid,)).fetchone()
    if not r:
        release_conn()
        raise HTTPException(404, "应收单不存在")
    if r["status"] == "settled":
        release_conn()
        raise HTTPException(400, "该应收单已结清")
    out = (r["amount"] or 0) - (r["received_amount"] or 0)
    if body.amount > out + 0.01:
        release_conn()
        raise HTTPException(400, f"收款金额超过未收余额（未收 {out:.2f}）")
    db.execute(
        "INSERT INTO payments(receivable_id,amount,currency,received_at,method,"
        "remark,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (rid, body.amount, body.currency, body.received_at or now()[:10],
         body.method, body.remark, u["id"], now()))
    new_recv = (r["received_amount"] or 0) + body.amount
    st = "settled" if new_recv >= (r["amount"] or 0) - 0.01 else "partial"
    db.execute("UPDATE receivables SET received_amount=?,status=?,updated_at=? WHERE id=?",
               (new_recv, st, now(), rid))
    db.commit()
    release_conn()
    log_activity("receivable", rid, "payment", u,
                 f"收款 {body.amount} {body.currency}（{PAY_METHODS.get(body.method, body.method)}）")
    audit(u, "finance", "payment", "receivable", rid, r["receivable_no"],
          before={"received": r["received_amount"]}, after={"received": new_recv})
    if st == "settled":
        close_task("order", r["order_id"], None, "款项已结清", u["id"]) if r["order_id"] else None
        if r["order_id"]:
            db2 = get_conn()
            o = db2.execute("SELECT status FROM orders WHERE id=?",
                            (r["order_id"],)).fetchone()
            if o and o["status"] == "shipped":
                db2.execute("UPDATE orders SET status='completed',updated_at=? WHERE id=?",
                            (now(), r["order_id"]))
                db2.commit()
                log_activity("order", r["order_id"], "completed", u, "款项结清，订单完成")
            release_conn()
    return {"ok": True, "received_amount": new_recv, "status": st,
            "outstanding": round(r["amount"] - new_recv, 2)}


class DueDateIn(BaseModel):
    due_date: str
    remark: str | None = None


@router.put("/receivables/{rid}")
def update_receivable(rid: int, body: DueDateIn, u: dict = Depends(current_user)):
    P.require(u, "finance.edit")
    db = get_conn()
    before = db.execute("SELECT * FROM receivables WHERE id=?", (rid,)).fetchone()
    if not before:
        release_conn()
        raise HTTPException(404, "应收单不存在")
    db.execute("UPDATE receivables SET due_date=?,remark=?,updated_at=? WHERE id=?",
               (body.due_date, body.remark or before["remark"], now(), rid))
    db.commit()
    release_conn()
    audit(u, "finance", "update", "receivable", rid, before["receivable_no"],
          before=dict(before), after=body.model_dump())
    return {"ok": True}


@router.delete("/receivables/{rid}")
def delete_receivable(rid: int, u: dict = Depends(current_user)):
    P.require(u, "finance.edit")
    db = get_conn()
    r = db.execute("SELECT receivable_no,received_amount FROM receivables WHERE id=?",
                   (rid,)).fetchone()
    if not r:
        release_conn()
        raise HTTPException(404, "应收单不存在")
    if (r["received_amount"] or 0) > 0:
        release_conn()
        raise HTTPException(400, "已有收款记录，不能删除；请改为手工核销")
    db.execute("DELETE FROM receivables WHERE id=?", (rid,))
    db.commit()
    release_conn()
    audit(u, "finance", "delete", "receivable", rid, r["receivable_no"])
    return {"ok": True}
