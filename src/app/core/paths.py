"""基础设施层 — 全局路径与配置。

所有模块共用，不含业务逻辑。
"""
import os

from ..database import BASE_DIR

APP_DIR = os.path.join(BASE_DIR, "app")
STATIC_DIR = os.path.join(APP_DIR, "static")
UPLOAD_DIR = os.path.join(STATIC_DIR, "uploads")
DATA_DIR = os.path.join(BASE_DIR, "data")
BACKUP_DIR = os.path.join(BASE_DIR, "backup")

for _d in (UPLOAD_DIR, DATA_DIR, BACKUP_DIR):
    os.makedirs(_d, exist_ok=True)


def upload_path(*parts: str) -> str:
    """上传目录下拼接路径（自动创建父目录）。"""
    p = os.path.join(UPLOAD_DIR, *parts)
    parent = os.path.dirname(p)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return p


def uuid_like() -> str:
    """短随机串，用于上传文件名去重。"""
    import uuid
    return uuid.uuid4().hex[:8]
