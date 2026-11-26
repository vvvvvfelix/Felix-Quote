"""M004 数据范围扩展自测 —— 真实起服务 + 登录 + 调接口 + 断言三轴范围与组织切换器。

验证点：
- 角色库扩到 董事长/总经理/厂长/人事/部门经理/主管/研发/技术员（新增 8 个）
- 三轴范围：编制看板按 company / factory / bu 轴过滤（显式 org_unit_id + 用户自身 scope）
- 组织切换器：switch-options 仅返回当前公司内的 company/factory/bu 节点；
  切换后 /api/org/me 反映当前组织；越权切换到别的公司被拒 403

用法：
    python tools/test_scope.py
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
PORT = 8795
URL = f"http://127.0.0.1:{PORT}"

# 隔离库：自测一律用独立临时库，绝不污染客户真实库 data/wv.db。
DEV_DB = os.path.join(BASE, ".qa_tmp", "wv_test_scope.db")
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

FAILED = []
PASSED = 0


def check(name, cond, extra=""):
    global PASSED
    print(("  OK  " if cond else "  FAIL") + f"  {name}" + (f"  {extra}" if extra else ""))
    if cond:
        PASSED += 1
    else:
        FAILED.append(name)


def call(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(URL + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode("utf-8")
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


def wait_up(timeout=90):
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
    print("[scope] 启动服务 ...")
    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "app.main:app", "--port", str(PORT),
         "--log-level", "warning"],
        cwd=BASE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        env=DEV_ENV,
    )
    try:
        if not wait_up():
            print("[scope] FAIL 服务未就绪")
            print((proc.stdout.read() if proc.stdout else "")[-3000:])
            return 1

        st, r = call("POST", "/api/auth/login",
                     {"username": "admin", "password": "admin.123"})
        check("管理员登录", st == 200 and bool(r and r.get("token")), f"status={st}")
        if st != 200:
            return 1
        tk = r["token"]

        # ================= 1. 角色库扩展 =================
        st, roles = call("GET", "/api/org/roles", token=tk)
        codes = {x["code"] for x in roles}
        new_codes = {"chairman", "gm", "factory_head", "hr", "manager",
                     "supervisor", "rnd", "technician"}
        check("新增 8 个三轴组织角色", new_codes.issubset(codes),
              str(sorted(new_codes & codes)))
        check("角色总数 ≥ 16（8 原 + 8 新）", len(roles) >= 16, str(len(roles)))

        # ================= 2. 组织树 company/factory/bu 节点 =================
        st, comps = call("GET", "/api/structure/units?org_type=company&active=1", token=tk)
        st, facts = call("GET", "/api/structure/units?org_type=factory&active=1", token=tk)
        check("存在公司节点与工厂节点", st == 200 and len(comps) >= 1 and len(facts) >= 1,
              f"companies={len(comps)} factories={len(facts)}")
        cid = comps[0]["id"]
        # 取该公司下的一个工厂（用于显式轴过滤对照）
        fid = next((f["id"] for f in facts
                    if f["path"].startswith(comps[0]["path"])), facts[0]["id"])

        # ================= 3. 看板三轴过滤（显式 org_unit_id） =================
        st, bc = call("GET", f"/api/position/board?dim=org&scope=company&org_unit_id={cid}",
                      token=tk)
        st, bf = call("GET", f"/api/position/board?dim=org&scope=factory&org_unit_id={fid}",
                      token=tk)
        check("公司轴看板返回编制数 > 工厂轴看板",
              bc["total"]["positions"] > bf["total"]["positions"] >= 1,
              f"company={bc['total']['positions']} factory={bf['total']['positions']}")

        # admin 自身 scope=all：company 轴（无 org_unit_id）等于全部编制
        st, ball = call("GET", "/api/position/board?dim=org&scope=company", token=tk)
        st, ball2 = call("GET", "/api/position/board?dim=org&scope=self", token=tk)
        check("admin(scope=all) 公司轴 = 全部编制（不受轴限制）",
              ball["total"]["positions"] == ball2["total"]["positions"]
              and ball["total"]["positions"] > 0,
              f"{ball['total']['positions']} vs {ball2['total']['positions']}")

        # 非法 scope 被拒
        st, _ = call("GET", "/api/position/board?dim=org&scope=nope", token=tk)
        check("看板非法 scope 被拒 400", st == 400, f"status={st}")

        # ================= 4. 组织切换器 =================
        st, opts = call("GET", "/api/org/switch-options", token=tk)
        check("admin 可切换全部 company/factory/bu 节点",
              st == 200 and len(opts) >= 3
              and {"company", "factory", "bu"}.issubset({o["org_type"] for o in opts}),
              str(sorted({o["org_type"] for o in opts})))
        st, sw = call("POST", "/api/org/switch", {"org_unit_id": fid}, token=tk)
        check("admin 切换到某工厂 200", st == 200, f"status={st}")
        st, me = call("GET", "/api/org/me", token=tk)
        check("切换后 me 反映当前工厂",
              st == 200 and me["current_org_unit_id"] == fid
              and me["current_org_type"] == "factory", str(me.get("current_org_unit_id")))
        # 切回公司节点
        st, _ = call("POST", "/api/org/switch", {"org_unit_id": cid}, token=tk)
        check("admin 切回公司节点 200", st == 200, f"status={st}")

        # ================= 5. 角色级范围隔离（factory_head 自身 scope=factory） ====
        ts = "fh_" + str(int(time.time()) % 100000)
        st, ru = call("POST", "/api/org/users", {
            "username": ts, "password": "Test@2026", "full_name": "自测厂长",
            "role": "factory_head", "org_unit_id": facts[0]["id"]}, token=tk)
        check("创建 factory_head 测试账号", st == 200 and bool(ru.get("id")), str(ru)[:120])
        if st != 200:
            return 1
        st, lg = call("POST", "/api/auth/login",
                      {"username": ts, "password": "Test@2026"})
        check("factory_head 登录", st == 200 and bool(lg.get("token")), f"status={st}")
        tk2 = lg["token"]

        st, fh_me = call("GET", "/api/org/me", token=tk2)
        check("factory_head 有 factory 范围标签", st == 200
              and fh_me["scope"] == "factory", str(fh_me.get("scope")))
        st, fh_opts = call("GET", "/api/org/switch-options", token=tk2)
        check("factory_head 仅能切本公司内节点（少于 admin 全集）",
              st == 200 and len(fh_opts) < len(opts) and len(fh_opts) >= 1,
              f"fh={len(fh_opts)} admin={len(opts)}")
        fh_company = fh_me["current_org_rollups"].get("company_id")
        fh_fids = [o["id"] for o in fh_opts if o["org_type"] == "factory"]
        if fh_fids:
            st, _ = call("POST", "/api/org/switch", {"org_unit_id": fh_fids[0]}, token=tk2)
            check("factory_head 切到本公司内工厂 200", st == 200, f"status={st}")
            st, bfr = call("GET", "/api/position/board?dim=org&scope=factory", token=tk2)
            st, bco = call("GET", "/api/position/board?dim=org&scope=company", token=tk2)
            check("factory_head 公司轴编制 ≥ 工厂轴编制（自身范围生效）",
                  bco["total"]["positions"] >= bfr["total"]["positions"] > 0,
                  f"company={bco['total']['positions']} factory={bfr['total']['positions']}")

        # 越权：factory_head 切到别的公司的节点 → 403
        other = next((o["id"] for o in opts if o["id"] not in {x["id"] for x in fh_opts}),
                     None)
        if other is not None:
            st, _ = call("POST", "/api/org/switch", {"org_unit_id": other}, token=tk2)
            check("factory_head 切到外公司被拒 403", st == 403, f"status={st}")

        # 清理
        st, _ = call("DELETE", f"/api/org/users/{ru['id']}", token=tk)
        check("测试账号已停用（软删）", st == 200, f"status={st}")

        print(f"\n[scope] 共 {PASSED} 项通过"
              + (f"，{len(FAILED)} 项失败：" + "、".join(FAILED) if FAILED
                 else "，全部通过"))
        return 1 if FAILED else 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        print("[scope] 服务已关停")


if __name__ == "__main__":
    sys.exit(main())
