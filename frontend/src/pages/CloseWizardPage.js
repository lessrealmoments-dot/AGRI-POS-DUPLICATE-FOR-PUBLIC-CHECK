import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth, api } from '../contexts/AuthContext';
import { formatPHP } from '../lib/utils';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Badge } from '../components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { ScrollArea } from '../components/ui/scroll-area';
import { Separator } from '../components/ui/separator';
import {
  CheckCircle2, Circle, ChevronRight, ChevronLeft, Receipt, CreditCard,
  Banknote, ReceiptText, Calculator, Wallet, Lock, Sun, Plus, RefreshCw,
  AlertTriangle, Package, Truck, ExternalLink, Check, XCircle, Search, Upload, Download,
  Percent, Zap, Info, ChevronDown, ChevronUp, Calendar
} from 'lucide-react';
import { toast } from 'sonner';
import UploadQRDialog from '../components/UploadQRDialog';
import ReviewDetailDialog from '../components/ReviewDetailDialog';
import InvoiceDetailModal from '../components/InvoiceDetailModal';
import CustomerStatementModal from '../components/CustomerStatementModal';
import ExpenseDetailModal from '../components/ExpenseDetailModal';

const STEPS = [
  { id: 1, title: 'Sales Log',        icon: Receipt,      desc: 'Verify all cash & credit sales' },
  { id: 2, title: 'Customer Credits', icon: CreditCard,   desc: 'Credit sales, cashouts & farm services' },
  { id: 3, title: 'AR Payments',      icon: Banknote,     desc: 'Payments received on existing credit' },
  { id: 4, title: 'Expenses',         icon: ReceiptText,  desc: 'All expenses recorded today' },
  { id: 5, title: 'Actual Count',       icon: Calculator,   desc: 'Count the actual funds in the operating fund' },
  { id: 6, title: 'Fund Allocation',  icon: Wallet,       desc: 'Distribute to vault and opening float' },
  { id: 7, title: 'Close & Sign Off', icon: Lock,         desc: 'Z-Report preview and manager sign-off' },
  { id: 8, title: 'Open Tomorrow',    icon: Sun,          desc: 'Confirm close and start next day' },
];

export default function CloseWizardPage() {
  const navigate = useNavigate();
  const { currentBranch, user, hasPerm } = useAuth();

  const [step, setStep] = useState(1);
  const [completed, setCompleted] = useState(new Set());
  const [loading, setLoading] = useState(true);
  const [date, setDate] = useState('');

  // Multi-day closing
  const [unclosedDays, setUnclosedDays] = useState([]);
  const [lastCloseDate, setLastCloseDate] = useState(null);
  const [selectedDayIndex, setSelectedDayIndex] = useState(0);

  // Group closing — when user ticks checkboxes on 2+ unclosed-day chips,
  // the wizard auto-aggregates data across those dates. No separate mode
  // switch; `inGroupMode` is derived from `batchDates.length >= 2`.
  const [batchDates, setBatchDates] = useState([]);
  const [batchReason, setBatchReason] = useState('');
  const [batchPreview, setBatchPreview] = useState(null);
  const inGroupMode = batchDates.length >= 2;

  // Wizard data
  const [dailyLog, setDailyLog] = useState(null);
  const [preview, setPreview] = useState(null);
  const [report, setReport]   = useState(null);
  const [isClosed, setIsClosed] = useState(false);
  const [closingRecord, setClosingRecord] = useState(null);

  // Step 5+6 inputs
  const [actualCash, setActualCash]     = useState('');
  const [cashToSafe, setCashToSafe]     = useState('');
  const [cashToDrawer, setCashToDrawer] = useState('');

  // Step 7 manager PIN + close
  const [managerPin, setManagerPin]     = useState('');
  const [pinVerified, setPinVerified]   = useState(false);
  const [pinVerifying, setPinVerifying] = useState(false);
  const [managerName, setManagerName]   = useState('');
  const [closing, setClosing]           = useState(false);

  // Quick-action dialogs
  const [saleDialog, setSaleDialog]   = useState(false);
  const [expDialog, setExpDialog]     = useState(false);
  const [invoiceModalOpen, setInvoiceModalOpen] = useState(false);
  const [selectedInvoiceNumber, setSelectedInvoiceNumber] = useState(null);
  const [selectedExpenseId, setSelectedExpenseId] = useState(null);
  const [detailType, setDetailType] = useState('sale');
  const [expenseModalOpen, setExpenseModalOpen] = useState(false);
  const openDetailModal = (num = null, expId = null, type = 'sale') => {
    if (expId) { setSelectedExpenseId(expId); setExpenseModalOpen(true); }
    else { setSelectedInvoiceNumber(num); setDetailType(type); setInvoiceModalOpen(true); }
  };
  const [stmtOpen, setStmtOpen] = useState(false);
  const [stmtCustomer, setStmtCustomer] = useState(null);
  const openCustStmt = (id, name) => { if (!id) return; setStmtCustomer({ id, name }); setStmtOpen(true); };
  const [wizUploadQROpen, setWizUploadQROpen] = useState(false);
  const [wizUploadExpenseId, setWizUploadExpenseId] = useState(null);
  const [pmtDialog, setPmtDialog]     = useState({ open: false, invoice: null });

  // 1-click report dialogs
  const [lowStockDialog, setLowStockDialog]     = useState(false);
  const [payablesDialog, setPayablesDialog]     = useState(false);
  const [lowStockData, setLowStockData]         = useState(null);
  const [payablesData, setPayablesData]         = useState(null);

  // Quick-add sale form
  const [saleForm, setSaleForm]   = useState({ search: '', product: null, qty: 1, price: '', paymentType: 'cash', customerName: '' });
  const [saleMatches, setSaleMatches] = useState([]);
  const [saleSchemes, setSaleSchemes] = useState([]);

  // Quick-add expense form
  const EXPENSE_CATEGORIES = [
    "Utilities", "Rent", "Supplies", "Transportation", "Fuel/Gas",
    "Repairs & Maintenance", "Marketing", "Salaries & Wages", "Communication",
    "Insurance", "Professional Fees", "Taxes & Licenses", "Office Supplies",
    "Equipment", "Miscellaneous"
  ];
  const PAYMENT_METHODS = ["Cash", "Check", "Bank Transfer", "GCash", "Maya", "Credit Card"];

  const [expForm, setExpForm] = useState({
    expenseType: 'regular', category: 'Miscellaneous',
    description: '', notes: '', amount: '',
    payment_method: 'Cash', reference_number: '',
  });
  const [expSaving, setExpSaving] = useState(false);

  // Customer picker (farm / cashout)
  const [expCustomerSearch, setExpCustomerSearch] = useState('');
  const [expCustomerMatches, setExpCustomerMatches] = useState([]);
  const [expCustomerSelected, setExpCustomerSelected] = useState(null);
  const expCustomerTimer = useRef(null);

  // Employee picker (advance)
  const [expEmployees, setExpEmployees] = useState([]);
  const [expEmployeeSelected, setExpEmployeeSelected] = useState(null);
  const [expCaSummary, setExpCaSummary] = useState(null);
  const [expCaPinNeeded, setExpCaPinNeeded] = useState(false);
  const [expCaPin, setExpCaPin] = useState('');
  const [expCaPinVerified, setExpCaPinVerified] = useState(false);

  // Quick receive payment form
  const [pmtAmount, setPmtAmount] = useState('');
  const [pmtSaving, setPmtSaving] = useState(false);

  // "Find any customer & receive payment" panel in Step 3
  const [findPayCustomer, setFindPayCustomer] = useState('');
  const [findPayMatches, setFindPayMatches] = useState([]);
  const [findPaySelected, setFindPaySelected] = useState(null);
  const [findPayInvoices, setFindPayInvoices] = useState([]);
  const [findPayShowPanel, setFindPayShowPanel] = useState(false);
  const findPayTimerRef = useRef(null);
  const [findPayRowAmounts, setFindPayRowAmounts] = useState({});
  const [findPayChargesPreview, setFindPayChargesPreview] = useState(null);
  const [findPayPenaltyRate, setFindPayPenaltyRate] = useState(5);
  const [findPayGenerating, setFindPayGenerating] = useState(null);
  const [findPayProcessing, setFindPayProcessing] = useState(false);

  const today = new Date().toISOString().split('T')[0];

  // Load unclosed days on mount
  const loadUnclosedDays = useCallback(async () => {
    if (!currentBranch) return;
    try {
      const res = await api.get('/daily-close/unclosed-days', { params: { branch_id: currentBranch.id } });
      const days = res.data.unclosed_days || [];
      setUnclosedDays(days);
      setLastCloseDate(res.data.last_close_date);
      return days;
    } catch { return []; }
  }, [currentBranch]);

  const loadWizardData = useCallback(async (targetDate) => {
    if (!currentBranch) { setLoading(false); return; }
    setLoading(true);
    try {
      const d = targetDate || today;
      setDate(d);
      const [logRes, previewRes, reportRes, closeRes] = await Promise.all([
        api.get('/daily-log', { params: { date: d, branch_id: currentBranch.id } }),
        api.get('/daily-close-preview', { params: { date: d, branch_id: currentBranch.id } }),
        api.get('/daily-report', { params: { date: d, branch_id: currentBranch.id } }),
        api.get(`/daily-close/${d}`, { params: { branch_id: currentBranch.id } }),
      ]);
      setDailyLog(logRes.data);
      setPreview(previewRes.data);
      setReport(reportRes.data);
      if (closeRes.data?.status === 'closed') {
        setIsClosed(true);
        setClosingRecord(closeRes.data);
        setStep(8);
        setCompleted(new Set([1,2,3,4,5,6,7]));
      } else {
        setIsClosed(false);
        setClosingRecord(null);
        setStep(1);
        setCompleted(new Set());
        setActualCash('');
        setCashToSafe('');
        setCashToDrawer('');
        setManagerPin('');
        setPinVerified(false);
        setManagerName('');
      }
    } catch (e) { toast.error('Failed to load wizard data'); }
    setLoading(false);
    // Load price schemes for quick sale
    api.get('/price-schemes').then(r => setSaleSchemes(r.data || [])).catch(() => {});
  }, [currentBranch, today]);

  useEffect(() => {
    (async () => {
      const days = await loadUnclosedDays();
      if (days && days.length > 0) {
        // Load the first unclosed day
        setSelectedDayIndex(0);
        await loadWizardData(days[0].date);
      } else {
        // All closed — just load today
        await loadWizardData(today);
      }
    })();
  }, [loadUnclosedDays, loadWizardData, today]);

  const markComplete = (s) => setCompleted(prev => new Set([...prev, s]));
  const canProceedToClose = completed.has(5) && completed.has(6) && actualCash !== '';

  const r2 = (n) => Math.round((n || 0) * 100) / 100;

  // ── Verify manager PIN ──────────────────────────────────────────────────────
  const verifyManagerPin = async () => {
    if (!managerPin) { toast.error('Enter PIN'); return; }
    setPinVerifying(true);
    try {
      const res = await api.post('/auth/verify-manager-pin', {
        pin: managerPin, action_key: 'daily_close',
        context: `Daily close ${date} — ${currentBranch?.name}`
      });
      if (res.data.valid) {
        setPinVerified(true);
        setManagerName(res.data.manager_name);
        toast.success(`Verified: ${res.data.manager_name}`);
      } else {
        toast.error('Invalid PIN');
      }
    } catch { toast.error('PIN verification failed'); }
    setPinVerifying(false);
  };

  // ── Close the day ───────────────────────────────────────────────────────────
  const handleCloseDay = async () => {
    if (!pinVerified) { toast.error('Manager PIN required'); return; }
    if (!actualCash) { toast.error('Enter actual cash count'); return; }
    const safe = parseFloat(cashToSafe) || 0;
    const drawer = parseFloat(cashToDrawer) || 0;
    const actual = parseFloat(actualCash);
    if (safe + drawer > actual) { toast.error('Vault + Opening Float cannot exceed actual count'); return; }
    setClosing(true);
    try {
      const res = await api.post('/daily-close', {
        date, branch_id: currentBranch.id,
        admin_pin: managerPin,
        actual_cash: actual,
        cash_to_safe: safe,
        cash_to_drawer: drawer,
        variance_notes: '',
      });
      setClosingRecord(res.data);
      setIsClosed(true);
      markComplete(7);
      setStep(8);
      toast.success(`Day ${date} closed successfully!`);
      // Refresh unclosed days list
      await loadUnclosedDays();
    } catch (e) { toast.error(e.response?.data?.detail || 'Close failed'); }
    setClosing(false);
  };

  // Advance to next unclosed day after closing
  const advanceToNextDay = async () => {
    const days = await loadUnclosedDays();
    if (days && days.length > 0) {
      setSelectedDayIndex(0);
      await loadWizardData(days[0].date);
    } else {
      toast.success('All days are now closed!');
      navigate('/sales-new');
    }
  };

  // Switch to a specific unclosed day
  const selectDay = async (index) => {
    if (index < 0 || index >= unclosedDays.length) return;
    setSelectedDayIndex(index);
    // Leaving group mode: user picked a specific day to focus on.
    setBatchDates([]);
    setBatchReason('');
    setBatchPreview(null);
    await loadWizardData(unclosedDays[index].date);
  };

  // Merge N daily-log responses into a single one sorted oldest → newest.
  // Each row carries `_date` so the Sales Log table can show which day it
  // came from. Cash running totals are recomputed across the merged stream.
  const mergeDailyLogs = (logs, dates) => {
    const tagged = logs.map((log, i) => ({ log: log || {}, date: dates[i] }));
    // entries: sort by (date, sequence) ascending
    const entries = tagged.flatMap(({ log, date }) =>
      (log.entries || []).map(e => ({ ...e, _date: date }))
    ).sort((a, b) => {
      const byDate = (a._date || '').localeCompare(b._date || '');
      if (byDate !== 0) return byDate;
      return (a.sequence || 0) - (b.sequence || 0);
    });
    const credit_invoices = tagged.flatMap(({ log, date }) =>
      (log.credit_invoices || []).map(inv => ({ ...inv, _date: date }))
    ).sort((a, b) => (a.created_at || '').localeCompare(b.created_at || ''));
    // Recompute cash_entries + cash_running_total across the merged stream
    const cash_entries = tagged.flatMap(({ log, date }) =>
      (log.cash_entries || []).map(e => ({ ...e, _date: date }))
    ).sort((a, b) => {
      const byDate = (a._date || '').localeCompare(b._date || '');
      if (byDate !== 0) return byDate;
      return (a.sequence || 0) - (b.sequence || 0);
    });
    let running = 0;
    for (const e of cash_entries) {
      const pm = (e.payment_method || 'cash').toLowerCase();
      let amt = 0;
      if (pm === 'split') amt = e._split_cash_portion || 0;
      else if (pm === 'partial') amt = e._partial_cash_portion || 0;
      else amt = e.line_total || 0;
      running = Math.round((running + amt) * 100) / 100;
      e.cash_running_total = running;
    }
    // Summary: sum numeric totals, merge by_payment_method + cash_by_category
    const sumKeys = ['total_cash', 'total_partial_cash', 'total_cash_all', 'total_credit',
      'total_credit_balance', 'total_all', 'grand_total', 'cash_count', 'credit_invoice_count'];
    const summary = {};
    for (const k of sumKeys) {
      summary[k] = tagged.reduce((a, { log }) => a + (log.summary?.[k] || 0), 0);
      if (k === 'cash_count' || k === 'credit_invoice_count') summary[k] = Math.round(summary[k]);
      else summary[k] = Math.round(summary[k] * 100) / 100;
    }
    const mergeMap = (getter) => {
      const out = {};
      for (const { log } of tagged) {
        const src = getter(log) || {};
        for (const [k, v] of Object.entries(src)) {
          if (typeof v === 'object' && v !== null && 'total' in v) {
            if (!out[k]) out[k] = { total: 0, count: 0 };
            out[k].total = Math.round((out[k].total + (v.total || 0)) * 100) / 100;
            out[k].count += (v.count || 0);
          } else {
            out[k] = Math.round(((out[k] || 0) + (v || 0)) * 100) / 100;
          }
        }
      }
      return out;
    };
    summary.by_payment_method = mergeMap(l => l.summary?.by_payment_method);
    summary.cash_by_category = mergeMap(l => l.summary?.cash_by_category);
    return { entries, cash_entries, credit_invoices, summary, date: `${dates[0]} → ${dates[dates.length-1]}`, count: entries.length };
  };

  // Merge daily-report responses: concat ar_credits_today + sum total_ar_credits_today.
  const mergeReports = (reports) => {
    const ar_credits_today = reports.flatMap(r => (r?.ar_credits_today || []))
      .sort((a, b) => (a.created_at || '').localeCompare(b.created_at || ''));
    const total_ar_credits_today = Math.round(
      reports.reduce((a, r) => a + (r?.total_ar_credits_today || 0), 0) * 100
    ) / 100;
    return { ...(reports[0] || {}), ar_credits_today, total_ar_credits_today };
  };

  // Load aggregated data for group-close mode: batch preview + parallel
  // daily-log + daily-report per selected date, merged oldest → newest.
  const loadAggregatedWizardData = useCallback(async (dates) => {
    if (!currentBranch || dates.length < 2) return;
    setLoading(true);
    try {
      const sorted = [...dates].sort();
      const [bpRes, ...rest] = await Promise.all([
        api.get('/daily-close-preview/batch', { params: { branch_id: currentBranch.id, dates: sorted.join(',') } }),
        ...sorted.map(d => api.get('/daily-log',    { params: { date: d, branch_id: currentBranch.id } })),
        ...sorted.map(d => api.get('/daily-report', { params: { date: d, branch_id: currentBranch.id } })),
      ]);
      const logResponses = rest.slice(0, sorted.length).map(r => r.data);
      const reportResponses = rest.slice(sorted.length).map(r => r.data);
      setBatchPreview(bpRes.data);
      setPreview(bpRes.data);
      setDailyLog(mergeDailyLogs(logResponses, sorted));
      setReport(mergeReports(reportResponses));
      setDate(`${sorted[0]} → ${sorted[sorted.length - 1]} (${sorted.length} days)`);
      setIsClosed(false);
      setClosingRecord(null);
    } catch (e) {
      toast.error('Failed to load group data');
    }
    setLoading(false);
  }, [currentBranch]);

  // Toggle a date in/out of the group selection. Auto-loads aggregated data
  // when 2+ selected; when dropping below 2, falls back to single-day flow
  // with whichever chip is active.
  const toggleBatchDate = (d) => {
    const next = batchDates.includes(d)
      ? batchDates.filter(x => x !== d)
      : [...batchDates, d].sort();
    setBatchDates(next);
    if (next.length >= 2) {
      loadAggregatedWizardData(next);
    } else if (next.length === 1) {
      // Single date ticked: treat as normal single-day focus on that date.
      const idx = unclosedDays.findIndex(u => u.date === next[0]);
      if (idx >= 0) setSelectedDayIndex(idx);
      loadWizardData(next[0]);
    } else {
      // No dates ticked — re-load whichever day is currently the active one.
      const activeDay = unclosedDays[selectedDayIndex]?.date || today;
      loadWizardData(activeDay);
    }
  };

  // Handle batch close
  const handleBatchClose = async () => {
    if (!pinVerified) { toast.error('Manager PIN required'); return; }
    if (!actualCash) { toast.error('Enter actual cash count'); return; }
    if (!batchReason.trim()) { toast.error('Please provide a reason for group closing'); return; }
    const safe = parseFloat(cashToSafe) || 0;
    const drawer = parseFloat(cashToDrawer) || 0;
    const actual = parseFloat(actualCash);
    if (safe + drawer > actual) { toast.error('Vault + Opening Float cannot exceed actual count'); return; }
    setClosing(true);
    try {
      const res = await api.post('/daily-close/batch', {
        branch_id: currentBranch.id,
        dates: batchDates,
        reason: batchReason,
        admin_pin: managerPin,
        actual_cash: actual,
        cash_to_safe: safe,
        cash_to_drawer: drawer,
        variance_notes: '',
      });
      setClosingRecord(res.data);
      setIsClosed(true);
      markComplete(7);
      setStep(8);
      toast.success(`Batch close complete: ${batchDates[0]} to ${batchDates[batchDates.length - 1]}`);
      await loadUnclosedDays();
    } catch (e) { toast.error(e.response?.data?.detail || 'Batch close failed'); }
    setClosing(false);
  };

  // ── Quick add sale ──────────────────────────────────────────────────────────
  const searchProducts = async (q) => {
    setSaleForm(f => ({ ...f, search: q, product: null }));
    if (q.length < 1) { setSaleMatches([]); return; }
    const res = await api.get('/products', { params: { search: q, is_repack: false, limit: 6 } });
    setSaleMatches(res.data.products || []);
  };

  const quickAddSale = async () => {
    if (!saleForm.product) { toast.error('Select a product'); return; }
    const price = parseFloat(saleForm.price);
    if (!price || price <= 0) { toast.error('Enter price'); return; }
    try {
      const allPrices = {};
      saleSchemes.forEach(s => { allPrices[s.key] = price; });
      await api.post('/unified-sale', {
        branch_id: currentBranch.id,
        date,
        items: [{ product_id: saleForm.product.id, product_name: saleForm.product.name,
          sku: saleForm.product.sku, unit: saleForm.product.unit,
          quantity: saleForm.qty, unit_price: price, line_total: price * saleForm.qty,
          category: saleForm.product.category || 'General' }],
        payment_type: saleForm.paymentType,
        customer_name: saleForm.paymentType === 'cash' ? 'Walk-in' : (saleForm.customerName || 'Walk-in'),
        subtotal: price * saleForm.qty,
        grand_total: price * saleForm.qty,
        amount_paid: saleForm.paymentType === 'cash' ? price * saleForm.qty : 0,
        balance: saleForm.paymentType === 'cash' ? 0 : price * saleForm.qty,
      });
      toast.success('Sale added');
      setSaleDialog(false);
      setSaleForm({ search: '', product: null, qty: 1, price: '', paymentType: 'cash', customerName: '' });
      setSaleMatches([]);
      loadWizardData(date);
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed to add sale'); }
  };

  // ── Search customers for expense form ─────────────────────────────────────
  const searchExpCustomer = (q) => {
    setExpCustomerSearch(q);
    setExpCustomerSelected(null);
    if (expCustomerTimer.current) clearTimeout(expCustomerTimer.current);
    if (!q || q.length < 1) { setExpCustomerMatches([]); return; }
    expCustomerTimer.current = setTimeout(async () => {
      try {
        const res = await api.get('/customers', { params: { search: q, branch_id: currentBranch?.id, limit: 8 } });
        setExpCustomerMatches(res.data.customers || res.data || []);
      } catch { setExpCustomerMatches([]); }
    }, 200);
  };

  // ── Employee select — load CA summary ─────────────────────────────────────
  const handleExpEmployeeSelect = async (emp) => {
    setExpEmployeeSelected(emp);
    setExpCaSummary(null);
    setExpCaPinNeeded(false);
    setExpCaPinVerified(false);
    if (!emp) return;
    try {
      const res = await api.get(`/employees/${emp.id}/ca-summary`);
      setExpCaSummary(res.data);
    } catch { setExpCaSummary(null); }
  };

  // ── Quick add expense ───────────────────────────────────────────────────────
  const quickAddExpense = async () => {
    if (!expForm.description || !expForm.amount) { toast.error('Description and amount required'); return; }
    const needsCustomer = expForm.expenseType === 'farm' || expForm.expenseType === 'cashout';
    if (needsCustomer && !expCustomerSelected) { toast.error('Please select a customer'); return; }
    if (expForm.expenseType === 'advance' && !expEmployeeSelected) { toast.error('Please select an employee'); return; }

    // Check CA limit for employee advance
    if (expForm.expenseType === 'advance' && expCaSummary && !expCaPinVerified) {
      const amount = parseFloat(expForm.amount) || 0;
      const limit = expCaSummary.monthly_ca_limit || 0;
      const thisMonth = expCaSummary.this_month_total || 0;
      if (limit > 0 && (thisMonth + amount) > limit) {
        setExpCaPinNeeded(true);
        return;
      }
    }

    setExpSaving(true);
    try {
      const endpoint = expForm.expenseType === 'farm' ? '/expenses/farm'
        : expForm.expenseType === 'cashout' ? '/expenses/customer-cashout'
        : expForm.expenseType === 'advance' ? '/expenses/employee-advance'
        : '/expenses';

      const payload = {
        description: expForm.description,
        notes: expForm.notes,
        amount: parseFloat(expForm.amount),
        payment_method: expForm.payment_method || 'Cash',
        reference_number: expForm.reference_number || '',
        branch_id: currentBranch.id,
        date,
      };

      if (expForm.expenseType === 'regular') {
        payload.category = expForm.category;
      }
      if (needsCustomer && expCustomerSelected) {
        payload.customer_id = expCustomerSelected.id;
        payload.customer_name = expCustomerSelected.name;
      }
      if (expForm.expenseType === 'advance' && expEmployeeSelected) {
        payload.employee_id = expEmployeeSelected.id;
        payload.employee_name = expEmployeeSelected.name;
        if (expCaPinVerified) payload.manager_approved_by = expCaPin;
      }

      await api.post(endpoint, payload);
      toast.success('Expense added');
      resetExpDialog();
      loadWizardData(date);
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed'); }
    setExpSaving(false);
  };

  const resetExpDialog = () => {
    setExpDialog(false);
    setExpForm({ expenseType: 'regular', category: 'Miscellaneous', description: '', notes: '', amount: '', payment_method: 'Cash', reference_number: '' });
    setExpCustomerSearch(''); setExpCustomerMatches([]); setExpCustomerSelected(null);
    setExpEmployeeSelected(null); setExpCaSummary(null); setExpCaPinNeeded(false); setExpCaPin(''); setExpCaPinVerified(false);
  };

  const verifyExpCaPin = async () => {
    if (!expCaPin) { toast.error('Enter manager PIN'); return; }
    try {
      await api.post('/auth/verify-manager-pin', { pin: expCaPin, action_key: 'reverse_employee_advance' });
      setExpCaPinVerified(true);
      setExpCaPinNeeded(false);
      toast.success('PIN verified — you can now save the advance');
    } catch { toast.error('Invalid PIN'); }
  };

  // ── Quick receive payment ───────────────────────────────────────────────────
  const quickReceivePayment = async () => {
    if (!pmtAmount || !pmtDialog.invoice) { toast.error('Enter amount'); return; }
    setPmtSaving(true);
    try {
      // Find invoice by invoice_number if no invoice_id
      let invId = pmtDialog.invoice.invoice_id;
      if (!invId) {
        const res = await api.get('/invoices', { params: { invoice_number: pmtDialog.invoice.invoice_number } });
        invId = res.data?.[0]?.id;
      }
      if (!invId) { toast.error('Invoice not found'); setPmtSaving(false); return; }
      await api.post(`/invoices/${invId}/payment`, {
        amount: parseFloat(pmtAmount),
        date,
        method: 'Cash',
        branch_id: currentBranch.id,
      });
      toast.success('Payment recorded');
      setPmtDialog({ open: false, invoice: null });
      setPmtAmount('');
      loadWizardData(date);
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed'); }
    setPmtSaving(false);
  };

  // ── Find any customer & receive payment (Step 3 panel) ──────────────────────
  const searchFindPayCustomer = (query) => {
    setFindPayCustomer(query);
    setFindPaySelected(null);
    setFindPayInvoices([]);
    if (findPayTimerRef.current) clearTimeout(findPayTimerRef.current);
    if (!query || query.length < 1) { setFindPayMatches([]); return; }
    findPayTimerRef.current = setTimeout(async () => {
      try {
        const res = await api.get('/customers', { params: { search: query, branch_id: currentBranch?.id, limit: 8 } });
        const list = res.data.customers || res.data || [];
        setFindPayMatches(list.filter(c => (c.balance || 0) > 0));
      } catch {}
    }, 200);
  };

  const selectFindPayCustomer = async (customer) => {
    setFindPaySelected(customer);
    setFindPayCustomer(customer.name);
    setFindPayMatches([]);
    setFindPayRowAmounts({});
    setFindPayChargesPreview(null);
    try {
      const res = await api.get(`/customers/${customer.id}/invoices`);
      const invoices = (Array.isArray(res.data) ? res.data : res.data.invoices || []).filter(inv => (inv.balance || 0) > 0);
      setFindPayInvoices(invoices);
    } catch { setFindPayInvoices([]); }
    // Load charges preview
    try {
      const cp = await api.get(`/customers/${customer.id}/charges-preview`, { params: { as_of_date: date } });
      setFindPayChargesPreview(cp.data);
    } catch { setFindPayChargesPreview(null); }
  };

  // ── Find-Pay: helpers for multi-invoice allocation, interest, penalty ──────
  const findPayTotalApplied = findPayInvoices.reduce((s, inv) => {
    const v = parseFloat(findPayRowAmounts[inv.id] || 0);
    return s + (isNaN(v) ? 0 : v);
  }, 0);
  const findPayTotalOpen = findPayInvoices.reduce((s, i) => s + (i.balance || 0), 0);

  const findPayAutoApply = (totalAmt) => {
    const amt = parseFloat(totalAmt) || 0;
    if (amt <= 0) { setFindPayRowAmounts({}); return; }
    let remaining = amt;
    const newAmts = {};
    for (const inv of findPayInvoices) {
      if (remaining <= 0) break;
      const apply = Math.min(remaining, inv.balance);
      if (apply > 0) { newAmts[inv.id] = apply.toFixed(2); remaining = Math.round((remaining - apply) * 100) / 100; }
    }
    setFindPayRowAmounts(newAmts);
  };

  const reloadFindPayData = async (custId) => {
    try {
      const res = await api.get(`/customers/${custId}/invoices`);
      const invoices = (Array.isArray(res.data) ? res.data : res.data.invoices || []).filter(inv => (inv.balance || 0) > 0);
      setFindPayInvoices(invoices);
    } catch {}
    try {
      const cp = await api.get(`/customers/${custId}/charges-preview`, { params: { as_of_date: date } });
      setFindPayChargesPreview(cp.data);
    } catch {}
  };

  const handleFindPayGenerateInterest = async () => {
    if (!findPaySelected) return;
    setFindPayGenerating('interest');
    try {
      const res = await api.post(`/customers/${findPaySelected.id}/generate-interest`, { as_of_date: date });
      if (res.data.total_interest > 0) {
        toast.success(`Interest invoice ${res.data.invoice_number} created — ${formatPHP(res.data.total_interest)}`);
        await reloadFindPayData(findPaySelected.id);
        setFindPayRowAmounts({});
      } else {
        toast(`No interest to generate — ${res.data.message}`, { description: `Grace: ${res.data.grace_period} days` });
      }
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed to generate interest'); }
    setFindPayGenerating(null);
  };

  const handleFindPayGeneratePenalty = async () => {
    if (!findPaySelected) return;
    setFindPayGenerating('penalty');
    try {
      const res = await api.post(`/customers/${findPaySelected.id}/generate-penalty`, { penalty_rate: findPayPenaltyRate, as_of_date: date });
      if (res.data.total_penalty > 0) {
        toast.success(`Penalty invoice ${res.data.invoice_number} created — ${formatPHP(res.data.total_penalty)}`);
        await reloadFindPayData(findPaySelected.id);
        setFindPayRowAmounts({});
      } else {
        toast(`No penalty applicable — ${res.data.message}`);
      }
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed to generate penalty'); }
    setFindPayGenerating(null);
  };

  const handleFindPayApplyPayment = async () => {
    const allocations = findPayInvoices
      .map(inv => ({ invoice_id: inv.id, amount: parseFloat(findPayRowAmounts[inv.id] || 0) }))
      .filter(a => a.amount > 0);
    if (allocations.length === 0) { toast.error('Enter payment amounts for at least one invoice'); return; }
    setFindPayProcessing(true);
    try {
      const res = await api.post(`/customers/${findPaySelected.id}/receive-payment`, {
        allocations, method: 'Cash', date, branch_id: currentBranch?.id,
      });
      toast.success(`${formatPHP(res.data.total_applied)} applied to ${res.data.applied_invoices.length} invoice(s)`);
      setFindPayRowAmounts({});
      await reloadFindPayData(findPaySelected.id);
      loadWizardData(date); // refresh closing wizard totals
    } catch (e) { toast.error(e.response?.data?.detail || 'Payment failed'); }
    setFindPayProcessing(false);
  };

  // ── Load 1-click reports ────────────────────────────────────────────────────
  const loadLowStock = async () => {
    if (!currentBranch) return;
    const res = await api.get('/low-stock-alert', { params: { branch_id: currentBranch.id } });
    setLowStockData(res.data);
    setLowStockDialog(true);
  };

  const loadPayables = async () => {
    const res = await api.get('/supplier-payables', { params: { branch_id: currentBranch?.id } });
    setPayablesData(res.data);
    setPayablesDialog(true);
  };

  // Sync step 6 pre-fill whenever actualCash changes (after step 5)
  useEffect(() => {
    if (!actualCash) return;
    const actual = parseFloat(actualCash);
    if (isNaN(actual) || actual <= 0) return;
    const safe = Math.max(0, Math.floor(actual * 0.7));
    const drawer = Math.max(0, Math.round((actual - safe) * 100) / 100);
    setCashToSafe(String(safe));
    setCashToDrawer(String(drawer));
  }, [actualCash]);
  const expectedCash = r2(preview?.expected_counter || 0);
  const overShort    = actualCash !== '' ? r2(parseFloat(actualCash) - expectedCash) : null;

  if (loading) return (
    <div className="flex items-center justify-center h-64">
      <RefreshCw size={24} className="animate-spin text-slate-400" />
    </div>
  );

  if (!currentBranch) return (
    <div className="text-center py-20 text-slate-400">
      <p>Please select a branch first to run the Close Wizard.</p>
    </div>
  );

  return (
    <div className="max-w-5xl mx-auto space-y-6 animate-fadeIn" data-testid="close-wizard-page">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight" style={{ fontFamily: 'Manrope' }}>
            Daily Close Wizard
          </h1>
          <p className="text-sm text-slate-500 mt-0.5">
            {currentBranch?.name} — {date}
            {isClosed && <Badge className="ml-2 bg-emerald-100 text-emerald-700 text-[10px]">CLOSED</Badge>}
            {lastCloseDate && <span className="ml-2 text-xs text-slate-400">Last close: {lastCloseDate}</span>}
          </p>
        </div>
        {/* 1-Click Reports */}
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={loadLowStock} data-testid="low-stock-report-btn">
            <Package size={14} className="mr-1.5 text-amber-500" /> Low Stock
          </Button>
          <Button variant="outline" size="sm" onClick={loadPayables} data-testid="payables-report-btn">
            <Truck size={14} className="mr-1.5 text-red-500" /> Supplier Payables
          </Button>
        </div>
      </div>

      {/* Multi-Day Selector — unified chip row. Click body to focus a single
          day; tick checkbox to add it to the group. When 2+ chips are ticked,
          an amber "GROUP (N days)" badge appears in the wizard header and the
          steps auto-aggregate data across those dates (oldest → newest). */}
      {unclosedDays.length > 1 && (
        <Card className="border-amber-200 bg-amber-50/50">
          <CardContent className="p-3">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <AlertTriangle size={14} className="text-amber-600" />
                <span className="text-sm font-semibold text-amber-800">
                  {unclosedDays.length} unclosed day{unclosedDays.length > 1 ? 's' : ''} detected
                </span>
                <span className="text-xs text-amber-600">
                  {lastCloseDate ? `Since ${lastCloseDate}` : 'No previous closing found'}
                </span>
              </div>
              {inGroupMode && (
                <span className="text-[11px] font-semibold text-amber-700" data-testid="batch-selection-counter">
                  GROUP: {batchDates.length} days selected
                </span>
              )}
            </div>

            <div className="flex gap-1.5 overflow-x-auto pb-1">
              {unclosedDays.map((day, idx) => {
                const isActive = !inGroupMode && date === day.date;
                const isTicked = batchDates.includes(day.date);
                const isInGroupHighlight = inGroupMode && isTicked;
                const dayLabel = new Date(day.date + 'T12:00:00').toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
                return (
                  <div
                    key={day.date}
                    className={`relative flex flex-col items-center px-3 py-2 pr-7 rounded-lg border text-xs shrink-0 transition-all cursor-pointer ${
                      isActive || isInGroupHighlight
                        ? 'bg-[#1A4D2E] text-white border-[#1A4D2E] shadow-sm'
                        : day.has_activity
                        ? 'bg-white border-amber-300 hover:border-[#1A4D2E]/50 hover:bg-slate-50'
                        : 'bg-white border-slate-200 hover:bg-slate-50 opacity-60'
                    }`}
                    onClick={() => selectDay(idx)}
                    data-testid={`day-select-${day.date}`}
                  >
                    <span className="font-semibold">{dayLabel}</span>
                    {day.has_activity ? (
                      <span className={`text-[10px] mt-0.5 ${isActive || isInGroupHighlight ? 'text-emerald-200' : 'text-slate-400'}`}>
                        {day.sales_count} sales &middot; {formatPHP(day.cash_sales_total)}
                      </span>
                    ) : (
                      <span className={`text-[10px] mt-0.5 ${isActive || isInGroupHighlight ? 'text-slate-300' : 'text-slate-400'}`}>No activity</span>
                    )}
                    {/* Checkbox for group selection — stopPropagation so the
                        chip's onClick (selectDay) doesn't also fire. */}
                    <input
                      type="checkbox"
                      checked={isTicked}
                      onChange={() => toggleBatchDate(day.date)}
                      onClick={(e) => e.stopPropagation()}
                      title="Add to group close"
                      data-testid={`batch-chip-${day.date}`}
                      className="absolute top-1.5 right-1.5 w-3 h-3 rounded border-slate-400 cursor-pointer accent-amber-600"
                    />
                  </div>
                );
              })}
            </div>

            {inGroupMode ? (
              <div className="mt-2 space-y-2">
                <div>
                  <Label className="text-xs text-amber-700 font-semibold">Reason for group close *</Label>
                  <Input
                    data-testid="batch-reason-input"
                    value={batchReason}
                    onChange={e => setBatchReason(e.target.value)}
                    placeholder="e.g., Annual inventory week, store audit, power outage..."
                    className="mt-1 h-8 text-sm bg-white border-amber-200"
                  />
                </div>
                <p className="text-[10px] text-amber-600">
                  Sales, credits, and expenses from {batchDates.length} selected days ({batchDates[0]} → {batchDates[batchDates.length-1]}) are combined below — sorted oldest to newest.
                </p>
              </div>
            ) : (
              <p className="text-[10px] text-amber-600 mt-1.5">
                Tip: tick the checkbox on 2+ days to close them together as a group. Close days in order — each day's opening float chains from the previous closing.
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {/* Stepper */}
      <div className="flex items-center gap-0 overflow-x-auto pb-1">
        {STEPS.map((s, idx) => {
          const done = completed.has(s.id);
          const active = step === s.id;
          const Icon = s.icon;
          return (
            <div key={s.id} className="flex items-center shrink-0">
              <button
                onClick={() => setStep(s.id)}
                className={`flex flex-col items-center gap-1 px-3 py-2 rounded-xl transition-colors min-w-[80px] ${
                  active ? 'bg-[#1A4D2E]/10 border border-[#1A4D2E]/30' : 'hover:bg-slate-50'
                }`}
              >
                <div className={`w-8 h-8 rounded-full flex items-center justify-center shrink-0 transition-colors ${
                  done ? 'bg-emerald-500 text-white' : active ? 'bg-[#1A4D2E] text-white' : 'bg-slate-200 text-slate-500'
                }`}>
                  {done ? <Check size={15} /> : <Icon size={15} />}
                </div>
                <span className={`text-[10px] font-medium text-center leading-tight ${active ? 'text-[#1A4D2E]' : 'text-slate-500'}`}>
                  {s.title}
                </span>
              </button>
              {idx < STEPS.length - 1 && (
                <div className={`h-px w-5 mx-0.5 shrink-0 ${completed.has(s.id) ? 'bg-emerald-400' : 'bg-slate-200'}`} />
              )}
            </div>
          );
        })}
      </div>

      {/* Step content */}
      <Card className="border-slate-200 min-h-[400px]">
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className={`w-8 h-8 rounded-full flex items-center justify-center bg-[#1A4D2E] text-white`}>
                {(() => { const Icon = STEPS[step-1].icon; return <Icon size={15} />; })()}
              </div>
              <div>
                <CardTitle className="text-base font-bold" style={{ fontFamily: 'Manrope' }}>
                  Step {step}: {STEPS[step-1].title}
                  {inGroupMode && <Badge className="ml-2 bg-amber-100 text-amber-700 border-amber-300 text-[10px]" data-testid="group-mode-badge">GROUP ({batchDates.length})</Badge>}
                </CardTitle>
                <p className="text-xs text-slate-500">
                  {inGroupMode ? `${batchDates[0]} → ${batchDates[batchDates.length-1]} · ${batchDates.length} days combined (oldest to newest)` : STEPS[step-1].desc}
                </p>
              </div>
            </div>
            {!completed.has(step) && step < 7 && (
              <Button size="sm" variant="outline" onClick={() => markComplete(step)}
                className="text-emerald-700 border-emerald-300 hover:bg-emerald-50" data-testid={`mark-complete-step-${step}`}>
                <CheckCircle2 size={13} className="mr-1" /> Mark Complete
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent>

          {/* ── STEP 1: Sales Log (non-credit only — credit is in Step 2) ── */}
          {step === 1 && (
            <div className="space-y-3">
              {/* Pending stock releases warning */}
              {preview?.pending_stock_releases?.pending_invoice_count > 0 && (
                <div className={`rounded-lg border px-4 py-3 flex items-start gap-3 ${
                  preview.pending_stock_releases.has_overdue
                    ? 'bg-red-50 border-red-200'
                    : 'bg-amber-50 border-amber-200'
                }`}>
                  <Package size={16} className={`mt-0.5 shrink-0 ${preview.pending_stock_releases.has_overdue ? 'text-red-500' : 'text-amber-500'}`} />
                  <div>
                    <p className={`text-sm font-semibold ${preview.pending_stock_releases.has_overdue ? 'text-red-700' : 'text-amber-700'}`}>
                      {preview.pending_stock_releases.pending_invoice_count} invoice{preview.pending_stock_releases.pending_invoice_count !== 1 ? 's' : ''} with unreleased stock
                    </p>
                    <p className={`text-xs mt-0.5 ${preview.pending_stock_releases.has_overdue ? 'text-red-600' : 'text-amber-600'}`}>
                      {preview.pending_stock_releases.total_reserved_qty} units still reserved.
                      {preview.pending_stock_releases.has_overdue && ` ${preview.pending_stock_releases.overdue_reservations} overdue (>30 days).`}
                      {' '}Closing is allowed but review pending releases in Sales History.
                    </p>
                  </div>
                </div>
              )}

              {/* Negative stock warning */}
              {preview?.negative_stock?.count > 0 && (
                <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 flex items-start gap-3" data-testid="close-wizard-negative-stock-warning">
                  <AlertTriangle size={16} className="mt-0.5 shrink-0 text-red-500" />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-semibold text-red-700">
                      {preview.negative_stock.count} item{preview.negative_stock.count !== 1 ? 's' : ''} with negative inventory
                    </p>
                    <p className="text-xs mt-0.5 text-red-600">
                      These items were sold via manager override. An investigation ticket was auto-created for each.
                      Check Incident Tickets to resolve (encode missing PO or adjust).
                    </p>
                    <div className="mt-1.5 flex flex-wrap gap-1">
                      {(preview.negative_stock.items || []).slice(0, 5).map((item, i) => (
                        <span key={i} className="inline-flex items-center gap-1 text-[10px] bg-red-100 text-red-700 rounded px-1.5 py-0.5 font-mono">
                          {item.product_name} ({item.quantity})
                        </span>
                      ))}
                      {preview.negative_stock.count > 5 && (
                        <span className="text-[10px] text-red-500">+{preview.negative_stock.count - 5} more</span>
                      )}
                    </div>
                  </div>
                </div>
              )}
              {(() => {
                // Digital payment detection — mirrors backend DIGITAL_PAYMENT_METHODS
                const CASH_METHODS = new Set(['cash', 'check', 'cheque', 'credit', 'partial', 'split']);
                const isDigital = (pm) => !CASH_METHODS.has(pm);

                // Filter out full-credit entries — they're shown in Step 2 (Customer Credits)
                const nonCreditEntries = (dailyLog?.entries || []).filter(e => {
                  const pm = (e.payment_method || 'cash').toLowerCase();
                  return pm !== 'credit';
                });
                // Compute running total — includes ALL non-credit sales (cash + digital)
                // For partial entries, only count the cash portion
                let runTotal = 0;
                let cashTotal = 0;
                let digitalTotal = 0;
                const entriesWithTotal = nonCreditEntries.map(e => {
                  const pm = (e.payment_method || 'cash').toLowerCase();
                  const lt = parseFloat(e.line_total || 0);
                  let cashAmt;
                  if (pm === 'partial') {
                    cashAmt = parseFloat(e._partial_cash_portion || 0);
                    cashTotal += cashAmt;
                  } else if (pm === 'split') {
                    cashAmt = lt;
                    cashTotal += parseFloat(e._split_cash_portion || lt);
                    digitalTotal += (lt - parseFloat(e._split_cash_portion || lt));
                  } else if (isDigital(pm)) {
                    cashAmt = lt;
                    digitalTotal += lt;
                  } else {
                    cashAmt = lt;
                    cashTotal += lt;
                  }
                  runTotal += cashAmt;
                  return { ...e, _running: Math.round(runTotal * 100) / 100, _cash_amount: cashAmt, _is_digital: isDigital(pm) };
                });
                const totalNonCredit = Math.round(runTotal * 100) / 100;
                const totalDigital = Math.round(digitalTotal * 100) / 100;
                return (
                  <>
                    <div className="flex items-center justify-between flex-wrap gap-2">
                      <div className="flex gap-3 text-sm flex-wrap">
                        <span className="text-slate-500">Total Sales: <strong className="text-emerald-700">{formatPHP(totalNonCredit)}</strong></span>
                        {totalDigital > 0 && (
                          <span className="text-violet-500 text-xs">(includes {formatPHP(totalDigital)} digital)</span>
                        )}
                        {dailyLog?.summary?.by_payment_method && Object.entries(dailyLog.summary.by_payment_method)
                          .filter(([method]) => method.toLowerCase() !== 'credit')
                          .map(([method, d]) => (
                            <span key={method} className="text-slate-400 capitalize">{method}: <strong className="text-slate-600">{formatPHP(d.total)}</strong> <span className="text-[10px]">({d.count})</span></span>
                        ))}
                      </div>
                      <div className="flex gap-2">
                        <Button size="sm" variant="outline" onClick={() => setSaleDialog(true)} data-testid="quick-add-sale-btn">
                          <Plus size={13} className="mr-1" /> Quick Add Sale
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => window.open('/sales-new', '_blank')} className="text-slate-500">
                          <ExternalLink size={13} className="mr-1" /> Full Sales Panel
                        </Button>
                      </div>
                    </div>
                    <ScrollArea className="h-[280px] rounded-lg border border-slate-200">
                      <table className="w-full text-sm">
                        <thead className="sticky top-0 bg-slate-50 border-b border-slate-200">
                          <tr className="text-xs uppercase text-slate-500">
                            <th className="px-3 py-2 text-left">#</th>
                            <th className="px-3 py-2 text-left">Product</th>
                            <th className="px-3 py-2 text-center">Qty</th>
                            <th className="px-3 py-2 text-center">Payment</th>
                            <th className="px-3 py-2 text-right">Amount</th>
                            <th className="px-3 py-2 text-right">Running Total</th>
                          </tr>
                        </thead>
                        <tbody>
                          {entriesWithTotal.length === 0
                            ? <tr><td colSpan={6} className="text-center py-8 text-slate-400">No sales yet for this day</td></tr>
                            : entriesWithTotal.map((e, i) => {
                              const pm = (e.payment_method || 'cash').toLowerCase();
                              let pmLabel = pm;
                              let pmColor = pm === 'cash' ? 'bg-emerald-100 text-emerald-700'
                                : pm === 'gcash' ? 'bg-blue-100 text-blue-700'
                                : pm === 'maya' ? 'bg-green-100 text-green-700'
                                : pm === 'partial' ? 'bg-teal-100 text-teal-700'
                                : 'bg-violet-100 text-violet-700';
                              if (pm === 'split') {
                                pmLabel = `Cash+${e.split_digital_platform || 'Digital'}`;
                                pmColor = 'bg-indigo-100 text-indigo-700';
                              }
                              if (pm === 'partial') {
                                const cashP = e._partial_cash_portion || 0;
                                const creditP = e._partial_credit_portion || 0;
                                pmLabel = cashP > 0 ? `Partial` : 'Credit';
                              }
                              return (
                                <tr key={i} className="border-b border-slate-50 hover:bg-slate-50/50">
                                  <td className="px-3 py-1.5 text-xs text-slate-400">
                                    {inGroupMode && e._date && (
                                      <span className="inline-block mr-1 px-1 rounded bg-amber-100 text-amber-700 text-[9px] font-mono" title={e._date}>
                                        {e._date.slice(5)}
                                      </span>
                                    )}
                                    {e.sequence || i+1}
                                  </td>
                                  <td className="px-3 py-1.5">
                                    <span className="font-medium">{e.product_name}</span>
                                    {e.customer_name && e.customer_name !== 'Walk-in' && (
                                      <span className="text-[10px] text-slate-400 ml-1">({e.customer_name})</span>
                                    )}
                                  </td>
                                  <td className="px-3 py-1.5 text-center text-slate-500">{e.quantity} {e.unit || ''}</td>
                                  <td className="px-3 py-1.5 text-center">
                                    <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase ${pmColor}`}>{pmLabel}</span>
                                    {pm === 'partial' && (e._partial_cash_portion > 0 || e._partial_credit_portion > 0) && (
                                      <div className="text-[9px] text-slate-400 mt-0.5">
                                        {e._partial_cash_portion > 0 && <span className="text-emerald-600">{formatPHP(e._partial_cash_portion)} cash</span>}
                                        {e._partial_cash_portion > 0 && e._partial_credit_portion > 0 && <span> · </span>}
                                        {e._partial_credit_portion > 0 && <span className="text-amber-600">{formatPHP(e._partial_credit_portion)} AR</span>}
                                      </div>
                                    )}
                                  </td>
                                  <td className="px-3 py-1.5 text-right font-mono">
                                    {pm === 'partial' ? (
                                      <span title={`Full: ${formatPHP(e.line_total)}`}>{formatPHP(e._cash_amount || e.line_total)}</span>
                                    ) : e._is_digital ? (
                                      <span className="text-violet-600">{formatPHP(e.line_total)}</span>
                                    ) : formatPHP(e.line_total)}
                                  </td>
                                  <td className="px-3 py-1.5 text-right font-mono text-emerald-700">
                                    {formatPHP(e._running)}
                                  </td>
                                </tr>
                              );
                            })}
                        </tbody>
                      </table>
                    </ScrollArea>
                  </>
                );
              })()}
            </div>
          )}

          {/* ── STEP 2: Customer Credits ── */}
          {step === 2 && (
            <div className="space-y-3">
              <LateEncodedCarryover branchId={currentBranch?.id} />
              <div className="flex items-center justify-between mb-1">
                <p className="text-sm text-slate-500">Credit invoices with per-order breakdown and payment status.</p>
                <Button size="sm" variant="outline" onClick={() => window.open('/sales-new', '_blank')}>
                  <ExternalLink size={13} className="mr-1" /> Add Credit Sale
                </Button>
              </div>
              <ScrollArea className="h-[320px]">
                <div className="space-y-2">
                  {(dailyLog?.credit_invoices || []).map((inv, idx) => (
                    <div key={idx} className="rounded-lg border border-amber-200 overflow-hidden" data-testid={`credit-invoice-${idx}`}>
                      <div className="flex items-center justify-between px-4 py-2.5 bg-amber-50">
                        <div>
                          <p className="font-semibold text-sm">{inv.customer_id ? <button onClick={() => openCustStmt(inv.customer_id, inv.customer_name)} className="hover:text-[#1A4D2E] hover:underline">{inv.customer_name}</button> : inv.customer_name}</p>
                          <p className="text-xs text-slate-400">
                            <button className="text-blue-600 hover:underline font-mono" onClick={() => openDetailModal(inv.invoice_number)}>{inv.invoice_number}</button> · {inv.payment_type}
                            {inv.sale_type && !['credit','walk_in'].includes(inv.sale_type) && ` · ${inv.sale_type.replace(/_/g, ' ')}`}
                          </p>
                        </div>
                        <div className="text-right">
                          <p className="font-mono font-bold text-amber-700">{formatPHP(inv.balance || inv.grand_total)}</p>
                          <div className="text-[10px] space-x-2">
                            {(inv.amount_paid || 0) > 0 && <span className="text-emerald-600">Paid: {formatPHP(inv.amount_paid)}</span>}
                            {(inv.amount_paid || 0) > 0 && <span className="text-slate-400">of {formatPHP(inv.grand_total)}</span>}
                            {(inv.balance || 0) === 0 && <span className="text-emerald-600 font-semibold">FULLY PAID</span>}
                          </div>
                        </div>
                      </div>
                      {inv.items && inv.items.length > 0 && (
                        <div className="px-4 py-1 bg-white divide-y divide-slate-100">
                          {inv.items.map((item, ii) => (
                            <div key={ii} className="flex items-center justify-between py-1.5 text-xs">
                              <div>
                                <span className="text-slate-700">{item.product_name || item.description}</span>
                                <span className="text-slate-400 ml-1">x{item.quantity} {item.unit || ''}</span>
                              </div>
                              <span className="font-mono text-slate-600">
                                {formatPHP(item.line_total || (item.quantity * (item.unit_price || item.rate || item.price || 0)))}
                              </span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}

                  {(report?.ar_credits_today || []).length > 0 && (
                    <div>
                      <p className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1.5 mt-3">Cash Advances & Farm Services</p>
                      {(report.ar_credits_today).map((inv, i) => (
                        <div key={i} className="flex items-center justify-between p-3 rounded-lg bg-blue-50 border border-blue-100 mb-1.5 text-sm">
                          <div>
                            <p className="font-semibold">{inv.customer_id ? <button onClick={() => openCustStmt(inv.customer_id, inv.customer_name)} className="hover:text-[#1A4D2E] hover:underline">{inv.customer_name}</button> : inv.customer_name}</p>
                            <p className="text-xs text-slate-400"><button className="text-blue-600 hover:underline font-mono" onClick={() => openDetailModal(inv.invoice_number)}>{inv.invoice_number}</button> · {inv.sale_type === 'cash_advance' ? 'Cash-out' : 'Farm Service'}</p>
                            {inv.items?.[0]?.product_name && (
                              <p className="text-xs text-slate-500 mt-0.5">{inv.items[0].product_name}{inv.items[0].description ? ` — ${inv.items[0].description}` : ''}</p>
                            )}
                          </div>
                          <p className="font-mono font-bold text-blue-700">{formatPHP(inv.grand_total)}</p>
                        </div>
                      ))}
                    </div>
                  )}

                  {!(dailyLog?.credit_invoices?.length) && !(report?.ar_credits_today?.length) && (
                    <p className="text-center py-8 text-slate-400 text-sm">No credit activity today</p>
                  )}
                </div>
              </ScrollArea>
              <Separator />
              <div className="flex justify-between text-sm font-semibold px-1">
                <span>Total Credit Extended Today</span>
                <span className="text-amber-700">{formatPHP(r2((dailyLog?.summary?.total_credit || 0) + (report?.total_ar_credits_today || 0)))}</span>
              </div>
            </div>
          )}

          {/* ── STEP 3: AR Payments ── */}
          {step === 3 && (
            <div className="space-y-3">
              <div className="flex items-center justify-between mb-1">
                <p className="text-sm text-slate-500">Payments received today on existing credit accounts.</p>
                <div className="flex items-center gap-3">
                  {(preview?.ar_discount_today || 0) > 0 && (
                    <span className="text-xs text-blue-600 flex items-center gap-1">
                      Disc: <span className="font-mono font-semibold">{formatPHP(preview.ar_discount_today)}</span>
                    </span>
                  )}
                  {(preview?.ar_interest_collected || 0) > 0 && (
                    <span className="text-xs text-amber-600 flex items-center gap-1">
                      INT collected: <span className="font-mono font-semibold">{formatPHP(preview.ar_interest_collected)}</span>
                    </span>
                  )}
                  <p className="text-sm font-semibold text-blue-700">Total Received: {formatPHP(preview?.total_ar_received || 0)}</p>
                </div>
              </div>

              {/* AR payment method breakdown chips */}
              {preview?.ar_payment_by_method && Object.keys(preview.ar_payment_by_method).length > 0 && (
                <div className="flex items-center gap-2 flex-wrap" data-testid="ar-method-breakdown">
                  {Object.entries(preview.ar_payment_by_method).map(([m, v]) => (
                    <div key={m} className="flex items-center gap-1 px-2 py-0.5 bg-white border border-slate-200 rounded-full text-xs text-slate-600">
                      <span className="font-semibold">{m}</span>
                      <span className="font-mono">{formatPHP(v.total)}</span>
                      <span className="text-[10px] text-slate-400">({v.count})</span>
                    </div>
                  ))}
                </div>
              )}

              <ScrollArea className="h-[240px] rounded-lg border border-slate-200">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-slate-50 border-b border-slate-200">
                    <tr className="text-xs uppercase text-slate-500">
                      <th className="px-3 py-2 text-left">Customer / Invoice</th>
                      <th className="px-3 py-2 text-left">Method</th>
                      <th className="px-3 py-2 text-right">Bal Before</th>
                      <th className="px-3 py-2 text-right">Interest</th>
                      <th className="px-3 py-2 text-right">Penalty</th>
                      <th className="px-3 py-2 text-right text-blue-600 font-bold">Paid</th>
                      <th className="px-3 py-2 text-right">Remaining</th>
                      <th className="px-3 py-2 w-10"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {(preview?.ar_payments || []).filter(p => p.fund_source !== 'discount').length === 0
                      ? <tr><td colSpan={8} className="text-center py-8 text-slate-400">No AR payments received today</td></tr>
                      : (preview?.ar_payments || []).filter(p => p.fund_source !== 'discount').map((p, i) => (
                      <tr key={i} className="border-b border-slate-50 hover:bg-slate-50/50">
                        <td className="px-3 py-2">
                          <p className="font-medium">{p.customer_id ? <button onClick={() => openCustStmt(p.customer_id, p.customer_name)} className="hover:text-[#1A4D2E] hover:underline">{p.customer_name}</button> : p.customer_name}</p>
                          <p className="text-xs text-slate-400 font-mono"><button className="text-blue-600 hover:underline" onClick={() => openDetailModal(p.invoice_number)}>{p.invoice_number}</button></p>
                        </td>
                        <td className="px-3 py-2 text-xs text-slate-600">{p.method || 'Cash'}</td>
                        <td className="px-3 py-2 text-right font-mono text-xs">{formatPHP(p.balance_before)}</td>
                        <td className="px-3 py-2 text-right font-mono text-xs text-amber-600">{p.interest_paid > 0 ? formatPHP(p.interest_paid) : '—'}</td>
                        <td className="px-3 py-2 text-right font-mono text-xs text-red-500">{p.penalty_paid > 0 ? formatPHP(p.penalty_paid) : '—'}</td>
                        <td className="px-3 py-2 text-right font-mono font-bold text-blue-700">{formatPHP(p.amount_paid)}</td>
                        <td className="px-3 py-2 text-right font-mono text-xs">{formatPHP(p.remaining_balance)}</td>
                        <td className="px-3 py-2">
                          <Button size="sm" variant="ghost" className="h-6 px-1.5 text-[10px] text-blue-500"
                            onClick={() => { setPmtDialog({ open: true, invoice: p }); setPmtAmount(''); }}>
                            + Pay
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </ScrollArea>

              {/* Interest / Penalty Invoices Created Today */}
              {(preview?.interest_invoices_today || []).length > 0 && (
                <div className="rounded-lg border border-amber-200 overflow-hidden" data-testid="interest-invoices-today">
                  <div className="px-3 py-2 bg-amber-50 flex items-center justify-between">
                    <p className="text-xs font-bold uppercase tracking-wide text-amber-700">Interest & Penalty Invoices Created Today</p>
                    <span className="text-xs font-mono text-amber-700 font-semibold">
                      {formatPHP((preview.interest_invoices_today || []).reduce((s, i) => s + (i.grand_total || 0), 0))}
                    </span>
                  </div>
                  <table className="w-full text-xs">
                    <thead className="bg-amber-50/50 border-b border-amber-100">
                      <tr>
                        <th className="px-3 py-1.5 text-left text-amber-700 font-medium">Customer</th>
                        <th className="px-3 py-1.5 text-left text-amber-700 font-medium">Invoice #</th>
                        <th className="px-3 py-1.5 text-left text-amber-700 font-medium">Type</th>
                        <th className="px-3 py-1.5 text-right text-amber-700 font-medium">Amount</th>
                        <th className="px-3 py-1.5 text-right text-amber-700 font-medium">Collected</th>
                        <th className="px-3 py-1.5 text-right text-amber-700 font-medium">Balance</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(preview.interest_invoices_today || []).map((inv, i) => (
                        <tr key={i} className="border-b border-amber-50">
                          <td className="px-3 py-1.5 font-medium text-slate-700">{inv.customer_name}</td>
                          <td className="px-3 py-1.5 font-mono text-slate-500">{inv.invoice_number}</td>
                          <td className="px-3 py-1.5">
                            <span className={`px-1.5 py-0.5 rounded text-[9px] font-medium ${
                              inv.sale_type === 'penalty_charge' ? 'bg-red-100 text-red-700' : 'bg-amber-100 text-amber-700'
                            }`}>
                              {inv.sale_type === 'penalty_charge' ? 'Penalty' : inv.manual_interest ? 'Manual INT' : 'Interest'}
                            </span>
                          </td>
                          <td className="px-3 py-1.5 text-right font-mono">{formatPHP(inv.grand_total)}</td>
                          <td className="px-3 py-1.5 text-right font-mono text-emerald-600">{inv.amount_paid > 0 ? formatPHP(inv.amount_paid) : '—'}</td>
                          <td className="px-3 py-1.5 text-right font-mono font-semibold text-amber-700">{formatPHP(inv.balance)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* Find & Record Payment for ANY customer */}
              <div className="border border-dashed border-blue-300 rounded-lg">
                <button
                  type="button"
                  onClick={() => setFindPayShowPanel(v => !v)}
                  className="w-full flex items-center gap-2 px-4 py-2.5 bg-blue-50 hover:bg-blue-100 transition-colors text-sm font-medium text-blue-700 rounded-t-lg"
                  data-testid="find-pay-toggle-btn"
                >
                  <Plus size={14} /> Receive Payment for a Customer (not listed above)
                  {findPayShowPanel ? <ChevronUp size={14} className="ml-auto" /> : <ChevronDown size={14} className="ml-auto" />}
                </button>
                {findPayShowPanel && (
                  <div className="p-4 space-y-3 border-t border-blue-200">
                    {/* Customer Search */}
                    <div className="relative">
                      <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                      <Input
                        value={findPayCustomer}
                        onChange={e => searchFindPayCustomer(e.target.value)}
                        placeholder="Search customer name..."
                        className="pl-8 h-9"
                        data-testid="find-pay-customer-search"
                      />
                      {findPayMatches.length > 0 && (
                        <div className="absolute z-50 top-full mt-1 w-full bg-white border border-slate-200 rounded-lg shadow-lg max-h-40 overflow-y-auto">
                          {findPayMatches.map(c => (
                            <button key={c.id} onMouseDown={() => selectFindPayCustomer(c)}
                              className="w-full text-left px-3 py-2 hover:bg-slate-50 text-sm border-b last:border-0">
                              <span className="font-medium">{c.name}</span>
                              <span className="text-slate-400 ml-2 text-xs">AR: {formatPHP(c.balance)}</span>
                            </button>
                          ))}
                        </div>
                      )}
                    </div>

                    {/* Selected Customer */}
                    {findPaySelected && (
                      <div className="space-y-3">
                        {/* Customer header */}
                        <div className="flex items-center justify-between p-2.5 bg-blue-50 rounded-lg border border-blue-200">
                          <div>
                            <p className="text-sm font-semibold text-blue-800">{findPaySelected.name}</p>
                            <p className="text-[11px] text-blue-600">
                              {findPaySelected.interest_rate > 0 ? `${findPaySelected.interest_rate}%/mo interest` : 'No interest rate'}
                              {' · '}Grace: {findPaySelected.grace_period || 7} days
                            </p>
                          </div>
                          <div className="text-right">
                            <p className="text-[10px] text-blue-500 uppercase">Total AR</p>
                            <p className="text-lg font-bold text-red-600" style={{ fontFamily: 'Manrope' }}>{formatPHP(findPayTotalOpen)}</p>
                          </div>
                        </div>

                        {/* Interest / Penalty generation */}
                        <div className="border border-slate-200 rounded-lg">
                          <div className="flex flex-wrap items-center gap-2 px-3 py-2 bg-slate-50 rounded-t-lg border-b border-slate-200">
                            <Calculator size={13} className="text-amber-500" />
                            <span className="text-xs font-medium text-slate-600">Charges</span>
                            {findPayChargesPreview?.total_interest > 0 && (
                              <Badge className="text-[9px] bg-amber-100 text-amber-700 border-amber-200">
                                ~{formatPHP(findPayChargesPreview.total_interest)} accrued
                              </Badge>
                            )}
                            <div className="ml-auto flex items-center gap-2 flex-wrap">
                              <Button size="sm" variant="outline" onClick={handleFindPayGenerateInterest}
                                disabled={!!findPayGenerating || !findPaySelected.interest_rate}
                                className="h-7 text-[11px] text-amber-600 border-amber-200 hover:bg-amber-50 gap-1" data-testid="find-pay-gen-interest-btn">
                                <Percent size={11} /> {findPayGenerating === 'interest' ? 'Working...' : 'Interest'}
                              </Button>
                              <div className="flex items-center gap-0.5 bg-white border border-slate-200 rounded px-1.5 py-1">
                                <Input type="number" value={findPayPenaltyRate} onChange={e => setFindPayPenaltyRate(parseFloat(e.target.value) || 0)}
                                  className="w-10 h-5 text-[11px] text-center border-0 bg-transparent p-0" />
                                <span className="text-[11px] text-slate-500">%</span>
                              </div>
                              <Button size="sm" variant="outline" onClick={handleFindPayGeneratePenalty}
                                disabled={!!findPayGenerating}
                                className="h-7 text-[11px] text-red-600 border-red-200 hover:bg-red-50 gap-1" data-testid="find-pay-gen-penalty-btn">
                                <AlertTriangle size={11} /> {findPayGenerating === 'penalty' ? 'Working...' : 'Penalty'}
                              </Button>
                            </div>
                          </div>
                          {/* Interest preview details */}
                          {findPayChargesPreview?.interest_preview?.length > 0 && (
                            <div className="px-3 py-2 space-y-0.5">
                              {findPayChargesPreview.interest_preview.map((item, i) => (
                                <div key={i} className="flex items-center justify-between text-[11px] text-amber-700">
                                  <span className="font-mono">{item.invoice_number}</span>
                                  <span>{item.days_for_interest}d × {item.rate}%/mo</span>
                                  <span className="font-medium">{formatPHP(item.interest_amount)}</span>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>

                        {/* Invoice table with per-row amounts */}
                        {findPayInvoices.length === 0
                          ? <p className="text-xs text-slate-400 text-center py-4">No open invoices found</p>
                          : (<div className="space-y-2">
                              <table className="w-full text-xs" data-testid="find-pay-invoices-table">
                                <thead className="bg-slate-50 border-b border-slate-200">
                                  <tr>
                                    <th className="text-left px-2 py-1.5 text-[10px] font-semibold text-slate-500 uppercase">Invoice</th>
                                    <th className="text-left px-2 py-1.5 text-[10px] font-semibold text-slate-500 uppercase">Type</th>
                                    <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-slate-500 uppercase">Balance</th>
                                    <th className="text-right px-2 py-1.5 text-[10px] font-semibold text-slate-500 uppercase w-28">Payment</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {findPayInvoices.map(inv => {
                                    const rowAmt = findPayRowAmounts[inv.id] || '';
                                    const isApplied = parseFloat(rowAmt) > 0;
                                    const saleType = inv.sale_type || 'regular';
                                    const typeLabel = saleType === 'penalty_charge' ? 'Penalty' : saleType === 'interest_charge' ? 'Interest' : 'Invoice';
                                    const typeCls = saleType === 'penalty_charge' ? 'bg-red-100 text-red-700' : saleType === 'interest_charge' ? 'bg-amber-100 text-amber-700' : 'bg-slate-100 text-slate-700';
                                    return (
                                      <tr key={inv.id} className={`border-b border-slate-100 ${isApplied ? 'bg-emerald-50/40' : ''}`}>
                                        <td className="px-2 py-1.5">
                                          <button className="font-mono text-blue-600 hover:underline text-[11px]" onClick={() => openDetailModal(inv.invoice_number)}>{inv.invoice_number}</button>
                                          <p className="text-[10px] text-slate-400">{inv.order_date}{inv.due_date ? ` · Due: ${inv.due_date}` : ''}</p>
                                        </td>
                                        <td className="px-2 py-1.5">
                                          <Badge variant="outline" className={`text-[9px] ${typeCls}`}>{typeLabel}</Badge>
                                        </td>
                                        <td className="px-2 py-1.5 text-right font-mono font-semibold">{formatPHP(inv.balance)}</td>
                                        <td className="px-2 py-1.5 text-right">
                                          <Input type="number" min="0" max={inv.balance} step="0.01" value={rowAmt} placeholder="0.00"
                                            className={`h-7 w-24 text-right text-xs ml-auto ${isApplied ? 'border-emerald-400 bg-emerald-50' : 'border-slate-200'}`}
                                            onChange={e => setFindPayRowAmounts(prev => ({ ...prev, [inv.id]: e.target.value }))}
                                            onFocus={e => e.target.select()}
                                            data-testid={`find-pay-row-${inv.id}`}
                                          />
                                        </td>
                                      </tr>
                                    );
                                  })}
                                </tbody>
                              </table>

                              {/* Footer: total + auto-apply + submit */}
                              <div className="flex flex-wrap items-center gap-2 pt-1">
                                <div className="flex items-center gap-1.5">
                                  <span className="text-[11px] text-slate-500">Quick:</span>
                                  <Input type="number" placeholder="Total amount" className="h-7 w-24 text-xs"
                                    onChange={e => findPayAutoApply(e.target.value)} data-testid="find-pay-total-input" />
                                  <Button variant="outline" size="sm" className="h-7 text-[11px] gap-1" onClick={() => findPayAutoApply(findPayTotalOpen)}>
                                    <Zap size={11} /> All
                                  </Button>
                                </div>
                                <div className="ml-auto flex items-center gap-3">
                                  <span className="text-xs text-slate-500">
                                    Applying: <strong className="text-emerald-600">{formatPHP(findPayTotalApplied)}</strong>
                                  </span>
                                  <Button size="sm" className="h-8 bg-blue-600 hover:bg-blue-700 text-white gap-1"
                                    onClick={handleFindPayApplyPayment}
                                    disabled={findPayProcessing || findPayTotalApplied <= 0}
                                    data-testid="find-pay-apply-btn">
                                    {findPayProcessing ? <RefreshCw size={12} className="animate-spin" /> : <Check size={12} />}
                                    {findPayProcessing ? 'Processing...' : 'Record Payment'}
                                  </Button>
                                </div>
                              </div>
                            </div>
                          )}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* ── STEP 4: Expenses ── */}
          {step === 4 && (
            <div className="space-y-3">
              <div className="flex items-center justify-between mb-1">
                <p className="text-sm text-slate-500">All expenses grouped by category. Includes farm, cashouts, advances & regular.</p>
                <Button size="sm" variant="outline" onClick={() => {
                  setExpDialog(true);
                  // Load employees for advance type
                  api.get('/employees', { params: { active: true } }).then(r => setExpEmployees(r.data.employees || r.data || [])).catch(() => {});
                }} data-testid="quick-add-expense-btn">
                  <Plus size={13} className="mr-1" /> Add Expense
                </Button>
              </div>
              <ScrollArea className="h-[260px] rounded-lg border border-slate-200">
                {(() => {
                  const expenses = preview?.expenses || [];
                  if (expenses.length === 0) {
                    return <p className="text-center py-8 text-slate-400 text-sm">No expenses today</p>;
                  }
                  const groups = {};
                  expenses.forEach(e => {
                    const cat = e.category || 'Miscellaneous';
                    if (!groups[cat]) groups[cat] = [];
                    groups[cat].push(e);
                  });
                  return (
                    <div className="divide-y divide-slate-200">
                      {Object.entries(groups).map(([cat, items]) => {
                        const groupTotal = items.reduce((sum, e) => sum + parseFloat(e.amount || 0), 0);
                        const catColor = cat.toLowerCase().includes('farm') ? 'bg-green-50 text-green-800'
                          : cat.toLowerCase().includes('cash') ? 'bg-blue-50 text-blue-800'
                          : cat.toLowerCase().includes('employee') || cat.toLowerCase().includes('advance') ? 'bg-amber-50 text-amber-800'
                          : 'bg-slate-50 text-slate-700';
                        return (
                          <div key={cat}>
                            <div className={`px-4 py-2 flex items-center justify-between ${catColor}`}>
                              <span className="text-xs font-semibold uppercase tracking-wider">{cat}</span>
                              <span className="text-xs font-bold font-mono">{formatPHP(groupTotal)}</span>
                            </div>
                            {items.map((e, i) => (
                              <div key={i} className="px-4 py-2.5 flex items-center justify-between text-sm bg-white border-b border-slate-50">
                                <div className="flex-1 min-w-0">
                                  <p className="font-medium truncate">{e.description || e.notes || e.category}</p>
                                  {e.customer_name && <p className="text-xs text-slate-400">Customer: {e.customer_name}</p>}
                                  {e.employee_name && <p className="text-xs text-slate-400">Employee: {e.employee_name}</p>}
                                  {e.payment_method && e.payment_method !== 'Cash' && <p className="text-[10px] text-slate-400">via {e.payment_method}</p>}
                                  {e.fund_source === 'safe' && <span className="inline-block text-[9px] bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded font-semibold mt-0.5">SAFE</span>}
                                  {e.monthly_ca_total != null && (
                                    <div className={`mt-1 inline-flex items-center gap-1 ${e.is_over_ca ? 'bg-red-50 border-red-200 text-red-700' : 'bg-amber-50 border-amber-200 text-amber-700'} border rounded px-2 py-0.5 text-[10px]`}>
                                      <AlertTriangle size={9} />
                                      Monthly total: {formatPHP(e.monthly_ca_total)}
                                      {e.monthly_ca_limit > 0 && <span> / Limit: {formatPHP(e.monthly_ca_limit)}</span>}
                                      {e.is_over_ca && <span className="font-bold ml-1">OVER CA</span>}
                                    </div>
                                  )}
                                  {e.manager_approved_by && (
                                    <div className="mt-0.5 inline-flex items-center gap-1 bg-violet-50 border border-violet-200 rounded px-2 py-0.5 text-[10px] text-violet-700">
                                      Approved by: {e.manager_approved_by}
                                    </div>
                                  )}
                                </div>
                                <div className="flex items-center gap-2 ml-4">
                                  <p className="font-mono font-semibold text-red-600">{formatPHP(e.amount)}</p>
                                  {e.id && (
                                    <button
                                      onClick={() => { setWizUploadExpenseId(e.id); setWizUploadQROpen(true); }}
                                      className="w-6 h-6 rounded-md bg-blue-50 hover:bg-blue-100 flex items-center justify-center transition-colors"
                                      title="Upload receipt for this expense">
                                      <Upload size={11} className="text-blue-600" />
                                    </button>
                                  )}
                                </div>
                              </div>
                            ))}
                          </div>
                        );
                      })}
                    </div>
                  );
                })()}
              </ScrollArea>
              <Separator />
              <div className="flex justify-between text-sm font-semibold px-1">
                <span>Total Expenses</span>
                <span className="text-red-600">{formatPHP(preview?.total_expenses || 0)}</span>
              </div>
              {((preview?.total_safe_expenses || 0) > 0 || (preview?.total_digital_expenses || 0) > 0) && (
                <div className="flex justify-between text-xs px-1 text-slate-500">
                  <span>Drawer: {formatPHP(preview?.total_cashier_expenses || 0)}{(preview?.total_safe_expenses || 0) > 0 ? ` | Safe: ${formatPHP(preview?.total_safe_expenses || 0)}` : ''}{(preview?.total_digital_expenses || 0) > 0 ? ` | Digital: ${formatPHP(preview?.total_digital_expenses || 0)}` : ''}</span>
                </div>
              )}
            </div>
          )}

          {/* ── STEP 5: Actual Count ── */}
          {step === 5 && (
            <div className="space-y-4 max-w-lg mx-auto">
              {/* Cash Drawer Breakdown */}
              <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">Cash Drawer Breakdown</p>
              <div className="grid grid-cols-2 gap-3">
                <div className="p-3 rounded-lg bg-slate-50 border border-slate-200">
                  <p className="text-xs text-slate-500 uppercase font-medium mb-1">Opening Float</p>
                  <p className="text-xl font-bold font-mono">{formatPHP(preview?.starting_float || 0)}</p>
                </div>
                <div className="p-3 rounded-lg bg-emerald-50 border border-emerald-200">
                  <p className="text-xs text-emerald-600 uppercase font-medium mb-1">Cash Sales</p>
                  <p className="text-xl font-bold font-mono text-emerald-700">{formatPHP(preview?.total_cash_sales || 0)}</p>
                </div>
                {(preview?.total_split_cash || 0) > 0 && (
                  <div className="p-3 rounded-lg bg-teal-50 border border-teal-200">
                    <p className="text-xs text-teal-600 uppercase font-medium mb-1">Split Cash Portion</p>
                    <p className="text-xl font-bold font-mono text-teal-700">{formatPHP(preview?.total_split_cash || 0)}</p>
                  </div>
                )}
                <div className="p-3 rounded-lg bg-blue-50 border border-blue-200">
                  <p className="text-xs text-blue-500 uppercase font-medium mb-1">Partial Cash Received</p>
                  <p className="text-xl font-bold font-mono text-blue-700">{formatPHP(preview?.total_partial_cash || 0)}</p>
                </div>
                <div className="p-3 rounded-lg bg-indigo-50 border border-indigo-200">
                  <p className="text-xs text-indigo-500 uppercase font-medium mb-1">AR Cash Payments</p>
                  <p className="text-xl font-bold font-mono text-indigo-700">{formatPHP(preview?.total_cash_ar || preview?.total_ar_received || 0)}</p>
                  {(preview?.total_digital_ar || 0) > 0 && (
                    <p className="text-[10px] text-indigo-400 mt-0.5">+ {formatPHP(preview.total_digital_ar)} digital AR (e-wallet)</p>
                  )}
                </div>
                {(preview?.net_fund_transfers || 0) !== 0 && (
                  <div className={`p-3 rounded-lg border ${preview.net_fund_transfers > 0 ? 'bg-cyan-50 border-cyan-200' : 'bg-orange-50 border-orange-200'}`}>
                    <p className={`text-xs uppercase font-medium mb-1 ${preview.net_fund_transfers > 0 ? 'text-cyan-600' : 'text-orange-600'}`}>Fund Transfers</p>
                    <p className={`text-xl font-bold font-mono ${preview.net_fund_transfers > 0 ? 'text-cyan-700' : 'text-orange-700'}`}>
                      {preview.net_fund_transfers > 0 ? '+' : ''}{formatPHP(preview.net_fund_transfers)}
                    </p>
                    <div className="text-[10px] mt-1 space-y-0.5">
                      {(preview?.capital_to_cashier || 0) > 0 && <p className="text-cyan-500">Capital: +{formatPHP(preview.capital_to_cashier)}</p>}
                      {(preview?.safe_to_cashier || 0) > 0 && <p className="text-cyan-500">Safe In: +{formatPHP(preview.safe_to_cashier)}</p>}
                      {(preview?.cashier_to_safe || 0) > 0 && <p className="text-orange-500">To Safe: -{formatPHP(preview.cashier_to_safe)}</p>}
                    </div>
                  </div>
                )}
              </div>

              {/* E-Wallet / Digital Payments */}
              {(preview?.total_digital_today || 0) > 0 && (
                <div className="p-3 rounded-lg bg-violet-50 border border-violet-200">
                  <div className="flex items-center justify-between">
                    <p className="text-xs text-violet-600 uppercase font-medium">E-Wallet Payments</p>
                    <p className="text-lg font-bold font-mono text-violet-700">{formatPHP(preview?.total_digital_today || 0)}</p>
                  </div>
                  {Object.entries(preview?.digital_by_platform || {}).map(([platform, amt]) => (
                    <div key={platform} className="flex justify-between text-xs mt-1 text-violet-500">
                      <span>{platform}</span>
                      <span className="font-mono font-semibold">{formatPHP(amt)}</span>
                    </div>
                  ))}
                  <p className="text-[10px] text-violet-400 mt-1">Tracked separately — not part of cash drawer</p>
                </div>
              )}

              {/* Summary Row */}
              <div className="grid grid-cols-3 gap-3">
                <div className="p-3 rounded-lg bg-green-50 border border-green-200">
                  <p className="text-xs text-green-600 uppercase font-medium mb-1">Total Cash In</p>
                  <p className="text-lg font-bold font-mono text-green-700">{formatPHP(preview?.total_cash_in || 0)}</p>
                </div>
                <div className="p-3 rounded-lg bg-red-50 border border-red-200">
                  <p className="text-xs text-red-500 uppercase font-medium mb-1">Cashier Expenses</p>
                  <p className="text-lg font-bold font-mono text-red-600">{formatPHP(preview?.total_cashier_expenses ?? preview?.total_expenses ?? 0)}</p>
                  {(preview?.total_safe_expenses || 0) > 0 && (
                    <p className="text-[10px] text-red-400 mt-0.5">+ {formatPHP(preview.total_safe_expenses)} from safe</p>
                  )}
                </div>
                <div className="p-3 rounded-lg bg-emerald-50 border border-emerald-300">
                  <p className="text-xs text-emerald-600 uppercase font-medium mb-1">Expected in Drawer</p>
                  <p className="text-lg font-bold font-mono text-emerald-700">{formatPHP(expectedCash)}</p>
                </div>
              </div>

              <Separator />
              <div>
                <Label className="text-sm font-semibold">Actual Fund Count</Label>
                <Input
                  data-testid="actual-cash-input"
                  type="number" min={0} step="0.01"
                  value={actualCash}
                  onChange={e => setActualCash(e.target.value)}
                  placeholder="Count the cash and enter here"
                  className="h-12 text-xl font-mono mt-1"
                />
              </div>
              {overShort !== null && (
                <div className={`p-3 rounded-lg border text-center ${overShort > 0 ? 'bg-emerald-50 border-emerald-200' : overShort < 0 ? 'bg-red-50 border-red-200' : 'bg-slate-50 border-slate-200'}`}>
                  <p className="text-xs uppercase font-medium text-slate-500 mb-0.5">Over / Short</p>
                  <p className={`text-2xl font-bold font-mono ${overShort > 0 ? 'text-emerald-700' : overShort < 0 ? 'text-red-600' : 'text-slate-500'}`}>
                    {overShort > 0 ? '+' : ''}{formatPHP(overShort)}
                  </p>
                  {overShort !== 0 && <p className="text-xs text-slate-500 mt-0.5">{overShort > 0 ? 'Cash over — check for unrecorded sales' : 'Cash short — check for missing expenses or errors'}</p>}
                </div>
              )}
              {actualCash && <Button onClick={() => markComplete(5)} className="w-full bg-[#1A4D2E] text-white" data-testid="complete-cash-count-btn">
                <CheckCircle2 size={15} className="mr-2" /> Confirm Fund Count
              </Button>}
            </div>
          )}

          {/* ── STEP 6: Fund Allocation ── */}
          {step === 6 && (
            <div className="space-y-4 max-w-md mx-auto">
              {!completed.has(5) && (
                <div className="p-3 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-800 flex gap-2">
                  <AlertTriangle size={16} className="shrink-0 mt-0.5" /> Complete the Fund Count (Step 5) first.
                </div>
              )}
              <div className="p-4 rounded-lg bg-slate-50 border border-slate-200">
                <p className="text-xs text-slate-500 uppercase font-medium mb-1">Actual Funds Available</p>
                <p className="text-3xl font-bold font-mono">{formatPHP(parseFloat(actualCash) || 0)}</p>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <Label>Transfer to Vault</Label>
                  <Input data-testid="cash-to-safe-input" type="number" min={0} step="0.01"
                    value={cashToSafe}
                    onChange={e => { setCashToSafe(e.target.value); const rem = r2((parseFloat(actualCash)||0)-(parseFloat(e.target.value)||0)); setCashToDrawer(String(Math.max(0,rem))); }}
                    className="h-10 font-mono mt-1" />
                </div>
                <div>
                  <Label>Stay as Opening Float (Next Day)</Label>
                  <Input data-testid="cash-to-drawer-input" type="number" min={0} step="0.01"
                    value={cashToDrawer}
                    onChange={e => { setCashToDrawer(e.target.value); const rem = r2((parseFloat(actualCash)||0)-(parseFloat(e.target.value)||0)); setCashToSafe(String(Math.max(0,rem))); }}
                    className="h-10 font-mono mt-1" />
                </div>
              </div>
              {(() => {
                const safe = parseFloat(cashToSafe)||0, drawer = parseFloat(cashToDrawer)||0, actual = parseFloat(actualCash)||0;
                const allocated = r2(safe+drawer), unallocated = r2(actual-allocated);
                return (
                  <div className={`p-3 rounded-lg border text-sm ${Math.abs(unallocated) < 0.01 ? 'bg-emerald-50 border-emerald-200' : 'bg-amber-50 border-amber-200'}`}>
                    <div className="flex justify-between">
                      <span>Allocated</span><span className="font-mono font-bold">{formatPHP(allocated)}</span>
                    </div>
                    {Math.abs(unallocated) > 0.01 && (
                      <div className="flex justify-between text-amber-700 mt-0.5">
                        <span>Unallocated</span><span className="font-mono font-bold">{formatPHP(unallocated)}</span>
                      </div>
                    )}
                  </div>
                );
              })()}
              <Button onClick={() => markComplete(6)} className="w-full bg-[#1A4D2E] text-white"
                disabled={!completed.has(5)} data-testid="complete-allocation-btn">
                <CheckCircle2 size={15} className="mr-2" /> Confirm Allocation
              </Button>
            </div>
          )}

          {/* ── STEP 7: Close & Sign Off ── */}
          {step === 7 && (
            <div className="space-y-4">
              {!canProceedToClose && (
                <div className="p-3 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-800">
                  <AlertTriangle size={15} className="inline mr-1" /> Complete Steps 5 and 6 before closing.
                </div>
              )}

              {/* Z-Report: Cash Reconciliation */}
              <div className="rounded-xl border border-slate-200 overflow-hidden">
                <div className="px-4 py-2 bg-slate-100 text-xs font-bold uppercase tracking-wider text-slate-600">Cash Drawer Reconciliation</div>
                <div className="p-4 space-y-2 text-sm">
                  <div className="flex justify-between"><span className="text-slate-500">Opening Float</span><span className="font-mono">{formatPHP(preview?.starting_float || 0)}</span></div>
                  <div className="flex justify-between"><span className="text-emerald-600">+ Cash Sales</span><span className="font-mono font-semibold text-emerald-700">{formatPHP(preview?.total_cash_sales || 0)}</span></div>
                  {(preview?.total_split_cash || 0) > 0 && (
                    <div className="flex justify-between"><span className="text-teal-600">+ Split Cash Portion</span><span className="font-mono font-semibold text-teal-700">{formatPHP(preview?.total_split_cash || 0)}</span></div>
                  )}
                  <div className="flex justify-between"><span className="text-blue-600">+ Partial Cash Received</span><span className="font-mono text-blue-700">{formatPHP(preview?.total_partial_cash || 0)}</span></div>
                  <div className="flex justify-between"><span className="text-indigo-600">+ AR Cash Payments</span><span className="font-mono text-indigo-700">{formatPHP(preview?.total_cash_ar ?? preview?.total_ar_received ?? 0)}</span></div>
                  {(preview?.total_digital_ar || 0) > 0 && (
                    <div className="flex justify-between text-xs"><span className="text-indigo-400 pl-3">AR Digital (e-wallet, not in drawer)</span><span className="font-mono text-indigo-400">{formatPHP(preview?.total_digital_ar || 0)}</span></div>
                  )}
                  {(preview?.ar_interest_collected || 0) > 0 && (
                    <div className="flex justify-between text-xs"><span className="text-amber-500 pl-3">↳ of which interest collected</span><span className="font-mono text-amber-600">{formatPHP(preview.ar_interest_collected)}</span></div>
                  )}
                  {(preview?.ar_discount_today || 0) > 0 && (
                    <div className="flex justify-between text-xs"><span className="text-blue-500 pl-3">↳ discounts given on interest/penalty</span><span className="font-mono text-blue-600">-{formatPHP(preview.ar_discount_today)}</span></div>
                  )}
                  {(preview?.net_fund_transfers || 0) !== 0 && (
                    <>
                      {(preview?.capital_to_cashier || 0) > 0 && (
                        <div className="flex justify-between"><span className="text-cyan-600">+ Capital Injection</span><span className="font-mono font-semibold text-cyan-700">{formatPHP(preview.capital_to_cashier)}</span></div>
                      )}
                      {(preview?.safe_to_cashier || 0) > 0 && (
                        <div className="flex justify-between"><span className="text-cyan-600">+ Safe → Cashier</span><span className="font-mono text-cyan-700">{formatPHP(preview.safe_to_cashier)}</span></div>
                      )}
                      {(preview?.cashier_to_safe || 0) > 0 && (
                        <div className="flex justify-between"><span className="text-orange-600">- Cashier → Safe</span><span className="font-mono font-semibold text-orange-600">{formatPHP(preview.cashier_to_safe)}</span></div>
                      )}
                    </>
                  )}
                  <Separator />
                  <div className="flex justify-between font-semibold"><span className="text-green-700">= Total Cash In</span><span className="font-mono text-green-700">{formatPHP(preview?.total_cash_in || 0)}</span></div>
                  <div className="flex justify-between"><span className="text-red-600">- Cashier Expenses</span><span className="font-mono font-semibold text-red-600">{formatPHP(preview?.total_cashier_expenses ?? preview?.total_expenses ?? 0)}</span></div>
                  {(preview?.total_safe_expenses || 0) > 0 && (
                    <div className="flex justify-between text-xs"><span className="text-red-400 pl-3">Safe expenses (not from drawer)</span><span className="font-mono text-red-400">{formatPHP(preview?.total_safe_expenses || 0)}</span></div>
                  )}
                  {(preview?.total_digital_expenses || 0) > 0 && (
                    <div className="flex justify-between text-xs"><span className="text-cyan-500 pl-3">Digital expenses (not from drawer)</span><span className="font-mono text-cyan-500">{formatPHP(preview?.total_digital_expenses || 0)}</span></div>
                  )}
                  <Separator />
                  <div className="flex justify-between font-bold text-base"><span>Expected in Drawer</span><span className="font-mono">{formatPHP(expectedCash)}</span></div>
                  <div className="flex justify-between font-bold text-base"><span>Actual Count</span><span className="font-mono">{formatPHP(parseFloat(actualCash) || 0)}</span></div>
                  {overShort !== null && (
                    <div className={`flex justify-between font-bold ${overShort >= 0 ? 'text-emerald-700' : 'text-red-600'}`}>
                      <span>Over / Short</span>
                      <span className="font-mono">{overShort > 0 ? '+' : ''}{formatPHP(overShort)}</span>
                    </div>
                  )}
                </div>
              </div>

              {/* Z-Report: Fund Allocation */}
              <div className="grid grid-cols-2 gap-3">
                <div className="p-3 rounded-lg bg-slate-50 border border-slate-200 text-sm">
                  <p className="text-xs text-slate-500 uppercase font-medium mb-0.5">To Vault</p>
                  <p className="text-lg font-bold font-mono">{formatPHP(parseFloat(cashToSafe) || 0)}</p>
                </div>
                <div className="p-3 rounded-lg bg-emerald-50 border border-emerald-200 text-sm">
                  <p className="text-xs text-emerald-600 uppercase font-medium mb-0.5">Float Tomorrow</p>
                  <p className="text-lg font-bold font-mono text-emerald-700">{formatPHP(parseFloat(cashToDrawer) || 0)}</p>
                </div>
              </div>

              {/* Z-Report: Discount Breakdown by Product */}
              {(preview?.discount_breakdown?.products?.length > 0 || (preview?.discount_breakdown?.total_overall_discount || 0) > 0) && (
                <div className="rounded-xl border border-slate-200 overflow-hidden" data-testid="z-discount-breakdown">
                  <div className="px-4 py-2 bg-rose-50 text-xs font-bold uppercase tracking-wider text-rose-700 flex items-center justify-between">
                    <span><Percent size={12} className="inline mr-1" /> Discounts Given Today</span>
                    <span className="font-mono">{formatPHP(preview?.discount_breakdown?.total_discount || 0)}</span>
                  </div>
                  <div className="p-3 space-y-2">
                    {(preview?.discount_breakdown?.products || []).length > 0 && (
                      <div className="overflow-hidden border border-rose-100 rounded">
                        <table className="w-full text-xs">
                          <thead className="bg-rose-50/60">
                            <tr>
                              <th className="text-left px-2 py-1.5 text-rose-700 font-medium">Product</th>
                              <th className="text-right px-2 py-1.5 text-rose-700 font-medium w-20">Units</th>
                              <th className="text-right px-2 py-1.5 text-rose-700 font-medium w-28">Total Disc.</th>
                              <th className="text-right px-2 py-1.5 text-rose-700 font-medium w-28">Avg / Unit</th>
                            </tr>
                          </thead>
                          <tbody>
                            {(preview.discount_breakdown.products || []).map((p, i) => (
                              <tr key={i} className="border-t border-rose-50">
                                <td className="px-2 py-1 text-slate-700">{p.product_name}</td>
                                <td className="px-2 py-1 text-right font-mono">{p.units_sold}</td>
                                <td className="px-2 py-1 text-right font-mono text-rose-600">{formatPHP(p.total_discount)}</td>
                                <td className="px-2 py-1 text-right font-mono text-rose-500">{formatPHP(p.avg_discount_per_unit)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                    {(preview?.discount_breakdown?.total_overall_discount || 0) > 0 && (
                      <div className="text-xs text-rose-700 flex items-center justify-between bg-rose-50 px-2 py-1 rounded">
                        <span>Overall (invoice-level) discounts</span>
                        <span className="font-mono">{formatPHP(preview.discount_breakdown.total_overall_discount)}</span>
                      </div>
                    )}
                    <p className="text-[10px] text-slate-500 italic">
                      {preview?.discount_breakdown?.transaction_count || 0} transaction(s) had discounts. Tracked in Reports → Discounts.
                    </p>
                  </div>
                </div>
              )}

              {/* Z-Report: Price Changes Today */}
              {(preview?.price_changes_today?.count || 0) > 0 && (
                <div className="rounded-xl border border-slate-200 overflow-hidden" data-testid="z-price-changes">
                  <div className="px-4 py-2 bg-amber-50 text-xs font-bold uppercase tracking-wider text-amber-700 flex items-center justify-between">
                    <span>Permanent Price Changes Today</span>
                    <span className="font-mono">{preview.price_changes_today.count} change(s)</span>
                  </div>
                  <div className="p-3 space-y-1 text-xs">
                    {(preview.price_changes_today.rows || []).map((r, i) => (
                      <div key={i} className="flex items-center justify-between border-b border-amber-50 last:border-0 py-1">
                        <div className="min-w-0 flex-1">
                          <span className="font-medium text-slate-700">{r.product_name}</span>
                          <span className="ml-2 text-[10px] text-slate-400">{r.invoice_number}</span>
                        </div>
                        <div className="font-mono w-44 text-right">
                          <span className="line-through text-slate-400">{formatPHP(r.old_price)}</span>
                          {' → '}
                          <span className={`font-semibold ${r.new_price < r.old_price ? 'text-red-600' : 'text-emerald-600'}`}>
                            {formatPHP(r.new_price)}
                          </span>
                        </div>
                        <div className="w-32 text-right text-[10px] text-slate-500">
                          {r.approver_name && `OK ${r.approver_name}`}
                        </div>
                      </div>
                    ))}
                    <p className="text-[10px] text-slate-500 italic pt-1">
                      Tracked in Reports → Price Changes.
                    </p>
                  </div>
                </div>
              )}

              {/* Z-Report: AR Cash Payments — Detailed */}
              {(preview?.ar_payments || []).filter(p => p.fund_source !== 'discount').length > 0 && (
                <div className="rounded-xl border border-indigo-200 overflow-hidden" data-testid="z-ar-payments">
                  <div className="px-4 py-2 bg-indigo-50 flex items-center justify-between flex-wrap gap-2">
                    <div className="flex items-center gap-3">
                      <p className="text-xs font-bold uppercase tracking-wider text-indigo-600">AR Collections Today</p>
                      {/* Method breakdown chips */}
                      {preview?.ar_payment_by_method && Object.entries(preview.ar_payment_by_method).map(([m, v]) => (
                        <span key={m} className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-700">
                          {m}: {formatPHP(v.total)}
                        </span>
                      ))}
                    </div>
                    <span className="font-bold font-mono text-indigo-700 text-sm">{formatPHP((preview?.ar_payments || []).filter(p => p.fund_source !== 'discount').reduce((s, p) => s + p.amount_paid, 0))}</span>
                  </div>
                  <div className="divide-y divide-indigo-100">
                    {preview.ar_payments.filter(p => p.fund_source !== 'discount').map((p, i) => (
                      <div key={i} className="px-4 py-2 flex items-center justify-between text-sm">
                        <div>
                          <span className="font-medium text-slate-800">{p.customer_id ? <button onClick={() => openCustStmt(p.customer_id, p.customer_name)} className="hover:text-[#1A4D2E] hover:underline">{p.customer_name}</button> : p.customer_name}</span>
                          <button className="text-xs text-blue-600 hover:underline font-mono ml-2" onClick={() => openDetailModal(p.invoice_number)}>{p.invoice_number}</button>
                          <span className={`ml-2 text-[10px] px-1.5 py-0.5 rounded font-medium ${p.fund_source === 'cashier' ? 'bg-emerald-100 text-emerald-700' : 'bg-violet-100 text-violet-700'}`}>
                            {p.method || (p.fund_source === 'cashier' ? 'Cash' : 'Digital')}
                          </span>
                          {p.interest_paid > 0 && (
                            <span className="ml-1 text-[10px] text-amber-600">INT: {formatPHP(p.interest_paid)}</span>
                          )}
                        </div>
                        <span className="font-mono font-semibold text-indigo-700">{formatPHP(p.amount_paid)}</span>
                      </div>
                    ))}
                  </div>
                  {/* Discounts given on interest/penalty */}
                  {(preview?.ar_discount_today || 0) > 0 && (
                    <div className="px-4 py-2 bg-blue-50/60 border-t border-indigo-100 flex items-center justify-between text-xs">
                      <span className="text-blue-600 flex items-center gap-1">Discounts given on interest/penalty (not in cashier drawer)</span>
                      <span className="font-mono font-semibold text-blue-600">-{formatPHP(preview.ar_discount_today)}</span>
                    </div>
                  )}
                </div>
              )}

              {/* Z-Report: Interest Invoices Created Today */}
              {(preview?.interest_invoices_today || []).length > 0 && (
                <div className="rounded-xl border border-amber-200 overflow-hidden" data-testid="z-interest-invoices">
                  <div className="px-4 py-2 bg-amber-50 flex items-center justify-between">
                    <p className="text-xs font-bold uppercase tracking-wider text-amber-700">Interest & Penalty Invoices Issued Today</p>
                    <span className="font-mono font-bold text-amber-700 text-sm">{formatPHP((preview.interest_invoices_today || []).reduce((s, i) => s + (i.grand_total || 0), 0))}</span>
                  </div>
                  <div className="divide-y divide-amber-100">
                    {(preview.interest_invoices_today || []).map((inv, i) => (
                      <div key={i} className="px-4 py-2 flex items-center justify-between text-sm">
                        <div>
                          <span className="font-medium text-slate-700">{inv.customer_name}</span>
                          <span className="font-mono text-xs text-slate-400 ml-2">{inv.invoice_number}</span>
                          <span className={`ml-2 text-[9px] px-1.5 py-0.5 rounded font-medium ${inv.sale_type === 'penalty_charge' ? 'bg-red-100 text-red-700' : 'bg-amber-100 text-amber-700'}`}>
                            {inv.sale_type === 'penalty_charge' ? 'Penalty' : inv.manual_interest ? 'Manual INT' : 'Interest'}
                          </span>
                        </div>
                        <div className="text-right">
                          <span className="font-mono font-semibold text-amber-700">{formatPHP(inv.grand_total)}</span>
                          {inv.amount_paid > 0 && (
                            <span className="text-[10px] text-emerald-600 ml-2">({formatPHP(inv.amount_paid)} collected)</span>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Z-Report: Capital Injections — Detailed, separated by target */}
              {(preview?.fund_transfers_today || []).length > 0 && (
                <div className="rounded-xl border border-cyan-200 overflow-hidden">
                  <div className="px-4 py-2 bg-cyan-50 flex items-center justify-between">
                    <p className="text-xs font-bold uppercase tracking-wider text-cyan-700">Fund Transfers</p>
                    <span className="font-bold font-mono text-cyan-700 text-sm">{preview.net_fund_transfers > 0 ? '+' : ''}{formatPHP(preview?.net_fund_transfers || 0)}</span>
                  </div>
                  <div className="divide-y divide-cyan-100">
                    {preview.fund_transfers_today.map((ft, i) => {
                      const isCapital = ft.type === 'capital_add';
                      const isCashierToSafe = ft.type === 'cashier_to_safe';
                      const label = isCapital
                        ? `Capital → ${ft.target_wallet === 'safe' ? 'Safe' : 'Cashier'}`
                        : ft.type === 'safe_to_cashier' ? 'Safe → Cashier'
                        : 'Cashier → Safe';
                      return (
                        <div key={i} className="px-4 py-2 flex items-center justify-between text-sm">
                          <div>
                            <span className="font-medium text-slate-800">{label}</span>
                            {ft.note && <span className="text-xs text-slate-400 ml-2">{ft.note}</span>}
                            {ft.authorized_by && <span className="text-[10px] text-slate-400 ml-1">by {ft.authorized_by}</span>}
                          </div>
                          <span className={`font-mono font-semibold ${isCashierToSafe ? 'text-orange-600' : 'text-cyan-700'}`}>
                            {isCashierToSafe ? '-' : '+'}{formatPHP(ft.amount)}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Z-Report: Cashier Expenses — Detailed */}
              {(() => {
                const cashierExps = (preview?.expenses || []).filter(e => e.fund_source !== 'safe');
                const safeExps = (preview?.expenses || []).filter(e => e.fund_source === 'safe');
                return (
                  <>
                    {cashierExps.length > 0 && (
                      <div className="rounded-xl border border-red-200 overflow-hidden">
                        <div className="px-4 py-2 bg-red-50 flex items-center justify-between">
                          <p className="text-xs font-bold uppercase tracking-wider text-red-600">Cashier Expenses (from drawer)</p>
                          <span className="font-bold font-mono text-red-700 text-sm">{formatPHP(preview?.total_cashier_expenses || 0)}</span>
                        </div>
                        <div className="divide-y divide-red-100">
                          {cashierExps.map((e, i) => (
                            <div key={i} className="px-4 py-2 text-sm">
                              <div className="flex items-center justify-between">
                                <div>
                                  <span className="font-medium text-slate-800">{e.description || e.category}</span>
                                  <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded bg-red-100 text-red-600 font-medium">{e.category}</span>
                                  {e.employee_name && <span className="text-xs text-slate-400 ml-1">({e.employee_name})</span>}
                                  {e.customer_name && <span className="text-xs text-slate-400 ml-1">({e.customer_name})</span>}
                                </div>
                                <span className="font-mono font-semibold text-red-700">{formatPHP(e.amount)}</span>
                              </div>
                              {e.notes && <p className="text-[11px] text-slate-400 mt-0.5">{e.notes}</p>}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Z-Report: Safe Expenses — Detailed */}
                    {safeExps.length > 0 && (
                      <div className="rounded-xl border border-orange-200 overflow-hidden">
                        <div className="px-4 py-2 bg-orange-50 flex items-center justify-between">
                          <p className="text-xs font-bold uppercase tracking-wider text-orange-600">Safe Expenses (not from drawer)</p>
                          <span className="font-bold font-mono text-orange-700 text-sm">{formatPHP(preview?.total_safe_expenses || 0)}</span>
                        </div>
                        <div className="divide-y divide-orange-100">
                          {safeExps.map((e, i) => (
                            <div key={i} className="px-4 py-2 text-sm">
                              <div className="flex items-center justify-between">
                                <div>
                                  <span className="font-medium text-slate-800">{e.description || e.category}</span>
                                  <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded bg-orange-100 text-orange-600 font-medium">{e.category}</span>
                                </div>
                                <span className="font-mono font-semibold text-orange-700">{formatPHP(e.amount)}</span>
                              </div>
                              {e.notes && <p className="text-[11px] text-slate-400 mt-0.5">{e.notes}</p>}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </>
                );
              })()}

              {/* Z-Report: Credit Extended Today — Detailed */}
              {(preview?.credit_sales_today || []).length > 0 && (
                <div className="rounded-xl border border-amber-200 overflow-hidden">
                  <div className="px-4 py-2 bg-amber-50 flex items-center justify-between">
                    <p className="text-xs font-bold uppercase tracking-wider text-amber-700">Credit Extended Today</p>
                    <span className="font-bold font-mono text-amber-700 text-sm">{formatPHP(preview?.total_credit_today || 0)}</span>
                  </div>
                  <div className="divide-y divide-amber-100">
                    {preview.credit_sales_today.map((inv, i) => (
                      <div key={i} className="px-4 py-2 flex items-center justify-between text-sm">
                        <div>
                          <span className="font-medium text-slate-800">{inv.customer_id ? <button onClick={() => openCustStmt(inv.customer_id, inv.customer_name)} className="hover:text-[#1A4D2E] hover:underline">{inv.customer_name}</button> : inv.customer_name}</span>
                          <button className="text-xs text-blue-600 hover:underline font-mono ml-2" onClick={() => openDetailModal(inv.invoice_number)}>{inv.invoice_number}</button>
                          <span className={`ml-2 text-[10px] px-1.5 py-0.5 rounded font-medium ${inv.payment_type === 'credit' ? 'bg-red-100 text-red-600' : 'bg-amber-100 text-amber-600'}`}>
                            {inv.payment_type === 'credit' ? 'Full Credit' : 'Partial'}
                          </span>
                        </div>
                        <div className="text-right">
                          <span className="font-mono font-semibold text-amber-700">{formatPHP(inv.balance)}</span>
                          {inv.amount_paid > 0 && <span className="text-[10px] text-emerald-500 ml-1">(paid {formatPHP(inv.amount_paid)})</span>}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Z-Report: E-Wallet Payments — Detailed */}
              {(preview?.digital_sales_today || []).length > 0 && (
                <div className="rounded-xl border border-violet-200 overflow-hidden">
                  <div className="px-4 py-2 bg-violet-50 flex items-center justify-between">
                    <p className="text-xs font-bold uppercase tracking-wider text-violet-700">E-Wallet Payments</p>
                    <span className="font-bold font-mono text-violet-700 text-sm">{formatPHP(preview?.total_digital_today || 0)}</span>
                  </div>
                  <p className="px-4 py-1 text-[11px] text-violet-500 bg-violet-50/50">Tracked separately — not part of cash drawer</p>
                  <div className="divide-y divide-violet-100">
                    {preview.digital_sales_today.map((d, i) => (
                      <div key={i} className="px-4 py-2 flex items-center justify-between text-sm">
                        <div>
                          <span className="font-medium text-slate-800">{d.customer_id ? <button onClick={() => openCustStmt(d.customer_id, d.customer_name)} className="hover:text-[#1A4D2E] hover:underline">{d.customer_name || 'Walk-in'}</button> : (d.customer_name || 'Walk-in')}</span>
                          <button className="text-xs text-blue-600 hover:underline font-mono ml-2" onClick={() => openDetailModal(d.invoice_number)}>{d.invoice_number}</button>
                          <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded bg-violet-100 text-violet-600 font-medium">{d.platform}</span>
                          {d.ref_number && <span className="text-[10px] text-slate-400 ml-1">Ref: {d.ref_number}</span>}
                        </div>
                        <span className="font-mono font-semibold text-violet-700">{formatPHP(d.amount)}</span>
                      </div>
                    ))}
                  </div>
                  {/* Platform totals */}
                  <div className="px-4 py-2 bg-violet-50 border-t border-violet-200">
                    {Object.entries(preview?.digital_by_platform || {}).map(([platform, amt]) => (
                      <div key={platform} className="flex justify-between text-xs py-0.5 text-violet-600">
                        <span className="font-medium">{platform} Total</span><span className="font-mono font-semibold">{formatPHP(amt)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Batch Mode: Per-Day Breakdown */}
              {inGroupMode && batchPreview?.daily_breakdown && (
                <div className="rounded-xl border border-slate-200 overflow-hidden">
                  <div className="px-4 py-2 bg-slate-100 text-xs font-bold uppercase tracking-wider text-slate-600">Per-Day Breakdown</div>
                  <div className="divide-y divide-slate-100">
                    {Object.entries(batchPreview.daily_breakdown).sort(([a],[b]) => a.localeCompare(b)).map(([d, info]) => {
                      const dayLabel = new Date(d + 'T12:00:00').toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
                      return (
                        <div key={d} className="px-4 py-2 text-sm flex items-center justify-between">
                          <div>
                            <span className="font-medium">{dayLabel}</span>
                            <span className="text-xs text-slate-400 ml-2">
                              {Object.entries(info.sales_by_method || {}).map(([m, t]) => `${m}: ${formatPHP(t)}`).join(' · ') || 'No sales'}
                            </span>
                          </div>
                          <div className="text-right">
                            <span className="font-mono text-emerald-700">{formatPHP(info.sales_total || 0)}</span>
                            {(info.expenses_total || 0) > 0 && <span className="font-mono text-red-500 ml-3">-{formatPHP(info.expenses_total)}</span>}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Group Mode: Reason (same state as the top input; editable here too) */}
              {inGroupMode && (
                <div className="rounded-xl border border-amber-200 bg-amber-50 p-3">
                  <Label className="text-sm font-semibold text-amber-800">Group Close Reason *</Label>
                  <Input
                    data-testid="batch-reason-zreport"
                    value={batchReason}
                    onChange={e => setBatchReason(e.target.value)}
                    placeholder="e.g., Store audit, holiday break..."
                    className="mt-1 h-9 bg-white border-amber-200"
                  />
                </div>
              )}

              <Separator />

              {/* Manager PIN */}
              <div className="max-w-sm">
                <Label className="text-sm font-semibold flex items-center gap-1.5">
                  <Lock size={14} className="text-slate-500" /> Manager / Owner Sign-Off
                </Label>
                {pinVerified ? (
                  <div className="mt-1 p-2.5 rounded-lg bg-emerald-50 border border-emerald-200 flex items-center gap-2 text-sm">
                    <CheckCircle2 size={16} className="text-emerald-600" />
                    <span className="text-emerald-700 font-medium">Signed off by {managerName}</span>
                  </div>
                ) : (
                  <div className="flex gap-2 mt-1">
                    <Input
                      data-testid="manager-pin-input"
                      type="password" autoComplete="new-password"
                      value={managerPin}
                      onChange={e => setManagerPin(e.target.value)}
                      placeholder="Enter manager PIN"
                      className="h-9"
                      onKeyDown={e => e.key === 'Enter' && verifyManagerPin()}
                    />
                    <Button size="sm" onClick={verifyManagerPin} disabled={pinVerifying || !canProceedToClose}
                      className="shrink-0 bg-slate-700 hover:bg-slate-800 text-white">
                      {pinVerifying ? <RefreshCw size={13} className="animate-spin" /> : 'Verify'}
                    </Button>
                  </div>
                )}
              </div>

              {/* Close Day Button */}
              <Button
                data-testid="close-day-btn"
                onClick={inGroupMode ? handleBatchClose : handleCloseDay}
                disabled={!pinVerified || !canProceedToClose || closing || isClosed || (inGroupMode && !batchReason.trim())}
                className="w-full h-12 bg-red-600 hover:bg-red-700 text-white text-base font-semibold"
              >
                {closing ? <><RefreshCw size={16} className="animate-spin mr-2" />Closing...</>
                  : isClosed ? <><CheckCircle2 size={16} className="mr-2" /> Already Closed</>
                  : inGroupMode ? <><Lock size={16} className="mr-2" /> Close {batchDates.length} Days as Group ({batchDates[0]} → {batchDates[batchDates.length-1]})</>
                  : <><Lock size={16} className="mr-2" /> Close Accounts for {date}</>}
              </Button>
              <p className="text-xs text-slate-400 text-center">
                {inGroupMode ? `This will close ${batchDates.length} days as a single entry. This action is permanent.` : 'This action is permanent. The day will be locked and the Z-Report saved.'}
              </p>
            </div>
          )}

          {/* ── STEP 8: Open Tomorrow ── */}
          {step === 8 && (
            <div className="space-y-4 text-center max-w-lg mx-auto py-4">
              {isClosed ? (
                <>
                  <div className="w-16 h-16 rounded-full bg-emerald-100 flex items-center justify-center mx-auto">
                    <CheckCircle2 size={32} className="text-emerald-600" />
                  </div>
                  <h2 className="text-xl font-bold" style={{ fontFamily: 'Manrope' }}>
                    {closingRecord?.is_batch ? 'Batch Close Complete' : 'Day Closed Successfully'}
                  </h2>
                  <p className="text-slate-500 text-sm">
                    {closingRecord?.is_batch ? (
                      <>{closingRecord.date_from} to {closingRecord.date_to} ({closingRecord.dates_covered?.length} days) closed for <strong>{currentBranch?.name}</strong>.<br />
                      Reason: <em>{closingRecord.batch_reason}</em></>
                    ) : (
                      <>{date} has been closed for <strong>{currentBranch?.name}</strong>.<br />
                      All new sales, purchase orders, and expenses will automatically be dated <strong>tomorrow</strong>.</>
                    )}
                  </p>
                  {closingRecord && (
                    <div className="mt-2 grid grid-cols-3 gap-3 text-sm text-left">
                      {[
                        { label: 'Closed by', value: closingRecord.closed_by_name },
                        { label: 'To Safe', value: formatPHP(closingRecord.cash_to_safe) },
                        { label: 'Float', value: formatPHP(closingRecord.cash_to_drawer) },
                        { label: 'Over/Short', value: (closingRecord.over_short > 0 ? '+' : '') + formatPHP(closingRecord.over_short) },
                        { label: 'Total Cash', value: formatPHP(closingRecord.actual_cash) },
                        { label: 'AR Received', value: formatPHP(closingRecord.total_ar_received) },
                      ].map(({ label, value }) => (
                        <div key={label} className="p-2 rounded-lg bg-slate-50 border border-slate-100">
                          <p className="text-xs text-slate-400">{label}</p>
                          <p className="font-semibold">{value}</p>
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="flex gap-3 justify-center mt-4">
                    <Button variant="outline" onClick={() => window.print()}>Print Z-Report</Button>
                    <Button variant="outline" onClick={() => {
                      const url = `${process.env.REACT_APP_BACKEND_URL}/api/reports/z-report-pdf?date=${inGroupMode ? batchDates[0] : preview?.date || ''}&branch_id=${preview?.branch_id || ''}`;
                      window.open(url, '_blank');
                    }}>
                      <Download size={14} className="mr-1.5" /> Download PDF
                    </Button>
                    {unclosedDays.length > 1 ? (
                      <Button onClick={advanceToNextDay} className="bg-[#1A4D2E] text-white" data-testid="close-next-day-btn">
                        <Sun size={15} className="mr-1.5" /> Close Next Day
                      </Button>
                    ) : (
                      <Button onClick={() => navigate('/sales-new')} className="bg-[#1A4D2E] text-white">
                        <Sun size={15} className="mr-1.5" /> Open Tomorrow's Sales
                      </Button>
                    )}
                  </div>
                </>
              ) : (
                <div className="py-8">
                  <p className="text-slate-400">Complete Step 7 (Close & Sign Off) first.</p>
                  <Button variant="outline" onClick={() => setStep(7)} className="mt-3">Go to Step 7</Button>
                </div>
              )}
            </div>
          )}

        </CardContent>
      </Card>

      {/* Navigation */}
      <div className="flex items-center justify-between">
        <Button variant="outline" onClick={() => setStep(s => Math.max(1, s - 1))} disabled={step === 1}>
          <ChevronLeft size={15} className="mr-1" /> Previous
        </Button>
        <span className="text-sm text-slate-400">Step {step} of {STEPS.length}</span>
        <Button onClick={() => { if (step < 8) { markComplete(step); setStep(s => s + 1); } }}
          disabled={step === 8 || (step === 7 && !isClosed)}
          className="bg-[#1A4D2E] hover:bg-[#14532d] text-white" data-testid="next-step-btn">
          {step === 7 ? (isClosed ? 'Next' : 'Close First') : 'Next'} <ChevronRight size={15} className="ml-1" />
        </Button>
      </div>

      {/* ── Quick Add Sale Dialog ── */}
      <Dialog open={saleDialog} onOpenChange={setSaleDialog}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader><DialogTitle style={{ fontFamily: 'Manrope' }}>Quick Add Sale</DialogTitle></DialogHeader>
          <div className="space-y-3 mt-2">
            <div className="relative">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <Input value={saleForm.search} onChange={e => searchProducts(e.target.value)}
                placeholder="Search product..." className="pl-8" autoFocus />
              {saleMatches.length > 0 && (
                <div className="absolute z-50 top-full mt-1 w-full bg-white border border-slate-200 rounded-lg shadow-lg max-h-40 overflow-y-auto">
                  {saleMatches.map(p => (
                    <button key={p.id} onMouseDown={() => {
                      const defaultPrice = p.prices?.retail || p.prices?.wholesale || p.cost_price || '';
                      setSaleForm(f => ({ ...f, product: p, search: p.name, price: String(defaultPrice) }));
                      setSaleMatches([]);
                    }} className="w-full text-left px-3 py-2 hover:bg-slate-50 text-sm border-b last:border-0">
                      <span className="font-medium">{p.name}</span>
                      <span className="text-slate-400 text-xs ml-2">{p.unit} · Cost {formatPHP(p.cost_price)}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
            {saleForm.product && (
              <div className="grid grid-cols-2 gap-3">
                <div><Label>Quantity</Label><Input type="number" min={1} value={saleForm.qty}
                  onChange={e => setSaleForm(f => ({ ...f, qty: parseInt(e.target.value)||1 }))} className="h-9 mt-1" /></div>
                <div><Label>Unit Price</Label><Input type="number" min={0} value={saleForm.price}
                  onChange={e => setSaleForm(f => ({ ...f, price: e.target.value }))} className="h-9 mt-1 font-mono" /></div>
              </div>
            )}
            <div>
              <Label>Payment Type</Label>
              <Select value={saleForm.paymentType} onValueChange={v => setSaleForm(f => ({ ...f, paymentType: v }))}>
                <SelectTrigger className="mt-1 h-9"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="cash">Cash</SelectItem>
                  <SelectItem value="credit">Credit</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {saleForm.paymentType === 'credit' && (
              <div><Label>Customer Name</Label>
                <Input value={saleForm.customerName} onChange={e => setSaleForm(f => ({ ...f, customerName: e.target.value }))} className="h-9 mt-1" placeholder="Customer name" /></div>
            )}
            {saleForm.product && saleForm.price && (
              <div className="p-2 bg-emerald-50 rounded border border-emerald-200 text-sm">
                Total: <strong className="font-mono">{formatPHP((parseFloat(saleForm.price)||0) * saleForm.qty)}</strong>
              </div>
            )}
            <div className="flex gap-2 pt-1">
              <Button variant="outline" className="flex-1" onClick={() => setSaleDialog(false)}>Cancel</Button>
              <Button className="flex-1 bg-[#1A4D2E] text-white" onClick={quickAddSale}
                disabled={!saleForm.product}>Add Sale</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* ── Quick Add Expense Dialog ── */}
      <Dialog open={expDialog} onOpenChange={v => { if (!v) resetExpDialog(); else setExpDialog(true); }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader><DialogTitle style={{ fontFamily: 'Manrope' }}>Add Expense</DialogTitle></DialogHeader>
          <div className="space-y-3 mt-1">

            {/* Expense Type */}
            <div>
              <Label className="text-xs text-slate-500">Type</Label>
              <Select value={expForm.expenseType} onValueChange={v => {
                setExpForm(f => ({ ...f, expenseType: v, category: 'Miscellaneous' }));
                setExpCustomerSearch(''); setExpCustomerMatches([]); setExpCustomerSelected(null);
                setExpEmployeeSelected(null); setExpCaSummary(null); setExpCaPinNeeded(false); setExpCaPin(''); setExpCaPinVerified(false);
              }}>
                <SelectTrigger className="mt-1 h-9"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="regular">Regular Expense</SelectItem>
                  <SelectItem value="farm">Farm Service (bill to customer)</SelectItem>
                  <SelectItem value="cashout">Customer Cash Out</SelectItem>
                  <SelectItem value="advance">Employee Advance</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* Category — Regular only */}
            {expForm.expenseType === 'regular' && (
              <div>
                <Label className="text-xs text-slate-500">Category</Label>
                <Select value={expForm.category} onValueChange={v => setExpForm(f => ({ ...f, category: v }))}>
                  <SelectTrigger className="mt-1 h-9"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {EXPENSE_CATEGORIES.map(c => <SelectItem key={c} value={c}>{c}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            )}

            {/* Customer picker — Farm / Cash Out */}
            {(expForm.expenseType === 'farm' || expForm.expenseType === 'cashout') && (
              <div className="relative">
                <Label className="text-xs text-slate-500">Customer <span className="text-red-500">*</span></Label>
                {expCustomerSelected ? (
                  <div className="mt-1 flex items-center justify-between p-2 rounded border border-emerald-300 bg-emerald-50">
                    <div>
                      <p className="text-sm font-medium text-emerald-800">{expCustomerSelected.name}</p>
                      <p className="text-xs text-emerald-600">Balance: {formatPHP(expCustomerSelected.balance || 0)}</p>
                    </div>
                    <button onClick={() => { setExpCustomerSelected(null); setExpCustomerSearch(''); }} className="text-slate-400 hover:text-red-500 px-2 text-base">×</button>
                  </div>
                ) : (
                  <div>
                    <Input value={expCustomerSearch} onChange={e => searchExpCustomer(e.target.value)}
                      placeholder="Search customer..." className="h-9 mt-1" />
                    {expCustomerMatches.length > 0 && (
                      <div className="absolute z-50 w-full bg-white border border-slate-200 rounded-lg shadow-lg mt-0.5 max-h-36 overflow-y-auto">
                        {expCustomerMatches.map(c => (
                          <button key={c.id} onMouseDown={() => { setExpCustomerSelected(c); setExpCustomerSearch(c.name); setExpCustomerMatches([]); }}
                            className="w-full text-left px-3 py-2 hover:bg-slate-50 border-b last:border-0 text-sm">
                            <span className="font-medium">{c.name}</span>
                            <span className="text-xs text-slate-400 ml-2">Bal: {formatPHP(c.balance || 0)}</span>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )}
                <p className="text-[10px] text-slate-400 mt-0.5">
                  {expForm.expenseType === 'farm' ? 'Creates an AR invoice billed to this customer.' : 'Creates a cash advance invoice for this customer.'}
                </p>
              </div>
            )}

            {/* Employee picker — Advance */}
            {expForm.expenseType === 'advance' && (
              <div className="space-y-2">
                <div>
                  <Label className="text-xs text-slate-500">Employee <span className="text-red-500">*</span></Label>
                  <Select value={expEmployeeSelected?.id || 'none'} onValueChange={v => {
                    const emp = expEmployees.find(e => e.id === v);
                    handleExpEmployeeSelect(emp || null);
                  }}>
                    <SelectTrigger className="mt-1 h-9"><SelectValue placeholder="Select employee..." /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none">— Select employee —</SelectItem>
                      {expEmployees.filter(e => e.active !== false).map(e => (
                        <SelectItem key={e.id} value={e.id}>{e.name}{e.position ? ` (${e.position})` : ''}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                {expCaSummary && (
                  <div className="p-2.5 rounded-lg bg-amber-50 border border-amber-200">
                    <div className="grid grid-cols-3 gap-2 text-center">
                      <div>
                        <p className="text-[10px] text-slate-400">This Month</p>
                        <p className={`text-sm font-bold ${expCaSummary.is_over_limit ? 'text-red-600' : 'text-amber-700'}`}>{formatPHP(expCaSummary.this_month_total)}</p>
                      </div>
                      <div>
                        <p className="text-[10px] text-slate-400">Monthly Limit</p>
                        <p className="text-sm font-bold">{expCaSummary.monthly_ca_limit > 0 ? formatPHP(expCaSummary.monthly_ca_limit) : 'No limit'}</p>
                      </div>
                      <div>
                        <p className="text-[10px] text-slate-400">Unpaid Balance</p>
                        <p className="text-sm font-bold text-slate-700">{formatPHP(expCaSummary.total_advance_balance)}</p>
                      </div>
                    </div>
                    {expCaPinVerified && (
                      <p className="text-xs text-emerald-700 mt-1.5 flex items-center gap-1">✓ Manager PIN verified — over-limit advance allowed</p>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* PIN dialog for CA over limit */}
            {expCaPinNeeded && (
              <div className="p-3 rounded-lg bg-red-50 border border-red-200 space-y-2">
                <p className="text-xs font-semibold text-red-800 flex items-center gap-1">
                  ⚠ Monthly CA limit exceeded — manager PIN required
                </p>
                <div className="flex gap-2">
                  <Input type="password" autoComplete="new-password" value={expCaPin} onChange={e => setExpCaPin(e.target.value)}
                    placeholder="Manager PIN" className="h-8 text-sm flex-1" maxLength={6}
                    onKeyDown={e => e.key === 'Enter' && verifyExpCaPin()} />
                  <Button size="sm" onClick={verifyExpCaPin} className="h-8 bg-red-700 text-white">Verify</Button>
                </div>
              </div>
            )}

            {/* Description */}
            <div>
              <Label className="text-xs text-slate-500">Description</Label>
              <Input value={expForm.description} onChange={e => setExpForm(f => ({ ...f, description: e.target.value }))}
                className="h-9 mt-1" placeholder={
                  expForm.expenseType === 'farm' ? 'e.g. Spraying, fertilizer application...' :
                  expForm.expenseType === 'cashout' ? 'e.g. Cash advance for market...' :
                  expForm.expenseType === 'advance' ? 'e.g. Salary advance...' :
                  'What is this expense for?'
                } />
            </div>

            {/* Amount + Payment Method */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label className="text-xs text-slate-500">Amount (₱)</Label>
                <Input type="number" min={0} value={expForm.amount}
                  onChange={e => setExpForm(f => ({ ...f, amount: e.target.value }))}
                  className="h-9 mt-1 font-mono" placeholder="0.00" />
              </div>
              <div>
                <Label className="text-xs text-slate-500">Payment Method</Label>
                <Select value={expForm.payment_method} onValueChange={v => setExpForm(f => ({ ...f, payment_method: v }))}>
                  <SelectTrigger className="mt-1 h-9"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {PAYMENT_METHODS.map(m => <SelectItem key={m} value={m}>{m}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>

            {/* Reference # + Notes */}
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label className="text-xs text-slate-500">Reference # (optional)</Label>
                <Input value={expForm.reference_number} onChange={e => setExpForm(f => ({ ...f, reference_number: e.target.value }))}
                  className="h-9 mt-1 text-sm" placeholder="OR / Check #" />
              </div>
              <div>
                <Label className="text-xs text-slate-500">Notes (optional)</Label>
                <Input value={expForm.notes} onChange={e => setExpForm(f => ({ ...f, notes: e.target.value }))}
                  className="h-9 mt-1 text-sm" placeholder="Additional details..." />
              </div>
            </div>

            <div className="flex gap-2 pt-1">
              <Button variant="outline" className="flex-1" onClick={resetExpDialog}>Cancel</Button>
              <Button className="flex-1 bg-[#1A4D2E] text-white" onClick={quickAddExpense} disabled={expSaving}>
                {expSaving ? <RefreshCw size={13} className="animate-spin mr-1" /> : null} Add Expense
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* ── Quick Receive Payment Dialog ── */}
      <Dialog open={pmtDialog.open} onOpenChange={v => setPmtDialog({ open: v, invoice: null })}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader><DialogTitle style={{ fontFamily: 'Manrope' }}>Receive Payment</DialogTitle></DialogHeader>
          {pmtDialog.invoice && (
            <div className="space-y-3 mt-2">
              <div className="p-3 rounded-lg bg-blue-50 border border-blue-200 text-sm">
                <p className="font-semibold">{pmtDialog.invoice.customer_name}</p>
                <p className="text-xs text-slate-500">{pmtDialog.invoice.invoice_number} · Balance: {formatPHP(pmtDialog.invoice.remaining_balance)}</p>
              </div>
              <div><Label>Amount Paid</Label>
                <Input type="number" min={0} step="0.01" value={pmtAmount}
                  onChange={e => setPmtAmount(e.target.value)}
                  placeholder={`Max ${formatPHP(pmtDialog.invoice.remaining_balance)}`}
                  className="h-9 mt-1 font-mono" autoFocus /></div>
              <div className="flex gap-2 pt-1">
                <Button variant="outline" className="flex-1" onClick={() => setPmtDialog({ open: false, invoice: null })}>Cancel</Button>
                <Button className="flex-1 bg-blue-600 text-white" onClick={quickReceivePayment} disabled={pmtSaving}>
                  {pmtSaving ? <RefreshCw size={13} className="animate-spin mr-1" /> : null} Record Payment
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* ── Low Stock Report Dialog ── */}
      <Dialog open={lowStockDialog} onOpenChange={setLowStockDialog}>
        <DialogContent className="sm:max-w-2xl max-h-[80vh]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
              <Package size={18} className="text-amber-500" /> Low Stock Alert — {currentBranch?.name}
            </DialogTitle>
          </DialogHeader>
          <ScrollArea className="max-h-[60vh] mt-2">
            {lowStockData === null ? <p className="text-center py-8 text-slate-400">Loading...</p> :
             lowStockData.length === 0 ? <p className="text-center py-8 text-emerald-600">All products are adequately stocked!</p> : (
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-white border-b border-slate-200">
                  <tr className="text-xs uppercase text-slate-500">
                    <th className="px-3 py-2 text-left">Product</th>
                    <th className="px-3 py-2 text-center">Status</th>
                    <th className="px-3 py-2 text-right">Current Qty</th>
                    <th className="px-3 py-2 text-right">Reorder Pt</th>
                    <th className="px-3 py-2 text-right">Reorder Qty</th>
                  </tr>
                </thead>
                <tbody>
                  {lowStockData.map((p, i) => (
                    <tr key={i} className="border-b border-slate-50 hover:bg-slate-50/50">
                      <td className="px-3 py-2">
                        <p className="font-medium">{p.name}</p>
                        <p className="text-xs text-slate-400 font-mono">{p.sku} · {p.category}</p>
                      </td>
                      <td className="px-3 py-2 text-center">
                        <Badge className={`text-[10px] ${p.status === 'out_of_stock' ? 'bg-red-100 text-red-700' : 'bg-amber-100 text-amber-700'}`}>
                          {p.status === 'out_of_stock' ? 'Out of Stock' : 'Low Stock'}
                        </Badge>
                      </td>
                      <td className="px-3 py-2 text-right font-mono font-bold">{p.current_qty} {p.unit}</td>
                      <td className="px-3 py-2 text-right font-mono text-slate-500">{p.reorder_point || '—'}</td>
                      <td className="px-3 py-2 text-right font-mono text-slate-500">{p.reorder_quantity || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </ScrollArea>
        </DialogContent>
      </Dialog>

      {/* ── Supplier Payables Report Dialog ── */}
      <Dialog open={payablesDialog} onOpenChange={setPayablesDialog}>
        <DialogContent className="sm:max-w-2xl max-h-[80vh]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
              <Truck size={18} className="text-red-500" /> Outstanding Supplier Payables
            </DialogTitle>
          </DialogHeader>
          <ScrollArea className="max-h-[60vh] mt-2">
            {payablesData === null ? <p className="text-center py-8 text-slate-400">Loading...</p> :
             payablesData.length === 0 ? <p className="text-center py-8 text-emerald-600">No outstanding payables!</p> : (
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-white border-b border-slate-200">
                  <tr className="text-xs uppercase text-slate-500">
                    <th className="px-3 py-2 text-left">Supplier / PO</th>
                    <th className="px-3 py-2 text-right">Total</th>
                    <th className="px-3 py-2 text-right">Balance Due</th>
                    <th className="px-3 py-2 text-center">Due Date</th>
                    <th className="px-3 py-2 text-center">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {payablesData.map((p, i) => (
                    <tr key={i} className={`border-b border-slate-50 hover:bg-slate-50/50 ${p.is_overdue ? 'bg-red-50/50' : p.is_urgent ? 'bg-amber-50/50' : ''}`}>
                      <td className="px-3 py-2">
                        <p className="font-medium">{p.vendor}</p>
                        <p className="text-xs font-mono"><button className="text-blue-600 hover:underline" onClick={() => openDetailModal(p.po_number, null, 'po')}>{p.po_number}</button></p>
                      </td>
                      <td className="px-3 py-2 text-right font-mono text-slate-500">{formatPHP(p.subtotal)}</td>
                      <td className="px-3 py-2 text-right font-mono font-bold text-red-700">{formatPHP(p.balance)}</td>
                      <td className="px-3 py-2 text-center">
                        <p className={`text-xs font-medium ${p.is_overdue ? 'text-red-700' : p.is_urgent ? 'text-amber-700' : 'text-slate-500'}`}>
                          {p.due_date || '—'}
                        </p>
                        {p.days_until_due !== null && (
                          <p className={`text-[10px] ${p.is_overdue ? 'text-red-500' : p.is_urgent ? 'text-amber-500' : 'text-slate-400'}`}>
                            {p.is_overdue ? `${Math.abs(p.days_until_due)}d overdue` : `${p.days_until_due}d left`}
                          </p>
                        )}
                      </td>
                      <td className="px-3 py-2 text-center">
                        <Badge className={`text-[10px] ${p.is_overdue ? 'bg-red-100 text-red-700' : p.is_urgent ? 'bg-amber-100 text-amber-700' : 'bg-slate-100 text-slate-600'}`}>
                          {p.is_overdue ? 'OVERDUE' : p.is_urgent ? 'DUE SOON' : 'Pending'}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </ScrollArea>
          <div className="flex justify-end pt-2">
            <Button variant="outline" onClick={() => navigate('/pay-supplier')}>
              <ExternalLink size={13} className="mr-1.5" /> Go to Pay Supplier
            </Button>
          </div>
        </DialogContent>
      </Dialog>
      <UploadQRDialog
        open={wizUploadQROpen}
        onClose={(count) => { setWizUploadQROpen(false); if (count > 0) toast.success(`Receipt saved!`); }}
        recordType="expense"
        recordId={wizUploadExpenseId}
      />
      <ReviewDetailDialog
        open={invoiceModalOpen && detailType === 'po'}
        onOpenChange={(open) => { if (!open) { setInvoiceModalOpen(false); setSelectedInvoiceNumber(null); } }}
        poNumber={selectedInvoiceNumber}
        showReviewAction={false}
        showPayAction={false}
      />
      <InvoiceDetailModal compact
        open={invoiceModalOpen && detailType === 'sale'}
        onOpenChange={(open) => { if (!open) { setInvoiceModalOpen(false); setSelectedInvoiceNumber(null); } }}
        invoiceNumber={selectedInvoiceNumber}
      />
      <ExpenseDetailModal
        open={expenseModalOpen}
        onOpenChange={(open) => { setExpenseModalOpen(open); if (!open) setSelectedExpenseId(null); }}
        expenseId={selectedExpenseId}
      />
      <CustomerStatementModal
        open={stmtOpen}
        onOpenChange={setStmtOpen}
        customer={stmtCustomer}
      />
    </div>
  );
}


// ─────────────────────────────────────────────────────────────────────────────
//  LateEncodedCarryover
//  Appears at the top of step 2 (Customer Credits) in the Close Wizard.
//  Lists every late-encoded invoice created since the last close for the
//  current branch — these are credits dated to prior closed days that now
//  appear on TODAY's Z-report as a clearly-tagged carryover.
// ─────────────────────────────────────────────────────────────────────────────
function LateEncodedCarryover({ branchId }) {
  const [data, setData] = React.useState(null);
  const [loading, setLoading] = React.useState(false);

  React.useEffect(() => {
    if (!branchId) return;
    let cancel = false;
    setLoading(true);
    api.get('/sales/late-encoded-since-close', { params: { branch_id: branchId } })
      .then(res => { if (!cancel) setData(res.data); })
      .catch(() => {})
      .finally(() => { if (!cancel) setLoading(false); });
    return () => { cancel = true; };
  }, [branchId]);

  if (loading || !data || data.count === 0) return null;

  return (
    <div className="rounded-lg border-2 border-amber-300 bg-amber-50 overflow-hidden" data-testid="late-encoded-carryover">
      <div className="px-4 py-2.5 bg-amber-100 border-b border-amber-200 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-amber-700">⚠</span>
          <p className="text-sm font-bold text-amber-900">
            Late-Encoded Credits Since Last Close ({data.count})
          </p>
        </div>
        <p className="text-sm font-mono font-bold text-amber-900">{formatPHP(data.total_amount)}</p>
      </div>
      <div className="px-4 py-2 text-[11px] text-amber-800 bg-amber-50 border-b border-amber-200">
        These credits were posted after their original sale date's close. The original Z-reports stay sealed —
        they are included in TODAY's Z-report as a separate carryover line.
      </div>
      <div className="divide-y divide-amber-100">
        {data.entries.map((e, i) => (
          <div key={e.id || i} className="px-4 py-2 flex items-start gap-3 text-xs">
            <span className="inline-block px-1.5 py-0.5 rounded bg-amber-200 text-amber-900 text-[9px] font-bold uppercase tracking-wider shrink-0">LATE ENCODE</span>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-semibold text-slate-800">{e.invoice_number}</span>
                <span className="text-slate-500">· {e.customer_name || 'Walk-in'}</span>
                <span className="text-[10px] text-amber-700">
                  originally {e.order_date} ({e.late_encode_days_back}d back)
                </span>
              </div>
              {e.late_encode_reason && (
                <p className="text-[10px] text-slate-500 mt-0.5 italic">"{e.late_encode_reason}"</p>
              )}
              <p className="text-[9px] text-slate-400 mt-0.5">
                by {e.late_encoded_by_name} · verified {e.late_encode_verifier_name}
              </p>
            </div>
            <p className="font-mono font-bold text-amber-700 shrink-0">{formatPHP(e.grand_total || 0)}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
