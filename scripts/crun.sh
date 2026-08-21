#!/usr/bin/env bash
# crun.sh — Cron-Wrapper fuer das Aktien-System.
# flock verhindert ueberlappende Laeufe. Feiertage/Sonntag via market_calendar.
#
# Nutzung: bash crun.sh <daily|p1|p2|p3|p4|events|p5|p7|drivers|backup|migrate>
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

LOCK="/tmp/stock_analysis.lock"
LOG_DIR="$ROOT/data/logs"
mkdir -p "$LOG_DIR"

run() {
    local job="$1"; shift
    flock -n "$LOCK" python scripts/"$job".py "$@" >>"$LOG_DIR/${job}.log" 2>&1 \
        || echo "[crun] $job uebersprungen (Lock belegt oder Fehler)"
}

case "${1:-daily}" in
    daily)     run daily_check ;;
    p1)        run llm_runner p1 ;;
    p2)        run llm_runner p2 ;;
    p3)        run llm_runner p3 "${2:-}" ;;
    p4)        run llm_runner p4 "${2:-}" ;;
    events)    run event_watcher ;;
    p5)        run llm_runner p5 ;;
    p7)        run llm_runner p7 ;;
    drivers)   python -c "from lib.macro_drivers import fetch_driver_values; print('drivers ok')" ;;
    backup)    python -c "from lib.db import _conn; print('backup stub')" ;;
    migrate)   python -c "from lib.db import init_database; init_database(); \
               import scripts.migrate_v2 as m2, scripts.migrate_v3 as m3, scripts.migrate_v4 as m4; \
               m2.migrate_v2(); m3.migrate_v3(); m4.migrate_v4(); print('migration done')" ;;
    *)         echo "Unbekannt: $1"; exit 1 ;;
esac
