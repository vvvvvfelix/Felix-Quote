"""冒烟自测 — 每晚迭代的验收关卡。

做什么：
1. 拉起真实服务（会跑建库 → 版本化迁移 → 灌种子，迁移出错在这里必炸）
2. 抓 /openapi.json，校验 HTTP 200 且能解析出路由
3. 校验指定模块前缀的 API 已注册（命令行传入）
4. 关停服务，返回退出码

用法：
    python tools/smoke_test.py                       # 只做基础健康检查
    python tools/smoke_test.py /api/org /api/hr      # 额外校验这些前缀存在
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = r"E:/workbuddy/felix-quote/src"
PY = BASE + r"/runtime/python/python.exe"
PORT = 8791
URL = f"http://127.0.0.1:{PORT}"

# 隔离库：开发/验证一律用临时库，绝不污染客户真实库 data/wv.db。
# 真库由 database.DB_PATH 读 WV_DB 环境变量决定；这里指向独立 devtest 库，
# 并在每次运行前删除，保证从干净种子起步（种子幂等，重复启动数据一致）。
DEV_DB = os.path.join(BASE, ".qa_tmp", "wv_devtest.db")
os.makedirs(os.path.dirname(DEV_DB), exist_ok=True)
if os.path.exists(DEV_DB):
    os.remove(DEV_DB)
# T1 防护（M006）：隔离库必须位于 src/data/ 之外，否则会污染或命中真实库 wv.db。
_DATA_DIR = os.path.abspath(os.path.join(BASE, "data"))
_abs_dev = os.path.abspath(DEV_DB)
if _abs_dev == _DATA_DIR or _abs_dev.startswith(_DATA_DIR + os.sep):
    sys.stderr.write(
        "[FATAL] 隔离库 %s 位于真实库目录 src/data/ 下，会污染或命中真实库 wv.db，"
        "已拒绝运行。\n请通过 WV_DB 环境变量把测试库指向 src/data/ 之外的路径"
        "（如 .qa_tmp/）。\n" % DEV_DB)
    sys.exit(2)

DEV_ENV = {**os.environ, "WV_DB": DEV_DB}


def wait_up(timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(URL + "/openapi.json", timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(1)
    return False


def _normalize(arg: str) -> str:
    """Git Bash 会把 /api/xxx 转成 Windows 绝对路径，这里还原成 /api/xxx。"""
    m = re.search(r"/api/[^\s]*", arg)
    return m.group(0) if m else arg


def main():
    args = sys.argv[1:]
    prefixes = [_normalize(a) for a in args if not a.startswith("--")]
    # --absent=/api/xxx 断言该前缀**不**存在 —— 用于验证模块关闭后路由确实摘除
    absent = [_normalize(a.split("=", 1)[1])
              for a in args if a.startswith("--absent=")]
    print(f"[smoke] 启动服务 {URL} ...")
    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "app.main:app", "--port", str(PORT),
         "--log-level", "warning"],
        cwd=BASE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        env=DEV_ENV,
    )
    ok = False
    try:
        if not wait_up():
            proc.kill()
            out = proc.stdout.read() if proc.stdout else ""
            print("[smoke] FAIL 服务未能在 60s 内就绪")
            print(out[-4000:])
            return 1

        with urllib.request.urlopen(URL + "/openapi.json", timeout=10) as r:
            spec = json.load(r)
        paths = list(spec.get("paths", {}))
        print(f"[smoke] 服务就绪，已注册 {len(paths)} 条路由")

        # 首页可访问
        with urllib.request.urlopen(URL + "/", timeout=10) as r:
            html = r.read().decode("utf-8", "replace")
        assert r.status == 200 and len(html) > 200, "首页异常"
        print("[smoke] 首页 OK")

        missing = [p for p in prefixes
                   if not any(k.startswith(p) for k in paths)]
        if missing:
            print(f"[smoke] FAIL 以下 API 前缀未注册: {missing}")
            return 2
        for p in prefixes:
            print(f"[smoke] 路由前缀 OK: {p}")

        leaked = [p for p in absent
                  if any(k.startswith(p) for k in paths)]
        if leaked:
            print(f"[smoke] FAIL 以下前缀本应随模块关闭而消失，却仍在: {leaked}")
            return 5
        for p in absent:
            print(f"[smoke] 已确认摘除: {p}")

        ok = True
        return 0
    except urllib.error.HTTPError as e:
        print(f"[smoke] FAIL HTTP {e.code}")
        return 3
    except AssertionError as e:
        print(f"[smoke] FAIL {e}")
        return 4
    finally:
        if not ok:
            try:
                proc.kill()
            except Exception:
                pass
        else:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
        print("[smoke] 服务已关停")


if __name__ == "__main__":
    sys.exit(main())
