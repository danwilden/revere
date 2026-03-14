/**
 * Mandatory UI warnings — these MUST be surfaced in any view
 * that presents research-grade output or backtest results.
 */

export const RESEARCH_GRADE_WARNING =
  'RESEARCH GRADE — oracle regime labels active. Viterbi labels use the full inference window and are forward-looking. Not suitable for live trading simulation.'

export const EQUITY_CURVE_NOTE =
  'Reflects closed trade P&L only. Open/unrealized P&L is excluded.'

export const ANNUALIZED_RETURN_LABEL = 'Net Return (approx. annualized)'

export const PIP_SIZE_WARNING =
  'pip_size must match the instrument (JPY pairs = 0.01, all others = 0.0001). Incorrect pip_size will silently miscalculate costs.'

/**
 * Regime semantic labels — the 7 valid values from backend/models/labeling.py
 */
export const REGIME_LABELS = [
  'TREND_BULL_LOW_VOL',
  'TREND_BULL_HIGH_VOL',
  'TREND_BEAR_LOW_VOL',
  'TREND_BEAR_HIGH_VOL',
  'RANGE_MEAN_REVERT',
  'CHOPPY_SIGNAL',
  'CHOPPY_NOISE',
]

/**
 * Color map for regime labels — used in chips, chart overlays, legends
 */
export const REGIME_COLORS = {
  TREND_BULL_LOW_VOL: '#00FF41',   // neon green
  TREND_BULL_HIGH_VOL: '#66FF88',  // lighter green
  TREND_BEAR_LOW_VOL: '#FF2222',   // red
  TREND_BEAR_HIGH_VOL: '#FF6666',  // lighter red
  RANGE_MEAN_REVERT: '#4FC3F7',    // info blue
  CHOPPY_SIGNAL: '#FFE600',        // yellow
  CHOPPY_NOISE: '#777777',         // grey
  UNKNOWN: '#444444',
}

/**
 * Job status colors — matches status-chip CSS classes
 */
export const JOB_STATUS_COLORS = {
  queued: '#aaaaaa',
  running: '#FFE600',
  succeeded: '#00FF41',
  failed: '#FF2222',
  cancelled: '#777777',
}

/**
 * Pip sizes by instrument suffix — for JPY pair detection
 */
export const PIP_SIZE_MAP = {
  JPY: 0.01,
  DEFAULT: 0.0001,
}

/**
 * Detect pip size for a given instrument ID (e.g. "USD_JPY" → 0.01)
 * @param {string} instrumentId
 * @returns {number}
 */
export const getPipSize = (instrumentId) => {
  if (!instrumentId) return PIP_SIZE_MAP.DEFAULT
  return instrumentId.toUpperCase().includes('JPY')
    ? PIP_SIZE_MAP.JPY
    : PIP_SIZE_MAP.DEFAULT
}
