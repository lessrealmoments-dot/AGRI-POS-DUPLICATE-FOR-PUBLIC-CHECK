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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { Plus, ArrowDown, Search, Filter, Edit2, Tractor, FileText, Banknote, AlertTriangle, Shield, Upload, UserCheck } from 'lucide-react';
import { toast } from 'sonner';
import { UnclosedDaysBanner } from '../components/UnclosedDaysBanner';
import UploadQRDialog from '../components/UploadQRDialog';
import ReceiptUploadInline from '../components/ReceiptUploadInline';
import VerificationBadge from '../components/VerificationBadge';
import VerifyPinDialog from '../components/VerifyPinDialog';
import CalcInput from '../components/CalcInput';
import ViewQRDialog from '../components/ViewQRDialog';
import ExpenseDetailModal from '../components/ExpenseDetailModal';
import InvoiceDetailModal from '../components/InvoiceDetailModal';
import { localTodayStr } from '../lib/dateFormat';

const EXPENSE_CATEGORIES = [
  "Utilities", "Rent", "Supplies", "Transportation", "Fuel/Gas",
  "Employee Advance", "Farm Expense", "Customer Cash Out", "Repairs & Maintenance",
  "Marketing", "Salaries & Wages", "Communication", "Insurance",
  "Professional Fees", "Taxes & Licenses", "Office Supplies",
  "Equipment", "Miscellaneous"
];

const PAYMENT_METHODS = ["Cash", "Cash (from Safe)", "GCash", "Maya", "Bank Transfer", "Check", "Credit Card"];

export default function ExpensesPage() {
  const { currentBranch } = useAuth();
  const [expenses, setExpenses] = useState([]);
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

  const [expenseDialog, setExpenseDialog] = useState(false);
  const [farmExpenseDialog, setFarmExpenseDialog] = useState(false);
  const [cashOutDialog, setCashOutDialog] = useState(false);
  const [employeeAdvanceDialog, setEmployeeAdvanceDialog] = useState(false);
  const [editMode, setEditMode] = useState(false);

  const [expenseForm, setExpenseForm] = useState({
    category: 'Miscellaneous', description: '', notes: '', amount: 0,
    payment_method: 'Cash', reference_number: '', date: localTodayStr()
  });
  const [farmExpenseForm, setFarmExpenseForm] = useState({
    description: '', notes: '', amount: 0, customer_id: '',
    payment_method: 'Cash', reference_number: '', date: localTodayStr(),
    due_date: '', terms: ''
  });
  const [cashOutForm, setCashOutForm] = useState({
    description: '', notes: '', amount: 0, customer_id: '',
    payment_method: 'Cash', reference_number: '', date: localTodayStr(),
    due_date: '', terms: ''
  });
  const [employeeAdvanceForm, setEmployeeAdvanceForm] = useState({
    description: '', notes: '', amount: 0, employee_id: '',
    date: localTodayStr(),
  });
  const [submitting, setSubmitting] = useState(false);  // double-submit guard for all save buttons
  const [eaCaSummary, setEaCaSummary] = useState(null);
  const [eaManagerPin, setEaManagerPin] = useState('');
  const [eaManagerPinDialog, setEaManagerPinDialog] = useState(false);
  const [expenseReceiptData, setExpenseReceiptData] = useState(null);
  const [farmReceiptData, setFarmReceiptData] = useState(null);

  // Farm Expense customer search (replaces dropdown)
  const [farmCustomerQuery, setFarmCustomerQuery] = useState('');
  const [farmCustomerHighlight, setFarmCustomerHighlight] = useState(0);
  const [farmCustomerListOpen, setFarmCustomerListOpen] = useState(false);

  // Cash Out customer search (replaces dropdown)
  const [cashOutCustomerQuery, setCashOutCustomerQuery] = useState('');
  const [cashOutCustomerHighlight, setCashOutCustomerHighlight] = useState(0);
  const [cashOutCustomerListOpen, setCashOutCustomerListOpen] = useState(false);

  const [encodingDate, setEncodingDate] = useState(localTodayStr());

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

      const [expRes, custRes, empRes] = await Promise.all([
        api.get('/expenses', { params }),
        api.get('/customers', { params: { limit: 500 } }),
        api.get('/employees').catch(() => ({ data: [] })),
      ]);
      setExpenses(expRes.data.expenses || []);
      setCustomers(custRes.data.customers || []);
      setEmployees(empRes.data || []);
    } catch { toast.error('Failed to load expenses'); }
  }, [currentBranch, filters]);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const openNewExpense = () => {
    setEditMode(false);
    setExpenseForm({
      category: 'Miscellaneous', description: '', notes: '', amount: 0,
      payment_method: 'Cash', reference_number: '', date: encodingDate,
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
      date: expense.date || localTodayStr(),
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
      payment_method: 'Cash', reference_number: '', date: encodingDate,
      due_date: '', terms: ''
    });
    setFarmCustomerQuery('');
    setFarmCustomerHighlight(0);
    setFarmCustomerListOpen(false);
    setFarmExpenseDialog(true);
  };

  const openCashOut = () => {
    setCashOutForm({
      description: '', notes: '', amount: 0, customer_id: '',
      payment_method: 'Cash', reference_number: '', date: encodingDate,
      due_date: '', terms: ''
    });
    setCashOutCustomerQuery('');
    setCashOutCustomerHighlight(0);
    setCashOutCustomerListOpen(false);
    setCashOutDialog(true);
  };

  const openEmployeeAdvance = () => {
    setEmployeeAdvanceForm({
      description: '', notes: '', amount: 0, employee_id: '',
      date: encodingDate,
    });
    setEaCaSummary(null);
    setEmployeeAdvanceDialog(true);
  };

  const handleEaEmployeeSelect = async (empId) => {
    const emp = employees.find(e => e.id === empId);
    if (!emp) { setEmployeeAdvanceForm(f => ({...f, employee_id: ''})); setEaCaSummary(null); return; }
    setEmployeeAdvanceForm(f => ({...f, employee_id: emp.id, description: f.description || `Advance to ${emp.name}`}));
    try {
      const res = await api.get(`/employees/${emp.id}/ca-summary`);
      setEaCaSummary(res.data);
    } catch { setEaCaSummary(null); }
  };

  const handleCreateEmployeeAdvance = async (approvedBy = '') => {
    if (submitting) return;
    if (!employeeAdvanceForm.employee_id) { toast.error('Please select an employee'); return; }
    if (!employeeAdvanceForm.amount || employeeAdvanceForm.amount <= 0) { toast.error('Amount must be greater than 0'); return; }
    if (!currentBranch?.id) { toast.error('Please select a branch first'); return; }

    // Check CA limit
    if (eaCaSummary) {
      const limit = eaCaSummary.monthly_ca_limit || 0;
      if (limit > 0 && (eaCaSummary.this_month_total + parseFloat(employeeAdvanceForm.amount)) > limit && !approvedBy) {
        setEaManagerPinDialog(true);
        return;
      }
    }

    setSubmitting(true);
    try {
      const emp = employees.find(e => e.id === employeeAdvanceForm.employee_id);
      const payload = {
        ...employeeAdvanceForm,
        branch_id: currentBranch.id,
        employee_name: emp?.name || '',
      };
      if (approvedBy) payload.manager_approved_by = approvedBy;
      await api.post('/expenses/employee-advance', payload);
      toast.success(`Cash advance recorded for ${emp?.name || 'employee'}`);
      setEmployeeAdvanceDialog(false);
      setEaCaSummary(null);
      fetchAll();
    } catch (e) {
      const detail = e.response?.data?.detail;
      const msg = typeof detail === 'string' ? detail : detail?.message || 'Error creating employee advance';
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

  const handleEaManagerPin = async () => {
    if (!eaManagerPin) { toast.error('Enter manager PIN'); return; }
    try {
      const emp = employees.find(e => e.id === employeeAdvanceForm.employee_id);
      const employeeName = emp?.name || 'employee';
      const limit = eaCaSummary?.monthly_ca_limit || 0;
      const res = await api.post('/auth/verify-manager-pin', {
        pin: eaManagerPin,
        action_key: 'reverse_employee_advance',
        context: {
          type: 'employee_advance',
          description: `₱${parseFloat(employeeAdvanceForm.amount).toFixed(2)} cash advance for ${employeeName} (over ₱${limit.toFixed(2)} monthly limit)`,
          amount: parseFloat(employeeAdvanceForm.amount),
          employee_name: employeeName,
          monthly_limit: limit,
          branch_id: currentBranch?.id,
          branch_name: currentBranch?.name,
        }
      });
      if (res.data.valid) {
        toast.success(`Approved by ${res.data.manager_name}`);
        setEaManagerPinDialog(false);
        setEaManagerPin('');
        await handleCreateEmployeeAdvance(res.data.manager_name);
      } else {
        toast.error('Invalid PIN');
      }
    } catch (e) {
      const detail = e.response?.data?.detail;
      const msg = typeof detail === 'string' ? detail : detail?.message || 'Verification failed';
      toast.error(msg);
    }
  };

  const handleSaveExpense = async (approvedBy = '') => {
    if (submitting) return;
    if (!expenseForm.amount || expenseForm.amount <= 0) {
      toast.error('Amount must be greater than 0');
      return;
    }
    if (!currentBranch?.id) {
      toast.error('Please select a specific branch from the sidebar before recording expenses');
      return;
    }
    if (expenseForm.category === 'Employee Advance' && expenseForm.employee_id && caSummary && !editMode) {
      const limit = caSummary.monthly_ca_limit || 0;
      if (limit > 0 && (caSummary.this_month_total + parseFloat(expenseForm.amount)) > limit && !approvedBy) {
        setCaManagerPinDialog(true);
        return;
      }
    }
    setSubmitting(true);
    try {
      const payload = { ...expenseForm, branch_id: currentBranch?.id };
      if (approvedBy) payload.manager_approved_by = approvedBy;
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
      const detail = e.response?.data?.detail;
      const msg = typeof detail === 'string' ? detail : detail?.message || 'Error saving expense';
      toast.error(msg);
    } finally {
      setSubmitting(false);
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
    } catch (e) {
      const detail = e.response?.data?.detail;
      const msg = typeof detail === 'string' ? detail : detail?.message || 'Verification failed';
      toast.error(msg);
    }
  };

  const handleCreateFarmExpense = async () => {
    if (submitting) return;
    if (!farmExpenseForm.customer_id) { toast.error('Please select a customer to bill'); return; }
    if (!farmExpenseForm.amount || farmExpenseForm.amount <= 0) { toast.error('Amount must be greater than 0'); return; }
    setSubmitting(true);
    try {
      const payload = { ...farmExpenseForm, branch_id: currentBranch?.id };
      if (farmReceiptData?.sessionId) {
        payload.upload_session_ids = [farmReceiptData.sessionId];
      }
      const res = await api.post('/expenses/farm', payload);
      toast.success(res.data.message);
      setFarmExpenseDialog(false);
      setFarmReceiptData(null);
      fetchAll();
    } catch (e) {
      const detail = e.response?.data?.detail;
      const msg = typeof detail === 'string' ? detail : detail?.message || 'Error creating farm expense';
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  };

  const handleCreateCashOut = async () => {
    if (submitting) return;
    if (!cashOutForm.customer_id) { toast.error('Please select a customer'); return; }
    if (!cashOutForm.amount || cashOutForm.amount <= 0) { toast.error('Amount must be greater than 0'); return; }
    setSubmitting(true);
    try {
      const res = await api.post('/expenses/customer-cashout', { ...cashOutForm, branch_id: currentBranch?.id });
      toast.success(res.data.message);
      setCashOutDialog(false);
      fetchAll();
    } catch (e) {
      const detail = e.response?.data?.detail;
      const msg = typeof detail === 'string' ? detail : detail?.message || 'Error creating cash out';
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
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
    <div className="space-y-6 animate-fadeIn" data-testid="expenses-page">
      <div>
        <h1 className="text-2xl font-bold tracking-tight" style={{ fontFamily: 'Manrope' }}>Expenses</h1>
        <p className="text-sm text-slate-500 mt-1">Record and manage all business expenses</p>
      </div>

      {/* Unclosed Days Banner */}
      {currentBranch?.id && (
        <UnclosedDaysBanner
          branchId={currentBranch.id}
          onDateSelect={setEncodingDate}
        />
      )}

      {/* Summary */}
      <Card className="border-slate-200">
        <CardContent className="p-4">
          <div className="flex items-center gap-2 mb-2">
            <ArrowDown size={16} className="text-red-500" />
            <span className="text-xs text-slate-500 uppercase font-medium">Total Expenses</span>
          </div>
          <p className="text-2xl font-bold text-red-600" data-testid="expenses-total" style={{ fontFamily: 'Manrope' }}>{formatPHP(totalExpenses)}</p>
        </CardContent>
      </Card>

      {/* Action Buttons */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => setShowFilters(!showFilters)} data-testid="toggle-filters-btn">
            <Filter size={14} className="mr-1" /> Filters
          </Button>
          {(filters.category || filters.payment_method || filters.date_from || filters.date_to || filters.search) && (
            <Button variant="ghost" size="sm" onClick={clearFilters} className="text-slate-500" data-testid="clear-filters-btn">
              Clear filters
            </Button>
          )}
        </div>
        <div className="flex gap-2">
          <Button onClick={openEmployeeAdvance} variant="outline" className="border-violet-300 text-violet-700 hover:bg-violet-50" data-testid="expenses-employee-advance-btn">
            <UserCheck size={16} className="mr-2" /> Employee Cash Advance
          </Button>
          <Button onClick={openCashOut} variant="outline" className="border-blue-300 text-blue-700 hover:bg-blue-50" data-testid="expenses-cashout-btn">
            <Banknote size={16} className="mr-2" /> Customer Cash Out
          </Button>
          <Button onClick={openFarmExpense} variant="outline" className="border-amber-300 text-amber-700 hover:bg-amber-50" data-testid="expenses-farm-btn">
            <Tractor size={16} className="mr-2" /> Farm Expense
          </Button>
          <Button onClick={openNewExpense} className="bg-[#1A4D2E] hover:bg-[#14532d] text-white" data-testid="expenses-create-btn">
            <Plus size={16} className="mr-2" /> Record Expense
          </Button>
        </div>
      </div>

      {/* Filters Panel */}
      {showFilters && (
        <Card className="border-slate-200 bg-slate-50" data-testid="filters-panel">
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
                  <Input className="h-9 pl-8" placeholder="Description, ref..." value={filters.search} onChange={e => setFilters(f => ({ ...f, search: e.target.value }))} />
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
                      {e.category === 'Employee Advance' && <UserCheck size={10} className="mr-1" />}
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
                        {e.category === 'Customer Cash Out' ? 'Loaned to: ' : 'Billed to: '}{e.customer_name}
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
                    {e.fund_source && e.fund_source !== 'cashier' && (
                      <div className={`text-[9px] mt-0.5 font-medium ${e.fund_source === 'safe' ? 'text-orange-600' : 'text-cyan-600'}`}>
                        {e.fund_source === 'safe' ? 'from Safe' : 'from Digital'}
                      </div>
                    )}
                  </TableCell>
                  <TableCell className="text-xs text-slate-500 font-mono">{e.reference_number || '-'}</TableCell>
                  <TableCell className="text-right font-semibold text-red-600">{formatPHP(e.amount)}</TableCell>
                  <TableCell className="text-xs text-slate-500">{e.created_by_name}</TableCell>
                  <TableCell>
                    <div className="flex gap-1">
                      <Button variant="ghost" size="sm" onClick={() => openEditExpense(e)} className="text-slate-500 h-7 px-2" data-testid={`edit-expense-${e.id}`}>
                        <Edit2 size={12} />
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => { setUploadExpenseId(e.id); setUploadQROpen(true); }}
                        className="text-blue-500 h-7 px-2" title="Upload Receipt" data-testid={`upload-receipt-${e.id}`}>
                        <Upload size={12} />
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => { setViewQRExpenseId(e.id); setViewQRExpenseOpen(true); }}
                        className="text-slate-500 h-7 px-2" title="View on Phone">
                        <span className="text-xs">📱</span>
                      </Button>
                      {!e.verified && (
                        <Button variant="ghost" size="sm" onClick={() => { setVerifyExpenseId(e.id); setVerifyExpenseLabel(e.description); setVerifyExpenseOpen(true); }}
                          className="text-[#1A4D2E] h-7 px-2" title="Verify" data-testid={`verify-expense-${e.id}`}>
                          <Shield size={12} />
                        </Button>
                      )}
                      <VerificationBadge doc={e} compact />
                      <Button variant="ghost" size="sm" onClick={() => deleteExpense(e.id)} className="text-red-500 h-7 px-2" data-testid={`delete-expense-${e.id}`}>Del</Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
              {!expenses.length && (
                <TableRow>
                  <TableCell colSpan={8} className="text-center py-8 text-slate-400" data-testid="no-expenses-msg">
                    No expenses found
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

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
                  <SelectTrigger className="h-10" data-testid="expenses-expense-category">
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
              <Input data-testid="expenses-expense-description" className="h-10" value={expenseForm.description} onChange={e => setExpenseForm({ ...expenseForm, description: e.target.value })} placeholder="What is this expense for?" />
            </div>
            {expenseForm.category === 'Employee Advance' && !editMode && (
              <div className="space-y-3 bg-amber-50 border border-amber-200 rounded-lg p-3">
                <div>
                  <Label className="text-xs text-amber-700 font-medium">Employee *</Label>
                  <Select value={expenseForm.employee_id || 'none'} onValueChange={v => handleEmployeeSelect(v === 'none' ? '' : v)}>
                    <SelectTrigger className="h-9 bg-white" data-testid="expenses-ca-employee-select"><SelectValue placeholder="Select employee..." /></SelectTrigger>
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
                <CalcInput data-testid="expenses-expense-amount" className="h-10" value={String(expenseForm.amount || '')} onChange={(v) => setExpenseForm({ ...expenseForm, amount: parseFloat(v) || 0 })} />
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
              <Button onClick={() => handleSaveExpense()} disabled={submitting} className="bg-[#1A4D2E] hover:bg-[#14532d] text-white disabled:opacity-50 disabled:cursor-not-allowed" data-testid="expenses-save-btn">
                {submitting ? 'Saving…' : (editMode ? 'Update Expense' : 'Save Expense')}
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
        <DialogContent className="sm:max-w-lg max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle style={{ fontFamily: 'Manrope' }} className="flex items-center gap-2">
              <Tractor size={20} className="text-amber-600" /> Farm Expense
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-sm text-amber-800">
              This will record a farm expense and automatically create an invoice for the selected customer.
            </div>
            {/* Bill to Customer — moved to top per user feedback. Replaces the
                dropdown with a typeahead search; press Enter (or click) to
                pick the highlighted match. */}
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
                      autoFocus
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
              <Input className="h-10" value={farmExpenseForm.description} onChange={e => setFarmExpenseForm({ ...farmExpenseForm, description: e.target.value })}
                placeholder="e.g. Tilling, Plowing, Harvesting" data-testid="expenses-farm-description" />
            </div>
            <div>
              <Label className="text-xs text-slate-500">Notes (Tilling, Labor, Gas, etc.)</Label>
              <Input className="h-10" value={farmExpenseForm.notes} onChange={e => setFarmExpenseForm({ ...farmExpenseForm, notes: e.target.value })}
                placeholder="e.g. 2 hours tilling + 5L gas + 2 workers" />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label className="text-xs text-slate-500">Amount (₱) *</Label>
                <CalcInput className="h-10" value={String(farmExpenseForm.amount || '')}
                  onChange={(v) => setFarmExpenseForm({ ...farmExpenseForm, amount: parseFloat(v) || 0 })} data-testid="expenses-farm-amount" />
              </div>
              <div>
                <Label className="text-xs text-slate-500">Date</Label>
                <Input type="date" className="h-10" value={farmExpenseForm.date}
                  onChange={e => setFarmExpenseForm({ ...farmExpenseForm, date: e.target.value })} />
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
                <Input className="h-10" value={farmExpenseForm.reference_number}
                  onChange={e => setFarmExpenseForm({ ...farmExpenseForm, reference_number: e.target.value })} placeholder="Optional" />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label className="text-xs text-slate-500">Invoice Due Date</Label>
                <Input type="date" className="h-10" value={farmExpenseForm.due_date}
                  onChange={e => setFarmExpenseForm({ ...farmExpenseForm, due_date: e.target.value })} />
              </div>
              <div>
                <Label className="text-xs text-slate-500">Payment Terms</Label>
                <Input className="h-10" value={farmExpenseForm.terms}
                  onChange={e => setFarmExpenseForm({ ...farmExpenseForm, terms: e.target.value })} placeholder="e.g. Net 30" />
              </div>
            </div>
            <div className="border-t pt-4">
              <ReceiptUploadInline
                required={false}
                label="Farm Receipt Photo (Optional — can upload later in Closing Wizard)"
                recordType="expense"
                compact={false}
                recordSummary={{
                  type_label: 'Farm Expense',
                  title: farmExpenseForm.description || 'Farm Expense',
                  description: farmExpenseForm.notes || '',
                  amount: farmExpenseForm.amount || 0,
                  date: farmExpenseForm.date,
                }}
                onUploaded={(data) => setFarmReceiptData(data)}
              />
              <p className="text-[11px] text-slate-400 mt-1.5 italic">
                No receipt yet? Skip this — you can upload it later from the Closing Wizard or the expense row's upload button.
              </p>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" onClick={() => setFarmExpenseDialog(false)}>Cancel</Button>
              <Button onClick={handleCreateFarmExpense} disabled={submitting} className="bg-amber-600 hover:bg-amber-700 text-white disabled:opacity-50 disabled:cursor-not-allowed" data-testid="expenses-save-farm-btn">
                <Tractor size={14} className="mr-2" /> {submitting ? 'Creating…' : 'Create Expense & Invoice'}
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
              <Banknote size={20} className="text-blue-600" /> Customer Cash Out
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
                      autoFocus
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
                <CalcInput className="h-10" value={String(cashOutForm.amount || '')}
                  onChange={(v) => setCashOutForm({ ...cashOutForm, amount: parseFloat(v) || 0 })} data-testid="expenses-cashout-amount" />
              </div>
              <div>
                <Label className="text-xs text-slate-500">Date</Label>
                <Input type="date" className="h-10" value={cashOutForm.date}
                  onChange={e => setCashOutForm({ ...cashOutForm, date: e.target.value })} />
              </div>
            </div>
            <div>
              <Label className="text-xs text-slate-500">Purpose / Description</Label>
              <Input className="h-10" value={cashOutForm.description}
                onChange={e => setCashOutForm({ ...cashOutForm, description: e.target.value })}
                placeholder="e.g. Cash Advance, Loan, etc." data-testid="expenses-cashout-description" />
            </div>
            <div>
              <Label className="text-xs text-slate-500">Notes (optional)</Label>
              <Input className="h-10" value={cashOutForm.notes}
                onChange={e => setCashOutForm({ ...cashOutForm, notes: e.target.value })} placeholder="Additional details..." />
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
                <Input className="h-10" value={cashOutForm.reference_number}
                  onChange={e => setCashOutForm({ ...cashOutForm, reference_number: e.target.value })} placeholder="Optional" />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label className="text-xs text-slate-500">Invoice Due Date</Label>
                <Input type="date" className="h-10" value={cashOutForm.due_date}
                  onChange={e => setCashOutForm({ ...cashOutForm, due_date: e.target.value })} />
              </div>
              <div>
                <Label className="text-xs text-slate-500">Payment Terms</Label>
                <Input className="h-10" value={cashOutForm.terms}
                  onChange={e => setCashOutForm({ ...cashOutForm, terms: e.target.value })} placeholder="e.g. Net 30" />
              </div>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" onClick={() => setCashOutDialog(false)}>Cancel</Button>
              <Button onClick={handleCreateCashOut} disabled={submitting} className="bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50 disabled:cursor-not-allowed" data-testid="expenses-save-cashout-btn">
                <Banknote size={14} className="mr-2" /> {submitting ? 'Releasing…' : 'Release Cash & Create Invoice'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* EMPLOYEE CASH ADVANCE DIALOG */}
      <Dialog open={employeeAdvanceDialog} onOpenChange={setEmployeeAdvanceDialog}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle style={{ fontFamily: 'Manrope' }} className="flex items-center gap-2">
              <UserCheck size={20} className="text-violet-600" /> Employee Cash Advance
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="bg-violet-50 border border-violet-200 rounded-lg p-3 text-sm text-violet-800">
              This will record a cash advance for an employee and deduct from the cashier wallet.
            </div>
            <div>
              <Label className="text-xs text-slate-500 font-semibold">Select Employee *</Label>
              <Select value={employeeAdvanceForm.employee_id || 'none'} onValueChange={v => handleEaEmployeeSelect(v === 'none' ? '' : v)}>
                <SelectTrigger className="h-10" data-testid="expenses-ea-employee-select">
                  <SelectValue placeholder="Select employee..." />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="none">— Select employee —</SelectItem>
                  {employees.filter(e => e.active !== false).map(e => (
                    <SelectItem key={e.id} value={e.id}>{e.name} {e.position ? `(${e.position})` : ''}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {eaCaSummary && (
              <div className="space-y-2">
                <div className="grid grid-cols-3 gap-2 text-center">
                  <div className="bg-white rounded border p-2">
                    <p className="text-[10px] text-slate-400">This Month</p>
                    <p className={`text-sm font-bold ${eaCaSummary.is_over_limit ? 'text-red-600' : 'text-violet-600'}`}>{formatPHP(eaCaSummary.this_month_total)}</p>
                  </div>
                  <div className="bg-white rounded border p-2">
                    <p className="text-[10px] text-slate-400">Monthly Limit</p>
                    <p className="text-sm font-bold">{eaCaSummary.monthly_ca_limit > 0 ? formatPHP(eaCaSummary.monthly_ca_limit) : 'None'}</p>
                  </div>
                  <div className="bg-white rounded border p-2">
                    <p className="text-[10px] text-slate-400">Unpaid Balance</p>
                    <p className="text-sm font-bold text-slate-700">{formatPHP(eaCaSummary.total_advance_balance)}</p>
                  </div>
                </div>
                {eaCaSummary.is_over_limit && (
                  <div className="flex items-center gap-2 text-xs text-red-700 bg-red-50 rounded p-2">
                    <AlertTriangle size={13} /> Monthly limit reached — manager PIN will be required
                  </div>
                )}
              </div>
            )}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label className="text-xs text-slate-500">Amount (₱) *</Label>
                <CalcInput className="h-10" value={String(employeeAdvanceForm.amount || '')}
                  onChange={(v) => setEmployeeAdvanceForm({ ...employeeAdvanceForm, amount: parseFloat(v) || 0 })} data-testid="expenses-ea-amount" />
              </div>
              <div>
                <Label className="text-xs text-slate-500">Date</Label>
                <Input type="date" className="h-10" value={employeeAdvanceForm.date}
                  onChange={e => setEmployeeAdvanceForm({ ...employeeAdvanceForm, date: e.target.value })} />
              </div>
            </div>
            <div>
              <Label className="text-xs text-slate-500">Description</Label>
              <Input className="h-10" value={employeeAdvanceForm.description}
                onChange={e => setEmployeeAdvanceForm({ ...employeeAdvanceForm, description: e.target.value })}
                placeholder="e.g. Emergency advance, rice advance..." data-testid="expenses-ea-description" />
            </div>
            <div>
              <Label className="text-xs text-slate-500">Notes (optional)</Label>
              <Input className="h-10" value={employeeAdvanceForm.notes}
                onChange={e => setEmployeeAdvanceForm({ ...employeeAdvanceForm, notes: e.target.value })} placeholder="Additional details..." />
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" onClick={() => setEmployeeAdvanceDialog(false)}>Cancel</Button>
              <Button onClick={() => handleCreateEmployeeAdvance()} disabled={submitting} className="bg-violet-600 hover:bg-violet-700 text-white disabled:opacity-50 disabled:cursor-not-allowed" data-testid="expenses-save-ea-btn">
                <UserCheck size={14} className="mr-2" /> {submitting ? 'Recording…' : 'Record Cash Advance'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* EA MANAGER PIN DIALOG */}
      <Dialog open={eaManagerPinDialog} onOpenChange={setEaManagerPinDialog}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
              <Shield size={18} className="text-amber-500" /> Manager Approval Required
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="bg-red-50 border border-red-200 rounded-lg p-3">
              <p className="text-sm font-medium text-red-700">
                {employees.find(e => e.id === employeeAdvanceForm.employee_id)?.name || 'Employee'} has exceeded their monthly CA limit
              </p>
              <p className="text-xs text-red-600 mt-1">This month: {formatPHP(eaCaSummary?.this_month_total || 0)} / Limit: {formatPHP(eaCaSummary?.monthly_ca_limit || 0)}</p>
              <p className="text-xs text-red-500 mt-1">Additional: {formatPHP(parseFloat(employeeAdvanceForm.amount || 0))}</p>
            </div>
            <div>
              <Label>Manager PIN</Label>
              <Input type="password" autoComplete="new-password" value={eaManagerPin} onChange={e => setEaManagerPin(e.target.value)}
                placeholder="Enter 4-digit PIN" className="text-center text-2xl tracking-widest h-14" maxLength={6} />
            </div>
            <div className="flex gap-2">
              <Button variant="outline" className="flex-1" onClick={() => { setEaManagerPinDialog(false); setEaManagerPin(''); }}>Cancel</Button>
              <Button className="flex-1 bg-amber-500 hover:bg-amber-600 text-white" onClick={handleEaManagerPin}>Approve & Save</Button>
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
          setExpenses(prev => prev.map(e => e.id === verifyExpenseId
            ? { ...e, verified: true, verification_status: 'clean' } : e));
        }}
      />
      <ExpenseDetailModal
        open={expenseModalOpen}
        onOpenChange={(open) => { setExpenseModalOpen(open); if (!open) setSelectedExpenseId(null); }}
        expenseId={selectedExpenseId}
        onUpdated={fetchAll}
      />
      <InvoiceDetailModal compact
        open={invoiceModalOpen}
        onOpenChange={(open) => { setInvoiceModalOpen(open); if (!open) setSelectedInvoiceNumber(null); }}
        invoiceNumber={selectedInvoiceNumber}
        onUpdated={fetchAll}
      />
    </div>
  );
}