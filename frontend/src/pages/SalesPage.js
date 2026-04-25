import { useState, useEffect, useCallback } from 'react';
import { useAuth, api } from '../contexts/AuthContext';
import { formatPHP } from '../lib/utils';
import PrintEngine from '../lib/PrintEngine';
import { Card, CardContent } from '../components/ui/card';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '../components/ui/dropdown-menu';
import { Edit3, Search, ArrowUpDown, ArrowUp, ArrowDown, RefreshCw, ChevronLeft, ChevronRight, MoreHorizontal, Printer, FileText, Ban, DollarSign } from 'lucide-react';
import { toast } from 'sonner';
import InvoiceDetailModal from '../components/InvoiceDetailModal';
import CustomerStatementModal from '../components/CustomerStatementModal';
import CustomerBalanceBadge, { useCustomerBalances } from '../components/CustomerBalanceBadge';

const STATUS_FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'paid', label: 'Paid' },
  { key: 'partial', label: 'Partial' },
  { key: 'open', label: 'Credit' },
  { key: 'voided', label: 'Voided' },
];

const SORT_OPTIONS = [
  { key: 'created_at', label: 'Date' },
  { key: 'customer', label: 'Customer' },
  { key: 'amount', label: 'Amount' },
  { key: 'number', label: 'SO #' },
];

export default function SalesPage() {
  const { currentBranch } = useAuth();
  const [sales, setSales] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [sortBy, setSortBy] = useState('created_at');
  const [sortDir, setSortDir] = useState('desc');
  const [loading, setLoading] = useState(false);
  const LIMIT = 50;

  const [invoiceModalOpen, setInvoiceModalOpen] = useState(false);
  const [selectedInvoiceId, setSelectedInvoiceId] = useState(null);
  const [businessInfo, setBusinessInfo] = useState({});
  const [stmtOpen, setStmtOpen] = useState(false);
  const [stmtCustomer, setStmtCustomer] = useState(null);
  const { byId: balanceById } = useCustomerBalances();

  // Load business info for printing
  useEffect(() => {
    api.get('/settings/business-info').then(r => setBusinessInfo(r.data)).catch(() => {});
  }, []);

  const fetchSales = useCallback(async () => {
    setLoading(true);
    try {
      const params = {
        skip: page * LIMIT,
        limit: LIMIT,
        sort_by: sortBy,
        sort_dir: sortDir,
      };
      if (currentBranch) params.branch_id = currentBranch.id;
      if (search) params.search = search;
      if (statusFilter !== 'all') {
        if (statusFilter === 'voided') {
          params.status = 'voided';
          params.include_voided = true;
        } else {
          params.status = statusFilter;
        }
      }
      const res = await api.get('/invoices', { params });
      setSales(res.data.invoices || []);
      setTotal(res.data.total || 0);
    } catch { toast.error('Failed to load sales'); }
    setLoading(false);
  }, [currentBranch, page, search, statusFilter, sortBy, sortDir]);

  useEffect(() => { fetchSales(); }, [fetchSales]);

  // Reset to page 0 on filter/search change
  useEffect(() => { setPage(0); }, [search, statusFilter, sortBy, sortDir]);

  const handleSort = (col) => {
    if (sortBy === col) {
      setSortDir(d => d === 'desc' ? 'asc' : 'desc');
    } else {
      setSortBy(col);
      setSortDir('desc');
    }
  };

  const handleSearch = (e) => {
    e.preventDefault();
    setSearch(searchInput.trim());
  };

  const openInvoiceDetail = (sale) => {
    setSelectedInvoiceId(sale.id);
    setInvoiceModalOpen(true);
  };

  const handlePrint = async (sale, format) => {
    const docType = PrintEngine.getDocType(sale);
    let docCode = sale.doc_code || '';
    if (!docCode && sale.id) {
      try {
        const res = await api.post('/doc/generate-code', { doc_type: 'invoice', doc_id: sale.id });
        docCode = res.data?.code || '';
      } catch { /* print without QR */ }
    }
    PrintEngine.print({ type: docType, data: sale, format, businessInfo, docCode });
  };

  const totalPages = Math.ceil(total / LIMIT);

  const SortIcon = ({ col }) => {
    if (sortBy !== col) return <ArrowUpDown size={11} className="text-slate-300" />;
    return sortDir === 'desc'
      ? <ArrowDown size={11} className="text-[#1A4D2E]" />
      : <ArrowUp size={11} className="text-[#1A4D2E]" />;
  };

  const statusBadge = (s) => {
    const map = {
      paid: 'bg-emerald-100 text-emerald-700',
      partial: 'bg-amber-100 text-amber-700',
      open: 'bg-blue-100 text-blue-700',
      overdue: 'bg-red-100 text-red-700',
      voided: 'bg-red-100 text-red-600',
    };
    return map[s] || 'bg-slate-100 text-slate-600';
  };

  const paymentBadge = (method) => {
    if (!method) return '';
    const m = method.toLowerCase();
    if (m === 'cash') return 'bg-emerald-50 text-emerald-700 border-emerald-200';
    if (m === 'gcash' || m === 'maya') return 'bg-blue-50 text-blue-700 border-blue-200';
    if (m === 'credit') return 'bg-amber-50 text-amber-700 border-amber-200';
    return 'bg-slate-50 text-slate-600 border-slate-200';
  };

  return (
    <div className="space-y-5 animate-fadeIn" data-testid="sales-page">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight" style={{ fontFamily: 'Manrope' }}>Sales History</h1>
          <p className="text-sm text-slate-500 mt-0.5">{total} transaction{total !== 1 ? 's' : ''}</p>
        </div>
        <Button variant="outline" size="sm" onClick={fetchSales} disabled={loading} className="h-8" data-testid="refresh-sales">
          <RefreshCw size={13} className={loading ? 'animate-spin mr-1.5' : 'mr-1.5'} /> Refresh
        </Button>
      </div>

      {/* Search + Filters */}
      <div className="flex flex-col sm:flex-row gap-3 items-start sm:items-center">
        <form onSubmit={handleSearch} className="relative flex-1 max-w-md">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
          <Input
            data-testid="sales-search"
            className="h-9 pl-8 pr-16"
            placeholder="Search by invoice # or customer..."
            value={searchInput}
            onChange={e => setSearchInput(e.target.value)}
          />
          {searchInput && (
            <button type="button" onClick={() => { setSearchInput(''); setSearch(''); }}
              className="absolute right-12 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 text-xs">
              Clear
            </button>
          )}
          <Button type="submit" size="sm" variant="ghost" className="absolute right-0.5 top-0.5 h-8 px-2">
            <Search size={13} />
          </Button>
        </form>

        <div className="flex gap-1.5 flex-wrap" data-testid="sales-filters">
          {STATUS_FILTERS.map(f => (
            <button
              key={f.key}
              onClick={() => setStatusFilter(f.key)}
              data-testid={`filter-${f.key}`}
              className={`px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${
                statusFilter === f.key
                  ? 'bg-[#1A4D2E] text-white'
                  : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {/* Table */}
      <Card className="border-slate-200">
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow className="bg-slate-50">
                  <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">
                    <button onClick={() => handleSort('number')} className="flex items-center gap-1 hover:text-slate-700" data-testid="sort-number">
                      Sale # <SortIcon col="number" />
                    </button>
                  </TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">
                    <button onClick={() => handleSort('customer')} className="flex items-center gap-1 hover:text-slate-700" data-testid="sort-customer">
                      Customer <SortIcon col="customer" />
                    </button>
                  </TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">Items</TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium text-right">
                    <button onClick={() => handleSort('amount')} className="flex items-center gap-1 hover:text-slate-700 ml-auto" data-testid="sort-amount">
                      Total <SortIcon col="amount" />
                    </button>
                  </TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">Payment</TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">Cashier</TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">Status</TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">
                    <button onClick={() => handleSort('created_at')} className="flex items-center gap-1 hover:text-slate-700" data-testid="sort-date">
                      Date <SortIcon col="created_at" />
                    </button>
                  </TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium w-10"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sales.map(s => (
                  <TableRow key={s.id} className="table-row-hover">
                    <TableCell className="cursor-pointer" onClick={() => openInvoiceDetail(s)}>
                      <span className="font-mono text-xs text-blue-600 hover:text-blue-800 hover:underline flex items-center gap-1">
                        {s.invoice_number}
                        {s.edited && <Edit3 size={10} className="text-orange-500" />}
                      </span>
                    </TableCell>
                    <TableCell className="font-medium text-sm max-w-[220px] truncate">
                      {s.customer_id ? (
                        <div className="flex items-center gap-1.5 truncate">
                          <button
                            className="text-left hover:text-[#1A4D2E] hover:underline truncate"
                            onClick={(e) => { e.stopPropagation(); setStmtCustomer({ id: s.customer_id, name: s.customer_name, phone: s.customer_phone, address: s.customer_address }); setStmtOpen(true); }}
                            data-testid={`customer-link-${s.id}`}
                            title={`View statement of account — ${s.customer_name}`}
                          >
                            {s.customer_name || 'Walk-in'}
                          </button>
                          <CustomerBalanceBadge customerId={s.customer_id} byId={balanceById} />
                        </div>
                      ) : (
                        <span className="text-slate-500">{s.customer_name || 'Walk-in'}</span>
                      )}
                    </TableCell>
                    <TableCell className="text-slate-500 text-xs cursor-pointer" onClick={() => openInvoiceDetail(s)}>{s.items?.length || 0}</TableCell>
                    <TableCell className="text-right font-semibold font-mono text-sm cursor-pointer" onClick={() => openInvoiceDetail(s)}>{formatPHP(s.grand_total)}</TableCell>
                    <TableCell>
                      <Badge variant="outline" className={`text-[10px] ${paymentBadge(s.payment_method)}`}>
                        {s.payment_type === 'split' ? 'Split' : (s.payment_method || 'Cash')}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-slate-500 text-xs">{s.cashier_name}</TableCell>
                    <TableCell>
                      <Badge className={`text-[10px] ${statusBadge(s.status)}`}>{s.status}</Badge>
                      {s.balance > 0 && s.status !== 'voided' && (
                        <span className="block text-[9px] text-red-600 font-mono mt-0.5">Bal: {formatPHP(s.balance)}</span>
                      )}
                      {s.release_mode === 'partial' && s.stock_release_status !== 'na' && (
                        <span className={`block text-[9px] font-medium mt-0.5 ${
                          s.stock_release_status === 'fully_released' ? 'text-emerald-600' :
                          s.stock_release_status === 'partially_released' ? 'text-blue-600' :
                          'text-amber-600'
                        }`}>
                          {s.stock_release_status === 'fully_released' ? 'Released' :
                           s.stock_release_status === 'partially_released' ? 'Part. Released' :
                           'Unreleased'}
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="text-xs text-slate-500 whitespace-nowrap">
                      {s.order_date || (s.created_at ? new Date(s.created_at).toLocaleDateString() : '')}
                    </TableCell>
                    <TableCell>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="ghost" size="sm" className="h-7 w-7 p-0" data-testid={`quick-action-${s.id}`}
                            onClick={e => e.stopPropagation()}>
                            <MoreHorizontal size={14} />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end" className="w-48">
                          <DropdownMenuItem onClick={() => openInvoiceDetail(s)} data-testid={`action-view-${s.id}`}>
                            <FileText size={13} className="mr-2 text-slate-500" /> View Details
                          </DropdownMenuItem>
                          <DropdownMenuSeparator />
                          <DropdownMenuItem onClick={() => handlePrint(s, 'thermal')} data-testid={`action-print-thermal-${s.id}`}>
                            <Printer size={13} className="mr-2 text-slate-500" /> Print Receipt (58mm)
                          </DropdownMenuItem>
                          <DropdownMenuItem onClick={() => handlePrint(s, 'full_page')} data-testid={`action-print-full-${s.id}`}>
                            <Printer size={13} className="mr-2 text-slate-500" /> Print Full Page (8.5x11)
                          </DropdownMenuItem>
                          {s.status !== 'voided' && s.balance > 0 && (
                            <>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem onClick={() => openInvoiceDetail(s)} data-testid={`action-pay-${s.id}`}>
                                <DollarSign size={13} className="mr-2 text-emerald-500" /> Add Payment
                              </DropdownMenuItem>
                            </>
                          )}
                          {s.status === 'voided' && (
                            <>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem disabled className="text-red-400">
                                <Ban size={13} className="mr-2" /> Voided
                              </DropdownMenuItem>
                            </>
                          )}
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </TableCell>
                  </TableRow>
                ))}
                {!sales.length && !loading && (
                  <TableRow><TableCell colSpan={9} className="text-center py-12 text-slate-400">No sales found</TableCell></TableRow>
                )}
                {loading && (
                  <TableRow><TableCell colSpan={9} className="text-center py-12">
                    <RefreshCw size={18} className="animate-spin mx-auto text-slate-400" />
                  </TableCell></TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <p className="text-xs text-slate-500">
            Showing {page * LIMIT + 1}–{Math.min((page + 1) * LIMIT, total)} of {total}
          </p>
          <div className="flex items-center gap-1">
            <Button variant="outline" size="sm" disabled={page === 0} onClick={() => setPage(0)} className="h-8 px-2">
              First
            </Button>
            <Button variant="outline" size="sm" disabled={page === 0} onClick={() => setPage(p => p - 1)} className="h-8 px-2" data-testid="prev-page">
              <ChevronLeft size={14} />
            </Button>
            <span className="text-xs text-slate-600 px-2 font-medium">Page {page + 1} / {totalPages}</span>
            <Button variant="outline" size="sm" disabled={page >= totalPages - 1} onClick={() => setPage(p => p + 1)} className="h-8 px-2" data-testid="next-page">
              <ChevronRight size={14} />
            </Button>
            <Button variant="outline" size="sm" disabled={page >= totalPages - 1} onClick={() => setPage(totalPages - 1)} className="h-8 px-2">
              Last
            </Button>
          </div>
        </div>
      )}

      <InvoiceDetailModal compact
        open={invoiceModalOpen}
        onOpenChange={setInvoiceModalOpen}
        saleId={selectedInvoiceId}
        onUpdated={fetchSales}
      />

      <CustomerStatementModal
        open={stmtOpen}
        onOpenChange={setStmtOpen}
        customer={stmtCustomer}
      />
    </div>
  );
}
