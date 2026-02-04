import logging
import ssl
import json
from typing import Any, Tuple

from ..const import (
    CONF_SUCCESS,
    CONF_EMPTY,
    CONF_ERR_UNKNOWN,
    CONF_ERR_CANNOT_CONNECT,
    CONF_ERR_INVALID_AUTH,
)

_LOGGER = logging.getLogger(__name__)

# -------------------------------------------------------------------
# SSL CONTEXT (shared & cached)
# -------------------------------------------------------------------

_SSL_CONTEXT: ssl.SSLContext | None = None


def create_ssl_context() -> ssl.SSLContext:
    """Create relaxed SSL context for EVN legacy endpoints (cached)."""
    global _SSL_CONTEXT
    if _SSL_CONTEXT is None:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.set_ciphers("ALL:@SECLEVEL=1")
        _SSL_CONTEXT = ctx
    return _SSL_CONTEXT


# -------------------------------------------------------------------
# SAFE HELPERS
# -------------------------------------------------------------------

def safe_float(value: Any, default: float = 0.0) -> float:
    """Safely convert EVN numeric values to float."""
    if value is None:
        return default
    try:
        if isinstance(value, str):
            value = (
                value.replace("đ", "")
                .replace(" ", "")
                .replace(".", "")
                .replace(",", ".")
            )
        return float(value)
    except Exception:
        return default


# -------------------------------------------------------------------
# RESPONSE / JSON HANDLING
# -------------------------------------------------------------------

async def json_processing(resp) -> Tuple[str, Any]:
    """Common JSON processing with EVN-specific error handling."""
    if resp.status != 200:
        if resp.status in (400, 401):
            return CONF_ERR_INVALID_AUTH, {}
        return CONF_ERR_CANNOT_CONNECT, {}

    try:
        data = await resp.json(content_type=None)
        return (CONF_SUCCESS, data) if data else (CONF_EMPTY, {})
    except Exception:
        try:
            text = (await resp.text()).strip()
            return (CONF_SUCCESS, json.loads(text, strict=False)) if text else (CONF_EMPTY, {})
        except Exception as err:
            _LOGGER.error("JSON processing error: %s", err)
            return CONF_ERR_UNKNOWN, {}


# -------------------------------------------------------------------
# HTTP FETCH WITH RETRIES
# -------------------------------------------------------------------

async def fetch_with_retries(
    url: str,
    *,
    session,
    method: str = "GET",
    headers=None,
    params=None,
    data=None,
    max_retries: int = 3,
    allow_empty: bool = False,
    api_name: str = "API",
) -> Tuple[str, Any]:
    """Generic fetch with retry & unified return contract."""
    ssl_ctx = create_ssl_context()

    for attempt in range(1, max_retries + 1):
        try:
            if method.upper() == "GET":
                resp = await session.get(
                    url, headers=headers, params=params, ssl=ssl_ctx
                )
            else:
                resp = await session.post(
                    url, headers=headers, data=data, ssl=ssl_ctx
                )

            status, body = await json_processing(resp)

            if status == CONF_SUCCESS:
                return status, body

            if allow_empty and status == CONF_EMPTY:
                return CONF_EMPTY, body

        except Exception as err:
            _LOGGER.warning(
                "%s attempt %s/%s failed: %s",
                api_name,
                attempt,
                max_retries,
                err,
            )

    _LOGGER.error("%s failed after %s attempts", api_name, max_retries)
    return CONF_ERR_UNKNOWN, {}
