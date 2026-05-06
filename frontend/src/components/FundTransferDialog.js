/**
 * FundTransferDialog — Extracted from FundManagementPage inline dialog.
 * Reusable fund transfer dialog for Cashier↔Safe, Safe→Bank, and Capital Injection.
 *
 * Props:
 *   open           – boolean (is dialog visible)
 *   onClose        – fn() close dialog
 *   transferType   – TRANSFER_TYPES entry object { key, label, desc, auth, icon, from, to }
 *   walletByType   – fn(type) → wallet object with .balance
 *   branchId       – current branch UUID
 *   onSuccess      – fn() called after successful transfer
 */
import { useEffect, useState } from 'react';
import { api } from '../contexts/AuthContext';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Shield, RefreshCw, ArrowRightLeft } from 'lucide-react';
import { toast } from 'sonner';
import { formatPHP } from '../lib/utils';
import CalcInput from './CalcInput';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

const WALLET_LABELS = {
  cashier: 'Cashier Drawer',
  safe: 'Physical Safe',
  digital: 'Digital / E-Wallet',
  bank: 'Bank Account',
};

export default function FundTransferDialog({ open, onClose, transferType, walletByType, branchId, onSuccess }) {
  const todayLocal = () => {
    const d = new Date();
    const yyyy = d.getFullYear();
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return `${yyyy}-${mm}-${dd}`;
  };
  const tomorrowLocal = () => {
    const d = new Date(); d.setDate(d.getDate() + 1);
    const yyyy = d.getFullYear();
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return `${yyyy}-${mm}-${dd}`;
  };
  const [amount, setAmount] = useState('');
  const [note, setNote] = useState('');
  const [managerPin, setManagerPin] = useState('');
  const [totpCode, setTotpCode] = useState('');
  const [ownerPin, setOwnerPin] = useState('');
  const [capitalTarget, setCapitalTarget] = useState('cashier');
  const [transferDate, setTransferDate] = useState(todayLocal());
  const [dateClosed, setDateClosed] = useState(false);
  // Whether today is closed — drives auto-bump to tomorrow + max-date extension.
  const [todayClosed, setTodayClosed] = useState(false);
  const [saving, setSaving] = useState(false);

  // On open: detect if today is closed → auto-bump default to tomorrow so
  // the user can still record (e.g. a Safe → Bank deposit) without being
  // stuck on a closed day.
  useEffect(() => {
    if (!open || !branchId) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await api.get(`${BACKEND_URL}/api/invoices/check-date-closed`, {
          params: { date: todayLocal(), branch_id: branchId },
        });
        if (cancelled) return;
        const isClosed = !!r.data.closed;
        setTodayClosed(isClosed);
        // Only bump if user hasn't picked a non-default date yet.
        setTransferDate(prev => (prev === todayLocal() && isClosed) ? tomorrowLocal() : prev);
      } catch { /* ignore */ }
    })();
    return () => { cancelled = true; };
  }, [open, branchId]);

  // Validate the selected date against daily-closings — fund movements
  // can only be recorded on an open day.
  useEffect(() => {
    if (!open || !transferDate || !branchId) { setDateClosed(false); return; }
    let cancelled = false;
    (async () => {
      try {
        const r = await api.get(`${BACKEND_URL}/api/invoices/check-date-closed`, {
          params: { date: transferDate, branch_id: branchId },
        });
        if (!cancelled) setDateClosed(!!r.data.closed);
      } catch {
        if (!cancelled) setDateClosed(false);
      }
    })();
    return () => { cancelled = true; };
  }, [open, transferDate, branchId]);

  const reset = () => {
    setAmount(''); setNote(''); setManagerPin('');
    setTotpCode(''); setOwnerPin(''); setCapitalTarget('cashier');
    setTransferDate(todayLocal()); setDateClosed(false); setTodayClosed(false);
  };

  const handleClose = () => { reset(); onClose?.(); };

  const executeTransfer = async () => {
    if (!amount || parseFloat(amount) <= 0) { toast.error('Enter a valid amount'); return; }
    if (!transferType) return;
    if (transferType.key !== 'capital_add' && !note.trim()) { toast.error('Please add a note'); return; }
    if (!transferDate) { toast.error('Select a date'); return; }
    if (dateClosed) { toast.error('Day is already closed — pick an open day'); return; }

    setSaving(true);
    try {
      const payload = {
        branch_id: branchId,
        transfer_type: transferType.key,
        amount: parseFloat(amount),
        note,
        date: transferDate,
        target_wallet: transferType.key === 'capital_add' ? capitalTarget : undefined,
        manager_pin: managerPin || undefined,
        totp_code: totpCode || undefined,
        owner_pin: ownerPin || undefined,
      };
      const res = await api.post(`${BACKEND_URL}/api/fund-transfers`, payload);
      toast.success(res.data.message);
      handleClose();
      onSuccess?.();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Transfer failed');
    }
    setSaving(false);
  };

  if (!open || !transferType) return null;

  return (
    <div className="fixed inset-0 flex items-center justify-center p-4" style={{ backgroundColor: 'rgba(0,0,0,0.6)', zIndex: 9999 }}
      onClick={e => { if (e.target === e.currentTarget) handleClose(); }}
      data-testid="fund-transfer-dialog">
      <div className="bg-white rounded-2xl shadow-2xl w-full p-5 overflow-y-auto" style={{ maxWidth: '400px', maxHeight: '90vh' }}>
        <div className="flex items-center gap-3 mb-4">
          <span className="text-2xl">{transferType.icon}</span>
          <div>
            <p className="font-bold text-slate-800" data-testid="fund-transfer-title">{transferType.label}</p>
            <p className="text-xs text-slate-400">{transferType.desc}</p>
          </div>
        </div>

        {/* Fund availability info */}
        {transferType.from && walletByType && (
          <div className="rounded-xl bg-slate-50 border border-slate-200 px-3 py-2 mb-4">
            <p className="text-xs text-slate-500">Available in {WALLET_LABELS[transferType.from] || transferType.from}:</p>
            <p className="font-bold text-slate-800 font-mono" data-testid="fund-transfer-available">
              {(() => {
                const w = walletByType(transferType.from);
                return w ? formatPHP(w.balance || 0) : '—';
              })()}
            </p>
          </div>
        )}

        <div className="space-y-3">
          <div>
            <Label className="text-xs text-slate-600">Amount (&#8369;) *</Label>
            <CalcInput value={amount} onChange={setAmount}
              placeholder="0.00" className="mt-1 h-10 text-lg font-mono" autoFocus
              data-testid="fund-transfer-amount" />
          </div>
          <div>
            <Label className="text-xs text-slate-600">Date *</Label>
            <Input type="date" value={transferDate}
              onChange={e => setTransferDate(e.target.value)}
              max={todayClosed ? tomorrowLocal() : todayLocal()}
              className="mt-1 h-9"
              data-testid="fund-transfer-date" />
            <p className={`text-[10px] mt-1 ${dateClosed ? 'text-red-600 font-medium' : todayClosed ? 'text-amber-600' : 'text-slate-400'}`}>
              {dateClosed
                ? 'This day is already closed — only open days can receive fund transfers.'
                : todayClosed
                ? `Today is already closed for this branch — defaulting to ${tomorrowLocal()} so you can still record this transfer.`
                : 'Default: today. Only open (unclosed) days are accepted.'}
            </p>
          </div>
          {transferType.key !== 'capital_add' && (
            <div>
              <Label className="text-xs text-slate-600">Note / Reason *</Label>
              <Input value={note} onChange={e => setNote(e.target.value)}
                placeholder="e.g. End of shift cash deposit" className="mt-1 h-9"
                data-testid="fund-transfer-note" />
            </div>
          )}
          {transferType.key === 'capital_add' && (
            <div>
              <Label className="text-xs text-slate-600">Note (optional)</Label>
              <Input value={note} onChange={e => setNote(e.target.value)}
                placeholder="e.g. Initial operating capital" className="mt-1 h-9"
                data-testid="fund-transfer-note" />
            </div>
          )}

          {/* Authorization */}
          {(transferType.key === 'cashier_to_safe' || transferType.key === 'safe_to_cashier') && (
            <div>
              <Label className="text-xs text-slate-600 flex items-center gap-1">
                <Shield size={11} /> Manager PIN *
              </Label>
              <Input type="password" autoComplete="new-password" value={managerPin} onChange={e => setManagerPin(e.target.value)}
                placeholder="Enter manager PIN" className="mt-1 h-9"
                onKeyDown={e => e.key === 'Enter' && executeTransfer()}
                data-testid="fund-transfer-manager-pin" />
            </div>
          )}
          {transferType.key === 'safe_to_bank' && (
            <div>
              <Label className="text-xs text-slate-600 flex items-center gap-1">
                <Shield size={11} /> TOTP Code *
              </Label>
              <Input type="password" autoComplete="new-password" value={totpCode} onChange={e => setTotpCode(e.target.value)}
                placeholder="6-digit authenticator code" className="mt-1 h-9 font-mono text-lg text-center"
                maxLength={6}
                onKeyDown={e => e.key === 'Enter' && executeTransfer()}
                data-testid="fund-transfer-totp" />
              <p className="text-[10px] text-slate-400 mt-1">From your Google Authenticator app</p>
            </div>
          )}
          {transferType.key === 'capital_add' && (
            <div className="space-y-2">
              <div>
                <Label className="text-xs text-slate-600">Deposit Into *</Label>
                <div className="flex gap-2 mt-1">
                  {['cashier', 'safe'].map(t => (
                    <button key={t} onClick={() => setCapitalTarget(t)}
                      className={`flex-1 py-1.5 rounded-lg text-xs font-medium border transition-colors capitalize ${capitalTarget === t ? 'bg-[#1A4D2E] text-white border-[#1A4D2E]' : 'border-slate-200 text-slate-600 hover:bg-slate-50'}`}
                      data-testid={`fund-transfer-target-${t}`}>
                      {t === 'cashier' ? 'Cashier' : 'Safe'}
                    </button>
                  ))}
                </div>
              </div>
              <div>
                <Label className="text-xs text-slate-600 flex items-center gap-1">
                  <Shield size={11} /> Owner PIN or TOTP Code *
                </Label>
                <Input type="password" autoComplete="new-password" value={ownerPin}
                  onChange={e => setOwnerPin(e.target.value)}
                  placeholder="Enter PIN or 6-digit TOTP" className="mt-1 h-9 font-mono"
                  onKeyDown={e => e.key === 'Enter' && executeTransfer()}
                  data-testid="fund-transfer-owner-pin" />
                <p className="text-[10px] text-slate-400 mt-1">
                  If admin is present: Owner PIN. If away: call admin for TOTP code.
                </p>
              </div>
            </div>
          )}
        </div>

        <div className="flex gap-2 mt-5">
          <button onClick={handleClose}
            className="flex-1 py-2.5 rounded-xl border border-slate-200 text-sm text-slate-600 hover:bg-slate-50"
            data-testid="fund-transfer-cancel">Cancel</button>
          <button onClick={executeTransfer} disabled={saving || !amount || dateClosed}
            className="flex-1 py-2.5 rounded-xl bg-[#1A4D2E] hover:bg-[#14532d] text-white text-sm font-semibold disabled:opacity-50 flex items-center justify-center gap-2"
            data-testid="fund-transfer-confirm">
            {saving ? <RefreshCw size={14} className="animate-spin" /> : <ArrowRightLeft size={14} />}
            Confirm Transfer
          </button>
        </div>
      </div>
    </div>
  );
}
