# Quenya × Supabase optimisation plan

Goal: cut Supabase **storage, egress, Postgres disk, and query CPU** without changing ranking behaviour. Shared project note: other apps share the same Supabase project — attribute usage carefully.

**Legend:** impact/effort are engineering estimates. We cannot see the live dashboard; “measured” means confirmed in repo/code/math, not live GB/$.

## Already shipped (PR #102)

- Vinted photos never re-hosted; retail persist off by default (`QUENYA_PERSIST_RETAIL_IMAGES=0`).
- Hotlink CDN URLs in API; frontend lazy-loads with failure fallback.
- `raw_json` nullable; Vinted inserts omit raw payloads; migration `0004_vinted_privacy`.
- Retention + purge CLIs (`vinted_retention`, `purge_vinted_images`); worker kind exists.
- Retention query split for `idx_items_source_fetched`; shared HTTP client for CDN fetches.

## Dashboard metrics the founder should check

**Storage**

- Buckets `quenya-img` and `quenya-listings`: size, object count, egress GB.
- Prefix mix: hash keys vs uploads vs `{shop}/`.
- Before/after purge and any R2 cutover.

**Database**

- Table sizes: `quenya.items`, `quenya.item_embeddings`, `quenya.listings`, `quenya.llm_calls`, `quenya.deck_events`.
- Column sizes (`pg_column_size` / toast): `raw_json`, `image_preview`, `description`, `img`/`txt`/`mix`.
- Counts: `source='vinted'`, rows older than 14d, items vs embeddings.
- `pg_stat_statements`: deck/live catalogue SQL, `mix <#>`, `ORDER BY random()`, `SELECT * FROM llm_calls`.
- Index usage on `idx_items_*`; sequential scans on `items` / `item_embeddings`.
- Connection count via pooler (port **6543** transaction mode) vs session port.

**Billing**

- Storage egress vs DB size vs compute spikes aligned with ingest timers (Vinted ~3h, LV ~2h, retail 04:00 UTC).

## Ranked backlog

| # | Change | Est. saving | Effort | Status |
|---|--------|-------------|--------|--------|
| 1 | Apply `0004` + run retention (delete aged/sold Vinted) + `--clear-raw-json` once | High disk/rows | S | **Manual** |
| 2 | `purge_vinted_images --execute` for legacy re-hosted Vinted objects | High storage/egress | S | **Manual** |
| 3 | Keep persist/hotlink flags at safe defaults; verify prod API returns CDN URLs | High growth curb | S | Done in #102 |
| 4 | Schedule daily `vinted_retention` systemd timer | High ongoing | S | **Code in this PR**; install on VPS |
| 5 | Null stale Vinted `image_preview`; stop sending previews for hotlinked sources | Med DB + payload | S | **Code** |
| 6 | Slim catalogue SELECT (drop unused fat columns); cheaper embedding sample | Med CPU/IO | S | **Code** |
| 7 | Batch item fetches (outfit/history N+1); aggregate `model_stats` in SQL | Med pooler | S | **Code** |
| 8 | Cache-Control on `/img` redirects/local files; slightly smaller encode defaults | Med egress | S | **Code** |
| 9 | Indexes: tagged `fetched_at`, `(source, availability)` | Med query | S | **Code** (`0005`) |
| 10 | Move display store to R2 (`QUENYA_IMAGE_STORE=r2`) | High egress | M | **Manual** |
| 11 | TTL/purge `quenya.listings` + `quenya-listings` bucket after import | High if scrape heavy | M | **Manual** |
| 12 | Drop/archive unused `img`/`txt` vectors; optional `halfvec(512)` on `mix` | High disk | M–L | **Manual** (see below) |
| 13 | HNSW on `mix` only if queries become global ANN (today: ID-filtered exact) | Uncertain | L | Defer |
| 14 | Confirm transaction pooler DSN; cap multi-process pools | Med reliability | S | **Manual** check |

## Manual steps (do not run destructively from agents)

1. **Deploy** this optimisation PR so migrate applies `0005_cost_indexes`.
2. Confirm migration `0004_vinted_privacy` applied (`schema_migrations` / migrate status).
3. Dry-run then execute retention:
   ```sh
   python -m app.vinted_retention --dry-run
   python -m app.vinted_retention --clear-raw-json --clear-previews
   ```
4. Dry-run then purge storage objects:
   ```sh
   python -m app.purge_vinted_images
   python -m app.purge_vinted_images --execute
   ```
5. Install retention timer on VPS (units in `deploy/vps/`):
   ```sh
   sudo install -m 644 deploy/vps/quenya-vinted-retention.service /etc/systemd/system/
   sudo install -m 644 deploy/vps/quenya-vinted-retention.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now quenya-vinted-retention.timer
   ```
6. Optionally set `QUENYA_IMAGE_STORE=r2` and backfill remaining display objects.
7. **halfvec (optional, after dashboard size check):** on Postgres with pgvector ≥ 0.7, convert `mix` only after a backup; keep ranking on `mix`; archive `img`/`txt` if unused. Do **not** run without a measured `item_embeddings` size and a rollback plan. Approximate: float32 `vector(512)` ≈ 2 KiB; three columns ≈ 6 KiB/item; `halfvec` mix-only ≈ 1 KiB/item.

## What this PR implements (safe, code-only)

- Docs plan (this file).
- `/img` Cache-Control headers.
- Smaller default encode (`QUENYA_IMAGE_MAX_SIDE=1280`, quality `80`).
- Catalogue column trim; cheaper Postgres embedding sample.
- Batched `get_items_with_tags`; SQL `model_stats`.
- Hotlink responses omit `image_preview`; retention can `--clear-previews`.
- Migration `0005_cost_indexes`.
- Vinted retention systemd timer/service units + README note.
