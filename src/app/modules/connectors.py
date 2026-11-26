"""模块层 — 连接器：企业微信 / 钉钉 / 飞书（M120）。

最小可用交付：
- ConnectorBase + WeComConnector：企业微信网页授权（corpid+corpsecret+agentid 换
  access_token、授权回调 code→userinfo）、文本 / 文本卡片消息推送。仓库内无现成企业微信
  SDK，故用标准库 urllib 直连官方 API（不引入新依赖，避免破坏 runtime）。
- 配置存 settings 单列「connector:wecom」（JSON），独立于 M006 的 5 个品牌键。
- GET/POST /api/connectors/wecom/config：带权限（仅 admin）+ 审计，仿 M006 写接口。
- 入口：GET /api/connectors/wecom/authorize-url（公开，供未登录企微内置浏览器跳转）、
  GET /api/connectors/wecom/callback（code 换 userid → 匹配内部用户 → 签 JWT）。
- 钉钉 / 飞书：ConnectorBase 占位子类，本轮不强制实现（接口预留）。

凭据（CorpID / Secret / AgentID）由用户后续提供；代码与配置界面已落地，缺口仅在配置值。
"""
import json
import urllib.parse
import urllib.request

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import brand
from ..auth import create_token, current_user
from ..core.services import audit
from ..core.db import get_conn
from ..database import get_setting, set_setting

router = APIRouter(tags=["connectors"])

# 单列连接器配置（与 M006 品牌键彻底隔离）
SETTING_KEY = "connector:wecom"

WECHAT_OAUTH_BASE = "https://open.weixin.qq.com/connect/oauth2/authorize"
WECHAT_API_BASE = "https://qyapi.weixin.qq.com/cgi-bin"


# --------------------------------------------------------------------------
# 连接器抽象
# --------------------------------------------------------------------------
class ConnectorBase:
    """连接器基类：所有第三方 IM / SSO 的共性接口。"""
    code = "base"
    name = "Base"

    def enabled(self) -> bool:
        return False

    def authorize_url(self, redirect_uri: str, state: str = "") -> str:
        raise NotImplementedError

    def exchange_code(self, code: str) -> dict:
        raise NotImplementedError

    def send_text(self, to_user: str, content: str) -> dict:
        raise NotImplementedError

    def send_card(self, to_user: str, title: str, desc: str, url: str = "") -> dict:
        raise NotImplementedError


def _http_get_json(url: str, params: dict | None = None, timeout: int = 8) -> dict:
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "felix-connector/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def _http_post_json(url: str, payload: dict, timeout: int = 8) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json", "User-Agent": "felix-connector/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


class WeComConnector(ConnectorBase):
    code = "wecom"
    name = "企业微信"

    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or self.load_config()

    @staticmethod
    def load_config() -> dict:
        raw = get_setting(SETTING_KEY, "")
        try:
            cfg = json.loads(raw) if raw else {}
        except Exception:
            cfg = {}
        return cfg or {}

    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled")) and bool(self.cfg.get("corpid"))

    def _token(self) -> str:
        corpid = self.cfg.get("corpid", "")
        secret = self.cfg.get("secret", "")
        if not corpid or not secret:
            raise RuntimeError("企业微信凭据未配置")
        resp = _http_get_json(f"{WECHAT_API_BASE}/gettoken",
                              {"corpid": corpid, "corpsecret": secret})
        if resp.get("errcode"):
            raise RuntimeError(f"获取 access_token 失败：{resp.get('errmsg')}")
        return resp["access_token"]

    def authorize_url(self, redirect_uri: str, state: str = "") -> str:
        corpid = self.cfg.get("corpid", "")
        agentid = self.cfg.get("agentid", "")
        params = {
            "appid": corpid,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "snsapi_base",
            "state": state or "felix",
            "agentid": agentid,
        }
        return WECHAT_OAUTH_BASE + "?" + urllib.parse.urlencode(params) + "#wechat_redirect"

    def exchange_code(self, code: str) -> dict:
        token = self._token()
        info = _http_get_json(f"{WECHAT_API_BASE}/auth/getuserinfo",
                              {"access_token": token, "code": code})
        if info.get("errcode"):
            raise RuntimeError(f"换取 userinfo 失败：{info.get('errmsg')}")
        return info  # {userid, openid, ...}

    def send_text(self, to_user: str, content: str) -> dict:
        token = self._token()
        return _http_post_json(
            f"{WECHAT_API_BASE}/message/send?access_token={token}",
            {"touser": to_user, "msgtype": "text",
             "agentid": self.cfg.get("agentid"), "text": {"content": content}})

    def send_card(self, to_user: str, title: str, desc: str, url: str = "") -> dict:
        token = self._token()
        card = {"title": title, "description": desc}
        if url:
            card["url"] = url
        return _http_post_json(
            f"{WECHAT_API_BASE}/message/send?access_token={token}",
            {"touser": to_user, "msgtype": "textcard",
             "agentid": self.cfg.get("agentid"), "textcard": card})


class DingTalkConnector(ConnectorBase):
    """钉钉连接器（占位）：本轮不强制实现，接口预留。"""
    code = "dingtalk"
    name = "钉钉"

    def enabled(self) -> bool:
        return False


class FeishuConnector(ConnectorBase):
    """飞书连接器（占位）：本轮不强制实现，接口预留。"""
    code = "feishu"
    name = "飞书"

    def enabled(self) -> bool:
        return False


# 连接器注册表：后续按 code 取实例
CONNECTORS = {c.code: c for c in (WeComConnector, DingTalkConnector, FeishuConnector)}


# --------------------------------------------------------------------------
# 配置接口（带权限 + 审计，仿 M006 /api/platform/brand）
# --------------------------------------------------------------------------
class WeComConfigIn(BaseModel):
    corpid: str = ""
    agentid: str = ""
    secret: str = ""
    enabled: bool = False


def _mask_secret(cfg: dict) -> dict:
    out = dict(cfg)
    if out.get("secret"):
        out["secret"] = "******"  # 读取时脱敏，绝不回传明文
    return out


@router.get("/api/connectors/wecom/config")
def api_wecom_config_get(u: dict = Depends(current_user)):
    """读取企业微信连接器配置（secret 脱敏）。仅管理员。"""
    if u["role"] != "admin":
        raise HTTPException(403, "仅管理员可查看连接器配置")
    cfg = WeComConnector.load_config()
    return {"ok": True, "config": _mask_secret(cfg),
            "enabled": WeComConnector(cfg).enabled()}


@router.post("/api/connectors/wecom/config")
def api_wecom_config_set(body: WeComConfigIn, u: dict = Depends(current_user)):
    """保存企业微信连接器配置（单列 settings: connector:wecom）。仅管理员 + 审计。"""
    if u["role"] != "admin":
        raise HTTPException(403, "仅管理员可配置连接器")
    cfg = {
        "corpid": (body.corpid or "").strip(),
        "agentid": (body.agentid or "").strip(),
        "secret": (body.secret or "").strip(),
        "enabled": bool(body.enabled),
    }
    # 允许「只改开关 / 只改部分字段」：若前端传空 secret 且已有配置，则保留原 secret
    existing = WeComConnector.load_config()
    if not cfg["secret"] and existing.get("secret"):
        cfg["secret"] = existing["secret"]
    before = _mask_secret(existing)
    set_setting(SETTING_KEY, json.dumps(cfg, ensure_ascii=False))
    audit(u, "connectors", "wecom_config_set", "connector", None, "wecom",
          before={"config": before}, after={"config": _mask_secret(cfg)})
    return {"ok": True, "config": _mask_secret(cfg),
            "enabled": WeComConnector(cfg).enabled()}


# --------------------------------------------------------------------------
# 入口：授权地址（公开，供未登录企微浏览器跳转）
# --------------------------------------------------------------------------
@router.get("/api/connectors/wecom/authorize-url")
def api_wecom_authorize_url(redirect_uri: str = "", state: str = "felix"):
    """返回企业微信网页授权地址。公开接口：未登录的企微内置浏览器需据此跳转。

    仅当连接器已启用且有 corpid 时返回地址；否则返回 enabled=false，前端不跳转。
    """
    conn = WeComConnector()
    if not conn.enabled():
        return {"ok": False, "enabled": False,
                "message": "企业微信连接器未启用或未配置凭据"}
    return {"ok": True, "enabled": True,
            "url": conn.authorize_url(redirect_uri or "", state)}


# --------------------------------------------------------------------------
# 回调用：code 换 userid → 匹配内部用户 → 签 JWT（走既有登录态）
# --------------------------------------------------------------------------
@router.get("/api/connectors/wecom/callback")
def api_wecom_callback(code: str = ""):
    """企业微信授权回调：用 code 换 userid，匹配内部用户并签发 JWT。

    匹配规则（最小可用）：内部用户的 username 等于企微 userid。
    命中则复用既有登录态（签发 Bearer token）；未命中返回 unmatched，
    由前端提示「请联系管理员在用户账号上绑定企业微信 userid」。
    """
    if not code:
        raise HTTPException(400, "缺少 code")
    conn = WeComConnector()
    if not conn.enabled():
        raise HTTPException(409, "企业微信连接器未启用")
    try:
        info = conn.exchange_code(code)
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    userid = (info.get("userid") or info.get("openid") or "").strip()
    if not userid:
        raise HTTPException(502, "未能从企业微信换取用户标识")
    db = get_conn()
    row = db.execute(
        "SELECT id,username,role FROM users WHERE is_active=1 AND username=?",
        (userid,)).fetchone()
    if not row:
        return {"ok": False, "matched": False,
                "message": "企业微信账号未匹配到系统用户，请联系管理员绑定"}
    token = create_token(row["id"], row["username"], row["role"])
    return {"ok": True, "matched": True, "token": token,
            "username": row["username"]}
