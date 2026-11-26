"""模块层 — 邮件发送：SMTP 配置、主流邮箱预设、报价单邮件发送。

设计要点：
1. 零新增依赖 —— SMTP 用标准库 smtplib/email；授权码用 Windows DPAPI
   加密（纯 ctypes，见 app/core/crypto.py），不引入 cryptography
   （它带 11MB Rust 扩展，会把便携包推出 99MB 上限）。
2. 每用户独立配置 —— 业务员用自己的发件邮箱，settings 表以
   `smtp:<user_id>` 为 key 分开存，互不干扰。
3. 主流邮箱预设内置 —— 国内企业常用 QQ / 163 / 126 / Gmail / Outlook /
   企业微信 / 阿里企业邮 / 腾讯企业邮，选了自动填服务器地址，
   并给出官方配置文档链接（点击打开详情页）。
"""
import os
import smtplib
import socket
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from ..auth import current_user
from ..core import crypto
from ..core.db import BASE_DIR, get_conn, jdump, jload, now
from ..brand import effective_brand

router = APIRouter()


# ==========================================================================
# 主流邮箱 SMTP 预设
#   host/port/secure  —— 发送端参数
#   doc                —— 官方「客户端配置/授权码」说明页，点击可打开
#   note               —— 该邮箱特有的坑（如必须用授权码而非登录密码）
# ==========================================================================
MAIL_PROVIDERS = [
    {
        "key": "qq", "name": "QQ邮箱", "host": "smtp.qq.com", "port": 465,
        "secure": True, "use_auth": True,
        "doc": "https://service.mail.qq.com/detail/session/465",
        "note": "需在设置→账户中开启 SMTP 服务并生成「授权码」，不能填 QQ 密码",
    },
    {
        "key": "163", "name": "网易163邮箱", "host": "smtp.163.com", "port": 465,
        "secure": True, "use_auth": True,
        "doc": "https://help.163.com/imap.html",
        "note": "需开启 SMTP 并使用「客户端授权密码」，不能填登录密码",
    },
    {
        "key": "126", "name": "网易126邮箱", "host": "smtp.126.com", "port": 465,
        "secure": True, "use_auth": True,
        "doc": "https://help.163.com/imap.html",
        "note": "需开启 SMTP 并使用「客户端授权密码」",
    },
    {
        "key": "gmail", "name": "Gmail", "host": "smtp.gmail.com", "port": 587,
        "secure": False, "use_auth": True,
        "doc": "https://support.google.com/mail/answer/7126229",
        "note": "需开启两步验证并生成「应用专用密码」；587 端口用 STARTTLS",
    },
    {
        "key": "outlook", "name": "Outlook / Hotmail", "host": "smtp.office365.com",
        "port": 587, "secure": False, "use_auth": True,
        "doc": "https://support.microsoft.com/office/pop-imap-and-smtp-settings",
        "note": "Microsoft 365 账号通常直接用登录密码；587 端口用 STARTTLS",
    },
    {
        "key": "qqbiz", "name": "腾讯企业邮", "host": "smtp.exmail.qq.com", "port": 465,
        "secure": True, "use_auth": True,
        "doc": "https://service.work.weixin.qq.com/help/wx?person_id=1&doc_id=10659",
        "note": "用企业邮箱登录密码；若开启了安全密码则填安全密码",
    },
    {
        "key": "aliyun", "name": "阿里企业邮", "host": "smtp.mxhichina.com", "port": 465,
        "secure": True, "use_auth": True,
        "doc": "https://help.aliyun.com/zh/mail/user-guide/client-settings",
        "note": "用企业邮箱登录密码",
    },
    {
        "key": "custom", "name": "其他服务器", "host": "", "port": 465,
        "secure": True, "use_auth": True,
        "doc": "",
        "note": "自建邮件服务器，请填写实际 SMTP 地址与端口",
    },
]


# ==========================================================================
# 凭据加密 —— Windows DPAPI（纯 ctypes，零新增依赖）
#   见 app/core/crypto.py 的说明：为什么不用 cryptography 的 Fernet
#   （会多 11MB 包体积，且密钥与数据库同盘，安全模型更弱）。
# ==========================================================================
def _enc(plain: str) -> str:
    """加密明文 → 存库的字符串。"""
    if not plain:
        return ""
    return crypto.encrypt(plain)


def _dec(cipher: str) -> str:
    """解密 → 明文。密文损坏或换了机器解不开时返回空串。"""
    if not cipher:
        return ""
    return crypto.decrypt(cipher)


# ==========================================================================
# 配置读写
# ==========================================================================
def _cfg_key(user_id: int) -> str:
    return f"smtp:{user_id}"


def load_config(user_id: int) -> dict:
    db = get_conn()
    row = db.execute("SELECT value FROM settings WHERE key=?",
                     (_cfg_key(user_id),)).fetchone()
    if not row or not row["value"]:
        return {}
    cfg = jload(row["value"], {}) or {}
    cfg["password"] = _dec(cfg.get("password", ""))
    return cfg


def save_config(user_id: int, cfg: dict) -> None:
    db = get_conn()
    payload = dict(cfg)
    payload["password"] = _enc(cfg.get("password", ""))
    payload["updated_at"] = now()
    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",
               (_cfg_key(user_id), jdump(payload)))
    db.commit()


# ==========================================================================
# 发送
# ==========================================================================
def send_mail(cfg: dict, to_addr: str, subject: str, body: str,
              attachments: list[tuple[str, bytes, str]] | None = None,
              cc: list[str] | None = None) -> dict:
    """发送一封邮件。

    attachments: [(文件名, 内容bytes, MIME类型), ...]
    返回 {"ok": True, "message_id": ...}
    失败抛 RuntimeError，消息里带可读原因（认证失败/连接超时等）。
    """
    host = (cfg.get("host") or "").strip()
    port = int(cfg.get("port") or 465)
    user = (cfg.get("username") or "").strip()
    password = cfg.get("password") or ""
    sender = (cfg.get("sender") or user).strip()
    display = (cfg.get("sender_name") or "").strip()
    secure = bool(cfg.get("secure", True))

    if not host:
        raise RuntimeError("未配置 SMTP 服务器地址")
    if not sender:
        raise RuntimeError("未配置发件人邮箱")
    if not to_addr:
        raise RuntimeError("收件人邮箱为空")

    msg = EmailMessage()
    # formataddr 负责加尖括号，不要自己拼 —— 否则会出现双重尖括号
    msg["From"] = formataddr((display, sender)) if display else sender
    msg["To"] = to_addr
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject or "(无主题)"
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1] if "@" in sender else None)
    msg.set_content(body or "")

    for name, data, mime in (attachments or []):
        main, _, sub = (mime or "application/octet-stream").partition("/")
        msg.add_attachment(data, maintype=main, subtype=sub or "octet-stream",
                           filename=name)

    try:
        if secure and port == 465:
            # 隐式 SSL（465）
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, timeout=30, context=ctx) as s:
                if cfg.get("use_auth", True) and user:
                    s.login(user, password)
                s.send_message(msg)
        else:
            # 明文或 STARTTLS（587 / 25）
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.ehlo()
                if cfg.get("starttls", not secure):
                    try:
                        s.starttls(context=ssl.create_default_context())
                        s.ehlo()
                    except (smtplib.SMTPNotSupportedError, ssl.SSLError):
                        pass
                if cfg.get("use_auth", True) and user:
                    s.login(user, password)
                s.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise RuntimeError(
            f"SMTP 认证失败：{exc.smtp_code} {exc.smtp_error.decode(errors='ignore')}"
            "。请检查授权码是否正确、是否已开启 SMTP 服务")
    except smtplib.SMTPRecipientsRefused as exc:
        raise RuntimeError(f"收件人被拒绝：{exc.recipients}")
    except smtplib.SMTPServerDisconnected:
        raise RuntimeError("服务器断开连接，请检查服务器地址/端口是否正确")
    except socket.timeout:
        raise RuntimeError("连接邮件服务器超时，请检查网络或服务器地址")
    except OSError as exc:
        raise RuntimeError(f"无法连接邮件服务器 {host}:{port} — {exc}")

    return {"ok": True, "message_id": msg["Message-ID"],
            "to": to_addr, "attachments": [a[0] for a in (attachments or [])]}


# ==========================================================================
# API
# ==========================================================================
class MailCfgIn(BaseModel):
    host: str = ""
    port: int = 465
    secure: bool = True
    use_auth: bool = True
    username: str = ""
    password: str = ""
    sender: str = ""
    sender_name: str = ""
    provider: str = ""


class SendQuoteIn(BaseModel):
    customer_id: int
    quotation_id: int | None = None
    to: str = ""
    cc: str = ""
    subject: str = ""
    body: str = ""
    attach_pdf: bool = True
    # 已签字盖章的扫描件（打印后手签/盖章再扫描上传）
    signed_name: str = ""
    signed_mime: str = ""
    signed_b64: str = ""


@router.post("/api/mail/upload-signed")
async def api_mail_upload_signed(
    file: UploadFile = File(...),
    u: dict = Depends(current_user),
):
    """上传已签字盖章的报价单扫描件（打印→签字→扫描→上传）。

    文件暂存于 uploads 目录，返回 base64 供前端随发送请求带上。
    单文件上限 8MB。
    """
    data = await file.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(400, "扫描件不能超过 8MB")
    if not data:
        raise HTTPException(400, "文件内容为空")
    name = (file.filename or "signed.pdf").replace("\\", "/").split("/")[-1]
    safe = "".join(c for c in name if c.isalnum() or c in "._-")[:80] or "signed.pdf"
    out_dir = os.path.join(BASE_DIR, "static", "uploads", "signed")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{u['id']}_{safe}")
    with open(path, "wb") as f:
        f.write(data)
    import base64
    return {"name": safe, "mime": file.content_type or "application/octet-stream",
            "b64": base64.b64encode(data).decode(), "size": len(data)}


@router.get("/api/mail/providers")
def api_mail_providers(u: dict = Depends(current_user)):
    """主流邮箱预设（含官方配置说明链接）。"""
    return {"providers": MAIL_PROVIDERS}


@router.get("/api/mail/config")
def api_mail_config_get(u: dict = Depends(current_user)):
    """读取当前用户的发件配置（密码脱敏）。"""
    cfg = load_config(u["id"])
    cfg["password"] = "********" if cfg.get("password") else ""
    cfg["configured"] = bool(cfg.get("host") and cfg.get("sender"))
    return cfg


@router.put("/api/mail/config")
def api_mail_config_put(body: MailCfgIn, u: dict = Depends(current_user)):
    """保存当前用户的发件配置。"""
    old = load_config(u["id"])
    # 前端传回的是脱敏占位符时，保留原密码
    pwd = body.password
    if pwd == "********" or not pwd:
        pwd = old.get("password", "")
    cfg = {
        "host": body.host.strip(), "port": int(body.port or 465),
        "secure": bool(body.secure), "use_auth": bool(body.use_auth),
        "username": body.username.strip(), "password": pwd,
        "sender": body.sender.strip(), "sender_name": body.sender_name.strip(),
        "provider": body.provider,
    }
    if not cfg["host"]:
        raise HTTPException(400, "SMTP 服务器地址不能为空")
    if not cfg["sender"]:
        raise HTTPException(400, "发件人邮箱不能为空")
    save_config(u["id"], cfg)
    return {"ok": True}


@router.post("/api/mail/test")
def api_mail_test(u: dict = Depends(current_user)):
    """发送测试邮件到自己填的发件邮箱，验证配置可用。"""
    cfg = load_config(u["id"])
    if not cfg.get("host"):
        raise HTTPException(400, "尚未配置发件邮箱")
    to = cfg.get("sender")
    if not to:
        raise HTTPException(400, "未配置发件人邮箱")
    try:
        r = send_mail(cfg, to, f"{effective_brand().get('product_name_cn', 'Felix 企业管理平台')} — 发件配置测试",
                      "这是一封测试邮件。\n\n"
                      "如果你收到了它，说明你的发件邮箱配置正确，"
                      "现在可以直接给客户发送报价单了。\n\n"
                      "—— Felix 外贸业务系统")
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))
    return r


@router.get("/api/mail/quotations")
def api_mail_quotations(customer_id: int, u: dict = Depends(current_user)):
    """列出某客户名下的报价单，供发送时选择。

    不做数据范围过滤 —— 与 /api/quotations 现有行为保持一致
    （报价单表无 dept_id，按 owner 过滤会让业务员看不到同事的单）。
    """
    from ..core import permissions as P
    db = get_conn()
    P.require(u, "quotations.view")
    rows = db.execute(
        "SELECT id, quote_no, title, currency, total_amount, created_at "
        "FROM quotations WHERE customer_id=? ORDER BY id DESC LIMIT 50",
        (customer_id,)).fetchall()
    return {"items": [dict(r) for r in rows]}


@router.post("/api/mail/send-quotation")
def api_mail_send_quotation(body: SendQuoteIn, u: dict = Depends(current_user)):
    """把报价单（PDF 附件）发到客户邮箱。"""
    from ..core import permissions as P
    from ..core.services import audit
    from .. import quote as quote_engine

    P.require(u, "quotations.view")

    cfg = load_config(u["id"])
    if not cfg.get("host"):
        raise HTTPException(400, "尚未配置发件邮箱，请先在「系统 → 发件设置」中配置")
    if not cfg.get("password") and cfg.get("use_auth", True):
        raise HTTPException(400, "发件邮箱缺少授权码/密码，请重新配置")

    db = get_conn()
    cust = db.execute("SELECT * FROM customers WHERE id=?",
                      (body.customer_id,)).fetchone()
    if not cust:
        raise HTTPException(404, "客户不存在")
    to_addr = (body.to or cust["email"] or "").strip()
    if not to_addr:
        raise HTTPException(400, "该客户没有邮箱地址，请先在客户资料中补充")

    if not body.quotation_id:
        raise HTTPException(400, "请选择要发送的报价单")

    qrow = db.execute("SELECT * FROM quotations WHERE id=?",
                      (body.quotation_id,)).fetchone()
    if not qrow:
        raise HTTPException(404, "报价单不存在")

    # 正文模板复用渲染引擎的 ctx，保证与 PDF 上印的内容一致
    try:
        ctx = _mail_context(body.quotation_id, u)
    except ValueError:
        raise HTTPException(404, "报价单不存在")
    subject = (body.subject or ctx["subject"]).strip()
    mail_body = body.body if body.body.strip() else ctx["body"]

    attachments = []
    if body.attach_pdf:
        try:
            pdf = quote_engine.html_to_pdf(body.quotation_id)
        except Exception as exc:
            raise HTTPException(500, f"报价单 PDF 生成失败：{exc}")
        attachments.append((f"{qrow['quote_no']}.pdf", pdf, "application/pdf"))

    # 已签字盖章的扫描件（打印→手签/盖章→扫描→上传）
    if body.signed_b64 and body.signed_name:
        import base64 as _b64
        try:
            raw = _b64.b64decode(body.signed_b64)
        except Exception:
            raise HTTPException(400, "扫描件数据损坏，请重新上传")
        if len(raw) > 8 * 1024 * 1024:
            raise HTTPException(400, "扫描件不能超过 8MB")
        mime = body.signed_mime or "application/octet-stream"
        if mime == "application/octet-stream":
            mime = ("image/png" if body.signed_name.lower().endswith(".png")
                    else "image/jpeg" if body.signed_name.lower().endswith((".jpg", ".jpeg"))
                    else "application/pdf")
        attachments.append((body.signed_name, raw, mime))

    cc_list = [x.strip() for x in (body.cc or "").split(",") if x.strip()]
    try:
        r = send_mail(cfg, to_addr, subject, mail_body, attachments, cc_list)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))

    audit(u, "mail", "send_quotation", "quotation", body.quotation_id,
          qrow["quote_no"], {"to": to_addr, "cc": cc_list},
          {"subject": subject, "attachments": [a[0] for a in attachments]})

    db.execute(
        "UPDATE quotations SET status=CASE WHEN status='draft' THEN 'sent' ELSE status END,"
        " updated_at=? WHERE id=?", (now(), body.quotation_id))
    db.commit()

    return {**r, "subject": subject, "quotation_no": qrow["quote_no"]}


def _mail_context(quote_id: int, u: dict) -> dict:
    """取报价单的邮件上下文。

    复用 quote_engine.render() 的 ctx —— 这样邮件正文里的有效期、金额
    等与 PDF 上印的完全一致（valid_until 是渲染时算的，库里那个字段为空）。
    """
    from .. import quote as quote_engine
    _, ctx = quote_engine.render(quote_id)
    comp = ctx.get("company") or {}
    cust = ctx.get("customer") or {}
    q = ctx.get("quote") or {}
    totals = ctx.get("totals") or {}
    brand = comp.get("brand", "Felixv")
    return {
        "subject": f"{brand} Quotation {q.get('quote_no','')}".strip(),
        "body": (
            f"Dear {cust.get('contact') or cust.get('company_en') or 'Customer'},\n\n"
            f"Please find attached our quotation {q.get('quote_no','')}"
            f"{(' for ' + q['title']) if q.get('title') and q['title'] != 'Quotation' else ''}.\n\n"
            f"Total: {q.get('currency','')} {totals.get('total', 0)}\n"
            f"Valid until: {q.get('valid_until','')}\n\n"
            f"Best regards,\n{u.get('full_name') or u.get('username')}\n"
            f"{comp.get('name_en') or comp.get('name_cn') or ''}"
        ),
        "to": cust.get("email", "") or "",
        "customer_name": cust.get("company_en", "") or "",
        "contact": cust.get("contact", "") or "",
        "total": f"{q.get('currency','')} {totals.get('total', 0)}",
        "valid_until": q.get("valid_until", "") or "",
        "quote_no": q.get("quote_no", ""),
    }


@router.get("/api/mail/quotations/{qid}/body")
def api_mail_quotation_body(qid: int, u: dict = Depends(current_user)):
    """取报价单默认邮件正文（前端预览用）。"""
    from ..core import permissions as P
    P.require(u, "quotations.view")
    try:
        return _mail_context(qid, u)
    except ValueError:
        raise HTTPException(404, "报价单不存在")
