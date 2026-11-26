"""模块层 — 资料库：文档上传解析、图片识别。

OCR 与文档解析同属「把外部资料变成结构化数据」，故合并为一个模块。
"""
import os
import shutil
from datetime import datetime

from fastapi import (APIRouter, Depends, File, HTTPException, UploadFile)
from fastapi.responses import JSONResponse

from .. import docparser
from .. import ocr as ocr_mod
from .. import selfcheck
from ..auth import current_user
from ..categories_params import get_params
from ..core.db import get_conn, jload, now, release_conn
from ..core.paths import UPLOAD_DIR, uuid_like

router = APIRouter(tags=["documents"])


# ==========================================================================
# 文档
# ==========================================================================
@router.get("/api/documents")
def api_documents(u: dict = Depends(current_user)):
    db = get_conn()
    rows = [dict(r) for r in db.execute(
        "SELECT id,filename,doc_type,size,status,message,upload_by,created_at,LENGTH(parsed_text) AS text_len "
        "FROM documents ORDER BY id DESC")]
    return rows


@router.post("/api/documents")
async def api_document_upload(file: UploadFile = File(...), u: dict = Depends(current_user)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in docparser.ALLOWED:
        raise HTTPException(400, f"不支持的文件类型 {ext}，可用：{', '.join(sorted(docparser.ALLOWED))}")
    safe = os.path.basename(file.filename)
    dest = os.path.join(UPLOAD_DIR, f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{safe}")
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    size = os.path.getsize(dest)
    db = get_conn()
    db.execute("INSERT INTO documents(filename,doc_type,size,stored_path,status,message,upload_by,created_at) "
               "VALUES(?,?,?,?,'pending','待解析',?,?)",
               (safe, ext.lstrip("."), size, dest, u["id"], now()))
    db.commit()
    did = db.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    try:
        result = docparser.parse_document(did)
    except Exception as exc:
        result = {"doc_id": did, "status": "failed", "message": str(exc), "count": 0, "candidates": []}
    return result


@router.get("/api/documents/{did}")
def api_document(did: int, u: dict = Depends(current_user)):
    db = get_conn()
    r = db.execute("SELECT * FROM documents WHERE id=?", (did,)).fetchone()
    if not r:
        raise HTTPException(404, "文档不存在")
    d = dict(r)
    d["candidates"] = jload(d.pop("parsed_json"), [])
    d.pop("parsed_text", None)
    return d


@router.get("/api/documents/{did}/text")
def api_document_text(did: int, u: dict = Depends(current_user)):
    db = get_conn()
    r = db.execute("SELECT parsed_text FROM documents WHERE id=?", (did,)).fetchone()
    if not r:
        raise HTTPException(404, "文档不存在")
    return {"text": r["parsed_text"] or ""}


@router.post("/api/documents/{did}/parse")
def api_document_reparse(did: int, u: dict = Depends(current_user)):
    return docparser.parse_document(did)


@router.post("/api/documents/{did}/commit")
def api_document_commit(did: int, body: dict, u: dict = Depends(current_user)):
    items = body.get("items") or []
    return docparser.commit_candidates(did, items, u["username"])


@router.delete("/api/documents/{did}")
def api_document_delete(did: int, u: dict = Depends(current_user)):
    db = get_conn()
    r = db.execute("SELECT stored_path FROM documents WHERE id=?", (did,)).fetchone()
    db.execute("DELETE FROM documents WHERE id=?", (did,))
    db.commit()
    if r and os.path.exists(r["stored_path"]):
        try:
            os.remove(r["stored_path"])
        except OSError:
            pass
    return {"ok": True}


# ==========================================================================
# 通用图片上传
# ==========================================================================
@router.post("/api/uploads")
async def api_upload(file: UploadFile = File(...), u: dict = Depends(current_user)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        raise HTTPException(400, "仅支持图片文件")
    name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid_like()}{ext}"
    dest = os.path.join(UPLOAD_DIR, name)
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"url": "/static/uploads/" + name}


# ==========================================================================
# 图片 OCR — 截图 / 照片 识别（本地推理，0 Token）
# ==========================================================================
@router.get("/api/ocr/status")
def api_ocr_status(u: dict = Depends(current_user)):
    """OCR 可用性与不可用原因（精确到具体成因）。"""
    return selfcheck.diagnose_ocr()


@router.post("/api/ocr/image")
async def api_ocr_image(file: UploadFile = File(...),
                        category_code: str | None = None,
                        u: dict = Depends(current_user)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext in ocr_mod.NO_DECODER_EXT:
        return JSONResponse(status_code=200, content={
            "ok": False,
            "reason": f"暂不支持 {ext.upper()} 格式图片",
            "solution": "为压缩安装包体积，未内置 AVIF/HEIF 解码器。"
                        "请把图片另存为 JPG 或 PNG 后再上传"
                        "（微信/QQ「保存图片」默认就是 JPG）。",
        })
    if ext not in ocr_mod.IMG_EXT:
        raise HTTPException(400, f"不支持的图片类型 {ext}，可用：{', '.join(sorted(ocr_mod.IMG_EXT))}")

    # 先做静态自检（不加载模型，毫秒级）
    diag = selfcheck.diagnose_ocr()
    if not diag["available"]:
        return JSONResponse(status_code=200, content={
            "ok": False, "diagnostic": diag,
            "reason": diag["reason"], "solution": diag.get("solution", ""),
        })

    # 静态检查通过后才真正跑推理，此时失败才归因到引擎本身
    dest = os.path.join(UPLOAD_DIR, f"ocr_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid_like()}{ext}")
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        result = ocr_mod.ocr_image(dest, category_code)
    except Exception as exc:
        diag = selfcheck.diagnose_ocr()
        diag.update(code="INFER_FAILED", available=False, status=selfcheck.FAIL,
                    reason="图片识别引擎运行失败",
                    solution=f"请运行「检查依赖.bat」查看详细信息（技术细节：{exc}）")
        return JSONResponse(status_code=200, content={
            "ok": False, "diagnostic": diag,
            "reason": diag["reason"], "solution": diag["solution"],
        })

    if not result.get("ok"):
        raise HTTPException(503, result.get("error", "OCR 引擎不可用"))

    cand = ocr_mod.build_candidate(result)
    cat = result.get("category_code") or "OTHER"
    tpl = get_params(cat)

    # 记录到文档表，便于追溯
    db = get_conn()
    db.execute("INSERT INTO documents(filename,doc_type,size,stored_path,status,message,upload_by,created_at) "
               "VALUES(?,?,?,?,?,?,?,?)",
               (file.filename, "image/ocr", os.path.getsize(dest), dest, "ocr",
                f"识别 {result['line_count']} 行，置信度 {result['avg_conf']}", u["id"], now()))
    db.commit()
    doc_id = db.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    release_conn()

    return {
        "doc_id": doc_id,
        "image_url": "/static/uploads/" + os.path.basename(dest),
        "text": result["text"],
        "lines": result["lines"],
        "line_count": result["line_count"],
        "avg_conf": result["avg_conf"],
        "fields": result["fields"],
        "category_code": cat,
        "category_family": result["category_family"],
        "param_template": {"family": tpl["family"] if "family" in tpl else result["category_family"],
                           "label": tpl["label"], "params": tpl["params"]},
        "candidate": cand,
    }


@router.get("/api/ocr/families")
def api_ocr_families(u: dict = Depends(current_user)):
    """品类识别规则（供前端展示识别依据）。"""
    return [
        {"code": "MIXER", "label": "搅拌设备类", "keywords": ["搅拌机", "和面机", "打蛋机", "起酥机"]},
        {"code": "FREEZER", "label": "冷冻冷藏类", "keywords": ["冷藏", "冷冻", "冷柜", "冰箱"]},
        {"code": "WASH", "label": "清洗消毒类", "keywords": ["洗碗", "洗杯", "消毒"]},
        {"code": "FURNACE", "label": "炉具灶具类", "keywords": ["炒炉", "汤炉", "灶具", "炸炉"]},
        {"code": "PROCESS", "label": "食品加工机械类", "keywords": ["切肉", "绞肉", "灌肠", "切菜"]},
        {"code": "DISPLAY", "label": "展示保温类", "keywords": ["展示柜", "保温柜", "陈列柜"]},
        {"code": "OVEN", "label": "烘焙烤箱类", "keywords": ["烤箱", "烤炉", "披萨炉", "蒸烤箱", "醒发箱"]},
    ]
