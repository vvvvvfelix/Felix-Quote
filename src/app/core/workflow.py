"""审批引擎 —— 流程定义 → 实例 → 任务 → 回调。

为什么单独成模块（刻意不塞进某个业务模块）：请假/报销/用章/采购/报价……所有
「申请→审批」类单据迟早都要走流程，若各自写审批逻辑必然腐烂。统一引擎后，业务
单据只负责「提交表单 + 注册回调」，流程怎么走、谁批、怎么签全部交给本模块。

模型（对标 Frappe/ERPNext 的 DocType 状态流，但更轻）：
- 流程定义 workflow_defs.definition 是一份 JSON：
    {
      "nodes": [
        {"id":"start","type":"start"},
        {"id":"mgr","type":"approval","name":"部门主管","mode":"or",
         "assignee":{"role":"manager"}},
        {"id":"hr","type":"approval","name":"人事","mode":"and",
         "assignee":{"role":"hr"}},
        {"id":"end","type":"end"}
      ],
      "edges":[
        {"from":"start","to":"mgr"},
        {"from":"mgr","to":"hr","condition":"days>=3"},
        {"from":"mgr","to":"end"},
        {"from":"hr","to":"end"}
      ]
    }
  node.type: start（起点）/ approval（审批）/ end（终点）
  node.mode:  or 或签（任一批准即通过） / and 会签（全部批准才通过）
  node.assignee: {"role":"x"} 按角色解析审批人 / {"user_id":N} 指定人 /
                 {"user_ids":[...]} 指定多人
  edge.condition: 用表单字段求值（如 "days>=3"），无 condition 的视为默认/fallback 边

- 审批节点完成判定（按 mode）：
    and 会签：所有「有效任务」都 approved → 节点通过；任一 rejected → 节点驳回
    or  或签：任一 approved → 节点通过；全部 rejected（无 pending）→ 节点驳回
  「有效任务」= status ∈ (pending/approved/rejected)，排除 transferred/cancelled。

- 操作：
    approve  批准（仅当前 assignee）
    reject   驳回（仅当前 assignee）→ 节点驳回 → 实例驳回
    transfer 转办（把当前任务交给他人，旧任务转 transferred、新任务 pending）
    add_sign 加签（在当前节点增加一个必签人，新任务 pending，归到该节点有效任务集）
    cancel   撤回/作废（申请人或管理员）

- 条件分支：advance 时按表单字段求值选边；无命中边则取无 condition 的 fallback。

- 回调：实例走到 end 时调用注册在 CALLBACKS[biz_type] 的回调，由业务模块注册
  （如请假单审批通过 → 更新 leave_requests.status）。

安全说明：condition 表达式来自管理员维护的流程定义，非终端用户输入，故用受限
eval（清空 __builtins__，命名空间仅表单字段）；即便如此，表达式只在启动时由
管理员写入，运行时不可被普通用户注入。
"""
import functools

from fastapi import HTTPException

from .db import get_conn, jdump, jload, now, release_conn
from .services import _log_exc, audit, log_warn, next_no


# ==========================================================================
# 故障隔离：任何引擎操作炸了，都必须先把连接收拾干净再抛出去
# ==========================================================================
# 为什么需要：SQLite 连接在事务未提交时持有库级锁。异常路径若直接抛出而不
# 回滚，锁会随线程级长连接一直留存，后续请求全部阻塞在数据库上 —— 一个成环
# 的流程定义就能让整个 ERP 对外无响应，必须重启进程（QA 实测）。
def _safe_release() -> None:
    """尽最大努力回滚并归还连接；回收过程自身绝不再抛异常。"""
    try:
        db = get_conn()
        if db.in_transaction:
            db.rollback()
    except Exception:
        pass
    try:
        release_conn()
    except Exception:
        pass


def _guarded(fn):
    """包住引擎对外操作：无论怎么炸，连接都被干净归还，故障隔离在单请求内。"""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            _safe_release()
            raise

    return wrapper


# ==========================================================================
# 回调注册中心：业务模块在 import 时注册「实例结束时如何回写业务单」
# ==========================================================================
CALLBACKS: dict[str, callable] = {}


def register_callback(biz_type: str, fn: callable) -> None:
    """业务模块（如请假）在加载时调用，注册实例结束后的回写逻辑。"""
    CALLBACKS[biz_type] = fn


# ==========================================================================
# 内部工具
# ==========================================================================
def _node_by_id(definition: dict, node_id: str) -> dict | None:
    for n in definition.get("nodes", []):
        if n["id"] == node_id:
            return n
    return None


def _outgoing(definition: dict, node_id: str) -> list:
    return [e for e in definition.get("edges", []) if e.get("from") == node_id]


def _resolve_assignees(db, assignee: dict | None, applicant_id: int | None) -> list[int]:
    """把节点 assignee 配置解析成一组审批人 user_id。"""
    if not assignee:
        return []
    if "user_id" in assignee:
        return [int(assignee["user_id"])]
    if "user_ids" in assignee:
        return [int(x) for x in assignee["user_ids"] if x]
    if "role" in assignee:
        role = assignee["role"]
        rows = db.execute(
            "SELECT id FROM users WHERE role=? AND is_active=1", (role,)).fetchall()
        return [r["id"] for r in rows]
    if "applicant_manager" in assignee and applicant_id:
        # 申请人的直属主管：取其主岗汇报线上的上级 position_assignments
        # 简单实现——先占位，复杂组织下按需扩展（避免把引擎耦合进 position 模块）
        return []
    return []


def _eval_condition(expr: str | None, form: dict) -> bool:
    """求流程边的条件表达式。命名空间仅表单字段，__builtins__ 清空。"""
    if not expr:
        return True
    try:
        ns = dict(form or {})
        return bool(eval(expr, {"__builtins__": {}}, ns))  # noqa: S307 定义来源可信
    except Exception:
        return False


# ==========================================================================
# 启动实例
# ==========================================================================
@_guarded
def start_instance(def_id: int, title: str, applicant_id: int,
                   form: dict | None = None, biz_type: str | None = None,
                   biz_id: int | None = None) -> dict:
    """从流程定义发起一个实例。返回实例 dict。"""
    db = get_conn()
    d = db.execute("SELECT * FROM workflow_defs WHERE id=?", (def_id,)).fetchone()
    if not d or not d["is_active"]:
        release_conn()
        raise HTTPException(400, "流程定义不存在或未启用")
    definition = jload(d["definition"], {})
    if not _node_by_id(definition, "start"):
        release_conn()
        raise HTTPException(400, "流程定义缺少 start 节点")
    # 运行时二次拦截：历史脏数据（本闸门上线前存进库的成环定义）在这里被挡住，
    # 宁可拒绝发起，也不能让引擎去跑一条走不到终点的流程。
    cyc = find_cycle(definition)
    if cyc:
        release_conn()
        raise _cycle_error(cyc)
    code = next_no("workflow")
    ts = now()
    cur = db.execute(
        "INSERT INTO workflow_instances(def_id,code,title,biz_type,biz_id,status,"
        "current_node,form_json,applicant_id,started_at,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (def_id, code, title, biz_type, biz_id, "running", "start",
         jdump(form or {}), applicant_id, ts, ts, ts))
    iid = cur.lastrowid
    db.commit()
    inst = db.execute("SELECT * FROM workflow_instances WHERE id=?", (iid,)).fetchone()
    _log(db, iid, "start", "start", applicant_id, "发起流程")
    db.commit()
    release_conn()
    # 从 start 节点推进（start 无审批人，直接沿边走到第一个审批/终点）
    _advance(iid, "start", form or {})
    # 推进后必须重读：_advance 可能已经把流程走到终点（无人审批的定义直接
    # approved）或被环路保护作废（canceled），返回推进前的快照会骗调用方。
    db = get_conn()
    fresh = db.execute("SELECT * FROM workflow_instances WHERE id=?",
                       (iid,)).fetchone()
    release_conn()
    return dict(fresh) if fresh else dict(inst)


# ==========================================================================
# 环路防护
# ==========================================================================
# 单次推进允许经过的最大节点数。正常流程的节点数远小于此（请假 4 个、报销 5 个），
# 取 64 既能容纳任意合理流程，又能在成环时早早收手。
_MAX_ADVANCE_DEPTH = 64


def _abort_loop(instance_id: int, node_id: str) -> None:
    """检测到环路：终止推进、作废实例并留痕，绝不让请求挂死。

    实例被置为 canceled 而不是卡在 running —— 卡住意味着「这张单永远审批不完
    也没人知道为什么」，作废至少让发起人和财务看到结果并重新提交。
    """
    audit(None, "workflow", "cycle_abort", "workflow_instance", instance_id,
          f"流程定义在节点 {node_id} 处检测到环路，已终止推进并作废该实例")
    db = get_conn()
    db.execute("UPDATE workflow_instances SET status='canceled',finished_at=?,"
               "updated_at=? WHERE id=? AND status='running'",
               (now(), now(), instance_id))
    db.execute("UPDATE workflow_tasks SET status='cancelled',updated_at=? "
               "WHERE instance_id=? AND status='pending'", (now(), instance_id))
    _log(db, instance_id, node_id, "cycle_abort", None,
         "检测到环路，流程已终止并作废")
    db.commit()
    release_conn()


def find_cycle(definition: dict) -> list | None:
    """从各节点出发做迭代式 DFS 找环路，返回环路径（如 ['a','b','a']），无环返回 None。

    用显式栈而不是递归：流程定义由人工维护，节点数理论上没有上限，
    递归写法在长链上会先把自己栈溢出，那防护就成了新的故障源。
    """
    adj: dict = {}
    for e in definition.get("edges", []):
        src, dst = e.get("from"), e.get("to")
        if src is not None and dst is not None:
            adj.setdefault(src, []).append(dst)

    GREY, BLACK = 1, 2          # GREY=在栈上（当前路径），BLACK=已探索完毕
    color: dict = {}
    for n in definition.get("nodes", []):
        root = n.get("id")
        if root is None or color.get(root):
            continue
        color[root] = GREY
        stack = [(root, iter(adj.get(root, [])))]
        path = [root]
        while stack:
            node, it = stack[-1]
            descended = False
            for nxt in it:
                c = color.get(nxt, 0)
                if c == GREY:
                    # 回边：nxt 还在当前路径上，构成环
                    i = path.index(nxt)
                    return path[i:] + [nxt]
                if c == 0:
                    color[nxt] = GREY
                    path.append(nxt)
                    stack.append((nxt, iter(adj.get(nxt, []))))
                    descended = True
                    break
            if not descended:
                color[node] = BLACK
                stack.pop()
                path.pop()
    return None


def _cycle_error(cyc: list) -> HTTPException:
    """把环路径翻译成用户看得懂的 400（保存入口与发起入口共用同一措辞）。"""
    return HTTPException(
        400, "流程定义存在环路：" + " → ".join(str(c) for c in cyc)
             + "。环路会让流程永远走不到终点，请断开其中一条连线后重试")


def validate_definition(definition: dict) -> None:
    """保存流程定义前的合法性校验（成环会使整个服务不可用，必须拦在入口）。

    三道防线的第一道：脏数据一旦入库，之后每个用它的请求都可能触发引擎侧
    的环路保护，逐个请求救火远不如入口直接拒绝。
    """
    nodes = definition.get("nodes") or []
    if not nodes:
        raise HTTPException(400, "definition 必须包含 nodes")
    if not _node_by_id(definition, "start"):
        raise HTTPException(400, "流程定义必须包含 id=start 的起始节点")
    # 节点 type 校验：start/approval/end 之外的类型（或缺失）会让 _activate_node
    # 取 node["type"] 时 KeyError 抛 500。把它拦在保存期，畸形定义连库都进不去，
    # 起单时就不会再炸（缺陷①）。
    _VALID_NODE_TYPES = {"start", "approval", "end"}
    for n in nodes:
        nid = n.get("id")
        t = n.get("type")
        if t not in _VALID_NODE_TYPES:
            raise HTTPException(
                400, f"节点 {nid!r} 的 type 缺失或非法（合法值：start / approval / end）。"
                     f"缺 type 的畸形定义发起时会在引擎内 KeyError 崩溃，故拒绝保存")
    # 指向不存在节点的边同样会让流程走进死胡同，一并拦掉
    ids = {n.get("id") for n in nodes}
    for e in definition.get("edges", []):
        if e.get("from") not in ids or e.get("to") not in ids:
            raise HTTPException(
                400, f"流程定义的边 {e.get('from')}→{e.get('to')} "
                     f"指向了不存在的节点（现有节点：{', '.join(sorted(str(i) for i in ids))}）")
    cyc = find_cycle(definition)
    if cyc:
        raise _cycle_error(cyc)


def audit_definition_health() -> list:
    """启动期体检：检查所有启用流程定义里「按角色配审批人」的节点是否还有在职用户。

    动机（PM 建议做成启动期自检，而非 M113 清洗时的一次性校验项）：
    流程节点按 role 配审批人；若该角色当前没有在职用户，实例发起后该节点会走
    「无人可审 → 自动通过」，结果就是三档审批全部秒过、界面一切正常、审批流形同虚设
    —— 这种失效最不容易被察觉。把它写成启动期自检，比一次性清单能防住每一次
    种子或角色库调整可能再次触发同样的静默失效。

    刻意只写日志不报错：岗位空缺是合理业务状态，硬拦会卡死流程。目标是「让人看见」，
    不是「替人决定」。日志写 service.log（不写 audit_logs —— 健康信号不是操作留痕，
    写进去会淹没真正的审计；也不写 _log_exc —— 那是为异常栈准备的）。

    返回告警文本列表（空列表 = 健康）。覆盖所有流程（请假/报销/未来用章采购），
    不局限于报销。
    """
    db = get_conn()
    rows = db.execute(
        "SELECT id,code,name,definition FROM workflow_defs WHERE is_active=1"
    ).fetchall()
    warns: list[str] = []
    for r in rows:
        definition = jload(r["definition"], {})
        for n in definition.get("nodes", []):
            if n.get("type") != "approval":
                continue
            assignee = n.get("assignee") or {}
            role = assignee.get("role")
            if not role:
                continue
            cnt = db.execute(
                "SELECT COUNT(*) c FROM users WHERE role=? AND is_active=1", (role,)
            ).fetchone()["c"]
            if cnt == 0:
                msg = (f"流程定义「{r['code']}」节点「{n.get('name') or n['id']}」"
                       f"按角色 {role} 配审批人，但当前无在职用户拥有该角色"
                       f"——该节点将无人可审、实例发起后此节点自动通过（流程形同虚设）")
                log_warn("workflow.health", msg)
                warns.append(msg)
    release_conn()
    return warns


# ==========================================================================
# 节点推进 / 激活
# ==========================================================================
def _advance(instance_id: int, from_node_id: str, form: dict,
             visited: set | None = None, depth: int = 0) -> None:
    """从 from_node_id 选边推进，直到进入审批节点 / 走到终点 / 被环路保护终止。

    三道防线的第二道（第一道在保存流程定义时拒绝成环，见 validate_definition）：

    1. **用循环代替递归**（本函数早期版本在第 200 行尾递归 _advance()）。
       自环边 a→a 会让递归永无止境，栈溢出后该连接上的事务未回滚，
       QA 实测结果是「发起一次成环流程 → 之后连 /api/about 都 15s 超时」，
       即单个请求把整个进程拖成无响应，必须重启。改成循环后，
       即使定义成环也只会在有限步内终止，不再消耗栈、不再卡住进程。

    2. **visited 集合**：重复踏入同一节点立即终止。自环与长环都覆盖。

    3. **深度上限**：兜住「环路只经过互不相同的超长节点链」这类极端情况。

    visited / depth 会一路传给 _activate_node（它可能对无审批人的节点再次
    调用 _advance），否则两者构成**互递归**，光把本函数改成循环仍会死循环。
    """
    if visited is None:
        visited = set()
    if depth > _MAX_ADVANCE_DEPTH:
        _abort_loop(instance_id, from_node_id)
        return
    cur = from_node_id
    steps = 0
    while True:
        if cur in visited:
            release_conn()
            _abort_loop(instance_id, cur)
            return
        visited.add(cur)
        steps += 1
        if steps > _MAX_ADVANCE_DEPTH:
            release_conn()
            _abort_loop(instance_id, cur)
            return
        db = get_conn()
        inst = db.execute("SELECT * FROM workflow_instances WHERE id=?",
                          (instance_id,)).fetchone()
        if not inst or inst["status"] != "running":
            release_conn()
            return
        definition = jload(db.execute(
            "SELECT definition FROM workflow_defs WHERE id=?", (inst["def_id"],)
        ).fetchone()["definition"], {})
        edges = _outgoing(definition, cur)
        chosen = None
        # 优先取「条件命中」的边；条件为空（默认边）放最后兜底
        for e in edges:
            if e.get("condition") is not None and _eval_condition(e["condition"], form):
                chosen = e
                break
        if chosen is None:
            for e in edges:
                if e.get("condition") is None:
                    chosen = e
                    break
        if chosen is None:
            # 无可用出边：视为自动结束为通过（避免流程卡死）
            release_conn()
            _finish(instance_id, "approved")
            return
        target = chosen["to"]
        node = _node_by_id(definition, target)
        if not node or node["type"] == "end":
            release_conn()
            _finish(instance_id, "approved")
            return
        if node["type"] == "approval":
            _activate_node(instance_id, node, form, inst["applicant_id"],
                           visited, depth)
            # 更新实例当前节点
            db = get_conn()
            db.execute("UPDATE workflow_instances SET current_node=?,updated_at=? "
                       "WHERE id=?", (target, now(), instance_id))
            db.commit()
            release_conn()
            return
        # 其它类型节点（start 被当作 target 等）：不递归，下一轮循环继续推进
        release_conn()
        cur = target


def _activate_node(instance_id: int, node: dict, form: dict,
                   applicant_id: int | None, visited: set | None = None,
                   depth: int = 0) -> None:
    """激活一个审批节点：解析审批人并生成任务（会签每人一条 / 或签每人一条）。

    visited / depth 承接自 _advance，用于让「无人可审 → 自动通过本节点 →
    继续推进」这条路径也带上环路保护（否则与 _advance 构成互递归）。
    """
    db = get_conn()
    ts = now()
    assignees = _resolve_assignees(db, node.get("assignee"), applicant_id)
    if not assignees:
        # 无人可审 → 自动通过本节点，继续推进
        _log(db, instance_id, node["id"], "auto_pass", None,
             "无可审批人，自动通过")
        # H2 运行期兜底：自动通过原本只写 workflow_logs（没人看），这里再写一条
        # service.log 告警，让「这一档又秒过了」可被运维查到。这是 H1 启动期体检
        # 的互补——H1 在启动时发现「角色无在职用户」，H2 在运行时把每一次实际
        # 发生的自动通过都留下痕迹，二者合力防止「审批流形同虚设却无人察觉」。
        log_warn(
            "workflow.auto_pass",
            f"实例#{instance_id} 节点「{node.get('name') or node['id']}」无人可审，已自动通过"
            f"（assignee={node.get('assignee')}）。该节点不会有人审批，流程形同虚设；"
            f"请检查该角色是否仍有在职用户，或在定义里改用指定审批人。")
        db.commit()
        release_conn()
        _advance(instance_id, node["id"], form, visited, depth + 1)
        return
    for uid in assignees:
        db.execute(
            "INSERT INTO workflow_tasks(instance_id,node_id,node_name,mode,"
            "assignee_id,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (instance_id, node["id"], node.get("name", node["id"]),
             node.get("mode", "or"), uid, "pending", ts, ts))
    db.commit()
    release_conn()


# ==========================================================================
# 节点完成判定
# ==========================================================================
def _check_node_complete(instance_id: int, node_id: str, form: dict) -> str | None:
    """检查某审批节点是否完成，返回 'approved' / 'rejected' / None（未完成）。"""
    db = get_conn()
    node = _node_by_id(
        jload(db.execute(
            "SELECT definition FROM workflow_instances i JOIN workflow_defs d "
            "ON d.id=i.def_id WHERE i.id=?", (instance_id,)).fetchone()["definition"], {}),
        node_id)
    mode = (node or {}).get("mode", "or")
    tasks = db.execute(
        "SELECT status FROM workflow_tasks WHERE instance_id=? AND node_id=? "
        "AND status IN ('pending','approved','rejected')",
        (instance_id, node_id)).fetchall()
    pending = sum(1 for t in tasks if t["status"] == "pending")
    approved = sum(1 for t in tasks if t["status"] == "approved")
    rejected = sum(1 for t in tasks if t["status"] == "rejected")
    if mode == "and":
        if rejected > 0:
            return "rejected"
        if pending == 0 and rejected == 0:
            return "approved"
        return None
    # or 或签：任一人批准即通过；任一人驳回即驳回，其余并行任务随之作废。
    # 旧实现写成「全员驳回才算驳回」（pending==0 and approved==0），本质是「与」语义，
    # 与或签矛盾：多人或签时一人驳回、其余未处理会把实例永久卡在 running。
    if approved > 0:
        return "approved"
    if rejected > 0:
        return "rejected"
    if pending == 0:
        return "rejected"
    return None


def _after_task_decided(instance_id: int, node_id: str, form: dict) -> None:
    """某任务被决定后，检查节点是否完成，进而推进或驳回实例。"""
    result = _check_node_complete(instance_id, node_id, form)
    if result is None:
        return
    if result == "approved":
        _advance(instance_id, node_id, form)
    else:
        _finish(instance_id, "rejected")


# ==========================================================================
# 任务操作：批准 / 驳回 / 转办 / 加签
# ==========================================================================
def _get_pending_task(db, task_id: int, actor_id: int) -> dict:
    t = db.execute("SELECT * FROM workflow_tasks WHERE id=?", (task_id,)).fetchone()
    if not t:
        raise HTTPException(404, "审批任务不存在")
    if t["status"] != "pending":
        raise HTTPException(400, "该任务已处理，不可重复操作")
    if t["assignee_id"] != actor_id:
        raise HTTPException(403, "无权处理他人的审批任务")
    return dict(t)


@_guarded
def approve_task(task_id: int, actor_id: int, comment: str = "") -> dict:
    db = get_conn()
    t = _get_pending_task(db, task_id, actor_id)
    db.execute("UPDATE workflow_tasks SET status='approved',comment=?,decided_at=?,"
               "updated_at=? WHERE id=?", (comment, now(), now(), task_id))
    _log(db, t["instance_id"], t["node_id"], "approve", actor_id, comment)
    db.commit()
    release_conn()
    _after_task_decided(t["instance_id"], t["node_id"],
                        _instance_form(t["instance_id"]))
    return {"ok": True, "task_id": task_id}


@_guarded
def reject_task(task_id: int, actor_id: int, comment: str = "") -> dict:
    db = get_conn()
    t = _get_pending_task(db, task_id, actor_id)
    db.execute("UPDATE workflow_tasks SET status='rejected',comment=?,decided_at=?,"
               "updated_at=? WHERE id=?", (comment, now(), now(), task_id))
    _log(db, t["instance_id"], t["node_id"], "reject", actor_id, comment)
    db.commit()
    release_conn()
    _after_task_decided(t["instance_id"], t["node_id"],
                        _instance_form(t["instance_id"]))
    return {"ok": True, "task_id": task_id}


@_guarded
def transfer_task(task_id: int, actor_id: int, target_user_id: int,
                  comment: str = "") -> dict:
    """转办：把当前任务交给他人，旧任务转 transferred，新任务 pending。"""
    db = get_conn()
    t = _get_pending_task(db, task_id, actor_id)
    if not db.execute("SELECT 1 FROM users WHERE id=? AND is_active=1",
                      (target_user_id,)).fetchone():
        release_conn()
        raise HTTPException(400, "转办目标用户不存在或已停用")
    db.execute("UPDATE workflow_tasks SET status='transferred',transfer_to=?,"
               "comment=?,decided_at=?,updated_at=? WHERE id=?",
               (target_user_id, "转办：" + (comment or ""), now(), now(), task_id))
    cur = db.execute(
        "INSERT INTO workflow_tasks(instance_id,node_id,node_name,mode,assignee_id,"
        "status,comment,added_by_task_id,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (t["instance_id"], t["node_id"], t["node_name"], t["mode"],
         target_user_id, "pending", "（转办自 #%d）" % task_id, task_id, now(), now()))
    new_id = cur.lastrowid
    _log(db, t["instance_id"], t["node_id"], "transfer", actor_id,
         "转办给 #%d：%s" % (target_user_id, comment))
    db.commit()
    release_conn()
    return {"ok": True, "task_id": task_id, "new_task_id": new_id}


@_guarded
def add_sign(task_id: int, actor_id: int, target_user_id: int,
             comment: str = "") -> dict:
    """加签：在当前审批节点增加一个必签人（新任务 pending）。

    语义：加签人加入该节点的「有效任务集」。会签下其必须也批准；或签下
    因其未批准前 pending>0，节点不会因他人批准而误结（即加签人必须先签）。
    """
    db = get_conn()
    t = _get_pending_task(db, task_id, actor_id)
    if not db.execute("SELECT 1 FROM users WHERE id=? AND is_active=1",
                      (target_user_id,)).fetchone():
        release_conn()
        raise HTTPException(400, "加签目标用户不存在或已停用")
    cur = db.execute(
        "INSERT INTO workflow_tasks(instance_id,node_id,node_name,mode,assignee_id,"
        "status,comment,added_by_task_id,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (t["instance_id"], t["node_id"], t["node_name"], t["mode"],
         target_user_id, "pending", "（加签）" + (comment or ""), task_id,
         now(), now()))
    new_id = cur.lastrowid
    _log(db, t["instance_id"], t["node_id"], "add_sign", actor_id,
         "加签 #%d：%s" % (target_user_id, comment))
    db.commit()
    release_conn()
    return {"ok": True, "task_id": task_id, "new_task_id": new_id}


@_guarded
def cancel_instance(instance_id: int, actor_id: int, comment: str = "") -> dict:
    """撤回/作废流程实例；作废其全部 pending 任务。"""
    db = get_conn()
    inst = db.execute("SELECT * FROM workflow_instances WHERE id=?",
                      (instance_id,)).fetchone()
    if not inst:
        release_conn()
        raise HTTPException(404, "流程实例不存在")
    if inst["status"] != "running":
        release_conn()
        raise HTTPException(400, "流程已结束，不可作废")
    db.execute("UPDATE workflow_instances SET status='canceled',finished_at=?,"
               "updated_at=? WHERE id=?", (now(), now(), instance_id))
    db.execute("UPDATE workflow_tasks SET status='cancelled',updated_at=? "
               "WHERE instance_id=? AND status='pending'",
               (now(), instance_id))
    _log(db, instance_id, None, "cancel", actor_id, comment)
    db.commit()
    release_conn()
    _run_callback(instance_id, "canceled")
    return {"ok": True, "instance_id": instance_id}


# ==========================================================================
# 结束实例 + 回调
# ==========================================================================
def _finish(instance_id: int, decision: str) -> None:
    db = get_conn()
    db.execute("UPDATE workflow_instances SET status=?,finished_at=?,"
               "current_node='end',updated_at=? WHERE id=?",
               (decision, now(), now(), instance_id))
    db.execute("UPDATE workflow_tasks SET status='cancelled',updated_at=? "
               "WHERE instance_id=? AND status='pending'", (now(), instance_id))
    _log(db, instance_id, "end", "finish", None,
         "流程结束：" + ("通过" if decision == "approved" else "驳回"))
    db.commit()
    release_conn()
    _run_callback(instance_id, decision)


def _run_callback(instance_id: int, decision: str) -> bool:
    """调用业务回调，返回是否成功。

    回调失败**不影响实例状态**（审批结果已落库），但绝不能一声不响：
    历史上这里只 catch 不记录，导致「实例已 approved，业务单据却停在草稿」
    这种最难查的故障（表面全对，只有单据状态不对）。现在失败会同时
    ① 进 audit_logs（后台可查）② 写 service.log（运维可查）③ 由本函数
    返回 False，让 retry_callback 有依据做人工补偿重试。
    """
    db = get_conn()
    # 注意：code 必须查出，回调签名 fn(biz_id, decision, inst_code) 会用 inst["code"]，
    # 漏查会让 Row 取列抛 KeyError，被下面的 except 静默吞掉（表现为「回调从不生效」）。
    inst = db.execute("SELECT biz_type,biz_id,code FROM workflow_instances WHERE id=?",
                      (instance_id,)).fetchone()
    release_conn()
    if not inst or not inst["biz_type"]:
        return True
    fn = CALLBACKS.get(inst["biz_type"])
    if not fn:
        return True
    try:
        fn(inst["biz_id"], decision, inst["code"])
        return True
    except Exception as exc:                                   # pragma: no cover
        audit(None, "workflow", "callback_error", "workflow_instance",
              instance_id, str(exc))
        _log_exc(f"workflow.callback#{instance_id}")
        return False


def retry_callback(instance_id: int) -> dict:
    """人工补偿：重跑一次业务回调（回调失败时由管理员/财务重试）。

    业务回调必须写成**幂等**（重复执行结果一致），否则重试会把单据改坏。
    报销回调已按此实现：目标状态一致时直接跳过。
    """
    db = get_conn()
    inst = db.execute("SELECT status,biz_type,biz_id FROM workflow_instances "
                      "WHERE id=?", (instance_id,)).fetchone()
    release_conn()
    if not inst:
        raise HTTPException(404, "流程实例不存在")
    if not inst["biz_type"]:
        raise HTTPException(400, "该实例没有关联业务单据，无需回调")
    decision = {"approved": "approved", "rejected": "rejected",
                "canceled": "canceled"}.get(inst["status"])
    if not decision:
        raise HTTPException(400, f"实例当前状态 {inst['status']} 尚未结束，无需回调")
    ok = _run_callback(instance_id, decision)
    if not ok:
        raise HTTPException(500, "回调执行仍然失败，详情见操作日志")
    return {"ok": True, "instance_id": instance_id, "decision": decision}


def list_callback_failures(limit: int = 100) -> list:
    """列出回调失败的实例（后台可查，配合 retry_callback 做人工补偿）。"""
    db = get_conn()
    rows = [dict(r) for r in db.execute(
        "SELECT id,target_id,target_label,created_at FROM audit_logs "
        "WHERE action='callback_error' ORDER BY id DESC LIMIT ?", (limit,))]
    release_conn()
    for r in rows:
        r["instance_id"] = r.pop("target_id")
        r["error"] = r.pop("target_label")
    return rows


# ==========================================================================
# 查询
# ==========================================================================
def _instance_form(instance_id: int) -> dict:
    db = get_conn()
    r = db.execute("SELECT form_json FROM workflow_instances WHERE id=?",
                   (instance_id,)).fetchone()
    release_conn()
    return jload(r["form_json"], {}) if r else {}


def _log(db, instance_id: int, node_id, action: str, actor_id: int | None,
         comment: str = "") -> None:
    db.execute(
        "INSERT INTO workflow_logs(instance_id,node_id,action,actor_id,comment,"
        "created_at) VALUES(?,?,?,?,?,?)",
        (instance_id, node_id, action, actor_id, comment, now()))


def list_instances(filters: dict | None = None) -> list:
    db = get_conn()
    sql = ("SELECT i.*, d.name AS def_name, u.full_name AS applicant_name "
           "FROM workflow_instances i JOIN workflow_defs d ON d.id=i.def_id "
           "LEFT JOIN users u ON u.id=i.applicant_id WHERE 1=1")
    args: list = []
    f = filters or {}
    if f.get("status"):
        sql += " AND i.status=?"
        args.append(f["status"])
    if f.get("applicant_id"):
        sql += " AND i.applicant_id=?"
        args.append(f["applicant_id"])
    if f.get("biz_type"):
        sql += " AND i.biz_type=?"
        args.append(f["biz_type"])
    sql += " ORDER BY i.id DESC LIMIT ?"
    try:
        limit = int(f.get("limit", 200))
    except (TypeError, ValueError, OverflowError):
        limit = 200
    args.append(min(max(1, limit), 500))
    rows = [dict(r) for r in db.execute(sql, args)]
    release_conn()
    return rows


def list_my_tasks(user_id: int) -> list:
    """待我审批：assignee=我 且 status=pending 且实例运行中。"""
    db = get_conn()
    rows = [dict(r) for r in db.execute(
        "SELECT t.*, i.code AS inst_code, i.title AS inst_title, i.biz_type, "
        "i.biz_id, i.status AS inst_status, u.full_name AS applicant_name "
        "FROM workflow_tasks t JOIN workflow_instances i ON i.id=t.instance_id "
        "LEFT JOIN users u ON u.id=i.applicant_id "
        "WHERE t.assignee_id=? AND t.status='pending' AND i.status='running' "
        "ORDER BY i.started_at, t.id", (user_id,))]
    release_conn()
    return rows


def list_my_done(user_id: int) -> list:
    """我已处理：我决定过的任务（approved/rejected/transferred）。"""
    db = get_conn()
    rows = [dict(r) for r in db.execute(
        "SELECT t.*, i.code AS inst_code, i.title AS inst_title, i.biz_type, "
        "i.status AS inst_status, u.full_name AS applicant_name "
        "FROM workflow_tasks t JOIN workflow_instances i ON i.id=t.instance_id "
        "LEFT JOIN users u ON u.id=i.applicant_id "
        "WHERE t.assignee_id=? AND t.status IN ('approved','rejected','transferred') "
        "ORDER BY t.decided_at DESC LIMIT 200", (user_id,))]
    release_conn()
    return rows


def list_my_started(user_id: int) -> list:
    """我发起的实例。"""
    return list_instances({"applicant_id": user_id})


def instance_detail(instance_id: int) -> dict | None:
    db = get_conn()
    inst = db.execute(
        "SELECT i.*, d.name AS def_name, d.definition AS definition, "
        "u.full_name AS applicant_name FROM workflow_instances i "
        "JOIN workflow_defs d ON d.id=i.def_id "
        "LEFT JOIN users u ON u.id=i.applicant_id WHERE i.id=?",
        (instance_id,)).fetchone()
    if not inst:
        release_conn()
        return None
    tasks = [dict(r) for r in db.execute(
        "SELECT t.*, a.full_name AS assignee_name, b.full_name AS transfer_name "
        "FROM workflow_tasks t LEFT JOIN users a ON a.id=t.assignee_id "
        "LEFT JOIN users b ON b.id=t.transfer_to WHERE t.instance_id=? "
        "ORDER BY t.node_id, t.id", (instance_id,))]
    logs = [dict(r) for r in db.execute(
        "SELECT l.*, u.full_name AS actor_name FROM workflow_logs l "
        "LEFT JOIN users u ON u.id=l.actor_id WHERE l.instance_id=? "
        "ORDER BY l.id", (instance_id,))]
    release_conn()
    out = dict(inst)
    out["definition"] = jload(out["definition"], {})
    out["tasks"] = tasks
    out["logs"] = logs
    return out
