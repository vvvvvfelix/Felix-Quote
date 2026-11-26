"""基础设施层 — 数据库访问封装。

对外只暴露 get_conn / release_conn，业务代码不直接管连接生命周期。
"""
from ..database import (BASE_DIR, DEFAULT_COMPANY, DB_PATH, effective_company,
                        get_conn, get_setting, init_db, jdump, jload, now,
                        release_conn, set_setting)

__all__ = ["BASE_DIR", "DB_PATH", "DEFAULT_COMPANY", "effective_company",
           "get_conn", "get_setting", "init_db", "jdump", "jload", "now",
           "release_conn", "set_setting"]
