#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///
"""Check latest prices against thresholds + rolling avg, send Telegram alerts."""

import os
import sys
import time
from datetime import date, datetime, timezone

import requests

from db import get_db, get_rolling_avg, get_rt_rolling_avg, log_alert, was_alerted_recently

# Telegram creds come from env vars first; a KEY=VALUE file is the fallback.
# Override the file location with TELEGRAM_CONFIG (e.g. point it at a synced dir).
TELEGRAM_CONFIG = os.environ.get(
    "TELEGRAM_CONFIG", os.path.expanduser("~/.config/flights-cli/telegram")
)
ROLLING_AVG_DAYS = 14
DROP_PERCENT = 30  # alert if 30%+ below rolling avg


def load_telegram_config():
    """Resolve Telegram bot token + chat id.

    Order: env vars (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID), then a KEY=VALUE
    file at $TELEGRAM_CONFIG. Exits with a hint if neither yields both.
    """
    cfg = {}
    if os.path.exists(TELEGRAM_CONFIG):
        with open(TELEGRAM_CONFIG) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    cfg[k.strip()] = v.strip()

    token = os.environ.get("TELEGRAM_BOT_TOKEN") or cfg.get("TELEGRAM_BOT_TOKEN")
    chat_id = (
        os.environ.get("TELEGRAM_CHAT_ID")
        or cfg.get("TELEGRAM_CHAT_ID")
        or cfg.get("TELEGRAM_FLIGHTS_CHANNEL")  # legacy key
        or cfg.get("TELEGRAM_CHANNEL_ID")  # legacy key
    )
    if not token or not chat_id:
        sys.exit(
            "telegram not configured — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID "
            f"env vars, or put them in {TELEGRAM_CONFIG}"
        )
    return {"token": token, "chat_id": chat_id}


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
    routes = db.execute("SELECT origin, dest, name, threshold FROM routes WHERE active=1 AND origin='CNX'").fetchall()
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
                  AND return_date IS NULL
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
    routes = db.execute("SELECT origin, dest, name, threshold FROM routes WHERE active=1 AND origin='CNX'").fetchall()
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


def find_chains(db, home="CNX", min_stops=2, max_stops=3, min_stay=2, max_stay=5):
    """Find cheapest multi-city chains starting and ending at home."""
    from itertools import permutations

    # Get all cheapest one-way prices per (origin, dest, date)
    rows = db.execute("""
        SELECT origin, dest, flight_date, MIN(price) as price
        FROM prices
        WHERE return_date IS NULL
          AND price > 0
          AND scanned_at >= datetime('now', '-2 days')
        GROUP BY origin, dest, flight_date
    """).fetchall()

    # Build lookup: (origin, dest) -> [(date, price), ...]
    edges = {}
    for r in rows:
        edges.setdefault((r["origin"], r["dest"]), []).append(
            (date.fromisoformat(r["flight_date"]), r["price"])
        )

    # Mirror CNX outbound as return legs (hub→CNX ≈ CNX→hub)
    for (orig, dest) in list(edges):
        if orig == home:
            reverse = (dest, home)
            if reverse not in edges:
                edges[reverse] = edges[(orig, dest)]

    # Get hub airports (non-CNX origins that have outbound flights)
    hubs = set()
    for (orig, dest) in edges:
        if orig != home:
            hubs.add(orig)
    # Also include destinations reachable from CNX that are hubs
    hubs = hubs & {dest for (orig, dest) in edges if orig == home}

    if not hubs:
        return []

    chains = []
    for n_stops in range(min_stops, max_stops + 1):
        for perm in permutations(hubs, n_stops):
            # Build circuit: home -> perm[0] -> perm[1] -> ... -> home
            route = [home] + list(perm) + [home]

            # Check all legs exist
            legs = [(route[i], route[i+1]) for i in range(len(route)-1)]
            if not all(leg in edges for leg in legs):
                continue

            # Find cheapest dates for this circuit
            _find_dated_chains(edges, legs, min_stay, max_stay, chains)

    chains.sort(key=lambda c: c["total"])

    # Deduplicate: keep cheapest per route signature
    seen = set()
    unique = []
    for c in chains:
        sig = tuple(leg["from"] for leg in c["legs"]) + (c["legs"][-1]["to"],)
        if sig not in seen:
            seen.add(sig)
            unique.append(c)
    return unique[:10]


def _find_dated_chains(edges, legs, min_stay, max_stay, results):
    """Find cheapest dated itinerary for a given leg sequence."""
    first_leg = legs[0]
    # Try each departure date for the first leg
    for dep_date, dep_price in edges[first_leg]:
        itinerary = [{"from": first_leg[0], "to": first_leg[1], "date": dep_date, "price": dep_price}]
        total = dep_price
        valid = True

        for leg in legs[1:]:
            # Find cheapest flight on this leg within stay window
            arrive = itinerary[-1]["date"]
            best = None
            for d, p in edges[leg]:
                gap = (d - arrive).days
                if min_stay <= gap <= max_stay:
                    if best is None or p < best[1]:
                        best = (d, p)
            if best is None:
                valid = False
                break
            itinerary.append({"from": leg[0], "to": leg[1], "date": best[0], "price": best[1]})
            total += best[1]

        if valid:
            trip_days = (itinerary[-1]["date"] - itinerary[0]["date"]).days
            results.append({"legs": itinerary, "total": total, "days": trip_days})


def format_chain_report(chains):
    """Format backpacker chain routes."""
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    lines = [f"🌏 *CNX Backpacker Routes* {today}"]

    for chain in chains:
        route = " → ".join(leg["from"] for leg in chain["legs"]) + " → " + chain["legs"][-1]["to"]
        lines.append(f"\n\n*{route}*  ${chain['total']} ({chain['days']}d)")
        for leg in chain["legs"]:
            d = leg["date"]
            lines.append(f"  {MONTH_NAMES[d.month]} {d.day}: {leg['from']}→{leg['to']} ${leg['price']}")

    return "\n".join(lines)


def run_alerts():
    db = get_db()
    try:
        tg = load_telegram_config()
        token = tg["token"]
        chat_id = tg["chat_id"]

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
            time.sleep(1)

        # Backpacker chains
        chains = find_chains(db)
        if chains:
            msg = format_chain_report(chains)
            if send_telegram(token, chat_id, msg):
                print(f"[alerts] sent {len(chains)} backpacker routes", file=sys.stderr)

        if not deals and not rt_deals and not chains:
            print("[alerts] no deals found", file=sys.stderr)

        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    run_alerts()
