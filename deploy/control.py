"""桌面入口控制器 —— 给「Felix 管理控制台.bat」用。

子命令：
    python deploy/control.py             # 默认：确保服务在跑，然后打开管理控制台
    python deploy/control.py status      # 打印运行状态
    python deploy/control.py start|stop|restart|open

为什么要它：管理员不该记端口和命令。桌面上双击图标就等于
「帮我把服务开起来，然后把控制台打开」。服务已在跑就直接开页面，不重复启动。
"""
import json
import os
import socket
import subprocess
import sys
import time
import webbrowser
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
PY = os.path.join(SRC, "runtime", "python", "python.exe")
PYW = os.path.join(SRC, "runtime", "python", "pythonw.exe")
LOG_DIR = os.path.join(ROOT, "deploy", "logs")
PID_FILE = os.path.join(LOG_DIR, "server.pid")
CONF = os.path.join(ROOT, "deploy", "deploy.conf")

DEFAULT_PORT = 8765
DEFAULT_DB = os.path.join(SRC, "data", "wv.db")


def conf():
    try:
        with open(CONF, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


C = conf()
PORT = int(C.get("port") or DEFAULT_PORT)
DB = os.environ.get("WV_DB") or C.get("db") or DEFAULT_DB


def port_open(port: int = PORT, timeout: float = 0.5) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def read_pid():
    try:
        with open(PID_FILE, encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return None


def start(wait: int = 30) -> bool:
    if port_open():
        return True
    os.makedirs(LOG_DIR, exist_ok=True)
    logf = open(os.path.join(LOG_DIR, f"console_{datetime.now():%Y%m%d}.log"),
                "a", encoding="utf-8")
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    exe = PYW if os.path.isfile(PYW) else PY
    p = subprocess.Popen([exe, "-m", "uvicorn", "app.main:app", "--port", str(PORT)],
                         cwd=SRC, stdout=logf, stderr=logf,
                         env={**os.environ, "WV_DB": DB},
                         creationflags=flags, close_fds=True)
    with open(PID_FILE, "w", encoding="utf-8") as f:
        f.write(str(p.pid))
    for _ in range(wait):
        if port_open():
            return True
        time.sleep(1)
    return False


def stop() -> bool:
    pid = read_pid()
    if pid:
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/f", "/pid", str(pid)],
                               capture_output=True, timeout=15)
            else:
                os.kill(pid, 15)
        except Exception:
            pass
    for _ in range(15):
        if not port_open():
            try:
                os.remove(PID_FILE)
            except Exception:
                pass
            return True
        time.sleep(1)
    return False


def open_page(path: str = "/") -> None:
    webbrowser.open(f"http://127.0.0.1:{PORT}{path}")


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "console"
    if cmd == "status":
        print(f"端口 {PORT}　{'运行中' if port_open() else '已停止'}　PID {read_pid() or '-'}")
        print(f"数据库 {DB}")
        return 0
    if cmd == "start":
        return 0 if start() else 1
    if cmd == "stop":
        return 0 if stop() else 1
    if cmd == "restart":
        stop()
        time.sleep(1)
        return 0 if start() else 1
    if cmd == "open":
        open_page("/")
        return 0
    # 默认：拉起服务并打开管理控制台
    ok = start()
    if ok:
        open_page("/#console")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
