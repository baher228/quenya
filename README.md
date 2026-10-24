# Quenya backend

The backend runs on Python 3.12. From the repository root, create the environment
and install the project:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e 'backend[dev]'
```

Place `MUBIT_API_KEY` and `TAVILY_API_KEY` in the root `.env` file. Start the API
from `backend/`:

```bash
../.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Ingest seed queries with `python -m app.ingest --source vinted --per-query 10`
or `--source retail --per-query 5`, then tag items with
`python -m app.tag --limit 1000 --concurrency 4`. Run offline tests and lint
from `backend/` with `../.venv/bin/pytest` and `../.venv/bin/ruff check .`.
