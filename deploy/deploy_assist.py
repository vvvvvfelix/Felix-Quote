"""服务器部署辅助工具（交互式）—— 服务器部署 与 数据库部署 分开。

为什么要单独做这个工具：
    既有 deploy/setup.py 是「参数驱动 + 线性流程」（检测→配置→建库→脚本→启动→验证），
    服务器与数据库混在一条链路上，且没有菜单。实际交付时会遇到两种场景：
      ① 只想换/初始化数据库，不想动服务；
      ② 只想重配端口与启动方式，数据库保持不动。
    本工具把两者拆成独立入口，交互选择参数，**复用 setup.py 已验证的阶段函数**
    （phase_configure / phase_init_db / phase_scripts），不重复造轮子。

数据库类型：
    1) SQLite      —— 零依赖，单机/小团队，**默认**
    2) SQL Server  —— MSSQL，局域网/多用户；默认管理员 root / 密码 88888888
    3) MySQL       —— 预留（当前 database.py 未实现该分支）
    4) PostgreSQL  —— 预留（当前 database.py 未实现该分支）
    选 3/4 时本工具**只登记配置并明确告知不可用**，绝不假装能用。

用法：
    python deploy/deploy_assist.py
"""
import importlib.util
import json
import os
import platform
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPLOY_DIR = os.path.join(ROOT, "deploy")
SRC = os.path.join(ROOT, "src")
CONF_PATH = os.path.join(DEPLOY_DIR, "deploy.conf")
DBCONFIG_PATH = os.path.join(SRC, "data", "dbconfig.json")

# 让 `from app...` 可用（setup.py 内部也会再插一次，这里双保险）
sys.path.insert(0, DEPLOY_DIR)
sys.path.insert(0, SRC)

# 数据库默认管理员（用户指定）
DEFAULT_DB_USER = "root"
DEFAULT_DB_PWD = "88888888"
DEFAULT_DB_NAME = "felix"
DEFAULT_DB_PORT = 1433
DEFAULT_DB_SERVER = "127.0.0.1"
DEFAULT_ODBC_DRIVER = "ODBC Driver 17 for SQL Server"

DB_TYPES = {
    "1": ("sqlite", "SQLite —— 零依赖，单机/小团队（默认）"),
    "2": ("mssql", "SQL Server (MSSQL) —— 局域网/多用户"),
    "3": ("mysql", "MySQL —— 预留，当前代码未实现"),
    "4": ("postgresql", "PostgreSQL —— 预留，当前代码未实现"),
}


# ---------------------------------------------------------------------------
# 加载既有 setup.py（用显式路径，避免与 setuptools 的 setup 重名）
# ---------------------------------------------------------------------------
def load_setup():
    path = os.path.join(DEPLOY_DIR, "setup.py")
    spec = importlib.util.spec_from_file_location("felix_deploy_setup", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def hr(title: str = "") -> None:
    print("\n" + "=" * 62)
    if title:
        print(f" {title}")
        print("=" * 62)


def ask(prompt: str, default: str = "") -> str:
    """带默认值的输入：直接回车即用默认值。"""
    tail = f" [默认: {default}]" if default else ""
    try:
        v = input(f"{prompt}{tail}\n  > ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return v or default


def ask_int(prompt: str, default: int) -> int:
    raw = ask(prompt, str(default))
    try:
        return int(raw)
    except ValueError:
        print(f"  输入非法（{raw}），改用默认 {default}")
        return default


def read_conf() -> dict:
    conf = {}
    if os.path.isfile(CONF_PATH):
        try:
            with open(CONF_PATH, encoding="utf-8") as f:
                conf = json.load(f)
        except Exception:
            conf = {}
    return conf


def read_dbconfig() -> dict:
    if os.path.isfile(DBCONFIG_PATH):
        try:
            with open(DBCONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def show_current() -> None:
    conf = read_conf()
    dbc = read_dbconfig()
    hr("当前部署配置")
    if not conf:
        print("  （尚未部署过，deploy/deploy.conf 不存在）")
    else:
        print(f"  数据库类型(后端) ：{conf.get('db_backend', 'sqlite')}")
        print(f"  数据库位置/库名 ：{conf.get('db', '-')}")
        print(f"  服务监听        ：{conf.get('host', '127.0.0.1')}:{conf.get('port', '-')}")
        print(f"  部署时间        ：{conf.get('deployed_at', '-')}")
        if conf.get("mssql"):
            m = conf["mssql"]
            print(f"  SQL Server      ：{m.get('server')}:{m.get('port')} "
                  f"库={m.get('database')} 账号={m.get('user')} 密码={'*' * 8}")
    if dbc:
        print(f"  data/dbconfig.json 已存在：{dbc.get('server')}:{dbc.get('port')} "
              f"库={dbc.get('database')} 账号={dbc.get('user')}")
    print(f"  运行平台        ：{platform.platform()} / Python {sys.version.split()[0]}")


# ---------------------------------------------------------------------------
# ① 数据库部署
# ---------------------------------------------------------------------------
def menu_database(setup) -> None:
    hr("数据库部署 —— 选择数据库类型")
    for k, (code, desc) in DB_TYPES.items():
        print(f"  {k}) {desc}")
    choice = ask("请选择数据库类型", "1")
    if choice not in DB_TYPES:
        print("  无效选择，返回主菜单。")
        return
    backend = DB_TYPES[choice][0]

    conf = read_conf()
    db_path = conf.get("db") or os.path.join(SRC, "data", "wv.db")

    if backend == "sqlite":
        db_path = ask("SQLite 数据库文件完整路径", db_path)
        setup.phase_configure(conf.get("port", 8795), db_path, "sqlite", None)
        print("\n[数据库] 开始初始化（建表 → 版本迁移 → 组织种子 → 管理员）…")
        try:
            info = setup.phase_init_db(None)
            print(f"  表 {info['tables']} 张 / 用户 {info['users']} / 部门 {info['departments']}")
            print(f"  管理员：{info['admin']} / 口令 {info['password']}")
            print("[数据库] SQLite 初始化完成。")
        except Exception as exc:
            print(f"[数据库] 初始化失败：{type(exc).__name__}: {exc}")

    elif backend == "mssql":
        print("\n  提示：SQL Server 传统管理员是 sa；按你的要求这里默认 root / 88888888，可改。")
        server = ask("SQL Server 地址", DEFAULT_DB_SERVER)
        port = ask_int("端口", DEFAULT_DB_PORT)
        database = ask("数据库名", DEFAULT_DB_NAME)
        user = ask("管理员账号", DEFAULT_DB_USER)
        pwd = ask("管理员密码", DEFAULT_DB_PWD)
        driver = ask("ODBC 驱动", DEFAULT_ODBC_DRIVER)
        mssql = {"server": server, "port": port, "database": database,
                 "user": user, "password": pwd, "driver": driver}
        setup.phase_configure(conf.get("port", 8795), db_path, "mssql", mssql)
        print(f"\n[数据库] SQL Server 配置已写入：{DBCONFIG_PATH}")
        print(f"        {server}:{port} 库={database} 账号={user}")
        # 诚实标注：代码侧 _mssql_conn 仍是 NotImplementedError（业务查询待客户切换时移植）
        print("\n  ⚠️ 当前状态（务必知悉）：")
        print("     - 连接配置已就绪，应用启动时会读取该配置；")
        print("     - 但 database.py 的 _mssql_conn() 目前仍 raise NotImplementedError，")
        print("       业务查询的 T-SQL 移植按计划留到「客户实际切换数据库」时完成（M108 已列为完成项）；")
        print("     - 因此**现在选 MSSQL 还不能真正跑起来**，真正切换前请先完成移植。")
        try:
            import pyodbc  # noqa: F401
            print("     - 已检测到 pyodbc，可做连通性测试。")
            if ask("是否测试连通性？(y/N)", "N").lower().startswith("y"):
                _test_mssql(mssql)
        except ImportError:
            print("     - 未安装 pyodbc（pip install pyodbc），暂不做连通性测试。")

    else:
        # MySQL / PostgreSQL：只登记，明确告知不可用
        print(f"\n  ⚠️ {backend} 为**预留类型**，当前 database.py 只有 sqlite / mssql 两个分支。")
        print("     本工具只把选择登记到 deploy.conf，不做任何初始化、也不会让应用连上它。")
        print("     若要真正支持，需改动：")
        print(f"       - src/app/database.py：DB_BACKEND 增加 '{backend}' 分支 + 连接工厂；")
        print("       - src/app/core/dialect.py：dialect() 与 translate_ddl_to_tsql 增加对应翻译。")
        setup.phase_configure(conf.get("port", 8795), db_path, backend, None)
        print(f"\n[数据库] 已登记类型 = {backend}（不可用，待实现）。")


def _test_mssql(mssql: dict) -> None:
    import pyodbc
    conn_str = (
        f"DRIVER={{{mssql.get('driver', DEFAULT_ODBC_DRIVER)}}};"
        f"SERVER={mssql['server']},{mssql['port']};"
        f"DATABASE={mssql['database']};UID={mssql['user']};PWD={mssql['password']}"
    )
    try:
        with pyodbc.connect(conn_str, timeout=5) as cn:
            cn.execute("SELECT 1")
        print("  ✅ 连通性测试通过。")
    except Exception as exc:
        print(f"  ❌ 连通性测试失败：{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# ② 服务器部署
# ---------------------------------------------------------------------------
def menu_server(setup) -> None:
    hr("服务器部署 —— 应用本体")
    conf = read_conf()
    backend = conf.get("db_backend", "sqlite")
    db_path = conf.get("db") or os.path.join(SRC, "data", "wv.db")
    print(f"  当前数据库后端：{backend}（要改请先走「数据库部署」）")
    print("\n  部署方式：")
    print("   1) 生成启动脚本并立即启动")
    print("   2) 仅生成启动脚本（不启动）")
    print("   3) 用 Docker 起 SQL Server 容器（仅当后端是 mssql 时有意义）")
    mode = ask("请选择", "2")

    host = ask("监听地址（0.0.0.0 允许局域网访问）", conf.get("host", "127.0.0.1"))
    port = ask_int("服务端口", conf.get("port", 8795))

    if mode == "3":
        if backend != "mssql":
            print(f"\n  当前后端是 {backend}，不需要 SQL Server 容器。")
            print("  若要用容器，请先到「数据库部署」选择 SQL Server。")
            return
        print("\n  将执行：docker compose up -d（见 docker-compose.yml，Azure SQL Edge）")
        print("  启动后回到「数据库部署」填同一套连接参数即可。")
        if ask("现在执行 docker compose up -d？(y/N)", "N").lower().startswith("y"):
            try:
                import subprocess
                print(subprocess.run(["docker", "compose", "up", "-d"], cwd=ROOT,
                                     capture_output=True, text=True).stdout)
            except Exception as exc:
                print(f"  执行失败：{exc}")
        return

    # 先落盘配置（含 host/port），再生成脚本
    conf = setup.phase_configure(port, db_path, backend,
                                 conf.get("mssql") if backend == "mssql" else None)
    conf["host"] = host
    with open(CONF_PATH, "w", encoding="utf-8") as f:
        json.dump(conf, f, ensure_ascii=False, indent=2)

    try:
        scripts = setup.phase_scripts(port, db_path, backend)
        print("\n[服务器] 已生成启动脚本：")
        for s in scripts:
            print(f"   - {s}")
    except Exception as exc:
        print(f"[服务器] 生成脚本失败：{type(exc).__name__}: {exc}")
        return

    print(f"\n  访问地址：http://{host}:{port}")
    if host == "0.0.0.0":
        print("  局域网其他机器请用本机实际 IP 访问。")

    if mode == "1":
        if backend != "sqlite":
            print("\n  ⚠️ 非 SQLite 后端当前无法真正启动（见数据库部署处的说明），跳过启动。")
            return
        print("\n[服务器] 正在启动…")
        try:
            setup.phase_start(port, db_path)
            setup.phase_verify(port)
        except Exception as exc:
            print(f"  启动/验证异常：{type(exc).__name__}: {exc}")
    else:
        print("\n  未启动。需要时运行上面生成的脚本，或回到本菜单选 1。")


# ---------------------------------------------------------------------------
# ③ 模块选择（可选部署）—— 非必要模块可以关掉
# ---------------------------------------------------------------------------
# 精简集 = 核心模块（永启）+ 报价主链路的最小集合
MINIMAL_SET = {"masterdata", "quotation", "report"}


def _open_registry(backend: str, db_path: str):
    """准备好 settings 表并拿到 registry；失败返回 None。"""
    os.environ["WV_DB"] = db_path
    os.environ["WV_DB_BACKEND"] = backend
    try:
        from app.database import init_db
        from app.core.schema import run_migrations
        from app.core import registry
        init_db()
        run_migrations()
        registry.ensure_states()
        return registry
    except Exception as exc:
        print(f"  无法打开模块清单：{type(exc).__name__}: {exc}")
        print("  请先完成「数据库部署」（SQLite）再来做模块选择。")
        return None


def _apply_targets(reg, wanted: set) -> int:
    """把模块开关收敛到目标集合。依赖有先后顺序，故迭代若干轮直到稳定。"""
    changed = 0
    for _ in range(8):
        moved = False
        for m in reg.MODULES:
            code = m["code"]
            if m["core"]:
                continue
            want = code in wanted
            if reg.is_enabled(code) != want:
                ok, _msg = reg.set_enabled(code, want)
                if ok:
                    moved = True
                    changed += 1
        if not moved:
            break
    return changed


def menu_modules(setup) -> None:
    hr("模块选择（可选部署）—— 非必要模块可以关掉")
    conf = read_conf()
    backend = conf.get("db_backend", "sqlite")
    db_path = conf.get("db") or os.path.join(SRC, "data", "wv.db")
    if backend != "sqlite":
        print("  模块开关存在数据库的 settings 表里；当前后端不是 SQLite，")
        print("  业务查询尚未移植完成，无法在线改动模块开关。")
        return
    reg = _open_registry(backend, db_path)
    if reg is None:
        return

    while True:
        print("\n  当前模块状态：")
        for i, m in enumerate(reg.catalog(), 1):
            flag = "启用" if m["enabled"] else "关闭"
            kind = "[核心]" if m["core"] else "[可选]"
            print(f"   {i:>2}. {kind} {m['code']:<12} {m['name']:<14} {flag}")
        print("\n  操作：")
        print("   直接输入模块代号（如 employee）切换启停")
        print("   all = 全部启用")
        print("   min = 精简集（核心 + 主数据 + 报价 + 报表）")
        print("   0 / q = 完成并返回主菜单")
        c = ask("请选择", "0").strip().lower()
        if c in ("0", "q", ""):
            break
        if c == "all":
            n = _apply_targets(reg, {m["code"] for m in reg.MODULES})
            print(f"  已切到全量（改动 {n} 个）。")
        elif c == "min":
            n = _apply_targets(reg, MINIMAL_SET)
            print(f"  已切到精简集（改动 {n} 个）：核心 + 主数据 + 报价 + 报表。")
        else:
            ok, msg = reg.set_enabled(c, not reg.is_enabled(c))
            print(f"  {c}: {'已改动 —— ' if ok else '未改动 —— '}{msg}")
    print("\n  提示：模块开关**重启服务后生效**（路由在启动时装配）。")
    print("        关闭只隐藏路由与菜单、不删表，随时可再开启且数据不丢。")


# ---------------------------------------------------------------------------
# 主菜单
# ---------------------------------------------------------------------------
def main() -> int:
    setup = load_setup()
    print("=" * 62)
    print(" Felix 企业管理平台 · 服务器部署辅助工具")
    print(" 服务器部署 与 数据库部署 分离 · 数据库默认管理员 root / 88888888")
    print("=" * 62)
    while True:
        print("\n  ---- 主菜单 ----")
        print("   1) 数据库部署（选类型 → 配置 → 初始化）")
        print("   2) 服务器部署（端口 / 启动方式 / 生成脚本）")
        print("   3) 模块选择（可选部署：非必要模块可关）")
        print("   4) 查看当前部署配置")
        print("   0) 退出")
        c = ask("请选择", "4")
        if c == "1":
            menu_database(setup)
        elif c == "2":
            menu_server(setup)
        elif c == "3":
            menu_modules(setup)
        elif c == "4":
            show_current()
        elif c == "0":
            print("\n已退出。")
            return 0
        else:
            print("  无效选择。")


if __name__ == "__main__":
    sys.exit(main())
