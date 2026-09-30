# Tickipate
## Requirements Document & Build Plan (for implementation via ChatGPT Codex)

Name: ticker + anticipate. Swappable later without code impact; use `tickipate` as the repo/package name.

---

## 1. Vision

A research-grade engine that tracks known upcoming market catalysts for S&P 500 stocks (earnings, macro releases, FDA decisions, insider and congressional filings), measures how each ticker has historically reacted to each event type, and turns that history into scored signals and hypothetical trade ideas. Queried on demand, with alerts pushed when something worth attention is coming up.

The core claim the product makes is narrow and testable: *"For this ticker and this event type, here is how the stock has reacted in the past, how reliable that reaction is, how quickly it shows up, and how that compares to what the options market is pricing in now."* It never claims to predict a specific outcome.

---

## 2. Locked Decisions

| Decision | Choice |
|---|---|
| Resume track | Quant. **Replaces the Alternative-Data Stat Arb project.** The congressional and Form 4 insider signals from that project are absorbed here, so nothing is lost. |
| Universe (v1) | S&P 500 current constituents, tracked point-in-time (Section 6.4) |
| Event types (v1) | Earnings, macro releases (FOMC, CPI, jobs report), FDA/biotech catalysts, insider (Form 4) and congressional (STOCK Act) filings |
| Query output | Scored signals with backtested stats, ranked hypothetical trade ideas with a suggested structure, and pushed alerts |
| Primary horizon | Within one trading day of the event. Every outcome is also tagged with how long the reaction took to materialize (Section 7.3) |

---

## 3. Research Basis (why these signals, and what's already out there)

- **Implied vs. realized event moves.** Implied volatility tends to sit above subsequent realized volatility around events, because option sellers demand compensation for event risk. Comparing a ticker's historical event-day move to the move currently priced into its options is a standard, defensible approach. This is Signal S1.
- **Post-earnings announcement drift (PEAD) is contested, so measure it rather than assume it.** Martineau (2022) reported drift fading from non-microcap stocks after 2001 and vanishing by 2006; Dickerson, Julliard and Mueller (2025/26) conclude it remains a major factor. Tickipate computes drift per ticker and reports whether it exists for that name. This framing is a deliberate interview talking point.
- **Existing tools cover pieces of this.** EarningsWatcher offers per-ticker earnings move and drift analysis; many open-source repos build earnings calendars or Form 4 screeners in isolation. The differentiation here is combining four event types, cross-event confluence (e.g., insider buying ahead of earnings), explicit outcome-latency tagging, statistical guardrails against false discoveries, and a live ledger that scores every past idea (FR8).

---

## 4. Data Sources (all free)

| Data | Source | Notes |
|---|---|---|
| Earnings, IPO calendars; company EPS history | Finnhub free tier | 60 calls/min. Earnings calendar includes BMO/AMC session timing. |
| FDA catalysts (PDUFA dates, AdComs) | pdufa.bio free read-only JSON API; Finnhub FDA committee calendar as a cross-check | pdufa.bio is independent, not an FDA product. Treat every date as provisional and confirm against the company's 8-K or press release where possible. |
| FDA outcomes (historical) | FDA Drugs@FDA database | Official; used to label historical approval/CRL outcomes. |
| FOMC schedule | federalreserve.gov FOMC calendar | Official. Statement time is 2:00 PM ET. |
| CPI, jobs report schedules | bls.gov release calendar | Official. Releases at 8:30 AM ET. |
| Macro actuals (point-in-time) | FRED / ALFRED (St. Louis Fed) | ALFRED vintages give the value as first reported, which avoids revised-data look-ahead. |
| Macro consensus estimates | **Open question (Section 15).** No reliable free consensus source confirmed. | Until resolved, surprise is measured against prior value and tagged `surprise_basis = prior` (Section 7.2). |
| Form 4 insider filings | SEC EDGAR via EdgarTools (free, no key) | Respect SEC fair-access rules: declared User-Agent, max 10 requests/second. |
| Congressional trades | Disclosed Capitol free tier; House Clerk and Senate eFD as primary-source fallback | Disclosures can lag trades by up to 45 days (Section 6.3). |
| Intraday + daily price history | Alpaca Market Data API, free plan | Free accounts can query full consolidated (SIP) history as long as the requested end time is at least 15 minutes old. Ideal for backtesting. |
| Option chains (implied move) | Alpaca Indicative options feed (free, 15-min delayed); yfinance as fallback only | yfinance is known to fail silently at scale. Confirm how far back Alpaca's historical options data goes before relying on it for backtests. |
| S&P 500 membership history | Public constituent change lists (S&P press releases, maintained GitHub datasets) | Needed for point-in-time universe (Section 6.4). |

**REQ-D1:** no source in this table may be replaced with a paid vendor without explicit approval. If a free source proves insufficient, stop and flag it.

---

## 5. Compliance & Scope Guardrails

- **REQ-C1:** Public data only. No scraping of any site whose terms prohibit it; prefer documented APIs and official schedules.
- **REQ-C2:** Every signal, idea, and alert carries a visible "research, not investment advice" label.
- **REQ-C3:** v1 does not place trades or connect to a brokerage account. Alpaca is used for market data only. (Paper-trading integration is a possible v2.)
- **REQ-C4:** No invented numbers. If an input is missing (no option chain, no consensus, too few historical events), the output says so rather than filling a default.

---

## 6. Event Timing & Methodology Rules (the part interviewers will probe)

### 6.1 Event timestamp normalization
Every event is stored with the exact moment the information became public, not just a date:
- Earnings: BMO → pre-market of that date; AMC → after close; unknown session → flagged, excluded from intraday windows.
- FOMC: 2:00 PM ET statement. CPI/jobs: 8:30 AM ET.
- Form 4: EDGAR filing acceptance timestamp.
- Congressional: the public disclosure timestamp, **never** the underlying trade date.
- FDA: announcement timestamp from the company release when known; otherwise the PDUFA date, tagged `timing_confidence = low`.

### 6.2 Reference price
The last tradable price **before** the event timestamp. Every return in the system is measured from this point.

### 6.3 Look-ahead protection
- Congressional trades can be disclosed weeks after execution. Measuring returns from the trade date would use information nobody had at the time. All congressional signals start from disclosure time.
- Macro actuals come from ALFRED first-release vintages, not revised series.
- Historical stats used to score an event may only include events that occurred before it (walk-forward). Tested explicitly in FR5.

### 6.4 Survivorship protection
Backtests use S&P 500 membership as of each event date, not today's list. A stock that was removed from the index still counts for events while it was a member.

### 6.5 Abnormal returns
Returns are market-adjusted: ticker return minus SPY return over the same window. Sector-ETF adjustment is an optional toggle for comparison.

---

## 7. Signal Specifications

### 7.1 Outcome windows
Measured from the Section 6.2 reference price for every event:
`+5m, +30m, +1h, event-session close, next open, T+1 close` (primary horizon) and extended windows `T+3, T+5, T+10, T+20` (used for latency tagging and drift).

### 7.2 Signals by event type

**S1 — Earnings**
- Implied move: at-the-money straddle price for the first expiry after the event, divided by spot.
- Historical realized move: distribution of absolute abnormal T+1 returns over past earnings for that ticker.
- `move_ratio = implied_move / median_historical_move`. Above 1 suggests options are pricing more movement than the ticker has historically delivered; below 1, less.
- Direction consistency and per-ticker drift probability (does the T+1 direction continue to T+5/T+20?), reported with sample size.

**S2 — Macro (FOMC, CPI, jobs)**
- Per-ticker sensitivity: regression of abnormal return on the release surprise, by event type.
- `surprise_basis` field: `consensus` if a consensus source is added later, otherwise `prior`. The UI must show which basis was used.
- Absolute-move profile: how much each ticker typically moves on each release type, regardless of direction.

**S3 — FDA**
- Upcoming PDUFA/AdCom date proximity for S&P 500 names.
- Historical reaction to approvals vs. complete response letters for that company, when enough history exists.
- **Known limitation:** single-drug decisions move large-cap pharma far less than small-cap biotech, and the sample per ticker is small. Expect this module to produce fewer, lower-confidence signals within the S&P 500. See Section 15 for the option to extend it.

**S4 — Insider and congressional**
- Insider cluster buy: 3+ distinct insiders making open-market purchases (Form 4 transaction code `P`) within 10 trading days. Exclude option exercises, gifts, and 10b5-1 plan sales.
- Congressional buy: purchase disclosures, measured from disclosure time.
- Reaction measured across all outcome windows.

**S5 — Confluence**
Flags overlap between event types, starting with: insider cluster buy within 30 days before an earnings date, and congressional buy disclosed within 30 days before an FDA decision. Confluence signals are scored separately so their hit rate can be compared against single-event signals.

### 7.3 Outcome latency tagging
For every historical event, compute cumulative abnormal return (CAR) at each window. Define the peak as the largest absolute CAR across all windows through T+20. The **latency tag** is the earliest window at which |CAR| reaches 80% of that peak:

| Tag | Earliest window reaching 80% of peak |
|---|---|
| Immediate | +5m or +30m |
| Intraday | +1h through event-session close |
| Next-day | Next open or T+1 close |
| Multi-day | T+3 or T+5 |
| Slow | T+10 or T+20 |

Each ticker × event type gets a latency distribution (e.g., "NVDA earnings: 70% Immediate, 20% Next-day, 10% Multi-day"). This directly answers "how long does it take to show up," and it tells you whether a within-one-day horizon even fits that name.

### 7.4 Statistical guardrails
- **REQ-S1 Minimum sample:** per-ticker stats need at least 8 historical events of that type. Below that, fall back to a pooled sector-level estimate, clearly labeled `pooled`, or show "insufficient history."
- **REQ-S2 Multiple testing:** with ~500 tickers × 5 event types × 10 windows, many "significant" results will be noise. Apply Benjamini-Hochberg false discovery rate control (q = 0.10) before any signal can be labeled significant.
- **REQ-S3 Uncertainty:** every reported hit rate and mean carries a confidence interval and sample size. No bare point estimates.

---

## 8. Trade Idea Ranking

Ideas are hypothetical and labeled as such (REQ-C2).

- **Score** combines three normalized components: edge size (e.g., distance of `move_ratio` from 1, or mean abnormal return), statistical confidence (sample size, FDR-adjusted significance, CI width), and liquidity (average daily volume, option open interest and bid-ask spread).
- **Structure mapping** (starting rules, all configurable):
  - `move_ratio` well above 1 with a historically stable move size → short-volatility candidate (e.g., defined-risk iron condor)
  - `move_ratio` well below 1 → long-volatility candidate (e.g., straddle)
  - Strong, FDR-significant directional history with Immediate/Intraday latency → directional equity candidate for the event session
  - Multi-day/Slow latency → flagged as outside the primary horizon rather than forced into a same-day idea
- **Cost realism:** every historical rule-performance number is computed net of an assumed spread and slippage, stated in the UI.

---

## 9. Alerts

- Channel: Telegram bot (free) as the default; Gmail API email as an alternative.
- Triggers: a new idea above a configurable score threshold; a watchlisted ticker has an event within 24 hours; a new insider cluster or congressional buy on an S&P 500 name.
- Controls: daily alert cap, quiet hours, per-trigger on/off toggles.

---

## 10. Functional Requirements & Tests

**FR1 — Event calendar ingestion.** Pull all four event types into a unified `Event` table with normalized timestamps (Section 6.1) and dedupe across sources.
*Test:* fixture events from two sources describing the same earnings release collapse to one row; an AMC earnings event gets an after-close timestamp; a congressional record stores disclosure time, not trade date.

**FR2 — Price and options ingestion.** Pull intraday bars around each event window from Alpaca (end time at least 15 minutes old) and live delayed option chains for upcoming events.
*Test:* a request with an end time inside the last 15 minutes is automatically shifted back rather than failing; a missing option chain produces `implied_move = unavailable`, not zero.

**FR3 — Outcome computation.** Compute market-adjusted returns at every Section 7.1 window from the Section 6.2 reference price.
*Test:* hand-computed fixture event with known prices matches the engine's CAR at every window exactly.

**FR4 — Latency tagging.** Apply Section 7.3 to every historical event.
*Test:* synthetic events with known CAR paths (instant jump, slow drift, reversal) receive the expected tags.

**FR5 — Signal engine.** Compute S1–S5 with walk-forward history only.
*Test:* scoring an event from 2023 never reads data from after that event's timestamp (assert on query bounds); a ticker with 5 events returns `pooled` or "insufficient history"; BH correction reduces the count of significant results on a synthetic pure-noise dataset to roughly the expected false discovery rate.

**FR6 — Point-in-time universe.** Resolve S&P 500 membership as of any date.
*Test:* a stock removed from the index in a known year is included for events before removal and excluded after.

**FR7 — Idea ranking.** Score and map ideas per Section 8.
*Test:* fixture signals produce the expected structure mapping; an illiquid option chain lowers the score; a Slow-latency signal is flagged as outside the horizon.

**FR8 — Idea ledger.** Every idea is saved at creation with its timestamp and inputs, then scored automatically after its horizon passes. This builds a live, unedited track record.
*Test:* an idea's stored inputs cannot be modified after creation; outcome scoring runs once the horizon passes and records the realized result.

**FR9 — On-demand query API.** Endpoints: `GET /events/upcoming`, `GET /tickers/{symbol}` (history, latency profile, signals), `GET /ideas`, `GET /ledger`. Fetch on request with cache TTLs: calendars 6 hours, option chains 15 minutes, historical stats recomputed nightly.
*Test:* a second identical request within the TTL is served from cache; a request after expiry triggers a refresh.

**FR10 — Alerts.** Per Section 9.
*Test:* the daily cap blocks further alerts once reached; quiet hours suppress delivery; each trigger type fires on a matching fixture.

---

## 11. Non-Functional Requirements

- **NFR1 Storage budget:** Supabase's free tier is small. Store derived outcomes per event (a few numbers per window), not raw minute bars. Fetch bars transiently during computation and discard them.
- **NFR2 API budgets:** batch and throttle every source to stay inside its free limits; log remaining quota.
- **NFR3 Reproducibility:** any historical signal can be recomputed from stored inputs and produce the same result.
- **NFR4 Auditability:** every alert and idea links back to the exact events and stats that produced it.

---

## 12. Frontend & Design Requirements

Benchmark: Hack the North's approach of building a site around one strong idea rather than generic sections.

- **Ground the design in the subject.** The subject is time and anticipation: a week of known events approaching, each with a measured history. The single bold element should be a **forward event timeline** showing the coming days' catalysts, where each event visibly carries its latency profile and signal strength. Everything else stays quiet.
- **Avoid the trading-app cliché.** A near-black background with a neon green accent and monospace labels is the default look for anything finance-flavored. Do not use it. Also avoid identical rounded cards with the same soft shadow, gradient decoration, ALL-CAPS labels, one highlighted word per headline, and `→` appended to buttons.
- **Deliberate system.** Define 4–6 named hex colors, one or two typefaces chosen for this project with a real type scale, and a layout concept written down before any code is built.
- **Honesty is visible.** Sample size, confidence interval, `pooled` vs. per-ticker, `surprise_basis`, and "research, not advice" appear on the signal itself, not behind a hover.
- **Core pages:** Upcoming (the timeline), Ticker detail (event history, latency distribution, current signals), Ideas (ranked), Ledger (past ideas and outcomes), Alerts settings.
- **Copy:** plain, sentence case, active voice. Buttons say what they do ("Mute alerts," not "Submit").
- **Quality floor:** responsive to mobile, keyboard navigable, visible focus, `prefers-reduced-motion` respected.

---

## 13. Free-Tier Deployment

| Layer | Tool |
|---|---|
| Frontend | Next.js + TypeScript on Vercel (free) |
| Backend API | Python FastAPI on Render free web service (sleeps when idle; acceptable for on-demand queries) |
| Database | PostgreSQL on Supabase free tier (pauses after about a week without activity; the scheduled jobs keep it active) |
| Scheduled jobs | GitHub Actions cron: calendar refresh several times daily, nightly stats recompute, ledger scoring, alert checks |
| Alerts | Telegram Bot API (free) or Gmail API |
| Analysis | pandas, NumPy, SciPy, statsmodels (regressions, BH correction) |

---

## 14. Build Order

1. Event ingestion and timestamp normalization (FR1), earnings first.
2. Price ingestion and outcome computation (FR2, FR3). Verify against hand-computed fixtures before anything else.
3. Point-in-time universe (FR6).
4. Latency tagging (FR4).
5. Earnings signal S1 with guardrails (FR5, Section 7.4). This is the first end-to-end result.
6. Add S4 insider/congressional, then S2 macro, then S3 FDA, then S5 confluence.
7. Idea ranking and ledger (FR7, FR8).
8. Query API with caching (FR9) and alerts (FR10).
9. Frontend and design pass (Section 12) once real data flows.

---

## 15. Open Questions

- **Macro consensus:** is a free consensus source acceptable if found, or is `surprise_basis = prior` fine for v1?
- **FDA universe:** keep FDA limited to S&P 500 (fewer, weaker signals), or extend only the FDA module to a small-cap biotech list (e.g., XBI holdings), where decisions move stocks far more?
- **History depth:** how many years of backtest history? Five years is a reasonable default for sample size vs. API budget.
- **Alert channel:** Telegram, email, or both?

---

## 16. Resume Framing (draft, finalize after results exist)

"Built an event-driven signal engine across 4 catalyst types for S&P 500 stocks, computing walk-forward, market-adjusted reaction statistics with false-discovery-rate control and outcome-latency tagging; scored [N] live hypothetical ideas in an immutable ledger with [X]% directional hit rate."
