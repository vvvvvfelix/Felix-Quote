"""模块层 — 平台：关于页、看板、用户管理、Token 用量、环境自检。

这一组接口互不相关，但都属于「系统本身」而非业务单据，故合并为一个模块。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import brand, selfcheck
from ..auth import current_user
from ..core import licensing as L
from ..core import registry as R
from ..core.db import get_conn, now
from ..database import db_backend
from ..core.services import audit

router = APIRouter(tags=["platform"])


# --------------------------------------------------------------------------
# 模块开关
# --------------------------------------------------------------------------
@router.get("/api/modules")
def api_modules(u: dict = Depends(current_user)):
    """已启用模块清单 —— 前端菜单据此渲染，关闭的模块不出现在导航里。"""
    return [{"code": m["code"], "name": m["name"], "group": m["group"],
             "desc": m["desc"]}
            for m in R.catalog() if m["enabled"]]


@router.get("/api/platform/modules")
def api_platform_modules(u: dict = Depends(current_user)):
    """模块开关面板（管理员）：全部模块 + 依赖 + 可关标记 + 装配失败回执。"""
    if u["role"] != "admin":
        raise HTTPException(403, "仅管理员可访问")
    return {"modules": R.catalog(), "failures": R.MOUNT_FAILURES}


@router.get("/api/platform/status")
def api_platform_status(u: dict = Depends(current_user)):
    """运行状态：服务进程、数据库、模块装配情况。管理控制台首屏用。"""
    if u["role"] != "admin":
        raise HTTPException(403, "仅管理员可访问")
    import os
    from ..database import DB_PATH
    size_mb, tables = 0.0, 0
    try:
        if os.path.isfile(DB_PATH):
            size_mb = round(os.path.getsize(DB_PATH) / 1024 / 1024, 2)
        tables = get_conn().execute(
            "SELECT COUNT(*) c FROM sqlite_master WHERE type='table'").fetchone()["c"]
    except Exception:
        pass
    return {"pid": os.getpid(), "db_path": DB_PATH, "db_size_mb": size_mb,
            "tables": tables,
            "db_backend": db_backend(),
            "license": L.get_state(),
            "modules_total": len(R.MODULES),
            "modules_enabled": len(R.enabled_codes()),
            "mount_failures": R.MOUNT_FAILURES,
            "server_time": now()}


@router.post("/api/platform/modules/toggle")
def api_module_toggle(body: dict, u: dict = Depends(current_user)):
    """启停模块。**重启服务后生效** —— 路由在启动时装配，运行中改的是持久化状态。"""
    if u["role"] != "admin":
        raise HTTPException(403, "仅管理员可访问")
    code = (body.get("code") or "").strip()
    on = bool(body.get("enabled"))
    ok, msg = R.set_enabled(code, on)
    if not ok:
        raise HTTPException(400, msg)
    audit(u, "platform", "module_toggle", "module", None, code,
          after={"enabled": on})
    return {"ok": True, "msg": msg, "restart_required": True}


# --------------------------------------------------------------------------
# 软件归属 / 责任声明
# --------------------------------------------------------------------------
@router.get("/api/about")
def api_about():
    """软件归属与责任声明 — 公开接口：登录页与关于页在未登录时也要能展示版权署名。"""
    return brand.about_payload()


# --------------------------------------------------------------------------
# 可配置署名（M006）：管理员改产品名/公司名，写入 settings 覆盖出厂默认值
# --------------------------------------------------------------------------
# 仅开放这 5 个白名单键；owner / owner_id 等权属署名不可改（docs/09 §0：
# 允许客户改掉权属号会让归属举证失效）。
_BRAND_EDIT_KEYS = {
    "product_name_cn": ("brand:product_name_cn", 60),
    "product_name_en": ("brand:product_name_en", 120),
    "company_cn":      ("brand:company_cn", 60),
    "company_en":      ("brand:company_en", 120),
    "brand_token":     ("brand:brand_token", 40),
}


class BrandIn(BaseModel):
    key: str
    value: str


@router.post("/api/platform/brand")
def api_set_brand(body: BrandIn, u: dict = Depends(current_user)):
    """管理员配置署名/公司名（覆盖出厂默认值）。

    写入即生效：关于页、单据抬头、页面内展示即时读取；FastAPI 标题与接口文档标题
    需重启服务才生效（已知限制，docs/09 §2.2 ⑦）。读取端不新增 GET——设置页直接读
    /api/about 的生效值 + brand_defaults，故路由总数只 +1（本接口）。
    """
    if u["role"] != "admin":
        raise HTTPException(403, "仅管理员可配置署名")
    rule = _BRAND_EDIT_KEYS.get(body.key)
    if not rule:
        raise HTTPException(
            400, f"不可配置的键：{body.key}（仅允许 {', '.join(_BRAND_EDIT_KEYS)}）")
    sk, maxlen = rule
    val = (body.value or "").strip()
    if not val:
        raise HTTPException(400, "值不能为空")
    if len(val) > maxlen:
        raise HTTPException(400, f"值过长（上限 {maxlen} 字符）")
    from ..database import set_setting
    before = brand.effective_brand().get(body.key)
    set_setting(sk, val)
    audit(u, "platform", "brand_set", "brand", None, body.key,
          before={"value": before}, after={"value": val})
    return {"ok": True, "key": body.key, "setting_key": sk, "value": val,
            "effective": brand.effective_brand()}


# --------------------------------------------------------------------------
# 看板
# --------------------------------------------------------------------------
@router.get("/api/dashboard")
def api_dashboard(u: dict = Depends(current_user)):
    db = get_conn()
    q = db.execute("SELECT COUNT(*) c FROM products WHERE is_active=1").fetchone()["c"]
    c = db.execute("SELECT COUNT(*) c FROM customers").fetchone()["c"]
    quo = db.execute("SELECT COUNT(*) c FROM quotations").fetchone()["c"]
    docs = db.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
    val = db.execute("SELECT IFNULL(SUM(total_amount),0) v FROM quotations WHERE status!='rejected'").fetchone()["v"]
    cats = [dict(r) for r in db.execute(
        "SELECT c.code, c.name_cn, COUNT(p.id) n FROM categories c "
        "LEFT JOIN products p ON p.category_code=c.code AND p.is_active=1 "
        "GROUP BY c.code ORDER BY c.sort_order")]
    recent = [dict(r) for r in db.execute(
        "SELECT id,quote_no,customer_id,title,total_amount,currency,status,created_at "
        "FROM quotations ORDER BY id DESC LIMIT 8")]
    return {"products": q, "customers": c, "quotations": quo, "documents": docs,
            "total_amount": round(val, 2), "categories": cats, "recent": recent}


# --------------------------------------------------------------------------
# 用户管理（仅管理员）
# --------------------------------------------------------------------------
@router.get("/api/users")
def api_users(u: dict = Depends(current_user)):
    if u["role"] != "admin":
        raise HTTPException(403, "仅管理员可访问")
    db = get_conn()
    rows = [dict(r) for r in db.execute(
        "SELECT id,username,email,full_name,company,role,is_active,created_at FROM users ORDER BY id")]
    return rows


@router.delete("/api/users/{uid}")
def api_user_toggle(uid: int, u: dict = Depends(current_user)):
    if u["role"] != "admin":
        raise HTTPException(403, "仅管理员可访问")
    db = get_conn()
    db.execute("UPDATE users SET is_active = 1 - is_active WHERE id=?", (uid,))
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------
# Token 用量计量（本地 0 Token 统计）
# --------------------------------------------------------------------------
@router.get("/api/ai/usage")
def api_ai_usage(u: dict = Depends(current_user)):
    db = get_conn()
    rows = [dict(r) for r in db.execute(
        "SELECT task,mode,COUNT(*) n,SUM(input_chars) ic,SUM(output_chars) oc,SUM(approx_tokens) tk "
        "FROM ai_usage GROUP BY task,mode ORDER BY n DESC")]
    for r in db.execute("SELECT * FROM ai_usage ORDER BY id DESC LIMIT 20"):
        db.execute("INSERT OR IGNORE INTO ai_usage(task,mode,input_chars,output_chars,approx_tokens,note,created_at) "
                   "VALUES(?,?,?,?,?,?,?)", ("刷新", "local", 0, 0, 0, "", now()))
    db.commit()
    total_tokens = db.execute("SELECT IFNULL(SUM(approx_tokens),0) t FROM ai_usage").fetchone()["t"]
    return {"rows": rows, "total_tokens": total_tokens,
            "note": "本地模式不消耗任何 Token；仅当启用云端增强功能时才会计入。"}


@router.post("/api/ai/log")
def api_ai_log(body: dict, u: dict = Depends(current_user)):
    ic = int(body.get("input_chars") or 0)
    oc = int(body.get("output_chars") or 0)
    db = get_conn()
    db.execute("INSERT INTO ai_usage(task,mode,input_chars,output_chars,approx_tokens,note,created_at) "
               "VALUES(?,?,?,?,?,?,?)",
               (body.get("task", "未命名"), body.get("mode", "local"), ic, oc,
                int((ic + oc) / 3.2), body.get("note", ""), now()))
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------
# 环境自检
# --------------------------------------------------------------------------
@router.get("/api/selfcheck")
def api_selfcheck(u: dict = Depends(current_user)):
    """完整环境自检。"""
    return selfcheck.full_check()
