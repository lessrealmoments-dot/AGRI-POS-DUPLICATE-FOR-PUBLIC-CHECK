/**
 * Phase 4A.1 — Connectivity helpers — unit tests.
 *
 * Covers:
 *   - HTTP 400/403/422/500 with a server response are NOT network errors
 *     (must surface the real error; must NOT save offline).
 *   - True network failures (ERR_NETWORK, no response, "Failed to fetch")
 *     ARE network errors and trigger the reconnect grace.
 *   - pingBackendHealth returns true for 200 and false for non-2xx /
 *     thrown / aborted.
 */

import { isTrueNetworkError, pingBackendHealth } from './connectivity';

describe('isTrueNetworkError', () => {
  test('HTTP 400 with response body is NOT a network error', () => {
    expect(isTrueNetworkError({ response: { status: 400, data: { detail: 'bad request' } } })).toBe(false);
  });
  test('HTTP 401 with response body is NOT a network error', () => {
    expect(isTrueNetworkError({ response: { status: 401, data: { detail: 'unauthorized' } } })).toBe(false);
  });
  test('HTTP 403 with response body is NOT a network error', () => {
    expect(isTrueNetworkError({ response: { status: 403, data: { detail: 'forbidden' } } })).toBe(false);
  });
  test('HTTP 404 with response body is NOT a network error', () => {
    expect(isTrueNetworkError({ response: { status: 404, data: { detail: 'not found' } } })).toBe(false);
  });
  test('HTTP 409 with response body is NOT a network error', () => {
    expect(isTrueNetworkError({ response: { status: 409, data: { detail: 'conflict' } } })).toBe(false);
  });
  test('HTTP 422 with response body is NOT a network error', () => {
    expect(isTrueNetworkError({ response: { status: 422, data: { detail: { errors: ['bad field'] } } } })).toBe(false);
  });
  test('HTTP 500 with response body is NOT a network error', () => {
    expect(isTrueNetworkError({ response: { status: 500, data: { detail: 'server bug' } } })).toBe(false);
  });
  test('axios ERR_NETWORK IS a network error', () => {
    expect(isTrueNetworkError({ code: 'ERR_NETWORK', message: 'Network Error' })).toBe(true);
  });
  test('axios ECONNABORTED IS a network error', () => {
    expect(isTrueNetworkError({ code: 'ECONNABORTED', message: 'timeout of 5000ms exceeded' })).toBe(true);
  });
  test('fetch "Failed to fetch" IS a network error', () => {
    expect(isTrueNetworkError({ message: 'Failed to fetch' })).toBe(true);
  });
  test('plain "Network Error" message IS a network error', () => {
    expect(isTrueNetworkError({ message: 'Network Error' })).toBe(true);
  });
  test('Error with no .response and no code IS a network error', () => {
    expect(isTrueNetworkError(new Error('connection reset'))).toBe(true);
  });
  test('null is NOT a network error', () => {
    expect(isTrueNetworkError(null)).toBe(false);
  });
});

describe('pingBackendHealth', () => {
  const realFetch = global.fetch;
  afterEach(() => { global.fetch = realFetch; });
  beforeEach(() => { process.env.REACT_APP_BACKEND_URL = 'http://example.test'; });

  test('returns true on 200 OK', async () => {
    global.fetch = jest.fn().mockResolvedValue({ ok: true });
    expect(await pingBackendHealth(1000)).toBe(true);
  });
  test('returns false on 500', async () => {
    global.fetch = jest.fn().mockResolvedValue({ ok: false });
    expect(await pingBackendHealth(1000)).toBe(false);
  });
  test('returns false when fetch throws', async () => {
    global.fetch = jest.fn().mockRejectedValue(new Error('boom'));
    expect(await pingBackendHealth(1000)).toBe(false);
  });
  test('hits /api/health on REACT_APP_BACKEND_URL', async () => {
    const spy = jest.fn().mockResolvedValue({ ok: true });
    global.fetch = spy;
    await pingBackendHealth(1000);
    expect(spy).toHaveBeenCalledWith(
      'http://example.test/api/health',
      expect.objectContaining({ method: 'GET', cache: 'no-store' }),
    );
  });
});
