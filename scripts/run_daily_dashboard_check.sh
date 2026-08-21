#!/usr/bin/env bash
set -uo pipefail
ROOT="C:/Monsieur-Prompt/07-Finanzen/Projekte/Portfoliobot"
PY="$ROOT/.venv311/Scripts/python.exe"
LOG="$ROOT/data/tagesform.log"
mkdir -p "$ROOT/data"

run() {
  local label="$1"; shift
  echo "[$(date -u +%Y-%m-%dT%H:%M:%S+00:00)] START $label" >> "$LOG"
  if "$PY" "$ROOT/scripts/$1" "${@:2}" >> "$LOG" 2>&1; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%S+00:00)] OK $label" >> "$LOG"
  else
    echo "[$(date -u +%Y-%m-%dT%H:%M:%S+00:00)] FAIL $label" >> "$LOG"
  fi
}

run "A1-dashboard" "daily_dashboard_check.py"
run "A2-kurse" "portfolio_data_refresh.py"
run "A3-bewertung" "valuation_refresh.py" "--apply"
run "A4-sparplan" "sparplan_perf_refresh.py"
