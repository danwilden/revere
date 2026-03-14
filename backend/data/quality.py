"""Data quality checks for market bars.

Provides gap detection, duplicate detection, and monotonic timestamp
validation. These are post-normalization checks — run after normalize_bars()
to assess the quality of an ingested dataset.

Usage:
    from backend.data.quality import check_bars_quality
    report = check_bars_quality(bars, expected_gap_minutes=1)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


@dataclass
class QualityReport:
    """Summary of data quality issues found in a bar set."""

    instrument_id: str
    total_bars: int
    gap_count: int = 0
    duplicate_count: int = 0
    ohlc_invalid_count: int = 0
    non_monotonic_count: int = 0
    gaps: list[dict] = field(default_factory=list)   # [{from, to, missing_bars}]
    duplicates: list[datetime] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return (
            self.gap_count == 0
            and self.duplicate_count == 0
            and self.ohlc_invalid_count == 0
            and self.non_monotonic_count == 0
        )

    def summary(self) -> dict:
        return {
            "instrument_id": self.instrument_id,
            "total_bars": self.total_bars,
            "gap_count": self.gap_count,
            "duplicate_count": self.duplicate_count,
            "ohlc_invalid_count": self.ohlc_invalid_count,
            "non_monotonic_count": self.non_monotonic_count,
            "is_clean": self.is_clean,
        }


def check_bars_quality(
    bars: list[dict],
    expected_gap_minutes: int = 1,
    instrument_id: str | None = None,
) -> QualityReport:
    """Run quality checks on a list of bar dicts.

    Args:
        bars:                   List of bar dicts (sorted or unsorted).
        expected_gap_minutes:   Expected gap between consecutive bars. 1 for M1,
                                60 for H1, etc. Gaps larger than this are flagged.
        instrument_id:          Override the instrument_id for the report label.
                                If None, taken from the first bar.

    Returns:
        QualityReport with all detected issues.
    """
    if not bars:
        iid = instrument_id or "unknown"
        return QualityReport(instrument_id=iid, total_bars=0)

    iid = instrument_id or bars[0].get("instrument_id", "unknown")
    report = QualityReport(instrument_id=iid, total_bars=len(bars))

    timestamps: list[datetime] = []
    ts_set: set[datetime] = set()
    seen_ts: dict[datetime, int] = {}

    for bar in bars:
        ts = _coerce_ts(bar["timestamp_utc"])
        timestamps.append(ts)
        seen_ts[ts] = seen_ts.get(ts, 0) + 1

    # 1. Duplicate detection
    for ts, count in seen_ts.items():
        if count > 1:
            report.duplicate_count += 1
            report.duplicates.append(ts)

    # 2. Sort for monotonic + gap checks
    sorted_ts = sorted(timestamps)

    # 3. Non-monotonic check (using the original order)
    for i in range(1, len(timestamps)):
        if timestamps[i] <= timestamps[i - 1]:
            report.non_monotonic_count += 1

    # 4. Gap detection (on sorted unique timestamps)
    expected_delta = timedelta(minutes=expected_gap_minutes)
    # Exclude weekends: Forex markets close Friday ~22:00 UTC, reopen Sunday ~21:00 UTC
    unique_sorted = sorted(set(sorted_ts))
    for i in range(1, len(unique_sorted)):
        prev, curr = unique_sorted[i - 1], unique_sorted[i]
        delta = curr - prev
        if delta > expected_delta:
            # Only flag as gap if NOT a typical weekend gap
            if not _is_weekend_gap(prev, curr):
                missing_count = int(delta.total_seconds() / (expected_gap_minutes * 60)) - 1
                report.gap_count += 1
                report.gaps.append({
                    "from": prev.isoformat(),
                    "to": curr.isoformat(),
                    "gap_minutes": int(delta.total_seconds() / 60),
                    "missing_bars": missing_count,
                })

    # 5. OHLC invalid count (from quality_flag if already set)
    for bar in bars:
        if bar.get("quality_flag") == "ohlc_invalid":
            report.ohlc_invalid_count += 1

    return report


def detect_gaps(
    bars: list[dict],
    expected_gap_minutes: int = 1,
) -> list[dict]:
    """Return list of gap dicts found in bars.

    Convenience wrapper that returns just the gaps list from check_bars_quality.
    """
    report = check_bars_quality(bars, expected_gap_minutes=expected_gap_minutes)
    return report.gaps


def detect_duplicates(bars: list[dict]) -> list[datetime]:
    """Return list of timestamps that appear more than once."""
    report = check_bars_quality(bars)
    return report.duplicates


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_weekend_gap(prev: datetime, curr: datetime) -> bool:
    """Return True if the gap between prev and curr spans a Forex weekend.

    Forex market closes ~22:00 UTC Friday, reopens ~21:00 UTC Sunday.
    We consider any gap that starts on Friday and ends by Monday to be a
    legitimate weekend gap (not flagged as a data gap).
    """
    # prev is Friday (weekday 4) or Saturday (5); curr is Saturday or Monday (0)
    if prev.weekday() == 4 and curr.weekday() in (5, 6, 0):
        return True
    if prev.weekday() == 5 and curr.weekday() in (6, 0):
        return True
    if prev.weekday() == 6 and curr.weekday() == 0:
        return True
    # Large but not cross-weekend? Could still be holiday — only flag if >3 days
    delta = curr - prev
    if delta.days >= 3:
        return False
    return False


def _coerce_ts(ts) -> datetime:
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts
    if isinstance(ts, str):
        ts = ts.rstrip("Z").split(".")[0]
        dt = datetime.fromisoformat(ts)
        return dt.replace(tzinfo=timezone.utc)
    raise TypeError(f"Cannot coerce {type(ts)} to datetime")
