"""M002 岗位职级接口自测 —— 真实起服务 + 登录 + 调接口 + 断言业务数据。

冒烟只校验「路由在不在」，本脚本校验「数据对不对」：
- 种子：职级四序列 / 岗位 / 编制落库，缺编·满编·超编 三种状态都存在
- 枚举接口（前端不硬编码中文的前提）
- job_titles / job_grades / positions 全套 CRUD
- 列表筛选（组织 / 岗位 / 序列 / 状态 / 差额 / 含下级）
- 编制 vs 在岗看板聚合口径（编制 − 在岗 = 差额；含下级时逐层归集）
- 任职登记（主岗唯一、重复任职拒绝、结束任职留痕）
- 权限：无 position.edit 的角色写操作被拒（403）
- 软删与引用校验（岗位/职级被引用、编制有下级汇报线 / 在职人员时拒绝停用）

用法：
    python tools/test_position.py
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import os

BASE = r"E:/workbuddy/felix-quote/src"
PY = BASE + r"/runtime/python/python.exe"
PORT = 8793
URL = f"http://127.0.0.1:{PORT}"

# 隔离库：自测一律用独立临时库，绝不污染客户真实库 data/wv.db。
# database.DB_PATH 读 WV_DB 环境变量；这里指向专属测试库并在每次运行前删除，
# 保证从干净种子起步（本脚本内的 CRUD 只做软删，残留不影响 active 口径的断言）。
DEV_DB = os.path.join(BASE, ".qa_tmp", "wv_test_position.db")
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
    """返回 (status_code, body)。断言 HTTP 码时用第一个返回值。"""
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
    print("[position] 启动服务 ...")
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
            print("[position] FAIL 服务未就绪")
            print((proc.stdout.read() if proc.stdout else "")[-3000:])
            return 1

        st, r = call("POST", "/api/auth/login",
                     {"username": "admin", "password": "admin.123"})
        check("管理员登录", st == 200 and bool(r and r.get("token")), f"status={st}")
        if st != 200:
            return 1
        tk = r["token"]

        # ================= 1. 枚举 =================
        st, t = call("GET", "/api/position/types", token=tk)
        fams = {x["value"]: x["label"] for x in t["families"]}
        check("职级序列枚举 4 项（M/P/T/O）",
              st == 200 and set(fams) == set("MPTO"), str(fams))
        check("编制状态枚举 4 项", len(t["statuses"]) == 4,
              str([x["value"] for x in t["statuses"]]))
        check("看板维度枚举 4 项", len(t["board_dims"]) == 4,
              str([x["value"] for x in t["board_dims"]]))
        check("序列层级上限 M=6 / O=5",
              t["family_max_level"]["M"] == 6 and t["family_max_level"]["O"] == 5,
              json.dumps(t["family_max_level"]))

        # ================= 2. 种子：职级与岗位 =================
        st, grades = call("GET", "/api/position/grades?active=1", token=tk)
        by_fam = {}
        for g in grades:
            by_fam.setdefault(g["family"], []).append(g["code"])
        check("职级 27 级：M6 + P8 + T8 + O5",
              len(grades) == 27 and len(by_fam.get("M", [])) == 6
              and len(by_fam.get("P", [])) == 8
              and len(by_fam.get("T", [])) == 8
              and len(by_fam.get("O", [])) == 5,
              f"total={len(grades)} {({k: len(v) for k, v in by_fam.items()})}")
        m3 = next((g for g in grades if g["code"] == "M3"), None)
        check("M3 = 经理级，薪资带宽 12000–20000 CNY",
              bool(m3) and m3["name"] == "经理级" and m3["salary_min"] == 12000
              and m3["salary_max"] == 20000 and m3["currency"] == "CNY",
              str(m3))

        st, titles = call("GET", "/api/position/titles?active=1", token=tk)
        codes = [x["code"] for x in titles]
        check("岗位 ≥ 15 个", len(titles) >= 15, str(len(titles)))
        check("关键岗位齐全（董事长/总经理/厂长/研发工程师/操作工）",
              all(c in codes for c in ("JT-CHAIRMAN", "JT-GM", "JT-FACTORY-HEAD",
                                       "JT-RND-ENG", "JT-OPERATOR")), str(codes[:6]))
        check("岗位均带默认序列与类别",
              all(x.get("family") in "MPTO" and x.get("category") for x in titles))

        # ================= 3. 种子：编制与三种差额状态 =================
        st, poss = call("GET", "/api/position/positions?active=1", token=tk)
        check("编制数据 ≥ 30 条", st == 200 and len(poss) >= 30, str(len(poss)))
        gaps = {r["gap_status"] for r in poss}
        check("三种差额状态齐全（缺编 / 满编 / 超编）",
              gaps == {"short", "full", "over"}, str(gaps))
        over = [r for r in poss if r["gap_status"] == "over"]
        check("超编：装配车间质检员 编制 2 / 在岗 3",
              len(over) == 1 and over[0]["headcount"] == 2
              and over[0]["occupied"] == 3 and over[0]["job_title_code"] == "JT-QC",
              str([(r["name"], r["headcount"], r["occupied"]) for r in over]))
        full = [r for r in poss if r["gap_status"] == "full"]
        check("满编岗位 ≥ 2 个（编制 = 在岗）",
              len(full) >= 2 and all(r["headcount"] == r["occupied"] for r in full),
              str([(r["name"], r["headcount"], r["occupied"]) for r in full]))
        short = [r for r in poss if r["gap_status"] == "short"]
        check("缺编岗位 ≥ 10 个", len(short) >= 10, str(len(short)))

        one = next((r for r in poss if r["org_code"] == "GRP"
                    and r["job_title_code"] == "JT-CHAIRMAN"), None)
        check("编制自动命名（组织 · 岗位 · 职级）",
              bool(one) and "菲力克斯集团" in one["name"] and "董事长" in one["name"]
              and "M6" in one["name"], str(one["name"] if one else None))
        leader = next((r for r in poss if r["org_code"] == "TM01"
                       and r["job_title_code"] == "JT-LEADER"), None)
        check("汇报线已建立（钣金A班班组长 → 钣金车间主管）",
              bool(leader) and "钣金车间" in (leader.get("parent_position_name") or ""),
              str(leader.get("parent_position_name") if leader else None))

        # ================= 4. 统计卡 =================
        st, s = call("GET", "/api/position/stats", token=tk)
        check("统计：headcount − occupied = gap",
              abs(s["headcount"] - s["occupied"] - s["gap"]) < 1e-6,
              f"{s['headcount']} - {s['occupied']} = {s['gap']}")
        check("统计：缺编 − 超编 = 净差额",
              s["short"] > 0 and s["over"] > 0 and s["short"] - s["over"] == s["gap"],
              f"short={s['short']} over={s['over']} gap={s['gap']}")
        check("统计：在职总人数 ≥ 已落编人数",
              s["members"] > 1 and s["members"] >= s["assigned"],
              f"members={s['members']} assigned={s['assigned']} "
              f"unassigned={s['unassigned']}")
        check("统计：序列分布 4 项且合计 = 总编制",
              len(s["by_family"]) == 4
              and abs(sum(x["headcount"] for x in s["by_family"])
                      - s["headcount"]) < 1e-6,
              json.dumps(s["by_family"], ensure_ascii=False)[:220])

        # ================= 5. 编制 vs 在岗看板 =================
        st, b = call("GET", "/api/position/board?dim=org&scope=self", token=tk)
        check("看板：按组织聚合有数据", st == 200 and len(b["rows"]) > 0)
        check("看板：每行 编制 − 在岗 = 差额",
              all(abs(x["headcount"] - x["occupied"] - x["gap"]) < 1e-6
                  for x in b["rows"]))
        check("看板：行求和 = 合计（scope=self 时一条编制只归一层）",
              abs(sum(x["headcount"] for x in b["rows"])
                  - b["total"]["headcount"]) < 1e-6
              and sum(x["positions"] for x in b["rows"]) == b["total"]["positions"],
              json.dumps(b["total"]))
        check("看板：按缺口降序（缺编最多在前）",
              all(b["rows"][i]["gap"] >= b["rows"][i + 1]["gap"]
                  for i in range(len(b["rows"]) - 1)))

        st, units = call("GET", "/api/structure/units?org_type=factory&active=1",
                         token=tk)
        fid = units[0]["id"]
        st, bo = call("GET", f"/api/position/board?dim=org&scope=self"
                             f"&org_unit_id={fid}", token=tk)
        st, bs = call("GET", f"/api/position/board?dim=org&scope=subtree"
                             f"&org_unit_id={fid}", token=tk)
        check("看板：含下级编制 ≥ 仅本级",
              bs["total"]["headcount"] >= bo["total"]["headcount"] > 0,
              f"self={bo['total']['headcount']} subtree={bs['total']['headcount']}")
        check("看板：含下级时逐层归集（行数变多）",
              len(bs["rows"]) > len(bo["rows"]),
              f"{len(bo['rows'])} → {len(bs['rows'])}")
        in_subtree = sum(1 for r in poss if f"/{fid}/" in (r["org_path"] or ""))
        check("看板：含下级合计按编制去重（不被层级放大）",
              bs["total"]["positions"] == in_subtree
              and bo["total"]["positions"] < in_subtree,
              f"board={bs['total']['positions']} expect={in_subtree}")

        st, bf = call("GET", "/api/position/board?dim=family", token=tk)
        check("看板：按职级序列聚合 4 行",
              len(bf["rows"]) == 4
              and abs(sum(x["headcount"] for x in bf["rows"])
                      - bf["total"]["headcount"]) < 1e-6,
              json.dumps([(x["code"], x["headcount"], x["occupied"])
                          for x in bf["rows"]], ensure_ascii=False))
        st, bt = call("GET", "/api/position/board?dim=dept", token=tk)
        check("看板：按部门聚合（班组编制上归到车间，不单独成行）",
              len(bt["rows"]) > 0
              and not any("班" in (x["name"] or "") for x in bt["rows"]),
              json.dumps([x["name"] for x in bt["rows"]], ensure_ascii=False)[:220])
        st, _ = call("GET", "/api/position/board?dim=nope", token=tk)
        check("看板：非法维度被拒 400", st == 400, f"status={st}")

        # ================= 6. 列表筛选 =================
        st, r2 = call("GET", "/api/position/positions?job_family=O", token=tk)
        check("按职级序列筛选（操作序列）",
              st == 200 and len(r2) > 0 and all(x["job_family"] == "O" for x in r2),
              str(len(r2)))
        st, r3 = call("GET", "/api/position/positions?status=open", token=tk)
        check("按状态筛选（待补编制）",
              st == 200 and len(r3) > 0 and all(x["status"] == "open" for x in r3),
              str([x["code"] for x in r3]))
        st, r4 = call("GET", "/api/position/positions?gap_status=over", token=tk)
        check("按差额筛选（超编）",
              st == 200 and len(r4) == 1
              and all(x["gap_status"] == "over" for x in r4))
        tid = next(t2["id"] for t2 in titles if t2["code"] == "JT-RND-ENG")
        st, r5 = call("GET", f"/api/position/positions?job_title_id={tid}", token=tk)
        check("按岗位筛选（研发工程师）",
              st == 200 and len(r5) > 0
              and all(x["job_title_id"] == tid for x in r5), str(len(r5)))
        st, r6 = call("GET", f"/api/position/positions?org_unit_id={fid}&subtree=1",
                      token=tk)
        st, r7 = call("GET", f"/api/position/positions?org_unit_id={fid}&subtree=0",
                      token=tk)
        check("按组织筛选（含下级 > 仅本级）",
              len(r6) > len(r7) >= 1, f"subtree={len(r6)} self={len(r7)}")

        # ================= 7. 编制 CRUD =================
        ts = "T" + str(int(time.time()) % 100000)
        gid = next(g["id"] for g in grades if g["code"] == "T4")
        st, all_units = call("GET", "/api/structure/units?active=-1", token=tk)
        oid = next(o["id"] for o in all_units if o["code"] == "DPRD")
        st, rp = call("POST", "/api/position/positions", {
            "org_unit_id": oid, "job_title_id": tid, "job_grade_id": gid,
            "headcount": 3, "status": "open", "remark": "自测编制"}, token=tk)
        check("新增编制", st == 200 and bool(rp and rp.get("id")), str(rp)[:150])
        pid = rp["id"]
        check("新增编制自动编码 POS-DPRD-xxx",
              str(rp.get("code", "")).startswith("POS-DPRD-"), str(rp.get("code")))
        check("新增编制自动命名（组织 · 岗位 · 职级）",
              "研发部" in rp["name"] and "研发工程师" in rp["name"]
              and "T4" in rp["name"], rp["name"])

        st, one = call("GET", f"/api/position/positions/{pid}", token=tk)
        check("编制详情：默认缺编 3 人",
              one["headcount"] == 3 and one["occupied"] == 0
              and one["gap"] == 3 and one["gap_status"] == "short", str(one["gap"]))
        check("编制详情：组织面包屑非空", len(one["org_chain"]) > 0,
              str([o["name"] for o in one["org_chain"]]))

        st, _ = call("PUT", f"/api/position/positions/{pid}", {
            "org_unit_id": oid, "job_title_id": tid, "job_grade_id": gid,
            "headcount": 5, "status": "active", "remark": "自测编制-改"}, token=tk)
        st, one = call("GET", f"/api/position/positions/{pid}", token=tk)
        check("改编制：编制人数 3 → 5", st == 200 and one["headcount"] == 5,
              str(one["headcount"]))

        # 下级编制 + 汇报线环检测
        st, rc = call("POST", "/api/position/positions", {
            "org_unit_id": oid, "job_title_id": tid, "job_grade_id": gid,
            "headcount": 1, "parent_position_id": pid, "remark": "下级"}, token=tk)
        check("新增下级编制", st == 200 and bool(rc.get("id")), str(rc)[:120])
        kid = rc["id"]
        st, _ = call("PUT", f"/api/position/positions/{pid}", {
            "org_unit_id": oid, "job_title_id": tid, "job_grade_id": gid,
            "headcount": 5, "parent_position_id": kid}, token=tk)
        check("汇报线成环被拒 400", st == 400, f"status={st}")
        st, one = call("GET", f"/api/position/positions/{kid}", token=tk)
        check("下级编制详情含汇报链（能追到上级）",
              len(one["report_chain"]) == 1
              and one["report_chain"][0]["id"] == pid,
              json.dumps([c["name"] for c in one["report_chain"]],
                         ensure_ascii=False))

        st, _ = call("DELETE", f"/api/position/positions/{pid}", token=tk)
        check("有下级汇报线时不许停用（400）", st == 400, f"status={st}")
        st, _ = call("DELETE", f"/api/position/positions/{kid}", token=tk)
        check("无引用下级编制可停用（软删）", st == 200, f"status={st}")

        # 任职 → 编制被占用
        st, uu = call("GET", "/api/position/candidates", token=tk)
        check("在职人员候选非空", st == 200 and len(uu) > 0, str(len(uu)))
        uid = uu[0]["id"]
        st, ra = call("POST", "/api/position/assignments", {
            "position_id": pid, "user_id": uid, "is_primary": 1}, token=tk)
        check("任职登记成功", st == 200 and bool(ra.get("id")), str(ra)[:120])
        aid = ra["id"]
        st, _ = call("POST", "/api/position/assignments", {
            "position_id": pid, "user_id": uid, "is_primary": 1}, token=tk)
        check("重复任职被拒 400", st == 400, f"status={st}")
        st, one = call("GET", f"/api/position/positions/{pid}", token=tk)
        check("任职后在岗 +1、缺口 −1",
              one["occupied"] == 1 and one["gap"] == 4, str(one["gap"]))
        st, _ = call("DELETE", f"/api/position/positions/{pid}", token=tk)
        check("有在职人员时不许停用（400）", st == 400, f"status={st}")

        # 主岗唯一：换主岗后旧主岗自动降为兼岗
        st, rc2 = call("POST", "/api/position/positions", {
            "org_unit_id": oid, "job_title_id": tid, "job_grade_id": gid,
            "headcount": 1, "remark": "主岗唯一测试"}, token=tk)
        kid2 = rc2["id"]
        st, ra2 = call("POST", "/api/position/assignments", {
            "position_id": kid2, "user_id": uid, "is_primary": 1}, token=tk)
        check("同一人第二岗位登记成功", st == 200 and bool(ra2.get("id")),
              str(ra2)[:120])
        st, asg = call("GET", f"/api/position/assignments?user_id={uid}&active=1",
                       token=tk)
        prim = [a for a in asg if a["is_primary"] == 1]
        check("主岗唯一：新主岗生效后仅剩 1 个主岗",
              len(prim) == 1 and prim[0]["position_id"] == kid2,
              json.dumps([(a["position_id"], a["is_primary"]) for a in asg]))

        # 结束任职（软删 + 留痕）
        st, _ = call("DELETE", f"/api/position/assignments/{aid}", token=tk)
        st, asg = call("GET", f"/api/position/assignments?user_id={uid}&active=1",
                       token=tk)
        check("结束任职后该编制不再有在岗人员",
              st == 200 and all(a["position_id"] != pid for a in asg),
              json.dumps([a["position_id"] for a in asg]))
        st, asg_all = call("GET",
                           f"/api/position/assignments?user_id={uid}&active=-1",
                           token=tk)
        check("结束的任职仍可查到（is_active=0 留痕）",
              any(a["position_id"] == pid and a["is_active"] == 0
                  for a in asg_all), str(len(asg_all)))

        # 清场 → 编制可停用
        st, _ = call("DELETE", f"/api/position/assignments/{ra2['id']}", token=tk)
        st, _ = call("DELETE", f"/api/position/positions/{kid2}", token=tk)
        st, _ = call("DELETE", f"/api/position/positions/{pid}", token=tk)
        check("清场后成功停用编制（软删）", st == 200, f"status={st}")
        st, after = call("GET", "/api/position/positions?active=1", token=tk)
        check("停用后不再出现在默认列表",
              all(x["id"] != pid for x in after), str(len(after)))
        st, _ = call("GET", f"/api/position/positions/{pid}", token=tk)
        check("停用的编制仍可查详情（历史可追溯）", st == 200, f"status={st}")

        # ================= 8. 岗位 / 职级 CRUD =================
        st, rt = call("POST", "/api/position/titles", {
            "code": "JT-" + ts, "name": "测试岗位", "family": "T",
            "category": "tech"}, token=tk)
        check("新增岗位", st == 200 and bool(rt.get("id")), str(rt)[:120])
        new_tid = rt["id"]
        st, _ = call("PUT", f"/api/position/titles/{new_tid}", {
            "code": "JT-" + ts, "name": "测试岗位（改名）", "family": "T",
            "category": "tech"}, token=tk)
        st, tl = call("GET", "/api/position/titles?active=-1", token=tk)
        check("改岗位名生效",
              any(x["id"] == new_tid and x["name"] == "测试岗位（改名）" for x in tl))
        st, _ = call("PUT", f"/api/position/titles/{new_tid}", {
            "code": "JT-" + ts, "name": "坏序列", "family": "X",
            "category": "tech"}, token=tk)
        check("未知职级序列被拒 400", st == 400, f"status={st}")

        used_tid = next(t2["id"] for t2 in titles if t2["code"] == "JT-QC")
        st, rused = call("DELETE", f"/api/position/titles/{used_tid}", token=tk)
        check("岗位被编制引用时不许停用，且提示在职人数",
              st == 400 and "在职人员" in str(rused), str(rused)[:150])
        st, _ = call("DELETE", f"/api/position/titles/{new_tid}", token=tk)
        check("未被引用的岗位可停用（软删）", st == 200, f"status={st}")

        gcode = "Z" + ts[:4]
        st, rg = call("POST", "/api/position/grades", {
            "code": gcode, "family": "P", "level": 3, "name": "测试职级",
            "salary_min": 1000, "salary_max": 2000}, token=tk)
        check("新增职级", st == 200 and bool(rg.get("id")), str(rg)[:120])
        new_gid = rg["id"]
        st, _ = call("POST", "/api/position/grades", {
            "code": gcode + "B", "family": "M", "level": 9, "name": "越级职级"},
            token=tk)
        check("越级职级被拒（M 序列只有 1–6 级）", st == 400, f"status={st}")
        st, _ = call("POST", "/api/position/grades", {
            "code": gcode + "C", "family": "P", "level": 2, "name": "带宽倒挂",
            "salary_min": 5000, "salary_max": 1000}, token=tk)
        check("薪资带宽倒挂被拒 400", st == 400, f"status={st}")
        used_gid = next(g["id"] for g in grades if g["code"] == "T4")
        st, _ = call("DELETE", f"/api/position/grades/{used_gid}", token=tk)
        check("职级被编制引用时不许停用（400）", st == 400, f"status={st}")
        st, _ = call("DELETE", f"/api/position/grades/{new_gid}", token=tk)
        check("未被引用的职级可停用（软删）", st == 200, f"status={st}")

        # ================= 9. 权限 =================
        uname = "pos_sales_" + ts.lower()
        st, ru = call("POST", "/api/org/users", {
            "username": uname, "password": "Test@2026", "full_name": "自测业务员",
            "role": "sales", "emp_status": "active"}, token=tk)
        check("创建 sales 测试账号", st == 200 and bool(ru.get("id")), str(ru)[:120])
        if st != 200:
            return 1
        st, lg = call("POST", "/api/auth/login",
                      {"username": uname, "password": "Test@2026"})
        check("sales 账号登录", st == 200 and bool(lg.get("token")), f"status={st}")
        tk2 = lg["token"]

        st, _ = call("GET", "/api/position/positions", token=tk2)
        check("sales 有 position.view（可读 200）", st == 200, f"status={st}")
        st, _ = call("GET", "/api/position/board?dim=org", token=tk2)
        check("sales 可看编制看板（200）", st == 200, f"status={st}")
        st, _ = call("POST", "/api/position/positions", {
            "org_unit_id": oid, "headcount": 1}, token=tk2)
        check("sales 无 position.edit：新增编制被拒 403", st == 403, f"status={st}")
        st, _ = call("POST", "/api/position/assignments", {
            "position_id": used_tid and poss[0]["id"], "user_id": uid,
            "is_primary": 1}, token=tk2)
        check("sales 不能安排任职（403）", st == 403, f"status={st}")
        st, _ = call("DELETE", f"/api/position/titles/{new_tid}", token=tk2)
        check("sales 不能停用岗位（403）", st == 403, f"status={st}")
        st, _ = call("POST", "/api/position/grades", {
            "code": gcode + "D", "family": "P", "level": 1, "name": "越权职级"},
            token=tk2)
        check("sales 不能新增职级（403）", st == 403, f"status={st}")
        st, _ = call("GET", "/api/position/positions")
        check("未登录访问被拒 401", st == 401, f"status={st}")

        # 清理：停用测试账号（软删，不物理删）
        st, _ = call("DELETE", f"/api/org/users/{ru['id']}", token=tk)
        check("测试账号已停用（软删）", st == 200, f"status={st}")

        print(f"\n[position] 共 {PASSED} 项通过"
              + (f"，{len(FAILED)} 项失败：" + "、".join(FAILED) if FAILED
                 else "，全部通过"))
        return 1 if FAILED else 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        print("[position] 服务已关停")


if __name__ == "__main__":
    sys.exit(main())
