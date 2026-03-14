"""
OANDA V20 REST API client wrapper.

This is the ONLY file that imports oandapyV20 directly.
All other modules call through this client.

Key behaviors:
- client.request(ep) calls oandapyV20 and returns ep.response (not None)
- practice/live URL switching is handled by oandapyV20.API(environment=)
- OandaAPIError wraps V20Error so callers don't need to import oandapyV20
"""

import oandapyV20
from oandapyV20 import API
from oandapyV20.exceptions import V20Error
from loguru import logger

from forex_system.config import settings


class OandaAPIError(Exception):
    """Wraps oandapyV20.exceptions.V20Error for clean error handling upstream."""
    pass


class OandaClient:
    """
    Authenticated OANDA V20 REST client.

    Usage:
        from forex_system.data.oanda_client import client
        resp = client.request(some_endpoint_object)
    """

    def __init__(self) -> None:
        self._api = API(
            access_token=settings.oanda_api_token,
            environment=settings.oanda_env,  # "practice" | "live"
        )
        logger.info(
            f"OandaClient initialized | env={settings.oanda_env} | "
            f"account={settings.oanda_account_id}"
        )

    def request(self, endpoint) -> dict:
        """
        Execute an oandapyV20 endpoint object.

        oandapyV20 mutates the endpoint in place — the response lives on
        endpoint.response after the call. This wrapper returns it directly.

        Raises:
            OandaAPIError on any V20Error.
        """
        try:
            self._api.request(endpoint)
            return endpoint.response
        except V20Error as exc:
            logger.error(f"OANDA API error: {exc}")
            raise OandaAPIError(str(exc)) from exc

    @property
    def account_id(self) -> str:
        return settings.oanda_account_id


# Module-level singleton — import and reuse
client = OandaClient()
