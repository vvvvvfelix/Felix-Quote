"""模块层 — 出货与出口单证。

外贸特有环节：订舱、提单、箱单发票、报关资料。
跨部门场景下这里最关键的是「出货后自动通知销售」——
客户问发货了吗，不该再靠人去仓库跑一趟。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_user
from ..core import permissions as P
from ..core.db import get_conn, jdump, jload, now, release_conn
from ..core.services import (STATUS_LABEL, assert_transition, audit, close_task,
                             create_task, log_activity, next_no, next_states,
                             timeline)

router = APIRouter(prefix="/api", tags=["logistics"])

DOC_TYPES = {
    "invoice": "商业发票", "packing_list": "装箱单", "bill_of_lading": "提单",
    "customs": "报关单", "cert": "证书", "contract": "合同", "other": "其他",
}


class ShipItemIn(BaseModel):
    order_item_id: int | None = None
    product_id: int | None = None
    description: str | None = None
    qty: float = 1
    carton_qty: int | None = None
    net_weight: float | None = None
    gross_weight: float | None = None
    volume: float | None = None


class ShipmentIn(BaseModel):
    order_id: int
    carrier: str | None = None
    vessel: str | None = None
    tracking_no: str | None = None
    etd: str | None = None
    eta: str | None = None
    remark: str | None = None
    items: list[ShipItemIn] = []


def _hydrate(r) -> dict:
    d = dict(r)
    d["status_label"] = STATUS_LABEL.get(d.get("status"), d.get("status"))
    d["next_states"] = next_states("shipment", d.get("status"))
    d["customer_name"] = d.pop("customer_name", None)
    d["order_no"] = d.pop("order_no", None)
    return d


@router.get("/shipments")
def list_shipments(u: dict = Depends(current_user), status: str | None = None,
                   order_id: int | None = None, limit: int = 50, offset: int = 0):
    P.require(u, "logistics.view")
    db = get_conn()
    sql = ("SELECT sh.*, o.order_no, c.company_en AS customer_name FROM shipments sh "
           "LEFT JOIN orders o ON o.id=sh.order_id "
           "LEFT JOIN customers c ON c.id=o.customer_id WHERE 1=1")
    args: list = []
    if status:
        sql += " AND sh.status=?"
        args.append(status)
    if order_id:
        sql += " AND sh.order_id=?"
        args.append(order_id)
    cnt = db.execute(f"SELECT COUNT(*) c FROM ({sql})", args).fetchone()["c"]
    sql += " ORDER BY sh.id DESC LIMIT ? OFFSET ?"
    args += [min(limit, 200), offset]
    rows = [_hydrate(r) for r in db.execute(sql, args)]
    release_conn()
    return {"items": rows, "total": cnt}


@router.get("/shipments/{sid}")
def get_shipment(sid: int, u: dict = Depends(current_user)):
    P.require(u, "logistics.view")
    db = get_conn()
    r = db.execute(
        "SELECT sh.*, o.order_no, c.company_en AS customer_name FROM shipments sh "
        "LEFT JOIN orders o ON o.id=sh.order_id "
        "LEFT JOIN customers c ON c.id=o.customer_id WHERE sh.id=?", (sid,)).fetchone()
    if not r:
        release_conn()
        raise HTTPException(404, "出货单不存在")
    d = _hydrate(r)
    d["items"] = [dict(x) for x in db.execute(
        "SELECT * FROM shipment_items WHERE shipment_id=? ORDER BY id", (sid,))]
    d["documents"] = [dict(x) for x in db.execute(
        "SELECT * FROM export_documents WHERE shipment_id=? ORDER BY id", (sid,))]
    d["timeline"] = timeline("shipment", sid)
    release_conn()
    return d


@router.post("/shipments")
def create_shipment(body: ShipmentIn, u: dict = Depends(current_user)):
    P.require(u, "logistics.edit")
    db = get_conn()
    o = db.execute("SELECT order_no,status FROM orders WHERE id=?", (body.order_id,)).fetchone()
    if not o:
        release_conn()
        raise HTTPException(404, "订单不存在")
    no = next_no("shipment")
    ts = now()
    cur = db.execute(
        "INSERT INTO shipments(shipment_no,order_id,carrier,vessel,tracking_no,etd,eta,"
        "status,remark,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'planned',?,?,?)",
        (no, body.order_id, body.carrier, body.vessel, body.tracking_no,
         body.etd, body.eta, body.remark, ts, ts))
    sid = cur.lastrowid
    for i in body.items:
        db.execute(
            "INSERT INTO shipment_items(shipment_id,order_item_id,product_id,description,"
            "qty,carton_qty,net_weight,gross_weight,volume) VALUES(?,?,?,?,?,?,?,?,?)",
            (sid, i.order_item_id, i.product_id, i.description, i.qty,
             i.carton_qty, i.net_weight, i.gross_weight, i.volume))
    db.commit()
    release_conn()
    log_activity("shipment", sid, "create", u, f"创建出货单 {no}")
    audit(u, "shipment", "create", "shipment", sid, no, after=body.model_dump())
    return {"id": sid, "shipment_no": no}


class ShipmentUp(BaseModel):
    carrier: str | None = None
    vessel: str | None = None
    tracking_no: str | None = None
    bl_no: str | None = None
    container_no: str | None = None
    etd: str | None = None
    eta: str | None = None
    remark: str | None = None


@router.put("/shipments/{sid}")
def update_shipment(sid: int, body: ShipmentUp, u: dict = Depends(current_user)):
    P.require(u, "logistics.edit")
    db = get_conn()
    before = db.execute("SELECT * FROM shipments WHERE id=?", (sid,)).fetchone()
    if not before:
        release_conn()
        raise HTTPException(404, "出货单不存在")
    db.execute(
        "UPDATE shipments SET carrier=?,vessel=?,tracking_no=?,bl_no=?,container_no=?,"
        "etd=?,eta=?,remark=?,updated_at=? WHERE id=?",
        (body.carrier, body.vessel, body.tracking_no, body.bl_no, body.container_no,
         body.etd, body.eta, body.remark, now(), sid))
    db.commit()
    release_conn()
    audit(u, "shipment", "update", "shipment", sid, before["shipment_no"],
          before=dict(before), after=body.model_dump())
    return {"ok": True}


class ShipStatusIn(BaseModel):
    status: str
    remark: str | None = None


@router.post("/shipments/{sid}/status")
def shipment_status(sid: int, body: ShipStatusIn, u: dict = Depends(current_user)):
    P.require(u, "logistics.edit")
    db = get_conn()
    r = db.execute(
        "SELECT sh.*, o.owner_id, o.dept_id, o.order_no FROM shipments sh "
        "LEFT JOIN orders o ON o.id=sh.order_id WHERE sh.id=?", (sid,)).fetchone()
    if not r:
        release_conn()
        raise HTTPException(404, "出货单不存在")
    try:
        assert_transition("shipment", r["status"], body.status)
    except HTTPException:
        release_conn()
        raise
    db.execute("UPDATE shipments SET status=?,updated_at=? WHERE id=?",
               (body.status, now(), sid))
    db.commit()
    release_conn()
    log_activity("shipment", sid, "status", u, body.remark or "", r["status"], body.status)

    # 实际装船 → 自动推进订单到已出货，并派活给销售催款
    if body.status == "shipped":
        db2 = get_conn()
        db2.execute("UPDATE orders SET status='shipped',updated_at=? WHERE id=?",
                    (now(), r["order_id"]))
        db2.commit()
        release_conn()
        if r["owner_id"]:
            create_task("order", r["order_id"], "receive",
                        f"订单 {r['order_no']} 已装船 {r['etd'] or ''}，请跟进尾款",
                        r["owner_id"], r["dept_id"], None,
                        f"船名 {r['vessel'] or '-'} / 提单 {r['bl_no'] or '-'}",
                        "high", u["id"])
            log_activity("order", r["order_id"], "shipped_notify", u,
                         f"出货单 {r['shipment_no']} 已装船，已通知销售跟进收款")
    audit(u, "shipment", "status", "shipment", sid, r["shipment_no"],
          before={"status": r["status"]}, after={"status": body.status})
    return {"ok": True}


class DocIn(BaseModel):
    doc_type: str
    doc_no: str | None = None
    issued_at: str | None = None
    remark: str | None = None


@router.post("/shipments/{sid}/documents")
def add_document(sid: int, body: DocIn, u: dict = Depends(current_user)):
    P.require(u, "logistics.edit")
    if body.doc_type not in DOC_TYPES:
        raise HTTPException(400, f"未知单证类型 {body.doc_type}")
    db = get_conn()
    sh = db.execute("SELECT shipment_no FROM shipments WHERE id=?", (sid,)).fetchone()
    if not sh:
        release_conn()
        raise HTTPException(404, "出货单不存在")
    db.execute(
        "INSERT INTO export_documents(shipment_id,doc_type,doc_no,issued_at,remark,created_at)"
        " VALUES(?,?,?,?,?,?)",
        (sid, body.doc_type, body.doc_no, body.issued_at or now()[:10],
         body.remark, now()))
    db.commit()
    release_conn()
    log_activity("shipment", sid, "document", u,
                 f"上传单证：{DOC_TYPES[body.doc_type]} {body.doc_no or ''}")
    audit(u, "shipment", "add_document", "shipment", sid, sh["shipment_no"],
          after=body.model_dump())
    return {"ok": True}


@router.delete("/documents/{did}")
def delete_document(did: int, u: dict = Depends(current_user)):
    P.require(u, "logistics.edit")
    db = get_conn()
    db.execute("DELETE FROM export_documents WHERE id=?", (did,))
    db.commit()
    release_conn()
    audit(u, "shipment", "delete_document", "export_document", did)
    return {"ok": True}
