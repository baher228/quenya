# Quenya

Quenya is a fashion discovery app for UK shoppers. Describe what you want ("roadman Nike Tech Fleece, all black or red", "old money for a wedding, under £300"), swipe through real listings like Tinder, and Quenya learns your taste and builds complete outfits from Vinted second-hand and UK retailers, shoes and accessories included.

AI tasks help interpret briefs and photos, update each board's taste profile, build outfits and tag catalogue items. Product tagging uses the local CLIP kNN tagger by default.

- App: https://quenya-rho.vercel.app (installable on iPhone: Share → Add to Home Screen)
- API: https://quenya-api.vercel.app

## How it works

1. **Brief.** Each style (board) starts from a brief: what you want, budget, sizes, gender, niche level and whether to include shoes. Every style is independent and learns only from its own brief and swipes.
2. **Style decoder.** The style decoder maps slang and references to known styles ("dress like Central Cee" → trackie / roadman, "Carti fits" → opium) using a 56-entry glossary (`backend/app/data/style_glossary.json`). Only the matching styles' short notes go into later prompts, at most 3.
3. **Swipe deck.** Listings are ranked against the style's taste profile. A few exploration cards keep the deck from narrowing too early.
4. **Taste learning.** After every 8 swipes, the taste task updates the profile (likes, avoids, palette, rules). A like or avoid only sticks when your keep rate for that tag is clearly above or below your usual rate on that style, and old entries fade when your swipes contradict them.
5. **Outfits.** The AI stylist builds outfits from a ranked pool. You can lock pieces, swap one piece or reject an outfit with a reason. "Buy the look" is a simulated bundle checkout that links to the real listings.
6. **Find & uploads.** Search the catalogue or run a live search, and upload inspiration photos. The AI reads each photo and files it under the right style.

Sold or out-of-stock products are hidden from decks, outfits and Find. They're kept in the database, so swipe history stays intact.

## AI providers and product tagging

All model tasks use Cursor's `cursor-agent` CLI by default. Set `QUENYA_LLM_PROVIDER=openai` to use an OpenAI-compatible API instead; its base URL defaults to `https://api.openai.com/v1`. Cursor uses `QUENYA_CURSOR_MODEL` for every task, while OpenAI mode uses the per-task model constants in `backend/app/prompts.py`.

| Task | What it does |
|---|---|
| `tagger` | Tags every listing: slot, category, colours, aesthetics, formality, niche level |
| `taste` | Updates a style's taste profile from swipes and feedback |
| `stylist` | Builds outfits from the ranked pool |
| `reader` | Reads inspiration photos |
| `router` | Routes briefs and photos to the right style |
| `decoder` | Maps style slang to glossary styles |

The local product tagger (`QUENYA_TAGGER=local`, the default) transfers tags from nearby non-local GPT-tagged products in CLIP space. It also stores image, text and combined vectors for newly tagged live-search items. Set `QUENYA_TAGGER=llm` to tag products through the configured model provider. `QUENYA_LLM_DAILY_CAP` limits logged model attempts per UTC day and defaults to 300.

Cursor mode requires `cursor-agent` and `CURSOR_API_KEY` on a real server. It is not suitable for the Vercel API function; use the OpenAI-compatible provider there if model calls are required.

## Repository layout

```
backend/            FastAPI app (Python 3.12)
  app/main.py       app entry, images, AI task stats
  app/phase2_api.py boards, deck, swipes, taste, outfits, search, uploads, checkout
  app/llm.py        Cursor CLI / OpenAI-compatible client (call_task)
  app/prompts.py    model task prompts
  app/taste.py      taste updates + evidence gate
  app/rank.py       deck ranking, exploration
  app/outfits.py    outfit pool + stylist content
  app/styles.py     style glossary + decoder
  app/sources/      Vinted, UK retail/sport readers, catalogue crawl
  app/db.py         SQLite locally, Postgres (Supabase) in production
  api/index.py      Vercel function entry
  tests/            pytest suite
frontend/           React 18 + Vite PWA (Brief, Swipe, Outfits, Find, Taste)
scraper/            UK laptop worker for category pages → Supabase (see scraper/README.md)
supabase/           SQL for the scraper's listings table
```

## Running locally

Backend (from the repo root):

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e 'backend[dev,embed]'
# .env in the repo root:
#   CURSOR_API_KEY=...
#   TAVILY_API_KEY=...      # retail page extraction and live search
cd backend && ../.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Without `QUENYA_DATABASE_URL`, the backend uses SQLite at `backend/data/quenya.db` and stores images on disk.

Frontend:

```bash
cd frontend && npm ci && npm run dev   # http://localhost:5173, proxies /api and /img to :8000 (override with QUENYA_API)
```

Seed the catalogue:

```bash
cd backend
../.venv/bin/python -m app.ingest --source vinted --per-query 10
../.venv/bin/python -m app.ingest --source retail --per-query 5
../.venv/bin/python -m app.catalog_seed --per-category 15
../.venv/bin/python -m app.tag --limit 1000 --concurrency 4
```

## Configuration

| Variable | Purpose |
|---|---|
| `QUENYA_LLM_PROVIDER` | `cursor` (default) or `openai` |
| `CURSOR_API_KEY` | Cursor CLI key; required for Cursor on a real server |
| `QUENYA_CURSOR_MODEL` | Cursor model for all tasks; default `gemini-3.7-flash-high` |
| `QUENYA_CURSOR_BIN` | Optional path to `cursor-agent`; otherwise resolved from `PATH` or `~/.local/bin` |
| `QUENYA_CURSOR_TIMEOUT_S` | Cursor subprocess timeout; default 120 seconds |
| `QUENYA_LLM_API_KEY` | API key for the OpenAI-compatible fallback |
| `QUENYA_LLM_BASE_URL` | OpenAI-compatible endpoint; default `https://api.openai.com/v1` |
| `QUENYA_LLM_DAILY_CAP` | Maximum logged LLM attempts per UTC day; default 300 |
| `QUENYA_TAGGER` | `local` (default) or `llm` |
| `TAVILY_API_KEY` | Tavily Extract/Search for retail pages and live search |
| `QUENYA_VINTED_PROXY_KEY` | Shared authentication key for VPS-to-Vercel Vinted searches |
| `QUENYA_VINTED_PROXY_URL` | VPS-only Vinted search proxy URL; do not set on Vercel |
| `QUENYA_DATABASE_URL` | Postgres URL. When set, data lives in the `quenya` schema (Supabase pooler in production) |
| `QUENYA_IMAGE_STORE=supabase` + `SUPABASE_URL`, `SUPABASE_SECRET_KEY` | Store images in the Supabase Storage bucket `quenya-img` |
| `SUPABASE_DB_URL` | Used by the importer and the scraper worker |
| `QUENYA_SHARED_CORE=1` | Turns the archived cross-board "Across your boards" layer back on (off by default) |
| `DB_PATH` | SQLite path for local dev |

## Deployment

Two Vercel projects deploy automatically from `main`:

- `quenya-api` (root `backend/`): Python function `api/index.py` in region `dub1`, installed from `requirements-vercel.txt`, with `app/data/**` bundled.
- `quenya` (root `frontend/`): Vite static build. `frontend/vercel.json` rewrites `/api` and `/img` to the API.

Set `QUENYA_VINTED_PROXY_KEY` on Vercel `quenya-api` and set both `QUENYA_VINTED_PROXY_URL=https://quenya-api.vercel.app` and the same key in `/etc/quenya/quenya.env` on the VPS; never set `QUENYA_VINTED_PROXY_URL` on Vercel.

Data is in Supabase Postgres (schema `quenya`) and Supabase Storage (`quenya-img`). The Supabase project is shared with other apps, so only touch the `quenya` schema.
Cursor CLI and the optional Torch/OpenCLIP image encoder are not installed in the Vercel bundle; run Cursor and local image tagging on a real server with the `embed` extra.

## Importing scraped products

```bash
cd backend
../.venv/bin/python -m app.pg_migrate --sqlite DB --images DIR --uploads DIR   # SQLite → Supabase
../.venv/bin/python -m app.supabase_import --from-file batch.jsonl [--per-shop N] [--exclude-source domain]  # JSONL → Supabase, tags each new item
```

Both are insert-only and safe to re-run. The importer skips rows marked out of stock, but `availability` in a scrape is only correct as of scrape time, so re-check stock live before importing.

## Tests and checks

```bash
cd backend && ../.venv/bin/ruff check . && ../.venv/bin/pytest
QUENYA_TEST_PG_URL=postgresql://... ../.venv/bin/pytest   # same suite on Postgres
cd frontend && npm run build
```

Tests run offline with the OpenAI client mocked and a fake `cursor-agent` executable.

## Known limits

- Taste profiles and corrections live in Quenya's own database.
- Vinted doesn't say when a listing sells, so sold Vinted items aren't re-checked after they're fetched.
- Live search runs as a FastAPI background task, and Vercel doesn't guarantee these keep running after the response is sent.
- Some UK retailers block automated access; `scraper/` runs from a UK laptop to cover category pages.
- Checkout is simulated: it opens the real listings, but no payment is taken.
