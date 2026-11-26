"""模块层 — 工作台与经营分析。

多用户场景下工作台的定位变了：不再是「看看有多少数据」，
而是「打开系统第一眼告诉我今天该干什么」。业务员打开系统第一眼就该知道
待回复询盘、报价即将过期、待催款、待发货、待确认收款。
"""
from collections import OrderedDict

from fastapi import APIRouter, Depends

from ..auth import current_user
from ..core import permissions as P
from ..core.db import get_conn, now, release_conn
from ..core.services import (STATUS_LABEL, _log_exc, my_tasks, overdue_alerts,
                             timeline)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/today")
def today(u: dict = Depends(current_user)):
    """今日待办首页。一次返回所有卡片，减少 VPN 下的往返次数。"""
    db = get_conn()
    tasks = my_tasks(u, 30)
    alerts = overdue_alerts(u)

    cards = OrderedDict([
        ("pending_inquiries", 0), ("pending_quotes", 0), ("pending_orders", 0),
        ("in_production", 0), ("pending_shipment", 0), ("pending_payment", 0),
    ])

    # 每张表单独生成片段：scope_clause 的列名必须带对应别名
    def count(table_sql: str, alias: str) -> int:
        f, a = P.scope_clause(u, alias=alias)
        return db.execute(f"SELECT COUNT(*) c FROM {table_sql}{f}", a).fetchone()["c"]

    try:
        cards["pending_inquiries"] = count("inquiries i WHERE i.status='new'", "i")
        cards["pending_quotes"] = count(
            "quotations q WHERE q.status IN ('draft','sent')", "q")
        cards["pending_orders"] = count(
            "orders o WHERE o.status IN ('draft','confirming')", "o")
        cards["in_production"] = count("orders o WHERE o.status='producing'", "o")
        cards["pending_shipment"] = count(
            "shipments s WHERE s.status='planned'", "s")
    except Exception as exc:
        _log_exc("dashboard.today")

    try:
        cards["pending_payment"] = count(
            "receivables r WHERE r.status IN ('open','partial')", "r")
    except Exception as exc:
        _log_exc("dashboard.today_payment")

    overdue_tasks = [t for t in tasks if t.get("is_overdue")]
    today_tasks = [t for t in tasks if t.get("is_today")]
    release_conn()

    return {
        "cards": cards,
        "tasks": tasks,
        "task_summary": {
            "open": len(tasks),
            "today": len(today_tasks),
            "overdue": len(overdue_tasks),
            "urgent": sum(1 for t in tasks if t.get("priority") == "urgent"),
        },
        "alerts": alerts,
        "greeting": _greeting(),
        "date": now()[:10],
    }


def _greeting() -> str:
    h = int(now()[11:13])
    if h < 6:
        return "凌晨了，早点休息"
    if h < 9:
        return "早上好"
    if h < 12:
        return "上午好"
    if h < 14:
        return "中午好，记得休息"
    if h < 18:
        return "下午好"
    return "晚上好"


@router.get("/recent")
def recent(u: dict = Depends(current_user), limit: int = 15):
    """最近动态：我经手的单据变化流水。"""
    db = get_conn()
    frag, args = P.scope_clause(u, alias="o")
    rows = [dict(r) for r in db.execute(
        "SELECT o.order_no, o.id, o.total_amount, o.currency, o.status, o.updated_at, "
        "c.company_en AS customer_name FROM orders o "
        "LEFT JOIN customers c ON c.id=o.customer_id WHERE 1=1"
        f"{frag} ORDER BY o.updated_at DESC LIMIT ?", args + [limit])]
    release_conn()
    for r in rows:
        r["status_label"] = STATUS_LABEL.get(r["status"], r["status"])
    return rows


@router.get("/analytics")
def analytics(u: dict = Depends(current_user), months: int = 6):
    """经营分析：趋势、来源、品类、人效、毛利。多站点看各分公司对比。"""
    db = get_conn()
    # 各查询表不同，别名/片段分别生成（scope_clause 需按表限定列名）
    frag_o, args_o = P.scope_clause(u, alias="o")
    frag_i, args_i = P.scope_clause(u, alias="i")
    frag_q, args_q = P.scope_clause(u, alias="q")

    # 月度趋势
    trend = [dict(r) for r in db.execute(
        f"SELECT strftime('%Y-%m', created_at) ym, COUNT(*) inquiries "
        f"FROM inquiries i WHERE 1=1{frag_i} GROUP BY ym ORDER BY ym DESC LIMIT ?",
        args_i + [months])][::-1]
    tmap = {r["ym"]: r["inquiries"] for r in trend}
    ot = [dict(r) for r in db.execute(
        f"SELECT strftime('%Y-%m', created_at) ym, COUNT(*) orders, "
        f"IFNULL(SUM(total_amount),0) amount FROM orders o WHERE 1=1{frag_o} "
        "GROUP BY ym ORDER BY ym DESC LIMIT ?", args_o + [months])][::-1]
    for r in ot:
        tmap.setdefault(r["ym"], 0)
    trend = [{"ym": k, "inquiries": tmap.get(k, 0)} for k in sorted(tmap)][-months:]
    for r in ot:
        for t in trend:
            if t["ym"] == r["ym"]:
                t["orders"] = r["orders"]
                t["amount"] = r["amount"]

    source = [dict(r) for r in db.execute(
        f"SELECT source, COUNT(*) n FROM inquiries i WHERE 1=1{frag_i} "
        "GROUP BY source ORDER BY n DESC", args_i)]

    category = [dict(r) for r in db.execute(
        # 别名：i=inquiry_items, q=inquiries（数据范围按 inquiries 的归属过滤）
        f"SELECT i.category_code, c.name_cn, COUNT(*) n FROM inquiry_items i "
        f"JOIN inquiries q ON q.id=i.inquiry_id "
        f"LEFT JOIN categories c ON c.code=i.category_code WHERE 1=1{frag_q} "
        "GROUP BY i.category_code ORDER BY n DESC LIMIT 10", args_q)]

    country = [dict(r) for r in db.execute(
        f"SELECT country, COUNT(*) n FROM inquiries i WHERE country IS NOT NULL "
        f"AND country!=''{frag_i} GROUP BY country ORDER BY n DESC LIMIT 10", args_i)]

    perf = [dict(r) for r in db.execute(
        "SELECT us.id, us.full_name, d.name AS dept_name, "
        "COUNT(DISTINCT q.id) inquiries, COUNT(DISTINCT o.id) orders, "
        "IFNULL(SUM(o.total_amount),0) amount "
        "FROM users us "
        "LEFT JOIN inquiries q ON q.owner_id=us.id "
        "LEFT JOIN orders o ON o.owner_id=us.id "
        "LEFT JOIN departments d ON d.id=us.dept_id "
        "WHERE us.is_active=1 GROUP BY us.id ORDER BY amount DESC")]

    money = db.execute(
        f"SELECT IFNULL(SUM(total_amount),0) amt, IFNULL(SUM(cost_amount),0) cost "
        f"FROM orders o WHERE status NOT IN ('draft','canceled'){frag_o}",
        args_o).fetchone()

    cust = db.execute("SELECT COUNT(*) c FROM customers").fetchone()["c"]

    won = db.execute(
        f"SELECT COUNT(*) c FROM inquiries i WHERE status='won'{frag_i}",
        args_i).fetchone()["c"]
    quoted = db.execute(
        f"SELECT COUNT(*) c FROM inquiries i WHERE status IN "
        f"('quoted','negotiating','won'){frag_i}", args_i).fetchone()["c"]

    release_conn()
    amt = money["amt"] or 0
    cost = money["cost"] or 0
    return {
        "trend": trend,
        "by_source": source,
        "by_category": category,
        "by_country": country,
        "performance": perf,
        "totals": {
            "amount": round(amt, 2), "cost": round(cost, 2),
            "gross_profit": round(amt - cost, 2),
            "gross_rate": round((amt - cost) / amt * 100, 1) if amt else 0,
            "customers": cust,
            "inquiries": sum(t.get("inquiries", 0) for t in trend),
            "win_rate": round(won * 100.0 / quoted, 1) if quoted else 0,
        },
    }
