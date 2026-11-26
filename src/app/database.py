"""数据库模型 — 全部使用 SQLite 本地存储，零外部依赖。"""
import json
import os
import sqlite3
import threading
import time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("WV_DB", os.path.join(BASE_DIR, "data", "wv.db"))
# 数据库后端：sqlite（默认，零依赖）或 mssql（实验性，需完成 M108 查询移植）。
# 部署时在 detect_env / setup 中可选择；默认 sqlite 保证开箱即用。
DB_BACKEND = os.environ.get("WV_DB_BACKEND", "sqlite").lower()

DEFAULT_COMPANY = {
    "name_cn": "菲力克斯科技（广州）有限公司",
    "name_en": "Felix Technology (Guangzhou) Co., Ltd.",
    "brand": "Felixv",
    "address_cn": "广州市黄埔区科学城揽月路8号",
    "phone": "020 8888 6666",
    "hotline": "400 888 6666",
    "website": "www.example.com",
    "intro_cn": "亚太地区领先的专业烘焙设备生产制造商，深耕行业十余年，专注于国际级标准的烘焙设备、食品机械、搅拌机、醒发箱等系列烘焙设备，并提供行业解决方案。",
    "intro_en": "Leading professional baking equipment manufacturer in Asia-Pacific, focused on international standard baking equipment, food machinery, mixers and proofing cabinets.",
    "founded": "2014-03-25",
    "capital": "CNY 20,200,000",
    "employees": 104,
    "factory_area": "30,000+ m2 across 4 plants in Guangzhou",
    "markets": "Products exported to 100+ countries/regions; domestic sales network covering 30 provinces",
    "honors": [
        "国家高新技术企业 National High-Tech Enterprise",
        "广东省创新型 SMEs 广东省创新型中小企业",
        "广东省守合同重信用企业",
        "科技型中小企业",
        "专精特新中小企业",
        "广州市「两高四新」企业",
        "A级纳税企业(2021)",
        "广州市白云区进出口商会常务理事单位",
    ],
    "certs": [
        {"code": "CVC", "name_cn": "CVC 食品接触安全认证", "name_en": "CVC Food Contact Safety Certificate"},
        {"code": "SC", "name_cn": "全国工业产品生产许可证", "name_en": "China Industrial Product Production License (SC)"},
        {"code": "ISO9001", "name_cn": "ISO 9001 质量管理体系认证", "name_en": "ISO 9001 Quality Management System"},
        {"code": "CE", "name_cn": "CE 认证", "name_en": "CE Certificate"},
        {"code": "FDA", "name_cn": "FDA 食品接触材料符合性声明", "name_en": "FDA Food Contact Compliance Declaration"},
        {"code": "ETL", "name_cn": "ETL 列名认证", "name_en": "ETL Listed Certification"},
    ],
}


def _add_column_if_missing(db: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """向后兼容：老库缺少列时补上（幂等）。"""
    existing = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        try:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        except sqlite3.OperationalError:
            pass


_local = threading.local()


_wal_ready = False


def _apply_pragmas(db: sqlite3.Connection) -> None:
    """应用连接级 PRAGMA。

    两处关键设置，都是多进程/多用户部署踩过的坑：

    1) journal_mode=WAL 是「写」操作（要建 -shm / -wal 文件）。库刚创建、
       多进程同时首次连接时并发切�� WAL 会抛 "readonly database"，
       因此由 init_db() 在跨进程锁内统一设置一次，后续连接自然继承
       （journal_mode 是库级持久属性）。

    2) 连接不再按请求反复开关。Windows 上 WAL 的每次 close() 会触发
       checkpoint，多进程频繁开关连接会互相抢 checkpoint，导致写入报
       "attempt to write a readonly database"。改为线程级长连接 +
       句柄泄漏时自愈重建（见 get_conn / release_conn）。
    """
    db.execute("PRAGMA busy_timeout=30000")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA foreign_keys=ON")


def get_conn() -> sqlite3.Connection:
    """返回当前工作线程的数据库连接。

    后端由 DB_BACKEND 决定：sqlite（默认）或 mssql（实验性）。
    线程级长连接：同一线程反复复用，避免 close() 触发的 WAL checkpoint 竞争。
    若连接已失效（句柄泄漏、库被替换等），检测到后自动重建。
    """
    if DB_BACKEND == "mssql":
        return _mssql_conn()
    db = getattr(_local, "conn", None)
    if db is not None:
        try:
            db.execute("SELECT 1")
            return db
        except sqlite3.Error:
            try:
                db.close()
            except sqlite3.Error:
                pass
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=30.0, check_same_thread=False)
    db.row_factory = sqlite3.Row
    _apply_pragmas(db)
    _local.conn = db
    return db


def release_conn() -> None:
    """请求结束时调用。

    早期实现在这里 close() 连接以防句柄泄漏，但这会踩 Windows 上 WAL 的
    checkpoint 竞争：多进程频繁开关连接时，写入会抛
    "attempt to write a readonly database"。

    现在改为「回滚未提交事务后保留连接」，让线程复用长连接。
    若连接真的失效，get_conn() 里的 SELECT 1 自检会自动重建。
    """
    db = getattr(_local, "conn", None)
    if db is None:
        return
    try:
        if db.in_transaction:
            db.rollback()
    except sqlite3.Error:
        # 连接已损坏：丢弃，下次 get_conn() 重建
        _local.conn = None
        try:
            db.close()
        except sqlite3.Error:
            pass


class db_session:
    """上下文管理器：确保连接一定被关闭，避免 SQLite 锁竞争。

    用法：with db_session() as db: ...
    """

    def __init__(self, commit: bool = False):
        self._conn = None
        self._commit = commit

    def __enter__(self) -> sqlite3.Connection:
        self._conn = get_conn()
        return self._conn

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._commit and exc_type is None:
                self._conn.commit()
            elif exc_type is not None:
                self._conn.rollback()
        finally:
            self._conn.close()
        return False


def _init_lock_path() -> str:
    return DB_PATH + ".initlock"


class _file_lock:
    """跨进程文件锁 —— 防止多个进程同时执行 init_db 造成建表竞争。

    Windows 下用 msvcrt，POSIX 用 fcntl；拿不到锁就退化��独占
    （此时依赖 SQLite 自身的 busy_timeout 兜底）。
    """

    def __init__(self, path: str, timeout: float = 15.0):
        self.path = path
        self.timeout = timeout
        self.fh = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        deadline = time.time() + self.timeout
        while True:
            try:
                self.fh = open(self.path, "a+b")
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except (OSError, ImportError):
                if self.fh:
                    try:
                        self.fh.close()
                    except OSError:
                        pass
                    self.fh = None
                if time.time() > deadline:
                    return self      # 拿不到锁也继续，由 SQLite 兜底
                time.sleep(0.15)

    def __exit__(self, *exc):
        if self.fh:
            try:
                if os.name == "nt":
                    import msvcrt
                    self.fh.seek(0)
                    msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
            except (OSError, ImportError):
                pass
            try:
                self.fh.close()
            except OSError:
                pass
            self.fh = None
        return False


def init_db() -> None:
    with _file_lock(_init_lock_path()):
        _init_db_locked()


def _ensure_wal(db: sqlite3.Connection) -> None:
    """在跨进程锁内把库切到 WAL（幂等）。"""
    global _wal_ready
    if _wal_ready:
        return
    try:
        db.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        return          # 并发时若已被别的进程切过，保持现状即可
    db.commit()
    _wal_ready = True


def _init_db_locked() -> None:
    db = get_conn()
    _ensure_wal(db)
    db.executescript(
        """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE,
    full_name TEXT,
    company TEXT,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'sales',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name_cn TEXT NOT NULL,
    name_en TEXT,
    sort_order INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS suppliers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name_cn TEXT NOT NULL,
    name_en TEXT,
    contact TEXT,
    phone TEXT,
    email TEXT,
    remark TEXT,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT UNIQUE NOT NULL,
    name_cn TEXT NOT NULL,
    name_en TEXT NOT NULL,
    category_code TEXT,
    supplier_code TEXT,
    brand TEXT DEFAULT 'Felixv',
    model TEXT,
    spec TEXT,                -- 规格描述(中文)
    spec_en TEXT,             -- 规格描述(英文)
    material TEXT,            -- 材质
    power TEXT,               -- 功率/电压/能源
    capacity TEXT,            -- 层数/盘数/容积
    dimension TEXT,           -- 外形尺寸 mm
    packing_size TEXT,        -- 包装尺寸
    net_weight REAL,          -- 净重 kg
    gross_weight REAL,        -- 毛重 kg
    price_cny REAL,           -- 国内参考价
    price_usd REAL,           -- 出口 FOB 参考价
    moq INTEGER DEFAULT 1,
    lead_time TEXT,           -- 交期
    hs_code TEXT,             -- 海关编码
    certs TEXT,               -- JSON array
    image_path TEXT,          -- 主图
    images TEXT,              -- JSON array 附图
    desc_en TEXT,             -- 英文详情描述(外贸用)
    keywords TEXT,
    specs TEXT,               -- JSON: 品类专属参数（按 param_template 模板）
    source TEXT,              -- 自建/供应商/资料导入
    doc_id INTEGER,           -- 来源文档
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    company_en TEXT NOT NULL,
    contact TEXT,
    country TEXT,
    address TEXT,
    email TEXT,
    phone TEXT,
    incoterm TEXT DEFAULT 'FOB Guangzhou',
    port TEXT DEFAULT 'Guangzhou / Shenzhen',
    payment_terms TEXT DEFAULT '30% T/T deposit, 70% balance before shipment',
    currency TEXT DEFAULT 'USD',
    lang TEXT DEFAULT 'en',
    remark TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS suppliers_link_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS quotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_no TEXT UNIQUE NOT NULL,
    customer_id INTEGER,
    title TEXT,
    lang TEXT DEFAULT 'en',
    template_key TEXT DEFAULT 'standard_en',
    incoterm TEXT,
    port TEXT,
    currency TEXT DEFAULT 'USD',
    payment_terms TEXT,
    valid_days INTEGER DEFAULT 30,
    freight REAL DEFAULT 0,
    insurance REAL DEFAULT 0,
    discount_rate REAL DEFAULT 0,
    remark TEXT,
    items_json TEXT NOT NULL,
    total_amount REAL DEFAULT 0,
    status TEXT DEFAULT 'draft',
    owner_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    lang TEXT DEFAULT 'en',
    body_html TEXT NOT NULL,
    css TEXT,
    is_builtin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

-- 客户自带报价单模板：按对方给的文件原样填充，而不是用系统预设模板
CREATE TABLE IF NOT EXISTS client_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,                 -- 模板名（一般用客户名 + 版本）
    customer_id INTEGER,                -- 关联客户（可空，表示通用）
    file_name TEXT NOT NULL,            -- 原始文件名
    file_type TEXT NOT NULL,            -- xlsx / xlsm / docx
    stored_path TEXT NOT NULL,
    sheet_name TEXT,                    -- Excel 工作表名
    field_map TEXT,                     -- JSON 单元格到字段的映射
    item_map TEXT,                      -- JSON 明细区表头行与列映射
    structure TEXT,                     -- JSON 解析出的模板结构
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 用客户模板生成的历史记录，便于回溯当时用的是哪份模板
CREATE TABLE IF NOT EXISTS filled_quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quotation_id INTEGER NOT NULL,
    template_id INTEGER NOT NULL,
    output_path TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    doc_type TEXT,
    size INTEGER,
    stored_path TEXT NOT NULL,
    parsed_text TEXT,
    parsed_json TEXT,
    status TEXT DEFAULT 'pending',
    message TEXT,
    upload_by INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS materials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    spec TEXT,
    supplier_code TEXT,
    unit_price REAL,
    unit TEXT DEFAULT 'set',
    net_weight REAL,
    gross_weight REAL,
    remark TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS ai_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task TEXT,
    mode TEXT,
    input_chars INTEGER DEFAULT 0,
    output_chars INTEGER DEFAULT 0,
    approx_tokens INTEGER DEFAULT 0,
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_products_cat ON products(category_code);
CREATE INDEX IF NOT EXISTS idx_products_name ON products(name_cn, name_en);
CREATE INDEX IF NOT EXISTS idx_quotations_customer ON quotations(customer_id);
"""
    )
    db.commit()
    # 向后兼容：老库补列
    _add_column_if_missing(db, "products", "specs", "TEXT")
    db.commit()
    release_conn()


def rows_to_dicts(rows) -> list:
    return [dict(r) for r in rows]


def jload(value, default=None):
    if not value:
        return default if default is not None else []
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else []


def jdump(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ==========================================================================
# 通用 settings 访问器（M006 收敛现状里那批裸 SQL）
# ==========================================================================
def get_setting(key: str, default: str = "") -> str:
    """读 settings(key)，读不到回落 default。不抛异常（装配期/首装可能表未就绪）。

    M006 把它用作「可配置署名」的底层读取：关于页 / 设置页 / 导出文件据此取生效值，
    读不到则回落 brand.py 的常量默认值，绝不把系统起成空壳。
    """
    try:
        db = get_conn()
        r = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default
    except Exception:
        return default


def set_setting(key: str, value: str) -> None:
    """写 settings(key)（INSERT OR REPLACE）。"""
    db = get_conn()
    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))
    db.commit()


def effective_company() -> dict:
    """当前生效的公司信息（报价单 / 报销单抬头用）。

    优先读管理员在 brand:company_cn/en 上的覆盖，否则回落 DEFAULT_COMPANY。
    与 brand.effective_brand() 共用同一对键 —— 关于页与单据抬头因此永远一致
    （docs/09 §2.2 ⑧ 事实来源唯一化）。
    """
    c = dict(DEFAULT_COMPANY)
    cn = get_setting("brand:company_cn", "")
    en = get_setting("brand:company_en", "")
    if cn:
        c["name_cn"] = cn
    if en:
        c["name_en"] = en
    return c


# ==========================================================================
# 多数据库后端（部署可选）
# ==========================================================================
def db_backend() -> str:
    """当前生效的数据库后端。"""
    return DB_BACKEND


def mssql_config() -> dict:
    """读取 SQL Server 连接配置。

    优先级：环境变量 WV_MSSQL_* > data/dbconfig.json > 内置默认。
    内置默认账户即用户指定的 root / 88888888，便于一键部署时直接可用。
    """
    cfg = {"server": "127.0.0.1", "port": 1433, "database": "felix",
           "user": "root", "password": "88888888", "driver": "ODBC Driver 17 for SQL Server"}
    env_map = {"server": "WV_MSSQL_SERVER", "port": "WV_MSSQL_PORT",
               "database": "WV_MSSQL_DB", "user": "WV_MSSQL_USER",
               "password": "WV_MSSQL_PWD"}
    for k, env in env_map.items():
        v = os.environ.get(env)
        if v:
            cfg[k] = v if k != "port" else int(v)
    path = os.path.join(BASE_DIR, "data", "dbconfig.json")
    if os.path.isfile(path):
        try:
            cfg.update(json.load(open(path, encoding="utf-8")))
        except Exception:
            pass
    return cfg


def _mssql_conn():
    """SQL Server 连接工厂（实验性）。

    重要：当前业务查询仍使用 SQLite 方言（IFNULL / SUBSTR / AUTOINCREMENT 等），
    尚未移植到 T-SQL。因此选择 mssql 后端时应用会在此拒绝启动并提示完成 M108。
    这一步是「保留两套数据库、部署可选」的接线层 —— 配置已就位（data/dbconfig.json），
    真正可用需等查询方言移植完成（路线图 M108）。
    """
    raise NotImplementedError(
        "SQL Server 后端尚未就绪：业务查询的 T-SQL 方言移植进行中（路线图 M108）。"
        "请暂时使用 SQLite（默认后端），或等待 M108 完成后切换。"
        "连接配置已保存在 data/dbconfig.json。"
    )