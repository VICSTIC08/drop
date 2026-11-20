# drop

A flash-sale reservation backend: 100 units go on sale, thousands of buyers click at once,
and exactly 100 units are sold, to 100 different people, once each.

Work in progress. Results will be published here only from real benchmark runs.

## Run it locally

Requires Docker, uv and Python 3.12.

```
docker compose up -d
uv sync
uv run python -m drop.migrate
uv run pytest -q
uv run uvicorn drop.main:app --reload
```

Interactive API docs: http://localhost:8000/docs
