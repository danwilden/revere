"""
High-level OANDA execution interface.

OandaBroker wraps raw oandapyV20 endpoint calls into clean methods.
All trading methods log the action and environment before execution.

Usage:
    from forex_system.execution.broker import OandaBroker

    broker = OandaBroker()
    summary = broker.get_account_summary()
    positions = broker.get_open_positions()
    fill = broker.place_order(OrderRequest.market_long("EUR_USD", 10_000, stop_loss_price=1.07))
"""

import oandapyV20.endpoints.accounts as accounts_ep
import oandapyV20.endpoints.orders as orders_ep
import oandapyV20.endpoints.positions as positions_ep
import oandapyV20.endpoints.trades as trades_ep
from loguru import logger

from forex_system.config import settings
from forex_system.data.oanda_client import client
from forex_system.execution.orders import OrderRequest


class OandaBroker:
    """
    Stateless execution interface. Each method is a self-contained API call.
    """

    # ── Account ──────────────────────────────────────────────────────────────

    def get_account_summary(self) -> dict:
        """
        Fetch account summary including NAV, balance, margin.

        Returns dict with keys:
            balance, nav, unrealized_pnl, margin_used, margin_available,
            open_trade_count, open_position_count, currency
        """
        req = accounts_ep.AccountSummary(accountID=client.account_id)
        resp = client.request(req)
        acct = resp["account"]
        return {
            "balance": float(acct["balance"]),
            "nav": float(acct["NAV"]),
            "unrealized_pnl": float(acct["unrealizedPL"]),
            "margin_used": float(acct["marginUsed"]),
            "margin_available": float(acct["marginAvailable"]),
            "open_trade_count": int(acct["openTradeCount"]),
            "open_position_count": int(acct["openPositionCount"]),
            "currency": acct.get("currency", settings.base_ccy),
        }

    # ── Positions ─────────────────────────────────────────────────────────────

    def get_open_positions(self) -> list[dict]:
        """
        Fetch all open positions.

        Returns list of dicts with keys:
            instrument, long_units, short_units, net_units,
            unrealized_pnl, avg_price
        """
        req = positions_ep.OpenPositions(accountID=client.account_id)
        resp = client.request(req)
        result = []
        for pos in resp.get("positions", []):
            long_units = int(pos["long"]["units"])
            short_units = int(pos["short"]["units"])
            # avg price from whichever side is open
            if long_units > 0:
                avg_price = float(pos["long"].get("averagePrice", 0.0))
            elif short_units < 0:
                avg_price = float(pos["short"].get("averagePrice", 0.0))
            else:
                avg_price = 0.0
            result.append(
                {
                    "instrument": pos["instrument"],
                    "long_units": long_units,
                    "short_units": short_units,
                    "net_units": long_units + short_units,
                    "unrealized_pnl": float(pos["unrealizedPL"]),
                    "avg_price": avg_price,
                }
            )
        return result

    def close_position(self, instrument: str, side: str = "ALL") -> dict:
        """
        Close a position.

        Args:
            instrument: e.g. "EUR_USD"
            side: "ALL" (default), "LONG", or "SHORT"

        Returns:
            OANDA response dict.
        """
        if side == "LONG":
            data = {"longUnits": "ALL", "shortUnits": "NONE"}
        elif side == "SHORT":
            data = {"longUnits": "NONE", "shortUnits": "ALL"}
        else:
            data = {"longUnits": "ALL", "shortUnits": "ALL"}

        req = positions_ep.PositionClose(
            accountID=client.account_id,
            instrument=instrument,
            data=data,
        )
        resp = client.request(req)
        logger.info(
            f"Position closed: {instrument} side={side} | env={settings.oanda_env}"
        )
        return resp

    # ── Trades ────────────────────────────────────────────────────────────────

    def get_open_trades(self) -> list[dict]:
        """
        Fetch all currently open trades (individual trade legs).
        Useful for attaching or modifying stop orders.
        """
        req = trades_ep.OpenTrades(accountID=client.account_id)
        resp = client.request(req)
        return resp.get("trades", [])

    # ── Orders ────────────────────────────────────────────────────────────────

    def place_order(self, order: OrderRequest) -> dict:
        """
        Submit an order to OANDA.

        Logs instrument, units, type, and environment before execution.
        Returns the fill response dict from OANDA.

        IMPORTANT: Will execute against practice or live API depending on
        OANDA_ENV setting. Always verify the environment before calling.
        """
        logger.info(
            f"Placing order: {order.instrument} {order.units:+d} units "
            f"({order.order_type}) | env={settings.oanda_env}"
        )

        body = order.to_oanda_body()
        req = orders_ep.OrderCreate(
            accountID=client.account_id,
            data=body,
        )
        resp = client.request(req)

        # Log fill details if available
        fill = resp.get("orderFillTransaction", {})
        if fill:
            logger.info(
                f"Fill: {fill.get('instrument')} @ {fill.get('price')} | "
                f"units={fill.get('units')} | pl={fill.get('pl', 'N/A')}"
            )

        return resp
