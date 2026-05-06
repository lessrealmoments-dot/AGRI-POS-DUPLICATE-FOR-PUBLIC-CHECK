import { useState, useEffect, useCallback } from 'react';
import { api, useAuth } from '../contexts/AuthContext';
import { formatPHP, fmtDate } from '../lib/utils';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Badge } from '../components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs';
import { ScrollArea } from '../components/ui/scroll-area';
import { Users, Plus, Pencil, Trash2, Search, FileText, Eye, X, Printer, AlertTriangle, Clock } from 'lucide-react';
import { toast } from 'sonner';
import CustomerStatementModal from '../components/CustomerStatementModal';
import InvoiceDetailModal from '../components/InvoiceDetailModal';
import CalcInput from '../components/CalcInput';

const SALE_TYPE_LABELS = {
  farm_expense: { label: 'Farm Expense', cls: 'bg-green-100 text-green-700' },
  cash_advance: { label: 'Customer Cash Out', cls: 'bg-purple-100 text-purple-700' },
  interest_charge: { label: 'Interest Charge', cls: 'bg-amber-100 text-amber-700' },
  penalty_charge: { label: 'Penalty Charge', cls: 'bg-red-100 text-red-700' },
  walk_in: { label: 'Sale', cls: 'bg-blue-100 text-blue-700' },
  credit: { label: 'Credit Sale', cls: 'bg-blue-100 text-blue-700' },
};
const getSaleTypeBadge = (inv) => {
  const key = inv.sale_type || inv.payment_type || 'walk_in';
  const cfg = SALE_TYPE_LABELS[key] || { label: key, cls: 'bg-slate-100 text-slate-600' };
  return <Badge variant="outline" className={`text-[10px] ${cfg.cls}`}>{cfg.label}</Badge>;
};

export default function CustomersPage() {
  const { currentBranch, hasPerm, user } = useAuth();
  const canViewBalance = hasPerm('customers', 'view_balance');
  const canManageCredit = hasPerm('customers', 'manage_credit');
  const canDelete = hasPerm('customers', 'delete');
  const isAdminOrOwner = user?.role === 'admin' || user?.role === 'owner' || user?.is_super_admin;
  const [customers, setCustomers] = useState([]);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [schemes, setSchemes] = useState([]);
  const [pageSize, setPageSize] = useState(() => {
    const stored = parseInt(localStorage.getItem('customers_page_size') || '20', 10);
    return [10, 25, 50, 100, 500].includes(stored) ? stored : 20;
  });
  const LIMIT = pageSize;
  const [form, setForm] = useState({ name: '', phone: '', email: '', address: '', price_scheme: 'retail', credit_limit: 0, interest_rate: 0 });

  // Bulk selection & bulk delete
  const [selected, setSelected] = useState(new Set());
  const [filterNoInvoice, setFilterNoInvoice] = useState(false);
  const [bulkDialogOpen, setBulkDialogOpen] = useState(false);
  const [bulkPin, setBulkPin] = useState('');
  const [bulkForce, setBulkForce] = useState(false);
  const [bulkSubmitting, setBulkSubmitting] = useState(false);
  const [bulkResult, setBulkResult] = useState(null);
  
  // Transaction history
  const [historyDialog, setHistoryDialog] = useState(false);
  const [statementDialog, setStatementDialog] = useState(false);
  const [statementCustomer, setStatementCustomer] = useState(null);
  const [invoiceModalOpen, setInvoiceModalOpen] = useState(false);
  const [selectedInvoiceNumber, setSelectedInvoiceNumber] = useState(null);
  const openDetailModal = (num) => { setSelectedInvoiceNumber(num); setInvoiceModalOpen(true); };
  const [selectedCustomer, setSelectedCustomer] = useState(null);
  const [transactions, setTransactions] = useState(null);
  const [loadingHistory, setLoadingHistory] = useState(false);

  // Payment history
  const [payHistoryDialog, setPayHistoryDialog] = useState(false);
  const [payHistoryCustomer, setPayHistoryCustomer] = useState(null);
  const [payHistory, setPayHistory] = useState([]);
  const [payHistoryLoading, setPayHistoryLoading] = useState(false);

  const openPayHistory = async (customer) => {
    setPayHistoryCustomer(customer);
    setPayHistoryDialog(true);
    setPayHistoryLoading(true);
    try {
      const res = await api.get(`/customers/${customer.id}/payment-history`);
      setPayHistory(res.data || []);
    } catch { toast.error('Failed to load payment history'); }
    setPayHistoryLoading(false);
  };

  const fetchCustomers = useCallback(async () => {
    try {
      const params = { skip: page * LIMIT, limit: LIMIT };
      if (search) params.search = search;
      if (currentBranch) params.branch_id = currentBranch.id;  // branch-scoped
      const res = await api.get('/customers', { params });
      setCustomers(res.data.customers);
      setTotal(res.data.total);
      setSelected(new Set()); // reset selection on page/search change
    } catch { toast.error('Failed to load customers'); }
  }, [search, page, currentBranch, LIMIT]);

  useEffect(() => { fetchCustomers(); }, [fetchCustomers]);
  useEffect(() => { api.get('/price-schemes').then(r => setSchemes(r.data)).catch(() => {}); }, []);

  // Derived list respecting the "no invoice" filter (applied client-side on the current page)
  const visibleCustomers = filterNoInvoice
    ? customers.filter(c => (c.balance || 0) <= 0)
    : customers;
  const allVisibleSelected = visibleCustomers.length > 0 && visibleCustomers.every(c => selected.has(c.id));
  const toggleAllVisible = () => {
    if (allVisibleSelected) {
      const next = new Set(selected);
      visibleCustomers.forEach(c => next.delete(c.id));
      setSelected(next);
    } else {
      const next = new Set(selected);
      visibleCustomers.forEach(c => next.add(c.id));
      setSelected(next);
    }
  };
  const toggleOne = (id) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  };

  const openBulkDelete = () => {
    if (!selected.size) { toast.error('Pick at least one customer'); return; }
    setBulkPin('');
    setBulkForce(false);
    setBulkResult(null);
    setBulkDialogOpen(true);
  };

  const submitBulkDelete = async () => {
    if (!bulkPin.trim()) { toast.error('PIN required'); return; }
    setBulkSubmitting(true);
    try {
      const res = await api.post('/customers/bulk-delete', {
        customer_ids: Array.from(selected),
        pin: bulkPin.trim(),
        force: bulkForce,
      });
      setBulkResult(res.data);
      toast.success(`Deleted ${res.data.deleted_count}, blocked ${res.data.blocked_count}`);
      setSelected(new Set());
      fetchCustomers();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Bulk delete failed');
    }
    setBulkSubmitting(false);
  };

  const openCreate = () => { 
    setEditing(null); 
    setForm({ name: '', phones: [''], email: '', address: '', price_scheme: 'retail', credit_limit: 0, interest_rate: 0, grace_period: 7 }); 
    setDialogOpen(true); 
  };
  
  const openEdit = (c) => { 
    setEditing(c); 
    setForm({ 
      name: c.name,
      phones: c.phones?.length ? c.phones : (c.phone ? [c.phone] : ['']),
      email: c.email || '', address: c.address || '', 
      price_scheme: c.price_scheme || 'retail', credit_limit: c.credit_limit || 0,
      interest_rate: c.interest_rate || 0, grace_period: c.grace_period || 7
    }); 
    setDialogOpen(true); 
  };

  const handleSave = async () => {
    try {
      const phones = form.phones.filter(p => p.trim());
      const payload = { ...form, phones, phone: phones[0] || '' };
      if (editing) {
        // Opening balance is migration-only (create flow). Strip from edit payload.
        delete payload.opening_balance;
        delete payload.opening_balance_date;
        await api.put(`/customers/${editing.id}`, payload);
        toast.success('Customer updated');
      } else {
        if (currentBranch) payload.branch_id = currentBranch.id;
        // Coerce opening balance to number
        payload.opening_balance = parseFloat(payload.opening_balance) || 0;
        const res = await api.post('/customers', payload);
        if (res.data?.opening_invoice_number) {
          toast.success(`Customer created — starting balance receipt ${res.data.opening_invoice_number} generated`);
        } else {
          toast.success('Customer created');
        }
      }
      setDialogOpen(false); fetchCustomers();
    } catch (e) { toast.error(e.response?.data?.detail || 'Error saving customer'); }
  };

  const handleDelete = async (id) => {
    if (!window.confirm('Delete this customer?')) return;
    try { await api.delete(`/customers/${id}`); toast.success('Customer deleted'); fetchCustomers(); }
    catch { toast.error('Failed to delete'); }
  };

  const openHistory = async (customer) => {
    setSelectedCustomer(customer);
    setHistoryDialog(true);
    setLoadingHistory(true);
    try {
      const res = await api.get(`/customers/${customer.id}/transactions`);
      setTransactions(res.data);
    } catch (e) {
      toast.error('Failed to load transactions');
      setTransactions(null);
    }
    setLoadingHistory(false);
  };

  const getStatusBadge = (status) => {
    const styles = {
      paid: 'bg-emerald-100 text-emerald-700',
      partial: 'bg-amber-100 text-amber-700',
      open: 'bg-red-100 text-red-700',
      overdue: 'bg-red-200 text-red-800',
      pending: 'bg-slate-100 text-slate-600',
    };
    return <Badge className={`text-[10px] ${styles[status] || styles.pending}`}>{status}</Badge>;
  };

  return (
    <div className="space-y-6 animate-fadeIn" data-testid="customers-page">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight" style={{ fontFamily: 'Manrope' }}>Customers</h1>
          <p className="text-sm text-slate-500 mt-1">
            {total} customers{currentBranch ? ` — ${currentBranch.name}` : ' (all branches)'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {canDelete && selected.size > 0 && (
            <Button
              data-testid="bulk-delete-btn"
              onClick={openBulkDelete}
              variant="outline"
              className="border-red-300 text-red-600 hover:bg-red-50"
            >
              <Trash2 size={14} className="mr-1.5" /> Delete Selected ({selected.size})
            </Button>
          )}
          <Button data-testid="create-customer-btn" onClick={openCreate} className="bg-[#1A4D2E] hover:bg-[#14532d] text-white">
            <Plus size={16} className="mr-2" /> Add Customer
          </Button>
        </div>
      </div>

      <div className="flex flex-col sm:flex-row sm:items-center gap-3">
        <div className="relative max-w-sm flex-1">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <Input data-testid="customer-search" value={search} onChange={e => { setSearch(e.target.value); setPage(0); }} placeholder="Search by name or phone..." className="pl-9 h-10" />
        </div>
        <div className="flex items-center gap-1.5 text-xs text-slate-600">
          <span>Show</span>
          <select
            data-testid="page-size-select"
            value={pageSize}
            onChange={e => {
              const v = parseInt(e.target.value, 10);
              setPageSize(v);
              setPage(0);
              localStorage.setItem('customers_page_size', String(v));
            }}
            className="h-8 border border-slate-200 rounded px-2 bg-white text-slate-700 text-xs focus:outline-none focus:ring-1 focus:ring-[#1A4D2E]"
          >
            <option value={10}>10</option>
            <option value={25}>25</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
            <option value={500}>500 (view all)</option>
          </select>
          <span>per page</span>
        </div>
        {canDelete && (
          <label className="flex items-center gap-2 text-xs text-slate-600 cursor-pointer select-none" data-testid="filter-no-invoice-label">
            <input
              type="checkbox"
              data-testid="filter-no-invoice-toggle"
              checked={filterNoInvoice}
              onChange={e => setFilterNoInvoice(e.target.checked)}
              className="w-4 h-4 accent-[#1A4D2E]"
            />
            Show only customers with no balance (safe to purge)
          </label>
        )}
      </div>

      <Card className="border-slate-200">
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow className="bg-slate-50">
                {canDelete && (
                  <TableHead className="w-10">
                    <input
                      type="checkbox"
                      data-testid="select-all-customers"
                      checked={allVisibleSelected}
                      onChange={toggleAllVisible}
                      className="w-4 h-4 accent-[#1A4D2E] cursor-pointer"
                      aria-label="Select all customers on this page"
                    />
                  </TableHead>
                )}
                <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">Name</TableHead>
                <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">Phone</TableHead>
                <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">Price Scheme</TableHead>
                {!currentBranch && <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">Branch</TableHead>}
                {canViewBalance && <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium text-right">Balance</TableHead>}
                {canViewBalance && <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium text-right">Credit Limit</TableHead>}
                <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium w-32">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {visibleCustomers.map(c => (
                <TableRow key={c.id} className="table-row-hover cursor-pointer" onClick={() => openHistory(c)}>
                  {canDelete && (
                    <TableCell onClick={e => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        data-testid={`select-customer-${c.id}`}
                        checked={selected.has(c.id)}
                        onChange={() => toggleOne(c.id)}
                        className="w-4 h-4 accent-[#1A4D2E] cursor-pointer"
                        aria-label={`Select ${c.name}`}
                      />
                    </TableCell>
                  )}
                  <TableCell className="font-medium">{c.name}</TableCell>
                  <TableCell className="text-slate-500 text-xs">
                    {(c.phones?.length ? c.phones : (c.phone ? [c.phone] : [])).join(', ') || '—'}
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline" className="text-[10px] capitalize">{c.price_scheme}</Badge>
                  </TableCell>
                  {!currentBranch && (
                    <TableCell className="text-xs text-slate-400">{c.branch_id ? c.branch_id.slice(0, 6) + '…' : '—'}</TableCell>
                  )}
                  {canViewBalance && (
                  <TableCell className="text-right">
                    <span className={c.balance > 0 ? 'text-red-600 font-semibold' : 'text-emerald-600'}>{formatPHP(c.balance || 0)}</span>
                  </TableCell>
                  )}
                  {canViewBalance && <TableCell className="text-right">{formatPHP(c.credit_limit || 0)}</TableCell>}
                  <TableCell onClick={e => e.stopPropagation()}>
                    <div className="flex gap-1">
                      <Button variant="ghost" size="sm" data-testid={`view-customer-${c.id}`} onClick={() => openHistory(c)} title="View Transactions">
                        <Eye size={14} />
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => openPayHistory(c)} title="Payment History"
                        className="text-indigo-400 hover:text-indigo-700" data-testid={`pay-history-${c.id}`}>
                        <Clock size={14} />
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => { setStatementCustomer(c); setStatementDialog(true); }}
                        title="Statement of Account" className="text-slate-400 hover:text-[#1A4D2E]">
                        <Printer size={14} />
                      </Button>
                      <Button variant="ghost" size="sm" data-testid={`edit-customer-${c.id}`} onClick={() => openEdit(c)}>
                        <Pencil size={14} />
                      </Button>
                      <Button variant="ghost" size="sm" data-testid={`delete-customer-${c.id}`} onClick={() => handleDelete(c.id)} className="text-red-500">
                        <Trash2 size={14} />
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
              {!visibleCustomers.length && (
                <TableRow><TableCell colSpan={(canDelete ? 1 : 0) + (currentBranch ? (4 + (canViewBalance ? 2 : 0)) : (5 + (canViewBalance ? 2 : 0)))} className="text-center py-8 text-slate-400">
                  {filterNoInvoice
                    ? 'No customers with zero balance match your filter.'
                    : (currentBranch ? `No customers for ${currentBranch.name} yet` : 'No customers found')}
                </TableCell></TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* Pagination */}
      {total > LIMIT && (
        <div className="flex items-center justify-between">
          <p className="text-sm text-slate-500">Showing {page * LIMIT + 1} - {Math.min((page + 1) * LIMIT, total)} of {total}</p>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" disabled={page === 0} onClick={() => setPage(p => p - 1)}>Previous</Button>
            <Button variant="outline" size="sm" disabled={(page + 1) * LIMIT >= total} onClick={() => setPage(p => p + 1)}>Next</Button>
          </div>
        </div>
      )}

      {/* Create/Edit Dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-md max-h-[90dvh] overflow-y-auto">
          <DialogHeader><DialogTitle style={{ fontFamily: 'Manrope' }}>{editing ? 'Edit Customer' : 'New Customer'}</DialogTitle></DialogHeader>
          <div className="space-y-4 mt-2">
            <div>
              <Label>Customer Name</Label>
              <Input data-testid="customer-name-input" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label>Email</Label>
                <Input value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} />
              </div>
            </div>
            {/* Multi-phone number list */}
            <div>
              <div className="flex items-center justify-between mb-1">
                <Label>Phone Numbers</Label>
                <button type="button"
                  onClick={() => setForm({ ...form, phones: [...(form.phones || ['']), ''] })}
                  className="text-xs text-[#1A4D2E] hover:underline flex items-center gap-0.5">
                  + Add number
                </button>
              </div>
              <div className="space-y-2">
                {(form.phones || ['']).map((ph, i) => (
                  <div key={i} className="flex gap-2 items-center">
                    <Input
                      data-testid={`customer-phone-input-${i}`}
                      value={ph}
                      onChange={e => {
                        const updated = [...(form.phones || [''])];
                        updated[i] = e.target.value;
                        setForm({ ...form, phones: updated });
                      }}
                      placeholder={i === 0 ? 'Primary phone' : `Phone ${i + 1}`}
                      className="flex-1"
                    />
                    {i === 0 && form.phones?.length === 1 ? null : (
                      <button type="button"
                        onClick={() => {
                          const updated = (form.phones || ['']).filter((_, idx) => idx !== i);
                          setForm({ ...form, phones: updated.length ? updated : [''] });
                        }}
                        className="text-slate-400 hover:text-red-500 p-1">
                        <X size={14} />
                      </button>
                    )}
                    {i === 0 && <span className="text-[9px] text-slate-400 shrink-0">Primary</span>}
                  </div>
                ))}
              </div>
            </div>
            <div><Label>Address</Label><Input value={form.address} onChange={e => setForm({ ...form, address: e.target.value })} /></div>
            <div className="grid grid-cols-4 gap-4">
              <div>
                <Label>Price Scheme</Label>
                <Select value={form.price_scheme} onValueChange={v => setForm({ ...form, price_scheme: v })}>
                  <SelectTrigger data-testid="customer-scheme-select"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {schemes.map(s => <SelectItem key={s.id} value={s.key}>{s.name}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label>Credit Limit</Label>
                <CalcInput data-testid="customer-credit-input" value={form.credit_limit}
 onChange={(v) => setForm({ ...form, credit_limit: parseFloat(v) || 0 })}
 disabled={!canManageCredit}
 className={!canManageCredit ? 'bg-slate-100 cursor-not-allowed opacity-60' : ''}
 title={!canManageCredit ? 'No permission to manage credit' : ''} />
              </div>
              <div>
                <Label>Interest (%/mo)</Label>
                <CalcInput value={form.interest_rate}
 onChange={(v) => setForm({ ...form, interest_rate: parseFloat(v) || 0 })}
 disabled={!canManageCredit}
 className={!canManageCredit ? 'bg-slate-100 cursor-not-allowed opacity-60' : ''}
 title={!canManageCredit ? 'No permission to manage credit' : ''} />
              </div>
              <div>
                <Label>Grace Period (days)</Label>
                <CalcInput value={form.grace_period}
 onChange={(v) => setForm({ ...form, grace_period: parseInt(v) || 7 })} placeholder="7"
 disabled={!canManageCredit}
 className={!canManageCredit ? 'bg-slate-100 cursor-not-allowed opacity-60' : ''}
 title={!canManageCredit ? 'No permission to manage credit' : ''} />
              </div>
            </div>

            {/* Migration helper — Starting Balance (create-only) */}
            {!editing && (
              <div className="rounded-lg border border-amber-200 bg-amber-50/60 p-3 space-y-2" data-testid="opening-balance-section">
                <div className="flex items-start gap-2">
                  <FileText size={14} className="text-amber-600 mt-0.5 shrink-0" />
                  <div className="text-xs text-amber-800">
                    <p className="font-semibold">Starting Balance (optional)</p>
                    <p className="text-[11px] text-amber-700 mt-0.5">
                      For migrating from another system. Generates a one-time receipt
                      flagged as <em>Opening Balance Carry-forward</em> so it appears
                      in AR aging without inflating sales reports.
                    </p>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <Label className="text-[11px]">Amount Owed (₱)</Label>
                    <CalcInput
                      data-testid="customer-opening-balance-input"
                      value={form.opening_balance}
                      onChange={(v) => setForm({ ...form, opening_balance: v })}
                      placeholder="0.00"
                    />
                  </div>
                  <div>
                    <Label className="text-[11px]">As Of Date</Label>
                    <Input
                      type="date"
                      data-testid="customer-opening-balance-date-input"
                      value={form.opening_balance_date}
                      onChange={(e) => setForm({ ...form, opening_balance_date: e.target.value })}
                    />
                  </div>
                </div>
              </div>
            )}

            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setDialogOpen(false)}>Cancel</Button>
              <Button data-testid="save-customer-btn" onClick={handleSave} className="bg-[#1A4D2E] hover:bg-[#14532d] text-white">Save</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Transaction History Dialog */}
      <Dialog open={historyDialog} onOpenChange={setHistoryDialog}>
        <DialogContent className="sm:max-w-4xl max-h-[85vh] flex flex-col">
          <DialogHeader>
            <DialogTitle style={{ fontFamily: 'Manrope' }} className="flex items-center gap-2">
              <Users size={20} />
              {selectedCustomer?.name} - Account History
            </DialogTitle>
          </DialogHeader>
          
          {loadingHistory ? (
            <div className="flex items-center justify-center py-12">
              <div className="text-slate-400">Loading transactions...</div>
            </div>
          ) : transactions ? (
            <div className="flex-1 overflow-hidden flex flex-col">
              {/* Summary Cards */}
              <div className="grid grid-cols-4 gap-3 mb-4">
                <Card className="border-slate-200">
                  <CardContent className="p-3">
                    <p className="text-xs text-slate-500">Total Invoiced</p>
                    <p className="text-lg font-bold">{formatPHP(transactions.summary.total_invoiced)}</p>
                  </CardContent>
                </Card>
                <Card className="border-slate-200">
                  <CardContent className="p-3">
                    <p className="text-xs text-slate-500">Total Paid</p>
                    <p className="text-lg font-bold text-emerald-600">{formatPHP(transactions.summary.total_paid)}</p>
                  </CardContent>
                </Card>
                <Card className="border-slate-200">
                  <CardContent className="p-3">
                    <p className="text-xs text-slate-500">Balance Due</p>
                    <p className="text-lg font-bold text-red-600">{formatPHP(transactions.summary.total_balance)}</p>
                  </CardContent>
                </Card>
                <Card className="border-slate-200">
                  <CardContent className="p-3">
                    <p className="text-xs text-slate-500">Open Invoices</p>
                    <p className="text-lg font-bold">{transactions.summary.open_invoices}</p>
                  </CardContent>
                </Card>
              </div>

              {/* Transactions Table */}
              <Tabs defaultValue="invoices" className="flex-1 flex flex-col overflow-hidden">
                <TabsList>
                  <TabsTrigger value="invoices">Invoices ({transactions.invoices.length})</TabsTrigger>
                  {transactions.receivables.length > 0 && (
                    <TabsTrigger value="receivables">Legacy AR ({transactions.receivables.length})</TabsTrigger>
                  )}
                </TabsList>
                
                <TabsContent value="invoices" className="flex-1 overflow-hidden mt-3">
                  <ScrollArea className="h-[350px]">
                    <Table>
                      <TableHeader>
                        <TableRow className="bg-slate-50">
                          <TableHead className="text-xs">Invoice #</TableHead>
                          <TableHead className="text-xs">Date</TableHead>
                          <TableHead className="text-xs text-right">Total</TableHead>
                          <TableHead className="text-xs text-right">Paid</TableHead>
                          <TableHead className="text-xs text-right">Balance</TableHead>
                          <TableHead className="text-xs">Status</TableHead>
                          <TableHead className="text-xs">Type</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {transactions.invoices.map(inv => (
                          <TableRow key={inv.id}>
                            <TableCell className="font-medium text-sm">
                              <button className="font-mono text-blue-600 hover:underline" data-testid={`inv-link-${inv.id}`}
                                onClick={(e) => { e.stopPropagation(); openDetailModal(inv.invoice_number); }}>
                                {inv.invoice_number}
                              </button>
                            </TableCell>
                            <TableCell className="text-sm text-slate-500">{inv.order_date}</TableCell>
                            <TableCell className="text-sm text-right">{formatPHP(inv.grand_total)}</TableCell>
                            <TableCell className="text-sm text-right text-emerald-600">{formatPHP(inv.amount_paid)}</TableCell>
                            <TableCell className="text-sm text-right text-red-600 font-medium">{formatPHP(inv.balance)}</TableCell>
                            <TableCell>{getStatusBadge(inv.status)}</TableCell>
                            <TableCell>
                              {getSaleTypeBadge(inv)}
                            </TableCell>
                          </TableRow>
                        ))}
                        {transactions.invoices.length === 0 && (
                          <TableRow><TableCell colSpan={7} className="text-center py-8 text-slate-400">No invoices</TableCell></TableRow>
                        )}
                      </TableBody>
                    </Table>
                  </ScrollArea>
                </TabsContent>

                {transactions.receivables.length > 0 && (
                  <TabsContent value="receivables" className="flex-1 overflow-hidden mt-3">
                    <ScrollArea className="h-[350px]">
                      <Table>
                        <TableHeader>
                          <TableRow className="bg-slate-50">
                            <TableHead className="text-xs">Reference</TableHead>
                            <TableHead className="text-xs">Date</TableHead>
                            <TableHead className="text-xs text-right">Amount</TableHead>
                            <TableHead className="text-xs text-right">Paid</TableHead>
                            <TableHead className="text-xs text-right">Balance</TableHead>
                            <TableHead className="text-xs">Status</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {transactions.receivables.map(rec => (
                            <TableRow key={rec.id}>
                              <TableCell className="font-medium text-sm">{rec.sale_id?.slice(0, 8) || '—'}</TableCell>
                              <TableCell className="text-sm text-slate-500">{fmtDate(rec.created_at)}</TableCell>
                              <TableCell className="text-sm text-right">{formatPHP(rec.amount)}</TableCell>
                              <TableCell className="text-sm text-right text-emerald-600">{formatPHP(rec.paid)}</TableCell>
                              <TableCell className="text-sm text-right text-red-600 font-medium">{formatPHP(rec.balance)}</TableCell>
                              <TableCell>{getStatusBadge(rec.status)}</TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </ScrollArea>
                  </TabsContent>
                )}
              </Tabs>
            </div>
          ) : (
            <div className="text-center py-8 text-slate-400">No transaction data available</div>
          )}
        </DialogContent>
      </Dialog>

      <CustomerStatementModal
        open={statementDialog}
        onOpenChange={setStatementDialog}
        customer={statementCustomer}
      />
      <InvoiceDetailModal compact
        open={invoiceModalOpen}
        onOpenChange={setInvoiceModalOpen}
        invoiceNumber={selectedInvoiceNumber}
      />

      {/* Bulk Delete Dialog — PIN-gated */}
      <Dialog open={bulkDialogOpen} onOpenChange={(v) => { setBulkDialogOpen(v); if (!v) setBulkResult(null); }}>
        <DialogContent className="sm:max-w-lg" data-testid="bulk-delete-dialog">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-red-700" style={{ fontFamily: 'Manrope' }}>
              <AlertTriangle size={18} /> Delete {selected.size} Customer{selected.size === 1 ? '' : 's'}?
            </DialogTitle>
          </DialogHeader>

          {!bulkResult ? (
            <div className="space-y-4 mt-2">
              <div className="bg-red-50 border border-red-200 rounded p-3 text-xs text-red-800">
                <p className="font-semibold mb-1">This action cannot be easily undone.</p>
                <ul className="list-disc ml-4 space-y-0.5">
                  <li>By default, only customers with <strong>zero balance and no open invoices</strong> will be deleted. Others will be reported as blocked.</li>
                  <li>Customers are soft-deleted (hidden, not hard-purged).</li>
                  <li>PIN is required: owner / admin / manager / auditor PIN or TOTP.</li>
                </ul>
              </div>

              <div>
                <Label htmlFor="bulk-delete-pin">Enter PIN</Label>
                <Input
                  id="bulk-delete-pin"
                  data-testid="bulk-delete-pin-input"
                  type="password"
                  value={bulkPin}
                  onChange={e => setBulkPin(e.target.value)}
                  autoFocus
                  placeholder="Admin / Manager / TOTP"
                  autoComplete="one-time-code"
                />
              </div>

              {isAdminOrOwner && (
                <label className="flex items-start gap-2 text-xs text-slate-600 cursor-pointer">
                  <input
                    type="checkbox"
                    data-testid="bulk-delete-force-toggle"
                    checked={bulkForce}
                    onChange={e => setBulkForce(e.target.checked)}
                    className="w-4 h-4 accent-red-600 mt-0.5"
                  />
                  <span>
                    <strong className="text-red-700">Force delete</strong> — override balance & open-invoice guard.
                    Orphans any remaining invoices/receivables. Admin/owner only.
                  </span>
                </label>
              )}

              <div className="flex justify-end gap-2 pt-2 border-t">
                <Button variant="outline" onClick={() => setBulkDialogOpen(false)} disabled={bulkSubmitting}>
                  Cancel
                </Button>
                <Button
                  onClick={submitBulkDelete}
                  disabled={bulkSubmitting || !bulkPin.trim()}
                  className="bg-red-600 hover:bg-red-700 text-white"
                  data-testid="bulk-delete-confirm-btn"
                >
                  {bulkSubmitting ? 'Deleting…' : `Delete ${selected.size}`}
                </Button>
              </div>
            </div>
          ) : (
            <div className="space-y-3 mt-2" data-testid="bulk-delete-result">
              <div className="grid grid-cols-2 gap-3">
                <div className="bg-emerald-50 border border-emerald-200 rounded p-3 text-center">
                  <p className="text-2xl font-bold text-emerald-700">{bulkResult.deleted_count}</p>
                  <p className="text-xs text-emerald-800 mt-0.5">Deleted</p>
                </div>
                <div className="bg-amber-50 border border-amber-200 rounded p-3 text-center">
                  <p className="text-2xl font-bold text-amber-700">{bulkResult.blocked_count}</p>
                  <p className="text-xs text-amber-800 mt-0.5">Blocked</p>
                </div>
              </div>
              {bulkResult.verified_by && (
                <p className="text-[11px] text-slate-500 text-center">Verified by: <strong>{bulkResult.verified_by}</strong></p>
              )}

              {bulkResult.blocked?.length > 0 && (
                <ScrollArea className="max-h-48 border rounded">
                  <Table>
                    <TableHeader>
                      <TableRow className="bg-slate-50">
                        <TableHead className="text-xs">Blocked</TableHead>
                        <TableHead className="text-xs">Reason</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {bulkResult.blocked.map(b => (
                        <TableRow key={b.id}>
                          <TableCell className="text-xs">{b.name || b.id}</TableCell>
                          <TableCell className="text-xs text-slate-500">
                            {b.reason === 'has_balance' ? `Balance ₱${Number(b.balance || 0).toFixed(2)}` :
                             b.reason === 'has_open_invoices' ? `${b.open_invoices} open invoice(s)` :
                             b.reason?.replace(/_/g, ' ')}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </ScrollArea>
              )}

              <div className="flex justify-end pt-2">
                <Button onClick={() => setBulkDialogOpen(false)} className="bg-[#1A4D2E] hover:bg-[#14532d] text-white">
                  Done
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* ── Customer Payment History Dialog ── */}
      <Dialog open={payHistoryDialog} onOpenChange={setPayHistoryDialog}>
        <DialogContent className="sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle style={{ fontFamily: 'Manrope' }} className="flex items-center gap-2">
              <Clock size={16} className="text-indigo-600" /> Payment History — {payHistoryCustomer?.name}
            </DialogTitle>
          </DialogHeader>
          {payHistoryLoading ? (
            <div className="py-8 text-center text-slate-400">Loading…</div>
          ) : (
            <ScrollArea className="max-h-[460px]">
              <Table>
                <TableHeader>
                  <TableRow className="bg-slate-50">
                    <TableHead className="text-xs">Date</TableHead>
                    <TableHead className="text-xs">Invoice #</TableHead>
                    <TableHead className="text-xs">Type</TableHead>
                    <TableHead className="text-xs">Method</TableHead>
                    <TableHead className="text-xs">Reference</TableHead>
                    <TableHead className="text-xs text-right">Amount</TableHead>
                    <TableHead className="text-xs">Recorded By</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {payHistory.length === 0 && (
                    <TableRow><TableCell colSpan={7} className="text-center py-6 text-slate-400">No payment history found</TableCell></TableRow>
                  )}
                  {payHistory.map((p, i) => {
                    const isDiscount = p.method === 'Discount';
                    const saleTypeMap = { penalty_charge: { label: 'Penalty', cls: 'bg-red-100 text-red-700' }, interest_charge: { label: 'Interest', cls: 'bg-amber-100 text-amber-700' } };
                    const tc = saleTypeMap[p.sale_type] || { label: 'Invoice', cls: 'bg-slate-100 text-slate-700' };
                    return (
                      <TableRow key={i} className={`${p.voided ? 'opacity-40 line-through' : ''} ${isDiscount ? 'bg-blue-50/40' : ''}`}
                        data-testid={`cust-pay-hist-${i}`}>
                        <TableCell className="text-xs">{p.date}</TableCell>
                        <TableCell className="font-mono text-xs">{p.invoice_number}</TableCell>
                        <TableCell><Badge variant="outline" className={`text-[9px] ${tc.cls}`}>{tc.label}</Badge></TableCell>
                        <TableCell className="text-xs">
                          {isDiscount ? <Badge className="text-[9px] bg-blue-100 text-blue-700">Discount</Badge> : p.method}
                        </TableCell>
                        <TableCell className="text-xs text-slate-400">{p.reference || '—'}</TableCell>
                        <TableCell className={`text-right font-medium text-sm ${isDiscount ? 'text-blue-600' : ''}`}>{formatPHP(p.amount)}</TableCell>
                        <TableCell className="text-xs text-slate-400">{p.recorded_by}</TableCell>
                      </TableRow>
                    );
                  })}
                  {payHistory.filter(p => !p.voided && p.method !== 'Discount').length > 0 && (
                    <TableRow className="bg-slate-50 font-semibold">
                      <TableCell colSpan={5} className="text-right text-xs text-slate-500">Total Received</TableCell>
                      <TableCell className="text-right">{formatPHP(payHistory.filter(p => !p.voided && p.method !== 'Discount').reduce((s, p) => s + (p.amount || 0), 0))}</TableCell>
                      <TableCell />
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </ScrollArea>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
