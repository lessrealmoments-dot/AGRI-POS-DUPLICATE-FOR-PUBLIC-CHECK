import { useState, useEffect, useCallback } from 'react';
import { useAuth, api } from '../contexts/AuthContext';
import { formatPHP, fmtDate } from '../lib/utils';
import { Card, CardContent } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Badge } from '../components/ui/badge';
import { Input } from '../components/ui/input';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs';
import {
  AlertTriangle, CheckCircle2, Clock, Search, MessageSquare, UserCheck,
  XCircle, FileText, ArrowRight, Eye, RefreshCw, ArrowLeftRight, Check,
  ShieldCheck, Truck, PackageX, Receipt, Scale, ClipboardCheck
} from 'lucide-react';
import { formatDateTime } from '../lib/dateFormat';
import { toast } from 'sonner';
import TransferDetailModal from '../components/TransferDetailModal';
import CalcInput from '../components/CalcInput';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

const STATUS_COLORS = {
  open: 'bg-red-100 text-red-700',
  investigating: 'bg-amber-100 text-amber-700',
  resolved: 'bg-emerald-100 text-emerald-700',
  closed: 'bg-slate-100 text-slate-600',
};

const PRIORITY_COLORS = {
  high: 'bg-red-100 text-red-700',
  medium: 'bg-amber-100 text-amber-700',
  low: 'bg-slate-100 text-slate-600',
};

const RESOLUTION_TYPE_META = {
  transit_loss: { icon: Truck, color: 'text-red-600', bg: 'bg-red-50', label: 'Transit Loss' },
  sender_error: { icon: ShieldCheck, color: 'text-blue-600', bg: 'bg-blue-50', label: 'Sender Error (No Loss)' },
  receiver_error: { icon: Scale, color: 'text-amber-600', bg: 'bg-amber-50', label: 'Receiver Error' },
  write_off: { icon: PackageX, color: 'text-slate-600', bg: 'bg-slate-50', label: 'Write Off' },
  insurance_claim: { icon: Receipt, color: 'text-purple-600', bg: 'bg-purple-50', label: 'Insurance Claim' },
  partial_recovery: { icon: CheckCircle2, color: 'text-emerald-600', bg: 'bg-emerald-50', label: 'Partial Recovery' },
  // Negative stock resolution types
  unencoded_po: { icon: ClipboardCheck, color: 'text-blue-600', bg: 'bg-blue-50', label: 'Unencoded PO' },
  count_error: { icon: Scale, color: 'text-amber-600', bg: 'bg-amber-50', label: 'Count Error' },
  wrong_item: { icon: XCircle, color: 'text-orange-600', bg: 'bg-orange-50', label: 'Wrong Item' },
  shrinkage: { icon: PackageX, color: 'text-red-600', bg: 'bg-red-50', label: 'Shrinkage' },
};

export default function IncidentTicketsPage() {
  const { user, branches, selectedBranchId, canViewAllBranches } = useAuth();

  // ── Main view ─────────────────────────────────────────────────────────
  const [mainTab, setMainTab] = useState('tickets');

  // ── Tickets state ─────────────────────────────────────────────────────
  const [tickets, setTickets] = useState([]);
  const [summary, setSummary] = useState({});
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState('open');
  const [search, setSearch] = useState('');
  const [selectedTicket, setSelectedTicket] = useState(null);
  const [actionLoading, setActionLoading] = useState(false);
  const [noteText, setNoteText] = useState('');

  // Resolve dialog state
  const [resolveDialog, setResolveDialog] = useState(null);
  const [resolveNote, setResolveNote] = useState('');
  const [recoveryAmount, setRecoveryAmount] = useState(0);
  const [resolutionType, setResolutionType] = useState('');
  const [accountableParty, setAccountableParty] = useState('');
  const [resolvePin, setResolvePin] = useState('');
  // Link PO state (for unencoded_po resolution)
  const [poSearch, setPoSearch] = useState('');
  const [poResults, setPoResults] = useState([]);
  const [poSearching, setPoSearching] = useState(false);
  const [linkedPo, setLinkedPo] = useState(null); // { id, po_number }

  // Assign dialog state
  const [assignDialog, setAssignDialog] = useState(null);
  const [assignUserId, setAssignUserId] = useState('');
  const [teamMembers, setTeamMembers] = useState([]);

  // Sender confirm dialog state
  const [senderConfirmDialog, setSenderConfirmDialog] = useState(null);
  const [senderConfirmItems, setSenderConfirmItems] = useState([]);
  const [senderConfirmNote, setSenderConfirmNote] = useState('');

  // ── Transfer Variances state ──────────────────────────────────────────
  const [varianceData, setVarianceData] = useState(null);
  const [varianceLoading, setVarianceLoading] = useState(false);
  const [varianceViewTransfer, setVarianceViewTransfer] = useState(null);
  const [varianceViewLoading, setVarianceViewLoading] = useState(null);

  // ── Fetch tickets ─────────────────────────────────────────────────────
  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const statusFilter = tab === 'all' ? '' : tab;
      const params = { status: statusFilter || undefined, limit: 100 };
      // Branch scoping: if a specific branch is selected, filter by it
      if (selectedBranchId && selectedBranchId !== 'all') {
        params.branch_id = selectedBranchId;
      }
      const [ticketsRes, summaryRes] = await Promise.all([
        api.get('/incident-tickets', { params }),
        api.get('/incident-tickets/summary'),
      ]);
      setTickets(ticketsRes.data.tickets || []);
      setSummary(summaryRes.data || {});
    } catch { toast.error('Failed to load tickets'); }
    setLoading(false);
  }, [tab, selectedBranchId]);

  useEffect(() => { if (mainTab === 'tickets') fetchData(); }, [fetchData, mainTab]);

  // ── Fetch variances ───────────────────────────────────────────────────
  const loadVariances = useCallback(async () => {
    setVarianceLoading(true);
    try {
      const res = await api.get(`${BACKEND_URL}/api/audit/transfer-variances`);
      setVarianceData(res.data);
    } catch { setVarianceData(null); }
    setVarianceLoading(false);
  }, []);

  useEffect(() => { loadVariances(); }, [loadVariances]);
  useEffect(() => { if (mainTab === 'variances') loadVariances(); }, [mainTab, loadVariances]);

  const openVarianceDetail = async (transferId) => {
    setVarianceViewLoading(transferId);
    try {
      const res = await api.get(`${BACKEND_URL}/api/branch-transfers/${transferId}`);
      setVarianceViewTransfer(res.data);
    } catch { toast.error('Failed to load transfer details'); }
    setVarianceViewLoading(null);
  };

  // ── Ticket actions ────────────────────────────────────────────────────
  const fetchTeam = async () => {
    try {
      const res = await api.get('/users');
      setTeamMembers(res.data || []);
    } catch { setTeamMembers([]); }
  };

  const refreshTicket = async (ticketId) => {
    try {
      const res = await api.get(`/incident-tickets/${ticketId}`);
      setSelectedTicket(res.data);
    } catch { /* ignore */ }
    fetchData();
  };

  const handleAddNote = async () => {
    if (!selectedTicket || !noteText.trim()) return;
    setActionLoading(true);
    try {
      await api.put(`/incident-tickets/${selectedTicket.id}/add-note`, { note: noteText });
      toast.success('Note added');
      setNoteText('');
      await refreshTicket(selectedTicket.id);
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed'); }
    setActionLoading(false);
  };

  const handleAssign = async () => {
    if (!assignDialog || !assignUserId) return;
    const member = teamMembers.find(m => m.id === assignUserId);
    setActionLoading(true);
    try {
      await api.put(`/incident-tickets/${assignDialog.id}/assign`, {
        assigned_to_id: assignUserId,
        assigned_to_name: member?.full_name || member?.username || '',
      });
      toast.success('Ticket assigned');
      setAssignDialog(null);
      await refreshTicket(assignDialog.id);
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed'); }
    setActionLoading(false);
  };

  const handleResolve = async () => {
    if (!resolveDialog || !resolveNote.trim()) { toast.error('Resolution note required'); return; }
    if (!resolutionType) { toast.error('Please select a resolution type'); return; }
    if (['transit_loss', 'insurance_claim'].includes(resolutionType) && !accountableParty.trim()) {
      toast.error('Accountable party required'); return;
    }
    if (!resolvePin) { toast.error('Authorization PIN required'); return; }
    setActionLoading(true);
    try {
      await api.put(`/incident-tickets/${resolveDialog.id}/resolve`, {
        resolution_type: resolutionType,
        resolution_note: resolveNote,
        accountable_party: accountableParty,
        recovery_amount: recoveryAmount,
        linked_po_id: linkedPo?.id || '',
        linked_po_number: linkedPo?.po_number || '',
        pin: resolvePin,
      });
      toast.success('Ticket resolved');
      setResolveDialog(null);
      setSelectedTicket(null);
      fetchData();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed to resolve'); }
    setActionLoading(false);
  };

  const handleSenderConfirm = async () => {
    if (!senderConfirmDialog) return;
    setActionLoading(true);
    try {
      const res = await api.put(`/incident-tickets/${senderConfirmDialog.id}/sender-confirm`, {
        confirmed_items: senderConfirmItems.map(i => ({
          product_id: i.product_id,
          sender_confirmed_qty: i.sender_confirmed_qty,
        })),
        note: senderConfirmNote,
      });
      if (res.data.auto_resolved) {
        toast.success('Variance cancelled — sender confirms no actual loss!');
        setSelectedTicket(null);
      } else {
        toast.success('Sender confirmation recorded. Variance still exists.');
        await refreshTicket(senderConfirmDialog.id);
      }
      setSenderConfirmDialog(null);
      fetchData();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed'); }
    setActionLoading(false);
  };

  const handleClose = async (ticketId) => {
    setActionLoading(true);
    try {
      await api.put(`/incident-tickets/${ticketId}/close`, { note: 'Closed by admin' });
      toast.success('Ticket closed');
      setSelectedTicket(null);
      fetchData();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed'); }
    setActionLoading(false);
  };

  const openResolveDialog = (ticket) => {
    setResolveDialog(ticket);
    setResolveNote('');
    setRecoveryAmount(0);
    setResolutionType('');
    setAccountableParty('');
    setResolvePin('');
    setPoSearch('');
    setPoResults([]);
    setLinkedPo(null);
  };

  // PO search for "Link PO" feature
  const searchPOs = async (query) => {
    setPoSearch(query);
    if (!query || query.length < 2) { setPoResults([]); return; }
    setPoSearching(true);
    try {
      const res = await api.get('/purchase-orders', { params: { search: query, limit: 8 } });
      setPoResults(res.data.purchase_orders || []);
    } catch { setPoResults([]); }
    setPoSearching(false);
  };

  const openSenderConfirm = (ticket) => {
    setSenderConfirmDialog(ticket);
    setSenderConfirmNote('');
    setSenderConfirmItems(
      (ticket.items || []).map(i => ({
        product_id: i.product_id,
        product_name: i.product_name,
        sku: i.sku,
        qty_ordered: i.qty_ordered,
        qty_received: i.qty_received,
        sender_confirmed_qty: i.sender_confirmed_qty ?? i.qty_ordered,
      }))
    );
  };

  const getBranchName = (branchId) => (branches || []).find(b => b.id === branchId)?.name || '';

  const filtered = search
    ? tickets.filter(t => t.ticket_number?.toLowerCase().includes(search.toLowerCase()) ||
        t.order_number?.toLowerCase().includes(search.toLowerCase()) ||
        t.product_name?.toLowerCase().includes(search.toLowerCase()) ||
        t.from_branch_name?.toLowerCase().includes(search.toLowerCase()) ||
        getBranchName(t.branch_id).toLowerCase().includes(search.toLowerCase()))
    : tickets;

  const fmtDate = (d) => d ? formatDateTime(d) : '';

  const totalVariances = varianceData?.summary?.total_variance_transfers || 0;
  const totalCapLoss = summary.total_real_capital_loss || varianceData?.summary?.total_capital_loss || 0;
  const openTickets = (summary.open || 0) + (summary.investigating || 0);
  const unresolvedLoss = summary.total_unresolved_capital_loss || 0;

  return (
    <div className="space-y-5 animate-fadeIn" data-testid="incident-tickets-page">
      <div>
        <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
          <AlertTriangle size={22} className="text-amber-600" /> Incident Tickets & Transfer Variances
        </h1>
        <p className="text-sm text-slate-500 mt-0.5">Track, investigate, and resolve transfer discrepancies and losses</p>
      </div>

      {/* ── Summary Cards ── */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <Card className="border border-amber-200 bg-amber-50/30">
          <CardContent className="p-3">
            <p className="text-[10px] uppercase font-medium text-slate-500">Total Variances</p>
            <p className="text-xl font-bold text-amber-700" style={{ fontFamily: 'Manrope' }}>{totalVariances}</p>
            <p className="text-[10px] text-slate-400">transfers with discrepancies</p>
          </CardContent>
        </Card>
        <Card className="border border-red-200 bg-red-50/30">
          <CardContent className="p-3">
            <p className="text-[10px] uppercase font-medium text-slate-500">Capital Loss</p>
            <p className="text-xl font-bold text-red-700" style={{ fontFamily: 'Manrope' }}>{formatPHP(totalCapLoss)}</p>
            <p className="text-[10px] text-slate-400">total from variances</p>
          </CardContent>
        </Card>
        <Card className={`border ${openTickets > 0 ? 'border-red-200 bg-red-50/30' : 'border-emerald-200 bg-emerald-50/30'}`}>
          <CardContent className="p-3">
            <p className="text-[10px] uppercase font-medium text-slate-500">Active Tickets</p>
            <p className={`text-xl font-bold ${openTickets > 0 ? 'text-red-600' : 'text-emerald-600'}`} style={{ fontFamily: 'Manrope' }}>{openTickets}</p>
            <p className="text-[10px] text-slate-400">open + investigating</p>
          </CardContent>
        </Card>
        <Card className="border border-emerald-200 bg-emerald-50/30">
          <CardContent className="p-3">
            <p className="text-[10px] uppercase font-medium text-slate-500">Resolved</p>
            <p className="text-xl font-bold text-emerald-600" style={{ fontFamily: 'Manrope' }}>{(summary.resolved || 0) + (summary.closed || 0)}</p>
            <p className="text-[10px] text-slate-400">resolved + closed</p>
          </CardContent>
        </Card>
        <Card className={`border ${unresolvedLoss > 0 ? 'border-red-200 bg-red-50/30' : 'border-slate-200'}`}>
          <CardContent className="p-3">
            <p className="text-[10px] uppercase font-medium text-slate-500">Unresolved Loss</p>
            <p className={`text-xl font-bold ${unresolvedLoss > 0 ? 'text-red-700' : 'text-slate-400'}`} style={{ fontFamily: 'Manrope' }}>{formatPHP(unresolvedLoss)}</p>
            <p className="text-[10px] text-slate-400">capital at risk</p>
          </CardContent>
        </Card>
      </div>

      {/* ── Main Tabs ── */}
      <Tabs value={mainTab} onValueChange={setMainTab}>
        <TabsList className="h-10">
          <TabsTrigger value="tickets" data-testid="tickets-main-tab" className="gap-1.5">
            <FileText size={14} /> Incident Tickets
            {openTickets > 0 && <span className="ml-1 bg-red-500 text-white text-[10px] rounded-full px-1.5 py-0.5 font-bold">{openTickets}</span>}
          </TabsTrigger>
          <TabsTrigger value="variances" data-testid="variances-main-tab" className="gap-1.5">
            <ArrowLeftRight size={14} /> All Transfer Variances
            {totalVariances > 0 && <span className="ml-1 bg-amber-500 text-white text-[10px] rounded-full px-1.5 py-0.5 font-bold">{totalVariances}</span>}
          </TabsTrigger>
        </TabsList>

        {/* ── TICKETS TAB ── */}
        <TabsContent value="tickets" className="mt-4 space-y-4">
          <div className="flex items-center gap-3">
            <Tabs value={tab} onValueChange={setTab} className="flex-1">
              <TabsList className="h-9 bg-slate-100">
                <TabsTrigger value="open" className="text-xs">Open</TabsTrigger>
                <TabsTrigger value="investigating" className="text-xs">Investigating</TabsTrigger>
                <TabsTrigger value="resolved" className="text-xs">Resolved</TabsTrigger>
                <TabsTrigger value="closed" className="text-xs">Closed</TabsTrigger>
                <TabsTrigger value="all" className="text-xs">All</TabsTrigger>
              </TabsList>
            </Tabs>
            <div className="relative w-48">
              <Search size={14} className="absolute left-2 top-1/2 -translate-y-1/2 text-slate-400" />
              <Input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search..." className="pl-8 h-9 text-xs" />
            </div>
          </div>

          <Card className="border-slate-200">
            <CardContent className="p-0">
              <Table>
                <TableHeader><TableRow className="bg-slate-50">
                  <TableHead className="text-xs uppercase text-slate-500">Ticket</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500">Type / Product</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500">Branch</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500 text-right">Impact</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500">Resolution</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500">Status</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500">Assigned</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500">Date</TableHead>
                </TableRow></TableHeader>
                <TableBody>
                  {loading ? (
                    <TableRow><TableCell colSpan={8} className="text-center py-8 text-slate-400">Loading...</TableCell></TableRow>
                  ) : filtered.length === 0 ? (
                    <TableRow><TableCell colSpan={8} className="text-center py-8 text-slate-400">
                      {tab === 'open' ? 'No open incidents' : 'No tickets found'}
                    </TableCell></TableRow>
                  ) : filtered.map(t => {
                    const rtMeta = RESOLUTION_TYPE_META[t.resolution_type];
                    const isNegativeStock = t.ticket_type === 'negative_stock_override';
                    return (
                    <TableRow key={t.id} className="cursor-pointer hover:bg-slate-50" onClick={() => setSelectedTicket(t)}
                      data-testid={`ticket-row-${t.id}`}>
                      <TableCell className="font-mono text-xs font-bold text-blue-600">{t.ticket_number}</TableCell>
                      <TableCell>
                        {isNegativeStock ? (
                          <div>
                            <span className="text-xs font-medium text-red-700">{t.product_name}</span>
                            <p className="text-[10px] text-slate-400">Inv: {t.invoice_number}</p>
                          </div>
                        ) : (
                          <button className="font-mono text-xs text-blue-600 hover:underline"
                            onClick={(e) => { e.stopPropagation(); openVarianceDetail(t.transfer_id); }}>
                            {t.order_number}
                          </button>
                        )}
                      </TableCell>
                      <TableCell className="text-xs">
                        {isNegativeStock ? (
                          <span className="text-xs text-slate-600">{getBranchName(t.branch_id) || '—'}</span>
                        ) : (
                          <span>{t.from_branch_name} → {t.to_branch_name}</span>
                        )}
                      </TableCell>
                      <TableCell className="text-right font-mono font-bold text-red-600">
                        {isNegativeStock
                          ? <span className="text-xs font-mono">{t.qty_before_sale} → {t.qty_after_sale}</span>
                          : formatPHP(t.total_capital_loss)
                        }
                      </TableCell>
                      <TableCell>
                        {isNegativeStock ? (
                          <Badge className="text-[10px] bg-slate-100 text-slate-600">{t.override_method || 'override'}</Badge>
                        ) : rtMeta ? (
                          <Badge className={`text-[10px] ${rtMeta.bg} ${rtMeta.color}`}>{rtMeta.label}</Badge>
                        ) : (
                          <span className="text-[10px] text-slate-400">{'\u2014'}</span>
                        )}
                      </TableCell>
                      <TableCell><Badge className={`text-[10px] ${STATUS_COLORS[t.status]}`}>{t.status}</Badge></TableCell>
                      <TableCell className="text-xs text-slate-500">{t.assigned_to_name || '\u2014'}</TableCell>
                      <TableCell className="text-xs text-slate-400">{fmtDate(t.created_at)}</TableCell>
                    </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </TabsContent>

        {/* ── ALL VARIANCES TAB ── */}
        <TabsContent value="variances" className="mt-4 space-y-4">
          {varianceLoading ? (
            <div className="text-center py-16 text-slate-400">
              <RefreshCw size={20} className="animate-spin mx-auto mb-2" /> Loading...
            </div>
          ) : !varianceData ? (
            <Card className="border-slate-200">
              <CardContent className="p-8 text-center text-slate-400">
                <ArrowLeftRight size={36} className="mx-auto mb-3 opacity-30" />
                <p className="text-sm">No transfer variance data available.</p>
              </CardContent>
            </Card>
          ) : (
            <Card className="border-slate-200">
              <CardContent className="p-0">
                <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between">
                  <h3 className="text-sm font-semibold text-slate-700 flex items-center gap-2">
                    <AlertTriangle size={14} className="text-amber-600" />
                    All Transfer Variances
                    <span className="text-xs font-normal text-slate-400">({varianceData.items.length} transfers)</span>
                  </h3>
                  <Button size="sm" variant="outline" className="h-7 text-xs" onClick={loadVariances}>
                    <RefreshCw size={12} className="mr-1" /> Refresh
                  </Button>
                </div>
                {varianceData.items.length === 0 ? (
                  <div className="text-center py-12 text-slate-400">
                    <Check size={24} className="mx-auto mb-2 text-emerald-400" />
                    <p className="text-sm">No transfer variances found.</p>
                  </div>
                ) : (
                  <div className="divide-y divide-slate-100">
                    {varianceData.items.map((item) => (
                      <div key={item.transfer_id} className="px-4 py-3 hover:bg-slate-50 transition-colors" data-testid={`variance-row-${item.transfer_id}`}>
                        <div className="flex items-start justify-between">
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 flex-wrap">
                              <span className="font-mono text-sm font-bold text-slate-700">{item.order_number}</span>
                              {item.incident_ticket_number ? (
                                <button className="inline-flex items-center gap-1 text-[10px] bg-amber-100 text-amber-700 px-2 py-0.5 rounded-full font-medium hover:bg-amber-200 transition-colors"
                                  onClick={() => { setMainTab('tickets'); setTab('all'); setSearch(item.incident_ticket_number); }}
                                  data-testid={`variance-ticket-${item.transfer_id}`}>
                                  <AlertTriangle size={9} /> {item.incident_ticket_number}
                                </button>
                              ) : (
                                <span className="text-[10px] bg-slate-100 text-slate-500 px-2 py-0.5 rounded-full">No ticket</span>
                              )}
                              {item.capital_loss > 0 && (
                                <span className="text-[10px] bg-red-100 text-red-700 px-2 py-0.5 rounded-full font-medium">
                                  Loss: {formatPHP(item.capital_loss)}
                                </span>
                              )}
                            </div>
                            <p className="text-xs text-slate-500 mt-1">
                              {item.from_branch_name} <ArrowRight size={10} className="inline text-slate-400" /> {item.to_branch_name}
                            </p>
                          </div>
                          <div className="text-right shrink-0 ml-4">
                            <div className="flex items-center gap-2 text-xs text-slate-500">
                              {item.shortages_count > 0 && <span className="text-amber-600 font-medium">{item.shortages_count} shortage(s)</span>}
                              {item.excesses_count > 0 && <span className="text-blue-600 font-medium">{item.excesses_count} excess(es)</span>}
                            </div>
                            <p className="text-[10px] text-slate-400 mt-0.5">{fmtDate(item.accepted_at)}</p>
                            <Button size="sm" variant="outline" className="h-7 text-xs mt-1.5"
                              onClick={() => openVarianceDetail(item.transfer_id)} data-testid={`view-variance-${item.transfer_id}`}
                              disabled={varianceViewLoading === item.transfer_id}>
                              {varianceViewLoading === item.transfer_id ? <RefreshCw size={12} className="mr-1 animate-spin" /> : <Eye size={12} className="mr-1" />} View
                            </Button>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          )}
        </TabsContent>
      </Tabs>

      {/* ═══════════════════════════════════════════════════════════════════
          TICKET DETAIL DIALOG
         ═══════════════════════════════════════════════════════════════════ */}
      <Dialog open={!!selectedTicket} onOpenChange={() => setSelectedTicket(null)}>
        <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
          {selectedTicket && (() => {
            const rt = RESOLUTION_TYPE_META[selectedTicket.resolution_type];
            return (
            <>
              <DialogHeader>
                <DialogTitle className="flex items-center gap-2 flex-wrap" style={{ fontFamily: 'Manrope' }}>
                  <FileText size={18} />
                  {selectedTicket.ticket_number}
                  <Badge className={`text-[10px] ${STATUS_COLORS[selectedTicket.status]}`}>{selectedTicket.status}</Badge>
                  {selectedTicket.ticket_type === 'negative_stock_override'
                    ? <Badge className="text-[10px] bg-red-100 text-red-700">Negative Stock</Badge>
                    : <Badge className={`text-[10px] ${PRIORITY_COLORS[selectedTicket.priority]}`}>{selectedTicket.priority}</Badge>
                  }
                  {rt && <Badge className={`text-[10px] ${rt.bg} ${rt.color}`}>{rt.label}</Badge>}
                </DialogTitle>
              </DialogHeader>
              <div className="space-y-4">
                {/* ── NEGATIVE STOCK HEADER ── */}
                {selectedTicket.ticket_type === 'negative_stock_override' ? (
                  <>
                    <div className="grid grid-cols-2 gap-3 text-sm">
                      <div><span className="text-slate-500">Product:</span> <span className="font-bold">{selectedTicket.product_name}</span></div>
                      <div><span className="text-slate-500">Branch:</span> <span className="font-medium">{getBranchName(selectedTicket.branch_id)}</span></div>
                      <div><span className="text-slate-500">Invoice:</span>{' '}
                        <span className="font-mono font-bold text-blue-600">{selectedTicket.invoice_number}</span>
                      </div>
                      <div><span className="text-slate-500">Stock Change:</span>{' '}
                        <span className="font-mono font-bold text-red-600">{selectedTicket.qty_before_sale} → {selectedTicket.qty_after_sale}</span>
                      </div>
                      <div><span className="text-slate-500">Cashier:</span> {selectedTicket.cashier_name}</div>
                      <div><span className="text-slate-500">Override by:</span>{' '}
                        <span className="font-semibold text-amber-700">{selectedTicket.override_by_name}</span>
                        {selectedTicket.override_method && <span className="text-[10px] text-slate-400 ml-1">({selectedTicket.override_method})</span>}
                      </div>
                      {selectedTicket.assigned_to_name && <div><span className="text-slate-500">Assigned:</span> {selectedTicket.assigned_to_name}</div>}
                      {selectedTicket.journal_entry_number && <div>
                        <span className="text-slate-500">Journal Entry:</span>{' '}
                        <span className="font-mono font-bold text-blue-600">{selectedTicket.journal_entry_number}</span>
                      </div>}
                      {selectedTicket.linked_po_number && <div>
                        <span className="text-slate-500">Linked PO:</span>{' '}
                        <span className="font-mono font-bold text-blue-600">{selectedTicket.linked_po_number}</span>
                      </div>}
                    </div>

                    {/* Root cause guidance */}
                    {['open', 'investigating'].includes(selectedTicket.status) && (
                      <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-xs text-amber-800 space-y-1.5">
                        <p className="font-semibold flex items-center gap-1.5"><AlertTriangle size={13} /> Investigate: Why was stock negative?</p>
                        <ul className="space-y-1 pl-4 text-amber-700">
                          <li className="flex items-center gap-1.5"><ClipboardCheck size={11} className="text-blue-500 shrink-0" /> <b>Unencoded PO?</b> — Supplier delivered goods but PO was never entered</li>
                          <li className="flex items-center gap-1.5"><Scale size={11} className="text-amber-500 shrink-0" /> <b>Count error?</b> — Last physical count was wrong</li>
                          <li className="flex items-center gap-1.5"><XCircle size={11} className="text-orange-500 shrink-0" /> <b>Wrong item?</b> — Cashier scanned the wrong product</li>
                          <li className="flex items-center gap-1.5"><PackageX size={11} className="text-red-500 shrink-0" /> <b>Shrinkage?</b> — Actual loss (theft, damage, expiry)</li>
                        </ul>
                      </div>
                    )}
                  </>
                ) : (
                  <>
                    {/* ── TRANSFER VARIANCE HEADER (existing) ── */}
                    <div className="grid grid-cols-2 gap-3 text-sm">
                      <div>
                        <span className="text-slate-500">Transfer:</span>{' '}
                        <button className="font-mono font-bold text-blue-600 hover:underline" data-testid="ticket-transfer-link"
                          onClick={() => { setSelectedTicket(null); openVarianceDetail(selectedTicket.transfer_id); }}>
                          {selectedTicket.order_number}
                        </button>
                      </div>
                      <div><span className="text-slate-500">Route:</span> {selectedTicket.from_branch_name} → {selectedTicket.to_branch_name}</div>
                      <div><span className="text-slate-500">Capital Loss:</span> <span className="font-bold text-red-600">{formatPHP(selectedTicket.total_capital_loss)}</span></div>
                      <div><span className="text-slate-500">Retail Loss:</span> <span className="font-bold text-red-600">{formatPHP(selectedTicket.total_retail_loss)}</span></div>
                      {selectedTicket.assigned_to_name && <div><span className="text-slate-500">Assigned:</span> {selectedTicket.assigned_to_name}</div>}
                      {selectedTicket.recovery_amount > 0 && <div><span className="text-slate-500">Recovered:</span> <span className="text-emerald-600 font-bold">{formatPHP(selectedTicket.recovery_amount)}</span></div>}
                      {selectedTicket.accountable_party && <div><span className="text-slate-500">Charged to:</span> <span className="font-semibold text-red-600">{selectedTicket.accountable_party}</span></div>}
                      {selectedTicket.approved_by_name && <div>
                        <span className="text-slate-500">Approved by:</span>{' '}
                        <span className="font-semibold text-emerald-700">{selectedTicket.approved_by_name}</span>
                        {selectedTicket.approval_method && <span className="text-[10px] text-slate-400 ml-1">({selectedTicket.approval_method})</span>}
                      </div>}
                      {selectedTicket.journal_entry_number && <div>
                        <span className="text-slate-500">Journal Entry:</span>{' '}
                        <span className="font-mono font-bold text-blue-600">{selectedTicket.journal_entry_number}</span>
                      </div>}
                      {selectedTicket.sender_confirmed && <div className="col-span-2">
                        <span className="inline-flex items-center gap-1 text-xs bg-blue-50 text-blue-700 px-2 py-1 rounded font-medium">
                          <ShieldCheck size={12} /> Sender confirmed quantities ({fmtDate(selectedTicket.sender_confirmed_at)})
                        </span>
                      </div>}
                    </div>
                  </>
                )}

                {/* Resolution summary card */}
                {selectedTicket.resolution_type && (
                  <div className={`rounded-lg border p-3 ${rt?.bg || 'bg-slate-50'}`}>
                    <p className="text-xs font-semibold text-slate-700 flex items-center gap-1.5">
                      {rt && <rt.icon size={13} className={rt.color} />} Resolution: {rt?.label || selectedTicket.resolution_type}
                    </p>
                    {selectedTicket.accountable_party && (
                      <p className="text-xs text-slate-600 mt-1">Charged to: <b>{selectedTicket.accountable_party}</b></p>
                    )}
                    {selectedTicket.resolution_note && (
                      <p className="text-xs text-slate-500 mt-1 italic">&quot;{selectedTicket.resolution_note}&quot;</p>
                    )}
                    {selectedTicket.recovery_amount > 0 && (
                      <p className="text-xs text-emerald-700 mt-1 font-medium">Recovery: {formatPHP(selectedTicket.recovery_amount)}</p>
                    )}
                    {selectedTicket.journal_entry_number && (
                      <p className="text-xs text-blue-600 mt-1 font-medium font-mono">
                        Journal Entry: {selectedTicket.journal_entry_number}
                      </p>
                    )}
                  </div>
                )}

                {/* Variance Items (transfer tickets only) */}
                {selectedTicket.ticket_type !== 'negative_stock_override' && (selectedTicket.items || []).length > 0 && (
                <div className="border rounded-lg overflow-hidden">
                  <table className="w-full text-xs">
                    <thead className="bg-slate-50">
                      <tr>
                        <th className="text-left px-3 py-2 font-medium">Product</th>
                        <th className="text-right px-3 py-2 font-medium">Sent</th>
                        {selectedTicket.sender_confirmed && <th className="text-right px-3 py-2 font-medium text-blue-600">Sender Says</th>}
                        <th className="text-right px-3 py-2 font-medium">Received</th>
                        <th className="text-right px-3 py-2 font-medium">Variance</th>
                        <th className="text-right px-3 py-2 font-medium">Loss</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-50">
                      {(selectedTicket.items || []).map((item, i) => (
                        <tr key={i} className={item.type === 'shortage' ? 'bg-red-50/40' : 'bg-blue-50/40'}>
                          <td className="px-3 py-2 font-medium">{item.product_name} <span className="text-slate-400 text-[10px]">{item.sku}</span></td>
                          <td className="px-3 py-2 text-right font-mono">{item.qty_ordered}</td>
                          {selectedTicket.sender_confirmed && (
                            <td className="px-3 py-2 text-right font-mono text-blue-600 font-bold">{item.sender_confirmed_qty ?? '—'}</td>
                          )}
                          <td className="px-3 py-2 text-right font-mono font-bold">{item.qty_received}</td>
                          <td className={`px-3 py-2 text-right font-mono font-bold ${item.type === 'shortage' ? 'text-red-600' : 'text-blue-600'}`}>
                            {item.type === 'shortage' ? `-${item.variance}` : `+${Math.abs(item.variance)}`}
                          </td>
                          <td className="px-3 py-2 text-right font-mono text-red-600">{formatPHP(item.capital_variance)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                )}

                {/* Timeline */}
                <div>
                  <h3 className="text-sm font-semibold mb-2 flex items-center gap-1"><Clock size={14} /> Timeline</h3>
                  <div className="space-y-2 max-h-[200px] overflow-y-auto">
                    {(selectedTicket.timeline || []).map((event, i) => (
                      <div key={i} className="flex gap-3 text-xs bg-slate-50 rounded-lg p-2.5">
                        <div className="shrink-0 mt-0.5">
                          {event.action === 'created' && <AlertTriangle size={12} className="text-red-500" />}
                          {event.action === 'assigned' && <UserCheck size={12} className="text-blue-500" />}
                          {event.action === 'note' && <MessageSquare size={12} className="text-slate-500" />}
                          {event.action === 'sender_confirmed' && <ShieldCheck size={12} className="text-blue-600" />}
                          {event.action === 'resolved' && <CheckCircle2 size={12} className="text-emerald-500" />}
                          {event.action === 'closed' && <XCircle size={12} className="text-slate-400" />}
                        </div>
                        <div className="flex-1">
                          <p className="text-slate-700">{event.detail}</p>
                          <p className="text-[10px] text-slate-400 mt-0.5">{event.by_name} &middot; {fmtDate(event.at)}</p>
                          {event.resolution_type && (
                            <Badge className={`mt-1 text-[10px] ${RESOLUTION_TYPE_META[event.resolution_type]?.bg || ''} ${RESOLUTION_TYPE_META[event.resolution_type]?.color || ''}`}>
                              {RESOLUTION_TYPE_META[event.resolution_type]?.label || event.resolution_type}
                            </Badge>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Add Note */}
                {['open', 'investigating'].includes(selectedTicket.status) && (
                  <div className="flex gap-2">
                    <Input value={noteText} onChange={e => setNoteText(e.target.value)}
                      placeholder="Add investigation note..." className="flex-1 h-9 text-sm" data-testid="ticket-note-input" />
                    <Button size="sm" onClick={handleAddNote} disabled={actionLoading || !noteText.trim()}
                      className="bg-[#1A4D2E] text-white" data-testid="add-note-btn">
                      <MessageSquare size={12} className="mr-1" /> Add Note
                    </Button>
                  </div>
                )}

                {/* Action Buttons */}
                <div className="flex gap-2 flex-wrap pt-2 border-t">
                  {['open', 'investigating'].includes(selectedTicket.status) && (
                    <>
                      <Button size="sm" variant="outline" onClick={() => { setAssignDialog(selectedTicket); fetchTeam(); setAssignUserId(''); }}
                        className="text-xs" data-testid="assign-btn">
                        <UserCheck size={12} className="mr-1" /> Assign
                      </Button>
                      {selectedTicket.ticket_type !== 'negative_stock_override' && !selectedTicket.sender_confirmed && (
                        <Button size="sm" variant="outline" onClick={() => openSenderConfirm(selectedTicket)}
                          className="text-xs border-blue-300 text-blue-700 hover:bg-blue-50" data-testid="sender-confirm-btn">
                          <ShieldCheck size={12} className="mr-1" /> Sender Confirm
                        </Button>
                      )}
                      <Button size="sm" onClick={() => openResolveDialog(selectedTicket)}
                        className="text-xs bg-emerald-600 text-white" data-testid="resolve-btn">
                        <CheckCircle2 size={12} className="mr-1" /> Resolve
                      </Button>
                    </>
                  )}
                  {selectedTicket.status === 'resolved' && user?.role === 'admin' && (
                    <Button size="sm" variant="outline" onClick={() => handleClose(selectedTicket.id)}
                      disabled={actionLoading} className="text-xs" data-testid="close-btn">
                      <XCircle size={12} className="mr-1" /> Close Ticket
                    </Button>
                  )}
                </div>
              </div>
            </>
            );
          })()}
        </DialogContent>
      </Dialog>

      {/* ═══════════════════════════════════════════════════════════════════
          ASSIGN DIALOG
         ═══════════════════════════════════════════════════════════════════ */}
      <Dialog open={!!assignDialog} onOpenChange={() => setAssignDialog(null)}>
        <DialogContent className="max-w-sm">
          <DialogHeader><DialogTitle style={{ fontFamily: 'Manrope' }}>Assign Investigator</DialogTitle></DialogHeader>
          <div className="space-y-3">
            <Select value={assignUserId} onValueChange={setAssignUserId}>
              <SelectTrigger data-testid="assign-user-select"><SelectValue placeholder="Select team member..." /></SelectTrigger>
              <SelectContent>
                {teamMembers.filter(m => m.active !== false).map(m => (
                  <SelectItem key={m.id} value={m.id}>{m.full_name || m.username} ({m.role})</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setAssignDialog(null)}>Cancel</Button>
              <Button onClick={handleAssign} disabled={!assignUserId || actionLoading}
                className="bg-[#1A4D2E] text-white" data-testid="confirm-assign-btn">Assign</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* ═══════════════════════════════════════════════════════════════════
          RESOLVE DIALOG (ENHANCED)
         ═══════════════════════════════════════════════════════════════════ */}
      <Dialog open={!!resolveDialog} onOpenChange={() => setResolveDialog(null)}>
        <DialogContent className="max-w-lg">
          <DialogHeader><DialogTitle style={{ fontFamily: 'Manrope' }}>Resolve Incident</DialogTitle></DialogHeader>
          {resolveDialog && (() => {
            const isStock = resolveDialog.ticket_type === 'negative_stock_override';
            const STOCK_KEYS = ['unencoded_po', 'count_error', 'wrong_item', 'shrinkage'];
            const TRANSFER_KEYS = ['transit_loss', 'sender_error', 'receiver_error', 'write_off', 'insurance_claim', 'partial_recovery'];
            const allowedKeys = isStock ? STOCK_KEYS : TRANSFER_KEYS;
            return (
          <div className="space-y-4">
            {/* Context summary */}
            <div className="flex gap-4 p-3 bg-slate-50 rounded-lg text-sm flex-wrap">
              {isStock ? (
                <>
                  <div><span className="text-slate-500">Product:</span> <span className="font-bold">{resolveDialog.product_name}</span></div>
                  <div><span className="text-slate-500">Stock:</span> <span className="font-bold text-red-600">{resolveDialog.qty_before_sale} → {resolveDialog.qty_after_sale}</span></div>
                  <div><span className="text-slate-500">Invoice:</span> <span className="font-mono font-bold">{resolveDialog.invoice_number}</span></div>
                </>
              ) : (
                <>
                  <div><span className="text-slate-500">Capital Loss:</span> <span className="font-bold text-red-600">{formatPHP(resolveDialog.total_capital_loss)}</span></div>
                  <div><span className="text-slate-500">Items:</span> <span className="font-bold">{resolveDialog.items?.length || 0}</span></div>
                </>
              )}
            </div>

            {/* Resolution Type */}
            <div>
              <label className="text-xs text-slate-500 font-medium block mb-1">Resolution Type *</label>
              <Select value={resolutionType} onValueChange={setResolutionType}>
                <SelectTrigger data-testid="resolution-type-select">
                  <SelectValue placeholder="How was this resolved?" />
                </SelectTrigger>
                <SelectContent>
                  {allowedKeys.map(key => {
                    const meta = RESOLUTION_TYPE_META[key];
                    if (!meta) return null;
                    return (
                    <SelectItem key={key} value={key}>
                      <span className="flex items-center gap-2">
                        <meta.icon size={14} className={meta.color} />
                        {meta.label}
                      </span>
                    </SelectItem>
                    );
                  })}
                </SelectContent>
              </Select>
              {resolutionType === 'unencoded_po' && (
                <p className="text-[10px] text-blue-600 mt-1 bg-blue-50 px-2 py-1 rounded">
                  The supplier delivered goods but the PO was never entered. Encode the PO and stock will self-correct.
                </p>
              )}
              {/* Link PO search — shown for unencoded_po */}
              {resolutionType === 'unencoded_po' && (
                <div className="mt-2 border border-blue-200 rounded-lg p-3 bg-blue-50/30 space-y-2">
                  <label className="text-xs text-blue-700 font-semibold flex items-center gap-1.5">
                    <ClipboardCheck size={12} /> Link Purchase Order (optional)
                  </label>
                  {linkedPo ? (
                    <div className="flex items-center justify-between bg-white rounded-lg border border-blue-200 px-3 py-2">
                      <div>
                        <span className="font-mono font-bold text-blue-700 text-sm">{linkedPo.po_number}</span>
                        <span className="text-xs text-slate-500 ml-2">{linkedPo.vendor || ''}</span>
                      </div>
                      <button onClick={() => { setLinkedPo(null); setPoSearch(''); }} className="text-slate-400 hover:text-red-500">
                        <XCircle size={14} />
                      </button>
                    </div>
                  ) : (
                    <div className="relative">
                      <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                      <Input value={poSearch} onChange={e => searchPOs(e.target.value)}
                        placeholder="Search PO number or supplier..."
                        className="pl-8 h-8 text-sm" data-testid="link-po-search" />
                      {poResults.length > 0 && (
                        <div className="absolute z-50 top-full left-0 right-0 mt-1 bg-white border border-slate-200 rounded-lg shadow-lg max-h-36 overflow-y-auto">
                          {poResults.map(po => (
                            <button key={po.id} className="w-full text-left px-3 py-2 text-xs hover:bg-blue-50 border-b border-slate-50 last:border-0"
                              onClick={() => { setLinkedPo({ id: po.id, po_number: po.po_number, vendor: po.vendor }); setPoResults([]); setPoSearch(''); }}
                              data-testid={`link-po-${po.id}`}>
                              <span className="font-mono font-bold text-blue-600">{po.po_number}</span>
                              <span className="text-slate-500 ml-2">{po.vendor}</span>
                              <span className="text-slate-400 ml-2">{po.purchase_date}</span>
                            </button>
                          ))}
                        </div>
                      )}
                      {poSearching && <p className="text-[10px] text-slate-400 mt-1">Searching...</p>}
                    </div>
                  )}
                  <p className="text-[10px] text-blue-600">Attach the PO that should have been encoded to create an audit trail.</p>
                </div>
              )}
              {resolutionType === 'count_error' && (
                <p className="text-[10px] text-amber-600 mt-1 bg-amber-50 px-2 py-1 rounded">
                  Last physical count was wrong. Do an inventory correction to fix the quantity.
                </p>
              )}
              {resolutionType === 'wrong_item' && (
                <p className="text-[10px] text-orange-600 mt-1 bg-orange-50 px-2 py-1 rounded">
                  Wrong product was sold or scanned. Void the sale or do an inventory adjustment.
                </p>
              )}
              {resolutionType === 'shrinkage' && (
                <p className="text-[10px] text-red-600 mt-1 bg-red-50 px-2 py-1 rounded">
                  Actual loss — goods are genuinely missing. A write-off journal entry will be created.
                </p>
              )}
              {resolutionType === 'sender_error' && (
                <p className="text-[10px] text-blue-600 mt-1 bg-blue-50 px-2 py-1 rounded">
                  Sender error means no actual loss occurred. The sender miscounted the original shipment.
                </p>
              )}
              {resolutionType === 'transit_loss' && (
                <p className="text-[10px] text-red-600 mt-1 bg-red-50 px-2 py-1 rounded">
                  Transit loss will charge the accountable party (driver/courier) below.
                </p>
              )}
            </div>

            {/* Accountable Party (shown for transit_loss, insurance_claim, partial_recovery, shrinkage) */}
            {['transit_loss', 'insurance_claim', 'partial_recovery', 'shrinkage'].includes(resolutionType) && (
              <div>
                <label className="text-xs text-slate-500 font-medium block mb-1">Accountable Party {resolutionType !== 'shrinkage' ? '*' : '(optional)'}</label>
                <Input value={accountableParty} onChange={e => setAccountableParty(e.target.value)}
                  placeholder={resolutionType === 'shrinkage' ? 'e.g. Unknown, Theft suspect, Expired batch...' : 'e.g. Driver Juan, LBC Express...'}
                  className="h-9 text-sm" data-testid="accountable-party-input" />
              </div>
            )}

            {/* Recovery Amount */}
            {resolutionType !== 'sender_error' && (
              <div>
                <label className="text-xs text-slate-500 font-medium block mb-1">Recovery Amount</label>
                <CalcInput value={recoveryAmount} onChange={(v) => setRecoveryAmount(parseFloat(v) || 0)}
 placeholder="0.00" className="h-9" data-testid="recovery-amount-input" />
                <p className="text-[10px] text-slate-400 mt-1">Amount recovered/to be recovered from responsible party</p>
              </div>
            )}

            {/* Resolution Note */}
            <div>
              <label className="text-xs text-slate-500 font-medium block mb-1">Resolution Note *</label>
              <textarea value={resolveNote} onChange={e => setResolveNote(e.target.value)}
                placeholder={resolutionType === 'unencoded_po' ? 'e.g. Found PO from supplier X, encoding now...'
                  : resolutionType === 'count_error' ? 'e.g. Counted 50 but system showed 45, corrected via count sheet...'
                  : resolutionType === 'wrong_item' ? 'e.g. Cashier scanned barcode for Product A instead of Product B...'
                  : resolutionType === 'shrinkage' ? 'e.g. Product found damaged in storage, writing off 3 units...'
                  : resolutionType === 'transit_loss' ? 'e.g. 2 bags fell from truck, driver acknowledges and will compensate...'
                  : resolutionType === 'sender_error' ? 'e.g. Warehouse manager confirmed they only packed 8 instead of 10...'
                  : 'Describe the resolution...'}
                rows={3} className="w-full text-sm border border-slate-200 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-emerald-300 resize-none"
                data-testid="resolve-note-input" />
            </div>

            {/* Authorization PIN */}
            <div className="border-t pt-3">
              <label className="text-xs font-semibold text-slate-700 block mb-1 flex items-center gap-1.5">
                <ShieldCheck size={13} className="text-blue-600" /> Authorization Required
              </label>
              <p className="text-[10px] text-slate-400 mb-2">
                Enter Admin PIN, Manager PIN, or Time-based PIN to authorize this resolution.
              </p>
              <Input
                type="password"
                value={resolvePin}
                onChange={e => setResolvePin(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleResolve()}
                placeholder="Enter PIN..."
                className="h-9 text-sm font-mono tracking-widest"
                data-testid="resolve-pin-input"
              />
            </div>

            <div className="flex justify-end gap-2 pt-2 border-t">
              <Button variant="outline" onClick={() => setResolveDialog(null)}>Cancel</Button>
              <Button onClick={handleResolve}
                disabled={!resolveNote.trim() || !resolutionType || !resolvePin || actionLoading
                  || (['transit_loss', 'insurance_claim'].includes(resolutionType) && !accountableParty.trim())}
                className="bg-emerald-600 text-white" data-testid="confirm-resolve-btn">
                <CheckCircle2 size={12} className="mr-1" /> Resolve
              </Button>
            </div>
          </div>
          );
          })()}
        </DialogContent>
      </Dialog>

      {/* ═══════════════════════════════════════════════════════════════════
          SENDER CONFIRMATION DIALOG
         ═══════════════════════════════════════════════════════════════════ */}
      <Dialog open={!!senderConfirmDialog} onOpenChange={() => setSenderConfirmDialog(null)}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
              <ShieldCheck size={18} className="text-blue-600" /> Sender Confirmation
            </DialogTitle>
          </DialogHeader>
          {senderConfirmDialog && (
          <div className="space-y-4">
            <p className="text-sm text-slate-600 bg-blue-50 border border-blue-200 rounded-lg p-3">
              The <b>sender</b> confirms the actual quantities they packed and shipped.
              If the sender&apos;s confirmed qty matches the receiver&apos;s count, the variance is <b>automatically cancelled</b> (no real loss).
            </p>

            <div className="border rounded-lg overflow-hidden">
              <table className="w-full text-xs">
                <thead className="bg-slate-50">
                  <tr>
                    <th className="text-left px-3 py-2 font-medium">Product</th>
                    <th className="text-right px-3 py-2 font-medium">Originally Logged</th>
                    <th className="text-right px-3 py-2 font-medium">Receiver Got</th>
                    <th className="text-right px-3 py-2 font-medium text-blue-600">Sender Actually Sent</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {senderConfirmItems.map((item, i) => {
                    const matches = item.sender_confirmed_qty === item.qty_received;
                    return (
                    <tr key={i} className={matches ? 'bg-emerald-50/40' : 'bg-amber-50/40'}>
                      <td className="px-3 py-2 font-medium">{item.product_name} <span className="text-slate-400 text-[10px]">{item.sku}</span></td>
                      <td className="px-3 py-2 text-right font-mono">{item.qty_ordered}</td>
                      <td className="px-3 py-2 text-right font-mono font-bold">{item.qty_received}</td>
                      <td className="px-3 py-2 text-right">
                        <CalcInput className="h-7 w-20 text-sm text-center ml-auto font-mono font-bold"
 value={item.sender_confirmed_qty}
 onChange={(v) => {
 const val = parseFloat(v) || 0;
 setSenderConfirmItems(prev => prev.map((p, j) => j === i ? { ...p, sender_confirmed_qty: val } : p));
 }}
 data-testid={`sender-confirm-qty-${i}`} />
                        {matches && <span className="text-[10px] text-emerald-600 font-medium block mt-0.5">Matches receiver</span>}
                      </td>
                    </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {senderConfirmItems.every(i => i.sender_confirmed_qty === i.qty_received) && (
              <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-3 text-sm text-emerald-700">
                <CheckCircle2 size={14} className="inline mr-1.5" />
                All quantities match! This will <b>auto-resolve</b> the ticket as &quot;Sender Error — No Loss&quot;.
              </div>
            )}

            <div>
              <label className="text-xs text-slate-500 font-medium block mb-1">Confirmation Note (optional)</label>
              <Input value={senderConfirmNote} onChange={e => setSenderConfirmNote(e.target.value)}
                placeholder="e.g. Warehouse manager confirmed packing count..."
                className="h-9 text-sm" data-testid="sender-confirm-note" />
            </div>

            <div className="flex justify-end gap-2 pt-2 border-t">
              <Button variant="outline" onClick={() => setSenderConfirmDialog(null)}>Cancel</Button>
              <Button onClick={handleSenderConfirm} disabled={actionLoading}
                className="bg-blue-600 text-white" data-testid="confirm-sender-btn">
                <ShieldCheck size={12} className="mr-1" /> Confirm Sender Quantities
              </Button>
            </div>
          </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Transfer Detail Modal */}
      <TransferDetailModal
        transfer={varianceViewTransfer}
        open={!!varianceViewTransfer}
        onOpenChange={(open) => { if (!open) setVarianceViewTransfer(null); }}
        branches={branches || []}
      />
    </div>
  );
}
