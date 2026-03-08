# cheap-flights Deal Scanner — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Daily scanner that finds cheap nonstop flights from CNX across 32 destinations × 60 days, stores every price in SQLite, and sends Telegram alerts for deals.

**Architecture:** Single Python package. `scan.py` drives fast-flights queries in parallel, logs every request to SQLite. `alerts.py` reads latest prices, compares to thresholds + rolling averages, sends Telegram messages. Cron on box runs both daily at 3am.

**Tech Stack:** Python 3.12, fast-flights, sqlite3 (stdlib), requests (for Telegram API), concurrent.futures

---

### Task 1: Project scaffold + DB schema

**Files:**
- Create: `~/src/cheap-flights/db.py`
- Create: `~/src/cheap-flights/routes.py`

**Step 1: Create `db.py` — database init and helpers**

```python
import sqlite3
import os

DB_PATH = os.path.expanduser("~/.cheap-flights/flights.db")


def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    _init_tables(db)
    return db


def _init_tables(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS prices (
            id          INTEGER PRIMARY KEY,
            scanned_at  TEXT NOT NULL,
            origin      TEXT NOT NULL,
            dest        TEXT NOT NULL,
            flight_date TEXT NOT NULL,
            price       INTEGER NOT NULL,
            currency    TEXT NOT NULL DEFAULT 'USD',
            airline     TEXT,
            stops       INTEGER,
            duration    TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_prices_route_date
            ON prices(origin, dest, flight_date);
        CREATE INDEX IF NOT EXISTS idx_prices_scanned
            ON prices(scanned_at);

        CREATE TABLE IF NOT EXISTS scan_log (
            id            INTEGER PRIMARY KEY,
            scanned_at    TEXT NOT NULL,
            origin        TEXT NOT NULL,
            dest          TEXT NOT NULL,
            flight_date   TEXT NOT NULL,
            status        TEXT NOT NULL,
            error_msg     TEXT,
            flights_found INTEGER DEFAULT 0,
            elapsed_ms    INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_scanlog_scanned
            ON scan_log(scanned_at);

        CREATE TABLE IF NOT EXISTS routes (
            origin    TEXT NOT NULL,
            dest      TEXT NOT NULL,
            name      TEXT NOT NULL,
            threshold INTEGER,
            nonstop   INTEGER DEFAULT 1,
            active    INTEGER DEFAULT 1,
            PRIMARY KEY (origin, dest)
        );

        CREATE TABLE IF NOT EXISTS alert_log (
            id         INTEGER PRIMARY KEY,
            alerted_at TEXT NOT NULL,
            origin     TEXT NOT NULL,
            dest       TEXT NOT NULL,
            flight_date TEXT NOT NULL,
            price      INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_alertlog_route_date
            ON alert_log(origin, dest, flight_date, alerted_at);
    """)
    db.commit()


def log_scan(db, scanned_at, origin, dest, flight_date, status, error_msg=None, flights_found=0, elapsed_ms=0):
    db.execute(
        "INSERT INTO scan_log (scanned_at, origin, dest, flight_date, status, error_msg, flights_found, elapsed_ms) VALUES (?,?,?,?,?,?,?,?)",
        (scanned_at, origin, dest, flight_date, status, error_msg, flights_found, elapsed_ms),
    )


def insert_prices(db, scanned_at, origin, dest, flight_date, flights, currency="USD"):
    for f in flights:
        db.execute(
            "INSERT INTO prices (scanned_at, origin, dest, flight_date, price, currency, airline, stops, duration) VALUES (?,?,?,?,?,?,?,?,?)",
            (scanned_at, origin, dest, flight_date, f["price_num"], currency, f["airline"], f.get("stops_num"), f["duration"]),
        )


def get_rolling_avg(db, origin, dest, days=14):
    row = db.execute(
        "SELECT AVG(price) as avg_price FROM prices WHERE origin=? AND dest=? AND scanned_at > datetime('now', ?)",
        (origin, dest, f"-{days} days"),
    ).fetchone()
    return row["avg_price"] if row and row["avg_price"] else None


def was_alerted_recently(db, origin, dest, flight_date, hours=24):
    row = db.execute(
        "SELECT 1 FROM alert_log WHERE origin=? AND dest=? AND flight_date=? AND alerted_at > datetime('now', ?)",
        (origin, dest, flight_date, f"-{hours} hours"),
    ).fetchone()
    return row is not None


def log_alert(db, alerted_at, origin, dest, flight_date, price):
    db.execute(
        "INSERT INTO alert_log (alerted_at, origin, dest, flight_date, price) VALUES (?,?,?,?,?)",
        (alerted_at, origin, dest, flight_date, price),
    )
```

**Step 2: Create `routes.py` — all 32 nonstop routes with thresholds**

```python
# Nonstop routes from CNX with deal thresholds (USD)
# Thresholds based on initial scans — will be refined by rolling avg after 1 week

ROUTES = [
    # International
    ("CNX", "AUH", "Abu Dhabi", 250),
    ("CNX", "CAN", "Guangzhou", 80),
    ("CNX", "CKG", "Chongqing", 80),
    ("CNX", "HAN", "Hanoi", 55),
    ("CNX", "HKG", "Hong Kong", 90),
    ("CNX", "ICN", "Seoul", 150),
    ("CNX", "JHG", "Jinghong", 60),
    ("CNX", "KHH", "Kaohsiung", 100),
    ("CNX", "KIX", "Osaka", 150),
    ("CNX", "KMG", "Kunming", 60),
    ("CNX", "KUL", "Kuala Lumpur", 50),
    ("CNX", "LPQ", "Luang Prabang", 80),
    ("CNX", "MDL", "Mandalay", 60),
    ("CNX", "PEK", "Beijing", 120),
    ("CNX", "PUS", "Busan", 150),
    ("CNX", "PVG", "Shanghai", 120),
    ("CNX", "RGN", "Yangon", 80),
    ("CNX", "SIN", "Singapore", 80),
    ("CNX", "TFU", "Chengdu", 80),
    ("CNX", "TPE", "Taipei", 100),
    ("CNX", "XIY", "Xi'an", 100),
    # Domestic
    ("CNX", "BKK", "Bangkok-Suv", 25),
    ("CNX", "DMK", "Bangkok-DMK", 20),
    ("CNX", "HDY", "Hat Yai", 25),
    ("CNX", "HHQ", "Hua Hin", 30),
    ("CNX", "HKT", "Phuket", 30),
    ("CNX", "KBV", "Krabi", 30),
    ("CNX", "KKC", "Khon Kaen", 25),
    ("CNX", "URT", "Surat Thani", 30),
    ("CNX", "USM", "Ko Samui", 40),
    ("CNX", "UTH", "Udon Thani", 25),
    ("CNX", "UTP", "Pattaya", 30),
]


def seed_routes(db):
    """Insert routes into DB if not already present."""
    for origin, dest, name, threshold in ROUTES:
        db.execute(
            "INSERT OR IGNORE INTO routes (origin, dest, name, threshold) VALUES (?,?,?,?)",
            (origin, dest, name, threshold),
        )
    db.commit()
```

**Step 3: Verify DB creates cleanly**

Run: `cd ~/src/cheap-flights && python3 -c "from db import get_db; from routes import seed_routes; db = get_db(); seed_routes(db); print('OK:', db.execute('SELECT COUNT(*) FROM routes').fetchone()[0], 'routes')"`
Expected: `OK: 32 routes`

**Step 4: Commit**

```bash
cd ~/src/cheap-flights
git init
git add db.py routes.py
git commit -m "feat: db schema and route definitions"
```

---

### Task 2: Scanner

**Files:**
- Create: `~/src/cheap-flights/scan.py`

**Step 1: Create `scan.py`**

```python
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

    now = datetime.now(timezone.utc).isoformat()
    total = len(routes) * len(dates)
    done = 0
    ok = 0
    errors = 0

    print(f"[scan] {len(routes)} routes × {len(dates)} dates = {total} queries", file=sys.stderr)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {}
        for route in routes:
            for flight_date in dates:
                f = pool.submit(search_one, route["origin"], route["dest"], flight_date, bool(route["nonstop"]))
                futures[f] = (route["origin"], route["dest"], route["name"], flight_date)

        for future in as_completed(futures):
            origin, dest, name, flight_date = futures[future]
            r = future.result()
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
    db.close()

    print(f"[scan] done: {done} queries, {ok} with prices, {errors} errors", file=sys.stderr)
    return errors == 0


if __name__ == "__main__":
    success = run_scan()
    sys.exit(0 if success else 1)
```

**Step 2: Test with a small run (2 routes × 3 days)**

Run: `cd ~/src/cheap-flights && python3 -c "
from scan import search_one
r = search_one('CNX', 'SIN', '2026-03-15')
print(r['status'], len(r['flights']), 'flights', r['elapsed_ms'], 'ms')
if r['flights']:
    print('  cheapest:', r['flights'][0]['price_num'])
"`
Expected: `ok 3-10 flights <ms>` with prices

**Step 3: Commit**

```bash
git add scan.py
git commit -m "feat: scanner — parallel flight search with full logging"
```

---

### Task 3: Telegram alerts

**Files:**
- Create: `~/src/cheap-flights/alerts.py`

**Step 1: Create `alerts.py`**

```python
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
    now = datetime.now(timezone.utc).isoformat()
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
```

**Step 2: Add `TELEGRAM_FLIGHTS_CHANNEL` to telegram config**

Append to `~/Sync/config/telegram/config`:
```
TELEGRAM_FLIGHTS_CHANNEL=@cnx_cheap_flights
```

**Step 3: Test alert formatting (dry run)**

Run: `cd ~/src/cheap-flights && python3 -c "
from alerts import format_deal
d = {'origin':'CNX','name':'Kuala Lumpur','dest':'KUL','price':45,'rolling_avg':72,'flight_date':'2026-03-19','airline':'AirAsia','stops':0,'duration':'2h 45m','reason':'38% below avg \$72'}
print(format_deal(d))
"`
Expected:
```
*CNX → Kuala Lumpur* $45 (avg $72)
2026-03-19 | AirAsia | Nonstop | 2h 45m
_38% below avg $72_
```

**Step 4: Commit**

```bash
git add alerts.py
git commit -m "feat: telegram deal alerts with dedup"
```

---

### Task 4: Move CLI tool into repo

**Files:**
- Move: `~/bin/cheap-flights` → `~/src/cheap-flights/cli.py`
- Create: `~/bin/cheap-flights` (symlink)

**Step 1: Move and symlink**

```bash
mv ~/bin/cheap-flights ~/src/cheap-flights/cli.py
ln -s ~/src/cheap-flights/cli.py ~/bin/cheap-flights
```

**Step 2: Verify CLI still works**

Run: `cheap-flights CNX SIN --nonstop -f 2026-03-15 -t 2026-03-16`
Expected: prices for 1-2 days

**Step 3: Commit**

```bash
git add cli.py
git commit -m "feat: move CLI into repo"
```

---

### Task 5: Cron setup + run script

**Files:**
- Create: `~/src/cheap-flights/run.sh`

**Step 1: Create `run.sh`**

```bash
#!/usr/bin/env bash
# Daily flight deal scan + alerts
set -e
cd "$(dirname "$0")"

LOG="$HOME/.cheap-flights/scan-$(date +%Y%m%d).log"
mkdir -p "$HOME/.cheap-flights"

echo "=== $(date -Iseconds) ===" >> "$LOG"
python3 scan.py 2>&1 | tee -a "$LOG"
python3 alerts.py 2>&1 | tee -a "$LOG"

# Keep 30 days of logs
find "$HOME/.cheap-flights" -name 'scan-*.log' -mtime +30 -delete
```

**Step 2: Make executable**

```bash
chmod +x ~/src/cheap-flights/run.sh
```

**Step 3: Add cron entry**

```bash
(crontab -l 2>/dev/null; echo "0 3 * * * $HOME/src/cheap-flights/run.sh") | crontab -
```

**Step 4: Commit**

```bash
git add run.sh
git commit -m "feat: run script + cron setup"
```

---

### Task 6: First full scan + smoke test

**Step 1: Run full scan**

Run: `cd ~/src/cheap-flights && python3 scan.py`
Expected: `[scan] 32 routes × 60 dates = 1920 queries` ... takes ~12 min ... `[scan] done: 1920 queries, N with prices, M errors`

**Step 2: Check DB has data**

Run: `sqlite3 ~/.cheap-flights/flights.db "SELECT dest, COUNT(*), MIN(price), AVG(price), MAX(price) FROM prices GROUP BY dest ORDER BY MIN(price)"`
Expected: table of destinations with price stats

**Step 3: Check scan_log for errors**

Run: `sqlite3 ~/.cheap-flights/flights.db "SELECT status, COUNT(*) FROM scan_log GROUP BY status"`
Expected: mostly 'ok' and 'no_flights', few 'error'

**Step 4: Run alerts**

Run: `cd ~/src/cheap-flights && python3 alerts.py`
Expected: sends deals to @cnx_cheap_flights (or "no deals found" if nothing below threshold)

**Step 5: Commit**

```bash
git add -A
git commit -m "feat: verified first full scan"
```

---

## Outcome

After completing all tasks:
- `~/.cheap-flights/flights.db` contains price data for 32 routes × 60 days
- Cron runs daily at 3am on box
- Deals posted to `@cnx_cheap_flights` Telegram channel
- Every request logged in `scan_log` for debugging
- `cheap-flights` CLI still works for ad-hoc searches
