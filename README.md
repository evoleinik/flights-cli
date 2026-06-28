# flights-cli

**Agent-friendly Google Flights CLI + daily deal scanner. Pure Python, JSON-first, zero-install via `uv`.**

Find the cheapest fares for a route across a whole date range in one command — and
optionally run it nightly to alert you when a price drops. It's a thin, scriptable
wrapper over [`fast_flights`](https://github.com/AWeirdDev/fast-flights) (which reads
Google Flights' embedded data), shaped for piping into other tools and AI agents.

```
$ uv run cli.py SIN CNX -f 2026-06-29 -t 2026-07-03 --currency SGD --nonstop
Date                            Price  Stops  Airline                   Duration
-------------------------------------------------------------------------------------
2026-06-29                    SGD 271      0  Scoot                     3 hr 5 min
2026-06-30                    SGD 230      0  Scoot                     3 hr 5 min
2026-07-01                    SGD 308      0  Scoot                     3 hr 5 min
2026-07-02                    SGD 354      0  Scoot                     3 hr 5 min
2026-07-03                    SGD 308      0  Scoot                     3 hr 5 min

CHEAPEST: 2026-06-30 -- SGD 230 on Scoot (3 hr 5 min)
```

## Why?

Booking sites make you scrub a fare calendar one day at a time. This scans every day
in a range concurrently and prints the cheapest per day, with a `--json` mode for
scripts and agents.

| | flights-cli | Google Flights UI | Most aggregators |
|---|---|---|---|
| Whole date range at once | ✅ | partial (calendar) | ❌ |
| Scriptable / `--json` | ✅ | ❌ | ❌ |
| Round-trip stay sweep | ✅ `--round N` | ❌ | ❌ |
| Nonstop filter | ✅ `--nonstop` | ✅ | ✅ |
| Nightly price alerts | ✅ (optional) | ❌ | some (email) |
| Install | one `uv` command | — | — |

## Install

Requires [`uv`](https://docs.astral.sh/uv/) and Python 3.10+. No manual `pip` —
dependencies are pinned inline ([PEP 723](https://peps.python.org/pep-0723/)) and
auto-installed on first run.

```bash
git clone https://github.com/evoleinik/flights-cli
cd flights-cli
uv run cli.py SIN CNX --nonstop      # that's it
```

## Quick start

```bash
# Cheapest per day, next 30 days
uv run cli.py CNX SIN

# Specific range, nonstop only, in SGD
uv run cli.py SIN CNX -f 2026-06-29 -t 2026-07-03 --nonstop --currency SGD

# A whole month
uv run cli.py CNX SIN -m 2026-08

# Round trip with a 7-day stay (sweeps each departure day)
uv run cli.py CNX SIN --round 7

# JSON for scripts/agents
uv run cli.py SIN CNX --nonstop --json | jq '.[0]'
```

## Commands

```
uv run cli.py ORIGIN DEST [options]

  ORIGIN, DEST          IATA airport codes (e.g. SIN, CNX)
  -f, --from-date DATE  Start date YYYY-MM-DD (default: tomorrow)
  -t, --to-date DATE    End date YYYY-MM-DD   (default: +30 days)
  -m, --month YYYY-MM   Scan an entire month
  --round DAYS          Round trip with an N-day stay
  --nonstop             Nonstop flights only
  --currency CODE       Currency (default: USD)
  --json                JSON output to stdout
  -w, --workers N       Parallel workers (default: 5)
```

`--json` emits an array of the cheapest flight per date:

```json
{
  "date": "2026-06-30",
  "return": null,
  "price": "SGD 230",
  "price_num": 230,
  "airline": "Scoot",
  "stops": 0,
  "duration": "3 hr 5 min",
  "departure": "2:30 PM on Tue, Jun 30",
  "arrival": "4:35 PM on Tue, Jun 30"
}
```

## Use with AI agents

flights-cli is built for agents: `--json`, deterministic exit codes, no interactive
prompts, data on stdout. Drop this in your agent's context (CLAUDE.md / AGENTS.md):

```
flights-cli — cheapest fares for a route across a date range (Google Flights data).
  uv run cli.py <ORIGIN> <DEST> [-f YYYY-MM-DD] [-t YYYY-MM-DD] [--round N] [--nonstop] [--currency XXX] --json
Returns JSON: array of {date, price_num, airline, stops, duration, departure, arrival},
one cheapest flight per day. Always pass --json. ORIGIN/DEST are IATA codes.
```

## Optional: nightly deal scanner

Beyond the one-shot CLI, the repo includes a cron-able scanner that tracks a watchlist
of routes in SQLite and pings you on Telegram when a fare beats its threshold or recent
average.

```bash
uv run scan.py      # scan all routes in routes.py × next 60 days -> SQLite
uv run alerts.py    # compare latest prices to thresholds, send Telegram alerts
./run.sh            # both, with logging (point cron at this)
```

- Routes & price thresholds: edit `routes.py`.
- Telegram credentials are read from `~/Sync/config/telegram/config`
  (`TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`) — nothing secret is committed.
- Data lives in `~/.cheap-flights/` (SQLite DB + daily logs).

## How it works

```
cli.py / scan.py / alerts.py   <- entry points (PEP 723 pinned deps)
        │
        ff.py                  <- single adapter over fast_flights 3.x
        │                         normalizes results; unions the nonstop bucket
        ▼                         (Google's all-stops payload omits it)
   fast_flights==3.0.2         <- parses Google Flights' embedded JSON
```

`ff.py` is the only file that touches the scraping library, so when Google moves its
data shape there's exactly one place to fix.

## Limitations

- It scrapes Google Flights' data; there is **no official API**. Expect occasional
  breakage when Google changes their payload — that's inherent to this category.
- Prices are indicative; always confirm on the airline/booking site before purchase.
- Connecting-flight times come from Google's data and can be incomplete on some itineraries.

## License

MIT — see [LICENSE](LICENSE).
