"""M005 审批引擎自测 —— 真实起服务 + 多角色登录 + 跑通完整审批链路。

覆盖：
- 流程定义种子（请假审批 LEAVE）存在
- 或签 + 条件分支（<3 天直达结束；≥3 天走人事审批）
- 回调：审批通过后请假单状态变 approved
- 会签：同一节点多人须全部批准才通过
- 转办：任务交他人，旧任务转 transferred、新任务 pending
- 加签：节点新增必签人，实例结束时其任务被取消
- 驳回：节点驳回 → 实例 rejected → 请假单 rejected
- 越权：非审批人处理任务 → 403
- 成环防护（远程 DoS）：保存入口拦截 / 引擎兜底 / 故障隔离 + 回调补偿

用法：python tools/test_workflow.py
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

BASE = r"E:/workbuddy/felix-quote/src"
PY = BASE + r"/runtime/python/python.exe"
PORT = 8796
URL = f"http://127.0.0.1:{PORT}"

DEV_DB = os.path.join(BASE, ".qa_tmp", "wv_test_workflow.db")
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


def new_user(token, username, role):
    st, r = call("POST", "/api/org/users", {
        "username": username, "password": "Test@2026", "full_name": username,
        "role": role}, token)
    return st, r


def call_to(method, path, body=None, token=None, timeout=20):
    """带显式超时的调用；超时返回 (0, {})，用于验证「接口没有挂死」。"""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(URL + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw
    except Exception as e:
        print(f"        [超时/异常] {method} {path} → {type(e).__name__}")
        return 0, {}


def timed(method, path, body=None, token=None, timeout=20):
    """返回 (status, body, 耗时秒)。"""
    t0 = time.time()
    st, r = call_to(method, path, body, token, timeout)
    return st, r, time.time() - t0


# 成环的流程定义：a 自环 + a→b→a 长环（QA 复现远程 DoS 用的就是这类数据）
CYC_SELF = {
    "nodes": [{"id": "start", "type": "start"},
              {"id": "a", "type": "approval", "name": "A", "mode": "or",
               "assignee": {"role": "manager"}},
              {"id": "end", "type": "end"}],
    "edges": [{"from": "start", "to": "a"}, {"from": "a", "to": "a"},
              {"from": "a", "to": "end"}],
}
CYC_LONG = {
    "nodes": [{"id": "start", "type": "start"},
              {"id": "a", "type": "approval", "name": "A", "mode": "or",
               "assignee": {"role": "manager"}},
              {"id": "b", "type": "approval", "name": "B", "mode": "or",
               "assignee": {"role": "hr"}},
              {"id": "end", "type": "end"}],
    "edges": [{"from": "start", "to": "a"}, {"from": "a", "to": "b"},
              {"from": "b", "to": "a"}, {"from": "b", "to": "end"}],
}


def main():
    print("[wf] 启动服务 ...")
    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "app.main:app", "--port", str(PORT),
         "--log-level", "warning"],
        cwd=BASE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP, env=DEV_ENV)
    try:
        if not wait_up():
            print("[wf] FAIL 服务未就绪")
            print((proc.stdout.read() if proc.stdout else "")[-3000:])
            return 1

        # ---- 登录 admin ----
        st, adm = call("POST", "/api/auth/login",
                       {"username": "admin", "password": "admin.123"})
        check("管理员登录", st == 200 and bool(adm and adm.get("token")), f"status={st}")
        if st != 200:
            return 1
        atk = adm["token"]

        # ---- 建测试账号 ----
        for u in ("wf_mgr", "wf_hr", "wf_ap1", "wf_ap2", "wf_mgr2"):
            role = "manager" if u == "wf_mgr" else "hr" if u == "wf_hr" else \
                "manager" if u == "wf_mgr2" else "sales"
            st, ru = new_user(atk, u, role)
            check(f"创建 {u}({role})", st == 200 and bool(ru.get("id")), str(ru)[:80])
            if st != 200:
                return 1

        def login(u):
            st, r = call("POST", "/api/auth/login",
                         {"username": u, "password": "Test@2026"})
            return r["token"] if st == 200 and r else None

        mtk = login("wf_mgr")
        htk = login("wf_hr")
        a1tk = login("wf_ap1")
        a2tk = login("wf_ap2")
        m2tk = login("wf_mgr2")

        # ---- 请假流程定义存在 ----
        st, defs = call("GET", "/api/workflow/defs", token=atk)
        leave = next((d for d in defs if d["code"] == "LEAVE"), None)
        check("请假审批流程已种子化", st == 200 and leave is not None)
        if not leave:
            return 1
        def_id = leave["id"]

        # ---- 用例 A：或签 + 条件分支(<3 天直达结束) + 回调 ----
        st, lv = call("POST", "/api/workflow/leave",
                      {"leave_type": "personal", "days": 1, "reason": "t"}, token=a1tk)
        check("申请人提交请假(<3天)", st == 200 and bool(lv.get("instance_id")), f"status={st}")
        iidA = lv["instance_id"]
        st, started = call("GET", "/api/workflow/my-started", token=a1tk)
        check("我发起的含该实例(running)", st == 200 and any(
            i["id"] == iidA and i["status"] == "running" for i in started))
        st, mts = call("GET", "/api/workflow/my-tasks", token=mtk)
        mtask = next((t for t in mts if t["instance_id"] == iidA), None)
        check("部门主管收到待办", st == 200 and mtask is not None)
        st, _ = call("POST", f"/api/workflow/tasks/{mtask['id']}/approve",
                     {"comment": "准"}, token=mtk)
        check("主管批准 → 实例通过(<3天无人事)", st == 200)
        st, instA = call("GET", f"/api/workflow/instances/{iidA}", token=atk)
        check("实例状态 approved", st == 200 and instA["status"] == "approved",
              str(instA.get("status")))
        st, leavesA = call("GET", "/api/workflow/leave", token=a1tk)
        check("回调：请假单变 approved", st == 200 and any(
            l["id"] == lv["leave_id"] and l["status"] == "approved" for l in leavesA))
        st, doneM = call("GET", "/api/workflow/my-done", token=mtk)
        check("主管已办含该任务", st == 200 and any(
            t["id"] == mtask["id"] for t in doneM))

        # ---- 用例 B：条件分支(≥3 天走人事审批) ----
        st, lvB = call("POST", "/api/workflow/leave",
                       {"leave_type": "annual", "days": 5}, token=a1tk)
        iidB = lvB["instance_id"]
        st, mtsB = call("GET", "/api/workflow/my-tasks", token=mtk)
        tB = next((t for t in mtsB if t["instance_id"] == iidB), None)
        st, _ = call("POST", f"/api/workflow/tasks/{tB['id']}/approve",
                     {"comment": "准"}, token=mtk)
        check("主管批准(≥3天)", st == 200)
        st, mtsH = call("GET", "/api/workflow/my-tasks", token=htk)
        tH = next((t for t in mtsH if t["instance_id"] == iidB), None)
        check("人事收到待办(条件分支生效)", st == 200 and tH is not None and
              tH["node_name"] == "人事审批")
        st, _ = call("POST", f"/api/workflow/tasks/{tH['id']}/approve",
                     {"comment": "准"}, token=htk)
        check("人事批准 → 实例通过", st == 200)
        st, instB = call("GET", f"/api/workflow/instances/{iidB}", token=atk)
        check("实例 B approved", st == 200 and instB["status"] == "approved")

        # ---- 用例 C：会签（自定义流程，or→and 双经理必须全批） ----
        def_c = {"nodes": [
            {"id": "start", "type": "start"},
            {"id": "sign", "type": "approval", "name": "会签", "mode": "and",
             "assignee": {"role": "manager"}},
            {"id": "end", "type": "end"}],
            "edges": [{"from": "start", "to": "sign"}, {"from": "sign", "to": "end"}]}
        st, _ = call("POST", "/api/workflow/defs",
                     {"code": "WF_AND", "name": "会签测试",
                      "definition": def_c}, token=atk)
        check("创建会签流程定义", st == 200, f"status={st}")
        st, defs2 = call("GET", "/api/workflow/defs", token=atk)
        and_id = next(d["id"] for d in defs2 if d["code"] == "WF_AND")
        st, iC = call("POST", "/api/workflow/start",
                      {"def_id": and_id, "title": "会签单", "form": {"x": 1},
                       "biz_type": "test", "biz_id": 0}, token=a1tk)
        iidC = iC["id"]
        st, mtsC = call("GET", "/api/workflow/my-tasks", token=mtk)
        tC1 = next(t for t in mtsC if t["instance_id"] == iidC)
        st, _ = call("POST", f"/api/workflow/tasks/{tC1['id']}/approve",
                     {"comment": "1"}, token=mtk)
        check("会签：一人批准后实例仍 running", st == 200)
        st, instC1 = call("GET", f"/api/workflow/instances/{iidC}", token=atk)
        check("会签未完成 → running", st == 200 and instC1["status"] == "running")
        st, mtsC2 = call("GET", "/api/workflow/my-tasks", token=m2tk)
        tC2 = next((t for t in mtsC2 if t["instance_id"] == iidC), None)
        check("会签第二人收到待办", st == 200 and tC2 is not None)
        st, _ = call("POST", f"/api/workflow/tasks/{tC2['id']}/approve",
                     {"comment": "2"}, token=m2tk)
        check("会签：第二人批准 → 通过", st == 200)
        st, instC2 = call("GET", f"/api/workflow/instances/{iidC}", token=atk)
        check("会签全部批准 → approved", st == 200 and instC2["status"] == "approved")

        # ---- 用例 D：转办 ----
        st, lvD = call("POST", "/api/workflow/leave",
                       {"leave_type": "personal", "days": 1}, token=a2tk)
        iidD = lvD["instance_id"]
        st, mtsD = call("GET", "/api/workflow/my-tasks", token=mtk)
        tD = next(t for t in mtsD if t["instance_id"] == iidD)
        # 取 wf_mgr2 的 id
        _, users = call("GET", "/api/org/users", token=atk)
        mgr2_id = next(u["id"] for u in users if u["username"] == "wf_mgr2")
        st, tr = call("POST", f"/api/workflow/tasks/{tD['id']}/transfer",
                      {"target_user_id": mgr2_id, "comment": "转你"}, token=mtk)
        check("转办成功", st == 200 and bool(tr.get("new_task_id")))
        st, mtsM2 = call("GET", "/api/workflow/my-tasks", token=m2tk)
        check("被转办人收到待办", st == 200 and any(
            t["instance_id"] == iidD for t in mtsM2))
        # 旧任务状态 transferred（实例详情可见）
        st, instD = call("GET", f"/api/workflow/instances/{iidD}", token=atk)
        check("原任务转 transferred", st == 200 and any(
            t["id"] == tD["id"] and t["status"] == "transferred" for t in instD["tasks"]
        ) if instD else False)
        st, _ = call("POST", f"/api/workflow/tasks/{tr['new_task_id']}/approve",
                     {"comment": "准"}, token=m2tk)
        check("被转办人批准 → 通过", st == 200)
        st, instD2 = call("GET", f"/api/workflow/instances/{iidD}", token=atk)
        check("转办后实例 approved", st == 200 and instD2["status"] == "approved")

        # ---- 用例 E：加签 ----
        st, lvE = call("POST", "/api/workflow/leave",
                       {"leave_type": "personal", "days": 1}, token=a2tk)
        iidE = lvE["instance_id"]
        st, mtsE = call("GET", "/api/workflow/my-tasks", token=mtk)
        tE = next(t for t in mtsE if t["instance_id"] == iidE)
        st, ad = call("POST", f"/api/workflow/tasks/{tE['id']}/add-sign",
                      {"target_user_id": mgr2_id, "comment": "加签"}, token=mtk)
        check("加签成功", st == 200 and bool(ad.get("new_task_id")))
        st, mtsE2 = call("GET", "/api/workflow/my-tasks", token=m2tk)
        check("加签人收到待办", st == 200 and any(
            t["instance_id"] == iidE and t["status"] == "pending" for t in mtsE2))
        st, _ = call("POST", f"/api/workflow/tasks/{tE['id']}/approve",
                     {"comment": "准"}, token=mtk)
        check("原审批人批准", st == 200)
        st, instE = call("GET", f"/api/workflow/instances/{iidE}", token=atk)
        # 或签下原人批准即通过 → 实例结束，加签任务被取消
        check("加签任务随实例结束被取消", st == 200 and any(
            t["id"] == ad["new_task_id"] and t["status"] == "cancelled"
            for t in instE["tasks"]) and instE["status"] == "approved")

        # ---- 用例 F：驳回 ----
        st, lvF = call("POST", "/api/workflow/leave",
                       {"leave_type": "personal", "days": 1}, token=a2tk)
        iidF = lvF["instance_id"]
        st, mtsF = call("GET", "/api/workflow/my-tasks", token=mtk)
        tF = next(t for t in mtsF if t["instance_id"] == iidF)
        st, _ = call("POST", f"/api/workflow/tasks/{tF['id']}/reject",
                     {"comment": "不批"}, token=mtk)
        check("主管驳回 → 实例 rejected", st == 200)
        st, instF = call("GET", f"/api/workflow/instances/{iidF}", token=atk)
        check("实例 F rejected", st == 200 and instF["status"] == "rejected")
        st, leavesF = call("GET", "/api/workflow/leave", token=a2tk)
        check("回调：请假单变 rejected", st == 200 and any(
            l["id"] == lvF["leave_id"] and l["status"] == "rejected" for l in leavesF))

        # ---- 用例 G：越权 ----
        st, lvG = call("POST", "/api/workflow/leave",
                       {"leave_type": "personal", "days": 1}, token=a2tk)
        iidG = lvG["instance_id"]
        st, mtsG = call("GET", "/api/workflow/my-tasks", token=mtk)
        tG = next(t for t in mtsG if t["instance_id"] == iidG)
        st, _ = call("POST", f"/api/workflow/tasks/{tG['id']}/approve",
                     {"comment": "x"}, token=a2tk)   # a2tk 非审批人
        check("非审批人处理任务 → 403", st == 403, f"status={st}")

        # ---- 用例 H：撤回 ----
        st, lvH = call("POST", "/api/workflow/leave",
                       {"leave_type": "personal", "days": 1}, token=a2tk)
        iidH = lvH["instance_id"]
        st, _ = call("POST", f"/api/workflow/instances/{iidH}/cancel",
                     {"comment": "撤回"}, token=a2tk)   # 申请人撤回
        check("申请人撤回 → 200", st == 200, f"status={st}")
        st, instH = call("GET", f"/api/workflow/instances/{iidH}", token=atk)
        check("实例 H canceled", st == 200 and instH["status"] == "canceled")

        # ==================================================================
        # 用例 I：成环流程定义在保存入口被拒（远程 DoS 第一道闸门）
        # ==================================================================
        st, r = call("POST", "/api/workflow/defs",
                     {"code": "WF_CYC_SELF", "name": "自环",
                      "definition": CYC_SELF}, token=atk)
        detail = str((r or {}).get("detail", r))
        check("自环定义保存被拒 400", st == 400, f"{st} {detail[:160]}")
        check("错误指明成环的节点", st == 400 and "环路" in detail and "a" in detail,
              detail[:200])
        st, r2 = call("POST", "/api/workflow/defs",
                      {"code": "WF_CYC_LONG", "name": "长环",
                       "definition": CYC_LONG}, token=atk)
        detail2 = str((r2 or {}).get("detail", r2))
        check("a→b→a 长环定义保存被拒 400", st == 400, f"{st} {detail2[:160]}")
        check("长环错误给出完整环路径 a → b → a",
              st == 400 and "a → b → a" in detail2, detail2[:200])
        st, dc = call("GET", "/api/workflow/defs", token=atk)
        check("被拒的定义未落库",
              not any(d["code"].startswith("WF_CYC") for d in dc))

        # 指向不存在节点的边同样拦掉
        bad_edge = {"nodes": [{"id": "start", "type": "start"},
                              {"id": "end", "type": "end"}],
                    "edges": [{"from": "start", "to": "ghost"}]}
        st, r3 = call("POST", "/api/workflow/defs",
                      {"code": "WF_GHOST", "name": "悬空边",
                       "definition": bad_edge}, token=atk)
        check("指向不存在节点的边被拒 400", st == 400,
              f"{st} {str((r3 or {}).get('detail', r3))[:160]}")

        # 缺陷①：缺 type 的畸形节点在保存期就要拦下（原本会漏校验，起单时
        # 引擎取 node["type"] 抛 KeyError → 500）。这里验证保存即 400，而非 200 漏过。
        missing_type = {"nodes": [{"id": "start", "type": "start"},
                                 {"id": "b"},   # 缺 type 的畸形节点
                                 {"id": "end", "type": "end"}],
                        "edges": [{"from": "start", "to": "b"},
                                  {"from": "b", "to": "end"}]}
        st, r4 = call("POST", "/api/workflow/defs",
                      {"code": "WF_NOTYPE", "name": "缺type",
                       "definition": missing_type}, token=atk)
        check("缺 type 的畸形定义被拒 400（不当作合法漏过）",
              st == 400, f"{st} {str((r4 or {}).get('detail', r4))[:160]}")
        # 非法 type 同样拒绝
        bad_type = {"nodes": [{"id": "start", "type": "start"},
                             {"id": "b", "type": "weird"},
                             {"id": "end", "type": "end"}],
                    "edges": [{"from": "start", "to": "b"},
                              {"from": "b", "to": "end"}]}
        st, r5 = call("POST", "/api/workflow/defs",
                      {"code": "WF_BADTYPE", "name": "非法type",
                       "definition": bad_type}, token=atk)
        check("非法 type 的畸形定义被拒 400", st == 400, f"{st}")

        # ---- 用例 J：改定义成环同样被拒，且不会破坏原定义 ----
        st, rj = call("PUT", f"/api/workflow/defs/{and_id}",
                      {"definition": CYC_SELF}, token=atk)
        check("PUT 改成自环被拒 400", st == 400,
              f"{st} {str((rj or {}).get('detail', rj))[:160]}")
        st, dj = call("GET", "/api/workflow/defs", token=atk)
        wf_and = next((d for d in dj if d["id"] == and_id), None)
        edges = (wf_and or {}).get("definition", {}).get("edges", [])
        check("被拒的修改未落库（原定义仍是合法会签流程）",
              st == 200 and any(e.get("from") == "start" and e.get("to") == "sign"
                                for e in edges), str(edges)[:160])
        # 合法改名应当成功
        st, rj2 = call("PUT", f"/api/workflow/defs/{and_id}",
                       {"name": "会签测试(已改名)"}, token=atk)
        check("PUT 改名字（不带 definition）成功", st == 200, f"{st} {rj2}")
        # 改回去，避免影响其它用例
        call("PUT", f"/api/workflow/defs/{and_id}", {"name": "会签测试"}, token=atk)

        # ==================================================================
        # 用例 K：脏数据（绕过接口直接入库）+ 引擎兜底 + 故障隔离
        # ==================================================================
        # 成环定义直接塞进库，模拟「本闸门上线前存进去的历史脏数据」。
        # 审批人角色用一个不存在的角色：该节点无人可审 → 自动通过 → 引擎会在
        # 节点间无限往返（这正是历史上把进程拖死的路径）。
        dirty = {
            "nodes": [{"id": "start", "type": "start"},
                      {"id": "a", "type": "approval", "name": "A", "mode": "or",
                       "assignee": {"role": "no_such_role"}},
                      {"id": "end", "type": "end"}],
            "edges": [{"from": "start", "to": "a"}, {"from": "a", "to": "a"},
                      {"from": "a", "to": "end"}],
        }
        con = sqlite3.connect(DEV_DB)
        con.execute(
            "INSERT INTO workflow_defs(code,name,definition,is_active,created_at,"
            "updated_at) VALUES('WF_DIRTY','脏',?,1,datetime('now'),datetime('now'))",
            (json.dumps(dirty),))
        con.commit()
        con.close()

        st, dd = call("GET", "/api/workflow/defs", token=atk)
        dirty_id = next((d["id"] for d in dd if d["code"] == "WF_DIRTY"), None)
        check("脏数据已在库中（绕过接口直接入库）", dirty_id is not None)
        if dirty_id:
            st, rd, el = timed("POST", "/api/workflow/start",
                               {"def_id": dirty_id, "title": "脏数据单",
                                "form": {}}, token=a1tk, timeout=30)
            check("成环定义发起实例被拒（运行时二次拦截，不挂死）",
                  st == 400 and el < 30, f"{st} {el:.2f}s")

            # 关键验收：失败之后整个服务还能不能对外服务
            st, _ = call_to("GET", "/api/about", token=None, timeout=10)
            check("失败后 /api/about 仍 200（服务未被拖垮）", st == 200, f"status={st}")
            st, _ = call_to("POST", "/api/auth/login",
                            {"username": "admin", "password": "admin.123"},
                            timeout=10)
            check("失败后登录接口仍可用", st == 200, f"status={st}")

        # ---- 引擎侧兜底：假装环路检查失灵，引擎自己也必须收得住 ----
        runner = os.path.join(tempfile.gettempdir(), "wf_cycle_probe.py")
        with open(runner, "w", encoding="utf-8") as f:
            f.write('''
import sys, time
sys.path.insert(0, r"{base}")
from app.core import workflow as WF
from app.core.db import get_conn, release_conn

# 模拟「两道闸门都失灵」的极端情况，单独验证引擎侧的环路保护
WF.find_cycle = lambda d: None

db = get_conn()
d = db.execute("SELECT id FROM workflow_defs WHERE code='WF_DIRTY'").fetchone()
release_conn()
did = d["id"] if d else 0
t0 = time.time()
err = ""
inst = None
try:
    inst = WF.start_instance(did, "脏数据", 1, {{}}, None, None)
except Exception as e:
    err = repr(e)
el = time.time() - t0
st = ""
if inst:
    db = get_conn()
    row = db.execute("SELECT status FROM workflow_instances WHERE id=?",
                     (inst["id"],)).fetchone()
    release_conn()
    st = row["status"] if row else ""
print("RESULT 引擎成环不挂死（有限时间内返回）|%d|%.2fs" % (1 if el < 10 else 0, el))
print("RESULT 成环实例被作废而非卡在 running|%d|status=%s %s"
      % (1 if st == "canceled" else 0, st, err))
print("RESULT 引擎异常不外泄到连接（仍可继续操作）|%d|%s"
      % (1 if (get_conn().execute("SELECT 1").fetchone() is not None) else 0, ""))
release_conn()
ab = get_conn()
n = ab.execute("SELECT COUNT(*) c FROM audit_logs WHERE action='cycle_abort'"
               ).fetchone()["c"]
release_conn()
print("RESULT 环路终止已写入审计留痕（可事后追查）|%d|cycle_abort=%d"
      % (1 if n >= 1 else 0, n))

# 故障隔离：引擎内部抛异常（且当时正开着事务），连接必须被收拾干净
orig = WF._advance
def boom(*a, **k):
    db = get_conn()
    db.execute("UPDATE workflow_instances SET title='boom' WHERE id>0")
    raise RuntimeError("模拟引擎内部崩溃")
WF._advance = boom
crashed = False
try:
    WF.start_instance(did, "炸弹", 1, {{}}, None, None)
except Exception as e:
    crashed = isinstance(e, RuntimeError)
WF._advance = orig
c = get_conn()
in_tx = bool(c.in_transaction)
readable = c.execute("SELECT COUNT(*) FROM workflow_instances").fetchone() is not None
release_conn()
print("RESULT 引擎崩溃后异常照常上抛（不吞错）|%d|" % (1 if crashed else 0))
print("RESULT 引擎崩溃后连接未卡在事务里（不会锁死全库）|%d|in_transaction=%s"
      % (0 if in_tx else 1, in_tx))
print("RESULT 引擎崩溃后连接仍可读（后续请求不受影响）|%d|" % (1 if readable else 0))

# 崩溃留下的脏事务必须真的回滚：标题不能被改成 boom
c = get_conn()
hit = c.execute("SELECT COUNT(*) c FROM workflow_instances WHERE title='boom'").fetchone()["c"]
release_conn()
print("RESULT 崩溃时未提交的写入已回滚|%d|残留=%d" % (1 if hit == 0 else 0, hit))
'''.format(base=BASE))
        p = subprocess.run([PY, runner], cwd=BASE, env=DEV_ENV,
                           capture_output=True, text=True, timeout=120)
        for line in (p.stdout or "").splitlines():
            if line.startswith("RESULT "):
                try:
                    name, ok, extra = line[len("RESULT "):].split("|", 2)
                    check(name, ok == "1", extra)
                except ValueError:
                    pass
        if p.returncode != 0:
            print("        probe stderr: " + (p.stderr or "")[-800:])
        try:
            os.remove(runner)
        except OSError:
            pass

        # ==================================================================
        # 用例 L：回调补偿（幂等重试）
        # ==================================================================
        st, lvL = call("POST", "/api/workflow/leave",
                       {"leave_type": "personal", "days": 1}, token=a2tk)
        iidL = lvL["instance_id"]
        st, mtsL = call("GET", "/api/workflow/my-tasks", token=mtk)
        tL = next(t for t in mtsL if t["instance_id"] == iidL)
        call("POST", f"/api/workflow/tasks/{tL['id']}/approve",
             {"comment": "准"}, token=mtk)
        st, rr = call("POST", f"/api/workflow/instances/{iidL}/retry-callback",
                      {}, token=atk)
        check("回调重试接口可用（admin）", st == 200, f"{st} {rr}")
        st, leavesL = call("GET", "/api/workflow/leave", token=a2tk)
        check("重试后请假单状态不变（回调幂等）", st == 200 and any(
            l["id"] == lvL["leave_id"] and l["status"] == "approved"
            for l in leavesL))
        st, cf = call("GET", "/api/workflow/callback-failures", token=atk)
        check("回调失败清单可读", st == 200 and isinstance(cf, list), f"{st}")
        st, _ = call("POST", f"/api/workflow/instances/{iidL}/retry-callback",
                     {}, token=a2tk)
        check("无 workflow.manage 者不能重试回调（403）", st == 403, f"status={st}")
        st, _ = call("GET", "/api/workflow/callback-failures", token=a2tk)
        check("无 workflow.manage 者不能看回调失败清单（403）", st == 403,
              f"status={st}")

        # ==================================================================
        # 用例 M：权限复核 —— sales_manager 不再持有 workflow.manage
        # ==================================================================
        st, rsm = new_user(atk, "wf_sm", "sales_manager")
        smtk = login("wf_sm") if st == 200 else None
        if smtk:
            st, _ = call("POST", "/api/workflow/defs",
                         {"code": "WF_SM", "name": "x",
                          "definition": def_c}, token=smtk)
            check("sales_manager 无 workflow.manage（建定义 403）", st == 403,
                  f"status={st}")
        st, rhr = call("POST", "/api/workflow/defs",
                       {"code": "WF_HR", "name": "人事可用",
                        "definition": def_c}, token=htk)
        check("hr 仍有 workflow.manage（建定义 200）", st == 200, f"status={st}")

        # 清理脏数据：停用而不是物理删除（本库物理删除与线上口径一致地禁用）
        con = sqlite3.connect(DEV_DB)
        con.execute("UPDATE workflow_defs SET is_active=0 WHERE code='WF_DIRTY'")
        con.commit()
        con.close()

        print(f"\n[wf] 共 {PASSED} 项通过" +
              (f"，{len(FAILED)} 项失败：" + "、".join(FAILED) if FAILED
               else "，全部通过"))
        return 1 if FAILED else 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        print("[wf] 服务已关停")


if __name__ == "__main__":
    sys.exit(main())
