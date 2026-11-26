"""授权与试用期引擎 — 纯标准库实现，无第三方依赖。

设计要点：
- 试用期 30 天，从首次安装时间（settings.installed_at）起算。
- 授权文件 .felix_license 放在 BASE_DIR 下（独立于 data/，换库不丢）。
- 授权码 = base64(json 载荷) + '.' + HMAC-SHA256 签名（作者私钥）。
- 机器绑定：绑定到本机 install_id；"ANY" 表示通用授权（不绑机）。
- 校验只在需要时调用 get_state()（含 HMAC 校验），不在每个请求里重复计算重活；
  中间件对「已过期」状态做轻量拦截。
- 说明：内嵌密钥属于「提高侵权成本」级别的保护，真正不可破解需要编译/混淆分发
  （见路线图 M106）。对于桌面 ERP 的试用管控已足够。
"""
import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta

from ..database import BASE_DIR

TRIAL_DAYS = 30
# 作者私钥（内嵌）。更换后旧授权码全部失效，故上线后不要随意改动。
AUTHOR_SECRET = b"FELIX-AUTH-7c3f9a2e8b1d4f6a5c0e9b2a7d3f1c8e"
MACHINE_SALT = b"felix-machine-v1"
LICENSE_FILE = os.path.join(BASE_DIR, ".felix_license")


# --------------------------------------------------------------------------
# 机器指纹
# --------------------------------------------------------------------------
def machine_token() -> str:
    """本机稳定指纹：基于 install_id 派生，不采集任何用户隐私。"""
    try:
        from .. import brand
        inst = brand.install_id()
    except Exception:
        inst = "UNKNOWN"
    return hmac.new(MACHINE_SALT, inst.encode("utf-8"), hashlib.sha256).hexdigest()[:24]


# --------------------------------------------------------------------------
# 编解码与签名
# --------------------------------------------------------------------------
def _sign(body_b64: str) -> str:
    return hmac.new(AUTHOR_SECRET, body_b64.encode("utf-8"), hashlib.sha256).hexdigest()


def make_token(payload: dict) -> str:
    """作者签发授权码（tools/license_gen.py 调用）。"""
    body = base64.urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")
    return f"{body}.{_sign(body)}"


def _parse(token: str):
    try:
        body, sig = token.strip().rsplit(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(_sign(body), sig):
        return None
    try:
        return json.loads(base64.urlsafe_b64decode(body.encode("ascii")))
    except Exception:
        return None


# --------------------------------------------------------------------------
# 时间辅助
# --------------------------------------------------------------------------
def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_dt(s: str):
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception:
        try:
            return datetime.strptime(s, "%Y-%m-%d")
        except Exception:
            return None


def _days_left(expiry: str | None) -> int | None:
    if not expiry:
        return None
    d = _parse_dt(expiry)
    if not d:
        return None
    return (d - datetime.now()).days


def installed_at() -> str | None:
    try:
        from .db import get_conn
        r = get_conn().execute(
            "SELECT value FROM settings WHERE key='installed_at'").fetchone()
        if r and r["value"]:
            return r["value"]
    except Exception:
        pass
    return None


def trial_end() -> str | None:
    ia = installed_at()
    if not ia:
        return None
    d = _parse_dt(ia)
    if not d:
        return None
    return (d + timedelta(days=TRIAL_DAYS)).strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------
# 状态与激活
# --------------------------------------------------------------------------
def get_state() -> dict:
    """返回当前授权状态（每次调用实时读取授权文件，便宜到可以忽略）。"""
    lic = None
    if os.path.isfile(LICENSE_FILE):
        try:
            lic = _parse(open(LICENSE_FILE, encoding="utf-8").read())
        except Exception:
            lic = None

    if lic:
        bound = lic.get("machine")
        if bound not in (None, "ANY", machine_token()):
            return {"mode": "invalid", "reason": "machine_mismatch",
                    "activated": False, "expired": True,
                    "message": "授权文件与本机不匹配，请联系作者重新签发"}
        expiry = lic.get("expires")
        expired = bool(expiry and _now_str() > expiry)
        return {
            "mode": lic.get("type", "commercial"),
            "activated": True,
            "expired": expired,
            "expires": expiry,
            "trial_end": trial_end(),
            "days_left": _days_left(expiry),
            "owner": lic.get("owner"),
            "features": lic.get("features", []),
            "machine_bound": bool(bound and bound != "ANY"),
            "message": "已激活" if not expired else "授权已到期，请联系作者续期",
        }

    # 未激活 → 试用期
    te = trial_end()
    expired = bool(te and _now_str() > te)
    return {
        "mode": "trial",
        "activated": False,
        "expired": expired,
        "expires": te,
        "trial_end": te,
        "days_left": _days_left(te),
        "owner": None,
        "features": [],
        "machine_bound": False,
        "message": "试用中" if not expired else "试用期已到，请联系作者开通服务",
    }


def activate(token: str) -> dict:
    """写入授权文件。成功返回最新状态。"""
    lic = _parse(token or "")
    if not lic:
        return {"ok": False, "msg": "授权码无效或签名错误"}
    bound = lic.get("machine")
    if bound not in (None, "ANY", machine_token()):
        return {"ok": False, "msg": "该授权码绑定了其他设备，无法在本机激活"}
    try:
        with open(LICENSE_FILE, "w", encoding="utf-8") as f:
            f.write(token.strip())
    except OSError as e:
        return {"ok": False, "msg": f"写入授权文件失败：{e}"}
    return {"ok": True, "msg": "激活成功", "state": get_state()}


def deactivate() -> dict:
    try:
        if os.path.isfile(LICENSE_FILE):
            os.remove(LICENSE_FILE)
    except OSError:
        pass
    return {"ok": True, "state": get_state()}
