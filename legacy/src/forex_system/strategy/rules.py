"""
Rule-based trading strategies.

Each strategy class exposes a single method:
    generate(df: pd.DataFrame) -> pd.DataFrame

Input:  OHLCV DataFrame with DatetimeIndex
Output: Same DataFrame with added columns:
    signal        (int):   1=long, -1=short, 0=flat
    stop_distance (float): distance from entry to stop in price units
    entry_price   (float): close price (entry executes at next bar's open)

Signal at bar t → execution at bar t+1 open (no look-ahead).
"""

import pandas as pd
import ta

from forex_system.risk.stops import dynamic_stop


class TrendFollowStrategy:
    """
    Dual EMA crossover with ADX trend-strength filter.

    Long  when fast EMA > slow EMA AND ADX > threshold.
    Short when fast EMA < slow EMA AND ADX > threshold.
    Flat  when ADX < threshold (non-trending market).

    Params:
        fast_ema (12), slow_ema (26): crossover periods
        adx_window (14), adx_threshold (20.0): filter
        atr_window (14), atr_multiplier (2.0): stop distance
    """

    def __init__(
        self,
        fast_ema: int = 12,
        slow_ema: int = 26,
        adx_window: int = 14,
        adx_threshold: float = 20.0,
        atr_window: int = 14,
        atr_multiplier: float = 2.0,
    ) -> None:
        self.fast_ema = fast_ema
        self.slow_ema = slow_ema
        self.adx_window = adx_window
        self.adx_threshold = adx_threshold
        self.atr_window = atr_window
        self.atr_multiplier = atr_multiplier

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        close = df["close"]
        high = df["high"]
        low = df["low"]

        fast = close.ewm(span=self.fast_ema, adjust=False).mean()
        slow = close.ewm(span=self.slow_ema, adjust=False).mean()
        adx_val = ta.trend.ADXIndicator(
            high, low, close, window=self.adx_window
        ).adx()
        trending = adx_val > self.adx_threshold

        signal = pd.Series(0, index=df.index, name="signal")
        signal[(fast > slow) & trending] = 1
        signal[(fast < slow) & trending] = -1

        stop_dist = dynamic_stop(
            high, low, close, self.atr_window, self.atr_multiplier
        )

        out = df.copy()
        out["signal"] = signal
        out["stop_distance"] = stop_dist
        out["entry_price"] = close
        return out


class MeanReversionStrategy:
    """
    RSI extremes with Bollinger Band boundary confirmation.

    Long  when RSI < oversold  AND close ≤ lower BB.
    Short when RSI > overbought AND close ≥ upper BB.
    Flat  otherwise.

    Params:
        rsi_window (14), rsi_oversold (30), rsi_overbought (70)
        bb_window (20), bb_std (2.0)
        atr_window (14), atr_multiplier (1.5): tighter stop for mean-reversion
    """

    def __init__(
        self,
        rsi_window: int = 14,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
        bb_window: int = 20,
        bb_std: float = 2.0,
        atr_window: int = 14,
        atr_multiplier: float = 1.5,
    ) -> None:
        self.rsi_window = rsi_window
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.bb_window = bb_window
        self.bb_std = bb_std
        self.atr_window = atr_window
        self.atr_multiplier = atr_multiplier

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        close = df["close"]
        high = df["high"]
        low = df["low"]

        rsi_val = ta.momentum.RSIIndicator(close, window=self.rsi_window).rsi()
        bb = ta.volatility.BollingerBands(
            close, window=self.bb_window, window_dev=self.bb_std
        )
        bb_lower = bb.bollinger_lband()
        bb_upper = bb.bollinger_hband()

        signal = pd.Series(0, index=df.index, name="signal")
        signal[(rsi_val < self.rsi_oversold) & (close <= bb_lower)] = 1
        signal[(rsi_val > self.rsi_overbought) & (close >= bb_upper)] = -1

        stop_dist = dynamic_stop(
            high, low, close, self.atr_window, self.atr_multiplier
        )

        out = df.copy()
        out["signal"] = signal
        out["stop_distance"] = stop_dist
        out["entry_price"] = close
        return out


class BreakoutStrategy:
    """
    Donchian Channel breakout with ATR expansion confirmation.

    Long  when close breaks above N-period high AND ATR is expanding.
    Short when close breaks below N-period low  AND ATR is expanding.
    Flat  otherwise.

    The shift(1) on Donchian levels prevents look-ahead: we use the
    high/low of the PREVIOUS N bars, not including the current bar.

    Params:
        channel_window (20): Donchian lookback
        atr_window (14), atr_multiplier (2.0): stop distance
        atr_expansion_factor (1.1): ATR must exceed this × rolling ATR mean
    """

    def __init__(
        self,
        channel_window: int = 20,
        atr_window: int = 14,
        atr_multiplier: float = 2.0,
        atr_expansion_factor: float = 1.1,
    ) -> None:
        self.channel_window = channel_window
        self.atr_window = atr_window
        self.atr_multiplier = atr_multiplier
        self.atr_expansion_factor = atr_expansion_factor

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        close = df["close"]
        high = df["high"]
        low = df["low"]

        # Shift by 1 to avoid look-ahead on the channel levels
        upper = high.rolling(self.channel_window).max().shift(1)
        lower = low.rolling(self.channel_window).min().shift(1)

        raw_atr = ta.volatility.AverageTrueRange(
            high, low, close, window=self.atr_window
        ).average_true_range()
        atr_mean = raw_atr.rolling(self.atr_window).mean()
        atr_expanding = raw_atr > atr_mean * self.atr_expansion_factor

        signal = pd.Series(0, index=df.index, name="signal")
        signal[(close > upper) & atr_expanding] = 1
        signal[(close < lower) & atr_expanding] = -1

        stop_dist = dynamic_stop(
            high, low, close, self.atr_window, self.atr_multiplier
        )

        out = df.copy()
        out["signal"] = signal
        out["stop_distance"] = stop_dist
        out["entry_price"] = close
        return out
