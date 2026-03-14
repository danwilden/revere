import client from './client.js'

/**
 * GET /api/instruments
 * Returns all available instrument specs.
 */
export const getInstruments = () =>
  client.get('/api/instruments').then((r) => r.data)

/**
 * GET /api/instruments/defaults
 * Returns the default instrument set for the platform.
 */
export const getDefaultInstruments = () =>
  client.get('/api/instruments/defaults').then((r) => r.data)

/**
 * GET /api/market-data/ranges
 * @param {Object} params - optional query params (e.g. instrument_id, timeframe)
 */
export const getMarketDataRanges = (params = {}) =>
  client.get('/api/market-data/ranges', { params }).then((r) => r.data)

/**
 * GET /api/market-data/bars
 * @param {Object} params - { instrument_id, timeframe, start, end, limit }
 */
export const getMarketDataBars = (params = {}) =>
  client.get('/api/market-data/bars', { params }).then((r) => r.data)
