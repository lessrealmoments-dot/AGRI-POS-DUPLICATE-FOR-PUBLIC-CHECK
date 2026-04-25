import { useState, useEffect } from 'react';
import { api, useAuth } from '../contexts/AuthContext';
import { formatPHP } from '../lib/utils';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from './ui/dialog';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Badge } from './ui/badge';
import { Textarea } from './ui/textarea';
import { Separator } from './ui/separator';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './ui/table';
import { ScrollArea } from './ui/scroll-area';
import { Card, CardContent } from './ui/card';
import ReceiptGallery from './ReceiptGallery';
import UploadQRDialog from './UploadQRDialog';
import ViewQRDialog from './ViewQRDialog';
import VerificationBadge from './VerificationBadge';
import VerifyPinDialog from './VerifyPinDialog';
import PrintEngine from '../lib/PrintEngine';
import {
  FileText, Edit3, History, Save, X, AlertTriangle, Package,
  User, Calendar, DollarSign, Trash2, Clock, CheckCircle2,
  Copy, Check, ShieldCheck, Ban, ImageIcon, Upload, Smartphone,
  Truck, CreditCard, Wallet, Pencil, RefreshCw, Printer
} from 'lucide-react';
import { toast } from 'sonner';

export default function InvoiceDetailModal({
  open, onOpenChange, invoiceId, invoiceNumber, expenseId, onUpdated,
  saleId, compact = false
}) {
  // saleId is a backward-compat alias for invoiceId (Phase 2 consolidation)
  const resolvedInvoiceId = invoiceId || saleId;

  const { hasPerm, user, currentBranch } = useAuth();
  const [invoice, setInvoice] = useState(null);
  const [loading, setLoading] = useState(true);
  const [editMode, setEditMode] = useState(false);
  const [editHistory, setEditHistory] = useState([]);
  const [showHistory, setShowHistory] = useState(false);
  const [saving, setSaving] = useState(false);
  const [copied, setCopied] = useState(false);
  const [businessInfo, setBusinessInfo] = useState({});

  // Edit form state
  const [editData, setEditData] = useState({ items: [], customer_name: '', notes: '', freight: 0, overall_discount: 0 });
  const [editReason, setEditReason] = useState('');
  const [proofFile, setProofFile] = useState(null);

  // Action dialogs
  const [voidOpen, setVoidOpen] = useState(false);
  const [voidReason, setVoidReason] = useState('');
  const [voidPin, setVoidPin] = useState('');
  const [verifyOpen, setVerifyOpen] = useState(false);
  const [verifyPin, setVerifyPin] = useState('');
  const [actionLoading, setActionLoading] = useState(false);

  // Compact-mode edit state
  const [editOrderDate, setEditOrderDate] = useState('');
  const [editDateClosed, setEditDateClosed] = useState(false);

  // External verify dialog (compact mode)
  const [verifyDialogOpen, setVerifyDialogOpen] = useState(false);

  // QR dialogs
  const [uploadQROpen, setUploadQROpen] = useState(false);
  const [viewQROpen, setViewQROpen] = useState(false);

  // Active section
  const [section, setSection] = useState('detail'); // detail | receipts | payments | history | signature

  // Signature state
  const [signatures, setSignatures] = useState([]);
  const [sigLoading, setSigLoading] = useState(false);

  // Load business info for printing (compact mode)
  useEffect(() => {
    if (compact) api.get('/settings/business-info').then(r => setBusinessInfo(r.data)).catch(() => {});
  }, [compact]);

  useEffect(() => {
    if (open && (resolvedInvoiceId || invoiceNumber || expenseId)) {
      loadInvoice();
      setSection('detail');
      setEditMode(false);
      setShowHistory(false);
    }
  // eslint-disable-next-line
  }, [open, resolvedInvoiceId, invoiceNumber, expenseId]);

  const loadInvoice = async () => {
    setLoading(true);
    try {
      let res;
      if (expenseId) {
        res = await api.get(`/expenses/${expenseId}`);
        res.data._collection = 'expenses';
      } else if (resolvedInvoiceId) {
        res = await api.get(`/invoices/${resolvedInvoiceId}`);
      } else {
        res = await api.get(`/invoices/by-number/${encodeURIComponent(invoiceNumber)}`);
      }
      setInvoice(res.data);
      setEditData({
        items: JSON.parse(JSON.stringify(res.data.items || [])),
        customer_name: res.data.customer_name || '',
        notes: res.data.notes || res.data.description || '',
        freight: res.data.freight || 0,
        overall_discount: res.data.overall_discount || 0,
      });
      setEditHistory(res.data.edit_history || []);

      // Load signatures for this invoice
      const invId = res.data.id;
      if (invId) {
        setSigLoading(true);
        api.get(`/signatures/record/invoice/${invId}`)
          .then(r => setSignatures(r.data || []))
          .catch(() => setSignatures([]))
          .finally(() => setSigLoading(false));
      }
    } catch (e) {
      toast.error('Failed to load details');
      onOpenChange(false);
    }
    setLoading(false);
  };

  // ── Derived state ──────────────────────────────────────────────────────
  const isPO = invoice?._collection === 'purchase_orders';
  const isExpense = invoice?._collection === 'expenses';
  const isInvoice = !isPO && !isExpense;
  const docNumber = invoice?.invoice_number || invoice?.sale_number || invoice?.po_number || invoice?.reference_number || invoice?.linked_invoice_number || '';
  const docName = isPO ? (invoice?.vendor || '') : isExpense ? (invoice?.description || invoice?.category || '') : (invoice?.customer_name || 'Walk-in');
  const docDate = invoice?.order_date || invoice?.purchase_date || invoice?.date || invoice?.created_at?.slice(0, 10) || '';
  const isVerified = invoice?.verified === true;
  const isVoided = invoice?.status === 'voided' || invoice?.status === 'cancelled';
  const payments = invoice?.payments || [];
  const recordType = isPO ? 'purchase_order' : isExpense ? 'expense' : 'invoice';
  const recordId = invoice?.id;

  // Permissions
  const isAdmin = user?.role === 'admin';
  const canEdit = isInvoice && !isVoided && !isExpense && (isAdmin || hasPerm('pos', 'sell'));
  const canVoid = !isVoided && !isExpense && (isAdmin || hasPerm('pos', 'sell'));
  const canVerify = !isVerified && !isVoided && (isAdmin || hasPerm('reports', 'view'));
  const canDeleteExpense = isExpense && (isAdmin || hasPerm('accounting', 'view'));

  // ── Edit logic ─────────────────────────────────────────────────────────
  const handleItemChange = (index, field, value) => {
    const newItems = [...editData.items];
    newItems[index] = { ...newItems[index], [field]: value };
    const item = newItems[index];
    const qty = parseFloat(item.quantity) || 0;
    const rate = parseFloat(item.rate) || 0;
    const discVal = parseFloat(item.discount_value) || 0;
    const discType = item.discount_type || 'amount';
    const discAmt = discType === 'percent' ? qty * rate * discVal / 100 : discVal;
    newItems[index].total = Math.round((qty * rate - discAmt) * 100) / 100;
    setEditData({ ...editData, items: newItems });
  };

  const removeItem = (index) => {
    setEditData({ ...editData, items: editData.items.filter((_, i) => i !== index) });
  };

  const calculateTotals = () => {
    const subtotal = editData.items.reduce((sum, item) => sum + (item.total || 0), 0);
    return { subtotal, grandTotal: subtotal + (editData.freight || 0) - (editData.overall_discount || 0) };
  };

  const handleSave = async () => {
    if (!editReason.trim()) { toast.error('Please provide a reason for the edit'); return; }
    setSaving(true);
    try {
      const res = await api.put(`/invoices/${invoice.id}/edit`, {
        ...editData, reason: editReason,
        proof_url: proofFile ? proofFile.name : null,
        _collection: invoice._collection || 'invoices',
      });
      toast.success(res.data.message);
      setEditMode(false); setEditReason(''); setProofFile(null);
      loadInvoice();
      onUpdated?.();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed to save changes'); }
    setSaving(false);
  };

  // ── Actions ────────────────────────────────────────────────────────────
  const handleVerify = async () => {
    if (!verifyPin) { toast.error('Enter PIN'); return; }
    setActionLoading(true);
    try {
      const docType = isPO ? 'purchase_order' : 'invoice';
      await api.post(`/verify/${docType}/${invoice.id}`, { pin: verifyPin });
      toast.success('Transaction verified');
      setVerifyOpen(false); setVerifyPin('');
      loadInvoice(); onUpdated?.();
    } catch (e) { toast.error(e.response?.data?.detail || 'Verification failed'); }
    setActionLoading(false);
  };

  const handleVoid = async () => {
    if (!voidReason || !voidPin) { toast.error('Reason and PIN required'); return; }
    setActionLoading(true);
    try {
      if (isPO) {
        await api.delete(`/purchase-orders/${invoice.id}`);
        toast.success('PO cancelled');
      } else if (isExpense) {
        await api.delete(`/expenses/${invoice.id}`);
        toast.success('Expense deleted');
      } else {
        await api.post(`/invoices/${invoice.id}/void`, { reason: voidReason, pin: voidPin });
        toast.success('Invoice voided');
      }
      setVoidOpen(false); setVoidReason(''); setVoidPin('');
      loadInvoice(); onUpdated?.();
    } catch (e) { toast.error(e.response?.data?.detail || 'Action failed'); }
    setActionLoading(false);
  };

  const copyNumber = async () => {
    try { await navigator.clipboard.writeText(docNumber); } catch { /* fallback */ }
    setCopied(true); setTimeout(() => setCopied(false), 2000);
  };

  const loadEditHistory = async () => {
    if (!invoice?.id) return;
    try {
      const res = await api.get(`/invoices/${invoice.id}/edit-history`);
      setEditHistory(res.data);
      setSection('history');
    } catch { toast.error('Failed to load edit history'); }
  };

  // ── Print (compact mode) ──────────────────────────────────────────────
  const handlePrint = async (format) => {
    if (!invoice) return;
    const docType = PrintEngine.getDocType(invoice);
    let docCode = invoice.doc_code || '';
    if (!docCode && invoice.id) {
      try {
        const res = await api.post('/doc/generate-code', { doc_type: 'invoice', doc_id: invoice.id });
        docCode = res.data?.code || '';
        setInvoice(prev => prev ? { ...prev, doc_code: docCode } : prev);
      } catch { /* print without QR */ }
    }
    PrintEngine.print({ type: docType, data: invoice, format, businessInfo, docCode });
  };

  // ── Compact edit helpers ──────────────────────────────────────────────
  const openCompactEdit = () => {
    setEditData({
      items: JSON.parse(JSON.stringify(invoice.items || [])),
      customer_name: invoice.customer_name || '',
      notes: invoice.notes || '',
      freight: invoice.freight || 0,
      overall_discount: invoice.overall_discount || 0,
    });
    setEditReason('');
    setEditOrderDate(invoice.order_date || invoice.created_at?.slice(0, 10) || '');
    const branchId = invoice.branch_id || currentBranch?.id;
    if (branchId && invoice.order_date) {
      api.get('/invoices/check-date-closed', { params: { date: invoice.order_date, branch_id: branchId } })
        .then(r => setEditDateClosed(r.data.closed))
        .catch(() => setEditDateClosed(false));
    }
    setEditMode(true);
  };

  const saveCompactEdit = async () => {
    if (!editReason.trim()) { toast.error('Please provide a reason for the edit'); return; }
    setSaving(true);
    try {
      const payload = {
        items: editData.items, notes: editData.notes, reason: editReason,
        customer_name: editData.customer_name, freight: editData.freight || 0,
        overall_discount: editData.overall_discount || 0,
        branch_id: invoice.branch_id || currentBranch?.id,
      };
      if (editOrderDate && editOrderDate !== (invoice.order_date || invoice.created_at?.slice(0, 10))) {
        payload.order_date = editOrderDate;
        payload.invoice_date = editOrderDate;
      }
      await api.put(`/invoices/${invoice.id}/edit`, payload);
      toast.success('Sale updated');
      setEditMode(false);
      loadInvoice();
      onUpdated?.();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed to save'); }
    setSaving(false);
  };

  // ── Compact void ──────────────────────────────────────────────────────
  const handleCompactVoid = async () => {
    if (!voidReason || !voidPin) { toast.error('Reason and PIN required'); return; }
    setActionLoading(true);
    try {
      await api.post(`/invoices/${invoice.id}/void`, { reason: voidReason, manager_pin: voidPin });
      toast.success('Sale voided');
      setVoidOpen(false); setVoidReason(''); setVoidPin('');
      loadInvoice(); onUpdated?.();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed to void'); }
    setActionLoading(false);
  };

  // ── Type info ──────────────────────────────────────────────────────────
  const getTypeInfo = () => {
    if (!invoice) return { label: 'Invoice', color: 'bg-blue-100 text-blue-700' };
    if (isPO) return { label: 'Purchase Order', color: 'bg-amber-100 text-amber-700' };
    if (isExpense) return { label: invoice.category || 'Expense', color: 'bg-red-100 text-red-700' };
    const st = invoice.sale_type || '';
    if (st === 'interest_charge') return { label: 'Interest Charge', color: 'bg-amber-100 text-amber-700' };
    if (st === 'penalty_charge') return { label: 'Penalty Charge', color: 'bg-red-100 text-red-700' };
    if (st === 'farm_expense') return { label: 'Farm Expense', color: 'bg-green-100 text-green-700' };
    if (st === 'cash_advance') return { label: 'Customer Cash Out', color: 'bg-purple-100 text-purple-700' };
    return { label: 'Sales Invoice', color: 'bg-blue-100 text-blue-700' };
  };

  const statusStyles = {
    paid: 'bg-emerald-100 text-emerald-700', partial: 'bg-amber-100 text-amber-700',
    open: 'bg-red-100 text-red-700', received: 'bg-emerald-100 text-emerald-700',
    draft: 'bg-slate-100 text-slate-600', voided: 'bg-slate-200 text-slate-500',
    cancelled: 'bg-red-100 text-red-600',
  };

  const typeInfo = getTypeInfo();
  const { subtotal, grandTotal } = calculateTotals();

  // ══════════════════════════════════════════════════════════════════════
  // COMPACT MODE — single-view layout (replaces SaleDetailModal / A4)
  // ══════════════════════════════════════════════════════════════════════
  if (compact) {
    const saleNumber = invoice?.invoice_number || invoice?.sale_number || '';

    return (
      <>
        <Dialog open={open} onOpenChange={v => { onOpenChange(v); if (!v) setEditMode(false); }}>
          <DialogContent className="sm:max-w-lg max-h-[90vh] overflow-y-auto overflow-x-hidden" data-testid="invoice-detail-modal">
            <DialogHeader>
              <DialogTitle style={{ fontFamily: 'Manrope' }} data-testid="invoice-number">
                {editMode ? `Edit Sale — ${saleNumber}` : `Sale Detail — ${saleNumber}`}
              </DialogTitle>
              <div className="mt-1 flex items-center gap-2 flex-wrap">
                {invoice?.verified && (
                  <>
                    <VerificationBadge doc={invoice} />
                    {invoice.verified_at && <span className="text-[10px] text-slate-400">{invoice.verified_at?.slice(0, 16)?.replace('T', ' ')}</span>}
                  </>
                )}
                {(invoice?.has_signature || signatures.length > 0) && (
                  <button
                    type="button"
                    onClick={() => {
                      setSection('signature');
                      // In compact mode the section is inline — scroll into view
                      setTimeout(() => {
                        const el = document.getElementById('compact-signature-section');
                        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
                      }, 80);
                    }}
                    className={`inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded border transition-colors ${
                      (invoice?.signature_status === 'bypassed' || signatures[0]?.status === 'bypassed')
                        ? 'bg-amber-50 text-amber-700 border-amber-200 hover:bg-amber-100'
                        : 'bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-100'
                    }`}
                    title={(invoice?.signature_status === 'bypassed' || signatures[0]?.status === 'bypassed') ? 'Authorized via Manager PIN — click to view' : 'Customer signed — click to view'}
                    data-testid="header-signature-chip"
                  >
                    <ShieldCheck size={11} />
                    {(invoice?.signature_status === 'bypassed' || signatures[0]?.status === 'bypassed') ? 'PIN bypass' : 'Signed by customer'}
                  </button>
                )}
              </div>
            </DialogHeader>

            {loading ? (
              <div className="flex items-center justify-center py-12"><div className="text-slate-400">Loading...</div></div>
            ) : invoice ? (
              <>
                {/* Action toolbar */}
                <div className="flex flex-wrap items-center gap-1.5 pb-2 border-b border-slate-100" data-testid="sale-action-bar">
                  <Button size="sm" variant="outline" className="h-7 text-xs whitespace-nowrap"
                    onClick={() => handlePrint('full_page')} data-testid="sale-print-full">
                    <Printer size={12} className="mr-1" /> Print Full
                  </Button>
                  <Button size="sm" variant="outline" className="h-7 text-xs whitespace-nowrap"
                    onClick={() => handlePrint('thermal')} data-testid="sale-print-thermal">
                    <Printer size={12} className="mr-1" /> 58mm
                  </Button>
                  <Button size="sm" variant="outline" className="h-7 text-xs whitespace-nowrap"
                    onClick={() => setViewQROpen(true)} data-testid="sale-view-phone-btn">
                    <Wallet size={12} className="mr-1" /> Phone
                  </Button>
                  <Button size="sm" variant="outline" className="h-7 text-xs whitespace-nowrap"
                    onClick={() => setUploadQROpen(true)} data-testid="sale-upload-receipt-btn">
                    <Upload size={12} className="mr-1" /> Upload
                  </Button>
                  {!invoice.verified && !isVoided && (
                    <Button size="sm" variant="outline" className="h-7 text-xs text-[#1A4D2E] border-[#1A4D2E]/30 hover:bg-[#1A4D2E]/5"
                      onClick={() => setVerifyDialogOpen(true)} data-testid="sale-verify-btn">
                      <ShieldCheck size={12} className="mr-1" /> Verify
                    </Button>
                  )}
                  {canEdit && !editMode && (
                    <Button size="sm" variant="outline" className="h-7 text-xs text-amber-600 border-amber-200 hover:bg-amber-50"
                      onClick={openCompactEdit} data-testid="sale-edit-btn">
                      <Pencil size={12} className="mr-1" /> Edit
                    </Button>
                  )}
                </div>

                <div className="space-y-4">
                  {/* Receipts gallery */}
                  <ReceiptGallery recordType="invoice" recordId={invoice.id} />

                  {/* Header info */}
                  <div className="grid grid-cols-2 gap-3 text-sm">
                    <div><span className="text-slate-500">Customer:</span> <b>{invoice.customer_name || 'Walk-in'}</b></div>
                    <div><span className="text-slate-500">Date:</span> {invoice.order_date || invoice.created_at?.slice(0, 10)}</div>
                    <div><span className="text-slate-500">Cashier:</span> {invoice.cashier_name || '—'}</div>
                    <div><span className="text-slate-500">Payment:</span> <Badge variant="outline" className="text-[10px]">{invoice.payment_method || 'Cash'}</Badge></div>
                    <div><span className="text-slate-500">Status:</span>
                      <Badge className={`text-[10px] ml-1 ${isVoided ? 'bg-red-100 text-red-700' : invoice.status === 'paid' ? 'bg-emerald-100 text-emerald-700' : invoice.status === 'partial' ? 'bg-amber-100 text-amber-700' : 'bg-blue-100 text-blue-700'}`}>
                        {invoice.status || 'completed'}
                      </Badge>
                    </div>
                    {invoice.terms && <div><span className="text-slate-500">Terms:</span> {invoice.terms_label || invoice.terms}</div>}
                    {invoice.due_date && <div><span className="text-slate-500">Due:</span> {invoice.due_date}</div>}
                    {invoice.balance > 0 && <div><span className="text-slate-500">Balance:</span> <b className="text-red-600">{formatPHP(invoice.balance)}</b></div>}
                  </div>

                  {/* Digital payment info */}
                  {(invoice.digital_platform || invoice.fund_source === 'digital' || invoice.fund_source === 'split') && (
                    <div className="grid grid-cols-2 gap-3 text-sm bg-slate-50 rounded-lg p-3">
                      {invoice.digital_platform && <div><span className="text-slate-500">Platform:</span> <span className="flex items-center gap-1 inline-flex"><Wallet size={12} className="text-blue-500" />{invoice.digital_platform}</span></div>}
                      {invoice.digital_ref_number && <div><span className="text-slate-500">Ref #:</span> <span className="font-mono">{invoice.digital_ref_number}</span></div>}
                      {invoice.digital_sender && <div><span className="text-slate-500">Sender:</span> {invoice.digital_sender}</div>}
                      {invoice.fund_source && <div><span className="text-slate-500">Fund Source:</span> <span className="capitalize">{invoice.fund_source}</span></div>}
                      {invoice.cash_amount > 0 && <div><span className="text-slate-500">Cash Portion:</span> {formatPHP(invoice.cash_amount)}</div>}
                      {invoice.digital_amount > 0 && <div><span className="text-slate-500">Digital Portion:</span> {formatPHP(invoice.digital_amount)}</div>}
                    </div>
                  )}

                  {/* Items table / edit mode */}
                  {editMode ? (
                    <div className="space-y-2">
                      <p className="text-xs text-slate-500 font-medium uppercase">Edit Items</p>
                      {editData.items.map((item, i) => (
                        <div key={i} className="grid grid-cols-12 gap-1.5 items-center p-2 bg-slate-50 rounded-lg border border-slate-200">
                          <div className="col-span-5 text-xs font-medium truncate">{item.product_name}</div>
                          <div className="col-span-3">
                            <Label className="text-[9px] text-slate-400">Qty</Label>
                            <Input type="number" min={0} value={item.quantity}
                              onChange={e => handleItemChange(i, 'quantity', parseFloat(e.target.value) || 0)}
                              className="h-7 text-sm text-right font-mono" />
                          </div>
                          <div className="col-span-4">
                            <Label className="text-[9px] text-slate-400">Price</Label>
                            <Input type="number" min={0} value={item.rate || item.unit_price || item.price}
                              onChange={e => handleItemChange(i, 'rate', parseFloat(e.target.value) || 0)}
                              className="h-7 text-sm text-right font-mono" />
                          </div>
                        </div>
                      ))}
                      <div className="mt-2 grid grid-cols-2 gap-2">
                        <div>
                          <Label className="text-xs text-slate-500">Sale Date</Label>
                          <Input type="date" value={editOrderDate}
                            onChange={e => setEditOrderDate(e.target.value)}
                            className="mt-1 h-9 text-sm" />
                          {editDateClosed && editOrderDate === (invoice.order_date || invoice.created_at?.slice(0, 10)) && (
                            <p className="text-[9px] text-amber-600 mt-0.5">This date is closed. PIN required to save changes.</p>
                          )}
                        </div>
                        <div>
                          <Label className="text-xs text-slate-500">Reason for Edit <span className="text-red-500">*</span></Label>
                          <Input value={editReason} onChange={e => setEditReason(e.target.value)}
                            placeholder="e.g. Customer correction, wrong item..."
                            className="mt-1 h-9 text-sm" />
                        </div>
                      </div>
                      <div className="flex gap-2 pt-2 border-t">
                        <Button variant="outline" onClick={() => setEditMode(false)} className="flex-1">Cancel</Button>
                        <Button onClick={saveCompactEdit} disabled={saving} className="flex-1 bg-amber-600 hover:bg-amber-700 text-white">
                          {saving ? <RefreshCw size={13} className="animate-spin mr-1.5" /> : <Check size={13} className="mr-1.5" />}
                          Save Changes
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead className="text-xs">Product</TableHead>
                          <TableHead className="text-xs text-right">Qty</TableHead>
                          <TableHead className="text-xs text-right">Price</TableHead>
                          <TableHead className="text-xs text-right">Disc</TableHead>
                          <TableHead className="text-xs text-right">Total</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {invoice.items?.map((item, i) => (
                          <TableRow key={i}>
                            <TableCell className="text-sm">
                              {item.product_name}
                              {item.is_repack && <Badge variant="outline" className="text-[9px] ml-1">R</Badge>}
                            </TableCell>
                            <TableCell className="text-right">{item.quantity}</TableCell>
                            <TableCell className="text-right font-mono">{formatPHP(item.rate || item.unit_price || item.price || 0)}</TableCell>
                            <TableCell className="text-right text-xs text-emerald-600">{(item.discount_amount || 0) > 0 ? `-${formatPHP(item.discount_amount)}` : '—'}</TableCell>
                            <TableCell className="text-right font-semibold font-mono">{formatPHP(item.total || 0)}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  )}

                  {!editMode && (
                    <>
                      <div className="text-sm space-y-1 border-t pt-3">
                        <div className="flex justify-between"><span className="text-slate-500">Subtotal</span><span className="font-mono">{formatPHP(invoice.subtotal || invoice.line_subtotal || 0)}</span></div>
                        {(invoice.overall_discount || invoice.overall_discount_amount || 0) > 0 && (
                          <div className="flex justify-between text-emerald-600"><span>Discount</span><span className="font-mono">-{formatPHP(invoice.overall_discount || invoice.overall_discount_amount || 0)}</span></div>
                        )}
                        {(invoice.freight || 0) > 0 && <div className="flex justify-between"><span className="text-slate-500">Freight</span><span className="font-mono">{formatPHP(invoice.freight)}</span></div>}
                        {(invoice.tax_amount || 0) > 0 && <div className="flex justify-between"><span className="text-slate-500">Tax</span><span className="font-mono">{formatPHP(invoice.tax_amount)}</span></div>}
                        <div className="flex justify-between font-bold text-base pt-1 border-t"><span>Grand Total</span><span className="font-mono text-[#1A4D2E]">{formatPHP(invoice.grand_total || invoice.total || 0)}</span></div>
                        {(invoice.amount_paid || 0) > 0 && <div className="flex justify-between text-sm text-emerald-600"><span>Paid</span><span className="font-mono">{formatPHP(invoice.amount_paid)}</span></div>}
                        {(invoice.balance || 0) > 0 && <div className="flex justify-between text-sm text-red-600"><span>Balance</span><span className="font-mono">{formatPHP(invoice.balance)}</span></div>}
                      </div>

                      {invoice.notes && <p className="text-sm text-slate-500 border-t pt-2">Notes: {invoice.notes}</p>}

                      {/* Sales rep / created info */}
                      <div className="text-xs text-slate-400 space-y-0.5 border-t pt-2">
                        {invoice.sales_rep_name && <p>Sales Rep: {invoice.sales_rep_name}</p>}
                        <p>Created: {invoice.created_at ? new Date(invoice.created_at).toLocaleString() : '—'} by {invoice.cashier_name || '—'}</p>
                        {invoice.edited && <p>Last edited: {invoice.last_edited_at ? new Date(invoice.last_edited_at).toLocaleString() : '—'} by {invoice.last_edited_by || '—'}</p>}
                      </div>

                      {/* Payment history */}
                      {payments.length > 0 && (
                        <div className="border-t pt-2">
                          <p className="text-xs font-semibold uppercase text-slate-400 mb-2">Payment History</p>
                          {payments.map((pay, i) => (
                            <div key={i} className="flex items-center justify-between text-xs py-1 border-b last:border-0">
                              <div className="flex items-center gap-2">
                                <Check size={10} className="text-emerald-500" />
                                <span>{pay.date || '—'}</span>
                                {pay.method && <Badge variant="outline" className="text-[9px]">{pay.method}</Badge>}
                                {pay.fund_source && <span className="text-slate-400">{pay.fund_source}</span>}
                              </div>
                              <span className="font-bold text-emerald-600">{formatPHP(pay.amount || 0)}</span>
                            </div>
                          ))}
                        </div>
                      )}

                      {/* Edit history */}
                      {invoice.edit_history?.length > 0 && (
                        <div className="border-t pt-2">
                          <p className="text-xs font-semibold uppercase text-slate-400 mb-2">Edit History</p>
                          {invoice.edit_history.map((edit, i) => (
                            <div key={i} className="text-xs p-2 bg-amber-50 rounded mb-1.5 border border-amber-100">
                              <div className="flex items-center justify-between mb-0.5">
                                <span className="font-semibold text-amber-800">{edit.edited_by_name || edit.changed_by}</span>
                                <span className="text-slate-400">{(edit.edited_at || edit.changed_at)?.slice(0, 10)}</span>
                              </div>
                              <p className="text-slate-600 italic">"{edit.reason}"</p>
                              {edit.change_summary && <p className="text-slate-500 mt-0.5">{edit.change_summary}</p>}
                            </div>
                          ))}
                        </div>
                      )}

                      {/* Customer signature(s) — inline in compact mode */}
                      {signatures.length > 0 && (
                        <div id="compact-signature-section" data-testid="signature-section" className="border-2 border-emerald-200 rounded-lg p-3 bg-emerald-50/30 space-y-2">
                          <h4 className="text-xs font-semibold flex items-center gap-1.5 text-emerald-700">
                            <ShieldCheck size={13} /> Credit Authorization Signature
                          </h4>
                          {signatures.map((sig, i) => (
                            <div key={sig.id || i} className="bg-white border border-slate-200 rounded-lg p-2.5">
                              <div className="flex items-center justify-between mb-2">
                                <Badge className={`text-[10px] ${sig.status === 'signed' ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'}`}>
                                  {sig.status === 'signed' ? '✓ Signed' : 'PIN Override'}
                                </Badge>
                                <span className="text-[10px] text-slate-400">
                                  {sig.signed_at ? new Date(sig.signed_at).toLocaleString('en-PH', { dateStyle: 'short', timeStyle: 'short' }) : sig.bypassed_at ? new Date(sig.bypassed_at).toLocaleString('en-PH', { dateStyle: 'short', timeStyle: 'short' }) : ''}
                                </span>
                              </div>
                              {sig.status === 'signed' && sig.signature_url ? (
                                <div className="border border-dashed border-slate-300 rounded p-2 bg-white">
                                  <img src={sig.signature_url} alt="Customer signature" className="max-h-24 mx-auto object-contain"
                                    onError={e => { e.target.style.display = 'none'; e.target.nextSibling && (e.target.nextSibling.style.display = 'block'); }} />
                                  <p className="text-[10px] text-slate-400 hidden text-center py-2">Image unavailable.</p>
                                  <div className="flex justify-center mt-1.5">
                                    <p className="text-[9px] text-slate-400 uppercase tracking-wider">Customer signature · {sig.signer_name || sig.credit_context?.customer_name || ''}</p>
                                  </div>
                                </div>
                              ) : sig.status === 'bypassed' ? (
                                <div className="bg-amber-50 border border-amber-200 rounded p-2 text-[11px] text-amber-700">
                                  <p className="font-medium">Manager PIN Override · By {sig.bypass_by_name}</p>
                                  {sig.bypass_reason && <p className="italic">"{sig.bypass_reason}"</p>}
                                </div>
                              ) : (
                                <p className="text-[11px] text-slate-400 text-center py-2">Status: {sig.status}</p>
                              )}
                            </div>
                          ))}
                        </div>
                      )}

                      {/* Void button */}
                      {canVoid && (
                        <Button variant="destructive" className="w-full" onClick={() => setVoidOpen(true)} data-testid="sale-void-btn">
                          <Ban size={14} className="mr-2" /> Void Sale
                        </Button>
                      )}
                    </>
                  )}
                </div>
              </>
            ) : (
              <div className="flex items-center justify-center py-12"><div className="text-slate-400">Sale not found</div></div>
            )}
          </DialogContent>
        </Dialog>

        {/* Upload QR Dialog */}
        <UploadQRDialog
          open={uploadQROpen}
          onClose={(count) => { setUploadQROpen(false); if (count > 0) { toast.success(`${count} photo(s) uploaded`); onUpdated?.(); } }}
          recordType="invoice"
          recordId={invoice?.id}
        />
        {/* View QR Dialog */}
        <ViewQRDialog open={viewQROpen} onClose={() => setViewQROpen(false)} recordType="invoice" recordId={invoice?.id} />
        {/* Verify PIN Dialog */}
        <VerifyPinDialog
          open={verifyDialogOpen}
          onClose={() => setVerifyDialogOpen(false)}
          docType="invoice"
          docId={invoice?.id}
          docLabel={saleNumber}
          onVerified={(result) => {
            setVerifyDialogOpen(false);
            setInvoice(prev => ({ ...prev, verified: true, verified_by_name: result.verified_by, verified_at: new Date().toISOString() }));
            onUpdated?.();
          }}
        />
        {/* Void dialog */}
        <Dialog open={voidOpen} onOpenChange={setVoidOpen}>
          <DialogContent className="sm:max-w-sm" data-testid="sale-void-dialog">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2 text-red-600"><Ban size={18} /> Void Sale</DialogTitle>
            </DialogHeader>
            <div className="space-y-3 py-2">
              <Input placeholder="Reason for voiding" value={voidReason} onChange={e => setVoidReason(e.target.value)} data-testid="void-reason-input" />
              <Input type="password" autoComplete="new-password" placeholder="Manager/Admin PIN" value={voidPin} onChange={e => setVoidPin(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && voidReason && voidPin) handleCompactVoid(); }} data-testid="void-pin-input" />
              <div className="flex gap-2">
                <Button variant="outline" className="flex-1" onClick={() => { setVoidOpen(false); setVoidReason(''); setVoidPin(''); }}>Cancel</Button>
                <Button variant="destructive" className="flex-1" onClick={handleCompactVoid} disabled={!voidReason || !voidPin || actionLoading} data-testid="void-confirm-btn">
                  {actionLoading ? 'Processing...' : 'Void Sale'}
                </Button>
              </div>
            </div>
          </DialogContent>
        </Dialog>
      </>
    );
  }

  // ══════════════════════════════════════════════════════════════════════
  // FULL MODE — tabbed layout (original InvoiceDetailModal)
  // ══════════════════════════════════════════════════════════════════════
  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-4xl max-h-[90vh] flex flex-col p-0 overflow-x-hidden" data-testid="invoice-detail-modal">
          {loading ? (
            <div className="flex items-center justify-center py-20">
              <div className="text-slate-400">Loading...</div>
            </div>
          ) : invoice ? (
            <>
              {/* ── Header ───────────────────────────────────────────── */}
              <div className="px-6 py-4 border-b border-slate-100">
                <div className="flex items-start justify-between">
                  <div>
                    <div className="flex items-center gap-2 flex-wrap">
                      <h2 className="text-xl font-bold" data-testid="invoice-number" style={{ fontFamily: 'Manrope' }}>
                        {docNumber}
                      </h2>
                      <button onClick={copyNumber} className="text-slate-400 hover:text-slate-600" data-testid="copy-number-btn">
                        {copied ? <Check size={14} className="text-green-600" /> : <Copy size={14} />}
                      </button>
                      <Badge className={`text-xs ${typeInfo.color}`}>{typeInfo.label}</Badge>
                      <Badge className={`text-xs ${statusStyles[invoice.status] || 'bg-slate-100 text-slate-600'}`}>
                        {invoice.status || (isPO ? invoice.payment_status : 'N/A')}
                      </Badge>
                      {isVerified && (
                        <Badge className="text-xs bg-emerald-100 text-emerald-700">
                          <CheckCircle2 size={10} className="mr-1" /> Verified
                        </Badge>
                      )}
                      {invoice.edited && (
                        <Badge className="text-xs bg-orange-100 text-orange-700 cursor-pointer hover:bg-orange-200"
                          onClick={loadEditHistory}>
                          <Edit3 size={10} className="mr-1" /> Edited {invoice.edit_count > 1 ? `(${invoice.edit_count}x)` : ''}
                        </Badge>
                      )}
                    </div>
                    <p className="text-sm text-slate-500 mt-1">{docName} · {docDate}</p>
                  </div>

                  {/* Header actions */}
                  <div className="flex items-center gap-1.5 shrink-0">
                    {canEdit && !editMode && section === 'detail' && (
                      <Button variant="outline" size="sm" onClick={() => setEditMode(true)} data-testid="edit-btn">
                        <Edit3 size={14} className="mr-1" /> Edit
                      </Button>
                    )}
                    {canVerify && (
                      <Button variant="outline" size="sm" className="text-emerald-700 border-emerald-200 hover:bg-emerald-50"
                        onClick={() => setVerifyOpen(true)} data-testid="verify-btn">
                        <ShieldCheck size={14} className="mr-1" /> Verify
                      </Button>
                    )}
                    {canVoid && (
                      <Button variant="outline" size="sm" className="text-red-600 border-red-200 hover:bg-red-50"
                        onClick={() => setVoidOpen(true)} data-testid="void-btn">
                        <Ban size={14} className="mr-1" /> {isPO ? 'Cancel' : 'Void'}
                      </Button>
                    )}
                    {canDeleteExpense && (
                      <Button variant="outline" size="sm" className="text-red-600 border-red-200 hover:bg-red-50"
                        onClick={() => setVoidOpen(true)} data-testid="delete-expense-btn">
                        <Ban size={14} className="mr-1" /> Delete
                      </Button>
                    )}
                  </div>
                </div>

                {/* Section tabs */}
                <div className="flex gap-1 mt-3 border-b border-slate-100 -mb-4 pb-0">
                  {[
                    { key: 'detail', label: 'Details', icon: FileText },
                    { key: 'receipts', label: 'Receipts', icon: ImageIcon },
                    ...(payments.length > 0 ? [{ key: 'payments', label: `Payments (${payments.length})`, icon: DollarSign }] : []),
                    ...(invoice.release_mode === 'partial' ? [{ key: 'releases', label: `Releases (${(invoice.stock_releases || []).length})`, icon: Package }] : []),
                    ...(editHistory.length > 0 || invoice.edit_count > 0 ? [{ key: 'history', label: 'History', icon: History }] : []),
                    ...(signatures.length > 0 ? [{ key: 'signature', label: `Signature (${signatures.length})`, icon: ShieldCheck }] : [
                      { key: 'signature', label: 'Signature', icon: ShieldCheck }
                    ]),
                  ].map(t => (
                    <button key={t.key} data-testid={`section-${t.key}`}
                      onClick={() => { setSection(t.key); if (t.key === 'history') loadEditHistory(); }}
                      className={`flex items-center gap-1.5 px-3 py-2 text-sm font-medium border-b-2 transition-colors ${
                        section === t.key ? 'border-[#1A4D2E] text-[#1A4D2E]' : 'border-transparent text-slate-400 hover:text-slate-600'
                      }`}>
                      <t.icon size={14} /> {t.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* ── Content ──────────────────────────────────────────── */}
              <ScrollArea className="flex-1 px-6 py-4">

                {/* DETAIL section */}
                {section === 'detail' && (
                  <div className="space-y-5">

                    {/* ── Expense-specific view ─────────────────────── */}
                    {isExpense ? (
                      <>
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                          <div>
                            <Label className="text-xs text-slate-500">Category</Label>
                            <p className="font-medium text-sm">{invoice.category || '—'}</p>
                          </div>
                          <div>
                            <Label className="text-xs text-slate-500">Amount</Label>
                            <p className="font-bold text-sm text-red-600">{formatPHP(invoice.amount || 0)}</p>
                          </div>
                          <div>
                            <Label className="text-xs text-slate-500">Date</Label>
                            <p className="font-medium text-sm">{invoice.date || '—'}</p>
                          </div>
                          <div>
                            <Label className="text-xs text-slate-500">Fund Source</Label>
                            <p className="font-medium text-sm capitalize">{invoice.fund_source || 'cashier'}</p>
                          </div>
                        </div>
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                          {invoice.vendor_name && <div><Label className="text-xs text-slate-500">Vendor</Label><p className="font-medium text-sm">{invoice.vendor_name}</p></div>}
                          {invoice.reference_number && <div><Label className="text-xs text-slate-500">Reference #</Label><p className="font-mono text-sm">{invoice.reference_number}</p></div>}
                          {invoice.customer_name && <div><Label className="text-xs text-slate-500">Customer</Label><p className="font-medium text-sm">{invoice.customer_name}</p></div>}
                          {invoice.employee_name && <div><Label className="text-xs text-slate-500">Employee</Label><p className="font-medium text-sm">{invoice.employee_name}</p></div>}
                          {invoice.linked_invoice_number && <div><Label className="text-xs text-slate-500">Linked Invoice</Label><p className="font-mono text-sm">{invoice.linked_invoice_number}</p></div>}
                        </div>
                        {invoice.description && (
                          <>
                            <Separator />
                            <div>
                              <Label className="text-xs text-slate-500">Description</Label>
                              <p className="text-sm text-slate-800 mt-1">{invoice.description}</p>
                            </div>
                          </>
                        )}
                        {/* Verification info */}
                        {isVerified && (
                          <div className="flex items-center gap-2 bg-emerald-50 border border-emerald-200 rounded-lg px-3 py-2">
                            <CheckCircle2 size={16} className="text-emerald-600" />
                            <span className="text-sm text-emerald-700">
                              Verified by <strong>{invoice.verified_by_name}</strong> on {invoice.verified_at?.slice(0, 10)}
                            </span>
                          </div>
                        )}
                        <div className="text-xs text-slate-400 space-y-1">
                          <p>Created: {invoice.created_at ? new Date(invoice.created_at).toLocaleString() : '—'} by {invoice.created_by_name || '—'}</p>
                        </div>
                      </>
                    ) : (
                    /* ── Invoice / PO view ──────────────────────────── */
                    <>
                    {/* Info grid */}
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                      <div>
                        <Label className="text-xs text-slate-500">{isPO ? 'Vendor' : 'Customer/Vendor'}</Label>
                        {editMode ? (
                          <Input value={editData.customer_name} onChange={e => setEditData({...editData, customer_name: e.target.value})} className="h-9" />
                        ) : (
                          <p className="font-medium text-sm">{docName}</p>
                        )}
                      </div>
                      <div>
                        <Label className="text-xs text-slate-500">Date</Label>
                        <p className="font-medium text-sm">{docDate}</p>
                      </div>
                      <div>
                        <Label className="text-xs text-slate-500">{isPO ? 'Due Date' : 'Due Date'}</Label>
                        <p className="font-medium text-sm">{invoice.due_date || '—'}</p>
                      </div>
                      <div>
                        <Label className="text-xs text-slate-500">Terms</Label>
                        <p className="font-medium text-sm">{invoice.terms || invoice.terms_label || 'COD'}</p>
                      </div>
                    </div>

                    {/* Digital payment & PO-specific info */}
                    {(invoice.digital_platform || invoice.fund_source === 'digital' || invoice.fund_source === 'split' || isPO) && (
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 bg-slate-50 rounded-lg p-3">
                        {isPO && invoice.dr_number && (
                          <div><Label className="text-xs text-slate-500">DR #</Label><p className="font-medium text-sm">{invoice.dr_number}</p></div>
                        )}
                        {isPO && (
                          <div><Label className="text-xs text-slate-500">Payment Status</Label>
                            <Badge className={`text-xs ${invoice.payment_status === 'paid' ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-600'}`}>
                              {invoice.payment_status}
                            </Badge>
                          </div>
                        )}
                        {invoice.digital_platform && (
                          <div><Label className="text-xs text-slate-500">Platform</Label>
                            <p className="font-medium text-sm flex items-center gap-1"><Wallet size={12} className="text-blue-500" /> {invoice.digital_platform}</p></div>
                        )}
                        {invoice.digital_ref_number && (
                          <div><Label className="text-xs text-slate-500">Reference #</Label><p className="font-mono text-sm">{invoice.digital_ref_number}</p></div>
                        )}
                        {invoice.digital_sender && (
                          <div><Label className="text-xs text-slate-500">Sender</Label><p className="font-medium text-sm">{invoice.digital_sender}</p></div>
                        )}
                        {invoice.fund_source && (
                          <div><Label className="text-xs text-slate-500">Fund Source</Label><p className="font-medium text-sm capitalize">{invoice.fund_source}</p></div>
                        )}
                        {(invoice.cash_amount > 0 || invoice.digital_amount > 0) && (
                          <>
                            {invoice.cash_amount > 0 && <div><Label className="text-xs text-slate-500">Cash Portion</Label><p className="font-medium text-sm">{formatPHP(invoice.cash_amount)}</p></div>}
                            {invoice.digital_amount > 0 && <div><Label className="text-xs text-slate-500">Digital Portion</Label><p className="font-medium text-sm">{formatPHP(invoice.digital_amount)}</p></div>}
                          </>
                        )}
                      </div>
                    )}

                    {/* Verification info */}
                    {isVerified && (
                      <div className="flex items-center gap-2 bg-emerald-50 border border-emerald-200 rounded-lg px-3 py-2">
                        <CheckCircle2 size={16} className="text-emerald-600" />
                        <span className="text-sm text-emerald-700">
                          Verified by <strong>{invoice.verified_by_name}</strong> on {invoice.verified_at?.slice(0, 10)}
                          {invoice.verification_status === 'discrepancy' && <span className="text-amber-600 ml-2">(with discrepancy)</span>}
                        </span>
                      </div>
                    )}

                    <Separator />

                    {/* Items Table */}
                    <div>
                      <Label className="text-xs text-slate-500 uppercase mb-2 block">Line Items</Label>
                      <div className="border border-slate-200 rounded-lg overflow-hidden">
                        <Table>
                          <TableHeader>
                            <TableRow className="bg-slate-50">
                              <TableHead className="text-xs w-8">#</TableHead>
                              <TableHead className="text-xs">Product</TableHead>
                              <TableHead className="text-xs text-right w-20">Qty</TableHead>
                              <TableHead className="text-xs text-right w-28">Rate</TableHead>
                              <TableHead className="text-xs text-right w-24">Discount</TableHead>
                              <TableHead className="text-xs text-right w-28">Amount</TableHead>
                              {editMode && <TableHead className="w-10"></TableHead>}
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {(editMode ? editData.items : invoice.items || []).map((item, i) => (
                              <TableRow key={i}>
                                <TableCell className="text-xs text-slate-400">{i + 1}</TableCell>
                                <TableCell>
                                  <p className="font-medium text-sm">{item.product_name || item.description || 'Item'}</p>
                                  {item.unit && <p className="text-xs text-slate-400">{item.unit}</p>}
                                </TableCell>
                                <TableCell className="text-right">
                                  {editMode ? (
                                    <Input type="number" value={item.quantity} onChange={e => handleItemChange(i, 'quantity', parseFloat(e.target.value) || 0)} className="h-8 w-16 text-right" />
                                  ) : <span className="text-sm">{item.quantity}</span>}
                                </TableCell>
                                <TableCell className="text-right">
                                  {editMode ? (
                                    <Input type="number" value={item.rate || item.unit_price} onChange={e => handleItemChange(i, 'rate', parseFloat(e.target.value) || 0)} className="h-8 w-24 text-right" />
                                  ) : <span className="text-sm">{formatPHP(item.rate || item.unit_price || 0)}</span>}
                                </TableCell>
                                <TableCell className="text-right text-sm text-slate-500">
                                  {(item.discount_amount || 0) > 0 ? formatPHP(item.discount_amount) : '—'}
                                </TableCell>
                                <TableCell className="text-right font-medium text-sm">{formatPHP(item.total || 0)}</TableCell>
                                {editMode && (
                                  <TableCell><Button variant="ghost" size="sm" className="h-7 w-7 p-0 text-red-500" onClick={() => removeItem(i)}><Trash2 size={12} /></Button></TableCell>
                                )}
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                      </div>
                    </div>

                    {/* Totals */}
                    <div className="flex justify-end">
                      <div className="w-64 space-y-2">
                        <div className="flex justify-between text-sm"><span className="text-slate-500">Subtotal</span><span>{formatPHP(editMode ? subtotal : (invoice.subtotal || invoice.line_subtotal || 0))}</span></div>
                        <div className="flex justify-between text-sm">
                          <span className="text-slate-500">Freight</span>
                          {editMode ? <Input type="number" value={editData.freight} onChange={e => setEditData({...editData, freight: parseFloat(e.target.value) || 0})} className="h-7 w-24 text-right" /> : <span>{formatPHP(invoice.freight || 0)}</span>}
                        </div>
                        {(invoice.overall_discount > 0 || invoice.overall_discount_amount > 0 || editMode) && (
                          <div className="flex justify-between text-sm">
                            <span className="text-slate-500">Discount</span>
                            {editMode ? <Input type="number" value={editData.overall_discount} onChange={e => setEditData({...editData, overall_discount: parseFloat(e.target.value) || 0})} className="h-7 w-24 text-right" /> : <span>{formatPHP(invoice.overall_discount || invoice.overall_discount_amount || 0)}</span>}
                          </div>
                        )}
                        {(invoice.tax_amount || 0) > 0 && (
                          <div className="flex justify-between text-sm"><span className="text-slate-500">Tax</span><span>{formatPHP(invoice.tax_amount)}</span></div>
                        )}
                        <Separator />
                        <div className="flex justify-between font-bold"><span>Grand Total</span><span className="text-lg">{formatPHP(editMode ? grandTotal : (invoice.grand_total || 0))}</span></div>
                        {(invoice.amount_paid || 0) > 0 && (
                          <div className="flex justify-between text-sm text-emerald-600"><span>Amount Paid</span><span>{formatPHP(invoice.amount_paid)}</span></div>
                        )}
                        {(invoice.balance || 0) > 0 && (
                          <div className="flex justify-between text-sm text-red-600 font-medium"><span>Balance Due</span><span>{formatPHP(invoice.balance)}</span></div>
                        )}
                      </div>
                    </div>

                    {/* Notes */}
                    <div>
                      <Label className="text-xs text-slate-500">Notes</Label>
                      {editMode ? <Textarea value={editData.notes} onChange={e => setEditData({...editData, notes: e.target.value})} placeholder="Add notes..." rows={2} /> : <p className="text-sm text-slate-600">{invoice.notes || '—'}</p>}
                    </div>

                    {/* Edit Reason */}
                    {editMode && (
                      <Card className="border-amber-200 bg-amber-50">
                        <CardContent className="p-4">
                          <div className="flex items-start gap-2 mb-3">
                            <AlertTriangle size={16} className="text-amber-600 mt-0.5" />
                            <div>
                              <p className="font-medium text-amber-800">Edit Reason Required</p>
                              <p className="text-xs text-amber-600">Please explain why this needs to be edited.</p>
                            </div>
                          </div>
                          <Textarea value={editReason} onChange={e => setEditReason(e.target.value)}
                            placeholder="e.g., Customer requested correction, Wrong product entered..." rows={2} className="bg-white" />
                          <div className="mt-2">
                            <Label className="text-xs text-amber-700">Proof/Attachment (optional)</Label>
                            <Input type="file" onChange={e => setProofFile(e.target.files?.[0] || null)} className="h-9 bg-white" />
                          </div>
                        </CardContent>
                      </Card>
                    )}

                    {/* Footer info */}
                    <div className="text-xs text-slate-400 space-y-1">
                      <p>Created: {invoice.created_at ? new Date(invoice.created_at).toLocaleString() : '—'} by {invoice.cashier_name || invoice.created_by_name || '—'}</p>
                      {invoice.sales_rep_name && <p>Sales Rep: {invoice.sales_rep_name}</p>}
                      {invoice.edited && <p>Last edited: {new Date(invoice.last_edited_at).toLocaleString()} by {invoice.last_edited_by}</p>}
                    </div>
                    </>
                    )}
                  </div>
                )}

                {/* RECEIPTS section */}
                {section === 'receipts' && recordId && (
                  <div className="space-y-4">
                    <div className="flex items-center justify-between">
                      <h3 className="font-semibold text-sm flex items-center gap-2"><ImageIcon size={16} /> Attached Receipts</h3>
                      <div className="flex gap-2">
                        <Button variant="outline" size="sm" onClick={() => setUploadQROpen(true)} data-testid="upload-qr-btn">
                          <Upload size={14} className="mr-1" /> Upload via Phone
                        </Button>
                        <Button variant="outline" size="sm" onClick={() => setViewQROpen(true)} data-testid="view-qr-btn">
                          <Smartphone size={14} className="mr-1" /> View on Phone
                        </Button>
                      </div>
                    </div>
                    <ReceiptGallery recordType={recordType} recordId={recordId} />
                  </div>
                )}

                {/* PAYMENTS section */}
                {section === 'payments' && (
                  <div className="space-y-3">
                    <h3 className="font-semibold text-sm flex items-center gap-2"><DollarSign size={16} /> Payment History</h3>
                    {payments.length === 0 ? (
                      <p className="text-slate-400 text-sm text-center py-6">No payments recorded</p>
                    ) : (
                      <div className="space-y-2">
                        {payments.map((pmt, i) => (
                          <div key={pmt.id || i} className="flex items-center gap-3 p-3 bg-slate-50 rounded-lg border border-slate-100">
                            <div className="w-8 h-8 rounded-full bg-emerald-100 flex items-center justify-center shrink-0">
                              <CreditCard size={14} className="text-emerald-600" />
                            </div>
                            <div className="flex-1">
                              <div className="flex items-center gap-2">
                                <span className="font-semibold text-sm text-emerald-700">{formatPHP(pmt.amount || 0)}</span>
                                {pmt.method && <Badge variant="outline" className="text-[10px]">{pmt.method}</Badge>}
                                {pmt.fund_source && <Badge variant="outline" className="text-[10px]">{pmt.fund_source}</Badge>}
                              </div>
                              <p className="text-xs text-slate-400 mt-0.5">
                                {pmt.date || '—'}
                                {pmt.received_by_name && ` · by ${pmt.received_by_name}`}
                                {pmt.applied_to_interest > 0 && ` · Interest: ${formatPHP(pmt.applied_to_interest)}`}
                                {pmt.applied_to_penalty > 0 && ` · Penalty: ${formatPHP(pmt.applied_to_penalty)}`}
                              </p>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {/* RELEASES section */}
                {section === 'releases' && (
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <h3 className="font-semibold text-sm flex items-center gap-2"><Package size={16} /> Stock Release History</h3>
                      <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                        invoice.stock_release_status === 'fully_released' ? 'bg-emerald-100 text-emerald-700' :
                        invoice.stock_release_status === 'partially_released' ? 'bg-blue-100 text-blue-700' :
                        invoice.stock_release_status === 'expired' ? 'bg-slate-200 text-slate-500' :
                        'bg-amber-100 text-amber-700'
                      }`}>
                        {invoice.stock_release_status === 'fully_released' ? 'Fully Released' :
                         invoice.stock_release_status === 'partially_released' ? 'Partially Released' :
                         invoice.stock_release_status === 'expired' ? 'Expired' : 'Not Released'}
                      </span>
                    </div>
                    {invoice.doc_code && (
                      <a href={`/doc/${invoice.doc_code}`} target="_blank" rel="noreferrer"
                        className="flex items-center gap-1.5 text-xs text-blue-600 hover:underline"
                        data-testid="open-doc-page-link">
                        <Package size={12} /> Open release page (shareable link)
                      </a>
                    )}
                    {(invoice.stock_releases || []).length === 0 ? (
                      <p className="text-slate-400 text-sm text-center py-6">No releases recorded yet</p>
                    ) : (
                      <div className="space-y-3">
                        {(invoice.stock_releases || []).map((r, idx) => (
                          <div key={idx} className="border border-slate-200 rounded-lg p-3 space-y-2" data-testid={`release-record-${r.release_number}`}>
                            <div className="flex items-center justify-between">
                              <span className="text-sm font-semibold text-slate-800">Release #{r.release_number}</span>
                              <span className="text-xs text-slate-400">
                                {r.released_at ? new Date(r.released_at).toLocaleString('en-PH', { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''}
                              </span>
                            </div>
                            <div className="space-y-1">
                              {r.items.map((it, i) => (
                                <div key={i} className="flex justify-between text-xs">
                                  <span className="text-slate-600">{it.product_name}</span>
                                  <span className="font-semibold">{it.qty_released} {it.unit}</span>
                                </div>
                              ))}
                            </div>
                            <div className="flex items-center justify-between pt-1 border-t border-slate-100 text-xs text-slate-400">
                              <span>By {r.released_by_name} · <span className="capitalize">{(r.pin_method || '').replace('_', ' ')}</span></span>
                              <span className={r.remaining_after > 0 ? 'text-amber-600' : 'text-emerald-600'}>
                                {r.remaining_after > 0 ? `${r.remaining_after} remaining` : 'All released'}
                              </span>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {/* HISTORY section */}
                {section === 'history' && (
                  <div className="space-y-3">
                    <h3 className="font-semibold text-sm flex items-center gap-2"><History size={16} /> Edit History</h3>
                    {editHistory.length === 0 ? (
                      <p className="text-slate-400 text-sm text-center py-6">No edit history</p>
                    ) : (
                      <div className="space-y-3">
                        {editHistory.map((edit) => (
                          <Card key={edit.id} className="border-slate-200">
                            <CardContent className="p-4">
                              <div className="flex items-start justify-between mb-2">
                                <div>
                                  <p className="font-medium text-sm">{edit.edited_by_name}</p>
                                  <p className="text-xs text-slate-400"><Clock size={10} className="inline mr-1" />{new Date(edit.edited_at).toLocaleString()}</p>
                                </div>
                                {edit.proof_url && <Badge variant="outline" className="text-xs">Has Proof</Badge>}
                              </div>
                              <div className="bg-slate-50 rounded-lg p-3 mb-2">
                                <p className="text-sm font-medium text-slate-700">Reason:</p>
                                <p className="text-sm text-slate-600">{edit.reason}</p>
                              </div>
                              {edit.changes?.length > 0 && (
                                <ul className="text-xs text-slate-600 space-y-0.5">
                                  {edit.changes.map((c, j) => <li key={j} className="flex items-start gap-1"><span className="text-slate-400">•</span> {c}</li>)}
                                </ul>
                              )}
                            </CardContent>
                          </Card>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {/* SIGNATURE section */}
                {section === 'signature' && (
                  <div className="space-y-3" data-testid="signature-section">
                    <h3 className="font-semibold text-sm flex items-center gap-2">
                      <ShieldCheck size={16} className="text-emerald-600" /> Credit Authorization Signature
                    </h3>
                    {sigLoading ? (
                      <p className="text-slate-400 text-sm text-center py-6">Loading signatures...</p>
                    ) : signatures.length === 0 ? (
                      <div className="text-center py-8 text-slate-400 space-y-2">
                        <ShieldCheck size={32} className="mx-auto text-slate-200" />
                        <p className="text-sm">No signature captured for this invoice.</p>
                        <p className="text-xs">Signatures are collected when using the Charged-to-Crop credit flow.</p>
                      </div>
                    ) : (
                      <div className="space-y-4">
                        {signatures.map((sig, i) => (
                          <Card key={sig.id || i} className={`border-2 ${sig.status === 'signed' ? 'border-emerald-200' : sig.status === 'bypassed' ? 'border-amber-200' : 'border-slate-200'}`}>
                            <CardContent className="p-4">
                              <div className="flex items-center justify-between mb-3">
                                <div className="flex items-center gap-2">
                                  <Badge className={`text-[10px] ${sig.status === 'signed' ? 'bg-emerald-100 text-emerald-700' : sig.status === 'bypassed' ? 'bg-amber-100 text-amber-700' : 'bg-slate-100 text-slate-600'}`}>
                                    {sig.status === 'signed' ? '✓ Signed' : sig.status === 'bypassed' ? 'PIN Override' : sig.status}
                                  </Badge>
                                  <span className="text-xs text-slate-400">
                                    {sig.signed_at ? new Date(sig.signed_at).toLocaleString('en-PH') : sig.bypassed_at ? new Date(sig.bypassed_at).toLocaleString('en-PH') : ''}
                                  </span>
                                </div>
                                {sig.status === 'bypassed' && (
                                  <span className="text-xs text-amber-600">By: {sig.bypass_by_name}</span>
                                )}
                              </div>

                              {/* Credit summary */}
                              {sig.credit_context && (
                                <div className="bg-slate-50 rounded-lg p-2.5 text-xs mb-3 grid grid-cols-2 gap-1">
                                  <div><span className="text-slate-400">Customer:</span> <span className="font-medium">{sig.credit_context.customer_name}</span></div>
                                  <div><span className="text-slate-400">Amount:</span> <span className="font-bold">{sig.credit_context.amount ? `₱${parseFloat(sig.credit_context.amount).toLocaleString('en-PH', {minimumFractionDigits: 2})}` : '—'}</span></div>
                                  <div><span className="text-slate-400">Type:</span> <span className="capitalize">{(sig.credit_context.credit_type || '').replace(/_/g, ' ')}</span></div>
                                  <div><span className="text-slate-400">Date:</span> <span>{sig.credit_context.date}</span></div>
                                </div>
                              )}

                              {/* Signature image */}
                              {sig.status === 'signed' && sig.signature_url ? (
                                <div className="border border-dashed border-slate-300 rounded-xl p-3 bg-white">
                                  <p className="text-[10px] text-slate-400 mb-2 text-center uppercase tracking-wider">Customer Signature</p>
                                  <img
                                    src={sig.signature_url}
                                    alt="Customer signature"
                                    className="max-h-32 mx-auto object-contain"
                                    onError={e => { e.target.style.display = 'none'; }}
                                  />
                                  <div className="flex justify-between items-center mt-2 pt-2 border-t border-slate-100">
                                    <div className="h-px bg-slate-300 flex-1" />
                                    <p className="text-[10px] text-slate-400 mx-2">SIGNATURE</p>
                                    <div className="h-px bg-slate-300 flex-1" />
                                  </div>
                                </div>
                              ) : sig.status === 'signed' && !sig.signature_url ? (
                                <p className="text-xs text-slate-400 text-center py-3">Signature captured but image unavailable (R2 link may have expired).</p>
                              ) : sig.status === 'bypassed' ? (
                                <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-xs text-amber-700">
                                  <p className="font-medium">Manager PIN Override</p>
                                  <p>Reason: {sig.bypass_reason || 'Not specified'}</p>
                                  <p>Authorized by: {sig.bypass_by_name}</p>
                                </div>
                              ) : null}
                            </CardContent>
                          </Card>
                        ))}
                      </div>
                    )}
                  </div>
                )}

              </ScrollArea>

              {/* ── Footer (edit mode) ───────────────────────────────── */}
              {editMode && section === 'detail' && (
                <div className="px-6 py-3 border-t border-slate-100 flex justify-end gap-2">
                  <Button variant="outline" onClick={() => { setEditMode(false); setEditReason(''); loadInvoice(); }}>Cancel</Button>
                  <Button onClick={handleSave} disabled={saving || !editReason.trim()} className="bg-[#1A4D2E] hover:bg-[#14532d] text-white">
                    {saving ? 'Saving...' : <><Save size={14} className="mr-2" /> Save Changes</>}
                  </Button>
                </div>
              )}
            </>
          ) : (
            <div className="flex items-center justify-center py-20"><div className="text-slate-400">Not found</div></div>
          )}
        </DialogContent>
      </Dialog>

      {/* Verify PIN dialog */}
      <Dialog open={verifyOpen} onOpenChange={setVerifyOpen}>
        <DialogContent className="sm:max-w-sm" data-testid="verify-pin-dialog">
          <DialogHeader><DialogTitle className="flex items-center gap-2"><ShieldCheck size={18} className="text-emerald-600" /> Verify Transaction</DialogTitle></DialogHeader>
          <div className="space-y-3 py-2">
            <p className="text-sm text-slate-500">Enter admin/manager PIN to verify this transaction</p>
            <Input data-testid="verify-pin-input" type="password" placeholder="Enter PIN" value={verifyPin}
              onChange={e => setVerifyPin(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') handleVerify(); }} />
            <div className="flex gap-2">
              <Button variant="outline" className="flex-1" onClick={() => { setVerifyOpen(false); setVerifyPin(''); }}>Cancel</Button>
              <Button className="flex-1 bg-emerald-600 hover:bg-emerald-700" onClick={handleVerify} disabled={!verifyPin || actionLoading} data-testid="verify-confirm-btn">
                {actionLoading ? 'Verifying...' : 'Verify'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Void dialog */}
      <Dialog open={voidOpen} onOpenChange={setVoidOpen}>
        <DialogContent className="sm:max-w-sm" data-testid="void-dialog">
          <DialogHeader><DialogTitle className="flex items-center gap-2 text-red-600"><Ban size={18} /> {isExpense ? 'Delete Expense' : isPO ? 'Cancel PO' : 'Void Invoice'}</DialogTitle></DialogHeader>
          <div className="space-y-3 py-2">
            <Input placeholder="Reason" value={voidReason} onChange={e => setVoidReason(e.target.value)} data-testid="void-reason-input" />
            <Input type="password" placeholder="Manager/Admin PIN" value={voidPin} onChange={e => setVoidPin(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && voidReason && voidPin) handleVoid(); }} data-testid="void-pin-input" />
            <div className="flex gap-2">
              <Button variant="outline" className="flex-1" onClick={() => { setVoidOpen(false); setVoidReason(''); setVoidPin(''); }}>Cancel</Button>
              <Button variant="destructive" className="flex-1" onClick={handleVoid} disabled={!voidReason || !voidPin || actionLoading} data-testid="void-confirm-btn">
                {actionLoading ? 'Processing...' : (isExpense ? 'Delete' : isPO ? 'Cancel PO' : 'Void')}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* QR Upload dialog */}
      <UploadQRDialog
        open={uploadQROpen}
        onClose={(count) => { setUploadQROpen(false); if (count > 0) { toast.success(`${count} photo(s) uploaded`); } }}
        recordType={recordType}
        recordId={recordId}
      />

      {/* QR View dialog */}
      <ViewQRDialog
        open={viewQROpen}
        onClose={() => setViewQROpen(false)}
        recordType={recordType}
        recordId={recordId}
      />
    </>
  );
}
