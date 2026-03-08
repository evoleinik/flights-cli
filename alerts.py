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
    })
    return resp.ok


def find_deals(db):
    """Find prices below threshold or 30%+ below rolling average."""
    routes = db.execute("SELECT origin, dest, name, threshold FROM routes WHERE active=1").fetchall()
    deals = []

    for route in routes:
        origin, dest, name, threshold = route["origin"], route["dest"], route["name"], route["threshold"]

        # Get latest scan's cheapest price per flight_date
        rows = db.execute("""
            SELECT flight_date, MIN(price) as min_price, airline, stops, duration
            FROM prices
            WHERE origin=? AND dest=?
              AND scanned_at = (SELECT MAX(scanned_at) FROM scan_log WHERE origin=? AND dest=?)
            GROUP BY flight_date
        """, (origin, dest, origin, dest)).fetchall()

        rolling_avg = get_rolling_avg(db, origin, dest, ROLLING_AVG_DAYS)

        for row in rows:
            price = row["min_price"]
            flight_date = row["flight_date"]
            reason = None

            # Check fixed threshold
            if threshold and price <= threshold:
                reason = f"under ${threshold} threshold"

            # Check rolling average drop
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
    stops = "Nonstop" if deal["stops"] == 0 else f"{deal['stops']} stop"
    return (
        f"*{deal['origin']} → {deal['name']}* ${deal['price']}{avg_info}\n"
        f"{deal['flight_date']} | {deal['airline']} | {stops} | {deal['duration']}\n"
        f"_{deal['reason']}_"
    )


def run_alerts():
    db = get_db()
    tg = load_telegram_config()
    token = tg["TELEGRAM_BOT_TOKEN"]
    chat_id = tg.get("TELEGRAM_FLIGHTS_CHANNEL", tg["TELEGRAM_CHANNEL_ID"])

    deals = find_deals(db)

    if not deals:
        print("[alerts] no deals found", file=sys.stderr)
        db.close()
        return

    print(f"[alerts] {len(deals)} deals found", file=sys.stderr)

    # Group deals into one message (max 10 per message)
    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    batch = []
    for deal in sorted(deals, key=lambda d: d["price"]):
        batch.append(format_deal(deal))
        log_alert(db, now, deal["origin"], deal["dest"], deal["flight_date"], deal["price"])

        if len(batch) == 10:
            msg = "\n\n".join(batch)
            send_telegram(token, chat_id, msg)
            batch = []

    if batch:
        msg = "\n\n".join(batch)
        send_telegram(token, chat_id, msg)

    db.commit()
    db.close()
    print(f"[alerts] sent {len(deals)} alerts", file=sys.stderr)


if __name__ == "__main__":
    run_alerts()
