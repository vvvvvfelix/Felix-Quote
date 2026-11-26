# M108 SQL Server (T-SQL) 移植 — 方言审计清单（分阶段交付物）

> 本文件是 M108「务实、分阶段」改造的审计与剩余工作清单。
> 配套代码：`src/app/core/dialect.py`（方言识别 + DDL→T-SQL 翻译）、
> `src/app/core/schema.py`（迁移层 DDL 已方言分支）、`docker-compose.yml`。
> 红线：SQLite 路径字节级不变，`python tools/smoke_test.py` 186 路由基线保持全绿。

## 1. 审计方法

在 `src/app/**/*.py` 内按 SQLite 方言特征逐一 grep 计数（命令见末节）。
凡 DDL 层（`schema.py` 的 `_vNNN` 迁移函数、`database.py` 的 `_init_db_locked`）
属「迁移层」，本次已做方言分支；凡业务查询（`modules/*.py`、部分 `core/*.py`）
属「剩余工作」，见第 4 节清单。

## 2. 本轮已完成（迁移层方言分支）

- 新增 `core/dialect.py`：`dialect()` 读 `WV_DB_BACKEND`（回落 `sqlite`）；
  `translate_ddl_to_tsql()` 覆盖 `IF NOT EXISTS` 剥离、`AUTOINCREMENT`→`IDENTITY(1,1)`、
  `TEXT`→`NVARCHAR(MAX)`、`REAL`→`FLOAT`、`INTEGER`→`BIGINT`、`COLLATE NOCASE`→`Latin1_General_CI_AI`。
- `core/schema.py`：所有 `db.executescript(...)` 改为 `_exec_ddl(db, ...)`；
  `run_migrations()` 打印方言并分支。sqlite 下 `_exec_ddl` 原样执行，行为不变。
- `docker-compose.yml`：Azure SQL Edge 实例 + 应用接线示例，便于后续起 mssql 冒烟。

## 3. 方言点审计清单（按构造分类）

| 方言构造 | 命中位置 | 数量 | 处理状态 |
|---|---|---|---|
| `AUTOINCREMENT` | `core/schema.py`(25)、`database.py`(14) | 39 | 迁移层已分支；`database.py` 初始建表 DDL 待分支（见 4） |
| `last_insert_rowid()` | `core/services.py`、`modules/documents.py`、`modules/masterdata.py`、`modules/quotation.py` | 4 文件 | 剩余 |
| `LIMIT` | 见 4（16 个模块共 46 行） | 46 行 | 剩余 |
| `OFFSET` | `finance / inquiry / logistics / masterdata / order / supply` | 6 文件 | 剩余 |
| `INSERT OR REPLACE` | `auth / brand / registry / schema / services / database / aiapi / mail / masterdata / quotation`（共 10 文件） | 10 文件 | settings 写可统一走 `set_setting`（已存在），业务 OR REPLACE 剩余 |
| `IFNULL(...)` | `schema / services / database / aiapi / expense / finance / masterdata / order / platform / position / structure / workbench`（12 文件） | 12 文件 | 剩余 |
| `SUBSTR(...)` | `schema.py`、`services.py` | 2 文件 | 剩余 |
| `PRAGMA` | `orgutil.py`、`schema.py`、`database.py` | 3 文件 | 迁移层 PRAGMA（WAL/foreign_keys）非 T-SQL，需分支或跳过 |
| `?` 占位符（裸 SQL 参数） | `modules/*.py` 累计 | 499 处 | 剩余（最大头，需逐查询改命名参数或迁 SQLAlchemy） |
| `datetime('now')` | 无 | 0 | 已用 Python `now()`，无需改 |
| `RANDOM()` | 无 | 0 | 无 |
| `json_extract/json_*` | 无 | 0 | JSON 列走 Python 解析，无需改 |
| `MATCH`（全文） | 无 | 0 | 无 |
| `COLLATE NOCASE` | 无（代码内） | 0 | 无 |

## 4. 剩余未移植模块清单（业务查询，按文件）

> 这些文件使用裸 `db.execute("... ? ...", (params,))` / `INSERT OR REPLACE` /
> `last_insert_rowid()` / `LIMIT ... OFFSET` / `IFNULL`，需逐查询改命名参数（SQLAlchemy
> `text()` 的 `:name`）或迁 Core/ORM；T-SQL 对应写法见各构造说明。

- `core/services.py`：`last_insert_rowid`、`INSERT OR REPLACE`、`IFNULL`、`SUBSTR`
- `core/orgutil.py`：`PRAGMA`
- `modules/documents.py`：`last_insert_rowid`
- `modules/masterdata.py`：`last_insert_rowid`、`INSERT OR REPLACE`、`IFNULL`、`OFFSET`
- `modules/quotation.py`：`last_insert_rowid`、`INSERT OR REPLACE`
- `modules/auth.py`：`INSERT OR REPLACE`（seed 管理员）
- `modules/brand.py`：`INSERT OR REPLACE`（install_id）
- `modules/registry.py`：`INSERT OR REPLACE`（模块开关）
- `modules/aiapi.py`：`INSERT OR REPLACE`、`IFNULL`
- `modules/mail.py`：`INSERT OR REPLACE`
- `modules/expense.py`：`IFNULL`、`LIMIT`
- `modules/finance.py`：`IFNULL`、`LIMIT`、`OFFSET`
- `modules/order.py`：`IFNULL`、`LIMIT`、`OFFSET`
- `modules/platform.py`：`IFNULL`、`LIMIT`
- `modules/position.py`：`IFNULL`、`LIMIT`
- `modules/structure.py`：`IFNULL`、`LIMIT`
- `modules/workbench.py`：`IFNULL`、`LIMIT`(5)
- `modules/identity.py`：`LIMIT`
- `modules/inquiry.py`：`LIMIT`、`OFFSET`
- `modules/logistics.py`：`LIMIT`、`OFFSET`
- `modules/supply.py`：`LIMIT`、`OFFSET`
- `modules/employee.py`：`LIMIT`
- `modules/report.py`：裸 SQL
- `modules/workflow.py`：裸 SQL
- `modules/task.py`：`LIMIT`
- `database.py` `_init_db_locked`：初始建表 DDL（14 处 AUTOINCREMENT + PRAGMA），需与 `schema.py` 同样分支

**量级提示**：`modules/*.py` 累计 `?` 占位符约 **499** 处，是 M108 后续阶段的主要工作量。
本轮未动这些文件，以保 186 路由冒烟全绿。

## 5. 切到 mssql 的已知阻塞（本阶段未解）

1. 连接层：`database._mssql_conn()` 仍 `raise NotImplementedError`。业务库需接 SQLAlchemy
   引擎（`mssql+pyodbc`）或等价抽象，本阶段仅把 DDL 迁移层分支就绪。
2. 业务查询：上述 499 处 `?` 占位符与方言函数在 mssql 下会直接报错，属剩余工作。
3. `PRAGMA`（WAL / foreign_keys / busy_timeout）在 SQL Server 无对应物，需在连接工厂按
   方言跳过或用等效设置（如 `SET TRANSACTION ISOLATION LEVEL`）。

## 6. 复现审计的命令

```bash
cd src/app
grep -rcn "AUTOINCREMENT" . --include=*.py | grep -v ':0'
grep -rln "last_insert_rowid" . --include=*.py
grep -rln "INSERT OR REPLACE" . --include=*.py
grep -rln "IFNULL" . --include=*.py
grep -rcn "LIMIT" modules --include=*.py | grep -v ':0'
grep -rln "OFFSET" modules --include=*.py
grep -rcn "?" modules --include=*.py | awk -F: '{s+=$2} END{print s}'
```
