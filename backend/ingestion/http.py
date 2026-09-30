import logging
import re
from datetime import timedelta
from typing import Protocol

import httpx

from backend.ingestion.errors import SourceLimitError, SourceUnavailable

logger = logging.getLogger(__name__)


class VendorUrlRedaction(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = re.sub(r"(apikey|token)=([^&\s]+)", r"\1=<redacted>", record.getMessage())
        record.args = ()
        return True


# httpx logs request URLs at INFO; Alpha Vantage requires a key in its query string.
logging.getLogger("httpx").addFilter(VendorUrlRedaction())


class RequestBudget(Protocol):
    def reserve_request(self, source: str, limit: int, window: timedelta) -> int: ...


def request_json(
    client: httpx.Client,
    budget: RequestBudget,
    source: str,
    url: str,
    limit: int,
    window: timedelta,
    params: dict,
    headers: dict | None = None,
) -> dict:
    remaining = budget.reserve_request(source, limit, window)
    logger.info("source=%s local_quota_remaining=%d", source, remaining)
    try:
        response = client.get(url, params=params, headers=headers)
    except httpx.HTTPError:
        # Vendor exception strings can contain the API key embedded in the URL.
        raise SourceUnavailable(f"{source}: HTTP transport failed") from None
    actual_remaining = response.headers.get("x-ratelimit-remaining")
    if actual_remaining is not None and actual_remaining.isdigit():
        logger.info("source=%s reported_quota_remaining=%s", source, actual_remaining)
    if response.status_code in {401, 402, 403, 429}:
        raise SourceLimitError(f"{source}: access or rate limit (HTTP {response.status_code})")
    if response.is_error:
        raise SourceUnavailable(f"{source}: HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError:
        raise SourceUnavailable(f"{source}: invalid JSON response") from None
    if not isinstance(payload, dict):
        raise SourceUnavailable(f"{source}: expected a JSON object")
    for error_field in ("error", "Error Message", "Note", "Information"):
        if error_field in payload:
            raise SourceLimitError(f"{source}: source returned an error or entitlement message")
    return payload
