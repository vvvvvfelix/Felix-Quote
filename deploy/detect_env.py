"""部署前置环境检测 —— 部署的第一道关，也是唯一会「劝退」的环节。

设计取向：**不满足就明确说不满足，并给出可执行的修改方案**，而不是硬着头皮
装完再让用户猜为什么起不来。

每条检查返回：
    {item, status(ok|warn|fail), detail, fix}
status 语义：
    ok   满足
    warn 不致命，但会影响体验/性能，给出优化建议
    fail 必须处理，否则部署必然失败（setup.py 遇到 fail 会中止）

用法：
    python deploy/detect_env.py                    # 人读报告
    python deploy/detect_env.py --json             # 机器读（setup.py 用）
    python deploy/detect_env.py --port 8080 --db D:/data/app.db
"""
import ctypes
import json
import os
import platform
import shutil
import socket
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
RUNTIME_PY = os.path.join(SRC, "runtime", "python", "python.exe")

_MIN_DISK_MB = 500          # 数据库 + 上传文件的最低余量
_MIN_MEM_MB = 1024          # 低于此值 SQLite + 多用户会明显卡顿
_WARN_MEM_MB = 4096


def _human(mb: float) -> str:
    return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{int(mb)} MB"


# ==========================================================================
# 各项检查
# ==========================================================================
def check_os() -> dict:
    rel = platform.platform()
    ok = sys.platform in ("win32", "linux", "darwin")
    return {"item": "操作系统", "status": "ok" if ok else "fail", "detail": rel,
            "fix": "" if ok else "支持 Windows / Linux / macOS，当前平台未适配"}


def check_python() -> dict:
    v = sys.version_info
    detail = f"Python {v.major}.{v.minor}.{v.micro}（{sys.executable}）"
    if (v.major, v.minor) < (3, 10):
        return {"item": "Python 版本", "status": "fail", "detail": detail,
                "fix": "需要 Python 3.10 及以上；请改用随包自带的 runtime\\python\\python.exe"}
    return {"item": "Python 版本", "status": "ok", "detail": detail, "fix": ""}


def check_runtime() -> dict:
    """自带运行时是否完整 —— 便携版能不能脱离系统 Python 跑起来就看它。"""
    if not os.path.isfile(RUNTIME_PY):
        return {"item": "自带运行时", "status": "fail", "detail": f"缺失 {RUNTIME_PY}",
                "fix": "发布包不完整，请重新下载完整的 felix-quote 便携版"}
    return {"item": "自带运行时", "status": "ok", "detail": RUNTIME_PY, "fix": ""}


def check_deps() -> dict:
    missing = []
    for m in ("fastapi", "uvicorn", "pydantic", "jinja2"):
        try:
            __import__(m)
        except Exception:
            missing.append(m)
    if missing:
        return {"item": "运行依赖", "status": "fail", "detail": f"缺失 {', '.join(missing)}",
                "fix": ("请用自带运行时执行：\n"
                        f"    {RUNTIME_PY} -m pip install {' '.join(missing)}\n"
                        "或改用 runtime\\python\\python.exe 启动（已内置全部依赖）")}
    return {"item": "运行依赖", "status": "ok",
            "detail": "fastapi / uvicorn / pydantic / jinja2 齐全", "fix": ""}


def check_memory() -> dict:
    try:
        if sys.platform == "win32":
            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = MS()
            st.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            total = st.ullTotalPhys / 1024 / 1024
        else:
            total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 / 1024
    except Exception as e:
        return {"item": "物理内存", "status": "warn", "detail": f"无法读取（{e}）",
                "fix": "无法自动检测，请自行确认内存不少于 2GB"}
    if total < _MIN_MEM_MB:
        return {"item": "物理内存", "status": "fail", "detail": f"总内存 {_human(total)}",
                "fix": f"至少需要 {_MIN_MEM_MB}MB；请关闭占用内存的程序或升级内存"}
    if total < _WARN_MEM_MB:
        return {"item": "物理内存", "status": "warn", "detail": f"总内存 {_human(total)}",
                "fix": "内存偏小，50 人以上并发建议升级到 8GB"}
    return {"item": "物理内存", "status": "ok", "detail": f"总内存 {_human(total)}", "fix": ""}


def check_disk(path: str) -> dict:
    try:
        usage = shutil.disk_usage(path if os.path.isdir(path) else os.path.dirname(path))
    except Exception as e:
        return {"item": "磁盘空间", "status": "warn", "detail": f"无法读取（{e}）", "fix": ""}
    free_mb = usage.free / 1024 / 1024
    if free_mb < _MIN_DISK_MB:
        return {"item": "磁盘空间", "status": "fail", "detail": f"可用 {_human(free_mb)}",
                "fix": f"至少需要 {_MIN_DISK_MB}MB 可用空间，请清理磁盘或换一个安装目录"}
    return {"item": "磁盘空间", "status": "ok", "detail": f"可用 {_human(free_mb)}", "fix": ""}


def check_port(port: int, host: str = "127.0.0.1") -> dict:
    s = socket.socket()
    s.settimeout(0.6)
    try:
        s.connect((host, port))
        return {"item": f"端口 {port}", "status": "fail", "detail": "已被占用",
                "fix": (f"换端口重跑：python deploy/setup.py --port {port + 1}\n"
                        f"或找出占用者：netstat -ano | findstr :{port}（Windows）"
                        " / lsof -i :{port}（Linux）".replace("{port}", str(port)))}
    except OSError:
        return {"item": f"端口 {port}", "status": "ok", "detail": "可用", "fix": ""}
    finally:
        s.close()


def check_dir_writable(path: str, label: str) -> dict:
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_probe")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("1")
        os.remove(probe)
        return {"item": label, "status": "ok", "detail": f"可写 {path}", "fix": ""}
    except Exception as e:
        return {"item": label, "status": "fail", "detail": f"不可写 {path}（{e}）",
                "fix": ("目录无写权限。Windows 请把程序放到 D:\\ 等非系统盘目录，"
                        "不要放在 C:\\Program Files 下；Linux 请 chown 给运行用户")}


def check_db(db_path: str) -> dict:
    """数据库可用性与完整性 —— 老库损坏要在部署前就发现，别等上线后炸。"""
    d = os.path.dirname(db_path)
    if d and not os.path.isdir(d):
        return {"item": "数据库目录", "status": "fail", "detail": f"不存在 {d}",
                "fix": f"请先创建目录：mkdir -p {d}"}
    if not os.path.isfile(db_path):
        return {"item": "数据库", "status": "ok", "detail": f"首次部署，将新建 {db_path}",
                "fix": ""}
    try:
        con = sqlite3.connect(db_path, timeout=5)
        r = con.execute("PRAGMA integrity_check").fetchone()[0]
        n = con.execute("SELECT COUNT(*) FROM sqlite_master "
                        "WHERE type='table'").fetchone()[0]
        con.close()
        size = os.path.getsize(db_path) / 1024 / 1024
        if r != "ok":
            return {"item": "数据库", "status": "fail", "detail": f"完整性检查失败：{r}",
                    "fix": ("数据库已损坏。恢复方案：\n"
                            f"    1) 用 backup 目录里的备份覆盖 {db_path}\n"
                            "    2) 无备份则移走该文件后重新部署（数据将丢失，慎行）")}
        return {"item": "数据库", "status": "ok",
                "detail": f"正常，{n} 张表，{size:.1f} MB", "fix": ""}
    except Exception as e:
        return {"item": "数据库", "status": "fail", "detail": f"无法打开（{e}）",
                "fix": "文件被占用或无权限；请先停止正在运行的服务再部署"}


def check_desktop() -> dict:
    """部署日志要落到桌面，桌面不可写就必须提前说。"""
    desk = os.path.join(os.path.expanduser("~"), "Desktop")
    if not os.path.isdir(desk):
        return {"item": "桌面目录", "status": "warn", "detail": f"未找到 {desk}",
                "fix": "部署日志将改写到程序目录下"}
    return check_dir_writable(desk, "桌面目录")


def check_mssql(backend: str) -> dict:
    """SQL Server 后端检测（实验性）。

    当前业务查询仍用 SQLite 方言，T-SQL 移植在路线图 M108 进行中，
    因此即使驱动齐全也暂不能运行。这里只做「前置条件」提示，状态定为 warn。
    """
    if backend != "mssql":
        return {"item": "数据库后端", "status": "ok", "detail": "SQLite（默认，零依赖）", "fix": ""}
    try:
        import pyodbc
    except Exception:
        return {"item": "SQL Server 驱动", "status": "warn",
                "detail": "未检测到 pyodbc",
                "fix": ("SQL Server 后端处于实验阶段（M108 查询移植进行中），暂不能运行。\n"
                        "若需提前试用：pip install pyodbc 并安装『ODBC Driver 17 for SQL Server』。\n"
                        "当前建议仍用默认 SQLite；选择 mssql 仅会生成配置，服务暂不启动。")}
    drivers = []
    try:
        drivers = [d for d in pyodbc.drivers() if "SQL Server" in d]
    except Exception:
        pass
    if not drivers:
        return {"item": "ODBC 驱动", "status": "warn",
                "detail": "未安装 SQL Server ODBC 驱动",
                "fix": "安装『ODBC Driver 17 for SQL Server』；当前建议先用默认 SQLite"}
    return {"item": "SQL Server 后端", "status": "warn",
            "detail": f"驱动就绪（{drivers[0]}），但查询移植 M108 尚未完成",
            "fix": "后端已可选，但服务需等 M108 完成方可启动；建议当前用 SQLite 部署"}


# ==========================================================================
# 汇总
# ==========================================================================
def run_checks(port: int = 8765, db_path: str = "", backend: str = "sqlite") -> list[dict]:
    db_path = db_path or os.path.join(SRC, "data", "wv.db")
    return [
        check_os(), check_python(), check_runtime(), check_deps(),
        check_memory(), check_disk(SRC), check_port(port),
        check_dir_writable(os.path.dirname(db_path), "数据目录"),
        check_db(db_path), check_desktop(), check_mssql(backend),
    ]


def summary(results: list[dict]) -> dict:
    return {"ok": sum(1 for r in results if r["status"] == "ok"),
            "warn": sum(1 for r in results if r["status"] == "warn"),
            "fail": sum(1 for r in results if r["status"] == "fail")}


def main() -> int:
    port = 8765
    db = ""
    backend = "sqlite"
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--port" and i + 1 < len(argv):
            port = int(argv[i + 1])
        elif a == "--db" and i + 1 < len(argv):
            db = argv[i + 1]
        elif a == "--backend" and i + 1 < len(argv):
            backend = argv[i + 1]

    results = run_checks(port, db, backend)
    s = summary(results)

    if "--json" in argv:
        print(json.dumps({"results": results, "summary": s}, ensure_ascii=False))
        return 0 if s["fail"] == 0 else 1

    mark = {"ok": "通过", "warn": "警告", "fail": "不满足"}
    print("=" * 62)
    print(" Felix 企业管理平台 — 部署环境检测")
    print("=" * 62)
    for r in results:
        print(f"[{mark[r['status']]:^4}] {r['item']}：{r['detail']}")
        if r["fix"]:
            for line in r["fix"].splitlines():
                print(f"         → {line}")
    print("-" * 62)
    print(f" 通过 {s['ok']} / 警告 {s['warn']} / 不满足 {s['fail']}")
    if s["fail"]:
        print("\n 存在必须处理的问题，请先按上面的方案修复，再运行：")
        print("     python deploy/setup.py")
        return 1
    print("\n 环境满足部署条件，可以运行：python deploy/setup.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
