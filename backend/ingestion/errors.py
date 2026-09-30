class SourceUnavailable(RuntimeError):
    """Source failed or returned data inconsistent with its documented contract."""


class SourceLimitError(SourceUnavailable):
    """Source signaled an entitlement or rate limit; do not switch to paid data."""


class BudgetExhausted(RuntimeError):
    """A request cannot fit within the persisted free-tier budget."""
