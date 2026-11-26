"""M071 电子报销自测 —— 真实起服务 + 多角色登录 + 跑通查重/审批/打印链路。

覆盖（按 DoD 要求）：
- 同一单据内重复票据被硬拦截
- 跨单据重复被标识 suspected，并给出 dup_of 来源（单号/报销人/日期/金额）
- confirmed_ok 后从待核实清单消失（不再标红）
- 软删票据不参与查重匹配
- 三个金额档触发不同审批分支（<1000 / ≥1000 / ≥10000）
- 审批结束后报销单状态被回调正确回写（最容易静默失效的一环）
- 打印权限（本人/财务）与打印留痕
- 越权访问被拒

用法：python tools/test_expense.py
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
PORT = 8797
URL = f"http://127.0.0.1:{PORT}"

DEV_DB = os.path.join(BASE, ".qa_tmp", "wv_test_expense.db")
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
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read().decode("utf-8")
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw
    except Exception as e:
        print(f"  !!EXC {type(e).__name__}: {e}")
        return None, None


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


# ============================ 业务动作封装 ============================
def new_report(tok):
    return call("POST", "/api/expense/reports", {}, token=tok)


def add_item(tok, rid, **kw):
    body = {"category": kw.get("category", "travel"),
            "occur_date": kw.get("occur_date", "2026-10-09"),
            "amount": kw.get("amount", 100),
            "ticket_type": kw.get("ticket_type", "none"),
            "invoice_code": kw.get("invoice_code"),
            "invoice_no": kw.get("invoice_no"),
            "trans_no": kw.get("trans_no"),
            "merchant": kw.get("merchant", "演示商家")}
    return call("POST", f"/api/expense/reports/{rid}/items", body, token=tok)


def submit(tok, rid):
    return call("POST", f"/api/expense/reports/{rid}/submit", {}, token=tok)


def approve_instance(iid, tok, comment="同意"):
    """用指定用户批准该实例的待办任务，返回 (找到并执行?, status)。"""
    st, mts = call("GET", "/api/workflow/my-tasks", token=tok)
    if st != 200:
        return False, st
    t = next((x for x in mts if x["instance_id"] == iid), None)
    if not t:
        return False, st
    st2, _ = call("POST", f"/api/workflow/tasks/{t['id']}/approve",
                  {"comment": comment}, token=tok)
    return True, st2


def main():
    print("[exp] 启动服务 ...")
    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "app.main:app", "--port", str(PORT),
         "--log-level", "warning"],
        cwd=BASE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP, env=DEV_ENV)
    try:
        if not wait_up():
            print("[exp] FAIL 服务未就绪")
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
        # viewer 的作用：scope=all 故看得见单据，但无 expense.finance 故不可打印 ——
        # 用它才能测到真正的 403（不可见的人拿到的是 404，因为不泄露单据存在性）
        roles = {"exp_ap": "sales", "exp_ap2": "sales", "exp_mgr": "manager",
                 "exp_fin": "finance", "exp_gm": "gm", "exp_viewer": "viewer"}
        tk = {}
        for uname, role in roles.items():
            st, r = call("POST", "/api/org/users",
                         {"username": uname, "password": "Test@2026",
                          "full_name": uname, "role": role}, token=atk)
            check(f"创建 {uname}({role})", st == 200 and bool(r.get("id")), str(r)[:70])
            if st != 200:
                return 1
        for uname in roles:
            st, r = call("POST", "/api/auth/login",
                         {"username": uname, "password": "Test@2026"})
            tk[uname] = r["token"] if st == 200 else None
        ap1, ap2 = tk["exp_ap"], tk["exp_ap2"]
        mgr, fin, gm = tk["exp_mgr"], tk["exp_fin"], tk["exp_gm"]
        viewer = tk["exp_viewer"]

        # ---- 枚举接口 ----
        st, ty = call("GET", "/api/expense/types", token=ap1)
        check("枚举接口可用（费用/票据/状态/重复/档位）", st == 200 and all(
            len(ty.get(k, [])) > 0 for k in
            ("categories", "ticket_types", "statuses", "dup_statuses",
             "approval_tiers")), f"status={st}")
        st, _ = call("GET", "/api/expense/types", token=ap1)

        # =============== A. 单据内重复 → 硬拦截 ===============
        st, r1 = new_report(ap1)
        R1 = r1["id"]
        st, i1 = add_item(ap1, R1, category="travel", amount=100,
                          ticket_type="vat_normal",
                          invoice_code="012345678901", invoice_no="00000001")
        check("A1 首张增值税票保存成功", st == 200 and i1["dup_status"] == "none",
              f"status={st}")
        st, i2 = add_item(ap1, R1, category="office", amount=100,
                          ticket_type="vat_normal",
                          invoice_code="012345678901", invoice_no="00000001")
        check("A2 同单内同票号 → 400 硬拦截", st == 400, f"status={st}")
        st, rd = call("GET", f"/api/expense/reports/{R1}", token=ap1)
        check("A3 总额只算一张（100）",
              st == 200 and abs(rd["total_amount"] - 100) < 0.001,
              str(rd.get("total_amount")))

        # =============== B. 跨单据重复 → 标识 suspected ===============
        st, r2 = new_report(ap2)
        R2 = r2["id"]
        st, j1 = add_item(ap2, R2, category="travel", amount=100,
                          ticket_type="vat_normal",
                          invoice_code="012345678901", invoice_no="00000001")
        check("B1 跨单同票 → suspected",
              st == 200 and j1["dup_status"] == "suspected", f"status={st}")
        info = j1.get("dup_of_info") or {}
        check("B2 dup_of 给出来源单号", info.get("report_code") == r1["code"],
              str(info.get("report_code")))
        check("B3 dup_of 给出来源报销人", info.get("applicant_name") == "exp_ap",
              str(info.get("applicant_name")))
        check("B4 dup_of 给出来源金额",
              abs((info.get("amount") or 0) - 100) < 0.001, str(info.get("amount")))
        check("B5 dup_of 给出来源日期", info.get("occur_date") == "2026-10-09",
              str(info.get("occur_date")))

        # =============== C. 指纹归一化（空格/大小写不漏检） ===============
        st, r3 = new_report(ap1)
        R3 = r3["id"]
        st, k1 = add_item(ap1, R3, category="travel", amount=100,
                          ticket_type="vat_normal",
                          invoice_code=" 0123 4567 8901 ", invoice_no="00000001")
        check("C1 发票号带空格仍能命中同一张票（归一化生效）",
              st == 200 and k1["dup_status"] == "suspected", f"status={st}")

        # =============== D. 水单：交易单号维度 ===============
        st, r4 = new_report(ap1)
        R4 = r4["id"]
        st, w1 = add_item(ap1, R4, category="meal", amount=50,
                          ticket_type="wechat", trans_no="TRN0001")
        check("D1 水单首条保存成功", st == 200 and w1["dup_status"] == "none",
              f"status={st}")
        st, w2 = add_item(ap1, R4, category="meal", amount=50,
                          ticket_type="wechat", trans_no="TRN0001")
        check("D2 同单内同交易号 → 400 拦截", st == 400, f"status={st}")
        st, r5 = new_report(ap2)
        R5 = r5["id"]
        st, w3 = add_item(ap2, R5, category="meal", amount=50,
                          ticket_type="wechat", trans_no="TRN0001")
        check("D3 水单跨单重复 → suspected",
              st == 200 and w3["dup_status"] == "suspected", f"status={st}")

        # =============== E. 财务复核台 + confirmed_ok 销案 ===============
        st, dups = call("GET", "/api/expense/dups?status=suspected", token=fin)
        target = next((d for d in dups if d["id"] == j1["id"]), None)
        check("E1 财务复核台列出疑似重复", st == 200 and target is not None,
              f"status={st}")
        st, _ = call("POST", f"/api/expense/items/{j1['id']}/dup",
                     {"action": "confirm", "note": "两人同行各报一半"}, token=fin)
        check("E2 财务标记 confirmed_ok → 200", st == 200, f"status={st}")
        st, dups2 = call("GET", "/api/expense/dups?status=suspected", token=fin)
        check("E3 确认后从待核实清单消失（不再标红）",
              st == 200 and not any(d["id"] == j1["id"] for d in dups2), f"status={st}")
        st, dups3 = call("GET", "/api/expense/dups?status=confirmed_ok", token=fin)
        check("E4 出现在已核实清单（便于回溯）",
              st == 200 and any(d["id"] == j1["id"] for d in dups3), f"status={st}")

        # =============== F. 软删票据不参与查重 ===============
        st, r6 = new_report(ap1)
        R6 = r6["id"]
        st, m1 = add_item(ap1, R6, category="office", amount=20,
                          ticket_type="vat_special",
                          invoice_code="999999999999", invoice_no="88888888")
        check("F1 明细保存成功", st == 200, f"status={st}")
        st, _ = call("DELETE", f"/api/expense/items/{m1['id']}", token=ap1)
        check("F2 软删明细 → 200", st == 200, f"status={st}")
        st, m2 = add_item(ap1, R6, category="office", amount=20,
                          ticket_type="vat_special",
                          invoice_code="999999999999", invoice_no="88888888")
        check("F3 软删后同票可重新录入（未被自己拦住）", st == 200, f"status={st}")
        check("F4 重新录入后不标疑似（软删项不参与匹配）",
              st == 200 and m2["dup_status"] == "none", str(m2.get("dup_status")))

        # =============== G. 金额分档审批 ===============
        # G-1 小额 <1000：主管一审直达
        st, r7 = new_report(ap1)
        R7 = r7["id"]
        add_item(ap1, R7, category="transport", amount=800, ticket_type="taxi")
        st, s7 = submit(ap1, R7)
        check("G1 小额单提交 → 200", st == 200 and bool(s7.get("instance_id")),
              f"status={st}")
        iid7 = s7["instance_id"]
        ok7, st7 = approve_instance(iid7, mgr)
        check("G2 小额：主管批准", ok7 and st7 == 200, f"status={st7}")
        st, rep7 = call("GET", f"/api/expense/reports/{R7}", token=ap1)
        check("G3 小额：审批回调回写 status=approved",
              st == 200 and rep7["status"] == "approved", str(rep7.get("status")))

        # G-2 中额 ≥1000：主管 → 财务
        st, r8 = new_report(ap1)
        R8 = r8["id"]
        add_item(ap1, R8, category="hotel", amount=5000, ticket_type="vat_normal",
                 invoice_code="111111111111", invoice_no="20000001")
        st, s8 = submit(ap1, R8)
        iid8 = s8["instance_id"]
        ok8a, st8a = approve_instance(iid8, mgr)
        check("G4 中额：主管批准", ok8a and st8a == 200, f"status={st8a}")
        st, ins8 = call("GET", f"/api/workflow/instances/{iid8}", token=atk)
        check("G5 中额：已进入财务复核节点",
              st == 200 and ins8["status"] == "running"
              and ins8["current_node"] == "fin", str(ins8.get("current_node")))
        ok8b, st8b = approve_instance(iid8, fin)
        check("G6 中额：财务批准", ok8b and st8b == 200, f"status={st8b}")
        st, rep8 = call("GET", f"/api/expense/reports/{R8}", token=ap1)
        check("G7 中额：回调回写 approved",
              st == 200 and rep8["status"] == "approved", str(rep8.get("status")))

        # G-3 大额 ≥10000：主管 → 财务 → 总经理
        st, r9 = new_report(ap1)
        R9 = r9["id"]
        add_item(ap1, R9, category="travel", amount=15000, ticket_type="flight",
                 merchant="航司")
        st, s9 = submit(ap1, R9)
        iid9 = s9["instance_id"]
        ok9a, st9a = approve_instance(iid9, mgr)
        check("G8 大额：主管批准", ok9a and st9a == 200, f"status={st9a}")
        ok9b, st9b = approve_instance(iid9, fin)
        check("G9 大额：财务批准", ok9b and st9b == 200, f"status={st9b}")
        st, ins9 = call("GET", f"/api/workflow/instances/{iid9}", token=atk)
        check("G10 大额：已进入总经理批准节点",
              st == 200 and ins9["status"] == "running"
              and ins9["current_node"] == "gm", str(ins9.get("current_node")))
        ok9c, st9c = approve_instance(iid9, gm)
        check("G11 大额：总经理批准", ok9c and st9c == 200, f"status={st9c}")
        st, rep9 = call("GET", f"/api/expense/reports/{R9}", token=ap1)
        check("G12 大额：回调回写 approved",
              st == 200 and rep9["status"] == "approved", str(rep9.get("status")))

        # =============== H. 打印权限 + 留痕 ===============
        st, pd = call("GET", f"/api/expense/reports/{R9}/print-data", token=ap1)
        check("H1 本人可获取打印数据（含金额大写）",
              st == 200 and bool(pd.get("amount_upper")), f"status={st}")
        check("H2 打印数据带公司抬头与签名所需字段",
              st == 200 and bool((pd.get("company") or {}).get("name_cn"))
              and len(pd.get("items") or []) > 0)
        st, pr1 = call("POST", f"/api/expense/reports/{R9}/print", {}, token=ap1)
        check("H3 打印留痕 print_count=1",
              st == 200 and pr1.get("print_count") == 1, str(pr1.get("print_count")))
        st, pd2 = call("GET", f"/api/expense/reports/{R9}/print-data", token=fin)
        check("H4 财务可打印他人单据", st == 200, f"status={st}")
        st, _ = call("POST", f"/api/expense/reports/{R9}/print", {}, token=viewer)
        check("H5 可见但无财务权（viewer）打印 → 403", st == 403, f"status={st}")
        st, _ = call("POST", f"/api/expense/reports/{R9}/print", {}, token=ap2)
        check("H6 不可见的他人打印 → 404（不泄露单据存在性）", st == 404,
              f"status={st}")

        # =============== I. 越权 ===============
        st, _ = call("GET", f"/api/expense/reports/{R7}", token=ap2)
        check("I1 他人报销单不可见 → 404", st == 404, f"status={st}")
        st, _ = call("GET", "/api/expense/dups?status=suspected", token=ap1)
        check("I2 非财务访问复核台 → 403", st == 403, f"status={st}")
        st, _ = call("POST", f"/api/expense/items/{k1['id']}/dup",
                     {"action": "confirm"}, token=ap1)
        check("I3 非财务标记重复 → 403", st == 403, f"status={st}")
        st, _ = call("POST", f"/api/expense/reports/{R7}/pay", {}, token=ap1)
        check("I4 无 expense.pay 登记支付 → 403", st == 403, f"status={st}")

        # =============== J. 支付登记（财务） ===============
        st, _ = call("POST", f"/api/expense/reports/{R7}/pay", {}, token=fin)
        st, rep7b = call("GET", f"/api/expense/reports/{R7}", token=ap1)
        check("J1 财务登记支付 → status=paid",
              st == 200 and rep7b["status"] == "paid", str(rep7b.get("status")))

        print(f"\n[exp] 共 {PASSED} 项通过" +
              (f"，{len(FAILED)} 项失败：" + "、".join(FAILED) if FAILED
               else "，全部通过"))
        return 1 if FAILED else 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        print("[exp] 服务已关停")


if __name__ == "__main__":
    sys.exit(main())
