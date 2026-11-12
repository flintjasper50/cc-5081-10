#!/usr/bin/env bash
# 从清洁目录生成参考批次并复核：
#   1) 仓库自检（配置引用 / 终止条件 / 统计分母）
#   2) 生成参考批次（逐场保存种子、规则摘要、事件流与结果）
#   3) 复核参考批次（事件流独立重算、展示表复算、场景约束）
#   4) 从原始记录复算展示表
#   5) 确认可运行示例：无有效样本、部分构筑不满足约束
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PYTHON:-python3}
OUT=${1:-runs}

echo "== 1/5 仓库自检 =="
$PY -m combat_sim check

echo "== 2/5 生成参考批次 -> $OUT =="
$PY -m combat_sim reference --out "$OUT"

echo "== 3/5 复核参考批次 =="
$PY -m combat_sim verify --dir "$OUT"

echo "== 4/5 从原始记录复算展示表 =="
$PY -m combat_sim show-runs --dir "$OUT"

echo "== 5/5 可运行示例 =="
echo "-- 无有效样本（配置错误，成功率不可计算）--"
$PY -m combat_sim batch --scenario no_valid_samples --data examples/empty_no_valid
echo "-- 部分构筑不满足场景约束（有效失败，不是模拟失败）--"
$PY -m combat_sim constraint-check --scenario partial_not_satisfied --data examples/partial_constraint

echo "全部完成。"
