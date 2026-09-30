# Phase 1 feasibility review

Checked on September 30, 2026. The user approved Alpha Vantage as the historical
source and retained the five-year target. This note records the source evidence,
approved timing clarification, and limits of verification.

## Finnhub historical coverage

The earnings calendar section in [Finnhub's API documentation](https://finnhub.io/docs/api/quote)
lists the free tier as “1 month of historical earnings and new updates.” That
is insufficient for a five-year historical calendar. Finnhub remains the upcoming
calendar source, with only its available recent month requested. The documentation
was available through the search index; direct requests redirected repeatedly
rather than returning the API page. No authenticated Finnhub coverage check has run.

## Approved historical source

[Alpha Vantage documents](https://www.alphavantage.co/documentation/) an `EARNINGS`
endpoint with quarterly history. Its [support page](https://www.alphavantage.co/support/)
states a standard free limit of 25 requests per day. The public IBM demo response
was inspected and returned 122 quarterly entries with `reportedDate` and
`reportTime` fields, including `pre-market` and `post-market` labels. This is
evidence for the adapter contract, not verification of coverage across the
S&P 500. No personal API key was supplied or used; a full backfill has not run.

The user approved this source addition rather than reducing history to one month.
The implementation filters reported dates to the five-year target and conservatively
enforces 25 requests per rolling 24 hours using persistent database reservations.
An initial 500-symbol backfill takes at least 20 days. Availability is not guaranteed
for every symbol or event. Missing session labels remain unknown. Revised EPS
inputs fetched now cannot be claimed as point-in-time inputs for historical scoring.

[FMP's FAQ](https://site.financialmodelingprep.com/faqs) identifies the earnings
calendar as a paid endpoint, and [StockAnalysis](https://stockanalysis.com/help/faq/api-access/)
does not offer programmatic access. SEC filings are free, but their filing
acceptance timestamps are not interchangeable with earnings release times, so
they were not substituted for the earnings calendar.

## Session codes are not observed release times

The same documentation describes `hour` as `bmo`, `amc`, or `dmh`; the earnings
schema does not supply an exact public release timestamp. The official
[Finnhub SDK model](https://github.com/Finnhub-Stock-API/finnhub-go/blob/master/docs/EarningRelease.md)
also records session timing as a string.

Sections 6.1 and 6.2 require an actual public timestamp and a pre-event reference
price. A synthetic BMO or AMC timestamp cannot establish the last tradable price
before a real release. A proposed session anchor must therefore be explicitly
approximate, separate from an observed timestamp, and excluded from intraday
windows. The user approved BMO at 09:29:59 Eastern and AMC one second after
the actual NYSE close (including early closes), with exact release time
unavailable and both excluded from intraday windows. Anchors also cannot establish
the Section 6.2 reference price. A later outcome phase must preserve that rule
or ask for an explicit methodological change; this phase computes no returns.

Session conflicts keep both observations and mark canonical timing unavailable.
Unknown sessions and non-trading dates get no anchor; non-trading releases are
not shifted to another day. Deduplication is exact on normalized ticker and
reported release date, without fuzzy date matching or share-class alias guessing.

## PR prerequisites

The local repository started without commits or a Git remote. The user selected
`https://github.com/JSakMad/Tickipate`, which was also empty. A requirements-only
baseline provides the base for the Phase 1 PR. No Phase 2 work is included.

## Acceptance status

The automated PostgreSQL suite covers all three FR1 Test clauses: the two adapters'
fixture observations collapse to one Event row; AMC persists an after-close anchor
with unavailable exact timing; and the congressional timing contract persists
disclosure time while retaining trade date only as metadata. Congressional source
ingestion remains in Phase 6. Additional tests cover BMO, DST, early closes,
non-trading dates, timing conflicts, snapshot revisions, historical knowledge
bounds, HTTP source errors, cache TTL, and quota concurrency. No FR1 tests are skipped.
