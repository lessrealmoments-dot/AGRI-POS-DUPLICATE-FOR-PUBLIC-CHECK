/**
 * ReviewDetailDialog — Shared review dialog for POs, Branch Transfers, and Expenses.
 * Phase 1: Balance fix + wallet balances
 * Phase 2: "Verify & Approve" button with PIN policy (no files.length gate for POs)
 *
 * Props:
 *   open, onClose, recordType, recordId, recordNumber
 *   showReviewAction   — show Verify & Approve section (both AP and Pending Reviews)
 *   showPayAction      — show Pay Now panel (AP widget only, Phase 3)
 *   onReviewed         — callback after successful review
 */
import { useState, useEffect } from 'react';
import { api } from '../contexts/AuthContext';
import { useNavigate } from 'react-router-dom';
import { formatPHP, fmtDate } from '../lib/utils';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from './ui/dialog';
import { Input } from './ui/input';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './ui/table';
import ViewQRDialog from './ViewQRDialog';
import {
  FileCheck, Receipt, CheckCircle2, RefreshCw, Camera, Building2, Clock,
  AlertTriangle, Shield, Package, User, CalendarDays, ShieldCheck,
  ExternalLink, QrCode, ChevronDown, ChevronUp, XCircle,
  Banknote, Wallet, Smartphone, Lock, CreditCard
} from 'lucide-react';
import { localTodayStr } from '../lib/dateFormat';
import { toast } from 'sonner';
import UploadQRDialog from './UploadQRDialog';
import CalcInput from './CalcInput';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

// Allowed PIN methods for the two review policy keys
const PO_VERIFY_METHODS = 'Admin PIN, Manager PIN, TOTP, or Auditor PIN';
const OTHER_VERIFY_METHODS = 'Admin PIN, Manager PIN, TOTP, or Auditor PIN';

export default function ReviewDetailDialog({
  open, onClose, recordType, recordId, recordNumber,
  showReviewAction = true,
  showPayAction = false,   // Phase 3 — Pay Now (only from AP widget)
  onReviewed,
  // ── Backward-compat aliases (Phase 1 migration from PODetailModal) ──────────
  poId,           // alias for recordId when recordType='purchase_order'
  poNumber,       // resolve UUID by PO number when recordId/poId not available
  onUpdated,      // alias for onReviewed
  onOpenChange,   // alias for (v) => { if (!v) onClose(); }
}) {
  const navigate = useNavigate();
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(false);

  // Resolve backward-compat aliases
  const effectiveClose = onClose || (onOpenChange ? () => onOpenChange(false) : undefined);
  const effectiveReviewed = onReviewed || onUpdated;

  // Resolve recordId: direct > poId alias > resolved from poNumber
  const [resolvedId, setResolvedId] = useState(null);
  const [resolvedType, setResolvedType] = useState(null);

  useEffect(() => {
    if (!open) return;
    if (recordId && recordType) {
      setResolvedId(recordId);
      setResolvedType(recordType);
      return;
    }
    if (poId) {
      setResolvedId(poId);
      setResolvedType('purchase_order');
      return;
    }
    if (poNumber) {
      // Resolve PO UUID by number
      api.get(`/invoices/by-number/${encodeURIComponent(poNumber)}`)
        .then(r => {
          setResolvedId(r.data.id);
          setResolvedType('purchase_order');
        })
        .catch(() => toast.error('PO not found'));
    }
  }, [open, recordId, recordType, poId, poNumber]);

  // Clear resolved state on close
  useEffect(() => {
    if (!open) { setResolvedId(null); setResolvedType(null); }
  }, [open]);

  // Verify section state
  const [verifyExpanded, setVerifyExpanded] = useState(false);
  const [reviewPin, setReviewPin] = useState('');
  const [reviewNotes, setReviewNotes] = useState('');
  const [reviewSaving, setReviewSaving] = useState(false);

  // QR viewer
  const [viewQROpen, setViewQROpen] = useState(false);

  // Pay Now state (Phase 3 — only when showPayAction=true)
  const [payExpanded, setPayExpanded] = useState(false);
  const [payAmount, setPayAmount] = useState('');
  const [payFundSource, setPayFundSource] = useState('cashier');
  const [payMethod, setPayMethod] = useState('Cash');
  const [payRef, setPayRef] = useState('');
  const [payDate, setPayDate] = useState(localTodayStr());
  const [payPin, setPayPin] = useState('');
  const [payProcessing, setPayProcessing] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false); // receipt upload after payment

  useEffect(() => {
    if (open && resolvedType && resolvedId) {
      setLoading(true);
      setDetail(null);
      setReviewPin('');
      setReviewNotes('');
      setVerifyExpanded(false);
      api.get(`/dashboard/review-detail/${resolvedType}/${resolvedId}`)
        .then(res => {
          setDetail(res.data);
          setPayAmount((res.data.balance || 0).toFixed(2));
          setPayDate(localTodayStr());
        })
        .catch(() => toast.error('Failed to load record details'))
        .finally(() => setLoading(false));
    }
  }, [open, resolvedType, resolvedId]);

  const handleReview = async () => {
    if (!reviewPin) { toast.error('Enter PIN or TOTP code'); return; }
    setReviewSaving(true);
    try {
      let endpoint;
      if (recordType === 'purchase_order') {
        endpoint = `/purchase-orders/${resolvedId}/mark-reviewed`;
      } else {
        endpoint = `/uploads/mark-reviewed/${resolvedType}/${resolvedId}`;
      }
      const res = await api.post(endpoint, { pin: reviewPin, notes: reviewNotes });
      toast.success(res.data.message || 'Verified & approved');
      setDetail(prev => prev ? { ...prev, receipt_review_status: 'reviewed' } : prev);
      setReviewPin('');
      setVerifyExpanded(false);
      if (effectiveReviewed) effectiveReviewed();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Verification failed');
    }
    setReviewSaving(false);
  };

  const handlePayNow = async () => {
    if (!payPin) { toast.error('PIN or TOTP is required'); return; }
    const amount = parseFloat(payAmount);
    if (!amount || amount <= 0) { toast.error('Enter a valid payment amount'); return; }
    setPayProcessing(true);
    try {
      const res = await api.post(`/purchase-orders/${resolvedId}/pay`, {
        amount,
        fund_source: payFundSource,
        method: payMethod,
        reference: payRef,
        payment_date: payDate,
        pin: payPin,
      });
      toast.success(res.data.message || `Payment recorded`);
      setPayExpanded(false);
      setPayPin('');
      setPayRef('');
      setUploadOpen(true); // auto-open receipt upload
      const updated = await api.get(`/dashboard/review-detail/${resolvedType}/${resolvedId}`);
      setDetail(updated.data);
      setPayAmount((updated.data.balance || 0).toFixed(2));
      if (effectiveReviewed) effectiveReviewed();
    } catch (e) {
      const detail = e.response?.data?.detail;
      if (typeof detail === 'object' && detail?.type === 'insufficient_funds') {
        toast.error(detail.message);
      } else {
        toast.error(typeof detail === 'string' ? detail : 'Payment failed');
      }
    }
    setPayProcessing(false);
  };

  const goToFullPage = () => {
    if (effectiveClose) effectiveClose();
    if (resolvedType === 'purchase_order') navigate(`/purchase-orders?open=${resolvedId}`);
    else if (resolvedType === 'branch_transfer') navigate(`/branch-transfers?tab=history&view=${resolvedId}`);
    else navigate('/accounting');
  };

  const d = detail;
  const files = d?.receipt_files || [];
  const isReviewed = d?.receipt_review_status === 'reviewed';
  const title = d?.record_number || recordNumber || 'Loading...';

  // For POs: verify button shows regardless of files
  // For other types: only when files exist (original behavior)
  const showVerifySection = showReviewAction && !isReviewed && (
    resolvedType === 'purchase_order' ? true : files.length > 0
  );

  return (
    <>
      <Dialog open={open && (!!resolvedId || loading)} onOpenChange={v => { if (!v && effectiveClose) effectiveClose(); }}>
        <DialogContent className="sm:max-w-2xl max-h-[90vh] flex flex-col overflow-hidden p-0" data-testid="review-detail-dialog">
          <DialogHeader className="px-6 pt-6 pb-0">
            <DialogTitle className="flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
              <FileCheck size={18} className="text-[#1A4D2E]" />
              {title}
            </DialogTitle>
          </DialogHeader>

          <div className="flex-1 min-h-0 overflow-y-auto px-6 py-4">
            <div className="space-y-4 pb-2">
              {loading ? (
                <div className="flex items-center justify-center py-8 text-slate-400 text-sm">
                  <RefreshCw size={14} className="animate-spin mr-2" /> Loading details...
                </div>
              ) : d ? (
                <>
                  {/* ── Purchase Order Header ── */}
                  {d.record_type === 'purchase_order' && (
                    <div className="bg-blue-50 border border-blue-200 rounded-xl p-4 space-y-2">
                      <div className="flex items-start justify-between">
                        <div>
                          <p className="text-sm font-bold text-slate-800">{d.record_number}</p>
                          <div className="flex items-center gap-1.5 mt-1">
                            <User size={11} className="text-blue-600" />
                            <span className="text-xs font-semibold text-blue-800">{d.supplier}</span>
                            {d.supplier_contact && <span className="text-[10px] text-slate-400">({d.supplier_contact})</span>}
                          </div>
                        </div>
                        <div className="text-right">
                          <p className="text-lg font-bold font-mono text-slate-800">{formatPHP(d.grand_total)}</p>
                          <Badge className={`text-[9px] ${
                            d.payment_status === 'paid' ? 'bg-emerald-100 text-emerald-700' :
                            d.payment_status === 'partial' ? 'bg-amber-100 text-amber-700' :
                            'bg-red-100 text-red-700'
                          }`}>
                            {d.payment_status === 'paid' ? 'Paid' :
                             d.payment_status === 'partial' ? `Partial — ${formatPHP(d.balance)} remaining` :
                             d.balance > 0 ? `Unpaid — ${formatPHP(d.balance)} due` : 'Unpaid'}
                          </Badge>
                        </div>
                      </div>
                      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-slate-500 pt-1 border-t border-blue-100">
                        <span className="flex items-center gap-1"><Building2 size={10} /> {d.branch_name}</span>
                        <span className="flex items-center gap-1"><CalendarDays size={10} /> Date: {d.date?.slice(0, 10)}</span>
                        {d.due_date && <span className="flex items-center gap-1"><Clock size={10} /> Due: {d.due_date.slice(0, 10)}</span>}
                        <span>Status: <Badge className="text-[9px] bg-slate-200 text-slate-600">{d.status}</Badge></span>
                        {d.received_by && <span>Received by: {d.received_by}</span>}
                        {d.created_by && <span>Created by: {d.created_by}</span>}
                      </div>
                      {d.verified && (
                        <div className="flex items-center gap-1.5 text-[10px] text-emerald-600 bg-emerald-50 rounded-lg px-2 py-1">
                          <ShieldCheck size={11} /> Verified by {d.verified_by} {d.verified_at ? `on ${fmtDate(d.verified_at)}` : ''}
                        </div>
                      )}
                      {isReviewed && (
                        <div className="flex items-center gap-1.5 text-[10px] text-emerald-600 bg-emerald-50 rounded-lg px-2 py-1">
                          <CheckCircle2 size={11} /> Approved &amp; reviewed{d.receipt_reviewed_by ? ` by ${d.receipt_reviewed_by}` : ''}
                        </div>
                      )}
                    </div>
                  )}

                  {/* ── Branch Transfer Header ── */}
                  {d.record_type === 'branch_transfer' && (
                    <div className="bg-emerald-50 border border-emerald-200 rounded-xl p-4 space-y-2">
                      <div className="flex items-start justify-between">
                        <div>
                          <p className="text-sm font-bold text-slate-800">{d.record_number}</p>
                          {d.invoice_number && <p className="text-[10px] text-blue-600 font-mono">{d.invoice_number}</p>}
                          <div className="flex items-center gap-1.5 mt-1 text-xs text-emerald-800">
                            <Building2 size={11} /> {d.from_branch} <span className="text-slate-400">→</span> {d.to_branch}
                          </div>
                        </div>
                        <div className="text-right">
                          <p className="text-lg font-bold font-mono text-slate-800">{formatPHP(d.grand_total)}</p>
                          {d.retail_total > 0 && <p className="text-[10px] text-slate-400">Retail: {formatPHP(d.retail_total)}</p>}
                        </div>
                      </div>
                      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-slate-500 pt-1 border-t border-emerald-100">
                        <span>Status: <Badge className="text-[9px] bg-slate-200 text-slate-600">{d.status}</Badge></span>
                        {d.sent_at && <span>Sent: {fmtDate(d.sent_at)}</span>}
                        {d.received_at && <span>Received: {fmtDate(d.received_at)}</span>}
                        {d.received_by && <span>By: {d.received_by}</span>}
                        {d.has_shortage && <Badge className="text-[9px] bg-red-100 text-red-700">Shortage</Badge>}
                      </div>
                      {isReviewed && (
                        <div className="flex items-center gap-1.5 text-[10px] text-emerald-600 bg-white rounded-lg px-2 py-1">
                          <CheckCircle2 size={11} /> Receipts reviewed
                        </div>
                      )}
                    </div>
                  )}

                  {/* ── Expense Header ── */}
                  {d.record_type === 'expense' && (
                    <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 space-y-2">
                      <div className="flex items-start justify-between">
                        <div>
                          <p className="text-sm font-bold text-slate-800">{d.category}</p>
                          <p className="text-xs text-slate-600">{d.description}</p>
                          {d.vendor && <p className="text-[10px] text-slate-500 mt-0.5">Payee: {d.vendor}</p>}
                        </div>
                        <p className="text-lg font-bold font-mono text-slate-800">{formatPHP(d.grand_total)}</p>
                      </div>
                      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-slate-500 pt-1 border-t border-amber-100">
                        <span className="flex items-center gap-1"><Building2 size={10} /> {d.branch_name}</span>
                        <span className="flex items-center gap-1"><CalendarDays size={10} /> {d.date?.slice(0, 10)}</span>
                        {d.payment_method && <span>Method: {d.payment_method}</span>}
                        {d.created_by && <span>By: {d.created_by}</span>}
                      </div>
                      {isReviewed && (
                        <div className="flex items-center gap-1.5 text-[10px] text-emerald-600 bg-emerald-50 rounded-lg px-2 py-1">
                          <CheckCircle2 size={11} /> Receipts reviewed
                        </div>
                      )}
                    </div>
                  )}

                  {/* ── Item Breakdown ── */}
                  {d.items?.length > 0 && (
                    <div className="bg-white border rounded-xl overflow-hidden">
                      <div className="px-3 py-2 bg-slate-50 border-b flex items-center gap-2">
                        <Package size={13} className="text-slate-500" />
                        <span className="text-xs font-semibold text-slate-700">Items ({d.items.length})</span>
                      </div>
                      <Table>
                        <TableHeader>
                          <TableRow className="bg-slate-50/50">
                            <TableHead className="text-[10px] uppercase text-slate-500">Product</TableHead>
                            <TableHead className="text-[10px] uppercase text-slate-500 text-right">Qty</TableHead>
                            <TableHead className="text-[10px] uppercase text-slate-500 text-right">Price</TableHead>
                            <TableHead className="text-[10px] uppercase text-slate-500 text-right">Total</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {d.items.map((item, idx) => (
                            <TableRow key={idx} className="hover:bg-slate-50/50">
                              <TableCell className="text-xs">
                                <span className="font-medium">{item.product_name}</span>
                                {item.sku && <span className="text-[10px] text-slate-400 ml-1">({item.sku})</span>}
                              </TableCell>
                              <TableCell className="text-xs text-right font-mono">
                                {item.quantity}{item.unit ? ` ${item.unit}` : ''}
                                {item.qty_received !== undefined && item.qty_received !== item.quantity && (
                                  <span className={`text-[10px] ml-1 ${item.qty_received < item.quantity ? 'text-red-500' : 'text-blue-500'}`}>
                                    (rcvd: {item.qty_received})
                                  </span>
                                )}
                              </TableCell>
                              <TableCell className="text-xs text-right font-mono">{formatPHP(item.unit_price || item.transfer_capital || 0)}</TableCell>
                              <TableCell className="text-xs text-right font-mono font-semibold">{formatPHP(item.total || (item.quantity * (item.unit_price || item.transfer_capital || 0)))}</TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                  )}

                  {/* ── Payment History (PO only) ── */}
                  {d.record_type === 'purchase_order' && (d.payment_history || []).length > 0 && (
                    <div className="bg-white border rounded-xl overflow-hidden">
                      <div className="px-3 py-2 bg-slate-50 border-b flex items-center gap-2">
                        <Receipt size={13} className="text-slate-500" />
                        <span className="text-xs font-semibold text-slate-700">Payment History ({d.payment_history.length})</span>
                      </div>
                      <div className="divide-y divide-slate-50">
                        {d.payment_history.map((p, idx) => (
                          <div key={idx} className="flex justify-between items-center px-3 py-2 text-xs">
                            <div>
                              <span className="font-semibold text-slate-700">{p.method || 'Cash'}</span>
                              <span className="text-slate-400 ml-2">from {p.fund_source || 'cashier'}</span>
                              {p.reference && <span className="text-slate-400 ml-1">· ref: {p.reference}</span>}
                              <p className="text-[10px] text-slate-400">{p.date} · {p.recorded_by}</p>
                            </div>
                            <span className="font-bold font-mono text-emerald-700">{formatPHP(p.amount)}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* ── Receipt Photos ── */}
                  {files.length > 0 && (
                    <div className="bg-white border rounded-xl overflow-hidden">
                      <div className="px-3 py-2 bg-slate-50 border-b flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <Camera size={13} className="text-slate-500" />
                          <span className="text-xs font-semibold text-slate-700">Receipt Photos ({files.length})</span>
                        </div>
                        <Button variant="outline" size="sm" className="h-6 text-[10px] px-2"
                          onClick={() => setViewQROpen(true)} data-testid="review-view-phone-btn">
                          <QrCode size={10} className="mr-1" /> View on Phone
                        </Button>
                      </div>

                      {/* Collection receipt notice — shown when any file was shared from another PO */}
                      {d.all_receipts_shared && files[0]?.shared_from_po_number && (
                        <div className="flex items-start gap-2 px-3 py-2 bg-blue-50 border-b border-blue-100 text-[10px] text-blue-700"
                          data-testid="shared-receipt-notice">
                          <Receipt size={11} className="mt-0.5 shrink-0 text-blue-500" />
                          <div>
                            <span className="font-semibold">Collection receipt</span>
                            {' · '}shared from{' '}
                            <span className="font-mono font-semibold">{files[0].shared_from_po_number}</span>
                            {files[0].shared_from_vendor && (
                              <span className="text-blue-500"> ({files[0].shared_from_vendor})</span>
                            )}
                            {' · '}This receipt covers multiple POs paid together.
                          </div>
                        </div>
                      )}
                      {!d.all_receipts_shared && files.some(f => f.is_shared) && (
                        <div className="flex items-start gap-2 px-3 py-2 bg-blue-50 border-b border-blue-100 text-[10px] text-blue-700"
                          data-testid="shared-receipt-notice">
                          <Receipt size={11} className="mt-0.5 shrink-0 text-blue-500" />
                          Some photos are from a collection receipt shared across multiple POs.
                        </div>
                      )}

                      <div className="p-3 flex flex-wrap gap-2">
                        {files.map((f, i) => {
                          const isImage = (f.content_type || '').startsWith('image/');
                          const url = `${BACKEND_URL}/api/uploads/file/${resolvedType}/${resolvedId}/${f.id}`;
                          return (
                            <div key={f.id || i} className="relative">
                              <a href={url} target="_blank" rel="noopener noreferrer"
                                className="block rounded-lg border border-slate-200 overflow-hidden hover:shadow-md transition-shadow">
                                {isImage ? (
                                  <img src={url} alt={f.filename} className="w-24 h-24 object-cover" />
                                ) : (
                                  <div className="w-24 h-24 bg-slate-50 flex flex-col items-center justify-center">
                                    <Receipt size={18} className="text-slate-400" />
                                    <span className="text-[8px] text-slate-400 mt-1 truncate max-w-[80px]">{f.filename}</span>
                                  </div>
                                )}
                              </a>
                              {/* Shared badge on individual photo */}
                              {f.is_shared && (
                                <div className="absolute bottom-0.5 left-0.5 right-0.5 bg-blue-600/80 rounded-b text-[7px] text-white text-center py-0.5 font-medium truncate px-1">
                                  {f.shared_from_po_number || 'Shared'}
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  {/* ── Verify & Approve Section ── */}
                  {showVerifySection && (
                    <div className="rounded-xl border border-amber-200 overflow-hidden">
                      {/* Collapsed: Show button */}
                      {!verifyExpanded ? (
                        <button
                          onClick={() => setVerifyExpanded(true)}
                          className="w-full flex items-center justify-between px-4 py-3 bg-amber-50 hover:bg-amber-100 transition-colors text-left"
                          data-testid="verify-po-btn"
                        >
                          <div className="flex items-center gap-2">
                            <ShieldCheck size={15} className="text-amber-600" />
                            <div>
                              <p className="text-sm font-semibold text-amber-800">Verify &amp; Approve this PO</p>
                              <p className="text-[10px] text-amber-600">
                                {files.length > 0
                                  ? `${files.length} receipt photo${files.length > 1 ? 's' : ''} pending review`
                                  : 'Confirm goods received — no photos uploaded yet'}
                              </p>
                            </div>
                          </div>
                          <ChevronDown size={15} className="text-amber-500" />
                        </button>
                      ) : (
                        /* Expanded: Verify form */
                        <div className="bg-amber-50 p-4 space-y-3">
                          <div className="flex items-center justify-between">
                            <p className="text-xs font-semibold text-amber-800 flex items-center gap-1.5">
                              <ShieldCheck size={13} className="text-amber-600" />
                              Verify &amp; Approve
                            </p>
                            <button onClick={() => { setVerifyExpanded(false); setReviewPin(''); setReviewNotes(''); }}
                              className="text-slate-400 hover:text-slate-600">
                              <XCircle size={15} />
                            </button>
                          </div>

                          {files.length === 0 && (
                            <div className="flex items-start gap-2 bg-amber-100 border border-amber-300 rounded-lg px-3 py-2 text-[10px] text-amber-700">
                              <AlertTriangle size={11} className="mt-0.5 shrink-0" />
                              No receipt photos uploaded. You can still approve — or upload receipts first.
                            </div>
                          )}

                          <Input
                            value={reviewNotes}
                            onChange={e => setReviewNotes(e.target.value)}
                            placeholder="Verification notes (optional)"
                            className="h-9 text-sm bg-white"
                            data-testid="review-notes-input"
                          />

                          <div>
                            <label className="text-[10px] text-slate-600 font-medium flex items-center gap-1 mb-1">
                              <Shield size={10} className="text-amber-500" />
                              {resolvedType === 'purchase_order' ? PO_VERIFY_METHODS : OTHER_VERIFY_METHODS}
                            </label>
                            <Input
                              type="password"
                              autoComplete="new-password"
                              value={reviewPin}
                              onChange={e => setReviewPin(e.target.value)}
                              onKeyDown={e => { if (e.key === 'Enter') handleReview(); }}
                              placeholder="Enter PIN or TOTP"
                              className="h-9 text-sm font-mono bg-white"
                              data-testid="review-pin-input"
                            />
                          </div>

                          <div className="flex gap-2">
                            <Button
                              variant="outline"
                              size="sm"
                              className="flex-1"
                              onClick={() => { setVerifyExpanded(false); setReviewPin(''); setReviewNotes(''); }}
                            >
                              Cancel
                            </Button>
                            <Button
                              size="sm"
                              onClick={handleReview}
                              disabled={reviewSaving || !reviewPin}
                              className="flex-1 bg-[#1A4D2E] hover:bg-[#14532d] text-white"
                              data-testid="confirm-review-btn"
                            >
                              {reviewSaving
                                ? <RefreshCw size={12} className="animate-spin mr-1" />
                                : <CheckCircle2 size={12} className="mr-1" />}
                              Confirm Verification
                            </Button>
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Already reviewed indicator */}
                  {showReviewAction && isReviewed && (
                    <div className="flex items-center gap-2 text-xs text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-xl px-4 py-3" data-testid="already-reviewed">
                      <CheckCircle2 size={14} className="shrink-0" />
                      <div>
                        <p className="font-semibold">Verified &amp; Approved</p>
                        {d?.receipt_reviewed_by && <p className="text-[10px] text-emerald-600">by {d.receipt_reviewed_by}</p>}
                      </div>
                    </div>
                  )}

                  {/* Phase 3 placeholder — Pay Now panel will be inserted here */}

                  {/* ── Pay Now Panel (AP widget only) ── */}
                  {showPayAction && d.record_type === 'purchase_order' && (d.balance > 0) && (
                    <div className="rounded-xl border border-emerald-200 overflow-hidden" data-testid="pay-now-section">
                      {!payExpanded ? (
                        <button
                          onClick={() => setPayExpanded(true)}
                          className="w-full flex items-center justify-between px-4 py-3 bg-emerald-50 hover:bg-emerald-100 transition-colors text-left"
                          data-testid="pay-now-btn"
                        >
                          <div className="flex items-center gap-2">
                            <Banknote size={15} className="text-emerald-600" />
                            <div>
                              <p className="text-sm font-semibold text-emerald-800">Pay This PO</p>
                              <p className="text-[10px] text-emerald-600">Outstanding balance: {formatPHP(d.balance)}</p>
                            </div>
                          </div>
                          <ChevronDown size={15} className="text-emerald-500" />
                        </button>
                      ) : (
                        <div className="bg-emerald-50 p-4 space-y-3">
                          <div className="flex items-center justify-between">
                            <p className="text-xs font-semibold text-emerald-800 flex items-center gap-1.5">
                              <Banknote size={13} className="text-emerald-600" /> Pay {d.record_number}
                            </p>
                            <button onClick={() => { setPayExpanded(false); setPayPin(''); }} className="text-slate-400 hover:text-slate-600">
                              <XCircle size={15} />
                            </button>
                          </div>

                          {/* Fund Source */}
                          <div>
                            <p className="text-[10px] text-slate-600 font-medium mb-1.5">Pay From</p>
                            <div className="grid grid-cols-2 gap-1.5">
                              {[
                                { key: 'cashier', label: 'Cashier', icon: Wallet, lock: false },
                                { key: 'safe', label: 'Safe', icon: Shield, lock: false },
                                { key: 'bank', label: 'Bank', icon: Smartphone, lock: true },
                                { key: 'digital', label: 'Digital/GCash', icon: CreditCard, lock: true },
                              ].map(f => {
                                const wb = d.wallet_balances?.[f.key] || {};
                                const bal = wb.balance || 0;
                                const name = wb.name || f.label;
                                const insuff = bal < parseFloat(payAmount || 0);
                                return (
                                  <button key={f.key} onClick={() => setPayFundSource(f.key)}
                                    className={`flex items-center gap-2 px-2.5 py-2 rounded-lg border text-left transition-all ${payFundSource === f.key ? 'border-emerald-500 bg-white' : 'border-slate-200 bg-white hover:border-slate-300'}`}>
                                    <f.icon size={13} className={payFundSource === f.key ? 'text-emerald-600' : 'text-slate-400'} />
                                    <div className="flex-1 min-w-0">
                                      <div className="flex items-center gap-1">
                                        <span className="text-[10px] font-medium truncate">{name}</span>
                                        {f.lock && <Lock size={8} className="text-amber-500 shrink-0" />}
                                      </div>
                                      <span className={`text-[9px] font-bold ${insuff ? 'text-red-500' : 'text-slate-600'}`}>{formatPHP(bal)}</span>
                                    </div>
                                  </button>
                                );
                              })}
                            </div>
                            {(payFundSource === 'bank' || payFundSource === 'digital') && (
                              <p className="text-[9px] text-amber-600 flex items-center gap-1 mt-1">
                                <Lock size={8} /> Admin PIN or TOTP required for bank/digital
                              </p>
                            )}
                          </div>

                          {/* Amount + Method + Ref + Date */}
                          <div className="grid grid-cols-2 gap-2">
                            <div>
                              <label className="text-[10px] text-slate-500 font-medium">Amount</label>
                              <CalcInput value={payAmount}
 onChange={(v) => setPayAmount(v)}
 className="h-9 text-sm font-mono bg-white" data-testid="pay-amount-input" />
                            </div>
                            <div>
                              <label className="text-[10px] text-slate-500 font-medium">Method</label>
                              <select value={payMethod} onChange={e => setPayMethod(e.target.value)}
                                className="w-full h-9 rounded-md border border-input bg-white px-3 text-sm">
                                {['Cash', 'Check', 'Bank Transfer', 'GCash', 'Maya'].map(m => (
                                  <option key={m} value={m}>{m}</option>
                                ))}
                              </select>
                            </div>
                            <div>
                              <label className="text-[10px] text-slate-500 font-medium">
                                {payMethod === 'Check' ? 'Check #' : 'Reference #'}
                              </label>
                              <Input value={payRef} onChange={e => setPayRef(e.target.value)}
                                placeholder="Optional" className="h-9 text-sm bg-white" />
                            </div>
                            <div>
                              <label className="text-[10px] text-slate-500 font-medium">Date</label>
                              <Input type="date" value={payDate} onChange={e => setPayDate(e.target.value)}
                                className="h-9 text-sm bg-white" />
                            </div>
                          </div>

                          {/* PIN */}
                          <div>
                            <label className="text-[10px] text-slate-600 font-medium flex items-center gap-1 mb-1">
                              <Shield size={10} className="text-emerald-500" />
                              {(payFundSource === 'bank' || payFundSource === 'digital') ? 'Admin PIN or TOTP' : 'Manager PIN, Admin PIN, or TOTP'}
                            </label>
                            <Input type="password" autoComplete="new-password"
                              value={payPin} onChange={e => setPayPin(e.target.value)}
                              onKeyDown={e => { if (e.key === 'Enter') handlePayNow(); }}
                              placeholder="Enter PIN or TOTP"
                              className="h-9 text-sm font-mono bg-white"
                              data-testid="pay-pin-input" />
                          </div>

                          <div className="flex gap-2">
                            <Button variant="outline" size="sm" className="flex-1"
                              onClick={() => { setPayExpanded(false); setPayPin(''); }}>
                              Cancel
                            </Button>
                            <Button size="sm" className="flex-1 bg-emerald-700 hover:bg-emerald-800 text-white"
                              onClick={handlePayNow} disabled={payProcessing || !payPin}
                              data-testid="confirm-pay-btn">
                              {payProcessing ? <RefreshCw size={12} className="animate-spin mr-1" /> : <Banknote size={12} className="mr-1" />}
                              Pay {formatPHP(parseFloat(payAmount) || 0)}
                            </Button>
                          </div>
                          <p className="text-[9px] text-slate-400 text-center">Receipt upload will open after payment</p>
                        </div>
                      )}
                    </div>
                  )}
                </>
              ) : null}
            </div>
          </div>

          {/* ── Footer ── */}
          <div className="flex justify-between items-center pt-3 pb-4 border-t px-6 shrink-0">
            <Button variant="ghost" size="sm" onClick={goToFullPage} className="text-xs text-slate-500 hover:text-[#1A4D2E]">
              <ExternalLink size={12} className="mr-1" /> Open Full Page
            </Button>
            <Button variant="outline" size="sm" onClick={effectiveClose}>Close</Button>
          </div>
        </DialogContent>
      </Dialog>

      <ViewQRDialog
        open={viewQROpen}
        onClose={() => setViewQROpen(false)}
        recordType={resolvedType}
        recordId={resolvedId}
        fileCount={files.length}
      />

      {/* Receipt upload after payment — required for audit trail */}
      <UploadQRDialog
        open={uploadOpen}
        onClose={(count) => {
          setUploadOpen(false);
          if (count > 0) {
            toast.success(`${count} receipt photo(s) saved`);
            if (resolvedType && resolvedId) {
              api.get(`/dashboard/review-detail/${resolvedType}/${resolvedId}`)
                .then(res => setDetail(res.data))
                .catch(() => {});
            }
          } else {
            toast.info('Receipt upload skipped — remember to upload for audit trail');
          }
        }}
        recordType="purchase_order"
        recordId={resolvedId}
      />
    </>
  );
}
