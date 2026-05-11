/**
 * CreditApprovalDialog — RTL tests.
 *
 * Pure presentational. Plain Jest assertions (no jest-dom) to match
 * the existing repo convention.
 */
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import CreditApprovalDialog from './CreditApprovalDialog';

function baseProps(overrides = {}) {
  return {
    open: true,
    onOpenChange: jest.fn(),
    creditCheckResult: null,
    balanceDue: 1500,
    pin: '',
    setPin: jest.fn(),
    pinSessionWarm: false,
    reason: '',
    setReason: jest.fn(),
    showOfflineReason: false,
    onVerify: jest.fn(),
    onCancel: jest.fn(),
    saving: false,
    ...overrides,
  };
}

describe('CreditApprovalDialog', () => {
  test('renders nothing when open=false', () => {
    render(<CreditApprovalDialog {...baseProps({ open: false })} />);
    expect(screen.queryByTestId('verify-pin')).toBeNull();
    expect(screen.queryByTestId('manager-pin')).toBeNull();
  });

  test('renders PIN input and verify button when open=true', () => {
    render(<CreditApprovalDialog {...baseProps()} />);
    expect(screen.getByTestId('manager-pin')).toBeTruthy();
    expect(screen.getByTestId('verify-pin')).toBeTruthy();
  });

  test('shows red Credit-Limit-Exceeded panel with all 5 amount rows when creditCheckResult.allowed=false', () => {
    const ccr = {
      allowed: false,
      currentBalance: 2000,
      creditLimit: 3000,
      newTotal: 3500,
      exceededBy: 500,
    };
    render(<CreditApprovalDialog {...baseProps({ creditCheckResult: ccr, balanceDue: 1500 })} />);
    expect(screen.getByText(/Credit Limit Exceeded/i)).toBeTruthy();
    expect(screen.getByText('Current Balance:')).toBeTruthy();
    expect(screen.getByText('This Sale:')).toBeTruthy();
    expect(screen.getByText('New Total:')).toBeTruthy();
    expect(screen.getByText('Credit Limit:')).toBeTruthy();
    expect(screen.getByText('Exceeded By:')).toBeTruthy();
  });

  test('shows amber "requires authorization" panel when creditCheckResult.allowed=true', () => {
    render(<CreditApprovalDialog {...baseProps({ creditCheckResult: { allowed: true }, balanceDue: 1500 })} />);
    expect(screen.getByText(/requires authorization/i)).toBeTruthy();
    expect(screen.queryByText(/Credit Limit Exceeded/i)).toBeNull();
  });

  test('renders credit-pin-session-hint when pinSessionWarm=true; hides it when false', () => {
    const { unmount } = render(<CreditApprovalDialog {...baseProps({ pinSessionWarm: true })} />);
    expect(screen.getByTestId('credit-pin-session-hint')).toBeTruthy();
    unmount();

    render(<CreditApprovalDialog {...baseProps({ pinSessionWarm: false })} />);
    expect(screen.queryByTestId('credit-pin-session-hint')).toBeNull();
  });

  test('renders offline-bypass-reason only when showOfflineReason=true; setReason wired', () => {
    const { unmount } = render(<CreditApprovalDialog {...baseProps({ showOfflineReason: false })} />);
    expect(screen.queryByTestId('offline-bypass-reason')).toBeNull();
    unmount();

    const props = baseProps({ showOfflineReason: true });
    render(<CreditApprovalDialog {...props} />);
    const input = screen.getByTestId('offline-bypass-reason');
    fireEvent.change(input, { target: { value: 'Customer in a hurry' } });
    expect(props.setReason).toHaveBeenCalledWith('Customer in a hurry');
  });

  test('Cancel button calls onCancel', () => {
    const props = baseProps();
    render(<CreditApprovalDialog {...props} />);
    fireEvent.click(screen.getByText('Cancel'));
    expect(props.onCancel).toHaveBeenCalledTimes(1);
  });

  test('verify button disabled when pin empty; enabled when non-empty; click → onVerify', () => {
    // Disabled when pin is empty
    const { unmount } = render(<CreditApprovalDialog {...baseProps({ pin: '' })} />);
    expect(screen.getByTestId('verify-pin').disabled).toBe(true);
    unmount();

    // Disabled when saving=true
    const propsSaving = baseProps({ pin: '1234', saving: true });
    const { unmount: u2 } = render(<CreditApprovalDialog {...propsSaving} />);
    expect(screen.getByTestId('verify-pin').disabled).toBe(true);
    u2();

    // Enabled when pin non-empty and not saving
    const props = baseProps({ pin: '1234' });
    render(<CreditApprovalDialog {...props} />);
    const btn = screen.getByTestId('verify-pin');
    expect(btn.disabled).toBe(false);
    fireEvent.click(btn);
    expect(props.onVerify).toHaveBeenCalledTimes(1);
  });

  test('Enter key on PIN input fires onVerify when pin is non-empty', () => {
    const props = baseProps({ pin: '1234' });
    render(<CreditApprovalDialog {...props} />);
    fireEvent.keyDown(screen.getByTestId('manager-pin'), { key: 'Enter' });
    expect(props.onVerify).toHaveBeenCalledTimes(1);
  });

  test('Enter key on PIN input does NOT fire onVerify when pin is empty', () => {
    const props = baseProps({ pin: '' });
    render(<CreditApprovalDialog {...props} />);
    fireEvent.keyDown(screen.getByTestId('manager-pin'), { key: 'Enter' });
    expect(props.onVerify).not.toHaveBeenCalled();
  });

  test('typing into PIN input calls setPin', () => {
    const props = baseProps();
    render(<CreditApprovalDialog {...props} />);
    fireEvent.change(screen.getByTestId('manager-pin'), { target: { value: '987654' } });
    expect(props.setPin).toHaveBeenCalledWith('987654');
  });
});
