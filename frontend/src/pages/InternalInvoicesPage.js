import React, { useState, useEffect, useCallback } from 'react';
import { useAuth, api } from '../contexts/AuthContext';
import { formatPHP, fmtDate } from '../lib/utils';
import { Card, CardContent } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Badge } from '../components/ui/badge';
import { Input } from '../components/ui/input';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs';
import {
  FileText, CreditCard, AlertTriangle, CheckCircle2, Clock, RefreshCw,
  Building2, ArrowRight, Banknote
} from 'lucide-react';
import { toast } from 'sonner';
import InvoiceDetailModal from '../components/InvoiceDetailModal';

const STATUS_COLORS = {
  prepared: 'bg-slate-100 text-slate-600',
  sent: 'bg-blue-100 text-blue-700',
  received: 'bg-amber-100 text-amber-700',
  paid: 'bg-emerald-100 text-emerald-700',
};

export default function InternalInvoicesPage() {
  const { branches, user } = useAuth();
  const isAdmin = user?.role === 'admin';

  const [invoices, setInvoices] = useState([]);
  const [loading, setLoading] = useState(false);
  const [invoiceModalOpen, setInvoiceModalOpen] = useState(false);
  const [selectedInvoiceNumber, setSelectedInvoiceNumber] = useState(null);
  const openDetailModal = (num) => { setSelectedInvoiceNumber(num); setInvoiceModalOpen(true); };
  const [summary, setSummary] = useState(null);
  const [tab, setTab] = useState('all');
  const [payDialog, setPayDialog] = useState(null);
  const [payNote, setPayNote] = useState('');
  const [paying, setPaying] = useState(false);

  const loadInvoices = useCallback(async () => {
    setLoading(true);
    try {
      const params = { limit: 100 };
      if (tab === 'unpaid') params.payment_status = 'unpaid';
      if (tab === 'paid') params.payment_status = 'paid';
      const [invRes, sumRes] = await Promise.all([
        api.get('/internal-invoices', { params }),
        api.get('/internal-invoices/summary'),
      ]);
      setInvoices(invRes.data.invoices || []);
      setSummary(sumRes.data);
    } catch { toast.error('Failed to load invoices'); }
    setLoading(false);
  }, [tab]);

  useEffect(() => { loadInvoices(); }, [loadInvoices]);

  const handlePay = async () => {
    if (!payDialog) return;
    setPaying(true);
    try {
      const res = await api.post(`/internal-invoices/${payDialog.id}/pay`, { note: payNote });
      toast.success(res.data.message);
      setPayDialog(null);
      setPayNote('');
      loadInvoices();
    } catch (e) {
      const detail = e.response?.data?.detail;
      if (typeof detail === 'object' && detail.type === 'insufficient_funds') {
        toast.error(`Insufficient bank balance: ${detail.message}`);
      } else {
        toast.error(typeof detail === 'string' ? detail : 'Payment failed');
      }
    }
    setPaying(false);
  };

  const getBranchName = (id) => branches.find(b => b.id === id)?.name || id?.slice(0, 8) || '—';

  const now = new Date();
  const isOverdue = (inv) => inv.payment_status === 'unpaid' && inv.due_date && new Date(inv.due_date) < now;
  const isDueSoon = (inv) => {
    if (inv.payment_status !== 'unpaid' || !inv.due_date) return false;
    const due = new Date(inv.due_date);
    const days = (due - now) / (1000 * 60 * 60 * 24);
    return days >= 0 && days <= 7;
  };

  return (
    <div className="space-y-5 animate-fadeIn" data-testid="internal-invoices-page">
      <div>
        <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
          <FileText size={22} className="text-[#1A4D2E]" /> Internal Invoices
        </h1>
        <p className="text-sm text-slate-500 mt-0.5">Branch-to-branch billing from stock transfers</p>
      </div>

      {/* Summary Cards */}
      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <Card className="border-amber-200 bg-amber-50/50">
            <CardContent className="p-4">
              <p className="text-xs text-amber-600 font-medium uppercase tracking-wider">Total Payable</p>
              <p className="text-xl font-bold text-amber-800 font-mono mt-1">{formatPHP(summary.payable.total)}</p>
              <p className="text-[10px] text-amber-500 mt-0.5">{summary.payable.count} invoice{summary.payable.count !== 1 ? 's' : ''}</p>
            </CardContent>
          </Card>
          <Card className={`border-red-200 ${summary.payable.overdue_count > 0 ? 'bg-red-50/50' : 'bg-slate-50/50'}`}>
            <CardContent className="p-4">
              <p className="text-xs text-red-600 font-medium uppercase tracking-wider">Overdue</p>
              <p className="text-xl font-bold text-red-700 font-mono mt-1">{formatPHP(summary.payable.overdue_total)}</p>
              <p className="text-[10px] text-red-500 mt-0.5">{summary.payable.overdue_count} overdue</p>
            </CardContent>
          </Card>
          <Card className="border-blue-200 bg-blue-50/50">
            <CardContent className="p-4">
              <p className="text-xs text-blue-600 font-medium uppercase tracking-wider">Due This Week</p>
              <p className="text-xl font-bold text-blue-700 font-mono mt-1">{formatPHP(summary.payable.due_soon_total)}</p>
              <p className="text-[10px] text-blue-500 mt-0.5">{summary.payable.due_soon_count} due soon</p>
            </CardContent>
          </Card>
          <Card className="border-emerald-200 bg-emerald-50/50">
            <CardContent className="p-4">
              <p className="text-xs text-emerald-600 font-medium uppercase tracking-wider">Total Receivable</p>
              <p className="text-xl font-bold text-emerald-700 font-mono mt-1">{formatPHP(summary.receivable.total)}</p>
              <p className="text-[10px] text-emerald-500 mt-0.5">{summary.receivable.count} pending</p>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Invoice List */}
      <Tabs value={tab} onValueChange={setTab}>
        <div className="flex items-center justify-between">
          <TabsList>
            <TabsTrigger value="all"><FileText size={13} className="mr-1" /> All</TabsTrigger>
            <TabsTrigger value="unpaid" data-testid="unpaid-tab"><Clock size={13} className="mr-1" /> Unpaid</TabsTrigger>
            <TabsTrigger value="paid"><CheckCircle2 size={13} className="mr-1" /> Paid</TabsTrigger>
          </TabsList>
          <Button variant="outline" size="sm" onClick={loadInvoices} disabled={loading}>
            <RefreshCw size={13} className={`mr-1.5 ${loading ? 'animate-spin' : ''}`} /> Refresh
          </Button>
        </div>

        <TabsContent value={tab} className="mt-3">
          <Card className="border-slate-200">
            <Table>
              <TableHeader>
                <TableRow className="bg-slate-50">
                  <TableHead className="text-xs uppercase text-slate-500">Invoice #</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500">Transfer</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500">From</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500">To</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500 text-right">Amount</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500">Status</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500">Due Date</TableHead>
                  <TableHead className="w-32"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {loading && (
                  <TableRow><TableCell colSpan={8} className="text-center py-8 text-slate-400">Loading...</TableCell></TableRow>
                )}
                {!loading && invoices.length === 0 && (
                  <TableRow><TableCell colSpan={8} className="text-center py-8 text-slate-400">No invoices found.</TableCell></TableRow>
                )}
                {invoices.map(inv => {
                  const overdue = isOverdue(inv);
                  const dueSoon = isDueSoon(inv);
                  return (
                    <TableRow key={inv.id} className={`hover:bg-slate-50 ${overdue ? 'bg-red-50/30' : dueSoon ? 'bg-amber-50/30' : ''}`}>
                      <TableCell className="font-mono text-sm text-blue-600" data-testid={`inv-${inv.id}`}>
                        <button className="hover:underline font-semibold" onClick={() => openDetailModal(inv.invoice_number)}>
                          {inv.invoice_number}
                        </button>
                      </TableCell>
                      <TableCell className="font-mono text-xs text-slate-500">{inv.transfer_number}</TableCell>
                      <TableCell className="text-sm">
                        <span className="flex items-center gap-1">
                          <Building2 size={12} className="text-slate-400" />
                          {inv.from_branch_name || getBranchName(inv.from_branch_id)}
                        </span>
                      </TableCell>
                      <TableCell className="text-sm font-medium">
                        <span className="flex items-center gap-1">
                          <ArrowRight size={12} className="text-slate-400" />
                          {inv.to_branch_name || getBranchName(inv.to_branch_id)}
                        </span>
                      </TableCell>
                      <TableCell className="text-right font-mono font-bold">{formatPHP(inv.grand_total)}</TableCell>
                      <TableCell>
                        <Badge className={`text-[10px] ${STATUS_COLORS[inv.payment_status === 'paid' ? 'paid' : inv.status]}`}>
                          {inv.payment_status === 'paid' ? 'paid' : inv.status}
                        </Badge>
                        {overdue && <Badge className="ml-1 text-[10px] bg-red-100 text-red-700">Overdue</Badge>}
                        {dueSoon && !overdue && <Badge className="ml-1 text-[10px] bg-amber-100 text-amber-700">Due Soon</Badge>}
                      </TableCell>
                      <TableCell className={`text-xs ${overdue ? 'text-red-600 font-bold' : 'text-slate-400'}`}>
                        {inv.due_date?.slice(0, 10)}
                      </TableCell>
                      <TableCell>
                        {inv.payment_status !== 'paid' && isAdmin && (
                          <Button size="sm" onClick={() => { setPayDialog(inv); setPayNote(''); }}
                            className="h-7 bg-emerald-600 hover:bg-emerald-700 text-white text-xs"
                            data-testid={`pay-btn-${inv.id}`}>
                            <Banknote size={12} className="mr-1" /> Pay Now
                          </Button>
                        )}
                        {inv.payment_status === 'paid' && (
                          <span className="flex items-center gap-1 text-xs text-emerald-600">
                            <CheckCircle2 size={12} /> Paid {fmtDate(inv.paid_at)}
                          </span>
                        )}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </Card>
        </TabsContent>
      </Tabs>

      {/* Pay Invoice Dialog */}
      {payDialog && (
        <Dialog open={!!payDialog} onOpenChange={() => setPayDialog(null)}>
          <DialogContent className="max-w-md">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
                <CreditCard size={18} className="text-emerald-600" />
                Pay Internal Invoice
              </DialogTitle>
            </DialogHeader>
            <div className="space-y-4">
              <div className="bg-slate-50 rounded-lg p-4 space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="text-slate-500">Invoice</span>
                  <span className="font-mono font-bold">{payDialog.invoice_number}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-slate-500">Transfer</span>
                  <span className="font-mono text-xs">{payDialog.transfer_number}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-slate-500">From</span>
                  <span className="font-medium">{payDialog.from_branch_name || getBranchName(payDialog.from_branch_id)}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-slate-500">To (pays)</span>
                  <span className="font-medium">{payDialog.to_branch_name || getBranchName(payDialog.to_branch_id)}</span>
                </div>
                <div className="border-t pt-2 mt-2 flex justify-between">
                  <span className="text-slate-700 font-semibold">Amount</span>
                  <span className="text-lg font-bold text-emerald-700 font-mono">{formatPHP(payDialog.received_total || payDialog.grand_total)}</span>
                </div>
              </div>

              <div className="flex items-center gap-2 px-3 py-2 bg-blue-50 border border-blue-200 rounded-lg text-xs text-blue-700">
                <Banknote size={14} />
                <span>This will deduct from <b>{payDialog.to_branch_name || getBranchName(payDialog.to_branch_id)}</b> bank and credit <b>{payDialog.from_branch_name || getBranchName(payDialog.from_branch_id)}</b> bank.</span>
              </div>

              <div>
                <label className="text-xs text-slate-500">Payment Note (optional)</label>
                <Input value={payNote} onChange={e => setPayNote(e.target.value)}
                  placeholder="e.g., Manual settlement" className="mt-1" />
              </div>

              <div className="flex gap-2 pt-1">
                <Button variant="outline" className="flex-1" onClick={() => setPayDialog(null)}>Cancel</Button>
                <Button onClick={handlePay} disabled={paying}
                  className="flex-1 bg-emerald-600 hover:bg-emerald-700 text-white"
                  data-testid="confirm-pay-btn">
                  {paying ? <RefreshCw size={13} className="animate-spin mr-1.5" /> : <CheckCircle2 size={14} className="mr-1.5" />}
                  Confirm Payment
                </Button>
              </div>
            </div>
          </DialogContent>
        </Dialog>
      )}
      <InvoiceDetailModal compact
        open={invoiceModalOpen}
        onOpenChange={setInvoiceModalOpen}
        invoiceNumber={selectedInvoiceNumber}
      />
    </div>
  );
}
