#!/usr/bin/env python3
"""Check latest prices against thresholds + rolling avg, send Telegram alerts."""

import os
import sys
import time
from datetime import date, datetime, timezone

import requests

from db import get_db, get_rolling_avg, get_rt_rolling_avg, log_alert, was_alerted_recently

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


def find_rt_deals(db):
    """Find cheapest round-trip deals per route."""
    routes = db.execute("SELECT origin, dest, name, threshold FROM routes WHERE active=1").fetchall()
    deals = []

    for route in routes:
        origin, dest, name, threshold = route["origin"], route["dest"], route["name"], route["threshold"]
        rt_threshold = threshold * 2 if threshold else None

        # Get cheapest round-trip per (flight_date, return_date) from latest RT scan
        rows = db.execute("""
            SELECT flight_date, return_date, price AS min_price, airline, stops, duration
            FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY flight_date, return_date ORDER BY price ASC) AS rn
                FROM prices
                WHERE origin=? AND dest=?
                  AND return_date IS NOT NULL
                  AND scanned_at = (SELECT MAX(scanned_at) FROM prices WHERE origin=? AND dest=? AND return_date IS NOT NULL)
            )
            WHERE rn = 1
            ORDER BY price ASC
        """, (origin, dest, origin, dest)).fetchall()

        if not rows:
            continue

        rolling_avg = get_rt_rolling_avg(db, origin, dest, ROLLING_AVG_DAYS)
        cheap_trips = []

        for row in rows:
            price = row["min_price"]
            # Deal = below threshold OR ≤75% of rolling avg
            is_deal = False
            if rt_threshold and price <= rt_threshold:
                is_deal = True
            if rolling_avg and price <= rolling_avg * 0.75:
                is_deal = True

            if is_deal:
                dep = row["flight_date"]
                ret = row["return_date"]
                stay = (date.fromisoformat(ret) - date.fromisoformat(dep)).days
                cheap_trips.append({
                    "flight_date": dep,
                    "return_date": ret,
                    "price": price,
                    "stay": stay,
                })

        if cheap_trips:
            deals.append({
                "origin": origin,
                "dest": dest,
                "name": name,
                "cheapest": cheap_trips[0]["price"],
                "rolling_avg": int(rolling_avg) if rolling_avg else None,
                "trips": cheap_trips,
            })

    deals.sort(key=lambda d: d["cheapest"])
    return deals


MONTH_NAMES = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

FLAGS = {
    "AUH": "🇦🇪", "CAN": "🇨🇳", "CKG": "🇨🇳", "HAN": "🇻🇳", "HKG": "🇭🇰",
    "ICN": "🇰🇷", "JHG": "🇨🇳", "KHH": "🇹🇼", "KIX": "🇯🇵", "KMG": "🇨🇳",
    "KUL": "🇲🇾", "LPQ": "🇱🇦", "MDL": "🇲🇲", "PEK": "🇨🇳", "PUS": "🇰🇷",
    "PVG": "🇨🇳", "RGN": "🇲🇲", "SIN": "🇸🇬", "TFU": "🇨🇳", "TPE": "🇹🇼",
    "XIY": "🇨🇳", "BKK": "🇹🇭", "DMK": "🇹🇭", "HDY": "🇹🇭", "HHQ": "🇹🇭",
    "HKT": "🇹🇭", "KBV": "🇹🇭", "KKC": "🇹🇭", "URT": "🇹🇭", "USM": "🇹🇭",
    "UTH": "🇹🇭", "UTP": "🇹🇭",
}


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
    lines = [f"✈ *CNX Deals* {today}"]

    for deal in deals:
        flag = FLAGS.get(deal["dest"], "")
        avg_info = f"  avg ${deal['rolling_avg']}" if deal["rolling_avg"] else ""
        dates_str = _group_dates_by_price(deal["dates"], deal.get("rolling_avg"))
        lines.append(f"\n\n{flag} *{deal['name']}*{avg_info}\n{dates_str}")

    return "\n".join(lines)


def format_rt_report(deals):
    """Format round-trip deals as trip ideas."""
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    lines = [f"✈ *CNX Trip Ideas* {today}"]

    for deal in deals:
        flag = FLAGS.get(deal["dest"], "")
        avg_info = f"  avg ${deal['rolling_avg']}" if deal["rolling_avg"] else ""
        lines.append(f"\n\n{flag} *{deal['name']}*{avg_info}")

        # Group trips by price, show date range + stay
        from collections import OrderedDict
        by_price = OrderedDict()
        for t in deal["trips"]:
            by_price.setdefault(t["price"], []).append(t)

        # Show top 5 cheapest trips per route
        all_trips = []
        for price, trips in by_price.items():
            for t in trips:
                all_trips.append((price, t))
        for price, t in all_trips[:5]:
            pct = f" ({int(price / deal['rolling_avg'] * 100)}%)" if deal.get("rolling_avg") else ""
            dep_m, dep_d = int(t["flight_date"][5:7]), int(t["flight_date"][8:])
            ret_m, ret_d = int(t["return_date"][5:7]), int(t["return_date"][8:])
            if dep_m == ret_m:
                dates = f"{MONTH_NAMES[dep_m]} {dep_d}–{ret_d} ({t['stay']}d)"
            else:
                dates = f"{MONTH_NAMES[dep_m]} {dep_d}–{MONTH_NAMES[ret_m]} {ret_d} ({t['stay']}d)"
            lines.append(f"  ${price}{pct} — {dates}")

    return "\n".join(lines)


def run_alerts():
    db = get_db()
    try:
        tg = load_telegram_config()
        token = tg["TELEGRAM_BOT_TOKEN"]
        chat_id = tg.get("TELEGRAM_FLIGHTS_CHANNEL", tg["TELEGRAM_CHANNEL_ID"])

        now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

        # One-way deals
        deals = find_deals(db)
        if deals:
            msg = format_report(deals)
            if send_telegram(token, chat_id, msg):
                for deal in deals:
                    for d in deal["dates"][:5]:
                        log_alert(db, now, deal["origin"], deal["dest"], d["flight_date"], d["price"])
                print(f"[alerts] sent {len(deals)} one-way deals", file=sys.stderr)
            time.sleep(1)

        # Round-trip deals
        rt_deals = find_rt_deals(db)
        if rt_deals:
            msg = format_rt_report(rt_deals)
            if send_telegram(token, chat_id, msg):
                for deal in rt_deals:
                    for t in deal["trips"][:5]:
                        log_alert(db, now, deal["origin"], deal["dest"], t["flight_date"], t["price"])
                print(f"[alerts] sent {len(rt_deals)} trip ideas", file=sys.stderr)

        if not deals and not rt_deals:
            print("[alerts] no deals found", file=sys.stderr)

        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    run_alerts()
