"""模块层 — 授权与激活。

试用期与授权是系统级能力，故作为核心模块常驻（无法关闭），保证即使到期
管理员仍能登录激活。激活接口需要管理员权限，且只接受作者签发的授权码。
"""
from fastapi import APIRouter, Depends, HTTPException

from ..auth import current_user
from ..core import licensing as L

router = APIRouter(tags=["licensing"])


@router.get("/api/licensing/status")
def lic_status():
    """授权状态 —— 公开（登录页/关于页可展示试用倒计时与到期提示）。"""
    return L.get_state()


@router.post("/api/licensing/activate")
def lic_activate(body: dict, u: dict = Depends(current_user)):
    """用作者签发的授权码激活。仅管理员。"""
    if u["role"] != "admin":
        raise HTTPException(403, "仅管理员可激活授权")
    token = (body.get("token") or "").strip()
    if not token:
        raise HTTPException(400, "请提供授权码")
    res = L.activate(token)
    if not res.get("ok"):
        raise HTTPException(400, res.get("msg", "激活失败"))
    return res


@router.post("/api/licensing/deactivate")
def lic_deactivate(u: dict = Depends(current_user)):
    """解绑本机授权（调试/转移设备用）。仅管理员。"""
    if u["role"] != "admin":
        raise HTTPException(403, "仅管理员可操作")
    return L.deactivate()
