"""一键部署 —— 检测 → 配置数据库 → 初始化 → 启动 → 验证 → 落日志到桌面。

跑法：
    python deploy/setup.py                       # 默认 8765 端口、内置 SQLite
    python deploy/setup.py --port 8080
    python deploy/setup.py --db D:/felixdata/app.db --admin-pass 强口令
    python deploy/setup.py --no-start            # 只配置不启动

流程铁律：**先检测，不满足就停下来给方案，绝不硬装**。
部署成功后会在桌面生成部署报告（含访问地址、账号、已启用模块）。
"""
import argparse
import json
import os
import platform
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
PY = os.path.join(SRC, "runtime", "python", "python.exe")
PYW = os.path.join(SRC, "runtime", "python", "pythonw.exe")
LOG_DIR = os.path.join(ROOT, "deploy", "logs")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SRC)

STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
_lines: list[str] = []


def say(msg: str, log: bool = True) -> None:
    print(msg)
    if log:
        _lines.append(msg.rstrip())


def step(n: int, total: int, msg: str) -> None:
    say(f"\n[{n}/{total}] {msg}")


# ==========================================================================
# 1) 环境检测
# ==========================================================================
def phase_detect(port: int, db: str) -> bool:
    import detect_env
    results = detect_env.run_checks(port, db)
    say("=" * 62)
    say(" 第 1 步 · 环境检测")
    say("=" * 62)
    mark = {"ok": "通过", "warn": "警告", "fail": "不满足"}
    for r in results:
        say(f"  [{mark[r['status']]:^4}] {r['item']}：{r['detail']}")
        if r["fix"]:
            for line in r["fix"].splitlines():
                say(f"          → {line}")
    fails = [r for r in results if r["status"] == "fail"]
    if fails:
        say("\n 环境未达标，部署已中止。请按上面的方案修复后重跑：")
        say("     python deploy/setup.py")
        return False
    return True


# ==========================================================================
# 2) 配置
# ==========================================================================
def phase_configure(port: int, db: str, backend: str = "sqlite",
                    mssql: dict | None = None) -> dict:
    os.makedirs(os.path.dirname(db), exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    conf = {"port": port, "db": db, "db_backend": backend,
            "deployed_at": datetime.now().isoformat(timespec="seconds"),
            "host": "127.0.0.1", "platform": platform.platform(),
            "python": sys.version.split()[0]}
    # SQL Server 连接配置落盘（database.mssql_config 会读它）
    if backend == "mssql" and mssql:
        cfg_path = os.path.join(SRC, "data", "dbconfig.json")
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(mssql, f, ensure_ascii=False, indent=2)
        conf["mssql"] = mssql
    with open(os.path.join(ROOT, "deploy", "deploy.conf"), "w", encoding="utf-8") as f:
        json.dump(conf, f, ensure_ascii=False, indent=2)
    # 数据库位置通过环境变量传给应用（database.py 读 WV_DB / WV_DB_BACKEND）
    os.environ["WV_DB"] = db
    os.environ["WV_DB_BACKEND"] = backend
    return conf


# ==========================================================================
# 3) 初始化数据库
# ==========================================================================
def phase_init_db(admin_pass: str | None) -> dict:
    from app.auth import ensure_bootstrap_admin, hash_password
    from app.core import registry
    from app.core.db import get_conn
    from app.core.schema import run_migrations
    from app.database import init_db
    from app.modules import identity

    init_db()
    run_migrations()
    registry.ensure_states()
    identity.seed_org()
    ensure_bootstrap_admin()

    if admin_pass:
        db = get_conn()
        db.execute("UPDATE users SET password_hash=?, must_change_pw=0 "
                   "WHERE username='admin'", (hash_password(admin_pass),))
        db.commit()

    db = get_conn()
    tables = db.execute("SELECT COUNT(*) c FROM sqlite_master WHERE type='table'").fetchone()["c"]
    users = db.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    depts = db.execute("SELECT COUNT(*) c FROM departments WHERE is_active=1").fetchone()["c"]
    return {"tables": tables, "users": users, "departments": depts,
            "modules": registry.enabled_codes(),
            "admin": "admin",
            "password": admin_pass or "admin.123（初始口令，首次登录请修改）"}


# ==========================================================================
# 4) 生成启动脚本
# ==========================================================================
def phase_scripts(port: int, db: str, backend: str = "sqlite") -> list[str]:
    made = []
    bat = os.path.join(SRC, "【启动服务】.bat")
    with open(bat, "w", encoding="utf-8") as f:
        f.write(
            "@echo off\n"
            "chcp 65001 >nul\n"
            f"set \"WV_DB={db}\"\n"
            f"set \"WV_DB_BACKEND={backend}\"\n"
            "cd /d \"%~dp0\"\n"
            f"start \"FelixServer\" /b \"%~dp0runtime\\python\\pythonw.exe\" "
            f"-m uvicorn app.main:app --port {port}\n"
            f"echo 服务已启动，访问 http://127.0.0.1:{port}\n"
            "timeout /t 3 >nul\n")
    made.append(bat)

    stop = os.path.join(SRC, "【停止服务】.bat")
    with open(stop, "w", encoding="utf-8") as f:
        f.write("@echo off\ntaskkill /f /im pythonw.exe /fi \"WINDOWTITLE eq FelixServer\" "
                ">nul 2>&1\necho 已发送停止指令\n")
    made.append(stop)

    if sys.platform != "win32":
        out = os.path.join(ROOT, "deploy", "out")
        os.makedirs(out, exist_ok=True)
        unit = os.path.join(out, "felix.service")
        with open(unit, "w", encoding="utf-8") as f:
            f.write(f"""[Unit]
Description=Felix Enterprise Platform
After=network.target

[Service]
Type=simple
WorkingDirectory={SRC}
Environment=WV_DB={db}
Environment=WV_DB_BACKEND={backend}
ExecStart={PY} -m uvicorn app.main:app --host 0.0.0.0 --port {port}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
""")
        made.append(unit)
        ngx = os.path.join(out, "nginx-felix.conf")
        with open(ngx, "w", encoding="utf-8") as f:
            f.write(f"""server {{
    listen 80;
    server_name _;
    client_max_body_size 200m;
    location / {{
        proxy_pass http://127.0.0.1:{port};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 300s;
    }}
}}
""")
        made.append(ngx)
    return made


# ==========================================================================
# 5) 启动并验证
# ==========================================================================
def _port_open(port: int, timeout: float = 0.6) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def phase_start(port: int, db: str) -> bool:
    if _port_open(port):
        say(f"  端口 {port} 已有服务在跑，跳过启动")
        return True
    exe = PYW if os.path.isfile(PYW) and sys.platform == "win32" else PY
    logf = open(os.path.join(LOG_DIR, f"server_{STAMP}.log"), "a", encoding="utf-8")
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    subprocess.Popen([exe, "-m", "uvicorn", "app.main:app", "--port", str(port)],
                     cwd=SRC, stdout=logf, stderr=logf,
                     env={**os.environ, "WV_DB": db},
                     creationflags=flags, close_fds=True)
    for _ in range(40):                       # 最多等 40 秒
        if _port_open(port):
            return True
        time.sleep(1)
    return False


def phase_verify(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/about", timeout=10) as r:
            data = json.load(r)
            say(f"  接口自检通过：{data.get('product', data.get('name', 'Felix'))}")
            return True
    except Exception as e:
        say(f"  接口自检失败：{e}")
        return False


# ==========================================================================
# 6) 部署报告（桌面 + deploy/logs）
# ==========================================================================
def phase_report(conf: dict, info: dict, scripts: list[str], ok: bool) -> list[str]:
    desk = os.path.join(os.path.expanduser("~"), "Desktop")
    if not os.path.isdir(desk):
        desk = LOG_DIR
    name = f"Felix企业管理系统_部署报告_{STAMP}.log"
    body = "\n".join(_lines)
    head = ("=" * 62 + "\n"
            " Felix 企业管理系统 — 部署报告\n"
            f" 生成时间 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            " 著作权人 Felix（WV-2026-FELIX-0001）\n"
            + "=" * 62 + "\n")
    tail = ("\n" + "=" * 62 + "\n"
            f" 部署结论：{'成功' if ok else '未完成（见上方错误）'}\n"
            f" 访问地址：http://127.0.0.1:{conf['port']}\n"
            f" 管理员账号：{info.get('admin', 'admin')}\n"
            f" 管理员口令：{info.get('password', '')}\n"
            f" 数据库文件：{conf['db']}\n"
            f" 数据库后端：{conf.get('db_backend', 'sqlite')}"
            f"{'（实验性，需 M108 完成后方可运行）' if conf.get('db_backend') == 'mssql' else ''}\n"
            f" 已启用模块（{len(info.get('modules', []))}）："
            f"{'、'.join(info.get('modules', []))}\n"
            " 数据表/用户/部门："
            f"{info.get('tables', '-')} / {info.get('users', '-')} / {info.get('departments', '-')}\n"
            " 启动脚本：" + "；".join(os.path.basename(s) for s in scripts) + "\n"
            + "=" * 62 + "\n"
            + (f" 特别说明：{info['note']}\n" if info.get("note") else "")
            + " 安全提示：请登录后立即修改管理员口令，并定期备份 data 目录。\n")
    text = head + body + tail
    paths = []
    for d in (desk, LOG_DIR):
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        paths.append(p)
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(description="Felix 企业管理系统 一键部署")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--db", default=os.path.join(SRC, "data", "wv.db"))
    ap.add_argument("--admin-pass", default=None, help="设置管理员口令（不设则用初始口令 admin.123）")
    ap.add_argument("--no-start", action="store_true", help="只配置不启动服务")
    ap.add_argument("--db-backend", default="sqlite", choices=["sqlite", "mssql"],
                    help="数据库后端：sqlite（默认）或 mssql（实验性，需 M108 完成后方可运行）")
    ap.add_argument("--mssql-server", default="127.0.0.1")
    ap.add_argument("--mssql-port", type=int, default=1433)
    ap.add_argument("--mssql-db", default="felix")
    ap.add_argument("--mssql-user", default="root", help="SQL Server 账户（默认 root）")
    ap.add_argument("--mssql-pwd", default="88888888", help="SQL Server 密码（默认 88888888）")
    args = ap.parse_args()

    backend = args.db_backend
    mssql_cfg = None
    if backend == "mssql":
        mssql_cfg = {"server": args.mssql_server, "port": args.mssql_port,
                     "database": args.mssql_db, "user": args.mssql_user,
                     "password": args.mssql_pwd,
                     "driver": "ODBC Driver 17 for SQL Server"}
    mssql_mode = backend == "mssql"

    say("Felix 企业管理系统 · 一键部署")
    say(f"目标：端口 {args.port}，数据库 {args.db}，后端 {backend}")

    if not phase_detect(args.port, args.db, backend):
        return 1

    if mssql_mode:
        say("\n[注意] 你选择了 SQL Server 后端。该后端目前处于实验阶段"
             "（业务查询的 T-SQL 移植 M108 尚未完成），本次仅生成配置与启动脚本，"
             "服务暂不启动。建议先用默认 SQLite 完成部署；待 M108 完成后切换即可。\n")

    step(2, 6, "写入配置")
    conf = phase_configure(args.port, args.db, backend, mssql_cfg)
    say(f"  配置已写入 deploy/deploy.conf")

    ok = True
    if mssql_mode:
        step(3, 6, "初始化数据库 —— 跳过（SQL Server 后端需 M108 完成后执行）")
        info = {"tables": 0, "users": 0, "departments": 0, "modules": [],
                "admin": "admin", "password": "admin.123",
                "note": "SQL Server 后端：配置已生成，服务需 M108 完成后启动"}
        ok = False
    else:
        step(3, 6, "初始化数据库（建表 → 版本迁移 → 模块开关 → 组织种子 → 管理员）")
        info = phase_init_db(args.admin_pass)
        say(f"  数据表 {info['tables']} 张，用户 {info['users']}，部门 {info['departments']}")

    step(4, 6, "生成启动脚本")
    scripts = phase_scripts(args.port, args.db, backend)
    for s in scripts:
        say(f"  {s}")

    if mssql_mode:
        step(5, 6, "启动服务 —— 跳过（等待 M108）")
        step(6, 6, "接口自检 —— 跳过（等待 M108）")
    else:
        if args.no_start:
            step(5, 6, "跳过启动（--no-start）")
            say("  手动启动：双击【启动服务】.bat")
        else:
            step(5, 6, f"启动服务（端口 {args.port}）")
            ok = phase_start(args.port, args.db)
            say("  服务已就绪" if ok else "  服务启动超时，请查看 deploy/logs/server_*.log")
        step(6, 6, "接口自检")
        if ok and not args.no_start:
            ok = phase_verify(args.port)

    paths = phase_report(conf, info, scripts, ok)
    say("\n" + "=" * 62)
    say(f" 部署{'成功' if ok else '未完成（见上方说明）'}")
    for p in paths:
        say(f" 报告已生成：{p}")
    say(f" 访问地址：http://127.0.0.1:{args.port}")
    say("=" * 62)
    return 0 if ok and not mssql_mode else (0 if mssql_mode else 1)


if __name__ == "__main__":
    sys.exit(main())
