"""模块层 — 销售订单与跨部门确认。

跨部门协作的核心载体。真实场景的三处卡点在这里解决：
1. 报价转订单（价格快照带入，不受后续改价影响）
2. 技术确认交期 —— 销售不能自己拍脑袋报交期
3. 超阈值价格审批 —— 业务员自己降价没人知道
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import quote as quote_engine
from ..auth import current_user
from ..core import permissions as P
from ..core.db import get_conn, jdump, jload, now, release_conn
from ..core.services import (STATUS_LABEL, assert_transition, audit, bump_version,
                             check_version, close_task, create_task, log_activity,
                             next_no, next_states, timeline)

router = APIRouter(prefix="/api/orders", tags=["order"])

# 审批阈值：超过任一条就需经理审批
APPROVE_RULES = {
    "discount_rate": 5.0,      # 折扣 >5%
    "total_amount": 50000,     # 金额 >5 万美元
    "payment_days": 45,        # 账期 >45 天
}
APPROVAL_THRESHOLD = 50000


class OrderItemIn(BaseModel):
    product_id: int | None = None
    sku: str | None = None
    model: str | None = None
    name_en: str | None = None
    spec_en: str | None = None
    qty: float = 1
    unit_price: float = 0
    source: str = "own"
    remark: str | None = None


class OrderIn(BaseModel):
    customer_id: int | None = None
    quotation_id: int | None = None
    inquiry_id: int | None = None
    po_number: str | None = None
    order_date: str | None = None
    delivery_date: str | None = None
    currency: str = "USD"
    payment_terms: str | None = None
    incoterm: str | None = None
    port: str | None = None
    remark: str | None = None
    items: list[OrderItemIn] = []


def _hydrate(r) -> dict:
    d = dict(r)
    d["status_label"] = STATUS_LABEL.get(d.get("status"), d.get("status"))
    d["next_states"] = next_states("order", d.get("status"))
    if d.get("total_amount"):
        d["gross_profit"] = round((d["total_amount"] or 0) - (d["cost_amount"] or 0), 2)
        rate = d["gross_profit"] / d["total_amount"] * 100 if d["total_amount"] else 0
        d["gross_rate"] = round(rate, 1)
    return d


def _need_approval(total: float, discount: float, payment_days: int) -> list:
    reasons = []
    if discount > APPROVE_RULES["discount_rate"]:
        reasons.append(f"折扣 {discount}% 超过 {APPROVE_RULES['discount_rate']}%")
    if total > APPROVE_RULES["total_amount"]:
        reasons.append(f"金额 {total:,.0f} 超过 {APPROVE_RULES['total_amount']:,.0f}")
    if payment_days > APPROVE_RULES["payment_days"]:
        reasons.append(f"账期 {payment_days} 天超过 {APPROVE_RULES['payment_days']} 天")
    return reasons


@router.get("")
def list_orders(u: dict = Depends(current_user), status: str | None = None,
                keyword: str | None = None, approval: str | None = None,
                limit: int = 50, offset: int = 0):
    P.require(u, "orders.view")
    db = get_conn()
    sql = ("SELECT o.*, c.company_en AS customer_name, us.full_name AS owner_name, "
           "d.name AS dept_name FROM orders o "
           "LEFT JOIN customers c ON c.id=o.customer_id "
           "LEFT JOIN users us ON us.id=o.owner_id "
           "LEFT JOIN departments d ON d.id=o.dept_id WHERE 1=1")
    args: list = []
    frag, pargs = P.scope_clause(u, alias="o")
    sql += frag
    args += pargs
    if status:
        sql += " AND o.status=?"
        args.append(status)
    if approval:
        sql += " AND o.approval_status=?"
        args.append(approval)
    if keyword:
        like = f"%{keyword}%"
        sql += (" AND (o.order_no LIKE ? OR o.po_number LIKE ? "
                "OR c.company_en LIKE ?)")
        args += [like] * 3
    cnt = db.execute(f"SELECT COUNT(*) c FROM ({sql})", args).fetchone()["c"]
    sql += " ORDER BY o.id DESC LIMIT ? OFFSET ?"
    args += [min(limit, 200), offset]
    rows = [_hydrate(r) for r in db.execute(sql, args)]
    release_conn()
    return {"items": rows, "total": cnt}


@router.get("/stats")
def order_stats(u: dict = Depends(current_user)):
    P.require(u, "orders.view")
    db = get_conn()
    frag, args = P.scope_clause(u, alias="o")
    by_status = {r["status"]: r["n"] for r in db.execute(
        f"SELECT status, COUNT(*) n FROM orders WHERE 1=1{frag} GROUP BY status", args)}
    money = db.execute(
        f"SELECT IFNULL(SUM(total_amount),0) amt, IFNULL(SUM(cost_amount),0) cost "
        f"FROM orders WHERE status NOT IN ('canceled','draft'){frag}", args).fetchone()
    pending = db.execute(
        f"SELECT COUNT(*) c FROM orders WHERE approval_status='pending'{frag}",
        args).fetchone()["c"]
    release_conn()
    amt = money["amt"] or 0
    cost = money["cost"] or 0
    return {
        "by_status": by_status, "total_amount": round(amt, 2),
        "cost_amount": round(cost, 2),
        "gross_profit": round(amt - cost, 2),
        "gross_rate": round((amt - cost) / amt * 100, 1) if amt else 0,
        "pending_approval": pending,
    }


@router.get("/{oid}")
def get_order(oid: int, u: dict = Depends(current_user)):
    P.require(u, "orders.view")
    db = get_conn()
    r = db.execute(
        "SELECT o.*, c.company_en AS customer_company, c.country, "
        "us.full_name AS owner_name, d.name AS dept_name FROM orders o "
        "LEFT JOIN customers c ON c.id=o.customer_id "
        "LEFT JOIN users us ON us.id=o.owner_id "
        "LEFT JOIN departments d ON d.id=o.dept_id WHERE o.id=?", (oid,)).fetchone()
    if not r:
        release_conn()
        raise HTTPException(404, "订单不存在")
    d = _hydrate(r)
    d["items"] = [dict(x) for x in db.execute(
        "SELECT * FROM order_items WHERE order_id=? ORDER BY id", (oid,))]
    d["timeline"] = timeline("order", oid)
    d["purchase_orders"] = [dict(x) for x in db.execute(
        "SELECT id,po_no,supplier_id,total_amount,status FROM purchase_orders "
        "WHERE order_id=?", (oid,))]
    d["shipments"] = [dict(x) for x in db.execute(
        "SELECT id,shipment_no,carrier,etd,eta,status FROM shipments WHERE order_id=?",
        (oid,))]
    d["receivable"] = [dict(x) for x in db.execute(
        "SELECT id,receivable_no,amount,received_amount,due_date,status "
        "FROM receivables WHERE order_id=?", (oid,))]
    if not P.visible(u, d):
        release_conn()
        raise HTTPException(403, "没有查看这张订单的权限")
    release_conn()
    return d


@router.post("")
def create_order(body: OrderIn, u: dict = Depends(current_user)):
    """建订单。两种来源：直接建，或从报价单一键转订单。"""
    P.require(u, "orders.edit")
    db = get_conn()
    items = body.items
    total = sum(i.qty * i.unit_price for i in items)
    # 成本快照：有成本记录则带入
    cost = 0
    for i in items:
        if i.product_id:
            c = db.execute(
                "SELECT AVG(cost_cny) a FROM cost_records WHERE product_id=?",
                (i.product_id,)).fetchone()["a"]
            if c:
                cost += c * i.qty
    disc = 0
    if body.quotation_id:
        q = db.execute("SELECT discount_rate FROM quotations WHERE id=?",
                       (body.quotation_id,)).fetchone()
        disc = float(q["discount_rate"] or 0) if q else 0
    days = 0
    if body.payment_terms:
        for token in str(body.payment_terms).replace("，", ",").split(","):
            t = "".join(ch for ch in token if ch.isdigit())
            if t:
                days = max(days, int(t))
    reasons = _need_approval(total, disc, days)

    no = next_no("order")
    ts = now()
    cur = db.execute(
        "INSERT INTO orders(order_no,inquiry_id,quotation_id,customer_id,po_number,"
        "order_date,delivery_date,currency,total_amount,cost_amount,payment_terms,"
        "incoterm,port,status,approval_status,owner_id,dept_id,remark,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'draft',?,?,?,?,?,?)",
        (no, body.inquiry_id, body.quotation_id, body.customer_id, body.po_number,
         body.order_date or ts[:10], body.delivery_date, body.currency,
         round(total, 2), round(cost, 2), body.payment_terms, body.incoterm,
         body.port, "pending" if reasons else "approved",
         u["id"], u.get("dept_id"), body.remark, ts, ts))
    oid = cur.lastrowid
    for i in items:
        db.execute(
            "INSERT INTO order_items(order_id,product_id,sku,model,name_en,spec_en,"
            "qty,unit_price,amount,cost_price,source,remark) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (oid, i.product_id, i.sku, i.model, i.name_en, i.spec_en, i.qty,
             i.unit_price, round(i.qty * i.unit_price, 2),
             round(total and cost / total * i.unit_price or 0, 2),
             i.source, i.remark))
    if body.quotation_id:
        db.execute("UPDATE quotations SET status='confirmed' WHERE id=?",
                   (body.quotation_id,))
    if body.inquiry_id:
        db.execute("UPDATE inquiries SET status='won',quotation_id=? WHERE id=?",
                   (body.quotation_id, body.inquiry_id))
        close_task("inquiry", body.inquiry_id, None, "已转订单", u["id"])
    db.commit()
    release_conn()

    log_activity("order", oid, "create", u, f"创建订单 {no}")
    audit(u, "order", "create", "order", oid, no, after=body.model_dump())

    # 需要审批 → 生成审批待办给销售经理
    if reasons:
        mgr = db_role_user("sales_manager")
        create_task("order", oid, "approve", f"订单 {no} 待审批：{'；'.join(reasons)}",
                    mgr, u.get("dept_id"), None, "；".join(reasons),
                    "high", u["id"])
        log_activity("order", oid, "approval_request", u, "；".join(reasons))
    else:
        log_activity("order", oid, "auto_approved", u, "未触发审批阈值")
    return {"id": oid, "order_no": no, "need_approval": bool(reasons),
            "approval_reasons": reasons}


def db_role_user(role: str) -> int | None:
    """找该角色下第一个启用的人（用于自动派活）。"""
    db = get_conn()
    r = db.execute("SELECT id FROM users WHERE role=? AND is_active=1 LIMIT 1",
                   (role,)).fetchone()
    return r["id"] if r else None


def dept_by_code(code: str) -> int | None:
    db = get_conn()
    r = db.execute("SELECT id FROM departments WHERE code=? AND is_active=1",
                   (code,)).fetchone()
    return r["id"] if r else None


class ApprovalIn(BaseModel):
    action: str        # approve / reject
    comment: str | None = None


@router.post("/{oid}/approval")
def approve_order(oid: int, body: ApprovalIn, u: dict = Depends(current_user)):
    P.require(u, "quotations.approve")
    db = get_conn()
    r = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not r:
        release_conn()
        raise HTTPException(404, "订单不存在")
    if r["approval_status"] != "pending":
        release_conn()
        raise HTTPException(400, "该订单不在待审批状态")
    ok = body.action == "approve"
    db.execute("UPDATE orders SET approval_status=?,updated_at=? WHERE id=?",
               ("approved" if ok else "rejected", now(), oid))
    bump_version(db, "orders", oid, u["id"])
    db.commit()
    release_conn()
    close_task("order", oid, "approve", body.comment or "", u["id"])
    log_activity("order", oid, "approval", u, body.comment or "",
                 "pending", "approved" if ok else "rejected")
    audit(u, "order", body.action, "order", oid, r["order_no"],
          before={"approval_status": "pending"},
          after={"approval_status": "approved" if ok else "rejected"})
    if ok:
        # 派给技术部（按部门派活，具体人由技术部内部决定）
        tech_dept = dept_by_code("TECH")
        create_task("order", oid, "tech_delivery", f"订单 {r['order_no']} 请技术确认交期",
                    db_role_user("tech"), tech_dept, None,
                    "确认实际可承诺交期", "high", u["id"])
    return {"ok": True}


class DeliveryIn(BaseModel):
    delivery_date: str
    remark: str | None = None
    reject: bool = False


@router.post("/{oid}/confirm-delivery")
def confirm_delivery(oid: int, body: DeliveryIn, u: dict = Depends(current_user)):
    """技术/生产确认交期。这是跨部门协作里最常卡的一环。

    销售报的是承诺给客户的交期，必须生产确认后才作数；
    生产可以驳回并给出实际交期，销售需要重新跟客户沟通。
    """
    P.require(u, "orders.tech_confirm")
    db = get_conn()
    r = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not r:
        release_conn()
        raise HTTPException(404, "订单不存在")
    if r["approval_status"] == "pending":
        release_conn()
        raise HTTPException(400, "订单尚未审批通过，不能确认交期")
    if body.reject:
        db.execute("UPDATE orders SET updated_at=? WHERE id=?", (now(), oid))
        bump_version(db, "orders", oid, u["id"])
        db.commit()
        release_conn()
        close_task("order", oid, "tech_delivery", "无法承诺该交期：" + (body.remark or ""), u["id"])
        log_activity("order", oid, "delivery_rejected", u, body.remark or "")
        audit(u, "order", "delivery_reject", "order", oid, r["order_no"],
              after={"remark": body.remark})
        create_task("order", oid, "followup", f"订单 {r['order_no']} 交期被驳回，请重新跟客户确认",
                    r["owner_id"], r["dept_id"], None,
                    body.remark or "生产无法承诺原交期", "urgent", u["id"])
        return {"ok": True, "rejected": True}
    db.execute("UPDATE orders SET delivery_date=?,updated_at=? WHERE id=?",
               (body.delivery_date, now(), oid))
    bump_version(db, "orders", oid, u["id"])
    db.commit()
    release_conn()
    close_task("order", oid, "tech_delivery", "确认交期 " + body.delivery_date, u["id"])
    log_activity("order", oid, "delivery_confirmed", u,
                 f"确认交期 {body.delivery_date} {body.remark or ''}")
    audit(u, "order", "delivery_confirm", "order", oid, r["order_no"],
          before={"delivery_date": r["delivery_date"]},
          after={"delivery_date": body.delivery_date})
    return {"ok": True, "delivery_date": body.delivery_date}


@router.put("/{oid}")
def update_order(oid: int, body: OrderIn, u: dict = Depends(current_user),
                 version: int | None = None):
    P.require(u, "orders.edit")
    db = get_conn()
    before = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not before:
        release_conn()
        raise HTTPException(404, "订单不存在")
    if before["status"] not in ("draft", "confirming"):
        release_conn()
        raise HTTPException(400, "订单已进入后续阶段，不能再修改基本信息")
    try:
        check_version(db, "orders", oid, version)
    except Exception as e:
        release_conn()
        if type(e).__name__ == "ConflictError":
            raise HTTPException(409, str(e))
        raise
    total = sum(i.qty * i.unit_price for i in body.items)
    db.execute(
        "UPDATE orders SET po_number=?,order_date=?,delivery_date=?,currency=?,"
        "payment_terms=?,incoterm=?,port=?,total_amount=?,remark=?,updated_at=? WHERE id=?",
        (body.po_number, body.order_date, body.delivery_date, body.currency,
         body.payment_terms, body.incoterm, body.port, round(total, 2),
         body.remark, now(), oid))
    bump_version(db, "orders", oid, u["id"])
    db.commit()
    release_conn()
    audit(u, "order", "update", "order", oid, before["order_no"],
          before=dict(before), after=body.model_dump())
    return {"ok": True}


class StatusIn(BaseModel):
    status: str
    remark: str | None = None


@router.post("/{oid}/status")
def change_status(oid: int, body: StatusIn, u: dict = Depends(current_user)):
    P.require(u, "orders.edit")
    db = get_conn()
    r = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not r:
        release_conn()
        raise HTTPException(404, "订单不存在")
    if body.status == "producing" and r["approval_status"] != "approved":
        release_conn()
        raise HTTPException(400, "订单未审批通过，不能进入生产")
    try:
        assert_transition("order", r["status"], body.status)
    except HTTPException:
        release_conn()
        raise
    db.execute("UPDATE orders SET status=?,updated_at=? WHERE id=?",
               (body.status, now(), oid))
    bump_version(db, "orders", oid, u["id"])
    db.commit()
    release_conn()
    log_activity("order", oid, "status", u, body.remark or "", r["status"], body.status)
    # 状态推进自动派活
    if body.status == "producing":
        create_task("order", oid, "production", f"订单 {r['order_no']} 需安排生产",
                    None, None, r["delivery_date"], "排产并跟进进度", "high", u["id"])
    elif body.status == "shipped":
        create_task("order", oid, "receive", f"订单 {r['order_no']} 已出货，跟踪收款",
                    r["owner_id"], r["dept_id"], None, "催收尾款", "normal", u["id"])
    elif body.status == "completed":
        create_task("order", oid, "receive", f"订单 {r['order_no']} 已完成，确认结清",
                    r["owner_id"], r["dept_id"], None, "确认款项是否到账", "high", u["id"])
    audit(u, "order", "status", "order", oid, r["order_no"],
          before={"status": r["status"]}, after={"status": body.status})
    return {"ok": True}


class FromQuoteIn(BaseModel):
    quotation_id: int
    po_number: str | None = None
    delivery_date: str | None = None
    remark: str | None = None


@router.post("/from-quotation")
def create_from_quotation(body: FromQuoteIn, u: dict = Depends(current_user)):
    """报价单一键转订单：价格快照带入，后续改报价不影响已成交订单。"""
    P.require(u, "orders.edit")
    db = get_conn()
    q = db.execute("SELECT * FROM quotations WHERE id=?", (body.quotation_id,)).fetchone()
    if not q:
        release_conn()
        raise HTTPException(404, "报价单不存在")
    items = jload(q["items_json"], [])
    if not items:
        release_conn()
        raise HTTPException(400, "该报价单没有明细")
    order = OrderIn(
        customer_id=q["customer_id"], quotation_id=q["id"], inquiry_id=q["inquiry_id"],
        po_number=body.po_number, order_date=now()[:10],
        delivery_date=body.delivery_date, currency=q["currency"],
        payment_terms=q["payment_terms"], incoterm=q["incoterm"],
        port=q["port"], remark=body.remark,
        items=[OrderItemIn(
            product_id=i.get("product_id"), sku=i.get("sku"), model=i.get("model"),
            name_en=i.get("name_en"), spec_en=i.get("spec_en"),
            qty=float(i.get("qty") or 1), unit_price=float(i.get("price") or 0),
            source=i.get("source") or "own") for i in items],
    )
    return create_order(order, u)


@router.delete("/{oid}")
def delete_order(oid: int, u: dict = Depends(current_user)):
    P.require(u, "orders.edit")
    db = get_conn()
    r = db.execute("SELECT order_no,status FROM orders WHERE id=?", (oid,)).fetchone()
    if not r:
        release_conn()
        raise HTTPException(404, "订单不存在")
    if r["status"] not in ("draft", "canceled"):
        release_conn()
        raise HTTPException(400, "已推进的订单不能删除，请改为取消")
    db.execute("DELETE FROM order_items WHERE order_id=?", (oid,))
    db.execute("DELETE FROM orders WHERE id=?", (oid,))
    db.commit()
    release_conn()
    audit(u, "order", "delete", "order", oid, r["order_no"])
    return {"ok": True}


class ProductionIn(BaseModel):
    item_id: int
    production_status: str
    remark: str | None = None


@router.post("/{oid}/production")
def update_production(oid: int, body: ProductionIn, u: dict = Depends(current_user)):
    """更新单行生产进度。生产部逐行推进，销售在工作台就能看到。"""
    P.require(u, "orders.production")
    db = get_conn()
    o = db.execute("SELECT order_no,status FROM orders WHERE id=?", (oid,)).fetchone()
    if not o:
        release_conn()
        raise HTTPException(404, "订单不存在")
    it = db.execute("SELECT * FROM order_items WHERE id=? AND order_id=?",
                    (body.item_id, oid)).fetchone()
    if not it:
        release_conn()
        raise HTTPException(404, "订单明细不存在")
    db.execute("UPDATE order_items SET production_status=?,remark=? WHERE id=?",
               (body.production_status, body.remark, body.item_id))
    # 全部完工则整单推进到生产完成
    pend = db.execute(
        "SELECT COUNT(*) c FROM order_items WHERE order_id=? "
        "AND production_status!='finished'", (oid,)).fetchone()["c"]
    if pend == 0 and o["status"] == "producing":
        db.execute("UPDATE orders SET status='shipped',updated_at=? WHERE id=?",
                   (now(), oid))
        log_activity("order", oid, "production_done", u, "全部明细已完工")
    db.commit()
    release_conn()
    log_activity("order", oid, "production", u,
                 f"{it['model'] or it['sku']} → {body.production_status}")
    audit(u, "order", "production", "order", oid, o["order_no"],
          before={"production_status": it["production_status"]},
          after={"production_status": body.production_status})
    return {"ok": True, "all_done": pend == 0}
