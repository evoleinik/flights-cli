#!/usr/bin/env python3
"""Find the cheapest flights for a route across a date range.

Usage:
  cheap-flights CNX SIN                      # one-way, next 30 days
  cheap-flights CNX SIN --round 7            # round trip, 7-day stay
  cheap-flights CNX SIN -m 2026-04           # specific month
  cheap-flights CNX SIN -f 2026-03-10 -t 2026-03-25  # date range
  cheap-flights CNX SIN --nonstop            # nonstop only
  cheap-flights CNX SIN --currency EUR       # currency (default USD)
  cheap-flights CNX SIN --json               # JSON output
"""

import argparse
import json
import sys
from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

from fast_flights import FlightData, Passengers, TFSData, get_flights_from_filter


def search_date(origin, dest, dep_date, return_date, currency, nonstop):
    try:
        flight_data = [FlightData(date=dep_date, from_airport=origin, to_airport=dest)]
        trip = "one-way"
        if return_date:
            flight_data.append(FlightData(date=return_date, from_airport=dest, to_airport=origin))
            trip = "round-trip"

        tfs = TFSData.from_interface(
            flight_data=flight_data,
            trip=trip,
            seat="economy",
            passengers=Passengers(adults=1),
            max_stops=0 if nonstop else None,
        )
        result = get_flights_from_filter(tfs, currency=currency)

        if not result.flights:
            return {"date": dep_date, "return": return_date, "flights": []}

        flights = []
        for fl in result.flights:
            try:
                price_str = fl.price.replace(",", "").strip()
                num = ""
                for c in price_str:
                    if c.isdigit():
                        num += c
                price_num = int(num) if num else 999999
            except Exception:
                price_num = 999999

            flights.append({
                "price": fl.price,
                "price_num": price_num,
                "airline": fl.name,
                "stops": fl.stops,
                "duration": fl.duration,
                "departure": fl.departure,
                "arrival": fl.arrival,
            })

        return {"date": dep_date, "return": return_date, "flights": flights}
    except Exception as e:
        return {"date": dep_date, "return": return_date, "flights": [], "error": str(e)[:80]}


def main():
    parser = argparse.ArgumentParser(description="Find cheapest flights across dates")
    parser.add_argument("origin", help="Origin airport code (e.g. CNX)")
    parser.add_argument("dest", help="Destination airport code (e.g. SIN)")
    parser.add_argument("-f", "--from-date", help="Start date (YYYY-MM-DD)")
    parser.add_argument("-t", "--to-date", help="End date (YYYY-MM-DD)")
    parser.add_argument("-m", "--month", help="Month (YYYY-MM)")
    parser.add_argument("--round", type=int, metavar="DAYS", help="Round trip with N-day stay")
    parser.add_argument("--nonstop", action="store_true", help="Nonstop flights only")
    parser.add_argument("--currency", default="USD", help="Currency code (default: USD)")
    parser.add_argument("--json", action="store_true", dest="json_out", help="JSON output")
    parser.add_argument("-w", "--workers", type=int, default=5, help="Parallel workers (default: 5)")
    args = parser.parse_args()

    today = date.today()

    # Build date range
    if args.month:
        y, m = map(int, args.month.split("-"))
        start = date(y, m, 1)
        end = date(y, m, monthrange(y, m)[1])
    elif args.from_date:
        start = date.fromisoformat(args.from_date)
        end = date.fromisoformat(args.to_date) if args.to_date else start + timedelta(days=30)
    else:
        start = today + timedelta(days=1)
        end = start + timedelta(days=30)

    # Skip past dates
    if start <= today:
        start = today + timedelta(days=1)

    dates = []
    d = start
    while d <= end:
        dep = d.isoformat()
        ret = (d + timedelta(days=args.round)).isoformat() if args.round else None
        dates.append((dep, ret))
        d += timedelta(days=1)

    if not dates:
        print("No valid dates in range.", file=sys.stderr)
        sys.exit(1)

    trip_type = f"round trip ({args.round}d)" if args.round else "one-way"
    label = f"{args.origin} -> {args.dest}"
    if not args.json_out:
        print(f"Searching {label} | {trip_type} | {len(dates)} days | {args.currency}", file=sys.stderr)

    # Search all dates
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {}
        for dep, ret in dates:
            f = pool.submit(search_date, args.origin, args.dest, dep, ret, args.currency, args.nonstop)
            futures[f] = dep

        done = 0
        for future in as_completed(futures):
            done += 1
            r = future.result()
            results.append(r)
            if not args.json_out:
                if r["flights"]:
                    best = min(r["flights"], key=lambda x: x["price_num"])
                    print(f"  [{done:2d}/{len(dates)}] {r['date']}: {best['price']}", file=sys.stderr)
                else:
                    err = r.get("error", "no flights")[:40]
                    print(f"  [{done:2d}/{len(dates)}] {r['date']}: - ({err})", file=sys.stderr)

    results.sort(key=lambda x: x["date"])

    # JSON output
    if args.json_out:
        out = []
        for r in results:
            if r["flights"]:
                best = min(r["flights"], key=lambda x: x["price_num"])
                out.append({"date": r["date"], "return": r.get("return"), **best})
        json.dump(out, sys.stdout, indent=2)
        sys.exit(0)

    # Table output
    print()
    hdr_date = "Depart" if not args.round else "Depart     Return"
    print(f"{'Date':<28} {'Price':>8}  {'Stops':>5}  {'Airline':<25} {'Duration'}")
    print("-" * 85)

    cheapest = None
    for r in results:
        if r["flights"]:
            best = min(r["flights"], key=lambda x: x["price_num"])
            if cheapest is None or best["price_num"] < cheapest["price_num"]:
                cheapest = {**best, "date": r["date"], "return": r.get("return")}

            date_col = r["date"]
            if r.get("return"):
                date_col += f"  {r['return']}"

            stops = str(best["stops"]) if best["stops"] != "Unknown" else "?"
            marker = ""
            print(f"{date_col:<28} {best['price']:>8}  {stops:>5}  {best['airline']:<25} {best['duration']}")
        else:
            date_col = r["date"]
            if r.get("return"):
                date_col += f"  {r['return']}"
            print(f"{date_col:<28}        -")

    if cheapest:
        print()
        ret_info = f" -> {cheapest['return']}" if cheapest.get("return") else ""
        print(f"CHEAPEST: {cheapest['date']}{ret_info} -- {cheapest['price']} on {cheapest['airline']} ({cheapest['duration']})")


if __name__ == "__main__":
    main()
