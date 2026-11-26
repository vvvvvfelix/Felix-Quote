"""模块层 — 员工档案与异动（M003）：人事档案 + 全生命周期异动流水。

三轴模型的第三轴（人员轴）的「人事属性」层。身份模块 `identity` 的 users 只管
登录与生命周期状态（probation/active/suspended/left/retired），本模块补上 HR 才
关心的纸质档案（证件 / 合同 / 社保 / 银行卡 / 紧急联系人）与**全留痕**的异动流水。

【异动复用 position_assignments】
    employee_events 不另起「任职历史表」——入职 / 调岗 / 晋升 写事件的同时，
    同步回写 M002 的 position_assignments（结束旧岗、新开主岗）；离职 / 退休
    则结束所有在职任职。一套任职关系、两个视角，避免两套数据对不齐。

【敏感字段单列权限】
    证件类字段需 `employee.sensitive.view`，与 `employee.view`（普通档案：合同 /
    紧急联系人姓名 / 入职日期）分层。普通 HR 看得到合同，但下列字段只有薪资 /
    人事负责人看到明文，其余人只拿到掩码：

        id_card 身份证、bank_account 银行卡、social_no 社保号、
        emergency_phone 紧急联系电话、id_scan_path 证件扫描件路径（整键不外发）

    社保号与紧急联系电话此前被漏掉：社保号是社保系统的登录凭据之一，紧急联系电话
    属于「第三人的可直接拨通的联系方式」，二者都与身份证同等级，不该跟着
    employee.view 裸奔。证件扫描件上是身份证全号，掩码救不回来，只能不外发。

【跨模块红线】
    本文件**不 import** position 模块，只在 SQL 层读取 positions / position_assignments /
    org_units 三张共享表；任职关系的写入通过本模块的 `_move_to_position` 直接落库。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_user
from ..core import orgutil, permissions as P
from ..core.db import get_conn, jdump, jload, now, release_conn
from ..core.services import audit

router = APIRouter(prefix="/api/employee", tags=["employee"])

# ==========================================================================
# 一、枚举 —— 前端不得硬编码中文，一律从 /types 取
# ==========================================================================
# 异动类型
EVENT_TYPES = {
    "onboard": "入职", "regular": "转正", "transfer": "调岗",
    "promote": "晋升", "leave": "离职", "retire": "退休",
}
# 生命周期状态标签（与 identity.EMP_STATUSES 对齐，本模块局部副本，避免跨模块 import）
EMP_STATUS_LABEL = {
    "probation": "试用期", "active": "在职", "suspended": "停职",
    "left": "离职", "retired": "退休",
}


# ==========================================================================
# 二、内部工具
# ==========================================================================
def _primary_position_map(db) -> dict[int, dict]:
    """当前在职主岗：user_id -> {pid, pname, org_unit_id, oname}。"""
    out: dict[int, dict] = {}
    for r in db.execute(
        "SELECT pa.user_id, p.id pid, p.name pname, p.org_unit_id, "
        "ou.name oname FROM position_assignments pa "
        "JOIN positions p ON p.id=pa.position_id "
        "LEFT JOIN org_units ou ON ou.id=p.org_unit_id "
        "WHERE pa.is_active=1 AND pa.is_primary=1"):
        out[r["user_id"]] = dict(r)
    return out


def _move_to_position(db, user_id: int, position_id: int,
                      event_date: str, operator: str) -> None:
    """结束该用户所有在职任职，并在目标岗位新开一条主岗任职。

    入职 / 调岗 / 晋升 统一走这里：先把旧岗（含兼岗）全部结束，再开新主岗。
    """
    db.execute("UPDATE position_assignments SET is_active=0,end_date=?,updated_at=? "
               "WHERE user_id=? AND is_active=1",
               (event_date, now(), user_id))
    db.execute(
        "INSERT INTO position_assignments(position_id,user_id,is_primary,ratio,"
        "start_date,end_date,remark,is_active,created_at,updated_at) "
        "VALUES(?,?,1,1.0,?,NULL,?,1,?,?)",
        (position_id, user_id, event_date, f"系统异动·{operator}", now(), now()))


def _end_all_positions(db, user_id: int, event_date: str) -> None:
    """离职 / 退休：结束所有在职任职（不再开新岗）。"""
    db.execute("UPDATE position_assignments SET is_active=0,end_date=?,updated_at=? "
               "WHERE user_id=? AND is_active=1",
               (event_date, now(), user_id))


def _assert_position(db, pid: int | None):
    if pid is None:
        return
    row = db.execute("SELECT id,is_active FROM positions WHERE id=?", (pid,)).fetchone()
    if not row:
        raise HTTPException(404, "岗位不存在")
    if not row["is_active"]:
        raise HTTPException(400, "该岗位已停用，无法作为异动目标")


# 敏感字段脱敏规则：字段 -> (保留前几位, 保留后几位)
# 为什么不直接删键：删掉后前端只能在「有权限才渲染」的分支里显示，一旦某个页面
# 忘了判权限就直接显示空；保留掩码（4401**********0011）既能让人认出是张三的证件，
# 又拿不到可用号码，误显示也泄露不了。
_SENSITIVE_FIELDS = {
    "id_card": (4, 4),            # 身份证：4401**********0011
    "bank_account": (0, 4),       # 银行卡：************0001
    "social_no": (0, 4),          # 社保号：*******0001
    "emergency_phone": (3, 4),    # 紧急联系电话：139****0001
}
# 附件类：证件扫描件上印着身份证全号，路径本身就是泄露渠道 —— 掩码救不回来，
# 只能整键不外发。
_SENSITIVE_ATTACHMENTS = ("id_scan_path",)


def _mask_value(v: object, head: int, tail: int) -> object:
    """保留首/尾若干位、中间打码；长度不够时整串打码（绝不漏明文）。"""
    if v is None or v == "":
        return v
    s = str(v)
    n = len(s)
    if n <= head + tail:
        return "*" * n
    return (s[:head] if head else "") + "*" * (n - head - tail) \
        + (s[n - tail:] if tail else "")


# 无 sensitive 权限时连「改」都不允许（见 _sanitize_profile 的说明）
_LOCKED_KEYS = tuple(_SENSITIVE_FIELDS) + _SENSITIVE_ATTACHMENTS


def _gated_profile(r: dict, with_sensitive: bool) -> dict:
    """脱敏：无 employee.sensitive.view 时，证件类字段只给掩码、扫描件路径不外发。"""
    if with_sensitive:
        return dict(r)
    out = dict(r)
    for k, (head, tail) in _SENSITIVE_FIELDS.items():
        if k in out:
            out[k] = _mask_value(out[k], head, tail)
    for k in _SENSITIVE_ATTACHMENTS:
        out.pop(k, None)
    return out


def _sanitize_profile(vals: dict, with_sensitive: bool) -> dict:
    """保存档案时，看不到敏感字段的人也**改不了**这些字段。

    脱敏改成「给掩码」之后这步是必须的：否则前端表单里看到的
    `4401**********000X` 会在保存时被原样提交回来，把真号码永久覆盖成掩码。
    无权时这些字段直接不参与写入（更新即保留原值，新建则留空）。
    """
    if with_sensitive:
        return vals
    return {k: v for k, v in vals.items() if k not in _LOCKED_KEYS}


# ==========================================================================
# 三、枚举接口
# ==========================================================================
@router.get("/types")
def types(u: dict = Depends(current_user)):
    P.require(u, "employee.view")
    return {
        "event_types": [{"value": k, "label": v} for k, v in EVENT_TYPES.items()],
        "statuses": [{"value": k, "label": v} for k, v in EMP_STATUS_LABEL.items()],
    }


# ==========================================================================
# 四、员工花名册（关联 org_unit + position）
# ==========================================================================
@router.get("/list")
def list_employees(keyword: str = "", org_unit_id: int | None = None,
                   status: str = "", u: dict = Depends(current_user)):
    """员工花名册：人员 + 档案摘要 + 当前主岗 + 所属组织。

    敏感字段（身份证 / 银行卡等）**不在此接口返回**，详情接口按权限控制。
    org_unit_id 命中该组织节点及其全部下级；未挂岗位（只有部门）的人不参与
    组织筛选——他们在组织树上没有位置。
    """
    P.require(u, "employee.view")
    db = get_conn()
    sql = (
        "SELECT u.id,u.username,u.full_name,u.emp_no,u.emp_status,u.dept_id,"
        "d.name AS dept_name, ep.id AS profile_id, ep.contract_no,"
        "ep.contract_start,ep.contract_end,ep.emergency_contact,ep.hire_date,"
        "ep.is_active AS p_active "
        "FROM users u LEFT JOIN departments d ON d.id=u.dept_id "
        "LEFT JOIN employee_profiles ep ON ep.user_id=u.id AND ep.is_active=1 "
        "WHERE u.is_active=1")
    args: list = []
    if keyword:
        sql += (" AND (u.full_name LIKE ? OR u.username LIKE ? OR u.emp_no LIKE ? "
                "OR u.username LIKE ?)")
        kw = f"%{keyword}%"
        args += [kw, kw, kw, f"demo_staff%{keyword}%"]
    if status:
        sql += " AND u.emp_status=?"
        args.append(status)
    sql += " ORDER BY u.id"
    rows = [dict(r) for r in db.execute(sql, args)]

    prim = _primary_position_map(db)
    orgs = orgutil.load_orgs("org_units")
    for r in rows:
        r["emp_status_label"] = EMP_STATUS_LABEL.get(r["emp_status"], r["emp_status"])
        p = prim.get(r["id"])
        r["position_id"] = p["pid"] if p else None
        r["position_name"] = p["pname"] if p else None
        # 注意两个 id 空间：dept_id 指向 M001 的 departments，org_unit_id 指向
        # M004 的组织树。这里**不**把 dept_id 当 org_unit_id 用（旧实现这么做了），
        # 否则筛选时「部门的 3」会命中「组织树上的 3」，筛出完全不相干的人。
        # 名称仍可回落到部门名，避免老数据看起来组织一栏全空。
        r["org_unit_id"] = p["org_unit_id"] if p else None
        r["org_unit_name"] = (orgs.get(p["org_unit_id"], {}).get("name")
                              if p and p["org_unit_id"] in orgs
                              else r["dept_name"])
        r["has_profile"] = bool(r["profile_id"])
    # 组织筛选：选中节点 + 其整棵子树（树上点「XX 事业部」要看得到事业部里的人，
    # 而不只是挂在该节点上的那几个）。走 Python 而非 SQL：员工挂哪个组织是
    # 「主岗 → 岗位 → 组织」两级推导的结果，SQL 里拼会重复这套逻辑。
    if org_unit_id:
        # 不存在的组织要明确报错：orgutil 的工具函数会「穿透」到根组织，
        # 那样筛出来的就是全公司，用户以为筛了其实没筛——比报错危险得多。
        if org_unit_id not in orgs:
            release_conn()
            raise HTTPException(400, f"筛选的组织 #{org_unit_id} 不存在")
        keep = set(orgutil.subtree_ids(org_unit_id, orgs))
        rows = [r for r in rows if r["org_unit_id"] in keep]
    release_conn()
    return rows


# ==========================================================================
# 五、档案 CRUD
# ==========================================================================
class ProfileIn(BaseModel):
    user_id: int
    id_card: str | None = None
    contract_no: str | None = None
    contract_start: str | None = None
    contract_end: str | None = None
    social_no: str | None = None
    bank_name: str | None = None
    bank_account: str | None = None
    emergency_contact: str | None = None
    emergency_phone: str | None = None
    hire_date: str | None = None
    id_scan_path: str | None = None
    is_active: int = 1


@router.get("/profiles")
def list_profiles(user_id: int | None = None, u: dict = Depends(current_user)):
    """档案列表 / 详情。

    传 user_id 返回该用户的档案详情（按 employee.sensitive.view 决定
    是否下发身份证 / 银行卡）；不传返回全部有效档案摘要（不含敏感字段）。
    """
    P.require(u, "employee.view")
    with_sensitive = P.has_perm(u, "employee.sensitive.view")
    db = get_conn()
    if user_id:
        r = db.execute(
            "SELECT * FROM employee_profiles WHERE user_id=? AND is_active=1",
            (user_id,)).fetchone()
        release_conn()
        if not r:
            raise HTTPException(404, "该员工暂无档案")
        return _gated_profile(dict(r), with_sensitive)
    rows = [dict(r) for r in db.execute(
        "SELECT ep.*, u.full_name, u.emp_no, u.emp_status "
        "FROM employee_profiles ep JOIN users u ON u.id=ep.user_id "
        "WHERE ep.is_active=1 ORDER BY ep.id")]
    release_conn()
    return [_gated_profile(r, with_sensitive) for r in rows]


@router.post("/profiles")
def create_profile(body: ProfileIn, u: dict = Depends(current_user)):
    """新建 / 更新档案（按 user_id 幂等 upsert：已有一份有效档案则覆盖更新）。

    敏感字段按权限过滤：无 employee.sensitive.view 的人提交上来的身份证等字段
    一律不落库（否则掩码会被写成真值）。
    """
    P.require(u, "employee.edit")
    with_sensitive = P.has_perm(u, "employee.sensitive.view")
    db = get_conn()
    if not db.execute("SELECT 1 FROM users WHERE id=?", (body.user_id,)).fetchone():
        release_conn()
        raise HTTPException(404, "人员不存在")
    exist = db.execute(
        "SELECT id FROM employee_profiles WHERE user_id=? AND is_active=1",
        (body.user_id,)).fetchone()
    fields = ("id_card", "contract_no", "contract_start", "contract_end", "social_no",
              "bank_name", "bank_account", "emergency_contact", "emergency_phone",
              "hire_date", "id_scan_path", "is_active")
    vals = _sanitize_profile({k: getattr(body, k) for k in fields}, with_sensitive)
    if exist:
        pid = exist["id"]
        cols = [k for k in fields if k in vals]
        sets = ",".join(f"{k}=?" for k in cols) + ",updated_at=?"
        db.execute(f"UPDATE employee_profiles SET {sets} WHERE id=?",
                   [vals[k] for k in cols] + [now(), pid])
        db.commit()
        audit(u, "employee", "update", "employee_profile", pid, after=vals)
        release_conn()
        return {"id": pid, "updated": True}
    # 无权写入的敏感字段在新建时留空
    for k in fields:
        vals.setdefault(k, None)
    cur = db.execute(
        "INSERT INTO employee_profiles(user_id,id_card,contract_no,contract_start,"
        "contract_end,social_no,bank_name,bank_account,emergency_contact,"
        "emergency_phone,hire_date,id_scan_path,is_active,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (body.user_id, vals["id_card"], vals["contract_no"], vals["contract_start"],
         vals["contract_end"], vals["social_no"], vals["bank_name"],
         vals["bank_account"], vals["emergency_contact"], vals["emergency_phone"],
         vals["hire_date"], vals["id_scan_path"], vals["is_active"], now(), now()))
    pid = cur.lastrowid
    db.commit()
    audit(u, "employee", "create", "employee_profile", pid, after=vals)
    release_conn()
    return {"id": pid, "created": True}


@router.put("/profiles/{pid}")
def update_profile(pid: int, body: ProfileIn, u: dict = Depends(current_user)):
    """更新档案。无 sensitive 权限时，敏感字段不参与写入（保留原值）。"""
    P.require(u, "employee.edit")
    with_sensitive = P.has_perm(u, "employee.sensitive.view")
    db = get_conn()
    before = db.execute("SELECT * FROM employee_profiles WHERE id=?", (pid,)).fetchone()
    if not before:
        release_conn()
        raise HTTPException(404, "档案不存在")
    fields = ("id_card", "contract_no", "contract_start", "contract_end", "social_no",
              "bank_name", "bank_account", "emergency_contact", "emergency_phone",
              "hire_date", "id_scan_path", "is_active")
    vals = _sanitize_profile({k: getattr(body, k) for k in fields}, with_sensitive)
    cols = [k for k in fields if k in vals]
    sets = ",".join(f"{k}=?" for k in cols) + ",updated_at=?"
    db.execute(f"UPDATE employee_profiles SET {sets} WHERE id=?",
               [vals[k] for k in cols] + [now(), pid])
    db.commit()
    audit(u, "employee", "update", "employee_profile", pid,
          before={k: before[k] for k in fields}, after=vals)
    release_conn()
    return {"ok": True}


@router.delete("/profiles/{pid}")
def disable_profile(pid: int, u: dict = Depends(current_user)):
    """注销档案（软删，保留历史）。"""
    P.require(u, "employee.edit")
    db = get_conn()
    if not db.execute("SELECT 1 FROM employee_profiles WHERE id=?", (pid,)).fetchone():
        release_conn()
        raise HTTPException(404, "档案不存在")
    db.execute("UPDATE employee_profiles SET is_active=0,updated_at=? WHERE id=?",
               (now(), pid))
    db.commit()
    release_conn()
    audit(u, "employee", "disable", "employee_profile", pid)
    return {"ok": True}


# ==========================================================================
# 六、异动流水
# ==========================================================================
class EventIn(BaseModel):
    user_id: int
    event_type: str
    from_position_id: int | None = None
    to_position_id: int | None = None
    event_date: str | None = None
    remark: str | None = None


@router.get("/events")
def list_events(user_id: int | None = None, u: dict = Depends(current_user)):
    P.require(u, "employee.view")
    db = get_conn()
    sql = ("SELECT e.*, u.full_name, tp.name AS to_pos_name, fp.name AS from_pos_name "
           "FROM employee_events e JOIN users u ON u.id=e.user_id "
           "LEFT JOIN positions tp ON tp.id=e.to_position_id "
           "LEFT JOIN positions fp ON fp.id=e.from_position_id "
           "WHERE 1=1")
    args: list = []
    if user_id:
        sql += " AND e.user_id=?"
        args.append(user_id)
    sql += " ORDER BY e.event_date DESC, e.id DESC LIMIT 500"
    rows = [dict(r) for r in db.execute(sql, args)]
    for r in rows:
        r["event_type_label"] = EVENT_TYPES.get(r["event_type"], r["event_type"])
    release_conn()
    return rows


@router.post("/events")
def create_event(body: EventIn, u: dict = Depends(current_user)):
    """登记异动并联动任职关系。

    - 入职 / 调岗 / 晋升：需 to_position_id，结束旧岗并新开主岗（复用 position_assignments）
    - 离职 / 退休：结束所有在职任职，不再开新岗
    - 转正：仅留痕，不动任职
    """
    P.require(u, "employee.edit")
    if body.event_type not in EVENT_TYPES:
        raise HTTPException(400, f"未知异动类型 {body.event_type}")
    if body.event_type in ("onboard", "transfer", "promote") and not body.to_position_id:
        raise HTTPException(400, "入职 / 调岗 / 晋升 需指定异动后岗位")
    db = get_conn()
    usr = db.execute("SELECT id,full_name,emp_status,is_active FROM users WHERE id=?",
                     (body.user_id,)).fetchone()
    if not usr or not usr["is_active"]:
        release_conn()
        raise HTTPException(404, "人员不存在或已停用")
    ev_date = body.event_date or now()[:10]
    _assert_position(db, body.to_position_id)
    _assert_position(db, body.from_position_id)

    # 联动任职关系
    if body.event_type in ("onboard", "transfer", "promote"):
        _move_to_position(db, body.user_id, body.to_position_id, ev_date,
                          u.get("username", ""))
    elif body.event_type in ("leave", "retire"):
        _end_all_positions(db, body.user_id, ev_date)

    cur = db.execute(
        "INSERT INTO employee_events(user_id,event_type,from_position_id,"
        "to_position_id,event_date,remark,operator,created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (body.user_id, body.event_type, body.from_position_id, body.to_position_id,
         ev_date, body.remark, u.get("username", ""), now()))
    eid = cur.lastrowid
    db.commit()
    audit(u, "employee", "event", "employee_event", eid,
          after={"user_id": body.user_id, "event_type": body.event_type,
                 "to_position_id": body.to_position_id, "event_date": ev_date})
    release_conn()
    return {"id": eid}


# ==========================================================================
# 七、种子
# =========================================================================
def seed_employees() -> dict:
    """给 M002 造的 17/18 名 demo_staff_xx 挂档案 + 异动，不新建人员、不动任职。

    幂等：已有有效档案 / 异动则跳过。绝不修改 position_assignments，保护 M002 的
    编制 / 在岗 / 超编演示数据。
    """
    db = get_conn()
    staff = db.execute(
        "SELECT id,emp_no,full_name FROM users "
        "WHERE username LIKE 'demo_staff_%' AND is_active=1 "
        "ORDER BY id").fetchall()
    seeded_profiles = 0
    seeded_events = 0
    base_year = "2019-07-01"
    for i, s in enumerate(staff):
        uid = s["id"]
        # 入职日期：按序错开，制造「老员工 vs 新人」观感
        hire = f"{2019 + (i % 5)}-{(i % 12) + 1:02d}-15"
        # 档案（幂等：有效档案已存在则跳过）
        if not db.execute(
                "SELECT 1 FROM employee_profiles WHERE user_id=? AND is_active=1",
                (uid,)).fetchone():
            db.execute(
                "INSERT INTO employee_profiles(user_id,id_card,contract_no,"
                "contract_start,contract_end,social_no,bank_name,bank_account,"
                "emergency_contact,emergency_phone,hire_date,id_scan_path,"
                "is_active,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
                (uid, f"4401{i:013d}X", f"HT-{s['emp_no']}", hire,
                 f"{int(hire[:4]) + 3}-{(i % 12) + 1:02d}-14", f"4401{s['emp_no']}",
                 "招商银行广州白云支行", f"6214{i:015d}", "家属-" + s["full_name"][-2:],
                 "1380000" + f"{i:04d}", hire, f"scans/{s['emp_no']}.jpg", now(), now()))
            seeded_profiles += 1
        # 异动：入职事件（幂等）
        if not db.execute("SELECT 1 FROM employee_events WHERE user_id=?",
                          (uid,)).fetchone():
            prim = db.execute(
                "SELECT position_id FROM position_assignments "
                "WHERE user_id=? AND is_active=1 AND is_primary=1 LIMIT 1",
                (uid,)).fetchone()
            to_pid = prim["position_id"] if prim else None
            db.execute(
                "INSERT INTO employee_events(user_id,event_type,to_position_id,"
                "event_date,remark,operator,created_at) VALUES(?,?,?,?,?,?,?)",
                (uid, "onboard", to_pid, hire, "系统初始化·演示数据", "seed", now()))
            seeded_events += 1
    db.commit()
    return {"employees_profiles": seeded_profiles,
            "employees_events": seeded_events,
            "employees_staff": len(staff)}
