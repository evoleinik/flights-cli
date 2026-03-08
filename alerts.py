#!/usr/bin/env python3
"""Check latest prices against thresholds + rolling avg, send Telegram alerts."""

import os
import sys
import time
from datetime import datetime, timezone

import requests

from db import get_db, get_rolling_avg, log_alert, was_alerted_recently

TELEGRAM_CONFIG = os.path.expanduser("~/Sync/config/telegram/config")
ROLLING_AVG_DAYS = 14
DROP_PERCENT = 30  # alert if 30%+ below rolling avg


def load_telegram_config():
    config = {}
    with open(TELEGRAM_CONFIG) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                config[k.strip()] = v.strip()
    return config


def send_telegram(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }, timeout=15)
    if not resp.ok:
        print(f"[alerts] telegram send failed: {resp.status_code} {resp.text[:100]}", file=sys.stderr)
    return resp.ok


def find_deals(db):
    """Find cheapest deal per route (not per date) below threshold or rolling avg."""
    routes = db.execute("SELECT origin, dest, name, threshold FROM routes WHERE active=1").fetchall()
    deals = []

    for route in routes:
        origin, dest, name, threshold = route["origin"], route["dest"], route["name"], route["threshold"]

        # Get the single cheapest flight across all dates from latest scan
        row = db.execute("""
            SELECT flight_date, price AS min_price, airline, stops, duration
            FROM (
                SELECT *, ROW_NUMBER() OVER (ORDER BY price ASC) AS rn
                FROM prices
                WHERE origin=? AND dest=?
                  AND scanned_at = (SELECT MAX(scanned_at) FROM scan_log WHERE origin=? AND dest=?)
            )
            WHERE rn = 1
        """, (origin, dest, origin, dest)).fetchone()

        if not row:
            continue

        price = row["min_price"]
        flight_date = row["flight_date"]
        rolling_avg = get_rolling_avg(db, origin, dest, ROLLING_AVG_DAYS)
        reason = None

        if threshold and price <= threshold:
            reason = f"under ${threshold} threshold"

        if rolling_avg and price <= rolling_avg * (1 - DROP_PERCENT / 100):
            pct = int((1 - price / rolling_avg) * 100)
            reason = f"{pct}% below avg ${int(rolling_avg)}"

        if reason and not was_alerted_recently(db, origin, dest, flight_date):
            deals.append({
                "origin": origin,
                "dest": dest,
                "name": name,
                "flight_date": flight_date,
                "price": price,
                "airline": row["airline"] or "—",
                "stops": row["stops"],
                "duration": row["duration"] or "—",
                "rolling_avg": int(rolling_avg) if rolling_avg else None,
                "reason": reason,
            })

    return deals


def format_deal(deal):
    avg_info = f" (avg ${deal['rolling_avg']})" if deal["rolling_avg"] else ""
    stops = "nonstop" if deal["stops"] == 0 else f"{deal['stops']} stop{'s' if deal['stops'] != 1 else ''}"
    return f"`{deal['dest']}` *{deal['name']}* ${deal['price']}{avg_info} | {deal['flight_date']} | {stops}"


def format_report(deals):
    """Format all deals as a single daily report message."""
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    lines = [f"*CNX Deals* {today}\n"]
    for deal in sorted(deals, key=lambda d: d["price"]):
        lines.append(format_deal(deal))
    return "\n".join(lines)


def run_alerts():
    db = get_db()
    try:
        tg = load_telegram_config()
        token = tg["TELEGRAM_BOT_TOKEN"]
        chat_id = tg.get("TELEGRAM_FLIGHTS_CHANNEL", tg["TELEGRAM_CHANNEL_ID"])

        deals = find_deals(db)

        if not deals:
            print("[alerts] no deals found", file=sys.stderr)
            return

        print(f"[alerts] {len(deals)} deals found", file=sys.stderr)

        now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        msg = format_report(deals)

        if send_telegram(token, chat_id, msg):
            for d in deals:
                log_alert(db, now, d["origin"], d["dest"], d["flight_date"], d["price"])
            db.commit()
            print(f"[alerts] sent {len(deals)} deals in 1 message", file=sys.stderr)
        else:
            print("[alerts] failed to send", file=sys.stderr)
    finally:
        db.close()


if __name__ == "__main__":
    run_alerts()
