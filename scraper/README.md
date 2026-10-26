# UK category-page scraper

The worker runs from the user's UK laptop so category tiles can be parsed without
requesting retailer product pages. It writes candidates directly to the private
`quenya-listings` Supabase Storage bucket and the `quenya.listings` Postgres table.

Install with Python 3.13:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e '..\backend[scrape]'
```

Create `scraper\.env` (ignored by git) with `SUPABASE_URL`,
`SUPABASE_SECRET_KEY`, and `SUPABASE_DB_URL`. Run a local scrape preview first:

```powershell
python worker.py --dry-run --max-pages 1
```

The JSONL output is written under `scraper\out\`. Import to Supabase with:

```powershell
python worker.py --shops cos.com,arket.com --max-pages 2
```

The worker waits at least five seconds between category pages for the same shop,
does not wait for network idle, and limits each page fetch to about 45 seconds.
`--max-pages` is per shop; `--shops` accepts a comma-separated domain allowlist.
`--time-budget` sets the overall run limit in minutes (default: 12); completed
JSONL rows are flushed as the run proceeds, and the worker stops between pages
when the budget is reached.
Create the database objects with `supabase/quenya_listings.sql` before the first
non-dry run. The worker does not use PostgREST.
