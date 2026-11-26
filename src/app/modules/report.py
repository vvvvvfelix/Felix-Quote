"""模块层 — 报表与打印。

覆盖每个岗位日常都会碰到的纸面单据与报销凭证：
- 报价单 / 送货单：直接复用既有业务数据（报价、订单）生成可打印视图
- 行程单：行政/销售出差报销用
- 发票：很多公司要求「一张 A4 打印两行 A5 票」，提供 A4 双联排版
- 水单报销：微信/支付宝付款凭证截图导入 → 自动排版到合适大小 →
  可选脱敏（遮挡金额/姓名）→ 直接走浏览器打印（连打印机）

后端只负责：报表定义、水单影像归档、脱敏标注的存取、打印数据源。
真正的排版与打印由前端 HTML/CSS（@media print）完成 —— 零额外依赖、
所见即所得、直接调起系统打印机。
"""
import os

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from ..auth import current_user
from ..core import permissions as P
from ..core.db import get_conn, jdump, jload, now
from ..core.paths import DATA_DIR
from ..core.services import save_slip

router = APIRouter(tags=["report"])

# 影像目录：与 core/services.SLIPS_DIR 同源。这里保留一份是为了模块自解释与
# 启动时预建目录，真正落盘由 save_slip 统一负责，避免两套路径各写各的。
SLIPS_DIR = os.path.join(DATA_DIR, "slips")
os.makedirs(SLIPS_DIR, exist_ok=True)

# 报表类型定义（数据源说明用于前端引导，不强制绑定）
REPORT_TYPES = [
    {"key": "quotation", "name": "报价单", "group": "销售",
     "source": "报价管理", "desc": "从已有报价单生成打印视图，可带公司抬头与水印"},
    {"key": "delivery", "name": "送货单", "group": "供应链",
     "source": "销售订单", "desc": "按订单生成送货/出库单，含收货签收栏"},
    {"key": "itinerary", "name": "行程单", "group": "行政",
     "source": "手工录入", "desc": "出差行程与交通明细，用于差旅报销"},
    {"key": "invoice", "name": "发票", "group": "财务",
     "source": "手工录入", "desc": "支持 A4 单张 / A4 双联（上下两张 A5）排版"},
    {"key": "slip", "name": "水单报销", "group": "财务",
     "source": "影像导入", "desc": "微信/支付宝付款水单截图导入，自动排版+可选脱敏"},
]


# --------------------------------------------------------------------------
# 报表类型与打印数据源
# --------------------------------------------------------------------------
@router.get("/api/report/types")
def api_report_types(u: dict = Depends(current_user)):
    P.require(u, "reports.view")
    return REPORT_TYPES


@router.get("/api/report/print-data/quotation/{qid}")
def api_pd_quotation(qid: int, u: dict = Depends(current_user)):
    """报价单打印数据。"""
    P.require(u, "reports.view")
    db = get_conn()
    q = db.execute("SELECT * FROM quotations WHERE id=?", (qid,)).fetchone()
    if not q:
        raise HTTPException(404, "报价单不存在")
    d = dict(q)
    d["items"] = jload(d.get("items_json"), [])
    return d


@router.get("/api/report/print-data/order/{oid}")
def api_pd_order(oid: int, u: dict = Depends(current_user)):
    """送货单打印数据（取订单及客户信息）。"""
    P.require(u, "reports.view")
    db = get_conn()
    o = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not o:
        raise HTTPException(404, "订单不存在")
    d = dict(o)
    cust = db.execute("SELECT company_en,contact,country,address FROM customers WHERE id=?",
                      (o.get("customer_id"),)).fetchone()
    d["customer"] = dict(cust) if cust else {}
    return d


# --------------------------------------------------------------------------
# 水单（影像）归档 + 脱敏标注
# --------------------------------------------------------------------------
@router.post("/api/report/slips/upload")
async def api_slip_upload(files: list[UploadFile] = File(...),
                          u: dict = Depends(current_user)):
    """批量上传微信/支付宝水单图片，存入 data/slips，返回 id 列表。

    落盘与入库统一走 core/services.save_slip —— M071 电子报销的票据影像挂在
    同一张 report_slips 表、同一个 data/slips 目录，两个模块共用一套存储，
    避免出现「同一张水单存两遍、归档各找各的」的割裂。
    """
    P.require(u, "reports.edit")
    out = []
    for f in files:
        try:
            out.append(save_slip(f.filename or "", f.file, u["id"]))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    return out


@router.get("/api/report/slips")
def api_slip_list(u: dict = Depends(current_user)):
    P.require(u, "reports.view")
    db = get_conn()
    rows = [dict(r) for r in db.execute(
        "SELECT id,filename,size,memo,mask_json,created_at FROM report_slips "
        "WHERE deleted_at IS NULL ORDER BY id DESC")]
    for r in rows:
        r["url"] = f"/api/report/slips/{r['id']}/file"
        r["mask"] = jload(r.pop("mask_json"), [])
    return rows


@router.get("/api/report/slips/{sid}/file")
def api_slip_file(sid: int, u: dict = Depends(current_user)):
    """直接返回水单原图（打印/预览用）。"""
    P.require(u, "reports.view")
    db = get_conn()
    r = db.execute("SELECT stored_path FROM report_slips WHERE id=? AND deleted_at IS NULL",
                   (sid,)).fetchone()
    if not r or not os.path.exists(r["stored_path"]):
        raise HTTPException(404, "水单不存在")
    ext = os.path.splitext(r["stored_path"])[1].lower().lstrip(".")
    ctype = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
             "webp": "image/webp"}.get(ext, "application/octet-stream")
    from fastapi.responses import FileResponse
    return FileResponse(r["stored_path"], media_type=ctype)


@router.post("/api/report/slips/{sid}/mask")
def api_slip_mask(sid: int, body: dict, u: dict = Depends(current_user)):
    """保存脱敏标注（归一化坐标矩形列表 [{x,y,w,h}]，0~1）。"""
    P.require(u, "reports.edit")
    db = get_conn()
    if not db.execute("SELECT 1 FROM report_slips WHERE id=? AND deleted_at IS NULL",
                      (sid,)).fetchone():
        raise HTTPException(404, "水单不存在")
    mask = body.get("mask")
    if not isinstance(mask, list):
        raise HTTPException(400, "mask 必须是矩形列表")
    db.execute("UPDATE report_slips SET mask_json=? WHERE id=?", (jdump(mask), sid))
    db.commit()
    return {"ok": True}


@router.delete("/api/report/slips/{sid}")
def api_slip_delete(sid: int, u: dict = Depends(current_user)):
    """软删除水单。"""
    P.require(u, "reports.edit")
    db = get_conn()
    db.execute("UPDATE report_slips SET deleted_at=? WHERE id=?", (now(), sid))
    db.commit()
    return {"ok": True}
