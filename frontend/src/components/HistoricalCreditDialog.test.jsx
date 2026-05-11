/**
 * HistoricalCreditDialog — RTL tests.
 *
 * Asserts the pure-rendering contract using plain Jest assertions
 * (no `@testing-library/jest-dom` — matches existing repo convention).
 */
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import HistoricalCreditDialog from './HistoricalCreditDialog';

const LONG_REASON = 'Notebook AR carry-forward verified against ledger page 12.';

function mkHc(overrides = {}) {
  return {
    dialogOpen: true,
    setDialogOpen: jest.fn(),
    closeDialog: jest.fn(),
    committing: false,
    reason: LONG_REASON,
    preview: null,
    previewLoading: false,
    previewError: '',
    runPreview: jest.fn(),
    approvalCode: '',
    setApprovalCode: jest.fn(),
    commit: jest.fn(),
    commitError: '',
    allowInv: false,
    setAllowInv: jest.fn(),
    setPreview: jest.fn(),
    ...overrides,
  };
}

const baseProps = {
  customer: { name: 'Juan Dela Cruz' },
  branch: { name: 'Main Branch' },
  orderDate: '2026-01-15',
  daysBack: 30,
  itemsCount: 3,
  grandTotal: 1500,
};

describe('HistoricalCreditDialog', () => {
  test('does NOT render dialog body when hc.dialogOpen=false', () => {
    render(<HistoricalCreditDialog hc={mkHc({ dialogOpen: false })} {...baseProps} />);
    expect(screen.queryByTestId('historical-credit-dialog')).toBeNull();
  });

  test('renders dialog + snapshot rows when hc.dialogOpen=true', () => {
    render(<HistoricalCreditDialog hc={mkHc()} {...baseProps} />);
    expect(screen.getByTestId('historical-credit-dialog')).toBeTruthy();
    expect(screen.getByText('Juan Dela Cruz')).toBeTruthy();
    expect(screen.getByText('Main Branch')).toBeTruthy();
    expect(screen.getByTestId('historical-credit-dialog').textContent).toMatch(/2026-01-15.*30 days back/i);
  });

  test('Run Preview button is disabled when reason < 20 chars', () => {
    const hc = mkHc({ reason: 'too short' });
    render(<HistoricalCreditDialog hc={hc} {...baseProps} />);
    const btn = screen.getByTestId('historical-credit-preview-btn');
    expect(btn.disabled).toBe(true);
  });

  test('Run Preview button calls runPreview when reason is valid', () => {
    const hc = mkHc({ reason: LONG_REASON });
    render(<HistoricalCreditDialog hc={hc} {...baseProps} />);
    const btn = screen.getByTestId('historical-credit-preview-btn');
    expect(btn.disabled).toBe(false);
    fireEvent.click(btn);
    expect(hc.runPreview).toHaveBeenCalledTimes(1);
  });

  test('when preview is provided, customer-owes snapshot + closed-day note render', () => {
    const preview = {
      grand_total: 1500,
      customer: { current_balance: 500, projected_balance: 2000 },
      inventory_action: 'deducted',
    };
    render(<HistoricalCreditDialog hc={mkHc({ preview })} {...baseProps} />);
    expect(screen.getByTestId('historical-credit-customer-owes-snapshot')).toBeTruthy();
    expect(screen.getByTestId('historical-credit-report-effect')).toBeTruthy();
    expect(screen.queryByTestId('historical-credit-count-stopper')).toBeNull();
  });

  test('count-sheet stopper renders when inventory_action=skipped_count_sheet_lock; checkbox toggles allowInv + clears preview', () => {
    const hc = mkHc({
      preview: {
        grand_total: 1500,
        customer: { current_balance: 500, projected_balance: 2000 },
        inventory_action: 'skipped_count_sheet_lock',
        count_sheet_stopper: { latest_count_date: '2025-12-31' },
      },
    });
    render(<HistoricalCreditDialog hc={hc} {...baseProps} />);
    const stopper = screen.getByTestId('historical-credit-count-stopper');
    expect(stopper).toBeTruthy();
    expect(stopper.textContent).toMatch('2025-12-31');

    const cb = screen.getByTestId('historical-credit-allow-inv-checkbox');
    fireEvent.click(cb);
    expect(hc.setAllowInv).toHaveBeenCalledWith(true);
    expect(hc.setPreview).toHaveBeenCalledWith(null);
  });

  test('commit button disabled until preview + approvalCode + valid reason', () => {
    // Missing preview
    let hc = mkHc({ approvalCode: '123456' });
    const { unmount: u1 } = render(<HistoricalCreditDialog hc={hc} {...baseProps} />);
    expect(screen.getByTestId('historical-credit-commit-btn').disabled).toBe(true);
    u1();

    // Has preview but no approvalCode
    hc = mkHc({ preview: { customer: {}, grand_total: 1500, inventory_action: 'deducted' } });
    const { unmount: u2 } = render(<HistoricalCreditDialog hc={hc} {...baseProps} />);
    expect(screen.getByTestId('historical-credit-commit-btn').disabled).toBe(true);
    u2();

    // Has both — commit clickable
    hc = mkHc({
      preview: { customer: {}, grand_total: 1500, inventory_action: 'deducted' },
      approvalCode: '123456',
    });
    render(<HistoricalCreditDialog hc={hc} {...baseProps} />);
    const commitBtn = screen.getByTestId('historical-credit-commit-btn');
    expect(commitBtn.disabled).toBe(false);
    fireEvent.click(commitBtn);
    expect(hc.commit).toHaveBeenCalledTimes(1);
  });

  test('Cancel button calls hc.closeDialog', () => {
    const hc = mkHc();
    render(<HistoricalCreditDialog hc={hc} {...baseProps} />);
    fireEvent.click(screen.getByTestId('historical-credit-cancel-btn'));
    expect(hc.closeDialog).toHaveBeenCalledTimes(1);
  });

  test('approval-code input sanitises non-numeric and caps at 8 chars', () => {
    const hc = mkHc({
      preview: { customer: {}, grand_total: 1500, inventory_action: 'deducted' },
    });
    render(<HistoricalCreditDialog hc={hc} {...baseProps} />);
    const input = screen.getByTestId('historical-credit-approval-code-input');
    fireEvent.change(input, { target: { value: 'abc12345678extra' } });
    expect(hc.setApprovalCode).toHaveBeenCalledWith('12345678');
  });
});
