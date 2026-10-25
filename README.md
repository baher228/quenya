# Quenya

Quenya is a fashion discovery app for UK shoppers. Describe what you want ("roadman Nike Tech Fleece, all black or red", "old money for a wedding, under £300"), swipe through real listings like Tinder, and Quenya learns your taste and builds complete outfits from Vinted second-hand and UK retailers, shoes and accessories included.

Built for the Night Shift **Mubit** hackathon. Every AI call goes through the Mubit gateway, and shoppers' corrections become Mubit training data.

- App: https://quenya-rho.vercel.app (installable on iPhone: Share → Add to Home Screen)
- API: https://quenya-api.vercel.app

## How it works

1. **Brief.** Each style (board) starts from a brief: what you want, budget, sizes, gender, niche level and whether to include shoes. Every style is independent and learns only from its own brief and swipes.
2. **Style decoder.** Mubit maps slang and references to known styles ("dress like Central Cee" → trackie / roadman, "Carti fits" → opium) using a 56-entry glossary (`backend/app/data/style_glossary.json`). Only the matching styles' short notes go into later prompts, at most 3.
3. **Swipe deck.** Listings are ranked against the style's taste profile. A few exploration cards keep the deck from narrowing too early.
4. **Taste learning.** After every 8 swipes, Mubit updates the taste profile (likes, avoids, palette, rules). A like or avoid only sticks when your keep rate for that tag is clearly above or below your usual rate on that style, and old entries fade when your swipes contradict them.
5. **Outfits.** Mubit's stylist builds outfits from a ranked pool. You can lock pieces, swap one piece or reject an outfit with a reason. "Buy the look" is a simulated bundle checkout that links to the real listings.
6. **Find & uploads.** Search the catalogue or run a live search, and upload inspiration photos. Mubit reads each photo and files it under the right style.

Sold or out-of-stock products are hidden from decks, outfits and Find. They're kept in the database, so swipe history stays intact.

## How Mubit is used

All model calls go through Mubit's OpenAI-compatible gateway (`MUBIT_BASE_URL`, default `https://api.mubit.ai/openai/v1`) as six tasks. Each task has a fixed system prompt, model and output format in `backend/app/prompts.py`:

| Task | What it does |
|---|---|
| `tagger` | Tags every listing: slot, category, colours, aesthetics, formality, niche level |
| `taste` | Updates a style's taste profile from swipes and feedback |
| `stylist` | Builds outfits from the ranked pool |
| `reader` | Reads inspiration photos |
| `router` | Routes briefs and photos to the right style |
| `decoder` | Maps style slang to glossary styles |

Corrections (tag fixes, outfit rejections with a reason, router fixes) are sent to the same task, which gives Mubit labelled data to train a smaller per-task adapter. The Mubit panel on the Taste screen shows each task's call count.

> Don't edit the existing system prompts: Mubit groups calls by prompt, model and output format, so a change starts a new task and resets its training count. Add new behaviour through user content or a new task, and bump `PROMPT_VERSION`.

## Repository layout

```
backend/            FastAPI app (Python 3.12)
  app/main.py       app entry, images, Mubit task stats
  app/phase2_api.py boards, deck, swipes, taste, outfits, search, uploads, checkout
  app/mubit.py      gateway client (call_task)
  app/prompts.py    frozen Mubit task prompts
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
.venv/bin/pip install -e 'backend[dev]'
# .env in the repo root:
#   MUBIT_API_KEY=...
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
| `MUBIT_API_KEY` | Mubit gateway key (required) |
| `MUBIT_BASE_URL` | Gateway URL, default `https://api.mubit.ai/openai/v1` |
| `TAVILY_API_KEY` | Tavily Extract/Search for retail pages and live search |
| `QUENYA_DATABASE_URL` | Postgres URL. When set, data lives in the `quenya` schema (Supabase pooler in production) |
| `QUENYA_IMAGE_STORE=supabase` + `SUPABASE_URL`, `SUPABASE_SECRET_KEY` | Store images in the Supabase Storage bucket `quenya-img` |
| `SUPABASE_DB_URL` | Used by the importer and the scraper worker |
| `QUENYA_SHARED_CORE=1` | Turns the archived cross-board "Across your boards" layer back on (off by default) |
| `DB_PATH` | SQLite path for local dev |

## Deployment

Two Vercel projects deploy automatically from `main`:

- `quenya-api` (root `backend/`): Python function `api/index.py` in region `dub1`, installed from `requirements-vercel.txt`, with `app/data/**` bundled.
- `quenya` (root `frontend/`): Vite static build. `frontend/vercel.json` rewrites `/api` and `/img` to the API.

Data is in Supabase Postgres (schema `quenya`) and Supabase Storage (`quenya-img`). The Supabase project is shared with other apps, so only touch the `quenya` schema.

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

Tests run offline with Mubit mocked.

## Known limits

- Mubit's memory API (`remember`, `recall`, `mubit.learn`) isn't available with the hackathon gateway key, so taste lives in our own database.
- Vinted doesn't say when a listing sells, so sold Vinted items aren't re-checked after they're fetched.
- Live search runs as a FastAPI background task, and Vercel doesn't guarantee these keep running after the response is sent.
- Some UK retailers block automated access; `scraper/` runs from a UK laptop to cover category pages.
- Checkout is simulated: it opens the real listings, but no payment is taken.
