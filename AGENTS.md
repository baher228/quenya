## Cursor Cloud specific instructions

Quenya is one product: a FastAPI API, a Vite PWA (`frontend/`, port 5173), and a separate marketing site (`marketing/`, port 5174). Standard install, dev-server, lint, and test commands are in the root `README.md` and `.github/workflows/ci.yml`. The iOS app needs Xcode and does not run in this Linux environment.

- Local data is SQLite unless `QUENYA_DATABASE_URL` is set. `/api/health` still reports `schema.ok: false` on SQLite, because schema status is filled in only for Postgres. Use `/api/ready` as the local readiness check.
- Browser user agents are rejected for tester accounts. An admin session works in the browser. The PWA reads `localStorage["quenya.token"]`. `POST /api/auth/invite` with an admin invite returns that token. `/open` is the iPhone landing page and does not start a web session. `/admin` is a separate password login. Create the admin with `python -m app.accounts bootstrap-admin` and `set-password --generate` from `backend/`, and set `QUENYA_APP_URL=http://localhost:5173` so the printed invite points at Vite.
- Real model calls need `CURSOR_API_KEY` (provider `cursor`) or `QUENYA_LLM_PROVIDER=openai` with `QUENYA_LLM_API_KEY`. `ios/scripts/dev_backend.py` serves the API on port 8000 with deterministic fake model tasks and the tag ranker. Start it with `PYTHON_DOTENV_DISABLED=1`, `DB_PATH`, and `QUENYA_WARM_CACHES=0`. It stores images under `QUENYA_IMAGE_DIR` (default `~/ios-work/images`).
- `ios/scripts/seed_local_catalog.py` reads `https://app.quenya.co.uk/api/search`, and that route requires an invite. It will not fill a catalogue from this environment without a production session.
- The optional `embed` extra (Torch and OpenCLIP) is not part of the startup install. CI installs `backend[dev]` plus `scraper/requirements.txt` (`scrapling[fetchers]`). Add the extra with `pip install -e 'backend[embed]'` when working on the image encoder.
- This image has Node 22. CI uses Node 20. Frontend and marketing `npm run typecheck`, `npm run build`, and Vite dev servers run on Node 22.
- Scraper checks are `pytest scraper/tests` from the repo root using the backend virtualenv. They are not in CI.
