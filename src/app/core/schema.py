"""数据库迁移 — 带版本号的顺序迁移。

为什么需要：早期靠 _add_column_if_missing() 手工补列，没有版本管理，
多站点并发升级时无法判断谁升过。现在统一走 schema_version 表。

迁移函数约定：
    def _v001(db): ...   # 版本号从 1 开始，递增
    函数内只做 DDL，不要 commit（由 run_migrations 统一提交）
"""
import re

from ..database import DB_BACKEND, _ensure_wal, _file_lock, _init_lock_path
from .db import get_conn, now, release_conn
from .dialect import dialect, translate_ddl_to_tsql
from .orgutil import recompute_tree


def _exec_ddl(db, sqlite_ddl: str) -> None:
    """执行迁移 DDL，按方言自动分支（M108）。

    - sqlite：原样执行，行为与改造前字节级一致（保 186 路由冒烟基线全绿）。
    - mssql：先经 translate_ddl_to_tsql 把 SQLite DDL 翻成 T-SQL 再执行。
    """
    ddl = translate_ddl_to_tsql(sqlite_ddl) if dialect() == "mssql" else sqlite_ddl
    db.executescript(ddl)

# ==========================================================================
# 迁移函数区 —— 每新增一个版本，在下面追加一个 _vNNN 函数
# ==========================================================================

def _v001(db):
    """组织与身份：部门、用户扩展、角色数据范围。"""
    _exec_ddl(db,"""
CREATE TABLE IF NOT EXISTS departments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'office',   -- office 总部/branch 分公司/factory 工厂
    parent_id INTEGER,
    leader_id INTEGER,
    sort_order INTEGER DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS roles (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'self',   -- all 全部/dept 本部门/self 仅本人
    permissions TEXT NOT NULL DEFAULT '[]',
    sort_order INTEGER DEFAULT 0,
    is_builtin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT,
    module TEXT,
    action TEXT,
    target_type TEXT,
    target_id INTEGER,
    target_label TEXT,
    before_json TEXT,
    after_json TEXT,
    ip TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_target ON audit_logs(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_logs(user_id);
""")


def _v002(db):
    """业务链主表：询盘、订单、采购生产、出货、应收。"""
    _exec_ddl(db,"""
CREATE TABLE IF NOT EXISTS inquiries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inquiry_no TEXT UNIQUE NOT NULL,
    source TEXT DEFAULT 'email',          -- alibaba/email/exhibition/website/referral
    customer_id INTEGER,
    customer_name TEXT,
    contact_name TEXT,
    contact_email TEXT,
    country TEXT,
    subject TEXT,
    received_at TEXT,
    currency TEXT DEFAULT 'USD',
    target_price REAL,
    incoterm TEXT,
    qty_hint INTEGER,
    status TEXT NOT NULL DEFAULT 'new',    -- new/quoted/negotiating/won/lost/archived
    priority TEXT DEFAULT 'normal',        -- low/normal/high/urgent
    owner_id INTEGER,
    dept_id INTEGER,
    remark TEXT,
    quotation_id INTEGER,
    lost_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_inq_status ON inquiries(status);
CREATE INDEX IF NOT EXISTS idx_inq_owner ON inquiries(owner_id);

CREATE TABLE IF NOT EXISTS inquiry_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inquiry_id INTEGER NOT NULL,
    product_id INTEGER,
    category_code TEXT,
    description TEXT,
    spec_text TEXT,
    qty REAL DEFAULT 1,
    unit TEXT DEFAULT 'set',
    target_price REAL,
    remark TEXT
);
CREATE INDEX IF NOT EXISTS idx_inqitem_inq ON inquiry_items(inquiry_id);

CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    biz_type TEXT NOT NULL,
    biz_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT,
    content TEXT,
    user_id INTEGER,
    username TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_act_biz ON activities(biz_type, biz_id);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_no TEXT UNIQUE,
    biz_type TEXT,
    biz_id INTEGER,
    task_type TEXT NOT NULL,               -- followup/confirm/approval/ship/receive
    title TEXT NOT NULL,
    detail TEXT,
    assignee_id INTEGER,
    dept_id INTEGER,
    due_at TEXT,
    priority TEXT DEFAULT 'normal',
    status TEXT NOT NULL DEFAULT 'open',   -- open/done/canceled
    result TEXT,
    created_by INTEGER,
    created_at TEXT NOT NULL,
    done_at TEXT,
    done_by INTEGER
);
CREATE INDEX IF NOT EXISTS idx_task_assignee ON tasks(assignee_id, status);
CREATE INDEX IF NOT EXISTS idx_task_due ON tasks(due_at, status);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT UNIQUE NOT NULL,
    inquiry_id INTEGER,
    quotation_id INTEGER,
    customer_id INTEGER,
    po_number TEXT,                       -- 客户 PO 号
    order_date TEXT,
    delivery_date TEXT,
    currency TEXT DEFAULT 'USD',
    total_amount REAL DEFAULT 0,
    cost_amount REAL DEFAULT 0,
    payment_terms TEXT,
    incoterm TEXT,
    port TEXT,
    status TEXT NOT NULL DEFAULT 'draft',  -- draft/confirming/confirmed/producing/shipped/completed/canceled
    approval_status TEXT DEFAULT 'none',  -- none/pending/approved/rejected
    owner_id INTEGER,
    dept_id INTEGER,
    remark TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_order_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_order_customer ON orders(customer_id);

CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    product_id INTEGER,
    sku TEXT,
    model TEXT,
    name_en TEXT,
    spec_en TEXT,
    qty REAL DEFAULT 1,
    unit_price REAL DEFAULT 0,
    amount REAL DEFAULT 0,
    cost_price REAL DEFAULT 0,
    source TEXT DEFAULT 'own',             -- own 自有产线/out 外协
    production_status TEXT DEFAULT 'pending', -- pending/material/producing/quality/finished
    remark TEXT
);
CREATE INDEX IF NOT EXISTS idx_oitem_order ON order_items(order_id);

CREATE TABLE IF NOT EXISTS purchase_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    po_no TEXT UNIQUE NOT NULL,
    order_id INTEGER,
    supplier_id INTEGER,
    po_date TEXT,
    expected_date TEXT,
    total_amount REAL DEFAULT 0,
    currency TEXT DEFAULT 'CNY',
    status TEXT DEFAULT 'draft',           -- draft/sent/producing/received/closed
    remark TEXT,
    created_by INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_po_order ON purchase_orders(order_id);

CREATE TABLE IF NOT EXISTS purchase_order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    po_id INTEGER NOT NULL,
    product_id INTEGER,
    description TEXT,
    qty REAL DEFAULT 1,
    unit_price REAL DEFAULT 0,
    received_qty REAL DEFAULT 0,
    remark TEXT
);
CREATE INDEX IF NOT EXISTS idx_poitem_po ON purchase_order_items(po_id);

CREATE TABLE IF NOT EXISTS shipments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_no TEXT UNIQUE NOT NULL,
    order_id INTEGER,
    carrier TEXT,
    vessel TEXT,
    tracking_no TEXT,
    bl_no TEXT,                           -- 提单号
    container_no TEXT,
    etd TEXT,
    eta TEXT,
    status TEXT DEFAULT 'planned',         -- planned/shipped/arrived/customs/delivered
    remark TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ship_order ON shipments(order_id);

CREATE TABLE IF NOT EXISTS shipment_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id INTEGER NOT NULL,
    order_item_id INTEGER,
    product_id INTEGER,
    description TEXT,
    qty REAL DEFAULT 1,
    carton_qty INTEGER,
    net_weight REAL,
    gross_weight REAL,
    volume REAL
);
CREATE INDEX IF NOT EXISTS idx_shipitem_ship ON shipment_items(shipment_id);

CREATE TABLE IF NOT EXISTS export_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id INTEGER,
    doc_type TEXT NOT NULL,               -- invoice/packing_list/bill_of_lading/customs/cert
    doc_no TEXT,
    issued_at TEXT,
    file_path TEXT,
    remark TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_expdoc_ship ON export_documents(shipment_id);

CREATE TABLE IF NOT EXISTS receivables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receivable_no TEXT UNIQUE NOT NULL,
    order_id INTEGER,
    invoice_no TEXT,
    amount REAL DEFAULT 0,
    received_amount REAL DEFAULT 0,
    currency TEXT DEFAULT 'USD',
    due_date TEXT,
    status TEXT DEFAULT 'open',            -- open/partial/settled/overdue
    owner_id INTEGER,
    dept_id INTEGER,
    remark TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recv_status ON receivables(status);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receivable_id INTEGER NOT NULL,
    amount REAL NOT NULL,
    currency TEXT DEFAULT 'USD',
    received_at TEXT,
    method TEXT,                          -- T/T/L/C/D/P/其他
    remark TEXT,
    created_by INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pay_recv ON payments(receivable_id);

CREATE TABLE IF NOT EXISTS price_lists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    customer_id INTEGER,
    currency TEXT DEFAULT 'USD',
    min_qty REAL DEFAULT 1,
    max_qty REAL,
    unit_price REAL NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plist_product ON price_lists(product_id);

CREATE TABLE IF NOT EXISTS cost_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    supplier_id INTEGER,
    cost_cny REAL DEFAULT 0,
    qty REAL DEFAULT 1,
    recorded_at TEXT,
    remark TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cost_product ON cost_records(product_id);
""")


def _v003(db):
    """给已有表补列（向后兼容，老库升级不丢数据）。"""
    alters = [
        # users：部门 + 强制改密 + 登录锁定
        ("users", "dept_id", "INTEGER"),
        ("users", "phone", "TEXT"),
        ("users", "must_change_pw", "INTEGER NOT NULL DEFAULT 0"),
        ("users", "login_fail_count", "INTEGER NOT NULL DEFAULT 0"),
        ("users", "locked_until", "TEXT"),
        ("users", "last_login_at", "TEXT"),
        # customers：分级 / 来源 / 归属
        ("customers", "level", "TEXT DEFAULT 'B'"),
        ("customers", "source", "TEXT"),
        ("customers", "owner_id", "INTEGER"),
        ("customers", "dept_id", "INTEGER"),
        ("customers", "tags", "TEXT"),
        ("customers", "credit_limit", "REAL DEFAULT 0"),
        # suppliers：评级 / 账期 / 交期
        ("suppliers", "level", "TEXT DEFAULT 'B'"),
        ("suppliers", "payment_terms", "TEXT"),
        ("suppliers", "lead_time_days", "INTEGER"),
        # quotations：溯源 / 毛利 / 版本 / 并发控制
        ("quotations", "inquiry_id", "INTEGER"),
        ("quotations", "version", "INTEGER NOT NULL DEFAULT 1"),
        ("quotations", "valid_until", "TEXT"),
        ("quotations", "cost_amount", "REAL DEFAULT 0"),
        ("quotations", "gross_profit", "REAL DEFAULT 0"),
        ("quotations", "updated_by", "INTEGER"),
        # documents：可挂到任意单据
        ("documents", "ref_type", "TEXT"),
        ("documents", "ref_id", "INTEGER"),
    ]
    for table, column, ddl in alters:
        _add_column(db, table, column, ddl)

    # 并发控制：所有业务主表加 version + updated_by
    for table in ("quotations", "inquiries", "orders", "customers", "products"):
        _add_column(db, table, "version", "INTEGER NOT NULL DEFAULT 1")
        _add_column(db, table, "updated_by", "INTEGER")

    _exec_ddl(db,"""
CREATE INDEX IF NOT EXISTS idx_inq_customer ON inquiries(customer_id);
CREATE INDEX IF NOT EXISTS idx_order_owner ON orders(owner_id);
CREATE INDEX IF NOT EXISTS idx_recv_due ON receivables(due_date);
""")


def _v004(db):
    """补齐并发控制列。

    教训：迁移一旦发布就不可修改 —— 已应用的版本不会再跑。
    本次在 _v003 里加 updated_by 时，测试库已经应用过 _v003，
    导致新库有列、老库没有。正确做法是新增版本补列。
    """
    for table in ("quotations", "inquiries", "orders", "customers", "products"):
        _add_column(db, table, "version", "INTEGER NOT NULL DEFAULT 1")
        _add_column(db, table, "updated_by", "INTEGER")


def _v005(db):
    """员工工号（系统强制生成）+ 生命周期；部门重组为四大分组。

    工号规则：
    - 格式 WV-EMP-0001，由系统按序号生成，用户不可自选、不可编辑
    - 离职后工号保留不回收（序号取 MAX，离职的人还在表里，序号自然不倒退）
    - emp_status 生命周期：probation 试用期 / active 在职 /
      suspended 停职 / left 离职 / retired 退休
      非 active/probation 的账号自动禁止登录（is_active=0）

    部门结构（传统外贸制造企业架构）：
        人事行政 ─┬─ 行政人事部
                  └─ 总部
        财务部门
        生产车间 ─┬─ 技术部 / 采购部 / 物流部 / 工厂
        销售部门 ─┬─ 分公司
    """
    # ---- 1. 用户表：工号与生命周期 ----
    _add_column(db, "users", "emp_no", "TEXT")
    _add_column(db, "users", "emp_status", "TEXT NOT NULL DEFAULT 'active'")
    _add_column(db, "users", "join_date", "TEXT")
    _add_column(db, "users", "leave_date", "TEXT")
    _add_column(db, "users", "remark", "TEXT")

    # 已有用户补工号与状态
    rows = db.execute("SELECT id FROM users WHERE emp_no IS NULL OR emp_no=''").fetchall()
    n = db.execute("SELECT IFNULL(MAX(CAST(SUBSTR(emp_no,8) AS INTEGER)),0) m "
                   "FROM users WHERE emp_no LIKE 'WV-EMP-%'").fetchone()["m"] or 0
    for r in rows:
        n += 1
        db.execute("UPDATE users SET emp_no=? WHERE id=?",
                   (f"WV-EMP-{n:04d}", r["id"]))

    # ---- 2. 部门：四个顶层分组 ----
    # 只调整**已存在**的分组，不在这里插入。
    # 原因：插入会让 seed_org() 的「表空才灌种子」判断失效，
    # 导致新装系统漏掉总部/技术部等子部门。
    # 分组数据归 seed_org()（幂等 upsert），迁移只管结构与归属修正。
    groups = [("SALES", "销售部门", 1), ("PROD", "生产车间", 2),
              ("FIN", "财务部门", 3), ("HR", "人事行政", 4)]
    for code, name, order in groups:
        r = db.execute("SELECT id FROM departments WHERE code=?", (code,)).fetchone()
        if r:
            db.execute("UPDATE departments SET name=?, kind='group', parent_id=NULL,"
                       " sort_order=?, is_active=1 WHERE id=?",
                       (name, order, r["id"]))

    def gid(code: str):
        r = db.execute("SELECT id FROM departments WHERE code=?", (code,)).fetchone()
        return r["id"] if r else None

    hr = gid("HR")
    sales = gid("SALES")
    prod = gid("PROD")

    # 行政人事部 —— 人事行政下的执行部门
    if not db.execute("SELECT 1 FROM departments WHERE code='AD'").fetchone():
        db.execute("INSERT INTO departments(code,name,kind,parent_id,sort_order,"
                   "is_active,created_at) VALUES('AD','行政人事部','office',?,5,1,?)",
                   (hr, now()))

    # 现有部门挂到所属分组下（总部归人事行政，分公司归销售部门，其余归生产车间）
    reparent = [
        ("HQ", hr), ("AD", hr),
        ("BRANCH", sales),
        ("TECH", prod), ("PUR", prod), ("LOG", prod), ("FACTORY", prod),
    ]
    for code, parent in reparent:
        if parent is None:
            continue
        db.execute("UPDATE departments SET parent_id=? WHERE code=? AND id!=?",
                   (parent, code, parent))


def _v006(db):
    """修数据：admin 归属部门。

    老库的 admin 是在 departments 表建出来之前创建的，dept_id 为空 ——
    于是「按部门统计人数」永远少一个人，admin 也看不到自己的部门。
    迁移只能新增版本，不能改已发布的 _vNNN。
    """
    r = db.execute("SELECT id FROM departments WHERE code='HQ'").fetchone()
    if not r:
        return
    db.execute("UPDATE users SET dept_id=? WHERE dept_id IS NULL AND role='admin'",
               (r["id"],))
    # 管理员账号也要有工号（早期创建的账号可能没补上）
    rows = db.execute("SELECT id FROM users WHERE emp_no IS NULL OR emp_no=''").fetchall()
    n = db.execute("SELECT IFNULL(MAX(CAST(SUBSTR(emp_no,8) AS INTEGER)),0) m "
                   "FROM users WHERE emp_no LIKE 'WV-EMP-%'").fetchone()["m"] or 0
    for row in rows:
        n += 1
        db.execute("UPDATE users SET emp_no=? WHERE id=?",
                   (f"WV-EMP-{n:04d}", row["id"]))


def _add_column(db, table: str, column: str, ddl: str) -> None:
    """加列前先查是否已存在 —— 所有 IF NOT EXISTS 的 SQLite 版本都不支持。"""
    cols = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
    if column in cols:
        return
    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


# ==========================================================================
# 迁移执行器
# ==========================================================================
def _discover() -> list:
    """按版本号收集所有 _vNNN 函数。

    注意必须用 globals()：在函数里 dir() 只返回局部名。
    """
    fns = []
    for name, obj in list(globals().items()):
        m = re.fullmatch(r"_v(\d{3})", name)
        if m and callable(obj):
            fns.append((int(m.group(1)), obj))
    return sorted(fns, key=lambda x: x[0])


def current_version(db) -> int:
    db.execute("CREATE TABLE IF NOT EXISTS schema_version "
               "(version INTEGER PRIMARY KEY, applied_at TEXT)")
    row = db.execute("SELECT IFNULL(MAX(version),0) v FROM schema_version").fetchone()
    return int(row["v"])


def _v007(db):
    """报表与打印：水单/票据影像归档（用于报销排版打印）。"""
    _exec_ddl(db,"""
CREATE TABLE IF NOT EXISTS report_slips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    size INTEGER,
    memo TEXT,
    mask_json TEXT,                 -- JSON 数组：脱敏矩形 [{x,y,w,h}]，坐标归一化 0~1
    created_by INTEGER,
    created_at TEXT NOT NULL,
    deleted_at TEXT
);
""")


def _v008(db):
    """M001 组织树重构：org_units 多类型无限层级组织树。

    为什么不再用 departments 单表：现实集团是**三轴**的 ——
    法人公司 / 工厂 / 事业部 / 办公地 是三条不同的归属线，
    一个部门可能同时属于「一厂 + 国内事业部 + 广州公司」。
    单表 + parent_id 只能表达一条线，撑不住多工厂与事业部制。

    兼容策略（渐进，不推倒重来）：
    - 旧 departments 数据**保留 id** 复制进 org_units，
      这样 users.dept_id 与 org_units.id 天然对齐，旧页面零改动继续可用
    - departments 进入冻结期，由组织树单向回写（structure 模块负责），
      待 M004 数据范围改造完成后废弃
    """
    _exec_ddl(db,"""
CREATE TABLE IF NOT EXISTS org_units (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    name_en TEXT,
    org_type TEXT NOT NULL DEFAULT 'dept',
    parent_id INTEGER,
    leader_id INTEGER,                  -- 负责人（users.id）
    cost_center TEXT,                   -- 成本中心（费用归集与预算控制的落点）
    company_id INTEGER,                 -- 所属法人公司（树推导，冗余）
    factory_id INTEGER,                 -- 所属工厂（树推导，冗余）
    bu_id INTEGER,                      -- 所属事业部（树推导，冗余）
    path TEXT NOT NULL DEFAULT '/',     -- /1/4/9/ 冗余：子树查询一次 LIKE 搞定
    level INTEGER NOT NULL DEFAULT 0,   -- 层级深度（根=0）
    sort_order INTEGER NOT NULL DEFAULT 0,
    address TEXT,                       -- 办公/生产地址（多工厂各自不同）
    phone TEXT,
    tax_no TEXT,                        -- 税号（仅法人公司用）
    remark TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_orgunit_parent ON org_units(parent_id);
CREATE INDEX IF NOT EXISTS idx_orgunit_type ON org_units(org_type);
CREATE INDEX IF NOT EXISTS idx_orgunit_path ON org_units(path);
CREATE INDEX IF NOT EXISTS idx_orgunit_company ON org_units(company_id);
""")

    # ---- 1. 旧 departments 数据迁进来（保留 id） ----
    # 旧 kind: group/office/branch/factory，其余一律视为 dept
    db.execute("""
INSERT OR IGNORE INTO org_units(id,code,name,org_type,parent_id,leader_id,
    sort_order,is_active,created_at,updated_at)
SELECT id, code, name,
       CASE kind WHEN 'group' THEN 'group'
                 WHEN 'office' THEN 'office'
                 WHEN 'branch' THEN 'branch'
                 WHEN 'factory' THEN 'factory'
                 ELSE 'dept' END,
       parent_id, leader_id, sort_order, is_active, created_at, ?
FROM departments
""", (now(),))

    # ---- 2. 人员加组织归属列（未来模块一律用 org_unit_id） ----
    _add_column(db, "users", "org_unit_id", "INTEGER")
    db.execute("UPDATE users SET org_unit_id=dept_id "
               "WHERE org_unit_id IS NULL AND dept_id IS NOT NULL "
               "AND EXISTS(SELECT 1 FROM org_units ou WHERE ou.id=users.dept_id)")

    # ---- 3. 重算 path / level / 归属三轴 ----
    recompute_tree(db, table="org_units",
                   rollups={"company_id": ["company"], "factory_id": ["factory"],
                            "bu_id": ["bu"]})


def _v009(db):
    """M002 岗位职级体系：岗位 / 职级 / 编制 / 任职关系。

    三轴模型的第二轴。只有组织树（M001）时系统只能回答「人在哪个部门」，
    回答不了「这个部门该设几个岗、每岗几人、现在缺多少人、谁向谁汇报」——
    那是编制（positions）的活儿。四张表的关系：

        job_titles  岗位   —— 干什么活（研发工程师 / 会计 / 操作工）
        job_grades  职级   —— 干到什么水平（M/P/T/O 四序列）
        positions   编制   —— 组织 × 岗位 × 职级 × 编制数 × 汇报上级
        position_assignments 任职 —— 谁在这个编制上（主岗/兼岗、占比、起止）

    「编制 vs 在岗」= positions.headcount − 在职任职人数，看板口径见
    modules/position.py 的 _occupied()。

    任职关系刻意做成独立表而不是 users 加 position_id 外键：
    - 外键方案一个人只能占一个岗，兼岗（小厂厂长兼采购）表达不了
      —— 兼岗恰恰是国内制造企业的常态；
    - users 是身份模块的底盘，动它的列清单等于让 15 个已有模块的
      INSERT / SELECT 一起返工，与「不破坏现有模块」的取舍相悖；
    - 独立表自带 start_date / end_date，M003「入职/调岗/晋升/离职
      全留痕」直接复用，不必到时候推倒重来。
    """
    _exec_ddl(db,"""
CREATE TABLE IF NOT EXISTS job_titles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    name_en TEXT,
    family TEXT NOT NULL DEFAULT 'P',        -- 默认职级序列 M/P/T/O
    category TEXT,                            -- 岗位类别 mgmt/admin/tech/sales/prod/op/support
    level_hint INTEGER NOT NULL DEFAULT 0,    -- 建议层级（汇报线排序与职级映射参考，0=不限）
    duty TEXT,                                -- 岗位职责
    require_json TEXT,                        -- 任职要求（JSON 数组）
    sort_order INTEGER NOT NULL DEFAULT 0,
    remark TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobtitle_family ON job_titles(family);
CREATE INDEX IF NOT EXISTS idx_jobtitle_cat ON job_titles(category);
CREATE INDEX IF NOT EXISTS idx_jobtitle_active ON job_titles(is_active);

CREATE TABLE IF NOT EXISTS job_grades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,               -- M1 / P3 / T5 / O2
    family TEXT NOT NULL,                    -- M 管理 / P 专业 / T 技术 / O 操作
    level INTEGER NOT NULL,                  -- 序列内层级，1 起
    name TEXT NOT NULL,                      -- 经理级 / 高级工程师 / 技师
    name_en TEXT,
    salary_min REAL,                         -- 薪资带宽下限（REAL + currency）
    salary_max REAL,                         -- 薪资带宽上限
    currency TEXT NOT NULL DEFAULT 'CNY',
    duty TEXT,                               -- 该职级的通用能力要求
    sort_order INTEGER NOT NULL DEFAULT 0,
    remark TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobgrade_family ON job_grades(family, level);
CREATE INDEX IF NOT EXISTS idx_jobgrade_active ON job_grades(is_active);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,               -- POS-HQ-001
    name TEXT NOT NULL,                      -- 组织 · 岗位 · 职级（写入时自动生成）
    org_unit_id INTEGER NOT NULL,            -- 所属组织（org_units.id）
    job_title_id INTEGER,                    -- 岗位
    job_grade_id INTEGER,                    -- 职级
    headcount REAL NOT NULL DEFAULT 1,       -- 编制人数（REAL 兼容 0.5 编制）
    parent_position_id INTEGER,              -- 汇报上级编制（position 自引用）
    status TEXT NOT NULL DEFAULT 'active',   -- open 待补 / active 在编 / frozen 冻结 / closed 撤销
    is_key INTEGER NOT NULL DEFAULT 0,       -- 关键岗位（招聘优先级与继任者计划用）
    work_place TEXT,                         -- 工作地点（多工厂各自不同）
    duty TEXT,
    require_json TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    remark TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_position_org ON positions(org_unit_id);
CREATE INDEX IF NOT EXISTS idx_position_title ON positions(job_title_id);
CREATE INDEX IF NOT EXISTS idx_position_grade ON positions(job_grade_id);
CREATE INDEX IF NOT EXISTS idx_position_parent ON positions(parent_position_id);
CREATE INDEX IF NOT EXISTS idx_position_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_position_active ON positions(is_active);

CREATE TABLE IF NOT EXISTS position_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 1,   -- 1 主岗 / 0 兼岗
    ratio REAL NOT NULL DEFAULT 1.0,         -- 任职占比 0~1（兼岗按投入折算）
    start_date TEXT,
    end_date TEXT,
    remark TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,    -- 0 = 任职结束（软删，留历史）
    created_at TEXT NOT NULL,
    updated_at TEXT
);
-- 刻意不加 (position_id,user_id) 的唯一索引：任职关系是软删的，
-- 同一个人「调走后再调回同一岗位」会留下两条历史记录，唯一索引会让第二条写不进去。
-- 「同一编制不能重复挂同一个人」由模块层在写入前校验 is_active=1 的记录。
CREATE INDEX IF NOT EXISTS idx_posassign_pair ON position_assignments(position_id, user_id);
CREATE INDEX IF NOT EXISTS idx_posassign_user ON position_assignments(user_id);
CREATE INDEX IF NOT EXISTS idx_posassign_active ON position_assignments(is_active);
""")


def _v010(db):
    """M003 员工档案与异动：人事档案 + 全生命周期异动流水。

    三轴模型的第三轴（人员轴）的「人事属性」层。身份模块 users 只管登录与
    生命周期状态（probation/active/left/retired），本迁移补上 HR 才关心的
    纸质档案（证件 / 合同 / 社保 / 银行卡 / 紧急联系人）与全留痕的异动流水。

    异动流水 employee_events 刻意**复用 M002 的 position_assignments** 作为任职
    事实来源：入职 / 调岗 / 晋升 写事件的同时同步 position_assignments 的起止与
    主岗归属，离职 / 退休 则结束所有在职任职 —— 一套任职关系、两个视角，
    M003 不必再造一份「任职历史表」，也避免了两套数据对不齐。

    敏感字段（身份证 id_card / 银行卡 bank_account）单列权限点
    employee.sensitive.view，与 employee.view（普通档案）分层：
    普通 HR 看得到合同与紧急联系人，但身份证号、银行卡号只有薪资/人事负责人
    可见，符合「成本与隐私类字段单列权限」的架构取向（见 00-ARCHITECTURE 第四节）。
    """
    _exec_ddl(db,"""
CREATE TABLE IF NOT EXISTS employee_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    id_card TEXT,                            -- 身份证号（敏感，需 employee.sensitive.view）
    contract_no TEXT,                        -- 合同编号
    contract_start TEXT,                     -- 合同起始
    contract_end TEXT,                       -- 合同终止
    social_no TEXT,                          -- 社保号
    bank_name TEXT,                          -- 开户行
    bank_account TEXT,                       -- 银行卡号（敏感）
    emergency_contact TEXT,                  -- 紧急联系人
    emergency_phone TEXT,                    -- 紧急联系人电话
    hire_date TEXT,                          -- 入职日期
    id_scan_path TEXT,                       -- 证件扫描文件路径
    is_active INTEGER NOT NULL DEFAULT 1,    -- 软删除：档案注销（历史可追溯）
    created_at TEXT NOT NULL,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_empprofile_user ON employee_profiles(user_id, is_active);

CREATE TABLE IF NOT EXISTS employee_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,                -- onboard 入职 / regular 转正 / transfer 调岗 / promote 晋升 / leave 离职 / retire 退休
    from_position_id INTEGER,                -- 异动前岗位（调岗/晋升）
    to_position_id INTEGER,                  -- 异动后岗位（入职/调岗/晋升）
    event_date TEXT,                         -- 发生日期
    remark TEXT,
    operator TEXT,                           -- 操作人（username）
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_empevent_user ON employee_events(user_id, created_at);
""")


def _v011(db):
    """M004 数据范围扩展：用户在三轴上的组织归属 + 当前组织切换锚点。

    为什么需要：M001 把组织拆成三轴（公司 / 工厂 / 事业部），但用户与业务数据
    只挂了一个 `org_unit_id`，「数据范围」只能做到 all / dept / self 三档。
    多工厂 + 事业部制下，董事长要看全集团、总经理只看本公司、厂长只看本厂、
    事业部总经理只看本事业部 —— 这要求**用户与数据都带三轴归属**，过滤时
    退化成 `WHERE factory_id=?` 才扛得住，运行时递归子树撑不住高频查询。

    - `users` 加 `company_id` / `factory_id` / `bu_id`：从 `org_unit_id` 的组织树
      冗余三轴（与 org_units 同口径，组织树重算时一并更新）；
    - `users` 加 `current_org_unit_id`：**组织切换器**的锚点 —— 用户当前「正在看 /
      正在操作」的组织节点；切换组织后，company / factory / bu 三档范围的口径
      随之改变（如厂长在 A 厂时只看 A 厂，切到 B 厂只看 B 厂）。默认等于自己的
      `org_unit_id`，管理员可在切换器里改。
    - 旧表（orders / inquiries 等）不动：它们的 all / dept / self 口径保持不变，
      三轴范围只在新 HR/组织类模块（员工档案 / 岗位编制 / 组织树）上生效，见
      `core/permissions.py` 的 `scope_clause_org`。
    """
    _add_column(db, "users", "company_id", "INTEGER")
    _add_column(db, "users", "factory_id", "INTEGER")
    _add_column(db, "users", "bu_id", "INTEGER")
    _add_column(db, "users", "current_org_unit_id", "INTEGER")
    # 从 org_unit_id 的组织树冗余三轴回填（组织树已算好 company/factory/bu）
    db.execute("""
    UPDATE users
       SET company_id = (SELECT company_id FROM org_units WHERE id=users.org_unit_id),
           factory_id = (SELECT factory_id FROM org_units WHERE id=users.org_unit_id),
           bu_id       = (SELECT bu_id FROM org_units WHERE id=users.org_unit_id),
           current_org_unit_id = org_unit_id
     WHERE org_unit_id IS NOT NULL
       AND (company_id IS NULL OR current_org_unit_id IS NULL)
    """)


def _v012(db):
    """M005 审批引擎：流程定义 → 实例 → 任务 → 回调；会签/或签/条件分支/转办/加签。

    为什么需要：请假/报销/用章/采购/报价……所有「申请→审批」类单据迟早都要走流程，
    各自写审批逻辑必然腐烂。统一引擎后，业务单据只负责「提交表单 + 注册回调」，
    流程怎么走、谁批、怎么签全部交给本表与 core/workflow.py。

    - `workflow_defs`    流程定义（节点/边/条件，JSON 存储，管理员可改）
    - `workflow_instances` 流程实例（谁发起、关联哪张业务单、当前节点、状态）
    - `workflow_tasks`   审批任务（一条审批节点对每个审批人生成一条任务；会签/或签、
                          转办、加签都作用在任务上）
    - `workflow_logs`    实例级流水（替代散落各处的「审批记录」表）
    - `leave_requests`   请假申请示例业务单 —— 端到端演示「提交→审批→回调改状态」整条链
    """
    _exec_ddl(db,"""
    CREATE TABLE IF NOT EXISTS workflow_defs (
        id           INTEGER PRIMARY KEY,
        code         TEXT NOT NULL UNIQUE,
        name         TEXT NOT NULL,
        description  TEXT,
        definition   TEXT NOT NULL,
        is_active    INTEGER NOT NULL DEFAULT 1,
        created_at   TEXT NOT NULL,
        updated_at   TEXT
    );

    CREATE TABLE IF NOT EXISTS workflow_instances (
        id           INTEGER PRIMARY KEY,
        def_id       INTEGER NOT NULL,
        code         TEXT NOT NULL UNIQUE,
        title        TEXT,
        biz_type     TEXT,
        biz_id       INTEGER,
        status       TEXT NOT NULL DEFAULT 'running',
        current_node TEXT,
        form_json    TEXT,
        applicant_id INTEGER,
        started_at   TEXT,
        finished_at  TEXT,
        created_at   TEXT NOT NULL,
        updated_at   TEXT
    );

    CREATE TABLE IF NOT EXISTS workflow_tasks (
        id              INTEGER PRIMARY KEY,
        instance_id    INTEGER NOT NULL,
        node_id        TEXT NOT NULL,
        node_name      TEXT,
        mode           TEXT NOT NULL DEFAULT 'or',
        assignee_id    INTEGER,
        status         TEXT NOT NULL DEFAULT 'pending',
        comment        TEXT,
        added_by_task_id INTEGER,
        transfer_to    INTEGER,
        decided_at     TEXT,
        created_at     TEXT NOT NULL,
        updated_at     TEXT
    );

    CREATE TABLE IF NOT EXISTS workflow_logs (
        id           INTEGER PRIMARY KEY,
        instance_id  INTEGER NOT NULL,
        node_id      TEXT,
        action       TEXT NOT NULL,
        actor_id     INTEGER,
        comment      TEXT,
        created_at   TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS leave_requests (
        id           INTEGER PRIMARY KEY,
        code         TEXT NOT NULL UNIQUE,
        applicant_id INTEGER,
        leave_type   TEXT,
        days         REAL,
        reason       TEXT,
        start_date   TEXT,
        status       TEXT NOT NULL DEFAULT 'pending',
        created_at   TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_wf_inst_def ON workflow_instances(def_id);
    CREATE INDEX IF NOT EXISTS idx_wf_inst_biz ON workflow_instances(biz_type, biz_id);
    CREATE INDEX IF NOT EXISTS idx_wf_inst_status ON workflow_instances(status);
    CREATE INDEX IF NOT EXISTS idx_wf_task_inst ON workflow_tasks(instance_id);
    CREATE INDEX IF NOT EXISTS idx_wf_task_assignee ON workflow_tasks(assignee_id, status);
    CREATE INDEX IF NOT EXISTS idx_wf_log_inst ON workflow_logs(instance_id);
    CREATE INDEX IF NOT EXISTS idx_leave_applicant ON leave_requests(applicant_id);
    """)


def _v013(db):
    """M071 电子报销：报销单 + 费用明细 + 票据查重指纹 + 影像关联。

    为什么需要：员工报销是「每天在做的事」里纸质负担最重的一环 —— 纸质发票 /
    水单要打印、要贴、要签字、要归档。本迁移把报销单与费用明细落到库里，影像
    复用 M109 的 report_slips（data/slips），打印留痕落在报销单上，审批交给
    M005 引擎。

    三张概念的关系：
        expense_reports 报销单 —— 谁报、报哪个期间、共多少钱、现在什么状态
        expense_items   费用明细 —— 每一笔费用（含票据身份信息）
        report_slips    影像（M109 已有，本模块只通过 slip_ids 引用，不重复建表）

    ticket_fingerprint 查重指纹（本模块的核心）：
    按「票据身份强弱」分三级，强身份命中即可认定重复，弱身份只作疑似：
        INV|发票代码|发票号码    增值税票最强（代码+号码全国唯一）
        TRN|交易单号            水单/支付的交易流水号（平台唯一）
        PLT|票据类型|金额|日期   水单无单号时的降级（平台+金额+交易时间）
        GEN|票据类型|金额|日期|商家  兜底（火车票/机票/出租车票等无唯一号票据）
    指纹生成前一律归一化（去空白、英文大写、金额两位小数、日期 YYYY-MM-DD），
    否则同一张票因「空格/全角/小数点位数」差异就漏检。

    查重策略刻意分两档（业务取舍，见 modules/expense.py 的 _detect_dups）：
        - 单据内重复 → 硬拦截（自己单子里出现同一张票必是操作失误）
        - 跨单据重复 → 只标记疑似，不拦截（一张票被两个人报销也可能是合理的
          拆分/垫付场景，硬拦会误伤且财务失去裁量权）

    dup_status 三态 none / suspected / confirmed_ok 与 dup_of（指向重复来源明细）
    让财务能在复核台里「看到 → 核实 → 销案」，而不是让系统自作主张。
    """
    _exec_ddl(db,"""
    CREATE TABLE IF NOT EXISTS expense_reports (
        id             INTEGER PRIMARY KEY,
        code           TEXT NOT NULL UNIQUE,
        applicant_id   INTEGER NOT NULL,
        org_unit_id    INTEGER,
        period_from    TEXT,
        period_to      TEXT,
        total_amount   REAL NOT NULL DEFAULT 0,
        status         TEXT NOT NULL DEFAULT 'draft',
        reason         TEXT,
        submitted_at   TEXT,
        paid_at        TEXT,
        paid_by        INTEGER,
        print_count    INTEGER NOT NULL DEFAULT 0,
        last_print_by  INTEGER,
        last_print_at  TEXT,
        is_active      INTEGER NOT NULL DEFAULT 1,
        created_at     TEXT NOT NULL,
        updated_at     TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_exp_rep_applicant ON expense_reports(applicant_id);
    CREATE INDEX IF NOT EXISTS idx_exp_rep_status ON expense_reports(status);
    CREATE INDEX IF NOT EXISTS idx_exp_rep_org ON expense_reports(org_unit_id);
    CREATE INDEX IF NOT EXISTS idx_exp_rep_active ON expense_reports(is_active);

    CREATE TABLE IF NOT EXISTS expense_items (
        id                 INTEGER PRIMARY KEY,
        report_id          INTEGER NOT NULL,
        category           TEXT NOT NULL DEFAULT 'other',
        occur_date         TEXT,
        amount             REAL NOT NULL DEFAULT 0,
        ticket_type        TEXT NOT NULL DEFAULT 'none',
        invoice_code       TEXT,
        invoice_no         TEXT,
        trans_no           TEXT,
        merchant           TEXT,
        title              TEXT,
        slip_ids           TEXT,
        ticket_fingerprint TEXT,
        dup_status         TEXT NOT NULL DEFAULT 'none',
        dup_of             INTEGER,
        remark             TEXT,
        sort_order         INTEGER NOT NULL DEFAULT 0,
        is_active          INTEGER NOT NULL DEFAULT 1,
        created_at         TEXT NOT NULL,
        updated_at         TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_exp_item_report ON expense_items(report_id);
    CREATE INDEX IF NOT EXISTS idx_exp_item_fp ON expense_items(ticket_fingerprint);
    CREATE INDEX IF NOT EXISTS idx_exp_item_dup ON expense_items(dup_status);
    CREATE INDEX IF NOT EXISTS idx_exp_item_active ON expense_items(is_active);
    """)


def _v014(db):
    """M006 工号前缀纠正：把旧迁移（_v005/_v006）写入的 `WG-EMP-` 工号统一改为 `WV-EMP-`。

    迁移号分配（团队负责人拍板，docs/09 §1.5）：`_v014` 归 **M006 独占**；M113 不占迁移号
    （它靠删库重建），其后一律从 `_v015` 起，后来者不得抢号。

    为什么新增纠正迁移而不是直接改旧迁移：
    项目公约「迁移只追加、不改已发布」；且 M113 删库重建时迁移链会重跑，若直接改旧迁移，
    老库升级路径与全新库路径会分叉（老库拿旧前缀、新版拿新前缀），而迁移内容本身还被
    build_fingerprint() 的归属举证依赖。新增纠正迁移让两条路径最终都收敛到 `WV-EMP-`，
    旧迁移一字未改。

    幂等：`WHERE emp_no LIKE 'WG-EMP-%'` 只命中未纠正的行，重复执行影响行数为 0。
    偏移量 8 已实测验证（非推算）：`WG-EMP-` 前缀是 7 个字符（W1 G2 -3 E4 M5 P6 -7），
    序号从第 8 位开始。`substr(emp_no, 8)` 得 `WV-EMP-0001`；若误用 9 会把首位数字吃掉。
    """
    db.execute(
        "UPDATE users SET emp_no = 'WV-EMP-' || substr(emp_no, 8) "
        "WHERE emp_no LIKE 'WG-EMP-%'"
    )


def run_migrations() -> list[int]:
    """执行所有未应用的迁移，返回本次应用的版本号列表。

    M108 方言感知：DDL 执行经 _exec_ddl 按 dialect() 分支。
    SQLite 路径与改造前字节级一致；mssql 路径下 DDL 会被翻译成 T-SQL。
    业务库连接/查询的方言移植不在此函数范围，见工具审计清单的「剩余模块」。
    """
    print(f"[schema] run_migrations dialect={dialect()}")
    with _file_lock(_init_lock_path()):
        db = get_conn()
        _ensure_wal(db)
        cur = current_version(db)
        applied = []
        for ver, fn in _discover():
            if ver <= cur:
                continue
            fn(db)
            db.execute("INSERT OR REPLACE INTO schema_version(version,applied_at) VALUES(?,?)",
                       (ver, now()))
            db.commit()
            applied.append(ver)
        release_conn()
        return applied
