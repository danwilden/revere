"""Tests for backend/data/aggregate.py"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.data.aggregate import aggregate_bars, _floor_h1, _floor_h4, _floor_d


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bar(ts: str, o=1.1, h=1.2, l=1.0, c=1.15, vol=100.0, instrument="EUR_USD"):
    dt = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
    return {
        "instrument_id": instrument,
        "timestamp_utc": dt,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": vol,
        "source": "oanda",
        "quality_flag": "ok",
    }


# ---------------------------------------------------------------------------
# Floor functions
# ---------------------------------------------------------------------------

class TestFloorFunctions:
    def test_floor_h1(self):
        dt = datetime(2024, 1, 1, 13, 45, 30, tzinfo=timezone.utc)
        result = _floor_h1(dt)
        assert result == datetime(2024, 1, 1, 13, 0, 0, tzinfo=timezone.utc)

    def test_floor_h4_midnight(self):
        dt = datetime(2024, 1, 1, 0, 30, 0, tzinfo=timezone.utc)
        assert _floor_h4(dt) == datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    def test_floor_h4_block4(self):
        dt = datetime(2024, 1, 1, 5, 59, 59, tzinfo=timezone.utc)
        assert _floor_h4(dt) == datetime(2024, 1, 1, 4, 0, 0, tzinfo=timezone.utc)

    def test_floor_h4_block8(self):
        dt = datetime(2024, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        assert _floor_h4(dt) == datetime(2024, 1, 1, 8, 0, 0, tzinfo=timezone.utc)

    def test_floor_h4_block20(self):
        dt = datetime(2024, 1, 1, 23, 0, 0, tzinfo=timezone.utc)
        assert _floor_h4(dt) == datetime(2024, 1, 1, 20, 0, 0, tzinfo=timezone.utc)

    def test_floor_d(self):
        dt = datetime(2024, 1, 15, 23, 59, 59, tzinfo=timezone.utc)
        assert _floor_d(dt) == datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# H1 aggregation
# ---------------------------------------------------------------------------

class TestH1Aggregation:
    def test_one_hour_two_bars(self):
        bars = [
            _bar("2024-01-01T00:00:00", o=1.1, h=1.15, l=1.05, c=1.12, vol=50),
            _bar("2024-01-01T00:30:00", o=1.12, h=1.20, l=1.10, c=1.18, vol=80),
        ]
        result = aggregate_bars(bars, "H1")
        assert len(result) == 1
        agg = result[0]
        assert agg["open"] == pytest.approx(1.1)       # first open
        assert agg["high"] == pytest.approx(1.20)      # max high
        assert agg["low"] == pytest.approx(1.05)       # min low
        assert agg["close"] == pytest.approx(1.18)     # last close
        assert agg["volume"] == pytest.approx(130.0)   # sum volumes
        assert agg["timeframe"] == "H1"

    def test_two_separate_hours(self):
        bars = [
            _bar("2024-01-01T00:00:00"),
            _bar("2024-01-01T00:30:00"),
            _bar("2024-01-01T01:00:00"),
            _bar("2024-01-01T01:30:00"),
        ]
        result = aggregate_bars(bars, "H1")
        assert len(result) == 2
        assert result[0]["timestamp_utc"].hour == 0
        assert result[1]["timestamp_utc"].hour == 1

    def test_single_bar_in_window(self):
        bars = [_bar("2024-01-01T00:00:00", o=1.1, h=1.2, l=1.0, c=1.15)]
        result = aggregate_bars(bars, "H1")
        assert len(result) == 1
        assert result[0]["open"] == pytest.approx(1.1)
        assert result[0]["close"] == pytest.approx(1.15)

    def test_timestamp_floored_to_hour(self):
        bars = [
            _bar("2024-01-01T00:45:00"),
            _bar("2024-01-01T00:55:00"),
        ]
        result = aggregate_bars(bars, "H1")
        ts = result[0]["timestamp_utc"]
        assert ts.minute == 0
        assert ts.second == 0


# ---------------------------------------------------------------------------
# H4 aggregation
# ---------------------------------------------------------------------------

class TestH4Aggregation:
    def test_four_hours_grouped_correctly(self):
        bars = []
        # 4 bars in 00-04 window, 4 bars in 04-08 window
        for hour in [0, 1, 2, 3]:
            bars.append(_bar(f"2024-01-01T0{hour}:00:00", o=1.1, h=1.1+hour*0.01, l=1.0, c=1.1))
        for hour in [4, 5, 6, 7]:
            bars.append(_bar(f"2024-01-01T0{hour}:00:00", o=1.2, h=1.2+hour*0.01, l=1.1, c=1.2))

        result = aggregate_bars(bars, "H4")
        assert len(result) == 2
        assert result[0]["timestamp_utc"].hour == 0
        assert result[1]["timestamp_utc"].hour == 4

    def test_h4_high_is_max_of_all_bars(self):
        bars = [
            _bar("2024-01-01T00:00:00", h=1.10),
            _bar("2024-01-01T01:00:00", h=1.25),
            _bar("2024-01-01T02:00:00", h=1.15),
            _bar("2024-01-01T03:00:00", h=1.20),
        ]
        result = aggregate_bars(bars, "H4")
        assert result[0]["high"] == pytest.approx(1.25)

    def test_h4_low_is_min_of_all_bars(self):
        bars = [
            _bar("2024-01-01T00:00:00", l=1.05),
            _bar("2024-01-01T01:00:00", l=1.02),
            _bar("2024-01-01T02:00:00", l=1.08),
        ]
        result = aggregate_bars(bars, "H4")
        assert result[0]["low"] == pytest.approx(1.02)


# ---------------------------------------------------------------------------
# D aggregation
# ---------------------------------------------------------------------------

class TestDailyAggregation:
    def test_one_day_many_bars(self):
        bars = [
            _bar(f"2024-01-01T{h:02d}:00:00", o=1.1, h=1.1+h*0.001, l=1.0, c=1.1, vol=10)
            for h in range(24)
        ]
        result = aggregate_bars(bars, "D")
        assert len(result) == 1
        assert result[0]["timestamp_utc"].day == 1
        assert result[0]["timestamp_utc"].hour == 0
        assert result[0]["volume"] == pytest.approx(240.0)  # 24 * 10

    def test_two_days_two_bars(self):
        bars = [
            _bar("2024-01-01T12:00:00"),
            _bar("2024-01-02T12:00:00"),
        ]
        result = aggregate_bars(bars, "D")
        assert len(result) == 2
        assert result[0]["timestamp_utc"].day == 1
        assert result[1]["timestamp_utc"].day == 2

    def test_daily_open_is_first_m1_open(self):
        bars = [
            _bar("2024-01-01T00:00:00", o=1.10),
            _bar("2024-01-01T01:00:00", o=1.15),
            _bar("2024-01-01T02:00:00", o=1.20),
        ]
        result = aggregate_bars(bars, "D")
        assert result[0]["open"] == pytest.approx(1.10)

    def test_daily_close_is_last_m1_close(self):
        bars = [
            _bar("2024-01-01T00:00:00", c=1.10),
            _bar("2024-01-01T01:00:00", c=1.15),
            _bar("2024-01-01T02:00:00", c=1.20),
        ]
        result = aggregate_bars(bars, "D")
        assert result[0]["close"] == pytest.approx(1.20)


# ---------------------------------------------------------------------------
# Edge cases and error handling
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_input_returns_empty(self):
        assert aggregate_bars([], "H1") == []

    def test_invalid_timeframe_raises(self):
        bars = [_bar("2024-01-01T00:00:00")]
        with pytest.raises(ValueError, match="Unsupported timeframe"):
            aggregate_bars(bars, "W1")

    def test_metadata_propagated(self):
        bars = [_bar("2024-01-01T00:00:00")]
        result = aggregate_bars(bars, "H1", source="oanda", derivation_version="2")
        assert result[0]["source"] == "oanda"
        assert result[0]["derivation_version"] == "2"
        assert result[0]["instrument_id"] == "EUR_USD"

    def test_result_sorted_by_timestamp(self):
        # Even if input is unsorted, output should be sorted
        bars = [
            _bar("2024-01-01T02:00:00"),
            _bar("2024-01-01T00:00:00"),
            _bar("2024-01-01T01:00:00"),
        ]
        result = aggregate_bars(bars, "H1")
        hours = [b["timestamp_utc"].hour for b in result]
        assert hours == sorted(hours)

    def test_string_timestamps_accepted(self):
        """Connector may pass ISO string timestamps instead of datetime."""
        bars = [
            {
                "instrument_id": "EUR_USD",
                "timestamp_utc": "2024-01-01T00:00:00",
                "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15,
                "volume": 100.0, "source": "oanda",
            }
        ]
        result = aggregate_bars(bars, "H1")
        assert len(result) == 1
