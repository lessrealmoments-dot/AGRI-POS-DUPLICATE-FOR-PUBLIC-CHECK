/**
 * FundManagementPage — 4-wallet branch fund system
 *
 * Wallet types and their flow rules:
 *   Cashier  → receives cash/check sales; used for expenses/POs; admin capital add only
 *   Safe     → receives close-day transfers; cashier↔safe via manager PIN
 *   Digital  → receives GCash/Maya/digital sales; audit-only, no direct spend
 *   Bank     → receives safe→bank deposits; admin TOTP required; balance hidden from non-admins
 *
 * Allowed transfers (all require authorization + full audit trail):
 *   Cashier → Safe      Manager PIN
 *   Safe → Cashier      Manager PIN
 *   Safe → Bank         TOTP (Time-Based PIN)
 *   Capital (→Cashier)  Admin only
 */
import { useState, useEffect, useCallback } from 'react';
import { useAuth, api } from '../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Badge } from '../components/ui/badge';
import FundTransferDialog from '../components/FundTransferDialog';
import OverageReserveCard from '../components/OverageReserveCard';
import {
  Banknote, Lock, Smartphone, Building2, RefreshCw, ArrowRight,
  ArrowRightLeft, Shield, EyeOff, History, AlertTriangle
} from 'lucide-react';
import { toast } from 'sonner';
import { formatPHP } from '../lib/utils';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

const WALLET_META = {
  cashier: {
    label: 'Cashier Drawer',
    icon: Banknote,
    color: 'text-emerald-600',
    bg: 'bg-emerald-50',
    border: 'border-emerald-200',
    desc: 'Cash & check sales. Expenses & PO payments.',
  },
  safe: {
    label: 'Physical Safe',
    icon: Lock,
    color: 'text-amber-600',
    bg: 'bg-amber-50',
    border: 'border-amber-200',
    desc: 'Close-day transfers from cashier. Can pay expenses.',
  },
  digital: {
    label: 'Digital / E-Wallet',
    icon: Smartphone,
    color: 'text-blue-600',
    bg: 'bg-blue-50',
    border: 'border-blue-200',
    desc: 'GCash, Maya, Bank Transfer. Audit trail only.',
  },
  bank: {
    label: 'Bank Account',
    icon: Building2,
    color: 'text-purple-600',
    bg: 'bg-purple-50',
    border: 'border-purple-200',
    desc: 'Safe→Bank deposits with TOTP (time-based PIN). Balance confidential.',
  },
};

const TRANSFER_TYPES = [
  {
    key: 'cashier_to_safe',
    label: 'Cashier → Safe',
    desc: 'Move cash from drawer to safe at end of shift',
    auth: 'Manager PIN',
    icon: '🔒',
    from: 'cashier', to: 'safe',
  },
  {
    key: 'safe_to_cashier',
    label: 'Safe → Cashier',
    desc: 'Pull cash from safe to replenish drawer',
    auth: 'Manager PIN',
    icon: '💵',
    from: 'safe', to: 'cashier',
  },
  {
    key: 'safe_to_bank',
    label: 'Safe → Bank',
    desc: 'Deposit safe balance to bank account',
    auth: 'TOTP',
    icon: '🏦',
    from: 'safe', to: 'bank',
  },
  {
    key: 'capital_add',
    label: 'Capital Injection',
    desc: 'Add operating capital to cashier or safe — requires admin authorization',
    auth: 'Owner PIN or TOTP',
    icon: '💰',
    to: 'cashier',
  },
];

export default function FundManagementPage() {
  const { currentBranch, user } = useAuth();
  const isAdmin = user?.role === 'admin';

  const [wallets, setWallets] = useState([]);
  const [transfers, setTransfers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState('overview'); // overview | transfers | history

  // Transfer form state
  const [activeTransfer, setActiveTransfer] = useState(null); // TRANSFER_TYPES entry

  // Wallet movements
  const [selectedWallet, setSelectedWallet] = useState(null);
  const [movements, setMovements] = useState([]);
  const [movLoading, setMovLoading] = useState(false);

  const branchId = currentBranch?.id;

  const loadData = useCallback(async () => {
    if (!branchId) { setLoading(false); return; }
    setLoading(true);
    try {
      const [walletsRes, transfersRes] = await Promise.all([
        api.get(`${BACKEND_URL}/api/fund-wallets`, { params: { branch_id: branchId } }),
        api.get(`${BACKEND_URL}/api/fund-transfers`, { params: { branch_id: branchId, limit: 20 } }),
      ]);
      setWallets(walletsRes.data || []);
      setTransfers(transfersRes.data || []);
    } catch { toast.error('Failed to load fund data'); }
    setLoading(false);
  }, [branchId]);

  useEffect(() => { loadData(); }, [loadData]);

  const walletByType = (type) => wallets.find(w => w.type === type);

  const openTransfer = (ttype) => {
    setActiveTransfer(ttype);
  };

  const loadMovements = async (wallet) => {
    setSelectedWallet(wallet);
    setMovLoading(true);
    try {
      const res = await api.get(`${BACKEND_URL}/api/fund-wallets/${wallet.id}/movements`);
      setMovements(res.data || []);
    } catch {}
    setMovLoading(false);
  };

  if (loading) return (
    <div className="flex items-center justify-center h-48 text-slate-400">
      <RefreshCw size={18} className="animate-spin mr-2" /> Loading fund data...
    </div>
  );

  if (!branchId) return (
    <div className="flex items-center justify-center h-48 text-slate-400">
      <AlertTriangle size={18} className="mr-2 text-amber-400" /> Please select a branch to view fund management.
    </div>
  );

  // ── Overview ────────────────────────────────────────────────────────────────
  return (
    <div className="space-y-5 animate-fadeIn" data-testid="fund-management-page">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight" style={{ fontFamily: 'Manrope' }}>Fund Management</h1>
          <p className="text-sm text-slate-500 mt-0.5">{currentBranch?.name} — 4-wallet system</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={loadData}>
            <RefreshCw size={13} className="mr-1.5" /> Refresh
          </Button>
        </div>
      </div>

      {/* 4 Wallet Cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {['cashier', 'safe', 'digital', 'bank'].map(type => {
          const wallet = walletByType(type);
          const meta = WALLET_META[type];
          const Icon = meta.icon;
          const hidden = wallet?.balance_hidden;
          return (
            <Card key={type} className={`border-2 ${meta.border} cursor-pointer hover:shadow-md transition-shadow`}
              onClick={() => wallet && loadMovements(wallet)}>
              <CardContent className="p-4">
                <div className="flex items-center justify-between mb-3">
                  <div className={`w-9 h-9 rounded-xl ${meta.bg} flex items-center justify-center`}>
                    <Icon size={18} className={meta.color} />
                  </div>
                  {type === 'bank' && !isAdmin && (
                    <Badge className="text-[9px] bg-purple-100 text-purple-600">Confidential</Badge>
                  )}
                </div>
                <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1">{meta.label}</p>
                {hidden ? (
                  <div className="flex items-center gap-1.5">
                    <EyeOff size={14} className="text-slate-400" />
                    <span className="text-sm text-slate-400 italic">Balance hidden</span>
                  </div>
                ) : (
                  <p className={`text-xl font-bold font-mono ${meta.color}`} style={{ fontFamily: 'Manrope' }}>
                    {wallet ? formatPHP(wallet.balance || 0) : '—'}
                  </p>
                )}
                <p className="text-[10px] text-slate-400 mt-1 leading-tight">{meta.desc}</p>
                {wallet && (
                  <button className="text-[10px] text-slate-400 hover:text-slate-600 mt-2 flex items-center gap-1">
                    <History size={10} /> View history
                  </button>
                )}
              </CardContent>
            </Card>
          );
        })}
      </div>

      {/* Overage Reserve summary — read-only badge into the Audit Center */}
      <OverageReserveCard branchId={branchId} />

      {/* Movements panel */}
      {selectedWallet && (
        <Card className="border-slate-200">
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-semibold flex items-center gap-2">
                <History size={14} className="text-slate-500" />
                {WALLET_META[selectedWallet.type]?.label || selectedWallet.name} — Transaction History
              </CardTitle>
              <button onClick={() => setSelectedWallet(null)} className="text-slate-400 hover:text-slate-600 text-xs">Close</button>
            </div>
          </CardHeader>
          <CardContent>
            {movLoading ? (
              <div className="text-center py-4"><RefreshCw size={16} className="animate-spin mx-auto text-slate-400" /></div>
            ) : movements.length === 0 ? (
              <p className="text-xs text-slate-400 text-center py-4">No transactions yet</p>
            ) : (
              <div className="space-y-1.5 max-h-64 overflow-y-auto">
                {movements.map(m => (
                  <div key={m.id} className="flex items-center justify-between text-xs py-1.5 border-b border-slate-50 last:border-0">
                    <div>
                      <span className="text-slate-400 mr-2">{m.created_at?.slice(0, 16)?.replace('T', ' ')}</span>
                      <span className="text-slate-600 truncate max-w-[220px]">{m.reference || m.type}</span>
                      {m.platform && <span className="ml-1.5 text-blue-500 text-[10px]">{m.platform}</span>}
                      {m.ref_number && <span className="ml-1 text-slate-400 font-mono text-[10px]">#{m.ref_number}</span>}
                    </div>
                    <span className={`font-mono font-semibold shrink-0 ml-3 ${m.amount >= 0 ? 'text-emerald-600' : 'text-red-600'}`}>
                      {m.amount >= 0 ? '+' : ''}{formatPHP(m.amount)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Fund Transfers */}
      <Card className="border-slate-200">
        <CardHeader className="pb-3">
          <CardTitle className="text-base font-semibold flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
            <ArrowRightLeft size={16} className="text-[#1A4D2E]" />
            Fund Transfers
          </CardTitle>
          <p className="text-xs text-slate-500">All transfers require authorization and are permanently logged.</p>
        </CardHeader>
        <CardContent>
          <div className="grid sm:grid-cols-2 gap-3">
            {TRANSFER_TYPES.map(ttype => (
              <button key={ttype.key} onClick={() => openTransfer(ttype)}
                className="flex items-start gap-3 p-3 rounded-xl border border-slate-200 hover:border-[#1A4D2E]/40 hover:bg-slate-50 transition-all text-left">
                <span className="text-xl shrink-0">{ttype.icon}</span>
                <div className="flex-1 min-w-0">
                  <p className="font-semibold text-sm text-slate-800">{ttype.label}</p>
                  <p className="text-[11px] text-slate-400 mt-0.5">{ttype.desc}</p>
                  <span className="inline-flex items-center gap-1 text-[10px] font-medium text-slate-500 mt-1">
                    <Shield size={10} /> {ttype.auth}
                  </span>
                </div>
                <ArrowRight size={14} className="text-slate-400 shrink-0 mt-1" />
              </button>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Transfer History */}
      {transfers.length > 0 && (
        <Card className="border-slate-200">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold flex items-center gap-2">
              <History size={14} className="text-slate-500" /> Recent Transfers
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {transfers.slice(0, 8).map(t => (
                <div key={t.id} className="flex items-center justify-between text-xs py-1.5 border-b border-slate-50 last:border-0">
                  <div>
                    <span className="font-medium text-slate-700">{t.description}</span>
                    {t.note && <span className="text-slate-400 ml-2">— {t.note}</span>}
                    <p className="text-[10px] text-slate-400 mt-0.5">
                      By {t.performed_by_name} · Auth: {t.authorized_by} · {t.created_at?.slice(0, 16)?.replace('T', ' ')}
                    </p>
                  </div>
                  <span className="font-bold font-mono text-[#1A4D2E] shrink-0 ml-3">{formatPHP(t.amount)}</span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Transfer Dialog */}
      <FundTransferDialog
        open={!!activeTransfer}
        onClose={() => setActiveTransfer(null)}
        transferType={activeTransfer}
        walletByType={walletByType}
        branchId={branchId}
        onSuccess={loadData}
      />
    </div>
  );
}
