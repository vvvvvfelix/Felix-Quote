"""组织跳层解析测试 —— 专测「组织架构不完整时系统还能不能跑」。

覆盖：空表、中间层级停用、父级不存在（孤儿）、环、单级公司、按类型穿透。
验收标准：**任何场景都不抛异常**，且解析结果落到真实存在的组织上。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

# T1 防护（M006）：先钉死隔离库路径，再让 app 在 import 期读到正确的 WV_DB，
# 否则会落到真实库 data/wv.db。隔离库必须位于 src/data/ 之外，否则拒绝运行。
DEV_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".qa_tmp",
                      "wv_test_orgutil.db")
_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "..", "src", "data"))
_abs_dev = os.path.abspath(DEV_DB)
if _abs_dev == _DATA_DIR or _abs_dev.startswith(_DATA_DIR + os.sep):
    sys.stderr.write(
        "[FATAL] 隔离库 %s 位于真实库目录 src/data/ 下，会污染或命中真实库 wv.db，"
        "已拒绝运行。\n" % DEV_DB)
    sys.exit(2)
os.environ["WV_DB"] = _abs_dev
os.makedirs(os.path.dirname(_abs_dev), exist_ok=True)
if os.path.exists(_abs_dev):
    os.remove(_abs_dev)

from app.core import orgutil                                   # noqa: E402
from app.core.db import get_conn, init_db, release_conn        # noqa: E402
from app.core.schema import run_migrations                    # noqa: E402
from app.core import registry as R                             # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")


def _exec(sql, args=()):
    db = get_conn()
    db.execute(sql, args)
    db.commit()


def main():
    # 隔离库初始化：建库 + 跑版本化迁移（创建 departments 等表），绝不碰真实库。
    init_db()
    run_migrations()
    R.ensure_states()
    print("[org] 0) 隔离库已就绪（data/wv.db 未被触碰）")

    print("[org] 1) 空组织表（组织被清空 / 表不存在）")
    orgutil.ORG_TABLE = "departments_not_exist"
    try:
        a = orgutil.resolve_anchor(None)
        check("空表 resolve_anchor 返回 None 而不抛异常", a is None, f"got={a}")
        check("空表 check_integrity 不抛异常", isinstance(orgutil.check_integrity(), dict))
        check("空表 subtree_ids 返回空列表", orgutil.subtree_ids(1) == [])
    except Exception as e:
        check("空表场景不抛异常", False, repr(e))
    orgutil.ORG_TABLE = "departments"

    print("[org] 2) 构造测试链：公司 > 事业部 > 部门")
    for code in ("_T_CO", "_T_BU", "_T_DEPT"):
        _exec("DELETE FROM departments WHERE code=?", (code,))
    _exec("INSERT INTO departments(code,name,kind,parent_id,sort_order,is_active,"
          "created_at) VALUES('_T_CO','测试公司','office',NULL,900,1,'2026-01-01')")
    co = _exec_last()
    _exec("INSERT INTO departments(code,name,kind,parent_id,sort_order,is_active,"
          "created_at) VALUES('_T_BU','测试事业部','office',?,901,1,'2026-01-01')", (co,))
    bu = _exec_last()
    _exec("INSERT INTO departments(code,name,kind,parent_id,sort_order,is_active,"
          "created_at) VALUES('_T_DEPT','测试部门','office',?,902,1,'2026-01-01')", (bu,))
    dept = _exec_last()

    check("自身有效时锚点即自身", orgutil.resolve_anchor(dept) == dept)
    check("父链正确", orgutil.effective_parent(dept) == bu)
    check("子树含自身与后代", set(orgutil.subtree_ids(co)) >= {co, bu, dept})

    print("[org] 3) 停用中间层级（事业部撤销）→ 必须自动关联到上上级")
    _exec("UPDATE departments SET is_active=0 WHERE id=?", (bu,))
    check("部门自身仍有效", orgutil.resolve_anchor(dept) == dept)
    check("effective_parent 跳过停用层落到公司",
          orgutil.effective_parent(dept) == co, f"got={orgutil.effective_parent(dept)}")
    check("链路自动跳过停用层级",
          [o["name"] for o in orgutil.chain(dept)] == ["测试公司", "测试部门"],
          str([o["name"] for o in orgutil.chain(dept)]))
    check("子树不再包含停用节点", bu not in orgutil.subtree_ids(co))

    print("[org] 4) 该节点自身也失效 → 向下/向上穿透")
    check("失效节点的锚点落到有效祖先", orgutil.resolve_anchor(bu) == co,
          f"got={orgutil.resolve_anchor(bu)}")

    print("[org] 5) 孤儿（父级 id 指向不存在的组织）")
    _exec("INSERT INTO departments(code,name,kind,parent_id,sort_order,is_active,"
          "created_at) VALUES('_T_ORPHAN','孤儿部门','office',999999,903,1,'2026-01-01')")
    orphan = _exec_last()
    r = orgutil.resolve_anchor(orphan)
    check("孤儿节点可用（不被卡死）", r == orphan, f"got={r}")
    integ = orgutil.check_integrity()
    check("体检能报出孤儿", any(o["id"] == orphan for o in integ["orphans"]),
          f"orphans={integ['orphans']}")

    print("[org] 6) 环（A 的父是 B，B 的父是 A）")
    _exec("INSERT INTO departments(code,name,kind,parent_id,sort_order,is_active,"
          "created_at) VALUES('_T_C1','环A','office',NULL,904,1,'2026-01-01')")
    c1 = _exec_last()
    _exec("INSERT INTO departments(code,name,kind,parent_id,sort_order,is_active,"
          "created_at) VALUES('_T_C2','环B','office',?,905,1,'2026-01-01')", (c1,))
    c2 = _exec_last()
    _exec("UPDATE departments SET parent_id=? WHERE id=?", (c2, c1))
    try:
        orgutil.ancestors(c1)
        orgutil.resolve_anchor(c1)
        check("成环不死循环", True)
    except Exception as e:
        check("成环不死循环", False, repr(e))

    print("[org] 7) 按类型穿透：没有工厂层级时不返回空")
    r = orgutil.resolve_by_type(dept, ["factory"])
    check("缺 factory 层级时兜底到有效组织而非 None", r is not None, f"got={r}")
    r2 = orgutil.resolve_by_type(dept, ["office"])
    check("命中期望类型", r2 in (dept, co), f"got={r2}")

    print("[org] 8) 不存在的 id / None 输入")
    for bad in (None, 0, -1, 88888888):
        try:
            orgutil.resolve_anchor(bad)
            orgutil.effective_leader(bad)
            orgutil.subtree_ids(bad)
        except Exception as e:
            check(f"异常输入 {bad} 不抛异常", False, repr(e))
            break
    else:
        check("异常输入（None/0/负数/不存在 id）全部安全", True)

    # 清理
    _exec("DELETE FROM departments WHERE code LIKE '_T_%'")
    print(f"\n[org] 结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
    for f in FAIL:
        print(f"  - 失败项：{f}")
    return 1 if FAIL else 0


def _exec_last():
    db = get_conn()
    return db.execute("SELECT last_insert_rowid() id").fetchone()["id"]


if __name__ == "__main__":
    sys.exit(main())
