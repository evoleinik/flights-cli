#!/usr/bin/env python3
"""Check latest prices against thresholds + rolling avg, send Telegram alerts."""

import os
import sys
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
    """Find prices below threshold or 30%+ below rolling average."""
    routes = db.execute("SELECT origin, dest, name, threshold FROM routes WHERE active=1").fetchall()
    deals = []

    for route in routes:
        origin, dest, name, threshold = route["origin"], route["dest"], route["name"], route["threshold"]

        # Get cheapest flight per date from latest scan (window function ensures correct airline/stops/duration)
        rows = db.execute("""
            SELECT flight_date, price AS min_price, airline, stops, duration
            FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY flight_date ORDER BY price ASC) AS rn
                FROM prices
                WHERE origin=? AND dest=?
                  AND scanned_at = (SELECT MAX(scanned_at) FROM scan_log WHERE origin=? AND dest=?)
            )
            WHERE rn = 1
        """, (origin, dest, origin, dest)).fetchall()

        rolling_avg = get_rolling_avg(db, origin, dest, ROLLING_AVG_DAYS)

        for row in rows:
            price = row["min_price"]
            flight_date = row["flight_date"]
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
    stops = "Nonstop" if deal["stops"] == 0 else f"{deal['stops']} stop{'s' if deal['stops'] != 1 else ''}"
    return (
        f"*{deal['origin']} → {deal['name']}* ${deal['price']}{avg_info}\n"
        f"{deal['flight_date']} | {deal['airline']} | {stops} | {deal['duration']}\n"
        f"_{deal['reason']}_"
    )


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
        sent = 0
        batch = []
        batch_deals = []
        for deal in sorted(deals, key=lambda d: d["price"]):
            batch.append(format_deal(deal))
            batch_deals.append(deal)

            if len(batch) == 10:
                msg = "\n\n".join(batch)
                if send_telegram(token, chat_id, msg):
                    for d in batch_deals:
                        log_alert(db, now, d["origin"], d["dest"], d["flight_date"], d["price"])
                    sent += len(batch_deals)
                batch = []
                batch_deals = []

        if batch:
            msg = "\n\n".join(batch)
            if send_telegram(token, chat_id, msg):
                for d in batch_deals:
                    log_alert(db, now, d["origin"], d["dest"], d["flight_date"], d["price"])
                sent += len(batch_deals)

        db.commit()
        print(f"[alerts] sent {sent}/{len(deals)} alerts", file=sys.stderr)
    finally:
        db.close()


if __name__ == "__main__":
    run_alerts()
