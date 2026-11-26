"""模块层 — 采购与生产。

制造商的核心环节。订单里 source=out 的行要走采购，
自有产线的行走生产跟踪，两者在同一张订单上分别推进。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_user
from ..core import permissions as P
from ..core.db import get_conn, jdump, jload, now, release_conn
from ..core.services import (STATUS_LABEL, assert_transition, audit, close_task,
                             create_task, log_activity, next_no, next_states,
                             timeline)

router = APIRouter(prefix="/api", tags=["supply"])

PRODUCTION_STATUS = {
    "pending": "待排产", "material": "备料中", "producing": "生产中",
    "quality": "质检中", "finished": "已完工",
}


# ==========================================================================
# 采购单
# ==========================================================================
class POItemIn(BaseModel):
    product_id: int | None = None
    description: str | None = None
    qty: float = 1
    unit_price: float = 0
    remark: str | None = None


class POIn(BaseModel):
    order_id: int | None = None
    supplier_id: int | None = None
    po_date: str | None = None
    expected_date: str | None = None
    currency: str = "CNY"
    remark: str | None = None
    items: list[POItemIn] = []


def _po_hydrate(r) -> dict:
    d = dict(r)
    d["status_label"] = STATUS_LABEL.get(d.get("status"), d.get("status"))
    d["next_states"] = next_states("purchase", d.get("status"))
    d["supplier_name"] = d.pop("supplier_name", None)
    d["order_no"] = d.pop("order_no", None)
    return d


@router.get("/purchase-orders")
def list_po(u: dict = Depends(current_user), status: str | None = None,
            order_id: int | None = None, limit: int = 50, offset: int = 0):
    P.require(u, "orders.view")
    db = get_conn()
    sql = ("SELECT po.*, s.name_cn AS supplier_name, o.order_no FROM purchase_orders po "
           "LEFT JOIN suppliers s ON s.id=po.supplier_id "
           "LEFT JOIN orders o ON o.id=po.order_id WHERE 1=1")
    args: list = []
    if status:
        sql += " AND po.status=?"
        args.append(status)
    if order_id:
        sql += " AND po.order_id=?"
        args.append(order_id)
    cnt = db.execute(f"SELECT COUNT(*) c FROM ({sql})", args).fetchone()["c"]
    sql += " ORDER BY po.id DESC LIMIT ? OFFSET ?"
    args += [min(limit, 200), offset]
    rows = [_po_hydrate(r) for r in db.execute(sql, args)]
    release_conn()
    return {"items": rows, "total": cnt}


@router.get("/purchase-orders/{pid}")
def get_po(pid: int, u: dict = Depends(current_user)):
    P.require(u, "orders.view")
    db = get_conn()
    r = db.execute(
        "SELECT po.*, s.name_cn AS supplier_name, o.order_no FROM purchase_orders po "
        "LEFT JOIN suppliers s ON s.id=po.supplier_id "
        "LEFT JOIN orders o ON o.id=po.order_id WHERE po.id=?", (pid,)).fetchone()
    if not r:
        release_conn()
        raise HTTPException(404, "采购单不存在")
    d = _po_hydrate(r)
    d["items"] = [dict(x) for x in db.execute(
        "SELECT * FROM purchase_order_items WHERE po_id=? ORDER BY id", (pid,))]
    d["timeline"] = timeline("purchase", pid)
    release_conn()
    return d


@router.post("/purchase-orders")
def create_po(body: POIn, u: dict = Depends(current_user)):
    P.require(u, "orders.purchase")
    if not body.supplier_id:
        raise HTTPException(400, "请选择供应商")
    db = get_conn()
    no = next_no("purchase")
    ts = now()
    total = sum(i.qty * i.unit_price for i in body.items)
    cur = db.execute(
        "INSERT INTO purchase_orders(po_no,order_id,supplier_id,po_date,expected_date,"
        "total_amount,currency,status,remark,created_by,created_at,updated_at)"
        # 11 个占位符 + 字面量 'draft' = 12，与列数一致
        " VALUES(?,?,?,?,?,?,?,'draft',?,?,?,?)",
        (no, body.order_id, body.supplier_id, body.po_date or ts[:10],
         body.expected_date, round(total, 2), body.currency, body.remark,
         u["id"], ts, ts))
    pid = cur.lastrowid
    for i in body.items:
        db.execute(
            "INSERT INTO purchase_order_items(po_id,product_id,description,qty,"
            "unit_price,remark) VALUES(?,?,?,?,?,?)",
            (pid, i.product_id, i.description, i.qty, i.unit_price, i.remark))
    db.commit()
    release_conn()
    log_activity("purchase", pid, "create", u, f"创建采购单 {no}")
    audit(u, "purchase", "create", "purchase", pid, no, after=body.model_dump())
    return {"id": pid, "po_no": no}


class POStatusIn(BaseModel):
    status: str
    remark: str | None = None


@router.post("/purchase-orders/{pid}/status")
def po_status(pid: int, body: POStatusIn, u: dict = Depends(current_user)):
    P.require(u, "orders.purchase")
    db = get_conn()
    r = db.execute("SELECT * FROM purchase_orders WHERE id=?", (pid,)).fetchone()
    if not r:
        release_conn()
        raise HTTPException(404, "采购单不存在")
    try:
        assert_transition("purchase", r["status"], body.status)
    except HTTPException:
        release_conn()
        raise
    db.execute("UPDATE purchase_orders SET status=?,updated_at=? WHERE id=?",
               (body.status, now(), pid))
    db.commit()
    release_conn()
    log_activity("purchase", pid, "status", u, body.remark or "", r["status"], body.status)
    if body.status == "received":
        close_task("purchase", pid, None, "已收货", u["id"])
        audit(u, "purchase", "status", "purchase", pid, r["po_no"],
              before={"status": r["status"]}, after={"status": body.status})
    return {"ok": True}


class ReceiveIn(BaseModel):
    item_id: int
    received_qty: float


@router.post("/purchase-orders/{pid}/receive")
def po_receive(pid: int, body: ReceiveIn, u: dict = Depends(current_user)):
    """到货登记。全部收齐则采购单转已收货。"""
    P.require(u, "orders.purchase")
    db = get_conn()
    it = db.execute("SELECT * FROM purchase_order_items WHERE id=? AND po_id=?",
                    (body.item_id, pid)).fetchone()
    if not it:
        release_conn()
        raise HTTPException(404, "采购明细不存在")
    db.execute("UPDATE purchase_order_items SET received_qty=? WHERE id=?",
               (body.received_qty, body.item_id))
    pend = db.execute(
        "SELECT COUNT(*) c FROM purchase_order_items WHERE po_id=? AND received_qty<qty",
        (pid,)).fetchone()["c"]
    if pend == 0:
        db.execute("UPDATE purchase_orders SET status='received',updated_at=? WHERE id=?",
                   (now(), pid))
    db.commit()
    release_conn()
    log_activity("purchase", pid, "receive", u,
                 f"{it['description']} 到货 {body.received_qty}")
    return {"ok": True, "all_received": pend == 0}


# ==========================================================================
# 成本记录（毛利测算的数据基础）
# ==========================================================================
class CostIn(BaseModel):
    product_id: int
    supplier_id: int | None = None
    cost_cny: float
    qty: float = 1
    recorded_at: str | None = None
    remark: str | None = None


@router.post("/costs")
def add_cost(body: CostIn, u: dict = Depends(current_user)):
    P.require(u, "cost.edit")
    db = get_conn()
    db.execute(
        "INSERT INTO cost_records(product_id,supplier_id,cost_cny,qty,recorded_at,"
        "remark,created_at) VALUES(?,?,?,?,?,?,?)",
        (body.product_id, body.supplier_id, body.cost_cny, body.qty,
         body.recorded_at or now()[:10], body.remark, now()))
    db.commit()
    release_conn()
    audit(u, "cost", "create", "product", body.product_id,
          after={"cost_cny": body.cost_cny})
    return {"ok": True}


@router.get("/costs")
def list_costs(u: dict = Depends(current_user), product_id: int | None = None,
               limit: int = 100):
    P.require(u, "cost.view")
    db = get_conn()
    sql = ("SELECT cr.*, p.sku, p.name_cn, s.name_cn AS supplier_name FROM cost_records cr "
           "LEFT JOIN products p ON p.id=cr.product_id "
           "LEFT JOIN suppliers s ON s.id=cr.supplier_id WHERE 1=1")
    args: list = []
    if product_id:
        sql += " AND cr.product_id=?"
        args.append(product_id)
    sql += " ORDER BY cr.id DESC LIMIT ?"
    args.append(min(limit, 500))
    rows = [dict(r) for r in db.execute(sql, args)]
    release_conn()
    return rows


@router.delete("/costs/{cid}")
def delete_cost(cid: int, u: dict = Depends(current_user)):
    P.require(u, "cost.edit")
    db = get_conn()
    db.execute("DELETE FROM cost_records WHERE id=?", (cid,))
    db.commit()
    release_conn()
    audit(u, "cost", "delete", "cost_record", cid)
    return {"ok": True}


# ==========================================================================
# 生产看板（跨部门：销售能看到进度，生产看到自己该干的）
# ==========================================================================
@router.get("/production-board")
def production_board(u: dict = Depends(current_user)):
    """生产看板：按明细列出当前在产/待产的订单行。"""
    P.require(u, "orders.view")
    db = get_conn()
    rows = [dict(r) for r in db.execute(
        "SELECT oi.id AS item_id, o.id AS order_id, o.order_no, o.delivery_date, "
        "o.status AS order_status, oi.model, oi.sku, oi.name_en, oi.qty, "
        "oi.unit_price, oi.source, oi.production_status, "
        "c.company_en AS customer_name "
        "FROM order_items oi "
        "JOIN orders o ON o.id=oi.order_id "
        "LEFT JOIN customers c ON c.id=o.customer_id "
        "WHERE o.status IN ('confirmed','producing') "
        "ORDER BY CASE oi.production_status WHEN 'producing' THEN 0 "
        "WHEN 'material' THEN 1 WHEN 'quality' THEN 2 ELSE 3 END, o.delivery_date")]
    release_conn()
    today = now()[:10]
    for r in rows:
        r["status_label"] = PRODUCTION_STATUS.get(r["production_status"], r["production_status"])
        r["is_late"] = bool(r["delivery_date"] and r["delivery_date"] < today
                            and r["production_status"] != "finished")
    return {"items": rows,
            "summary": {k: sum(1 for r in rows if r["production_status"] == k)
                        for k in PRODUCTION_STATUS}}
