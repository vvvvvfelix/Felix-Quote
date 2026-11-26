"""数据库方言适配层（M108 分阶段移植）。

当前应用通过裸 sqlite3 直接访问数据库（database.get_conn 返回 sqlite3.Connection）。
M108 的目标是把系统做成「可选 SQL Server 后端」：SQLite 仍默认零依赖，env 指定
WV_DB_BACKEND=mssql 时走 T-SQL。本模块是分阶段改造的「方言感知」底座：

- dialect(): 返回当前生效后端 'sqlite' / 'mssql'（读 WV_DB_BACKEND，回落 sqlite）。
- translate_ddl_to_tsql(sqlite_ddl): 把 SQLite DDL 字符串翻译为兼容的 T-SQL，
  供迁移层（core/schema.py 的 _vNNN 函数）在 mssql 分支下使用。

业务查询（SELECT/INSERT...）的方言差异（占位符 ? vs @p、last_insert_rowid 等）
不在此处理，列入「剩余未移植模块清单」由后续阶段逐查询移植。

硬约束（docs/ 路线图 M108）：
- SQLite 路径必须字节级不变，保证 python tools/smoke_test.py 186 路由基线全绿。
- 本模块的翻译只在 DB_BACKEND=='mssql' 时生效，对 sqlite 零影响。
"""
import re

from ..database import DB_BACKEND


def dialect() -> str:
    """当前生效的数据库方言：'sqlite'（默认）或 'mssql'。"""
    return "mssql" if DB_BACKEND == "mssql" else "sqlite"


# ---------------------------------------------------------------------------
# DDL 翻译：SQLite -> T-SQL
# ---------------------------------------------------------------------------
# 仅覆盖迁移层实际出现的构造；未覆盖的（如 PRAGMA、触发器、全文 MATCH）在
# 「剩余未移植模块清单」中列出，由 M108 后续阶段单独处理。
_AUTOINC_PK_RE = re.compile(r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", re.IGNORECASE)
_AUTOINC_RE = re.compile(r"\bAUTOINCREMENT\b", re.IGNORECASE)
_TYPE_TEXT_RE = re.compile(r"\bTEXT\b")
_TYPE_REAL_RE = re.compile(r"\bREAL\b")
_TYPE_INTEGER_RE = re.compile(r"\bINTEGER\b")
_CREATE_TABLE_RE = re.compile(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS", re.IGNORECASE)
_CREATE_INDEX_RE = re.compile(r"CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS", re.IGNORECASE)
_NOCASE_RE = re.compile(r"COLLATE\s+NOCASE", re.IGNORECASE)


def translate_ddl_to_tsql(ddl: str) -> str:
    """把一段 SQLite DDL（CREATE TABLE / INDEX / 注释）翻译为 T-SQL。

    说明：这是对迁移层常见构造的「务实」翻译，能覆盖本仓库 _vNNN 中出现的
    结构；个别高级构造（PRAGMA、触发器、全文索引）需要 M108 后续阶段单独处理。
    """
    if not ddl:
        return ddl
    s = ddl
    # 1) 去掉 SQLite 专属的 IF NOT EXISTS（SQL Server 不支持 CREATE TABLE/INDEX IF NOT EXISTS）
    s = _CREATE_TABLE_RE.sub("CREATE TABLE", s)
    s = _CREATE_INDEX_RE.sub("CREATE INDEX", s)
    # 2) 自增主键：INTEGER PRIMARY KEY AUTOINCREMENT -> INT IDENTITY(1,1) PRIMARY KEY
    s = _AUTOINC_PK_RE.sub("INT IDENTITY(1,1) PRIMARY KEY", s)
    # 3) 其余 AUTOINCREMENT（极少见）兜底
    s = _AUTOINC_RE.sub("IDENTITY(1,1)", s)
    # 4) 类型映射
    s = _TYPE_TEXT_RE.sub("NVARCHAR(MAX)", s)
    s = _TYPE_REAL_RE.sub("FLOAT", s)
    s = _TYPE_INTEGER_RE.sub("BIGINT", s)
    # 5) 大小写不敏感排序
    s = _NOCASE_RE.sub("COLLATE Latin1_General_CI_AI", s)
    return s
