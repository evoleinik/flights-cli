"""Adapter over fast_flights 3.x — returns normalized flight dicts.

The library's API broke between 2.x (HTML class-name scrape) and 3.x
(parses Google's embedded JSON). Keep that surface in ONE place so cli.py
and scan.py stay thin and there's a single file to fix when Google's
payload shape moves again.

Normalized flight dict keys:
    price       e.g. "SGD 308"  (display string)
    price_num   int             (sort/compare; 999999 if unknown)
    airline     str             (comma-joined if multiple carriers)
    stops       int             (0 = nonstop)
    duration    str             e.g. "3 hr 5 min" (total, incl. layovers)
    departure   str             e.g. "8:20 AM on Wed, Jul 1" (first leg)
    arrival     str             (last leg)
"""

from datetime import datetime

from fast_flights import FlightQuery, Passengers, create_query, get_flights
from fast_flights.exceptions import FlightsNotFound


def _dt(sd):
    """SimpleDatetime(date=(Y,M,D), time=(H,M)) -> datetime, or None."""
    try:
        y, mo, d = sd.date
        h, mi = sd.time
        return datetime(y, mo, d, h, mi)
    except Exception:
        return None


def _fmt_dt(sd):
    dt = _dt(sd)
    return dt.strftime("%-I:%M %p on %a, %b %-d") if dt else ""


def _fmt_dur(minutes):
    if not minutes or minutes < 0:
        return ""
    h, m = divmod(int(minutes), 60)
    return f"{h} hr {m} min" if h else f"{m} min"


def _total_minutes(legs):
    """Total trip time: sum of flight legs + layovers between them.

    Per-leg `duration` is true flight time (tz-correct). Layovers are
    computed at the connecting airport, where both times share a tz.
    """
    if not legs:
        return 0
    total = sum((l.duration or 0) for l in legs)
    for a, b in zip(legs, legs[1:]):
        arr, dep = _dt(a.arrival), _dt(b.departure)
        if arr and dep:
            total += max(0, int((dep - arr).total_seconds() // 60))
    return total


def search(origin, dest, dep_date, return_date=None, currency="USD", nonstop=False):
    """Search one route/date. Returns normalized flight dicts (possibly []).

    Network/parse errors propagate to the caller.
    """
    queries = [FlightQuery(date=dep_date, from_airport=origin, to_airport=dest)]
    trip = "one-way"
    if return_date:
        queries.append(FlightQuery(date=return_date, from_airport=dest, to_airport=origin))
        trip = "round-trip"

    q = create_query(
        flights=queries,
        trip=trip,
        seat="economy",
        passengers=Passengers(adults=1),
        currency=currency,
        max_stops=0 if nonstop else None,
    )

    try:
        result = get_flights(q)
    except FlightsNotFound:
        return []

    out = []
    for f in result:
        legs = f.flights or []
        price_num = int(f.price) if f.price else 999999
        out.append({
            "price": f"{currency} {f.price}".strip() if currency else str(f.price),
            "price_num": price_num,
            "airline": ", ".join(f.airlines) if f.airlines else "",
            "stops": max(0, len(legs) - 1),
            "duration": _fmt_dur(_total_minutes(legs)),
            "departure": _fmt_dt(legs[0].departure) if legs else "",
            "arrival": _fmt_dt(legs[-1].arrival) if legs else "",
        })
    return out
