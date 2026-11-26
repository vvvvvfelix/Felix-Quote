"""模块层 — 岗位职级体系（M002）：岗位 / 职级 / 编制 / 任职 + 编制 vs 在岗看板。

三轴模型的第二轴。组织树（M001）回答「这个部门在哪一层、归哪个公司/工厂/事业部」，
本模块回答「这个部门该设几个岗、每岗几人、现在缺多少人、谁向谁汇报」：

    org_units            组织树（轴1，M001）
    job_titles × job_grades × positions   岗位职级编制（轴2，本模块）
    users                人员（轴3）

一条编制 = 组织 × 岗位 × 职级 × 编制人数 × 汇报上级；
人通过 position_assignments 挂到编制上。于是「编制 vs 在岗」退化成
一个简单的差：headcount − 在职任职人数。缺编说明要招人，超编说明要么
兼岗要么该调岗 —— 这是厂长和人事每天早上要看的第一个数。

【任职关联方案的取舍】
    A. users 加 position_id 外键 —— 改动行数最少，但：
       - 一个人只能占一个岗。兼岗（小厂厂长兼采购经理、会计兼行政）
         在国内制造企业是常态，表达不了就等于数据失真；
       - position_id 会渗进 identity / auth 的 INSERT 与 SELECT 列清单，
         与「不破坏现有模块」的取舍相悖。
    B. 独立任职关系表 position_assignments（本模块采用）——
       - users 一行不动，现有模块零影响；
       - 主岗/兼岗、任职占比、起止日期天然支持；
       - M003「入职/转正/调岗/晋升/离职全留痕」直接复用本表，不必推倒重来。
    代价是算 occupied 要一次 JOIN；几百条编制的规模下这点开销可忽略。

【编制占用口径】
    occupied = 该编制下「在职人员」数（任职关系 is_active=1 且
    人员 is_active=1 且 emp_status ∈ probation/active）。兼岗也计入 ——
    人确实在那个岗位上干活。因此按组织汇总 occupied 时，兼岗者会被
    重复计一次；看板同时给出 M001 口径的 members（按 users.org_unit_id
    统计、天然去重）供交叉核对，两个数的差就是兼岗人数。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_user, hash_password
from ..core import orgutil, permissions as P
from ..core.db import get_conn, jdump, now, release_conn
from ..core.paths import uuid_like
from ..core.services import audit, next_emp_no

router = APIRouter(prefix="/api/position", tags=["position"])

# ==========================================================================
# 一、枚举 —— 前端不得硬编码中文，一律从 /types 取
# ==========================================================================
# 职级序列（架构 3.2）：M 管理 / P 专业 / T 技术 / O 操作
JOB_FAMILIES = {
    "M": "管理序列（M1–M6）",
    "P": "专业序列（P1–P8）",
    "T": "技术序列（T1–T8）",
    "O": "操作序列（O1–O5）",
}
# 各序列的层级上限 —— 新增职级时校验用，防止出现 M9 这种不存在的职级
FAMILY_MAX_LEVEL = {"M": 6, "P": 8, "T": 8, "O": 5}

# 岗位类别
JOB_CATEGORIES = {
    "mgmt": "管理类", "admin": "职能类", "tech": "技术类", "sales": "销售类",
    "prod": "生产类", "op": "操作类", "support": "辅助类",
}

# 编制状态
POS_STATUSES = {
    "open": "待补（招聘中）", "active": "正常在编",
    "frozen": "冻结编制", "closed": "编制撤销",
}

# 任职方式
ASSIGN_MODES = {"1": "主岗", "0": "兼岗"}

# 编制差额状态 —— 看板上三种颜色
HEADCOUNT_STATUS = {"short": "缺编", "full": "满编", "over": "超编"}

# 「按部门」口径：编制沿组织树上溯，归到最近的这类组织
DEPT_TYPES = ("dept", "workshop")

# 看板维度
BOARD_DIMS = {"org": "按组织", "dept": "按部门", "family": "按职级序列",
              "title": "按岗位"}

# 在职口径（与 M001 structure._member_counts 保持一致，避免两个看板人数对不上）
#   ON_JOB_SQL       带 users 表别名 us.（JOIN 场景）
#   ON_JOB_SQL_PLAIN 不带别名（单表 COUNT(*) 场景）
_ON_JOB = "is_active=1 AND IFNULL(emp_status,'active') IN ('probation','active')"
ON_JOB_SQL = "us." + _ON_JOB
ON_JOB_SQL_PLAIN = _ON_JOB


# ==========================================================================
# 二、内部工具
# ==========================================================================
def _occupied(db) -> dict[int, int]:
    """各编制的在岗人数（兼岗重复计）。"""
    rows = db.execute(
        "SELECT pa.position_id pid, COUNT(DISTINCT pa.user_id) c "
        "FROM position_assignments pa JOIN users us ON us.id=pa.user_id "
        f"WHERE pa.is_active=1 AND {ON_JOB_SQL} GROUP BY pa.position_id")
    return {r["pid"]: r["c"] for r in rows}


def _member_counts(db) -> dict[int, int]:
    """各组织在岗人数 —— 口径与 M001 组织树完全一致（按人去重）。"""
    rows = db.execute(
        "SELECT COALESCE(org_unit_id, dept_id) uid, COUNT(*) c FROM users "
        f"WHERE {ON_JOB_SQL_PLAIN} AND COALESCE(org_unit_id, dept_id) IS NOT NULL "
        "GROUP BY uid")
    return {r["uid"]: r["c"] for r in rows}


def _child_counts(db) -> dict[int, int]:
    rows = db.execute(
        "SELECT parent_position_id pid, COUNT(*) c FROM positions "
        "WHERE parent_position_id IS NOT NULL AND is_active=1 GROUP BY pid")
    return {r["pid"]: r["c"] for r in rows}


_POS_SQL = (
    "SELECT p.*, ou.name AS org_name, ou.code AS org_code, "
    "ou.org_type AS org_type, ou.path AS org_path, "
    "jt.name AS job_title_name, jt.code AS job_title_code, "
    "jt.category AS job_category, "
    "jg.code AS grade_code, jg.name AS grade_name, jg.family AS job_family, "
    "jg.level AS grade_level, pp.name AS parent_position_name "
    "FROM positions p "
    "LEFT JOIN org_units ou ON ou.id=p.org_unit_id "
    "LEFT JOIN job_titles jt ON jt.id=p.job_title_id "
    "LEFT JOIN job_grades jg ON jg.id=p.job_grade_id "
    "LEFT JOIN positions pp ON pp.id=p.parent_position_id ")


def _gap_status(gap: float) -> str:
    if gap > 0:
        return "short"
    return "over" if gap < 0 else "full"


def _pos_rows(db, where: str = "1=1", args: list | None = None) -> list[dict]:
    """编制列表（带组织/岗位/职级冗余字段与编制差额）。"""
    rows = [dict(r) for r in db.execute(
        _POS_SQL + f"WHERE {where} ORDER BY ou.sort_order, p.sort_order, p.id",
        args or [])]
    occ, kids = _occupied(db), _child_counts(db)
    for r in rows:
        r["job_family_label"] = JOB_FAMILIES.get(r.get("job_family") or "", "未定序列")
        r["job_category_label"] = JOB_CATEGORIES.get(r.get("job_category") or "", "—")
        r["grade_label"] = r.get("grade_code") or "未定级"
        r["job_title_label"] = r.get("job_title_name") or "未指定岗位"
        r["status_label"] = POS_STATUSES.get(r["status"], r["status"])
        r["occupied"] = occ.get(r["id"], 0)
        r["gap"] = round(float(r["headcount"] or 0) - r["occupied"], 2)
        r["gap_status"] = _gap_status(r["gap"])
        r["gap_status_label"] = HEADCOUNT_STATUS[r["gap_status"]]
        r["child_count"] = kids.get(r["id"], 0)
    return rows


def _title_rows(db, where: str = "1=1", args: list | None = None) -> list[dict]:
    """岗位列表（附带「被多少生效编制引用 / 多少在职人员任职」）。"""
    rows = [dict(r) for r in db.execute(
        "SELECT t.*, "
        "(SELECT COUNT(*) FROM positions p "
        " WHERE p.job_title_id=t.id AND p.is_active=1) AS pos_count, "
        "(SELECT COUNT(DISTINCT pa.user_id) FROM position_assignments pa "
        " JOIN positions p2 ON p2.id=pa.position_id "
        " JOIN users us ON us.id=pa.user_id "
        " WHERE p2.job_title_id=t.id AND pa.is_active=1 AND "
        + ON_JOB_SQL + ") AS holder_count "
        "FROM job_titles t WHERE " + where + " ORDER BY t.sort_order, t.id",
        args or [])]
    for r in rows:
        r["family_label"] = JOB_FAMILIES.get(r["family"], r["family"])
        r["category_label"] = JOB_CATEGORIES.get(r.get("category") or "", "—")
    return rows


def _grade_rows(db, where: str = "1=1", args: list | None = None) -> list[dict]:
    rows = [dict(r) for r in db.execute(
        "SELECT g.*, "
        "(SELECT COUNT(*) FROM positions p "
        " WHERE p.job_grade_id=g.id AND p.is_active=1) AS pos_count, "
        "(SELECT COUNT(DISTINCT pa.user_id) FROM position_assignments pa "
        " JOIN positions p2 ON p2.id=pa.position_id "
        " JOIN users us ON us.id=pa.user_id "
        " WHERE p2.job_grade_id=g.id AND pa.is_active=1 AND "
        + ON_JOB_SQL + ") AS holder_count "
        "FROM job_grades g WHERE " + where + " ORDER BY g.family, g.level, g.id",
        args or [])]
    for r in rows:
        r["family_label"] = JOB_FAMILIES.get(r["family"], r["family"])
    return rows


def _assert_refs(db, org_unit_id: int | None, job_title_id: int | None,
                 job_grade_id: int | None) -> None:
    """引用完整性校验 —— 组织/岗位/职级必须真实存在且启用。

    org_units 由 core/schema 的迁移建，属基础设施数据；这里直接读表，
    不 import modules.structure（modules/ 之间禁止跨模块 import）。
    """
    if org_unit_id is None:
        raise HTTPException(400, "必须指定所属组织")
    if not db.execute("SELECT 1 FROM org_units WHERE id=? AND is_active=1",
                      (org_unit_id,)).fetchone():
        raise HTTPException(400, "所属组织不存在或已停用")
    if job_title_id and not db.execute(
            "SELECT 1 FROM job_titles WHERE id=?", (job_title_id,)).fetchone():
        raise HTTPException(400, "岗位不存在")
    if job_grade_id and not db.execute(
            "SELECT 1 FROM job_grades WHERE id=?", (job_grade_id,)).fetchone():
        raise HTTPException(400, "职级不存在")


def _assert_no_report_cycle(db, pos_id: int | None, parent_id: int | None) -> None:
    """汇报线环检测。

    汇报线成环后，任何「向上找审批人 / 向下找团队」的遍历都会死循环，
    和组织树成环一样必须在写入前挡住。
    """
    if not parent_id:
        return
    if pos_id and parent_id == pos_id:
        raise HTTPException(400, "汇报上级不能是自己")
    seen: set[int] = set()
    cur: int | None = parent_id
    while cur and cur not in seen:
        if pos_id and cur == pos_id:
            raise HTTPException(400, "不能把下级岗位设为汇报上级（汇报线会成环）")
        seen.add(cur)
        row = db.execute("SELECT parent_position_id FROM positions WHERE id=?",
                         (cur,)).fetchone()
        cur = row["parent_position_id"] if row else None


def _auto_name(db, org_unit_id: int, job_title_id: int | None,
               job_grade_id: int | None) -> str:
    """编制默认名：组织 · 岗位 · 职级 —— 人事一眼能看懂。"""
    org = db.execute("SELECT name FROM org_units WHERE id=?", (org_unit_id,)).fetchone()
    title = db.execute("SELECT name FROM job_titles WHERE id=?",
                       (job_title_id,)).fetchone() if job_title_id else None
    grade = db.execute("SELECT code FROM job_grades WHERE id=?",
                       (job_grade_id,)).fetchone() if job_grade_id else None
    parts = [org["name"] if org else "未知组织",
             title["name"] if title else "未指定岗位",
             grade["code"] if grade else "未定级"]
    return " · ".join(parts)


def _next_code(db, org_code: str) -> str:
    """编制编码：POS-<组织编码>-<三位序号>，按组织分段便于人工识别。"""
    prefix = f"POS-{org_code or 'ORG'}-"
    n = db.execute("SELECT COUNT(*) c FROM positions WHERE code LIKE ?",
                   (prefix + "%",)).fetchone()["c"]
    for i in range(1, 1000):
        code = f"{prefix}{i:03d}"
        if not db.execute("SELECT 1 FROM positions WHERE code=?", (code,)).fetchone():
            return code
    return f"{prefix}{int(n) + 1:03d}"


def _dept_of(org_id: int | None, orgs: dict) -> int | None:
    """沿组织树上溯找最近的部门 / 车间 —— 「按部门」看板的归集口径。"""
    cur, seen = org_id, set()
    while cur and cur in orgs and cur not in seen:
        if orgs[cur].get("org_type") in DEPT_TYPES:
            return cur
        seen.add(cur)
        cur = orgs[cur].get("parent_id")
    return org_id


def _self_and_ancestors(org_id: int | None, orgs: dict) -> list[int]:
    """自己 + 全部祖先（用于 scope=subtree 时把编制逐层归集上去）。"""
    out, cur, seen = [], org_id, set()
    while cur and cur in orgs and cur not in seen:
        out.append(cur)
        seen.add(cur)
        cur = orgs[cur].get("parent_id")
    if org_id and org_id not in out:
        out.append(org_id)
    return out


def _subtree_member_total(org_id: int, orgs: dict, counts: dict[int, int]) -> int:
    """组织含下级的在岗人数（与 M001 树节点 total_count 同口径）。"""
    ids = set(orgutil.descendants(org_id, orgs)) | {org_id}
    return sum(counts.get(i, 0) for i in ids)


# ==========================================================================
# 三、枚举与只读参考数据
# ==========================================================================
@router.get("/types")
def list_types(u: dict = Depends(current_user)):
    """一次性下发全部枚举 —— 前端零硬编码中文。"""
    P.require(u, "position.view")
    return {
        "families": [{"value": k, "label": v} for k, v in JOB_FAMILIES.items()],
        "family_max_level": FAMILY_MAX_LEVEL,
        "categories": [{"value": k, "label": v} for k, v in JOB_CATEGORIES.items()],
        "statuses": [{"value": k, "label": v} for k, v in POS_STATUSES.items()],
        "assign_modes": [{"value": k, "label": v} for k, v in ASSIGN_MODES.items()],
        "gap_statuses": [{"value": k, "label": v} for k, v in HEADCOUNT_STATUS.items()],
        "board_dims": [{"value": k, "label": v} for k, v in BOARD_DIMS.items()],
    }


@router.get("/org-options")
def org_options(u: dict = Depends(current_user), active: int = 1):
    """组织下拉 —— 只读 org_units 表（不 import structure 模块）。"""
    P.require(u, "position.view")
    db = get_conn()
    where = "is_active=1" if active else "1=1"
    rows = [dict(r) for r in db.execute(
        "SELECT id,code,name,org_type,parent_id FROM org_units "
        f"WHERE {where} ORDER BY sort_order,id")]
    release_conn()
    return rows


@router.get("/candidates")
def candidates(u: dict = Depends(current_user), keyword: str | None = None):
    """在职人员候选（任职下拉用）。

    只返回在职/试用期人员 —— 给离职人员派活是人事系统最典型的脏数据。
    """
    P.require(u, "position.view")
    db = get_conn()
    where, args = [ON_JOB_SQL], []
    if keyword:
        where.append("(us.full_name LIKE ? OR us.username LIKE ? OR "
                     "IFNULL(us.emp_no,'') LIKE ?)")
        args += [f"%{keyword}%"] * 3
    rows = [dict(r) for r in db.execute(
        "SELECT us.id, us.username, us.full_name, us.emp_no, us.emp_status, "
        "COALESCE(us.org_unit_id, us.dept_id) AS org_unit_id, ou.name AS org_name "
        "FROM users us LEFT JOIN org_units ou "
        "ON ou.id=COALESCE(us.org_unit_id, us.dept_id) "
        "WHERE " + " AND ".join(where) + " ORDER BY us.id", args)]
    release_conn()
    return rows


# ==========================================================================
# 四、岗位 job_titles
# ==========================================================================
class TitleIn(BaseModel):
    code: str
    name: str
    name_en: str | None = None
    family: str = "P"
    category: str | None = None
    level_hint: int = 0
    duty: str | None = None
    requires: list[str] | None = None
    sort_order: int = 0
    remark: str | None = None
    is_active: int = 1


@router.get("/titles")
def list_titles(u: dict = Depends(current_user), family: str | None = None,
                category: str | None = None, keyword: str | None = None,
                active: int | None = 1):
    P.require(u, "position.view")
    where, args = ["1=1"], []
    if family:
        where.append("t.family=?")
        args.append(family)
    if category:
        where.append("t.category=?")
        args.append(category)
    if keyword:
        where.append("(t.name LIKE ? OR t.code LIKE ? OR IFNULL(t.name_en,'') LIKE ?)")
        args += [f"%{keyword}%"] * 3
    if active is not None and int(active) >= 0:
        where.append("t.is_active=?")
        args.append(int(active))
    db = get_conn()
    rows = _title_rows(db, " AND ".join(where), args)
    release_conn()
    return rows


@router.post("/titles")
def create_title(body: TitleIn, u: dict = Depends(current_user)):
    P.require(u, "position.edit")
    if body.family not in JOB_FAMILIES:
        raise HTTPException(400, f"未知职级序列 {body.family}")
    if body.category and body.category not in JOB_CATEGORIES:
        raise HTTPException(400, f"未知岗位类别 {body.category}")
    db = get_conn()
    if db.execute("SELECT 1 FROM job_titles WHERE code=?", (body.code,)).fetchone():
        release_conn()
        raise HTTPException(400, "岗位编码已存在")
    cur = db.execute(
        "INSERT INTO job_titles(code,name,name_en,family,category,level_hint,"
        "duty,require_json,sort_order,remark,is_active,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (body.code, body.name, body.name_en, body.family, body.category,
         body.level_hint, body.duty, jdump(body.requires or []),
         body.sort_order, body.remark, body.is_active, now(), now()))
    db.commit()
    tid = cur.lastrowid
    release_conn()
    audit(u, "position", "create", "job_title", tid, body.name,
          after=body.model_dump())
    return {"id": tid}


@router.put("/titles/{tid}")
def update_title(tid: int, body: TitleIn, u: dict = Depends(current_user)):
    P.require(u, "position.edit")
    if body.family not in JOB_FAMILIES:
        raise HTTPException(400, f"未知职级序列 {body.family}")
    if body.category and body.category not in JOB_CATEGORIES:
        raise HTTPException(400, f"未知岗位类别 {body.category}")
    db = get_conn()
    before = db.execute("SELECT * FROM job_titles WHERE id=?", (tid,)).fetchone()
    if not before:
        release_conn()
        raise HTTPException(404, "岗位不存在")
    if db.execute("SELECT 1 FROM job_titles WHERE code=? AND id<>?",
                  (body.code, tid)).fetchone():
        release_conn()
        raise HTTPException(400, "岗位编码已被占用")
    db.execute(
        "UPDATE job_titles SET code=?,name=?,name_en=?,family=?,category=?,"
        "level_hint=?,duty=?,require_json=?,sort_order=?,remark=?,is_active=?,"
        "updated_at=? WHERE id=?",
        (body.code, body.name, body.name_en, body.family, body.category,
         body.level_hint, body.duty, jdump(body.requires or []),
         body.sort_order, body.remark, body.is_active, now(), tid))
    db.commit()
    release_conn()
    audit(u, "position", "update", "job_title", tid, body.name,
          before=dict(before), after=body.model_dump())
    return {"ok": True}


@router.delete("/titles/{tid}")
def disable_title(tid: int, u: dict = Depends(current_user)):
    """停用岗位 —— 不物理删（历史编制与任职要能追溯）。

    被生效编制引用且有在职人员时拒绝停用，并明确告诉操作人牵涉多少人，
    而不是丢一句「有关联数据」让人猜。
    """
    P.require(u, "position.edit")
    db = get_conn()
    row = db.execute("SELECT * FROM job_titles WHERE id=?", (tid,)).fetchone()
    if not row:
        release_conn()
        raise HTTPException(404, "岗位不存在")
    pos = db.execute("SELECT COUNT(*) c FROM positions WHERE job_title_id=? "
                     "AND is_active=1", (tid,)).fetchone()["c"]
    holders = db.execute(
        "SELECT COUNT(DISTINCT pa.user_id) c FROM position_assignments pa "
        "JOIN positions p ON p.id=pa.position_id JOIN users us ON us.id=pa.user_id "
        f"WHERE p.job_title_id=? AND pa.is_active=1 AND {ON_JOB_SQL}", (tid,)
    ).fetchone()["c"]
    if pos or holders:
        release_conn()
        raise HTTPException(400, f"该岗位被 {pos} 个生效编制引用，共 {holders} 名"
                                 "在职人员任职，请先调整编制或任职关系再停用")
    db.execute("UPDATE job_titles SET is_active=0,updated_at=? WHERE id=?",
               (now(), tid))
    db.commit()
    release_conn()
    audit(u, "position", "disable", "job_title", tid, row["name"], before=dict(row))
    return {"ok": True}


# ==========================================================================
# 五、职级 job_grades
# ==========================================================================
class GradeIn(BaseModel):
    code: str
    family: str = "P"
    level: int = 1
    name: str
    name_en: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    currency: str = "CNY"
    duty: str | None = None
    sort_order: int = 0
    remark: str | None = None
    is_active: int = 1


@router.get("/grades")
def list_grades(u: dict = Depends(current_user), family: str | None = None,
                active: int | None = 1):
    P.require(u, "position.view")
    where, args = ["1=1"], []
    if family:
        where.append("g.family=?")
        args.append(family)
    if active is not None and int(active) >= 0:
        where.append("g.is_active=?")
        args.append(int(active))
    db = get_conn()
    rows = _grade_rows(db, " AND ".join(where), args)
    release_conn()
    return rows


def _check_grade(db, body: "GradeIn", gid: int | None = None) -> None:
    if body.family not in JOB_FAMILIES:
        raise HTTPException(400, f"未知职级序列 {body.family}")
    mx = FAMILY_MAX_LEVEL.get(body.family, 8)
    if body.level < 1 or body.level > mx:
        raise HTTPException(400, f"{body.family} 序列只有 1–{mx} 级，"
                                 f"不允许 {body.level} 级")
    if body.salary_min and body.salary_max and body.salary_min > body.salary_max:
        raise HTTPException(400, "薪资带宽下限不能大于上限")
    if body.code:
        q = "SELECT 1 FROM job_grades WHERE code=?"
        a: list = [body.code]
        if gid:
            q += " AND id<>?"
            a.append(gid)
        if db.execute(q, a).fetchone():
            raise HTTPException(400, "职级编码已被占用")


@router.post("/grades")
def create_grade(body: GradeIn, u: dict = Depends(current_user)):
    P.require(u, "position.edit")
    db = get_conn()
    _check_grade(db, body)
    cur = db.execute(
        "INSERT INTO job_grades(code,family,level,name,name_en,salary_min,"
        "salary_max,currency,duty,sort_order,remark,is_active,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (body.code, body.family, body.level, body.name, body.name_en,
         body.salary_min, body.salary_max, body.currency, body.duty,
         body.sort_order, body.remark, body.is_active, now(), now()))
    db.commit()
    gid = cur.lastrowid
    release_conn()
    audit(u, "position", "create", "job_grade", gid, body.code,
          after=body.model_dump())
    return {"id": gid}


@router.put("/grades/{gid}")
def update_grade(gid: int, body: GradeIn, u: dict = Depends(current_user)):
    P.require(u, "position.edit")
    db = get_conn()
    before = db.execute("SELECT * FROM job_grades WHERE id=?", (gid,)).fetchone()
    if not before:
        release_conn()
        raise HTTPException(404, "职级不存在")
    _check_grade(db, body, gid)
    db.execute(
        "UPDATE job_grades SET code=?,family=?,level=?,name=?,name_en=?,"
        "salary_min=?,salary_max=?,currency=?,duty=?,sort_order=?,remark=?,"
        "is_active=?,updated_at=? WHERE id=?",
        (body.code, body.family, body.level, body.name, body.name_en,
         body.salary_min, body.salary_max, body.currency, body.duty,
         body.sort_order, body.remark, body.is_active, now(), gid))
    db.commit()
    release_conn()
    audit(u, "position", "update", "job_grade", gid, body.code,
          before=dict(before), after=body.model_dump())
    return {"ok": True}


@router.delete("/grades/{gid}")
def disable_grade(gid: int, u: dict = Depends(current_user)):
    """停用职级 —— 被生效编制引用或有在职人员时拒绝。"""
    P.require(u, "position.edit")
    db = get_conn()
    row = db.execute("SELECT * FROM job_grades WHERE id=?", (gid,)).fetchone()
    if not row:
        release_conn()
        raise HTTPException(404, "职级不存在")
    pos = db.execute("SELECT COUNT(*) c FROM positions WHERE job_grade_id=? "
                     "AND is_active=1", (gid,)).fetchone()["c"]
    holders = db.execute(
        "SELECT COUNT(DISTINCT pa.user_id) c FROM position_assignments pa "
        "JOIN positions p ON p.id=pa.position_id JOIN users us ON us.id=pa.user_id "
        f"WHERE p.job_grade_id=? AND pa.is_active=1 AND {ON_JOB_SQL}", (gid,)
    ).fetchone()["c"]
    if pos or holders:
        release_conn()
        raise HTTPException(400, f"该职级被 {pos} 个生效编制引用，共 {holders} 名"
                                 "在职人员任职，请先调整编制或任职关系再停用")
    db.execute("UPDATE job_grades SET is_active=0,updated_at=? WHERE id=?",
               (now(), gid))
    db.commit()
    release_conn()
    audit(u, "position", "disable", "job_grade", gid, row["code"], before=dict(row))
    return {"ok": True}


# ==========================================================================
# 六、编制 positions
# ==========================================================================
class PositionIn(BaseModel):
    code: str | None = None
    name: str | None = None
    org_unit_id: int
    job_title_id: int | None = None
    job_grade_id: int | None = None
    headcount: float = 1
    parent_position_id: int | None = None
    status: str = "active"
    is_key: int = 0
    work_place: str | None = None
    duty: str | None = None
    requires: list[str] | None = None
    sort_order: int = 0
    remark: str | None = None
    is_active: int = 1


@router.get("/positions")
def list_positions(u: dict = Depends(current_user),
                   org_unit_id: int | None = None,
                   job_title_id: int | None = None,
                   job_grade_id: int | None = None,
                   job_family: str | None = None,
                   status: str | None = None,
                   gap_status: str | None = None,
                   keyword: str | None = None,
                   active: int | None = 1,
                   subtree: int = 0):
    """编制列表（带筛选）。

    subtree=1 且给了 org_unit_id 时，按 path 带上全部下级组织的编制 ——
    「一厂缺多少人」问的是整个厂，不是厂部机关那几个人。
    """
    P.require(u, "position.view")
    if active is None or int(active) < 0:
        where, args = ["1=1"], []
    else:
        where, args = ["p.is_active=?"], [int(active)]
    if org_unit_id is not None:
        orgs = orgutil.load_orgs("org_units")
        ids = (set(orgutil.descendants(int(org_unit_id), orgs)) | {int(org_unit_id)}
               if int(subtree) else {int(org_unit_id)})
        where.append(f"p.org_unit_id IN ({','.join('?' * len(ids))})")
        args += sorted(ids)
    for col, val in (("p.job_title_id", job_title_id),
                     ("p.job_grade_id", job_grade_id)):
        if val is not None:
            where.append(f"{col}=?")
            args.append(val)
    if job_family:
        where.append("jg.family=?")
        args.append(job_family)
    if status:
        where.append("p.status=?")
        args.append(status)
    if keyword:
        where.append("(p.name LIKE ? OR p.code LIKE ? OR IFNULL(ou.name,'') LIKE ? "
                     "OR IFNULL(jt.name,'') LIKE ?)")
        args += [f"%{keyword}%"] * 4
    db = get_conn()
    rows = _pos_rows(db, " AND ".join(where), args)
    release_conn()
    if gap_status:
        rows = [r for r in rows if r["gap_status"] == gap_status]
    return rows


@router.get("/positions/{pid}")
def get_position(pid: int, u: dict = Depends(current_user)):
    P.require(u, "position.view")
    db = get_conn()
    rows = _pos_rows(db, "p.id=?", [pid])
    if not rows:
        release_conn()
        raise HTTPException(404, "编制不存在")
    row = rows[0]
    incumbents = [dict(r) for r in db.execute(
        "SELECT pa.id, pa.user_id, pa.is_primary, pa.ratio, pa.start_date, "
        "us.username, us.full_name, us.emp_no, us.emp_status, ou.name AS org_name "
        "FROM position_assignments pa JOIN users us ON us.id=pa.user_id "
        "LEFT JOIN org_units ou ON ou.id=COALESCE(us.org_unit_id, us.dept_id) "
        "WHERE pa.position_id=? AND pa.is_active=1 "
        "ORDER BY pa.is_primary DESC, pa.id", (pid,))]
    for r in incumbents:
        r["is_primary_label"] = ASSIGN_MODES.get(str(int(r["is_primary"])), "兼岗")
    children = _pos_rows(db, "p.parent_position_id=? AND p.is_active=1", [pid])
    # 汇报链：逐级向上取到顶层；seen 兜底，历史脏数据成环也不会死循环
    chain, cur, seen = [], row.get("parent_position_id"), set()
    while cur and cur not in seen:
        seen.add(cur)
        p = db.execute("SELECT id,code,name,parent_position_id,org_unit_id "
                       "FROM positions WHERE id=?", (cur,)).fetchone()
        if not p:
            break
        p = dict(p)
        holder = db.execute(
            "SELECT us.full_name, us.username FROM position_assignments pa "
            f"JOIN users us ON us.id=pa.user_id WHERE pa.position_id=? "
            f"AND pa.is_active=1 AND pa.is_primary=1 AND {ON_JOB_SQL} LIMIT 1",
            (cur,)).fetchone()
        p["holder_name"] = (holder["full_name"] or holder["username"]) if holder else None
        chain.append(p)
        cur = p.get("parent_position_id")
    orgs = orgutil.load_orgs("org_units")
    release_conn()
    row["incumbents"] = incumbents
    row["children"] = children
    row["report_chain"] = chain
    row["org_chain"] = [{"id": o["id"], "name": o["name"]}
                        for o in orgutil.chain(row["org_unit_id"], orgs)]
    return row


@router.post("/positions")
def create_position(body: PositionIn, u: dict = Depends(current_user)):
    P.require(u, "position.edit")
    if body.status not in POS_STATUSES:
        raise HTTPException(400, f"未知编制状态 {body.status}")
    if body.headcount is None or float(body.headcount) <= 0:
        raise HTTPException(400, "编制人数必须大于 0")
    db = get_conn()
    _assert_refs(db, body.org_unit_id, body.job_title_id, body.job_grade_id)
    if body.parent_position_id and not db.execute(
            "SELECT 1 FROM positions WHERE id=?", (body.parent_position_id,)).fetchone():
        release_conn()
        raise HTTPException(400, "汇报上级编制不存在")
    _assert_no_report_cycle(db, None, body.parent_position_id)
    org = db.execute("SELECT code FROM org_units WHERE id=?",
                     (body.org_unit_id,)).fetchone()
    code = (body.code or "").strip() or _next_code(db, org["code"] if org else "")
    if db.execute("SELECT 1 FROM positions WHERE code=?", (code,)).fetchone():
        release_conn()
        raise HTTPException(400, "编制编码已存在")
    name = (body.name or "").strip() or _auto_name(
        db, body.org_unit_id, body.job_title_id, body.job_grade_id)
    cur = db.execute(
        "INSERT INTO positions(code,name,org_unit_id,job_title_id,job_grade_id,"
        "headcount,parent_position_id,status,is_key,work_place,duty,require_json,"
        "sort_order,remark,is_active,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (code, name, body.org_unit_id, body.job_title_id, body.job_grade_id,
         float(body.headcount), body.parent_position_id, body.status,
         body.is_key, body.work_place, body.duty, jdump(body.requires or []),
         body.sort_order, body.remark, body.is_active, now(), now()))
    db.commit()
    pid = cur.lastrowid
    release_conn()
    audit(u, "position", "create", "position", pid, name, after=body.model_dump())
    return {"id": pid, "code": code, "name": name}


@router.put("/positions/{pid}")
def update_position(pid: int, body: PositionIn, u: dict = Depends(current_user)):
    P.require(u, "position.edit")
    if body.status not in POS_STATUSES:
        raise HTTPException(400, f"未知编制状态 {body.status}")
    if body.headcount is None or float(body.headcount) <= 0:
        raise HTTPException(400, "编制人数必须大于 0")
    db = get_conn()
    before = db.execute("SELECT * FROM positions WHERE id=?", (pid,)).fetchone()
    if not before:
        release_conn()
        raise HTTPException(404, "编制不存在")
    _assert_refs(db, body.org_unit_id, body.job_title_id, body.job_grade_id)
    if body.parent_position_id and not db.execute(
            "SELECT 1 FROM positions WHERE id=?", (body.parent_position_id,)).fetchone():
        release_conn()
        raise HTTPException(400, "汇报上级编制不存在")
    _assert_no_report_cycle(db, pid, body.parent_position_id)
    code = (body.code or "").strip() or before["code"]
    if db.execute("SELECT 1 FROM positions WHERE code=? AND id<>?",
                  (code, pid)).fetchone():
        release_conn()
        raise HTTPException(400, "编制编码已被占用")
    name = (body.name or "").strip() or _auto_name(
        db, body.org_unit_id, body.job_title_id, body.job_grade_id)
    db.execute(
        "UPDATE positions SET code=?,name=?,org_unit_id=?,job_title_id=?,"
        "job_grade_id=?,headcount=?,parent_position_id=?,status=?,is_key=?,"
        "work_place=?,duty=?,require_json=?,sort_order=?,remark=?,is_active=?,"
        "updated_at=? WHERE id=?",
        (code, name, body.org_unit_id, body.job_title_id, body.job_grade_id,
         float(body.headcount), body.parent_position_id, body.status,
         body.is_key, body.work_place, body.duty, jdump(body.requires or []),
         body.sort_order, body.remark, body.is_active, now(), pid))
    db.commit()
    release_conn()
    audit(u, "position", "update", "position", pid, name,
          before=dict(before), after=body.model_dump())
    return {"ok": True, "code": code, "name": name}


@router.delete("/positions/{pid}")
def disable_position(pid: int, u: dict = Depends(current_user)):
    """停用编制 —— 不物理删。

    两道闸：
    1. 还有下级汇报线 → 先调整（否则下级编制变成孤儿，汇报链断掉）；
    2. 还有在职任职人员 → 先转岗（否则人凭空消失在编制表外）。
    """
    P.require(u, "position.edit")
    db = get_conn()
    row = db.execute("SELECT * FROM positions WHERE id=?", (pid,)).fetchone()
    if not row:
        release_conn()
        raise HTTPException(404, "编制不存在")
    kids = db.execute("SELECT COUNT(*) c FROM positions WHERE "
                      "parent_position_id=? AND is_active=1", (pid,)).fetchone()["c"]
    if kids:
        release_conn()
        raise HTTPException(400, f"还有 {kids} 个下级岗位向它汇报，"
                                 "请先调整汇报线")
    holders = _occupied(db).get(pid, 0)
    if holders:
        release_conn()
        raise HTTPException(400, f"该编制下还有 {holders} 名在职人员，请先转岗")
    db.execute("UPDATE positions SET is_active=0,status='closed',updated_at=? "
               "WHERE id=?", (now(), pid))
    db.execute("UPDATE position_assignments SET is_active=0,end_date=?,"
               "updated_at=? WHERE position_id=? AND is_active=1",
               (now()[:10], now(), pid))
    db.commit()
    release_conn()
    audit(u, "position", "disable", "position", pid, row["name"], before=dict(row))
    return {"ok": True}


# ==========================================================================
# 七、任职关系 position_assignments
# ==========================================================================
class AssignIn(BaseModel):
    position_id: int
    user_id: int
    is_primary: int = 1
    ratio: float = 1.0
    start_date: str | None = None
    remark: str | None = None


@router.get("/assignments")
def list_assignments(u: dict = Depends(current_user),
                     position_id: int | None = None,
                     user_id: int | None = None,
                     active: int | None = 1):
    P.require(u, "position.view")
    where, args = ["1=1"], []
    if position_id:
        where.append("pa.position_id=?")
        args.append(position_id)
    if user_id:
        where.append("pa.user_id=?")
        args.append(user_id)
    if active is not None and int(active) >= 0:
        where.append("pa.is_active=?")
        args.append(int(active))
    db = get_conn()
    rows = [dict(r) for r in db.execute(
        "SELECT pa.*, us.username, us.full_name, us.emp_no, us.emp_status, "
        "p.name AS position_name, p.code AS position_code, "
        "ou.name AS org_name, jt.name AS job_title_name, jg.code AS grade_code "
        "FROM position_assignments pa "
        "JOIN users us ON us.id=pa.user_id "
        "JOIN positions p ON p.id=pa.position_id "
        "LEFT JOIN org_units ou ON ou.id=p.org_unit_id "
        "LEFT JOIN job_titles jt ON jt.id=p.job_title_id "
        "LEFT JOIN job_grades jg ON jg.id=p.job_grade_id "
        "WHERE " + " AND ".join(where) + " ORDER BY pa.is_active DESC, pa.id DESC "
        "LIMIT 500", args)]
    release_conn()
    for r in rows:
        r["is_primary_label"] = ASSIGN_MODES.get(str(int(r["is_primary"])), "兼岗")
    return rows


@router.post("/assignments")
def create_assignment(body: AssignIn, u: dict = Depends(current_user)):
    """任职登记。一个人只能有一个主岗 —— 新设主岗时旧主岗自动降为兼岗。"""
    P.require(u, "position.edit")
    db = get_conn()
    pos = db.execute("SELECT * FROM positions WHERE id=?",
                     (body.position_id,)).fetchone()
    if not pos:
        release_conn()
        raise HTTPException(404, "编制不存在")
    if not pos["is_active"]:
        release_conn()
        raise HTTPException(400, "该编制已停用，无法安排任职")
    usr = db.execute("SELECT id,username,full_name,is_active,emp_status "
                     "FROM users WHERE id=?", (body.user_id,)).fetchone()
    if not usr:
        release_conn()
        raise HTTPException(404, "人员不存在")
    if not usr["is_active"] or usr["emp_status"] not in ("probation", "active"):
        release_conn()
        raise HTTPException(400, "只能给在职人员安排任职（离职/停职不可）")
    if db.execute("SELECT 1 FROM position_assignments WHERE position_id=? "
                  "AND user_id=? AND is_active=1",
                  (body.position_id, body.user_id)).fetchone():
        release_conn()
        raise HTTPException(400, "该人员已在此编制上任职")
    if body.is_primary:
        # 主岗唯一：旧主岗降为兼岗，避免「两个主岗」导致编制口径重复
        db.execute("UPDATE position_assignments SET is_primary=0,updated_at=? "
                   "WHERE user_id=? AND is_active=1 AND is_primary=1",
                   (now(), body.user_id))
    cur = db.execute(
        "INSERT INTO position_assignments(position_id,user_id,is_primary,ratio,"
        "start_date,end_date,remark,is_active,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,1,?,?)",
        (body.position_id, body.user_id, 1 if body.is_primary else 0,
         float(body.ratio or 1.0), body.start_date or now()[:10], None,
         body.remark, now(), now()))
    db.commit()
    aid = cur.lastrowid
    release_conn()
    audit(u, "position", "assign", "position_assignment", aid,
          f"{usr['full_name'] or usr['username']} → {pos['name']}",
          after=body.model_dump())
    return {"id": aid}


@router.delete("/assignments/{aid}")
def end_assignment(aid: int, u: dict = Depends(current_user)):
    """结束任职（软删，留历史）—— 调岗/离职都走这里。"""
    P.require(u, "position.edit")
    db = get_conn()
    row = db.execute("SELECT * FROM position_assignments WHERE id=?", (aid,)).fetchone()
    if not row:
        release_conn()
        raise HTTPException(404, "任职记录不存在")
    db.execute("UPDATE position_assignments SET is_active=0,end_date=?,updated_at=? "
               "WHERE id=?", (now()[:10], now(), aid))
    db.commit()
    release_conn()
    audit(u, "position", "unassign", "position_assignment", aid,
          f"position#{row['position_id']} / user#{row['user_id']}",
          before=dict(row))
    return {"ok": True}


# ==========================================================================
# 八、统计与编制 vs 在岗看板
# ==========================================================================
@router.get("/stats")
def stats(u: dict = Depends(current_user)):
    """统计卡：总编制 / 在岗 / 缺编 / 超编 + 序列与状态分布。"""
    P.require(u, "position.view")
    db = get_conn()
    rows = _pos_rows(db, "p.is_active=1")
    titles = db.execute("SELECT COUNT(*) c FROM job_titles WHERE is_active=1"
                        ).fetchone()["c"]
    grades = db.execute("SELECT COUNT(*) c FROM job_grades WHERE is_active=1"
                        ).fetchone()["c"]
    members = db.execute(f"SELECT COUNT(*) c FROM users WHERE {ON_JOB_SQL_PLAIN}"
                         ).fetchone()["c"]
    assigned = db.execute(
        "SELECT COUNT(DISTINCT pa.user_id) c FROM position_assignments pa "
        f"JOIN users us ON us.id=pa.user_id WHERE pa.is_active=1 AND {ON_JOB_SQL}"
    ).fetchone()["c"]
    release_conn()

    headcount = round(sum(float(r["headcount"] or 0) for r in rows), 2)
    occupied = sum(int(r["occupied"]) for r in rows)
    short = round(sum(r["gap"] for r in rows if r["gap"] > 0), 2)
    over = round(sum(-r["gap"] for r in rows if r["gap"] < 0), 2)
    by_family = {}
    by_status = {}
    for r in rows:
        f = r.get("job_family") or "-"
        b = by_family.setdefault(f, {"value": f,
                                     "label": JOB_FAMILIES.get(f, "未定序列"),
                                     "headcount": 0.0, "occupied": 0,
                                     "positions": 0, "gap": 0.0})
        b["headcount"] += float(r["headcount"] or 0)
        b["occupied"] += int(r["occupied"])
        b["positions"] += 1
        b["gap"] = round(b["headcount"] - b["occupied"], 2)
        s = by_status.setdefault(r["status"], {"value": r["status"],
                                               "label": POS_STATUSES.get(
                                                   r["status"], r["status"]),
                                               "count": 0})
        s["count"] += 1
    return {
        "positions": len(rows),
        "job_titles": titles,
        "job_grades": grades,
        "headcount": headcount,
        "occupied": occupied,
        "gap": round(headcount - occupied, 2),
        "short": short,
        "over": over,
        "full": sum(1 for r in rows if r["gap"] == 0),
        "short_positions": sum(1 for r in rows if r["gap"] > 0),
        "over_positions": sum(1 for r in rows if r["gap"] < 0),
        "members": members,             # M001 口径：在职总人数（按人去重）
        "assigned": assigned,           # 已落编制的人数
        "unassigned": members - assigned,   # 在职但没编制归属 —— 人事要处理的挂空人员
        "by_family": sorted(by_family.values(), key=lambda x: (-x["headcount"],
                                                               x["value"])),
        "by_status": [by_status.get(k, {"value": k, "label": v, "count": 0})
                      for k, v in POS_STATUSES.items()],
    }


@router.get("/board")
def board(u: dict = Depends(current_user), dim: str = "org",
          scope: str = "self", org_unit_id: int | None = None,
          job_family: str | None = None, status: str | None = None,
          keyword: str | None = None):
    """编制 vs 在岗看板。

    dim   —— org 按组织 / dept 按部门（沿树上溯到最近的部门·车间）/
             family 按职级序列 / title 按岗位
    scope —— self 只算该组织直属编制；subtree 含全部下级组织（仅 dim=org 生效）

    口径说明（看板上直接写出来，避免「两个数对不上」的扯皮）：
    - headcount 编制人数，occupied 该维度下各编制在职人数之和（兼岗重复计）
    - members 是该组织的在职总人数（M001 口径，按人去重）
      occupied 与 members 的差 ≈ 兼岗人次 + 跨组织任职
    """
    P.require(u, "position.view")
    if dim not in BOARD_DIMS:
        raise HTTPException(400, f"未知看板维度 {dim}")
    if scope not in ("self", "subtree", "company", "factory", "bu"):
        raise HTTPException(400, "scope 只能是 self / subtree / company / factory / bu")
    db = get_conn()
    rows = _pos_rows(db, "p.is_active=1")
    orgs = orgutil.load_orgs("org_units")
    counts = _member_counts(db)
    release_conn()

    # ---- 范围过滤 ----
    if scope in ("company", "factory", "bu"):
        # M004 三轴范围：圈定「当前组织节点所在轴根」的整棵子树（或指定 org_unit_id）
        # —— 厂长在 A 厂时只看 A 厂编制；切到 B 厂只看 B 厂；总经理看整个公司。
        # 显式 org_unit_id 优先（管理员也能定点查看某厂）；无显式节点且 scope=all
        # 时不过滤（否则管理员会因无轴锚点裸奔到 0 条）。
        if org_unit_id is not None:
            ids = set(P._axis_subtree_ids(int(org_unit_id)))
        elif P.scope_of(u) == "all":
            ids = None
        else:
            root = P.current_org_rollups(u).get(f"{scope}_id")
            ids = set(P._axis_subtree_ids(root))
        if ids is not None:
            rows = [r for r in rows if r["org_unit_id"] in ids]
    elif org_unit_id is not None:
        oid = int(org_unit_id)
        # scope 只对 dim=org 有意义：self = 只看该组织直属编制；
        # 其余维度与 subtree 一律先把范围圈到整棵子树，再各自归集
        if dim == "org" and scope == "self":
            rows = [r for r in rows if r["org_unit_id"] == oid]
        else:
            ids = set(orgutil.descendants(oid, orgs)) | {oid}
            rows = [r for r in rows if r["org_unit_id"] in ids]
    if job_family:
        rows = [r for r in rows if (r.get("job_family") or "-") == job_family]
    if status:
        rows = [r for r in rows if r["status"] == status]
    if keyword:
        rows = [r for r in rows
                if keyword in (r.get("name") or "") or keyword in (r.get("code") or "")]

    buckets: dict = {}

    def _bucket(key, code, name) -> dict:
        if key not in buckets:
            buckets[key] = {"key": key, "code": code, "name": name,
                            "headcount": 0.0, "occupied": 0, "positions": 0,
                            "members": 0}
        return buckets[key]

    for r in rows:
        hc = float(r["headcount"] or 0)
        occ = int(r["occupied"] or 0)
        if dim == "family":
            fam = r.get("job_family") or "-"
            keys = [(fam, fam, JOB_FAMILIES.get(fam, "未定序列"))]
        elif dim == "title":
            keys = [(r.get("job_title_id"), r.get("job_title_code") or "-",
                     r.get("job_title_name") or "未指定岗位")]
        elif dim == "dept":
            oid = _dept_of(r["org_unit_id"], orgs)
            o = orgs.get(oid) or {}
            keys = [(oid, o.get("code") or "-", o.get("name") or "未归属")]
        else:
            oids = (_self_and_ancestors(r["org_unit_id"], orgs) if scope == "subtree"
                    else [r["org_unit_id"]])
            keys = []
            for oid in oids:
                o = orgs.get(oid) or {}
                keys.append((oid, o.get("code") or "-", o.get("name") or "未归属"))
        for k, code, name in keys:
            b = _bucket(k, code, name)
            b["headcount"] += hc
            b["occupied"] += occ
            b["positions"] += 1

    out = []
    for b in buckets.values():
        b["headcount"] = round(b["headcount"], 2)
        b["gap"] = round(b["headcount"] - b["occupied"], 2)
        b["gap_status"] = _gap_status(b["gap"])
        b["gap_status_label"] = HEADCOUNT_STATUS[b["gap_status"]]
        if dim in ("org", "dept") and isinstance(b["key"], int):
            b["members"] = (_subtree_member_total(b["key"], orgs, counts)
                            if scope == "subtree" and dim == "org"
                            else counts.get(b["key"], 0))
        else:
            b["members"] = None
        out.append(b)
    out.sort(key=lambda x: (-x["gap"], -x["headcount"], str(x["code"])))

    # 合计**按编制去重地直接统计**，而不是把各行相加：scope=subtree 时一条编制
    # 会被记进它自己和每一级祖先，行求和会把同一批人算好几遍 —— 合计必须说真话。
    total = {
        "headcount": round(sum(float(r["headcount"] or 0) for r in rows), 2),
        "occupied": sum(int(r["occupied"] or 0) for r in rows),
        "positions": len(rows),
    }
    total["gap"] = round(total["headcount"] - total["occupied"], 2)
    return {
        "dim": dim, "scope": scope,
        "dim_label": BOARD_DIMS[dim],
        "rows": out, "total": total,
        "note": "编制=headcount，在岗=各编制在职任职人数之和（兼岗重复计）；"
                "members 为该组织在职总人数（M001 口径，按人去重），两者之差即兼岗人次。",
    }


# ==========================================================================
# 九、种子：岗位 / 职级 / 编制 / 演示任职（幂等，按 code upsert）
# ==========================================================================
# 职级：(code, 序列, 层级, 名称, 英文名, 薪资下限, 薪资上限)
SEED_GRADES: list[tuple] = [
    ("M1", "M", 1, "班组长级", "Team Leader", 6000, 9000),
    ("M2", "M", 2, "主管级", "Supervisor", 8000, 13000),
    ("M3", "M", 3, "经理级", "Manager", 12000, 20000),
    ("M4", "M", 4, "高级经理级", "Senior Manager", 18000, 30000),
    ("M5", "M", 5, "总监级", "Director", 26000, 45000),
    ("M6", "M", 6, "总经理级", "General Manager", 40000, 80000),
    ("P1", "P", 1, "助理级", "Assistant", 4000, 6000),
    ("P2", "P", 2, "专员级", "Specialist", 5000, 8000),
    ("P3", "P", 3, "高级专员级", "Senior Specialist", 7000, 11000),
    ("P4", "P", 4, "资深专员级", "Expert Specialist", 9000, 15000),
    ("P5", "P", 5, "主管专员级", "Principal Specialist", 12000, 18000),
    ("P6", "P", 6, "专家级", "Expert", 15000, 24000),
    ("P7", "P", 7, "高级专家级", "Senior Expert", 20000, 32000),
    ("P8", "P", 8, "首席专家级", "Chief Expert", 28000, 50000),
    ("T1", "T", 1, "技术员级", "Technician", 4500, 7000),
    ("T2", "T", 2, "助理工程师级", "Assistant Engineer", 6000, 9000),
    ("T3", "T", 3, "工程师级", "Engineer", 8000, 13000),
    ("T4", "T", 4, "高级工程师级", "Senior Engineer", 11000, 18000),
    ("T5", "T", 5, "资深工程师级", "Staff Engineer", 15000, 24000),
    ("T6", "T", 6, "专家工程师级", "Principal Engineer", 20000, 32000),
    ("T7", "T", 7, "首席工程师级", "Chief Engineer", 28000, 45000),
    ("T8", "T", 8, "技术院士级", "Technical Fellow", 40000, 70000),
    ("O1", "O", 1, "初级操作工", "Operator I", 4000, 5500),
    ("O2", "O", 2, "操作工", "Operator II", 5000, 7000),
    ("O3", "O", 3, "熟练操作工", "Skilled Operator", 6000, 8500),
    ("O4", "O", 4, "高级操作工", "Senior Operator", 7000, 10000),
    ("O5", "O", 5, "技师", "Technician Master", 8500, 13000),
]

# 岗位：(code, 名称, 英文名, 序列, 类别, 建议层级, 排序)
SEED_TITLES: list[tuple] = [
    ("JT-CHAIRMAN", "董事长", "Chairman", "M", "mgmt", 6, 1),
    ("JT-GM", "总经理", "General Manager", "M", "mgmt", 5, 2),
    ("JT-DGM", "副总经理", "Deputy General Manager", "M", "mgmt", 4, 3),
    ("JT-FACTORY-HEAD", "厂长", "Factory Head", "M", "mgmt", 4, 4),
    ("JT-VICE-HEAD", "副厂长", "Deputy Factory Head", "M", "mgmt", 3, 5),
    ("JT-MANAGER", "经理", "Manager", "M", "mgmt", 3, 6),
    ("JT-SUPERVISOR", "主管", "Supervisor", "M", "mgmt", 2, 7),
    ("JT-LEADER", "班组长", "Team Leader", "M", "mgmt", 1, 8),
    ("JT-RND-ENG", "研发工程师", "R&D Engineer", "T", "tech", 4, 10),
    ("JT-PROC-ENG", "工艺工程师", "Process Engineer", "T", "tech", 5, 11),
    ("JT-ELEC-ENG", "电气工程师", "Electrical Engineer", "T", "tech", 4, 12),
    ("JT-TECHNICIAN", "技术员", "Technician", "T", "tech", 3, 13),
    ("JT-QC", "质检员", "QC Inspector", "T", "tech", 2, 14),
    ("JT-SALES", "业务员", "Sales Representative", "P", "sales", 2, 20),
    ("JT-SALES-INTL", "外贸业务员", "Export Sales", "P", "sales", 3, 21),
    ("JT-MERCH", "跟单员", "Merchandiser", "P", "admin", 2, 22),
    ("JT-BUYER", "采购员", "Buyer", "P", "admin", 2, 23),
    ("JT-PLANNER", "生产计划员", "Production Planner", "P", "prod", 3, 24),
    ("JT-ACCOUNTANT", "会计", "Accountant", "P", "admin", 3, 25),
    ("JT-CASHIER", "出纳", "Cashier", "P", "admin", 2, 26),
    ("JT-HR", "人事专员", "HR Specialist", "P", "admin", 2, 27),
    ("JT-ADMIN", "行政专员", "Admin Specialist", "P", "admin", 2, 28),
    ("JT-LOGISTICS", "物流专员", "Logistics Specialist", "P", "admin", 2, 29),
    ("JT-WAREHOUSE", "仓管", "Warehouse Keeper", "O", "support", 2, 30),
    ("JT-OPERATOR", "操作工", "Operator", "O", "op", 2, 40),
    ("JT-SHEET", "钣金工", "Sheet Metal Worker", "O", "op", 3, 41),
    ("JT-ASSEMBLER", "装配工", "Assembler", "O", "op", 3, 42),
    ("JT-PACKER", "包装工", "Packer", "O", "op", 2, 43),
    ("JT-ELECTRICIAN", "电工", "Electrician", "O", "op", 4, 44),
    ("JT-FORKLIFT", "叉车工", "Forklift Operator", "O", "op", 3, 45),
]

# 编制：(组织 code, 岗位 code, 职级 code, 编制人数, 上级编制(组织+岗位), 状态, 关键岗, 排序)
# 上级编制用「组织 code + 岗位 code」定位，避免依赖自增 id
SEED_POSITIONS: list[tuple] = [
    ("GRP", "JT-CHAIRMAN", "M6", 1, None, "active", 1, 1),
    ("GRP", "JT-GM", "M5", 1, ("GRP", "JT-CHAIRMAN"), "active", 1, 2),
    ("CO01", "JT-DGM", "M4", 1, ("GRP", "JT-GM"), "open", 1, 3),
    ("HQ", "JT-ADMIN", "P2", 2, ("GRP", "JT-GM"), "active", 0, 10),
    ("AD", "JT-MANAGER", "M3", 1, ("GRP", "JT-GM"), "active", 0, 11),
    ("DPHR", "JT-SUPERVISOR", "M2", 1, ("GRP", "JT-GM"), "active", 0, 12),
    ("DPHR", "JT-HR", "P2", 2, ("DPHR", "JT-SUPERVISOR"), "active", 0, 13),
    ("DPFIN", "JT-MANAGER", "M3", 1, ("GRP", "JT-GM"), "active", 0, 14),
    ("DPFIN", "JT-ACCOUNTANT", "P3", 2, ("DPFIN", "JT-MANAGER"), "active", 0, 15),
    ("DPFIN", "JT-CASHIER", "P2", 1, ("DPFIN", "JT-MANAGER"), "active", 0, 16),
    ("TECH", "JT-MANAGER", "M3", 1, ("GRP", "JT-GM"), "active", 0, 17),
    ("TECH", "JT-TECHNICIAN", "T3", 3, ("TECH", "JT-MANAGER"), "active", 0, 18),
    ("TECH", "JT-ELEC-ENG", "T4", 2, ("TECH", "JT-MANAGER"), "open", 0, 19),
    ("DPRD", "JT-SUPERVISOR", "M2", 1, ("GRP", "JT-GM"), "active", 0, 20),
    ("DPRD", "JT-RND-ENG", "T4", 4, ("DPRD", "JT-SUPERVISOR"), "active", 1, 21),
    ("DPRD", "JT-PROC-ENG", "T5", 2, ("DPRD", "JT-SUPERVISOR"), "frozen", 0, 22),
    ("PUR", "JT-SUPERVISOR", "M2", 1, ("GRP", "JT-GM"), "active", 0, 23),
    ("PUR", "JT-BUYER", "P2", 2, ("PUR", "JT-SUPERVISOR"), "active", 0, 24),
    ("LOG", "JT-SUPERVISOR", "M2", 1, ("GRP", "JT-GM"), "active", 0, 25),
    ("LOG", "JT-MERCH", "P2", 2, ("LOG", "JT-SUPERVISOR"), "active", 0, 26),
    ("LOG", "JT-LOGISTICS", "P2", 1, ("LOG", "JT-SUPERVISOR"), "active", 0, 27),
    ("DPSALE", "JT-MANAGER", "M3", 1, ("GRP", "JT-GM"), "active", 0, 30),
    ("DPSALE", "JT-SALES", "P2", 6, ("DPSALE", "JT-MANAGER"), "active", 0, 31),
    ("DPINTL", "JT-MANAGER", "M3", 1, ("GRP", "JT-GM"), "active", 0, 32),
    ("DPINTL", "JT-SALES-INTL", "P3", 4, ("DPINTL", "JT-MANAGER"), "open", 0, 33),
    ("BRANCH", "JT-MANAGER", "M3", 1, ("DPSALE", "JT-MANAGER"), "active", 0, 34),
    ("BRANCH", "JT-SALES", "P2", 2, ("BRANCH", "JT-MANAGER"), "active", 0, 35),
    ("FACTORY", "JT-FACTORY-HEAD", "M4", 1, ("GRP", "JT-GM"), "active", 1, 40),
    ("FACTORY", "JT-PLANNER", "P3", 2, ("FACTORY", "JT-FACTORY-HEAD"),
     "active", 0, 41),
    ("WS01", "JT-SUPERVISOR", "M2", 1, ("FACTORY", "JT-FACTORY-HEAD"),
     "active", 0, 42),
    ("TM01", "JT-LEADER", "M1", 1, ("WS01", "JT-SUPERVISOR"), "active", 0, 43),
    ("TM01", "JT-SHEET", "O3", 8, ("TM01", "JT-LEADER"), "active", 0, 44),
    ("TM02", "JT-LEADER", "M1", 1, ("WS01", "JT-SUPERVISOR"), "active", 0, 45),
    ("TM02", "JT-SHEET", "O3", 6, ("TM02", "JT-LEADER"), "active", 0, 46),
    ("WS02", "JT-SUPERVISOR", "M2", 1, ("FACTORY", "JT-FACTORY-HEAD"),
     "active", 0, 47),
    ("WS02", "JT-ASSEMBLER", "O3", 10, ("WS02", "JT-SUPERVISOR"), "active", 0, 48),
    ("WS02", "JT-QC", "T2", 2, ("WS02", "JT-SUPERVISOR"), "active", 0, 49),
    ("FA02", "JT-FACTORY-HEAD", "M4", 1, ("GRP", "JT-GM"), "active", 1, 50),
    ("WS03", "JT-SUPERVISOR", "M2", 1, ("FA02", "JT-FACTORY-HEAD"), "active", 0, 51),
    ("WS03", "JT-PACKER", "O2", 6, ("WS03", "JT-SUPERVISOR"), "active", 0, 52),
    ("WS03", "JT-WAREHOUSE", "O2", 2, ("WS03", "JT-SUPERVISOR"), "active", 0, 53),
]

# 演示任职：(姓名, 编制(组织 code, 岗位 code), 主岗 1/兼岗 0, 占比)
# 刻意造出三种状态：满编（董事长/总经理/装配车间主管）、超编（装配车间质检员
# 编制 2 人却派了 3 人）、其余缺编。看板上没这三种颜色就没法演示口径。
SEED_STAFF: list[tuple] = [
    ("陈志强", ("GRP", "JT-CHAIRMAN"), 1, 1.0),
    ("李国强", ("GRP", "JT-GM"), 1, 1.0),
    ("王海燕", ("HQ", "JT-ADMIN"), 1, 1.0),
    ("刘婷", ("DPHR", "JT-HR"), 1, 1.0),
    ("张明", ("DPFIN", "JT-ACCOUNTANT"), 1, 1.0),
    ("张明", ("PUR", "JT-BUYER"), 0, 0.3),          # 兼岗：会计兼采购助理
    ("赵伟", ("DPRD", "JT-RND-ENG"), 1, 1.0),
    ("孙磊", ("TECH", "JT-TECHNICIAN"), 1, 1.0),
    ("周敏", ("DPSALE", "JT-SALES"), 1, 1.0),
    ("马超", ("BRANCH", "JT-SALES"), 1, 1.0),
    ("吴建华", ("FACTORY", "JT-FACTORY-HEAD"), 1, 1.0),
    ("谢勇", ("WS02", "JT-SUPERVISOR"), 1, 1.0),    # 满编
    ("何军", ("WS02", "JT-ASSEMBLER"), 1, 1.0),
    ("林芳", ("WS02", "JT-QC"), 1, 1.0),            # ┐
    ("许静", ("WS02", "JT-QC"), 1, 1.0),            # ├ 编制 2 / 在岗 3 → 超编
    ("罗成", ("WS02", "JT-QC"), 1, 1.0),            # ┘
    ("郑凯", ("TM01", "JT-SHEET"), 1, 1.0),
    ("黄丽", ("WS03", "JT-PACKER"), 1, 1.0),
]

# 演示人员账号前缀 —— 刻意用统一前缀，客户清理时一眼能认出
DEMO_USER_PREFIX = "demo_staff_"


def _upsert_grade(db, code: str, family: str, level: int, name: str, name_en: str,
                  smin: float | None, smax: float | None) -> None:
    vals = (family, level, name, name_en, smin, smax, now())
    if db.execute("SELECT 1 FROM job_grades WHERE code=?", (code,)).fetchone():
        db.execute("UPDATE job_grades SET family=?,level=?,name=?,name_en=?,"
                   "salary_min=?,salary_max=?,updated_at=? WHERE code=?",
                   (*vals, code))
    else:
        db.execute("INSERT INTO job_grades(code,family,level,name,name_en,"
                   "salary_min,salary_max,currency,duty,sort_order,remark,"
                   "is_active,created_at,updated_at)"
                   " VALUES(?,?,?,?,?,?,?,'CNY',NULL,?,NULL,1,?,?)",
                   (code, family, level, name, name_en, smin, smax, level,
                    now(), now()))


def _upsert_title(db, code: str, name: str, name_en: str, family: str,
                  category: str, level_hint: int, sort_order: int) -> None:
    vals = (name, name_en, family, category, level_hint, sort_order, now())
    if db.execute("SELECT 1 FROM job_titles WHERE code=?", (code,)).fetchone():
        db.execute("UPDATE job_titles SET name=?,name_en=?,family=?,category=?,"
                   "level_hint=?,sort_order=?,updated_at=? WHERE code=?",
                   (*vals, code))
    else:
        db.execute("INSERT INTO job_titles(code,name,name_en,family,category,"
                   "level_hint,duty,require_json,sort_order,remark,is_active,"
                   "created_at,updated_at) VALUES(?,?,?,?,?,?,NULL,'[]',?,NULL,1,?,?)",
                   (code, name, name_en, family, category, level_hint, sort_order,
                    now(), now()))


def _ensure_demo_staff(db, name: str, org_unit_id: int | None) -> int:
    """造一名演示用人员（幂等，按姓名匹配）。

    **刻意不给可登录口令**：password_hash 用一次性随机串哈希，谁也不知道明文，
    因此这些账号只能参与编制与统计，无法登录。管理员在「用户管理」里
    点「强制改密」即可把其中任何一人转成真实账号 —— 宁可多一步，
    也不在客户系统里预置一批口令已知的演示账号。
    """
    row = db.execute("SELECT id FROM users WHERE full_name=? AND username LIKE ?",
                     (name, DEMO_USER_PREFIX + "%")).fetchone()
    if row:
        uid = row["id"]
        db.execute("UPDATE users SET dept_id=COALESCE(?,dept_id),"
                   "org_unit_id=COALESCE(?,org_unit_id) WHERE id=?",
                   (org_unit_id, org_unit_id, uid))
        return uid
    n = db.execute("SELECT COUNT(*) c FROM users WHERE username LIKE ?",
                   (DEMO_USER_PREFIX + "%",)).fetchone()["c"]
    username = f"{DEMO_USER_PREFIX}{n + 1:02d}"
    while db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        n += 1
        username = f"{DEMO_USER_PREFIX}{n + 1:02d}"
    db.execute(
        "INSERT INTO users(username,email,full_name,company,phone,password_hash,"
        "role,dept_id,must_change_pw,is_active,created_at,emp_no,emp_status,"
        "join_date,leave_date,remark,org_unit_id)"
        " VALUES(?,?,?,?,?,?,?,?,1,1,?,?,'active',?,NULL,?,?)",
        (username, None, name, "菲力克斯科技（广州）有限公司", None,
         hash_password(uuid_like() + uuid_like() + "!Aa1"),
         "viewer", org_unit_id, now(), next_emp_no(), now()[:10],
         "M002 编制看板演示人员 · 初始不可登录，管理员可在用户管理强制改密启用",
         org_unit_id))
    return db.execute("SELECT id FROM users WHERE username=?",
                      (username,)).fetchone()["id"]


def seed_positions() -> dict:
    """灌岗位职级编制种子 —— 幂等：按 code / 姓名 upsert，只补不删。

    返回各类条数，便于启动日志与自测核对。
    """
    db = get_conn()
    for code, family, level, name, name_en, smin, smax in SEED_GRADES:
        _upsert_grade(db, code, family, level, name, name_en, smin, smax)
    for code, name, name_en, family, cat, hint, order in SEED_TITLES:
        _upsert_title(db, code, name, name_en, family, cat, hint, order)
    db.commit()

    # ---- 编制 ----
    for org_code, title_code, grade_code, hc, parent, status, is_key, order in \
            SEED_POSITIONS:
        org = db.execute("SELECT id,code FROM org_units WHERE code=?",
                         (org_code,)).fetchone()
        title = db.execute("SELECT id FROM job_titles WHERE code=?",
                           (title_code,)).fetchone()
        grade = db.execute("SELECT id FROM job_grades WHERE code=?",
                           (grade_code,)).fetchone()
        if not org or not title or not grade:
            continue
        parent_id = None
        if parent:
            p_org, p_title = parent
            prow = db.execute(
                "SELECT p.id FROM positions p JOIN org_units ou ON ou.id=p.org_unit_id "
                "JOIN job_titles jt ON jt.id=p.job_title_id "
                "WHERE ou.code=? AND jt.code=?", (p_org, p_title)).fetchone()
            parent_id = prow["id"] if prow else None
        code = f"POS-{org_code}-{title_code.replace('JT-', '')}"
        # 名称与前端/接口新增时保持同一口径：组织 · 岗位 · 职级
        name = _auto_name(db, org["id"], title["id"], grade["id"])
        if db.execute("SELECT 1 FROM positions WHERE code=?", (code,)).fetchone():
            db.execute("UPDATE positions SET name=?,org_unit_id=?,job_title_id=?,"
                       "job_grade_id=?,headcount=?,parent_position_id=?,status=?,"
                       "is_key=?,sort_order=?,updated_at=? WHERE code=?",
                       (name, org["id"], title["id"], grade["id"], float(hc),
                        parent_id, status, is_key, order, now(), code))
        else:
            db.execute(
                "INSERT INTO positions(code,name,org_unit_id,job_title_id,"
                "job_grade_id,headcount,parent_position_id,status,is_key,"
                "work_place,duty,require_json,sort_order,remark,is_active,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,NULL,NULL,'[]',"
                "?,NULL,1,?,?)",
                (code, name, org["id"], title["id"], grade["id"], float(hc),
                 parent_id, status, is_key, order, now(), now()))
    db.commit()

    # ---- 演示任职 ----
    for name, (org_code, title_code), is_primary, ratio in SEED_STAFF:
        pos = db.execute(
            "SELECT p.id, p.org_unit_id FROM positions p "
            "JOIN org_units ou ON ou.id=p.org_unit_id "
            "JOIN job_titles jt ON jt.id=p.job_title_id "
            "WHERE ou.code=? AND jt.code=?", (org_code, title_code)).fetchone()
        if not pos:
            continue
        uid = _ensure_demo_staff(db, name, pos["org_unit_id"])
        if db.execute("SELECT 1 FROM position_assignments WHERE position_id=? "
                      "AND user_id=?", (pos["id"], uid)).fetchone():
            db.execute("UPDATE position_assignments SET is_primary=?,ratio=?,"
                       "is_active=1,end_date=NULL,updated_at=? "
                       "WHERE position_id=? AND user_id=?",
                       (is_primary, ratio, now(), pos["id"], uid))
        else:
            db.execute(
                "INSERT INTO position_assignments(position_id,user_id,is_primary,"
                "ratio,start_date,end_date,remark,is_active,created_at,updated_at)"
                " VALUES(?,?,?,?,?,NULL,?,1,?,?)",
                (pos["id"], uid, is_primary, ratio, now()[:10],
                 "种子演示任职" if is_primary else "种子演示兼岗", now(), now()))
    db.commit()

    counts = {
        "job_titles": db.execute("SELECT COUNT(*) c FROM job_titles").fetchone()["c"],
        "job_grades": db.execute("SELECT COUNT(*) c FROM job_grades").fetchone()["c"],
        "positions": db.execute("SELECT COUNT(*) c FROM positions").fetchone()["c"],
        "assignments": db.execute(
            "SELECT COUNT(*) c FROM position_assignments WHERE is_active=1"
        ).fetchone()["c"],
    }
    release_conn()
    return counts
