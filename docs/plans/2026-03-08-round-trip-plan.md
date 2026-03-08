# Round-Trip Deal Scanner — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add guided round-trip scanning that uses one-way price data to efficiently find the cheapest round-trip combos, reported as "Trip Ideas" in the daily Telegram message.

**Architecture:** After the existing one-way scan, `scan_roundtrip()` uses one-way results to guide targeted round-trip queries (top 10 routes × top 10 dates × sample durations, then expand winners). Results stored in the same `prices` table with a `return_date` column. `alerts.py` generates a combined report with both one-way deals and trip ideas.

**Tech Stack:** Python 3.12, fast-flights, sqlite3, concurrent.futures (all existing)

---

### Task 1: DB schema migration + insert_prices update

**Files:**
- Modify: `~/src/cheap-flights/db.py:22-35` (add return_date column)
- Modify: `~/src/cheap-flights/db.py:79-84` (insert_prices accepts return_date)
- Modify: `~/src/cheap-flights/db.py:87-93` (get_rolling_avg filters by trip type)

**Step 1: Add `return_date` column to prices table**

In `db.py`, change the prices table schema in `_init_tables` (line 22-33). Add `return_date TEXT` after `flight_date`:

```python
        CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY,
            scanned_at TEXT NOT NULL,
            origin TEXT NOT NULL,
            dest TEXT NOT NULL,
            flight_date TEXT NOT NULL,
            return_date TEXT,
            price INTEGER NOT NULL,
            currency TEXT NOT NULL DEFAULT 'USD',
            airline TEXT,
            stops INTEGER,
            duration TEXT
        );
```

Also add a migration after `_init_tables` to handle existing DBs. Add this at the end of `_init_tables`:

```python
    # Migration: add return_date if missing (existing DBs)
    cols = [r[1] for r in db.execute("PRAGMA table_info(prices)").fetchall()]
    if "return_date" not in cols:
        db.execute("ALTER TABLE prices ADD COLUMN return_date TEXT")
```

**Step 2: Update `insert_prices` to accept `return_date`**

Change `insert_prices` (line 79-84) to:

```python
def insert_prices(db, scanned_at, origin, dest, flight_date, flights, currency="USD", return_date=None):
    """Insert flight prices. flights is list of dicts with keys: price_num, airline, stops_num, duration."""
    db.executemany(
        "INSERT INTO prices (scanned_at, origin, dest, flight_date, return_date, price, currency, airline, stops, duration) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(scanned_at, origin, dest, flight_date, return_date, f["price_num"], currency, f["airline"], f.get("stops_num"), f["duration"]) for f in flights],
    )
```

**Step 3: Add `get_rt_rolling_avg`**

Add after `get_rolling_avg` (after line 93):

```python
def get_rt_rolling_avg(db, origin, dest, days=14):
    """Return AVG(price) for round-trip prices over last N days, or None."""
    row = db.execute(
        "SELECT AVG(price) as avg_price FROM prices WHERE origin=? AND dest=? AND return_date IS NOT NULL AND scanned_at >= datetime('now', ?)",
        (origin, dest, f"-{days} days"),
    ).fetchone()
    return row["avg_price"] if row else None
```

**Step 4: Verify migration works on existing DB**

Run: `cd ~/src/cheap-flights && python3 -c "from db import get_db; db = get_db(); print([r[1] for r in db.execute('PRAGMA table_info(prices)').fetchall()]); db.close()"`
Expected: list includes `return_date`

**Step 5: Commit**

```bash
cd ~/src/cheap-flights
git add db.py
git commit -m "feat: add return_date to prices, rt rolling avg helper"
```

---

### Task 2: Round-trip search + guided scan algorithm

**Files:**
- Modify: `~/src/cheap-flights/scan.py`

**Step 1: Add `search_roundtrip` function**

Add after `search_one` (after line 73):

```python
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
```

**Step 2: Add `scan_roundtrip` function**

Add after `run_scan` (before `if __name__`):

```python
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
        SELECT origin, dest, flight_date, MIN(price) as min_price, nonstop
        FROM prices p
        JOIN routes r ON p.origin = r.origin AND p.dest = r.dest
        WHERE p.return_date IS NULL
          AND p.scanned_at = ?
          AND r.active = 1
        GROUP BY p.origin, p.dest, p.flight_date
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

    print(f"[rt-scan] phase 2: {len(phase2_jobs)} queries ({len(top_routes)} routes × ≤{TOP_DATES} dates × {len(SAMPLE_DURATIONS)} durations)", file=sys.stderr)
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
```

**Step 3: Update `run_scan` to call `scan_roundtrip` after one-way scan**

Change the end of `run_scan` (lines 121-126). Move `db.close()` out and add the round-trip call. Replace the entire `run_scan` function:

```python
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

        # Phase 2+3: Guided round-trip scan
        scan_roundtrip(db, now)

    finally:
        db.close()

    return errors == 0
```

**Step 4: Test round-trip search**

Run: `cd ~/src/cheap-flights && python3 -c "from scan import search_roundtrip; r = search_roundtrip('CNX', 'KUL', '2026-03-20', '2026-03-27'); print(r['status'], len(r['flights']), 'flights'); print('cheapest:', min(f['price_num'] for f in r['flights'])) if r['flights'] else None"`
Expected: `ok N flights` with round-trip prices

**Step 5: Commit**

```bash
cd ~/src/cheap-flights
git add scan.py
git commit -m "feat: guided round-trip scan — sample then expand winners"
```

---

### Task 3: Trip Ideas report in alerts.py

**Files:**
- Modify: `~/src/cheap-flights/alerts.py`

**Step 1: Add `find_rt_deals` function**

Add after `find_deals` (after line 94):

```python
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
            is_deal = False

            if rt_threshold and price <= rt_threshold:
                is_deal = True
            if rolling_avg and price <= rolling_avg * (1 - DROP_PERCENT / 100):
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
```

**Step 2: Add `format_rt_report` function**

Add after `format_report` (after line 158):

```python
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

        for price, trips in by_price.items():
            pct = f" ({int(price / deal['rolling_avg'] * 100)}%)" if deal.get("rolling_avg") else ""
            trip_strs = []
            for t in trips[:3]:  # max 3 per price level
                dep_m, dep_d = int(t["flight_date"][5:7]), int(t["flight_date"][8:])
                ret_m, ret_d = int(t["return_date"][5:7]), int(t["return_date"][8:])
                if dep_m == ret_m:
                    trip_strs.append(f"{MONTH_NAMES[dep_m]} {dep_d}–{ret_d} ({t['stay']}d)")
                else:
                    trip_strs.append(f"{MONTH_NAMES[dep_m]} {dep_d}–{MONTH_NAMES[ret_m]} {ret_d} ({t['stay']}d)")
            extra = f" +{len(trips) - 3} more" if len(trips) > 3 else ""
            lines.append(f"  ${price}{pct} — {' · '.join(trip_strs)}{extra}")

    return "\n".join(lines)
```

**Step 3: Update `run_alerts` to send both reports**

Add import at top of file (line 11):

```python
from db import get_db, get_rolling_avg, get_rt_rolling_avg, log_alert, was_alerted_recently
```

Replace `run_alerts` (lines 161-193):

```python
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
```

**Step 4: Test formatting**

Run: `cd ~/src/cheap-flights && python3 -c "
from alerts import format_rt_report
deals = [{'origin':'CNX','dest':'KUL','name':'Kuala Lumpur','cheapest':125,'rolling_avg':180,
  'trips':[{'flight_date':'2026-04-08','return_date':'2026-04-15','price':125,'stay':7},
           {'flight_date':'2026-03-19','return_date':'2026-03-22','price':130,'stay':3}]}]
print(format_rt_report(deals))
"`
Expected:
```
✈ *CNX Trip Ideas* 2026-03-08

🇲🇾 *Kuala Lumpur*  avg $180
  $125 (69%) — Apr 8–15 (7d)
  $130 (72%) — Mar 19–22 (3d)
```

**Step 5: Commit**

```bash
cd ~/src/cheap-flights
git add alerts.py
git commit -m "feat: trip ideas report — round-trip deals with stay duration"
```

---

### Task 4: Smoke test — small round-trip scan

**Step 1: Run a small guided round-trip scan**

Run: `cd ~/src/cheap-flights && python3 -c "
from scan import search_roundtrip
r = search_roundtrip('CNX', 'KUL', '2026-03-20', '2026-03-27')
print('KUL 7d:', r['status'], len(r['flights']), 'flights')
if r['flights']: print('  cheapest:', min(f['price_num'] for f in r['flights']))

r2 = search_roundtrip('CNX', 'DMK', '2026-04-01', '2026-04-04')
print('DMK 3d:', r2['status'], len(r2['flights']), 'flights')
if r2['flights']: print('  cheapest:', min(f['price_num'] for f in r2['flights']))
"`
Expected: round-trip prices (higher than one-way)

**Step 2: Run full scan with round-trip**

Run: `cd ~/src/cheap-flights && python3 scan.py`
Expected:
```
[scan] 32 routes × 60 dates = 1920 queries
  ...
[scan] one-way done: 1920 queries, N with prices, M errors
[rt-scan] phase 2: ~300 queries (10 routes × ≤10 dates × 3 durations)
  ...
[rt-scan] phase 3: ~325 queries (expand top 5)
  ...
```

**Step 3: Run alerts**

Run: `cd ~/src/cheap-flights && python3 alerts.py`
Expected: sends one-way deals message + trip ideas message to @cnx_cheap_flights

**Step 4: Verify DB**

Run: `sqlite3 ~/.cheap-flights/flights.db "SELECT COUNT(*) as total, COUNT(return_date) as roundtrip, COUNT(*)-COUNT(return_date) as oneway FROM prices"`
Expected: shows both one-way and round-trip price counts

**Step 5: Commit**

```bash
cd ~/src/cheap-flights
git add -A
git commit -m "feat: verified round-trip scan end-to-end"
```

---

## Outcome

After completing all tasks:
- Daily scan runs one-way (1,920 queries) then guided round-trip (~625 queries)
- Two Telegram messages per day: one-way deals + trip ideas
- Trip ideas show total round-trip cost with stay duration
- Round-trip thresholds are 2× one-way (no extra config needed)
- Extra ~2 min added to daily scan time
