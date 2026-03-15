import client from './client.js'

/**
 * GET /api/market-data/ranges?instrument={instrumentId}
 * Returns coverage records for all timeframes for the given instrument.
 * Response shape: { ranges: [{instrument_id, timeframe, start, end, has_data}] }
 * `start` and `end` are ISO datetime strings (e.g. "2023-01-01T00:00:00") or null if no data.
 *
 * @param {string} instrumentId - e.g. "EUR_USD"
 */
export const getMarketDataRanges = (instrumentId) =>
  client.get('/api/market-data/ranges', { params: { instrument: instrumentId } }).then((r) => r.data)
