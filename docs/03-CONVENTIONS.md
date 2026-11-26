# 开发约定与每晚 SOP

## 一、目录约定

```
E:/workbuddy/felix-quote/
├─ src/                 工作副本（唯一可改的源码树，= 解压出的 felix-quote-portable）
│  ├─ app/              后端
│  │  ├─ core/          基础设施层
│  │  ├─ modules/       领域模块层
│  │  ├─ static/        前端
│  │  └─ main.py        装配层
│  ├─ runtime/python/   自带 Python 3.13（勿动）
│  └─ data/             运行时数据（.db 不入库包）
├─ docs/                本文及路线图、进度日志
├─ tools/               打包/自测脚本
└─ _inbox/              原始下载与解压快照（只读对照，勿改）
```

## 二、每晚 SOP（23:00 起）

1. **读状态**：`docs/00-ARCHITECTURE.md`（重申分层铁律）→ `docs/01-ROADMAP.md`（找下一个 ⬜）
2. **看昨夜**：`docs/02-PROGRESS.md` 最后一条，确认无遗留 🟨
3. **开工**：按 DoD 8 条实现该模块（先迁移 → 后端 → 权限 → 前端 → 种子）
4. **自测**：`runtime/python/python.exe tools/smoke_test.py`（必须全绿）
5. **记账**：ROADMAP 改状态；PROGRESS 追加「做了什么 / 坑 / 下一步」
6. **收尾**：`git` 无仓库则跳过；清 `__pycache__`
7. **发布判定**：完成一个 Phase 或累计 3 个模块 → 打包并替换资料库节点
8. **停下**：不贪多。做完一个模块即止，把上下文留给明晚

## 三、新模块代码模板

```python
"""模块层 — <域>：<一句话职责>。

<为什么这么设计，一句话。>
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_user
from ..core import permissions as P
from ..core.db import get_conn, jdump, jload, now, release_conn
from ..core.services import audit, next_no

router = APIRouter(prefix="/api/<域>", tags=["<域>"])

# 枚举集中定义 —— 前端不得硬编码中文
XXX_STATUSES = {"draft": "草稿", "submitted": "已提交", "closed": "已关闭"}


class XxxIn(BaseModel):
    name: str
    remark: str | None = None


@router.get("")
def list_xxx(u: dict = Depends(current_user)):
    P.require(u, "<域>.view")
    db = get_conn()
    rows = [dict(r) for r in db.execute("SELECT * FROM xxx ORDER BY id DESC LIMIT 200")]
    release_conn()
    return rows
```

挂载：在 `app/main.py` 的模块区 `app.include_router(xxx.router)`。
权限：在 `core/permissions.py` 的 `ROLES` 里分配到对应角色。
前端：`static/js/pages/<name>.js` + `app.js` 路由表注册。

## 四、红线（违反即回滚）

- ❌ 修改已发布的 `_vNNN` 迁移函数 —— 只追加
- ❌ `modules/` 内跨模块 import —— 走 `core/services.py`
- ❌ 在 `main.py` 写业务逻辑
- ❌ 物理删除业务数据 —— 一律 `is_active=0` 软删除
- ❌ 前端硬编码状态中文 —— 从后端枚举接口取
- ❌ 改动 `runtime/` 下任何文件
- ❌ 把 `.db`、`.log`、`__pycache__` 打进发布包

## 五、常用命令

```bash
# 启动服务（后台，端口 8765）
"E:/workbuddy/felix-quote/src/runtime/python/python.exe" -m uvicorn app.main:app --port 8765

# 冒烟自测
"E:/workbuddy/felix-quote/src/runtime/python/python.exe" "E:/workbuddy/felix-quote/tools/smoke_test.py"

# 组织树接口自测（M001 起：真起服务 + 登录 + 断言数据，不只是看路由在不在）
"E:/workbuddy/felix-quote/src/runtime/python/python.exe" "E:/workbuddy/felix-quote/tools/test_structure.py"

# 岗位职级接口自测（M002：编制 vs 在岗口径、权限、软删引用校验）
"E:/workbuddy/felix-quote/src/runtime/python/python.exe" "E:/workbuddy/felix-quote/tools/test_position.py"

# 组织跳层逻辑单测
"E:/workbuddy/felix-quote/src/runtime/python/python.exe" "E:/workbuddy/felix-quote/tools/test_orgutil.py"

# 打包发布（替换资料库节点 nLUL99BDxAK65v0niDKtqA，保持链接不变）
bash "E:/workbuddy/felix-quote/tools/build_release.sh"
```

## 六、打包发布细节

- 打包前：删 `src/data/*.db`、`src/backup/*`、`**/__pycache__`、`app/static/uploads/*` 演示文件
- 压缩：`"/c/Program Files/WinRAR/Rar.exe" a -r -ep1 <目标.rar> <src目录内容>`
- 顶层结构必须是 `felix-quote-portable/...`？—— 否，最终包内直接是
  `app/ runtime/ data/ backup/ *.bat 版本.txt ico` 一层（与原始 rar 一致）
- 上传替换（保持同名、链接不变）：
  `python3 <skill>/drive/upload_drive_file.py <rar路径> --node-id nLUL99BDxAK65v0niDKtqA --file-name "felix-quote-1.3.0.rar"`
  （替换后需 `node-info` 确认 node id 未变、version 递增）

## 七、账户 / 授权 / 报表 / 数据库 约定

### 7.1 管理员账户
- 初始管理员固定 `admin`，**初始口令 `admin.123`**（`auth.ensure_bootstrap_admin`）。
- 初始 `must_change_pw=1`，首次登录强制改密。
- 部署时可用 `deploy/setup.py --admin-pass <强口令>` 覆盖。
- 该口令仅用于空库种子；已存在用户时不会重置（防误改线上账号）。

### 7.2 授权与试用期
- 试用 **30 天**，从 `settings.installed_at` 起算（`core/licensing.TRIAL_DAYS`）。
- 授权文件 `.felix_license`（位于 `BASE_DIR`，独立于 `data/`，换库不丢）。
- 授权码格式：`base64(json).HMAC-SHA256`（作者私钥内嵌 `core/licensing.AUTHOR_SECRET`）。
  机器绑定到 `install_id`；`machine:"ANY"` 为通用授权。
- 签发：`python tools/license_gen.py --install-id <机器install_id> --owner "客户" --days 365 --out license.key`
  （`--any` 生成通用授权；`--type perpetual` 永久）。
- 激活：管理员在「授权激活」页粘贴授权码 → `POST /api/licensing/activate`。
- 过期处理：中间件 `_license_gate` 对"已过期且非永久"的业务接口返回 `402`，
  **放行**登录/关于/激活/静态资源，保证管理员仍能登录激活。
- 校验只在请求进入时实时算一次（`get_state()`），**不在每个请求里重复 HMAC**。

### 7.3 报表与打印
- 排版与打印**全部由前端 HTML/CSS（`@media print`）完成**，零额外依赖、直接调打印机。
- 发票支持 **A4 单张 / A4 双联（上下两张 A5）**（`inv.layout`）。
- 水单（微信/支付宝）走"水单报销"：图片导入 `data/slips/`（`report_slips` 表）→ 打印预览
  → 可选**脱敏**（前端框选矩形，归一化坐标存 `mask_json`）→ 直接打印。
- 权限点：`reports.view` / `reports.edit`（已分配给 财务/销售经理/物流/生产 等角色）。

### 7.4 多数据库（部署可选）
- 后端 `database.DB_BACKEND`（`sqlite` 默认 / `mssql` 实验性），读 `WEIGEAR_DB_BACKEND`。
- SQL Server 配置落 `data/dbconfig.json`，内置默认账户 **root / 88888888**，
  亦可用 `WEIGEAR_MSSQL_*` 环境变量。
- **mssql 当前不可用**：业务查询仍是 SQLite 方言，T-SQL 移植在 M108。选 mssql 会拒绝启动并提示。
- 部署：`python deploy/setup.py --db-backend mssql --mssql-user root --mssql-pwd 88888888`（仅生成配置）。
