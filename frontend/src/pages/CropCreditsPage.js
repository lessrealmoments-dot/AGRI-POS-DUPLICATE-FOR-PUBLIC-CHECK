/**
 * CropCreditsPage — Charged-to-Crop credit management.
 * Shows all crop credit accounts, allows creating, viewing, paying, and extending.
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import { api, useAuth } from '../contexts/AuthContext';
import { toast } from 'sonner';
import {
  Sprout, Plus, Search, ChevronRight, Calendar, Coins, TrendingUp,
  AlertTriangle, CheckCircle2, Clock, RotateCcw, XCircle,
  ArrowDownCircle, Expand, FileText, Shield, QrCode, X
} from 'lucide-react';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Badge } from '../components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../components/ui/dialog';
import { Label } from '../components/ui/label';
import { Separator } from '../components/ui/separator';
import { ScrollArea } from '../components/ui/scroll-area';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs';
import AuthDialog from '../components/AuthDialog';
import { QRCodeSVG } from 'qrcode.react';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

const formatPHP = (n) => `₱${parseFloat(n || 0).toLocaleString('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const fmt_date = (d) => d ? new Date(d + 'T00:00:00').toLocaleDateString('en-PH', { month: 'short', day: 'numeric', year: 'numeric' }) : '—';

const STATUS_CONFIG = {
  active:   { label: 'Active',   cls: 'bg-emerald-100 text-emerald-700 border-emerald-200', icon: <CheckCircle2 size={11} /> },
  extended: { label: 'Extended', cls: 'bg-amber-100 text-amber-700 border-amber-200',       icon: <Clock size={11} /> },
  overdue:  { label: 'Overdue',  cls: 'bg-red-100 text-red-700 border-red-200',             icon: <AlertTriangle size={11} /> },
  settled:  { label: 'Settled',  cls: 'bg-slate-100 text-slate-600 border-slate-200',       icon: <CheckCircle2 size={11} /> },
};

function StatusBadge({ status }) {
  const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.active;
  return (
    <Badge className={`text-[10px] flex items-center gap-1 border ${cfg.cls}`}>
      {cfg.icon} {cfg.label}
    </Badge>
  );
}

function DaysBar({ daysLeft, totalDays = 127 }) {
  const pct = Math.max(0, Math.min(100, (daysLeft / totalDays) * 100));
  const color = daysLeft <= 0 ? 'bg-red-500' : daysLeft <= 7 ? 'bg-amber-500' : daysLeft <= 15 ? 'bg-yellow-400' : 'bg-emerald-500';
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-[10px] text-slate-500">
        <span>{daysLeft <= 0 ? 'Overdue' : `${daysLeft} days left`}</span>
        <span>Season end</span>
      </div>
      <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
        <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

// ── QR Signature Dialog ─────────────────────────────────────────────────────
function QRSignatureDialog({ open, onClose, creditContext, linkedRecordId, linkedRecordType, onSigned }) {
  const [session, setSession] = useState(null);
  const [loading, setLoading] = useState(false);
  const [signedStatus, setSignedStatus] = useState(null); // null | 'signed' | 'bypassed'
  const [showBypass, setShowBypass] = useState(false);
  const [bypassPin, setBypassPin] = useState('');
  const [bypassReason, setBypassReason] = useState('');
  const [timeLeft, setTimeLeft] = useState(300);
  const pollRef = useRef(null);
  const timerRef = useRef(null);

  useEffect(() => {
    if (open && !session) {
      createSession();
    }
    return () => {
      clearInterval(pollRef.current);
      clearInterval(timerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const createSession = async () => {
    setLoading(true);
    setSignedStatus(null);
    setTimeLeft(300);
    try {
      const res = await api.post('/signatures/session', {
        credit_context: creditContext,
        linked_record_type: linkedRecordType || 'crop_credit',
        linked_record_id: linkedRecordId || '',
      });
      setSession(res.data);
      startPolling(res.data.token);
      startTimer();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Could not create signing session');
    } finally {
      setLoading(false);
    }
  };

  const startTimer = () => {
    clearInterval(timerRef.current);
    let t = 300;
    timerRef.current = setInterval(() => {
      t -= 1;
      setTimeLeft(t);
      if (t <= 0) {
        clearInterval(timerRef.current);
        clearInterval(pollRef.current);
        setSession(null);
      }
    }, 1000);
  };

  const startPolling = (token) => {
    clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const res = await api.get(`/signatures/status/${token}`);
        if (res.data.status === 'signed') {
          clearInterval(pollRef.current);
          clearInterval(timerRef.current);
          setSignedStatus('signed');
          toast.success('Signature received!');
        } else if (res.data.status === 'bypassed') {
          clearInterval(pollRef.current);
          clearInterval(timerRef.current);
          setSignedStatus('bypassed');
        }
      } catch { /* ignore */ }
    }, 2000);
  };

  const handleBypass = async () => {
    if (!bypassPin) { toast.error('PIN required'); return; }
    try {
      await api.post(`/signatures/bypass/${session.id}`, { pin: bypassPin, reason: bypassReason || 'Customer unable to sign' });
      clearInterval(pollRef.current);
      clearInterval(timerRef.current);
      setSignedStatus('bypassed');
      toast.success('Bypassed with manager PIN');
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Invalid PIN');
    }
  };

  const handleConfirm = () => {
    if (onSigned) onSigned(session, signedStatus);
    onClose();
  };

  const signingUrl = session ? `${window.location.origin}/sign/${session.token}` : '';

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-sm">
            <QrCode size={16} /> Digital Signature Request
          </DialogTitle>
        </DialogHeader>

        {signedStatus ? (
          <div className="text-center py-6 space-y-3">
            <div className="w-14 h-14 bg-emerald-100 rounded-full flex items-center justify-center mx-auto">
              <CheckCircle2 className="text-emerald-600" size={28} />
            </div>
            <p className="font-semibold text-slate-800">
              {signedStatus === 'signed' ? 'Signature Received!' : 'Authorized by Manager PIN'}
            </p>
            <p className="text-sm text-slate-500">
              {signedStatus === 'signed'
                ? 'Customer signature has been captured and saved.'
                : 'Staff override recorded. Credit authorized.'}
            </p>
            <Button data-testid="confirm-credit-btn" className="w-full bg-emerald-600 hover:bg-emerald-700 text-white" onClick={handleConfirm}>
              Confirm & Create Credit
            </Button>
          </div>
        ) : session && timeLeft > 0 ? (
          <div className="space-y-4">
            {/* Credit summary */}
            <div className="bg-slate-50 rounded-lg p-3 text-sm space-y-1">
              <div className="flex justify-between">
                <span className="text-slate-500">Customer</span>
                <span className="font-medium">{creditContext?.customer_name}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Amount</span>
                <span className="font-bold">{formatPHP(creditContext?.amount)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-500">Type</span>
                <span className="capitalize">{(creditContext?.credit_type || '').replace(/_/g, ' ')}</span>
              </div>
            </div>

            {/* QR Code */}
            <div className="flex flex-col items-center gap-2">
              <p className="text-xs text-slate-500">Ask customer to scan this QR with their phone:</p>
              <div className="border-2 border-slate-200 rounded-xl p-3 bg-white">
                <QRCodeSVG value={signingUrl} size={180} />
              </div>
              <div className="flex items-center gap-2 text-xs text-slate-400">
                <Clock size={11} />
                <span>Expires in {Math.floor(timeLeft / 60)}:{String(timeLeft % 60).padStart(2, '0')}</span>
              </div>
            </div>

            {/* Regenerate */}
            {timeLeft <= 30 && (
              <Button size="sm" variant="outline" className="w-full text-xs" onClick={createSession}>
                <RotateCcw size={12} className="mr-1" /> Regenerate QR
              </Button>
            )}

            {/* Bypass */}
            {!showBypass ? (
              <button onClick={() => setShowBypass(true)} className="text-xs text-slate-400 hover:text-slate-600 underline w-full text-center">
                Customer can't scan? Use Manager PIN
              </button>
            ) : (
              <div className="border border-slate-200 rounded-lg p-3 space-y-2 bg-slate-50">
                <p className="text-xs font-medium text-slate-700">Manager PIN Override</p>
                <Input placeholder="Manager PIN" type="password" value={bypassPin} onChange={e => setBypassPin(e.target.value)} className="h-8 text-sm" />
                <Input placeholder="Reason (optional)" value={bypassReason} onChange={e => setBypassReason(e.target.value)} className="h-8 text-sm" />
                <div className="flex gap-2">
                  <Button size="sm" className="flex-1 bg-slate-700 text-white" onClick={handleBypass}>Confirm Bypass</Button>
                  <Button size="sm" variant="outline" onClick={() => setShowBypass(false)}>Cancel</Button>
                </div>
              </div>
            )}
          </div>
        ) : timeLeft <= 0 ? (
          <div className="text-center py-4 space-y-3">
            <p className="text-sm text-amber-600 font-medium">QR code expired</p>
            <Button size="sm" onClick={createSession} disabled={loading} className="w-full">
              <RotateCcw size={13} className="mr-1" /> Generate New QR Code
            </Button>
          </div>
        ) : (
          <div className="flex items-center justify-center py-8">
            <div className="text-sm text-slate-400">{loading ? 'Generating QR...' : 'Loading...'}</div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

// ── Create Crop Credit Modal ────────────────────────────────────────────────
function CreateCropCreditModal({ open, onClose, onCreated }) {
  const { currentBranch } = useAuth();
  const [customers, setCustomers] = useState([]);
  const [customerSearch, setCustomerSearch] = useState('');
  const [selectedCustomer, setSelectedCustomer] = useState(null);
  const [blockInfo, setBlockInfo] = useState(null);
  const [form, setForm] = useState({ planting_date: '', initial_amount: '', description: 'Initial crop credit', monthly_interest_rate: '' });
  const [showSignature, setShowSignature] = useState(false);
  const [signatureSession, setSignatureSession] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (customerSearch.length >= 2) {
      api.get('/customers', { params: { search: customerSearch, limit: 10 } })
        .then(r => setCustomers(r.data.customers || []))
        .catch(() => {});
    }
  }, [customerSearch]);

  const selectCustomer = async (c) => {
    setSelectedCustomer(c);
    setCustomerSearch(c.name);
    setCustomers([]);
    // Check block status
    try {
      const res = await api.get(`/crop-credits/check-block/${c.id}`);
      setBlockInfo(res.data);
      // Pre-fill interest rate from customer
      setForm(f => ({ ...f, monthly_interest_rate: c.interest_rate || '' }));
    } catch { setBlockInfo(null); }
  };

  const harvestDate = form.planting_date
    ? new Date(new Date(form.planting_date).getTime() + 127 * 24 * 60 * 60 * 1000).toLocaleDateString('en-PH', { month: 'long', day: 'numeric', year: 'numeric' })
    : null;

  const handleRequestSignature = () => {
    if (!selectedCustomer || !form.planting_date) {
      toast.error('Please fill in all required fields first');
      return;
    }
    setShowSignature(true);
  };

  const handleCreate = async (sigSession, sigStatus) => {
    if (!selectedCustomer) return;
    setLoading(true);
    try {
      const payload = {
        customer_id: selectedCustomer.id,
        planting_date: form.planting_date,
        initial_amount: parseFloat(form.initial_amount) || 0,
        description: form.description,
        monthly_interest_rate: parseFloat(form.monthly_interest_rate) || selectedCustomer.interest_rate || 0,
        branch_id: currentBranch?.id || '',
        signature_session_id: sigSession?.id || '',
      };
      const res = await api.post('/crop-credits', payload);
      // Link signature session to the created crop credit
      if (sigSession?.id) {
        await api.post(`/signatures/session`, {
          credit_context: {
            customer_name: selectedCustomer.name,
            amount: payload.initial_amount,
            credit_type: 'charged_to_crop',
            date: new Date().toISOString().split('T')[0],
          },
          linked_record_type: 'crop_credit',
          linked_record_id: res.data.id,
        }).catch(() => {});
      }
      toast.success('Crop credit created!');
      onCreated(res.data);
      onClose();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Failed to create crop credit');
    } finally {
      setLoading(false);
    }
  };

  const handleDirectCreate = async () => {
    if (!selectedCustomer || !form.planting_date) {
      toast.error('Customer and planting date are required');
      return;
    }
    setLoading(true);
    try {
      const payload = {
        customer_id: selectedCustomer.id,
        planting_date: form.planting_date,
        initial_amount: parseFloat(form.initial_amount) || 0,
        description: form.description,
        monthly_interest_rate: parseFloat(form.monthly_interest_rate) || selectedCustomer.interest_rate || 0,
        branch_id: currentBranch?.id || '',
      };
      const res = await api.post('/crop-credits', payload);
      toast.success('Crop credit created!');
      onCreated(res.data);
      onClose();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Failed to create crop credit');
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <Dialog open={open} onOpenChange={onClose}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-sm">
              <Sprout size={16} className="text-emerald-600" /> New Charged-to-Crop Credit
            </DialogTitle>
          </DialogHeader>

          <div className="space-y-4">
            {/* Customer */}
            <div className="relative">
              <Label className="text-xs">Customer *</Label>
              <Input data-testid="crop-customer-search"
                placeholder="Search customer..."
                value={customerSearch}
                onChange={e => { setCustomerSearch(e.target.value); setSelectedCustomer(null); setBlockInfo(null); }}
                className="mt-1"
              />
              {customers.length > 0 && (
                <div className="absolute z-50 top-full left-0 right-0 bg-white border border-slate-200 rounded-lg shadow-lg mt-1 max-h-40 overflow-y-auto">
                  {customers.map(c => (
                    <button key={c.id} onClick={() => selectCustomer(c)}
                      className="w-full text-left px-3 py-2 hover:bg-slate-50 text-sm border-b border-slate-100 last:border-0">
                      <p className="font-medium">{c.name}</p>
                      <p className="text-xs text-slate-400">{c.phone}</p>
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* Block warning */}
            {blockInfo?.blocked && (
              <div className={`p-3 rounded-lg border text-xs ${blockInfo.reason === 'expired_season_unpaid' ? 'bg-red-50 border-red-200 text-red-700' : 'bg-amber-50 border-amber-200 text-amber-700'}`}>
                <p className="font-medium mb-1">{blockInfo.reason === 'expired_season_unpaid' ? 'Blocked — Unpaid Balance' : 'Active Season Exists'}</p>
                <p>{blockInfo.message}</p>
                {blockInfo.active_credit && (
                  <p className="mt-1">Total Due: {formatPHP(blockInfo.active_credit.total_due)}</p>
                )}
              </div>
            )}

            {/* Planting date */}
            <div>
              <Label className="text-xs">Date Started Growing (Planting Date) *</Label>
              <Input data-testid="planting-date-input" type="date" value={form.planting_date}
                onChange={e => setForm(f => ({ ...f, planting_date: e.target.value }))}
                className="mt-1"
              />
              {harvestDate && (
                <p className="text-[11px] text-emerald-600 mt-1 flex items-center gap-1">
                  <Calendar size={11} /> Expected harvest: <strong>{harvestDate}</strong> (120 days + 7 grace)
                </p>
              )}
            </div>

            {/* Initial amount */}
            <div>
              <Label className="text-xs">Initial Credit Amount (₱)</Label>
              <Input data-testid="initial-amount-input" type="number" placeholder="0.00" value={form.initial_amount}
                onChange={e => setForm(f => ({ ...f, initial_amount: e.target.value }))}
                className="mt-1"
              />
            </div>

            {/* Interest rate */}
            <div>
              <Label className="text-xs">Monthly Interest Rate (%)</Label>
              <Input type="number" step="0.1" placeholder="e.g. 2" value={form.monthly_interest_rate}
                onChange={e => setForm(f => ({ ...f, monthly_interest_rate: e.target.value }))}
                className="mt-1"
              />
              <p className="text-[10px] text-slate-400 mt-0.5">Interest accrues monthly on principal only</p>
            </div>

            {/* Actions */}
            <div className="flex gap-2 pt-2">
              <Button data-testid="request-signature-btn"
                variant="outline" className="flex items-center gap-2 text-xs flex-1" 
                onClick={handleRequestSignature}
                disabled={!selectedCustomer || !form.planting_date || blockInfo?.blocked}>
                <QrCode size={13} /> Get Signature & Create
              </Button>
              <Button data-testid="create-crop-credit-btn"
                className="flex-1 bg-emerald-600 hover:bg-emerald-700 text-white text-xs"
                onClick={handleDirectCreate}
                disabled={loading || !selectedCustomer || !form.planting_date || blockInfo?.blocked}>
                {loading ? 'Creating...' : 'Create (No Signature)'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <QRSignatureDialog
        open={showSignature}
        onClose={() => setShowSignature(false)}
        creditContext={{
          customer_name: selectedCustomer?.name || '',
          amount: parseFloat(form.initial_amount) || 0,
          credit_type: 'charged_to_crop',
          date: new Date().toISOString().split('T')[0],
          description: form.description,
        }}
        onSigned={handleCreate}
      />
    </>
  );
}

// ── Extension Dialog ────────────────────────────────────────────────────────
function ExtensionDialog({ open, onClose, credit, onExtended }) {
  const [reason, setReason] = useState('');
  const [showAuth, setShowAuth] = useState(false);
  const [loading, setLoading] = useState(false);

  const extCount = (credit?.extension_count || 0) + 1;
  const needsTotp = extCount >= 3;

  const newEnd = credit?.season_end_date
    ? new Date(new Date(credit.season_end_date).getTime() + 15 * 24 * 60 * 60 * 1000).toLocaleDateString('en-PH', { month: 'long', day: 'numeric', year: 'numeric' })
    : '—';

  const handleAuthConfirm = async (pin) => {
    if (!reason.trim()) { toast.error('Reason is required'); return; }
    setLoading(true);
    try {
      const res = await api.post(`/crop-credits/${credit.id}/extend`, { reason, pin });
      toast.success(res.data.message);
      onExtended(res.data.credit);
      onClose();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Extension failed');
    } finally {
      setLoading(false);
      setShowAuth(false);
    }
  };

  return (
    <>
      <Dialog open={open} onOpenChange={onClose}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-sm">
              <Clock size={15} /> Extend Crop Season
              {extCount >= 3 && <Badge className="bg-red-100 text-red-700 text-[10px] ml-1">Extension #{extCount} — Owner TOTP Required</Badge>}
            </DialogTitle>
          </DialogHeader>

          <div className="space-y-3">
            {extCount >= 3 && (
              <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-xs text-red-700">
                <p className="font-semibold mb-1">Owner Authorization Required</p>
                <p>Extension #{extCount} requires the Owner's Google Authenticator code. Manager PIN is not accepted.</p>
              </div>
            )}

            <div className="bg-slate-50 rounded-lg p-3 text-sm space-y-1">
              <div className="flex justify-between text-xs">
                <span className="text-slate-500">Customer</span>
                <span className="font-medium">{credit?.customer_name}</span>
              </div>
              <div className="flex justify-between text-xs">
                <span className="text-slate-500">Current end</span>
                <span>{credit?.season_end_date}</span>
              </div>
              <div className="flex justify-between text-xs">
                <span className="text-slate-500">New end</span>
                <span className="font-semibold text-emerald-700">{newEnd}</span>
              </div>
              <div className="flex justify-between text-xs">
                <span className="text-slate-500">Total due</span>
                <span className="font-bold text-red-600">{formatPHP((credit?.principal_balance || 0) + (credit?.accrued_interest || 0))}</span>
              </div>
            </div>

            <div>
              <Label className="text-xs">Reason for Extension *</Label>
              <textarea
                data-testid="extension-reason-input"
                className="w-full mt-1 border border-slate-200 rounded-lg p-2 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-slate-300"
                rows={3}
                placeholder="e.g. Farmer needs more time — harvest delayed due to weather"
                value={reason}
                onChange={e => setReason(e.target.value)}
              />
            </div>

            <Button
              data-testid="extend-season-btn"
              className="w-full bg-amber-600 hover:bg-amber-700 text-white"
              onClick={() => { if (!reason.trim()) { toast.error('Reason is required'); return; } setShowAuth(true); }}
              disabled={loading}
            >
              {needsTotp ? <Shield size={14} className="mr-2" /> : null}
              {needsTotp ? 'Enter Owner TOTP to Extend' : 'Enter Manager PIN to Extend'}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <AuthDialog
        open={showAuth}
        onOpenChange={setShowAuth}
        title={needsTotp ? `Extension #${extCount} — Owner TOTP Required` : `Extension #${extCount} — Manager Approval`}
        description={needsTotp
          ? 'Enter the 6-digit code from the Owner\'s Google Authenticator app.'
          : 'Enter Manager PIN to authorize this extension.'}
        mode={needsTotp ? 'totp' : 'pin'}
        onConfirm={handleAuthConfirm}
      />
    </>
  );
}

// ── Add Credit Modal ────────────────────────────────────────────────────────
function AddCreditModal({ open, onClose, credit, onAdded }) {
  const [amount, setAmount] = useState('');
  const [description, setDescription] = useState('');
  const [loading, setLoading] = useState(false);

  const handleAdd = async () => {
    if (!amount || parseFloat(amount) <= 0) { toast.error('Enter a valid amount'); return; }
    setLoading(true);
    try {
      const res = await api.post(`/crop-credits/${credit.id}/add-credit`, {
        amount: parseFloat(amount),
        description: description || 'Credit added to season',
        date: new Date().toISOString().split('T')[0],
      });
      toast.success(`₱${parseFloat(amount).toLocaleString()} added to crop credit`);
      onAdded(res.data);
      onClose();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Failed to add credit');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-sm">
            <Plus size={14} /> Add Credit to Season
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="bg-slate-50 rounded-lg p-2.5 text-xs">
            <p className="text-slate-500">Customer: <span className="font-medium text-slate-800">{credit?.customer_name}</span></p>
            <p className="text-slate-500 mt-0.5">Season ends: <span className="font-medium">{credit?.season_end_date}</span></p>
          </div>
          <div>
            <Label className="text-xs">Amount (₱) *</Label>
            <Input data-testid="add-credit-amount-input" type="number" placeholder="0.00" value={amount}
              onChange={e => setAmount(e.target.value)} className="mt-1" />
          </div>
          <div>
            <Label className="text-xs">Description</Label>
            <Input placeholder="e.g. Seeds, fertilizer" value={description}
              onChange={e => setDescription(e.target.value)} className="mt-1" />
          </div>
          <Button data-testid="add-credit-submit-btn"
            className="w-full bg-slate-800 text-white" onClick={handleAdd} disabled={loading}>
            {loading ? 'Adding...' : 'Add Credit'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ── Record Payment Modal ────────────────────────────────────────────────────
function PaymentModal({ open, onClose, credit, onPaid }) {
  const [amount, setAmount] = useState('');
  const [method, setMethod] = useState('Cash');
  const [loading, setLoading] = useState(false);

  const principal = credit?.principal_balance || 0;
  const interest = credit?.accrued_interest || 0;
  const total = principal + interest;

  // Preview allocation
  const amt = parseFloat(amount) || 0;
  const toInterest = Math.min(amt, interest);
  const toPrincipal = Math.min(amt - toInterest, principal);

  const handlePay = async () => {
    if (!amount || amt <= 0) { toast.error('Enter a valid amount'); return; }
    setLoading(true);
    try {
      const res = await api.post(`/crop-credits/${credit.id}/payment`, {
        amount: amt,
        method,
        date: new Date().toISOString().split('T')[0],
      });
      toast.success(res.data.message);
      onPaid(res.data.credit);
      onClose();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Payment failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-sm">
            <Coins size={14} /> Record Payment
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="bg-slate-50 rounded-lg p-3 text-xs space-y-1">
            <div className="flex justify-between">
              <span className="text-slate-500">Customer</span>
              <span className="font-medium">{credit?.customer_name}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">Accrued Interest</span>
              <span className="text-amber-600 font-medium">{formatPHP(interest)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-slate-500">Principal Balance</span>
              <span className="text-red-600 font-medium">{formatPHP(principal)}</span>
            </div>
            <Separator className="my-1" />
            <div className="flex justify-between font-semibold">
              <span>Total Due</span>
              <span className="text-red-700">{formatPHP(total)}</span>
            </div>
          </div>

          <div>
            <Label className="text-xs">Payment Amount (₱) *</Label>
            <Input data-testid="payment-amount-input" type="number" placeholder="0.00" value={amount}
              onChange={e => setAmount(e.target.value)} className="mt-1" />
          </div>

          {/* Allocation preview */}
          {amt > 0 && (
            <div className="bg-blue-50 border border-blue-100 rounded-lg p-2.5 text-xs space-y-1">
              <p className="font-medium text-blue-700 mb-1">Payment Allocation (Interest First)</p>
              <div className="flex justify-between">
                <span className="text-slate-600">→ Applied to Interest</span>
                <span className="text-amber-600 font-medium">{formatPHP(toInterest)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-600">→ Applied to Principal</span>
                <span className="text-slate-700 font-medium">{formatPHP(toPrincipal)}</span>
              </div>
              {amt > total && (
                <p className="text-green-600 font-medium">Account will be fully settled!</p>
              )}
            </div>
          )}

          <div>
            <Label className="text-xs">Payment Method</Label>
            <select value={method} onChange={e => setMethod(e.target.value)}
              className="w-full mt-1 border border-slate-200 rounded-lg p-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-300">
              {['Cash', 'GCash', 'Bank Transfer', 'Check'].map(m => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </div>

          <Button data-testid="record-payment-btn"
            className="w-full bg-emerald-600 hover:bg-emerald-700 text-white" onClick={handlePay} disabled={loading}>
            {loading ? 'Recording...' : `Record Payment of ${formatPHP(amt)}`}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ── Credit Detail Panel ─────────────────────────────────────────────────────
function CreditDetailPanel({ credit, onUpdated }) {
  const [showPayment, setShowPayment] = useState(false);
  const [showAddCredit, setShowAddCredit] = useState(false);
  const [showExtension, setShowExtension] = useState(false);
  const [accruing, setAccruing] = useState(false);

  const today = new Date().toISOString().split('T')[0];
  const seasonEnd = credit.season_end_date || '';
  const daysLeft = seasonEnd
    ? Math.floor((new Date(seasonEnd) - new Date(today)) / (1000 * 60 * 60 * 24))
    : null;

  const totalDue = (credit.principal_balance || 0) + (credit.accrued_interest || 0);

  const handleAccrue = async () => {
    setAccruing(true);
    try {
      const res = await api.post(`/crop-credits/${credit.id}/accrue-interest`);
      toast.success(res.data.message);
      onUpdated({ ...credit, accrued_interest: credit.accrued_interest + res.data.interest_added });
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Failed to accrue interest');
    } finally {
      setAccruing(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* Status Overview */}
      <div className="grid grid-cols-2 gap-3">
        <Card className="border-slate-200">
          <CardContent className="p-3 space-y-0.5">
            <p className="text-[10px] text-slate-400 uppercase tracking-wider">Principal Balance</p>
            <p className="text-xl font-bold text-red-600">{formatPHP(credit.principal_balance)}</p>
          </CardContent>
        </Card>
        <Card className="border-slate-200">
          <CardContent className="p-3 space-y-0.5">
            <p className="text-[10px] text-slate-400 uppercase tracking-wider">Accrued Interest</p>
            <p className="text-xl font-bold text-amber-600">{formatPHP(credit.accrued_interest)}</p>
          </CardContent>
        </Card>
        <Card className="border-slate-200">
          <CardContent className="p-3 space-y-0.5">
            <p className="text-[10px] text-slate-400 uppercase tracking-wider">Total Due</p>
            <p className="text-xl font-bold text-slate-800">{formatPHP(totalDue)}</p>
          </CardContent>
        </Card>
        <Card className="border-slate-200">
          <CardContent className="p-3 space-y-0.5">
            <p className="text-[10px] text-slate-400 uppercase tracking-wider">Total Paid</p>
            <p className="text-xl font-bold text-emerald-600">{formatPHP((credit.paid_principal || 0) + (credit.paid_interest || 0))}</p>
          </CardContent>
        </Card>
      </div>

      {/* Season timeline */}
      <Card className="border-slate-200">
        <CardContent className="p-3">
          <div className="flex items-center justify-between mb-2">
            <p className="text-xs font-medium text-slate-600">Season Timeline</p>
            <StatusBadge status={credit.status} />
          </div>
          <div className="grid grid-cols-3 gap-2 text-xs mb-3">
            <div>
              <p className="text-[10px] text-slate-400">Planting</p>
              <p className="font-medium">{fmt_date(credit.planting_date)}</p>
            </div>
            <div>
              <p className="text-[10px] text-slate-400">Original Harvest</p>
              <p className="font-medium">{fmt_date(credit.expected_harvest_date)}</p>
            </div>
            <div>
              <p className="text-[10px] text-slate-400">Current Deadline</p>
              <p className={`font-medium ${daysLeft !== null && daysLeft <= 7 ? 'text-red-600' : 'text-slate-800'}`}>
                {fmt_date(credit.season_end_date)}
              </p>
            </div>
          </div>
          {daysLeft !== null && credit.status !== 'settled' && (
            <DaysBar daysLeft={daysLeft} />
          )}
          {credit.extension_count > 0 && (
            <p className="text-[10px] text-amber-600 mt-1.5">
              {credit.extension_count} extension{credit.extension_count > 1 ? 's' : ''} used
              {credit.extension_count >= 3 && ' — Owner TOTP required for further extensions'}
            </p>
          )}
        </CardContent>
      </Card>

      {/* Actions */}
      {credit.status !== 'settled' && (
        <div className="flex gap-2 flex-wrap">
          <Button size="sm" className="bg-emerald-600 hover:bg-emerald-700 text-white text-xs"
            onClick={() => setShowPayment(true)}
            data-testid="record-payment-open-btn">
            <Coins size={12} className="mr-1" /> Record Payment
          </Button>
          {['active', 'extended'].includes(credit.status) && (
            <Button size="sm" variant="outline" className="text-xs"
              onClick={() => setShowAddCredit(true)}>
              <Plus size={12} className="mr-1" /> Add Credit
            </Button>
          )}
          <Button size="sm" variant="outline" className="text-xs border-amber-300 text-amber-700 hover:bg-amber-50"
            onClick={() => setShowExtension(true)}
            data-testid="extend-season-open-btn">
            <Clock size={12} className="mr-1" /> Extend Season
          </Button>
          <Button size="sm" variant="ghost" className="text-xs text-slate-400"
            onClick={handleAccrue} disabled={accruing}>
            <TrendingUp size={12} className="mr-1" />
            {accruing ? 'Computing...' : 'Accrue Interest'}
          </Button>
        </div>
      )}

      {/* Tabs: Credits / Payments / Extensions / Interest */}
      <Tabs defaultValue="credits">
        <TabsList className="text-xs">
          <TabsTrigger value="credits">Credits ({(credit.credits || []).length})</TabsTrigger>
          <TabsTrigger value="payments">Payments ({(credit.payments || []).length})</TabsTrigger>
          <TabsTrigger value="extensions">Extensions ({(credit.extensions || []).length})</TabsTrigger>
          <TabsTrigger value="interest">Interest Log ({(credit.interest_log || []).length})</TabsTrigger>
        </TabsList>

        <TabsContent value="credits">
          <ScrollArea className="h-52">
            <div className="space-y-1.5 p-1">
              {(credit.credits || []).length === 0 && (
                <p className="text-xs text-slate-400 text-center py-4">No credits recorded</p>
              )}
              {[...( credit.credits || [])].reverse().map(c => (
                <div key={c.id} className="flex justify-between items-center p-2.5 bg-slate-50 rounded-lg text-xs">
                  <div>
                    <p className="font-medium text-slate-700">{c.description || 'Credit'}</p>
                    <p className="text-slate-400">{c.date} · by {c.recorded_by}</p>
                  </div>
                  <span className="font-bold text-slate-800">{formatPHP(c.amount)}</span>
                </div>
              ))}
            </div>
          </ScrollArea>
        </TabsContent>

        <TabsContent value="payments">
          <ScrollArea className="h-52">
            <div className="space-y-1.5 p-1">
              {(credit.payments || []).length === 0 && (
                <p className="text-xs text-slate-400 text-center py-4">No payments recorded</p>
              )}
              {[...(credit.payments || [])].reverse().map(p => (
                <div key={p.id} className="p-2.5 bg-slate-50 rounded-lg text-xs">
                  <div className="flex justify-between">
                    <span className="font-medium">{p.method}</span>
                    <span className="font-bold text-emerald-600">{formatPHP(p.amount)}</span>
                  </div>
                  <div className="flex justify-between text-slate-400 mt-0.5">
                    <span>{p.date} · {p.recorded_by}</span>
                    <span>Int: {formatPHP(p.interest_portion)} | Principal: {formatPHP(p.principal_portion)}</span>
                  </div>
                </div>
              ))}
            </div>
          </ScrollArea>
        </TabsContent>

        <TabsContent value="extensions">
          <ScrollArea className="h-52">
            <div className="space-y-1.5 p-1">
              {(credit.extensions || []).length === 0 && (
                <p className="text-xs text-slate-400 text-center py-4">No extensions</p>
              )}
              {(credit.extensions || []).map(ext => (
                <div key={ext.extension_number}
                  className={`p-2.5 rounded-lg text-xs border ${ext.flagged ? 'bg-red-50 border-red-200' : 'bg-amber-50 border-amber-200'}`}>
                  <div className="flex justify-between">
                    <span className="font-semibold">Extension #{ext.extension_number}</span>
                    {ext.flagged && <Badge className="bg-red-100 text-red-700 text-[9px]">FLAGGED</Badge>}
                  </div>
                  <p className="text-slate-600 mt-0.5">Reason: {ext.reason}</p>
                  <p className="text-slate-400">
                    {ext.previous_end_date} → {ext.new_end_date} · by {ext.approved_by_name} ({ext.approved_method})
                  </p>
                </div>
              ))}
            </div>
          </ScrollArea>
        </TabsContent>

        <TabsContent value="interest">
          <ScrollArea className="h-52">
            <div className="space-y-1.5 p-1">
              {(credit.interest_log || []).length === 0 && (
                <p className="text-xs text-slate-400 text-center py-4">No interest accruals yet</p>
              )}
              {[...(credit.interest_log || [])].reverse().map((entry, i) => (
                <div key={i} className="flex justify-between items-center p-2.5 bg-slate-50 rounded-lg text-xs">
                  <div>
                    <p className="text-slate-500">{entry.date} {entry.manual ? '· Manual' : ''}</p>
                    <p className="text-[10px] text-slate-400">Principal: {formatPHP(entry.principal_basis)} × {entry.rate}%</p>
                  </div>
                  <span className="font-bold text-amber-600">{formatPHP(entry.amount)}</span>
                </div>
              ))}
            </div>
          </ScrollArea>
        </TabsContent>
      </Tabs>

      <PaymentModal open={showPayment} onClose={() => setShowPayment(false)} credit={credit} onPaid={onUpdated} />
      <AddCreditModal open={showAddCredit} onClose={() => setShowAddCredit(false)} credit={credit} onAdded={onUpdated} />
      <ExtensionDialog open={showExtension} onClose={() => setShowExtension(false)} credit={credit} onExtended={onUpdated} />
    </div>
  );
}

// ── Main Page ────────────────────────────────────────────────────────────────
export default function CropCreditsPage() {
  const [credits, setCredits] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [selectedCredit, setSelectedCredit] = useState(null);
  const [showCreate, setShowCreate] = useState(false);
  const [page, setPage] = useState(0);
  const LIMIT = 20;

  const fetchCredits = useCallback(async () => {
    setLoading(true);
    try {
      const params = { skip: page * LIMIT, limit: LIMIT };
      if (statusFilter) params.status = statusFilter;
      const res = await api.get('/crop-credits', { params });
      setCredits(res.data.items || []);
      setTotal(res.data.total || 0);
    } catch {
      toast.error('Failed to load crop credits');
    } finally {
      setLoading(false);
    }
  }, [page, statusFilter]);

  useEffect(() => { fetchCredits(); }, [fetchCredits]);

  const filtered = search
    ? credits.filter(c => c.customer_name?.toLowerCase().includes(search.toLowerCase()))
    : credits;

  const handleCreditUpdated = (updated) => {
    setSelectedCredit(updated);
    setCredits(prev => prev.map(c => c.id === updated.id ? updated : c));
  };

  const statusCounts = credits.reduce((acc, c) => {
    acc[c.status] = (acc[c.status] || 0) + 1;
    return acc;
  }, {});

  return (
    <div className="p-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-800 flex items-center gap-2">
            <Sprout className="text-emerald-600" size={24} />
            Crop Credits
          </h1>
          <p className="text-sm text-slate-500 mt-0.5">Charged-to-Crop credit accounts — harvest-backed financing</p>
        </div>
        <Button data-testid="new-crop-credit-btn"
          className="bg-emerald-600 hover:bg-emerald-700 text-white flex items-center gap-2"
          onClick={() => setShowCreate(true)}>
          <Plus size={16} /> New Crop Credit
        </Button>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-4 gap-3 mb-6">
        {[
          { label: 'Active', key: 'active', color: 'text-emerald-600', bg: 'bg-emerald-50 border-emerald-200' },
          { label: 'Extended', key: 'extended', color: 'text-amber-600', bg: 'bg-amber-50 border-amber-200' },
          { label: 'Overdue', key: 'overdue', color: 'text-red-600', bg: 'bg-red-50 border-red-200' },
          { label: 'Settled', key: 'settled', color: 'text-slate-600', bg: 'bg-slate-50 border-slate-200' },
        ].map(s => (
          <button key={s.key} onClick={() => setStatusFilter(statusFilter === s.key ? '' : s.key)}
            className={`p-3 rounded-xl border text-left transition-all ${statusFilter === s.key ? s.bg : 'bg-white border-slate-200 hover:bg-slate-50'}`}>
            <p className="text-xs text-slate-500">{s.label}</p>
            <p className={`text-2xl font-bold ${s.color}`}>{statusCounts[s.key] || 0}</p>
          </button>
        ))}
      </div>

      <div className="flex gap-4">
        {/* Left panel — list */}
        <div className="w-80 flex-shrink-0">
          <div className="mb-3">
            <div className="relative">
              <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
              <Input data-testid="crop-credit-search"
                className="pl-8 text-sm h-9"
                placeholder="Search by customer..."
                value={search}
                onChange={e => setSearch(e.target.value)}
              />
            </div>
          </div>

          <div className="space-y-2">
            {loading && <div className="text-center py-8 text-slate-400 text-sm">Loading...</div>}
            {!loading && filtered.length === 0 && (
              <div className="text-center py-8 text-slate-400 text-sm">
                {statusFilter ? `No ${statusFilter} crop credits` : 'No crop credits yet'}
              </div>
            )}
            {filtered.map(c => {
              const today = new Date().toISOString().split('T')[0];
              const daysLeft = c.season_end_date
                ? Math.floor((new Date(c.season_end_date) - new Date(today)) / (1000 * 60 * 60 * 24))
                : null;
              const totalDue = (c.principal_balance || 0) + (c.accrued_interest || 0);

              return (
                <button key={c.id} onClick={() => setSelectedCredit(c)}
                  data-testid={`crop-credit-item-${c.id}`}
                  className={`w-full text-left p-3 rounded-xl border transition-all ${selectedCredit?.id === c.id ? 'border-emerald-300 bg-emerald-50' : 'border-slate-200 bg-white hover:bg-slate-50'}`}>
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="font-medium text-sm text-slate-800 truncate">{c.customer_name}</p>
                      <p className="text-[11px] text-slate-400 mt-0.5">{fmt_date(c.planting_date)} → {fmt_date(c.season_end_date)}</p>
                    </div>
                    <StatusBadge status={c.status} />
                  </div>
                  <div className="flex justify-between items-center mt-2">
                    <p className="text-sm font-bold text-red-600">{formatPHP(totalDue)}</p>
                    {daysLeft !== null && c.status !== 'settled' && (
                      <p className={`text-[10px] ${daysLeft <= 0 ? 'text-red-600 font-semibold' : daysLeft <= 7 ? 'text-amber-600' : 'text-slate-400'}`}>
                        {daysLeft <= 0 ? 'Overdue' : `${daysLeft}d left`}
                      </p>
                    )}
                  </div>
                  {c.extension_count > 0 && (
                    <p className="text-[10px] text-amber-600 mt-1">{c.extension_count} extension{c.extension_count !== 1 ? 's' : ''}</p>
                  )}
                </button>
              );
            })}
          </div>

          {/* Pagination */}
          {total > LIMIT && (
            <div className="flex justify-between items-center mt-3">
              <button onClick={() => setPage(p => Math.max(0, p - 1))} disabled={page === 0}
                className="text-xs text-slate-500 disabled:opacity-30 hover:text-slate-800">← Prev</button>
              <span className="text-xs text-slate-400">{page * LIMIT + 1}–{Math.min((page + 1) * LIMIT, total)} of {total}</span>
              <button onClick={() => setPage(p => p + 1)} disabled={(page + 1) * LIMIT >= total}
                className="text-xs text-slate-500 disabled:opacity-30 hover:text-slate-800">Next →</button>
            </div>
          )}
        </div>

        {/* Right panel — detail */}
        <div className="flex-1 min-w-0">
          {selectedCredit ? (
            <Card className="border-slate-200">
              <CardHeader className="pb-3 pt-4 px-4">
                <div className="flex items-start justify-between">
                  <div>
                    <CardTitle className="text-base flex items-center gap-2">
                      <Sprout size={16} className="text-emerald-600" />
                      {selectedCredit.customer_name}
                    </CardTitle>
                    <p className="text-xs text-slate-400 mt-0.5">
                      {selectedCredit.monthly_interest_rate > 0 ? `${selectedCredit.monthly_interest_rate}%/mo interest` : 'No interest'} ·
                      {' '}Season started {fmt_date(selectedCredit.planting_date)}
                    </p>
                  </div>
                  <StatusBadge status={selectedCredit.status} />
                </div>
              </CardHeader>
              <CardContent className="px-4 pb-4">
                <CreditDetailPanel
                  credit={selectedCredit}
                  onUpdated={handleCreditUpdated}
                />
              </CardContent>
            </Card>
          ) : (
            <div className="flex flex-col items-center justify-center h-64 text-slate-400 border-2 border-dashed border-slate-200 rounded-xl">
              <Sprout size={32} className="mb-3 text-slate-300" />
              <p className="text-sm">Select a crop credit to view details</p>
            </div>
          )}
        </div>
      </div>

      <CreateCropCreditModal
        open={showCreate}
        onClose={() => setShowCreate(false)}
        onCreated={(newCredit) => {
          setCredits(prev => [newCredit, ...prev]);
          setSelectedCredit(newCredit);
          setTotal(prev => prev + 1);
        }}
      />
    </div>
  );
}
