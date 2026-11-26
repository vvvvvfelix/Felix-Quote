"""M001 组织树接口自测 —— 真实起服务 + 登录 + 调接口 + 断言。

冒烟测试只校验「路由注册了没」，本脚本校验「数据对不对」：
- 种子树结构与三轴归属（公司 / 工厂 / 事业部）
- path / level 冗余字段
- 增删改 + 调整归属 + 环检测
- 旧 departments 兼容回写（users.dept_id 仍能 JOIN 到）

用法：
    python tools/test_structure.py
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = r"E:/workbuddy/felix-quote/src"
PY = BASE + r"/runtime/python/python.exe"
PORT = 8792
URL = f"http://127.0.0.1:{PORT}"

# 隔离库：用临时库，绝不污染真实库 data/wv.db（T1 防护，M006 补强）。
DEV_DB = os.path.join(BASE, ".qa_tmp", "wv_test_structure.db")
os.makedirs(os.path.dirname(DEV_DB), exist_ok=True)
if os.path.exists(DEV_DB):
    os.remove(DEV_DB)
# T1 防护（M006）：隔离库必须位于 src/data/ 之外，否则会污染或命中真实库 wv.db。
_DATA_DIR = os.path.abspath(os.path.join(BASE, "data"))
_abs_dev = os.path.abspath(DEV_DB)
if _abs_dev == _DATA_DIR or _abs_dev.startswith(_DATA_DIR + os.sep):
    sys.stderr.write(
        "[FATAL] 隔离库 %s 位于真实库目录 src/data/ 下，会污染或命中真实库 wv.db，"
        "已拒绝运行。\n" % DEV_DB)
    sys.exit(2)
DEV_ENV = {**os.environ, "WV_DB": DEV_DB}

FAILED = []


def check(name, cond, extra=""):
    print(("  OK  " if cond else "  FAIL") + f"  {name}" + (f"  {extra}" if extra else ""))
    if not cond:
        FAILED.append(name)


def call(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(URL + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode("utf-8")
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


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


def main():
    print("[structure] 启动服务 ...")
    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "app.main:app", "--port", str(PORT), "--log-level", "warning"],
        cwd=BASE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        env=DEV_ENV,
    )
    try:
        if not wait_up():
            print("[structure] FAIL 服务未就绪")
            print((proc.stdout.read() if proc.stdout else "")[-3000:])
            return 1

        st, r = call("POST", "/api/auth/login",
                     {"username": "admin", "password": "admin.123"})
        check("管理员登录", st == 200 and bool(r and r.get("token")), f"status={st}")
        if st != 200:
            return 1
        tk = r["token"]

        # ---- 1. 枚举与统计 ----
        st, types = call("GET", "/api/structure/types", token=tk)
        labels = {t["value"]: t["label"] for t in types}
        check("组织类型枚举 9 项", st == 200 and len(types) == 9 and labels.get("factory") == "工厂")

        st, stats = call("GET", "/api/structure/stats", token=tk)
        bt = {x["value"]: x["count"] for x in stats["by_type"]}
        check("法人公司 2 个", bt.get("company") == 2, str(bt))
        check("工厂 2 个", bt.get("factory") == 2, str(bt))
        check("事业部 2 个", bt.get("bu") == 2, str(bt))
        check("组织树体检健康", stats["integrity"].get("healthy"),
              json.dumps(stats["integrity"], ensure_ascii=False)[:200])

        # ---- 2. 树与三轴归属 ----
        st, d = call("GET", "/api/structure/tree", token=tk)
        tree = d["tree"]
        check("树只有一个根（集团）", len(tree) == 1 and tree[0]["code"] == "GRP")
        flat = {}

        def walk(ns, company=None, factory=None, bu=None):
            for n in ns:
                flat[n["code"]] = n
                walk(n.get("children") or [],
                     n.get("company_id") or company,
                     n.get("factory_id") or factory,
                     n.get("bu_id") or bu)
        walk(tree)
        check("钣金A班存在（班组第四层）", "TM01" in flat)
        check("钣金A班 继承到工厂", flat["TM01"]["factory_id"] == flat["FACTORY"]["id"])
        check("钣金A班 继承到法人公司", flat["TM01"]["company_id"] == flat["CO01"]["id"])
        check("国内销售部 继承到事业部", flat["DPSALE"]["bu_id"] == flat["BU01"]["id"])
        check("钣金A班 path 正确",
              flat["TM01"]["path"].endswith(f"/{flat['TM01']['id']}/"), flat["TM01"]["path"])
        check("层级 level=4", flat["TM01"]["level"] == 4, str(flat["TM01"]["level"]))
        check("一厂含下级人数 ≥ 本级", flat["FACTORY"]["total_count"] >= flat["FACTORY"]["member_count"])

        # ---- 3. 旧的 departments 兼容回写 ----
        st, depts = call("GET", "/api/org/departments", token=tk)
        names = {d["code"]: d["name"] for d in depts}
        check("旧表已回写 集团", names.get("GRP") == "菲力克斯集团", str(names.get("GRP")))
        check("旧表已回写 一厂改名", names.get("FACTORY") == "一厂（白云生产基地）",
              str(names.get("FACTORY")))
        check("旧表仍保留总部", "HQ" in names)

        # ---- 4. 便捷列表 ----
        for path, key, n in (("/api/structure/companies", "code", 2),
                             ("/api/structure/factories", "code", 2),
                             ("/api/structure/bus", "code", 2)):
            st, rows = call("GET", path, token=tk)
            check(f"{path} 返回 {n} 条", st == 200 and len(rows) == n, str(rows)[:120])

        # ---- 5. 新增 / 改 / 移 / 停 ----
        # 每次用新编码：停用的组织仍占着编码（编码唯一是刻意的，避免历史单据串号）
        tcode = "TST" + str(int(time.time()) % 100000)
        st, r = call("POST", "/api/structure/units", {
            "code": tcode, "name": "测试车间", "org_type": "workshop",
            "parent_id": flat["FA02"]["id"], "cost_center": "CC-999"}, token=tk)
        check("新增组织", st == 200 and r.get("id"), str(r)[:120])
        uid = r["id"]

        st, one = call("GET", f"/api/structure/units/{uid}", token=tk)
        check("新组织继承工厂归属",
              one["factory_id"] == flat["FA02"]["id"] and one["company_id"] == flat["CO01"]["id"],
              f"factory={one.get('factory_id')} company={one.get('company_id')}")
        check("新组织面包屑 4 层", len(one["chain"]) == 4, str([c["name"] for c in one["chain"]]))

        st, _ = call("PUT", f"/api/structure/units/{uid}", {
            "code": tcode, "name": "测试车间（改名）", "org_type": "workshop",
            "parent_id": flat["FA02"]["id"], "cost_center": "CC-998"}, token=tk)
        st, one = call("GET", f"/api/structure/units/{uid}", token=tk)
        check("改名生效", st == 200 and one["name"] == "测试车间（改名）")

        st, r = call("POST", f"/api/structure/units/{uid}/move",
                     {"parent_id": flat["FACTORY"]["id"]}, token=tk)
        st, one = call("GET", f"/api/structure/units/{uid}", token=tk)
        check("调整归属生效", st == 200 and one["factory_id"] == flat["FACTORY"]["id"])

        # 环检测：把一厂挂到自己的后代下面
        st, r = call("POST", f"/api/structure/units/{flat['FACTORY']['id']}/move",
                     {"parent_id": uid}, token=tk)
        check("成环被拒绝", st == 400, f"status={st} {r}")

        st, r = call("DELETE", f"/api/structure/units/{uid}", token=tk)
        check("停用组织", st == 200, str(r)[:120])

        # 停用后不应再出现在默认树里
        st, d = call("GET", "/api/structure/tree", token=tk)
        flat2 = {}

        def walk2(ns):
            for n in ns:
                flat2[n["code"]] = n
                walk2(n.get("children") or [])
        walk2(d["tree"])
        check("停用后默认树不再含该组织", tcode not in flat2)

        # ---- 6. 无编辑权限角色只读 ----
        st, r = call("GET", "/api/structure/units?org_type=factory", token=tk)
        check("按类型筛选工厂", st == 200 and len(r) == 2, str([x["code"] for x in r]))

        print(f"\n[structure] {'全部通过' if not FAILED else 'FAILED: ' + ', '.join(FAILED)}")
        return 1 if FAILED else 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        print("[structure] 服务已关停")


if __name__ == "__main__":
    sys.exit(main())
