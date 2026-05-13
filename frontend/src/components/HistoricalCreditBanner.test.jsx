/**
 * HistoricalCreditBanner — RTL tests.
 *
 * Asserts the pure-rendering contract (post-iter-245 slim refactor —
 * inputs moved to HistoricalCreditDialog):
 *   • blocked=true     → only the red `backdated-non-credit-block` appears.
 *   • enabled=true     → only the amber notice strip `historical-credit-banner` appears.
 *   • both false       → nothing renders.
 *   • daysBack number  → echoed in the notice copy.
 *   • Banner is now a notice strip only — it must NOT render any of the
 *     three reason/proof/notebook inputs (those live in the dialog).
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import HistoricalCreditBanner from './HistoricalCreditBanner';

describe('HistoricalCreditBanner', () => {
  test('renders nothing when both enabled and blocked are false', () => {
    const { container } = render(
      <HistoricalCreditBanner enabled={false} blocked={false} daysBack={30} />
    );
    expect(container.innerHTML).toBe('');
  });

  test('renders the red backdated-non-credit-block when blocked=true', () => {
    render(
      <HistoricalCreditBanner enabled={false} blocked={true} daysBack={15} />
    );
    expect(screen.getByTestId('backdated-non-credit-block')).toBeTruthy();
    expect(screen.queryByTestId('historical-credit-banner')).toBeNull();
  });

  test('renders only the slim amber notice when enabled=true (no inputs)', () => {
    render(
      <HistoricalCreditBanner enabled={true} blocked={false} daysBack={42} />
    );
    const notice = screen.getByTestId('historical-credit-banner');
    expect(notice).toBeTruthy();
    expect(notice.textContent).toMatch(/42 days back/i);
    // Inputs no longer live in the banner — they're collected inside
    // HistoricalCreditDialog. The banner must NOT render them.
    expect(screen.queryByTestId('historical-credit-reason-input')).toBeNull();
    expect(screen.queryByTestId('historical-credit-proof-url-input')).toBeNull();
    expect(screen.queryByTestId('historical-credit-notebook-ref-input')).toBeNull();
  });

  test('when both enabled AND blocked are true, both banners render', () => {
    render(
      <HistoricalCreditBanner enabled={true} blocked={true} daysBack={20} />
    );
    expect(screen.getByTestId('backdated-non-credit-block')).toBeTruthy();
    expect(screen.getByTestId('historical-credit-banner')).toBeTruthy();
  });
});
