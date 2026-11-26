#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验核心文件完整性，与 tools/integrity_baseline.json 比对。

任一核心文件被改动/删除即报警。返回非零退出码便于 CI / 启动自检接入。
用法：
    python tools/integrity_check.py
    echo $?   # 0=通过 1=有改动 2=基线缺失
"""
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = os.path.join(ROOT, "tools", "integrity_baseline.json")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    if not os.path.exists(BASELINE):
        print("基线文件不存在，请先运行 gen_integrity_baseline.py")
        return 2
    with open(BASELINE, encoding="utf-8") as f:
        base = json.load(f)
    files = base.get("files", {})
    mismatches, missing = [], []
    for rel, expect in files.items():
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            missing.append(rel)
            continue
        if sha256_file(p) != expect:
            mismatches.append(rel)
    # 校验清单自身是否被篡改
    canonical = json.dumps(files, sort_keys=True, ensure_ascii=False)
    if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != base.get("manifest_hash"):
        print("警告：基线清单自身摘要不一致（可能基线文件被篡改）")

    if not mismatches and not missing:
        print(f"✅ 完整性校验通过：{len(files)} 个核心文件均未改动。")
        return 0
    for m in mismatches:
        print(f"❌ 已改动：{m}")
    for m in missing:
        print(f"⚠️  缺失：{m}")
    print(f"共 {len(mismatches)} 个改动，{len(missing)} 个缺失。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
