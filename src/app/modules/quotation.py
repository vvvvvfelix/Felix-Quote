"""模块层 — 报价：报价单、PDF 模板、客户自带模板、文件生成。

客户模板（按对方给的格式出单）与报价单强相关，故与报价单同模块。
"""
import os
import shutil
from datetime import datetime
from typing import Any

from fastapi import (APIRouter, Depends, File, Form, HTTPException, UploadFile)
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

from .. import client_tpl
from .. import quote as quote_engine
from ..auth import current_user
from ..core.db import get_conn, jdump, jload, now, release_conn
from ..core.paths import UPLOAD_DIR, uuid_like

router = APIRouter(tags=["quotation"])


# ==========================================================================
# 报价单
# ==========================================================================
class QuoteIn(BaseModel):
    customer_id: int | None = None
    title: str | None = None
    lang: str = "en"
    template_key: str = "standard_en"
    incoterm: str | None = "FOB Guangzhou"
    port: str | None = "Guangzhou / Shenzhen"
    currency: str = "USD"
    payment_terms: str | None = None
    valid_days: int = 30
    freight: float = 0
    insurance: float = 0
    discount_rate: float = 0
    remark: str | None = None
    items: list[dict] = []
    status: str = "draft"


@router.get("/api/quotations")
def api_quotations(u: dict = Depends(current_user)):
    db = get_conn()
    rows = [dict(r) for r in db.execute(
        "SELECT q.id,q.quote_no,q.customer_id,q.title,q.lang,q.template_key,q.currency,q.total_amount,"
        "q.status,q.created_at,c.company_en AS customer_name "
        "FROM quotations q LEFT JOIN customers c ON c.id=q.customer_id ORDER BY q.id DESC")]
    return rows


@router.get("/api/quotations/{qid}")
def api_quotation(qid: int, u: dict = Depends(current_user)):
    db = get_conn()
    r = db.execute("SELECT * FROM quotations WHERE id=?", (qid,)).fetchone()
    if not r:
        raise HTTPException(404, "报价单不存在")
    d = dict(r)
    d["items"] = jload(d.pop("items_json"), [])
    if d.get("customer_id"):
        c = db.execute("SELECT * FROM customers WHERE id=?", (d["customer_id"],)).fetchone()
        d["customer"] = dict(c) if c else None
    else:
        d["customer"] = None
    return d


@router.post("/api/quotations")
def api_quotation_create(body: QuoteIn, u: dict = Depends(current_user)):
    db = get_conn()
    qno = quote_engine.next_quote_no()
    ts = now()
    items = body.items
    totals = quote_engine.calc_totals(items, body.freight, body.insurance, body.discount_rate)
    db.execute(
        """INSERT INTO quotations(quote_no,customer_id,title,lang,template_key,incoterm,port,currency,
           payment_terms,valid_days,freight,insurance,discount_rate,remark,items_json,total_amount,
           status,owner_id,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (qno, body.customer_id, body.title, body.lang, body.template_key, body.incoterm, body.port,
         body.currency, body.payment_terms or "30% T/T deposit, 70% balance before shipment",
         body.valid_days, body.freight, body.insurance, body.discount_rate, body.remark,
         jdump(items), totals["total"], body.status, u["id"], ts, ts))
    db.commit()
    qid = db.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    return {"id": qid, "quote_no": qno, "total": totals["total"]}


@router.put("/api/quotations/{qid}")
def api_quotation_update(qid: int, body: QuoteIn, u: dict = Depends(current_user)):
    db = get_conn()
    if not db.execute("SELECT 1 FROM quotations WHERE id=?", (qid,)).fetchone():
        raise HTTPException(404, "报价单不存在")
    totals = quote_engine.calc_totals(body.items, body.freight, body.insurance, body.discount_rate)
    db.execute(
        """UPDATE quotations SET customer_id=?,title=?,lang=?,template_key=?,incoterm=?,port=?,
           currency=?,payment_terms=?,valid_days=?,freight=?,insurance=?,discount_rate=?,remark=?,
           items_json=?,total_amount=?,status=?,updated_at=? WHERE id=?""",
        (body.customer_id, body.title, body.lang, body.template_key, body.incoterm, body.port,
         body.currency, body.payment_terms, body.valid_days, body.freight, body.insurance,
         body.discount_rate, body.remark, jdump(body.items), totals["total"], body.status, now(), qid))
    db.commit()
    return {"ok": True, "total": totals["total"]}


@router.delete("/api/quotations/{qid}")
def api_quotation_delete(qid: int, u: dict = Depends(current_user)):
    db = get_conn()
    db.execute("DELETE FROM quotations WHERE id=?", (qid,))
    db.commit()
    return {"ok": True}


@router.get("/api/quotations/{qid}/preview")
def api_quotation_preview(qid: int, u: dict = Depends(current_user)):
    html, _ = quote_engine.render(qid)
    return HTMLResponse(html)


@router.get("/api/quotations/{qid}/pdf")
def api_quotation_pdf(qid: int, inline: int = 0, u: dict = Depends(current_user)):
    """生成报价单 PDF。

    inline=1 → inline  disposition，浏览器内直接预览（不下载）
    默认        → attachment disposition，下载到本地
    """
    try:
        data = quote_engine.html_to_pdf(qid)
    except ValueError as exc:
        # render() 对不存在的单号抛 ValueError —— 是 404 不是 500
        raise HTTPException(404, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"PDF 生成失败：{exc}")
    db = get_conn()
    row = db.execute("SELECT quote_no FROM quotations WHERE id=?", (qid,)).fetchone()
    name = (row["quote_no"] if row else f"quote-{qid}") + ".pdf"
    disp = "inline" if inline else "attachment"
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": f'{disp}; filename="{name}"'})


# ==========================================================================
# PDF 模板（Jinja2）
# ==========================================================================
@router.get("/api/templates")
def api_templates(u: dict = Depends(current_user)):
    db = get_conn()
    rows = [dict(r) for r in db.execute(
        "SELECT id,key,name,lang,is_builtin,created_at,LENGTH(body_html) AS size,LENGTH(css) AS css_size "
        "FROM templates ORDER BY is_builtin DESC, id")]
    return rows


@router.get("/api/templates/{tid}")
def api_template(tid: int, u: dict = Depends(current_user)):
    db = get_conn()
    r = db.execute("SELECT * FROM templates WHERE id=?", (tid,)).fetchone()
    if not r:
        raise HTTPException(404, "模板不存在")
    return dict(r)


class TemplateIn(BaseModel):
    key: str
    name: str
    lang: str = "en"
    body_html: str
    css: str | None = None


@router.post("/api/templates")
def api_template_create(body: TemplateIn, u: dict = Depends(current_user)):
    db = get_conn()
    db.execute("INSERT OR REPLACE INTO templates(key,name,lang,body_html,css,is_builtin,created_at) "
               "VALUES(?,?,?,?,?,0,?)", (body.key, body.name, body.lang, body.body_html, body.css, now()))
    db.commit()
    return {"ok": True}


@router.put("/api/templates/{tid}")
def api_template_update(tid: int, body: TemplateIn, u: dict = Depends(current_user)):
    db = get_conn()
    db.execute("UPDATE templates SET name=?,lang=?,body_html=?,css=? WHERE id=?",
               (body.name, body.lang, body.body_html, body.css, tid))
    db.commit()
    return {"ok": True}


@router.delete("/api/templates/{tid}")
def api_template_delete(tid: int, u: dict = Depends(current_user)):
    db = get_conn()
    r = db.execute("SELECT is_builtin FROM templates WHERE id=?", (tid,)).fetchone()
    if r and r["is_builtin"]:
        raise HTTPException(400, "内置模板不可删除")
    db.execute("DELETE FROM templates WHERE id=?", (tid,))
    db.commit()
    return {"ok": True}


# ==========================================================================
# 客户自带模板（按对方给的报价单格式出单）
# ==========================================================================
class ClientTemplateIn(BaseModel):
    name: str | None = None
    note: str | None = None
    sheet_name: str | None = None
    customer_id: int | None = None
    field_map: Any = None          # {"B3": "quote.quote_no", ...}
    item_map: Any = None           # {"header_row":12, "cols":{...}, ...}


class ClientPreviewIn(BaseModel):
    quotation_id: int
    field_map: Any = None
    item_map: Any = None
    sheet_name: str | None = None


class ClientFillIn(BaseModel):
    template_id: int


class SaveAsTplIn(BaseModel):
    """把刚生成的文件另存为某个客户的专属模板。"""
    file_id: str                  # uploads/filled 下的文件名
    name: str | None = None       # 模板名
    customer_id: int | None = None
    note: str | None = None


@router.get("/api/client-templates")
def api_client_templates(u: dict = Depends(current_user)):
    db = get_conn()
    rows = [dict(r) for r in db.execute(
        "SELECT id,name,customer_id,file_name,file_type,sheet_name,note,"
        "field_map,item_map,created_at FROM client_templates ORDER BY id DESC")]
    release_conn()
    for r in rows:
        fm = jload(r.pop("field_map", None), {}) or {}
        im = jload(r.pop("item_map", None), {}) or {}
        r["field_count"] = len(fm)
        r["item_count"] = len(im.get("cols") or {})
        r["has_item_map"] = bool(im.get("header_row") or im.get("start_row"))
    return rows


@router.get("/api/client-templates/fields")
def api_client_template_fields(u: dict = Depends(current_user)):
    """可映射的字段清单，前端渲染下拉用。"""
    return {"fields": [{"key": k, "label": v}
                       for k, v in client_tpl.FIELD_LABELS.items()],
            "item_fields": [{"key": k, "label": v}
                            for k, v in client_tpl.ITEM_FIELDS.items()]}


@router.post("/api/client-templates")
async def api_client_template_upload(
    file: UploadFile = File(...),
    name: str = Form(""),
    customer_id: int = Form(0),
    note: str = Form(""),
    u: dict = Depends(current_user)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in client_tpl.SUPPORTED:
        raise HTTPException(400, f"不支持的模板格式 {ext}，"
                                 f"可用：{' / '.join(client_tpl.SUPPORTED)}")
    tdir = os.path.join(UPLOAD_DIR, "client_tpl")
    os.makedirs(tdir, exist_ok=True)
    dest = os.path.join(
        tdir, f"{datetime.now():%Y%m%d%H%M%S}_{uuid_like()}{ext}")
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        structure = client_tpl.parse_template(dest)
    except Exception as exc:
        raise HTTPException(400, f"模板解析失败：{exc}")

    sheet = ""
    if structure.get("kind") == "excel":
        sheet = structure.get("default_sheet", "")
        n = sum(len(s["cells"]) for s in structure["sheets"])
        if n == 0:
            raise HTTPException(400, "这个 Excel 里没有任何文字内容，"
                                     "请确认上传的是报价单模板而不是空表")
    else:
        n = len(structure.get("blocks", []))

    db = get_conn()
    ts = now()
    cur = db.execute(
        "INSERT INTO client_templates(name,customer_id,file_name,file_type,"
        "stored_path,sheet_name,field_map,item_map,structure,note,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (name.strip() or os.path.splitext(file.filename)[0],
         customer_id or None, file.filename, client_tpl.SUPPORTED[ext],
         dest, sheet, jdump({}), jdump({}), jdump(structure), note, ts, ts))
    db.commit()
    tid = db.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    row = dict(db.execute("SELECT * FROM client_templates WHERE id=?",
                          (tid,)).fetchone())
    release_conn()
    return {"id": row["id"], "name": row["name"], "position_count": n,
            "file_type": row["file_type"], "sheet_name": row["sheet_name"]}


@router.get("/api/client-templates/{tid}")
def api_client_template(tid: int, u: dict = Depends(current_user)):
    db = get_conn()
    row = db.execute("SELECT * FROM client_templates WHERE id=?", (tid,)).fetchone()
    release_conn()
    if not row:
        raise HTTPException(404, "模板不存在")
    d = dict(row)
    d["field_map"] = jload(d.get("field_map"), {}) or {}
    d["item_map"] = jload(d.get("item_map"), {}) or {}
    d["structure"] = jload(d.get("structure"), {}) or {}
    return d


@router.put("/api/client-templates/{tid}")
def api_client_template_update(tid: int, body: ClientTemplateIn,
                               u: dict = Depends(current_user)):
    db = get_conn()
    if not db.execute("SELECT 1 FROM client_templates WHERE id=?", (tid,)).fetchone():
        release_conn()
        raise HTTPException(404, "模板不存在")
    sets, vals = [], []
    for k in ("name", "note", "sheet_name", "customer_id"):
        if body.model_dump().get(k) is not None:
            sets.append(f"{k}=?")
            vals.append(getattr(body, k))
    if body.field_map is not None:
        sets.append("field_map=?")
        vals.append(jdump(body.field_map))
    if body.item_map is not None:
        sets.append("item_map=?")
        vals.append(jdump(body.item_map))
    if not sets:
        release_conn()
        return {"ok": True}
    sets.append("updated_at=?")
    vals.append(now())
    vals.append(tid)
    db.execute(f"UPDATE client_templates SET {','.join(sets)} WHERE id=?", vals)
    db.commit()
    release_conn()
    return {"ok": True}


@router.delete("/api/client-templates/{tid}")
def api_client_template_delete(tid: int, u: dict = Depends(current_user)):
    db = get_conn()
    row = db.execute("SELECT stored_path FROM client_templates WHERE id=?",
                     (tid,)).fetchone()
    db.execute("DELETE FROM client_templates WHERE id=?", (tid,))
    db.execute("DELETE FROM filled_quotes WHERE template_id=?", (tid,))
    db.commit()
    release_conn()
    if row:
        try:
            os.remove(row["stored_path"])
        except OSError:
            pass
    return {"ok": True}


@router.post("/api/client-templates/{tid}/preview")
def api_client_template_preview(tid: int, body: ClientPreviewIn,
                                u: dict = Depends(current_user)):
    """用一份真实报价单试填，返回填充结果（不落盘）。"""
    db = get_conn()
    row = db.execute("SELECT * FROM client_templates WHERE id=?",
                     (tid,)).fetchone()
    release_conn()
    if not row:
        raise HTTPException(404, "模板不存在")
    src = row["stored_path"]
    if not os.path.exists(src):
        raise HTTPException(400, "模板文件已丢失，请重新上传")

    field_map = body.field_map or jload(row["field_map"], {}) or {}
    item_map = body.item_map or jload(row["item_map"], {}) or {}
    sheet = body.sheet_name or row["sheet_name"] or ""

    tmp_dir = os.path.join(UPLOAD_DIR, "_preview")
    os.makedirs(tmp_dir, exist_ok=True)
    try:
        if row["file_type"] in ("xlsx", "xlsm"):
            dst = os.path.join(tmp_dir, f"preview.{row['file_type']}")
            r = client_tpl.fill_excel(src, dst, body.quotation_id,
                                      field_map, item_map, sheet)
        else:
            dst = os.path.join(tmp_dir, "preview.docx")
            r = client_tpl.fill_docx(src, dst, body.quotation_id,
                                     field_map, item_map)
        r.pop("output", None)
        return r
    except Exception as exc:
        raise HTTPException(400, f"试填失败：{exc}")


# ==========================================================================
# 生成与下载
# ==========================================================================
@router.post("/api/quotations/{qid}/fill-template")
def api_quotation_fill(qid: int, body: ClientFillIn,
                       u: dict = Depends(current_user)):
    """用客户模板生成报价单文件，返回下载路径。"""
    out_dir = os.path.join(UPLOAD_DIR, "filled")
    try:
        r = client_tpl.fill_quote(qid, body.template_id, out_dir)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"生成失败：{exc}")
    return {"ok": True, "file_id": os.path.basename(r["output"]),
            "name": os.path.basename(r["output"]),
            **{k: v for k, v in r.items() if k != "output"}}


@router.get("/api/filled/{name}")
def api_filled_download(name: str, u: dict = Depends(current_user)):
    path = os.path.join(UPLOAD_DIR, "filled", os.path.basename(name))
    if not os.path.exists(path):
        raise HTTPException(404, "文件不存在")
    return FileResponse(path, filename=os.path.basename(name))


@router.post("/api/filled/save-as-template")
def api_save_as_template(body: SaveAsTplIn, u: dict = Depends(current_user)):
    """把刚生成的文件另存为模板，可指定归属客户。

    这样「按客户格式出了一次单」之后，就能直接存成该客户的专属模板，
    下次给这个客户出单直接选它，不用重新配映射。

    字段映射优先继承来源模板；没有来源时，回退到该客户名下最近一份模板。
    """
    src = os.path.join(UPLOAD_DIR, "filled", os.path.basename(body.file_id or ""))
    if not os.path.exists(src):
        raise HTTPException(404, "文件不存在或已清理，请重新生成后再保存")

    ext = os.path.splitext(src)[1].lower()
    ftype = {".xlsx": "xlsx", ".xlsm": "xlsm", ".docx": "docx"}.get(ext)
    if not ftype:
        raise HTTPException(400, f"不支持的格式 {ext}")

    try:
        structure = client_tpl.parse_template(src)
    except Exception as exc:
        raise HTTPException(400, f"解析生成的文件失败：{exc}")

    tdir = os.path.join(UPLOAD_DIR, "client_tpl")
    os.makedirs(tdir, exist_ok=True)
    dest = os.path.join(tdir, f"{datetime.now():%Y%m%d%H%M%S}_{uuid_like()}{ext}")
    shutil.copy2(src, dest)

    db = get_conn()
    # 1) 这份文件是由哪个模板生成的？（filled_quotes 记的是绝对路径）
    src_tpl = None
    last = db.execute(
        "SELECT t.* FROM filled_quotes f JOIN client_templates t ON t.id=f.template_id"
        " WHERE f.output_path=? ORDER BY f.id DESC LIMIT 1", (src,)).fetchone()
    if not last:
        last = db.execute(
            "SELECT t.* FROM filled_quotes f JOIN client_templates t ON t.id=f.template_id"
            " WHERE f.output_path LIKE ? ORDER BY f.id DESC LIMIT 1",
            ("%" + os.path.basename(src),)).fetchone()
    if last:
        src_tpl = last
    # 2) 退而求其次：该客户名下最近一份有映射的模板
    if not src_tpl and body.customer_id:
        src_tpl = db.execute(
            "SELECT * FROM client_templates WHERE customer_id=? "
            "ORDER BY id DESC LIMIT 1", (body.customer_id,)).fetchone()

    field_map = jload(src_tpl["field_map"], {}) if src_tpl else {}
    item_map = jload(src_tpl["item_map"], {}) if src_tpl else {}

    sheet = structure.get("default_sheet", "") if structure.get("kind") == "excel" else ""

    ts = now()
    db.execute(
        "INSERT INTO client_templates(name,customer_id,file_name,file_type,"
        "stored_path,sheet_name,field_map,item_map,structure,note,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (body.name or "客户专属模板", body.customer_id or None,
         os.path.basename(dest), ftype, dest, sheet,
         jdump(field_map), jdump(item_map), jdump(structure),
         body.note or "由已生成的报价单另存", ts, ts))
    db.commit()
    tid = db.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    release_conn()
    return {"ok": True, "template_id": tid,
            "name": body.name or "客户专属模板",
            "inherited_fields": len(field_map),
            "inherited_item_cols": len(item_map.get("cols") or {})}
