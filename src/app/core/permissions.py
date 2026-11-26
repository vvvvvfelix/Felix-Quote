"""权限与数据范围 — 企业化的第一道门。

两层设计（刻意不做字段级权限，成本过高实际少用）：
1. 功能权限：角色 → 模块 → 动作
2. 数据范围：角色 → all 全部 / dept 本部门 / self 仅本人

VPN 内网多站点场景下，关键是「财务看不到生产数据、生产看不到客户报价」。
"""
from fastapi import HTTPException

from .db import get_conn, jdump, jload, now

# ==========================================================================
# 角色定义
# ==========================================================================
ROLES = {
    "admin": {
        "name": "系统管理员", "scope": "all",
        "perm": "*",
    },
    "sales_manager": {
        "name": "销售经理", "scope": "dept",
        "perm": ["products.view", "customers.view", "customers.edit",
                 "inquiries.view", "inquiries.edit", "quotations.view",
                 "quotations.edit", "quotations.approve", "orders.view",
                 "orders.edit", "tasks.view", "finance.view", "analytics.view",
                 "suppliers.view", "documents.view", "reports.view", "reports.edit",
                 "structure.view", "position.view", "employee.view"],
    },
    "sales": {
        "name": "业务员", "scope": "self",
        "perm": ["products.view", "customers.view", "customers.edit",
                 "inquiries.view", "inquiries.edit", "quotations.view",
                 "quotations.edit", "orders.view", "orders.edit",
                 "tasks.view", "documents.view", "analytics.view.self",
                 "reports.view", "structure.view", "position.view",
                 "employee.view"],
    },
    "tech": {
        "name": "技术工程师", "scope": "dept",
        "perm": ["products.view", "products.edit", "orders.view",
                 "orders.tech_confirm", "tasks.view", "documents.view",
                 "customers.view", "structure.view", "position.view",
                 "employee.view"],
    },
    "purchase": {
        "name": "采购", "scope": "dept",
        "perm": ["suppliers.view", "suppliers.edit", "products.view",
                 "orders.view", "orders.purchase", "tasks.view",
                 "documents.view", "cost.view", "cost.edit", "structure.view",
                 "position.view", "employee.view"],
    },
    "production": {
        "name": "生产/跟单", "scope": "dept",
        "perm": ["orders.view", "orders.production", "tasks.view",
                 "documents.view", "products.view", "suppliers.view",
                 "reports.view", "structure.view", "position.view",
                 "employee.view"],
    },
    "finance": {
        "name": "财务", "scope": "all",
        "perm": ["orders.view", "finance.view", "finance.edit",
                 "customers.view", "tasks.view", "analytics.view",
                 "documents.view", "reports.view", "reports.edit",
                 "structure.view",
                 # position.edit / employee.* 暂挂财务（本系统人事行政归财务部口径，
                 # 见迁移 _v005 的「AD 行政人事部」）；M004 角色库扩出独立
                 # 人事角色后再迁走，避免现在为一个权限点新建一套角色体系。
                 "position.view", "position.edit",
                 "employee.view", "employee.edit", "employee.sensitive.view"],
    },
    "logistics": {
        "name": "物流/关务", "scope": "dept",
        "perm": ["orders.view", "logistics.view", "logistics.edit",
                 "tasks.view", "documents.view", "customers.view",
                 "reports.view", "reports.edit", "structure.view",
                 "position.view", "employee.view"],
    },
    "viewer": {
        "name": "只读", "scope": "all",
        "perm": ["products.view", "customers.view", "orders.view",
                 "quotations.view", "inquiries.view", "analytics.view",
                 "structure.view", "position.view", "employee.view"],
    },

    # ---- M004 新增：按三轴组织层级细分的角色（scope 扩到 company/factory/bu） ----
    # 说明：company/factory/bu 三档范围只在「组织轴模块」（员工档案 / 岗位编制 /
    # 组织树）通过 core/permissions.py 的 scope_clause_org 生效；业务单据（订单/
    # 询盘等）沿用 all/dept/self，由各自模块的历史 scope_clause 处理，避免一次性
    # 改动 15 个业务表的列结构（详见 00-ARCHITECTURE 第四节与 M004 进度日志）。
    "chairman": {
        "name": "董事长", "scope": "all",
        "perm": "*",
    },
    "gm": {
        "name": "总经理", "scope": "company",
        "perm": ["products.view", "customers.view", "orders.view",
                 "quotations.view", "inquiries.view", "analytics.view",
                 "finance.view", "structure.view", "position.view",
                 "employee.view", "employee.edit", "reports.view",
                 "reports.edit", "tasks.view"],
    },
    "factory_head": {
        "name": "厂长", "scope": "factory",
        "perm": ["products.view", "customers.view", "orders.view",
                 "production.view", "suppliers.view", "documents.view",
                 "structure.view", "position.view", "employee.view",
                 "tasks.view", "reports.view"],
    },
    "hr": {
        "name": "人事专员", "scope": "company",
        "perm": ["users.view", "structure.view", "position.view",
                 "employee.view", "employee.edit", "employee.sensitive.view",
                 "tasks.view"],
    },
    "manager": {
        "name": "部门经理", "scope": "dept",
        "perm": ["products.view", "customers.view", "quotations.view",
                 "orders.view", "inquiries.view", "tasks.view",
                 "structure.view", "position.view", "employee.view"],
    },
    "supervisor": {
        "name": "主管", "scope": "dept",
        "perm": ["products.view", "customers.view", "tasks.view",
                 "structure.view", "position.view", "employee.view"],
    },
    "rnd": {
        "name": "研发工程师", "scope": "dept",
        "perm": ["products.view", "products.edit", "orders.view",
                 "documents.view", "structure.view", "position.view",
                 "employee.view", "tasks.view"],
    },
    "technician": {
        "name": "技术员", "scope": "dept",
        "perm": ["products.view", "orders.view", "documents.view",
                 "structure.view", "position.view", "employee.view",
                 "tasks.view"],
    },
}

SCOPE_LABEL = {"all": "全部数据", "company": "本公司", "factory": "本工厂",
                "bu": "本事业部", "dept": "本部门", "self": "仅本人"}


def seed_roles() -> int:
    """把内置角色写入 roles 表（幂等）。"""
    db = get_conn()
    n = 0
    for i, (code, r) in enumerate(ROLES.items()):
        if db.execute("SELECT 1 FROM roles WHERE code=?", (code,)).fetchone():
            db.execute("UPDATE roles SET name=?,scope=?,permissions=? WHERE code=?",
                       (r["name"], r["scope"], jdump(r["perm"]), code))
        else:
            db.execute("INSERT INTO roles(code,name,scope,permissions,sort_order,is_builtin,created_at)"
                       " VALUES(?,?,?,?,?,1,?)",
                       (code, r["name"], r["scope"], jdump(r["perm"]), i, now()))
        n += 1
    db.commit()
    return n


# 审批引擎权限分配（M005）：审批是每个人的日常，故除 admin 外所有人默认可
# 「查看审批中心 + 处理待办」；流程定义（增删改流程模板）仅限管理角色。
_WORKFLOW_VIEW_APPROVE_ROLES = set(ROLES) - {"admin"}
# 流程定义是「内控规则」本身：改一条边就能让报销跳过财务复核，或在审批链里
# 插进自己。制定权必须与业务执行角色分离（职责分离），只留给管治理的角色。
# sales_manager 曾在此集合内 —— 销售经理日常只需「发起 / 审批」，没有理由
# 拿着改全公司审批链的钥匙；且流程定义成环历史上曾把整个服务拖成远程拒绝服务，
# 持有者越少，爆炸半径越小。客户若确需业务线自建流程，走自定义角色授权，
# 不写死在内建角色里。
_WORKFLOW_MANAGE_ROLES = {"admin", "chairman", "gm", "hr"}

# 报销权限分配（M071）：报销与审批一样是全员日常，故除 admin 外所有人默认可
# 「查看 + 起草自己的报销单」；财务复核（含标记重复与打印）与支付登记是财务口
# 专属动作，只给财务与总经理/董事长，出纳通过 finance 角色覆盖。
_EXPENSE_BASE_ROLES = set(ROLES) - {"admin"}
_EXPENSE_FINANCE_ROLES = {"admin", "chairman", "gm", "finance"}
_EXPENSE_PAY_ROLES = {"admin", "finance"}


def perms_of(role_code: str) -> list:
    """返回角色权限列表；admin 返回 ['*']。

    审批引擎权限在此集中注入，避免逐个角色改 perm 列表：
    - 所有非 admin 角色默认可查看待办与审批（workflow.view / workflow.approve）
    - workflow.manage（流程定义管理）仅限 _WORKFLOW_MANAGE_ROLES
    """
    r = ROLES.get(role_code)
    if r:
        base = list(r["perm"]) if isinstance(r["perm"], list) else ["*"]
    else:
        db = get_conn()
        row = db.execute("SELECT permissions FROM roles WHERE code=?",
                         (role_code,)).fetchone()
        base = (jload(row["permissions"], []) if row else []) or []
        if not isinstance(base, list):
            base = []
    # 注入审批引擎基础权限
    if role_code != "admin":
        for p in ("workflow.view", "workflow.approve",
                  "expense.view", "expense.edit"):
            if p not in base:
                base.append(p)
    if role_code in _WORKFLOW_MANAGE_ROLES and "workflow.manage" not in base:
        base.append("workflow.manage")
    if role_code in _EXPENSE_FINANCE_ROLES and "expense.finance" not in base:
        base.append("expense.finance")
    if role_code in _EXPENSE_PAY_ROLES and "expense.pay" not in base:
        base.append("expense.pay")
    return base


def has_perm(user: dict, perm: str) -> bool:
    """权限判定。

    精确匹配 + 一个方向的子动作放行：拥有 `x.y` 时允许 `x.y.z`
    （如 orders.production 归 orders 下所有子动作）。

    **不能反向放行**：早期写成「prefix.startswith(p)」导致
    拥有 quotations.edit 的人被判定拥有 quotations.approve，
    结果业务员可以自己审批自己的折扣 —— 这是实打实的越权。
    """
    ps = perms_of(user.get("role", ""))
    if "*" in ps:
        return True
    if perm in ps:
        return True
    # 只允许「已授权的上级」覆盖「未授权的下级」：
    #   p = x.y，要求 perm = x.y.z
    if "." in perm:
        parent = perm.rsplit(".", 1)[0]
        return parent in ps
    return False


def require(user: dict, perm: str) -> None:
    if not has_perm(user, perm):
        raise HTTPException(403, f"没有「{perm}」权限，请联系管理员")


def scope_of(user: dict) -> str:
    """数据范围：admin 恒为 all，其余按角色定义。"""
    role = user.get("role", "")
    if role == "admin":
        return "all"
    r = ROLES.get(role)
    if r:
        return r["scope"]
    db = get_conn()
    row = db.execute("SELECT scope FROM roles WHERE code=?", (role,)).fetchone()
    return (row["scope"] if row else "self")


def scope_clause(user: dict, owner_col: str = "owner_id",
                 dept_col: str = "dept_id", alias: str = "") -> tuple[str, list]:
    """生成数据范围过滤的 SQL 片段。

    返回 (sql_fragment, params)；空片段表示不限制。

    alias —— 列表查询常有 JOIN（如 inquiries JOIN users），
    此时 owner_id 会歧义，必须传表别名前缀（如 "i"）。
    """
    scope = scope_of(user)
    if scope == "all":
        return "", []
    prefix = f"{alias}." if alias else ""
    uid = user.get("id")
    dept = user.get("dept_id")
    if scope == "dept" and dept:
        return (f" AND ({prefix}{owner_col}=? OR {prefix}{dept_col}=?)",
                [uid, dept])
    return f" AND {prefix}{owner_col}=?", [uid]


def visible(user: dict, row) -> bool:
    """单行可见性判断（用于详情接口二次校验）。

    row 可以是 dict 或 sqlite3.Row —— 后者没有 .get()，需先转 dict。
    """
    d = dict(row) if not isinstance(row, dict) else row
    scope = scope_of(user)
    if scope == "all":
        return True
    if scope == "dept" and d.get("dept_id"):
        return d.get("dept_id") == user.get("dept_id")
    return d.get("owner_id") == user.get("id")


def current_org_rollups(user: dict) -> dict:
    """用户「当前组织节点」的三轴归属（company_id / factory_id / bu_id）。

    当前组织节点 = users.current_org_unit_id（组织切换器锚点），缺省回落到
    users.org_unit_id。返回 {} 表示用户尚未归属任何组织。
    """
    oid = user.get("current_org_unit_id") or user.get("org_unit_id")
    if not oid:
        return {}
    db = get_conn()
    r = db.execute(
        "SELECT company_id, factory_id, bu_id FROM org_units WHERE id=?",
        (oid,)).fetchone()
    return dict(r) if r else {}


def _axis_subtree_ids(root_id: int | None) -> list:
    """返回以 root_id 为根的组织子树（含自身）的所有 org_unit_id。

    靠 org_units.path 的 LIKE 前缀一次查出来 —— 三轴范围过滤是高频操作，
    必须退化成 `WHERE org_unit_id IN (...)` 而不是递归 join。
    """
    if not root_id:
        return []
    db = get_conn()
    r = db.execute("SELECT path FROM org_units WHERE id=?", (root_id,)).fetchone()
    if not r:
        return []
    rows = db.execute("SELECT id FROM org_units WHERE path LIKE ?",
                     (r["path"] + "%",)).fetchall()
    return [x["id"] for x in rows]


def switchable_org_nodes(user: dict) -> list[dict]:
    """用户可在「组织切换器」里切换到的组织节点（company / factory / bu 类型）。

    董事长 / 管理员：全集团所有 company/factory/bu 节点；
    其余：仅当前公司子树内的 company/factory/bu 节点（看不到别家的厂）。
    返回 [{id, code, name, org_type}, ...]。
    """
    db = get_conn()
    if user.get("role") in ("admin", "chairman"):
        rows = db.execute(
            "SELECT id, code, name, org_type FROM org_units "
            "WHERE org_type IN ('company','factory','bu') AND is_active=1 "
            "ORDER BY org_type, sort_order, id").fetchall()
        return [dict(r) for r in rows]
    rolls = current_org_rollups(user)
    cid = rolls.get("company_id")
    if not cid:
        return []
    comp = db.execute("SELECT path FROM org_units WHERE id=?", (cid,)).fetchone()
    if not comp:
        return []
    rows = db.execute(
        "SELECT id, code, name, org_type FROM org_units "
        "WHERE org_type IN ('company','factory','bu') AND is_active=1 "
        "AND path LIKE ? ORDER BY org_type, sort_order, id",
        (comp["path"] + "%",)).fetchall()
    return [dict(r) for r in rows]


def scope_clause_org(user: dict, org_unit_col: str = "org_unit_id",
                     alias: str = "") -> tuple[str, list]:
    """组织轴专用的数据范围过滤（M004 三轴范围）。

    返回 (sql_fragment, params)。与通用 scope_clause 不同，本函数只认 org_unit_id，
    用于员工档案 / 岗位编制 / 组织树这类天然带组织归属的模块：

        all      —— 不过滤（董事长 / 总部）
        company  —— 当前组织所在公司的整棵子树
        factory  —— 当前组织所在工厂的整棵子树
        bu       —— 当前组织所在事业部的整棵子树
        dept     —— 当前组织节点自身 + 其下级（本部门口径）
        self     —— 仅当前组织节点自身（最严）

    找不到对应轴归属时**保守返回 1=0**（看不到任何数据），绝不裸奔放行。
    """
    scope = scope_of(user)
    if scope == "all":
        return "", []
    prefix = f"{alias}." if alias else ""
    col = f"{prefix}{org_unit_col}"
    rolls = current_org_rollups(user)

    if scope in ("company", "factory", "bu"):
        axis_id = rolls.get(f"{scope}_id")
        ids = _axis_subtree_ids(axis_id)
        if not ids:
            return " AND 1=0", []
        ph = ",".join("?" * len(ids))
        return f" AND {col} IN ({ph})", ids

    # dept / self：以当前组织节点为锚
    oid = user.get("current_org_unit_id") or user.get("org_unit_id")
    if not oid:
        return " AND 1=0", []
    if scope == "dept":
        ids = _axis_subtree_ids(oid)
        if not ids:
            return f" AND {col}=?", [oid]
        ph = ",".join("?" * len(ids))
        return f" AND {col} IN ({ph})", ids
    # self：仅本人所在组织节点
    return f" AND {col}=?", [oid]


def roles_catalog() -> list:
    """给前端下拉用。"""
    db = get_conn()
    out = []
    for code, r in sorted(ROLES.items(), key=lambda x: list(ROLES).index(x[0])):
        out.append({"code": code, "name": r["name"], "scope": r["scope"],
                    "scope_label": SCOPE_LABEL[r["scope"]],
                    "perm_count": (len(r["perm"]) if isinstance(r["perm"], list)
                                   else 999)})
    for row in db.execute("SELECT code,name,scope,permissions FROM roles "
                          "WHERE code NOT IN (%s)"
                          % ",".join("?" * len(ROLES)), tuple(ROLES)):
        p = jload(row["permissions"], [])
        out.append({"code": row["code"], "name": row["name"], "scope": row["scope"],
                    "scope_label": SCOPE_LABEL.get(row["scope"], row["scope"]),
                    "perm_count": len(p) if isinstance(p, list) else 0})
    return out
