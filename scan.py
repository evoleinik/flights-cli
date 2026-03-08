#!/usr/bin/env python3
"""Scan all routes × dates, store prices + log every request."""

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone

from fast_flights import FlightData, Passengers, TFSData, get_flights_from_filter

from db import get_db, insert_prices, log_scan
from routes import seed_routes

CURRENCY = "USD"
DAYS_AHEAD = 60
WORKERS = 5


def parse_price(price_str):
    num = ""
    for c in price_str.replace(",", ""):
        if c.isdigit():
            num += c
    return int(num) if num else None


def parse_stops(stops_val):
    if isinstance(stops_val, int):
        return stops_val
    if stops_val == "Unknown" or stops_val is None:
        return None
    try:
        return int(stops_val)
    except (ValueError, TypeError):
        return None


def search_one(origin, dest, flight_date, nonstop=True):
    t0 = time.monotonic()
    try:
        tfs = TFSData.from_interface(
            flight_data=[FlightData(date=flight_date, from_airport=origin, to_airport=dest)],
            trip="one-way",
            seat="economy",
            passengers=Passengers(adults=1),
            max_stops=0 if nonstop else None,
        )
        result = get_flights_from_filter(tfs, currency=CURRENCY)
        elapsed = int((time.monotonic() - t0) * 1000)

        if not result.flights:
            return {"status": "no_flights", "flights": [], "elapsed_ms": elapsed}

        flights = []
        for fl in result.flights:
            price_num = parse_price(fl.price)
            if price_num is None:
                continue
            flights.append({
                "price_num": price_num,
                "airline": fl.name or "",
                "stops_num": parse_stops(fl.stops),
                "duration": fl.duration or "",
            })

        return {"status": "ok", "flights": flights, "elapsed_ms": elapsed}

    except Exception as e:
        elapsed = int((time.monotonic() - t0) * 1000)
        err = str(e)[:200]
        if "No flights found" in err:
            return {"status": "no_flights", "flights": [], "elapsed_ms": elapsed}
        return {"status": "error", "flights": [], "elapsed_ms": elapsed, "error": err}


def run_scan():
    db = get_db()
    seed_routes(db)

    routes = db.execute("SELECT origin, dest, name, nonstop FROM routes WHERE active=1").fetchall()
    today = date.today()
    dates = [(today + timedelta(days=d)).isoformat() for d in range(1, DAYS_AHEAD + 1)]

    now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    total = len(routes) * len(dates)
    done = 0
    ok = 0
    errors = 0

    print(f"[scan] {len(routes)} routes × {len(dates)} dates = {total} queries", file=sys.stderr)

    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {}
            for route in routes:
                for flight_date in dates:
                    f = pool.submit(search_one, route["origin"], route["dest"], flight_date, bool(route["nonstop"]))
                    futures[f] = (route["origin"], route["dest"], flight_date)

            for future in as_completed(futures):
                origin, dest, flight_date = futures[future]
                try:
                    r = future.result()
                except Exception as e:
                    r = {"status": "error", "flights": [], "elapsed_ms": 0, "error": str(e)[:200]}
                done += 1

                log_scan(db, now, origin, dest, flight_date, r["status"],
                         error_msg=r.get("error"), flights_found=len(r["flights"]), elapsed_ms=r["elapsed_ms"])

                if r["flights"]:
                    insert_prices(db, now, origin, dest, flight_date, r["flights"], CURRENCY)
                    ok += 1

                if r["status"] == "error":
                    errors += 1

                if done % 100 == 0:
                    db.commit()
                    print(f"  [{done}/{total}] {ok} ok, {errors} errors", file=sys.stderr)
    finally:
        db.commit()
        db.close()

    print(f"[scan] done: {done} queries, {ok} with prices, {errors} errors", file=sys.stderr)
    return errors == 0


if __name__ == "__main__":
    success = run_scan()
    sys.exit(0 if success else 1)
