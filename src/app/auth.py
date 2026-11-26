"""用户认证 — PBKDF2-SHA256 密码哈希 + JWT，纯本地无外部服务。"""
import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from .core.services import next_emp_no
from .database import get_conn, now, release_conn


def _resolve_secret() -> str:
    """JWT 签名密钥。

    多用户/局域网部署下，固定的默认密钥等于「任何人可伪造他人 token」。
    因此：优先读环境变量 WV_SECRET；否则在首次启动时生成随机密钥，
    存入 settings 表（install_id 附近），之后所有进程共用同一份。
    """
    env = os.environ.get("WV_SECRET")
    if env:
        return env
    try:
        db = get_conn()
        row = db.execute("SELECT value FROM settings WHERE key='jwt_secret'").fetchone()
        if row and row["value"]:
            return row["value"]
        secret = secrets.token_hex(32)
        db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('jwt_secret',?)",
                   (secret,))
        db.commit()
        release_conn()
        return secret
    except sqlite3.Error:
        # 库不可用时退回进程级随机密钥（重启后失效，可接受）
        return secrets.token_hex(32)


SECRET_KEY = _resolve_secret()
ALGO = "HS256"
TOKEN_TTL_HOURS = 24 * 7
PBKDF2_ROUNDS = 120_000

bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt, digest = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(dk.hex(), digest)
    except (ValueError, AttributeError):
        return False


def create_token(user_id: int, username: str, role: str) -> str:
    payload = {
        "sub": str(user_id),
        "usr": username,
        "role": role,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_TTL_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGO)


# --------------------------------------------------------------------------
# 密码策略与登录安全（多站点部署后，内网也不能用弱口令）
# --------------------------------------------------------------------------
MIN_PW_LEN = 8
LOCK_MINUTES = 15
MAX_FAILS = 5


def password_problem(pw: str, username: str = "") -> str | None:
    """返回密码不合规的原因；合规返回 None。"""
    if not pw or len(pw) < MIN_PW_LEN:
        return f"密码至少 {MIN_PW_LEN} 位"
    if pw.lower() in ("password", "12345678", "admin123", "wv2026"):
        return "这个密码太常见，请换一个"
    if username and len(username) >= 3 and username.lower() in pw.lower():
        return "密码不能包含用户名"
    kinds = sum([
        any(c.islower() for c in pw),
        any(c.isupper() for c in pw),
        any(c.isdigit() for c in pw),
        any(not c.isalnum() for c in pw),
    ])
    if kinds < 2:
        return "密码需包含大小写字母、数字、符号中的至少两类"
    return None


def create_user(username: str, password: str, email: str | None = None,
                full_name: str | None = None, company: str | None = None,
                role: str = "sales", dept_id: int | None = None,
                must_change_pw: bool = True) -> dict:
    db = get_conn()
    if not username or len(username) < 3:
        raise HTTPException(400, "用户名至少 3 个字符")
    problem = password_problem(password, username)
    if problem:
        raise HTTPException(400, problem)
    if db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        raise HTTPException(400, "用户名已存在")
    ts = now()
    emp_no = next_emp_no()
    cur = db.execute(
        "INSERT INTO users(username,email,full_name,company,password_hash,role,"
        "dept_id,must_change_pw,is_active,created_at,emp_no,emp_status)"
        " VALUES(?,?,?,?,?,?,?,?,1,?,?,'active')",
        (username, email, full_name, company, hash_password(password), role,
         dept_id, 1 if must_change_pw else 0, ts, emp_no),
    )
    db.commit()
    row = db.execute(
        "SELECT id,username,email,full_name,company,role,dept_id,must_change_pw,"
        "is_active,created_at FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


def authenticate(username: str, password: str) -> dict | None:
    """校验账号密码。连续失败会锁定账号。返回 None 表示失败。"""
    db = get_conn()
    row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not row or not row["is_active"]:
        return None

    locked = row["locked_until"]
    if locked:
        if locked > now():
            return None
        db.execute("UPDATE users SET locked_until=NULL,login_fail_count=0 WHERE id=?",
                   (row["id"],))

    if not verify_password(password, row["password_hash"]):
        fails = int(row["login_fail_count"] or 0) + 1
        until = None
        if fails >= MAX_FAILS:
            until = (datetime.now() + timedelta(minutes=LOCK_MINUTES)
                     ).strftime("%Y-%m-%d %H:%M:%S")
            fails = 0
        db.execute("UPDATE users SET login_fail_count=?,locked_until=? WHERE id=?",
                   (fails, until, row["id"]))
        db.commit()
        return None

    db.execute("UPDATE users SET login_fail_count=0,locked_until=NULL,last_login_at=? "
               "WHERE id=?", (now(), row["id"]))
    db.commit()
    return dict(row)


def change_password(user_id: int, old_password: str, new_password: str) -> None:
    """用户自助改密：校验旧密码与新密码强度，并清掉强制改密标记。"""
    db = get_conn()
    row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        raise HTTPException(404, "用户不存在")
    if not verify_password(old_password, row["password_hash"]):
        raise HTTPException(400, "原密码不正确")
    if old_password == new_password:
        raise HTTPException(400, "新密码不能与原密码相同")
    problem = password_problem(new_password, row["username"])
    if problem:
        raise HTTPException(400, problem)
    db.execute("UPDATE users SET password_hash=?,must_change_pw=0,login_fail_count=0,"
               "locked_until=NULL WHERE id=?", (hash_password(new_password), user_id))
    db.commit()


def force_password(user_id: int, new_password: str) -> None:
    """管理员重置密码；重置后用户下次登录必须改密。"""
    db = get_conn()
    db.execute("UPDATE users SET password_hash=?,must_change_pw=1,login_fail_count=0,"
               "locked_until=NULL WHERE id=?", (hash_password(new_password), user_id))
    db.commit()


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    if not creds:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未登录")
    try:
        payload = jwt.decode(creds.credentials, SECRET_KEY, algorithms=[ALGO])
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录状态已失效，请重新登录")
    db = get_conn()
    row = db.execute(
        "SELECT id,username,email,full_name,company,role,dept_id,must_change_pw,"
        "is_active,org_unit_id,company_id,factory_id,bu_id,current_org_unit_id "
        "FROM users WHERE id=? AND is_active=1",
        (int(payload["sub"]),),
    ).fetchone()
    if not row:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在或已停用")
    return dict(row)


def ensure_bootstrap_admin() -> None:
    db = get_conn()
    if db.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] == 0:
        # 初始管理员挂到总部，并标记强制改密。
        # 兜底：找不到 HQ 就取第一个可用部门 —— 部门表还没种子数据时
        # （老库的迁移在 seed_org 之前跑），挂空部门会让「按部门统计人数」
        # 永远少一个人。
        dept = db.execute("SELECT id FROM departments WHERE code='HQ'").fetchone()
        if not dept:
            dept = db.execute(
                "SELECT id FROM departments WHERE is_active=1 ORDER BY sort_order, id LIMIT 1"
            ).fetchone()
        db.execute(
            "INSERT INTO users(username,email,full_name,company,password_hash,role,"
            "dept_id,must_change_pw,is_active,created_at,emp_no,emp_status)"
            " VALUES(?,?,?,?,?,?,?,1,1,?,?,'active')",
            ("admin", "admin@example.com", "系统管理员", "菲力克斯科技（广州）有限公司",
             hash_password("admin.123"), "admin", dept["id"] if dept else None,
             now(), next_emp_no()),
        )
        db.commit()
        release_conn()