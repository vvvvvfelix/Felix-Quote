#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成核心文件完整性基线（sha256）。

输出 tools/integrity_baseline.json。仅离线计算，不改任何业务代码。
可接入 CI / 启动自检：改动核心文件后重新生成或比对即可发现篡改/损坏。

用法：
    python tools/gen_integrity_baseline.py
    FELIX_INTEGRITY_KEY=xxx python tools/gen_integrity_baseline.py   # 带 HMAC 签名，防基线本身被改
"""
import hashlib
import json
import os
import glob
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 纳入自检的"核心文件"范围。增减请同步更新本列表并在docs记录。
CORE_GLOBS = [
    "src/app/main.py",
    "src/app/seed.py",
    "src/app/core/**/*.py",
    "src/app/modules/**/*.py",
    "src/app/static/index.html",
    "src/app/static/app.css",
    "src/app/static/js/app.js",
    "src/app/static/js/pages/**/*.js",
]


def collect():
    files = set()
    for g in CORE_GLOBS:
        for p in glob.glob(os.path.join(ROOT, g), recursive=True):
            if os.path.isfile(p):
                files.add(p)
    return sorted(os.path.relpath(p, ROOT) for p in files)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    rel = collect()
    if not rel:
        print("未匹配到任何核心文件，请检查 CORE_GLOBS 与项目结构。")
        return 1
    files_map = {r: sha256_file(os.path.join(ROOT, r)) for r in rel}
    canonical = json.dumps(files_map, sort_keys=True, ensure_ascii=False)
    manifest_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    key = os.environ.get("FELIX_INTEGRITY_KEY", "")
    manifest_hmac = (
        hashlib.sha256((key + canonical).encode("utf-8")).hexdigest() if key else ""
    )
    out = {
        "algo": "sha256",
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "count": len(files_map),
        "manifest_hash": manifest_hash,
        "manifest_hmac": manifest_hmac,
        "files": files_map,
    }
    out_path = os.path.join(ROOT, "tools", "integrity_baseline.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"基线已生成：{out_path}（共 {len(files_map)} 个核心文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
