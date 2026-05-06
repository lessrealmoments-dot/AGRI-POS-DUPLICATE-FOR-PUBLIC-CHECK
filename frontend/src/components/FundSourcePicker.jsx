import React, { useEffect, useState } from 'react';
import { api } from '../contexts/AuthContext';
import { formatPHP } from '../lib/utils';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Banknote, ShieldCheck, Smartphone, Landmark, Lock, AlertTriangle } from 'lucide-react';

/**
 * 4-tile fund-source picker for expense/cashout/farm modals.
 *
 *   ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐
 *   │ 💵 Cashier │  │ 🛡️ Safe    │  │ 📱 Digital │  │ 🏦 Bank    │
 *   │  ₱45,200   │  │  ₱120,000  │  │  ₱15,400 🔒│  │   ••••• 🔒 │
 *   └────────────┘  └────────────┘  └────────────┘  └────────────┘
 *
 *  - Cashier (green) and Safe (amber) → no PIN, role auth only
 *  - Digital (purple) and Bank (blue) → admin PIN or TOTP required
 *  - Bank balance hidden from non-admins
 *
 * Props:
 *   value:        "cashier" | "safe" | "digital" | "bank"
 *   onChange:     (newSource) => void
 *   pin:          PIN string (only used for digital/bank)
 *   onPinChange:  (pin) => void
 *   branchId:     current branch (used to fetch wallet balances)
 *   amount:       optional — used to highlight insufficient funds
 */
const SOURCES = [
  {
    key: 'cashier',
    label: 'Cashier Drawer',
    sublabel: 'Daily till',
    icon: Banknote,
    accent: 'emerald',
    protected: false,
  },
  {
    key: 'safe',
    label: 'Physical Safe',
    sublabel: 'Vault / lock-box',
    icon: ShieldCheck,
    accent: 'amber',
    protected: false,
  },
  {
    key: 'digital',
    label: 'Digital / E-Wallet',
    sublabel: 'GCash · Maya · etc',
    icon: Smartphone,
    accent: 'violet',
    protected: true,
  },
  {
    key: 'bank',
    label: 'Bank Account',
    sublabel: 'Wire / cheque',
    icon: Landmark,
    accent: 'sky',
    protected: true,
    hideBalance: true,
  },
];

const ACCENT_RING = {
  emerald: 'ring-emerald-500 bg-emerald-50 border-emerald-400',
  amber: 'ring-amber-500 bg-amber-50 border-amber-400',
  violet: 'ring-violet-500 bg-violet-50 border-violet-400',
  sky: 'ring-sky-500 bg-sky-50 border-sky-400',
};
const ACCENT_TEXT = {
  emerald: 'text-emerald-700',
  amber: 'text-amber-700',
  violet: 'text-violet-700',
  sky: 'text-sky-700',
};
const ACCENT_BG = {
  emerald: 'bg-emerald-100 text-emerald-600',
  amber: 'bg-amber-100 text-amber-600',
  violet: 'bg-violet-100 text-violet-600',
  sky: 'bg-sky-100 text-sky-600',
};
const BANNER_BG = {
  emerald: 'bg-emerald-50 border-emerald-200 text-emerald-800',
  amber: 'bg-amber-50 border-amber-200 text-amber-800',
  violet: 'bg-violet-50 border-violet-200 text-violet-800',
  sky: 'bg-sky-50 border-sky-200 text-sky-800',
};

export default function FundSourcePicker({
  value = 'cashier',
  onChange,
  pin = '',
  onPinChange,
  branchId,
  amount = 0,
}) {
  const [wallets, setWallets] = useState([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancel = false;
    if (!branchId) { setWallets([]); setLoaded(true); return; }
    setLoaded(false);
    api.get('/fund-wallets', { params: { branch_id: branchId } })
      .then(res => { if (!cancel) setWallets(res.data || []); })
      .catch(() => { if (!cancel) setWallets([]); })
      .finally(() => { if (!cancel) setLoaded(true); });
    return () => { cancel = true; };
  }, [branchId]);

  const balanceFor = (key) => {
    const w = wallets.find(x => x.type === key && x.active !== false);
    if (!w) return null;
    if (w.balance_hidden) return { hidden: true };
    return { value: Number(w.balance || 0) };
  };

  const selected = SOURCES.find(s => s.key === value) || SOURCES[0];
  const selectedBal = balanceFor(value);
  const insufficient =
    selectedBal && !selectedBal.hidden && amount > 0 && (selectedBal.value || 0) < amount;

  return (
    <div className="space-y-3" data-testid="fund-source-picker">
      <div>
        <Label className="text-xs font-semibold text-slate-700 uppercase tracking-wider flex items-center gap-1.5">
          <Banknote size={13} /> Fund Source
        </Label>
        <p className="text-[11px] text-slate-500 mt-0.5">
          Where is the money coming from? Digital & Bank require admin authorization.
        </p>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        {SOURCES.map((s) => {
          const Icon = s.icon;
          const isSel = value === s.key;
          const bal = balanceFor(s.key);
          return (
            <button
              type="button"
              key={s.key}
              onClick={() => onChange?.(s.key)}
              data-testid={`fund-source-${s.key}`}
              className={`relative rounded-xl border-2 p-3 text-left transition-all ${
                isSel
                  ? `ring-2 ${ACCENT_RING[s.accent]}`
                  : 'border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50'
              }`}
            >
              {s.protected && (
                <span className="absolute top-1.5 right-1.5 inline-flex items-center gap-0.5 text-[9px] font-semibold uppercase tracking-wide bg-slate-900 text-white rounded px-1.5 py-0.5">
                  <Lock size={9} /> PIN
                </span>
              )}
              <div className={`w-8 h-8 rounded-lg flex items-center justify-center mb-2 ${ACCENT_BG[s.accent]}`}>
                <Icon size={16} />
              </div>
              <p className={`text-xs font-semibold leading-tight ${isSel ? ACCENT_TEXT[s.accent] : 'text-slate-800'}`}>
                {s.label}
              </p>
              <p className="text-[10px] text-slate-400 mt-0.5">{s.sublabel}</p>
              <div className="mt-1.5 text-xs font-mono tabular-nums">
                {!loaded ? (
                  <span className="text-slate-300">—</span>
                ) : s.hideBalance || bal?.hidden ? (
                  <span className="text-slate-400 italic text-[10px]">Balance hidden</span>
                ) : bal ? (
                  <span className={`font-bold ${isSel ? ACCENT_TEXT[s.accent] : 'text-slate-700'}`}>
                    {formatPHP(bal.value)}
                  </span>
                ) : (
                  <span className="text-slate-300 italic text-[10px]">No wallet</span>
                )}
              </div>
            </button>
          );
        })}
      </div>

      {/* Selected-source banner */}
      <div
        className={`rounded-lg border-2 px-4 py-2.5 flex items-center justify-between gap-3 ${BANNER_BG[selected.accent]}`}
        data-testid="fund-source-banner"
      >
        <div className="flex items-center gap-2.5">
          <selected.icon size={18} />
          <div>
            <p className="text-sm font-bold leading-tight">
              Paying from <span className="uppercase">{selected.label}</span>
            </p>
            {!selected.hideBalance && selectedBal && !selectedBal.hidden && (
              <p className="text-[11px] opacity-90 leading-tight">
                Available: <span className="font-mono font-semibold">{formatPHP(selectedBal.value)}</span>
                {amount > 0 && <span className="ml-2 opacity-80">·  After: <span className="font-mono">{formatPHP(Math.max(0, (selectedBal.value || 0) - amount))}</span></span>}
              </p>
            )}
            {selected.hideBalance && (
              <p className="text-[11px] opacity-80 leading-tight italic">
                Balance hidden — admin authorization required
              </p>
            )}
          </div>
        </div>
        {insufficient && (
          <span className="inline-flex items-center gap-1 text-[11px] font-bold bg-white/70 text-red-700 rounded px-2 py-1" data-testid="fund-source-insufficient">
            <AlertTriangle size={12} /> Insufficient
          </span>
        )}
      </div>

      {/* PIN input for protected sources */}
      {selected.protected && (
        <div className="rounded-lg border-2 border-dashed border-slate-300 bg-slate-50 p-3 space-y-1.5" data-testid="fund-source-pin-section">
          <Label className="text-xs font-semibold text-slate-700 flex items-center gap-1.5">
            <Lock size={12} className="text-slate-500" />
            Admin PIN or TOTP <span className="text-red-500">*</span>
          </Label>
          <Input
            type="password"
            inputMode="numeric"
            autoComplete="one-time-code"
            value={pin}
            onChange={(e) => onPinChange?.(e.target.value)}
            placeholder={`Enter admin PIN to authorize ${selected.label}`}
            className="h-10 text-center text-base font-mono tracking-widest bg-white"
            data-testid="fund-source-pin-input"
          />
          <p className="text-[10px] text-slate-500">
            Only Admin PIN or Time-Based OTP are accepted for this fund source.
          </p>
        </div>
      )}
    </div>
  );
}
