# Tickipate

Event-driven stock research. Research, not investment advice.

The source of truth is [the requirements](docs/tickipate_requirements.md).
[AGENTS.md](AGENTS.md) records conventions and install, test, lint, and run commands.

## Current state

Phase 1 includes a FastAPI application with `GET /health`, a static Next.js
placeholder, and earnings ingestion into PostgreSQL. Finnhub supplies the
upcoming calendar and available recent history. Alpha Vantage supplies a
five-year historical target, using the user-approved free source plan.

Events deduplicate by canonical ticker and release date. Distinct source
observations retain their fetched timestamps and inputs. Session-only records
have explicitly approximate anchors; exact release times remain unavailable,
and intraday eligibility is false. Conflicting session reports clear the anchor
instead of choosing a vendor. Historical outcomes and signals are later phases.

GitHub Actions CI runs on every push and pull request. It provisions PostgreSQL
and runs the backend suite, Python lint and format checks, frontend TypeScript
and ESLint checks, and a production frontend build.

## Configuration

Copy `.env.example` to `.env` and set `FINNHUB_API_KEY`, `ALPHA_VANTAGE_API_KEY`,
and `DATABASE_URL`. All three are backend-only. Never put credentials in frontend
environment variables. Use a Supabase direct connection or session pooler that
permits backend writes, with `sslmode=require`. The transaction pooler is not
supported because refresh serialization uses a session advisory lock. The tables have RLS enabled and no public
Data API policies. Supabase has not been provisioned or modified by this task.

Initialize the tables with `.venv/bin/python -m backend.ingestion.refresh --init-db`.
Then run `.venv/bin/python -m backend.ingestion.refresh --symbols AAPL,MSFT`, replacing
the example with an explicit allowlist of current S&P 500 tickers. Automatic
point-in-time membership resolution belongs to Phase 3; this allowlist must not
be used as a historical membership assertion. Use `--sources finnhub` or
`--sources alpha_vantage` to refresh just one source.

Finnhub requests are batched and bounded by its 60-call rolling minute budget.
The calendar covers the available past month and the next seven days, with a
six-hour refresh TTL. Alpha Vantage retrieves quarterly histories for the
allowlist, filters to five years, and prioritizes never-fetched and then oldest
tickers. A database-backed quota permits at most 25 requests in a rolling 24-hour
window across processes; exhausted work resumes on later runs. Requests that
fail still consume a reservation. HTTP or entitlement errors stop ingestion
without switching sources or upgrading plans. Only observed quota headers are
logged as vendor quota; local remaining reservations are labeled separately.

## Tests

Start the dedicated PostgreSQL test container using the command in AGENTS.md,
then run `.venv/bin/python -m pytest`. Tests use a database named `tickipate_test`
and clear its tables. Do not use a production database for tests. CI supplies a
fresh service automatically. Fixtures are synthetic, modeled after documented
response schemas; their ticker dates and values are not claimed as real data.
Source HTTP calls are mocked in the automated suite.

## Scheduled refresh

The earnings workflow runs four times daily and supports manual dispatch. Set
GitHub Actions secrets `DATABASE_URL`, `FINNHUB_API_KEY`, and
`ALPHA_VANTAGE_API_KEY`, plus repository variable `TICKIPATE_SYMBOLS` (the
comma-separated allowlist), after initializing the database. Unconfigured jobs
print the missing setting names and skip ingestion. A workflow concurrency
group and a database advisory lock prevent overlapping refreshes. API quota and
backfill progress survive GitHub runner restarts.

The workflow becomes scheduled only on the default branch after review and
merge. No deployed service, live backfill, or paid subscription was created.
See [the feasibility note](docs/phase1_feasibility.md) for verification limits.

Backend dependencies are pinned in `backend/requirements.txt`, and the test/lint
environment is pinned in `backend/requirements-dev.txt`. The `.in` files record
the intended direct dependencies. Commit updated locks when changing packages.
Frontend installation uses `frontend/package-lock.json` with `npm ci`.
