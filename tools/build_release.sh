#!/usr/bin/env bash
# 打包发布 — 把 src 打成便携版 rar，可选直接替换资料库节点。
#
# 用法：
#   bash tools/build_release.sh                 # 只打包
#   bash tools/build_release.sh --upload        # 打包并替换资料库节点（链接不变）
#
set -euo pipefail

ROOT="E:/workbuddy/felix-quote"
SRC="$ROOT/src"
OUT="$ROOT/dist"
RAR="/c/Program Files/WinRAR/Rar.exe"
NODE_ID="nLUL99BDxAK65v0niDKtqA"
FIXED_NAME="felix-quote-1.3.0.rar"      # 保持同名 → 资料库链接不变
SKILL="C:/Program Files/WorkBuddy/resources/app.asar.unpacked/resources/plugins/workbuddy-builtin/skills/library"

UPLOAD=0
[ "${1:-}" = "--upload" ] && UPLOAD=1

mkdir -p "$OUT"

echo "[build] 清理运行时残留..."
find "$SRC" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
find "$SRC" -type f -name "*.pyc" -delete 2>/dev/null || true
rm -f "$SRC"/data/*.db "$SRC"/data/*.db-wal "$SRC"/data/*.db-shm 2>/dev/null || true
rm -rf "$SRC"/backup/* 2>/dev/null || true

VER=$(grep -oP '版本\s+\Kv?[0-9]+\.[0-9]+\.[0-9]+' "$SRC/版本.txt" | head -1)
VER=${VER:-2.0.0}
TARGET="$OUT/felix-quote-${VER#v}.rar"

echo "[build] 打包 → $TARGET"
rm -f "$TARGET"
cd "$SRC"
"$RAR" a -r -ep1 -m3 -x'*.db' -x'*.db-wal' -x'*.db-shm' \
      -x'__pycache__' -x'*.pyc' "$TARGET" . >/dev/null
cd "$ROOT"
ls -lh "$TARGET"

if [ "$UPLOAD" = "1" ]; then
  echo "[build] 上传并替换资料库节点 $NODE_ID ..."
  python3 "$SKILL/drive/upload_drive_file.py" "$TARGET" \
      --node-id "$NODE_ID" --file-name "$FIXED_NAME"
  echo "[build] 复核节点状态："
  python3 "$SKILL/space_api.py" space.workspace.node-info --node-id "$NODE_ID"
fi

echo "[build] 完成。"
