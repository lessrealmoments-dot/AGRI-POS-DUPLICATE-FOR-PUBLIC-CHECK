import { useState, useEffect, useCallback } from 'react';
import { useAuth, api } from '../contexts/AuthContext';
import { formatPHP } from '../lib/utils';
import { Card, CardContent } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Badge } from '../components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { Plus, DollarSign, ArrowDown, ArrowUp, Search, Filter, Edit2, Tractor, FileText, Banknote, AlertTriangle, Shield, Upload } from 'lucide-react';
import { toast } from 'sonner';
import UploadQRDialog from '../components/UploadQRDialog';
import ReceiptUploadInline from '../components/ReceiptUploadInline';
import VerificationBadge from '../components/VerificationBadge';
import VerifyPinDialog from '../components/VerifyPinDialog';
import ViewQRDialog from '../components/ViewQRDialog';
import ExpenseDetailModal from '../components/ExpenseDetailModal';
import InvoiceDetailModal from '../components/InvoiceDetailModal';
import CustomerStatementModal from '../components/CustomerStatementModal';
import CustomerBalanceBadge, { useCustomerBalances } from '../components/CustomerBalanceBadge';
import CalcInput from '../components/CalcInput';

const EXPENSE_CATEGORIES = [
  "Utilities", "Rent", "Supplies", "Transportation", "Fuel/Gas",
  "Employee Advance", "Farm Expense", "Customer Cash Out", "Repairs & Maintenance",
  "Marketing", "Salaries & Wages", "Communication", "Insurance",
  "Professional Fees", "Taxes & Licenses", "Office Supplies",
  "Equipment", "Miscellaneous"
];

const PAYMENT_METHODS = ["Cash", "Check", "Bank Transfer", "GCash", "Maya", "Credit Card"];

// Safely turn an axios error into a string message. Backend may return
// detail as a string OR a structured object (e.g. insufficient_funds with
// {type, message, cashier_balance, required, shortfall, suggestion}).
// Rendering the raw object into <toast.error/> crashes React. This helper
// always returns a plain string.
function getErrorMessage(e, fallback = 'Something went wrong') {
  const detail = e?.response?.data?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (detail && typeof detail === 'object') {
    if (typeof detail.message === 'string' && detail.message.trim()) return detail.message;
    if (typeof detail.detail === 'string' && detail.detail.trim()) return detail.detail;
    try { return JSON.stringify(detail); } catch { /* ignore */ }
  }
  if (typeof e?.message === 'string' && e.message.trim()) return e.message;
  return fallback;
}

export default function AccountingPage() {
  const { currentBranch, hasPerm } = useAuth();
  const canCreateExpense = hasPerm('accounting', 'create_expense');
  const canEditExpense = hasPerm('accounting', 'edit_expense');
  const canReceivePayment = hasPerm('accounting', 'receive_payment');
  const [expenses, setExpenses] = useState([]);
  const [receivables, setReceivables] = useState([]);
  const [payables, setPayables] = useState([]);
  const [customers, setCustomers] = useState([]);
  const [employees, setEmployees] = useState([]);
  const [caSummary, setCaSummary] = useState(null);
  const [caManagerPin, setCaManagerPin] = useState('');
  const [caManagerPinDialog, setCaManagerPinDialog] = useState(false);
  const [uploadQROpen, setUploadQROpen] = useState(false);
  const [uploadExpenseId, setUploadExpenseId] = useState(null);
  const [verifyExpenseId, setVerifyExpenseId] = useState(null);
  const [verifyExpenseLabel, setVerifyExpenseLabel] = useState('');
  const [verifyExpenseOpen, setVerifyExpenseOpen] = useState(false);
  const [viewQRExpenseId, setViewQRExpenseId] = useState(null);
  const [viewQRExpenseOpen, setViewQRExpenseOpen] = useState(false);
  const [invoiceModalOpen, setInvoiceModalOpen] = useState(false);
  const [selectedInvoiceNumber, setSelectedInvoiceNumber] = useState(null);
  const [selectedExpenseId, setSelectedExpenseId] = useState(null);
  const [expenseModalOpen, setExpenseModalOpen] = useState(false);
  const openDetailModal = (num = null, expId = null) => {
    if (expId) { setSelectedExpenseId(expId); setExpenseModalOpen(true); }
    else if (num) { setSelectedInvoiceNumber(num); setInvoiceModalOpen(true); }
  };
  const [stmtOpen, setStmtOpen] = useState(false);
  const [stmtCustomer, setStmtCustomer] = useState(null);
  const openCustStmt = (id, name) => { if (!id) return; setStmtCustomer({ id, name }); setStmtOpen(true); };
  const { byId: balanceById } = useCustomerBalances();
  
  // Dialog states
  const [expenseDialog, setExpenseDialog] = useState(false);
  const [farmExpenseDialog, setFarmExpenseDialog] = useState(false);
  const [cashOutDialog, setCashOutDialog] = useState(false);
  const [payableDialog, setPayableDialog] = useState(false);
  const [paymentDialog, setPaymentDialog] = useState(false);
  const [paymentTarget, setPaymentTarget] = useState(null);
  const [paymentType, setPaymentType] = useState('');
  const [paymentAmount, setPaymentAmount] = useState(0);
  const [editMode, setEditMode] = useState(false);
  
  // Forms
  const [expenseForm, setExpenseForm] = useState({
    category: 'Miscellaneous', description: '', notes: '', amount: 0,
    payment_method: 'Cash', reference_number: '', date: new Date().toISOString().slice(0, 10)
  });
  const [farmExpenseForm, setFarmExpenseForm] = useState({
    description: '', notes: '', amount: 0, customer_id: '',
    payment_method: 'Cash', reference_number: '', date: new Date().toISOString().slice(0, 10),
    due_date: '', terms: ''
  });
  const [cashOutForm, setCashOutForm] = useState({
    description: '', notes: '', amount: 0, customer_id: '',
    payment_method: 'Cash', reference_number: '', date: new Date().toISOString().slice(0, 10),
    due_date: '', terms: ''
  });
  const [payableForm, setPayableForm] = useState({ supplier: '', description: '', amount: 0, due_date: '' });
  // Typeahead state for Bill-to-Customer (Farm Expense) and Customer Cash Out —
  // replaces the <Select> dropdown that forced scrolling through 500+ customers.
  const [farmCustomerQuery, setFarmCustomerQuery] = useState('');
  const [farmCustomerHighlight, setFarmCustomerHighlight] = useState(0);
  const [farmCustomerListOpen, setFarmCustomerListOpen] = useState(false);
  const [cashOutCustomerQuery, setCashOutCustomerQuery] = useState('');
  const [cashOutCustomerHighlight, setCashOutCustomerHighlight] = useState(0);
  const [cashOutCustomerListOpen, setCashOutCustomerListOpen] = useState(false);
  const [expenseReceiptData, setExpenseReceiptData] = useState(null);
  
  // Filters
  const [filters, setFilters] = useState({
    category: '', payment_method: '', date_from: '', date_to: '', search: ''
  });
  const [showFilters, setShowFilters] = useState(false);

  const fetchAll = useCallback(async () => {
    try {
      const params = currentBranch ? { branch_id: currentBranch.id, limit: 100 } : { limit: 100 };
      if (filters.category) params.category = filters.category;
      if (filters.payment_method) params.payment_method = filters.payment_method;
      if (filters.date_from) params.date_from = filters.date_from;
      if (filters.date_to) params.date_to = filters.date_to;
      if (filters.search) params.search = filters.search;
      
      const [expRes, recRes, payRes, custRes, empRes] = await Promise.all([
        api.get('/expenses', { params }),
        api.get('/receivables', { params: { limit: 100 } }),
        api.get('/payables', { params: { limit: 100 } }),
        api.get('/customers', { params: { limit: 500 } }),
        api.get('/employees').catch(() => ({ data: [] })),
      ]);
      setExpenses(expRes.data.expenses || []);
      setReceivables(Array.isArray(recRes.data) ? recRes.data : (recRes.data.receivables || []));
      setPayables(Array.isArray(payRes.data) ? payRes.data : (payRes.data.payables || []));
      setCustomers(custRes.data.customers || []);
      setEmployees(empRes.data || []);
    } catch { toast.error('Failed to load accounting data'); }
  }, [currentBranch, filters]);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const openNewExpense = () => {
    setEditMode(false);
    setExpenseForm({
      category: 'Miscellaneous', description: '', notes: '', amount: 0,
      payment_method: 'Cash', reference_number: '', date: new Date().toISOString().slice(0, 10),
      employee_id: '', employee_name: '',
    });
    setCaSummary(null);
    setExpenseDialog(true);
  };

  const openEditExpense = (expense) => {
    setEditMode(true);
    setExpenseForm({
      id: expense.id,
      category: expense.category || 'Miscellaneous',
      description: expense.description || '',
      notes: expense.notes || '',
      amount: expense.amount || 0,
      payment_method: expense.payment_method || 'Cash',
      reference_number: expense.reference_number || '',
      date: expense.date || new Date().toISOString().slice(0, 10),
      employee_id: expense.employee_id || '',
      employee_name: expense.employee_name || '',
    });
    setCaSummary(null);
    setExpenseDialog(true);
  };

  const handleEmployeeSelect = async (empId) => {
    const emp = employees.find(e => e.id === empId);
    if (!emp) { setExpenseForm(f => ({...f, employee_id: '', employee_name: ''})); setCaSummary(null); return; }
    setExpenseForm(f => ({...f, employee_id: emp.id, employee_name: emp.name}));
    try {
      const res = await api.get(`/employees/${emp.id}/ca-summary`);
      setCaSummary(res.data);
    } catch { setCaSummary(null); }
  };

  const openFarmExpense = () => {
    setFarmExpenseForm({
      description: '', notes: '', amount: 0, customer_id: '',
      payment_method: 'Cash', reference_number: '', date: new Date().toISOString().slice(0, 10),
      due_date: '', terms: ''
    });
    setFarmExpenseDialog(true);
  };

  const openCashOut = () => {
    setCashOutForm({
      description: '', notes: '', amount: 0, customer_id: '',
      payment_method: 'Cash', reference_number: '', date: new Date().toISOString().slice(0, 10),
      due_date: '', terms: ''
    });
    setCashOutDialog(true);
  };

  const handleSaveExpense = async (approvedBy = '') => {
    // Defensive: when bound directly to onClick, React passes a SyntheticEvent
    // here. Only treat string as a real approver name; ignore anything else.
    const approver = (typeof approvedBy === 'string') ? approvedBy.trim() : '';
    if (!expenseForm.amount || expenseForm.amount <= 0) {
      toast.error('Amount must be greater than 0');
      return;
    }
    // Branch required for all expenses
    if (!currentBranch?.id) {
      toast.error('Please select a specific branch from the sidebar before recording expenses');
      return;
    }
    // Employee Advance CA limit check
    if (expenseForm.category === 'Employee Advance' && expenseForm.employee_id && caSummary && !editMode) {
      const limit = caSummary.monthly_ca_limit || 0;
      if (limit > 0 && (caSummary.this_month_total + parseFloat(expenseForm.amount)) > limit && !approver) {
        setCaManagerPinDialog(true);
        return;
      }
    }
    try {
      const payload = { ...expenseForm, branch_id: currentBranch?.id };
      if (approver) payload.manager_approved_by = approver;
      if (expenseReceiptData?.sessionId) {
        payload.upload_session_ids = [expenseReceiptData.sessionId];
      }
      if (editMode && expenseForm.id) {
        await api.put(`/expenses/${expenseForm.id}`, payload);
        toast.success('Expense updated');
      } else {
        await api.post('/expenses', payload);
        toast.success('Expense recorded');
      }
      setExpenseDialog(false);
      setCaSummary(null);
      setExpenseReceiptData(null);
      fetchAll();
    } catch (e) {
      const detail = e?.response?.data?.detail;
      // Friendly insufficient-funds toast (backend returns structured object)
      if (detail && typeof detail === 'object' && detail.type === 'insufficient_funds') {
        toast.error(detail.message || 'Insufficient cashier balance', {
          description: detail.suggestion === 'safe'
            ? 'Tip: change Payment Method to use the Safe, or top up the Cashier first.'
            : undefined,
        });
        return;
      }
      toast.error(getErrorMessage(e, 'Error saving expense'));
    }
  };

  const handleCaManagerPin = async () => {
    if (!caManagerPin) { toast.error('Enter manager PIN'); return; }
    try {
      const employeeName = caSummary?.employee_name || expenseForm.employee_id || 'employee';
      const limit = caSummary?.monthly_ca_limit || 0;
      const res = await api.post('/auth/verify-manager-pin', {
        pin: caManagerPin,
        action_key: 'reverse_employee_advance',
        context: {
          type: 'employee_advance',
          description: `₱${parseFloat(expenseForm.amount).toFixed(2)} cash advance for ${employeeName} (over ₱${limit.toFixed(2)} monthly limit)`,
          amount: parseFloat(expenseForm.amount),
          employee_name: employeeName,
          monthly_limit: limit,
          branch_id: currentBranch?.id,
          branch_name: currentBranch?.name,
        }
      });
      if (res.data.valid) {
        toast.success(`Approved by ${res.data.manager_name}`);
        setCaManagerPinDialog(false);
        setCaManagerPin('');
        await handleSaveExpense(res.data.manager_name);
      } else {
        toast.error('Invalid PIN');
      }
    } catch (e) { toast.error(getErrorMessage(e, 'Verification failed')); }
  };

  const handleCreateFarmExpense = async () => {
    if (!farmExpenseForm.customer_id) {
      toast.error('Please select a customer to bill');
      return;
    }
    if (!farmExpenseForm.amount || farmExpenseForm.amount <= 0) {
      toast.error('Amount must be greater than 0');
      return;
    }
    try {
      const res = await api.post('/expenses/farm', { ...farmExpenseForm, branch_id: currentBranch?.id });
      toast.success(res.data.message);
      setFarmExpenseDialog(false);
      fetchAll();
    } catch (e) { toast.error(getErrorMessage(e, 'Error creating farm expense')); }
  };

  const handleCreateCashOut = async () => {
    if (!cashOutForm.customer_id) {
      toast.error('Please select a customer');
      return;
    }
    if (!cashOutForm.amount || cashOutForm.amount <= 0) {
      toast.error('Amount must be greater than 0');
      return;
    }
    try {
      const res = await api.post('/expenses/customer-cashout', { ...cashOutForm, branch_id: currentBranch?.id });
      toast.success(res.data.message);
      setCashOutDialog(false);
      fetchAll();
    } catch (e) { toast.error(getErrorMessage(e, 'Error creating cash out')); }
  };

  const handleCreatePayable = async () => {
    try {
      await api.post('/payables', { ...payableForm, branch_id: currentBranch?.id });
      toast.success('Payable recorded');
      setPayableDialog(false);
      fetchAll();
    } catch (e) { toast.error(getErrorMessage(e, 'Error recording payable')); }
  };

  const openPayment = (item, type) => {
    setPaymentTarget(item);
    setPaymentType(type);
    setPaymentAmount(item.balance || 0);
    setPaymentDialog(true);
  };

  const handlePayment = async () => {
    try {
      const url = paymentType === 'receivable'
        ? `/receivables/${paymentTarget.id}/payment`
        : `/payables/${paymentTarget.id}/payment`;
      await api.post(url, { amount: paymentAmount });
      toast.success('Payment recorded');
      setPaymentDialog(false);
      fetchAll();
    } catch (e) { toast.error(getErrorMessage(e, 'Error recording payment')); }
  };

  const deleteExpense = async (id) => {
    // Route through ExpenseDetailModal for proper PIN verification
    setSelectedExpenseId(id);
    setExpenseModalOpen(true);
  };

  const clearFilters = () => {
    setFilters({ category: '', payment_method: '', date_from: '', date_to: '', search: '' });
  };

  const totalExpenses = expenses.reduce((s, e) => s + (e.amount || 0), 0);
  const totalReceivables = receivables.filter(r => r.status !== 'paid').reduce((s, r) => s + (r.balance || 0), 0);
  const totalPayables = payables.filter(p => p.status !== 'paid').reduce((s, p) => s + (p.balance || 0), 0);

  const paymentMethodBadge = (method) => {
    const colors = {
      'Cash': 'bg-emerald-100 text-emerald-700',
      'Check': 'bg-blue-100 text-blue-700',
      'Bank Transfer': 'bg-purple-100 text-purple-700',
      'GCash': 'bg-sky-100 text-sky-700',
      'Maya': 'bg-green-100 text-green-700',
    };
    return colors[method] || 'bg-slate-100 text-slate-700';
  };

  return (
    <div className="space-y-6 animate-fadeIn" data-testid="accounting-page">
      <div>
        <h1 className="text-2xl font-bold tracking-tight" style={{ fontFamily: 'Manrope' }}>Accounting</h1>
        <p className="text-sm text-slate-500 mt-1">Expenses, Receivables & Payables</p>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-3 gap-4">
        <Card className="border-slate-200">
          <CardContent className="p-4">
            <div className="flex items-center gap-2 mb-2">
              <ArrowDown size={16} className="text-red-500" />
              <span className="text-xs text-slate-500 uppercase font-medium">Total Expenses</span>
            </div>
            <p className="text-2xl font-bold text-red-600" style={{ fontFamily: 'Manrope' }}>{formatPHP(totalExpenses)}</p>
          </CardContent>
        </Card>
        <Card className="border-slate-200">
          <CardContent className="p-4">
            <div className="flex items-center gap-2 mb-2">
              <ArrowUp size={16} className="text-amber-500" />
              <span className="text-xs text-slate-500 uppercase font-medium">Receivables</span>
            </div>
            <p className="text-2xl font-bold text-amber-600" style={{ fontFamily: 'Manrope' }}>{formatPHP(totalReceivables)}</p>
          </CardContent>
        </Card>
        <Card className="border-slate-200">
          <CardContent className="p-4">
            <div className="flex items-center gap-2 mb-2">
              <DollarSign size={16} className="text-blue-500" />
              <span className="text-xs text-slate-500 uppercase font-medium">Payables</span>
            </div>
            <p className="text-2xl font-bold text-blue-600" style={{ fontFamily: 'Manrope' }}>{formatPHP(totalPayables)}</p>
          </CardContent>
        </Card>
      </div>

      <Tabs defaultValue="expenses">
        <TabsList>
          <TabsTrigger value="expenses" data-testid="tab-expenses">Expenses</TabsTrigger>
          <TabsTrigger value="receivables" data-testid="tab-receivables">Receivables</TabsTrigger>
          <TabsTrigger value="payables" data-testid="tab-payables">Payables</TabsTrigger>
        </TabsList>

        {/* EXPENSES TAB */}
        <TabsContent value="expenses" className="mt-4 space-y-4">
          {/* Action Buttons */}
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" onClick={() => setShowFilters(!showFilters)}>
                <Filter size={14} className="mr-1" /> Filters
              </Button>
              {(filters.category || filters.payment_method || filters.date_from || filters.date_to || filters.search) && (
                <Button variant="ghost" size="sm" onClick={clearFilters} className="text-slate-500">
                  Clear filters
                </Button>
              )}
            </div>
            <div className="flex gap-2">
              {canCreateExpense && (
                <Button onClick={openCashOut} variant="outline" className="border-blue-300 text-blue-700 hover:bg-blue-50" data-testid="cashout-btn">
                  <Banknote size={16} className="mr-2" /> Customer Cash Out
                </Button>
              )}
              {canCreateExpense && (
                <Button onClick={openFarmExpense} variant="outline" className="border-amber-300 text-amber-700 hover:bg-amber-50" data-testid="farm-expense-btn">
                  <Tractor size={16} className="mr-2" /> Farm Expense
                </Button>
              )}
              {canCreateExpense && (
                <Button onClick={openNewExpense} className="bg-[#1A4D2E] hover:bg-[#14532d] text-white" data-testid="create-expense-btn">
                  <Plus size={16} className="mr-2" /> Record Expense
                </Button>
              )}
            </div>
          </div>

          {/* Filters Panel */}
          {showFilters && (
            <Card className="border-slate-200 bg-slate-50">
              <CardContent className="p-4">
                <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                  <div>
                    <Label className="text-xs">Category</Label>
                    <Select value={filters.category || "__all__"} onValueChange={v => setFilters(f => ({ ...f, category: v === "__all__" ? "" : v }))}>
                      <SelectTrigger className="h-9"><SelectValue placeholder="All categories" /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="__all__">All categories</SelectItem>
                        {EXPENSE_CATEGORIES.map(c => <SelectItem key={c} value={c}>{c}</SelectItem>)}
                      </SelectContent>
                    </Select>
                  </div>
                  <div>
                    <Label className="text-xs">Payment Method</Label>
                    <Select value={filters.payment_method || "__all__"} onValueChange={v => setFilters(f => ({ ...f, payment_method: v === "__all__" ? "" : v }))}>
                      <SelectTrigger className="h-9"><SelectValue placeholder="All methods" /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="__all__">All methods</SelectItem>
                        {PAYMENT_METHODS.map(m => <SelectItem key={m} value={m}>{m}</SelectItem>)}
                      </SelectContent>
                    </Select>
                  </div>
                  <div>
                    <Label className="text-xs">From Date</Label>
                    <Input type="date" className="h-9" value={filters.date_from} onChange={e => setFilters(f => ({ ...f, date_from: e.target.value }))} />
                  </div>
                  <div>
                    <Label className="text-xs">To Date</Label>
                    <Input type="date" className="h-9" value={filters.date_to} onChange={e => setFilters(f => ({ ...f, date_to: e.target.value }))} />
                  </div>
                  <div>
                    <Label className="text-xs">Search</Label>
                    <div className="relative">
                      <Search size={14} className="absolute left-2 top-1/2 -translate-y-1/2 text-slate-400" />
                      <Input
                        className="h-9 pl-8"
                        placeholder="Description, ref..."
                        value={filters.search}
                        onChange={e => setFilters(f => ({ ...f, search: e.target.value }))}
                      />
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>
          )}

          {/* Expenses Table */}
          <Card className="border-slate-200">
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow className="bg-slate-50">
                    <TableHead className="text-xs uppercase tracking-wider text-slate-500">Date</TableHead>
                    <TableHead className="text-xs uppercase tracking-wider text-slate-500">Category</TableHead>
                    <TableHead className="text-xs uppercase tracking-wider text-slate-500">Description</TableHead>
                    <TableHead className="text-xs uppercase tracking-wider text-slate-500">Payment</TableHead>
                    <TableHead className="text-xs uppercase tracking-wider text-slate-500">Ref #</TableHead>
                    <TableHead className="text-xs uppercase tracking-wider text-slate-500 text-right">Amount</TableHead>
                    <TableHead className="text-xs uppercase tracking-wider text-slate-500">By</TableHead>
                    <TableHead className="w-20"></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {expenses.map(e => (
                    <TableRow key={e.id} className="table-row-hover">
                      <TableCell className="text-sm">{e.date}</TableCell>
                      <TableCell>
                        <Badge variant="outline" className={`text-[10px] ${
                          e.category === 'Farm Expense' ? 'bg-amber-50 text-amber-700 border-amber-200' : 
                          e.category === 'Customer Cash Out' ? 'bg-blue-50 text-blue-700 border-blue-200' :
                          e.category === 'Employee Advance' ? 'bg-violet-50 text-violet-700 border-violet-200' : ''
                        }`}>
                          {e.category === 'Farm Expense' && <Tractor size={10} className="mr-1" />}
                          {e.category === 'Customer Cash Out' && <Banknote size={10} className="mr-1" />}
                          {e.category}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-sm">
                        <div><button className="text-blue-600 hover:underline" onClick={() => openDetailModal(null, e.id)}>{e.description || e.category}</button></div>
                        {e.notes && <div className="text-xs text-slate-400">{e.notes}</div>}
                        {e.employee_name && (
                          <div className="text-xs text-violet-600">
                            Employee: {e.employee_name}
                          </div>
                        )}
                        {e.customer_name && (
                          <div className={`text-xs ${e.category === 'Customer Cash Out' ? 'text-blue-600' : 'text-amber-600'}`}>
                            {e.category === 'Customer Cash Out' ? 'Loaned to: ' : 'Billed to: '}
                            {e.customer_id ? <button onClick={() => openCustStmt(e.customer_id, e.customer_name)} className="hover:underline font-medium">{e.customer_name}</button> : e.customer_name}
                          </div>
                        )}
                        {e.linked_invoice_number && (
                          <div className="text-xs text-blue-600 flex items-center gap-1">
                            <FileText size={10} /> Invoice: <button className="hover:underline" onClick={() => openDetailModal(e.linked_invoice_number)}>{e.linked_invoice_number}</button>
                          </div>
                        )}
                      </TableCell>
                      <TableCell>
                        <Badge className={`text-[10px] ${paymentMethodBadge(e.payment_method)}`}>{e.payment_method || 'Cash'}</Badge>
                      </TableCell>
                      <TableCell className="text-xs text-slate-500 font-mono">{e.reference_number || '-'}</TableCell>
                      <TableCell className="text-right font-semibold text-red-600">{formatPHP(e.amount)}</TableCell>
                      <TableCell className="text-xs text-slate-500">{e.created_by_name}</TableCell>
                      <TableCell>
                        <div className="flex gap-1">
                          {canEditExpense && (
                            <Button variant="ghost" size="sm" onClick={() => openEditExpense(e)} className="text-slate-500 h-7 px-2" data-testid={`edit-expense-${e.id}`}>
                              <Edit2 size={12} />
                            </Button>
                          )}
                          {canEditExpense && (
                            <Button variant="ghost" size="sm" onClick={() => { setUploadExpenseId(e.id); setUploadQROpen(true); }}
                              className="text-blue-500 h-7 px-2" title="Upload Receipt" data-testid={`upload-expense-${e.id}`}>
                              <Upload size={12} />
                            </Button>
                          )}
                          <Button variant="ghost" size="sm" onClick={() => { setViewQRExpenseId(e.id); setViewQRExpenseOpen(true); }}
                            className="text-slate-500 h-7 px-2" title="View on Phone">
                            <span className="text-xs">📱</span>
                          </Button>
                          {!e.verified && canEditExpense && (
                            <Button variant="ghost" size="sm" onClick={() => { setVerifyExpenseId(e.id); setVerifyExpenseLabel(e.description); setVerifyExpenseOpen(true); }}
                              className="text-[#1A4D2E] h-7 px-2" title="Verify" data-testid={`verify-expense-${e.id}`}>
                              <Shield size={12} />
                            </Button>
                          )}
                          <VerificationBadge doc={e} compact />
                          {canEditExpense && (
                            <Button variant="ghost" size="sm" onClick={() => deleteExpense(e.id)} className="text-red-500 h-7 px-2" data-testid={`delete-expense-${e.id}`}>Del</Button>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                  {!expenses.length && (
                    <TableRow>
                      <TableCell colSpan={8} className="text-center py-8 text-slate-400">
                        No expenses found
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </TabsContent>

        {/* RECEIVABLES TAB */}
        <TabsContent value="receivables" className="mt-4">
          <Card className="border-slate-200">
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow className="bg-slate-50">
                    <TableHead className="text-xs uppercase tracking-wider text-slate-500">Invoice #</TableHead>
                    <TableHead className="text-xs uppercase tracking-wider text-slate-500">Customer</TableHead>
                    <TableHead className="text-xs uppercase tracking-wider text-slate-500">Type</TableHead>
                    <TableHead className="text-xs uppercase tracking-wider text-slate-500">Description</TableHead>
                    <TableHead className="text-xs uppercase tracking-wider text-slate-500 text-right">Amount</TableHead>
                    <TableHead className="text-xs uppercase tracking-wider text-slate-500 text-right">Paid</TableHead>
                    <TableHead className="text-xs uppercase tracking-wider text-slate-500 text-right">Balance</TableHead>
                    <TableHead className="text-xs uppercase tracking-wider text-slate-500">Status</TableHead>
                    <TableHead className="w-32"></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {receivables.map(r => {
                    const typeConfig = {
                      farm_expense: { label: 'Farm', cls: 'bg-green-100 text-green-700' },
                      cash_advance: { label: 'Customer Cash Out', cls: 'bg-purple-100 text-purple-700' },
                      interest_charge: { label: 'Interest', cls: 'bg-amber-100 text-amber-700' },
                      penalty_charge: { label: 'Penalty', cls: 'bg-red-100 text-red-700' },
                      walk_in: { label: 'Sale', cls: 'bg-blue-100 text-blue-700' },
                    }[r.sale_type] || { label: 'Invoice', cls: 'bg-slate-100 text-slate-700' };
                    return (
                      <TableRow key={r.id} className="table-row-hover">
                        <TableCell><button className="font-mono text-xs text-blue-600 hover:underline" onClick={() => openDetailModal(r.invoice_number)}>{r.invoice_number || '—'}</button></TableCell>
                        <TableCell className="font-medium">
                          <div className="flex items-center gap-1.5">
                            {r.customer_id ? (
                              <button onClick={() => openCustStmt(r.customer_id, r.customer_name)} className="hover:text-[#1A4D2E] hover:underline">{r.customer_name}</button>
                            ) : (
                              <span>{r.customer_name}</span>
                            )}
                            <CustomerBalanceBadge customerId={r.customer_id} byId={balanceById} />
                          </div>
                        </TableCell>
                        <TableCell><Badge className={`text-[9px] ${typeConfig.cls}`}>{typeConfig.label}</Badge></TableCell>
                        <TableCell className="text-sm text-slate-600">{r.description || '-'}</TableCell>
                        <TableCell className="text-right">{formatPHP(r.amount)}</TableCell>
                        <TableCell className="text-right text-emerald-600">{formatPHP(r.paid)}</TableCell>
                        <TableCell className="text-right font-semibold text-amber-600">{formatPHP(r.balance)}</TableCell>
                        <TableCell>
                          <Badge className={`text-[10px] ${r.status === 'paid' ? 'bg-emerald-100 text-emerald-700' : r.status === 'partial' ? 'bg-amber-100 text-amber-700' : 'bg-slate-100 text-slate-700'}`}>
                            {r.status}
                          </Badge>
                        </TableCell>
                        <TableCell>
                          {r.status !== 'paid' && canReceivePayment && (
                            <Button size="sm" variant="outline" data-testid={`receivable-pay-${r.id}`} onClick={() => openPayment(r, 'receivable')}>
                              Record Payment
                            </Button>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                  {!receivables.length && (
                    <TableRow>
                      <TableCell colSpan={9} className="text-center py-8 text-slate-400">No outstanding receivables</TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </TabsContent>

        {/* PAYABLES TAB */}
        <TabsContent value="payables" className="mt-4 space-y-4">
          {canCreateExpense && (
            <div className="flex justify-end">
              <Button onClick={() => { setPayableForm({ supplier: '', description: '', amount: 0, due_date: '' }); setPayableDialog(true); }} className="bg-[#1A4D2E] hover:bg-[#14532d] text-white" data-testid="create-payable-btn">
                <Plus size={16} className="mr-2" /> Record Payable
              </Button>
            </div>
          )}
          <Card className="border-slate-200">
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow className="bg-slate-50">
                    <TableHead className="text-xs uppercase tracking-wider text-slate-500">Supplier</TableHead>
                    <TableHead className="text-xs uppercase tracking-wider text-slate-500">Description</TableHead>
                    <TableHead className="text-xs uppercase tracking-wider text-slate-500 text-right">Amount</TableHead>
                    <TableHead className="text-xs uppercase tracking-wider text-slate-500 text-right">Paid</TableHead>
                    <TableHead className="text-xs uppercase tracking-wider text-slate-500 text-right">Balance</TableHead>
                    <TableHead className="text-xs uppercase tracking-wider text-slate-500">Status</TableHead>
                    <TableHead className="w-28"></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {payables.map(p => (
                    <TableRow key={p.id} className="table-row-hover">
                      <TableCell className="font-medium">{p.supplier}</TableCell>
                      <TableCell className="text-sm text-slate-600">{p.description || '-'}</TableCell>
                      <TableCell className="text-right">{formatPHP(p.amount)}</TableCell>
                      <TableCell className="text-right text-emerald-600">{formatPHP(p.paid)}</TableCell>
                      <TableCell className="text-right font-semibold text-blue-600">{formatPHP(p.balance)}</TableCell>
                      <TableCell>
                        <Badge className={`text-[10px] ${p.status === 'paid' ? 'bg-emerald-100 text-emerald-700' : p.status === 'partial' ? 'bg-amber-100 text-amber-700' : 'bg-slate-100 text-slate-700'}`}>
                          {p.status}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {p.status !== 'paid' && canCreateExpense && (
                          <Button size="sm" variant="outline" data-testid={`payable-pay-${p.id}`} onClick={() => openPayment(p, 'payable')}>
                            Record Payment
                          </Button>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                  {!payables.length && (
                    <TableRow>
                      <TableCell colSpan={7} className="text-center py-8 text-slate-400">No payables</TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {/* REGULAR EXPENSE DIALOG */}
      <Dialog open={expenseDialog} onOpenChange={setExpenseDialog}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle style={{ fontFamily: 'Manrope' }}>{editMode ? 'Edit Expense' : 'Record Expense'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label className="text-xs text-slate-500">Category</Label>
                <Select value={expenseForm.category} onValueChange={v => setExpenseForm({ ...expenseForm, category: v })}>
                  <SelectTrigger className="h-10" data-testid="expense-category">
                    <SelectValue placeholder="Select category" />
                  </SelectTrigger>
                  <SelectContent>
                    {EXPENSE_CATEGORIES.filter(c => c !== 'Farm Expense' && c !== 'Customer Cash Out').map(c => (
                      <SelectItem key={c} value={c}>{c}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label className="text-xs text-slate-500">Date</Label>
                <Input type="date" className="h-10" value={expenseForm.date} onChange={e => setExpenseForm({ ...expenseForm, date: e.target.value })} />
              </div>
            </div>
            <div>
              <Label className="text-xs text-slate-500">Description</Label>
              <Input data-testid="expense-description" className="h-10" value={expenseForm.description} onChange={e => setExpenseForm({ ...expenseForm, description: e.target.value })} placeholder="What is this expense for?" />
            </div>
            {/* Employee CA section — shown when category is Employee Advance */}
            {expenseForm.category === 'Employee Advance' && !editMode && (
              <div className="space-y-3 bg-amber-50 border border-amber-200 rounded-lg p-3">
                <div>
                  <Label className="text-xs text-amber-700 font-medium">Employee *</Label>
                  <Select value={expenseForm.employee_id || 'none'} onValueChange={v => handleEmployeeSelect(v === 'none' ? '' : v)}>
                    <SelectTrigger className="h-9 bg-white" data-testid="ca-employee-select"><SelectValue placeholder="Select employee..." /></SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none">— Select employee —</SelectItem>
                      {employees.filter(e => e.active !== false).map(e => (
                        <SelectItem key={e.id} value={e.id}>{e.name} {e.position ? `(${e.position})` : ''}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                {caSummary && (
                  <div className="space-y-2">
                    <div className="grid grid-cols-3 gap-2 text-center">
                      <div className="bg-white rounded p-2">
                        <p className="text-[10px] text-slate-400">This Month</p>
                        <p className={`text-sm font-bold ${caSummary.is_over_limit ? 'text-red-600' : 'text-amber-600'}`}>{formatPHP(caSummary.this_month_total)}</p>
                      </div>
                      <div className="bg-white rounded p-2">
                        <p className="text-[10px] text-slate-400">Monthly Limit</p>
                        <p className="text-sm font-bold">{caSummary.monthly_ca_limit > 0 ? formatPHP(caSummary.monthly_ca_limit) : 'None'}</p>
                      </div>
                      <div className="bg-white rounded p-2">
                        <p className="text-[10px] text-slate-400">Unpaid Balance</p>
                        <p className="text-sm font-bold text-slate-700">{formatPHP(caSummary.total_advance_balance)}</p>
                      </div>
                    </div>
                    {caSummary.is_over_limit && (
                      <div className="flex items-center gap-2 text-xs text-red-700 bg-red-50 rounded p-2">
                        <AlertTriangle size={13} /> Monthly limit reached — manager PIN required to proceed
                      </div>
                    )}
                    {caSummary.prev_month_overage > 0 && (
                      <div className="text-xs text-amber-700 bg-amber-100 rounded p-2">
                        Previous month overage: {formatPHP(caSummary.prev_month_overage)} — deduct from next salary
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
            <div>
              <Label className="text-xs text-slate-500">Notes (optional)</Label>
              <Input className="h-10" value={expenseForm.notes} onChange={e => setExpenseForm({ ...expenseForm, notes: e.target.value })} placeholder="Additional details..." />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label className="text-xs text-slate-500">Amount (₱)</Label>
                <CalcInput data-testid="expense-amount" className="h-10" value={expenseForm.amount} onChange={(v) => setExpenseForm({ ...expenseForm, amount: parseFloat(v) || 0 })} />
              </div>
              <div>
                <Label className="text-xs text-slate-500">Payment Method</Label>
                <Select value={expenseForm.payment_method} onValueChange={v => setExpenseForm({ ...expenseForm, payment_method: v })}>
                  <SelectTrigger className="h-10"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {PAYMENT_METHODS.map(m => <SelectItem key={m} value={m}>{m}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div>
              <Label className="text-xs text-slate-500">Reference # (Check/Receipt/OR)</Label>
              <Input className="h-10" value={expenseForm.reference_number} onChange={e => setExpenseForm({ ...expenseForm, reference_number: e.target.value })} placeholder="Optional reference number" />
            </div>
            {/* Receipt upload — optional for expense */}
            {!editMode && (
              <ReceiptUploadInline
                required={false}
                label="Receipt Photo (Optional)"
                recordType="expense"
                compact={true}
                recordSummary={{
                  type_label: 'Expense',
                  title: expenseForm.category || 'Expense',
                  description: expenseForm.description || '',
                  amount: expenseForm.amount || 0,
                  date: expenseForm.date,
                }}
                onUploaded={(data) => setExpenseReceiptData(data)}
              />
            )}
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" onClick={() => setExpenseDialog(false)}>Cancel</Button>
              <Button onClick={() => handleSaveExpense()} className="bg-[#1A4D2E] hover:bg-[#14532d] text-white" data-testid="save-expense-btn">
                {editMode ? 'Update Expense' : 'Save Expense'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* CA LIMIT MANAGER PIN DIALOG */}
      <Dialog open={caManagerPinDialog} onOpenChange={setCaManagerPinDialog}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
              <Shield size={18} className="text-amber-500" /> Manager Approval Required
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="bg-red-50 border border-red-200 rounded-lg p-3">
              <p className="text-sm font-medium text-red-700">{expenseForm.employee_name} has exceeded their monthly CA limit</p>
              <p className="text-xs text-red-600 mt-1">This month: {formatPHP(caSummary?.this_month_total || 0)} / Limit: {formatPHP(caSummary?.monthly_ca_limit || 0)}</p>
              <p className="text-xs text-red-500 mt-1">Additional: {formatPHP(parseFloat(expenseForm.amount || 0))}</p>
            </div>
            <div>
              <Label>Manager PIN</Label>
              <Input type="password" autoComplete="new-password" value={caManagerPin} onChange={e => setCaManagerPin(e.target.value)}
                placeholder="Enter 4-digit PIN" className="text-center text-2xl tracking-widest h-14" maxLength={6} />
            </div>
            <div className="flex gap-2">
              <Button variant="outline" className="flex-1" onClick={() => { setCaManagerPinDialog(false); setCaManagerPin(''); }}>Cancel</Button>
              <Button className="flex-1 bg-amber-500 hover:bg-amber-600 text-white" onClick={handleCaManagerPin}>Approve & Save</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* FARM EXPENSE DIALOG */}
      <Dialog open={farmExpenseDialog} onOpenChange={setFarmExpenseDialog}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle style={{ fontFamily: 'Manrope' }} className="flex items-center gap-2">
              <Tractor size={20} className="text-amber-600" />
              Farm Expense
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-sm text-amber-800">
              This will record a farm expense and automatically create an invoice for the selected customer.
            </div>

            {/* Bill to Customer — moved to top per user feedback. Replaces
                dropdown with typeahead (500+ customers can't be browsed). */}
            <div>
              <Label className="text-xs text-slate-500 font-semibold">Bill to Customer *</Label>
              <p className="text-xs text-slate-400 mb-2">Search by name — an invoice will be auto-created for the selected customer</p>
              {(() => {
                const selectedCustomer = customers.find(c => c.id === farmExpenseForm.customer_id);
                if (selectedCustomer) {
                  return (
                    <div className="flex items-center justify-between h-10 px-3 rounded-md border-2 border-amber-400 bg-amber-50/50"
                      data-testid="farm-customer-selected">
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="text-sm font-semibold text-slate-800 truncate">{selectedCustomer.name}</span>
                        {selectedCustomer.balance > 0 && (
                          <span className="text-[10px] font-mono text-red-600 bg-red-50 px-1.5 py-0.5 rounded">
                            AR: {formatPHP(selectedCustomer.balance)}
                          </span>
                        )}
                      </div>
                      <button type="button"
                        onClick={() => {
                          setFarmExpenseForm({ ...farmExpenseForm, customer_id: '' });
                          setFarmCustomerQuery('');
                          setFarmCustomerListOpen(false);
                        }}
                        className="text-[11px] text-slate-500 hover:text-red-600 underline"
                        data-testid="farm-customer-clear">
                        Change
                      </button>
                    </div>
                  );
                }
                const q = farmCustomerQuery.trim().toLowerCase();
                const matches = q
                  ? customers.filter(c => (c.name || '').toLowerCase().includes(q)).slice(0, 8)
                  : [];
                const pick = (c) => {
                  setFarmExpenseForm({ ...farmExpenseForm, customer_id: c.id });
                  setFarmCustomerQuery('');
                  setFarmCustomerListOpen(false);
                };
                return (
                  <div className="relative">
                    <Input
                      className="h-10"
                      placeholder="Type customer name..."
                      value={farmCustomerQuery}
                      onChange={e => {
                        setFarmCustomerQuery(e.target.value);
                        setFarmCustomerHighlight(0);
                        setFarmCustomerListOpen(true);
                      }}
                      onFocus={() => setFarmCustomerListOpen(true)}
                      onKeyDown={(e) => {
                        if (matches.length === 0) return;
                        if (e.key === 'ArrowDown') {
                          e.preventDefault();
                          setFarmCustomerHighlight(h => Math.min(h + 1, matches.length - 1));
                        } else if (e.key === 'ArrowUp') {
                          e.preventDefault();
                          setFarmCustomerHighlight(h => Math.max(h - 1, 0));
                        } else if (e.key === 'Enter') {
                          e.preventDefault();
                          pick(matches[farmCustomerHighlight]);
                        } else if (e.key === 'Escape') {
                          setFarmCustomerListOpen(false);
                        }
                      }}
                      data-testid="farm-customer-search" />
                    {farmCustomerListOpen && matches.length > 0 && (
                      <div className="absolute z-30 left-0 right-0 mt-1 bg-white rounded-lg shadow-lg border border-slate-200 max-h-60 overflow-y-auto"
                        data-testid="farm-customer-results">
                        {matches.map((c, i) => (
                          <button type="button" key={c.id}
                            onClick={() => pick(c)}
                            onMouseEnter={() => setFarmCustomerHighlight(i)}
                            className={`w-full text-left px-3 py-2 text-sm border-b border-slate-50 last:border-0 ${
                              i === farmCustomerHighlight ? 'bg-amber-50' : 'hover:bg-slate-50'
                            }`}
                            data-testid={`farm-customer-result-${i}`}>
                            <div className="font-medium text-slate-800">{c.name}</div>
                            {c.balance > 0 && (
                              <div className="text-[10px] font-mono text-red-600">AR: {formatPHP(c.balance)}</div>
                            )}
                          </button>
                        ))}
                      </div>
                    )}
                    {farmCustomerListOpen && q && matches.length === 0 && (
                      <div className="absolute z-30 left-0 right-0 mt-1 bg-white rounded-lg shadow-lg border border-slate-200 px-3 py-3 text-xs text-slate-400">
                        No customer found for "{farmCustomerQuery}"
                      </div>
                    )}
                  </div>
                );
              })()}
            </div>

            <div>
              <Label className="text-xs text-slate-500">Service Description *</Label>
              <Input
                className="h-10"
                value={farmExpenseForm.description}
                onChange={e => setFarmExpenseForm({ ...farmExpenseForm, description: e.target.value })}
                placeholder="e.g. Tilling, Plowing, Harvesting"
                data-testid="farm-expense-description"
              />
            </div>
            
            <div>
              <Label className="text-xs text-slate-500">Notes (Tilling, Labor, Gas, etc.)</Label>
              <Input
                className="h-10"
                value={farmExpenseForm.notes}
                onChange={e => setFarmExpenseForm({ ...farmExpenseForm, notes: e.target.value })}
                placeholder="e.g. 2 hours tilling + 5L gas + 2 workers"
                data-testid="farm-expense-notes"
              />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label className="text-xs text-slate-500">Amount (₱) *</Label>
                <CalcInput className="h-10"
 value={farmExpenseForm.amount}
 onChange={(v) => setFarmExpenseForm({ ...farmExpenseForm, amount: parseFloat(v) || 0 })}
 data-testid="farm-expense-amount" />
              </div>
              <div>
                <Label className="text-xs text-slate-500">Date</Label>
                <Input
                  type="date"
                  className="h-10"
                  value={farmExpenseForm.date}
                  onChange={e => setFarmExpenseForm({ ...farmExpenseForm, date: e.target.value })}
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label className="text-xs text-slate-500">Payment Method</Label>
                <Select value={farmExpenseForm.payment_method} onValueChange={v => setFarmExpenseForm({ ...farmExpenseForm, payment_method: v })}>
                  <SelectTrigger className="h-10"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {PAYMENT_METHODS.map(m => <SelectItem key={m} value={m}>{m}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label className="text-xs text-slate-500">Reference #</Label>
                <Input
                  className="h-10"
                  value={farmExpenseForm.reference_number}
                  onChange={e => setFarmExpenseForm({ ...farmExpenseForm, reference_number: e.target.value })}
                  placeholder="Optional"
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label className="text-xs text-slate-500">Invoice Due Date</Label>
                <Input
                  type="date"
                  className="h-10"
                  value={farmExpenseForm.due_date}
                  onChange={e => setFarmExpenseForm({ ...farmExpenseForm, due_date: e.target.value })}
                />
              </div>
              <div>
                <Label className="text-xs text-slate-500">Payment Terms</Label>
                <Input
                  className="h-10"
                  value={farmExpenseForm.terms}
                  onChange={e => setFarmExpenseForm({ ...farmExpenseForm, terms: e.target.value })}
                  placeholder="e.g. Net 30"
                />
              </div>
            </div>

            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" onClick={() => setFarmExpenseDialog(false)}>Cancel</Button>
              <Button
                onClick={handleCreateFarmExpense}
                className="bg-amber-600 hover:bg-amber-700 text-white"
                data-testid="save-farm-expense-btn"
              >
                <Tractor size={14} className="mr-2" />
                Create Expense & Invoice
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* CUSTOMER CASH OUT DIALOG */}
      <Dialog open={cashOutDialog} onOpenChange={setCashOutDialog}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle style={{ fontFamily: 'Manrope' }} className="flex items-center gap-2">
              <Banknote size={20} className="text-blue-600" />
              Customer Cash Out
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-sm text-blue-800">
              This will release cash to a customer and automatically create an invoice for the amount owed.
            </div>
            
            <div>
              <Label className="text-xs text-slate-500 font-semibold">Select Customer *</Label>
              <p className="text-xs text-slate-400 mb-2">Search by name — an invoice will be auto-created for the selected customer</p>
              {(() => {
                const selectedCustomer = customers.find(c => c.id === cashOutForm.customer_id);
                if (selectedCustomer) {
                  return (
                    <div className="flex items-center justify-between h-10 px-3 rounded-md border-2 border-blue-400 bg-blue-50/50"
                      data-testid="cashout-customer-selected">
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="text-sm font-semibold text-slate-800 truncate">{selectedCustomer.name}</span>
                        {selectedCustomer.balance > 0 && (
                          <span className="text-[10px] font-mono text-red-600 bg-red-50 px-1.5 py-0.5 rounded">
                            AR: {formatPHP(selectedCustomer.balance)}
                          </span>
                        )}
                      </div>
                      <button type="button"
                        onClick={() => {
                          setCashOutForm({ ...cashOutForm, customer_id: '' });
                          setCashOutCustomerQuery('');
                          setCashOutCustomerListOpen(false);
                        }}
                        className="text-[11px] text-slate-500 hover:text-red-600 underline"
                        data-testid="cashout-customer-clear">
                        Change
                      </button>
                    </div>
                  );
                }
                const q = cashOutCustomerQuery.trim().toLowerCase();
                const matches = q
                  ? customers.filter(c => (c.name || '').toLowerCase().includes(q)).slice(0, 8)
                  : [];
                const pick = (c) => {
                  setCashOutForm({ ...cashOutForm, customer_id: c.id });
                  setCashOutCustomerQuery('');
                  setCashOutCustomerListOpen(false);
                };
                return (
                  <div className="relative">
                    <Input
                      className="h-10"
                      placeholder="Type customer name..."
                      value={cashOutCustomerQuery}
                      onChange={e => {
                        setCashOutCustomerQuery(e.target.value);
                        setCashOutCustomerHighlight(0);
                        setCashOutCustomerListOpen(true);
                      }}
                      onFocus={() => setCashOutCustomerListOpen(true)}
                      onKeyDown={(e) => {
                        if (matches.length === 0) return;
                        if (e.key === 'ArrowDown') {
                          e.preventDefault();
                          setCashOutCustomerHighlight(h => Math.min(h + 1, matches.length - 1));
                        } else if (e.key === 'ArrowUp') {
                          e.preventDefault();
                          setCashOutCustomerHighlight(h => Math.max(h - 1, 0));
                        } else if (e.key === 'Enter') {
                          e.preventDefault();
                          pick(matches[cashOutCustomerHighlight]);
                        } else if (e.key === 'Escape') {
                          setCashOutCustomerListOpen(false);
                        }
                      }}
                      data-testid="cashout-customer-search" />
                    {cashOutCustomerListOpen && matches.length > 0 && (
                      <div className="absolute z-30 left-0 right-0 mt-1 bg-white rounded-lg shadow-lg border border-slate-200 max-h-60 overflow-y-auto"
                        data-testid="cashout-customer-results">
                        {matches.map((c, i) => (
                          <button type="button" key={c.id}
                            onClick={() => pick(c)}
                            onMouseEnter={() => setCashOutCustomerHighlight(i)}
                            className={`w-full text-left px-3 py-2 text-sm border-b border-slate-50 last:border-0 ${
                              i === cashOutCustomerHighlight ? 'bg-blue-50' : 'hover:bg-slate-50'
                            }`}
                            data-testid={`cashout-customer-result-${i}`}>
                            <div className="font-medium text-slate-800">{c.name}</div>
                            {c.balance > 0 && (
                              <div className="text-[10px] font-mono text-red-600">AR: {formatPHP(c.balance)}</div>
                            )}
                          </button>
                        ))}
                      </div>
                    )}
                    {cashOutCustomerListOpen && q && matches.length === 0 && (
                      <div className="absolute z-30 left-0 right-0 mt-1 bg-white rounded-lg shadow-lg border border-slate-200 px-3 py-3 text-xs text-slate-400">
                        No customer found for "{cashOutCustomerQuery}"
                      </div>
                    )}
                  </div>
                );
              })()}
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label className="text-xs text-slate-500">Amount (₱) *</Label>
                <CalcInput className="h-10"
 value={cashOutForm.amount}
 onChange={(v) => setCashOutForm({ ...cashOutForm, amount: parseFloat(v) || 0 })}
 data-testid="cashout-amount" />
              </div>
              <div>
                <Label className="text-xs text-slate-500">Date</Label>
                <Input
                  type="date"
                  className="h-10"
                  value={cashOutForm.date}
                  onChange={e => setCashOutForm({ ...cashOutForm, date: e.target.value })}
                />
              </div>
            </div>
            
            <div>
              <Label className="text-xs text-slate-500">Purpose / Description</Label>
              <Input
                className="h-10"
                value={cashOutForm.description}
                onChange={e => setCashOutForm({ ...cashOutForm, description: e.target.value })}
                placeholder="e.g. Cash Advance, Loan, etc."
                data-testid="cashout-description"
              />
            </div>
            
            <div>
              <Label className="text-xs text-slate-500">Notes (optional)</Label>
              <Input
                className="h-10"
                value={cashOutForm.notes}
                onChange={e => setCashOutForm({ ...cashOutForm, notes: e.target.value })}
                placeholder="Additional details..."
                data-testid="cashout-notes"
              />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label className="text-xs text-slate-500">Payment Method</Label>
                <Select value={cashOutForm.payment_method} onValueChange={v => setCashOutForm({ ...cashOutForm, payment_method: v })}>
                  <SelectTrigger className="h-10"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {PAYMENT_METHODS.map(m => <SelectItem key={m} value={m}>{m}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label className="text-xs text-slate-500">Reference #</Label>
                <Input
                  className="h-10"
                  value={cashOutForm.reference_number}
                  onChange={e => setCashOutForm({ ...cashOutForm, reference_number: e.target.value })}
                  placeholder="Optional"
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label className="text-xs text-slate-500">Invoice Due Date</Label>
                <Input
                  type="date"
                  className="h-10"
                  value={cashOutForm.due_date}
                  onChange={e => setCashOutForm({ ...cashOutForm, due_date: e.target.value })}
                />
              </div>
              <div>
                <Label className="text-xs text-slate-500">Payment Terms</Label>
                <Input
                  className="h-10"
                  value={cashOutForm.terms}
                  onChange={e => setCashOutForm({ ...cashOutForm, terms: e.target.value })}
                  placeholder="e.g. Net 30"
                />
              </div>
            </div>

            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" onClick={() => setCashOutDialog(false)}>Cancel</Button>
              <Button
                onClick={handleCreateCashOut}
                className="bg-blue-600 hover:bg-blue-700 text-white"
                data-testid="save-cashout-btn"
              >
                <Banknote size={14} className="mr-2" />
                Release Cash & Create Invoice
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* PAYABLE DIALOG */}
      <Dialog open={payableDialog} onOpenChange={setPayableDialog}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle style={{ fontFamily: 'Manrope' }}>Record Payable</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div>
              <Label className="text-xs text-slate-500">Supplier</Label>
              <Input data-testid="payable-supplier" className="h-10" value={payableForm.supplier} onChange={e => setPayableForm({ ...payableForm, supplier: e.target.value })} />
            </div>
            <div>
              <Label className="text-xs text-slate-500">Description</Label>
              <Input className="h-10" value={payableForm.description} onChange={e => setPayableForm({ ...payableForm, description: e.target.value })} />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label className="text-xs text-slate-500">Amount (₱)</Label>
                <CalcInput data-testid="payable-amount" className="h-10" value={payableForm.amount} onChange={(v) => setPayableForm({ ...payableForm, amount: parseFloat(v) || 0 })} />
              </div>
              <div>
                <Label className="text-xs text-slate-500">Due Date</Label>
                <Input type="date" className="h-10" value={payableForm.due_date} onChange={e => setPayableForm({ ...payableForm, due_date: e.target.value })} />
              </div>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" onClick={() => setPayableDialog(false)}>Cancel</Button>
              <Button onClick={handleCreatePayable} className="bg-[#1A4D2E] hover:bg-[#14532d] text-white" data-testid="save-payable-btn">Save</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* PAYMENT DIALOG */}
      <Dialog open={paymentDialog} onOpenChange={setPaymentDialog}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle style={{ fontFamily: 'Manrope' }}>Record Payment</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <p className="text-sm">Outstanding Balance: <span className="font-bold">{formatPHP(paymentTarget?.balance || 0)}</span></p>
            <div>
              <Label className="text-xs text-slate-500">Payment Amount (₱)</Label>
              <CalcInput data-testid="payment-amount-input" className="h-10" value={paymentAmount} onChange={(v) => setPaymentAmount(parseFloat(v) || 0)} />
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" onClick={() => setPaymentDialog(false)}>Cancel</Button>
              <Button onClick={handlePayment} className="bg-[#1A4D2E] hover:bg-[#14532d] text-white" data-testid="confirm-payment-btn">Record Payment</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
      <UploadQRDialog
        open={uploadQROpen}
        onClose={(count) => { setUploadQROpen(false); if (count > 0) toast.success(`${count} receipt photo(s) saved!`); }}
        recordType="expense"
        recordId={uploadExpenseId}
      />
      <ViewQRDialog
        open={viewQRExpenseOpen}
        onClose={() => setViewQRExpenseOpen(false)}
        recordType="expense"
        recordId={viewQRExpenseId}
      />
      <VerifyPinDialog
        open={verifyExpenseOpen}
        onClose={() => setVerifyExpenseOpen(false)}
        docType="expense"
        docId={verifyExpenseId}
        docLabel={verifyExpenseLabel}
        onVerified={() => {
          setVerifyExpenseOpen(false);
          // Refresh expenses list
          setExpenses(prev => prev.map(e => e.id === verifyExpenseId
            ? { ...e, verified: true, verification_status: 'clean' } : e));
        }}
      />
      <ExpenseDetailModal
        open={expenseModalOpen}
        onOpenChange={(open) => { setExpenseModalOpen(open); if (!open) setSelectedExpenseId(null); }}
        expenseId={selectedExpenseId}
        onUpdated={() => fetchAll()}
      />
      <InvoiceDetailModal compact
        open={invoiceModalOpen}
        onOpenChange={(open) => { setInvoiceModalOpen(open); if (!open) setSelectedInvoiceNumber(null); }}
        invoiceNumber={selectedInvoiceNumber}
        onUpdated={() => fetchAll()}
      />
      <CustomerStatementModal
        open={stmtOpen}
        onOpenChange={setStmtOpen}
        customer={stmtCustomer}
      />
    </div>
  );
}