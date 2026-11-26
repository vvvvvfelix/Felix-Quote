"""FastAPI 主应用 — 装配层。

职责仅三件事：
1. 创建 app 实例（中间件、静态资源挂载）
2. 挂载各业务模块的路由
3. 启动钩子（建库、初始化管理员、灌入种子数据）

业务逻辑一律在 app/modules/ 下，本文件不含任何业务代码。
"""
import importlib
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import brand, seed
from .auth import ensure_bootstrap_admin
from .core import licensing as lic_ctl
from .core import registry as R
from .core.db import init_db, release_conn
from .core.paths import STATIC_DIR
from .core.schema import run_migrations
from .modules import identity, structure   # 种子数据在启动钩子中调用，故静态引入

app = FastAPI(title=f"{brand.PRODUCT_NAME_CN} {brand.VERSION}",
              version=brand.VERSION, docs_url="/api/docs")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def _release_db(request, call_next):
    """每个请求结束后回收数据库连接，杜绝异常路径下的连接泄漏与锁等待。"""
    try:
        return await call_next(request)
    finally:
        release_conn()


@app.middleware("http")
async def _license_gate(request, call_next):
    """试用期/授权到期网关。

    仅在「已过期且非永久授权」时拦截业务接口，放行：登录、关于、授权激活、
    自检、静态资源与文档。这样即使到期，管理员仍能登录激活，普通用户看到提示。
    状态实时读取（含 HMAC 校验），只在请求进入时算一次，开销可忽略。
    """
    p = request.url.path
    if (p.startswith(("/static", "/login", "/api/about", "/api/licensing",
                      "/api/selfcheck", "/api/docs", "/openapi.json", "/favicon"))
            or p in ("/", "/index.html")):
        return await call_next(request)
    try:
        st = lic_ctl.get_state()
    except Exception:
        return await call_next(request)
    if st.get("expired") and st.get("mode") != "perpetual":
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=402,
                            content={"error": "trial_expired",
                                     "message": st.get("message") or "试用期已到"})
    return await call_next(request)


@app.on_event("startup")
def _startup():
    init_db()
    run_migrations()          # 版本化迁移，必须在建库之后
    R.ensure_states()         # 模块开关落库（首次全启用，核心模块强制启用）
    # 顺序要紧：先把部门与角色种子数据灌进去，再建管理员账号 ——
    # 否则 ensure_bootstrap_admin 查不到 'HQ' 部门，admin 会挂空部门，
    # 「按部门统计人数」就永远少一个人。
    identity.seed_org()       # 部门与角色（旧表，兼容期）
    structure.seed_structure()  # 组织树 org_units（M001 起为权威组织主干）
    ensure_bootstrap_admin()
    seed.seed_all()
    _mount_modules()          # 此时库已就绪，按开关挂载路由


# --------------------------------------------------------------------------
# 业务模块路由挂载 —— 按模块开关动态装配
#   小公司关掉用不上的模块：菜单干净、权限点少、升级面小。
#   挂载顺序 = MODULES 清单顺序（即匹配优先级），静态路径与动态路径的
#   先后由各模块文件内部保证（如 quotation 的 /client-templates/fields）。
#   单个模块 import 失败只记日志不停服 —— 一个模块坏掉不该拖垮整个系统。
# --------------------------------------------------------------------------
def _mount_modules() -> None:
    R.MOUNT_FAILURES.clear()
    for pkg in R.enabled_packages():
        try:
            mod = importlib.import_module(f".modules.{pkg}", package=__package__)
            app.include_router(mod.router)
        except Exception as exc:                       # pragma: no cover
            R.MOUNT_FAILURES.append(
                {"module": pkg, "error": f"{type(exc).__name__}: {exc}"})
            print(f"[assembly] 模块 {pkg} 挂载失败：{exc}")


# --------------------------------------------------------------------------
# 前端入口
# --------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as f:
        return f.read()
