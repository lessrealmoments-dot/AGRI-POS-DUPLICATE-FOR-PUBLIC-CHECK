import { useState, useEffect, useCallback, useRef } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { api, useAuth } from '../contexts/AuthContext';
import { Input } from '../components/ui/input';
import { Button } from '../components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { Badge } from '../components/ui/badge';
import { Card } from '../components/ui/card';
import { Search, Filter, Calendar, X, FileText, Truck, Receipt, ArrowLeftRight, Wallet, ChevronRight, Loader2, RotateCcw, Building2, CreditCard } from 'lucide-react';
import ReviewDetailDialog from '../components/ReviewDetailDialog';
import InvoiceDetailModal from '../components/InvoiceDetailModal';
import ExpenseDetailModal from '../components/ExpenseDetailModal';

const TYPE_CONFIG = {
  invoice:          { label: 'Invoice / Sale', icon: FileText, color: 'bg-blue-100 text-blue-700 border-blue-200' },
  purchase_order:   { label: 'Purchase Order', icon: Truck,    color: 'bg-amber-100 text-amber-700 border-amber-200' },
  expense:          { label: 'Expense',        icon: Receipt,  color: 'bg-red-100 text-red-700 border-red-200' },
  internal_invoice: { label: 'Internal Invoice', icon: ArrowLeftRight, color: 'bg-purple-100 text-purple-700 border-purple-200' },
  fund_transfer:    { label: 'Fund Transfer',  icon: Wallet,   color: 'bg-emerald-100 text-emerald-700 border-emerald-200' },
  return:           { label: 'Return / Refund', icon: RotateCcw, color: 'bg-orange-100 text-orange-700 border-orange-200' },
  branch_transfer:  { label: 'Branch Transfer', icon: Building2, color: 'bg-indigo-100 text-indigo-700 border-indigo-200' },
  payable:          { label: 'Payable (AP)',    icon: CreditCard, color: 'bg-pink-100 text-pink-700 border-pink-200' },
};

function ResultRow({ item, branches, onClick }) {
  const cfg = TYPE_CONFIG[item.type] || TYPE_CONFIG.invoice;
  const Icon = cfg.icon;
  const branchName = branches.find(b => b.id === item.branch_id)?.name || '';

  return (
    <button
      data-testid={`search-result-${item.id}`}
      onClick={() => onClick(item)}
      className="w-full flex items-center gap-3 px-4 py-3 hover:bg-slate-50 border-b border-slate-100 transition-colors text-left group"
    >
      <div className={`w-9 h-9 rounded-lg flex items-center justify-center shrink-0 ${cfg.color.split(' ')[0]}`}>
        <Icon size={16} className={cfg.color.split(' ')[1]} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          {item.number && (
            <span className="font-mono text-sm font-semibold text-slate-800">{item.number}</span>
          )}
          <Badge variant="outline" className={`text-[10px] px-1.5 py-0 h-5 ${cfg.color}`}>
            {cfg.label}
          </Badge>
        </div>
        <p className="text-sm text-slate-600 truncate mt-0.5">{item.title}</p>
        <div className="flex items-center gap-3 mt-0.5 text-xs text-slate-400">
          {item.date && <span>{item.date}</span>}
          {branchName && <span>{branchName}</span>}
          {item.status && (
            <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-semibold capitalize ${
              item.status === 'for_preparation'
                ? 'bg-amber-100 text-amber-700 border border-amber-300'
                : item.status === 'voided' || item.status === 'cancelled_draft'
                  ? 'bg-red-100 text-red-600'
                  : 'text-slate-400'
            }`}>
              {item.status === 'for_preparation' ? 'FOR PREPARATION' : item.status}
            </span>
          )}
        </div>
      </div>
      <div className="text-right shrink-0">
        <p className="text-sm font-semibold text-slate-800">
          {typeof item.amount === 'number' ? `₱${item.amount.toLocaleString(undefined, { minimumFractionDigits: 2 })}` : ''}
        </p>
        {item.balance > 0 && (
          <p className="text-xs text-red-500">Bal: ₱{item.balance.toLocaleString(undefined, { minimumFractionDigits: 2 })}</p>
        )}
      </div>
      <ChevronRight size={16} className="text-slate-300 group-hover:text-slate-500 shrink-0" />
    </button>
  );
}

export default function TransactionSearchPage() {
  const { branches } = useAuth();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const inputRef = useRef(null);

  const [query, setQuery] = useState(searchParams.get('q') || '');
  const [type, setType] = useState(searchParams.get('type') || 'all');
  const [dateFrom, setDateFrom] = useState(searchParams.get('date_from') || '');
  const [dateTo, setDateTo] = useState(searchParams.get('date_to') || '');
  const [branchId, setBranchId] = useState(searchParams.get('branch_id') || '');
  const [results, setResults] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [showFilters, setShowFilters] = useState(false);
  const [searched, setSearched] = useState(false);

  // Detail modals — route by type
  const [detailModal, setDetailModal] = useState({ type: null, number: '', id: '' });

  const doSearch = useCallback(async (q, t, df, dt, bid) => {
    if (!q && !df && !dt) {
      setResults([]);
      setTotal(0);
      setSearched(false);
      return;
    }
    setLoading(true);
    setSearched(true);
    try {
      const params = { q, type: t, limit: 100 };
      if (df) params.date_from = df;
      if (dt) params.date_to = dt;
      if (bid) params.branch_id = bid;
      const res = await api.get('/search/transactions', { params });
      setResults(res.data.results || []);
      setTotal(res.data.total || 0);
    } catch {
      setResults([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, []);

  // Run search on mount if params exist
  useEffect(() => {
    const q = searchParams.get('q') || '';
    if (q) doSearch(q, type, dateFrom, dateTo, branchId);
    // Focus input
    setTimeout(() => inputRef.current?.focus(), 100);
    // eslint-disable-next-line
  }, []);

  const handleSearch = (e) => {
    e?.preventDefault();
    const params = {};
    if (query) params.q = query;
    if (type !== 'all') params.type = type;
    if (dateFrom) params.date_from = dateFrom;
    if (dateTo) params.date_to = dateTo;
    if (branchId) params.branch_id = branchId;
    setSearchParams(params);
    doSearch(query, type, dateFrom, dateTo, branchId);
  };

  const clearAll = () => {
    setQuery('');
    setType('all');
    setDateFrom('');
    setDateTo('');
    setBranchId('');
    setResults([]);
    setTotal(0);
    setSearched(false);
    setSearchParams({});
    inputRef.current?.focus();
  };

  const handleResultClick = (item) => {
    if (item.type === 'invoice' && item.number) {
      setDetailModal({ type: 'sale', number: item.number, id: '' });
    } else if (item.type === 'purchase_order' && item.number) {
      setDetailModal({ type: 'po', number: item.number, id: '' });
    } else if (item.type === 'expense' && item.id) {
      setDetailModal({ type: 'expense', number: '', id: item.id });
    }
    // Others: navigate to their native pages
    else if (item.type === 'return') navigate('/returns');
    else if (item.type === 'branch_transfer') navigate('/branch-transfers');
    else if (item.type === 'internal_invoice') navigate('/internal-invoices');
    else if (item.type === 'fund_transfer') navigate('/fund-management');
    else if (item.type === 'payable') navigate('/pay-supplier');
  };

  // Group results by type for summary
  const grouped = results.reduce((acc, r) => {
    acc[r.type] = (acc[r.type] || 0) + 1;
    return acc;
  }, {});

  return (
    <div className="p-4 lg:p-6 max-w-5xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-slate-800" style={{ fontFamily: 'Manrope' }}>
          Find Transaction
        </h1>
        <p className="text-sm text-slate-500 mt-1">
          Search across all invoices, purchase orders, expenses, and more
        </p>
      </div>

      {/* Search Bar */}
      <form onSubmit={handleSearch} className="mb-4">
        <div className="flex gap-2">
          <div className="relative flex-1">
            <Search size={18} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <Input
              ref={inputRef}
              data-testid="search-input"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search by receipt number, customer, vendor, description..."
              className="pl-10 h-11 text-base"
            />
            {query && (
              <button type="button" onClick={() => { setQuery(''); inputRef.current?.focus(); }}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600">
                <X size={16} />
              </button>
            )}
          </div>
          <Button data-testid="search-submit-btn" type="submit" className="h-11 px-6 bg-[#1A4D2E] hover:bg-[#154025]">
            {loading ? <Loader2 size={18} className="animate-spin" /> : <Search size={18} />}
          </Button>
          <Button
            type="button"
            variant="outline"
            data-testid="search-filter-toggle"
            onClick={() => setShowFilters(!showFilters)}
            className={`h-11 ${showFilters ? 'bg-slate-100' : ''}`}
          >
            <Filter size={16} />
          </Button>
        </div>
      </form>

      {/* Filters */}
      {showFilters && (
        <Card className="p-4 mb-4" data-testid="search-filters-panel">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            <div>
              <label className="text-xs font-medium text-slate-500 mb-1 block">Type</label>
              <Select value={type} onValueChange={setType}>
                <SelectTrigger data-testid="search-type-filter" className="h-9">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Types</SelectItem>
                  <SelectItem value="invoice">Invoices / Sales</SelectItem>
                  <SelectItem value="po">Purchase Orders</SelectItem>
                  <SelectItem value="expense">Expenses</SelectItem>
                  <SelectItem value="return">Returns / Refunds</SelectItem>
                  <SelectItem value="internal_invoice">Internal Invoices</SelectItem>
                  <SelectItem value="branch_transfer">Branch Transfers</SelectItem>
                  <SelectItem value="fund_transfer">Fund Transfers</SelectItem>
                  <SelectItem value="payable">Payables (AP)</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500 mb-1 block">From Date</label>
              <Input data-testid="search-date-from" type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="h-9" />
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500 mb-1 block">To Date</label>
              <Input data-testid="search-date-to" type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="h-9" />
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500 mb-1 block">Branch</label>
              <Select value={branchId || 'all'} onValueChange={(v) => setBranchId(v === 'all' ? '' : v)}>
                <SelectTrigger data-testid="search-branch-filter" className="h-9">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Branches</SelectItem>
                  {branches.map(b => (
                    <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="flex justify-end mt-3 gap-2">
            <Button variant="ghost" size="sm" onClick={clearAll}>Clear All</Button>
            <Button size="sm" onClick={handleSearch} className="bg-[#1A4D2E] hover:bg-[#15402]">Apply Filters</Button>
          </div>
        </Card>
      )}

      {/* Results Summary */}
      {searched && !loading && (
        <div className="flex items-center gap-2 mb-3 flex-wrap" data-testid="search-results-summary">
          <span className="text-sm text-slate-500">
            {total === 0 ? 'No results found' : `${total} result${total !== 1 ? 's' : ''}`}
          </span>
          {Object.entries(grouped).map(([t, count]) => {
            const cfg = TYPE_CONFIG[t];
            return cfg ? (
              <Badge key={t} variant="outline" className={`text-[10px] ${cfg.color}`}>
                {cfg.label}: {count}
              </Badge>
            ) : null;
          })}
        </div>
      )}

      {/* Results List */}
      {loading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 size={32} className="animate-spin text-slate-400" />
        </div>
      ) : results.length > 0 ? (
        <Card className="overflow-hidden" data-testid="search-results-list">
          {results.map((item) => (
            <ResultRow key={`${item.type}-${item.id}`} item={item} branches={branches} onClick={handleResultClick} />
          ))}
        </Card>
      ) : searched ? (
        <div className="text-center py-16" data-testid="search-no-results">
          <Search size={48} className="mx-auto text-slate-300 mb-4" />
          <p className="text-slate-500 text-lg font-medium">No transactions found</p>
          <p className="text-slate-400 text-sm mt-1">Try a different search term or adjust your filters</p>
        </div>
      ) : (
        <div className="text-center py-16" data-testid="search-empty-state">
          <Search size={48} className="mx-auto text-slate-300 mb-4" />
          <p className="text-slate-500 text-lg font-medium">Search for any transaction</p>
          <p className="text-slate-400 text-sm mt-1">
            Enter a receipt number, customer name, vendor, amount, or description
          </p>
          <div className="flex flex-wrap justify-center gap-2 mt-4">
            {['SI-MN-001042', 'Walk-in', 'PO-', 'GCash'].map(hint => (
              <button key={hint} onClick={() => { setQuery(hint); inputRef.current?.focus(); }}
                className="text-xs bg-slate-100 text-slate-600 px-3 py-1.5 rounded-full hover:bg-slate-200 transition-colors">
                {hint}
              </button>
            ))}
          </div>
        </div>
      )}

      <ReviewDetailDialog
        open={detailModal.type === 'po'}
        onOpenChange={(open) => { if (!open) setDetailModal({ type: null, number: '', id: '' }); }}
        poId={detailModal.id}
        poNumber={detailModal.number}
        onUpdated={() => doSearch(query, type, dateFrom, dateTo, branchId)}
        showReviewAction={false}
        showPayAction={false}
      />
      <InvoiceDetailModal compact
        open={detailModal.type === 'sale'}
        onOpenChange={(open) => { if (!open) setDetailModal({ type: null, number: '', id: '' }); }}
        invoiceNumber={detailModal.number}
        onUpdated={() => doSearch(query, type, dateFrom, dateTo, branchId)}
      />
      <ExpenseDetailModal
        open={detailModal.type === 'expense'}
        onOpenChange={(open) => { if (!open) setDetailModal({ type: null, number: '', id: '' }); }}
        expenseId={detailModal.id}
        onUpdated={() => doSearch(query, type, dateFrom, dateTo, branchId)}
      />
    </div>
  );
}
