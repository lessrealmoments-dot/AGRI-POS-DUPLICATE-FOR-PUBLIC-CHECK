/**
 * useHistoricalCredit — Phase 4 cleanup hook tests.
 *
 * Mocks the axios `api` instance, `invalidateBalanceCache`, and `toast`
 * so we can drive the hook deterministically without real network calls.
 */

jest.mock('../contexts/AuthContext', () => ({
  api: { post: jest.fn() },
}));
jest.mock('../components/CustomerBalanceBadge', () => ({
  invalidateBalanceCache: jest.fn(),
}));
jest.mock('sonner', () => ({
  toast: Object.assign(jest.fn(), { success: jest.fn(), error: jest.fn() }),
}));

import { renderHook, act } from '@testing-library/react';
import { useHistoricalCredit, HISTORICAL_CREDIT_FLOOR_DAYS } from './useHistoricalCredit';
import { api } from '../contexts/AuthContext';

const SAMPLE_CONTEXT = () => ({
  customer_id: 'cust-1',
  branch_id: 'br-1',
  transaction_date: '2026-01-15',
  subtotal: 1000,
  freight: 0,
  overall_discount: 0,
  grand_total: 1000,
});
const SAMPLE_ITEMS = () => [
  { product_id: 'p-1', product_name: 'Rice', quantity: 1, rate: 1000, price: 1000, total: 1000 },
];
const REASON_OK = 'Notebook AR carry-forward verified against ledger page 12.';

function defaultOpts(overrides = {}) {
  return {
    daysBack: 30,
    paymentType: 'credit',
    isPrivilegedRole: true,
    getItems: SAMPLE_ITEMS,
    getContext: SAMPLE_CONTEXT,
    hasCustomer: () => true,
    onCommitted: jest.fn(),
    ...overrides,
  };
}

beforeEach(() => {
  jest.clearAllMocks();
});

describe('useHistoricalCredit — initial state', () => {
  test('clean defaults, no preview, dialog closed, no errors', () => {
    const { result } = renderHook(() => useHistoricalCredit(defaultOpts()));
    expect(result.current.reason).toBe('');
    expect(result.current.proofUrl).toBe('');
    expect(result.current.notebookRef).toBe('');
    expect(result.current.allowInv).toBe(false);
    expect(result.current.approvalCode).toBe('');
    expect(result.current.dialogOpen).toBe(false);
    expect(result.current.preview).toBeNull();
    expect(result.current.previewLoading).toBe(false);
    expect(result.current.previewError).toBe('');
    expect(result.current.committing).toBe(false);
    expect(result.current.commitError).toBe('');
    expect(result.current.floorDays).toBe(HISTORICAL_CREDIT_FLOOR_DAYS);
  });

  test('isMode is true when daysBack > 7, payment=credit, isPrivilegedRole', () => {
    const { result } = renderHook(() => useHistoricalCredit(defaultOpts()));
    expect(result.current.isMode).toBe(true);
    expect(result.current.isBackdatedNonCreditBlocked).toBe(false);
  });

  test('isBackdatedNonCreditBlocked is true when daysBack > 7 and payment != credit', () => {
    const { result } = renderHook(() => useHistoricalCredit(defaultOpts({ paymentType: 'cash' })));
    expect(result.current.isMode).toBe(false);
    expect(result.current.isBackdatedNonCreditBlocked).toBe(true);
  });

  test('isMode is false when daysBack <= 7', () => {
    const { result } = renderHook(() => useHistoricalCredit(defaultOpts({ daysBack: 3 })));
    expect(result.current.isMode).toBe(false);
    expect(result.current.isBackdatedNonCreditBlocked).toBe(false);
  });
});

describe('useHistoricalCredit.runPreview', () => {
  test('blocks when no customer is selected and sets previewError', async () => {
    const { result } = renderHook(() => useHistoricalCredit(defaultOpts({ hasCustomer: () => false })));
    act(() => { result.current.setReason(REASON_OK); });
    await act(async () => { await result.current.runPreview(); });
    expect(result.current.previewError).toBe('Select a customer before preview.');
    expect(api.post).not.toHaveBeenCalled();
    expect(result.current.preview).toBeNull();
  });

  test('blocks when reason is under 20 chars', async () => {
    const { result } = renderHook(() => useHistoricalCredit(defaultOpts()));
    act(() => { result.current.setReason('too short'); });
    await act(async () => { await result.current.runPreview(); });
    expect(result.current.previewError).toBe('Reason must be at least 20 characters.');
    expect(api.post).not.toHaveBeenCalled();
    expect(result.current.preview).toBeNull();
  });

  test('happy path populates preview and does NOT send approval_code', async () => {
    const previewData = { customer: { current_balance: 0, projected_balance: 1000 }, grand_total: 1000 };
    api.post.mockResolvedValueOnce({ data: previewData });
    const { result } = renderHook(() => useHistoricalCredit(defaultOpts()));
    act(() => { result.current.setReason(REASON_OK); });
    await act(async () => { await result.current.runPreview(); });
    expect(api.post).toHaveBeenCalledTimes(1);
    const [path, payload] = api.post.mock.calls[0];
    expect(path).toBe('/historical-credit/preview');
    expect(payload).not.toHaveProperty('approval_code');
    expect(payload.customer_id).toBe('cust-1');
    expect(payload.reason).toBe(REASON_OK);
    expect(result.current.preview).toEqual(previewData);
    expect(result.current.previewError).toBe('');
  });
});

describe('useHistoricalCredit.commit', () => {
  async function primePreview(result) {
    api.post.mockResolvedValueOnce({ data: { customer: { current_balance: 0, projected_balance: 1000 }, grand_total: 1000 } });
    act(() => { result.current.setReason(REASON_OK); });
    await act(async () => { await result.current.runPreview(); });
    api.post.mockClear();
  }

  test('blocks when preview has not been run', async () => {
    const { result } = renderHook(() => useHistoricalCredit(defaultOpts()));
    act(() => { result.current.setApprovalCode('123456'); });
    await act(async () => { await result.current.commit(); });
    expect(result.current.commitError).toBe('Run preview first.');
    expect(api.post).not.toHaveBeenCalled();
  });

  test('blocks when approval code is missing', async () => {
    const { result } = renderHook(() => useHistoricalCredit(defaultOpts()));
    await primePreview(result);
    await act(async () => { await result.current.commit(); });
    expect(result.current.commitError).toBe('Owner / Admin Authenticator (TOTP) code is required.');
    expect(api.post).not.toHaveBeenCalled();
  });

  test('happy path invokes onCommitted and resets hook state', async () => {
    const opts = defaultOpts();
    const { result } = renderHook(() => useHistoricalCredit(opts));
    await primePreview(result);
    act(() => { result.current.setApprovalCode('654321'); });
    api.post.mockResolvedValueOnce({ data: { invoice: { invoice_number: 'HC-001' } } });
    await act(async () => { await result.current.commit(); });
    expect(opts.onCommitted).toHaveBeenCalledTimes(1);
    expect(opts.onCommitted.mock.calls[0][0]).toEqual({ invoice: { invoice_number: 'HC-001' } });
    // hook-owned state cleared
    expect(result.current.dialogOpen).toBe(false);
    expect(result.current.preview).toBeNull();
    expect(result.current.reason).toBe('');
    expect(result.current.proofUrl).toBe('');
    expect(result.current.notebookRef).toBe('');
    expect(result.current.allowInv).toBe(false);
    expect(result.current.approvalCode).toBe('');
    expect(result.current.commitError).toBe('');
  });

  test('decodes approval_invalid into the canonical message', async () => {
    const { result } = renderHook(() => useHistoricalCredit(defaultOpts()));
    await primePreview(result);
    act(() => { result.current.setApprovalCode('000000'); });
    api.post.mockRejectedValueOnce({
      response: { status: 403, data: { detail: { error: 'approval_invalid', message: 'no' } } },
    });
    await act(async () => { await result.current.commit(); });
    expect(result.current.commitError).toMatch(/Invalid or unauthorized/);
    expect(result.current.commitError).toMatch(/TOTP/);
  });

  test('decodes use_regular_late_encode error', async () => {
    const { result } = renderHook(() => useHistoricalCredit(defaultOpts()));
    await primePreview(result);
    act(() => { result.current.setApprovalCode('111111'); });
    api.post.mockRejectedValueOnce({
      response: {
        status: 400,
        data: { detail: { error: 'use_regular_late_encode', message: 'Use the regular Sales late-encode path for dates within 7 days back.', days_back: 3, soft_floor_days: 7 } },
      },
    });
    await act(async () => { await result.current.commit(); });
    expect(result.current.commitError).toMatch(/regular Sales late-encode/);
  });
});

describe('useHistoricalCredit.openDialog / closeDialog', () => {
  test('openDialog clears stale preview/errors and opens', () => {
    const { result } = renderHook(() => useHistoricalCredit(defaultOpts()));
    act(() => { result.current.setReason('preserved reason at least twenty chars'); result.current.setApprovalCode('stale'); });
    act(() => { result.current.openDialog(); });
    expect(result.current.dialogOpen).toBe(true);
    expect(result.current.approvalCode).toBe('');
    // reason preserved
    expect(result.current.reason).toBe('preserved reason at least twenty chars');
  });

  test('closeDialog clears preview/errors and keeps reason', () => {
    const { result } = renderHook(() => useHistoricalCredit(defaultOpts()));
    act(() => { result.current.setReason('preserved reason at least twenty chars'); result.current.openDialog(); });
    act(() => { result.current.closeDialog(); });
    expect(result.current.dialogOpen).toBe(false);
    expect(result.current.preview).toBeNull();
    expect(result.current.reason).toBe('preserved reason at least twenty chars');
  });
});
