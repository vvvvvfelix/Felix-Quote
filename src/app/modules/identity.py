"""模块层 — 组织与身份：部门、用户、角色、密码、登录安全。

企业化第一道门。多站点场景下的核心诉求：
- 人员归属到部门/工厂
- 角色对应权限与数据可见范围
- 强改密 + 登录失败锁定（内网也不代表可以弱口令）
- 所有身份变更留痕
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import (change_password, current_user, force_password, hash_password,
                   password_problem)
from ..core import orgutil, permissions as P
from ..core.db import get_conn, jdump, jload, now, release_conn
from ..core.services import audit, next_emp_no

router = APIRouter(prefix="/api/org", tags=["org"])

LOCK_MINUTES = 15
MAX_FAILS = 5

# ==========================================================================
# 员工生命周期
#   probation 试用期  active 在职  suspended 停职  left 离职  retired 退休
#   只有 probation / active 能登录系统；离职/退休账号保留历史单据责任归属
# ==========================================================================
EMP_STATUSES = {
    "probation": "试用期", "active": "在职", "suspended": "停职",
    "left": "离职", "retired": "退休",
}
LOGINABLE_STATUS = ("probation", "active")


# ==========================================================================
# 部门
# ==========================================================================
class DeptIn(BaseModel):
    code: str
    name: str
    kind: str = "office"          # office/branch/factory
    parent_id: int | None = None
    leader_id: int | None = None
    sort_order: int = 0


@router.get("/departments")
def list_departments(u: dict = Depends(current_user)):
    P.require(u, "users.view")
    db = get_conn()
    rows = [dict(r) for r in db.execute(
        "SELECT d.*, us.full_name AS leader_name FROM departments d "
        "LEFT JOIN users us ON us.id=d.leader_id "
        "WHERE d.is_active=1 ORDER BY d.sort_order, d.id")]
    release_conn()
    # 补跳层字段：小公司若跳过中间层级，前端直接拿 effective_* 即可，
    # 不必自己写递归，也不会因为父级停用而显示空白。
    orgs = orgutil.load_orgs()
    for r in rows:
        r["effective_parent_id"] = orgutil.effective_parent(r["id"], orgs)
        r["effective_leader_id"] = orgutil.effective_leader(r["id"], orgs)
    return rows


# ==========================================================================
# 部门目录看板
#   按传统组织架构分组（人事行政 / 财务部门 / 生产车间 / 销售部门），
#   每组下列出该组负责的功能模块与人员，点击可展开全部。
#   模块清单放在后端 —— 权限判定与分组逻辑不该散在前端。
# ==========================================================================
DEPT_MODULES = {
    "HR": [
        {"key": "inquiries", "name": "询盘管理", "perm": "inquiries.view",
         "desc": "客户询盘登记与跟进"},
        {"key": "org", "name": "组织与权限", "perm": "users.view",
         "desc": "部门 · 人员 · 工号 · 操作日志"},
        {"key": "documents", "name": "资料库", "perm": "documents.view",
         "desc": "合同 · 证书 · 图纸等文件"},
        {"key": "company", "name": "公司信息", "perm": None,
         "desc": "主体资质与对外署名"},
        {"key": "usage", "name": "Token 用量", "perm": None,
         "desc": "本地 0 Token 计量"},
        {"key": "mailsettings", "name": "发件设置", "perm": None,
         "desc": "配置你的发件邮箱"},
    ],
    "FIN": [
        {"key": "finance", "name": "财务应收", "perm": "finance.view",
         "desc": "应收台账与收汇登记"},
        {"key": "orders", "name": "销售订单", "perm": "orders.view",
         "desc": "订单履约与金额核对"},
        {"key": "analytics", "name": "经营分析", "perm": "analytics.view",
         "desc": "成交额与毛利趋势"},
    ],
    "PROD": [
        {"key": "production", "name": "生产看板", "perm": "orders.view",
         "desc": "在制订单进度与交期"},
        {"key": "orders", "name": "销售订单", "perm": "orders.view",
         "desc": "交期确认与技术参数"},
        {"key": "suppliers", "name": "供应商", "perm": "suppliers.view",
         "desc": "自有产线与外协厂商"},
        {"key": "products", "name": "产品库", "perm": "products.view",
         "desc": "规格参数与成本基础"},
    ],
    "SALES": [
        {"key": "dashboard", "name": "工作台", "perm": None,
         "desc": "我的待办与数据概览"},
        {"key": "tasks", "name": "待办中心", "perm": "tasks.view",
         "desc": "跨部门派活与我的待办"},
        {"key": "inquiries", "name": "询盘管理", "perm": "inquiries.view",
         "desc": "客户询盘登记与跟进"},
        {"key": "builder", "name": "报价单制作", "perm": "quotations.edit",
         "desc": "选品 → 生成报价单"},
        {"key": "quotations", "name": "报价单管理", "perm": "quotations.view",
         "desc": "历史报价单 · 打印 · 发邮件"},
        {"key": "orders", "name": "销售订单", "perm": "orders.view",
         "desc": "成交后的履约与跨部门确认"},
        {"key": "customers", "name": "客户管理", "perm": "customers.view",
         "desc": "海外客户资料与一键发报价单"},
    ],
}


@router.get("/integrity")
def org_integrity(u: dict = Depends(current_user)):
    """组织树体检：孤儿、断链、环。

    发现问题**不阻断运行** —— 只报告，由管理员决定是否修复。
    这是「中间层级可以不存在，但不能影响运行」的保障：系统照跑，问题可见。
    """
    P.require(u, "users.view")
    return orgutil.check_integrity()


@router.get("/resolve")
def org_resolve(org_id: int | None = None, kinds: str = "",
                u: dict = Depends(current_user)):
    """穿透解析调试接口：给定组织 id（可选期望类型，逗号分隔）返回最终生效组织。"""
    P.require(u, "users.view")
    orgs = orgutil.load_orgs()
    anchor = orgutil.resolve_anchor(org_id, orgs)
    out = {"input": org_id,
           "anchor": anchor,
           "anchor_name": (orgs.get(anchor) or {}).get("name") if anchor else None,
           "chain": [o.get("name") for o in orgutil.chain(org_id, orgs)],
           "leader": orgutil.effective_leader(org_id, orgs)}
    if kinds:
        t = orgutil.resolve_by_type(org_id, [k.strip() for k in kinds.split(",")],
                                    orgs)
        out["by_type"] = t
        out["by_type_name"] = (orgs.get(t) or {}).get("name") if t else None
    return out


@router.get("/org-tree")
def org_tree(u: dict = Depends(current_user)):
    """部门目录看板：四大分组 + 子部门 + 人员数 + 该组可见的功能模块。"""
    P.require(u, "users.view")
    db = get_conn()
    rows = [dict(r) for r in db.execute(
        "SELECT d.*, us.full_name AS leader_name FROM departments d "
        "LEFT JOIN users us ON us.id=d.leader_id "
        "WHERE d.is_active=1 ORDER BY d.sort_order, d.id")]
    counts = {r["dept_id"]: r["c"] for r in db.execute(
        "SELECT dept_id, COUNT(*) c FROM users "
        "WHERE is_active=1 AND dept_id IS NOT NULL GROUP BY dept_id")}

    groups = []
    for g in rows:
        if g.get("kind") != "group":
            continue
        children = [c for c in rows if c.get("parent_id") == g["id"]]
        mods = []
        for m in DEPT_MODULES.get(g["code"], []):
            perm = m.get("perm")
            if perm and not P.has_perm(u, perm):
                continue
            mods.append(m)
        # 该组人员数 = 子部门人员 + 本部门直属人员
        headcount = counts.get(g["id"], 0) + sum(counts.get(c["id"], 0) for c in children)
        groups.append({
            "id": g["id"], "code": g["code"], "name": g["name"],
            "leader_name": g.get("leader_name"), "headcount": headcount,
            "children": [{"id": c["id"], "name": c["name"], "code": c["code"],
                          "headcount": counts.get(c["id"], 0)} for c in children],
            "modules": mods,
        })
    release_conn()
    return {"groups": groups}


@router.post("/departments")
def create_department(body: DeptIn, u: dict = Depends(current_user)):
    P.require(u, "users.edit")
    db = get_conn()
    if db.execute("SELECT 1 FROM departments WHERE code=?", (body.code,)).fetchone():
        release_conn()
        raise HTTPException(400, "部门编码已存在")
    cur = db.execute(
        "INSERT INTO departments(code,name,kind,parent_id,leader_id,sort_order,created_at)"
        " VALUES(?,?,?,?,?,?,?)",
        (body.code, body.name, body.kind, body.parent_id, body.leader_id,
         body.sort_order, now()))
    db.commit()
    did = cur.lastrowid
    release_conn()
    audit(u, "org", "create", "department", did, body.name, after=body.model_dump())
    return {"id": did}


@router.put("/departments/{did}")
def update_department(did: int, body: DeptIn, u: dict = Depends(current_user)):
    P.require(u, "users.edit")
    db = get_conn()
    before = db.execute("SELECT * FROM departments WHERE id=?", (did,)).fetchone()
    if not before:
        release_conn()
        raise HTTPException(404, "部门不存在")
    db.execute("UPDATE departments SET name=?,kind=?,parent_id=?,leader_id=?,sort_order=? WHERE id=?",
               (body.name, body.kind, body.parent_id, body.leader_id, body.sort_order, did))
    db.commit()
    release_conn()
    audit(u, "org", "update", "department", did, body.name,
          before=dict(before), after=body.model_dump())
    return {"ok": True}


@router.delete("/departments/{did}")
def delete_department(did: int, u: dict = Depends(current_user)):
    P.require(u, "users.edit")
    db = get_conn()
    if db.execute("SELECT COUNT(*) c FROM users WHERE dept_id=?", (did,)).fetchone()["c"]:
        release_conn()
        raise HTTPException(400, "该部门下还有人员，不能删除")
    db.execute("UPDATE departments SET is_active=0 WHERE id=?", (did,))
    db.commit()
    release_conn()
    audit(u, "org", "delete", "department", did)
    return {"ok": True}


# ==========================================================================
# 用户
# ==========================================================================
class UserIn(BaseModel):
    username: str
    password: str | None = None
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    company: str | None = None
    role: str = "sales"
    dept_id: int | None = None
    # M004 三轴组织归属：org_unit_id 是权威组织锚点（current_org_unit_id 缺省回落到它）。
    # 落在某组织节点上后，company_id/factory_id/bu_id 三轴冗余列由本模块从组织树回写，
    # 供 scope_clause_org / switchable_org_nodes 直接读取。
    org_unit_id: int | None = None
    is_active: int = 1
    # 生命周期与人事信息 —— 由管理员维护
    emp_status: str = "active"
    join_date: str | None = None
    leave_date: str | None = None
    remark: str | None = None


def _org_rollups(db, org_unit_id: int | None) -> tuple:
    """给定组织节点 id，返回 (company_id, factory_id, bu_id) 三轴冗余值。"""
    if not org_unit_id:
        return (None, None, None)
    r = db.execute(
        "SELECT company_id, factory_id, bu_id FROM org_units WHERE id=?",
        (org_unit_id,)).fetchone()
    if not r:
        return (None, None, None)
    return (r["company_id"], r["factory_id"], r["bu_id"])


def _user_public(r) -> dict:
    keys = r.keys() if hasattr(r, "keys") else []
    return {
        "id": r["id"], "username": r["username"], "full_name": r["full_name"],
        "email": r["email"], "phone": r["phone"], "company": r["company"],
        "role": r["role"], "dept_id": r["dept_id"], "dept_name": r["dept_name"]
        if "dept_name" in keys else None,
        "is_active": r["is_active"], "must_change_pw": r["must_change_pw"],
        "locked_until": r["locked_until"], "last_login_at": r["last_login_at"],
        "created_at": r["created_at"],
        "emp_no": r["emp_no"] if "emp_no" in keys else None,
        "emp_status": r["emp_status"] if "emp_status" in keys else "active",
        "emp_status_label": EMP_STATUSES.get(
            r["emp_status"] if "emp_status" in keys else "active", "在职"),
        "join_date": r["join_date"] if "join_date" in keys else None,
        "leave_date": r["leave_date"] if "leave_date" in keys else None,
        "remark": r["remark"] if "remark" in keys else None,
    }


@router.get("/users")
def list_users(u: dict = Depends(current_user)):
    P.require(u, "users.view")
    db = get_conn()
    rows = [dict(r) for r in db.execute(
        "SELECT us.*, d.name AS dept_name FROM users us "
        "LEFT JOIN departments d ON d.id=us.dept_id ORDER BY us.id")]
    release_conn()
    return [_user_public(r) for r in rows]


@router.post("/users")
def create_user(body: UserIn, u: dict = Depends(current_user)):
    P.require(u, "users.edit")
    # 用统一的密码强度策略，不要只查长度 ——
    # 否则这里放行了弱口令，到改密时才被拦，用户体验差且留下弱密码
    problem = password_problem(body.password or "", body.username)
    if problem:
        raise HTTPException(400, problem)
    if body.role not in P.ROLES:
        raise HTTPException(400, f"未知角色 {body.role}")
    if body.emp_status not in EMP_STATUSES:
        raise HTTPException(400, f"未知生命周期状态 {body.emp_status}")
    db = get_conn()
    if db.execute("SELECT 1 FROM users WHERE username=?", (body.username,)).fetchone():
        release_conn()
        raise HTTPException(400, "用户名已存在")
    # 部门层级可能被停用或压根没有（小公司常跳过工厂/事业部）—— 自动穿透到
    # 最近的有效组织，保证员工永远挂在一个真实存在的组织上，不产生「无部门人员」。
    dept_id = orgutil.resolve_anchor(body.dept_id)
    # 权威组织锚点：org_unit_id 落点后回写 company/factory/bu 三轴冗余列，
    # 并作为 current_org_unit_id（组织切换器初始锚点）。
    org_unit_id = body.org_unit_id if body.org_unit_id else None
    company_id, factory_id, bu_id = _org_rollups(db, org_unit_id)
    # 工号由系统生成，用户不可自选
    emp_no = next_emp_no()
    # 生命周期决定能否登录：离职/退休/停职一律禁止登录
    is_active = 1 if body.emp_status in LOGINABLE_STATUS else 0
    cur = db.execute(
        "INSERT INTO users(username,email,full_name,company,phone,password_hash,"
        "role,dept_id,must_change_pw,is_active,created_at,emp_no,emp_status,"
        "join_date,leave_date,remark,org_unit_id,company_id,factory_id,bu_id,"
        "current_org_unit_id)"
        " VALUES(?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?,?)",
        (body.username, body.email, body.full_name, body.company, body.phone,
         hash_password(body.password), body.role, dept_id,
         is_active, now(), emp_no, body.emp_status,
         body.join_date, body.leave_date, body.remark,
         org_unit_id, company_id, factory_id, bu_id, org_unit_id))
    db.commit()
    uid = cur.lastrowid
    release_conn()
    audit(u, "org", "create", "user", uid, body.username,
          after={"username": body.username, "role": body.role,
                 "dept_id": dept_id, "org_unit_id": org_unit_id,
                 "emp_no": emp_no, "emp_status": body.emp_status})
    return {"id": uid, "must_change_pw": True, "emp_no": emp_no,
            "emp_status": body.emp_status, "is_active": is_active}


@router.put("/users/{uid}")
def update_user(uid: int, body: UserIn, u: dict = Depends(current_user)):
    P.require(u, "users.edit")
    if body.emp_status not in EMP_STATUSES:
        raise HTTPException(400, f"未知生命周期状态 {body.emp_status}")
    db = get_conn()
    before = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not before:
        release_conn()
        raise HTTPException(404, "用户不存在")
    if before["role"] == "admin" and body.role != "admin":
        admins = db.execute(
            "SELECT COUNT(*) c FROM users WHERE role='admin' AND is_active=1"
        ).fetchone()["c"]
        if admins <= 1:
            release_conn()
            raise HTTPException(400, "系统至少要保留一个管理员")
    # 生命周期决定能否登录：离职/退休/停职一律禁止登录。
    # 工号（emp_no）不在可更新字段里 —— 系统生成，人一生成就固定。
    is_active = 1 if body.emp_status in LOGINABLE_STATUS else 0
    dept_id = orgutil.resolve_anchor(body.dept_id)
    # 权威组织锚点回写三轴冗余列；若改了组织且当前组织锚点未单独设定，同步更新之
    org_unit_id = body.org_unit_id if body.org_unit_id else None
    company_id, factory_id, bu_id = _org_rollups(db, org_unit_id)
    # 离职/退休时自动补离职日期（管理员没填则用今天）
    leave_date = body.leave_date
    if body.emp_status in ("left", "retired") and not leave_date:
        leave_date = now()[:10]
    if body.emp_status in LOGINABLE_STATUS:
        leave_date = None
    db.execute(
        "UPDATE users SET full_name=?,email=?,phone=?,company=?,role=?,dept_id=?,"
        "is_active=?,emp_status=?,join_date=?,leave_date=?,remark=?,"
        "org_unit_id=?,company_id=?,factory_id=?,bu_id=? WHERE id=?",
        (body.full_name, body.email, body.phone, body.company, body.role,
         dept_id, is_active, body.emp_status, body.join_date, leave_date,
         body.remark, org_unit_id, company_id, factory_id, bu_id, uid))
    # 当前组织锚点若原本等于旧 org_unit_id（未单独指定过），随迁移同步；
    # 这里仅在用户尚未显式切换过组织时跟随 org_unit_id 变动。
    old_ou = before["org_unit_id"] if "org_unit_id" in before.keys() else None
    db.execute(
        "UPDATE users SET current_org_unit_id=? WHERE id=? AND "
        "(current_org_unit_id IS NULL OR current_org_unit_id=?)",
        (org_unit_id, uid, old_ou))
    if body.password:
        db.execute("UPDATE users SET password_hash=?,must_change_pw=1 WHERE id=?",
                   (hash_password(body.password), uid))
    db.commit()
    release_conn()
    audit(u, "org", "update", "user", uid, before["username"],
          before={k: before[k] for k in ("role", "dept_id", "is_active", "emp_status")
                  if k in before.keys()},
          after={"role": body.role, "dept_id": dept_id,
                 "emp_status": body.emp_status, "is_active": is_active,
                 "leave_date": leave_date})
    return {"ok": True, "is_active": is_active, "emp_status": body.emp_status}


@router.delete("/users/{uid}")
def disable_user(uid: int, u: dict = Depends(current_user)):
    """停用而非物理删除 —— 历史单据要保留责任人。"""
    P.require(u, "users.edit")
    if uid == u["id"]:
        raise HTTPException(400, "不能停用自己")
    db = get_conn()
    before = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not before:
        release_conn()
        raise HTTPException(404, "用户不存在")
    if before["role"] == "admin":
        admins = db.execute(
            "SELECT COUNT(*) c FROM users WHERE role='admin' AND is_active=1"
        ).fetchone()["c"]
        if admins <= 1:
            release_conn()
            raise HTTPException(400, "系统至少要保留一个管理员")
    db.execute("UPDATE users SET is_active=1-is_active WHERE id=?", (uid,))
    db.commit()
    release_conn()
    audit(u, "org", "toggle", "user", uid, before["username"], before=dict(before))
    return {"ok": True}


@router.post("/users/{uid}/unlock")
def unlock_user(uid: int, u: dict = Depends(current_user)):
    P.require(u, "users.edit")
    db = get_conn()
    db.execute("UPDATE users SET locked_until=NULL,login_fail_count=0 WHERE id=?", (uid,))
    db.commit()
    release_conn()
    audit(u, "org", "unlock", "user", uid)
    return {"ok": True}


@router.post("/users/{uid}/force-password")
def force_password_reset(uid: int, u: dict = Depends(current_user)):
    P.require(u, "users.edit")
    db = get_conn()
    db.execute("UPDATE users SET must_change_pw=1 WHERE id=?", (uid,))
    db.commit()
    release_conn()
    audit(u, "org", "force_password", "user", uid)
    return {"ok": True}


# ==========================================================================
# 角色
# ==========================================================================
@router.get("/roles")
def list_roles(u: dict = Depends(current_user)):
    P.require(u, "users.view")
    return P.roles_catalog()


@router.get("/permissions")
def list_permissions(u: dict = Depends(current_user)):
    """权限点清单，供前端渲染角色矩阵。"""
    P.require(u, "users.view")
    mods: dict[str, list] = {}
    for code, r in P.ROLES.items():
        perms = r["perm"] if isinstance(r["perm"], list) else ["*"]
        for p in perms:
            mod, act = (p.split(".", 1) + [""])[:2]
            mods.setdefault(mod, [])
            if act and act not in mods[mod]:
                mods[mod].append(act)
    out = []
    for mod in sorted(mods):
        actions = sorted(set(mods[mod]))
        if not actions:
            out.append({"module": mod, "actions": [{"key": "*", "label": "全部"}]})
            continue
        out.append({"module": mod,
                    "actions": [{"key": a, "label": {"view": "查看", "edit": "编辑",
                                                      "approve": "审批",
                                                      "tech_confirm": "技术确认",
                                                      "purchase": "采购",
                                                      "production": "生产",
                                                      "confirm": "确认"}.get(a, a)}
                                for a in actions]})
    return out


# ==========================================================================
# 我的资料与改密
# ==========================================================================
@router.get("/me")
def get_me(u: dict = Depends(current_user)):
    db = get_conn()
    r = db.execute(
        "SELECT us.*, d.name AS dept_name, d.kind AS dept_kind FROM users us "
        "LEFT JOIN departments d ON d.id=us.dept_id WHERE us.id=?", (u["id"],)).fetchone()
    # 当前组织节点（组织切换器锚点）的三轴信息 —— 趁连接还开着一次性取出来
    ou = None
    if r and r["current_org_unit_id"]:
        ou = db.execute(
            "SELECT id,name,org_type,company_id,factory_id,bu_id FROM org_units "
            "WHERE id=?", (r["current_org_unit_id"],)).fetchone()
    release_conn()
    if not r:
        raise HTTPException(404, "用户不存在")
    info = _user_public(r)
    info["dept_kind"] = r["dept_kind"]
    info["current_org_unit_id"] = r["current_org_unit_id"]
    info["current_org_name"] = ou["name"] if ou else None
    info["current_org_type"] = ou["org_type"] if ou else None
    info["current_org_rollups"] = dict(ou) if ou else {}
    info["scope"] = P.scope_of(u)
    info["scope_label"] = P.SCOPE_LABEL.get(info["scope"], info["scope"])
    info["permissions"] = P.perms_of(u["role"])
    info["role_name"] = P.ROLES.get(u["role"], {}).get("name", u["role"])
    info["switch_options"] = P.switchable_org_nodes(u)
    return info


@router.get("/switch-options")
def org_switch_options(u: dict = Depends(current_user)):
    """组织切换器：返回当前用户可切换到的组织节点（company/factory/bu）。"""
    P.require(u, "structure.view")
    return P.switchable_org_nodes(u)


@router.post("/switch")
def org_switch(body: dict, u: dict = Depends(current_user)):
    """切换当前组织锚点（current_org_unit_id）。

    只能切到 switch-options 返回的节点集合内（管理员/董事长可切任意）。
    切换后 company/factory/bu 三档数据范围随之改变。
    """
    P.require(u, "structure.view")
    target = body.get("org_unit_id") if isinstance(body, dict) else None
    if not target:
        raise HTTPException(400, "请提供 org_unit_id")
    db = get_conn()
    node = db.execute(
        "SELECT id,org_type,is_active FROM org_units WHERE id=?", (target,)).fetchone()
    if not node or not node["is_active"]:
        release_conn()
        raise HTTPException(404, "组织节点不存在或已停用")
    # 权限校验：必须落在 switchable 集合内（管理员/董事长集合为全部）
    allowed = {x["id"] for x in P.switchable_org_nodes(u)}
    if target not in allowed:
        release_conn()
        raise HTTPException(403, "无权切换到该组织")
    db.execute("UPDATE users SET current_org_unit_id=? WHERE id=?", (target, u["id"]))
    db.commit()
    release_conn()
    audit(u, "org", "switch", "user", u["id"], "切换当前组织",
          after={"org_unit_id": target})
    # 返回新的 me 片段，前端据此刷新范围口径
    return {"ok": True, "current_org_unit_id": target,
            "current_org_type": node["org_type"]}


class PwdIn(BaseModel):
    old_password: str
    new_password: str


@router.post("/me/password")
def change_own_password(body: PwdIn, u: dict = Depends(current_user)):
    change_password(u["id"], body.old_password, body.new_password)
    audit(u, "org", "change_password", "user", u["id"])
    return {"ok": True}


# ==========================================================================
# 审计日志查询
# ==========================================================================
@router.get("/audit-logs")
def list_audit_logs(u: dict = Depends(current_user),
                    module: str | None = None, target_type: str | None = None,
                    user_id: int | None = None, limit: int = 100):
    P.require(u, "audit.view")
    db = get_conn()
    sql = "SELECT * FROM audit_logs WHERE 1=1"
    args: list = []
    if module:
        sql += " AND module=?"
        args.append(module)
    if target_type:
        sql += " AND target_type=?"
        args.append(target_type)
    if user_id:
        sql += " AND user_id=?"
        args.append(user_id)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(min(limit, 500))
    rows = [dict(r) for r in db.execute(sql, args)]
    for r in rows:
        r["before"] = jload(r.pop("before_json", None), None)
        r["after"] = jload(r.pop("after_json", None), None)
    return rows


# ==========================================================================
# 初始化种子：默认部门与角色
# ==========================================================================
def seed_org() -> None:
    """灌部门与角色的种子数据 —— 幂等，可重复调用。

    四个顶层分组（传统组织架构）：人事行政 / 财务部门 / 生产车间 / 销售部门。
    子部门挂在对应分组下。

    用「按 code upsert」而不是「表空才插」：迁移里可能已经调整过某些部门，
    表空判断会让漏掉的部门永远补不上（新装系统曾漏掉总部/技术部等 6 个）。
    """
    db = get_conn()
    # (code, 名称, kind, 分组code, 排序)
    layout = [
        ("SALES", "销售部门", "group", None, 1),
        ("PROD", "生产车间", "group", None, 2),
        ("FIN", "财务部门", "group", None, 3),
        ("HR", "人事行政", "group", None, 4),
        ("AD", "行政人事部", "office", "HR", 5),
        ("HQ", "总部", "office", "HR", 6),
        ("TECH", "技术部", "office", "PROD", 7),
        ("PUR", "采购部", "office", "PROD", 8),
        ("LOG", "物流部", "office", "PROD", 9),
        ("FACTORY", "工厂", "factory", "PROD", 10),
        ("BRANCH", "分公司", "branch", "SALES", 11),
    ]
    ids: dict[str, int] = {}
    for code, name, kind, parent, order in layout:
        r = db.execute("SELECT id FROM departments WHERE code=?", (code,)).fetchone()
        if r:
            ids[code] = r["id"]
            # 名称与归属可能被管理员改过 —— 只在分组结构上还不对时纠正
            if kind == "group":
                db.execute("UPDATE departments SET kind='group', parent_id=NULL,"
                           " is_active=1 WHERE id=?", (r["id"],))
            continue
        cur = db.execute(
            "INSERT INTO departments(code,name,kind,parent_id,sort_order,is_active,"
            "created_at) VALUES(?,?,?,?,?,1,?)",
            (code, name, kind, None, order, now()))
        ids[code] = cur.lastrowid
    # 第二遍挂父子关系（父部门可能刚创建）
    for code, name, kind, parent, order in layout:
        if parent:
            db.execute("UPDATE departments SET parent_id=? WHERE code=?",
                       (ids.get(parent), code))
    db.commit()
    release_conn()
    P.seed_roles()
