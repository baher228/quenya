# UK clothing listing scraper

The config-driven scraper collects new-clothing listings from official UK
storefronts and writes rows compatible with `scraper/worker.py` and the backend
file importer. It does not open product pages during normal collection. Shops
which are expected to work only from a UK connection are marked `where = "uk"`
in `shops.toml`.

## Install

The repository environment already includes the project dependencies. For a
fresh Linux environment:

```bash
python3.13 -m venv .venv
. .venv/bin/activate
pip install -r scraper/requirements.txt
```

On the user's Windows laptop:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r scraper\requirements.txt
```

## Run

From the repository root:

```bash
python scraper/scrape.py --list
python scraper/scrape.py --probe
python scraper/scrape.py --workers 8 --batch-size 1000
python scraper/scrape.py --uk --shops cos,arket,barbour
python scraper/scrape.py --group streetwear --max-pages 2 --time-budget 8
```

By default the run selects enabled `where = "any"` shops. `--uk` also includes
UK-only shops; `--all-enabled` includes all enabled shops. `--shops` takes a
comma-separated list of registry keys and `--group` filters by the shop group.
Different shops can run concurrently, while requests for each individual shop
remain sequential. The default request pacing is five seconds plus up to 1.5
seconds of jitter. A shop stops on a block/challenge and has a 60-request
default cap; both pacing and cap are configurable from the CLI and registry.

`--probe` checks each configured shop (Shopify: `meta.json` and page one of the
product feed) without changing the registry. Probe evidence is saved as
`scraper/out/probe-<timestamp>.json`. Enable a shop only after the probe shows
GBP clothing rows. The regular run writes each shop's JSONL as it progresses,
flushing after each listing page, plus `all.jsonl`, `batch_001.jsonl` and
further batches, and `summary.json` / `summary.md`. Outputs are timestamped
under `scraper/out/`.

`--check-images N` checks up to N image URLs per shop (default 2).
`--max-pages`, `--time-budget` (minutes), `--workers`, `--max-requests`,
`--delay`, `--jitter` and `--batch-size` tune collection.

## Accessories

Run the accessory catalogue separately from apparel:

```bash
python scraper/scrape.py --category-set accessories --workers 8
python scraper/scrape.py --probe --category-set accessories --shops shop-key
```

Accessory mode classifies bags and fashion accessories, requires GBP Shopify
feeds, and keeps only available variants. It uses `accessory_pages` for JSON-LD
shops; apparel runs continue to use `pages`. Resume an interrupted run with
`--resume scraper/out/<run-id>`. The saved progress records its category set,
and existing JSONL output is used to deduplicate by product URL and record ID.
Use `--known file1.jsonl file2.jsonl` to seed deduplication from other runs or
`--shard K/N` to select a stable slice of the selected shop list.

## Optional Supabase upload

No Supabase request is made unless `--upload` is explicitly provided. Set
`SUPABASE_URL`, `SUPABASE_SECRET_KEY` and `SUPABASE_DB_URL` in the environment
or in an ignored `scraper/.env` file before using that flag:

```powershell
python scraper\scrape.py --uk --shops cos,arket --upload
```

`--upload` ensures the private `quenya-listings` bucket, stores the image, then
inserts each row into `quenya.listings` with `ON CONFLICT (url) DO NOTHING`.
Use the existing SQL setup in `supabase/quenya_listings.sql` first. Never put
credentials in `shops.toml`, source files, or committed output.

## Add a shop

1. Add one `[[shop]]` section to `shops.toml` with `key`, `brand`, canonical
   `domain`, adapter name, enabled/location/group values and probe notes.
2. Add category listing URLs as `[[shop.pages]]` records with a `gender` of
   `m` or `w`. For Shopify, set the `host`, `default_gender`, and optional
   `gender_rule`, `product_base` or `collections`.
3. Choose an existing adapter (`shopify`, `jsonld`, `burberry`, `nike`,
   `sfcc`, `tiles`, or `ssense`) or add one under `scraper/adapters/` and
   register it in `adapters/__init__.py`.
4. Add a small saved HTML/JSON fixture and offline tests before probing. Keep
   listing pages only; do not add product-detail requests or retry/block
   bypass logic.
5. Probe, inspect the output and only then set `enabled = true`. Leave
   `where = "uk"` for shops that need the user's UK laptop.

## Nightly Windows Task Scheduler

Create a daily task with **Program/script** set to
`C:\path\to\quenya\.venv\Scripts\python.exe`, **Add arguments** set to
`C:\path\to\quenya\scraper\scrape.py --uk --workers 8 --batch-size 1000`,
and **Start in** set to `C:\path\to\quenya`. The account must have network
access and any required environment variables. Run it first without
`--upload`, then review the timestamped `scraper\out` summary.

## Existing category-page worker

`scraper/worker.py` remains available alongside the config-driven scraper. It
uses the backend listing readers and takes shop domains rather than registry
keys:

```powershell
python scraper\worker.py --dry-run --max-pages 1
python scraper\worker.py --shops cos.com,arket.com --max-pages 2
```

The dry run writes a JSONL preview under `scraper\out\`. Without `--dry-run`,
the worker uploads images and inserts rows directly into Supabase; set
`SUPABASE_URL`, `SUPABASE_SECRET_KEY`, and `SUPABASE_DB_URL` in the environment
or an ignored `scraper\.env` file first. `--max-pages` is per shop and
`--time-budget` limits the overall run in minutes.
