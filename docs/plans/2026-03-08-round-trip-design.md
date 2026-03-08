# Round-Trip Deal Scanner — Design

**Goal:** Add guided round-trip scanning to the cheap-flights scanner. Use one-way scan data as a prior to efficiently find the cheapest round-trip combos, then report them as "Trip Ideas" in the daily Telegram message.

## Algorithm

One-way-guided round-trip search:

1. One-way scan runs first (existing, 1,920 queries)
2. Rank all route+date combos by one-way price
3. Top 10 cheapest routes × top 10 cheapest departure dates × 3 sample durations (3, 7, 14 days) = 300 queries
4. From results, take top 5 combos, expand: ±2 departure days × all 13 durations (2-14 days) = 325 queries
5. Total: ~625 extra queries (~2 min)

## DB Changes

Add to `prices` table:
- `trip_type TEXT DEFAULT 'oneway'` — `oneway` or `roundtrip`
- `return_date TEXT` — return flight date (NULL for one-way)

## Report Format

Replace one-way deals report with trip ideas:

```
✈ *CNX Trip Ideas* 2026-03-08

🇹🇭 *Bangkok-DMK* avg $85
  $52 (61%) — Mar 24–27 (3d)
  $55 (64%) — Apr 7–14 (7d)

🇲🇾 *Kuala Lumpur* avg $180
  $125 (69%) — Apr 8–15 (7d)
  $130 (72%) — Mar 19–22 (3d)
```

- Price = total round-trip cost
- Stay duration in parentheses
- Grouped by destination, sorted by price
- Same deal detection: fixed thresholds + rolling avg (30% below)

## What Changes

- `scan.py`: add `scan_roundtrip(db, oneway_results)` after one-way scan
- `db.py`: add `trip_type` and `return_date` columns to `prices`
- `alerts.py`: new report format showing round-trip deals
- `routes.py`: add round-trip thresholds (roughly 2x one-way)

## What Stays the Same

- One-way scan still runs (data collection continues)
- DB helpers, Telegram sending, cron schedule unchanged
- Same alert dedup logic (24h window)
