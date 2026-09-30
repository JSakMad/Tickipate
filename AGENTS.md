# AGENTS.md — Tickipate

Standing rules for every Codex task in this repo. Read this file before starting any task, even if the task description doesn't repeat it.

## What this project is
An event-driven research engine for S&P 500 stocks. It tracks upcoming catalysts (earnings, FOMC/CPI/jobs releases, FDA decisions, insider and congressional filings), measures how each ticker has historically reacted, tags how long each reaction took to show up, and produces scored signals, hypothetical trade ideas, and alerts.

Full spec: `docs/tickipate_requirements.md`. That document is the source of truth. This file holds the conventions that apply to every phase.

## Stack (do not substitute without asking)
- Frontend: Next.js + TypeScript on Vercel (free tier)
- Backend: Python + FastAPI on Render (free tier)
- Database: PostgreSQL on Supabase (free tier)
- Scheduled jobs: GitHub Actions cron
- Analysis: pandas, NumPy, SciPy, statsmodels
- Trading calendar: `pandas_market_calendars` (NYSE) for trading days, early closes, and holidays
- Alerts: Telegram Bot API by default, Gmail API as an option
- Data sources: only the free sources listed in spec Section 4. Never swap in a paid vendor. If a free source can't do the job, stop and flag it.

## Non-negotiable rules

1. **No look-ahead.** Any statistic used to score an event may only use data that existed before that event's timestamp. Every historical query must be bounded by the event timestamp, and tests must assert this.
2. **Congressional trades are timed by disclosure, never by trade date.** Same principle for Form 4: use the EDGAR acceptance timestamp.
3. **Macro actuals come from ALFRED first-release vintages**, not revised series.
4. **Point-in-time universe.** Backtests use S&P 500 membership as of each event date, not today's constituent list.
5. **Never invent a number.** Missing option chain, missing consensus, unknown earnings session, or too little history means the field is marked unavailable or insufficient. Never fill a zero or a default.
6. **Statistical guardrails are mandatory.** Minimum 8 events per ticker and event type for per-ticker stats (otherwise `pooled` or "insufficient history"). Benjamini-Hochberg FDR control at q = 0.10 before anything is labeled significant. Every mean and hit rate ships with a confidence interval and sample size.
7. **Store derived outcomes, not raw bars.** Supabase's free tier is small. Fetch minute bars transiently, compute window returns, save only the results.
8. **Respect free-tier limits.** Finnhub 60 calls/min. Alpha Vantage 25 calls per rolling 24 hours (conservative enforcement of its daily limit). SEC EDGAR: declared User-Agent with a contact email, max 10 requests/second. Alpaca free plan: historical requests must end at least 15 minutes in the past. Throttle and log remaining quota.
9. **No trading.** Alpaca is used for market data only. No order endpoints, no brokerage connection, no paper trading in v1.
10. **Research label everywhere.** Every signal, idea, and alert displays "Research, not investment advice."
11. **The idea ledger is append-only.** Once an idea is saved, its inputs cannot be edited. Only its outcome fields are filled in later.
12. **Timestamps are stored in UTC** and displayed in US Eastern time.

## Working style
- Build in the phases listed in spec Section 14. One phase per task, one PR per phase.
- Each functional requirement in the spec has a `Test:` line. That line is the acceptance criterion. Write the test, make it pass, then call the phase done.
- If the spec is ambiguous on anything touching the rules above, stop and ask. Don't guess.
- Run the full test suite before opening a PR and paste the output into the PR description.
- In every PR description, name the spec sections implemented and list any deviations with reasons.

## Code style
- Code should read like a careful person wrote it by hand. Prefer explicit `for` loops over dense one-line list comprehensions when the logic has more than one step.
- Comments explain the reasoning behind a decision (why a window starts at the pre-event price, why a trade date is ignored), not what the syntax does.
- Small, named functions over long pipelines. Name things after the finance concept they represent (`reference_price`, `abnormal_return`, `latency_tag`).
- No secrets in the repo. API keys live in `.env` locally (listed in `.gitignore`) and in GitHub Actions secrets / Render environment variables in deployment. Keep `.env.example` current with every key name and no values.

## Suggested repo layout
```
docs/                  spec and methodology notes
backend/
  ingestion/           one module per data source
  events/              event model and timestamp normalization
  outcomes/            window returns, abnormal returns, latency tags
  signals/             S1-S5, guardrails
  ideas/               ranking, structure mapping, ledger
  alerts/
  api/                 FastAPI routes
  tests/
frontend/              Next.js app
.github/workflows/     scheduled jobs and CI
```

## Commands
Run backend commands from the repository root. Use Python 3.14 and Node.js 24.
- Install: `python3 -m venv .venv && .venv/bin/python -m pip install -r backend/requirements-dev.txt && npm --prefix frontend ci`
- Backend tests: `.venv/bin/python -m pytest`
- Frontend tests: `npm --prefix frontend test` (Next.js type generation and TypeScript check; the placeholder has no UI test suite yet)
- Lint: `.venv/bin/python -m ruff check backend && .venv/bin/python -m ruff format --check backend && npm --prefix frontend run lint`
- Run API locally: `.venv/bin/python -m uvicorn backend.api.main:app --reload`
- Run frontend locally: `npm --prefix frontend run dev`
- Frontend production build: `npm --prefix frontend run build`
- Start the dedicated test database (Docker must be running): `docker run --name tickipate-test-db --rm -d -e POSTGRES_PASSWORD=tickipate_test -e POSTGRES_DB=tickipate_test -p 127.0.0.1:55432:5432 postgres:17`
- Stop the dedicated test database: `docker stop tickipate-test-db`
- Persistence tests default to this local database. Override with `TEST_DATABASE_URL`; its database name must be `tickipate_test` because tests clear its tables. CI provisions PostgreSQL automatically. Backend tests must run with PostgreSQL available; no FR1 tests are skipped.
- Initialize Phase 1 tables after setting `DATABASE_URL` in `.env`: `.venv/bin/python -m backend.ingestion.refresh --init-db`
- Refresh earnings after setting both API keys: `.venv/bin/python -m backend.ingestion.refresh --symbols AAPL,MSFT` (replace the example with the intended current constituent allowlist)
