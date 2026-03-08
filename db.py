"""SQLite database for cheap-flights deal scanner."""

import os
import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".cheap-flights" / "flights.db"


def get_db():
    """Create dir, connect, set WAL mode, init tables, return connection."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY,
            scanned_at TEXT,
            origin TEXT,
            dest TEXT,
            flight_date TEXT,
            price INTEGER,
            currency TEXT DEFAULT 'USD',
            airline TEXT,
            stops INTEGER,
            duration TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_prices_route_date ON prices (origin, dest, flight_date);
        CREATE INDEX IF NOT EXISTS idx_prices_scanned ON prices (scanned_at);

        CREATE TABLE IF NOT EXISTS scan_log (
            id INTEGER PRIMARY KEY,
            scanned_at TEXT,
            origin TEXT,
            dest TEXT,
            flight_date TEXT,
            status TEXT,
            error_msg TEXT,
            flights_found INTEGER DEFAULT 0,
            elapsed_ms INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_scan_log_scanned ON scan_log (scanned_at);

        CREATE TABLE IF NOT EXISTS routes (
            origin TEXT,
            dest TEXT,
            name TEXT,
            threshold INTEGER,
            nonstop INTEGER DEFAULT 1,
            active INTEGER DEFAULT 1,
            PRIMARY KEY (origin, dest)
        );

        CREATE TABLE IF NOT EXISTS alert_log (
            id INTEGER PRIMARY KEY,
            alerted_at TEXT,
            origin TEXT,
            dest TEXT,
            flight_date TEXT,
            price INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_alert_log_lookup ON alert_log (origin, dest, flight_date, alerted_at);
    """)
    return db


def log_scan(db, scanned_at, origin, dest, flight_date, status, error_msg=None, flights_found=0, elapsed_ms=0):
    db.execute(
        "INSERT INTO scan_log (scanned_at, origin, dest, flight_date, status, error_msg, flights_found, elapsed_ms) VALUES (?,?,?,?,?,?,?,?)",
        (scanned_at, origin, dest, flight_date, status, error_msg, flights_found, elapsed_ms),
    )
    db.commit()


def insert_prices(db, scanned_at, origin, dest, flight_date, flights, currency="USD"):
    """Insert flight prices. flights is list of dicts with keys: price_num, airline, stops_num, duration."""
    db.executemany(
        "INSERT INTO prices (scanned_at, origin, dest, flight_date, price, currency, airline, stops, duration) VALUES (?,?,?,?,?,?,?,?,?)",
        [(scanned_at, origin, dest, flight_date, f["price_num"], currency, f["airline"], f["stops_num"], f["duration"]) for f in flights],
    )
    db.commit()


def get_rolling_avg(db, origin, dest, days=14):
    """Return AVG(price) for last N days, or None."""
    row = db.execute(
        "SELECT AVG(price) as avg_price FROM prices WHERE origin=? AND dest=? AND scanned_at >= datetime('now', ?)",
        (origin, dest, f"-{days} days"),
    ).fetchone()
    return row["avg_price"] if row else None


def was_alerted_recently(db, origin, dest, flight_date, hours=24):
    """Return True if an alert was sent for this route+date within the last N hours."""
    row = db.execute(
        "SELECT 1 FROM alert_log WHERE origin=? AND dest=? AND flight_date=? AND alerted_at >= datetime('now', ?)",
        (origin, dest, flight_date, f"-{hours} hours"),
    ).fetchone()
    return row is not None


def log_alert(db, alerted_at, origin, dest, flight_date, price):
    db.execute(
        "INSERT INTO alert_log (alerted_at, origin, dest, flight_date, price) VALUES (?,?,?,?,?)",
        (alerted_at, origin, dest, flight_date, price),
    )
    db.commit()
