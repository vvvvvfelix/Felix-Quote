"""模块层 — 组织树（M001）：集团多组织主干。

为什么单独建 org_units 而不是继续用 departments：

现实里的集团是**三轴**的。一个「装配车间」同时属于 一厂（生产轴）、
国内事业部（经营轴）、广州菲力克斯机械（法人轴）。旧的 departments 只有
一条 parent_id 链，只能表达一条轴 —— 于是「一厂厂长看不到二厂数据」
「国际事业部看不到国内客户底价」这类刚需做不出来，只能在业务代码里
写特判，越写越烂。

org_units 的做法：树本身只管层级（parent_id + path），三条归属线用
company_id / factory_id / bu_id 三个冗余列自顶向下继承。这样：
- 查子树：`WHERE path LIKE '/1/4/%'`（一次索引扫描）
- 查数据范围：`WHERE company_id=?`（退化成等值过滤）
- 新增一条归属线：加一个冗余列 + 一条 rollup 规则，不动业务代码

兼容：旧 departments 表保留 id 对齐，本模块单向回写（_mirror），
让旧页面（部门看板、用户列表）零改动继续可用；M004 完成数据范围改造
后废弃 departments。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_user
from ..core import orgutil, permissions as P
from ..core.db import get_conn, now, release_conn
from ..core.services import audit

router = APIRouter(prefix="/api/structure", tags=["structure"])

# ==========================================================================
# 一、枚举 —— 前端不得硬编码中文，一律从 /types 取
# ==========================================================================
ORG_TYPES = {
    "group":    "集团",
    "company":  "法人公司",
    "factory":  "工厂",
    "office":   "总部/办事处",
    "branch":   "分公司",
    "bu":       "事业部",
    "dept":     "职能部门",
    "workshop": "车间",
    "team":     "班组",
}
# 树形下拉里允许挂子级的类型（班组下面不再分层）
CAN_HAVE_CHILD = ("group", "company", "factory", "office", "branch", "bu",
                  "dept", "workshop")

ROLLUPS = {"company_id": ["company"], "factory_id": ["factory"],
           "bu_id": ["bu"]}


# ==========================================================================
# 二、内部工具
# ==========================================================================
def _member_counts(db) -> dict[int, int]:
    """各组织在岗人数。

    用 COALESCE(org_unit_id, dept_id)：迁移前建的老用户只有 dept_id，
    两个列在 M001 里 id 天然对齐，合并统计才不会「少一个人」。
    """
    rows = db.execute(
        "SELECT COALESCE(org_unit_id, dept_id) uid, COUNT(*) c FROM users "
        "WHERE is_active=1 AND IFNULL(emp_status,'active') IN ('probation','active') "
        "AND COALESCE(org_unit_id, dept_id) IS NOT NULL GROUP BY uid")
    return {r["uid"]: r["c"] for r in rows}


def _child_counts(db) -> dict[int, int]:
    rows = db.execute(
        "SELECT parent_id pid, COUNT(*) c FROM org_units "
        "WHERE parent_id IS NOT NULL AND is_active=1 GROUP BY parent_id")
    return {r["pid"]: r["c"] for r in rows}


def _rows(db, where: str = "1=1", args: list | None = None) -> list[dict]:
    sql = ("SELECT ou.*, us.full_name AS leader_name, p.name AS parent_name "
           "FROM org_units ou LEFT JOIN users us ON us.id=ou.leader_id "
           "LEFT JOIN org_units p ON p.id=ou.parent_id "
           f"WHERE {where} ORDER BY ou.sort_order, ou.id")
    rows = [dict(r) for r in db.execute(sql, args or [])]
    counts = _member_counts(db)
    kids = _child_counts(db)
    for r in rows:
        r["org_type_label"] = ORG_TYPES.get(r["org_type"], r["org_type"])
        r["member_count"] = counts.get(r["id"], 0)
        r["child_count"] = kids.get(r["id"], 0)
        r["can_have_child"] = r["org_type"] in CAN_HAVE_CHILD
    return rows


def _recalc(db) -> None:
    """结构变动后统一重算冗余字段 + 回写旧表。"""
    orgutil.recompute_tree(db, table="org_units", rollups=ROLLUPS)
    _mirror(db)


def _mirror(db) -> int:
    """单向回写 departments（兼容层）。

    目的：users.dept_id 指向旧表，旧页面（部门看板、用户列表、待办）
    全都 LEFT JOIN departments。组织树改了名字/上级，旧页面必须同步看到，
    否则会出现「组织树里叫一厂，部门下拉里叫工厂」的双份真相。

    按 code 匹配；code 不存在时按 id 兜底（保证 id 对齐不被破坏）。
    """
    n = 0
    for r in db.execute("SELECT * FROM org_units"):
        code, uid = r["code"], r["id"]
        vals = (r["name"], r["org_type"], r["parent_id"], r["leader_id"],
                r["sort_order"], r["is_active"])
        row = db.execute("SELECT id FROM departments WHERE code=?", (code,)).fetchone()
        if row:
            db.execute("UPDATE departments SET name=?,kind=?,parent_id=?,"
                       "leader_id=?,sort_order=?,is_active=? WHERE code=?",
                       (*vals, code))
        elif db.execute("SELECT 1 FROM departments WHERE id=?", (uid,)).fetchone():
            db.execute("UPDATE departments SET code=?,name=?,kind=?,parent_id=?,"
                       "leader_id=?,sort_order=?,is_active=? WHERE id=?",
                       (code, *vals, uid))
        else:
            db.execute("INSERT INTO departments(id,code,name,kind,parent_id,"
                       "leader_id,sort_order,is_active,created_at)"
                       " VALUES(?,?,?,?,?,?,?,?,?)",
                       (uid, code, r["name"], r["org_type"], r["parent_id"],
                        r["leader_id"], r["sort_order"], r["is_active"], now()))
        n += 1
    db.commit()
    return n


def _assert_no_cycle(db, unit_id: int | None, parent_id: int | None) -> None:
    """环检测：不能把自己或自己的后代设为上级。

    组织树成环会让所有递归查询（path 重算、数据范围、面包屑）死循环或漏数据，
    必须在写入前挡住 —— 数据坏了可以修，库卡死就没救了。
    """
    if not parent_id:
        return
    if unit_id and parent_id == unit_id:
        raise HTTPException(400, "上级不能是自己")
    orgs = {r["id"]: dict(r) for r in db.execute("SELECT id,parent_id FROM org_units")}
    if unit_id:
        for kid in orgutil.descendants(unit_id, orgs):
            if kid == parent_id:
                raise HTTPException(400, "不能把下级组织设为上级（会成环）")


# ==========================================================================
# 三、查询接口
# ==========================================================================
class UnitIn(BaseModel):
    code: str
    name: str
    name_en: str | None = None
    org_type: str = "dept"
    parent_id: int | None = None
    leader_id: int | None = None
    cost_center: str | None = None
    address: str | None = None
    phone: str | None = None
    tax_no: str | None = None
    sort_order: int = 0
    remark: str | None = None
    is_active: int = 1


@router.get("/types")
def list_types(u: dict = Depends(current_user)):
    P.require(u, "structure.view")
    return [{"value": k, "label": v} for k, v in ORG_TYPES.items()]


@router.get("/units")
def list_units(u: dict = Depends(current_user), org_type: str | None = None,
               parent_id: int | None = None, keyword: str | None = None,
               active: int | None = 1, company_id: int | None = None,
               factory_id: int | None = None, bu_id: int | None = None):
    """扁平列表（带筛选）。前端下拉、筛选表格都用它。"""
    P.require(u, "structure.view")
    where, args = ["1=1"], []
    if org_type:
        where.append("ou.org_type=?")
        args.append(org_type)
    if parent_id is not None:
        where.append("ou.parent_id=?")
        args.append(parent_id)
    if keyword:
        where.append("(ou.name LIKE ? OR ou.code LIKE ? OR IFNULL(ou.cost_center,'') LIKE ?)")
        args += [f"%{keyword}%"] * 3
    # active=-1 表示「全部」（含已停用）；下拉选上级时必须能选到停用组织之外的全部节点，
    # 也方便管理员在「显示停用」时看整棵树
    if active is not None and int(active) >= 0:
        where.append("ou.is_active=?")
        args.append(active)
    for col, val in (("ou.company_id", company_id), ("ou.factory_id", factory_id),
                     ("ou.bu_id", bu_id)):
        if val is not None:
            where.append(f"{col}=?")
            args.append(val)
    db = get_conn()
    rows = _rows(db, " AND ".join(where), args)
    release_conn()
    return rows


@router.get("/tree")
def tree(u: dict = Depends(current_user), active: int | None = 1):
    """嵌套树 —— 前端一次拿到整棵树，不必逐级请求。

    每个节点带 member_count（本级）与 total_count（含全部下级），
    组织看板上「一厂 128 人」这种数是厂长每天要看的。
    """
    P.require(u, "structure.view")
    db = get_conn()
    if active is None or int(active) < 0:
        rows = _rows(db)
    else:
        rows = _rows(db, "ou.is_active=?", [active])
    release_conn()
    by_id = {r["id"]: r for r in rows}
    children: dict[int | None, list[dict]] = {}
    for r in rows:
        children.setdefault(r["parent_id"], []).append(r)

    def build(pid: int | None) -> list[dict]:
        out = []
        for n in children.get(pid, []):
            kids = build(n["id"])
            n["children"] = kids
            n["total_count"] = n["member_count"] + sum(k["total_count"] for k in kids)
            out.append(n)
        return out

    roots = build(None)
    return {"tree": roots,
            "orphans": [r for r in rows
                        if r["parent_id"] and r["parent_id"] not in by_id]}


@router.get("/units/{uid}")
def get_unit(uid: int, u: dict = Depends(current_user)):
    P.require(u, "structure.view")
    db = get_conn()
    rows = _rows(db, "ou.id=?", [uid])
    if not rows:
        release_conn()
        raise HTTPException(404, "组织不存在")
    row = rows[0]
    orgs = {r["id"]: dict(r) for r in db.execute("SELECT * FROM org_units")}
    release_conn()
    row["chain"] = [{"id": o["id"], "name": o["name"],
                     "org_type_label": ORG_TYPES.get(o.get("org_type"), "")}
                    for o in orgutil.chain(uid, orgs)]
    row["subtree_ids"] = [i for i in orgutil.subtree_ids(uid, orgs) if i != uid]
    return row


@router.get("/stats")
def stats(u: dict = Depends(current_user)):
    """组织体检看板：各类型数量 + 在岗总人数 + 无归属人员数。"""
    P.require(u, "structure.view")
    db = get_conn()
    by_type = {r["org_type"]: r["c"] for r in db.execute(
        "SELECT org_type, COUNT(*) c FROM org_units WHERE is_active=1 GROUP BY org_type")}
    total = db.execute("SELECT COUNT(*) c FROM users WHERE is_active=1 "
                       "AND IFNULL(emp_status,'active') IN ('probation','active')"
                       ).fetchone()["c"]
    no_org = db.execute("SELECT COUNT(*) c FROM users WHERE is_active=1 "
                        "AND COALESCE(org_unit_id, dept_id) IS NULL").fetchone()["c"]
    release_conn()
    return {"by_type": [{"value": k, "label": ORG_TYPES.get(k, k),
                         "count": by_type.get(k, 0)} for k in ORG_TYPES],
            "active_units": sum(by_type.values()),
            "headcount": total, "unassigned": no_org,
            "integrity": orgutil.check_integrity("org_units")}


@router.get("/companies")
def list_companies(u: dict = Depends(current_user)):
    """法人公司列表 —— 单据的「所属公司」与账套归属从这里取。"""
    P.require(u, "structure.view")
    db = get_conn()
    rows = [dict(r) for r in db.execute(
        "SELECT id,code,name,cost_center,tax_no FROM org_units "
        "WHERE org_type='company' AND is_active=1 ORDER BY sort_order,id")]
    release_conn()
    return rows


@router.get("/factories")
def list_factories(u: dict = Depends(current_user)):
    P.require(u, "structure.view")
    db = get_conn()
    rows = [dict(r) for r in db.execute(
        "SELECT id,code,name,cost_center,company_id,address FROM org_units "
        "WHERE org_type='factory' AND is_active=1 ORDER BY sort_order,id")]
    release_conn()
    return rows


@router.get("/bus")
def list_bus(u: dict = Depends(current_user)):
    P.require(u, "structure.view")
    db = get_conn()
    rows = [dict(r) for r in db.execute(
        "SELECT id,code,name,cost_center FROM org_units "
        "WHERE org_type='bu' AND is_active=1 ORDER BY sort_order,id")]
    release_conn()
    return rows


# ==========================================================================
# 四、写入接口
# ==========================================================================
@router.post("/units")
def create_unit(body: UnitIn, u: dict = Depends(current_user)):
    P.require(u, "structure.edit")
    if body.org_type not in ORG_TYPES:
        raise HTTPException(400, f"未知组织类型 {body.org_type}")
    db = get_conn()
    if db.execute("SELECT 1 FROM org_units WHERE code=?", (body.code,)).fetchone():
        release_conn()
        raise HTTPException(400, "组织编码已存在（可能是已停用的组织，请换一个或先恢复）")
    if body.parent_id and not db.execute(
            "SELECT 1 FROM org_units WHERE id=?", (body.parent_id,)).fetchone():
        release_conn()
        raise HTTPException(400, "上级组织不存在")
    _assert_no_cycle(db, None, body.parent_id)
    cur = db.execute(
        "INSERT INTO org_units(code,name,name_en,org_type,parent_id,leader_id,"
        "cost_center,address,phone,tax_no,sort_order,remark,is_active,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (body.code, body.name, body.name_en, body.org_type, body.parent_id,
         body.leader_id, body.cost_center, body.address, body.phone, body.tax_no,
         body.sort_order, body.remark, body.is_active, now(), now()))
    db.commit()
    uid = cur.lastrowid
    _recalc(db)
    release_conn()
    audit(u, "structure", "create", "org_unit", uid, body.name,
          after=body.model_dump())
    return {"id": uid}


@router.put("/units/{uid}")
def update_unit(uid: int, body: UnitIn, u: dict = Depends(current_user)):
    P.require(u, "structure.edit")
    if body.org_type not in ORG_TYPES:
        raise HTTPException(400, f"未知组织类型 {body.org_type}")
    db = get_conn()
    before = db.execute("SELECT * FROM org_units WHERE id=?", (uid,)).fetchone()
    if not before:
        release_conn()
        raise HTTPException(404, "组织不存在")
    if db.execute("SELECT 1 FROM org_units WHERE code=? AND id<>?",
                  (body.code, uid)).fetchone():
        release_conn()
        raise HTTPException(400, "组织编码已被占用")
    if body.parent_id and not db.execute(
            "SELECT 1 FROM org_units WHERE id=?", (body.parent_id,)).fetchone():
        release_conn()
        raise HTTPException(400, "上级组织不存在")
    _assert_no_cycle(db, uid, body.parent_id)
    db.execute(
        "UPDATE org_units SET code=?,name=?,name_en=?,org_type=?,parent_id=?,"
        "leader_id=?,cost_center=?,address=?,phone=?,tax_no=?,sort_order=?,"
        "remark=?,is_active=?,updated_at=? WHERE id=?",
        (body.code, body.name, body.name_en, body.org_type, body.parent_id,
         body.leader_id, body.cost_center, body.address, body.phone, body.tax_no,
         body.sort_order, body.remark, body.is_active, now(), uid))
    db.commit()
    _recalc(db)
    release_conn()
    audit(u, "structure", "update", "org_unit", uid, body.name,
          before=dict(before), after=body.model_dump())
    return {"ok": True}


class MoveIn(BaseModel):
    parent_id: int | None = None
    sort_order: int = 0


@router.post("/units/{uid}/move")
def move_unit(uid: int, body: MoveIn, u: dict = Depends(current_user)):
    """调整归属（拖拽）。单独接口是因为它最容易成环，校验要独立做。"""
    P.require(u, "structure.edit")
    db = get_conn()
    if not db.execute("SELECT 1 FROM org_units WHERE id=?", (uid,)).fetchone():
        release_conn()
        raise HTTPException(404, "组织不存在")
    if body.parent_id and not db.execute(
            "SELECT 1 FROM org_units WHERE id=?", (body.parent_id,)).fetchone():
        release_conn()
        raise HTTPException(400, "上级组织不存在")
    _assert_no_cycle(db, uid, body.parent_id)
    db.execute("UPDATE org_units SET parent_id=?,sort_order=?,updated_at=? WHERE id=?",
               (body.parent_id, body.sort_order, now(), uid))
    db.commit()
    _recalc(db)
    release_conn()
    audit(u, "structure", "move", "org_unit", uid,
          after={"parent_id": body.parent_id, "sort_order": body.sort_order})
    return {"ok": True}


@router.delete("/units/{uid}")
def delete_unit(uid: int, u: dict = Depends(current_user)):
    """停用而非物理删除 —— 历史单据要能追溯所属组织。"""
    P.require(u, "structure.edit")
    db = get_conn()
    row = db.execute("SELECT * FROM org_units WHERE id=?", (uid,)).fetchone()
    if not row:
        release_conn()
        raise HTTPException(404, "组织不存在")
    kids = db.execute("SELECT COUNT(*) c FROM org_units WHERE parent_id=? AND is_active=1",
                      (uid,)).fetchone()["c"]
    if kids:
        release_conn()
        raise HTTPException(400, f"还有 {kids} 个下级组织，请先调整或停用下级")
    members = _member_counts(db).get(uid, 0)
    if members:
        release_conn()
        raise HTTPException(400, f"还有 {members} 名在岗人员，请先调整其归属")
    db.execute("UPDATE org_units SET is_active=0,updated_at=? WHERE id=?", (now(), uid))
    db.commit()
    _recalc(db)
    release_conn()
    audit(u, "structure", "disable", "org_unit", uid, row["name"], before=dict(row))
    return {"ok": True}


# ==========================================================================
# 五、种子：集团型示例组织树（幂等 upsert）
# ==========================================================================
# (code, 名称, org_type, 上级 code, 排序, 成本中心, 备注)
SEED_UNITS: list[tuple] = [
    ("GRP",     "菲力克斯集团",                 "group",   None,     1, "CC-000",
     "虚拟顶层，统筹法人公司、事业部与工厂"),
    ("CO01",    "菲力克斯科技（广州）有限公司",  "company", "GRP",    1, "CC-100",
     "生产制造主体（一厂/二厂）"),
    ("CO02",    "菲力克斯国际贸易（广州）有限公司",  "company", "GRP",    2, "CC-200",
     "外贸出口主体（国际事业部结算口径）"),
    ("FACTORY", "一厂（白云生产基地）",      "factory", "CO01",   1, "CC-110",
     "钣金 → 装配 → 总装"),
    ("WS01",    "钣金车间",                 "workshop", "FACTORY", 1, "CC-111", ""),
    ("TM01",    "钣金 A 班",                "team",    "WS01",   1, "CC-111", "早班"),
    ("TM02",    "钣金 B 班",                "team",    "WS01",   2, "CC-111", "晚班"),
    ("WS02",    "装配车间",                 "workshop", "FACTORY", 2, "CC-112", ""),
    ("FA02",    "二厂（佛山生产基地）",      "factory", "CO01",   2, "CC-120",
     "包装与外协总装"),
    ("WS03",    "包装车间",                 "workshop", "FA02",  1, "CC-121", ""),
    ("HQ",      "公司总部",                 "office",  "CO01",   3, "CC-130",
     "职能中心：行政/人事/财务/技术/研发"),
    ("AD",      "行政部",                   "dept",    "HQ",     1, "CC-131", ""),
    ("DPHR",    "人事部",                   "dept",    "HQ",     2, "CC-132", ""),
    ("DPFIN",   "财务部",                   "dept",    "HQ",     3, "CC-133", ""),
    ("TECH",    "技术部",                   "dept",    "HQ",     4, "CC-134", ""),
    ("DPRD",    "研发部",                   "dept",    "HQ",     5, "CC-135", ""),
    ("PUR",     "采购部",                   "dept",    "HQ",     6, "CC-136", ""),
    ("LOG",     "物流部",                   "dept",    "HQ",     7, "CC-137", ""),
    ("BU01",    "国内事业部",               "bu",      "GRP",    3, "CC-300", ""),
    ("DPSALE",  "国内销售部",               "dept",    "BU01",   1, "CC-310", ""),
    ("BRANCH",  "深圳分公司",               "branch",  "BU01",   2, "CC-320", ""),
    ("BU02",    "国际事业部",               "bu",      "GRP",    4, "CC-400", ""),
    ("DPINTL",  "国际业务部",               "dept",    "BU02",   1, "CC-410", ""),
]


def _absorb_legacy(db) -> int:
    """收纳迁移遗留的顶层节点 —— 保证组织树只有一个根。

    迁移把旧 departments 全量复制了进来，里面有一批「UI 分组」
    （销售部门 / 生产车间 / 财务部门 / 人事行政，kind='group'），
    它们不是真实组织，留着就会变成和「集团」平级的第二个根，
    于是树形控件出现多棵树、统计口径分裂、数据范围算错。

    处理原则（与组织跳层一致：**不丢数据，只归位**）：
    - 遗留的 group 类型顶层节点 → 停用并标注（旧表同步停用，历史单据仍可追溯）
    - 其他无父节点（管理员自建的部门）→ 挂到集团下，不打断现有人员归属
    """
    root = db.execute("SELECT id FROM org_units WHERE code='GRP'").fetchone()
    root_id = root["id"] if root else None
    n = 0
    for r in db.execute("SELECT * FROM org_units WHERE parent_id IS NULL "
                        "AND code<>'GRP'"):
        if r["org_type"] == "group":
            db.execute("UPDATE org_units SET is_active=0, remark=?, updated_at=? WHERE id=?",
                       ("历史 UI 分组，已由事业部 / 职能部门取代（M001 自动归档）",
                        now(), r["id"]))
        elif root_id:
            db.execute("UPDATE org_units SET parent_id=?, updated_at=? WHERE id=?",
                       (root_id, now(), r["id"]))
        n += 1
    return n


def seed_structure() -> int:
    """灌组织树种子 —— 幂等：按 code upsert，只补不删。

    刻意用「按 code upsert」而不是「表空才插」：迁移已经把旧 departments
    复制进来了，表永远不空；而且管理员可能已改过名称，空表判断会让
    后续新增的组织永远补不上。
    """
    db = get_conn()
    for code, name, otype, parent, order, cost, remark in SEED_UNITS:
        parent_id = None
        if parent:
            r = db.execute("SELECT id FROM org_units WHERE code=?", (parent,)).fetchone()
            parent_id = r["id"] if r else None
        row = db.execute("SELECT id FROM org_units WHERE code=?", (code,)).fetchone()
        if row:
            db.execute("UPDATE org_units SET name=?,org_type=?,parent_id=?,"
                       "cost_center=?,remark=?,updated_at=? WHERE id=?",
                       (name, otype, parent_id, cost, remark, now(), row["id"]))
        else:
            db.execute(
                "INSERT INTO org_units(code,name,org_type,parent_id,cost_center,"
                "remark,sort_order,is_active,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,1,?,?)",
                (code, name, otype, parent_id, cost, remark, order, now(), now()))
    _absorb_legacy(db)
    db.commit()
    _recalc(db)
    n = db.execute("SELECT COUNT(*) c FROM org_units").fetchone()["c"]
    release_conn()
    return n
