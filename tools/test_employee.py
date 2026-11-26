"""M003 员工档案与异动接口自测 —— 真实起服务 + 登录 + 调接口 + 断言业务数据。

冒烟只校验「路由在不在」，本脚本校验「数据对不对」：
- 枚举接口（前端不硬编码中文的前提）：异动类型 6 项、生命周期状态 5 项
- 种子：给 M002 的 demo_staff 自动挂档案 + 入职异动
- 花名册：含档案标记、当前主岗、所属组织（敏感字段不下发）、按组织筛选
- 档案详情脱敏：无 sensitive 权限时证件号/社保号/紧急电话只给掩码，扫描件路径不外发
- 档案 CRUD（新建 / 更新 / 注销软删）
- 异动登记联动任职：调岗 → 新主岗、旧岗结束；离职 → 结束所有在职任职
- 权限：无 employee.edit 的角色写操作被拒（403）

用法：
    python tools/test_employee.py
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
PORT = 8794
URL = f"http://127.0.0.1:{PORT}"

# 隔离库：自测一律用独立临时库，绝不污染客户真实库 data/wv.db。
# database.DB_PATH 读 WV_DB 环境变量；这里指向专属测试库并在每次运行前删除，
# 保证从干净种子起步（种子会重新给 demo_staff 挂档案与入职异动）。
DEV_DB = os.path.join(BASE, ".qa_tmp", "wv_test_employee.db")
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
    """返回 (status_code, body)。"""
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
    print("[employee] 启动服务 ...")
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
            print("[employee] FAIL 服务未就绪")
            print((proc.stdout.read() if proc.stdout else "")[-3000:])
            return 1

        st, r = call("POST", "/api/auth/login",
                     {"username": "admin", "password": "admin.123"})
        check("管理员登录", st == 200 and bool(r and r.get("token")), f"status={st}")
        if st != 200:
            return 1
        tk = r["token"]

        # ================= 1. 枚举 =================
        st, t = call("GET", "/api/employee/types", token=tk)
        check("异动类型枚举 6 项（入职/转正/调岗/晋升/离职/退休）",
              st == 200 and len(t["event_types"]) == 6,
              str([x["value"] for x in t["event_types"]]))
        check("生命周期状态枚举 5 项",
              st == 200 and len(t["statuses"]) == 5,
              str([x["value"] for x in t["statuses"]]))

        # ================= 2. 种子：花名册含档案 =================
        st, rows = call("GET", "/api/employee/list", token=tk)
        check("花名册非空", st == 200 and len(rows) > 0, str(len(rows)))
        with_profile = [x for x in rows if x["has_profile"]]
        check("种子已为 demo_staff 挂档案（≥10 人有档案）",
              len(with_profile) >= 10, str(len(with_profile)))
        check("花名册不含敏感字段（id_card/bank_account）",
              all("id_card" not in x and "bank_account" not in x for x in rows))
        demo = next((x for x in with_profile if x["username"].startswith("demo_staff_")), None)
        check("种子含 demo_staff 人员", bool(demo), str(demo and demo["username"]))
        check("demo_staff 带当前主岗与所属组织",
              bool(demo and demo.get("position_name")) and bool(demo.get("org_unit_name")),
              str(demo and (demo.get("position_name"), demo.get("org_unit_name"))))

        # org_unit_id 筛选：选中的组织 + 其全部下级（此参数此前声明了却没进 SQL）
        st, units = call("GET", "/api/structure/units?active=1", token=tk)
        target_org = demo["org_unit_id"]
        kids: dict = {}
        for o in (units or []):
            kids.setdefault(o.get("parent_id"), []).append(o["id"])
        keep, stack = {target_org}, [target_org]
        while stack:
            cur = stack.pop()
            for c in kids.get(cur, []):
                if c not in keep:
                    keep.add(c)
                    stack.append(c)
        st, frows = call("GET", f"/api/employee/list?org_unit_id={target_org}",
                         token=tk)
        check("花名册按组织筛选可用（结果非空且是真子集）",
              st == 200 and len(frows) > 0 and len(frows) < len(rows),
              f"{len(frows)}/{len(rows)} org={target_org}")
        check("筛选结果全部属于该组织子树",
              st == 200 and all(x["org_unit_id"] in keep for x in frows),
              str(sorted({x["org_unit_id"] for x in frows})))
        check("筛选结果含该组织的 demo_staff",
              st == 200 and any(x["id"] == demo["id"] for x in frows))
        st, frows2 = call("GET", "/api/employee/list?org_unit_id=999999", token=tk)
        check("筛选不存在的组织被拒 400（不会悄悄穿透成全公司）", st == 400,
              f"{st} {str((frows2 or {}).get('detail', frows2))[:120]}")

        # ================= 3. 档案详情脱敏 =================
        uid = demo["id"]
        st, prof = call("GET", f"/api/employee/profiles?user_id={uid}", token=tk)
        check("档案详情可读（admin 含敏感字段）",
              st == 200 and bool(prof.get("id")) and "id_card" in prof
              and "bank_account" in prof, str(prof and prof.get("id")))
        check("档案含合同与入职日期",
              bool(prof and prof.get("contract_no")) and bool(prof.get("hire_date")),
              str(prof and (prof.get("contract_no"), prof.get("hire_date"))))

        # ================= 4. 异动流水（种子入职事件）=================
        st, evs = call("GET", f"/api/employee/events?user_id={uid}", token=tk)
        check("异动流水含种子入职事件", st == 200 and len(evs) >= 1
              and any(e["event_type"] == "onboard" for e in evs), str(len(evs)))

        # ================= 5. 档案 CRUD =================
        # 找一个还没档案的用户（admin 自身通常无档案），给它建一份
        admin_row = next((x for x in rows if x["username"] == "admin"), None)
        aid = admin_row["id"]
        st, rp = call("POST", "/api/employee/profiles", {
            "user_id": aid, "id_card": "440100199001010011",
            "contract_no": "HT-TEST-001", "bank_account": "6214000000000001",
            "emergency_contact": "测试家属", "emergency_phone": "13900000001",
            "hire_date": "2026-01-01"}, token=tk)
        check("新建档案（含敏感字段）", st == 200 and bool(rp.get("id")), str(rp)[:120])
        pid = rp["id"]
        st, prof2 = call("GET", f"/api/employee/profiles?user_id={aid}", token=tk)
        check("新建档案身份证/银行卡已落库",
              st == 200 and prof2["id_card"] == "440100199001010011"
              and prof2["bank_account"] == "6214000000000001", str(prof2 and prof2.get("id_card")))
        st, _ = call("PUT", f"/api/employee/profiles/{pid}", {
            "user_id": aid, "contract_no": "HT-TEST-002",
            "id_card": "440100199001010011", "bank_account": "6214000000000001",
            "emergency_contact": "测试家属", "emergency_phone": "13900000001",
            "hire_date": "2026-01-01"}, token=tk)
        st, prof3 = call("GET", f"/api/employee/profiles?user_id={aid}", token=tk)
        check("更新档案合同号生效", st == 200 and prof3["contract_no"] == "HT-TEST-002",
              str(prof3 and prof3.get("contract_no")))
        st, _ = call("DELETE", f"/api/employee/profiles/{pid}", token=tk)
        check("注销档案（软删）", st == 200, f"status={st}")
        st, prof4 = call("GET", f"/api/employee/profiles?user_id={aid}", token=tk)
        check("注销后档案不可查（404）", st == 404, f"status={st}")

        # ================= 6. 异动登记联动任职 =================
        # 取一个 demo_staff，调到另一个岗位，验证任职关系联动
        st, positions = call("GET", "/api/position/positions?active=1", token=tk)
        check("可拉取岗位列表用于异动目标", st == 200 and len(positions) > 1,
              str(len(positions)))
        # 该 demo 的当前主岗
        old_pid = demo["position_id"]
        new_pid = next((p["id"] for p in positions
                        if p["id"] != old_pid), positions[0]["id"])
        st, re = call("POST", "/api/employee/events", {
            "user_id": uid, "event_type": "transfer",
            "to_position_id": new_pid, "event_date": "2026-06-01",
            "remark": "自测调岗"}, token=tk)
        check("登记调岗异动", st == 200 and bool(re.get("id")), str(re)[:120])
        st, asg = call("GET", f"/api/position/assignments?user_id={uid}&active=1",
                       token=tk)
        check("调岗后新岗为主岗", st == 200 and any(
            a["position_id"] == new_pid and a["is_primary"] == 1 for a in asg),
            json.dumps([(a["position_id"], a["is_primary"]) for a in asg]))
        check("调岗后旧主岗已结束（不再 active）",
              not any(a["position_id"] == old_pid and a["is_active"] == 1
                      for a in asg), str(old_pid))

        # 离职：结束所有在职任职
        st, rl = call("POST", "/api/employee/events", {
            "user_id": uid, "event_type": "leave",
            "event_date": "2026-07-01", "remark": "自测离职"}, token=tk)
        check("登记离职异动", st == 200 and bool(rl.get("id")), str(rl)[:120])
        st, asg2 = call("GET", f"/api/position/assignments?user_id={uid}&active=1",
                        token=tk)
        check("离职后无任何在职任职", st == 200 and len(asg2) == 0, str(len(asg2)))

        # 转正：仅留痕，不报错
        st, rz = call("POST", "/api/employee/events", {
            "user_id": uid, "event_type": "regular",
            "event_date": "2026-08-01", "remark": "自测转正"}, token=tk)
        check("登记转正异动（仅留痕）", st == 200 and bool(rz.get("id")), str(rz)[:120])

        # 未知异动类型被拒
        st, _ = call("POST", "/api/employee/events", {
            "user_id": uid, "event_type": "nope"}, token=tk)
        check("未知异动类型被拒 400", st == 400, f"status={st}")

        # ================= 7. 权限 =================
        ts = "emp_sales_" + str(int(time.time()) % 100000)
        st, ru = call("POST", "/api/org/users", {
            "username": ts, "password": "Test@2026", "full_name": "自测业务员",
            "role": "sales"}, token=tk)
        check("创建 sales 测试账号", st == 200 and bool(ru.get("id")), str(ru)[:120])
        if st != 200:
            return 1
        st, lg = call("POST", "/api/auth/login",
                      {"username": ts, "password": "Test@2026"})
        check("sales 账号登录", st == 200 and bool(lg.get("token")), f"status={st}")
        tk2 = lg["token"]

        st, sl = call("GET", "/api/employee/list", token=tk2)
        check("sales 有 employee.view（可读花名册 200）", st == 200, f"status={st}")
        st, sp = call("GET", f"/api/employee/profiles?user_id={uid}", token=tk2)
        check("sales 无 sensitive.view：证件号/社保号/紧急电话只给掩码",
              st == 200 and all(
                  isinstance(sp.get(k), str) and sp[k] != prof.get(k)
                  and "*" in sp[k]
                  for k in ("id_card", "bank_account", "social_no",
                            "emergency_phone")),
              json.dumps({k: sp.get(k) for k in ("id_card", "bank_account",
                                                 "social_no", "emergency_phone")},
                         ensure_ascii=False))
        check("sales 无 sensitive.view：证件扫描件路径不外发",
              st == 200 and "id_scan_path" not in sp, str(sorted(sp.keys())))
        check("sales 仍可读非敏感档案（紧急联系人姓名/合同号）",
              st == 200 and bool(sp.get("emergency_contact"))
              and bool(sp.get("contract_no")),
              str(sp.get("emergency_contact")))
        st, _ = call("POST", "/api/employee/profiles", {
            "user_id": uid, "contract_no": "X"}, token=tk2)
        check("sales 无 employee.edit：新建档案被拒 403", st == 403, f"status={st}")
        st, _ = call("POST", "/api/employee/events", {
            "user_id": uid, "event_type": "transfer",
            "to_position_id": new_pid}, token=tk2)
        check("sales 不能登记异动（403）", st == 403, f"status={st}")

        # ================= 8. 档案保存：无 sensitive 权限者提交的敏感字段不落库 ====
        # gm 有 employee.edit 但没有 employee.sensitive.view —— 它看到的身份证是
        # 掩码，若表单直接提交，掩码会被写成真值，必须拦在后端。
        tgm = "emp_gm_" + str(int(time.time()) % 100000)
        st, rug = call("POST", "/api/org/users", {
            "username": tgm, "password": "Test@2026", "full_name": "自测总经理",
            "role": "gm"}, token=tk)
        gmtk = None
        if st == 200:
            st2, lg2 = call("POST", "/api/auth/login",
                            {"username": tgm, "password": "Test@2026"})
            gmtk = lg2.get("token") if st2 == 200 else None
        check("创建 gm 测试账号（有 employee.edit、无 sensitive）",
              gmtk is not None, str(rug)[:120])
        if gmtk:
            st, _ = call("POST", "/api/employee/profiles", {
                "user_id": uid, "id_card": "110100199001010099",
                "social_no": "SOCIAL-HACK", "emergency_phone": "13700000000",
                "contract_no": "HT-GM-001"}, token=gmtk)
            check("gm 可保存非敏感档案字段", st == 200, f"status={st}")
            st, pg = call("GET", f"/api/employee/profiles?user_id={uid}", token=tk)
            check("gm 提交的身份证/社保号/紧急电话未落库（原值保留）",
                  st == 200 and pg["id_card"] != "110100199001010099"
                  and pg["social_no"] != "SOCIAL-HACK"
                  and pg["emergency_phone"] != "13700000000",
                  str((pg.get("id_card"), pg.get("social_no"),
                       pg.get("emergency_phone"))))
            check("gm 提交的合同号已生效（非敏感字段照常可改）",
                  st == 200 and pg["contract_no"] == "HT-GM-001",
                  str(pg.get("contract_no")))
            call("DELETE", f"/api/org/users/{rug['id']}", token=tk)

        # 清理：停用测试账号
        st, _ = call("DELETE", f"/api/org/users/{ru['id']}", token=tk)
        check("测试账号已停用（软删）", st == 200, f"status={st}")

        print(f"\n[employee] 共 {PASSED} 项通过"
              + (f"，{len(FAILED)} 项失败：" + "、".join(FAILED) if FAILED
                 else "，全部通过"))
        return 1 if FAILED else 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        print("[employee] 服务已关停")


if __name__ == "__main__":
    sys.exit(main())
