"""模块层 — 基础主数据：产品、品类参数、供应商、客户、公司信息。

这一组是全系统的基础字典，被报价、订单、履约等模块共同引用。
"""
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import quote as quote_engine
from ..auth import current_user
from ..categories_params import (CATEGORY_FAMILY, PARAM_TEMPLATES, get_family,
                                 get_params)
from ..core.db import get_conn, jdump, jload, now, release_conn

router = APIRouter(tags=["masterdata"])


# ==========================================================================
# 产品
# ==========================================================================
def _hydrate_product(r) -> dict:
    d = dict(r)
    d["certs"] = jload(d.get("certs"), [])
    d["images"] = jload(d.get("images"), [])
    # 品类专属参数（不同品类字段不同）
    specs = jload(d.get("specs"), {})
    if not isinstance(specs, dict):
        specs = {}
    fam = get_family(d.get("category_code"))
    tpl = get_params(d.get("category_code"))
    d["family"] = fam
    d["specs"] = specs
    d["param_template"] = {"label": tpl["label"], "family": fam, "params": tpl["params"]}
    # 合并：固定列 + 品类专属参数
    d["params"] = {
        "sku": d.get("sku"), "model": d.get("model"),
        "name_cn": d.get("name_cn"), "name_en": d.get("name_en"),
        "material": d.get("material"), "power": d.get("power"),
        "capacity": d.get("capacity"), "dimension": d.get("dimension"),
        "packing_size": d.get("packing_size"),
        "net_weight": d.get("net_weight"), "gross_weight": d.get("gross_weight"),
        "price_cny": d.get("price_cny"), "price_usd": d.get("price_usd"),
        "moq": d.get("moq"), "lead_time": d.get("lead_time"),
        "hs_code": d.get("hs_code"), "certs": d.get("certs"),
        "spec": d.get("spec"), "spec_en": d.get("spec_en"),
        "desc_en": d.get("desc_en"), "keywords": d.get("keywords"),
        **{k: v for k, v in specs.items() if v not in (None, "", [])},
    }
    return d


class ProductIn(BaseModel):
    sku: str
    name_cn: str
    name_en: str | None = None
    category_code: str | None = "OTHER"
    supplier_code: str | None = "SELF"
    brand: str | None = "Felixv"
    model: str | None = None
    spec: str | None = None
    spec_en: str | None = None
    material: str | None = None
    power: str | None = None
    capacity: str | None = None
    dimension: str | None = None
    packing_size: str | None = None
    net_weight: float | None = None
    gross_weight: float | None = None
    price_cny: float | None = None
    price_usd: float | None = None
    moq: int = 1
    lead_time: str | None = None
    hs_code: str | None = None
    certs: list[str] = []
    image_path: str | None = None
    images: list[str] = []
    desc_en: str | None = None
    keywords: str | None = None
    specs: Any = {}


def _split_specs(body_dict: dict) -> tuple[dict, dict]:
    """把品类专属参数从通用字段中拆出。

    specs 允许前端回传 JSON 字符串（产品详情接口会把它序列化），
    这里做兼容解析，避免 PUT 报 422。

    返回 (通用字段, specs) — specs 只保留 param_template 中定义的 key。
    """
    tpl = get_params(body_dict.get("category_code"))
    valid_keys = {p["key"] for p in tpl["params"]}
    common = {k: v for k, v in body_dict.items()
              if k not in ("specs", "image_path", "images", "certs")}
    raw = body_dict.get("specs") or {}
    if isinstance(raw, str):
        raw = jload(raw, {})
    if not isinstance(raw, dict):
        raw = {}
    specs = {k: v for k, v in raw.items() if k in valid_keys and v not in (None, "")}
    # 品类模板字段中，通用列已覆盖的不重复写入 specs
    common_keys = {"sku", "model", "name_cn", "name_en", "material", "power", "capacity",
                   "dimension", "packing_size", "net_weight", "gross_weight",
                   "price_cny", "price_usd", "moq", "lead_time", "hs_code", "spec", "spec_en"}
    specs = {k: v for k, v in specs.items() if k not in common_keys}
    return common, specs


@router.get("/api/products")
def api_products(q: str | None = None, category: str | None = None,
                 supplier: str | None = None, price_min: float | None = None,
                 price_max: float | None = None, limit: int = 200, offset: int = 0,
                 u: dict = Depends(current_user)):
    sql = "SELECT * FROM products WHERE is_active=1"
    args: list = []
    if q:
        like = f"%{q}%"
        sql += (" AND (sku LIKE ? OR name_cn LIKE ? OR name_en LIKE ? OR model LIKE ? "
                "OR keywords LIKE ? OR spec LIKE ? OR material LIKE ? OR desc_en LIKE ?)")
        args += [like] * 8
    if category:
        sql += " AND category_code=?"
        args.append(category)
    if supplier:
        sql += " AND supplier_code=?"
        args.append(supplier)
    sql += " AND IFNULL(price_usd, price_cny) >= COALESCE(?, -1)"
    args.append(price_min)
    sql += " AND IFNULL(price_usd, price_cny) <= COALESCE(?, 99999999)"
    args.append(price_max)
    sql += " ORDER BY category_code, sku LIMIT ? OFFSET ?"
    args += [limit, offset]
    db = get_conn()
    rows = [_hydrate_product(r) for r in db.execute(sql, args)]
    total = db.execute("SELECT COUNT(*) c FROM products WHERE is_active=1").fetchone()["c"]
    return {"items": rows, "total": total}


@router.get("/api/products/{pid}")
def api_product(pid: int, u: dict = Depends(current_user)):
    db = get_conn()
    r = db.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    if not r:
        raise HTTPException(404, "产品不存在")
    return _hydrate_product(r)


@router.post("/api/products")
def api_product_create(body: ProductIn, u: dict = Depends(current_user)):
    db = get_conn()
    if db.execute("SELECT 1 FROM products WHERE sku=?", (body.sku,)).fetchone():
        release_conn()
        raise HTTPException(400, "SKU 已存在")
    raw = body.model_dump()
    common, specs = _split_specs(raw)
    common["certs"] = jdump(raw.get("certs") or [])
    common["images"] = jdump(raw.get("images") or [])
    common["specs"] = jdump(specs)
    common["source"] = "手工录入"
    common["is_active"] = 1
    common["created_at"] = now()
    common.setdefault("name_en", common["name_cn"])
    keys = list(common.keys())
    db.execute(f"INSERT INTO products({','.join(keys)}) "
               f"VALUES({','.join('?' for _ in keys)})", [common[k] for k in keys])
    db.commit()
    pid = db.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    r = db.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    release_conn()
    return _hydrate_product(r)


@router.put("/api/products/{pid}")
def api_product_update(pid: int, body: ProductIn, u: dict = Depends(current_user)):
    db = get_conn()
    if not db.execute("SELECT 1 FROM products WHERE id=?", (pid,)).fetchone():
        release_conn()
        raise HTTPException(404, "产品不存在")
    raw = body.model_dump()
    common, specs = _split_specs(raw)
    common["certs"] = jdump(raw.get("certs") or [])
    common["images"] = jdump(raw.get("images") or [])
    common["specs"] = jdump(specs)
    sets = ",".join(f"{k}=?" for k in common)
    db.execute(f"UPDATE products SET {sets} WHERE id=?",
               [common[k] for k in common] + [pid])
    db.commit()
    r = db.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    release_conn()
    return _hydrate_product(r)


@router.delete("/api/products/{pid}")
def api_product_delete(pid: int, u: dict = Depends(current_user)):
    db = get_conn()
    db.execute("UPDATE products SET is_active=0 WHERE id=?", (pid,))
    db.commit()
    return {"ok": True}


# ==========================================================================
# 品类与参数模板
# ==========================================================================
@router.get("/api/categories")
def api_categories(u: dict = Depends(current_user)):
    db = get_conn()
    rows = [dict(r) for r in db.execute("SELECT * FROM categories ORDER BY sort_order")]
    release_conn()
    for r in rows:
        fam = get_family(r["code"])
        r["family"] = fam
        r["family_label"] = get_params(r["code"])["label"]
        r["param_count"] = len(get_params(r["code"])["params"])
    return rows


@router.get("/api/param-templates")
def api_param_templates(u: dict = Depends(current_user)):
    """返回全部参数模板族及其字段定义。"""
    return {"families": [{"key": k, "label": v["label"], "params": v["params"]}
                         for k, v in PARAM_TEMPLATES.items()],
            "mapping": CATEGORY_FAMILY}


@router.get("/api/param-templates/{category_code}")
def api_param_template(category_code: str, u: dict = Depends(current_user)):
    """按品类返回其专属参数字段（前端表单据此动态渲染）。"""
    fam = get_family(category_code)
    tpl = PARAM_TEMPLATES[fam]
    return {"category_code": category_code, "family": fam,
            "label": tpl["label"], "params": tpl["params"]}


# ==========================================================================
# 供应商
# ==========================================================================
class SupplierIn(BaseModel):
    code: str
    name_cn: str
    name_en: str | None = None
    contact: str | None = None
    phone: str | None = None
    email: str | None = None
    remark: str | None = None


@router.get("/api/suppliers")
def api_suppliers(u: dict = Depends(current_user)):
    db = get_conn()
    rows = [dict(r) for r in db.execute("SELECT * FROM suppliers WHERE is_active=1 ORDER BY code")]
    return rows


@router.post("/api/suppliers")
def api_supplier_create(body: SupplierIn, u: dict = Depends(current_user)):
    db = get_conn()
    d = body.model_dump()
    d["is_active"] = 1
    keys = ",".join(d)
    db.execute(f"INSERT OR REPLACE INTO suppliers({keys}) VALUES({','.join('?' * len(d))})",
               list(d.values()))
    db.commit()
    return {"ok": True}


# ==========================================================================
# 公司信息
# ==========================================================================
@router.get("/api/company")
def api_company(u: dict = Depends(current_user)):
    return quote_engine.get_company()


@router.put("/api/company")
def api_company_update(body: dict, u: dict = Depends(current_user)):
    db = get_conn()
    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('company',?)", (jdump(body),))
    db.commit()
    return {"ok": True}


# ==========================================================================
# 客户
# ==========================================================================
class CustomerIn(BaseModel):
    code: str | None = None
    company_en: str
    contact: str | None = None
    country: str | None = None
    address: str | None = None
    email: str | None = None
    phone: str | None = None
    incoterm: str | None = "FOB Guangzhou"
    port: str | None = "Guangzhou / Shenzhen"
    payment_terms: str | None = None
    currency: str | None = "USD"
    lang: str | None = "en"
    remark: str | None = None


@router.get("/api/customers")
def api_customers(q: str | None = None, u: dict = Depends(current_user)):
    db = get_conn()
    sql = "SELECT * FROM customers"
    args: list = []
    if q:
        sql += " WHERE company_en LIKE ? OR country LIKE ? OR code LIKE ?"
        args += [f"%{q}%"] * 3
    sql += " ORDER BY id DESC"
    rows = [dict(r) for r in db.execute(sql, args)]
    return rows


@router.get("/api/customers/{cid}")
def api_customer(cid: int, u: dict = Depends(current_user)):
    db = get_conn()
    r = db.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
    if not r:
        raise HTTPException(404, "客户不存在")
    return dict(r)


@router.post("/api/customers")
def api_customer_create(body: CustomerIn, u: dict = Depends(current_user)):
    db = get_conn()
    d = body.model_dump()
    d["code"] = d["code"] or ("C" + datetime.now().strftime("%y%m%d") +
                              f"{db.execute('SELECT COUNT(*) c FROM customers').fetchone()['c'] + 1:03d}")
    d["created_at"] = now()
    keys = ",".join(d)
    db.execute(f"INSERT INTO customers({keys}) VALUES({','.join('?' * len(d))})", list(d.values()))
    db.commit()
    rid = db.execute("SELECT last_insert_rowid() id").fetchone()["id"]
    r = db.execute("SELECT * FROM customers WHERE id=?", (rid,)).fetchone()
    return dict(r)


@router.put("/api/customers/{cid}")
def api_customer_update(cid: int, body: CustomerIn, u: dict = Depends(current_user)):
    db = get_conn()
    d = body.model_dump()
    keys = ",".join(d)
    db.execute(f"UPDATE customers SET {keys} WHERE id=?", list(d.values()) + [cid])
    db.commit()
    r = db.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
    return dict(r)


@router.delete("/api/customers/{cid}")
def api_customer_delete(cid: int, u: dict = Depends(current_user)):
    db = get_conn()
    db.execute("DELETE FROM customers WHERE id=?", (cid,))
    db.commit()
    return {"ok": True}
