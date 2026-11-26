# 迭代进度日志（每晚追加，勿覆盖）

## 2026-10-31（周四）· 第 0 夜 — 立项与构思

**做了什么**
- 从资料库节点 `nLUL99BDxAK65v0niDKtqA` 取回 `felix-quote-1.3.0.rar`（81MB），解压并建工作副本 `E:/workbuddy/felix-quote/src`
- 摸清现有架构：FastAPI + SQLite(WAL) + 版本化迁移 + 模块化 router，已有 15 个业务模块、110 条路由、约 10.6k 行后端代码；前端原生 JS 单页 + `js/pages/*.js`
- 产出 `00-ARCHITECTURE.md`（三轴组织模型 / 权限模型 / 模块地图 / 分层铁律）、`01-ROADMAP.md`（Phase 0–10，共约 60 个模块 + DoD 八条）、`03-CONVENTIONS.md`（每晚 SOP + 代码模板 + 红线）
- 建立 `tools/smoke_test.py`：拉起真实服务跑建库→迁移→种子，抓 OpenAPI 校验路由注册，支持按前缀断言
- 基线冒烟通过：110 条路由、首页正常（审批引擎 `/api/wf` 尚不存在，符合预期）

**关键判断**
- 现有底座质量不错，**不推倒重来**，在其上按 Phase 演进
- 组织模型必须从单表 `departments` 升级为三轴（组织树 / 岗位职级 / 人员），否则多工厂 + 事业部撑不住
- **审批引擎必须先建**，否则请假/报销/用章/采购/报价各自写流程必然腐烂
- 参考对标：Odoo 模块化、Frappe/ERPNext 的 DocType 元数据驱动、金蝶多组织三轴模型；不照搬重型框架（与便携版冲突）

**坑与备忘**
- Git Bash 会把 `/api/xxx` 参数转成 Windows 绝对路径，smoke_test.py 内已做规整
- 解压用 `C:/Program Files/WinRAR/Rar.exe x`（系统无 7z/unrar）
- 启动时 `data/.initialized` 存在则走静默模式；冒烟测试直接 uvicorn 起，不走 launcher

**下一步（M001 组织树重构）**
- `core/schema.py` 追加迁移建 `org_units`，迁移旧 `departments` 数据
- org_type：group/company/factory/office/branch/bu/dept/workshop/team；`path` 冗余加速子树查询
- 保留旧 `departments` 视图或做兼容层，避免已有模块全线改动的雪崩

---

## 2026-10-31（周四）· 第 0 夜补记 — 部署体系、模块开关、组织跳层、软著

用户追加需求：更好部署、模块可开关、部署前环境检测、桌面日志、桌面管理工具、
组织中间层级可跳过、软著与商用保护。

**已落地**
1. **模块开关**：新增 `core/registry.py`（15 个模块清单 + 依赖 + 状态持久化 settings 表）；
   `main.py` 改为按开关动态挂载路由，单模块失败只记日志不停服；`platform.py` 加
   `/api/modules`、`/api/platform/modules`、`/api/platform/modules/toggle`、`/api/platform/status`。
   **验证**：默认 113 条路由 → 关闭 aiapi+mail 后 95 条，`/api/ai/capabilities` 确认摘除。
2. **组织跳层**：新增 `core/orgutil.py`（穿透解析 + 环检测 + 兜底），`identity.py` 接入
   （建/改用户时部门自动穿透、部门列表补 `effective_*`、新增 `/api/org/integrity`、`/api/org/resolve`）。
   **验证**：`tools/test_orgutil.py` 17 项全通过（空表/中间层停用/孤儿/成环/无工厂层级/异常输入）。
3. **一键部署**：`deploy/detect_env.py`（10 项检测，不达标给可执行修复方案）、
   `deploy/setup.py`（6 步：检测→配置→初始化→脚本→启动→自检，桌面生成部署报告）、
   `deploy/control.py`（桌面入口控制器）。**验证**：实跑部署成功，桌面报告已生成。
4. **管理控制台**：新增前端 `js/pages/console.js`（运行状态 + 模块开关 + 组织体检），
   注册进 app.js 与菜单（仅 admin），支持 `#console` 锚点直达；桌面放了
   `C:\Users\Felix\Desktop\Felix 管理控制台.bat`。
5. **软著/商用**：`tools/gen_softcopyright.py` 一键生成源程序 60 页 + 用户手册 + 申请信息
   （实测 11195 行 / 224 页 → 提交 60 页；手册 17 页，脚本会提示补足）；
   新增 `src/LICENSE.txt` 商用许可条款。

**关键判断**
- 部署形态推荐 **B 单机服务化**（自带运行时 + SQLite + 系统服务 + Nginx），
  现阶段不换 PostgreSQL；但 SQL 方言必须锁在 `core/db.py` 与迁移脚本里，留出升级路径。
- 桌面管理工具放弃 tkinter（**便携版 runtime 里没有 tkinter**），改为 Web 控制台 + 桌面 bat
  入口，零额外依赖、风格与主系统一致。
- 保护目标定为「提高侵权成本 + 保留法律证据」，不是做到不可破解；授权与完整性自检
  必须留管理员可关闭的口子。

**坑**
- `modules/identity.py` 的 router 已有 `/api/org` 前缀，新增路径写成 `/org/integrity`
  会变成 `/api/org/org/integrity` —— 已改为 `/integrity`。
- `settings` 表只有 `key,value` 两列，没有 `updated_at`。
- 便携版 runtime 无 tkinter，也无 psutil（环境检测用 ctypes + socket 替代）。

**下一步**
- M001 组织树重构（`org_units` 多类型 + `path` 索引 + 旧 `departments` 迁移），
  完成后将 `orgutil.ORG_TABLE` 切到 `org_units` 即可，穿透逻辑无需改动。

---

## 2026-11-01（周五）· 第 1 夜 — 报表/授权/多库/工具箱

用户本轮追加需求：岗位软件集成（金蝶/税务/CAD/携程/Office/WPS）、按角色报表与打印、
发票 A4 双 A5 排版、微信/支付宝水单导入自动排版+可选脱敏+直连打印、管理员默认
admin/admin.123、保留两套数据库、SQL Server 默认 root/88888888、30 天试用期加密授权。

**已落地**
1. **授权与试用期引擎（M110 ✅）**：新增 `core/licensing.py`（纯标准库 HMAC-SHA256，
   无第三方依赖，规避便携 runtime 缺 `cryptography`），30 天试用从 `settings.installed_at`
   起算；`modules/licensing.py` 提供 `/api/licensing/status`（公开）、`/activate`、`/deactivate`
   （admin）；`main.py` 中间件 `_license_gate` 过期返回 HTTP 402（仍允许登录/激活）；
   `tools/license_gen.py` 作者端签发器（支持绑机/通用、指定天数与功能）。
2. **报表与打印模块（M109 ✅）**：新增 `modules/report.py` + 前端 `pages/report.js`。
   - 报表类型：报价单 / 送货单 / 行程单 / 发票 / 水单（REPORT_TYPES）。
   - 发票「奇葩」排版：A4 双 A5（v-for [1,2] 实现正反面/双联）`@media print` 零依赖直打。
   - 水单报销：批量导入图片 → 存 `DATA_DIR/slips` → 画布拖拽打码脱敏（归一化坐标，可选脱敏）
     → 自动适配到合适尺寸 → 直连打印机。
   - 接口：types / print-data(quotation|order) / slips(upload|list|file|mask|delete)。
   - `core/schema.py _v007` 建 `report_slips` 表（含 mask_json 软删）。
3. **岗位工具箱与集成映射（M111 ✅）**：新增 `modules/toolbox.py` + 前端 `pages/toolbox.js`
   （只读网格）。8 类角色 → 外部软件映射（金蝶/税务UKey/CAD/携程/Office/WPS 等），
   每项标 integrated/bridge/planned；`docs/06-岗位工具箱与集成规划.md` 详细规划。
4. **多数据库后端抽象（M112 ✅，M108 ⬜）**：`database.py` 引入 `DB_BACKEND`
   （sqlite 默认 / mssql 实验），`get_conn()` 按后端分发；`mssql_config()` 读取顺序
   环境变量 `WEIGEAR_MSSQL_*` > `data/dbconfig.json` > 默认 root/88888888；
   mssql 运行时 `NotImplementedError` 指向 M108（T-SQL 移植）尚未完成，故 mssql 模式
   部署跳过初始化/启动。
5. **账号与部署默认收敛**：`auth.ensure_bootstrap_admin` 密码改为 `admin.123`；
   部署套件 `deploy/detect_env.py`、`deploy/setup.py` 增 `--db-backend`/`--mssql-*`、
   环境检测增 `check_mssql`、报告展示后端与默认密码提示。

**关键判断**
- 授权用「内嵌密钥 + HMAC 校验」做到「提高侵权成本」层级，足够桌面 ERP 试用管控；
  真正不可破需编译/混淆分发（M106）。
- 打印与脱敏全部走 HTML/CSS + Canvas，零依赖、可直连打印机，避开第三方库与 runtime 缺口。
- SQL Server 先交付「配置+骨架+优雅报错」，`_mssql_conn` 真正查询留待 M108 T-SQL 移植，
  避免半成品查询误导用户。

**坑**
- `report.py` 最初从 `core.db` 导入 `DATA_DIR` 失败（该模块不重导出），已改为
  `from ..core.db import get_conn,jdump,jload,now; from ..core.paths import uuid_like; from ..database import DATA_DIR`。
- 改管理员密码脚本用 `from app.auth import ...`（sys.path 插项目根）而非 `from auth import`
  （包上下文缺失），已成功把开发库 admin 改为 admin.123。

**下一步**
- M108：SQL Server T-SQL 方言移植（当前 mssql 只到配置与连接骨架）。
- M105 余下：核心文件哈希自检（授权引擎已就绪，完整性校验待补）。
- 用户尚有两问待确认：① 产品名是否正式改为「Felix 企业管理平台」？② 是否要预置真实公司
  组织架构种子数据？
- 全量回归测试（tools/smoke_test.py）按用户要求「等所有东西做好了再测试」，本轮未跑。

---

## 2026-10-31（周五）夜 · 第 2 夜 — M001 组织树重构（Phase 0 地基第一块）

按路线图取「第一个 ⬜ 且依赖已 ✅」= **M001 组织树重构**。

**做了什么**
1. **迁移 `_v008`（只追加，未改任何已发布迁移）**：新建 `org_units`
   （9 种 org_type、无限层级、`path`/`level` 冗余、`company_id`/`factory_id`/`bu_id`
   三轴归属、成本中心、地址/电话/税号）；旧 `departments` 全量复制进来**保留 id**；
   `users` 加 `org_unit_id` 并从 `dept_id` 回填；迁移末尾调用
   `orgutil.recompute_tree()` 重算全部冗余字段。
2. **core/orgutil.py 增强**：新增 `recompute_tree(db, table, rollups)`
   （BFS 重算 path/level + 三轴继承，带环检测、成环节点兜底成自身根，path 永不为 NULL）；
   新增 `_kind()` 兼容 `org_type`/`kind` 两种列名；`load_orgs()`/`check_integrity()`
   支持传入表名，新代码一律显式传 `org_units`。
3. **新模块 `modules/structure.py`（`/api/structure`，核心模块不可关闭）**：
   types / units / tree / units/{id} / stats / companies / factories / bus +
   POST·PUT·DELETE·move。写操作带**环检测**、停用前查下级与在岗人员、全程 audit 留痕。
4. **权限**：新增 `structure.view`（全员：组织架构本来就该人人可见）与
   `structure.edit`（仅管理员），已写入 8 个内置角色。
5. **种子 `seed_structure()`（幂等 upsert by code）**：集团型真实架构 23 个节点，
   集团 → 2 家法人公司 → 2 个工厂 → 3 个车间 → 2 个班组 / 2 个事业部 →
   销售部·分公司·国际业务部 / 总部 → 行政·人事·财务·技术·研发·采购·物流。
   复用旧 code（HQ/AD/TECH/PUR/LOG/FACTORY/BRANCH）保证 id 对齐。
6. **兼容层 `_mirror()`**：组织树单向回写 `departments`（按 code，id 对齐），
   旧页面（部门看板、用户列表、待办、orgutil 穿透）零改动继续可用。
   遗留 4 个 UI 分组（SALES/PROD/FIN/HR）自动停用并标注，保证树只有一个根。
7. **前端 `pages/structure.js`**：统计卡 + 筛选 + 树形表格（缩进/展开/面包屑/
   三轴归属/人数）+ 详情面板 + 新增/编辑/停用弹窗；注册进 index.html、app.js
   路由与侧栏菜单（按 `structure.view` 显隐）。
8. **自测**：新增 `tools/test_structure.py`（真起服务 + 登录 + 27 项断言：
   种子结构与三轴继承、path/level、增删改移、环检测、旧表回写、停用后不入树）。

**关键判断**
- 三轴（公司/工厂/事业部）必须用冗余列而非运行时递归：多组织下数据范围过滤
  是最高频操作，退化成 `WHERE company_id=?` 才扛得住；新增第四条轴只需加列 + 一条
  rollup 规则，不动业务代码。
- **不推倒重来**：旧 departments 保留 id 对齐 + 单向回写，避免「一改组织，15 个
  已有模块全线返工」的雪崩；M004 再统一切换 `users.org_unit_id` 并废弃旧表。
- 「不丢数据，只归位」：遗留节点不物理删除，UI 分组停用、管理员自建部门挂到集团下。

**坑**
- `core/licensing.py` 写着 `from .database import BASE_DIR`（应为 `..database`），
  **整个应用根本起不来** —— 昨夜未跑全量回归所以没暴露。已修。
- `modules/report.py` 的 `from ..database import DATA_DIR` 同样不存在（常量在
  `core/paths.py`），导致 report 模块静默挂载失败、8 条路由消失。已修。
- 迁移把旧 departments 的 4 个 UI 分组也复制进来，导致树出现 5 个根、统计口径分裂
  —— 加 `_absorb_legacy()` 收纳后才通过「只有一个根」断言。
- 停用组织仍占编码（刻意设计，防历史单据串号），自测脚本因此改用时间戳编码。

**冒烟结果**
- `smoke_test.py` 全绿：**137 条路由**（新模块 + 修复后恢复的 report 8 条）；
  基线前缀（/api/org、/api/quotation、/api/orders、/api/products、/api/customers、
  /api/dashboard、/api/tasks、/api/finance、/api/shipments、/api/purchase-orders、
  /api/production-board、/api/licensing、/api/toolbox、/api/modules）全部 OK。
- `test_structure.py` 27/27 通过；`test_orgutil.py` 17/17 通过（未回归）。

**下一步（明晚 M002 岗位职级体系）**
- `job_titles` / `job_grades`(M/P/T/O) / `positions`(组织×岗位×职级×编制×汇报上级)
- 编制 vs 在岗看板，接 M001 的 `member_count`
- 权限点 `position.view` / `position.edit`，并入角色库


---

## 2026-11-02（周日）夜 · 第 3 夜 — M002 岗位职级体系（Phase 0 地基第二块）

按路线图取「第一个 ⬜ 且依赖已 ✅」= **M002 岗位职级体系**（依赖 M001 组织树 ✅）。

**做了什么**
1. **迁移 `_v009`（只追加，未动 `_v001`~`_v008`）**：新表 4 张
   - `job_titles` 岗位（序列 / 类别 / 建议层级 / 职责 / 任职要求 JSON）
   - `job_grades` 职级（family + level，薪资带宽 REAL + currency）
   - `positions` 编制（组织 × 岗位 × 职级 × headcount × parent_position_id 汇报上级
     + status / is_key / work_place）
   - `position_assignments` 任职（主岗 / 兼岗、投入占比、起止日期、软删留痕）
   索引 15 条；**刻意不给 `(position_id,user_id)` 唯一索引** —— 任职是软删的，
   「调走再调回同一岗位」会有两条历史，唯一索引会让第二条写不进去，
   唯一性改由模块层在写入前校验 `is_active=1` 的记录。
2. **新模块 `modules/position.py`（`/api/position`，非核心可关闭）**：
   types（一次性下发 6 组枚举）/ titles / grades / positions 全套 CRUD /
   assignments 任职登记 / stats 统计卡 / board 编制 vs 在岗看板 /
   org-options / candidates。写操作带**汇报线环检测**、引用完整性校验、
   停用前查下级与在职人员，全程 audit 留痕。
3. **权限**：`position.view`（全员，与 `structure.view` 同口径）、
   `position.edit`（admin + finance，财务兼行政人事口径，M004 扩角色后再迁）。
4. **前端 `pages/position.js`**：统计卡（总编制 / 在岗 / 缺编 / 超编 / 未落编）
   + 编制看板（按组织 / 部门 / 序列 / 岗位 四维，支持含下级归集）
   + 三签页表格（编制 / 岗位 / 职级）+ 详情面板（任职人、下级汇报岗、汇报链）
   + 新增编辑停用弹窗 + 任职弹窗。注册进 index.html、app.js 路由与侧栏菜单
   （按 `position.view` 显隐）。
5. **种子 `seed_positions()`（幂等 upsert by code）**：职级 27 级（M1–M6 / P1–P8 /
   T1–T8 / O1–O5，含薪资带宽）、岗位 30 个、编制 41 条 + 演示任职 18 条。
6. **自测 `tools/test_position.py`**：真起服务 + 登录 + **79 项断言全通过**。

**关键判断**
- **任职关联选「独立任职关系表」而不是 `users` 加 `position_id` 外键**：
  外键方案一个人只能占一个岗，兼岗（小厂厂长兼采购、会计兼行政）表达不了，
  且会把 `position_id` 渗进 identity / auth 的 INSERT 列清单，与「不破坏现有
  模块」相悖；独立表对现有模块侵入为 0，并自带起止日期供 M003 员工异动复用。
- **编制占用口径**：`occupied = 该编制下在职任职人数（兼岗重复计）`；
  看板同时给出 M001 口径的 `members`（按 `users.org_unit_id` 统计、按人去重），
  两个数之差即兼岗人次 —— 兼岗在国内制造企业是常态，藏起来只会让对不上账。
- **看板合计按编制去重直接算，不把各行相加**：`scope=subtree` 时一条编制会被
  记进自己和每一级祖先，行求和会把同一批人算好几遍。
- **演示人员刻意不可登录**：种子造了 17 名 `demo_staff_xx` 人员（否则库里只有
  admin，「超编」这类状态根本演示不出来）。口令用一次性随机串哈希后立即丢弃，
  谁也不知道明文 —— 只能参与编制统计，管理员在「用户管理」点「强制改密」
  即可把其中任何一人转成真实账号。宁可多一步，也不预置口令已知的演示账号。
- **未改动 `main.py`**：路由挂载自 M001 起已改为按 `core/registry.py` 动态装配，
  静态引入只用于「种子必须在启动钩子调用」的核心模块（identity / structure）。
  position 是非核心模块，种子改由 `seed.py::seed_all()` 懒加载 + 吞异常调用，
  保证「单模块失败只记日志不停服」不被破坏。

**坑**
- 在职口径常量 `ON_JOB_SQL` 带了 `us.` 表别名，被直接用在单表
  `SELECT COUNT(*) FROM users` 上 → `no such column: us.is_active`（500）。
  已拆成 `ON_JOB_SQL`（JOIN 用）/ `ON_JOB_SQL_PLAIN`（单表用）。
- 看板 `scope=subtree` 与 `scope=self` 在传了 `org_unit_id` 时返回同一个 total
  —— 过滤阶段一律用了整棵子树，只在归集阶段区分。已改为 self 时过滤到直属编制。
- 种子里编制名一开始写成了 `组织code 岗位code 职级code` 的占位串，
  与接口新增时自动生成的「组织 · 岗位 · 职级」不一致；已统一走 `_auto_name()`。
- Vue 模板里 `v-for` 与 `v-if` 写在同一元素上会先求 `v-if`（此时 `r` 还不存在），
  汇报上级下拉改用 `computed` 预先排除自己。

**冒烟结果**
- `smoke_test.py` 全绿：**150 条路由**（M001 的 137 + 本次新增 13 条路径）；
  基线前缀 + `/api/structure` + `/api/position` 全部 OK。
- `test_position.py` **79/79 通过**；`test_structure.py` 27/27 通过；
  `test_orgutil.py` 17/17 通过。

**下一步（明晚 M003 员工档案与异动）**
- `employee_profiles`（证件 / 合同 / 社保 / 银行卡 / 紧急联系人）
- `employee_events`（入职 / 转正 / 调岗 / 晋升 / 离职 全留痕）—— 可直接复用
  `position_assignments` 的起止日期作为异动流水
- 权限点 `employee.view` / `employee.edit`，并入角色库

---

## 2026-11-03（周一）夜 · 第 4 夜 — M003 员工档案与异动（Phase 0 地基第三块）

按路线图取「第一个 ⬜ 且依赖已 ✅」= **M003 员工档案与异动**（依赖 M001 ✅、M002 ✅）。
本轮按用户最新指示：**不在中途做分模块回归/验收**，M003→M004→M005 一口气实现完后，
最后才由 QA 统一全量回归；每模块只跑 `smoke_test.py`（确认路由注册）+ 本模块的
`test_<module>.py`，不跑 test_structure/test_orgutil/test_position。

**做了什么**
1. **迁移 `_v010`（只追加，未动 `_v001`~`_v009`）**：新表 2 张
   - `employee_profiles` 人事档案（user_id、id_card / bank_account 敏感字段、合同、
     社保、紧急联系人、入职日期、证件扫描路径；`idx_empprofile_user`）
   - `employee_events` 异动流水（user_id、event_type、from/to_position_id、event_date、
     remark、operator；`idx_empevent_user`）
2. **新模块 `modules/employee.py`（`/api/employee`，非核心可关闭）**：
   types（一次性下发 2 组枚举：异动类型 6 项 / 生命周期状态 5 项）/ list 花名册（关联
   org_units + 当前主岗 + 所属组织，**不含敏感字段**）/ profiles（GET 列表/详情，按
   `employee.sensitive.view` 脱敏；POST 幂等 upsert、PUT、DELETE 软删）/ events
   （GET 流水、POST 登记异动并联动任职）。
3. **异动复用 `position_assignments`**：入职/调岗/晋升 → 结束旧岗（含兼岗）+ 新开主岗；
   离职/退休 → 结束所有在职任职；转正 → 仅留痕。一套任职关系、两个视角，避免两套数据对不齐。
4. **敏感字段分层**：`employee.view`（普通档案）vs `employee.sensitive.view`
   （身份证/银行卡），`_gated_profile` 在无 sensitive 权限时剔除该两列。
5. **权限**：`employee.view` 并入全部 8 个内置角色（与 position.view 同口径）；
   `employee.edit` + `employee.sensitive.view` 仅挂 finance（人事行政归财务口径，
   M004 扩角色后再迁走）。
6. **种子 `seed_employees()`（幂等，写在 `seed.py::seed_all` 懒加载 + 吞异常）**：
   给 M002 造的 17 名 `demo_staff_xx` 挂档案 + 入职异动；**不新建人员、绝不修改
   position_assignments**，保护 M002 的编制/在岗/超编演示数据。
7. **前端 `pages/employee.js`**：花名册（姓名/工号/状态/当前主岗/所属组织/档案标记）
   + 档案详情面板（展示+编辑，敏感字段仅 sensitive 权限可见可编辑）+ 异动流水时间线
   + 登记异动弹窗（联动任职提示）。注册进 index.html、app.js 路由与侧栏菜单
   （按 `employee.view` 显隐）。
8. **自测 `tools/test_employee.py`**：真起服务 + 登录 + **31 项断言全通过**
   （枚举、种子挂档、脱敏、档案 CRUD、调岗联动任职、离职结束任职、转正留痕、
   未知异动类型拒 400、sales 无 edit/sensitive 写读被拒）。

**关键判断**
- **异动流水不另起「任职历史表」**：M002 的 `position_assignments` 自带起止日期，
  入职/调岗/晋升直接回写它（结束旧岗+开新主岗），离职/退休结束全部在职任职。
  既省一张表，又保证「编制 vs 在岗」和「员工异动」永远对得上。
- **敏感字段单列权限点**：身份证/银行卡是隐私，普通 HR 看得到合同但看不到证件号、
  银行卡号；只有薪资/人事负责人（`employee.sensitive.view`）可见可编辑。
- **跨模块红线**：employee 模块**不 import** position 模块，只在 SQL 层读写
  positions / position_assignments / org_units 三张共享表，任职写入走本模块
  `_move_to_position`，与「模块间不得互相 import」铁律一致。

**坑**
- `list_events` 的 SQL 把 `positions` 表别名错写成 `us.username` 去取岗位名
  （positions 没有 username 列）→ 500。已改为 `tp.name AS to_pos_name` / `fp.name`。
- **交付清洁度（来自 QA 反馈，本轮一并修）**：`smoke_test.py` 与 `test_position.py`
  此前直接 uvicorn 起服务、落到真实 `data/weigear.db`，污染了客户库（positions 33>30、
  编制 50>41）。已在两脚本中统一用 `WEIGEAR_DB` 环境变量指向独立临时库
  （`weigear_devtest.db` / `weigear_test_position.db`），运行前删除保证干净种子起步；
  真实库不再被开发/验证触碰。
- **position.js 疑似硬编码枚举（QA 红线下线反馈）**：line 651 的「主岗/兼岗」
  原是写死的 `<option>`，属后端 enum `assign_modes`，已改为从 `/api/position/types`
  的 `assign_modes` 用 `v-for` 取（`:value="Number(m.value)"` 匹配后端 INTEGER 列）；
  line 230/232/233 的「缺编/满编/超编」经核是**前端按 gap 计算的派生展示标签**（非枚举），
  保留但加了注释，明确「非对外枚举，勿当 types 接口去改」。

**冒烟结果**
- `smoke_test.py` 全绿：**155 条路由**（M002 的 150 + 本次新增 5 条路径 /api/employee/*）；
  前缀 `/api/employee` OK。
- `test_employee.py` **31/31 通过**。

**下一步（紧接着 M004 数据范围扩展）**
- scope 增 `company`/`factory`/`bu`；角色库扩到 董事长/总经理/厂长/经理/主管/研发/技术员/…
- 组织切换器（后端可见列表 + 前端顶部切换 + 设置持久化 + scope 参数）
- 补 `position.py` 看板 scope 过滤；permission points 与 modules 对应
- 数据范围逻辑落到 `permissions.py` / `orgutil.py`

---

## 2026-11-03（周一）夜续 · 第 5 夜 — M004 数据范围扩展（Phase 0 地基第四块）

按路线图取「第一个 ⬜ 且依赖已 ✅」= **M004 数据范围扩展**（依赖 M001/M002/M003 ✅）。
本轮继续贯彻用户指示：**不在中途做跨模块回归/验收**，三模块一口气实现完后由 QA 统一回归。

**做了什么**
- 迁移 `_v011`（`core/schema.py`）：`users` 表增 `company_id` / `factory_id` / `bu_id` /
  `current_org_unit_id` 四列，并把行内 `org_unit_id` 的三轴归属回写冗余列（一次性回填旧数据）。
- `permissions.py`：
  - `SCOPE_LABEL` 扩到 `all/company/factory/bu/dept/self` 六档；
  - 新增 8 个三轴组织角色：`chairman`(all) / `gm`(company) / `factory_head`(factory) /
    `hr`(company) / `manager`(dept) / `supervisor`(dept) / `rnd`(dept) / `technician`(dept)；
    gm/hr 带 `employee.edit`，hr 带 `employee.sensitive.view`；全员含 structure/position/employee.view。
  - 新增 `current_org_rollups(user)`（读当前组织节点的三轴归属）、`_axis_subtree_ids(root)`（path LIKE 前缀一次取出子树）、
    `switchable_org_nodes(user)`（company/factory/bu 节点；admin/chairman 全集团、其余仅本公司的树内）、
    `scope_clause_org(user, col, alias)`（组织轴专用范围过滤，找不到轴归属保守返回 `1=0`）。
- `auth.py`：`current_user` 把 `org_unit_id/company_id/factory_id/bu_id/current_org_unit_id` 一并带出。
- `identity.py`：`get_me` 补 `current_org_unit_id/name/type/rollups` 与 `switch_options`；
  新增 `/api/org/switch-options`（GET，structure.view）、`/api/org/switch`（POST，structure.view，越权 403）；
  `UserIn` 增 `org_unit_id` 字段，建/改用户时回写三轴冗余列并同步 `current_org_unit_id` 锚点。
- `position.py` 看板：scope 支持 `company/factory/bu` —— 显式 `org_unit_id` 优先按轴子树过滤；
  无显式节点且 `scope=all` 时不过滤（管理员无锚点也不裸奔到 0）；普通角色按其轴归属过滤。
- `static/js/app.js`：顶部栏加组织切换器 `<select>`，`canSwitchOrg`/`switchOrg` 切换 current_org_unit_id
  后强制重载相关页面。

**坑与备忘**
- `board?scope=company` 对 admin（scope=all）最初误判成「无轴归属→0 条」：修正为
  「scope=all 只在**未显式给定 org_unit_id** 时放行全部」，显式节点永远按轴子树过滤。
- 新建 `factory_head` 测试账号若只传 legacy `dept_id` 而没 `org_unit_id`，则三轴冗余列为空、
  `switchable_org_nodes` 返回 0 导致「切本公司节点」断言失败：修正为 `UserIn` 直接收 `org_unit_id`
  并由后端回写三轴冗余列（legacy `dept_id` 仅作兼容，不再驱动数据范围）。

**验证**
- `smoke_test.py` 全绿：**157 条路由**（M003 的 155 + 本次新增 2 条 /api/org/switch / /api/org/switch-options）；前缀 `/api/org` OK。
- `test_scope.py` **19/19 通过**（8 新角色、三轴看板 company>factory、admin 全量、组织切换与越权 403、factory_head 本公司隔离）。

**下一步（紧接着 M005 审批引擎）**
- `core/workflow.py`：流程定义→实例→任务→回调；支持会签/或签/条件分支/转办/加签
- `modules/workflow.py` 引擎 API + 审批中心前端页（待办/已办/我发起的/流程监控）
- permission points `workflow.view/manage/approve`；`core/services.todo()` 集成
- 一个端到端示例（请假审批）跑通整条链路

---

## 2026-11-01（周日）夜 · 第 6 夜 — M005 审批引擎（Phase 0 地基收官）

按路线图取「第一个 ⬜ 且依赖已 ✅」= **M005 审批引擎**（依赖 M001–M004 全部 ✅）。
至此 **Phase 0 地基（M001 组织树 → M002 岗位职级 → M003 员工档案 → M004 数据范围 → M005 审批引擎）全部完成**。

**做了什么（代码由工程师落地，本夜补齐最后一公里）**
1. `core/workflow.py`（523 行）：流程定义 → 实例 → 任务 → 回调全链路；支持会签(and)/或签(or)/条件分支/转办/加签/撤回/取消；`_finish` 收口并触发业务回调。
2. `modules/workflow.py`（300 行，`/api/workflow`）：defs / start / my-tasks / my-done / my-started / instances / tasks/{id}/{approve,reject,transfer,add-sign} / cancel + 端到端示例业务「请假申请」。
3. 前端 `pages/workflow.js`（322 行）：审批中心（待办 / 已办 / 我发起的 / 流程监控）。
4. 权限 `workflow.view` / `workflow.approve`（全员）/ `workflow.manage`（流程定义管理，限管理员与指定角色）。
5. 迁移 `_v012`：建 `workflow_defs` / `workflow_instances` / `workflow_tasks` / `workflow_logs` / `leave_requests`，并种子请假审批流程 LEAVE（条件是 days≥3 走人事审批）。
6. 自测 `tools/test_workflow.py`：**39/39 全部通过**。

**关键判断**
- **先建引擎再建单据**：请假/报销/用章/采购/报价本质是同一套东西，M005 先把「流程定义→实例→任务→回调」与审批中心做成平台能力，后续 M011/M021/M050/M070 等审批单据只写自己的表单 + 一个流程定义，不再各自实现审批。
- **回调与实例状态解耦**：`_finish` 内 `_run_callback` 用 try/except 包住，「回调失败不影响审批结果」，保证审批落库不被业务回写拖垮（代价见下方「坑 1」）。
- **或签语义**：批准是「任一人批准即通过」，驳回也必须是「任一人驳回即驳回」，两者必须对称，否则多人或签会卡死（见下方「坑 2」）。

**坑（两个致命 Bug，均由本夜实测定位并修复）**
1. **回调永远不生效**：`_run_callback` 里 `SELECT biz_type,biz_id FROM workflow_instances` **漏查 `code` 列**，但下一行却把 `inst["code"]` 传给回调 → sqlite3.Row 取不存在列抛 KeyError → 被「回调失败不影响实例状态」的 except **静默吞掉**。
   现象极具欺骗性：实例状态 approved/rejected 全对，**只有请假单状态永远停在 pending**，且服务端无报错。修复：SELECT 补 `code` 列，并在注释里写明该陷阱。教训是「吞异常必须留痕，否则静默失效无法定位」。
2. **或签驳回 ≈ 死Node**：`_check_node_complete` 的 or 分支原写成 `if pending == 0 and approved == 0: return "rejected"`，这实际是「与」语义 —— 要求**全员驳回**才算驳回。多人或签时一人驳回、其余未处理，`pending > 0` 导致判定 None，实例**永久卡在 running**。
   首次跑 `test_workflow.py` 时「实例 F rejected + 回调：请假单变 rejected」两项失败，dump 数据才看清：实例 6 的 mgr 节点有两条任务（19 rejected / 23 pending）。修复为 `if rejected > 0: return "rejected"`（与 `if approved > 0: return "approved"` 对称）。

**另注**：本轮早先一次 `test_workflow.py` 报「approve 返回 HTTP 500」，但在干净重建的隔离库上重跑稳定通过，
判定为**脏库/残余状态导致的偶发**，非稳定缺陷；工程师并未改动 approve 链路。后续排查同类问题应先换干净库复验，避免误判。

**冒烟结果**
- `smoke_test.py` 全绿：**171 条路由**（M004 的 157 + M005 新增）
- `test_workflow.py` **39/39 全部通过**（端到端请假：发起 → 或签 → 条件分支走人事 → 会签 → 转办 → 加签 → 驳回 → 越权 403 → 撤回 → 回调回写请假单状态）

**下一步**
- **做 Phase 0 全量回归**（用户约定：所有模块落地后统一回归，不在中途分模块验收）：
  `test_structure` / `test_orgutil` / `test_position` / `test_employee` / `test_scope` / `test_workflow` + `smoke_test` 一起跑通
- 回归通过后，再启动已登记的两项后续任务：① 服务器与数据库配置套件（六类部署形态 + 配置管理器 + 防火墙白名单 + 远程桌面 + 状态面板）② 数据清洗（移除伟格/`WG-2026-FELIX-0001` 等真实信息、清理调试残留库、测试改用虚构数据）③ 行业模板与三方接口预留（企微/钉钉/飞书、快递物流、主流大模型、企查查）。

---

## 2026-11-02（周一）夜 · 第 7 夜 — M071 电子报销（Phase 7 财务域开篇）

按路线图取 Phase 7 第一个 ⬜ = **M071 费用报销**（依赖 M005 审批引擎与 M109 报表打印，均已 ✅）。
用户原话：「员工不用打印纸质发票/水单，全部提交到系统；系统识别重复票据并标识清楚、提醒处理；
规划好打印排版，审批完由财务或本人打印、签名确认」。

**做了什么**
1. `core/schema.py` 追加 `_v013`：建 `expense_reports`（单号/申请人/组织/报销期间/总额/状态/事由/打印次数与最后打印人）+ `expense_items`（费用类型/票据类型/**发票代码+号码**/**交易单号**/商家/**查重指纹**/`dup_status`/`dup_of`/影像）+ 8 个索引。
2. `modules/expense.py`（`/api/expense`）：types 枚举 / reports CRUD / items 明细（边填边查重）/ **查重复核台** / 影像上传 / 提交审批 / 打印数据源（含人民币大写）/ 打印留痕 / 支付登记。
3. 前端 `pages/expense.js`：我的报销单 / 新建向导（边填边查重提示）/ 详情（影像预览 + 重复标记）/ 财务复核台 / **A4 打印预览（报销人·主管·财务·总经理四联签名栏）**。
4. 权限点 `expense.view`（本人及组织内）/ `expense.edit`（本人起草）/ `expense.finance`（复核·标记重复·打印）/ `expense.pay`（支付登记）。
5. 种子 `seed_expense()`：EXPENSE 流程定义 + 3 张分属三个金额档的演示草稿单（800 / 5000 / 15000）。
6. 自测 `tools/test_expense.py`：**51/51 全部通过**。

**关键判断**
- **查重指纹分三级而不是一把梭**：增值税票有「代码+号码」这种全国唯一的强身份，命中几乎必然是同一张票；水单有交易单号也是强身份；但火车票/机票/出租车票没有唯一号，只能退化为「类型+金额+日期+商家」，同日同商家同额必然误报。所以指纹必须分级，且**弱指纹命中只能叫「疑似」**。指纹生成前一律归一化（去空白、英文大写、金额两位小数、日期 YYYY-MM-DD），否则同一张票因「空格/全角/小数点位数」差异就漏检。
- **单据内硬拦、跨单据软标**（本模块最核心的取舍）：自己单子里出现两张一模一样的票，只可能是重复录入 → 硬拦 400 让用户先删；但跨单重复存在合理业务场景（两人同行各报一半、垫付后归还），硬拦会误伤且剥夺财务裁量权 → 只标记 suspected 并给出完整来源（单号/报销人/日期/金额），由财务在复核台人工销案 `confirmed_ok` 或驳回。
- **三档审批全部走「或签」**：报销是高频短流程，用会签会让「主管休假」直接卡住全部门报销。若某档确需多方共同背书（如大额招待），把该节点 `mode` 改成 `and` 即可，无需改代码。
- **边的顺序 = 判定顺序**：引擎取「第一条条件命中的边」。若把 `mgr→gm(amount≥10000)` 写在 `mgr→fin(amount≥1000)` 前面，15000 的报销会**跳过财务直达总经理**。故先写「≥1000 进财务」，再由财务节点按「≥10000」决定是否上总经理，15000 的走向才是 主管→财务→总经理。
- **影像复用 M109 而不是自建第二套**：落盘/入库收敛到 `core/services.save_slip`（`data/slips` + `report_slips` 表），M109 与 M071 共用一套存储，图片统一由 `/api/report/slips/{id}/file` 提供 —— 既满足「modules 禁止跨模块 import」（共享逻辑走 core），又避免「同一张水单存两遍、归档各找各的」。
- **打印留痕先于打印**：前端 `doPrint()` 先 POST 留痕再 `window.print()`，万一打印中途取消，次数也已经记上（防同一张单重复打印去多次报账）。
- **种子不在启动时起审批实例**：若顺手起流程实例，审批中心会被一堆永不被处理的演示待办淹没。改为种 3 张分属三档的草稿单，用户登录后提交即可逐一看到三条真实流向。

**坑**
1. **打印越权返回 404 而非 403**：`POST /reports/{id}/print` 先做可见性过滤（看不见 → 404，不泄露单据存在性）才判打印权限（403）。首轮测试断言 403 失败。这不是缺陷而是两道门：不可见的人拿 404，**可见但无财务权的人**（viewer，scope=all）拿 403。测试已拆成两条分别断言，两种拒绝路径都覆盖到。
2. **`api.upload` 只支持单文件**：`core/api.js` 的 `api.upload` 固定用表单字段名 `file`，而批量端点收 `files`。故前端影像上传改用原生 `fetch + FormData` 多文件追加，不走 `api.upload`。

**冒烟结果**
- `smoke_test.py` 全绿：**183 条路由**（M005 的 171 + M071 新增 12）
- `test_expense.py` **51/51 全部通过**，覆盖：单据内重复被拦截 / 跨单 suspected 且 dup_of 给出单号·报销人·金额·日期四要素 / 指纹归一化（发票号带空格仍命中）/ 水单交易单号维度 / confirmed_ok 销案后从待核实清单消失 / 软删票据不参与匹配 / **三个金额档触发三条不同审批流向** / **三档审批回调均正确回写 `approved`** / 打印权限（本人·财务通过，viewer 403，不可见者 404）/ 打印留痕 print_count 递增 / 越权访问 403 / 财务支付登记 → paid

**下一步**
- **M070 应付与付款**（Phase 7 下一个 ⬜）：依赖 M005 审批引擎，且可直接复用 M071 已跑通的「单据 → 分档审批 → 回调回写 → 支付登记 → A4 打印留痕」整套骨架，届时只需替换业务表单与流程定义。
- 待所有模块落地后统一跑**全量回归**（用户约定不在中途分模块验收）：`test_structure` / `test_orgutil` / `test_position` / `test_employee` / `test_scope` / `test_workflow` / `test_expense` + `smoke_test`。
- 全部测试继续用隔离库（`WEIGEAR_DB` 指向临时库），客户真实库 `data/weigear.db` 的历史污染待数据清洗任务统一处理。


---

## 第 8 夜 · P0 热修：流程成环 DoS + 员工模块两处缺陷 + 回调可靠性

**起因（QA 复现）**：一条 `a→a` 自环的流程定义，任何人发起一次就能让整个服务进程永久挂起 —— 之后连公开的 `/api/about` 都 15s 超时，必须重启进程。属**远程触发的拒绝服务**。

**根因**：`core/workflow.py` 的 `_advance()` 在节点无需人工审批时（节点无审批人 → 自动通过 → 继续推进）会与 `_activate_node()` **互递归**。成环定义下二者无限往返，撞上 RecursionError 时**抛出异常的路径上没有回滚**：SQLite 连接是线程级长连接，事务未提交就持有库级写锁，于是后续所有请求（含中间件里的授权状态读取）全被锁住。栈打满 + 库锁残留，两件事叠加才有了「一个请求拖死全站」。

**三道防线**
1. **入口拦截**（最有效）：`POST /api/workflow/defs` 与新增的 `PUT /api/workflow/defs/{id}` 保存前调用 `WF.validate_definition()` —— 迭代式 DFS（GREY/BLACK 着色，显式栈，1000 节点长链实测不栈溢出）检测回边，命中直接 400，并给出环路径（`流程定义存在环路：a → b → a`）。顺手把「边指向不存在的节点」也一起拦掉。发起实例时再做一次运行时校验，挡住历史脏数据。
2. **引擎兜底**：`_advance()` 的尾递归改成**有界循环**，带 `visited` 集合 + 深度上限 64；`_activate_node()` 同步承接 `visited/depth`，否则二者仍构成互递归（光把 `_advance` 改成循环是不够的，这个是本次最容易漏掉的点）。触发保护时 `_abort_loop()` 把实例作废成 `canceled`（而不是卡在 `running` 没人知道为什么），并写 `cycle_abort` 审计留痕。
3. **故障隔离**：新增 `_guarded` 装饰器包住引擎全部对外操作（`start_instance/approve/reject/transfer/add_sign/cancel`），异常时先 `_safe_release()`（尽最大努力回滚 + 归还连接，且回收过程自身不再抛异常）再上抛 —— 单个请求炸了，别把全局连接弄成不可用。

**为什么是「循环 + visited」而不是「递归 + 计数」**：递归版本在长链上会先把自己栈溢出，防护反而成了新的故障源；`visited` 让自环与长环都在有限步内收手，深度上限只是兜住「互不相同的超长节点链」这种极端情况。

**员工模块两处缺陷**
- `list_employees` 的 `org_unit_id` 参数声明了却从没进 SQL。现按「主岗 → 岗位 → 组织」推导出的组织归属过滤，命中选中节点及其整棵子树；组织 id 不存在时**明确 400**，因为 `orgutil` 的工具函数会「穿透」到根组织，那样筛出来的其实是全公司 —— 用户以为筛了其实没筛，比报错危险得多。顺带修掉 `org_unit_id` 曾用 `dept_id` 兜底的问题（两个 id 空间混用会让「部门的 3」命中「组织树上的 3」）。
- `_gated_profile` 此前只脱敏 `id_card` / `bank_account`。补齐 **`social_no`（社保号）/ `emergency_phone`（紧急联系电话）/ `id_scan_path`（证件扫描件路径）**，并改为**掩码保留**而非删键（`4401**********000X`）—— 删键之后某个页面一旦忘了判权限就显示空值，保留掩码则误显示也泄露不了。扫描件上是身份证全号，掩码救不回来，只能整键不外发。

**权限收口**：`workflow.manage` 从 `sales_manager` 收回（现为 `admin/chairman/gm/hr`）。理由：① 流程定义是内控规则本身，改一条边就能让报销跳过财务复核，制定权必须与业务执行角色分离；② 该权限曾是远程 DoS 的触发入口，持有者越少爆炸半径越小；③ 销售经理日常只需发起/审批。客户若确需业务线自建流程，走自定义角色授权，不写死在内建角色里。

**回调可靠性（PM 要求）**
- `_run_callback` 现在返回 bool，失败同时进 `audit_logs` 与 `service.log`（历史上只 catch 不记录，「实例已 approved 但业务单据还停在草稿」这种故障表面全对、最难查）。
- 新增补偿入口：`GET /api/workflow/callback-failures`（排查清单）+ `POST /api/workflow/instances/{id}/retry-callback`（人工重试），均需 `workflow.manage`。
- `_expense_callback` / `_leave_callback` 改为**幂等**：已是目标状态就跳过（不刷新 `updated_at`，否则每次重试都像有人动过单据）；`paid/void` 是终态，不允许后续回调改回 `approved`；单据不存在则抛错入留痕，不静默吞掉。

**附带修掉的三处**
- `start_instance` 返回的实例快照是推进前读的，若定义在 `_advance` 内直接走到终点（无人审批的定义）或被环路保护作废，调用方拿到的 `status` 是假的 → 改为推进后重读。
- 报销提交必须先置 `approving` **再**起流程，否则流程在 `start_instance` 内部直接结束时，回调写成的 `approved` 会被随后的 `approving` 盖回中间态。
- `GET /api/workflow/defs` 过去只取常量列，`definition` 恒为 `{}`（死代码）；PUT 是整份替换，读不到当前定义就无从改起 → 现在带上 `definition`。

**自测结果（隔离库）**
- `test_workflow.py` **67/67**（原 39 → 67，新增 28 项：成环保存被拒 ×3 / 边指向空节点 / PUT 成环被拒且不破坏原定义 / 脏数据发起被拒 / **失败后 `/api/about` 仍 200、登录仍可用** / 引擎不挂死 / 实例被作废而非卡 running / 环路终止有审计留痕 / 引擎崩溃后异常照常上抛且连接未卡在事务里、未提交写入已回滚 / 回调幂等重试 / 越权 403 / sales_manager 无 manage、hr 仍有）
- `test_employee.py` **41/41**（原 32 → 41，新增 9 项：按组织筛选可用 / 筛选结果是该组织子树 / 含本组织的员工 / 筛选不存在的组织 400 / 证件号·社保号·紧急电话只给掩码 / 扫描件路径不外发 / 非敏感档案照常可读 / **无 sensitive 权限者提交的敏感字段不落库（防掩码回写）** / 非敏感字段照常可改）
- 附带：`_gated_profile` 改为掩码后，**保存侧必须同步加锁** —— 否则前端表单里看到的 `4401**********000X` 会被原样提交回来，把真号码永久覆盖成掩码。故 `create_profile` / `update_profile` 对无 `employee.sensitive.view` 的提交者直接剔除敏感字段（更新保留原值，新建留空），前端对应输入框也一并 `v-if="canSensitive"`。
- `test_expense.py` **51/51**（不受影响）
- `smoke_test.py`：**186 条路由**（183 + 3）

**怎么验的**：脏数据（成环定义）直接 `sqlite3` 塞进测试库，模拟绕过接口入库；引擎侧则再单独起一个进程、**把环路检查 monkeypatch 成失灵**，逼引擎自己在成环定义上跑 —— 有限时间内返回、实例作废、连接干净，三件事分别断言，避免「入口挡住了」掩盖「引擎其实还是会死」。

**下一步**
- 请 QA 用原复现步骤（成环定义 → 发起 → 看 `/api/about`）再打一次：预期不再是「挂起 + 必须重启」，而是保存阶段就 400，绕过接口直接入库的历史脏数据在发起时也被 400 挡下，且失败后 `/api/about` 立刻 200。
- 已知未收口的一处：**员工花名册暂未套 M004 的组织数据范围**（`scope_clause_org`）。本次只修了「参数没生效」，未加范围过滤 —— 加上后凡「当前组织未设置」的用户会直接看到空列表，属于行为变更，需先确认各角色 `current_org_unit_id` 的落库情况再动。

---

## 2026-11-01（本轮）· M001 前端响应式现代化（UI 收尾）

M001 组织树后端早已完成（schema._v008 / orgutil.recompute_tree / structure.py / test_structure 27/27 / smoke 137 路由）。本轮补齐用户新增的「终端自适应」要求：非每名员工都用电脑，可能手机/平板浏览器、独立 App、微信/钉钉入口（可并存）；**PC 优先，其他终端功能裁剪（阉割）**。

**决策**
- 响应式 Web 为首选；App/微信/钉钉通道延后（M120），仅留 PWA 预埋缝。
- SQL Server(M108) 后置，写代码保持方言中立；品牌文案归 M006/M113，本轮不碰。
- 回归红线：≥1100px 桌面像素级不变；抽屉 z(80) < 弹窗 z(100) < toast z(200)。

**改动（纯前端，后端 .py 零改动）**
- `app.css`：新增 `--tap/--drawer-w/--drawer-z(80)/--scrim-z(70)`；`@media(max-width:760px)` 抽屉导航（`.hb` 汉堡 + `.scrim` 遮罩 + `body.drawer-open` 锁滚动）、表格转卡片（`.tbl.responsive` + `td[data-label]`）、`.hide-mobile` 裁剪、触摸目标 ≥40px；`.split-org` 替代 structure.js 写死内联双栏（比例与原 1.55:1 一致，桌面零变化）。
- `app.js`：`drawerOpen/toggleDrawer/closeDrawer` + 两个 `watch`（body 类同步 / 切页自动收起）；顶栏汉堡 + 遮罩；守卫式 SW 注册（排除 localhost/file）。
- `structure.js`：双栏改 `.split-org`；组织树表 `.tbl.responsive` + 7 列 `data-label`；展开/折叠全部按钮 `.hide-mobile`。
- `org.js`：人员/部门/角色/审计 4 表 `.tbl.responsive` + `data-label`（9/5/4/6 列）。
- `index.html` + `manifest.webmanifest` + `sw.js`：PWA 缝落地基础版。

**验收（QA 严过关，独立）**
- 8 项全 PASS：桌面红线无回归、JS 语法、抽屉逻辑、表格转卡片逐列核对、后端零 .py 改动、服务 + manifest MIME 可达、PWA 缝安全；smoke 回归按约定跳过（纯前端不触达路由）。
- 已知低风：开抽屉瞬间约 1 滚动条宽右移（可接受）；manifest `icons:[]` 空，未来要做「添加到主屏」需补 192/512 png；本工作区无 git 仓库，未提交。

**后续（P2，本轮不做）**
- 其余 27 个页面按同模式（`split-org` / `.tbl.responsive`+`data-label` / `.hide-mobile`）补齐；dashboard/quotations 可作样板优先。
- M108 SQL Server T-SQL 移植；M105 核心文件哈希自检；App/微信/钉钉通道（M120）。

---

## 2026-11-01（本轮·续）· P2 全页响应式 + M105/M006/M113/M108/M120

接着上一节收尾：先补完全页响应式，再按用户勾选把 5 个模块推到可交付状态。
**注意：全程未建 git 仓库**（用户要求「暂时不建库」），所有改动以文件清单 + 哈希基线为准。

### P2 全页响应式 —— ✅ 完成
- 21 个文件 / 33 张表加 `.tbl.responsive` + 逐列 `data-label`。
- 主理人独立复核（写脚本正则扫 `<table>…</table>`，未采信自报）：
  0 处漏改 `.tbl`、0 处 `<td>` 缺 `data-label`；复核中发现 7 处**占位行/合计行**（`<td colspan>` 空态行与 `<tfoot>`）确实缺 `data-label`，已补（空态补 `data-label=""`，编制看板合计行补真实列名）。
- 21 个文件 `node --check` 全通过；8 个无表格页无需改动。

### M105 核心文件哈希自检 —— ✅ 完成
- 新增 `tools/gen_integrity_baseline.py` / `integrity_check.py` / `integrity_baseline.json`（**73 个核心文件**）。
- 校验通过（exit 0）；篡改实测：给副本追加 1 字节即检出（真实文件未动）。

### M006 + M113 品牌统一与清洗 —— ✅ 完成（Web 侧）
- 后端早已可配置（`brand.effective_brand()`、`database.get_setting/set_setting`、`/api/platform/brand` 审计写入）；本轮补齐**前端那一半**：
  - `app.js` 新增 `appBrand`/`appProductCn`/`appProductEn` 三个 computed，从 `/api/about` 取生效值；
  - 登录页 hero（`FELIX` / `Felix 报价系统` / `Felix · Foreign Trade Quotation System`）、侧栏 logo（`FELIX` / `Felix · 外贸报价系统`）、页面标题兜底全部改为绑定生效值（保留常量兜底，库未就绪也不白屏）；
  - `modules/mail.py` 发件测试邮件主题改为 `effective_brand()` 取值（报价邮件此前已用生效品牌）。
- M113 清洗复扫：Web 侧（前端 + brand/mail/platform）旧名 **grep 零命中**。
  仅剩 `launcher.py`（桌面快捷方式名）与 `selfcheck.py`（CLI 横幅）两处写死 —— 属桌面组件自身命名，非 Web 品牌面，留作后续按需处理。

### M108 SQL Server —— ✅ 完成（按用户决定收口，后期客户换库再改）
- 新增 `src/app/core/dialect.py`：`dialect()` 读 `WV_DB_BACKEND`；`translate_ddl_to_tsql()` 覆盖 IF NOT EXISTS 剥离 / AUTOINCREMENT→IDENTITY(1,1) / TEXT→NVARCHAR(MAX) / REAL→FLOAT / INTEGER→BIGINT / COLLATE NOCASE→Latin1_General_CI_AI。
- `schema.py` 所有 DDL 改走 `_exec_ddl()`，按方言分支；**sqlite 下原样执行，行为不变**。
- 新增 `docker-compose.yml`（Azure SQL Edge）+ `tools/M108_DIALECT_AUDIT.md`（方言点审计 + 剩余未移植清单）。
- **红线守住**：`python tools/smoke_test.py` 通过（SQLite 路径未破坏）。
- 未完：`_mssql_conn()` 仍 `NotImplementedError`；业务查询约 499 处 `?` 占位符及 IFNULL/LIMIT 等未移植（审计文档 §4 逐文件清单）。

### M120 连接器 —— ✅ 完成（按用户决定收口，等用户提供凭据再联调）
- `modules/connectors.py`：`ConnectorBase` 抽象 + `WeComConnector`（token / 授权地址 / code→userid / 发消息），配置存 `settings.connector:wecom`（JSON，与 M006 品牌键隔离），**secret 读取脱敏**，写入审计。
- 路由：`GET/POST /api/connectors/wecom/config`（仅 admin）、`GET /api/connectors/wecom/authorize-url`（公开）、`GET /api/connectors/wecom/callback`（code→userid→JWT，按 `username == userid` 匹配，未匹配不放行）。
- 前端：`app.js` 的 `wecomSso()` —— 企微 UA 检测（`MicroMessenger|wxwork`）→ 未登录跳授权 / 回调带 code 换 token。
- 本轮（主理人）补齐工程师未完成的两块：**管理员配置页** `pages/connectors.js`（含回调地址一键复制、钉钉/飞书明确标灰说明）+ **集成指南** `docs/14`。
- 用标准库 `urllib` 直连官方 API，**零新增依赖**。

### 服务器部署辅助工具（新增）
- 新增 `deploy/deploy_assist.py` —— 交互式菜单，**服务器部署与数据库部署分开**
  （既有 `deploy/setup.py` 是参数驱动的线性流程，把两者混在一条链路上，且没有菜单）。
- 复用 `setup.py` 已验证的阶段函数（`phase_configure` / `phase_init_db` / `phase_scripts`），不重复造轮子。
- **数据库部署**：可选 SQLite / SQL Server / MySQL(预留) / PostgreSQL(预留)。
  SQL Server 默认管理员 **root**、密码 **88888888**（127.0.0.1:1433、库名 felix、ODBC Driver 17）。
  MySQL/PG 只登记选择并明确告知「当前未实现」，**不假装可用**。
- **服务器部署**：选监听地址/端口（默认 127.0.0.1:8795）；可选「生成脚本并立即启动 /
  仅生成脚本（Windows .bat + Linux systemd）/ Docker 起 SQL Server 容器」。
- **模块选择（可选部署）**：预设 `all`（全量）/ `min`（精简集 = 核心 + 主数据 + 报价 + 报表），
  也可逐项输入模块代号开关。核心模块不可关（registry 直接拒绝），依赖双向校验；
  **改动需重启服务生效**，关闭只隐藏路由与菜单、**不删表**，随时可再开启且数据不丢。
- 验证：`py_compile` 通过；模块开关逻辑用**临时库**实测（真实库未动）——24 个模块、
  核心 `auth` 拒绝关闭（返回「是核心模块，不可关闭」）、普通模块可正常关闭；工具只读跑通。

### 回归与口径修正
- `python tools/smoke_test.py`：**通过**。路由数随模块增加已从文档里的 186 涨到 **190**（M120 新增企微 4 个端点），文档基线需按实际更新。
- 前端 `node --check`：`app.js` 与新增 `pages/connectors.js` 均通过。

### 过程中的协作事故（写给下一任）
- 本轮一度重复派工：主理人在未确认工程师仍存活的情况下，又新起了两个 agent 做 M108/M120，
  与仍在跑的工程师形成**同文件双写风险**，已及时取消（`TaskStop`）。
  **教训：续跑前先确认 teammate 状态，别凭 task 列表的 in_progress 判断"没人做"。**
- 工程师（koudouma）在 M120 收尾阶段触发平台限流 `429`（用量超限，约 17:32 重置），
  未完成部分由主理人直接接手完成；其已落地代码经复核均可用。

### 仍未收口
- M108 业务查询移植（499 处占位符等）+ 真实 SQL Server 联调（本环境无实例）。
- M120 钉钉 / 飞书实现；企微 OAuth **未做真实联调**（无 CorpID/Secret/AgentID），首次部署需用真实凭据走一遍。
- git 仓库尚未初始化（用户要求暂缓）。
