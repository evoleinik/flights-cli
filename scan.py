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


def search_roundtrip(origin, dest, dep_date, ret_date, nonstop=True):
    """Search for round-trip flights. Returns same format as search_one."""
    t0 = time.monotonic()
    try:
        tfs = TFSData.from_interface(
            flight_data=[
                FlightData(date=dep_date, from_airport=origin, to_airport=dest),
                FlightData(date=ret_date, from_airport=dest, to_airport=origin),
            ],
            trip="round-trip",
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
        db.commit()
        print(f"[scan] one-way done: {done} queries, {ok} with prices, {errors} errors", file=sys.stderr)

        # Guided round-trip scan
        scan_roundtrip(db, now)

    finally:
        db.close()

    print(f"[scan] all done", file=sys.stderr)
    return errors == 0


SAMPLE_DURATIONS = [3, 7, 14]
ALL_DURATIONS = list(range(2, 15))  # 2-14 days
TOP_ROUTES = 10
TOP_DATES = 10
EXPAND_TOP = 5
EXPAND_DAYS = 2  # ±2 days around winners


def scan_roundtrip(db, now):
    """Guided round-trip scan using one-way prices as prior."""

    # Phase 1: Find top routes and dates from one-way scan
    top = db.execute("""
        SELECT origin, dest, flight_date, MIN(price) as min_price
        FROM prices
        WHERE return_date IS NULL
          AND scanned_at = ?
        GROUP BY origin, dest, flight_date
        ORDER BY min_price ASC
    """, (now,)).fetchall()

    if not top:
        print("[rt-scan] no one-way data to guide round-trip scan", file=sys.stderr)
        return

    # Get unique top routes
    seen_routes = set()
    top_routes = []
    for row in top:
        key = (row["origin"], row["dest"])
        if key not in seen_routes:
            seen_routes.add(key)
            top_routes.append(key)
        if len(top_routes) >= TOP_ROUTES:
            break

    # Get top dates per route
    route_dates = {}
    for row in top:
        key = (row["origin"], row["dest"])
        if key in seen_routes:
            route_dates.setdefault(key, []).append(row["flight_date"])

    # Phase 2: Sample scan — top routes × top dates × sample durations
    phase2_jobs = []
    for origin, dest in top_routes:
        dates = route_dates[(origin, dest)][:TOP_DATES]
        nonstop = db.execute("SELECT nonstop FROM routes WHERE origin=? AND dest=?", (origin, dest)).fetchone()["nonstop"]
        for dep_date in dates:
            dep = date.fromisoformat(dep_date)
            for stay in SAMPLE_DURATIONS:
                ret_date = (dep + timedelta(days=stay)).isoformat()
                phase2_jobs.append((origin, dest, dep_date, ret_date, bool(nonstop)))

    print(f"[rt-scan] phase 2: {len(phase2_jobs)} queries ({len(top_routes)} routes)", file=sys.stderr)
    phase2_results = _run_rt_batch(db, now, phase2_jobs)

    # Phase 3: Expand top winners — ±2 days × all durations
    phase2_results.sort(key=lambda x: x["price"])
    seen_combos = set()
    phase3_jobs = []
    for r in phase2_results[:EXPAND_TOP]:
        dep = date.fromisoformat(r["flight_date"])
        nonstop = db.execute("SELECT nonstop FROM routes WHERE origin=? AND dest=?", (r["origin"], r["dest"])).fetchone()["nonstop"]
        for day_offset in range(-EXPAND_DAYS, EXPAND_DAYS + 1):
            new_dep = dep + timedelta(days=day_offset)
            if new_dep <= date.today():
                continue
            for stay in ALL_DURATIONS:
                ret_date = (new_dep + timedelta(days=stay)).isoformat()
                combo = (r["origin"], r["dest"], new_dep.isoformat(), ret_date)
                if combo not in seen_combos:
                    seen_combos.add(combo)
                    phase3_jobs.append((r["origin"], r["dest"], new_dep.isoformat(), ret_date, bool(nonstop)))

    print(f"[rt-scan] phase 3: {len(phase3_jobs)} queries (expand top {EXPAND_TOP})", file=sys.stderr)
    _run_rt_batch(db, now, phase3_jobs)


def _run_rt_batch(db, now, jobs):
    """Run a batch of round-trip searches, store results, return cheapest per job."""
    results = []
    done = 0
    errors = 0

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {}
        for origin, dest, dep_date, ret_date, nonstop in jobs:
            f = pool.submit(search_roundtrip, origin, dest, dep_date, ret_date, nonstop)
            futures[f] = (origin, dest, dep_date, ret_date)

        for future in as_completed(futures):
            origin, dest, dep_date, ret_date = futures[future]
            try:
                r = future.result()
            except Exception as e:
                r = {"status": "error", "flights": [], "elapsed_ms": 0, "error": str(e)[:200]}
            done += 1

            log_scan(db, now, origin, dest, dep_date, r["status"],
                     error_msg=r.get("error"), flights_found=len(r["flights"]), elapsed_ms=r["elapsed_ms"])

            if r["flights"]:
                insert_prices(db, now, origin, dest, dep_date, r["flights"], CURRENCY, return_date=ret_date)
                cheapest = min(r["flights"], key=lambda x: x["price_num"])
                results.append({
                    "origin": origin, "dest": dest,
                    "flight_date": dep_date, "return_date": ret_date,
                    "price": cheapest["price_num"],
                })

            if r["status"] == "error":
                errors += 1

            if done % 50 == 0:
                db.commit()

    db.commit()
    print(f"  [{done} done, {len(results)} with prices, {errors} errors]", file=sys.stderr)
    return results


if __name__ == "__main__":
    success = run_scan()
    sys.exit(0 if success else 1)
