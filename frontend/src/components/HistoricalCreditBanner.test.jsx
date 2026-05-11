/**
 * HistoricalCreditBanner — RTL tests.
 *
 * Asserts the pure-rendering contract:
 *   • blocked=true     → only the red `backdated-non-credit-block` appears.
 *   • enabled=true     → only the amber `historical-credit-banner` appears
 *                        with the 3 inputs wired to `hc.*` setters.
 *   • both false       → nothing renders.
 *   • daysBack number  → echoed in the banner copy.
 *   • Reason counter   → shows current length / 20 minimum.
 *   • Setter wiring    → typing in each input calls the matching setter.
 *
 * Uses plain Jest assertions (no `@testing-library/jest-dom`) to match the
 * existing repo test convention.
 */
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import HistoricalCreditBanner from './HistoricalCreditBanner';

function mkHc(overrides = {}) {
  return {
    reason: '',
    setReason: jest.fn(),
    proofUrl: '',
    setProofUrl: jest.fn(),
    notebookRef: '',
    setNotebookRef: jest.fn(),
    ...overrides,
  };
}

describe('HistoricalCreditBanner', () => {
  test('renders nothing when both enabled and blocked are false', () => {
    const { container } = render(
      <HistoricalCreditBanner enabled={false} blocked={false} daysBack={30} hc={mkHc()} />
    );
    expect(container.innerHTML).toBe('');
  });

  test('renders the red backdated-non-credit-block when blocked=true', () => {
    render(
      <HistoricalCreditBanner enabled={false} blocked={true} daysBack={15} hc={mkHc()} />
    );
    expect(screen.getByTestId('backdated-non-credit-block')).toBeTruthy();
    expect(screen.queryByTestId('historical-credit-banner')).toBeNull();
  });

  test('renders the amber historical-credit-banner + 3 inputs when enabled=true', () => {
    render(
      <HistoricalCreditBanner
        enabled={true}
        blocked={false}
        daysBack={42}
        hc={mkHc({ reason: 'short', proofUrl: 'http://x', notebookRef: 'p12' })}
      />
    );
    expect(screen.getByTestId('historical-credit-banner')).toBeTruthy();
    expect(screen.getByTestId('historical-credit-reason-input').value).toBe('short');
    expect(screen.getByTestId('historical-credit-proof-url-input').value).toBe('http://x');
    expect(screen.getByTestId('historical-credit-notebook-ref-input').value).toBe('p12');
    expect(screen.getByTestId('historical-credit-banner').textContent).toMatch(/42 days back/i);
    expect(screen.getByTestId('historical-credit-banner').textContent).toMatch(/5 \/ 20 minimum/i);
  });

  test('typing into each input calls the matching setter', () => {
    const hc = mkHc();
    render(<HistoricalCreditBanner enabled={true} blocked={false} daysBack={10} hc={hc} />);

    fireEvent.change(screen.getByTestId('historical-credit-reason-input'), {
      target: { value: 'New reason here' },
    });
    expect(hc.setReason).toHaveBeenCalledWith('New reason here');

    fireEvent.change(screen.getByTestId('historical-credit-proof-url-input'), {
      target: { value: 'https://photo' },
    });
    expect(hc.setProofUrl).toHaveBeenCalledWith('https://photo');

    fireEvent.change(screen.getByTestId('historical-credit-notebook-ref-input'), {
      target: { value: 'Ledger 99' },
    });
    expect(hc.setNotebookRef).toHaveBeenCalledWith('Ledger 99');
  });

  test('when both enabled AND blocked are true, both banners render', () => {
    render(
      <HistoricalCreditBanner enabled={true} blocked={true} daysBack={20} hc={mkHc()} />
    );
    expect(screen.getByTestId('backdated-non-credit-block')).toBeTruthy();
    expect(screen.getByTestId('historical-credit-banner')).toBeTruthy();
  });
});
