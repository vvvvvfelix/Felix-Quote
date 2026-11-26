"""模块层 — 审批引擎 API（M005）：流程定义管理、发起、待办/已办/我发起、任务操作、
审批中心查询，以及「请假」端到端示例业务单。

审批引擎是「每天在做的事」的骨架，早于所有审批类单据（请假/报销/用章/采购/报价）。
本模块只做 HTTP 编排，流程流转逻辑全部在 core/workflow.py。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_user
from ..core import workflow as WF
from ..core import permissions as P
from ..core.db import get_conn, jdump, jload, now, release_conn
from ..core.services import audit, next_no
from ..core.paths import uuid_like

router = APIRouter(prefix="/api/workflow", tags=["workflow"])

# ==========================================================================
# 流程定义管理（workflow.manage）
# ==========================================================================
class DefIn(BaseModel):
    code: str
    name: str
    description: str | None = None
    definition: dict


class DefUpdate(BaseModel):
    """更新流程定义：名称/说明可选，definition 给了就整份替换。"""
    name: str | None = None
    description: str | None = None
    definition: dict | None = None
    is_active: bool | None = None


@router.get("/defs")
def list_defs(u: dict = Depends(current_user)):
    """流程定义清单（前端发起时下拉）。"""
    P.require(u, "workflow.view")
    db = get_conn()
    # 带上 definition：PUT 是「整份替换」，调用方必须先读到当前定义才能改，
    # 否则只能盲改（旧实现只取常量列，definition 恒为 {}，PUT 无从下手）。
    rows = [dict(r) for r in db.execute(
        "SELECT id,code,name,description,definition,is_active,created_at,updated_at "
        "FROM workflow_defs ORDER BY id")]
    for r in rows:
        r["definition"] = jload(r.get("definition") or "{}", {})
    release_conn()
    return rows


@router.post("/defs")
def create_def(body: DefIn, u: dict = Depends(current_user)):
    """新建流程定义。

    成环的定义会让引擎永远推进不到终点（历史上曾据此把整个服务拖成无响应），
    因此保存前必须过 WF.validate_definition 这道闸门。
    """
    P.require(u, "workflow.manage")
    definition = body.definition
    WF.validate_definition(definition)
    db = get_conn()
    if db.execute("SELECT 1 FROM workflow_defs WHERE code=?", (body.code,)).fetchone():
        release_conn()
        raise HTTPException(400, "流程编码已存在")
    db.execute(
        "INSERT INTO workflow_defs(code,name,description,definition,is_active,"
        "created_at,updated_at) VALUES(?,?,?,?,1,?,?)",
        (body.code, body.name, body.description, jdump(definition), now(), now()))
    db.commit()
    release_conn()
    audit(u, "workflow", "create", "workflow_def", body.code, body.name,
          after=body.model_dump())
    return {"ok": True, "code": body.code}


@router.put("/defs/{def_id}")
def update_def(def_id: int, body: DefUpdate, u: dict = Depends(current_user)):
    """更新流程定义（改名字/说明/启用状态，或整份替换 definition）。

    替换 definition 时同样过环路校验 —— 只拦新建不拦修改的话，
    管理员在页面上把终点连回起点一样能把流程改成死循环。
    """
    P.require(u, "workflow.manage")
    if body.definition is not None:
        WF.validate_definition(body.definition)
    db = get_conn()
    d = db.execute("SELECT * FROM workflow_defs WHERE id=?", (def_id,)).fetchone()
    if not d:
        release_conn()
        raise HTTPException(404, "流程定义不存在")
    name = body.name if body.name is not None else d["name"]
    desc = body.description if body.description is not None else d["description"]
    is_active = 1 if body.is_active is None else (1 if body.is_active else 0)
    definition = jdump(body.definition) if body.definition is not None else d["definition"]
    db.execute("UPDATE workflow_defs SET name=?,description=?,definition=?,"
               "is_active=?,updated_at=? WHERE id=?",
               (name, desc, definition, is_active, now(), def_id))
    db.commit()
    release_conn()
    audit(u, "workflow", "update", "workflow_def", str(def_id), name,
          before=dict(d), after=body.model_dump(exclude_none=True))
    return {"ok": True, "id": def_id}


# ==========================================================================
# 发起实例
# ==========================================================================
class StartIn(BaseModel):
    def_id: int
    title: str | None = None
    form: dict | None = None
    biz_type: str | None = None
    biz_id: int | None = None


@router.post("/start")
def start_instance(body: StartIn, u: dict = Depends(current_user)):
    """从流程定义发起实例。申请人 = 当前用户。"""
    P.require(u, "workflow.approve")
    inst = WF.start_instance(body.def_id, body.title or "未命名流程",
                             u["id"], body.form, body.biz_type, body.biz_id)
    return inst


@router.get("/instances")
def list_instances(status: str | None = None, mine: int = 0,
                  biz_type: str | None = None, u: dict = Depends(current_user)):
    """流程实例列表。mine=1 只看我发起的。"""
    P.require(u, "workflow.view")
    filters: dict = {}
    if status:
        filters["status"] = status
    if mine:
        filters["applicant_id"] = u["id"]
    if biz_type:
        filters["biz_type"] = biz_type
    return WF.list_instances(filters)


@router.get("/instances/{iid}")
def instance_detail(iid: int, u: dict = Depends(current_user)):
    P.require(u, "workflow.view")
    inst = WF.instance_detail(iid)
    if not inst:
        raise HTTPException(404, "流程实例不存在")
    return inst


# ==========================================================================
# 审批中心：待办 / 已办 / 我发起的
# ==========================================================================
@router.get("/my-tasks")
def my_tasks(u: dict = Depends(current_user)):
    """待我审批 —— 审批中心首页主视图。"""
    P.require(u, "workflow.approve")
    return WF.list_my_tasks(u["id"])


@router.get("/my-done")
def my_done(u: dict = Depends(current_user)):
    """我已处理。"""
    P.require(u, "workflow.approve")
    return WF.list_my_done(u["id"])


@router.get("/my-started")
def my_started(u: dict = Depends(current_user)):
    """我发起的。"""
    P.require(u, "workflow.approve")
    return WF.list_my_started(u["id"])


# ==========================================================================
# 任务操作：批准 / 驳回 / 转办 / 加签
# ==========================================================================
class TaskAction(BaseModel):
    comment: str | None = None


class TransferIn(BaseModel):
    target_user_id: int
    comment: str | None = None


@router.post("/tasks/{task_id}/approve")
def approve_task(task_id: int, body: TaskAction, u: dict = Depends(current_user)):
    P.require(u, "workflow.approve")
    return WF.approve_task(task_id, u["id"], body.comment or "")


@router.post("/tasks/{task_id}/reject")
def reject_task(task_id: int, body: TaskAction, u: dict = Depends(current_user)):
    P.require(u, "workflow.approve")
    return WF.reject_task(task_id, u["id"], body.comment or "")


@router.post("/tasks/{task_id}/transfer")
def transfer_task(task_id: int, body: TransferIn, u: dict = Depends(current_user)):
    P.require(u, "workflow.approve")
    return WF.transfer_task(task_id, u["id"], body.target_user_id, body.comment or "")


@router.post("/tasks/{task_id}/add-sign")
def add_sign(task_id: int, body: TransferIn, u: dict = Depends(current_user)):
    P.require(u, "workflow.approve")
    return WF.add_sign(task_id, u["id"], body.target_user_id, body.comment or "")


@router.post("/instances/{iid}/cancel")
def cancel_instance(iid: int, body: TaskAction, u: dict = Depends(current_user)):
    P.require(u, "workflow.approve")
    return WF.cancel_instance(iid, u["id"], body.comment or "")


# ==========================================================================
# 回调补偿（业务回调失败时可人工重试）
# ==========================================================================
@router.get("/callback-failures")
def callback_failures(limit: int = 100, u: dict = Depends(current_user)):
    """列出回调失败的实例 —— 「审批已通过但单据没动」这类故障的排查入口。"""
    P.require(u, "workflow.manage")
    return WF.list_callback_failures(limit)


@router.post("/instances/{iid}/retry-callback")
def retry_callback(iid: int, u: dict = Depends(current_user)):
    """重跑一次业务回调。业务回调必须幂等，重试不会把单据改坏。"""
    P.require(u, "workflow.manage")
    return WF.retry_callback(iid)


# ==========================================================================
# 示例业务单：请假申请（端到端演示 提交 → 审批 → 回调改状态）
# ==========================================================================
LEAVE_TYPES = {
    "sick": "病假", "annual": "年假", "personal": "事假",
    "marriage": "婚假", "maternity": "产假", "bereavement": "丧假",
}


class LeaveIn(BaseModel):
    leave_type: str = "personal"
    days: float = 1.0
    reason: str | None = None
    start_date: str | None = None


def _leave_callback(biz_id: int | None, decision: str, inst_code: str) -> None:
    """流程结束后回写请假单状态：通过→approved，驳回/作废→rejected/canceled。

    幂等：已是目标状态就直接跳过（人工重试时不再重复写库）；单据不存在则抛错，
    交给引擎记 callback_error，不静默吞掉。
    """
    if biz_id is None:
        return
    target = {"approved": "approved", "rejected": "rejected",
              "canceled": "canceled"}.get(decision, "pending")
    db = get_conn()
    r = db.execute("SELECT status FROM leave_requests WHERE id=?", (biz_id,)).fetchone()
    if not r:
        release_conn()
        raise ValueError(f"请假单 #{biz_id} 不存在，审批回调无法回写状态")
    if r["status"] == target:
        release_conn()
        return
    db.execute("UPDATE leave_requests SET status=? WHERE id=?", (target, biz_id))
    db.commit()
    release_conn()


# 业务模块在加载时注册回调，确保实例结束时能回写请假单
WF.register_callback("leave", _leave_callback)


@router.get("/leave/types")
def leave_types(u: dict = Depends(current_user)):
    P.require(u, "workflow.view")
    return [{"value": k, "label": v} for k, v in LEAVE_TYPES.items()]


@router.get("/leave")
def list_leave(u: dict = Depends(current_user)):
    """我的请假单（含审批状态）。"""
    P.require(u, "workflow.view")
    db = get_conn()
    rows = [dict(r) for r in db.execute(
        "SELECT l.*, w.code AS wf_code, w.status AS wf_status "
        "FROM leave_requests l LEFT JOIN workflow_instances w "
        "ON w.biz_type='leave' AND w.biz_id=l.id "
        "WHERE l.applicant_id=? ORDER BY l.id DESC", (u["id"],))]
    release_conn()
    return rows


@router.post("/leave")
def create_leave(body: LeaveIn, u: dict = Depends(current_user)):
    """提交请假申请：建请假单 + 发起审批流程（自动回调改状态）。"""
    P.require(u, "workflow.view")
    if body.leave_type not in LEAVE_TYPES:
        raise HTTPException(400, f"未知请假类型 {body.leave_type}")
    if body.days is None or float(body.days) <= 0:
        raise HTTPException(400, "请假天数必须大于 0")
    db = get_conn()
    code = next_no("leave")
    cur = db.execute(
        "INSERT INTO leave_requests(code,applicant_id,leave_type,days,reason,"
        "start_date,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (code, u["id"], body.leave_type, float(body.days), body.reason,
         body.start_date, "pending", now()))
    lid = cur.lastrowid
    db.commit()
    release_conn()
    # 找出请假流程定义（seed 注入，code=LEAVE）
    db = get_conn()
    d = db.execute("SELECT id FROM workflow_defs WHERE code='LEAVE' AND is_active=1"
                   ).fetchone()
    release_conn()
    if not d:
        raise HTTPException(500, "请假审批流程未配置，请联系管理员")
    form = {"leave_type": body.leave_type, "days": float(body.days),
            "reason": body.reason or "", "start_date": body.start_date or ""}
    title = f"{LEAVE_TYPES[body.leave_type]}申请 · {float(body.days)}天"
    inst = WF.start_instance(d["id"], title, u["id"], form, "leave", lid)
    audit(u, "workflow", "submit", "leave_request", lid, title,
          after={"days": body.days, "type": body.leave_type})
    return {"ok": True, "leave_id": lid, "code": code, "instance_id": inst["id"]}


# ==========================================================================
# 种子：请假审批流程定义（幂等，按 code upsert）
# ==========================================================================
LEAVE_DEFINITION = {
    "nodes": [
        {"id": "start", "type": "start"},
        {"id": "mgr", "type": "approval", "name": "部门主管审批", "mode": "or",
         "assignee": {"role": "manager"}},
        {"id": "hr", "type": "approval", "name": "人事审批", "mode": "or",
         "assignee": {"role": "hr"}},
        {"id": "end", "type": "end"},
    ],
    "edges": [
        {"from": "start", "to": "mgr"},
        {"from": "mgr", "to": "hr", "condition": "days>=3"},
        {"from": "mgr", "to": "end"},
        {"from": "hr", "to": "end"},
    ],
}


def seed_workflows() -> dict:
    """灌审批引擎示例流程（请假审批），幂等。返回条数供启动日志核对。"""
    # 启动自检：流程定义是代码里的常量，成环会在启动时就暴露，
    # 不会等到某个用户点「提交」才发现整个服务被拖死。
    WF.validate_definition(LEAVE_DEFINITION)
    db = get_conn()
    if db.execute("SELECT 1 FROM workflow_defs WHERE code='LEAVE'").fetchone():
        db.execute("UPDATE workflow_defs SET definition=?,is_active=1,updated_at=? "
                   "WHERE code='LEAVE'",
                   (jdump(LEAVE_DEFINITION), now()))
    else:
        db.execute(
            "INSERT INTO workflow_defs(code,name,description,definition,is_active,"
            "created_at,updated_at) VALUES(?,?,?,?,1,?,?)",
            ("LEAVE", "请假审批", "员工请假：部门主管审批；≥3 天需人事加签审批",
             jdump(LEAVE_DEFINITION), now(), now()))
    db.commit()
    release_conn()
    return {"workflow_defs": 1}
