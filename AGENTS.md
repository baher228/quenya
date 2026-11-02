# Agent notes

## Cursor Cloud specific instructions

Quenya is one product: a FastAPI API, a Vite PWA (`frontend/`, port 5173), and a separate marketing site (`marketing/`, port 5174). Standard install, dev-server, lint, and test commands are in the root `README.md` and `.github/workflows/ci.yml`. The iOS app needs Xcode and does not run in this Linux environment.

- Local data is SQLite unless `QUENYA_DATABASE_URL` is set. `/api/health` still reports `schema.ok: false` on SQLite, because schema status is filled in only for Postgres. Use `/api/ready` as the local readiness check.
- Browser user agents are rejected for tester accounts. An admin session works in the browser. The PWA reads `localStorage["quenya.token"]`. `POST /api/auth/invite` with an admin invite returns that token. `/open` is the iPhone landing page and does not start a web session. `/admin` is a separate password login. Create the admin with `python -m app.accounts bootstrap-admin` and `set-password --generate` from `backend/`, and set `QUENYA_APP_URL=http://localhost:5173` so the printed invite points at Vite.
- Real model calls need `CURSOR_API_KEY` (provider `cursor`) or `QUENYA_LLM_PROVIDER=openai` with `QUENYA_LLM_API_KEY`, plus `TAVILY_API_KEY` for retail/live search. Put them in the gitignored root `.env` (see `.env.example`). Cursor mode also needs the CLI: `curl https://cursor.com/install -fsS | bash` so `~/.local/bin/cursor-agent` is on `PATH`. `ios/scripts/dev_backend.py` is the no-key fallback: deterministic fake model tasks and the tag ranker. Start it with `PYTHON_DOTENV_DISABLED=1`, `DB_PATH`, and `QUENYA_WARM_CACHES=0`. It stores images under `QUENYA_IMAGE_DIR` (default `~/ios-work/images`).
- `ios/scripts/seed_local_catalog.py` reads `https://app.quenya.co.uk/api/search`, and that route requires an invite. It will not fill a catalogue from this environment without a production session.
- The optional `embed` extra (Torch and OpenCLIP) is not part of the startup install. CI installs `backend[dev]` plus `scrapling[fetchers]`. Add the extra with `pip install -e 'backend[embed]'` when working on the image encoder.
- This image has Node 22. CI uses Node 20. Frontend and marketing `npm run typecheck`, `npm run build`, and Vite dev servers run on Node 22.
- Scraper checks are `pytest scraper/tests` from the repo root (also in the backend CI job). Frontend vitest runs via `npm test` in `frontend/` when present.
- Default job runner is `QUENYA_JOB_RUNNER=inline` (BackgroundTasks / in-process threads). Set `QUENYA_JOB_RUNNER=worker` for durable queue behaviour used on serverless: catalogue fetch, Vinted refill, and live Find tagging enqueue into `jobs` and are claimed by `python -m app.worker`.
- Live Find (`POST /api/search/live`): inline tags synchronously; worker mode returns untagged hits plus `job_id` while `live_tag` runs in the worker. Previously failed tag rows (`tag_error` set) are not re-queued and are omitted from the response.
- Rate limits for admin login and waitlist are durable in `rate_limit_events` (SQLite schema + Postgres migration `0003_rate_limits`), not process memory.
