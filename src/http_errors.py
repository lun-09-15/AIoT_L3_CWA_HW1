"""Helpers for exposing useful HTTP diagnostics without leaking credentials."""
import re
from urllib.parse import quote, quote_plus


def safe_transport_detail(exc: Exception, secret: str = "", limit: int = 220) -> str:
    """Return a short exception detail with request query strings and keys redacted."""
    detail = f"{type(exc).__name__}: {exc}"
    if secret:
        for value in {secret, quote(secret, safe=""), quote_plus(secret)}:
            if value:
                detail = detail.replace(value, "[redacted]")

    # requests/urllib3 errors may include the full request URL, whose query can
    # contain Authorization or api_key. Keep the host/path but discard all query data.
    detail = re.sub(
        r"(?i)(https?://[^?\s'\"<>]+)\?[^\s'\"<>]*",
        r"\1?[query redacted]",
        detail,
    )
    detail = re.sub(
        r"(?i)(\burl:\s*[^?\s'\"<>]+)\?[^\s)'\"<>]*",
        r"\1?[query redacted]",
        detail,
    )
    detail = re.sub(
        r"(?i)((?:authorization|api_key|token|key)=)[^&\s,'\"<>)]*",
        r"\1[redacted]",
        detail,
    )
    return detail[:limit]
