import { useState, useEffect } from 'react';
import { api } from '../contexts/AuthContext';
import { formatPHP } from '../lib/utils';
import PrintEngine from '../lib/PrintEngine';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from './ui/dialog';
import { Badge } from './ui/badge';
import { Printer, FileText } from 'lucide-react';
import { toast } from 'sonner';
import { localTodayStr } from '../lib/dateFormat';

export default function CustomerStatementModal({ open, onOpenChange, customer }) {
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState(localTodayStr());
  const [statement, setStatement] = useState(null);
  const [loading, setLoading] = useState(false);
  const [businessInfo, setBusinessInfo] = useState({});

  useEffect(() => {
    api.get('/settings/business-info').then(r => setBusinessInfo(r.data)).catch(() => {});
  }, []);

  const loadStatement = async () => {
    if (!customer?.id) return;
    setLoading(true);
    try {
      const res = await api.get(`/customers/${customer.id}/statement`, {
        params: { date_from: dateFrom || undefined, date_to: dateTo || undefined }
      });
      setStatement(res.data);
    } catch { toast.error('Failed to load statement'); }
    setLoading(false);
  };

  const handlePrint = () => {
    if (!statement) return;
    PrintEngine.print({
      type: 'statement',
      data: { ...statement, customer_name: customer?.name, customer_phone: customer?.phone, customer_address: customer?.address },
      format: 'full_page',
      businessInfo,
    });
  };

  const typeLabel = (t) => {
    const map = {
      interest_charge: { text: 'Interest', cls: 'bg-amber-100 text-amber-700' },
      penalty_charge: { text: 'Penalty', cls: 'bg-red-100 text-red-700' },
      farm_expense: { text: 'Farm', cls: 'bg-green-100 text-green-700' },
      payment: { text: 'Payment', cls: 'bg-emerald-100 text-emerald-700' },
    };
    return map[t] || { text: 'Invoice', cls: 'bg-blue-100 text-blue-700' };
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) setStatement(null); }}>
      <DialogContent className="sm:max-w-3xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle style={{ fontFamily: 'Manrope' }} className="flex items-center gap-2">
            <FileText size={18} className="text-[#1A4D2E]" />
            Statement of Account — {customer?.name}
          </DialogTitle>
        </DialogHeader>

        {/* Date Range */}
        <div className="flex items-end gap-3 flex-wrap">
          <div>
            <Label className="text-xs">From</Label>
            <Input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} className="h-9 w-36" />
          </div>
          <div>
            <Label className="text-xs">To</Label>
            <Input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} className="h-9 w-36" />
          </div>
          <Button onClick={loadStatement} disabled={loading} className="bg-[#1A4D2E] hover:bg-[#14532d] text-white h-9">
            {loading ? 'Loading...' : 'Generate Statement'}
          </Button>
          {statement && (
            <Button variant="outline" onClick={handlePrint} className="h-9 gap-2">
              <Printer size={14} /> Print
            </Button>
          )}
        </div>

        {statement && (
          <div className="mt-4 space-y-4" id="statement-print-area">
            {/* Print header */}
            <div className="print:block hidden text-center mb-6">
              <h2 className="text-xl font-bold">STATEMENT OF ACCOUNT</h2>
              <p className="text-lg">{customer?.name}</p>
              {customer?.address && <p>{customer.address}</p>}
              <p className="text-sm text-slate-500 mt-1">
                {statement.date_from ? `${statement.date_from} to ${statement.date_to}` : `As of ${statement.statement_date}`}
              </p>
            </div>

            {/* Customer summary */}
            <div className="grid grid-cols-3 gap-3 bg-slate-50 rounded-lg p-3">
              <div>
                <p className="text-xs text-slate-500">Customer</p>
                <p className="font-semibold text-sm">{customer?.name}</p>
                {customer?.phone && <p className="text-xs text-slate-400">{customer.phone}</p>}
              </div>
              <div>
                <p className="text-xs text-slate-500">Statement Date</p>
                <p className="font-semibold text-sm">{statement.statement_date}</p>
                {(dateFrom || dateTo) && <p className="text-xs text-slate-400">{dateFrom || '—'} to {dateTo}</p>}
              </div>
              <div className="text-right">
                <p className="text-xs text-slate-500">Balance Due</p>
                <p className={`text-xl font-bold ${statement.closing_balance > 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                  {formatPHP(statement.closing_balance)}
                </p>
              </div>
            </div>

            {/* Transaction table */}
            {statement.transactions.length === 0 ? (
              <p className="text-center text-slate-400 py-8">No transactions in this period</p>
            ) : (
              <div className="border rounded-lg overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-slate-800 text-white">
                    <tr>
                      <th className="text-left px-3 py-2 text-xs font-semibold">Date</th>
                      <th className="text-left px-3 py-2 text-xs font-semibold">Reference</th>
                      <th className="text-left px-3 py-2 text-xs font-semibold">Description</th>
                      <th className="text-right px-3 py-2 text-xs font-semibold">Charges</th>
                      <th className="text-right px-3 py-2 text-xs font-semibold">Payments</th>
                      <th className="text-right px-3 py-2 text-xs font-semibold">Balance</th>
                    </tr>
                  </thead>
                  <tbody>
                    {/* Opening balance row */}
                    <tr className="bg-slate-50 border-b border-slate-200">
                      <td className="px-3 py-2 text-xs text-slate-400" colSpan={5}>Opening Balance</td>
                      <td className="px-3 py-2 text-right font-medium">{formatPHP(0)}</td>
                    </tr>
                    {statement.transactions.map((t, i) => {
                      const tl = typeLabel(t.type === 'payment' ? 'payment' : t.invoice_id ? 'invoice' : '');
                      return (
                        <tr key={i} className={`border-b border-slate-100 ${t.type === 'payment' ? 'bg-emerald-50/30' : i % 2 === 0 ? '' : 'bg-slate-50/50'}`}>
                          <td className="px-3 py-2 text-xs text-slate-500">{t.date}</td>
                          <td className="px-3 py-2 font-mono text-xs text-blue-600">{t.reference}</td>
                          <td className="px-3 py-2 text-xs">{t.description}</td>
                          <td className="px-3 py-2 text-right text-sm">
                            {t.debit > 0 ? <span className="font-medium">{formatPHP(t.debit)}</span> : <span className="text-slate-300">—</span>}
                          </td>
                          <td className="px-3 py-2 text-right text-sm">
                            {t.credit > 0 ? <span className="font-medium text-emerald-600">{formatPHP(t.credit)}</span> : <span className="text-slate-300">—</span>}
                          </td>
                          <td className={`px-3 py-2 text-right text-sm font-bold ${t.running_balance > 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                            {formatPHP(t.running_balance)}
                          </td>
                        </tr>
                      );
                    })}
                    {/* Closing balance row */}
                    <tr className="bg-slate-800 text-white font-bold">
                      <td className="px-3 py-2 text-xs" colSpan={3}>CLOSING BALANCE</td>
                      <td className="px-3 py-2 text-right text-sm">
                        {formatPHP(statement.transactions.reduce((s, t) => s + t.debit, 0))}
                      </td>
                      <td className="px-3 py-2 text-right text-sm">
                        {formatPHP(statement.transactions.reduce((s, t) => s + t.credit, 0))}
                      </td>
                      <td className={`px-3 py-2 text-right text-lg ${statement.closing_balance > 0 ? 'text-red-300' : 'text-emerald-300'}`}>
                        {formatPHP(statement.closing_balance)}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            )}

            {statement.closing_balance > 0 && (
              <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-center">
                <p className="text-sm text-red-700">
                  Total Amount Due: <strong className="text-lg">{formatPHP(statement.closing_balance)}</strong>
                </p>
                <p className="text-xs text-red-500 mt-1">Please settle your outstanding balance. Thank you.</p>
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
