#!/usr/bin/env bash
# Daily flight deal scan + alerts
set -e
cd "$(dirname "$0")"

LOG="$HOME/.cheap-flights/scan-$(date +%Y%m%d).log"
mkdir -p "$HOME/.cheap-flights"

# PEP 723 inline deps (fast_flights, requests) are auto-installed by `uv run`.
echo "=== $(date -Iseconds) ===" >> "$LOG"
uv run scan.py 2>&1 | tee -a "$LOG"
uv run alerts.py 2>&1 | tee -a "$LOG"

# Keep 30 days of logs
find "$HOME/.cheap-flights" -name 'scan-*.log' -mtime +30 -delete
