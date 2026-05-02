import { useState, useEffect, useRef, useMemo } from 'react';
import { useAuth, api } from '../contexts/AuthContext';
import { useNavigate, useSearchParams } from 'react-router-dom';
import UploadQRDialog from '../components/UploadQRDialog';
import ReceiptGallery from '../components/ReceiptGallery';
import VerificationBadge from '../components/VerificationBadge';
import VerifyPinDialog from '../components/VerifyPinDialog';
import ViewQRDialog from '../components/ViewQRDialog';
import { formatPHP } from '../lib/utils';
import PrintEngine from '../lib/PrintEngine';
import { Card, CardContent } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Badge } from '../components/ui/badge';
import { Separator } from '../components/ui/separator';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '../components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs';
import SmartProductSearch from '../components/SmartProductSearch';
import ReceiptUploadInline from '../components/ReceiptUploadInline';
import ReferenceNumberPrompt from '../components/ReferenceNumberPrompt';
import {
  FileText, Plus, Trash2, Save, Truck, Check, X, DollarSign,
  Search, History, ArrowRight, Receipt, UserPlus, Package,
  Wallet, Banknote, CreditCard, AlertTriangle, ChevronDown, RefreshCw,
  ShieldCheck, Clock, Pencil, Upload, ImageIcon, TrendingDown, TrendingUp, Printer,
  Smartphone, Lock
} from 'lucide-react';
import { toast } from 'sonner';
import { useUnsavedChangesGuard } from '../lib/useUnsavedChangesGuard';

const EMPTY_LINE = {
  product_id: '', product_name: '', unit: '', description: '',
  quantity: 1, unit_price: 0,
  discount_type: 'amount', discount_value: 0,
};
const PAYMENT_METHODS = ['Cash', 'Check', 'Bank Transfer', 'GCash', 'Maya'];
const TERMS_OPTIONS = [
  { label: 'COD (Due on Receipt)', days: 0 },
  { label: 'Net 7', days: 7 },
  { label: 'Net 15', days: 15 },
  { label: 'Net 30', days: 30 },
  { label: 'Net 45', days: 45 },
  { label: 'Net 60', days: 60 },
];

const statusColor = (s) => {
  if (s === 'received') return 'bg-emerald-100 text-emerald-700';
  if (s === 'ordered') return 'bg-blue-100 text-blue-700';
  if (s === 'sent_to_terminal') return 'bg-amber-100 text-amber-700';
  if (s === 'draft') return 'bg-slate-100 text-slate-600';
  if (s === 'cancelled') return 'bg-red-100 text-red-600';
  return 'bg-slate-100 text-slate-700';
};
const payStatusColor = (s) => {
  if (s === 'paid') return 'bg-emerald-100 text-emerald-700';
  if (s === 'partial') return 'bg-amber-100 text-amber-700';
  return 'bg-red-100 text-red-600';
};

// ── Totals row component ────────────────────────────────────────────────────
function TotalsRow({ label, value, bold, accent }) {
  return (
    <div className={`flex justify-between items-center py-1 text-sm ${bold ? 'font-bold text-base' : ''} ${accent || ''}`}>
      <span className="text-slate-600">{label}</span>
      <span className="font-mono">{formatPHP(value)}</span>
    </div>
  );
}

export default function PurchaseOrderPage() {
  const { currentBranch, branches, user } = useAuth();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const isAdmin = user?.role === 'admin';
  const today = new Date().toISOString().slice(0, 10);

  // ── Header state ────────────────────────────────────────────────────────
  const [tab, setTab] = useState('create');
  const [header, setHeader] = useState({
    vendor: '', dr_number: '', po_number: '', purchase_date: today, notes: '',
    show_freight: false, freight: 0,
    overall_discount_type: 'amount', overall_discount_value: '',
    show_vat: false, tax_rate: 12,
    payment_type: 'cash', // 'cash' | 'terms'
    terms_label: 'Net 30', terms_days: 30,
  });
  const [lines, setLines] = useState([{ ...EMPTY_LINE }]);
  const [saving, setSaving] = useState(false);

  // Unsaved-changes guard — fires when user tries to leave with at least
  // one filled line. The Provider renders the dialog; we only need to
  // register the dirty state.
  useUnsavedChangesGuard({
    isDirty: !saving && lines.some(l => l.product_id),
    label: 'Purchase Order',
  });

  // ── Supplier search ────────────────────────────────────────────────────
  const [suppliers, setSuppliers] = useState([]);
  const [vendorsList, setVendorsList] = useState([]);
  const [supplierSearch, setSupplierSearch] = useState('');
  const [supplierResults, setSupplierResults] = useState([]);
  const [showSupplierDd, setShowSupplierDd] = useState(false);
  const supplierRef = useRef(null);
  const [vendorPrices, setVendorPrices] = useState({}); // { product_id: last_price }

  // ── Cash dialog ────────────────────────────────────────────────────────
  const [cashDialog, setCashDialog] = useState(false);
  const [cashFunds, setCashFunds] = useState({ cashier: 0, safe: 0 });
  const [cashForm, setCashForm] = useState({ fund_source: 'cashier', payment_method_detail: 'Cash', check_number: '' });
  const [cashLoading, setCashLoading] = useState(false);

  // ── Terms dialog ───────────────────────────────────────────────────────
  const [termsDialog, setTermsDialog] = useState(false);
  const [termsForm, setTermsForm] = useState({ terms_days: 30, terms_label: 'Net 30', due_date: '' });

  // ── PO List ────────────────────────────────────────────────────────────
  const [orders, setOrders] = useState([]);
  const [totalOrders, setTotalOrders] = useState(0);
  const [listFilter, setListFilter] = useState('all');
  const [detailDialog, setDetailDialog] = useState(false);
  const [detailPO, setDetailPO] = useState(null);
  const [detailEditMode, setDetailEditMode] = useState(false);
  const [detailEditItems, setDetailEditItems] = useState([]);
  const [detailEditReason, setDetailEditReason] = useState('');
  const [detailEditDR, setDetailEditDR] = useState('');
  const [detailEditDate, setDetailEditDate] = useState('');
  const [detailSaving, setDetailSaving] = useState(false);
  const [uploadQROpen, setUploadQROpen] = useState(false);
  const [uploadRecordId, setUploadRecordId] = useState(null);
  const [verifyDialogOpen, setVerifyDialogOpen] = useState(false);
  const [viewQROpen, setViewQROpen] = useState(false);
  const [viewQRFileCount, setViewQRFileCount] = useState(0);
  const [reviewPinDialog, setReviewPinDialog] = useState(false);
  const [reviewPin, setReviewPin] = useState('');
  const [reviewSaving, setReviewSaving] = useState(false);
  const [cancelPinDialog, setCancelPinDialog] = useState(false);
  const [cancelPinTarget, setCancelPinTarget] = useState(null);
  const [cancelPin, setCancelPin] = useState('');
  const [cancelSaving, setCancelSaving] = useState(false);
  const [reopenPinDialog, setReopenPinDialog] = useState(false);
  const [reopenPinTarget, setReopenPinTarget] = useState(null);
  const [reopenPin, setReopenPin] = useState('');
  const [reopenSaving, setReopenSaving] = useState(false);
  const [payAdjDialog, setPayAdjDialog] = useState(false);
  const [payAdjData, setPayAdjData] = useState(null); // { po, delta, oldTotal, newTotal }
  const [payAdjFundSource, setPayAdjFundSource] = useState('cashier');
  const [payAdjReason, setPayAdjReason] = useState('');
  const [payAdjFunds, setPayAdjFunds] = useState({ cashier: 0, safe: 0 });
  const [payAdjSaving, setPayAdjSaving] = useState(false);
  const [schemes, setSchemes] = useState([]);

  // ── Create product dialog ──────────────────────────────────────────────
  const [createProdDialog, setCreateProdDialog] = useState(false);
  const [newProdForm, setNewProdForm] = useState({ sku: '', name: '', category: 'Pesticide', unit: 'Box', cost_price: 0, prices: {}, product_type: 'stockable' });

  // ── Smart Capital Dialog ───────────────────────────────────────────────
  const [capitalDialog, setCapitalDialog] = useState(false);
  const [capitalPreview, setCapitalPreview] = useState(null); // { po_number, vendor, items, has_warnings }
  const [capitalChoices, setCapitalChoices] = useState({}); // { product_id: 'last_purchase'|'moving_average' }
  const [capitalPendingPOId, setCapitalPendingPOId] = useState(null);

  // ── Source type / supply branch (branch request form) ────────────────
  const [sourceType, setSourceType] = useState('external');
  const [supplyBranchId, setSupplyBranchId] = useState('');
  const [showRetailToggle, setShowRetailToggle] = useState(isAdmin);

  // ── Receipt upload inline (during creation) ─────────────────────────
  const [createReceiptData, setCreateReceiptData] = useState(null); // { sessionId, fileCount }

  // ── Reference number prompt after successful creation ───────────────
  const [refPrompt, setRefPrompt] = useState({ open: false, number: '', vendor: '' });

  // ── Supplier history dialog ────────────────────────────────────────────
  const [historyDialog, setHistoryDialog] = useState(false);
  const [historyVendor, setHistoryVendor] = useState('');
  const [historyPOs, setHistoryPOs] = useState([]);

  const qtyRefs = useRef([]);

  // ── Business info for printing ─────────────────────────────────────────
  const [bizInfo, setBizInfo] = useState({});
  useEffect(() => { api.get('/settings/business-info').then(r => setBizInfo(r.data)).catch(() => {}); }, []);

  const handlePrintPO = async (po, format = 'full_page') => {
    let docCode = po.doc_code || '';
    if (!docCode) {
      try {
        const res = await api.post('/doc/generate-code', { doc_type: 'purchase_order', doc_id: po.id });
        docCode = res.data.code || '';
      } catch { /* print without QR */ }
    }
    PrintEngine.print({ type: 'purchase_order', data: po, format, businessInfo: bizInfo, docCode });
  };

  // ── Init ───────────────────────────────────────────────────────────────
  useEffect(() => {
    api.get('/purchase-orders/vendors').then(r => setVendorsList(r.data)).catch(() => {});
    api.get('/suppliers').then(r => setSuppliers(r.data)).catch(() => {});
    api.get('/price-schemes').then(r => setSchemes(r.data)).catch(() => {});
    fetchOrders();
  }, [currentBranch]);

  // Deep-link: auto-open PO detail when ?open=<po_id> is present
  useEffect(() => {
    const openId = searchParams.get('open');
    if (openId) {
      setTab('list');
      api.get(`/purchase-orders/${openId}`).then(res => {
        setDetailPO(res.data);
        setDetailDialog(true);
      }).catch(() => {});
      // Clear the param so it doesn't re-open on re-render
      setSearchParams({}, { replace: true });
    }
  }, [searchParams]); // eslint-disable-line

  // Supplier search autocomplete
  useEffect(() => {
    if (supplierSearch.length > 0) {
      const all = [...new Set([...vendorsList, ...suppliers.map(s => s.name)])];
      setSupplierResults(all.filter(n => n.toLowerCase().includes(supplierSearch.toLowerCase())).slice(0, 8));
      setShowSupplierDd(true);
    } else { setSupplierResults([]); setShowSupplierDd(false); }
  }, [supplierSearch, vendorsList, suppliers]);

  // Close supplier dropdown on outside click
  useEffect(() => {
    const h = (e) => { if (supplierRef.current && !supplierRef.current.contains(e.target)) setShowSupplierDd(false); };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, []);

  // ── Computed totals ────────────────────────────────────────────────────
  const computed = useMemo(() => {
    const lineDiscounts = lines.map(l => {
      const base = (parseFloat(l.quantity) || 0) * (parseFloat(l.unit_price) || 0);
      if (l.discount_type === 'percent') return Math.round(base * (parseFloat(l.discount_value) || 0) / 100 * 100) / 100;
      return parseFloat(l.discount_value) || 0;
    });
    const lineTotals = lines.map((l, i) => Math.max(0,
      (parseFloat(l.quantity) || 0) * (parseFloat(l.unit_price) || 0) - lineDiscounts[i]
    ));
    const subtotal = lineTotals.reduce((s, t) => s + t, 0);
    const odVal = parseFloat(header.overall_discount_value) || 0;
    const overallDisc = header.overall_discount_type === 'percent'
      ? Math.round(subtotal * odVal / 100 * 100) / 100
      : odVal;
    const afterDiscount = Math.max(0, subtotal - overallDisc);
    const freight = header.show_freight ? (parseFloat(header.freight) || 0) : 0;
    const preTax = afterDiscount + freight;
    const taxAmt = header.show_vat ? Math.round(preTax * header.tax_rate / 100 * 100) / 100 : 0;
    const grandTotal = preTax + taxAmt;
    return { subtotal, lineDiscounts, lineTotals, overallDisc, afterDiscount, freight, preTax, taxAmt, grandTotal };
  }, [lines, header]);

  // ── Fetch orders ───────────────────────────────────────────────────────
  const fetchOrders = async () => {
    try {
      const res = await api.get('/purchase-orders', { params: { limit: 200 } });
      setOrders(res.data.purchase_orders || []);
      setTotalOrders(res.data.total || 0);
    } catch {}
  };

  // ── Supplier actions ───────────────────────────────────────────────────
  const selectSupplier = (name) => {
    setHeader(h => ({ ...h, vendor: name }));
    setSupplierSearch(name);
    setShowSupplierDd(false);
    // Load vendor-specific product prices for this branch
    if (name && currentBranch?.id) {
      api.get('/purchase-orders/vendor-prices', { params: { vendor: name, branch_id: currentBranch.id } })
        .then(r => setVendorPrices(r.data || {}))
        .catch(() => setVendorPrices({}));
    }
  };

  const quickCreateSupplier = async () => {
    if (!supplierSearch.trim()) return;
    try {
      await api.post('/suppliers', { name: supplierSearch.trim() });
      toast.success(`Supplier "${supplierSearch}" created`);
      setHeader(h => ({ ...h, vendor: supplierSearch.trim() }));
      setShowSupplierDd(false);
      const res = await api.get('/suppliers');
      setSuppliers(res.data);
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed'); }
  };

  // ── Line item actions ──────────────────────────────────────────────────
  const handleProductSelect = (index, product) => {
    const nl = [...lines];
    // Use vendor's last price for this product if available, else fall back to product cost
    const vendorPrice = vendorPrices[product.id] || 0;
    const fillPrice = vendorPrice > 0 ? vendorPrice : (product.cost_price || 0);
    nl[index] = { ...nl[index], product_id: product.id, product_name: product.name, unit: product.unit || '', unit_price: fillPrice };
    if (index === lines.length - 1) nl.push({ ...EMPTY_LINE });
    setLines(nl);
    setTimeout(() => qtyRefs.current[index]?.focus(), 50);
  };

  const updateLine = (index, field, value) => {
    const nl = [...lines]; nl[index] = { ...nl[index], [field]: value }; setLines(nl);
  };

  const removeLine = (index) => { if (lines.length > 1) setLines(lines.filter((_, i) => i !== index)); };

  // ── Reset form ─────────────────────────────────────────────────────────
  const resetForm = () => {
    setLines([{ ...EMPTY_LINE }]);
    setHeader({ vendor: '', dr_number: '', po_number: '', purchase_date: today, notes: '', show_freight: false, freight: 0, overall_discount_type: 'amount', overall_discount_value: '', show_vat: false, tax_rate: 12, payment_type: 'cash', terms_label: 'Net 30', terms_days: 30 });
    setSupplierSearch('');
    setVendorPrices({});
    setSourceType('external');
    setSupplyBranchId('');
    setShowRetailToggle(isAdmin);
    setCreateReceiptData(null);
  };

  // ── Validate ───────────────────────────────────────────────────────────
  const validate = () => {
    const valid = lines.filter(l => l.product_id);
    if (!valid.length) { toast.error('Add at least one product'); return null; }
    if (!header.vendor) { toast.error('Enter supplier name'); return null; }
    if (!currentBranch) { toast.error('Select a branch'); return null; }
    return valid;
  };

  const buildPayload = (validLines, extra = {}) => {
    const base = {
      vendor: header.vendor,
      dr_number: header.dr_number,
      po_number: header.po_number,
      purchase_date: header.purchase_date,
      notes: header.notes,
      branch_id: currentBranch.id,
      items: validLines,
      overall_discount_type: header.overall_discount_type,
      overall_discount_value: parseFloat(header.overall_discount_value) || 0,
      freight: header.show_freight ? (parseFloat(header.freight) || 0) : 0,
      tax_rate: header.show_vat ? header.tax_rate : 0,
      grand_total: computed.grandTotal,
      ...extra,
    };
    // Attach inline receipt upload sessions
    if (createReceiptData?.sessionId) {
      base.upload_session_ids = [createReceiptData.sessionId];
    }
    return base;
  };

  // ── Save as Draft ──────────────────────────────────────────────────────
  const handleSaveDraft = async () => {
    const valid = validate(); if (!valid) return;
    setSaving(true);
    try {
      const res = await api.post('/purchase-orders', buildPayload(valid, { po_type: 'draft' }));
      toast.success(`Draft PO ${res.data.po_number} saved`);
      setRefPrompt({ open: true, number: res.data.po_number, vendor: header.vendor });
      resetForm(); fetchOrders(); setTab('list');
    } catch (e) {
      const detail = e.response?.data?.detail;
      const msg = typeof detail === 'string' ? detail
        : detail?.message ? detail.message
        : e.message || 'Error saving PO';
      toast.error(msg);
    }
    setSaving(false);
  };

  // ── Open Cash Dialog ───────────────────────────────────────────────────
  const openCashDialog = async () => {
    const valid = validate(); if (!valid) return;
    if (!createReceiptData?.fileCount) { toast.error('Please upload at least 1 receipt photo before proceeding'); return; }
    setCashLoading(true);
    try {
      const res = await api.get('/purchase-orders/fund-balances', { params: { branch_id: currentBranch.id } });
      const funds = { cashier: res.data.cashier || 0, safe: res.data.safe || 0, cashier_is_negative: res.data.cashier_is_negative };
      setCashFunds(funds);
      // Auto-select Safe if: cashier is negative, or cashier doesn't have enough
      if (funds.cashier_is_negative || funds.cashier < computed.grandTotal) {
        if (funds.safe >= computed.grandTotal) {
          setCashForm(f => ({ ...f, fund_source: 'safe' }));
        } else if (funds.cashier_is_negative) {
          setCashForm(f => ({ ...f, fund_source: 'safe' })); // Safe even if insufficient, user can see warning
        } else {
          setCashForm(f => ({ ...f, fund_source: 'cashier' }));
        }
      } else {
        setCashForm(f => ({ ...f, fund_source: 'cashier' }));
      }
    } catch { setCashFunds({ cashier: 0, safe: 0 }); }
    setCashLoading(false);
    setCashDialog(true);
  };

  const handlePayInCash = async () => {
    const valid = validate(); if (!valid) return;
    const insufficient = cashForm.fund_source === 'cashier'
      ? cashFunds.cashier < computed.grandTotal
      : cashFunds.safe < computed.grandTotal;
    if (insufficient) { toast.error('Insufficient funds in selected source'); return; }
    setSaving(true);
    try {
      const res = await api.post('/purchase-orders', buildPayload(valid, {
        po_type: 'cash',
        fund_source: cashForm.fund_source,
        payment_method_detail: cashForm.payment_method_detail,
        check_number: cashForm.check_number,
      }));
      toast.success(`PO ${res.data.po_number} created — inventory updated, ₱${computed.grandTotal.toFixed(2)} deducted from ${cashForm.fund_source}`);
      setRefPrompt({ open: true, number: res.data.po_number, vendor: header.vendor });
      setCashDialog(false); resetForm(); fetchOrders(); setTab('list');
    } catch (e) {
      const detail = e.response?.data?.detail;
      if (detail?.type === 'insufficient_funds') {
        toast.error(detail.message);
        setCashFunds({ cashier: detail.cashier_balance || 0, safe: detail.safe_balance || 0 });
      } else {
        const msg = typeof detail === 'string' ? detail
          : detail?.message ? detail.message
          : e.response?.data ? JSON.stringify(e.response.data).slice(0, 200)
          : e.message || 'Error creating PO — check your connection and try again';
        toast.error(msg);
      }
    }
    setSaving(false);
  };

  // ── Open Terms Dialog ──────────────────────────────────────────────────
  const openTermsDialog = () => {
    const valid = validate(); if (!valid) return;
    if (!createReceiptData?.fileCount) { toast.error('Please upload at least 1 receipt photo before proceeding'); return; }
    // Pre-populate from header payment type selection
    const days = header.payment_type === 'terms' ? header.terms_days : termsForm.terms_days;
    const label = header.payment_type === 'terms' ? header.terms_label : termsForm.terms_label;
    const due = days > 0
      ? new Date(new Date(header.purchase_date).getTime() + days * 86400000).toISOString().slice(0, 10)
      : header.purchase_date;
    setTermsForm({ terms_days: days, terms_label: label, due_date: due });
    setTermsDialog(true);
  };

  const handleReceiveOnTerms = async () => {
    const valid = validate(); if (!valid) return;
    setSaving(true);
    try {
      const res = await api.post('/purchase-orders', buildPayload(valid, {
        po_type: 'terms',
        terms_days: termsForm.terms_days,
        terms_label: termsForm.terms_label,
        due_date: termsForm.due_date,
      }));
      toast.success(`PO ${res.data.po_number} created — inventory updated, payable created (due ${termsForm.due_date || 'on receipt'})`);
      setRefPrompt({ open: true, number: res.data.po_number, vendor: header.vendor });
      setTermsDialog(false); resetForm(); fetchOrders(); setTab('list');
    } catch (e) {
      const detail = e.response?.data?.detail;
      const msg = typeof detail === 'string' ? detail
        : detail?.message ? detail.message
        : e.response?.data ? JSON.stringify(e.response.data).slice(0, 200)
        : e.message || 'Error creating PO — check your connection and try again';
      toast.error(msg);
    }
    setSaving(false);
  };

  // ── PO List actions ────────────────────────────────────────────────────
  const receivePO = async (poId) => {
    try {
      // 0. Check receipt count first
      const po = orders.find(o => o.id === poId);
      const receiptCount = po?.receipt_count || 0;
      if (receiptCount === 0) {
        toast.error('Receipt upload required before receiving. Open PO detail and upload at least 1 receipt photo.');
        // Open PO detail for the user to upload
        setDetailPO(po);
        setDetailDialog(true);
        return;
      }

      // 1. Get capital preview
      const preview = await api.get(`/purchase-orders/${poId}/capital-preview`);
      const data = preview.data;

      if (data.has_warnings) {
        // 2. Price drops detected — show smart capital dialog
        const defaultChoices = {};
        data.items.forEach(item => {
          // Price drop → default to moving_average (cushions the capital drop)
          // Price same or higher → default to last_purchase (safe to use new price)
          defaultChoices[item.product_id] = item.needs_warning ? 'moving_average' : 'last_purchase';
        });
        setCapitalChoices(defaultChoices);
        setCapitalPreview(data);
        setCapitalPendingPOId(poId);
        setCapitalDialog(true);
      } else {
        // 3. No warnings — confirm and receive directly
        if (!window.confirm('Mark as received? This will add items to inventory.')) return;
        await api.post(`/purchase-orders/${poId}/receive`, { capital_choices: {} });
        toast.success('PO received — inventory updated');
        fetchOrders();
      }
    } catch (e) { toast.error(e.response?.data?.detail || 'Error'); }
  };

  const confirmReceivePO = async () => {
    try {
      await api.post(`/purchase-orders/${capitalPendingPOId}/receive`, { capital_choices: capitalChoices });
      toast.success('PO received — inventory updated');
      setCapitalDialog(false);
      setCapitalPreview(null);
      setCapitalPendingPOId(null);
      fetchOrders();
    } catch (e) { toast.error(e.response?.data?.detail || 'Error'); }
  };

  const sendPOToTerminal = async (poId) => {
    if (!window.confirm('Send this PO to the terminal for checking? It will be locked here until the terminal finalizes it.')) return;
    try {
      await api.post(`/purchase-orders/${poId}/send-to-terminal`);
      toast.success('PO sent to terminal for checking');
      fetchOrders();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed to send to terminal'); }
  };

  const cancelPO = async (poId) => {
    setCancelPinTarget(poId);
    setCancelPin('');
    setCancelPinDialog(true);
  };

  const confirmCancelPO = async () => {
    if (!cancelPin) { toast.error('PIN is required'); return; }
    setCancelSaving(true);
    try {
      await api.delete(`/purchase-orders/${cancelPinTarget}`, { data: { pin: cancelPin } });
      toast.success('PO cancelled');
      setCancelPinDialog(false);
      setCancelPin('');
      setCancelPinTarget(null);
      fetchOrders();
    } catch (e) { toast.error(e.response?.data?.detail || 'Error'); }
    setCancelSaving(false);
  };

  const reopenPO = async (po) => {
    setReopenPinTarget(po);
    setReopenPin('');
    setReopenPinDialog(true);
  };

  const confirmReopenPO = async () => {
    if (!reopenPin) { toast.error('PIN is required'); return; }
    setReopenSaving(true);
    try {
      const res = await api.post(`/purchase-orders/${reopenPinTarget.id}/reopen`, { pin: reopenPin });
      toast.success(res.data.message);
      setReopenPinDialog(false);
      setReopenPin('');
      setReopenPinTarget(null);
      fetchOrders();
    } catch (e) { toast.error(e.response?.data?.detail || 'Error'); }
    setReopenSaving(false);
  };

  const handleMarkReviewed = async () => {
    if (!reviewPin) { toast.error('Enter admin PIN or TOTP'); return; }
    setReviewSaving(true);
    try {
      const res = await api.post(`/purchase-orders/${detailPO.id}/mark-reviewed`, { pin: reviewPin });
      toast.success(res.data.message);
      setReviewPinDialog(false);
      setReviewPin('');
      setDetailPO(prev => ({
        ...prev,
        receipt_review_status: 'reviewed',
        receipt_reviewed_by_name: res.data.reviewed_by,
        receipt_reviewed_at: new Date().toISOString(),
      }));
      fetchOrders();
    } catch (e) { toast.error(e.response?.data?.detail || 'Review failed'); }
    setReviewSaving(false);
  };


  // ── Pay Supplier tab (now standalone page) ────────────────────────────
  const openDetailForEdit = (po) => {
    setDetailPO(po);
    setDetailEditItems(po.items?.map(i => ({ ...i })) || []);
    setDetailEditDR(po.dr_number || '');
    setDetailEditDate(po.purchase_date || '');
    setDetailEditReason('');
    setDetailEditMode(true);
    setDetailDialog(true);
  };

  const saveDetailEdit = async () => {
    if (!detailEditReason.trim()) { toast.error('Please enter a reason for the edit'); return; }
    setDetailSaving(true);
    try {
      const payload = {
        items: detailEditItems,
        dr_number: detailEditDR,
        notes: detailPO.notes,
        edit_reason: detailEditReason,
      };
      // Include date change if modified
      if (detailEditDate && detailEditDate !== detailPO.purchase_date) {
        payload.purchase_date = detailEditDate;
      }
      const res = await api.put(`/purchase-orders/${detailPO.id}`, payload);
      const updatedPO = res.data;
      setDetailPO(updatedPO);
      setDetailEditMode(false);
      toast.success('PO updated!');
      fetchOrders();

      // Check if payment adjustment is needed
      const oldTotal = poTotal(detailPO);
      const newTotal = poTotal(updatedPO);
      const delta = Math.round((newTotal - oldTotal) * 100) / 100;
      const isPaid = detailPO.payment_status === 'paid' || detailPO.po_type === 'cash' || detailPO.payment_method === 'cash';

      if (Math.abs(delta) > 0.01 && isPaid) {
        // Fetch fund balances for the dialog
        try {
          const fundsRes = await api.get('/purchase-orders/fund-balances', { params: { branch_id: currentBranch?.id } });
          setPayAdjFunds({ cashier: fundsRes.data.cashier || 0, safe: fundsRes.data.safe || 0 });
        } catch {}
        setPayAdjData({ po: updatedPO, delta, oldTotal, newTotal });
        setPayAdjReason(detailEditReason);
        setPayAdjFundSource('cashier');
        setPayAdjDialog(true);
        toast.info(`Payment adjustment of ₱${Math.abs(delta).toFixed(2)} ${delta > 0 ? 'needed' : 'to be refunded'} — see the adjustment dialog.`);
      } else if (Math.abs(delta) > 0.01) {
        toast.success('PO updated! Remember to click Receive to update inventory.');
      }
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed to save'); }
    setDetailSaving(false);
  };

  const handlePayAdjustment = async () => {
    if (!payAdjReason.trim()) { toast.error('Please enter a reason'); return; }
    if (!payAdjData) return;
    setPayAdjSaving(true);
    try {
      const res = await api.post(`/purchase-orders/${payAdjData.po.id}/adjust-payment`, {
        new_grand_total: payAdjData.newTotal,
        old_grand_total: payAdjData.oldTotal,
        fund_source: payAdjFundSource,
        reason: payAdjReason,
        payment_method: 'Cash',
      });
      toast.success(res.data.message);
      setPayAdjDialog(false);
      setPayAdjData(null);
      // Refresh the detail PO
      const updated = await api.get(`/purchase-orders/${payAdjData.po.id}`).catch(() => null);
      if (updated) setDetailPO(updated.data);
      fetchOrders();
    } catch (e) {
      const detail = e.response?.data?.detail;
      if (typeof detail === 'object' && detail?.type === 'insufficient_funds') {
        toast.error(detail.message);
      } else {
        toast.error(typeof detail === 'string' ? detail : 'Adjustment failed');
      }
    }
    setPayAdjSaving(false);
  };

  const openSupplierHistory = async (vendor) => {
    setHistoryVendor(vendor);
    try {
      const res = await api.get('/purchase-orders/by-vendor', { params: { vendor } });
      setHistoryPOs(res.data); setHistoryDialog(true);
    } catch { setHistoryPOs([]); }
  };

  const saveNewProduct = async () => {
    try {
      const res = await api.post('/products', newProdForm);
      toast.success(`Product "${res.data.name}" created!`);
      setCreateProdDialog(false);
    } catch (e) { toast.error(e.response?.data?.detail || 'Error'); }
  };

  // ── Filtered PO list ───────────────────────────────────────────────────
  const filteredOrders = useMemo(() => {
    if (listFilter === 'all') return orders;
    if (listFilter === 'draft') return orders.filter(o => o.status === 'draft');
    if (listFilter === 'ordered') return orders.filter(o => o.status === 'ordered' || o.status === 'sent_to_terminal');
    if (listFilter === 'received') return orders.filter(o => o.status === 'received');
    if (listFilter === 'unpaid') return orders.filter(o => o.payment_status !== 'paid' && o.status !== 'cancelled');
    return orders;
  }, [orders, listFilter]);

  const poTotal = (po) => po.grand_total || po.subtotal || 0;

  // ── RENDER ─────────────────────────────────────────────────────────────
  return (
    <div className="space-y-5 animate-fadeIn" data-testid="purchase-order-page">
      <div>
        <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
          <FileText size={22} className="text-[#1A4D2E]" /> Purchase Orders
        </h1>
        <p className="text-sm text-slate-500">Receive stock, manage payables, pay suppliers</p>
      </div>

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="create" data-testid="tab-create-po">
            <Plus size={14} className="mr-1.5" /> New PO
          </TabsTrigger>
          <TabsTrigger value="list" data-testid="tab-list-po">
            <FileText size={14} className="mr-1.5" /> PO List ({totalOrders})
          </TabsTrigger>
        </TabsList>

        {/* ── NEW PO TAB ─────────────────────────────────────────────── */}
        <TabsContent value="create" className="mt-4 space-y-4">

          {/* Header */}
          <Card className="border-slate-200">
            <CardContent className="p-5 space-y-4">
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                {/* Supplier */}
                <div className="relative lg:col-span-2" ref={supplierRef}>
                    <Label className="text-xs text-slate-500">Supplier / Vendor <span className="text-red-500">*</span></Label>
                    <div className="relative mt-1">
                      <Truck size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                      <Input
                        data-testid="po-vendor"
                        className="h-9 pl-8"
                        value={supplierSearch || header.vendor}
                        onChange={e => { setSupplierSearch(e.target.value); setHeader(h => ({ ...h, vendor: e.target.value })); }}
                        onFocus={() => supplierSearch && setShowSupplierDd(true)}
                        placeholder="Type or search supplier..."
                        autoComplete="new-password"
                      />
                  </div>
                  {showSupplierDd && (
                    <div className="absolute z-50 w-full mt-1 bg-white border border-slate-200 rounded-lg shadow-lg max-h-48 overflow-y-auto">
                      {supplierResults.map(name => (
                        <button key={name} onClick={() => selectSupplier(name)}
                          className="w-full text-left px-3 py-2 text-sm hover:bg-slate-50 flex items-center gap-2">
                          <Truck size={12} className="text-slate-400" /> {name}
                        </button>
                      ))}
                      {supplierSearch && !supplierResults.some(n => n.toLowerCase() === supplierSearch.toLowerCase()) && (
                        <button onClick={quickCreateSupplier}
                          className="w-full text-left px-3 py-2 text-sm bg-emerald-50 hover:bg-emerald-100 text-emerald-700 flex items-center gap-2 border-t">
                          <UserPlus size={12} /> Create "{supplierSearch}" as new supplier
                        </button>
                      )}
                    </div>
                  )}
                </div>
                <div>
                  <Label className="text-xs text-slate-500">Purchase Date</Label>
                  <Input className="h-9 mt-1" type="date" value={header.purchase_date}
                    onChange={e => setHeader(h => ({ ...h, purchase_date: e.target.value }))} />
                </div>

                {/* DR / Reference # */}
                <div>
                  <Label className="text-xs text-slate-500">DR / Reference #</Label>
                  <Input className="h-9 mt-1" value={header.dr_number}
                    onChange={e => setHeader(h => ({ ...h, dr_number: e.target.value }))}
                    placeholder="Supplier's Delivery Receipt #" />
                </div>

                {/* Payment Type */}
                <div>
                  <Label className="text-xs text-slate-500">Payment Type</Label>
                  <Select value={header.payment_type} onValueChange={v => setHeader(h => ({ ...h, payment_type: v }))}>
                    <SelectTrigger className="mt-1 h-9" data-testid="po-payment-type">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="cash">Cash (Pay on Receive)</SelectItem>
                      <SelectItem value="terms">Credit / Terms</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                {/* Terms selector — only when credit/terms */}
                {header.payment_type === 'terms' && (
                  <div>
                    <Label className="text-xs text-slate-500">Payment Terms</Label>
                    <Select value={header.terms_label}
                      onValueChange={v => {
                        const opt = TERMS_OPTIONS.find(o => o.label === v) || { label: v, days: 30 };
                        setHeader(h => ({ ...h, terms_label: opt.label, terms_days: opt.days }));
                      }}>
                      <SelectTrigger className="mt-1 h-9" data-testid="po-terms-select">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {TERMS_OPTIONS.map(o => <SelectItem key={o.label} value={o.label}>{o.label}</SelectItem>)}
                      </SelectContent>
                    </Select>
                  </div>
                )}
              </div>

              <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                {/* PO Number */}
                <div>
                  <Label className="text-xs text-slate-500">PO Number (auto if blank)</Label>
                  <Input data-testid="po-number" className="h-9 mt-1" value={header.po_number}
                    onChange={e => setHeader(h => ({ ...h, po_number: e.target.value }))}
                    placeholder="Auto-generated" />
                </div>

                {/* Notes */}
                <div className="lg:col-span-3">
                  <Label className="text-xs text-slate-500">Notes</Label>
                  <Input className="h-9 mt-1" value={header.notes}
                    onChange={e => setHeader(h => ({ ...h, notes: e.target.value }))}
                    placeholder="Optional notes for this purchase order" />
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Line Items */}
          <Card className="border-slate-200">
            <CardContent className="p-0">
              <div className="overflow-x-auto">
                <table className="w-full text-sm" data-testid="po-lines-table">
                  <thead>
                    <tr className="bg-slate-50 border-b">
                      <th className="text-left px-3 py-2 text-xs uppercase text-slate-500 font-medium w-7">#</th>
                      <th className="text-left px-3 py-2 text-xs uppercase text-slate-500 font-medium" style={{minWidth:'260px'}}>Product</th>
                      <th className="text-left px-2 py-2 text-xs uppercase text-slate-500 font-medium w-16">Unit</th>
                      <th className="text-left px-2 py-2 text-xs uppercase text-slate-500 font-medium w-28">Description</th>
                      <th className="text-right px-2 py-2 text-xs uppercase text-slate-500 font-medium w-20">Qty</th>
                      <th className="text-right px-2 py-2 text-xs uppercase text-slate-500 font-medium w-28">Unit Price</th>
                      <th className="text-right px-2 py-2 text-xs uppercase text-slate-500 font-medium w-32">Discount</th>
                      <th className="text-right px-2 py-2 text-xs uppercase text-slate-500 font-medium w-28">Total</th>
                      <th className="w-8"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {lines.map((line, i) => (
                      <tr key={i} className="border-b border-slate-100 hover:bg-slate-50/40">
                        <td className="px-3 py-1 text-xs text-slate-400">{i + 1}</td>
                        <td className="px-2 py-1">
                          {line.product_id ? (
                            <div className="flex items-center gap-1.5">
                              <span className="font-medium text-sm">{line.product_name}</span>
                              <button onClick={() => updateLine(i, 'product_id', '')} className="text-slate-400 hover:text-red-500">&times;</button>
                            </div>
                          ) : (
                            <SmartProductSearch branchId={currentBranch?.id} onSelect={(p) => handleProductSelect(i, p)} onCreateNew={(n) => { setNewProdForm(f => ({ ...f, name: n })); setCreateProdDialog(true); }} />
                          )}
                        </td>
                        <td className="px-2 py-1">
                          <input className="w-full h-8 px-2 text-xs border border-transparent hover:border-slate-200 focus:border-[#1A4D2E] focus:outline-none rounded text-center"
                            value={line.unit} onChange={e => updateLine(i, 'unit', e.target.value)} placeholder="Box" />
                        </td>
                        <td className="px-2 py-1">
                          <input className="w-full h-8 px-2 text-xs border border-transparent hover:border-slate-200 focus:border-[#1A4D2E] focus:outline-none rounded"
                            value={line.description} onChange={e => updateLine(i, 'description', e.target.value)} placeholder="Optional" />
                        </td>
                        <td className="px-2 py-1">
                          <input ref={el => qtyRefs.current[i] = el} type="number" min="0"
                            className="w-full h-8 px-2 text-sm text-right font-mono border border-transparent hover:border-slate-200 focus:border-[#1A4D2E] focus:outline-none rounded"
                            value={line.quantity} onChange={e => updateLine(i, 'quantity', parseFloat(e.target.value) || 0)} />
                        </td>
                        <td className="px-2 py-1">
                          <input type="number" min="0"
                            className="w-full h-8 px-2 text-sm text-right font-mono border border-transparent hover:border-slate-200 focus:border-[#1A4D2E] focus:outline-none rounded"
                            value={line.unit_price} onChange={e => updateLine(i, 'unit_price', parseFloat(e.target.value) || 0)} />
                        </td>
                        <td className="px-2 py-1">
                          <div className="flex gap-1">
                            <select value={line.discount_type} onChange={e => updateLine(i, 'discount_type', e.target.value)}
                              className="h-8 text-xs border border-slate-200 rounded px-1 bg-white focus:outline-none w-12">
                              <option value="amount">₱</option>
                              <option value="percent">%</option>
                            </select>
                            <input type="number" min="0"
                              className="flex-1 h-8 px-2 text-xs text-right font-mono border border-slate-200 hover:border-slate-300 focus:border-[#1A4D2E] focus:outline-none rounded"
                              value={line.discount_value || ''} placeholder="0"
                              onChange={e => updateLine(i, 'discount_value', e.target.value)} />
                          </div>
                          {computed.lineDiscounts[i] > 0 && (
                            <p className="text-[9px] text-emerald-600 text-right mt-0.5">-{formatPHP(computed.lineDiscounts[i])}</p>
                          )}
                        </td>
                        <td className="px-3 py-1 text-right font-semibold text-sm font-mono">
                          {line.product_id ? formatPHP(computed.lineTotals[i]) : ''}
                        </td>
                        <td className="px-1 py-1">
                          {lines.length > 1 && line.product_id && (
                            <button onClick={() => removeLine(i)} className="text-slate-300 hover:text-red-500 p-1"><Trash2 size={13} /></button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>

          {/* Totals + Action Buttons */}
          <div className="flex flex-col lg:flex-row gap-6 items-start justify-between">

            {/* Optional toggles */}
            <div className="flex flex-col gap-2">
              {!header.show_freight && (
                <button onClick={() => setHeader(h => ({ ...h, show_freight: true }))}
                  className="text-xs text-slate-400 hover:text-slate-600 flex items-center gap-1.5">
                  <Plus size={12} /> Add Freight / Shipping Cost
                </button>
              )}
              {header.show_freight && (
                <div className="flex items-center gap-2">
                  <Label className="text-xs text-slate-500 w-32 shrink-0">Freight (₱)</Label>
                  <Input type="number" min="0" className="h-8 w-28 font-mono text-sm text-right"
                    value={header.freight} onChange={e => setHeader(h => ({ ...h, freight: e.target.value }))} />
                  <button onClick={() => setHeader(h => ({ ...h, show_freight: false, freight: 0 }))}
                    className="text-slate-400 hover:text-red-500"><X size={13} /></button>
                </div>
              )}
              {!header.show_vat && (
                <button onClick={() => setHeader(h => ({ ...h, show_vat: true }))}
                  className="text-xs text-slate-400 hover:text-slate-600 flex items-center gap-1.5">
                  <Plus size={12} /> Add VAT
                </button>
              )}
              {header.show_vat && (
                <div className="flex items-center gap-2">
                  <Label className="text-xs text-slate-500 w-32 shrink-0">VAT Rate (%)</Label>
                  <Input type="number" min="0" max="100" className="h-8 w-20 font-mono text-sm text-right"
                    value={header.tax_rate} onChange={e => setHeader(h => ({ ...h, tax_rate: parseFloat(e.target.value) || 0 }))} />
                  <button onClick={() => setHeader(h => ({ ...h, show_vat: false }))}
                    className="text-slate-400 hover:text-red-500"><X size={13} /></button>
                </div>
              )}
            </div>

            {/* Totals + Buttons */}
            <div className="w-full lg:w-80 space-y-2">
              <div className="p-4 bg-slate-50 rounded-lg border border-slate-200 space-y-1">
                <TotalsRow label="Subtotal (before discounts)" value={computed.subtotal} />
                {/* Overall discount */}
                <div className="flex items-center gap-2 py-1">
                  <span className="text-sm text-slate-600 flex-1">Overall Discount</span>
                  <div className="flex gap-1 items-center">
                    <select value={header.overall_discount_type}
                      onChange={e => setHeader(h => ({ ...h, overall_discount_type: e.target.value }))}
                      className="h-7 text-xs border border-slate-200 rounded px-1 bg-white focus:outline-none">
                      <option value="amount">₱</option>
                      <option value="percent">%</option>
                    </select>
                    <input type="number" min="0"
                      className="w-20 h-7 px-2 text-xs text-right font-mono border border-slate-200 rounded focus:outline-none focus:border-[#1A4D2E]"
                      value={header.overall_discount_value} placeholder="0"
                      onChange={e => setHeader(h => ({ ...h, overall_discount_value: e.target.value }))} />
                  </div>
                  {computed.overallDisc > 0 && (
                    <span className="text-xs font-mono text-emerald-600">-{formatPHP(computed.overallDisc)}</span>
                  )}
                </div>
                {header.show_freight && <TotalsRow label="Freight" value={computed.freight} />}
                {header.show_vat && <TotalsRow label={`VAT (${header.tax_rate}%)`} value={computed.taxAmt} />}
                <Separator className="my-1" />
                <TotalsRow label="Grand Total" value={computed.grandTotal} bold accent="text-[#1A4D2E]" />
              </div>

              {/* Action Buttons — change based on source type */}
              {/* Receipt Upload — mandatory for actual PO */}
              <ReceiptUploadInline
                required={true}
                label="Receipt / DR Photo (Required)"
                recordType="purchase_order"
                recordSummary={{
                  type_label: 'Purchase Order',
                  title: header.vendor ? `PO for ${header.vendor}` : 'New Purchase Order',
                  description: `${lines.filter(l => l.product_id).length} item(s)${header.dr_number ? ` · DR# ${header.dr_number}` : ''}`,
                  amount: computed.grandTotal || 0,
                  date: header.purchase_date,
                }}
                onUploaded={(data) => setCreateReceiptData(data)}
              />

              <div className="grid grid-cols-3 gap-2">
                    <Button variant="outline" size="sm" onClick={handleSaveDraft} disabled={saving}
                      data-testid="save-draft-btn" className="flex flex-col h-14 gap-0.5">
                      <Save size={16} /><span className="text-[10px] leading-tight text-center">Save Draft</span>
                    </Button>
                    <Button size="sm" onClick={openTermsDialog} disabled={saving}
                      data-testid="receive-terms-btn"
                      className={`flex flex-col h-14 gap-0.5 text-white ${header.payment_type === 'terms' ? 'ring-2 ring-offset-1 ring-blue-400 bg-blue-600 hover:bg-blue-700' : 'bg-blue-600 hover:bg-blue-700'}`}>
                      <CreditCard size={16} />
                      <span className="text-[10px] leading-tight text-center">
                        {header.payment_type === 'terms' ? `Terms (${header.terms_label})` : 'Receive on Terms'}
                      </span>
                    </Button>
                    <Button size="sm" onClick={openCashDialog} disabled={saving || cashLoading}
                      data-testid="pay-cash-btn"
                      className={`flex flex-col h-14 gap-0.5 text-white ${header.payment_type === 'cash' ? 'ring-2 ring-offset-1 ring-emerald-400 bg-[#1A4D2E] hover:bg-[#14532d]' : 'bg-[#1A4D2E] hover:bg-[#14532d]'}`}>
                      {cashLoading ? <RefreshCw size={16} className="animate-spin" /> : <Wallet size={16} />}
                      <span className="text-[10px] leading-tight text-center">Pay in Cash</span>
                    </Button>
                  </div>
                  <p className="text-[10px] text-slate-400 text-center">
                    {header.payment_type === 'terms'
                      ? `Terms pre-set to ${header.terms_label} — click "Terms" to confirm`
                      : 'Both "Receive" options immediately update inventory'}
                  </p>
            </div>
          </div>
        </TabsContent>

        {/* ── PO LIST TAB ───────────────────────────────────────────── */}
        <TabsContent value="list" className="mt-4 space-y-3">
          {/* Unpaid POs banner → link to Pay Supplier page */}
          {/* Filter chips */}
          <div className="flex items-center gap-2 flex-wrap">
            {[
              { key: 'all', label: `All (${orders.length})` },
              { key: 'draft', label: `Draft (${orders.filter(o => o.status === 'draft').length})` },
              { key: 'ordered', label: `Ordered (${orders.filter(o => o.status === 'ordered' || o.status === 'sent_to_terminal').length})` },
              { key: 'received', label: `Received (${orders.filter(o => o.status === 'received').length})` },
              { key: 'unpaid', label: `Unpaid (${orders.filter(o => o.payment_status !== 'paid' && o.status !== 'cancelled').length})` },
            ].map(f => (
              <button key={f.key} onClick={() => setListFilter(f.key)}
                data-testid={`filter-${f.key}`}
                className={`px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${listFilter === f.key ? 'bg-[#1A4D2E] text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>
                {f.label}
              </button>
            ))}
            <div className="ml-auto flex gap-2">
              <Button variant="outline" size="sm" onClick={() => navigate('/pay-supplier')}
                className="h-7 border-[#1A4D2E] text-[#1A4D2E] hover:bg-[#1A4D2E]/5">
                <Banknote size={12} className="mr-1" /> Pay Supplier
              </Button>
              <Button variant="outline" size="sm" onClick={fetchOrders} className="h-7">
                <RefreshCw size={12} className="mr-1" /> Refresh
              </Button>
            </div>
          </div>

          <Card className="border-slate-200">
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow className="bg-slate-50">
                    <TableHead className="text-xs uppercase text-slate-500">PO #</TableHead>
                    <TableHead className="text-xs uppercase text-slate-500">Supplier</TableHead>
                    <TableHead className="text-xs uppercase text-slate-500">DR #</TableHead>
                    <TableHead className="text-xs uppercase text-slate-500">Items</TableHead>
                    <TableHead className="text-xs uppercase text-slate-500 text-right">Grand Total</TableHead>
                    <TableHead className="text-xs uppercase text-slate-500">Date</TableHead>
                    <TableHead className="text-xs uppercase text-slate-500">Receive</TableHead>
                    <TableHead className="text-xs uppercase text-slate-500">Payment</TableHead>
                    <TableHead className="text-xs uppercase text-slate-500">Receipts</TableHead>
                    <TableHead className="w-36">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredOrders.map(po => (
                    <TableRow key={po.id} className="hover:bg-slate-50">
                      <TableCell className="font-mono text-xs cursor-pointer text-blue-600 hover:underline"
                        onClick={() => { setDetailPO(po); setDetailDialog(true); }}>{po.po_number}</TableCell>
                      <TableCell className="font-medium max-w-[120px] truncate">
                        <button onClick={() => openSupplierHistory(po.vendor)} className="hover:text-blue-600 hover:underline">{po.vendor}</button>
                      </TableCell>
                      <TableCell className="text-xs text-slate-400">{po.dr_number || '—'}</TableCell>
                      <TableCell className="text-slate-500 text-xs">{po.items?.length || 0}</TableCell>
                      <TableCell className="text-right font-semibold font-mono">{formatPHP(poTotal(po))}</TableCell>
                      <TableCell className="text-xs text-slate-500">{po.purchase_date || '—'}</TableCell>
                      <TableCell>
                        <Badge className={`text-[10px] ${statusColor(po.status)}`}>{po.status === 'sent_to_terminal' ? 'On Terminal' : po.status}</Badge>
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-col gap-0.5">
                          {po.status === 'draft' ? (
                            <Badge className="text-[10px] bg-slate-100 text-slate-500">Draft — pending</Badge>
                          ) : (
                            <Badge className={`text-[10px] ${payStatusColor(po.payment_status || (po.payment_method === 'cash' ? 'paid' : 'unpaid'))}`}>
                              {po.po_type === 'cash' || po.payment_method === 'cash' ? 'Cash' : 'Terms'} · {po.payment_status || (po.payment_method === 'cash' ? 'paid' : 'unpaid')}
                            </Badge>
                          )}
                          {po.balance > 0 && <span className="text-[10px] text-red-600 font-mono">{formatPHP(po.balance)}</span>}
                          {po.due_date && po.payment_status !== 'paid' && po.status !== 'draft' && (
                            <span className={`text-[9px] ${new Date(po.due_date) < new Date() ? 'text-red-600 font-semibold' : 'text-slate-400'}`}>
                              {new Date(po.due_date) < new Date() ? '⚠ ' : ''}Due {po.due_date}
                            </span>
                          )}
                          <VerificationBadge doc={po} compact />
                        </div>
                      </TableCell>
                      <TableCell>
                        <div className="flex flex-col gap-0.5">
                          {(po.receipt_count || 0) > 0 ? (
                            <>
                              <Badge className="text-[10px] bg-emerald-100 text-emerald-700">
                                {po.receipt_count} photo{po.receipt_count !== 1 ? 's' : ''}
                              </Badge>
                              {po.receipt_review_status === 'reviewed' ? (
                                <span className="text-[9px] text-emerald-600 flex items-center gap-0.5">
                                  <Check size={9} /> {po.receipt_reviewed_by_name || 'Reviewed'}
                                </span>
                              ) : po.status === 'received' ? (
                                <span className="text-[9px] text-amber-600 font-medium">Pending review</span>
                              ) : null}
                            </>
                          ) : (
                            <Badge className="text-[10px] bg-red-50 text-red-500">No receipts</Badge>
                          )}
                        </div>
                      </TableCell>
                      <TableCell>
                        <div className="flex gap-1 flex-wrap">
                          {(po.status === 'draft' || po.status === 'ordered') && (
                            <Button size="sm" variant="outline" onClick={() => receivePO(po.id)}
                              className="h-7 text-[11px]" data-testid={`receive-po-${po.id}`}>
                              <Check size={11} className="mr-0.5" /> Receive
                            </Button>
                          )}
                          {(po.status === 'draft' || po.status === 'ordered') && (
                            <Button size="sm" variant="outline" onClick={() => sendPOToTerminal(po.id)}
                              className="h-7 text-[11px] text-amber-600 border-amber-200 hover:bg-amber-50"
                              data-testid={`send-terminal-po-${po.id}`}>
                              <Smartphone size={11} className="mr-0.5" /> Send to Terminal
                            </Button>
                          )}
                          {po.status === 'sent_to_terminal' && (
                            <Badge className="text-[10px] bg-amber-100 text-amber-700 flex items-center gap-1">
                              <Lock size={10} /> Locked — checking on terminal
                            </Badge>
                          )}
                          {po.status === 'ordered' && po.terminal_verified && (
                            <Badge className="text-[10px] bg-emerald-100 text-emerald-700 flex items-center gap-1">
                              <Smartphone size={10} /> Terminal verified
                            </Badge>
                          )}
                          {po.status === 'received' && (
                            <Button size="sm" variant="outline" onClick={() => reopenPO(po)}
                              className="h-7 text-[11px] text-amber-600 border-amber-200 hover:bg-amber-50"
                              data-testid={`reopen-po-${po.id}`}>
                              ↩ Reopen
                            </Button>
                          )}
                          {po.payment_status !== 'paid' && (po.po_type === 'terms' || po.payment_method === 'credit') && po.status !== 'cancelled' && (
                            <Button size="sm" variant="outline" onClick={() => navigate('/pay-supplier')}
                              className="h-7 text-[11px]" data-testid={`pay-po-${po.id}`}>
                              <DollarSign size={11} className="mr-0.5" /> Pay
                            </Button>
                          )}
                          {po.status !== 'cancelled' && po.status !== 'received' && (
                            <Button size="sm" variant="ghost" onClick={() => cancelPO(po.id)} className="h-7 text-red-500 px-1.5">
                              <X size={12} />
                            </Button>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                  {!filteredOrders.length && (
                    <TableRow><TableCell colSpan={10} className="text-center py-8 text-slate-400">No purchase orders found</TableCell></TableRow>
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {/* ── PAY IN CASH DIALOG ──────────────────────────────────────── */}
      <Dialog open={cashDialog} onOpenChange={v => { if (!v) setCashDialog(false); }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle style={{ fontFamily: 'Manrope' }} className="flex items-center gap-2">
              <Wallet size={18} className="text-[#1A4D2E]" /> Pay in Cash — {formatPHP(computed.grandTotal)}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4 mt-1">
            {/* Fund source selection */}
            <div>
              <Label className="text-xs text-slate-500 font-medium">Select Fund Source</Label>
              <div className="grid grid-cols-2 gap-3 mt-2">
                {[
                  { key: 'cashier', label: 'Cashier Drawer', balance: cashFunds.cashier },
                  { key: 'safe', label: 'Safe / Vault', balance: cashFunds.safe },
                ].map(f => {
                  const sufficient = f.balance >= computed.grandTotal;
                  const isNegative = f.key === 'cashier' && cashFunds.cashier_is_negative;
                  return (
                    <button key={f.key} onClick={() => setCashForm(c => ({ ...c, fund_source: f.key }))}
                      className={`p-3 rounded-lg border-2 text-left transition-all ${cashForm.fund_source === f.key ? 'border-[#1A4D2E] bg-emerald-50' : 'border-slate-200 hover:border-slate-300'} ${(isNegative || !sufficient) ? 'opacity-80' : ''}`}>
                      <p className="text-xs font-medium text-slate-600">{f.label}</p>
                      <p className={`text-xl font-bold font-mono mt-0.5 ${isNegative ? 'text-red-600' : sufficient ? 'text-[#1A4D2E]' : 'text-red-600'}`}>
                        {formatPHP(f.balance)}
                      </p>
                      {isNegative && (
                        <p className="text-[10px] text-red-600 mt-0.5 flex items-center gap-0.5 font-semibold">
                          <AlertTriangle size={10} /> Cashier is already negative — use Safe instead
                        </p>
                      )}
                      {!isNegative && !sufficient && (
                        <p className="text-[10px] text-red-500 mt-0.5 flex items-center gap-0.5">
                          <AlertTriangle size={10} /> Short {formatPHP(computed.grandTotal - f.balance)}
                        </p>
                      )}
                      {!isNegative && sufficient && cashForm.fund_source === f.key && (
                        <p className="text-[10px] text-emerald-600 mt-0.5">
                          Balance after: {formatPHP(f.balance - computed.grandTotal)}
                        </p>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Payment method */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label className="text-xs text-slate-500">Payment Method</Label>
                <Select value={cashForm.payment_method_detail}
                  onValueChange={v => setCashForm(c => ({ ...c, payment_method_detail: v }))}>
                  <SelectTrigger className="h-9 mt-1"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {PAYMENT_METHODS.map(m => <SelectItem key={m} value={m}>{m}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              {cashForm.payment_method_detail === 'Check' && (
                <div>
                  <Label className="text-xs text-slate-500">Check #</Label>
                  <Input className="h-9 mt-1" value={cashForm.check_number}
                    onChange={e => setCashForm(c => ({ ...c, check_number: e.target.value }))} />
                </div>
              )}
            </div>

            {/* Summary */}
            <div className="p-3 rounded-lg bg-slate-50 border border-slate-200 text-sm space-y-1">
              <p className="font-semibold text-slate-700">On confirm:</p>
              <ul className="text-xs text-slate-500 space-y-0.5 list-disc list-inside">
                <li><b>{formatPHP(computed.grandTotal)}</b> deducted from {cashForm.fund_source === 'safe' ? 'Safe' : 'Cashier'}</li>
                <li>Expense record created: PO Payment — {header.vendor}</li>
                <li>Inventory updated immediately</li>
              </ul>
            </div>

            <div className="flex gap-2">
              <Button variant="outline" className="flex-1" onClick={() => setCashDialog(false)}>Cancel</Button>
              <Button onClick={handlePayInCash} disabled={saving}
                className="flex-1 bg-[#1A4D2E] hover:bg-[#14532d] text-white"
                data-testid="confirm-pay-cash-btn">
                {saving ? <RefreshCw size={14} className="animate-spin mr-1.5" /> : <Check size={14} className="mr-1.5" />}
                Confirm & Receive
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* ── RECEIVE ON TERMS DIALOG ──────────────────────────────────── */}
      <Dialog open={termsDialog} onOpenChange={v => { if (!v) setTermsDialog(false); }}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle style={{ fontFamily: 'Manrope' }} className="flex items-center gap-2">
              <CreditCard size={18} className="text-blue-600" /> Receive on Terms — {formatPHP(computed.grandTotal)}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4 mt-1">
            <div>
              <Label className="text-xs text-slate-500">Payment Terms</Label>
              <Select value={termsForm.terms_label}
                onValueChange={v => {
                  const opt = TERMS_OPTIONS.find(o => o.label === v) || { label: v, days: termsForm.terms_days };
                  const due = opt.days > 0
                    ? new Date(new Date(header.purchase_date).getTime() + opt.days * 86400000).toISOString().slice(0, 10)
                    : header.purchase_date;
                  setTermsForm({ terms_days: opt.days, terms_label: opt.label, due_date: due });
                }}>
                <SelectTrigger className="mt-1 h-9"><SelectValue placeholder="Select terms..." /></SelectTrigger>
                <SelectContent>
                  {TERMS_OPTIONS.map(o => <SelectItem key={o.label} value={o.label}>{o.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label className="text-xs text-slate-500">Terms (days)</Label>
                <Input type="number" min="0" className="h-9 mt-1 font-mono" value={termsForm.terms_days}
                  onChange={e => {
                    const d = parseInt(e.target.value) || 0;
                    const due = d > 0
                      ? new Date(new Date(header.purchase_date).getTime() + d * 86400000).toISOString().slice(0, 10)
                      : header.purchase_date;
                    setTermsForm(f => ({ ...f, terms_days: d, due_date: due }));
                  }} />
              </div>
              <div>
                <Label className="text-xs text-slate-500">Due Date</Label>
                <Input type="date" className="h-9 mt-1" value={termsForm.due_date}
                  onChange={e => setTermsForm(f => ({ ...f, due_date: e.target.value }))} />
              </div>
            </div>
            <div className="p-3 rounded-lg bg-blue-50 border border-blue-200 text-sm space-y-1">
              <p className="font-semibold text-blue-800">On confirm:</p>
              <ul className="text-xs text-blue-700 space-y-0.5 list-disc list-inside">
                <li>Inventory updated immediately</li>
                <li>Accounts Payable created: <b>{formatPHP(computed.grandTotal)}</b> due to {header.vendor}</li>
                <li>Due date: <b>{termsForm.due_date || 'on receipt'}</b></li>
              </ul>
            </div>
            <div className="flex gap-2">
              <Button variant="outline" className="flex-1" onClick={() => setTermsDialog(false)}>Cancel</Button>
              <Button onClick={handleReceiveOnTerms} disabled={saving}
                className="flex-1 bg-blue-600 hover:bg-blue-700 text-white"
                data-testid="confirm-terms-btn">
                {saving ? <RefreshCw size={14} className="animate-spin mr-1.5" /> : <Check size={14} className="mr-1.5" />}
                Confirm & Receive
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* ── PO DETAIL DIALOG ─────────────────────────────────────────── */}
      <Dialog open={detailDialog} onOpenChange={v => { setDetailDialog(v); if (!v) { setDetailEditMode(false); } }}>
        <DialogContent className="sm:max-w-lg max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle style={{ fontFamily: 'Manrope' }}>
              {detailEditMode ? `Edit PO — ${detailPO?.po_number}` : `PO Detail — ${detailPO?.po_number}`}
            </DialogTitle>
            {/* Verification badge row */}
            {detailPO && detailPO.verified && (
              <div className="mt-1 flex items-center gap-2">
                <VerificationBadge doc={detailPO} />
                {detailPO.verified_at && (
                  <span className="text-[10px] text-slate-400">
                    {detailPO.verified_at?.slice(0, 16)?.replace('T', ' ')}
                  </span>
                )}
              </div>
            )}
          </DialogHeader>

          {/* Action toolbar — always visible */}
          {detailPO && (
            <div className="flex flex-wrap items-center gap-1.5 pb-2 border-b border-slate-100" data-testid="po-page-action-bar">
              <Button size="sm" variant="outline" className="h-7 text-xs"
                onClick={() => handlePrintPO(detailPO, 'full_page')} data-testid="po-print-btn">
                <Printer size={12} className="mr-1" /> Print Full
              </Button>
              <Button size="sm" variant="outline" className="h-7 text-xs"
                onClick={() => handlePrintPO(detailPO, 'thermal')} data-testid="po-print-thermal-btn">
                <Printer size={12} className="mr-1" /> Print 58mm
              </Button>
              <Button size="sm" variant="outline" className="h-7 text-xs"
                onClick={() => { setViewQROpen(true); setViewQRFileCount(0); }} data-testid="po-view-phone-btn">
                <Package size={12} className="mr-1" /> View on Phone
              </Button>
              <Button size="sm" variant="outline" className="h-7 text-xs"
                onClick={() => { setUploadRecordId(detailPO?.id); setUploadQROpen(true); }} data-testid="po-upload-receipt-btn">
                <Upload size={12} className="mr-1" /> Upload Receipt
              </Button>
              {!detailPO.verified && (
                <Button size="sm" variant="outline" className="h-7 text-xs text-[#1A4D2E] border-[#1A4D2E]/30 hover:bg-[#1A4D2E]/5"
                  onClick={() => setVerifyDialogOpen(true)} data-testid="po-verify-btn">
                  <ShieldCheck size={12} className="mr-1" /> Verify
                </Button>
              )}
              {detailPO?.status === 'ordered' && detailPO?.reopened_at && !detailEditMode && (
                <Button size="sm" variant="outline" className="h-7 text-xs text-amber-600 border-amber-200 hover:bg-amber-50"
                  onClick={() => openDetailForEdit(detailPO)} data-testid="po-edit-btn">
                  <Pencil size={12} className="mr-1" /> Edit
                </Button>
              )}
            </div>
          )}

          {detailPO && (
            <div className="space-y-4">
              {/* Reopened banner */}
              {detailPO.reopened_at && (
                <div className="p-2.5 rounded-lg bg-amber-50 border border-amber-200 text-xs text-amber-800 flex items-center gap-2">
                  <AlertTriangle size={12} className="shrink-0 text-amber-600" />
                  <span>This PO was reopened by <b>{detailPO.reopened_by}</b>. Inventory was reversed. Edit then click <b>Receive</b> to re-add stock.</span>
                </div>
              )}

              {/* Receipts gallery (if uploaded) */}
              <ReceiptGallery recordType="purchase_order" recordId={detailPO.id} />

              {/* Receipt Review Status */}
              {(detailPO.receipt_count > 0 || detailPO.receipt_review_status) && (
                <div className={`p-3 rounded-xl border ${
                  detailPO.receipt_review_status === 'reviewed'
                    ? 'bg-emerald-50 border-emerald-200'
                    : 'bg-amber-50 border-amber-200'
                }`}>
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      {detailPO.receipt_review_status === 'reviewed' ? (
                        <>
                          <ShieldCheck size={16} className="text-emerald-600" />
                          <div>
                            <p className="text-xs font-semibold text-emerald-700">Receipts Reviewed</p>
                            <p className="text-[10px] text-emerald-600">
                              by {detailPO.receipt_reviewed_by_name} {detailPO.receipt_reviewed_at ? `on ${detailPO.receipt_reviewed_at.slice(0, 10)}` : ''}
                            </p>
                          </div>
                        </>
                      ) : (
                        <>
                          <AlertTriangle size={16} className="text-amber-600" />
                          <div>
                            <p className="text-xs font-semibold text-amber-700">Receipts Pending Review</p>
                            <p className="text-[10px] text-amber-600">{detailPO.receipt_count || 0} photo(s) attached</p>
                          </div>
                        </>
                      )}
                    </div>
                    {detailPO.receipt_review_status !== 'reviewed' && detailPO.status === 'received' && (
                      <Button size="sm" variant="outline"
                        className="h-7 text-xs text-[#1A4D2E] border-[#1A4D2E]/40 hover:bg-[#1A4D2E]/10"
                        onClick={() => setReviewPinDialog(true)}
                        data-testid="mark-reviewed-btn">
                        <ShieldCheck size={12} className="mr-1" /> Mark as Reviewed
                      </Button>
                    )}
                  </div>
                </div>
              )}

              {/* Header info */}
              <div className="grid grid-cols-2 gap-3 text-sm">
                <div><span className="text-slate-500">Vendor:</span> <b>{detailPO.vendor}</b></div>
                <div>
                  <span className="text-slate-500">Date:</span>{' '}
                  {detailEditMode ? (
                    <Input type="date" value={detailEditDate} onChange={e => setDetailEditDate(e.target.value)}
                      className="h-7 text-sm mt-0.5 w-full" />
                  ) : detailPO.purchase_date}
                </div>
                <div>
                  <span className="text-slate-500">DR #:</span>{' '}
                  {detailEditMode ? (
                    <Input value={detailEditDR} onChange={e => setDetailEditDR(e.target.value)}
                      className="h-7 text-sm mt-0.5 w-full" placeholder="DR number" />
                  ) : <b>{detailPO.dr_number || '—'}</b>}
                </div>
                <div><span className="text-slate-500">Status:</span> <Badge className={`${statusColor(detailPO.status)} text-[10px]`}>{detailPO.status}</Badge></div>
                <div className="flex items-center gap-1"><span className="text-slate-500">Payment:</span>
                  <Badge className={`text-[10px] ${payStatusColor(detailPO.payment_status || 'unpaid')}`}>
                    {detailPO.po_type === 'cash' || detailPO.payment_method === 'cash' ? 'Cash' : 'Terms'} · {detailPO.payment_status || 'unpaid'}
                  </Badge>
                </div>
                {detailPO.balance > 0 && <div><span className="text-slate-500">Balance:</span> <b className="text-red-600">{formatPHP(detailPO.balance)}</b></div>}
                {detailPO.due_date && <div><span className="text-slate-500">Due:</span> {detailPO.due_date}</div>}
              </div>

              {/* Items table — view or edit mode */}
              {detailEditMode ? (
                <div className="space-y-2">
                  <p className="text-xs text-slate-500 font-medium uppercase">Edit Items</p>
                  {detailEditItems.map((item, i) => (
                    <div key={i} className="grid grid-cols-12 gap-1.5 items-center p-2 bg-slate-50 rounded-lg border border-slate-200">
                      <div className="col-span-5 text-xs font-medium truncate">{item.product_name}</div>
                      <div className="col-span-3">
                        <Label className="text-[9px] text-slate-400">Qty</Label>
                        <Input type="number" min={0} value={item.quantity}
                          onChange={e => { const n = [...detailEditItems]; n[i] = { ...n[i], quantity: parseFloat(e.target.value) || 0 }; setDetailEditItems(n); }}
                          className="h-7 text-sm text-right font-mono" />
                      </div>
                      <div className="col-span-4">
                        <Label className="text-[9px] text-slate-400">Unit Price</Label>
                        <Input type="number" min={0} value={item.unit_price}
                          onChange={e => { const n = [...detailEditItems]; n[i] = { ...n[i], unit_price: parseFloat(e.target.value) || 0 }; setDetailEditItems(n); }}
                          className="h-7 text-sm text-right font-mono" />
                      </div>
                    </div>
                  ))}
                  <div className="mt-2">
                    <Label className="text-xs text-slate-500">Reason for Edit <span className="text-red-500">*</span></Label>
                    <Input value={detailEditReason}
                      onChange={e => setDetailEditReason(e.target.value)}
                      placeholder="e.g. Supplier corrected quantity on actual DR, price was wrong on original..."
                      className="mt-1 h-9 text-sm" />
                    <p className="text-[10px] text-slate-400 mt-0.5">This reason will be saved in the edit history.</p>
                  </div>
                  <div className="flex gap-2 pt-2 border-t">
                    <Button variant="outline" onClick={() => setDetailEditMode(false)} className="flex-1">Cancel Edit</Button>
                    <Button onClick={saveDetailEdit} disabled={detailSaving}
                      className="flex-1 bg-amber-600 hover:bg-amber-700 text-white">
                      {detailSaving ? <RefreshCw size={13} className="animate-spin mr-1.5" /> : <Check size={13} className="mr-1.5" />}
                      Save Changes
                    </Button>
                  </div>
                  <p className="text-[10px] text-amber-600 text-center">After saving, use the Receive button in the PO list to re-add inventory.</p>
                </div>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="text-xs">Product</TableHead>
                      <TableHead className="text-xs">Unit</TableHead>
                      <TableHead className="text-xs text-right">Qty</TableHead>
                      <TableHead className="text-xs text-right">Price</TableHead>
                      <TableHead className="text-xs text-right">Disc</TableHead>
                      <TableHead className="text-xs text-right">Total</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {detailPO.items?.map((item, i) => (
                      <TableRow key={i}>
                        <TableCell className="text-sm">{item.product_name}</TableCell>
                        <TableCell className="text-xs text-slate-500">{item.unit || '—'}</TableCell>
                        <TableCell className="text-right">{item.quantity}</TableCell>
                        <TableCell className="text-right font-mono">{formatPHP(item.unit_price)}</TableCell>
                        <TableCell className="text-right text-xs text-emerald-600">{item.discount_amount > 0 ? `-${formatPHP(item.discount_amount)}` : '—'}</TableCell>
                        <TableCell className="text-right font-semibold font-mono">{formatPHP(item.total || item.quantity * item.unit_price)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}

              {!detailEditMode && (
                <>
                  <div className="text-sm space-y-1 border-t pt-3">
                    <div className="flex justify-between"><span className="text-slate-500">Subtotal</span><span className="font-mono">{formatPHP(detailPO.line_subtotal || detailPO.subtotal)}</span></div>
                    {detailPO.overall_discount_amount > 0 && <div className="flex justify-between text-emerald-600"><span>Overall Discount</span><span className="font-mono">-{formatPHP(detailPO.overall_discount_amount)}</span></div>}
                    {detailPO.freight > 0 && <div className="flex justify-between"><span className="text-slate-500">Freight</span><span className="font-mono">{formatPHP(detailPO.freight)}</span></div>}
                    {detailPO.tax_amount > 0 && <div className="flex justify-between"><span className="text-slate-500">VAT ({detailPO.tax_rate}%)</span><span className="font-mono">{formatPHP(detailPO.tax_amount)}</span></div>}
                    <div className="flex justify-between font-bold text-base pt-1 border-t"><span>Grand Total</span><span className="font-mono text-[#1A4D2E]">{formatPHP(detailPO.grand_total || detailPO.subtotal)}</span></div>
                  </div>
                  {detailPO.notes && <p className="text-sm text-slate-500 border-t pt-2">Notes: {detailPO.notes}</p>}
                  {detailPO.edit_history?.length > 0 && (
                    <div className="border-t pt-2">
                      <p className="text-xs font-semibold uppercase text-slate-400 mb-2">Edit History</p>
                      {detailPO.edit_history.map((edit, i) => (
                        <div key={i} className="text-xs p-2 bg-amber-50 rounded mb-1.5 border border-amber-100">
                          <div className="flex items-center justify-between mb-0.5">
                            <span className="font-semibold text-amber-800">{edit.changed_by}</span>
                            <span className="text-slate-400">{edit.changed_at?.slice(0, 10)}</span>
                          </div>
                          <p className="text-slate-600 italic">"{edit.reason}"</p>
                          <p className="text-slate-500 mt-0.5">{edit.change_summary}</p>
                        </div>
                      ))}
                    </div>
                  )}
                  {detailPO.payment_history?.length > 0 && (
                    <div className="border-t pt-2">
                      <p className="text-xs font-semibold uppercase text-slate-400 mb-2">Payment History</p>
                      {detailPO.payment_history.map((pay, i) => (
                        <div key={i} className="flex items-center justify-between text-xs py-1 border-b last:border-0">
                          <div className="flex items-center gap-2">
                            <Check size={10} className="text-emerald-500" />
                            <span>{pay.date}</span>
                            <span className="text-slate-400">{pay.method}</span>
                            {pay.check_number && <span className="text-slate-400">#{pay.check_number}</span>}
                            <span className="text-slate-400">{pay.fund_source || ''}</span>
                          </div>
                          <span className="font-bold text-emerald-600">{formatPHP(pay.amount)}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* ── UPLOAD QR DIALOG ────────────────────────────────────────────── */}
      <UploadQRDialog
        open={uploadQROpen}
        onClose={(count) => {
          setUploadQROpen(false);
          if (count > 0) {
            toast.success(`${count} receipt photo(s) uploaded!`);
            // Update receipt count in detail PO and list
            if (detailPO) setDetailPO(prev => ({ ...prev, receipt_count: (prev.receipt_count || 0) + count }));
            fetchOrders();
          }
        }}
        recordType="purchase_order"
        recordId={uploadRecordId}
      />

      {/* ── VIEW QR DIALOG ────────────────────────────────────────────── */}
      <ViewQRDialog
        open={viewQROpen}
        onClose={() => setViewQROpen(false)}
        recordType="purchase_order"
        recordId={detailPO?.id}
        fileCount={viewQRFileCount}
      />

      {/* ── VERIFY PIN DIALOG ─────────────────────────────────────────── */}
      <VerifyPinDialog
        open={verifyDialogOpen}
        onClose={() => setVerifyDialogOpen(false)}
        docType="purchase_order"
        docId={detailPO?.id}
        docLabel={detailPO?.po_number}
        onVerified={(result) => {
          setVerifyDialogOpen(false);
          // Refresh the PO detail and list
          if (detailPO) {
            setDetailPO(prev => ({
              ...prev,
              verified: true,
              verified_by_name: result.verified_by,
              verified_at: new Date().toISOString(),
              verification_status: result.status,
              has_discrepancy: result.status === 'discrepancy',
            }));
          }
          fetchOrders();
        }}
      />

      {/* ── MARK AS REVIEWED PIN DIALOG ──────────────────────────────── */}
      <Dialog open={reviewPinDialog} onOpenChange={v => { if (!v) { setReviewPinDialog(false); setReviewPin(''); } }}>
        <DialogContent className="sm:max-w-xs">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
              <ShieldCheck size={18} className="text-[#1A4D2E]" /> Review Receipts
            </DialogTitle>
            <DialogDescription>
              Enter your admin PIN or TOTP to confirm you have reviewed the receipt photos for PO {detailPO?.po_number}.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3 mt-2">
            <div>
              <Label className="text-xs">Admin PIN or TOTP</Label>
              <Input
                type="password" autoComplete="new-password"
                value={reviewPin}
                onChange={e => setReviewPin(e.target.value)}
                placeholder="Enter PIN..."
                className="mt-1"
                onKeyDown={e => { if (e.key === 'Enter') handleMarkReviewed(); }}
                data-testid="review-pin-input"
              />
            </div>
            <Button
              onClick={handleMarkReviewed}
              disabled={reviewSaving || !reviewPin}
              className="w-full bg-[#1A4D2E] hover:bg-[#14532d] text-white"
              data-testid="confirm-review-btn"
            >
              {reviewSaving ? <RefreshCw size={13} className="animate-spin mr-1.5" /> : <ShieldCheck size={13} className="mr-1.5" />}
              Confirm Review
            </Button>
          </div>
        </DialogContent>
      </Dialog>


      {/* ── CANCEL PO PIN DIALOG ─────────────────────────────────────── */}
      <Dialog open={cancelPinDialog} onOpenChange={v => { if (!v) { setCancelPinDialog(false); setCancelPin(''); } }}>
        <DialogContent className="sm:max-w-xs">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-red-600" style={{ fontFamily: 'Manrope' }}>
              <X size={18} /> Cancel Purchase Order
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3 mt-2">
            <p className="text-sm text-slate-500">Enter your PIN to confirm cancellation.</p>
            <Input type="password" autoComplete="new-password" value={cancelPin} onChange={e => setCancelPin(e.target.value)}
              placeholder="Enter PIN..." onKeyDown={e => { if (e.key === 'Enter') confirmCancelPO(); }}
              data-testid="cancel-po-pin-input" />
            <div className="flex gap-2">
              <Button variant="outline" className="flex-1" onClick={() => { setCancelPinDialog(false); setCancelPin(''); }}>Back</Button>
              <Button variant="destructive" className="flex-1" onClick={confirmCancelPO} disabled={cancelSaving || !cancelPin} data-testid="confirm-cancel-po-btn">
                {cancelSaving ? <RefreshCw size={13} className="animate-spin mr-1.5" /> : <X size={13} className="mr-1.5" />}
                Cancel PO
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* ── REOPEN PO PIN DIALOG ─────────────────────────────────────── */}
      <Dialog open={reopenPinDialog} onOpenChange={v => { if (!v) { setReopenPinDialog(false); setReopenPin(''); } }}>
        <DialogContent className="sm:max-w-xs">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-amber-600" style={{ fontFamily: 'Manrope' }}>
              <AlertTriangle size={18} /> Reopen Purchase Order
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3 mt-2">
            <p className="text-sm text-amber-700">This will reverse inventory and payments for <b>{reopenPinTarget?.po_number}</b>.</p>
            <Input type="password" autoComplete="new-password" value={reopenPin} onChange={e => setReopenPin(e.target.value)}
              placeholder="Enter PIN..." onKeyDown={e => { if (e.key === 'Enter') confirmReopenPO(); }}
              data-testid="reopen-po-pin-input" />
            <div className="flex gap-2">
              <Button variant="outline" className="flex-1" onClick={() => { setReopenPinDialog(false); setReopenPin(''); }}>Back</Button>
              <Button className="flex-1 bg-amber-600 hover:bg-amber-700 text-white" onClick={confirmReopenPO} disabled={reopenSaving || !reopenPin} data-testid="confirm-reopen-po-btn">
                {reopenSaving ? <RefreshCw size={13} className="animate-spin mr-1.5" /> : <AlertTriangle size={13} className="mr-1.5" />}
                Reopen PO
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>


      {/* ── CREATE PRODUCT DIALOG ────────────────────────────────────── */}
      <Dialog open={createProdDialog} onOpenChange={setCreateProdDialog}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader><DialogTitle style={{ fontFamily: 'Manrope' }}>Create New Product</DialogTitle></DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="grid grid-cols-2 gap-4">
              <div><Label>SKU</Label><Input value={newProdForm.sku} onChange={e => setNewProdForm(f => ({ ...f, sku: e.target.value }))} placeholder="e.g. LAN-250G" /></div>
              <div><Label>Product Name</Label><Input value={newProdForm.name} onChange={e => setNewProdForm(f => ({ ...f, name: e.target.value }))} /></div>
            </div>
            <div className="grid grid-cols-3 gap-4">
              <div><Label>Category</Label>
                <Select value={newProdForm.category} onValueChange={v => setNewProdForm(f => ({ ...f, category: v }))}>
                  <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {['Pesticide','Fertilizers','Seeds','Feeds','Tools','Veterinary','Customized','Others'].map(c => <SelectItem key={c} value={c}>{c}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div><Label>Unit</Label><Input value={newProdForm.unit} onChange={e => setNewProdForm(f => ({ ...f, unit: e.target.value }))} /></div>
              <div><Label>Cost Price</Label><Input type="number" value={newProdForm.cost_price} onChange={e => setNewProdForm(f => ({ ...f, cost_price: parseFloat(e.target.value) || 0 }))} /></div>
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setCreateProdDialog(false)}>Cancel</Button>
              <Button onClick={saveNewProduct} className="bg-[#1A4D2E] hover:bg-[#14532d] text-white">Create Product</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* ── SUPPLIER HISTORY DIALOG ──────────────────────────────────── */}
      <Dialog open={historyDialog} onOpenChange={setHistoryDialog}>
        <DialogContent className="sm:max-w-3xl max-h-[85vh] overflow-y-auto">
          <DialogHeader><DialogTitle style={{ fontFamily: 'Manrope' }}>Supplier History — {historyVendor}</DialogTitle></DialogHeader>
          <div className="space-y-3 mt-2">
            {historyPOs.map(po => (
              <Card key={po.id} className={`border-slate-200 ${po.payment_status === 'paid' ? 'opacity-70' : ''}`}>
                <CardContent className="p-4 space-y-2">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <button onClick={() => { setDetailPO(po); setDetailDialog(true); }}
                        className="font-mono text-sm text-blue-600 hover:underline font-bold">{po.po_number}</button>
                      <Badge className={`text-[10px] ${statusColor(po.status)}`}>{po.status}</Badge>
                      <Badge className={`text-[10px] ${payStatusColor(po.payment_status || 'unpaid')}`}>{po.payment_status || 'unpaid'}</Badge>
                    </div>
                    <span className="text-lg font-bold font-mono">{formatPHP(poTotal(po))}</span>
                  </div>
                  <div className="text-xs text-slate-500">
                    Date: {po.purchase_date} · Items: {po.items?.length || 0}
                    {po.dr_number && <> · DR: <span className="font-mono">{po.dr_number}</span></>}
                    {po.balance > 0 && <> · <span className="text-red-600 font-semibold">Balance: {formatPHP(po.balance)}</span></>}
                    {po.due_date && po.payment_status !== 'paid' && <> · Due: {po.due_date}</>}
                  </div>
                  {po.payment_history?.length > 0 && (
                    <div className="mt-1 bg-slate-50 rounded p-2 space-y-0.5">
                      {po.payment_history.map((pay, i) => (
                        <div key={i} className="flex items-center justify-between text-xs">
                          <span className="text-slate-500">{pay.date} · {pay.method} · {pay.fund_source}</span>
                          <span className="font-bold text-emerald-600">{formatPHP(pay.amount)}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>
            ))}
            {!historyPOs.length && <p className="text-center py-8 text-slate-400">No purchase orders found</p>}
          </div>
        </DialogContent>
      </Dialog>

      {/* ── PAYMENT ADJUSTMENT DIALOG ───────────────────────────────────── */}
      {payAdjDialog && payAdjData && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="bg-white rounded-2xl shadow-2xl max-w-md w-full space-y-4 max-h-[90vh] overflow-y-auto p-6">
            <div className="flex items-center gap-3">
              <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${payAdjData.delta > 0 ? 'bg-amber-100' : 'bg-blue-100'}`}>
                <Banknote size={20} className={payAdjData.delta > 0 ? 'text-amber-700' : 'text-blue-700'} />
              </div>
              <div>
                <h3 className="font-bold text-slate-800" style={{ fontFamily: 'Manrope' }}>
                  {payAdjData.delta > 0 ? 'Additional Payment Required' : 'Overpayment — Refund Due'}
                </h3>
                <p className="text-xs text-slate-500">{payAdjData.po.po_number} · {payAdjData.po.vendor}</p>
              </div>
            </div>

            {/* What changed */}
            <div className="p-3 rounded-xl bg-slate-50 border border-slate-200 text-sm space-y-1">
              <div className="flex justify-between"><span className="text-slate-500">Original total</span><span className="font-mono">{formatPHP(payAdjData.oldTotal)}</span></div>
              <div className="flex justify-between"><span className="text-slate-500">New total (after edit)</span><span className="font-mono font-bold">{formatPHP(payAdjData.newTotal)}</span></div>
              <div className="flex justify-between pt-1 border-t font-bold">
                <span>{payAdjData.delta > 0 ? 'Still owed' : 'Overpaid'}</span>
                <span className={`font-mono text-base ${payAdjData.delta > 0 ? 'text-amber-700' : 'text-blue-700'}`}>
                  {payAdjData.delta > 0 ? '' : '-'}{formatPHP(Math.abs(payAdjData.delta))}
                </span>
              </div>
            </div>

            {/* Explanation */}
            <div className={`p-3 rounded-xl text-xs ${payAdjData.delta > 0 ? 'bg-amber-50 border border-amber-200 text-amber-800' : 'bg-blue-50 border border-blue-200 text-blue-800'}`}>
              {payAdjData.delta > 0
                ? `The edited PO total increased by ₱${Math.abs(payAdjData.delta).toFixed(2)}. You need to pay the difference from a fund.`
                : `You previously paid ₱${Math.abs(payAdjData.delta).toFixed(2)} more than the new total. This will be returned to the selected fund.`
              }
            </div>

            {/* Fund source — always ask explicitly, show balances with warnings */}
            <div>
              <Label className="text-xs font-semibold text-slate-600 uppercase tracking-wide">
                Where to {payAdjData.delta > 0 ? 'get the funds from' : 'return the funds to'}?
              </Label>
              <div className="grid grid-cols-2 gap-2 mt-2">
                {[
                  { k: 'cashier', label: 'Cashier Drawer', bal: payAdjFunds.cashier },
                  { k: 'safe', label: 'Safe / Vault', bal: payAdjFunds.safe },
                ].map(f => {
                  const isNegative = f.k === 'cashier' && payAdjFunds.cashier < 0;
                  const wouldGoNeg = payAdjData.delta > 0 && f.bal < Math.abs(payAdjData.delta);
                  const selected = payAdjFundSource === f.k;
                  return (
                    <button key={f.k} onClick={() => setPayAdjFundSource(f.k)}
                      className={`p-3 rounded-xl border-2 text-left transition-all ${selected ? 'border-[#1A4D2E] bg-emerald-50' : 'border-slate-200 hover:border-slate-300'}`}>
                      <p className="text-xs font-medium text-slate-600 mb-0.5">{f.label}</p>
                      <p className={`text-lg font-bold font-mono ${isNegative ? 'text-red-600' : wouldGoNeg ? 'text-amber-600' : 'text-[#1A4D2E]'}`}>
                        {formatPHP(f.bal)}
                      </p>
                      {isNegative && (
                        <p className="text-[9px] text-red-600 mt-0.5 font-semibold flex items-center gap-0.5">
                          <AlertTriangle size={9} /> Already negative — use Safe
                        </p>
                      )}
                      {!isNegative && wouldGoNeg && payAdjData.delta > 0 && (
                        <p className="text-[9px] text-amber-600 mt-0.5 flex items-center gap-0.5">
                          <AlertTriangle size={9} /> Short ₱{(Math.abs(payAdjData.delta) - f.bal).toFixed(2)}
                        </p>
                      )}
                      {selected && !isNegative && !wouldGoNeg && payAdjData.delta > 0 && (
                        <p className="text-[9px] text-emerald-600 mt-0.5">After: {formatPHP(f.bal - Math.abs(payAdjData.delta))}</p>
                      )}
                      {selected && payAdjData.delta < 0 && (
                        <p className="text-[9px] text-blue-600 mt-0.5">After: {formatPHP(f.bal + Math.abs(payAdjData.delta))}</p>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Reason */}
            <div>
              <Label className="text-xs text-slate-500">Reason for Adjustment</Label>
              <Input value={payAdjReason} onChange={e => setPayAdjReason(e.target.value)}
                className="mt-1 h-9 text-sm" placeholder="e.g. Corrected quantity per supplier's DR" />
            </div>

            <div className="flex gap-2 pt-1 border-t">
              <Button variant="outline" className="flex-1" onClick={() => setPayAdjDialog(false)}>
                Skip for Now
              </Button>
              <Button onClick={handlePayAdjustment} disabled={payAdjSaving || !payAdjReason.trim()}
                className={`flex-1 text-white ${payAdjData.delta > 0 ? 'bg-amber-600 hover:bg-amber-700' : 'bg-blue-600 hover:bg-blue-700'}`}>
                {payAdjSaving ? <RefreshCw size={13} className="animate-spin mr-1.5" /> : <Check size={13} className="mr-1.5" />}
                {payAdjData.delta > 0 ? `Pay ${formatPHP(Math.abs(payAdjData.delta))}` : `Refund ${formatPHP(Math.abs(payAdjData.delta))}`}
              </Button>
            </div>
            <p className="text-[10px] text-slate-400 text-center">
              Creates an audit-traceable adjustment record. You can also settle later via Pay Supplier.
            </p>
          </div>
        </div>
      )}

    {/* Smart Capital Pricing Dialog */}
    {capitalDialog && capitalPreview && (
      <Dialog open={capitalDialog} onOpenChange={setCapitalDialog}>
        <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-amber-700">
              <TrendingDown size={18} className="text-amber-500" />
              Smart Capital Pricing — Price Drop Detected
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-sm text-amber-800">
              <strong>PO {capitalPreview.po_number}</strong> from <strong>{capitalPreview.vendor}</strong> contains items
              priced <strong>lower than their current capital</strong>. Choose how to update each product's capital.
            </div>

            {/* Bulk actions */}
            <div className="flex gap-2">
              <Button size="sm" variant="outline" className="text-xs border-slate-300"
                onClick={() => {
                  const all = {};
                  capitalPreview.items.forEach(i => { all[i.product_id] = 'last_purchase'; });
                  setCapitalChoices(all);
                }}>
                Use all new prices
              </Button>
              <Button size="sm" variant="outline" className="text-xs border-slate-300"
                onClick={() => {
                  const all = {};
                  capitalPreview.items.forEach(i => { all[i.product_id] = 'moving_average'; });
                  setCapitalChoices(all);
                }}>
                Use all moving averages
              </Button>
            </div>

            {/* Items table */}
            <div className="border rounded-lg overflow-hidden">
              <table className="w-full text-xs">
                <thead className="bg-slate-50">
                  <tr>
                    <th className="text-left px-3 py-2 font-medium text-slate-600">Product</th>
                    <th className="text-right px-3 py-2 font-medium text-slate-600">Current Capital</th>
                    <th className="text-right px-3 py-2 font-medium text-slate-600">New PO Price</th>
                    <th className="text-right px-3 py-2 font-medium text-slate-600">Moving Avg</th>
                    <th className="text-center px-3 py-2 font-medium text-slate-600">Use</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {capitalPreview.items.map(item => (
                    <tr key={item.product_id} className={item.needs_warning ? 'bg-amber-50/60' : ''}>
                      <td className="px-3 py-2.5">
                        <div className="font-medium text-slate-800 leading-tight">{item.product_name}</div>
                        <div className="text-[10px] text-slate-400">{item.sku} · {item.qty} {item.unit}</div>
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono text-slate-700">
                        ₱{item.current_capital.toFixed(2)}
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono">
                        {item.needs_warning ? (
                          <span className="text-amber-700 font-semibold flex items-center justify-end gap-1">
                            <TrendingDown size={12} />₱{item.new_price.toFixed(2)}
                            <span className="text-[10px] text-amber-500">(-{item.price_drop_pct}%)</span>
                          </span>
                        ) : (
                          <span className="text-emerald-700 flex items-center justify-end gap-1">
                            <TrendingUp size={12} />₱{item.new_price.toFixed(2)}
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono text-slate-500">
                        ₱{item.projected_moving_avg.toFixed(2)}
                      </td>
                      <td className="px-3 py-2.5">
                        {item.needs_warning ? (
                          <div className="flex gap-1 justify-center">
                            <button
                              onClick={() => setCapitalChoices(prev => ({ ...prev, [item.product_id]: 'last_purchase' }))}
                              className={`px-2 py-1 rounded text-[10px] font-semibold transition-colors ${
                                capitalChoices[item.product_id] === 'last_purchase'
                                  ? 'bg-amber-500 text-white'
                                  : 'bg-slate-100 text-slate-500 hover:bg-amber-100'
                              }`}>
                              New Price
                            </button>
                            <button
                              onClick={() => setCapitalChoices(prev => ({ ...prev, [item.product_id]: 'moving_average' }))}
                              className={`px-2 py-1 rounded text-[10px] font-semibold transition-colors ${
                                capitalChoices[item.product_id] === 'moving_average'
                                  ? 'bg-blue-500 text-white'
                                  : 'bg-slate-100 text-slate-500 hover:bg-blue-100'
                              }`}>
                              Avg
                            </button>
                          </div>
                        ) : (
                          <span className="text-[10px] text-emerald-600 text-center block">Auto ↑</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <p className="text-[11px] text-slate-500">
              <strong>New Price</strong> — sets capital to the actual PO price (good if supplier permanently lowered price).
              <br /><strong>Moving Avg</strong> — smooths fluctuations across all purchases (good for temporary discounts).
            </p>

            <div className="flex gap-2 pt-1 border-t">
              <Button variant="outline" className="flex-1" onClick={() => setCapitalDialog(false)}>
                Cancel
              </Button>
              <Button onClick={confirmReceivePO}
                className="flex-1 bg-emerald-600 hover:bg-emerald-700 text-white">
                <Check size={14} className="mr-1.5" />
                Confirm Receive
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    )}

    <ReferenceNumberPrompt
      open={refPrompt.open}
      onClose={() => setRefPrompt(p => ({ ...p, open: false }))}
      referenceNumber={refPrompt.number}
      type="po"
      title={refPrompt.vendor}
    />
  </div>
  );
}
