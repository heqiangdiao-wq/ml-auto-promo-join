#!/usr/bin/env bash
# 美客多推广中心自动报活动 —— 快速运行
#
# 用法：
#   ./run.sh            真实参加
#   ./run.sh --dry      只判定
#   ./run.sh --store 4店 --dry
#
# 日志自动写入 logs/run_<日期>_<时间>.log

set -euo pipefail

cd "$(dirname "$0")"
mkdir -p logs

STAMP="$(date +%Y%m%d_%H%M)"
LOG="logs/run_${STAMP}.log"

PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || PY=python

echo "[run] $PY -m src.main $*" | tee "$LOG"
set +e
"$PY" -m src.main "$@" 2>&1 | tee -a "$LOG"
RC=${PIPESTATUS[0]}
set -e

echo
echo "[run] exit=$RC  log=$LOG"
exit "$RC"
