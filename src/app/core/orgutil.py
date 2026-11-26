"""基础设施层 — 组织架构跳层解析。

为什么需要：现实里的组织树从来不是规整的。

- 十几个人的公司：「公司 → 部门」两级就够了，没有工厂、没有事业部
- 稍有规模的：公司 → 工厂 → 车间 → 班组
- 集团化的：集团 → 法人公司 → 事业部 → 工厂 → 部门

如果代码假设「订单一定挂在工厂下」「人员一定属于某个部门」，那么小公司
缺了中间层级就会拿到空值，进而空指针、报表少数据、权限算错。

本模块提供**穿透式解析**：中间层级不存在就往上找，往上没有就往下找，
再找不到就落到根组织。**任何函数都不抛异常、不返回 None 导致的崩溃**，
最多返回一个兜底组织并在自检里报告出来的问题。

M001 会把 departments 升级为 org_units，届时只需改 ORG_TABLE 一处。
"""
from typing import Any

from .db import get_conn, now

# M001 起 org_units 是权威组织树；这里继续指向 departments 是因为
# users.dept_id 仍指向旧表，切换会让「建用户时穿透组织」拿到新表 id 而写不回去。
# 切换点放在 M004（数据范围扩展）：届时 users.org_unit_id 就位、旧表废弃，
# 只改这一行即可，其余逻辑无需改动。
ORG_TABLE = "departments"


# ==========================================================================
# 一、基础读取
# ==========================================================================
def load_orgs(table: str | None = None) -> dict[int, dict[str, Any]]:
    """全量读入组织表。组织数据只有几十到几百行，全量读比递归查询更简单可靠。

    table 可显式指定（M001 起新代码传 "org_units"），不传则用 ORG_TABLE。
    """
    try:
        db = get_conn()
        return {r["id"]: dict(r)
                for r in db.execute(f"SELECT * FROM {table or ORG_TABLE}")}
    except Exception:
        return {}


def _is_live(o: dict | None) -> bool:
    return bool(o) and int(o.get("is_active") or 0) == 1


def _kind(o: dict | None) -> str:
    """组织类型：新表 org_units 用 org_type，旧表 departments 用 kind。

    两套表并存期（M001 之后到 departments 废弃之前）统一走这个取值函数，
    避免每个调用点各写一半，将来删掉旧表时只改这一处。
    """
    if not o:
        return ""
    return o.get("org_type") or o.get("kind") or ""


def ancestors(org_id: int | None, orgs: dict | None = None) -> list[int]:
    """从父到根返回祖先 id 链（不含自身）。带环检测 —— 数据被改坏也不会死循环。"""
    orgs = orgs if orgs is not None else load_orgs()
    out, seen, cur = [], set(), org_id
    while cur and cur in orgs:
        p = orgs[cur].get("parent_id")
        if not p or p in seen or p == cur:
            break
        out.append(p)
        seen.add(p)
        cur = p
    return out


def descendants(org_id: int, orgs: dict | None = None) -> list[int]:
    """返回全部后代 id（含自身），广度优先。带环检测。"""
    orgs = orgs if orgs is not None else load_orgs()
    out, queue, seen = [], [org_id], {org_id}
    while queue:
        cur = queue.pop(0)
        out.append(cur)
        for oid, o in orgs.items():
            if o.get("parent_id") == cur and oid not in seen:
                seen.add(oid)
                queue.append(oid)
    return out


# ==========================================================================
# 二、穿透式解析（本模块的核心）
# ==========================================================================
def effective_parent(org_id: int | None, orgs: dict | None = None) -> int | None:
    """向上找第一个「有效」的父组织，自动跳过已停用/缺失的中间层级。"""
    orgs = orgs if orgs is not None else load_orgs()
    for pid in ancestors(org_id, orgs):
        if _is_live(orgs.get(pid)):
            return pid
    return None


def effective_leader(org_id: int | None, orgs: dict | None = None) -> int | None:
    """向上找第一个有负责人的组织（含自身）—— 小公司部门没设主管时，自动落到上级。"""
    orgs = orgs if orgs is not None else load_orgs()
    cur = org_id
    seen = set()
    while cur and cur in orgs and cur not in seen:
        seen.add(cur)
        o = orgs[cur]
        if _is_live(o) and o.get("leader_id"):
            return o["leader_id"]
        cur = o.get("parent_id")
    return None


def resolve_anchor(org_id: int | None, orgs: dict | None = None) -> int | None:
    """把任意组织 id 解析成一个「可用于业务的有效组织」。

    优先级：自身有效 → 最近的有效祖先 → 最近的有效后代 → 根组织 → 任一有效组织。
    返回值可能为 None（组织表完全为空），调用方按「无组织约束」处理即可。
    """
    orgs = orgs if orgs is not None else load_orgs()
    if not orgs:
        return None
    if org_id and _is_live(orgs.get(org_id)):
        return org_id

    # 1) 往上穿透：中间层级不要紧，找到最近还存在的上级
    if org_id:
        for pid in ancestors(org_id, orgs):
            if _is_live(orgs.get(pid)):
                return pid

    # 2) 往下穿透：这个节点本身失效但下级还在（例如撤销事业部、部门并入公司）
    if org_id:
        kids = descendants(org_id, orgs) if org_id in orgs else []
        for kid in kids:
            if kid != org_id and _is_live(orgs.get(kid)):
                return kid

    # 3) 兜底：顶层有效组织（sort_order 最小者）
    tops = [o for o in orgs.values() if _is_live(o) and not o.get("parent_id")]
    if tops:
        return sorted(tops, key=lambda x: (x.get("sort_order") or 0, x["id"]))[0]["id"]
    live = [o for o in orgs.values() if _is_live(o)]
    return live[0]["id"] if live else None


def resolve_by_type(org_id: int | None, kinds: list[str],
                    orgs: dict | None = None) -> int | None:
    """按类型解析：向上找最近的指定类型（如 factory / bu），没有就向下找。

    典型场景：单据要「所属工厂」，但该公司压根没有工厂层级 ——
    那就往上落到公司，或往下落到车间，**绝不返回空导致单据卡住**。
    """
    orgs = orgs if orgs is not None else load_orgs()
    if not orgs:
        return None
    cur = org_id
    seen = set()
    while cur and cur in orgs and cur not in seen:        # 向上
        seen.add(cur)
        o = orgs[cur]
        if _is_live(o) and _kind(o) in kinds:
            return cur
        cur = o.get("parent_id")
    if org_id in orgs:                                     # 向下
        for kid in descendants(org_id, orgs):
            o = orgs.get(kid)
            if kid != org_id and _is_live(o) and _kind(o) in kinds:
                return kid
    return resolve_anchor(org_id, orgs)                    # 都没有 → 兜底


def chain(org_id: int | None, orgs: dict | None = None) -> list[dict[str, Any]]:
    """返回从根到该组织的**有效**链路，中间失效层级自动跳过（面包屑用）。"""
    orgs = orgs if orgs is not None else load_orgs()
    anchor = resolve_anchor(org_id, orgs)
    if not anchor:
        return []
    ids = list(reversed(ancestors(anchor, orgs))) + [anchor]
    return [orgs[i] for i in ids if _is_live(orgs.get(i))]


def subtree_ids(org_id: int | None, orgs: dict | None = None) -> list[int]:
    """数据范围：该组织及其全部**有效**后代。传入无效 id 时自动穿透到有效锚点。"""
    orgs = orgs if orgs is not None else load_orgs()
    anchor = resolve_anchor(org_id, orgs)
    if not anchor:
        return []
    return [i for i in descendants(anchor, orgs) if _is_live(orgs.get(i))]


# ==========================================================================
# 二之二、冗余字段重算（path / level / 归属公司·工厂·事业部）
# ==========================================================================
def recompute_tree(db=None, table: str | None = None,
                   rollups: dict[str, list[str]] | None = None) -> int:
    """重算组织树的冗余字段 —— 每次结构变动后调用一次。

    为什么存冗余：`path` 让「查某工厂下所有组织」变成一次 LIKE，
    不必递归；`company_id/factory_id/bu_id` 让数据范围过滤退化成
    `WHERE company_id=?`，多组织下这是性能与代码复杂度的大头。

    rollups 形如 {"company_id": ["company"]}，含义：
    自顶向下继承**最近的**指定类型祖先 id（含自身）。

    带环检测：成环的节点不会被 BFS 到达，最后统一按「自身为根」兜底写入，
    保证 path 永不为 NULL —— 下游 LIKE 查询不会因为脏数据静默漏数据。
    """
    table = table or ORG_TABLE
    if db is None:
        db = get_conn()
    try:
        rows = [dict(r) for r in db.execute(f"SELECT * FROM {table}")]
    except Exception:
        return 0
    if not rows:
        return 0
    by_id = {r["id"]: r for r in rows}
    kids: dict[int, list[int]] = {}
    for r in rows:
        p = r.get("parent_id")
        if p and p != r["id"] and p in by_id:
            kids.setdefault(p, []).append(r["id"])

    def _sort_key(i: int):
        return (by_id[i].get("sort_order") or 0, i)

    updates: list[tuple] = []
    seen: set[int] = set()

    def _walk(seed_ids: list[int], base_path: str, base_level: int,
              carry: dict) -> None:
        queue = [(i, base_path, base_level, dict(carry)) for i in seed_ids]
        while queue:
            oid, ppath, level, car = queue.pop(0)
            if oid in seen or oid not in by_id:
                continue
            seen.add(oid)
            o = by_id[oid]
            path = f"{ppath}{oid}/"
            cur = dict(car)
            for col, types in (rollups or {}).items():
                if _kind(o) in types:
                    cur[col] = oid
            updates.append((path, level, cur, oid))
            for c in sorted(kids.get(oid, []), key=_sort_key):
                if c not in seen:
                    queue.append((c, path, level + 1, cur))

    roots = sorted([r["id"] for r in rows
                    if not (r.get("parent_id") and r.get("parent_id") in by_id)],
                   key=_sort_key)
    _walk(roots, "/", 0, {})
    # 成环的残留节点：兜底成自己的根，保证 path 非空
    left = [r["id"] for r in rows if r["id"] not in seen]
    if left:
        _walk(sorted(left, key=_sort_key), "/", 0, {})

    cols = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
    for path, level, car, oid in updates:
        sets = ["path=?", "level=?"]
        args: list = [path, level]
        for col, val in car.items():
            if col in cols:
                sets.append(f"{col}=?")
                args.append(val)
        if "updated_at" in cols:
            sets.append("updated_at=?")
            args.append(now())
        args.append(oid)
        db.execute(f"UPDATE {table} SET {','.join(sets)} WHERE id=?", args)
    return len(updates)


# ==========================================================================
# 三、完整性自检 —— 问题可以存在，但必须看得见
# ==========================================================================
def check_integrity(table: str | None = None) -> dict[str, Any]:
    """体检组织树：孤儿（父级不存在）、断链（父级已停用）、环、空树。

    设计取向：**发现问题不阻断运行**，只报告。修复与否由管理员决定。
    """
    orgs = load_orgs(table)
    orphans, broken, cycles = [], [], []
    for oid, o in orgs.items():
        p = o.get("parent_id")
        if not p:
            continue
        if p not in orgs:
            orphans.append({"id": oid, "name": o.get("name"), "missing_parent": p})
        elif not _is_live(orgs[p]):
            broken.append({"id": oid, "name": o.get("name"),
                           "inactive_parent": p,
                           "auto_parent": effective_parent(oid, orgs)})
        if oid in ancestors(oid, orgs):
            cycles.append({"id": oid, "name": o.get("name")})
    tops = [o for o in orgs.values() if _is_live(o) and not o.get("parent_id")]
    return {"count": len(orgs), "active": sum(1 for o in orgs.values() if _is_live(o)),
            "roots": len(tops), "orphans": orphans, "broken_links": broken,
            "cycles": cycles,
            "healthy": not (orphans or broken or cycles) and bool(orgs)}
