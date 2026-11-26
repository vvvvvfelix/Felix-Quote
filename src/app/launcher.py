"""Felix 报价系统 — 后台常驻与开机自启。

三个能力：
1. 后台启动服务（无黑窗口，用 pythonw.exe 运行本模块）
2. 注册 / 取消开机自启（写当前用户 Run 注册表项，不需要管理员权限）
3. 托盘图标常驻（pystray 可选，缺失时退化为纯后台进程）

命令行：
    python -m app.launcher start        前台启动服务并阻塞（调试用）
    python -m app.launcher tray         托盘常驻 + 启动服务（默认）
    python -m app.launcher autostart    仅开机自启，失败静默退出
    python -m app.launcher enable-autostart / disable-autostart / status
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "FelixQuotationSystem"
PORT_RANGE = range(8765, 8800)
START_TIMEOUT = 90          # 首次启动要初始化数据库 + 自检，给足时间
LOG_FILE = os.path.join(BASE_DIR, "data", "service.log")


# --------------------------------------------------------------------------
# 端口探测
# --------------------------------------------------------------------------
def port_free(port: int) -> bool:
    s = socket.socket()
    s.settimeout(0.4)
    try:
        return s.connect_ex(("127.0.0.1", port)) != 0
    finally:
        s.close()


def pick_port(start: int = PORT_RANGE[0]) -> int:
    for p in PORT_RANGE:
        if port_free(p):
            return p
    raise RuntimeError("8765-8799 端口都被占用，请重启电脑或关闭占用程序")


def find_running() -> int | None:
    """若服务已在运行，返回它的端口；否则 None。

    先用 netstat 一次性拿到监听表，避免逐端口 connect 的累积延迟
    （35 个端口 × 0.5s 超时 = 最坏 17 秒，会让启动看起来像卡死）。
    netstat 不可用时退回逐端口探测。
    """
    listening: set[int] = set()
    netstat_ok = False
    if sys.platform == "win32":
        try:
            r = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="ignore", timeout=12)
            netstat_ok = True
            for line in r.stdout.splitlines():
                if "LISTENING" not in line:
                    continue
                parts = line.split()
                if len(parts) < 4 or ":" not in parts[1]:
                    continue
                try:
                    listening.add(int(parts[1].rsplit(":", 1)[1]))
                except ValueError:
                    pass
        except (OSError, subprocess.SubprocessError):
            netstat_ok = False

    if netstat_ok:
        for p in PORT_RANGE:
            if p in listening:
                return p
        return None

    for p in PORT_RANGE:                      # 退路：逐端口探测
        s = socket.socket()
        s.settimeout(0.25)
        try:
            if s.connect_ex(("127.0.0.1", p)) == 0:
                return p
        except OSError:
            pass
        finally:
            s.close()
    return None


# --------------------------------------------------------------------------
# 服务进程管理
# --------------------------------------------------------------------------
def _write_log(text: str) -> None:
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {text}\n")
    except OSError:
        pass


def _python_exe(windows: bool = True) -> str:
    """定位 Python 解释器。

    便携版用内置 runtime，源码目录直接用当前解释器，
    这样 launcher 在开发机和打包版里都能跑。
    """
    if windows:
        rel = os.path.join("runtime", "python",
                           "pythonw.exe" if _is_pythonw() else "python.exe")
    else:
        rel = os.path.join("runtime", "python", "python")
    bundled = os.path.join(BASE_DIR, rel)
    if os.path.exists(bundled):
        return bundled
    return sys.executable


def _is_pythonw() -> bool:
    return os.path.basename(sys.executable).lower().startswith("pythonw")


def _detached_flags() -> int:
    """让子进程彻底脱离当前控制台，父进程退出后继续存活。"""
    if sys.platform != "win32":
        return 0
    f = getattr(subprocess, "DETACHED_PROCESS", 0)
    f |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return f


def _bind_host() -> str:
    """监听地址。默认仅本机；设置 WV_HOST 可改为局域网访问。"""
    return os.environ.get("WV_HOST", "127.0.0.1").strip() or "127.0.0.1"


def _lan_enabled() -> bool:
    return _bind_host() not in ("127.0.0.1", "localhost", "::1")


def _lan_ip() -> str:
    """取本机在局域网中的 IPv4 地址（用于给同事的访问地址）。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))          # 不会真的发包，仅用于选路
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return socket.gethostbyname(socket.gethostname())


def _child_cmd(port: int) -> list[str]:
    return [_python_exe(windows=False), "-m", "uvicorn", "app.main:app",
            "--host", _bind_host(), "--port", str(port)]


def start_service(wait: bool = True) -> tuple[int | None, subprocess.Popen | None]:
    """启动服务。返回 (端口, 进程对象)；已在运行时返回 (端口, None)。"""
    running = find_running()
    if running:
        _write_log(f"服务已在运行，端口 {running}")
        return running, None

    port = pick_port()
    _write_log(f"启动服务，端口 {port}")
    proc = subprocess.Popen(
        _child_cmd(port), cwd=BASE_DIR,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=_detached_flags(),
    )
    if not wait:
        return port, proc

    deadline = time.time() + START_TIMEOUT
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"服务进程已退出（返回码 {proc.returncode}），详见 {LOG_FILE}")
        if not port_free(port):
            _write_log(f"服务就绪，端口 {port}")
            return port, proc
        time.sleep(0.6)
    proc.terminate()
    raise TimeoutError(f"服务启动超时（{START_TIMEOUT}s）")


def _pid_on_port(port: int) -> int | None:
    """查占用某端口的 PID。

    netstat -ano 的行格式：
        TCP    127.0.0.1:8765    0.0.0.0:0    LISTENING    10212
        [0]      [1]              [2]          [3]          [4]
    本地地址在 parts[1]，PID 在 parts[4]。
    """
    if sys.platform == "win32":
        try:
            r = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                capture_output=True, text=True, encoding="utf-8",
                errors="ignore", timeout=15)
            for line in r.stdout.splitlines():
                parts = line.split()
                if len(parts) < 5 or parts[3] != "LISTENING":
                    continue
                local = parts[1]
                if ":" not in local:
                    continue
                try:
                    if int(local.rsplit(":", 1)[1]) == port:
                        return int(parts[4])
                except ValueError:
                    continue
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
    else:
        try:
            r = subprocess.run(["lsof", "-ti", f":{port}"],
                               capture_output=True, text=True, timeout=10)
            return int(r.stdout.strip().split()[0]) if r.stdout.strip() else None
        except Exception:
            return None
    return None


def stop_service() -> bool:
    """结束服务进程。按端口精确定位 PID，只杀自己，不误伤其他 Python。"""
    killed = []
    for p in PORT_RANGE:
        pid = _pid_on_port(p)
        if not pid:
            continue
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=20)
            else:
                import signal
                os.kill(pid, signal.SIGTERM)
            killed.append((p, pid))
        except (OSError, subprocess.SubprocessError):
            pass
    _write_log(f"已停止服务：{killed or '无运行中的服务'}")
    return bool(killed)


# --------------------------------------------------------------------------
# 开机自启（当前用户 Run 键，无需管理员）
# --------------------------------------------------------------------------
def _winreg():
    try:
        import winreg
        return winreg
    except ImportError:
        return None


def _autostart_command() -> str:
    """开机要执行的命令：用 pythonw.exe 无窗口运行 launcher tray-host。"""
    return f'"{_python_exe()}" -m app.launcher tray-host'


def autostart_path() -> str | None:
    """返回 .lnk 路径（当前用户「启动」文件夹），无 pywin32 则回退 Run 键。"""
    shell = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                         "Start Menu", "Programs", "Startup")
    return os.path.join(shell, "Felix 报价系统.lnk") if shell else None


def enable_autostart() -> tuple[bool, str]:
    reg = _winreg()
    if reg is None:
        return False, "当前系统不支持注册表操作"
    try:
        key = reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE)
    except OSError:
        key = reg.CreateKey(reg.HKEY_CURRENT_USER, RUN_KEY)
    try:
        reg.SetValueEx(key, VALUE_NAME, 0, reg.REG_SZ, _autostart_command())
    finally:
        reg.CloseKey(key)
    return True, "已设置开机自动启动（当前用户）"


def disable_autostart() -> tuple[bool, str]:
    reg = _winreg()
    if reg is None:
        return False, "当前系统不支持注册表操作"
    try:
        key = reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE)
    except OSError:
        return False, "尚未设置开机自启"
    try:
        reg.DeleteValue(key, VALUE_NAME)
    except OSError:
        return False, "删除失败，可能未设置过"
    finally:
        reg.CloseKey(key)
    return True, "已取消开机自动启动"


def autostart_status() -> bool:
    reg = _winreg()
    if reg is None:
        return False
    try:
        key = reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_READ)
    except OSError:
        return False
    try:
        reg.QueryValueEx(key, VALUE_NAME)
        return True
    except OSError:
        return False
    finally:
        key.Close()


# --------------------------------------------------------------------------
# 托盘图标（可选依赖 pystray）
# --------------------------------------------------------------------------
ICON_FILE = os.path.join(BASE_DIR, "Felix 报价系统.ico")
SERVER_URL = "http://127.0.0.1:{port}/"


def access_hint(port: int) -> str:
    """局域网模式下，给出同事可用的访问地址提示。"""
    if not _lan_enabled():
        return f"服务已启动：http://127.0.0.1:{port}/"
    return (f"服务已启动（本机）：http://127.0.0.1:{port}/\n"
            f"　局域网访问：http://{_lan_ip()}:{port}/\n"
            f"　需在本机防火墙放行 {port} 端口，且所有同事使用同一个数据目录")


def run_tray(port: int) -> None:
    """托盘常驻。pystray 不可用时退化为等待 Ctrl+C。"""
    try:
        import pystray
        from PIL import Image
    except ImportError:
        _write_log("未安装 pystray，托盘图标不可用，服务已在后台运行")
        print("服务已在后台运行，按 Ctrl+C 结束。", file=sys.stderr)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        return

    img = (Image.open(ICON_FILE) if os.path.exists(ICON_FILE)
           else Image.new("RGB", (64, 64), "#B8860B"))
    url = SERVER_URL.format(port=port)

    def open_app(icon, item):
        webbrowser.open(url)

    def show_info(icon, item):
        import tkinter
        from tkinter import messagebox
        root = tkinter.Tk()
        root.withdraw()
        messagebox.showinfo("Felix 报价系统",
                            f"服务运行中\n\n访问地址：{url}\n"
                            f"数据目录：{BASE_DIR}\n\n"
                            f"双击托盘图标可打开系统。")
        root.destroy()

    def do_quit(icon, item):
        icon.stop()
        stop_service()

    menu = pystray.Menu(
        pystray.MenuItem("打开系统", open_app, default=True),
        pystray.MenuItem("复制访问地址", lambda i, it: _clipboard(url)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("使用说明", show_info),
        pystray.MenuItem("停止服务", do_quit),
        pystray.MenuItem("退出", do_quit),
    )
    icon = pystray.Icon("felixquot", img, "Felix 报价系统", menu)
    _write_log("托盘图标已启动")
    icon.run()


def _clipboard(text: str) -> None:
    try:
        import subprocess as sp
        sp.run(["clip"], input=text.encode("utf-16-le"), timeout=5)
    except Exception:
        pass


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def _open(url: str) -> None:
    webbrowser.open(url)


# --------------------------------------------------------------------------
# 首次运行引导
# --------------------------------------------------------------------------
MARK_FILE = os.path.join(BASE_DIR, "data", ".initialized")

BANNER = [
    "",
    " ==========================================================",
    "    Felix 外贸报价单生成器   FELIX",
    "    菲力克斯科技（广州）有限公司",
    " ==========================================================",
    "",
]


def spawn_tray(port: int) -> bool:
    """拉起独立的托盘宿主进程（pythonw，无窗口），随开机自启常驻。

    托盘必须由独立进程承载：服务子进程本身不带 UI，
    若让服务进程兼管托盘，服务一停图标就消失。
    """
    if sys.platform != "win32":
        return False
    pyw = _python_exe()
    try:
        subprocess.Popen([pyw, "-m", "app.launcher", "tray-host", str(port)],
                         cwd=BASE_DIR, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=_detached_flags())
        _write_log(f"托盘宿主已启动，端口 {port}")
        return True
    except OSError as exc:
        _write_log(f"托盘宿主启动失败：{exc}")
        return False


def cmd_boot(args: list[str]) -> int:
    """启动项入口。

    首次运行：环境自检 + 初始化数据 + 注册开机自启 + 后台启动 + 打开浏览器
    后续运行：静默后台启动（无黑窗口，秒级退出）
    返回码：0 正常 / 1 初始化失败 / 2 环境不满足
    """
    first = not os.path.exists(MARK_FILE)

    if not first:
        # 已初始化：静默后台跑，直接退出（bat 窗口会立即关闭）
        try:
            port, _ = start_service(wait=False)
            _write_log(f"静默启动服务，端口 {port}")
        except Exception as exc:
            _write_log(f"静默启动失败：{exc}")
        return 0

    for line in BANNER:
        print(line)
    print("   首次运行，正在检查运行环境...\n")
    try:
        from .selfcheck import print_report
        ok = print_report() == 0
    except Exception as exc:
        print(f"  [ERROR] 自检模块异常：{exc}")
        ok = False
    if not ok:
        print("\n   [无法启动] 当前系统不满足运行要求，请参考上方说明。")
        _write_log("自检未通过")
        return 2

    print()
    if not os.path.exists(os.path.join(BASE_DIR, "data", "wv.db")):
        print("   正在初始化产品数据，首次约需 10 秒...")
        try:
            from .database import init_db
            from .core.schema import run_migrations
            from .auth import ensure_bootstrap_admin
            from .modules import identity
            from . import seed
            init_db()
            run_migrations()
            # 顺序要紧：先灌部门种子，再建管理员 —— 否则 admin 挂空部门
            identity.seed_org()
            ensure_bootstrap_admin()
            r = seed.seed_all()
            print(f"   已导入产品 {r['products']} 个 / 分类 {r['categories']} 个")
        except Exception as exc:
            print(f"  [ERROR] 数据初始化失败：{exc}")
            print("          请把程序放到 D 盘根目录或桌面，不要放在 C:\\Program Files 下。")
            _write_log(f"数据初始化失败：{exc}")
            return 1

    print("   正在设置开机自动启动...")
    ok_a, msg = enable_autostart()
    print(f"   {msg}")

    try:
        os.makedirs(os.path.dirname(MARK_FILE), exist_ok=True)
        with open(MARK_FILE, "w", encoding="utf-8") as f:
            f.write("ok")
    except OSError as exc:
        _write_log(f"标记文件写入失败：{exc}")

    print()
    print(" ==========================================================")
    print("   首次设置完成！")
    print()
    print("   * 服务常驻后台运行，桌面右下角有金色托盘图标")
    print("   * 已设置为开机自动启动")
    print("   * 以后只需双击桌面「Felix 报价系统」图标即可访问")
    print()
    print("   浏览器即将自动打开...")
    print(" ==========================================================")
    print()

    try:
        port, _proc = start_service(wait=True)
        if port:
            _open(SERVER_URL.format(port=port))
        _write_log(f"首次运行完成，服务端口 {port}")
    except Exception as exc:
        _write_log(f"首次启动服务失败：{exc}")
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "tray"

    if cmd == "boot":
        return cmd_boot(argv[2:])

    if cmd == "tray-host":
        # 独立托盘宿主：只管托盘 UI，服务由它以脱离进程的方式另起，
        # 这样即使托盘宿主崩溃/被关，服务仍继续运行。
        # 用 pythonw 启动时没有控制台，任何异常都只能写日志。
        try:
            port = int(argv[2]) if len(argv) > 2 and argv[2].isdigit() else None
        except (IndexError, ValueError):
            port = None
        try:
            os.chdir(BASE_DIR)
            if BASE_DIR not in sys.path:
                sys.path.insert(0, BASE_DIR)
            _write_log(f"托盘宿主启动：cwd={os.getcwd()} py={sys.executable}")
            running = port or find_running()
            if not running:
                running, _ = start_service(wait=True)
            if not running:
                _write_log("托盘宿主：服务无法启动")
                return 1
            _write_log(f"托盘宿主就绪，端口 {running}")
            run_tray(running)
            _write_log("托盘已退出")
            stop_service()
        except Exception as exc:
            import traceback
            _write_log("托盘宿主异常：\n" + traceback.format_exc())
        return 0

    if cmd == "status":
        p = find_running()
        print(f"服务: {'运行中' if p else '未运行'}"
              + (f"（端口 {p}）" if p else ""))
        print(f"开机自启: {'已开启' if autostart_status() else '未开启'}")
        return 0

    if cmd == "enable-autostart":
        ok, msg = enable_autostart()
        print(msg)
        return 0 if ok else 1

    if cmd == "disable-autostart":
        ok, msg = disable_autostart()
        print(msg)
        return 0 if ok else 1

    if cmd == "autostart":
        # 开机自启用：任何异常都静默，不能弹窗打扰用户
        try:
            port, _ = start_service()
            if port:
                run_tray(port)
        except Exception as exc:
            _write_log(f"开机自启失败：{exc}")
        return 0

    if cmd == "start":
        port, proc = start_service()
        if not port:
            return 1
        print(access_hint(port))
        if proc:
            try:
                proc.wait()
            except KeyboardInterrupt:
                proc.terminate()
        return 0

    # tray：起服务 + 开浏览器 + 托盘常驻（手动运行时的完整体验）
    port, proc = start_service()
    if not port:
        return 1
    url = SERVER_URL.format(port=port)
    _open(url)
    print(f"服务运行中：{url}")
    print("已设置开机自启。双击桌面图标即可访问，从托盘菜单可停止服务。")
    enable_autostart()
    try:
        run_tray(port)
    except KeyboardInterrupt:
        pass
    if proc:
        proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
