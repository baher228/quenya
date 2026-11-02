# Agent notes

## Cursor Cloud specific instructions

- Default job runner is `QUENYA_JOB_RUNNER=inline` (BackgroundTasks / in-process threads). Set `QUENYA_JOB_RUNNER=worker` for durable queue behaviour used on serverless: catalogue fetch, Vinted refill, and live Find tagging enqueue into `jobs` and are claimed by `python -m app.worker`.
- Live Find (`POST /api/search/live`): inline tags synchronously; worker mode returns untagged hits plus `job_id` while `live_tag` runs in the worker. Previously failed tag rows (`tag_error` set) are not re-queued and are omitted from the response.
- Rate limits for admin login and waitlist are durable in `rate_limit_events` (SQLite schema + Postgres migration `0003_rate_limits`), not process memory.
- Standard commands: backend `pytest` / `ruff check` from `backend/`; frontend `npm test` (vitest) + `npm run typecheck` / `npm run dev` from `frontend/`; scraper tests via `pytest scraper/tests` (also in CI). See root `README.md` for invite seeding and API/frontend ports.
