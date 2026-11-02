# Quenya production-readiness checklist

Status key: **done** · **missing** · **risk** (partial / needs founder action).  
Code audit date: 2026-11-02 (Europe/London). Shared Supabase project — attribute costs/RLS carefully.

## Security

| Item | Status | Notes |
|------|--------|-------|
| Secrets via env / `.env.example` documented | done | Root + `deploy/vps/quenya.env.example`; CI blanks secrets |
| Secrets not logged | done | Access logs omit bodies; Sentry `before_send` strips auth/cookie |
| Invite + bearer auth | done | `accounts.py`; hashed invite/session; tester browser gate |
| Admin login rate limit | done | IP + name buckets |
| Waitlist rate limit | done | Per-IP |
| Expensive API rate limits (deck/find/outfits) | done* | Added in this PR (`QUENYA_API_RATE_*`) |
| Invite redeem rate limit + typed body | done* | This PR |
| CORS allowlist | done | `QUENYA_CORS_ORIGINS` (exact origins) |
| Pydantic validation on most bodies | done | `schemas.py`; invite body tightened in this PR |
| Security response headers | done* | App middleware + nginx notes in this PR |
| Dependency vulnerability scanning | risk | CI audit jobs added (continue-on-error); Dependabot config; founder must review alerts |
| Supabase RLS on `quenya.*` | missing / risk | App uses service role / DB URL; no RLS policies in repo. Founder: enable RLS or confirm network-only access |
| Vinted proxy shared key | done | HMAC compare; 404 if unset. Rate limit added this PR |
| Cookie auth | N/A | Bearer in `localStorage` (XSS risk if XSS exists — keep CSP/headers tight) |

\* = shipped in the production-readiness PR.

## Reliability

| Item | Status | Notes |
|------|--------|-------|
| `/api/live`, `/api/ready`, `/api/health` | done | Compose uses ready; deploy script requires health+schema.ok |
| HTTP timeouts on outbound calls | done | Shared image client; LLM/proxy timeouts env-tunable |
| Durable jobs + retries | done | `jobs.py` SKIP LOCKED, attempts, stale reclaim |
| Graceful shop/Vinted failure | done | Proxy 502; ingest continues; deck refill best-effort |
| Job idempotency | risk | Claim is safe; business handlers should stay idempotent (outfit gen) |
| Backups | missing | Founder: Supabase PITR / scheduled dumps; R2 versioning if used |
| Ingest timers + retention timer | risk | Ingest units exist; retention timer in #103 (unmerged) — install after merge |

## Observability

| Item | Status | Notes |
|------|--------|-------|
| Access logging + request IDs | done | `quenya.access`; `X-Request-ID` |
| Sentry hook behind env | done | `QUENYA_SENTRY_DSN` (api+worker); document in `.env.example` this PR |
| Uptime probe + GitHub outage issues | done | `.github/workflows/uptime.yml` every 10m |
| Metrics / dashboards | missing | No Prometheus/OTel export yet |
| Ingest failure alerting | missing | systemd journal only; founder: alert on timer failure / Sentry |

## Performance

| Item | Status | Notes |
|------|--------|-------|
| Catalogue + embedding sample caches | done | TTL refresh threads |
| DB indexes (source/fetched, rate limits) | done / risk | `0004` on main; cost indexes in #103 — also included here as `0005` if not yet applied |
| Pagination | risk | Deck/search capped; admin/llm_calls historically heavy (improved in #103) |
| Frontend bundle tooling | risk | Vite build; no bundle budget CI |
| Image lazy load / hotlink | done | #102 |

## Data / privacy (UK GDPR)

| Item | Status | Notes |
|------|--------|-------|
| Marketing privacy notice | done | `/privacy` + consent checkbox |
| App account deletion | done | Taste → Delete account → `/api/account/delete` |
| App privacy link | done* | Link to marketing privacy in Taste |
| Cookie consent (marketing) | done | No tracking cookies claimed; consent for waitlist |
| App cookies | N/A | Token in localStorage |
| Waitlist email + SMTP | done | Needs prod SMTP secrets |
| DSR export endpoint | missing | Manual process via privacy notice; optional later |

## Deploy

| Item | Status | Notes |
|------|--------|-------|
| CI tests + Ruff on PRs | done | `.github/workflows/ci.yml` |
| Frontend/marketing build (+test) | done | Matrix job |
| Postgres migrate idempotence job | done | |
| Docker image build | done | |
| Dependency audit in CI | done* | pip-audit + npm audit, non-blocking |
| Dependabot | done* | `.github/dependabot.yml` |
| Migrations additive + deploy runner | done | `deploy.sh` runs migrate then swap |
| Rollback script | done | `rollback.sh` (no down-migrate) |
| Zero-downtime | risk | Brief dual-version during migrate; additive-only rule |
| Env config | done | `/etc/quenya/quenya.env` |

## Legal / compliance in-app

| Item | Status | Notes |
|------|--------|-------|
| Shop listing links (noreferrer) | done | Open retailer pages |
| Affiliate / commercial disclosure (ASA) | done* | In-app disclosure copy; update if affiliate IDs go live |
| Ranking unchanged by legal photo policy | done | #102 |

## Top 10 gaps by risk (founder view)

1. **Supabase RLS / network exposure of DB** — critical if DB reachable beyond app — **missing**  
2. **No automated backups verified** — critical data loss risk — **missing**  
3. **Sentry DSN unset in prod** — high (blind to errors) — **risk** until env set  
4. **Expensive API abuse without limits** — high — **fixed in this PR**  
5. **Unauthenticated health leaks ops stats** — med — **risk** (needed by deploy; acceptable on private host)  
6. **Dependency vulns untriaged** — med — **risk** (scanning added)  
7. **Ingest/retention failure silent** — med — **missing** alerting  
8. **Affiliate disclosure** — med (ASA if commercial) — **fixed copy; confirm programme**  
9. **Retention cron not installed** — med disk/legal — depends on #103 merge + VPS install  
10. **Metrics/alerting beyond uptime issue** — med — **missing**

## Founder actions

1. Review & merge this PR (do not apply secrets here).  
2. Set `QUENYA_SENTRY_DSN` (+ optional `QUENYA_SENTRY_TRACES`, `QUENYA_ENV`, `QUENYA_RELEASE`) on VPS and Vercel API.  
3. Confirm Supabase: RLS or IP allowlist; PITR/backups; transaction pooler URL.  
4. After #103 (or this PR’s `0005`): run migrate; dry-run retention + purge; enable retention timer.  
5. Review Dependabot / audit CI output; patch high CVEs.  
6. If affiliate links ship: update disclosure with partner names and mark affiliate URLs.  
7. Wire ingest failure alerts (email/Slack on systemd failed units or Sentry).
