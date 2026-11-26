"""运行环境自检 —— 面向真实生产环境的常见问题。

原则：只覆盖日常高频问题（系统版本、运行库、内存、磁盘、路径权限、
端口、浏览器、中文字体），极端边界（ARM、32位、特殊企业定制环境）不处理。
"""
import json
import os
import platform
import shutil
import socket
import sys

OK, WARN, FAIL = "ok", "warn", "fail"

# OCR 组件需要的最低内存（模型加载约需 400MB 峰值）
OCR_MIN_MEM_MB = 2048
# 运行所需磁盘（含数据库增长余量）
MIN_FREE_DISK_MB = 500


def win_version_info() -> dict:
    """Windows 版本信息。"""
    info = {"is_windows": platform.system() == "Windows",
            "release": platform.release(), "version": platform.version(),
            "machine": platform.machine()}
    if not info["is_windows"]:
        return info
    try:
        import winreg

        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SOFTWARE\Microsoft\Windows NT\CurrentVersion")
        for name, field in (("ProductName", "product_name"),
                            ("DisplayVersion", "display_version"),
                            ("CurrentBuild", "build")):
            try:
                info[field] = winreg.QueryValueEx(key, name)[0]
            except OSError:
                pass
        winreg.CloseKey(key)
    except Exception:
        pass
    try:
        info["nt_major"] = int(platform.version().split(".")[0])
    except (ValueError, IndexError):
        info["nt_major"] = None
    try:
        info["build"] = int(str(info.get("build") or 0))
    except (TypeError, ValueError):
        info["build"] = 0
    return info


def check_os() -> dict:
    """操作系统是否支持。"""
    v = win_version_info()
    if not v["is_windows"]:
        return {"level": "unsupported", "desc": "非 Windows 系统", "major": None, "build": 0}

    major, build = v.get("nt_major"), v.get("build", 0)
    if major is None:
        return {"level": "unknown", "desc": "无法识别系统版本", "major": None, "build": build}

    if major >= 10:
        name = "Windows 11" if build >= 22000 else "Windows 10"
        dv = v.get("display_version")
        return {"level": "modern", "major": major, "build": build,
                "desc": f"{name}{(' ' + dv) if dv else ''}"}
    if major == 6:
        if build >= 9600:
            return {"level": "legacy", "major": major, "build": build,
                    "desc": f"Windows 8.1（6.{build}）"}
        if build >= 7601:
            return {"level": "low", "major": major, "build": build,
                    "desc": f"Windows 7 SP1（6.{build}）"}
        if build >= 7600:
            return {"level": "low", "major": major, "build": build,
                    "desc": f"Windows 7（6.{build}）"}
        return {"level": "too_low", "major": major, "build": build,
                "desc": f"Windows Vista / Server 2008（6.{build}）"}
    if major <= 5:
        return {"level": "too_low", "major": major, "build": build,
                "desc": "Windows XP / 2003 及以下"}
    return {"level": "unknown", "desc": f"未识别版本 {major}.{build}", "major": major, "build": build}


def check_vc_runtime() -> dict:
    """Visual C++ 运行库（缺失会导致 Python 完全起不来）。"""
    sysdir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32")
    needed = {
        "vcruntime140.dll": "VC++ 2015-2022 运行库",
        "vcruntime140_1.dll": "VC++ 2015-2022 运行库（64位）",
        "msvcp140.dll": "VC++ 标准库",
    }
    missing = [{"dll": d, "desc": desc} for d, desc in needed.items()
               if not os.path.exists(os.path.join(sysdir, d))]
    return {"ok": not missing, "missing": missing}


def check_memory() -> dict:
    """物理内存。"""
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        return {"total_mb": int(st.ullTotalPhys / 1048576),
                "available_mb": int(st.ullAvailPhys / 1048576)}
    except Exception:
        return {"total_mb": 0, "available_mb": 0}


def check_disk(path: str = None) -> dict:
    """所在磁盘剩余空间。"""
    p = path or os.path.dirname(os.path.abspath(sys.executable))
    drive = os.path.splitdrive(p)[0] or "C:\\"
    try:
        usage = shutil.disk_usage(drive)
        return {"drive": drive,
                "total_gb": round(usage.total / 1073741824, 1),
                "free_gb": round(usage.free / 1073741824, 1),
                "free_mb": int(usage.free / 1048576)}
    except OSError:
        return {"drive": drive, "total_gb": 0, "free_gb": 0, "free_mb": 0}


def check_writable() -> dict:
    """程序目录是否可写（PDF 临时文件、数据库 WAL 需要）。"""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    probe = os.path.join(base, "data")
    try:
        os.makedirs(probe, exist_ok=True)
        test = os.path.join(probe, ".write_test")
        with open(test, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(test)
        return {"ok": True, "path": base}
    except (OSError, PermissionError) as exc:
        return {"ok": False, "path": base, "error": str(exc)}


def check_chinese_path() -> dict:
    """路径含中文/空格是否安全（SQLite 与 ReportLab 常见坑）。"""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    has_cn = any("\u4e00" <= ch <= "\u9fff" for ch in base)
    has_space = " " in base
    return {"ok": not (has_cn and has_space), "path": base,
            "has_chinese": has_cn, "has_space": has_space}


def find_port(start: int = 8765, span: int = 30) -> int:
    """找一个可用端口。"""
    for p in range(start, start + span):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return start


def check_browser() -> dict:
    """默认浏览器是否可用（服务靠它展示界面）。"""
    try:
        import webbrowser

        bw = webbrowser.get()
        # 只探测注册情况，不真的打开窗口
        controller = webbrowser.get(try_other_which=False)
        return {"ok": bool(controller), "type": bw}
    except Exception:
        return {"ok": False, "type": None}


def check_chinese_font() -> dict:
    """系统中文字体（PDF 导出会用到）。"""
    fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    for name in ("msyh.ttc", "msyh.ttf", "simhei.ttf", "simsun.ttc", "simkai.ttf"):
        if os.path.exists(os.path.join(fonts_dir, name)):
            return {"ok": True, "font": name}
    return {"ok": False, "font": None}


def selfcheck_find_ocr_pkg():
    """定位 rapidocr_onnxruntime 包目录（基于文件系统，不受 import 缓存影响）。"""
    import glob
    import site

    roots = []
    try:
        roots.extend(site.getsitepackages())
    except Exception:
        pass
    try:
        roots.append(site.getusersitepackages())
    except Exception:
        pass
    roots.extend(p for p in sys.path if "site-packages" in p or "dist-packages" in p)

    seen = set()
    for root in roots:
        if not root or root in seen:
            continue
        seen.add(root)
        cand = os.path.join(root, "rapidocr_onnxruntime")
        if os.path.isdir(cand):
            return cand
    for pat in (os.path.join(sys.prefix, "Lib", "site-packages", "rapidocr_onnxruntime"),
                os.path.join(sys.base_prefix, "Lib", "site-packages", "rapidocr_onnxruntime")):
        if os.path.isdir(pat):
            return pat
    return None


def diagnose_ocr() -> dict:
    """OCR 可用性 —— 精确说明为什么不能用。"""
    r = {"available": False, "status": FAIL, "code": None,
         "reason": "", "solution": ""}
    os_info = check_os()
    r["os"] = os_info

    # 1) 系统版本门槛
    if os_info["level"] == "too_low":
        r.update(code="OS_TOO_LOW",
                 reason=f"操作系统版本过低（{os_info['desc']}）",
                 solution="图片识别依赖的 AI 推理引擎要求 Windows 8.1 及以上。"
                          "请在 Windows 10 / 11 上使用。"
                          "当前系统仍可正常使用产品库、客户、报价单、PDF 导出，"
                          "只是不能直接上传截图/照片识别。")
        return r
    if os_info["level"] == "low":
        # Windows 7：官方不支持，提前告知，不浪费用户的推理等待时间
        r.update(code="OS_UNSUPPORTED_BY_ENGINE",
                 reason=f"{os_info['desc']} 不受 AI 推理引擎支持",
                 solution="图片识别需要 Windows 8.1 及以上，本机为 Windows 7。"
                          "其余功能（产品库、客户、报价单、PDF 导出）完全正常。"
                          "如需图片识别，请改用 Excel/PDF 文件导入方式，"
                          "或换到 Windows 10 及以上系统。")
        return r

    # 2) 推理引擎缺失
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        r.update(code="ENGINE_MISSING",
                 reason="缺少 AI 推理引擎（onnxruntime）",
                 solution="压缩包解压不完整，或文件被杀毒软件隔离。"
                          "请重新完整解压，并检查杀毒软件的隔离区。")
        return r
    except OSError as exc:
        r.update(code="RUNTIME_MISSING",
                 reason="缺少 Visual C++ 运行库，AI 推理引擎无法加载",
                 solution="安装微软官方运行库后重启电脑："
                          "https://aka.ms/vs/17/release/vc_redist.x64.exe"
                          f"（技术细节：{exc}）")
        return r

    # 3) OCR 包
    ocr_pkg_dir = selfcheck_find_ocr_pkg()
    if not ocr_pkg_dir:
        r.update(status=WARN, code="OCR_NOT_PACKED",
                 reason="本包未包含图片识别组件",
                 solution="如需该功能，请向管理员索取完整版压缩包。")
        return r

    models = os.path.join(ocr_pkg_dir, "models")
    if not os.path.isdir(models):
        r.update(code="MODEL_MISSING",
                 reason="图片识别模型目录缺失",
                 solution="模型被杀毒软件删除或解压不完整，请重新完整解压压缩包，"
                          "并检查杀毒软件的隔离区。")
        return r
    lack = [m for m in ("ch_PP-OCRv3_det_infer.onnx", "ch_PP-OCRv3_rec_infer.onnx")
            if not os.path.exists(os.path.join(models, m))]
    if lack:
        r.update(code="MODEL_MISSING",
                 reason="图片识别模型文件缺失",
                 solution="模型被杀毒软件删除或解压不完整，请重新完整解压压缩包，"
                          "并检查杀毒软件的隔离区。")
        return r

    # 5) 真正 import 并推理
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except ImportError as exc:
        r.update(code="OCR_BROKEN",
                 reason="图片识别组件损坏",
                 solution=f"组件文件不完整，请重新完整解压压缩包（{exc}）")
        return r

    # 5) 内存（生产环境常见：老机器内存不足）
    mem = check_memory()
    if mem["available_mb"] and mem["available_mb"] < 700:
        r.update(code="LOW_MEMORY",
                 reason=f"当前可用内存仅 {mem['available_mb']} MB，不足以加载识别模型（需约 700MB）",
                 solution="请关闭其他占用内存的程序（如浏览器多开、Office、CAD）后重试。"
                          "建议本机内存配置 4GB 以上。")
        return r

    # 6) 快速可用性判断
#    刻意不做实际推理探测：加载 ONNX 模型需 3-5 秒且占 700MB 内存，
#    每次打开资料库页都做一次会明显卡顿。真正的推理验证放在
#    /api/ocr/image 上传时执行，失败会返回同样的诊断信息。
    r.update(available=True, status=OK, code="READY",
             reason="功能正常", solution="")
    r["probe"] = "静态检查通过（实际推理在上传图片时验证）"
    return r


def full_check() -> dict:
    """完整自检。"""
    os_i = check_os()
    vc = check_vc_runtime()
    mem = check_memory()
    disk = check_disk()
    writable = check_writable()
    cnpath = check_chinese_path()
    font = check_chinese_font()
    ocr = diagnose_ocr()

    blockers, warnings = [], []

    if os_i["level"] in ("too_low", "unsupported"):
        blockers.append("操作系统版本过低")
    if not writable["ok"]:
        blockers.append("程序目录不可写")
    if vc["ok"] is False:
        warnings.append("缺少 VC++ 运行库")
    if disk["free_mb"] and disk["free_mb"] < MIN_FREE_DISK_MB:
        warnings.append(f"磁盘剩余空间仅 {disk['free_gb']} GB")
    if mem["total_mb"] and mem["total_mb"] < 2048:
        warnings.append(f"内存偏小（{round(mem['total_mb'] / 1024, 1)} GB）")
    if not font["ok"]:
        warnings.append("缺少中文字体（PDF 中文可能乱码）")
    if cnpath["has_chinese"] and cnpath["has_space"]:
        warnings.append("安装路径含中文和空格，建议移到纯英文路径")
    if ocr["status"] != OK:
        warnings.append("图片识别不可用")

    if blockers:
        overall = FAIL
    elif warnings:
        overall = WARN
    else:
        overall = OK

    return {"overall": overall, "blockers": blockers, "warnings": warnings,
            "os": os_i, "vc": vc, "memory": mem, "disk": disk,
            "writable": writable, "chinese_path": cnpath, "font": font, "ocr": ocr}


def print_report() -> int:
    """命令行自检报告。"""
    r = full_check()
    L = "=" * 64
    print(L)
    print("  Felix 报价系统 — 环境自检")
    print(L)

    tag = {OK: "[正常]", WARN: "[注意]", FAIL: "[无法运行]"}
    print(f"\n  {tag[r['overall']]}\n")
    print(f"  {'系统版本':<12}: {r['os']['desc']}")
    print(f"  {'系统架构':<12}: {win_version_info().get('machine', '?')}")
    print(f"  {'物理内存':<12}: {round(r['memory']['total_mb'] / 1024, 1)} GB"
          f"（可用 {round(r['memory']['available_mb'] / 1024, 1)} GB）")
    print(f"  {'磁盘空间':<12}: {r['disk']['drive']} 剩余 {r['disk']['free_gb']} GB")
    print(f"  {'目录写入':<12}: {'正常' if r['writable']['ok'] else '不可写 —— ' + r['writable'].get('error', '')}")
    print(f"  {'中文字体':<12}: {r['font']['font'] or '未检测到'}")
    print(f"  {'VC++ 运行库':<12}: {'正常' if r['vc']['ok'] else '缺失 —— ' + '、'.join(m['dll'] for m in r['vc']['missing'])}")

    o = r["ocr"]
    print()
    print(f"  {tag.get(o['status'], '[注意]')} 图片识别（OCR）")
    if o["status"] == OK:
        print(f"      {o['reason']}")
    else:
        print(f"      原因：{o['reason']}")
        if o.get("solution"):
            print(f"      处理：{o['solution']}")

    if r["warnings"]:
        print()
        print("  提示：")
        for w in r["warnings"]:
            print(f"    · {w}")
    if r["blockers"]:
        print()
        print("  阻塞问题：")
        for b in r["blockers"]:
            print(f"    × {b}")
    print()
    print(L)
    return 0 if r["overall"] != FAIL else 1


if __name__ == "__main__":
    sys.exit(print_report())