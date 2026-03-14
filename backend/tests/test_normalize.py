"""Tests for backend/data/normalize.py"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.data.normalize import normalize_bars, _ohlc_valid


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bar(ts: str, o=1.1, h=1.2, l=1.0, c=1.1, instrument="EUR_USD", source="oanda", quality_flag=None):
    dt = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
    b = {
        "instrument_id": instrument,
        "timestamp_utc": dt,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": 100.0,
        "source": source,
    }
    if quality_flag is not None:
        b["quality_flag"] = quality_flag
    return b


# ---------------------------------------------------------------------------
# OHLC validation
# ---------------------------------------------------------------------------

class TestOHLCValid:
    def test_valid_bar(self):
        assert _ohlc_valid(_bar("2024-01-01T00:00:00", o=1.1, h=1.2, l=1.0, c=1.15))

    def test_high_equals_open(self):
        assert _ohlc_valid(_bar("2024-01-01T00:00:00", o=1.2, h=1.2, l=1.0, c=1.1))

    def test_low_equals_close(self):
        assert _ohlc_valid(_bar("2024-01-01T00:00:00", o=1.1, h=1.2, l=1.05, c=1.05))

    def test_high_below_open_invalid(self):
        assert not _ohlc_valid(_bar("2024-01-01T00:00:00", o=1.3, h=1.2, l=1.0, c=1.1))

    def test_high_below_close_invalid(self):
        assert not _ohlc_valid(_bar("2024-01-01T00:00:00", o=1.1, h=1.0, l=0.9, c=1.15))

    def test_low_above_open_invalid(self):
        assert not _ohlc_valid(_bar("2024-01-01T00:00:00", o=1.0, h=1.2, l=1.1, c=1.15))

    def test_low_above_close_invalid(self):
        assert not _ohlc_valid(_bar("2024-01-01T00:00:00", o=1.1, h=1.2, l=1.12, c=1.09))

    def test_high_equals_low_valid(self):
        # Doji bar — all prices equal
        assert _ohlc_valid(_bar("2024-01-01T00:00:00", o=1.1, h=1.1, l=1.1, c=1.1))


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

class TestDeduplication:
    def test_no_duplicates_passthrough(self):
        bars = [
            _bar("2024-01-01T00:00:00"),
            _bar("2024-01-01T00:01:00"),
            _bar("2024-01-01T00:02:00"),
        ]
        result = normalize_bars(bars)
        assert len(result) == 3

    def test_duplicate_removed(self):
        bars = [
            _bar("2024-01-01T00:00:00", c=1.1),
            _bar("2024-01-01T00:01:00"),
            _bar("2024-01-01T00:00:00", c=1.2),  # duplicate ts — keep last
        ]
        result = normalize_bars(bars)
        # Dedup keeps last occurrence per key
        assert len(result) == 2
        # The kept bar for 00:00 is the last one (c=1.2)
        bar_00 = next(b for b in result if b["timestamp_utc"].minute == 0)
        assert bar_00["close"] == pytest.approx(1.2)

    def test_duplicate_quality_flag_set(self):
        bars = [
            _bar("2024-01-01T00:00:00"),
            _bar("2024-01-01T00:00:00"),
        ]
        result = normalize_bars(bars)
        assert len(result) == 1
        assert result[0]["quality_flag"] == "duplicate"

    def test_multiple_duplicates_flagged(self):
        bars = [
            _bar("2024-01-01T00:00:00"),
            _bar("2024-01-01T00:00:00"),
            _bar("2024-01-01T00:01:00"),
            _bar("2024-01-01T00:01:00"),
        ]
        result = normalize_bars(bars)
        assert len(result) == 2
        assert all(b["quality_flag"] == "duplicate" for b in result)


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------

class TestSorting:
    def test_unsorted_input_sorted_output(self):
        bars = [
            _bar("2024-01-01T00:02:00"),
            _bar("2024-01-01T00:00:00"),
            _bar("2024-01-01T00:01:00"),
        ]
        result = normalize_bars(bars)
        ts_seq = [b["timestamp_utc"].minute for b in result]
        assert ts_seq == [0, 1, 2]

    def test_empty_input(self):
        assert normalize_bars([]) == []

    def test_single_bar(self):
        bars = [_bar("2024-01-01T00:00:00")]
        result = normalize_bars(bars)
        assert len(result) == 1
        assert result[0]["quality_flag"] == "ok"


# ---------------------------------------------------------------------------
# Quality flags
# ---------------------------------------------------------------------------

class TestQualityFlags:
    def test_valid_bar_gets_ok_flag(self):
        bars = [_bar("2024-01-01T00:00:00", o=1.1, h=1.2, l=1.0, c=1.15)]
        result = normalize_bars(bars)
        assert result[0]["quality_flag"] == "ok"

    def test_invalid_ohlc_gets_flag(self):
        bars = [_bar("2024-01-01T00:00:00", o=1.3, h=1.2, l=1.0, c=1.15)]
        result = normalize_bars(bars)
        assert result[0]["quality_flag"] == "ohlc_invalid"

    def test_duplicate_flag_takes_precedence(self):
        # Even if OHLC would be invalid, duplicate flag is set
        bars = [
            _bar("2024-01-01T00:00:00", o=1.3, h=1.2, l=1.0, c=1.15),
            _bar("2024-01-01T00:00:00", o=1.3, h=1.2, l=1.0, c=1.15),
        ]
        result = normalize_bars(bars)
        assert result[0]["quality_flag"] == "duplicate"

    def test_existing_quality_flag_preserved_for_ok_bar(self):
        """If source already set a flag, we only override for detected issues."""
        bars = [_bar("2024-01-01T00:00:00", quality_flag="estimated")]
        result = normalize_bars(bars)
        # "estimated" is not overridden since the bar passes OHLC and dedup checks
        assert result[0]["quality_flag"] == "estimated"

    def test_mixed_valid_and_invalid(self):
        bars = [
            _bar("2024-01-01T00:00:00", o=1.1, h=1.2, l=1.0, c=1.15),  # ok
            _bar("2024-01-01T00:01:00", o=1.3, h=1.2, l=1.0, c=1.15),  # invalid
            _bar("2024-01-01T00:02:00", o=1.1, h=1.2, l=1.0, c=1.1),   # ok
        ]
        result = normalize_bars(bars)
        flags = [b["quality_flag"] for b in result]
        assert flags == ["ok", "ohlc_invalid", "ok"]
