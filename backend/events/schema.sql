CREATE TABLE IF NOT EXISTS public.events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_key text NOT NULL UNIQUE,
    event_type text NOT NULL CHECK (event_type IN ('earnings', 'congressional')),
    symbol text NOT NULL,
    event_date date NOT NULL,
    session text NOT NULL CHECK (session IN ('bmo', 'amc', 'dmh', 'unknown')),
    event_timestamp timestamptz,
    exact_timestamp timestamptz,
    timestamp_basis text NOT NULL CHECK (timestamp_basis IN ('exact', 'session_anchor', 'unavailable')),
    timing_reason text,
    timing_conflict boolean NOT NULL DEFAULT false,
    intraday_eligible boolean NOT NULL,
    first_observed_at timestamptz NOT NULL,
    last_observed_at timestamptz NOT NULL,
    CHECK (
        (timestamp_basis = 'exact' AND exact_timestamp IS NOT NULL AND event_timestamp IS NOT NULL
            AND event_timestamp = exact_timestamp AND intraday_eligible)
        OR (timestamp_basis = 'session_anchor' AND exact_timestamp IS NULL
            AND event_timestamp IS NOT NULL AND NOT intraday_eligible)
        OR (timestamp_basis = 'unavailable' AND exact_timestamp IS NULL
            AND event_timestamp IS NULL AND NOT intraday_eligible)
    )
);

CREATE INDEX IF NOT EXISTS events_by_date ON public.events (event_date, symbol);

CREATE TABLE IF NOT EXISTS public.event_observations (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id bigint NOT NULL REFERENCES public.events(id),
    source text NOT NULL,
    source_record_id text,
    observed_at timestamptz NOT NULL,
    session text NOT NULL CHECK (session IN ('bmo', 'amc', 'dmh', 'unknown')),
    event_timestamp timestamptz,
    exact_timestamp timestamptz,
    timestamp_basis text NOT NULL,
    timing_reason text,
    inputs jsonb NOT NULL,
    fingerprint text NOT NULL,
    UNIQUE (event_id, source, observed_at, fingerprint)
);

CREATE INDEX IF NOT EXISTS observations_as_of ON public.event_observations (event_id, observed_at);

CREATE TABLE IF NOT EXISTS public.source_requests (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source text NOT NULL,
    requested_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS requests_by_source ON public.source_requests (source, requested_at);

CREATE TABLE IF NOT EXISTS public.source_refreshes (
    source text NOT NULL,
    scope text NOT NULL,
    completed_at timestamptz NOT NULL,
    PRIMARY KEY (source, scope)
);

-- Backend-only storage: owner access through PostgreSQL, no public Data API policy.
ALTER TABLE public.events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.event_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.source_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.source_refreshes ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.events, public.event_observations, public.source_requests,
    public.source_refreshes FROM PUBLIC;
