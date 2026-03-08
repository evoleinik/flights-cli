#!/usr/bin/env bash
# Daily flight deal scan + alerts
set -e
cd "$(dirname "$0")"

LOG="$HOME/.cheap-flights/scan-$(date +%Y%m%d).log"
mkdir -p "$HOME/.cheap-flights"

echo "=== $(date -Iseconds) ===" >> "$LOG"
python3 scan.py 2>&1 | tee -a "$LOG"
python3 alerts.py 2>&1 | tee -a "$LOG"

# Keep 30 days of logs
find "$HOME/.cheap-flights" -name 'scan-*.log' -mtime +30 -delete
