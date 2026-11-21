# drop

[![CI](https://github.com/VICSTIC08/drop/actions/workflows/ci.yml/badge.svg)](https://github.com/VICSTIC08/drop/actions/workflows/ci.yml)

A flash-sale backend that cannot oversell. 100 units go on sale, 1,000 buyers
click at the same moment, and exactly 100 units are sold, to 100 different
people, once each.

The same problem appears whenever many people compete for a limited thing:
concert tickets, seat selection, limited product releases, appointment slots.

This repository implements five ways to reserve stock behind one interface,
from a naive version that oversells to per-unit rows with `SKIP LOCKED`, and
measures each one for correctness, throughput and tail latency. Every number
below is generated from the raw benchmark files in `bench/results/`.

## Results

<!-- results:start -->
<!-- results:end -->

<!-- findings -->

## The five strategies

All five implement the same `reserve(drop, buyer)` contract and pass the same
test suite when buyers arrive one at a time. They differ only in how they stop
two buyers from taking the same unit.

| Strategy | How it claims a unit | What it costs |
|---|---|---|
| `naive` | Read the stock, decide in Python, write the new number back | Two buyers can read the same number before either writes. It oversells. Kept as the control group |
| `row_lock` | The same, but the read takes a row lock (`SELECT ... FOR UPDATE`) | Correct. Every buyer queues behind one row |
| `atomic_update` | Check and subtract in one statement (`UPDATE ... WHERE available > 0`) | Correct, one round trip fewer. Still one hot row |
| `redis_counter` | An atomic Lua script in Redis decides; the hold is then saved in PostgreSQL | Fast, and sold-out buyers never reach the database. But one purchase now writes to two systems that cannot commit together |
| `unit_rows` | One row per unit; each buyer locks a different free row (`FOR UPDATE SKIP LOCKED`) | Correct and all in PostgreSQL, with no single hot row. Can say "sold out" slightly early while the last units are locked by requests that later fail |

`unit_rows` is modelled on the pattern Shopify Engineering described in May 2026
for moving inventory reservations from Redis into their SQL database. This is a
small PostgreSQL version of the idea, not a reproduction of their system.

## How correctness is judged

After every test and every benchmark run, a checker (`src/drop/verify.py`) looks
only at the final rows in the database and tests four rules. It does not know
which strategy ran.

1. **No oversell.** Live holds never exceed the units for sale.
2. **One per customer.** No buyer holds two units of the same drop. A partial
   unique index enforces this in the database itself.
3. **Conservation.** Live holds plus remaining stock equals the units for sale.
   This catches units that silently go missing.
4. **Orders match.** Every checked-out hold has exactly one order.

Two races are forced to happen every time in the test suite instead of being
left to luck:

- **The lost update.** A pause point holds two buyers after both have read
  "1 left". Both then write "0 left" and both get the unit.
- **The dual write.** The request is killed after Redis has given out a unit and
  before PostgreSQL has recorded who got it. The checker reports one missing
  unit, and `src/drop/reconcile.py` repairs the counter from PostgreSQL.

## Holds expire

A reservation is a hold with a deadline. If the buyer does not check out in
time, a background sweeper marks the hold expired and returns the unit to
stock. Several sweepers can run at once without returning a unit twice, and a
hold can be checked out or expired but never both, because both paths read the
database clock.

## Run it yourself

Requires Docker, [uv](https://docs.astral.sh/uv/), Python 3.12 and, for the
benchmarks, [k6](https://k6.io).

```
docker compose up -d
uv sync
uv run python -m drop.migrate
uv run pytest -q
```

Start the API and open the interactive docs at http://localhost:8000/docs:

```
uv run uvicorn drop.main:app --reload
```

Reproduce the benchmarks, tables and figure:

```
uv run python bench/run.py burst
uv run python bench/run.py throughput
uv run python bench/run.py sweep
uv run python bench/run.py pool
uv run python bench/table.py --readme
uv run python bench/figure.py
```

## Limits of these numbers

- The load generator, the API and both databases share one laptop, so the load
  generator competes with the system it is measuring. The numbers compare the
  strategies with each other. They say nothing about production scale.
- The API is a single Python process.
- Buyers are opaque IDs sent in a header. There are no accounts and no real
  payments; checkout marks the order paid immediately.
- Every request borrows a PostgreSQL connection, including the ones Redis turns
  away, which understates what the Redis strategy could do.

## What is next

Idempotency keys, an asynchronous payment pipeline with retries and a
dead-letter queue, tests that kill the process in the middle of checkout, a
deployment on AWS, and a fair waiting room.

## Stack

Python 3.12, FastAPI, PostgreSQL 18, Redis 8, psycopg 3 (raw SQL, no ORM),
Docker Compose, pytest, k6, GitHub Actions.

## Licence

MIT
