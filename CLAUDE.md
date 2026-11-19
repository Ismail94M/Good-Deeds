# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Good Deeds is a social feed of good deeds that people **witness others** doing, launching in Bangalore. `docs/PRD.md` is the source of truth for product rules (verification, caps, scoring, prizes). Check it before changing business logic. Every tunable limit there (same-act cap, daily caps, invite expiry, 30% reporter concentration, evidence caps) must be stored in config rows, never hard-coded.

The build is split into milestones M0–M8 (`docs/ROADMAP.md`). Each milestone is reviewed by the user before the next one starts.

## Commands

Full stack (Postgres+PostGIS, migrations, API :8000, worker, web :5173):
```
docker compose up -d --build
```

Backend (`cd backend`, Python 3.12, managed with `uv`; tests need the `db` container running):
```
uv sync
uv run pytest                                   # all tests (uses DB gooddeeds_test, auto-created + migrated)
uv run pytest tests/test_events.py::test_run_handler_is_idempotent   # single test
uv run ruff check . && uv run ruff format .
uv run mypy app tests                           # strict
uv run alembic revision --autogenerate -m "..." # then review the file
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
uv run procrastinate --app=app.core.jobs.job_app worker
```

Frontend (`cd frontend`, React 19 + TypeScript + Vite + Tailwind v4; `/api` is proxied to the backend):
```
npm run dev
npm test                                        # vitest
npx vitest run src/features/home/HomePage.test.tsx -t "healthy"   # single test
npm run lint && npm run typecheck && npm run format:check
```

## Architecture

**Backend: modular monolith** (`backend/app`).
- `core/`: shared infrastructure:
  - `config` (env vars prefixed `GD_`): deployment settings only (secrets, URLs, token lifetimes, cookie flag, providers)
  - `runtime_config` (versioned, append-only `config_versions` rows; one namespace per area, typed by a `RuntimeConfig` subclass with no defaults, read with `load()`, changed with `publish()`). All product limits live here, including M1's OTP and rate limits
  - `db` (async SQLAlchemy over psycopg 3)
  - `events` (transactional outbox)
  - `jobs` (Procrastinate queue in Postgres)
  - `audit` (append-only log)
  - `security` (argon2 hashing off the event loop, JWT, keyed hashes with a purpose prefix)
  - `ratelimit` (fixed-window counters in Postgres; the `Limiter` dependency)
  - `errors` (`AppError` subclasses → JSON `{detail, code}`; services raise these, routers stay thin)
  - `sms` (provider interface; `console` is dev-only)
- `modules/<name>/`: feature modules. Each must be listed in `modules/registry.py`. Its `router.py`, `models.py`, `handlers.py` and `jobs.py` are imported automatically. Private helpers are `_name.py`.
- **Module boundary rule:** a module may import only `app.core` and another module's `service.py`. Cross-module side effects go through domain events, never direct calls. `tests/test_architecture.py` enforces this.
- **identity** (M1): accounts, sessions, phone OTP, consent, QR codes, forgot password (M2 addition: `/api/auth/password/forgot` and `/reset`; `phone_otps.purpose` keeps phone-verify and reset codes apart; a reset revokes all sessions and bumps `token_version`).
  - Other modules use its `service.py`: `CurrentUser`, `OnboardedUser` (adult + consents + verified phone), `require_role`, `resolve_qr_token` and `resolve_manual_code` (both return a `ResolvedDoer` with the token window; the static short ID alone never resolves), `phone_hashes`, `device_hash`, `device_hashes`, `public_summaries`, `is_active`. QR window and grace come from the `qr` config namespace (`_qr.QrConfig`); OTP and auth rate limits from the `identity` namespace (`_limits.IdentityLimits`).
  - **Auth model:** short-lived JWT access token held in browser memory. A rotating refresh token in an httpOnly cookie on `/api/auth`, stored hashed. `users.token_version` in the JWT lets `revoke_all_sessions` kill access tokens too.
- **deeds** (M2): QR scans, deeds (QR and invite paths), same-act grouping, invites, edit/withdraw, self-logs, profile deed counts. One module because verification, grouping and acceptance change a deed in one transaction. Design and decisions: `docs/M2-DESIGN.md`.
  - Limits come from the `deeds` config namespace (`_limits.DeedLimits`).
  - Lock order (keep it, to avoid deadlocks). QR deed: `reporter_month` advisory lock → scan row → `same_act` advisory lock → deed rows. Invite create: `reporter_month` → `invites` advisory lock. Accept: `reporter_month` advisory lock (reporter read unlocked first by token, then re-checked on the locked invite row) → invite row → `shared_device` advisory lock → pending deed row → `same_act` (safe because a pending deed is in no group yet). Withdraw: invite row → `same_act` → deed row.
  - Invites are switched off by config (`invites_enabled`, PO decision D17); invite tests turn them on with the `invites_on` fixture.
  - Responses and event payloads never contain coordinates, phone numbers or free text, and never name the doer of a pending deed. Non-owners get 404, not 403.
  - Other modules use `deeds/service.py` (`get_deed`, `deeds_for_doer`, `same_act_group_view`).

**Event flow:**
1. A service calls `emit(session, SomeEvent(...))` in the same transaction as its change. This writes an `outbox_events` row.
2. The worker's periodic `core.dispatch_outbox` (every 5 s) enqueues one `core.handle_event` job per subscribed handler.
3. `run_handler` records `(event_id, handler)` in `processed_events` in the handler's transaction, so retries are idempotent.
4. Subscribe with `@subscribe(EventClass)` in a module's `handlers.py`. Events subclass `DomainEvent` with a unique `event_type`.

**Append-only data:** `audit_log`, `config_versions`, `verification_events` and `deed_revisions` (and later the points ledger) reject UPDATE and DELETE through the `forbid_modification()` trigger. Corrections are written as new reversal rows.

**Migrations:** Alembic migrations are written by hand or autogenerated and then reviewed. Migration `0001` installs PostGIS and Procrastinate's schema. When upgrading Procrastinate, add its migration SQL in a new Alembic revision.

**Tests:**
- The `session` fixture wraps each test in a transaction that is rolled back (`join_transaction_mode="create_savepoint"`), so code under test may commit. The `client` fixture overrides `get_session` with that session.
- `tests/identity/conftest.py` has a stricter `client` that rolls back a failed request's work, as the real app does. Commit any setup data before calling the API.
- Rate limits use a fresh in-memory limiter per test (autouse `rate_limiter` fixture). To change a limit in a test, publish a config version (`publish_limits` in `tests/identity/helpers.py` and `tests/deeds/helpers.py`); never patch settings.
- SMS and OAuth are faked through dependency overrides. Read OTP codes from `ConsoleSmsProvider.sent`.
- Concurrency tests (`tests/deeds/test_*concurrency.py`) use committed data in separate sessions and an `asyncio.Barrier` so both callers really overlap; their fixtures clean up afterwards.

**Frontend:**
- **Layout:** `src/app` (routes, layout, route guards `RequireAuth`/`RequireOnboarded`), `src/features/<feature>` (pages, components and their tests), `src/components/ui.tsx` (shared UI).
- **API client:** `src/lib/api.ts`. All calls go to `/api/...`, with the access token held in memory. A 401 triggers one shared refresh, using `navigator.locks` across tabs, then a single retry. Every request sends an `X-Device-Id` header.
- **State:** server state uses TanStack Query. The signed-in user is the `['me']` query, provided by `features/auth/AuthProvider`.
- **Test helpers:** `src/test/utils.tsx` has a fetch mock keyed by method and path, plus `renderApp`.

## Gotchas

- **Settings default to production and refuse unsafe values** (dev secret, insecure cookies, console SMS, `*`/http CORS). Running locally needs `GD_ENV=development` and `GD_COOKIE_SECURE=false`. docker-compose sets them; for running outside Docker, copy `backend/.env.example` to `backend/.env`. Tests set `GD_ENV=test`.
- Locally, OTP codes appear in the API log: `docker compose logs api | grep "DEV SMS"`.
- The Google sign-in button appears only when `GD_GOOGLE_CLIENT_ID` is set in the root `.env` (docker-compose passes it to the API). Setup steps are in `docs/SETUP-GOOGLE-SIGNIN.md`.

- On Windows, psycopg's async mode needs a selector event loop. `alembic/env.py` and `tests/conftest.py` already handle this. Do the same in any new entry point that runs asyncio on Windows.
- The local directory `backend/alembic/` has the same name as the `alembic` package. Ruff is configured to treat `alembic` as third-party.
- Object storage (photos/videos) is not set up yet. Decision (2026-11-18): code against the S3-compatible API, Google Cloud Storage (asia-south1) at launch via its S3-interoperable XML API and HMAC keys, and a local S3-compatible container in development (the MinIO image is no longer on Docker Hub, so M3 picks a replacement).

## Claude Code hooks

Project hooks are in `.claude/settings.json`, and the scripts are in `.claude/hooks/` (Node). They only load when Claude Code is started in this folder.
- **PreToolUse `guard.mjs`:**
  - denies reading `.env` and other secret files, and printing environment variables;
  - asks before any push, any destructive git or delete command, any DB-destructive command (`down -v`, `alembic downgrade`, DROP/DELETE/TRUNCATE…), edits to existing migrations, lockfiles, CI or the hooks themselves;
  - never auto-approves.
- **PostToolUse `format-lint.mjs`:** runs ruff or prettier and oxlint on the edited file only.
- **Stop `validate.mjs`:** checks the changed code only, using lint, mypy/tsc, related pytest/vitest and the Vite build.
  - It is skipped when nothing changed, when the changes already passed, or while background agents are running.
  - It blocks once on failure.
  - The full test suite stays in CI.
- **SessionStart / PreCompact:** load and preserve the project status (milestone, open PO questions, git, standing rules). State lives in the gitignored `.claude/state/`.

## Specialist subagents

Project subagents live in `.claude/agents/`. For each roadmap milestone:
1. **architect** designs the milestone and writes a task list with an owner for each task.
2. **database-specialist** handles schema changes and migrations. **backend-developer** and **frontend-developer** build the features, in parallel where the task list allows.
3. **qa-engineer** tests the PRD rules, including boundaries and abuse cases.
4. **security-reviewer** reviews read-only, before the milestone is committed.
5. **architecture-diagram** updates the draw.io diagrams in `docs/architecture/` after the security review, before product-owner review.

The main session coordinates: it hands tasks to agents, checks their reports, and asks the product owner for review before the next milestone. AI-specialist and DevOps agents will be added when AI scoring (M4) and deployment work begin.
