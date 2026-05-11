/**
 * useHistoricalCredit — Phase 4 Cleanup pass: extract the Historical
 * Credit / Notebook AR feature state + helpers out of UnifiedSalesPage.
 *
 * This hook is BEHAVIOR-PRESERVING. It owns:
 *   • The 12 HC state fields (preview, reason, proofUrl, notebookRef,
 *     allowInv, approvalCode, dialog open, committing, preview-loading,
 *     and the three error strings).
 *   • The HC-mode trigger flags (`isHistoricalCreditMode`,
 *     `isBackdatedNonCreditBlocked`) plus the `HISTORICAL_CREDIT_FLOOR_DAYS`
 *     constant.
 *   • The four helpers `buildItems`, `buildPayload`, `runPreview`,
 *     `commit`.
 *
 * The hook deliberately keeps the following OUT of its boundary:
 *   • `isPrivilegedRole` — used by date-picker widening and other
 *     non-HC code paths. Passed IN as an option.
 *   • `daysBack` — used by both HC and the date-error banner. Passed IN.
 *   • Cart, lines, customer, branch, header, totals — page-owned;
 *     passed IN through getter callbacks so we never capture stale
 *     closures.
 *   • Page-side cleanup (`clearCart`, reset header, close checkout
 *     dialog, etc.) — invoked through the REQUIRED `onCommitted`
 *     callback so the page stays in control of its own state.
 *
 * Reuses (does NOT recreate):
 *   • `contexts/AuthContext`            → `api` axios instance, plus the
 *                                          callers' `toast` import
 *   • `components/CustomerBalanceBadge` → `invalidateBalanceCache`
 *   • `lib/dateFormat`                  → `localTodayStr`
 *   • The backend Phase 4A endpoints
 *     `POST /api/historical-credit/preview`
 *     `POST /api/historical-credit`
 *
 * Server-side guards remain the only source of truth for approval rules
 * (Owner/Admin TOTP) — this hook never sees a secret and only passes
 * the typed code through.
 */
import { useState, useMemo, useCallback } from 'react';
import { toast } from 'sonner';
import { api } from '../contexts/AuthContext';
import { invalidateBalanceCache } from '../components/CustomerBalanceBadge';
import { localTodayStr } from './dateFormat';

export const HISTORICAL_CREDIT_FLOOR_DAYS = 7;
const REASON_MIN_CHARS = 20;

/**
 * @param {object} opts
 * @param {number} opts.daysBack            Days the order_date is in the past (0+).
 *                                          Caller computes this once for the page.
 * @param {'cash'|'credit'|'partial'|'digital'|'split'} opts.paymentType
 * @param {boolean} opts.isPrivilegedRole   Admin/owner/super_admin? Same value
 *                                          the page uses for date-picker widening.
 * @param {() => Array}  opts.getItems      Returns the cart in the shape the
 *                                          backend expects (page-owned mapper).
 * @param {() => {customer_id, branch_id, transaction_date, subtotal, freight,
 *               overall_discount, grand_total}} opts.getContext
 *                                          Returns the non-item payload pieces.
 * @param {() => boolean} opts.hasCustomer  True when a customer is selected.
 * @param {(result: {invoice: object}) => void} opts.onCommitted
 *                                          REQUIRED. Page-side cleanup after a
 *                                          successful commit (clearCart, reset
 *                                          header to today, close any checkout
 *                                          dialog, etc.). Errors thrown from
 *                                          this callback are swallowed.
 */
export function useHistoricalCredit({
  daysBack = 0,
  paymentType = 'cash',
  isPrivilegedRole = false,
  getItems,
  getContext,
  hasCustomer,
  onCommitted,
} = {}) {
  // ── State ────────────────────────────────────────────────────────
  const [reason, setReason] = useState('');
  const [proofUrl, setProofUrl] = useState('');
  const [notebookRef, setNotebookRef] = useState('');
  const [allowInv, setAllowInv] = useState(false);
  const [approvalCode, setApprovalCode] = useState('');
  const [dialogOpen, setDialogOpen] = useState(false);
  const [preview, setPreview] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState('');
  const [committing, setCommitting] = useState(false);
  const [commitError, setCommitError] = useState('');

  // ── Trigger flags (memoised — same conditions as before extraction) ─
  const isMode = useMemo(() => (
    daysBack > HISTORICAL_CREDIT_FLOOR_DAYS
    && paymentType === 'credit'
    && !!isPrivilegedRole
  ), [daysBack, paymentType, isPrivilegedRole]);

  const isBackdatedNonCreditBlocked = useMemo(() => (
    daysBack > HISTORICAL_CREDIT_FLOOR_DAYS && paymentType !== 'credit'
  ), [daysBack, paymentType]);

  // ── Payload builder ──────────────────────────────────────────────
  // Approval code is NEVER attached on preview — only on commit.
  const buildPayload = useCallback(({ withApprovalCode = false } = {}) => {
    const ctx = typeof getContext === 'function' ? (getContext() || {}) : {};
    const items = typeof getItems === 'function' ? (getItems() || []) : [];
    const base = {
      customer_id: ctx.customer_id || '',
      branch_id: ctx.branch_id || '',
      transaction_date: ctx.transaction_date || '',
      items,
      subtotal: ctx.subtotal,
      freight: ctx.freight,
      overall_discount: ctx.overall_discount,
      grand_total: ctx.grand_total,
      reason: reason.trim(),
      proof_url: proofUrl.trim() || undefined,
      notebook_reference: notebookRef.trim() || undefined,
      allow_inventory_deduction: !!allowInv,
    };
    if (withApprovalCode) base.approval_code = approvalCode.trim();
    return base;
  }, [getContext, getItems, reason, proofUrl, notebookRef, allowInv, approvalCode]);

  // ── Preview ──────────────────────────────────────────────────────
  const runPreview = useCallback(async () => {
    setPreviewError('');
    setPreview(null);
    if (typeof hasCustomer === 'function' ? !hasCustomer() : false) {
      setPreviewError('Select a customer before preview.');
      return;
    }
    if (reason.trim().length < REASON_MIN_CHARS) {
      setPreviewError(`Reason must be at least ${REASON_MIN_CHARS} characters.`);
      return;
    }
    setPreviewLoading(true);
    try {
      const res = await api.post('/historical-credit/preview', buildPayload({ withApprovalCode: false }));
      setPreview(res.data);
    } catch (e) {
      const detail = e?.response?.data?.detail;
      if (detail && typeof detail === 'object') {
        if (Array.isArray(detail.errors)) setPreviewError(detail.errors.join(' • '));
        else if (detail.message) setPreviewError(detail.message);
        else setPreviewError(JSON.stringify(detail));
      } else {
        setPreviewError(detail || e?.message || 'Preview failed');
      }
    } finally {
      setPreviewLoading(false);
    }
  }, [hasCustomer, reason, buildPayload]);

  // ── Commit ───────────────────────────────────────────────────────
  const commit = useCallback(async () => {
    setCommitError('');
    if (!preview) {
      setCommitError('Run preview first.');
      return;
    }
    if (!approvalCode.trim()) {
      setCommitError('Owner / Admin Authenticator (TOTP) code is required.');
      return;
    }
    setCommitting(true);
    try {
      const res = await api.post('/historical-credit', buildPayload({ withApprovalCode: true }));
      const inv = res.data?.invoice || {};
      const invoiceNum = inv.invoice_number || res.data?.invoice_number || '(unknown)';
      invalidateBalanceCache();
      toast.success(`Historical credit recorded — Invoice ${invoiceNum}`, { duration: 5000 });
      // Reset hook-owned state.
      setDialogOpen(false);
      setPreview(null);
      setReason('');
      setProofUrl('');
      setNotebookRef('');
      setAllowInv(false);
      setApprovalCode('');
      setCommitError('');
      // Let the page perform its own cleanup (clearCart, reset header,
      // close the checkout dialog, etc.).
      if (typeof onCommitted === 'function') {
        try { onCommitted(res.data || {}); } catch { /* swallow */ }
      }
    } catch (e) {
      const detail = e?.response?.data?.detail;
      const status = e?.response?.status;
      if (detail && typeof detail === 'object') {
        if (detail.error === 'approval_code_required') {
          setCommitError('Approval code required. Ask the company owner / admin to enter their Authenticator App (TOTP) code.');
        } else if (detail.error === 'approval_invalid') {
          setCommitError('Invalid or unauthorized approval code. Only Owner / Admin Authenticator App (TOTP) codes are accepted for Historical Credit.');
        } else if (detail.error === 'use_regular_late_encode') {
          setCommitError(detail.message || 'Use the regular Sales late-encode path for dates within 7 days back.');
        } else if (Array.isArray(detail.errors)) {
          setCommitError(detail.errors.join(' • '));
        } else if (detail.message) {
          setCommitError(detail.message);
        } else {
          setCommitError(JSON.stringify(detail));
        }
      } else {
        setCommitError(
          (typeof detail === 'string' ? detail : null)
          || (status === 403 ? 'Approval rejected (403). Owner / Admin TOTP required.' : null)
          || e?.message
          || 'Commit failed.',
        );
      }
    } finally {
      setCommitting(false);
    }
  }, [preview, approvalCode, buildPayload, onCommitted]);

  // Reset-on-open: the caller invokes this from `handleCreditSale` to
  // clear any stale preview/error/code before opening the dialog. The
  // user-typed reason/proof/notebook are preserved (the cashier may
  // re-open without re-typing).
  const openDialog = useCallback(() => {
    setPreview(null);
    setPreviewError('');
    setCommitError('');
    setApprovalCode('');
    setDialogOpen(true);
  }, []);

  // Reset-on-close: invoked when the user dismisses the dialog without
  // committing. Mirrors the inline behaviour the page used to do
  // directly. Preserves user-typed reason/proof/notebook so a re-open
  // doesn't lose them.
  const closeDialog = useCallback(() => {
    setDialogOpen(false);
    setPreview(null);
    setPreviewError('');
    setCommitError('');
    setApprovalCode('');
  }, []);

  // Convenience consumed by the post-success path of the consumer page
  // (already covered inside `commit`; exposed for symmetry).
  const reset = useCallback(() => {
    setDialogOpen(false);
    setPreview(null);
    setReason('');
    setProofUrl('');
    setNotebookRef('');
    setAllowInv(false);
    setApprovalCode('');
    setCommitError('');
    setPreviewError('');
  }, []);

  // For tests that want to drive state directly (also used by the page
  // for legacy localToday() resets if needed).
  const today = localTodayStr;

  return {
    // flags
    isMode,
    isBackdatedNonCreditBlocked,
    floorDays: HISTORICAL_CREDIT_FLOOR_DAYS,
    // inputs
    reason, setReason,
    proofUrl, setProofUrl,
    notebookRef, setNotebookRef,
    allowInv, setAllowInv,
    approvalCode, setApprovalCode,
    // dialog
    dialogOpen, setDialogOpen,
    openDialog, closeDialog,
    // preview
    preview, previewLoading, previewError, setPreview,
    runPreview,
    // commit
    committing, commitError,
    commit,
    // misc
    reset,
    today,
  };
}
