/**
 * CheckoutDialog — RTL tests.
 *
 * Pure presentational tests. Uses plain Jest assertions (no jest-dom)
 * to match the existing repo convention.
 */
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import CheckoutDialog from './CheckoutDialog';

function setters() {
  return {
    setSelectedCustomer: jest.fn(),
    setCustSearch: jest.fn(),
    setPaymentType: jest.fn(),
    setAmountTendered: jest.fn(),
    setPartialPayment: jest.fn(),
    setSplitCash: jest.fn(),
    setSplitDigital: jest.fn(),
    setDigitalPlatform: jest.fn(),
    setDigitalRefNumber: jest.fn(),
    setDigitalSender: jest.fn(),
    setReleaseMode: jest.fn(),
    onConfirm: jest.fn(),
    onOpenChange: jest.fn(),
  };
}

function baseProps(overrides = {}) {
  return {
    open: true,
    ...setters(),
    selectedCustomer: null,
    custSearch: '',
    customers: [
      { id: 'c1', name: 'Alice', balance: 0, credit_limit: 5000 },
      { id: 'c2', name: 'Bob', balance: 1200, credit_limit: 10000 },
    ],
    canViewBalance: true,
    grandTotal: 1000,
    change: 0,
    paymentType: 'cash',
    amountTendered: 1000,
    partialPayment: 0,
    splitCash: '',
    splitDigital: '',
    digitalPlatform: 'GCash',
    digitalRefNumber: '',
    digitalSender: '',
    releaseMode: 'full',
    saving: false,
    confirmDisabled: false,
    isHistoricalCreditMode: false,
    ...overrides,
  };
}

describe('CheckoutDialog', () => {
  test('does not render dialog content when open=false', () => {
    render(<CheckoutDialog {...baseProps({ open: false })} />);
    expect(screen.queryByTestId('confirm-payment')).toBeNull();
  });

  test('renders 5 payment-type tab triggers when open', () => {
    render(<CheckoutDialog {...baseProps()} />);
    expect(screen.getByTestId('pay-cash')).toBeTruthy();
    expect(screen.getByTestId('pay-digital')).toBeTruthy();
    expect(screen.getByTestId('pay-split')).toBeTruthy();
    expect(screen.getByTestId('pay-partial')).toBeTruthy();
    expect(screen.getByTestId('pay-credit')).toBeTruthy();
  });

  test('cash tab shows amount-tendered input and change row when change>0', () => {
    render(<CheckoutDialog {...baseProps({ paymentType: 'cash', change: 250 })} />);
    expect(screen.getByTestId('amount-tendered')).toBeTruthy();
    expect(screen.getByText(/Change:/i)).toBeTruthy();
  });

  test('digital tab shows platform + ref + sender inputs; setters wired', () => {
    const props = baseProps({ paymentType: 'digital' });
    render(<CheckoutDialog {...props} />);
    expect(screen.getByTestId('digital-platform')).toBeTruthy();

    fireEvent.change(screen.getByTestId('digital-ref-number'), { target: { value: 'GC123' } });
    expect(props.setDigitalRefNumber).toHaveBeenCalledWith('GC123');

    fireEvent.change(screen.getByTestId('digital-sender'), { target: { value: 'Juan' } });
    expect(props.setDigitalSender).toHaveBeenCalledWith('Juan');
  });

  test('split tab auto-balances cash → digital', () => {
    const props = baseProps({ paymentType: 'split', grandTotal: 1000, splitCash: '', splitDigital: '' });
    render(<CheckoutDialog {...props} />);
    const cashInput = screen.getByTestId('split-cash').querySelector('input') || screen.getByTestId('split-cash');
    fireEvent.change(cashInput, { target: { value: '300' } });
    expect(props.setSplitCash).toHaveBeenCalledWith('300');
    expect(props.setSplitDigital).toHaveBeenCalledWith('700');
  });

  test('partial tab without customer shows the amber warning, not the input', () => {
    render(<CheckoutDialog {...baseProps({ paymentType: 'partial', selectedCustomer: null })} />);
    expect(screen.queryByTestId('partial-amount')).toBeNull();
    expect(screen.getByText(/Select a customer above/i)).toBeTruthy();
  });

  test('partial tab with customer shows the input + AR balance hint', () => {
    const props = baseProps({
      paymentType: 'partial',
      selectedCustomer: { id: 'c1', name: 'Alice', balance: 0, credit_limit: 5000 },
      partialPayment: 400,
      grandTotal: 1000,
    });
    render(<CheckoutDialog {...props} />);
    expect(screen.getByTestId('partial-amount')).toBeTruthy();
    expect(screen.getByText(/Balance \(to AR\)/i)).toBeTruthy();
  });

  test('credit tab shows the red full-credit info card', () => {
    render(<CheckoutDialog {...baseProps({ paymentType: 'credit', selectedCustomer: { id: 'c1', name: 'Alice', balance: 0, credit_limit: 5000 } })} />);
    expect(screen.getByText(/Full Credit Sale/i)).toBeTruthy();
  });

  test('release-mode buttons toggle setReleaseMode', () => {
    const props = baseProps();
    render(<CheckoutDialog {...props} />);
    fireEvent.click(screen.getByTestId('release-mode-partial'));
    expect(props.setReleaseMode).toHaveBeenCalledWith('partial');
    fireEvent.click(screen.getByTestId('release-mode-full'));
    expect(props.setReleaseMode).toHaveBeenCalledWith('full');
  });

  test('confirm-payment honors confirmDisabled prop and fires onConfirm when enabled', () => {
    // Disabled
    const propsDisabled = baseProps({ confirmDisabled: true });
    const { unmount } = render(<CheckoutDialog {...propsDisabled} />);
    expect(screen.getByTestId('confirm-payment').disabled).toBe(true);
    fireEvent.click(screen.getByTestId('confirm-payment'));
    expect(propsDisabled.onConfirm).not.toHaveBeenCalled();
    unmount();

    // Enabled
    const propsEnabled = baseProps({ confirmDisabled: false });
    render(<CheckoutDialog {...propsEnabled} />);
    const btn = screen.getByTestId('confirm-payment');
    expect(btn.disabled).toBe(false);
    fireEvent.click(btn);
    expect(propsEnabled.onConfirm).toHaveBeenCalledTimes(1);
  });

  test('confirm-payment label switches based on paymentType + HC mode + saving', () => {
    const { unmount: u1 } = render(<CheckoutDialog {...baseProps({ paymentType: 'cash' })} />);
    expect(screen.getByTestId('confirm-payment').textContent).toMatch(/Complete Sale/i);
    u1();

    const { unmount: u2 } = render(<CheckoutDialog {...baseProps({ paymentType: 'digital', digitalPlatform: 'GCash' })} />);
    expect(screen.getByTestId('confirm-payment').textContent).toMatch(/Complete — GCash/i);
    u2();

    const { unmount: u3 } = render(<CheckoutDialog {...baseProps({ paymentType: 'cash', isHistoricalCreditMode: true })} />);
    expect(screen.getByTestId('confirm-payment').textContent).toMatch(/Historical Credit/i);
    u3();

    render(<CheckoutDialog {...baseProps({ saving: true })} />);
    expect(screen.getByTestId('confirm-payment').textContent).toMatch(/Processing/i);
  });

  test('selected-customer chip clear button calls setSelectedCustomer(null) + setCustSearch("")', () => {
    const props = baseProps({ selectedCustomer: { id: 'c1', name: 'Alice', balance: 0, credit_limit: 5000 } });
    render(<CheckoutDialog {...props} />);
    fireEvent.click(screen.getByTestId('clear-customer-btn'));
    expect(props.setSelectedCustomer).toHaveBeenCalledWith(null);
    expect(props.setCustSearch).toHaveBeenCalledWith('');
  });

  test('customer search renders matches with checkout-cust-{id} testids', () => {
    render(<CheckoutDialog {...baseProps({ custSearch: 'al' })} />);
    expect(screen.getByTestId('checkout-cust-c1')).toBeTruthy();
  });
});
