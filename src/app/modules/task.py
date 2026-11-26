"""模块层 — 待办中心。

跨部门协作的枢纽。销售派活给技术/生产/财务，所有人都从这里看自己的活。
轮询而非 WebSocket：内网 10–30 人、待办变化以分钟计，轮询够用且断线重连零成本。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import current_user
from ..core import permissions as P
from ..core.db import get_conn, jload, now, release_conn
from ..core.services import (TASK_LABEL, audit, create_task, log_activity,
                             my_tasks)

router = APIRouter(prefix="/api/tasks", tags=["task"])


def _hydrate(r) -> dict:
    d = dict(r)
    d["task_label"] = TASK_LABEL.get(d["task_type"], d["task_type"])
    d["is_overdue"] = bool(d.get("due_at") and d["due_at"][:10] < now()[:10])
    d["is_today"] = bool(d.get("due_at") and d["due_at"][:10] == now()[:10])
    return d


@router.get("")
def list_tasks(u: dict = Depends(current_user), status: str = "open",
               mine: int = 1, limit: int = 50):
    """我的待办。mine=1 时只看我负责或本部门的。"""
    db = get_conn()
    sql = ("SELECT t.*, d.name AS dept_name FROM tasks t "
           "LEFT JOIN departments d ON d.id=t.dept_id WHERE 1=1")
    args: list = []
    if status:
        sql += " AND t.status=?"
        args.append(status)
    if mine and u.get("role") != "admin":
        sql += " AND (t.assignee_id=? OR t.dept_id=?)"
        args += [u["id"], u.get("dept_id")]
    sql += " ORDER BY CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 "
    sql += "WHEN 'normal' THEN 2 ELSE 3 END, COALESCE(t.due_at,'9999'), t.id DESC LIMIT ?"
    args.append(min(limit, 200))
    rows = [_hydrate(r) for r in db.execute(sql, args)]
    release_conn()
    return rows


@router.get("/board")
def task_board(u: dict = Depends(current_user)):
    """部门任务看板：谁手上有什么活。管理员/经理看全部。"""
    P.require(u, "tasks.view")
    db = get_conn()
    rows = [dict(r) for r in db.execute(
        "SELECT t.*, d.name AS dept_name, us.full_name AS assignee_name "
        "FROM tasks t LEFT JOIN departments d ON d.id=t.dept_id "
        "LEFT JOIN users us ON us.id=t.assignee_id "
        "WHERE t.status='open' ORDER BY "
        "CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 ELSE 2 END, "
        "COALESCE(t.due_at,'9999')")]
    release_conn()
    today = now()[:10]
    groups: dict = {}
    for r in rows:
        d = _hydrate(r)
        key = d.get("dept_name") or "未分配"
        groups.setdefault(key, []).append(d)
    return {
        "groups": [{"dept": k, "items": v,
                    "overdue": sum(1 for x in v if x["is_overdue"])}
                   for k, v in groups.items()],
        "total": len(rows),
        "overdue": sum(1 for r in rows
                       if r.get("due_at") and r["due_at"][:10] < today),
    }


class TaskCreate(BaseModel):
    task_type: str = "followup"
    title: str
    detail: str = ""
    assignee_id: int | None = None
    dept_id: int | None = None
    due_at: str | None = None
    priority: str = "normal"
    biz_type: str | None = None
    biz_id: int | None = None


@router.post("")
def add_task(body: TaskCreate, u: dict = Depends(current_user)):
    P.require(u, "tasks.view")
    tid = create_task(body.biz_type or "manual", body.biz_id or 0, body.task_type,
                      body.title, body.assignee_id, body.dept_id, body.due_at,
                      body.detail, body.priority, u["id"])
    if tid:
        audit(u, "task", "create", "task", tid, body.title, after=body.model_dump())
    return {"ok": True, "id": tid}


class TaskDone(BaseModel):
    result: str = ""


@router.post("/{tid}/done")
def done_task(tid: int, body: TaskDone, u: dict = Depends(current_user)):
    db = get_conn()
    t = db.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    if not t:
        release_conn()
        raise HTTPException(404, "待办不存在")
    if t["assignee_id"] and t["assignee_id"] != u["id"] and u.get("role") not in ("admin", "sales_manager"):
        release_conn()
        raise HTTPException(403, "这条待办不是分配给你的")
    db.execute("UPDATE tasks SET status='done',result=?,done_at=?,done_by=? WHERE id=?",
               (body.result, now(), u["id"], tid))
    db.commit()
    release_conn()
    if t["biz_type"] and t["biz_type"] != "manual":
        log_activity(t["biz_type"], t["biz_id"], "task_done", u,
                     f"{TASK_LABEL.get(t['task_type'], '')}：{body.result or '已完成'}")
    audit(u, "task", "done", "task", tid, t["title"], after={"result": body.result})
    return {"ok": True}


@router.post("/{tid}/cancel")
def cancel_task(tid: int, u: dict = Depends(current_user)):
    db = get_conn()
    t = db.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    if not t:
        release_conn()
        raise HTTPException(404, "待办不存在")
    db.execute("UPDATE tasks SET status='canceled',done_at=?,done_by=? WHERE id=?",
               (now(), u["id"], tid))
    db.commit()
    release_conn()
    audit(u, "task", "cancel", "task", tid, t["title"])
    return {"ok": True}


@router.get("/{tid}")
def get_task(tid: int, u: dict = Depends(current_user)):
    db = get_conn()
    t = db.execute(
        "SELECT t.*, d.name AS dept_name, us.full_name AS assignee_name "
        "FROM tasks t LEFT JOIN departments d ON d.id=t.dept_id "
        "LEFT JOIN users us ON us.id=t.assignee_id WHERE t.id=?", (tid,)).fetchone()
    if not t:
        release_conn()
        raise HTTPException(404, "待办不存在")
    d = _hydrate(t)
    d["timeline"] = [dict(r) for r in db.execute(
        "SELECT * FROM activities WHERE biz_type=? AND biz_id=? ORDER BY id DESC",
        (t["biz_type"], t["biz_id"]))]
    release_conn()
    return d
