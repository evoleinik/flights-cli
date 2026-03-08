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
    """Find all cheap dates per route, grouped by destination."""
    routes = db.execute("SELECT origin, dest, name, threshold FROM routes WHERE active=1").fetchall()
    deals = []

    for route in routes:
        origin, dest, name, threshold = route["origin"], route["dest"], route["name"], route["threshold"]

        # Get cheapest flight per date from latest scan
        rows = db.execute("""
            SELECT flight_date, price AS min_price, airline, stops, duration
            FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY flight_date ORDER BY price ASC) AS rn
                FROM prices
                WHERE origin=? AND dest=?
                  AND scanned_at = (SELECT MAX(scanned_at) FROM scan_log WHERE origin=? AND dest=?)
            )
            WHERE rn = 1
            ORDER BY price ASC
        """, (origin, dest, origin, dest)).fetchall()

        if not rows:
            continue

        rolling_avg = get_rolling_avg(db, origin, dest, ROLLING_AVG_DAYS)
        cheap_dates = []

        for row in rows:
            price = row["min_price"]
            flight_date = row["flight_date"]
            is_deal = False

            if threshold and price <= threshold:
                is_deal = True
            if rolling_avg and price <= rolling_avg * (1 - DROP_PERCENT / 100):
                is_deal = True

            if is_deal:
                cheap_dates.append({"flight_date": flight_date, "price": price})

        if cheap_dates:
            cheapest = cheap_dates[0]["price"]
            deals.append({
                "origin": origin,
                "dest": dest,
                "name": name,
                "cheapest": cheapest,
                "rolling_avg": int(rolling_avg) if rolling_avg else None,
                "dates": cheap_dates,
            })

    deals.sort(key=lambda d: d["cheapest"])
    return deals


MONTH_NAMES = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _collapse_ranges(days):
    """Turn [1,2,3,5,7,8,9] into '1-3, 5, 7-9'."""
    nums = sorted(int(d) for d in days)
    ranges = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
        else:
            ranges.append(f"{start}-{prev}" if prev > start else str(start))
            start = prev = n
    ranges.append(f"{start}-{prev}" if prev > start else str(start))
    return ", ".join(ranges)


def _group_dates_by_price(dates, avg=None):
    """Group dates by price, collapse contiguous days into ranges."""
    from collections import OrderedDict
    by_price = OrderedDict()
    for d in dates:
        by_price.setdefault(d["price"], []).append(d["flight_date"])

    parts = []
    for price, fdates in by_price.items():
        by_month = OrderedDict()
        for fd in fdates:
            m, day = int(fd[5:7]), fd[8:]
            by_month.setdefault(m, []).append(day.lstrip("0"))
        month_parts = []
        for m, days in by_month.items():
            month_parts.append(f"{MONTH_NAMES[m]} {_collapse_ranges(days)}")
        pct = f" ({int(price / avg * 100)}%)" if avg else ""
        parts.append(f"  ${price}{pct} — {' · '.join(month_parts)}")
    return "\n".join(parts)


def format_report(deals):
    """Format grouped deals as a single daily report."""
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    lines = [f"*CNX Deals* {today}"]

    for deal in deals:
        avg_info = f"  avg ${deal['rolling_avg']}" if deal["rolling_avg"] else ""
        dates_str = _group_dates_by_price(deal["dates"], deal.get("rolling_avg"))
        lines.append(f"\n\n*{deal['name']}*{avg_info}\n{dates_str}")

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
        total_dates = sum(len(d["dates"]) for d in deals)

        if send_telegram(token, chat_id, msg):
            for deal in deals:
                for d in deal["dates"][:5]:
                    log_alert(db, now, deal["origin"], deal["dest"], d["flight_date"], d["price"])
            db.commit()
            print(f"[alerts] sent {len(deals)} routes ({total_dates} dates) in 1 message", file=sys.stderr)
        else:
            print("[alerts] failed to send", file=sys.stderr)
    finally:
        db.close()


if __name__ == "__main__":
    run_alerts()
