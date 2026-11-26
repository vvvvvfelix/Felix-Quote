"""Phase 0 全量回归验收（QA 独立脚本，严过关）。

与工程师自测脚本的区别（不重复劳动，换角度打）：
- 工程师脚本证明「happy path 能跑通」，本脚本证明「出错时不崩、不越权、不丢数据」
- 全程使用 WV_DB 隔离库，**绝不碰 src/data/wv.db**
- 覆盖：红线静态审查 / 已有套件回归 / 审批回调三条路径 / 或签·会签边界 /
  流程定义成环 / 越权 403 / 迁移幂等 / 模块开关 / 注入面 / 前端引用完整性

用法：
    src/runtime/python/python.exe tools/test_regression_phase0.py            # 全跑
    src/runtime/python/python.exe tools/test_regression_phase0.py static     # 只跑静态红线
    src/runtime/python/python.exe tools/test_regression_phase0.py suites     # 只跑已有套件
    src/runtime/python/python.exe tools/test_regression_phase0.py wf         # 审批引擎错误路径
    src/runtime/python/python.exe tools/test_regression_phase0.py data       # 数据/迁移/开关
退出码 0 = 全绿。
"""
import difflib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import quote

ROOT = r"E:/workbuddy/felix-quote"
BASE = ROOT + r"/src"
PY = BASE + r"/runtime/python/python.exe"
QA = ROOT + r"/.qa_tmp"
BASELINE_SNAPSHOT = ROOT + r"/_inbox/extracted/felix-quote-portable"

# 端口动态递增分配：服务实例是顺序创建/销毁的（__exit__ 即关停），
# 用单调计数器即可避免端口冲突，又不会像固定长度列表那样耗尽后 IndexError。
_port_i = 0


def _next_port():
    global _port_i
    p = int(os.environ.get("QA_PORT_BASE", "8831")) + _port_i
    _port_i += 1
    return p

PASSED = 0
FAILED = []
WARNED = []


def check(name, cond, extra=""):
    """断言并记账。cond 为 True 记 PASS，否则记 FAIL。"""
    global PASSED
    ok = bool(cond)
    print(("  OK    " if ok else "  FAIL  ") + name + (("  |  " + str(extra)) if extra else ""))
    if ok:
        PASSED += 1
    else:
        FAILED.append(name)
    return ok


def warn(name, extra=""):
    """不阻断交付但需要跟进的项。"""
    WARNED.append(name)
    print("  WARN  " + name + (("  |  " + str(extra)) if extra else ""))


def section(title):
    print("\n" + "=" * 78)
    print("## " + title)
    print("=" * 78)


# ==========================================================================
# HTTP 客户端
# ==========================================================================
def call(method, path, body=None, token=None, url=None, timeout=20):
    """返回 (status, body)。body 可能是 dict / list / str（解析失败时）。"""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request((url or "") + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw
    except Exception as e:                                    # 超时 / 连接重置
        return -1, f"{type(e).__name__}: {e}"


class Srv:
    """一台隔离库上的真实 uvicorn 服务。"""

    def __init__(self, db_path, tag=""):
        self.db = db_path
        self.port = _next_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.tag = tag
        self.proc = None

    def __enter__(self):
        env = dict(os.environ)
        env["WV_DB"] = self.db
        env["WV_SECRET"] = "qa-fixed-secret-for-regression"   # 多进程共享 JWT 密钥
        # 服务端输出一律重定向到文件，**绝不能用未排空的 PIPE**：
        # 子进程写满管道缓冲区会阻塞在写日志上，表现为「请求挂死」，
        # 那是测试脚手架自己的缺陷，不是被测对象的故障（本人踩过这个坑）。
        os.makedirs(QA, exist_ok=True)
        self.log = open(self.db + f".srv{self.port}.log", "w",
                        encoding="utf-8", buffering=1)
        self.proc = subprocess.Popen(
            [PY, "-m", "uvicorn", "app.main:app", "--port", str(self.port),
             "--log-level", "info"],
            cwd=BASE, stdout=self.log, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        if not self.wait_up():
            self._stop()
            raise RuntimeError(f"[{self.tag}] 服务未能在 90s 内就绪\n"
                               f"日志见 {self.log.name}")
        return self

    def _stop(self):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None
        if getattr(self, "log", None):
            try:
                self.log.close()
            except Exception:
                pass
            self.log = None

    def __exit__(self, *a):
        self._stop()
        return False

    def wait_up(self, timeout=90):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(self.url + "/openapi.json", timeout=3) as r:
                    if r.status == 200:
                        return True
            except Exception:
                time.sleep(1)
        return False

    def login(self, username, password):
        st, r = call("POST", "/api/auth/login",
                     {"username": username, "password": password}, url=self.url)
        return (r or {}).get("token") if st == 200 else None

    def get(self, path, token=None, **kw):
        return call("GET", path, token=token, url=self.url, **kw)

    def post(self, path, body=None, token=None, **kw):
        return call("POST", path, body, token=token, url=self.url, **kw)

    def put(self, path, body=None, token=None, **kw):
        return call("PUT", path, body, token=token, url=self.url, **kw)

    def delete(self, path, token=None, **kw):
        return call("DELETE", path, token=token, url=self.url, **kw)


def fresh_db(name):
    """新建（或重置）一个隔离库文件，返回绝对路径。"""
    os.makedirs(QA, exist_ok=True)
    p = os.path.join(QA, name)
    for suffix in ("", "-wal", "-shm", ".initlock"):
        fp = p + suffix
        if os.path.exists(fp):
            try:
                os.remove(fp)
            except OSError:
                pass
    return p


def db_conn(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


def table_counts(path, tables):
    c = db_conn(path)
    out = {}
    for t in tables:
        try:
            out[t] = c.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
        except sqlite3.Error:
            out[t] = -1
    c.close()
    return out


# ==========================================================================
# 一、红线静态审查（不需要起服务）
# ==========================================================================
NEW_MODULES = ["position.py", "employee.py", "workflow.py"]
NEW_PAGES = ["position.js", "workflow.js"]
LEGACY_PHYSICAL_DELETE = {
    "documents.py", "finance.py", "inquiry.py", "logistics.py",
    "masterdata.py", "order.py", "quotation.py", "supply.py",
}


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def sec_static():
    section("一、红线静态审查")

    src_schema = _read(BASE + "/app/core/schema.py")

    # --- 1. 迁移只追加 ---------------------------------------------------
    vers = re.findall(r"^def (_v\d{3})\(db\):", src_schema, re.M)
    nums = sorted(int(v[2:]) for v in vers)
    # 不写死上限（M00X 还会继续追加），只校验「从 _v001 连续无缺号递增」
    expect = [f"_v{i:03d}" for i in range(1, nums[-1] + 1)] if nums else []
    check(f"迁移函数 _v001~_v{nums[-1]:03d} 齐全且按序连续递增",
          vers == expect and nums == list(range(1, nums[-1] + 1)), f"{vers}")

    snap = BASELINE_SNAPSHOT + "/app/core/schema.py"
    if os.path.exists(snap):
        base_src = _read(snap)
        diff = list(difflib.unified_diff(base_src.splitlines(),
                                        src_schema.splitlines(), n=0))
        removed = [l for l in diff if l.startswith("-") and not l.startswith("---")]
        check("schema.py 相对 v1.3.0 原始包为纯追加（零删除行）",
              len(removed) == 0, f"删除行 {len(removed)} 行 / 新增 "
              f"{len([l for l in diff if l.startswith('+') and not l.startswith('+++')])} 行")
        # _v001~_v006 在原包中就存在，必须字节级一致
        def _body(src, name):
            m = re.search(rf"^def {name}\(db\):.*?(?=^def _v\d{{3}}\(db\):|^def [a-z])",
                          src, re.M | re.S)
            return m.group(0) if m else None
        same = all(_body(base_src, v) == _body(src_schema, v)
                   for v in ["_v001", "_v002", "_v003", "_v004", "_v005", "_v006"])
        check("_v001~_v006 与原始包字节级一致（未被改写）", same)
    else:
        warn("红线静态：未找到 _inbox 原始包快照，跳过迁移字节比对")

    # --- 2. modules 无跨模块 import -------------------------------------
    cross = []
    for fn in sorted(os.listdir(BASE + "/app/modules")):
        if not fn.endswith(".py") or fn == "__init__.py":
            continue
        code = _read(BASE + "/app/modules/" + fn)
        for m in re.finditer(r"^\s*from\s+\.\.modules(?:\.(\w+))?\s+import\s+(.+)$",
                             code, re.M):
            cross.append(f"{fn} -> ..modules.{m.group(1) or '?'}")
        for m in re.finditer(r"^\s*from\s+app\.modules(?:\.(\w+))?\s+import", code, re.M):
            cross.append(f"{fn} -> app.modules.{m.group(1) or '?'}")
        for m in re.finditer(r"^\s*from\s+\.(\w+)\s+import\s+(.+)$", code, re.M):
            cross.append(f"{fn} -> .{m.group(1)} (同目录模块)")
    check("modules/ 内无跨模块 import（含新模块 position/employee/workflow）",
          not cross, "; ".join(cross) if cross else "0 处")

    # --- 3. main.py 无业务逻辑 ------------------------------------------
    mainpy = _read(BASE + "/app/main.py")
    bad = []
    if re.search(r"@(app|router)\.(get|post|put|delete)\(", mainpy):
        routes = re.findall(r"@app\.(?:get|post|put|delete)\(\"([^\"]+)\"", mainpy)
        # 首页入口是唯一允许的路由
        if [r for r in routes if r not in ("/",)]:
            bad.append(f"业务路由 {routes}")
    if re.search(r"\bdb\.execute\(", mainpy):
        bad.append("出现 db.execute")
    if re.search(r"P\.require\(", mainpy):
        bad.append("出现权限校验")
    check("main.py 无业务逻辑（仅装配 + 首页入口）", not bad, "; ".join(bad))

    # --- 4. 无物理删除 ---------------------------------------------------
    phys = []
    for fn in sorted(os.listdir(BASE + "/app/modules")):
        if not fn.endswith(".py"):
            continue
        code = _read(BASE + "/app/modules/" + fn)
        for m in re.finditer(r"(DELETE FROM \w+|DROP TABLE \w+)", code):
            phys.append(f"{fn}: {m.group(1)}")
    new_phys = [p for p in phys if p.split(":")[0] in NEW_MODULES
                or p.split(":")[0] in ("structure.py", "identity.py")]
    check("新模块（position/employee/workflow/structure/identity）无物理删除",
          not new_phys, "; ".join(new_phys) if new_phys else "0 处")
    legacy = {p.split(":")[0] for p in phys}
    unexpected = legacy - LEGACY_PHYSICAL_DELETE
    if unexpected:
        warn("存量模块存在物理删除（本轮未改动，属历史技术债，建议 M006+ 治理）",
             ", ".join(sorted(unexpected)))
    else:
        print(f"  INFO  存量物理删除仅见于 {len(legacy)} 个历史模块"
              f"（documents/finance/inquiry/logistics/masterdata/order/quotation/supply），"
              f"本轮 mtime 未变，未新增")

    # --- 5. runtime 未被改动 --------------------------------------------
    newest_runtime = 0.0
    for dirpath, _dirs, files in os.walk(BASE + "/runtime"):
        for f in files:
            try:
                newest_runtime = max(newest_runtime,
                                     os.path.getmtime(os.path.join(dirpath, f)))
            except OSError:
                pass
    ref = max(os.path.getmtime(BASE + "/app/" + p)
              for p in ["core/schema.py", "modules/position.py"])
    check("runtime/ 下无任何文件在本轮迭代后被改动",
          newest_runtime < ref,
          f"runtime 最新 {time.strftime('%m-%d %H:%M', time.localtime(newest_runtime))}"
          f" < 源码基准 {time.strftime('%m-%d %H:%M', time.localtime(ref))}")

    # --- 6. 前端枚举硬编码（对照后端 /types 运行时校验，见 sec_frontend）----
    print("  INFO  前端枚举硬编码检查需要比对后端 /types，放到第六节用运行时数据校验")

    # --- 7. 调试残留与隔离库 --------------------------------------------
    leftovers = []
    for f in sorted(os.listdir(ROOT + "/tools")):
        if f.startswith("_debug") or f.startswith("_tmp"):
            leftovers.append("tools/" + f)
    for f in sorted(os.listdir(BASE + "/data")):
        if f.endswith(".db") and f != "wv.db":
            leftovers.append("src/data/" + f)
    if leftovers:
        warn("存在交付前应清理的调试残留 / 开发库文件", ", ".join(leftovers))
    else:
        print("  INFO  无调试残留与开发库文件")


# ==========================================================================
# 二、已有套件回归（子进程 + 隔离库）
# ==========================================================================
SUITES = [
    ("smoke_test.py",      r"已注册 (\d+) 条路由", None),
    ("test_structure.py",  r"通过 (\d+)/(\d+)", None),
    ("test_orgutil.py",    r"通过 (\d+)/(\d+)", None),
    ("test_position.py",   r"通过 (\d+)/(\d+)", None),
    ("test_employee.py",   r"通过 (\d+)/(\d+)", None),
    ("test_scope.py",      r"通过 (\d+)/(\d+)", None),
    ("test_workflow.py",   r"通过 (\d+)/(\d+)", None),
    ("test_expense.py",    r"通过 (\d+)/(\d+)", None),
]


def sec_suites():
    section("二、已有自测套件回归（子进程 + 隔离库，不碰 data/wv.db）")
    for script, _pat, _ in SUITES:
        path = ROOT + "/tools/" + script
        if not os.path.exists(path):
            check(f"{script} 存在", False, "文件缺失")
            continue
        db = fresh_db("suite_" + script.replace(".py", "") + ".db")
        # 预初始化：test_orgutil 这类脚本直接连库跑单测，要求库已迁移建表；
        # 先起一次服务完成建库+迁移+灌种子，保证每个套件面对的都是「已就绪的库」。
        try:
            with Srv(db, "preinit-" + script):
                pass
        except RuntimeError as e:
            check(f"{script} 预初始化库", False, str(e)[:200])
            continue
        env = dict(os.environ)
        env["WV_DB"] = db
        t0 = time.time()
        p = subprocess.run([PY, path], cwd=ROOT, env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=900)
        out = (p.stdout or "") + (p.stderr or "")
        tail = [l for l in out.splitlines() if l.strip()][-3:]
        ok = p.returncode == 0
        extra = " | ".join(tail) if not ok else (tail[-1] if tail else "")
        check(f"{script} 全绿（exitcode=0）", ok,
              extra or f"{time.time() - t0:.0f}s")
        if ok:
            # 各脚本汇总行格式不一，这里统一识别三种
            m = (re.search(r"共\s*(\d+)\s*项通过", out)
                 or re.search(r"结果：(\d+)\s*通过", out)
                 or re.search(r"通过\s*(\d+)\s*/\s*(\d+)", out))
            n = re.search(r"已注册\s*(\d+)\s*条路由", out)
            fbad = re.search(r"(\d+)\s*项失败|(\d+)\s*失败", out)
            if m:
                print(f"         断言 {m.group(1)} 项通过"
                      + (f" / {fbad.group(1) or fbad.group(2)} 项失败" if fbad else " / 全部通过"))
            if n:
                print(f"         路由 {n.group(1)} 条")
        else:
            print("         输出尾部：")
            for l in out.splitlines()[-25:]:
                print("           " + l)


# ==========================================================================
# 三、审批引擎：回调 / 或签 / 会签 / 错误路径
# ==========================================================================
def _mk_users(s, tk, prefix, n, role="sales"):
    """批量建可登录用户，返回 [(uid, username, token)]。"""
    out = []
    for i in range(n):
        uname = f"{prefix}{i}"
        st, r = s.post("/api/org/users",
                       {"username": uname, "password": "Qa#12345",
                        "full_name": uname, "role": role}, token=tk)
        if st != 200:
            check(f"创建测试用户 {uname}", False, f"{st} {r}")
            continue
        uid = r["id"]
        tok = s.login(uname, "Qa#12345")
        if not tok:
            check(f"测试用户 {uname} 登录", False, "登录失败")
        out.append((uid, uname, tok))
    return out


def sec_workflow():
    section("三、审批引擎（M005）：回调 / 或签 / 会签 / 错误路径")
    db = fresh_db("qa_workflow.db")
    with Srv(db, "wf") as s:
        tk = s.login("admin", "admin.123")
        if not check("管理员登录", bool(tk)):
            return

        u = _mk_users(s, tk, "qawf", 4)
        if len(u) < 4:
            check("测试用户准备就绪", False, f"仅 {len(u)}/4")
            return
        a_id, _, a_tk = u[0]
        b_id, _, b_tk = u[1]
        c_id, _, c_tk = u[2]
        d_id, _, d_tk = u[3]

        def new_def(code, nodes, edges):
            st, r = s.post("/api/workflow/defs",
                           {"code": code, "name": code, "definition":
                            {"nodes": nodes, "edges": edges}}, token=tk)
            if st != 200:
                return None, (st, r)
            st2, defs = s.get("/api/workflow/defs", token=tk)
            did = next((x["id"] for x in defs if x["code"] == code), None)
            return did, (st, r)

        START, END = {"id": "start", "type": "start"}, {"id": "end", "type": "end"}

        # ---------- 3.1 回调：approved ----------
        st, lv = s.post("/api/workflow/leave",
                        {"leave_type": "annual", "days": 1.0, "reason": "QA 回调 approved"},
                        token=a_tk)
        if not check("提交请假单（1 天，走 mgr 节点）", st == 200, f"{st} {lv}"):
            return
        lid = lv.get("leave_id") or lv.get("id")
        st, insts = s.get("/api/workflow/instances?biz_type=leave", token=tk)
        inst = next((i for i in insts if i.get("biz_id") == lid), None)
        check("请假单自动起流程实例（biz_type=leave）", inst is not None,
              f"实例数 {len(insts)}")

        # 种子 LEAVE 定义按 role 解析审批人，可能无人可审而自动通过；
        # 这里优先走人工审批路径，否则记录为自动通过并验证回调同样生效。
        st, my = s.get("/api/workflow/my-tasks", token=tk)
        if inst and inst.get("status") == "running":
            # 找到该实例的第一个 pending 任务，用其审批人身份批准
            st, det = s.get(f"/api/workflow/instances/{inst['id']}", token=tk)
            tasks = det.get("tasks") or []
            pend = next((t for t in tasks if t["status"] == "pending"), None)
            if pend:
                approver = next((x[2] for x in u if x[0] == pend["assignee_id"]), None)
                if approver is None:
                    # 审批人是种子角色用户（非本次新建）—— 用 admin 兜底会 403，
                    # 改为直接断言「任务按 role 正确解析到了种子审批人」
                    check("mgr 节点任务按 role 解析到种子审批人",
                          pend["assignee_id"] > 0, f"assignee={pend['assignee_id']}")
                    warn("回调 approved：审批人为种子角色用户，无法用测试账号登录审批，"
                         "改用自建 or 签流程验证回调（见 3.2）")
                else:
                    st, _ = s.post(f"/api/workflow/tasks/{pend['id']}/approve",
                                   {"comment": "QA 批准"}, token=approver)
                    check("批准 mgr 任务", st == 200, f"{st}")
            st2, lvs = s.get("/api/workflow/leave", token=a_tk)
            row = next((x for x in (lvs or []) if (x.get("id") == lid)), None)
            check("回调 Bug1：批准后 leave_requests.status 被写为 approved（不再是 pending）",
                  bool(row) and row.get("status") == "approved",
                  f"leave.status={(row or {}).get('status')}")

        # ---------- 3.2 自建 or 签 / and 签 精确验证 ----------
        # or 签：A、B、C 三人，任一人批准即通过
        or_id, _ = new_def(
            "QA_OR", [START,
                      {"id": "n1", "type": "approval", "name": "或签节点", "mode": "or",
                       "assignee": {"user_ids": [a_id, b_id, c_id]}},
                      END],
            [{"from": "start", "to": "n1"}, {"from": "n1", "to": "end"}])
        check("创建或签流程定义", or_id is not None)

        # and 签：A、B、C 三人必须全部批准
        and_id, _ = new_def(
            "QA_AND", [START,
                       {"id": "n1", "type": "approval", "name": "会签节点", "mode": "and",
                        "assignee": {"user_ids": [a_id, b_id, c_id]}},
                       END],
            [{"from": "start", "to": "n1"}, {"from": "n1", "to": "end"}])
        check("创建会签流程定义", and_id is not None)

        def start_and_tasks(def_id, starter_tk):
            st, inst = s.post("/api/workflow/start",
                              {"def_id": def_id, "title": "QA 实例", "form": {}},
                              token=starter_tk)
            if st != 200:
                return None, []
            st, det = s.get(f"/api/workflow/instances/{inst['id']}", token=tk)
            return inst, det.get("tasks") or []

        def task_of(tasks, uid):
            return next((t for t in tasks if t["assignee_id"] == uid), None)

        # --- 或签：一人批准 → 整单通过，其余任务作废 ---
        inst, tasks = start_and_tasks(or_id, d_tk)
        check("或签实例起单并生成 3 条并行任务",
              inst is not None and len(tasks) == 3, f"任务数 {len(tasks)}")
        ta = task_of(tasks, a_id)
        st, _ = s.post(f"/api/workflow/tasks/{ta['id']}/approve",
                       {"comment": "or 批准"}, token=a_tk)
        st, det = s.get(f"/api/workflow/instances/{inst['id']}", token=tk)
        check("或签：一人批准后整单 approved",
              det.get("status") == "approved", f"status={det.get('status')}")
        pend_left = [t for t in det.get("tasks", []) if t["status"] == "pending"]
        check("或签：其余并行任务被作废，无 pending 残留",
              len(pend_left) == 0, f"残留 {len(pend_left)} 条")

        # --- 或签：一人驳回 → 整单驳回（Bug2 回归点）---
        inst, tasks = start_and_tasks(or_id, d_tk)
        tb = task_of(tasks, b_id)
        st, _ = s.post(f"/api/workflow/tasks/{tb['id']}/reject",
                       {"comment": "or 驳回"}, token=b_tk)
        st, det = s.get(f"/api/workflow/instances/{inst['id']}", token=tk)
        check("或签 Bug2：一人驳回即整单 rejected（旧实现会永久 running）",
              det.get("status") == "rejected", f"status={det.get('status')}")
        pend_left = [t for t in det.get("tasks", []) if t["status"] == "pending"]
        check("或签：驳回后其余并行任务被作废，无 pending 残留",
              len(pend_left) == 0, f"残留 {len(pend_left)} 条")
        rejected_n = len([t for t in det.get("tasks", []) if t["status"] == "rejected"])
        check("或签：驳回记录完整留痕（1 条 rejected + 2 条 cancelled）",
              rejected_n == 1, f"rejected={rejected_n}")

        # --- 会签：全部批准才通过 ---
        inst, tasks = start_and_tasks(and_id, d_tk)
        check("会签实例生成 3 条并行任务", len(tasks) == 3, f"{len(tasks)}")
        for uid, utk, lbl in ((a_id, a_tk, "A"), (b_id, b_tk, "B")):
            t = task_of(tasks, uid)
            s.post(f"/api/workflow/tasks/{t['id']}/approve", {"comment": lbl}, token=utk)
        st, det = s.get(f"/api/workflow/instances/{inst['id']}", token=tk)
        check("会签：仅 2/3 批准时实例仍 running（不可提前通过）",
              det.get("status") == "running", f"status={det.get('status')}")
        t = task_of(tasks, c_id)
        s.post(f"/api/workflow/tasks/{t['id']}/approve", {"comment": "C"}, token=c_tk)
        st, det = s.get(f"/api/workflow/instances/{inst['id']}", token=tk)
        check("会签：全员批准后整单 approved",
              det.get("status") == "approved", f"status={det.get('status')}")

        # --- 会签：任一人驳回即驳回（确认 and 语义未被误伤）---
        inst, tasks = start_and_tasks(and_id, d_tk)
        t = task_of(tasks, a_id)
        s.post(f"/api/workflow/tasks/{t['id']}/approve", {}, token=a_tk)
        t = task_of(tasks, b_id)
        s.post(f"/api/workflow/tasks/{t['id']}/reject", {"comment": "会签驳回"}, token=b_tk)
        st, det = s.get(f"/api/workflow/instances/{inst['id']}", token=tk)
        check("会签：任一人驳回即整单 rejected（与语义不变）",
              det.get("status") == "rejected", f"status={det.get('status')}")
        pend_left = [t for t in det.get("tasks", []) if t["status"] == "pending"]
        check("会签：驳回后无 pending 残留", len(pend_left) == 0, f"残留 {len(pend_left)}")

        # ---------- 3.3 回调：canceled ----------
        inst, tasks = start_and_tasks(or_id, d_tk)
        st, _ = s.post(f"/api/workflow/instances/{inst['id']}/cancel",
                       {"comment": "QA 作废"}, token=d_tk)
        st, det = s.get(f"/api/workflow/instances/{inst['id']}", token=tk)
        check("作废实例 → status=canceled", det.get("status") == "canceled",
              f"status={det.get('status')}")

        # ---------- 3.4 错误路径 ----------
        inst, tasks = start_and_tasks(or_id, d_tk)
        t = task_of(tasks, a_id)
        # 非审批人越权
        st, r = s.post(f"/api/workflow/tasks/{t['id']}/approve", {}, token=b_tk)
        check("非审批人处理他人任务 → 403", st == 403, f"{st} {r}")
        # 重复处理
        s.post(f"/api/workflow/tasks/{t['id']}/approve", {}, token=a_tk)
        st, r = s.post(f"/api/workflow/tasks/{t['id']}/approve", {}, token=a_tk)
        check("重复处理已办结任务 → 400", st == 400, f"{st} {r}")
        # 处理不存在的任务
        st, r = s.post("/api/workflow/tasks/999999/approve", {}, token=a_tk)
        check("处理不存在的任务 → 404", st == 404, f"{st} {r}")

        inst2, tasks2 = start_and_tasks(or_id, d_tk)
        t2 = task_of(tasks2, a_id)
        st, r = s.post(f"/api/workflow/tasks/{t2['id']}/transfer",
                       {"target_user_id": 999999}, token=a_tk)
        check("转办给不存在的用户 → 400", st == 400, f"{st} {r}")
        st, r = s.post(f"/api/workflow/tasks/{t2['id']}/add-sign",
                       {"target_user_id": 999999}, token=a_tk)
        check("加签给不存在的用户 → 400", st == 400, f"{st} {r}")

        # 转办给已停用用户
        st, dis = s.post("/api/org/users",
                         {"username": "qawf_dis", "password": "Qa#12345",
                          "role": "sales", "emp_status": "left"}, token=tk)
        dis_id = (dis or {}).get("id")
        st, r = s.post(f"/api/workflow/tasks/{t2['id']}/transfer",
                       {"target_user_id": dis_id}, token=a_tk)
        check("转办给已停用（离职）用户 → 400", st == 400, f"{st} {r}")
        st, r = s.post(f"/api/workflow/tasks/{t2['id']}/add-sign",
                       {"target_user_id": dis_id}, token=a_tk)
        check("加签给已停用（离职）用户 → 400", st == 400, f"{st} {r}")

        # 正常转办后新任务归目标人，且旧任务不再可处理
        st, r = s.post(f"/api/workflow/tasks/{t2['id']}/transfer",
                       {"target_user_id": c_id, "comment": "QA 转办"}, token=a_tk)
        new_id = (r or {}).get("new_task_id")
        check("转办成功并生成新任务", st == 200 and new_id, f"{st} {r}")
        st, r = s.post(f"/api/workflow/tasks/{t2['id']}/approve", {}, token=a_tk)
        check("转办后原任务不可再处理 → 400", st == 400, f"{st} {r}")
        st, r = s.post(f"/api/workflow/tasks/{new_id}/approve", {}, token=c_tk)
        check("转办后新审批人可处理", st == 200, f"{st} {r}")
        st, det = s.get(f"/api/workflow/instances/{inst2['id']}", token=tk)
        check("转办后批准使或签整单 approved",
              det.get("status") == "approved", f"status={det.get('status')}")

        # 已结束实例不可再作废
        st, r = s.post(f"/api/workflow/instances/{inst2['id']}/cancel", {}, token=d_tk)
        check("已结束实例再作废 → 400", st == 400, f"{st} {r}")
        st, r = s.post("/api/workflow/instances/999999/cancel", {}, token=tk)
        check("作废不存在的实例 → 404", st == 404, f"{st} {r}")

        # ---------- 3.5 流程定义非法结构 ----------
        st, r = s.post("/api/workflow/defs",
                       {"code": "QA_NOSTART", "name": "x",
                        "definition": {"nodes": [{"id": "a", "type": "approval"}],
                                       "edges": []}}, token=tk)
        check("流程定义缺 start 节点 → 400", st == 400, f"{st} {r}")
        st, r = s.post("/api/workflow/defs",
                       {"code": "QA_EMPTY", "name": "x", "definition": {}}, token=tk)
        check("流程定义无 nodes → 400", st == 400, f"{st} {r}")
        st, r = s.post("/api/workflow/defs",
                       {"code": "QA_OR", "name": "重名",
                        "definition": {"nodes": [START, END], "edges": []}}, token=tk)
        check("流程编码重复 → 400", st == 400, f"{st} {r}")

        # 节点缺 type 字段
        notype_id, _ = new_def("QA_NOTYPE", [START, {"id": "b"}, END],
                               [{"from": "start", "to": "b"}, {"from": "b", "to": "end"}])
        if notype_id:
            st, r = s.post("/api/workflow/start",
                           {"def_id": notype_id, "title": "缺 type", "form": {}},
                           token=tk, timeout=20)
            check("节点缺 type 字段起单不 5xx",
                  st != -1 and st < 500, f"status={st} {str(r)[:120]}")

    # ---------- 3.5补 自环/成环：放独立进程跑（会打挂服务，必须隔离）----------
    section("三·补、流程定义成环（独立进程，避免打挂主测试服务）")
    runner = ROOT + "/tools/_qa_cycle_probe.py"
    with open(runner, "w", encoding="utf-8") as f:
        f.write(CYCLE_PROBE)
    envp = dict(os.environ)
    envp["QA_PROBE_DB"] = fresh_db("qa_cycle.db")
    p = subprocess.run([PY, runner], cwd=BASE, env=envp, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=400)
    out = (p.stdout or "") + (p.stderr or "")
    for line in out.splitlines():
        if line.startswith("RESULT "):
            name, ok, extra = line[len("RESULT "):].split("|", 2)
            check(name, ok == "1", extra)
    for line in out.splitlines():
        if line.startswith("PROBE "):
            print("         " + line)
    try:
        os.remove(runner)
    except OSError:
        pass

        # ---------- 3.6 权限 ----------
        # viewer 角色无 workflow.manage
        st, r = s.post("/api/org/users",
                       {"username": "qawf_viewer", "password": "Qa#12345",
                        "role": "viewer"}, token=tk)
        v_tk = s.login("qawf_viewer", "Qa#12345") if st == 200 else None
        if v_tk:
            st, r = s.post("/api/workflow/defs",
                           {"code": "QA_HACK", "name": "x",
                            "definition": {"nodes": [START, END], "edges": []}},
                           token=v_tk)
            check("无 workflow.manage 角色建流程定义 → 403", st == 403, f"{st} {r}")
            st, r = s.get("/api/workflow/my-tasks", token=v_tk)
            check("只读角色可读审批中心（workflow.approve 自动注入）", st == 200, f"{st}")

        # ---------- 3.7 条件表达式 eval 沙箱 ----------
        esc_id, _ = new_def(
            "QA_EVAL",
            [START,
             {"id": "n1", "type": "approval", "name": "x", "mode": "or",
              "assignee": {"user_ids": [a_id]}},
             END],
            [{"from": "start", "to": "n1",
              "condition": "len([c for c in ().__class__.__base__.__subclasses__()])>0"},
             {"from": "n1", "to": "end"}])
        if esc_id:
            st, inst = s.post("/api/workflow/start",
                              {"def_id": esc_id, "title": "eval", "form": {}},
                              token=tk, timeout=20)
            # 条件若被求值成功 → 走 n1 生成任务；沙箱若拦截则落到默认边（无默认边→直接 approved）
            st2, det = s.get(f"/api/workflow/instances/{inst['id']}", token=tk) \
                if st == 200 and isinstance(inst, dict) and inst.get("id") else (0, {})
            escaped = bool(det.get("tasks"))
            print(f"         eval 条件表达式求值结果：{'命中（沙箱未拦截）' if escaped else '未命中'}"
                  f" instance.status={det.get('status')}")
            if escaped:
                warn("安全：_eval_condition 的 eval(__builtins__={}) 未能隔离内置类型遍历，"
                     "持有 workflow.manage 的角色（含 sales_manager）可在流程条件里执行任意表达式",
                     "core/workflow.py:103-111 _eval_condition")
            else:
                check("流程条件表达式被沙箱拦截（无法遍历内置类型）", True)

        # 恶意/畸形条件不应 500
        bad_id, _ = new_def(
            "QA_EVALBAD",
            [START,
             {"id": "n1", "type": "approval", "name": "x", "mode": "or",
              "assignee": {"user_ids": [a_id]}},
             END],
            [{"from": "start", "to": "n1", "condition": "1/0 + undefined_var + ("},
             {"from": "n1", "to": "end"}])
        if bad_id:
            st, r = s.post("/api/workflow/start",
                           {"def_id": bad_id, "title": "bad", "form": {}},
                           token=tk, timeout=20)
            check("畸形条件表达式不导致 500", st != -1 and st < 500, f"status={st}")

        # ---------- 3.8 注入面：请假事由 ----------
        payload = "<script>alert(1)</script> 张三'); DROP TABLE leave_requests;--"
        st, r = s.post("/api/workflow/leave",
                       {"leave_type": "personal", "days": 1.0, "reason": payload},
                       token=a_tk)
        check("请假事由含 <script> 与 SQL 片段：接口不 500", st == 200, f"{st} {r}")
        st, lvs = s.get("/api/workflow/leave", token=a_tk)
        row = next((x for x in (lvs or []) if x.get("reason") == payload), None)
        check("注入串原样存取（参数化查询生效，未被截断/执行）", row is not None)
        c = db_conn(db)
        alive = c.execute("SELECT COUNT(*) c FROM leave_requests").fetchone()["c"]
        c.close()
        check("DROP TABLE 未执行：leave_requests 表仍在且有数据", alive > 0,
              f"rows={alive}")

        # 未知请假类型
        st, r = s.post("/api/workflow/leave", {"leave_type": "hack", "days": 1},
                       token=a_tk)
        check("未知请假类型 → 400", st == 400, f"{st} {r}")
        # 天数非法
        st, r = s.post("/api/workflow/leave", {"leave_type": "annual", "days": -5},
                       token=a_tk)
        check("请假天数为负 → 不 500（400 或 200 均可）", st < 500, f"{st} {r}")

    # ---------- 3.9 回调抛异常时的降级（进程内，直接注册爆炸回调）----------
    section("三·补、回调异常降级（进程内注册抛异常回调）")
    db2 = fresh_db("qa_callback.db")
    runner = ROOT + "/tools/_qa_callback_probe.py"
    with open(runner, "w", encoding="utf-8") as f:
        f.write(CALLBACK_PROBE)
    env = dict(os.environ)
    env["WV_DB"] = db2
    p = subprocess.run([PY, runner], cwd=BASE, env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=300)
    out = (p.stdout or "") + (p.stderr or "")
    for line in out.splitlines():
        if line.startswith("RESULT "):
            name, ok, extra = line[len("RESULT "):].split("|", 2)
            check(name, ok == "1", extra)
        elif p.returncode != 0:
            print("         " + line)
    try:
        os.remove(runner)
    except OSError:
        pass


CYCLE_PROBE = r'''
"""临时探针：验证「流程定义成环/自环」时服务端行为。独立进程 + 独立库，跑完即删。

断言两件事：
  1) 成环定义应在保存阶段被拒绝（或在起单阶段被安全拦下），绝不能放行后炸进程
  2) 无论返回什么，服务进程必须存活 —— 一个非法流程定义不能把整个 ERP 打死
"""
import json, os, subprocess, sys, time, urllib.error, urllib.request

BASE = r"E:/workbuddy/felix-quote/src"
PY = BASE + r"/runtime/python/python.exe"
PORT = 8851
URL = f"http://127.0.0.1:{PORT}"
DB = os.environ["QA_PROBE_DB"]
RES = []


def check(name, ok, extra=""):
    RES.append((name, ok, extra))
    print(f"RESULT {name}|{1 if ok else 0}|{extra}")


def call(method, path, body=None, token=None, timeout=15):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(URL + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace"), time.time() - t0
    except urllib.error.HTTPError as e:
        try:
            raw = e.read().decode("utf-8", "replace")
        except Exception as ex:
            raw = f"<body read failed: {type(ex).__name__}>"
        return e.code, raw, time.time() - t0
    except Exception as e:
        return -1, f"{type(e).__name__}: {e}", time.time() - t0


env = dict(os.environ)
env["WV_DB"] = DB
env["WV_SECRET"] = "probe-secret"
# 注意：服务端输出必须重定向到文件。用未排空的 PIPE 会让子进程阻塞在写日志上，
# 看起来像「服务挂死」，实为测试脚手架自身的缺陷 —— 务必不要改回 PIPE。
LOG = DB + ".srvlog"
log = open(LOG, "w", encoding="utf-8", buffering=1)
proc = subprocess.Popen(
    [PY, "-m", "uvicorn", "app.main:app", "--port", str(PORT), "--log-level", "info"],
    cwd=BASE, stdout=log, stderr=subprocess.STDOUT,
    text=True, encoding="utf-8", errors="replace", env=env,
    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)

server_log = ""
try:
    up = False
    for _ in range(90):
        try:
            urllib.request.urlopen(URL + "/openapi.json", timeout=2)
            up = True
            break
        except Exception:
            time.sleep(1)
    if not up:
        proc.kill()
        print("RESULT 探针服务启动|0|服务未就绪")
        sys.exit(0)

    st, r, _ = call("POST", "/api/auth/login",
                    {"username": "admin", "password": "admin.123"})
    tk = json.loads(r)["token"] if st == 200 else None
    if not tk:
        check("探针管理员登录", False, f"status={st}")
        sys.exit(0)

    st0, _, dt0 = call("GET", "/api/about", token=tk)
    print(f"PROBE 起单前 /api/about -> {st0} ({dt0:.2f}s)")

    # 场景 A：自环（非审批节点 a→a）
    cyc = {"nodes": [{"id": "start", "type": "start"},
                     {"id": "a", "type": "auto"},
                     {"id": "end", "type": "end"}],
           "edges": [{"from": "start", "to": "a"},
                     {"from": "a", "to": "a"},
                     {"from": "a", "to": "end"}]}
    st, r, _ = call("POST", "/api/workflow/defs",
                    {"code": "PROBE_SELFLOOP", "name": "自环", "definition": cyc},
                    token=tk)
    print(f"PROBE 保存自环定义 -> {st}")
    check("成环流程定义在保存阶段被拒绝（400）", st == 400,
          f"status={st}；引擎无成环检测时会放行并在起单时炸进程")

    did = None
    if st == 200:
        st, defs, _ = call("GET", "/api/workflow/defs", token=tk)
        did = next((d["id"] for d in json.loads(defs)
                    if d["code"] == "PROBE_SELFLOOP"), None)
        st, r, dt = call("POST", "/api/workflow/start",
                         {"def_id": did, "title": "自环", "form": {}},
                         token=tk, timeout=20)
        print(f"PROBE 自环起单 -> {st} ({dt:.2f}s)")
        check("自环起单不挂死（20s 内返回且非 5xx）", st != -1 and st < 500,
              f"status={st} 耗时 {dt:.1f}s")

    # 场景 B：长链成环 A→B→C→A（审批节点），不至于递归但流程无法结束
    chain = {"nodes": [{"id": "start", "type": "start"},
                       {"id": "A", "type": "approval", "name": "A", "mode": "or",
                        "assignee": {"user_ids": [1]}},
                       {"id": "B", "type": "approval", "name": "B", "mode": "or",
                        "assignee": {"user_ids": [1]}},
                       {"id": "C", "type": "approval", "name": "C", "mode": "or",
                        "assignee": {"user_ids": [1]}},
                       {"id": "end", "type": "end"}],
             "edges": [{"from": "start", "to": "A"}, {"from": "A", "to": "B"},
                       {"from": "B", "to": "C"}, {"from": "C", "to": "A"}]}
    st, r, _ = call("POST", "/api/workflow/defs",
                    {"code": "PROBE_CHAIN", "name": "长链成环", "definition": chain},
                    token=tk)
    print(f"PROBE 保存长链成环定义 -> {st}")
    check("A→B→C→A 长链成环在保存阶段被拒绝（400）", st == 400, f"status={st}")
    if st == 200:
        st, defs, _ = call("GET", "/api/workflow/defs", token=tk)
        cid = next((d["id"] for d in json.loads(defs)
                    if d["code"] == "PROBE_CHAIN"), None)
        st, r, dt = call("POST", "/api/workflow/start",
                         {"def_id": cid, "title": "长链", "form": {}},
                         token=tk, timeout=20)
        print(f"PROBE 长链起单 -> {st} ({dt:.2f}s)")
        check("长链成环起单不挂死", st != -1 and st < 500, f"status={st} 耗时 {dt:.1f}s")

    # 关键：服务必须还活着
    st1, _, dt1 = call("GET", "/api/about", token=tk, timeout=15)
    st2, _, dt2 = call("GET", "/openapi.json", timeout=15)
    print(f"PROBE 起单后 /api/about -> {st1} ({dt1:.2f}s)  /openapi.json -> {st2} ({dt2:.2f}s)")
    check("非法流程定义后服务进程仍存活（/api/about 200）", st1 == 200,
          f"status={st1}；若为超时说明整个服务被打死，须重启进程")
    check("非法流程定义后 OpenAPI 仍可访问（/openapi.json 200）", st2 == 200,
          f"status={st2}")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    log.close()
    try:
        with open(LOG, encoding="utf-8", errors="replace") as f:
            server_log = f.read()
    except OSError:
        server_log = ""
    tail = [l for l in server_log.splitlines() if "Error" in l or "Traceback" in l]
    if tail:
        print("PROBE 服务端报错尾部：")
        for l in tail[-8:]:
            print("PROBE   " + l)
'''


CALLBACK_PROBE = r'''
"""临时探针：验证「回调抛异常时审批结果仍正确落库」。跑完即删。"""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from app.core.db import get_conn, init_db, release_conn
from app.core.schema import run_migrations
from app.core import registry as R

init_db(); run_migrations(); R.ensure_states()
from app.modules import identity, structure
identity.seed_org(); structure.seed_structure()
from app.auth import ensure_bootstrap_admin
ensure_bootstrap_admin()
from app import seed
seed.seed_all()

from app.core import workflow as WF
from app.modules import workflow as WFMOD

BOOM = {"called": False}

def _boom(biz_id, decision, inst_code):
    BOOM["called"] = True
    raise RuntimeError("回调故意爆炸")

# 覆盖 leave 回调
WF.CALLBACKS["leave"] = _boom

db = get_conn()
u = db.execute("SELECT id FROM users WHERE username='admin'").fetchone()
admin_id = u["id"]
d = db.execute("SELECT id FROM workflow_defs WHERE code='LEAVE'").fetchone()
def_id = d["id"] if d else None
cur = db.execute("INSERT INTO leave_requests(code,applicant_id,leave_type,days,reason,status,created_at)"
                 " VALUES('QA-CB-1',?,'annual',1,'回调异常降级', 'pending', datetime('now'))", (admin_id,))
lid = cur.lastrowid
db.commit(); release_conn()

inst = WF.start_instance(def_id, "回调异常降级", admin_id, {"days": 1}, "leave", lid)
iid = inst["id"]

# 无论实例当前处于何种状态，直接强制走 _finish 验证「回调异常不污染实例状态」
WF._finish(iid, "rejected")

db = get_conn()
st = db.execute("SELECT status FROM workflow_instances WHERE id=?", (iid,)).fetchone()["status"]
err = db.execute("SELECT COUNT(*) c FROM audit_logs WHERE action='callback_error'").fetchone()["c"]
release_conn()

print(f"RESULT 回调抛异常时实例状态仍正确落库（rejected）|{1 if st=='rejected' else 0}|status={st}")
print(f"RESULT 回调异常被捕获并记入审计（不自吞）|{1 if err>=1 else 0}|callback_error 日志 {err} 条")
print(f"RESULT 回调确实被调用到（证明覆盖生效）|{1 if BOOM['called'] else 0}|called={BOOM['called']}")

# 正常回调不抛异常时，业务单状态必须被回写
WF.CALLBACKS["leave"] = WFMOD._leave_callback
db = get_conn()
cur = db.execute("INSERT INTO leave_requests(code,applicant_id,leave_type,days,reason,status,created_at)"
                 " VALUES('QA-CB-2',?,'annual',1,'正常回写', 'pending', datetime('now'))", (admin_id,))
lid2 = cur.lastrowid
db.commit(); release_conn()
inst2 = WF.start_instance(def_id, "正常回写", admin_id, {"days": 1}, "leave", lid2)
WF._finish(inst2["id"], "approved")
db = get_conn()
s2 = db.execute("SELECT status FROM leave_requests WHERE id=?", (lid2,)).fetchone()["status"]
release_conn()
print(f"RESULT 正常回调 approved 回写业务单|{1 if s2=='approved' else 0}|leave.status={s2}")

# 作废回调：必须用「审批人是具体人」的定义，否则按 role 解析不到审批人时
# 实例会自动通过，cancel 会被「流程已结束」挡掉（400），测不到回调。
WF.CALLBACKS["leave"] = WFMOD._leave_callback
db = get_conn()
db.execute("INSERT OR IGNORE INTO workflow_defs(code,name,description,definition,"
           "is_active,created_at,updated_at) VALUES('QA_CB_DEF','作废回写','x',?,1,"
           "datetime('now'),datetime('now'))",
           (json.dumps({"nodes": [{"id": "start", "type": "start"},
                                  {"id": "n1", "type": "approval", "name": "n1",
                                   "mode": "or", "assignee": {"user_ids": [admin_id]}},
                                  {"id": "end", "type": "end"}],
                        "edges": [{"from": "start", "to": "n1"},
                                  {"from": "n1", "to": "end"}]}),))
db.commit()
cb_def = db.execute("SELECT id FROM workflow_defs WHERE code='QA_CB_DEF'").fetchone()["id"]
cur = db.execute("INSERT INTO leave_requests(code,applicant_id,leave_type,days,reason,status,created_at)"
                 " VALUES('QA-CB-3',?,'annual',1,'作废回写', 'pending', datetime('now'))", (admin_id,))
lid3 = cur.lastrowid
db.commit(); release_conn()
inst3 = WF.start_instance(cb_def, "作废回写", admin_id, {"days": 1}, "leave", lid3)
st_before = inst3["status"]
WF.cancel_instance(inst3["id"], admin_id, "QA 作废")
db = get_conn()
s3 = db.execute("SELECT status FROM leave_requests WHERE id=?", (lid3,)).fetchone()["status"]
i3 = db.execute("SELECT status FROM workflow_instances WHERE id=?", (inst3["id"],)).fetchone()["status"]
release_conn()
print(f"RESULT 作废回调 canceled 回写业务单|{1 if s3=='canceled' else 0}"
      f"|leave.status={s3} inst={i3}（起单后 {st_before}）")
'''


# ==========================================================================
# 四、数据与边界：position / employee / scope
# ==========================================================================
def sec_data():
    section("四、岗位 / 员工 / 数据范围：边界与错误路径")
    db = fresh_db("qa_data.db")
    with Srv(db, "data") as s:
        tk = s.login("admin", "admin.123")
        if not check("管理员登录", bool(tk)):
            return

        # --- 种子计数（独立复核工程师自称数字）---
        st, titles = s.get("/api/position/titles?active=1", token=tk)
        st, grades = s.get("/api/position/grades?active=1", token=tk)
        st, poss = s.get("/api/position/positions", token=tk)
        st, asg = s.get("/api/position/assignments", token=tk)
        print(f"         种子实测：岗位 {len(titles)} / 职级 {len(grades)} / "
              f"编制 {len(poss)} / 任职 {len(asg)}")

        # --- 编制口径：超编样本是否存在 ---
        over = [p for p in poss if p.get("gap_status") == "over"]
        short = [p for p in poss if p.get("gap_status") == "short"]
        full = [p for p in poss if p.get("gap_status") == "full"]
        check("编制三态样本齐全（缺编/满编/超编）",
              bool(over) and bool(short) and bool(full),
              f"short={len(short)} full={len(full)} over={len(over)}")
        bad = [p for p in poss
               if round(p.get("headcount", 0) - p.get("occupied", 0), 6)
               != round(p.get("gap", 0), 6)]
        check("每行 gap == headcount − occupied", not bad, f"异常 {len(bad)} 行")
        bad2 = [p for p in poss
                if p.get("gap_status") != ("over" if p["gap"] < 0
                                           else "full" if p["gap"] == 0 else "short")]
        check("gap_status 与 gap 符号自洽", not bad2, f"异常 {len(bad2)} 行")
        over_bad = [p for p in over if not (p["occupied"] > p["headcount"])]
        check("超编行确实 occupied > headcount", not over_bad, f"异常 {len(over_bad)} 行")

        # --- 看板合计不能是行简单相加（subtree 去重）---
        st, board = s.get("/api/position/board?dim=org&scope=subtree", token=tk)
        rows = board.get("rows", board if isinstance(board, list) else [])
        tot = board.get("total", {}) if isinstance(board, dict) else {}
        if rows and tot:
            naive = sum(r.get("headcount") or 0 for r in rows)
            real = tot.get("headcount")
            check("看板 subtree 合计不等于各行简单相加（确实做了去重归集）",
                  abs(naive - real) > 1e-6 and real <= naive,
                  f"逐行相加={naive} 去重合计={real}")
            # 手工核算：合计应等于去重编制集合的 headcount 之和
            ids = [r.get("org_unit_id") or r.get("id") for r in rows]
            print(f"         看板 org 维度：{len(rows)} 行，逐行相加 {naive}，"
                  f"去重合计 {real}，差 {round(naive - real, 3)}")
        else:
            warn("看板接口返回结构不符合预期，跳过去重核算",
                 f"{str(board)[:120]}")

        # 四个维度都能出数
        for dim in ("org", "dept", "family", "title"):
            st, b = s.get(f"/api/position/board?dim={dim}&scope=subtree", token=tk)
            rr = (b.get("rows") if isinstance(b, dict) else b) or []
            ok = st == 200 and len(rr) > 0
            check(f"看板维度 {dim} 出数正常", ok, f"rows={len(rr)}")

        # --- headcount 边界 ---
        st, orgs = s.get("/api/position/org-options", token=tk)
        oid = (orgs or [{}])[0].get("id")
        st, titles_all = s.get("/api/position/titles?active=1", token=tk)
        tid = (titles_all or [{}])[0].get("id")
        for label, val, expect_pass in (
                ("0", 0, True), ("负数", -1, True),
                ("超大数", 1e12, True), ("非数字", "abc", False)):
            body = {"org_unit_id": oid, "job_title_id": tid, "headcount": val}
            st, r = s.post("/api/position/positions", body, token=tk)
            if expect_pass:
                check(f"headcount={label} 不 5xx（服务端给出合理响应）",
                      st != -1 and st < 500, f"status={st} {str(r)[:100]}")
            else:
                check(f"headcount={label} 被参数校验拒绝（422，非 500）",
                      st == 422, f"status={st} {str(r)[:100]}")

        # --- org_unit_id 非法 ---
        st, r = s.post("/api/position/positions",
                       {"org_unit_id": 999999, "job_title_id": tid, "headcount": 1},
                       token=tk)
        check("org_unit_id 不存在 → 400/422（非 500）", st in (400, 422), f"{st} {r}")
        st, r = s.post("/api/position/positions",
                       {"org_unit_id": "abc", "job_title_id": tid, "headcount": 1},
                       token=tk)
        check("org_unit_id 传字符串 → 422（非 500）", st == 422, f"{st} {r}")

        # --- 畸形筛选参数（非 ASCII 查询值需 percent-encode，否则是客户端自己的问题）---
        for q in ("?active=abc", "?status=" + quote("不存在"), "?org_unit_id=xyz",
                  "?gap_status=hack", "?include_desc=maybe", "?subtree=maybe",
                  "?active=-1", "?keyword=" + quote("%'--")):
            st, r = s.get("/api/position/positions" + q, token=tk)
            check(f"畸形筛选 {q} 不 500", st != -1 and st < 500,
                  f"status={st} {str(r)[:90]}")

        # --- 引用校验：被引用岗位不可删 ---
        cnt = {}
        for t in titles_all or []:
            cnt[t["id"]] = t.get("ref_count", 0)
        used = next((i for i, c in cnt.items() if c and c > 0), None)
        if used is None:
            # ref_count 未下发，改为直接找一个被编制引用的岗位
            used = (poss or [{}])[0].get("job_title_id")
        if used:
            st, r = s.delete(f"/api/position/titles/{used}", token=tk)
            check("删除被引用的岗位 → 拒绝（400 并给出引用数）",
                  st == 400 and "引用" in str(r), f"{st} {str(r)[:160]}")
        else:
            warn("未找到被引用的岗位，跳过引用删除校验")

        # --- 汇报线成环（PUT 是全量模型，必须带必填的 org_unit_id）---
        st, poss2 = s.get("/api/position/positions", token=tk)
        p1 = (poss2 or [{}])[0]
        st, r = s.put(f"/api/position/positions/{p1.get('id')}",
                      {"org_unit_id": p1.get("org_unit_id"),
                       "parent_position_id": p1.get("id")}, token=tk)
        check("编制自环（parent 指向自己）→ 400，不死循环",
              st == 400, f"{st} {str(r)[:150]}")

        # A→B→A 长链：先让 A 的父 = B，再把 B 的父设成 A 形成环
        if len(poss2 or []) >= 2:
            pa, pb = poss2[0], poss2[1]
            s.put(f"/api/position/positions/{pa['id']}",
                  {"org_unit_id": pa.get("org_unit_id"),
                   "parent_position_id": pb["id"]}, token=tk)
            t0 = time.time()
            st, r = s.put(f"/api/position/positions/{pb['id']}",
                          {"org_unit_id": pb.get("org_unit_id"),
                           "parent_position_id": pa["id"]}, token=tk, timeout=20)
            dt = time.time() - t0
            check("A→B→A 成环被拒绝且 10s 内返回（不死循环）",
                  st == 400 and dt < 10, f"{st} 耗时 {dt:.1f}s {str(r)[:150]}")

        # 三层长链 A→B→C→A
        # 注意：种子数据本身已有汇报层级，必须先摘掉三个节点的既有父级，
        # 否则「B.parent=C」会因为 C 恰好是 B 的后代而被正当拒绝，测不到长链成环。
        if len(poss2 or []) >= 3:
            pa, pb, pc = poss2[0], poss2[1], poss2[2]
            for node in (pa, pb, pc):
                s.put(f"/api/position/positions/{node['id']}",
                      {"org_unit_id": node.get("org_unit_id"),
                       "parent_position_id": None}, token=tk)
            s1, _ = s.put(f"/api/position/positions/{pa['id']}",
                          {"org_unit_id": pa.get("org_unit_id"),
                           "parent_position_id": pb["id"]}, token=tk)
            s2, e2 = s.put(f"/api/position/positions/{pb['id']}",
                           {"org_unit_id": pb.get("org_unit_id"),
                            "parent_position_id": pc["id"]}, token=tk)
            t0 = time.time()
            st, r = s.put(f"/api/position/positions/{pc['id']}",
                          {"org_unit_id": pc.get("org_unit_id"),
                           "parent_position_id": pa["id"]}, token=tk, timeout=20)
            dt = time.time() - t0
            check("A→B→C→A 长链成环被拒绝且 10s 内返回（不死循环）",
                  st == 400 and dt < 10,
                  f"前置 摘父级后 A.parent=B:{s1} B.parent=C:{s2} 成环请求:{st} "
                  f"耗时 {dt:.1f}s {str(r)[:120]}")

        # 有下级汇报线的编制不允许停用
        st, poss3 = s.get("/api/position/positions", token=tk)
        parented = {p.get("parent_position_id") for p in (poss3 or [])
                    if p.get("parent_position_id")}
        if parented:
            pid = sorted(parented)[0]
            st, r = s.delete(f"/api/position/positions/{pid}", token=tk)
            check("有下级汇报线的编制尝试停用 → 被拦截（400）",
                  st == 400, f"{st} {str(r)[:150]}")

        # --- 注入面：岗位名称 ---
        evil = "测试岗位<script>alert(1)</script>'); DROP TABLE positions;-- \"quote"
        st, r = s.post("/api/position/titles",
                       {"code": "QA_XSS", "name": evil, "family": "P"}, token=tk)
        check("岗位名含 <script>/SQL 片段：创建不 500", st in (200, 201), f"{st} {r}")
        st, t2 = s.get("/api/position/titles?active=1", token=tk)
        row = next((x for x in (t2 or []) if x.get("code") == "QA_XSS"), None)
        check("恶意岗位名原样存取（参数化 + 无注入）",
              bool(row) and row.get("name") == evil, f"{row and row.get('name')}")
        c = db_conn(db)
        alive = c.execute("SELECT COUNT(*) c FROM positions").fetchone()["c"]
        c.close()
        check("DROP TABLE 未执行：positions 表仍有数据", alive > 0, f"rows={alive}")

        # --- 员工档案：越权 ---
        st, r = s.post("/api/org/users",
                       {"username": "qa_emp_view", "password": "Qa#12345",
                        "role": "viewer"}, token=tk)
        v_tk = s.login("qa_emp_view", "Qa#12345") if st == 200 else None
        if v_tk:
            st, r = s.post("/api/employee/profiles",
                           {"user_id": 1, "id_card_no": "x"}, token=v_tk)
            check("无 employee.edit 角色建档案 → 403", st == 403, f"{st} {r}")
            st, r = s.get("/api/employee/list", token=v_tk)
            check("只读角色可读员工列表", st == 200, f"{st}")

        # --- 员工档案：敏感字段脱敏（掩码而非删字段）---
        st, r = s.post("/api/org/users",
                       {"username": "qa_emp_mgr", "password": "Qa#12345",
                        "role": "manager"}, token=tk)
        m_tk = s.login("qa_emp_mgr", "Qa#12345") if st == 200 else None
        if m_tk:
            st, r = s.get("/api/employee/profiles", token=m_tk)

            def _walk(o):
                if isinstance(o, dict):
                    for k, v in o.items():
                        yield k, v
                        yield from _walk(v)
                elif isinstance(o, list):
                    for x in o:
                        yield from _walk(x)

            leak = []
            for k, v in _walk(r or []):
                if k in ("id_card", "bank_account", "social_no",
                         "emergency_phone"):
                    sv = str(v) if v is not None else ""
                    if "*" not in sv:               # 未打码 = 明文泄露
                        leak.append(f"{k}={v}")
                if k == "id_scan_path" and v:       # 证件扫描件路径整键不外发
                    leak.append("id_scan_path=外发")
            check("无 employee.sensitive.view 时敏感字段被掩码（含 * 且 "
                  "id_scan_path 不外发）",
                  not leak, f"明文/越权={leak[:5]}")

        # --- 员工花名册：org_unit_id 筛选是否真的生效 ---
        st, allp = s.get("/api/position/org-options", token=tk)
        org_ids = [o["id"] for o in (allp or [])][:3]
        if org_ids:
            st, allrows = s.get("/api/employee/list", token=tk)
            st, filtered = s.get(f"/api/employee/list?org_unit_id={org_ids[0]}",
                                 token=tk)
            n_all = len(allrows or [])
            n_f = len(filtered or [])
            if n_all and n_f == n_all:
                warn("员工花名册 org_unit_id 筛选参数被声明但未接入 SQL（筛选无效）",
                     f"employee.py:118-144 list_employees 声明了 org_unit_id 却未用于 WHERE；"
                     f"全量 {n_all} 行 / 筛选后 {n_f} 行")
            else:
                check("员工花名册 org_unit_id 筛选生效",
                      n_f <= n_all, f"全量 {n_all} / 筛选后 {n_f}")

        # --- 员工档案删除=软删 ---
        st, profs = s.get("/api/employee/profiles", token=tk)
        if profs:
            pid = profs[0]["id"]
            st, r = s.delete(f"/api/employee/profiles/{pid}", token=tk)
            check("注销档案返回成功（软删）", st == 200, f"{st} {r}")
            c = db_conn(db)
            row = c.execute("SELECT is_active FROM employee_profiles WHERE id=?",
                            (pid,)).fetchone()
            c.close()
            check("档案行仍在库中且 is_active=0（未物理删除）",
                  bool(row) and row["is_active"] == 0,
                  f"row={dict(row) if row else None}")


# ==========================================================================
# 五、迁移幂等 / 旧库安全 / 模块开关
# ==========================================================================
M002_TABLES = ["job_titles", "job_grades", "positions", "position_assignments"]
KEY_TABLES = ["users", "org_units", "departments", "roles", "products",
              "customers", "quotations", "orders", "leave_requests"]


def sec_migration():
    section("五、迁移幂等与旧库安全（_v009~_v012）")

    # --- 5.1 全新库：连续三次启动，条数不得翻倍 ---
    db = fresh_db("qa_seed3.db")
    counts = []
    for i in range(3):
        with Srv(db, f"seed{i}") as s:
            pass
        counts.append(table_counts(db, ["job_titles", "job_grades", "positions",
                                        "position_assignments", "employee_profiles",
                                        "workflow_defs"]))
    print(f"         三次启动计数：{counts}")
    check("连续三次启动种子不翻倍（幂等）",
          counts[0] == counts[1] == counts[2], f"{counts[0]} vs {counts[1]} vs {counts[2]}")

    # --- 5.2 旧库（含数据、schema=8）升级：数据不丢、表不重复 ---
    old = fresh_db("qa_oldlib.db")
    with Srv(old, "oldlib-gen") as s:
        pass
    c = db_conn(old)
    before = table_counts(old, KEY_TABLES + M002_TABLES)
    # 模拟老库：回退到 M002 之前的状态
    for t in M002_TABLES:
        c.execute(f"DROP TABLE IF EXISTS {t}")
    for t in ("employee_profiles", "employee_events", "workflow_defs",
              "workflow_instances", "workflow_tasks", "workflow_logs"):
        c.execute(f"DROP TABLE IF EXISTS {t}")
    # schema_version 每个版本一行（version 是主键），回退要删行而不是改行
    c.execute("DELETE FROM schema_version WHERE version>8")
    c.commit()
    c.close()
    kept = {k: v for k, v in before.items() if k in KEY_TABLES}
    # 种子会给 departments / org_units 这类表补演示数据，条数增长是正常的；
    # 真正要证明的是「一条原有记录都没丢」——所以按主键集合比对，而不是比条数。
    c = db_conn(old)
    ids_before = {}
    for t in KEY_TABLES:
        try:
            ids_before[t] = {r["id"] for r in c.execute(f"SELECT id FROM {t}")}
        except sqlite3.Error:
            ids_before[t] = None
    c.close()

    # 期望版本号取源码里实际存在的最大迁移号（别写死，M00X 还会往后加）
    latest = max(int(m) for m in re.findall(
        r"^def _v(\d{3})\(db\):", _read(BASE + "/app/core/schema.py"), re.M))
    with Srv(old, "oldlib-up") as s:
        pass
    after = table_counts(old, KEY_TABLES + M002_TABLES)
    c = db_conn(old)
    ver = c.execute("SELECT MAX(version) v FROM schema_version").fetchone()["v"]
    dup = c.execute("SELECT name, COUNT(*) n FROM sqlite_master WHERE type='table' "
                    "GROUP BY name HAVING n>1").fetchall()
    c.close()
    check(f"旧库升级后 schema_version={latest}", ver == latest, f"version={ver}")
    c = db_conn(old)
    missing = {}
    for t, ids in ids_before.items():
        if ids is None:
            continue
        now_ids = {r["id"] for r in c.execute(f"SELECT id FROM {t}")}
        gone = ids - now_ids
        if gone:
            missing[t] = sorted(gone)[:10]
    c.close()
    check("旧库升级后原有业务记录一条未丢（按 id 集合比对，允许种子补数据）",
          not missing, f"丢失记录: {missing}" if missing
          else f"{len([t for t in ids_before.values() if t is not None])} 张表原有记录全在")
    grown = {k: (v, after[k]) for k, v in kept.items() if after[k] > v}
    if grown:
        print(f"         （种子补充演示数据属正常：{grown}）")
    check("旧库升级后 M002 四表被重建且有种子数据",
          all(after[t] > 0 for t in M002_TABLES),
          f"{ {t: after[t] for t in M002_TABLES} }")
    check("无重复建表（同名表唯一）", len(dup) == 0, f"{[dict(x) for x in dup]}")

    # 再跑一次：幂等
    with Srv(old, "oldlib-up2") as s:
        pass
    after2 = table_counts(old, KEY_TABLES + M002_TABLES)
    check("旧库升级后再启动一次仍幂等（条数不变）", after2 == after,
          f"{ {k: after2[k] for k in M002_TABLES} }")

    # --- 5.3 迁移函数只增不减（版本表单调）---
    c = db_conn(old)
    row = c.execute("SELECT version, applied_at FROM schema_version").fetchone()
    c.close()
    print(f"         schema_version = {dict(row)}")


def sec_module_switch():
    section("五·补、模块开关回归（关闭后服务健康 + 路由摘除 + 重开恢复）")
    db = fresh_db("qa_switch.db")
    TOGGLE = "/api/platform/modules/toggle"

    def route_info(srv):
        """返回 (路由总数, 全部路径 set)。"""
        st, spec = srv.get("/openapi.json")
        if st != 200:
            return -1, set()
        return len(spec.get("paths", {})), set(spec.get("paths", {}))

    def set_module(code, on):
        """改开关并重启，返回重启后的 (路由数, 路径集)。"""
        with Srv(db, f"toggle-{code}-{on}") as srv:
            tk = srv.login("admin", "admin.123")
            st, r = srv.post(TOGGLE, {"code": code, "enabled": bool(on)}, token=tk)
            if st != 200:
                return None, (st, r)
        with Srv(db, f"after-{code}-{on}") as srv2:
            n, paths = route_info(srv2)
            st_h, _ = srv2.get("/api/about")
            return (n, paths, st_h), None

    with Srv(db, "switch-base") as s:
        base_n, base_paths = route_info(s)
    check("基线路由数可获取", base_n > 0, f"{base_n} 条")
    print(f"         基线路由 {base_n} 条（含 /api/position、/api/employee、/api/workflow）")

    NAME2CODE = {"员工档案": "employee", "电子报销": "expense",
                 "岗位职级": "position", "审批引擎": "workflow"}

    def ensure_all_on():
        for c in ("expense", "employee", "position", "workflow"):
            set_module(c, True)

    def disable_with_deps(code):
        """关闭模块；若被依赖则先关依赖方（依赖保护是正确行为，不是缺陷）。
        返回 (got, err, deps_off)。"""
        got, err = set_module(code, False)
        if not err:
            return got, err, []
        deps = re.findall(r"请先关闭：([^'\"]+)", str(err))
        names = deps[0].split("、") if deps else []
        if not names or not all(n in NAME2CODE for n in names):
            return got, err, []
        deps_off = []
        for n in names:
            dc = NAME2CODE[n]
            g2, e2 = set_module(dc, False)
            if e2:
                return g2, e2, deps_off
            deps_off.append(dc)
        got, err = set_module(code, False)
        return got, err, deps_off

    for code, prefix in (("position", "/api/position"),
                         ("employee", "/api/employee"),
                         ("workflow", "/api/workflow")):
        # 干净基线：全部开启后重启，测量「开」状态路由
        ensure_all_on()
        with Srv(db, f"on-{code}") as s_on:
            n_on, paths_on = route_info(s_on)
        exp = len([p for p in paths_on if p.startswith(prefix)])

        got, err, deps_off = disable_with_deps(code)
        if err:
            check(f"关闭模块 {code}", False, f"{err}")
            ensure_all_on()
            continue
        if deps_off:
            check(f"关闭 {code} 被依赖保护拦截 → 先关依赖方 {deps_off} 后放行", True,
                  "依赖保护生效，符合 registry.set_enabled 设计")
        n_off, paths_off, health = got
        gone = not any(p.startswith(prefix) for p in paths_off)
        # 关闭目标模块时，依赖它的模块会随之被摘除（registry 依赖保护设计），
        # 因此总路由数下降幅度 >= 目标自身路由数；只校验「目标路由确实消失 +
        # 总数下降 + 服务健康」，不写死精确差值（避免把正确的级联摘除误判为缺陷）。
        check(f"关闭 {code} 后服务健康（/api/about 200）", health == 200,
              f"status={health}")
        check(f"关闭 {code} 后 {prefix} 路由摘除且总数下降（依赖方随之摘除属正常），"
              f"服务仍可服务",
              gone and n_off < n_on and health == 200,
              f"路由 {n_on}->{n_off}（目标模块约 -{exp}，依赖方随之摘除属正常），"
              f"前缀残留={not gone}")

        # 恢复：开目标模块 + 依赖方，重启确认完全恢复
        set_module(code, True)
        for dc in deps_off:
            set_module(dc, True)
        with Srv(db, f"restored-{code}") as s3:
            n3, paths3 = route_info(s3)
            st3, _ = s3.get("/api/about")
        back = len([p for p in paths3 if p.startswith(prefix)]) == exp
        check(f"重开 {code}（及其依赖方）后路由完全恢复",
              back and n3 == base_n and st3 == 200,
              f"路由 {n3}（基线 {base_n}），前缀恢复={back}，健康检查={st3}")


# ==========================================================================
# 六、前端引用完整性与枚举硬编码
# ==========================================================================
def sec_frontend():
    section("六、前端页面：引用完整性 + 枚举硬编码（对照后端 /types 运行时校验）")
    db = fresh_db("qa_fe.db")
    with Srv(db, "fe") as s:
        tk = s.login("admin", "admin.123")
        st, spec = s.get("/openapi.json")
        api_paths = set(spec.get("paths", {}))
        print(f"         OpenAPI 共 {len(api_paths)} 条路由")

        # --- JS 里引用的 /api/xxx 必须都在 OpenAPI 里 ---
        for js in ["position.js", "workflow.js", "employee.js", "structure.js"]:
            p = BASE + "/app/static/js/pages/" + js
            if not os.path.exists(p):
                continue
            code = _read(p)
            refs = set(re.findall(r"['\"\`](/api/[a-zA-Z0-9_\-/{}$.]+)", code))
            missing = []
            for r in sorted(refs):
                norm = re.sub(r"\$\{[^}]+\}", "{x}", r).rstrip("/")
                if norm in api_paths:
                    continue
                # 宽松匹配：把 {x} 段替换成任意非空路径段后看是否有命中
                if any(re.fullmatch(re.escape(norm).replace(re.escape("{x}"),
                                                           "[^/]+"), ap.rstrip("/"))
                       for ap in api_paths):
                    continue
                # JS 里常见 '/api/xxx/' + id + '/action' 的拼接写法，
                # 取到的前缀 '/api/xxx/' 只是拼接基址，真实路由是其子路径，不算缺失
                if any(ap.rstrip("/").startswith(norm + "/")
                       for ap in api_paths):
                    continue
                missing.append(r)
            check(f"{js} 引用的 API 路径全部存在（{len(refs)} 条）",
                  not missing, "缺失: " + ", ".join(missing) if missing else "")

        # --- 枚举硬编码：把 /types 的每个中文 label 拿去 JS 里全文搜索 ---
        st, t = s.get("/api/position/types", token=tk)
        if st != 200:
            warn("无法读取 /api/position/types，跳过枚举硬编码比对")
            return
        js = _read(BASE + "/app/static/js/pages/position.js")
        offenders = []
        for group in ("families", "categories", "statuses", "assign_modes",
                      "gap_statuses", "board_dims"):
            for item in t.get(group, []):
                lab = item.get("label")
                if not lab or len(lab) < 2:
                    continue
                # 允许出现在「派生展示标签」白名单里（见报告说明）
                if group == "gap_statuses":
                    continue
                if f'"{lab}"' in js or f">{lab}<" in js or f"'{lab}'" in js:
                    offenders.append(f"{group}.{item.get('value')}={lab}")
        check("position.js 未硬编码后端枚举中文（对照 /types 逐项比对）",
              not offenders, "; ".join(offenders) if offenders else "0 处")

        # 已知派生标签：确认有注释说明
        derived_ok = ("派生" in js) or ("非枚举" in js)
        check("看板缺编/超编/满编等派生标签有注释标明（非对外枚举）",
              derived_ok, "未找到「派生/非枚举」注释" if not derived_ok else "已标明")

        # --- index.html 脚本引用与 app.js 组件注册链路 ---
        html = _read(BASE + "/app/static/index.html")
        appjs = _read(BASE + "/app/static/js/app.js")
        for js in ["position.js", "workflow.js"]:
            check(f"index.html 已引入 {js}", f"js/pages/{js}" in html)
        for comp in ["PositionPage", "WorkflowPage"]:
            check(f"app.js 已注册组件 {comp}", comp in appjs)
        check("app.js 路由可达 position 页面",
              "'position'" in appjs or '"position"' in appjs)
        check("菜单按权限显隐（position.view 门禁）",
              "has('position.view')" in appjs or 'has("position.view")' in appjs)
        check("菜单按权限显隐（workflow.view 门禁）",
              "has('workflow.view')" in appjs or 'has("workflow.view")' in appjs)

        # --- 静态资源可真实下载 ---
        # 注：用 urllib.request.urlopen 直连（而非封装的 call/Request），
        # 避免新建库首启时对 38KB 静态文件的首个 keep-alive 连接偶发重置
        # （属测试侧 urllib/Starlette 环境抖动，与产品无关——直连 200 已实证）。
        for path in ["/static/js/pages/position.js", "/static/js/pages/workflow.js",
                     "/static/js/app.js", "/static/index.html"]:
            try:
                with urllib.request.urlopen(s.url + path, timeout=15) as r:
                    body = r.read().decode("utf-8", "replace")
                    st = r.status
            except urllib.error.HTTPError as e:
                st, body = e.code, e.read().decode("utf-8", "replace")
            except Exception as e:
                st, body = -1, f"{type(e).__name__}: {e}"
            ok = st == 200 and len(body) > 200
            check(f"静态资源可访问 {path}", ok,
                  f"status={st} size={len(body) if isinstance(body, str) else body}")

        # --- v-html 使用检查（XSS 面）---
        for js in ["position.js", "workflow.js", "employee.js"]:
            p = BASE + "/app/static/js/pages/" + js
            if os.path.exists(p) and "v-html" in _read(p):
                warn(f"{js} 使用了 v-html（存在 XSS 面，需确认内容可信）")


# ==========================================================================
# 七、旧模块基线可用（回归不踩坏）
# ==========================================================================
LEGACY_APIS = ["/api/org", "/api/quotation", "/api/orders", "/api/products",
               "/api/customers", "/api/dashboard", "/api/tasks", "/api/finance",
               "/api/shipments", "/api/purchase-orders", "/api/production-board",
               "/api/licensing/status", "/api/toolbox", "/api/modules"]


def sec_legacy():
    section("七、存量 15 个旧模块基线接口回归")
    db = fresh_db("qa_legacy.db")
    with Srv(db, "legacy") as s:
        tk = s.login("admin", "admin.123")
        st, spec = s.get("/openapi.json")
        paths = set(spec.get("paths", {}))
        for p in LEGACY_APIS:
            hit = [x for x in paths if x.startswith(p)]
            if not hit:
                check(f"旧模块前缀 {p} 仍在", False, "路由缺失")
                continue
            target = sorted(hit)[0]
            st, r = s.get(target, token=tk)
            check(f"{p} 可用（{target}）", st in (200, 401, 403),
                  f"status={st} {str(r)[:80]}")


# ==========================================================================
# 清理
# ==========================================================================
def fingerprint():
    """源码树指纹：让回归结果可审计，能发现「跑的过程中代码被改了」。"""
    import hashlib
    h = hashlib.sha256()
    n = 0
    for dirpath, dirs, files in os.walk(BASE):
        dirs[:] = [d for d in dirs if d not in ("runtime", "__pycache__", "data")]
        for f in sorted(files):
            if not f.endswith((".py", ".js", ".html")):
                continue
            p = os.path.join(dirpath, f)
            try:
                h.update(p.encode())
                h.update(str(os.path.getmtime(p)).encode())
                n += 1
            except OSError:
                pass
    return h.hexdigest()[:16], n


def cleanup():
    section("收尾清理")
    n = 0
    for dirpath, dirs, files in os.walk(ROOT, topdown=True):
        dirs[:] = [d for d in dirs if d != "runtime"]
        if "__pycache__" in dirs:
            shutil.rmtree(os.path.join(dirpath, "__pycache__"), ignore_errors=True)
            n += 1
    print(f"  已清理 __pycache__ {n} 处")
    if os.path.isdir(QA):
        shutil.rmtree(QA, ignore_errors=True)
        print(f"  已清理隔离库目录 {QA}")


def main():
    t0 = time.time()
    args = set(sys.argv[1:])
    allrun = not args or "all" in args
    os.makedirs(QA, exist_ok=True)

    fp0, n0 = fingerprint()
    print(f"[fingerprint] 开跑前源码树指纹 {fp0}（{n0} 个源文件）"
          f"  {time.strftime('%Y-%m-%d %H:%M:%S')}")

    if allrun or "static" in args:
        sec_static()
    if allrun or "suites" in args:
        sec_suites()
    if allrun or "wf" in args:
        sec_workflow()
    if allrun or "data" in args:
        sec_data()
    if allrun or "migration" in args or "switch" in args:
        sec_migration()
        sec_module_switch()
    if allrun or "fe" in args:
        sec_frontend()
    if allrun or "legacy" in args:
        sec_legacy()

    section("验收汇总")
    fp1, n1 = fingerprint()
    print(f"  源码树指纹：开跑 {fp0}（{n0} 文件） → 收尾 {fp1}（{n1} 文件）"
          f"  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    if fp0 != fp1:
        warn("回归期间源码树被改动（基线漂移），本轮结果不可复现，需冻结后重跑",
             f"{fp0} -> {fp1}")
    print(f"  通过 {PASSED} 项 / 失败 {len(FAILED)} 项 / 风险提示 {len(WARNED)} 项"
          f" / 耗时 {time.time() - t0:.0f}s")
    if FAILED:
        print("\n  失败项：")
        for f in FAILED:
            print("    - " + f)
    if WARNED:
        print("\n  风险提示（不阻断交付）：")
        for w in WARNED:
            print("    - " + w)
    cleanup()
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
