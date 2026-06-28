# flights-cli

I kept clicking through fare calendars one day at a time to find a cheap flight, so I
wrote this instead. Give it a route and a date range; it prints the cheapest flight on
each day, right in the terminal.

Under the hood it's a small wrapper around
[`fast_flights`](https://github.com/AWeirdDev/fast-flights), which reads the data behind
Google Flights. There's a CLI for one-off searches, and an optional nightly scanner that
pings me on Telegram when a fare drops.

```
$ uv run cli.py SIN CNX -f 2026-06-29 -t 2026-07-03 --nonstop --currency SGD
Date                            Price  Stops  Airline                   Duration
-------------------------------------------------------------------------------------
2026-06-29                    SGD 271      0  Scoot                     3 hr 5 min
2026-06-30                    SGD 230      0  Scoot                     3 hr 5 min
2026-07-01                    SGD 308      0  Scoot                     3 hr 5 min
2026-07-02                    SGD 354      0  Scoot                     3 hr 5 min
2026-07-03                    SGD 308      0  Scoot                     3 hr 5 min

CHEAPEST: 2026-06-30 -- SGD 230 on Scoot (3 hr 5 min)
```

## Why bother

Booking sites make you check dates one by one. This checks the whole range at once and
hands you a table. And because it can print JSON, I can pipe it into other scripts or let
an AI agent drive it instead of eyeballing prices in a browser tab.

If the Google Flights website already works fine for you, you honestly might not need
this. It earns its keep when you're flexible on dates, comparing a stretch of days, or
wiring flight prices into something else.

## Install

You'll need [`uv`](https://docs.astral.sh/uv/) and Python 3.10+. There's no `pip install`
step — the dependencies are pinned right inside each script
([PEP 723](https://peps.python.org/pep-0723/)) and `uv` grabs them on the first run.

```bash
git clone https://github.com/evoleinik/flights-cli
cd flights-cli
uv run cli.py SIN CNX --nonstop
```

## Using it

```bash
# Cheapest per day for the next 30 days
uv run cli.py CNX SIN

# A specific window, nonstop only, priced in SGD
uv run cli.py SIN CNX -f 2026-06-29 -t 2026-07-03 --nonstop --currency SGD

# A whole month
uv run cli.py CNX SIN -m 2026-08

# Round trip with a 7-day stay (tries each departure day)
uv run cli.py CNX SIN --round 7

# JSON, for scripts and agents
uv run cli.py SIN CNX --nonstop --json | jq '.[0]'
```

## The flags

```
uv run cli.py ORIGIN DEST [options]

  ORIGIN, DEST          IATA airport codes (SIN, CNX, ...)
  -f, --from-date DATE  Start date YYYY-MM-DD (default: tomorrow)
  -t, --to-date DATE    End date YYYY-MM-DD   (default: +30 days)
  -m, --month YYYY-MM   Scan an entire month
  --round DAYS          Round trip with an N-day stay
  --nonstop             Nonstop only
  --currency CODE       Currency (default: USD)
  --json                Print JSON to stdout
  -w, --workers N       Parallel workers (default: 5)
```

With `--json` you get an array — the cheapest flight for each date:

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

## Letting an agent use it

I built this partly so an agent could run it: JSON output, clean exit codes, no
interactive prompts, data on stdout. If you want yours to know about it, drop something
like this in its context (CLAUDE.md / AGENTS.md):

```
flights-cli — cheapest fares for a route across a date range (Google Flights data).
  uv run cli.py <ORIGIN> <DEST> [-f YYYY-MM-DD] [-t YYYY-MM-DD] [--round N] [--nonstop] [--currency XXX] --json
Returns JSON: array of {date, price_num, airline, stops, duration, departure, arrival},
one cheapest flight per day. Always pass --json. ORIGIN/DEST are IATA codes.
```

## The nightly scanner (optional)

If you want price tracking rather than one-off lookups, there's a small scanner too. It
keeps a watchlist of routes in SQLite and messages you when a fare beats its threshold or
its recent average.

```bash
uv run scan.py      # scan every route in routes.py across the next 60 days -> SQLite
uv run alerts.py    # compare the latest prices to thresholds, send Telegram alerts
./run.sh            # both, with logging — point cron at this
```

Edit your routes and price thresholds in `routes.py`. For alerts, give it a Telegram bot
token and chat id — either as environment variables:

```bash
export TELEGRAM_BOT_TOKEN=123456:abc...
export TELEGRAM_CHAT_ID=-1001234567890
uv run alerts.py
```

or in a `KEY=VALUE` file at `~/.config/flights-cli/telegram` (override the path with the
`TELEGRAM_CONFIG` env var). Either way no secret lives in the repo. The database and logs
go in `~/.cheap-flights/`.

## How it's put together

```
cli.py / scan.py / alerts.py   entry points (deps pinned inline via PEP 723)
        |
        ff.py                  the one adapter over fast_flights 3.x:
        |                      normalizes results and merges the nonstop
        v                      bucket back in (Google's all-stops payload drops it)
   fast_flights==3.0.2         parses Google Flights' embedded JSON
```

`ff.py` is the only file that talks to the scraping library. When Google reshuffles its
data, that's the single place to fix.

## Caveats

There's no official Google Flights API — this reads the page's data, so it'll break now
and then when Google changes things. Prices are a guide, not a quote; always confirm on
the airline's site before you book. And connecting-flight times come straight from
Google's data, which is occasionally patchy.

## License

MIT — see [LICENSE](LICENSE). Built by Evgeny Oleynik. Issues and PRs welcome.
