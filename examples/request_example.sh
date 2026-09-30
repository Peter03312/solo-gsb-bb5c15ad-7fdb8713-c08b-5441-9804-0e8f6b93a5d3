#!/usr/bin/env bash
# 端到端请求示例：录入资料 -> 求解 -> 确认 -> 查看占用。
# 用法：BASE_URL=http://127.0.0.1:8000 ./examples/request_example.sh
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==> 1) 录入/整套替换资料（槽位、箱、不相容类别对、相邻关系、力矩区间）"
curl -sS -X PUT "$BASE_URL/admin/dataset" \
  -H "Content-Type: application/json" \
  --data-binary "@$DIR/dataset.json" | python3 -m json.tool

echo "==> 2) 查询当前资料与修订号"
curl -sS "$BASE_URL/dataset" | python3 -m json.tool

echo "==> 3) 核算配载方案（无解返回 422 且不保存）"
SOLVE_JSON="$(curl -sS -X POST "$BASE_URL/plans/solve")"
echo "$SOLVE_JSON" | python3 -m json.tool

PLAN_REV="$(echo "$SOLVE_JSON" | python3 -c 'import sys,json; print(json.load(sys.stdin)["plan_revision"])')"
SLOTS_REV="$(echo "$SOLVE_JSON" | python3 -c 'import sys,json; print(json.load(sys.stdin)["slots_revision"])')"
BOXES_REV="$(echo "$SOLVE_JSON" | python3 -c 'import sys,json; print(json.load(sys.stdin)["containers_revision"])')"

echo "==> 4) 确认方案（携带资料修订号；过期返回 409 STALE_REVISION，槽位并发冲突返回 409 SLOT_OCCUPIED）"
curl -sS -X POST "$BASE_URL/plans/$PLAN_REV/confirm" \
  -H "Content-Type: application/json" \
  -d "{\"plan_revision\": $PLAN_REV, \"slots_revision\": $SLOTS_REV, \"containers_revision\": $BOXES_REV}" \
  | python3 -m json.tool

echo "==> 5) 当前全部已确认槽位占用"
curl -sS "$BASE_URL/occupancies" | python3 -m json.tool
