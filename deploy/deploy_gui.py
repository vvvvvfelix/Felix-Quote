"""服务器部署辅助工具 —— 图形窗口版（本地网页 UI）。

为什么是网页窗口而不是原生窗口：
    本项目自带的便携 runtime **没有 tkinter**（系统 Python 也没有），装 GUI 库又会
    破坏「零第三方依赖」的交付约束。所以用标准库 http.server + webbrowser：
    起一个只监听 127.0.0.1 的本地服务，自动打开浏览器 —— 用户看到的就是一个
    带按钮 / 下拉框 / 勾选框的图形窗口，且**真正执行部署**，不是一份说明文档。

能力（与命令行版 deploy_assist.py 完全一致，共用 setup.py 的阶段函数）：
    ① 数据库部署：SQLite / SQL Server / MySQL(预留) / PostgreSQL(预留)，
       SQL Server 默认管理员 root、密码 88888888
    ② 服务器部署：监听地址 / 端口；仅生成脚本 或 生成并启动
    ③ 模块选择（可选部署）：全量 / 精简预设，或逐项勾选；核心模块不可关

用法：
    python deploy/deploy_gui.py
    （或双击 src/【部署助手】.bat）
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPLOY_DIR = os.path.join(ROOT, "deploy")
SRC = os.path.join(ROOT, "src")
CONF_PATH = os.path.join(DEPLOY_DIR, "deploy.conf")
DBCONFIG_PATH = os.path.join(SRC, "data", "dbconfig.json")
PY = os.path.join(SRC, "runtime", "python", "python.exe")
PYW = os.path.join(SRC, "runtime", "python", "pythonw.exe")

sys.path.insert(0, DEPLOY_DIR)
sys.path.insert(0, SRC)

DEFAULT_DB_USER = "root"
DEFAULT_DB_PWD = "88888888"
DEFAULT_DB_NAME = "felix"
DEFAULT_DB_PORT = 1433
DEFAULT_DB_SERVER = "127.0.0.1"
DEFAULT_ODBC_DRIVER = "ODBC Driver 17 for SQL Server"
MINIMAL_SET = {"masterdata", "quotation", "report"}

_setup = None          # 延迟加载的 setup.py 模块


def load_setup():
    global _setup
    if _setup is None:
        import importlib.util
        path = os.path.join(DEPLOY_DIR, "setup.py")
        spec = importlib.util.spec_from_file_location("felix_deploy_setup", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _setup = mod
    return _setup


def read_conf() -> dict:
    if os.path.isfile(CONF_PATH):
        try:
            with open(CONF_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


# ---------------------------------------------------------------------------
# 业务逻辑（三个部署动作）
# ---------------------------------------------------------------------------
def do_db(data: dict) -> dict:
    setup = load_setup()
    btype = (data.get("type") or "sqlite").strip()
    conf = read_conf()
    db_path = data.get("db_path") or conf.get("db") or os.path.join(SRC, "data", "wv.db")
    port = int(data.get("port") or conf.get("port") or 8795)

    if btype == "sqlite":
        setup.phase_configure(port, db_path, "sqlite", None)
        info = setup.phase_init_db(None)
        return {"ok": True, "msg":
                f"SQLite 初始化完成：表 {info['tables']} 张 / 用户 {info['users']} / "
                f"部门 {info['departments']}；管理员 {info['admin']} / {info['password']}"}

    if btype == "mssql":
        mssql = {
            "server": data.get("server") or DEFAULT_DB_SERVER,
            "port": int(data.get("db_port") or DEFAULT_DB_PORT),
            "database": data.get("database") or DEFAULT_DB_NAME,
            "user": data.get("user") or DEFAULT_DB_USER,
            "password": data.get("password") or DEFAULT_DB_PWD,
            "driver": data.get("driver") or DEFAULT_ODBC_DRIVER,
        }
        setup.phase_configure(port, db_path, "mssql", mssql)
        return {"ok": True, "msg":
                f"SQL Server 配置已保存到 data/dbconfig.json（{mssql['server']}:{mssql['port']} "
                f"库={mssql['database']} 账号={mssql['user']}）。\n"
                "注意：业务查询的 T-SQL 移植留到客户实际换库时完成，"
                "当前 _mssql_conn() 仍是 NotImplementedError，选它暂时不能真正跑起来。"}

    # MySQL / PostgreSQL：预留
    setup.phase_configure(port, db_path, btype, None)
    return {"ok": True, "msg":
            f"已登记 {btype}（预留类型）。当前 database.py 只有 sqlite / mssql 分支，"
            f"选择它**不能真正使用**；要支持需改 database.py 的连接工厂与 core/dialect.py。"}


def do_server(data: dict) -> dict:
    setup = load_setup()
    conf = read_conf()
    backend = conf.get("db_backend", "sqlite")
    db_path = conf.get("db") or os.path.join(SRC, "data", "wv.db")
    host = data.get("host") or conf.get("host") or "127.0.0.1"
    port = int(data.get("port") or conf.get("port") or 8795)
    mode = data.get("mode") or "scripts"

    conf = setup.phase_configure(port, db_path, backend,
                                 conf.get("mssql") if backend == "mssql" else None)
    conf["host"] = host
    with open(CONF_PATH, "w", encoding="utf-8") as f:
        json.dump(conf, f, ensure_ascii=False, indent=2)

    scripts = setup.phase_scripts(port, db_path, backend)
    msg = f"已生成启动脚本：\n  " + "\n  ".join(scripts) + f"\n访问地址：http://{host}:{port}"

    if mode == "start":
        if backend != "sqlite":
            return {"ok": True, "msg": msg + "\n\n当前后端非 SQLite，未启动（业务查询未移植）。"}
        if _port_open(port):
            return {"ok": True, "msg": msg + f"\n\n端口 {port} 已有服务在跑，跳过启动。"}
        exe = PYW if os.path.isfile(PYW) and sys.platform == "win32" else PY
        logf = open(os.path.join(DEPLOY_DIR, "logs", "gui_server.log"), "a", encoding="utf-8")
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        subprocess.Popen([exe, "-m", "uvicorn", "app.main:app", "--port", str(port)],
                         cwd=SRC, stdout=logf, stderr=logf,
                         env={**os.environ, "WV_DB": db_path},
                         creationflags=flags, close_fds=True)
        return {"ok": True, "msg": msg + "\n\n服务正在后台启动（约 30–50 秒），稍后刷新该地址即可。"}
    return {"ok": True, "msg": msg}


def _open_registry(backend: str, db_path: str):
    os.environ["WV_DB"] = db_path
    os.environ["WV_DB_BACKEND"] = backend
    from app.database import init_db
    from app.core.schema import run_migrations
    from app.core import registry
    init_db()
    run_migrations()
    registry.ensure_states()
    return registry


def _apply_targets(reg, wanted: set) -> int:
    changed = 0
    for _ in range(8):
        moved = False
        for m in reg.MODULES:
            code = m["code"]
            if m["core"]:
                continue
            want = code in wanted
            if reg.is_enabled(code) != want:
                ok, _msg = reg.set_enabled(code, want)
                if ok:
                    moved = True
                    changed += 1
        if not moved:
            break
    return changed


def build_config() -> dict:
    conf = read_conf()
    backend = conf.get("db_backend", "sqlite")
    db_path = conf.get("db") or os.path.join(SRC, "data", "wv.db")
    mods, mod_err = [], None
    if backend == "sqlite":
        try:
            reg = _open_registry(backend, db_path)
            mods = [{"code": m["code"], "name": m["name"], "group": m["group"],
                     "core": m["core"], "enabled": m["enabled"], "desc": m["desc"],
                     "example": m.get("example", "")}
                    for m in reg.catalog()]
        except Exception as exc:
            mod_err = f"{type(exc).__name__}: {exc}"
    return {"conf": conf, "dbconfig": (json.load(open(DBCONFIG_PATH, encoding="utf-8"))
                                       if os.path.isfile(DBCONFIG_PATH) else {}),
            "modules": mods, "modules_error": mod_err,
            "defaults": {"user": DEFAULT_DB_USER, "password": DEFAULT_DB_PWD,
                         "database": DEFAULT_DB_NAME, "port": DEFAULT_DB_PORT,
                         "server": DEFAULT_DB_SERVER, "driver": DEFAULT_ODBC_DRIVER,
                         "db_path": db_path}}


def do_modules(data: dict) -> dict:
    conf = read_conf()
    backend = conf.get("db_backend", "sqlite")
    db_path = conf.get("db") or os.path.join(SRC, "data", "wv.db")
    if backend != "sqlite":
        return {"ok": False, "msg": "非 SQLite 后端，无法改动模块开关。"}
    reg = _open_registry(backend, db_path)
    preset = data.get("preset")
    if preset == "all":
        n = _apply_targets(reg, {m["code"] for m in reg.MODULES})
        msg = f"已切换到全量（改动 {n} 个）。"
    elif preset == "min":
        n = _apply_targets(reg, MINIMAL_SET)
        msg = f"已切换到精简集（改动 {n} 个）：核心 + 主数据 + 报价 + 报表。"
    else:
        codes = set(data.get("codes") or [])
        n = _apply_targets(reg, codes)
        msg = f"已应用选择（改动 {n} 个）。"
    return {"ok": True, "msg": msg + "\n模块开关重启服务后生效；关闭只隐藏菜单、不删表，可再开启。"}


def do_detect(data: dict) -> dict:
    """环境检测：当前机器是否满足安装/部署要求。

    复用 deploy/detect_env.py 的检查项（操作系统 / Python / 自带运行时 / 依赖 /
    内存 / 磁盘 / 端口占用 / 数据目录可写 / 数据库 / 桌面目录 / MSSQL），
    不在这里重复实现 —— 保证命令行版与图形版判定口径一致。
    """
    import detect_env
    conf = read_conf()
    port = int(data.get("port") or conf.get("port") or 8795)
    db_path = data.get("db_path") or conf.get("db") or os.path.join(SRC, "data", "wv.db")
    backend = conf.get("db_backend", "sqlite")
    results = detect_env.run_checks(port, db_path, backend)
    s = detect_env.summary(results)
    return {"ok": True, "results": results, "summary": s, "passed": s["fail"] == 0}


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------
PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>Felix 企业管理平台 · 部署助手</title>
<style>
*{box-sizing:border-box}body{margin:0;font:14px/1.6 "Microsoft YaHei",system-ui;background:#F5F6F8;color:#1F2430}
header{background:#2B3A55;color:#fff;padding:14px 20px;font-size:16px;font-weight:600}
.wrap{max-width:960px;margin:0 auto;padding:18px}
.card{background:#fff;border:1px solid #E3E6EC;border-radius:8px;padding:16px;margin-bottom:14px}
h2{margin:0 0 10px;font-size:15px;color:#2B3A55;border-left:3px solid #4A6FA5;padding-left:8px}
label{display:inline-block;margin-right:14px;cursor:pointer}
.row{margin:8px 0}
input[type=text]{width:260px;padding:6px 8px;border:1px solid #C9CFDA;border-radius:4px}
input[type=number]{width:110px;padding:6px 8px;border:1px solid #C9CFDA;border-radius:4px}
button{background:#4A6FA5;color:#fff;border:0;padding:8px 16px;border-radius:5px;cursor:pointer;margin-right:8px}
button:hover{background:#3C5C8A}button.ghost{background:#fff;color:#4A6FA5;border:1px solid #4A6FA5}
.mods{display:grid;grid-template-columns:repeat(3,1fr);gap:4px;margin:10px 0}
.mod{padding:4px 6px;border:1px solid #E3E6EC;border-radius:4px;font-size:13px}
.mod.core{background:#F2F4F8;color:#8B90A0}
.mod:hover{background:#EAF0F9;border-color:#4A6FA5}
.info{background:#F2F4F8;border-left:3px solid #4A6FA5;border-radius:4px;padding:10px 12px;margin-top:10px;min-height:58px;font-size:13px}
.env{border:1px solid #E3E6EC;border-radius:6px;padding:10px;margin-top:8px}
.envrow{display:flex;gap:8px;padding:5px 0;border-bottom:1px dashed #EDF0F4;align-items:flex-start}
.envrow:last-child{border-bottom:0}
.badge{flex:0 0 62px;text-align:center;border-radius:4px;font-size:12px;padding:2px 0;color:#fff}
.b-ok{background:#1E7A4C}.b-warn{background:#C87F0A}.b-fail{background:#C0392B}
.envrow .txt{flex:1}
.fix{color:#9C2B20;font-size:12.5px;margin-top:3px}
.muted{color:#8B90A0;font-size:12.5px}
pre{background:#1F2430;color:#D8DEE9;padding:12px;border-radius:6px;white-space:pre-wrap;min-height:70px;font-size:12.5px}
.hide{display:none}
</style></head><body>
<header>Felix 企业管理平台 · 服务器部署辅助工具</header>
<div class="wrap">

<div class="card"><h2>⓪ 环境检测（是否满足安装要求）</h2>
  <div class="row"><button onclick="runEnv()">重新检测</button>
  <span class="muted">检查操作系统、Python、自带运行时、运行依赖、内存、磁盘、端口占用、数据目录、数据库等</span></div>
  <div id="envBox"></div>
</div>

<div class="card"><h2>① 数据库部署</h2>
  <div class="row">
    <label><input type="radio" name="db" value="sqlite" checked> SQLite（零依赖，默认）</label>
    <label><input type="radio" name="db" value="mssql"> SQL Server（root / 88888888）</label>
    <label><input type="radio" name="db" value="mysql"> MySQL（预留）</label>
    <label><input type="radio" name="db" value="postgresql"> PostgreSQL（预留）</label>
  </div>
  <div class="row" id="sqliteBox">
    <span class="muted">数据库文件：</span><input type="text" id="dbPath">
  </div>
  <div class="row hide" id="mssqlBox">
    <div>地址 <input type="text" id="mServer"> 端口 <input type="number" id="mPort"></div>
    <div style="margin-top:6px">库名 <input type="text" id="mDb"> 账号 <input type="text" id="mUser"> 密码 <input type="text" id="mPwd"></div>
    <div style="margin-top:6px">驱动 <input type="text" id="mDriver" style="width:320px"></div>
  </div>
  <div class="row"><button onclick="runDb()">执行数据库部署</button>
  <span class="muted" id="dbHint"></span></div>
</div>

<div class="card"><h2>② 服务器部署</h2>
  <div class="row">监听地址 <input type="text" id="host"> 端口 <input type="number" id="port"></div>
  <div class="row"><button onclick="runServer('scripts')">仅生成启动脚本</button>
  <button class="ghost" onclick="runServer('start')">生成并启动服务</button></div>
</div>

<div class="card"><h2>③ 模块选择（可选部署）</h2>
  <div class="row"><button class="ghost" onclick="preset('all')">全量</button>
  <button class="ghost" onclick="preset('min')">精简集（核心+主数据+报价+报表）</button>
  <span class="muted">灰底为核心模块，永不可关</span></div>
  <div class="mods" id="mods"></div>
  <div class="info" id="modInfo"></div>
  <div class="row"><button onclick="applyMods()">应用模块选择</button></div>
</div>

<div class="card"><h2>执行结果</h2><pre id="log">等待操作…</pre>
  <div class="row"><button class="ghost" onclick="quit()">关闭部署助手</button>
  <span class="muted">当前配置：<span id="cur"></span></span></div>
</div>
</div>
<script>
const log = t => document.getElementById('log').textContent = t;
const TIP='把鼠标移到任意模块上，这里会显示它的 <b>具体用途举例</b>，帮你判断要不要勾选。';
function resetInfo(){document.getElementById('modInfo').innerHTML=TIP;}
function showInfo(m){
  document.getElementById('modInfo').innerHTML =
    '<b>'+m.name+'</b> <span class="muted">（'+m.group+' · '+(m.core?'核心模块，不可关闭':'可选')+'）</span><br>'
    +'<span class="muted">说明：</span>'+m.desc+'<br>'
    +'<span class="muted">举例：</span>'+(m.example||'（暂无示例）');
}
async function runEnv(){
  const box=document.getElementById('envBox'); box.innerHTML='检测中…';
  const r=await post('/api/detect',{port:document.getElementById('port').value,
    db_path:document.getElementById('dbPath').value});
  const s=r.summary||{};
  let verdict = r.passed ? (s.warn ? '⚠️ 基本满足（有 '+s.warn+' 项警告）' : '✅ 满足安装要求')
                         : '❌ 不满足安装要求（'+s.fail+' 项必须先处理）';
  let h='<div class="env"><div style="margin-bottom:6px"><b>结论：'+verdict+'</b>'
      +' <span class="muted">通过 '+(s.ok||0)+' / 警告 '+(s.warn||0)+' / 不满足 '+(s.fail||0)+'</span></div>';
  (r.results||[]).forEach(x=>{
    const cls = x.status==='ok'?'b-ok':(x.status==='warn'?'b-warn':'b-fail');
    const label = x.status==='ok'?'通过':(x.status==='warn'?'警告':'不满足');
    h+='<div class="envrow"><span class="badge '+cls+'">'+label+'</span><span class="txt">'
      +'<b>'+x.item+'</b> <span class="muted">'+x.detail+'</span>';
    if(x.fix) h+='<div class="fix">→ '+x.fix.split(String.fromCharCode(10)).join('<br>→ ')+'</div>';
    h+='</span></div>';
  });
  box.innerHTML=h+'</div>';
}
function sel(){return document.querySelector('input[name=db]:checked').value}
function syncType(){const v=sel();
  document.getElementById('sqliteBox').className='row'+(v==='sqlite'?'':' hide');
  document.getElementById('mssqlBox').className='row'+(v==='mssql'?'':' hide');
  document.getElementById('dbHint').textContent =
    v==='mysql'||v==='postgresql' ? '预留类型：当前代码未实现该分支，选择后不可用。' : '';
}
document.querySelectorAll('input[name=db]').forEach(r=>r.onchange=syncType);
async function post(u,b){const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});return r.json()}
async function load(){
  const c = await (await fetch('/api/config')).json();
  const d = c.defaults||{};
  document.getElementById('dbPath').value=d.db_path||'';
  document.getElementById('mServer').value=d.server||'';
  document.getElementById('mPort').value=d.port||1433;
  document.getElementById('mDb').value=d.database||'';
  document.getElementById('mUser').value=d.user||'';
  document.getElementById('mPwd').value=d.password||'';
  document.getElementById('mDriver').value=d.driver||'';
  document.getElementById('host').value=(c.conf&&c.conf.host)||'127.0.0.1';
  document.getElementById('port').value=(c.conf&&c.conf.port)||8795;
  document.getElementById('cur').textContent='后端 '+((c.conf&&c.conf.db_backend)||'sqlite');
  const box=document.getElementById('mods'); box.innerHTML='';
  (c.modules||[]).forEach(m=>{
    const d=document.createElement('div'); d.className='mod'+(m.core?' core':'');
    d.innerHTML='<label><input type="checkbox" value="'+m.code+'"'+(m.enabled?' checked':'')+(m.core?' disabled':'')+'> '+m.name+(m.core?' 🔒':'')+'</label>';
    d.title=m.name+' · '+m.desc;
    d.onmouseenter=()=>showInfo(m);
    d.onmouseleave=()=>resetInfo();
    box.appendChild(d);
  });
  resetInfo();
  await runEnv();
  if(c.modules_error) log('模块清单读取失败：'+c.modules_error+'（请先完成 SQLite 数据库部署）');
  syncType();
}
async function runDb(){
  log('执行中…');
  const t=sel();
  const b={type:t, db_path:document.getElementById('dbPath').value,
    port:document.getElementById('port').value};
  if(t==='mssql'){b.server=document.getElementById('mServer').value;
    b.db_port=document.getElementById('mPort').value;b.database=document.getElementById('mDb').value;
    b.user=document.getElementById('mUser').value;b.password=document.getElementById('mPwd').value;
    b.driver=document.getElementById('mDriver').value;}
  const r=await post('/api/db',b); log((r.ok?'✅ ':'❌ ')+r.msg); load();
}
async function runServer(mode){
  log('执行中…');
  const r=await post('/api/server',{host:document.getElementById('host').value,
    port:document.getElementById('port').value,mode:mode});
  log((r.ok?'✅ ':'❌ ')+r.msg); load();
}
async function preset(p){log('执行中…');const r=await post('/api/modules',{preset:p});log((r.ok?'✅ ':'❌ ')+r.msg);load();}
async function applyMods(){
  log('执行中…');
  const codes=[...document.querySelectorAll('#mods input:checked')].map(i=>i.value);
  const r=await post('/api/modules',{codes:codes}); log((r.ok?'✅ ':'❌ ')+r.msg); load();
}
async function quit(){await post('/api/quit',{});log('已关闭，可关掉浏览器窗口。');}
load();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/config":
            try:
                self._json(build_config())
            except Exception as exc:
                self._json({"conf": {}, "modules": [], "modules_error": str(exc),
                            "defaults": {}})
        else:
            self.send_error(404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8") if n else "{}"
        try:
            data = json.loads(raw)
        except Exception:
            data = {}
        try:
            if self.path == "/api/detect":
                self._json(do_detect(data))
            elif self.path == "/api/db":
                self._json(do_db(data))
            elif self.path == "/api/server":
                self._json(do_server(data))
            elif self.path == "/api/modules":
                self._json(do_modules(data))
            elif self.path == "/api/quit":
                self._json({"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self._json({"ok": False, "msg": "未知接口"}, 404)
        except Exception as exc:
            self._json({"ok": False, "msg": f"{type(exc).__name__}: {exc}"})

    def log_message(self, fmt, *args):
        pass


def _free_port(start: int = 8796) -> int:
    for p in range(start, start + 12):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return start


def main() -> int:
    os.makedirs(os.path.join(DEPLOY_DIR, "logs"), exist_ok=True)
    port = _free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print("=" * 60)
    print(" Felix 企业管理平台 · 服务器部署辅助工具（图形窗口版）")
    print(f" 界面地址：{url}")
    print(" 已尝试自动打开浏览器；若没弹出，请手动访问上面的地址。")
    print(" 用完请点页面里的「关闭部署助手」。")
    print("=" * 60)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    print("部署助手已退出。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
