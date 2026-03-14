"""
Order data model and OANDA request body builder.

OrderRequest is a typed container for all order parameters.
.to_oanda_body() converts it to the dict format expected by oandapyV20.

OANDA V20 requirements:
    - units must be a STRING: "1000" not 1000
    - negative units = sell/short: "-1000"
    - stop loss price must be a string with 5 decimal places for most pairs
    - clientExtensions.id max 32 chars, alphanumeric + dash/underscore only
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

OrderType = Literal["MARKET", "LIMIT", "STOP"]
TimeInForce = Literal["FOK", "IOC", "GFD", "GTC", "GTD"]


@dataclass
class OrderRequest:
    """
    Represents a single order to be submitted to OANDA.

    units: positive = buy (long), negative = sell (short)
    price: required for LIMIT and STOP orders; ignored for MARKET
    """

    instrument: str
    units: int                                # positive=buy, negative=sell
    order_type: OrderType = "MARKET"
    price: float | None = None               # LIMIT/STOP orders only
    stop_loss_price: float | None = None     # attached stop loss on fill
    take_profit_price: float | None = None   # attached take profit on fill
    time_in_force: TimeInForce = "FOK"       # FOK for MARKET, GTC for LIMIT/STOP
    client_order_id: str | None = None       # for audit trail (max 32 chars)

    def to_oanda_body(self) -> dict:
        """
        Build the OANDA V20 order body dict.

        OANDA requires:
            - units as a string (positive or negative)
            - prices as 5-decimal strings
        """
        order: dict = {
            "type": self.order_type,
            "instrument": self.instrument,
            "units": str(self.units),         # MUST be string
            "timeInForce": self.time_in_force,
            "positionFill": "DEFAULT",
        }

        if self.order_type in ("LIMIT", "STOP") and self.price is not None:
            order["price"] = f"{self.price:.5f}"

        body: dict = {"order": order}

        if self.stop_loss_price is not None:
            body["order"]["stopLossOnFill"] = {
                "price": f"{self.stop_loss_price:.5f}",
                "timeInForce": "GTC",
            }

        if self.take_profit_price is not None:
            body["order"]["takeProfitOnFill"] = {
                "price": f"{self.take_profit_price:.5f}",
                "timeInForce": "GTC",
            }

        if self.client_order_id:
            # Sanitize: keep only safe characters, max 32 chars
            safe_id = self.client_order_id[:32].replace(" ", "_")
            body["order"]["clientExtensions"] = {
                "id": safe_id,
                "comment": f"forex_system_{self.instrument}",
            }

        return body

    @classmethod
    def market_long(
        cls,
        instrument: str,
        units: int,
        stop_loss_price: float | None = None,
        client_id: str | None = None,
    ) -> "OrderRequest":
        """Convenience constructor for a long market order."""
        return cls(
            instrument=instrument,
            units=abs(units),
            order_type="MARKET",
            stop_loss_price=stop_loss_price,
            client_order_id=client_id,
        )

    @classmethod
    def market_short(
        cls,
        instrument: str,
        units: int,
        stop_loss_price: float | None = None,
        client_id: str | None = None,
    ) -> "OrderRequest":
        """Convenience constructor for a short market order."""
        return cls(
            instrument=instrument,
            units=-abs(units),
            order_type="MARKET",
            stop_loss_price=stop_loss_price,
            client_order_id=client_id,
        )
