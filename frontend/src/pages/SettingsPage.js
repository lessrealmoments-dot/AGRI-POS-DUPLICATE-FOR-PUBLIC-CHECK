import { useState, useEffect, useCallback } from 'react';
import { useAuth, api } from '../contexts/AuthContext';
import { fmtDate } from '../lib/utils';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Badge } from '../components/ui/badge';
import { Switch } from '../components/ui/switch';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table';
import { ScrollArea } from '../components/ui/scroll-area';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs';
import {
  Settings, Shield, Key, Smartphone, CheckCircle2, XCircle, Lock,
  RefreshCw, AlertTriangle, ShieldCheck, Eye, EyeOff, User, Building2, Save, Monitor, Trash2, Copy, Clock
} from 'lucide-react';
import ResetCompanyModal from '../components/ResetCompanyModal';
import { QRCodeSVG } from 'qrcode.react';
import { toast } from 'sonner';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

// ── My PIN change form ───────────────────────────────────────────────────────
function MyPinForm({ hasExistingPin, pinLabel = 'PIN' }) {
  const [currentPin, setCurrentPin] = useState('');
  const [newPin, setNewPin] = useState('');
  const [confirmPin, setConfirmPin] = useState('');
  const [saving, setSaving] = useState(false);
  const [showPin, setShowPin] = useState(false);

  const handleSave = async () => {
    if (newPin !== confirmPin) { toast.error('PINs do not match'); return; }
    if (newPin.length < 4) { toast.error('PIN must be at least 4 digits'); return; }
    if (hasExistingPin && !currentPin) { toast.error('Enter your current PIN'); return; }
    setSaving(true);
    try {
      await api.put('/auth/change-my-pin', { current_pin: currentPin, new_pin: newPin });
      toast.success('Your PIN has been updated');
      setCurrentPin(''); setNewPin(''); setConfirmPin('');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to change PIN');
    }
    setSaving(false);
  };

  return (
    <div className="max-w-sm space-y-3">
      {hasExistingPin && (
        <div>
          <Label className="text-xs text-slate-500">Current {pinLabel}</Label>
          <Input data-testid="my-current-pin" type="password" autoComplete="new-password" value={currentPin}
            onChange={e => setCurrentPin(e.target.value.replace(/\D/g, '').slice(0, 8))}
            placeholder={`Enter current ${pinLabel}`} className="mt-1" />
        </div>
      )}
      <div>
        <Label className="text-xs text-slate-500">{hasExistingPin ? `New ${pinLabel}` : `Set ${pinLabel}`}</Label>
        <div className="relative mt-1">
          <Input data-testid="my-new-pin" type={showPin ? 'text' : 'password'} value={newPin}
            onChange={e => setNewPin(e.target.value.replace(/\D/g, '').slice(0, 8))}
            placeholder="Enter 4-8 digit PIN" className="pr-10" />
          <button type="button" onClick={() => setShowPin(v => !v)} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400">
            {showPin ? <EyeOff size={14} /> : <Eye size={14} />}
          </button>
        </div>
      </div>
      <div>
        <Label className="text-xs text-slate-500">Confirm {pinLabel}</Label>
        <Input data-testid="my-confirm-pin" type="password" autoComplete="new-password" value={confirmPin}
          onChange={e => setConfirmPin(e.target.value.replace(/\D/g, '').slice(0, 8))}
          placeholder={`Re-enter ${pinLabel}`} className="mt-1" />
      </div>
      {newPin && confirmPin && newPin !== confirmPin && <p className="text-xs text-red-500">PINs do not match</p>}
      <Button data-testid="save-my-pin-btn" onClick={handleSave}
        disabled={saving || !newPin || newPin !== confirmPin || (hasExistingPin && !currentPin)}
        className="bg-[#1A4D2E] hover:bg-[#14532d] text-white">
        {saving ? <RefreshCw size={13} className="animate-spin mr-1.5" /> : <Key size={13} className="mr-1.5" />}
        {hasExistingPin ? `Change My ${pinLabel}` : `Set My ${pinLabel}`}
      </Button>
    </div>
  );
}

// ── Change Password form ─────────────────────────────────────────────────────
function ChangePasswordForm() {
  const [currentPw, setCurrentPw] = useState('');
  const [newPw, setNewPw] = useState('');
  const [confirmPw, setConfirmPw] = useState('');
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    if (newPw !== confirmPw) { toast.error('Passwords do not match'); return; }
    if (newPw.length < 6) { toast.error('Password must be at least 6 characters'); return; }
    setSaving(true);
    try {
      await api.put('/auth/change-password', { current_password: currentPw, new_password: newPw });
      toast.success('Password changed');
      setCurrentPw(''); setNewPw(''); setConfirmPw('');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to change password');
    }
    setSaving(false);
  };

  return (
    <div className="max-w-sm space-y-3">
      <div>
        <Label className="text-xs text-slate-500">Current Password</Label>
        <Input data-testid="current-password" type="password" autoComplete="new-password" value={currentPw}
          onChange={e => setCurrentPw(e.target.value)} placeholder="Enter current password" className="mt-1" />
      </div>
      <div>
        <Label className="text-xs text-slate-500">New Password</Label>
        <Input data-testid="new-password" type="password" autoComplete="new-password" value={newPw}
          onChange={e => setNewPw(e.target.value)} placeholder="Min. 6 characters" className="mt-1" />
      </div>
      <div>
        <Label className="text-xs text-slate-500">Confirm Password</Label>
        <Input data-testid="confirm-password" type="password" autoComplete="new-password" value={confirmPw}
          onChange={e => setConfirmPw(e.target.value)} placeholder="Re-enter password" className="mt-1" />
      </div>
      {newPw && confirmPw && newPw !== confirmPw && <p className="text-xs text-red-500">Passwords do not match</p>}
      <Button data-testid="save-password-btn" onClick={handleSave}
        disabled={saving || !currentPw || !newPw || newPw !== confirmPw}
        className="bg-[#1A4D2E] hover:bg-[#14532d] text-white">
        {saving ? <RefreshCw size={13} className="animate-spin mr-1.5" /> : <Lock size={13} className="mr-1.5" />}
        Change Password
      </Button>
    </div>
  );
}

// ── Connect Terminal Panel ─────────────────────────────────────────────────
function ConnectTerminalPanel({ branches }) {
  const [code, setCode] = useState('');
  const [branchId, setBranchId] = useState('');
  const [pairing, setPairing] = useState(false);
  const [terminals, setTerminals] = useState([]);
  const [loading, setLoading] = useState(false);
  const [qrBranchId, setQrBranchId] = useState('');
  const [qrToken, setQrToken] = useState('');
  const [qrGenerating, setQrGenerating] = useState(false);
  const terminalUrl = `${window.location.origin}/terminal`;

  const loadTerminals = async () => {
    setLoading(true);
    try {
      const res = await api.get('/terminal/active');
      setTerminals(res.data || []);
    } catch {}
    setLoading(false);
  };

  useEffect(() => { loadTerminals(); }, []);

  const handlePair = async () => {
    if (!code.trim() || code.trim().length !== 6) { toast.error('Enter the 6-character code from the terminal'); return; }
    if (!branchId) { toast.error('Select a branch'); return; }
    setPairing(true);
    try {
      const res = await api.post('/terminal/pair', { code: code.trim(), branch_id: branchId });
      toast.success(res.data.message);
      setCode('');
      loadTerminals();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to pair terminal');
    }
    setPairing(false);
  };

  const disconnectTerminal = async (terminalId) => {
    try {
      await api.post(`/terminal/disconnect/${terminalId}`);
      toast.success('Terminal disconnected');
      loadTerminals();
    } catch {
      toast.error('Failed to disconnect');
    }
  };

  const generateQR = async () => {
    if (!qrBranchId) { toast.error('Select a branch first'); return; }
    setQrGenerating(true);
    try {
      const res = await api.post('/terminal/initiate-qr-pairing', { branch_id: qrBranchId });
      setQrToken(res.data.token);
      toast.success(`QR ready for ${res.data.branch_name}`);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to generate QR');
    }
    setQrGenerating(false);
  };

  return (
    <div className="space-y-6">
      <Card className="border-slate-200">
        <CardHeader className="pb-3">
          <CardTitle className="text-base font-semibold flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
            <Monitor size={16} /> Pair a Terminal
          </CardTitle>
          <p className="text-sm text-slate-500">
            Connect an Android device, tablet, or phone as an AgriSmart Terminal.
          </p>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Quick Pair via QR Code */}
          <div className="p-4 rounded-xl bg-gradient-to-br from-slate-50 to-emerald-50/30 border border-emerald-200/50">
            <h4 className="text-sm font-semibold text-slate-800 flex items-center gap-2 mb-3" style={{ fontFamily: 'Manrope' }}>
              <Smartphone size={15} className="text-emerald-600" /> Quick Pair — Scan QR Code
            </h4>
            <p className="text-xs text-slate-500 mb-3">Select a branch, generate a QR code, and scan it with your mobile device to instantly connect.</p>
            <div className="flex items-end gap-3">
              <div className="flex-1">
                <Label className="text-xs text-slate-500 mb-1 block">Branch</Label>
                <select
                  value={qrBranchId}
                  onChange={e => { setQrBranchId(e.target.value); setQrToken(''); }}
                  className="w-full h-9 rounded-md border border-slate-200 px-3 text-sm bg-white"
                  data-testid="qr-branch-select"
                >
                  <option value="">Select branch...</option>
                  {(branches || []).map(b => (
                    <option key={b.id} value={b.id}>{b.name}</option>
                  ))}
                </select>
              </div>
              <Button onClick={generateQR} disabled={qrGenerating || !qrBranchId}
                className="bg-[#1A4D2E] hover:bg-[#14532d] text-white h-9 text-xs"
                data-testid="generate-qr-btn">
                {qrGenerating ? <RefreshCw size={13} className="animate-spin mr-1.5" /> : <Monitor size={13} className="mr-1.5" />}
                {qrToken ? 'Regenerate' : 'Generate QR'}
              </Button>
            </div>
            {qrToken && (
              <div className="mt-4 flex flex-col items-center gap-3 p-4 bg-white rounded-xl border border-slate-200 shadow-sm">
                <QRCodeSVG
                  value={`${terminalUrl}?pair=${qrToken}`}
                  size={200}
                  level="M"
                  includeMargin={true}
                  bgColor="#ffffff"
                  fgColor="#1A4D2E"
                />
                <div className="text-center">
                  <p className="text-xs text-slate-500">Scan with your mobile camera to connect</p>
                  <p className="text-[10px] text-slate-400 mt-1 font-mono break-all max-w-[280px]">{terminalUrl}?pair={qrToken.slice(0,12)}...</p>
                  <p className="text-[10px] text-emerald-600 mt-1 font-medium">
                    {branches?.find(b => b.id === qrBranchId)?.name || ''} — Expires in 10 minutes
                  </p>
                </div>
              </div>
            )}
          </div>

          {/* Divider */}
          <div className="flex items-center gap-3">
            <div className="flex-1 h-px bg-slate-200" />
            <span className="text-[10px] text-slate-400 uppercase tracking-wider font-medium">or pair manually</span>
            <div className="flex-1 h-px bg-slate-200" />
          </div>

          {/* Terminal URL */}
          <div>
            <Label className="text-xs text-slate-500 mb-1 block">Terminal URL</Label>
            <div className="flex items-center gap-2">
              <Input value={terminalUrl} readOnly className="font-mono text-xs bg-slate-50" data-testid="terminal-url" />
              <Button variant="outline" size="sm" onClick={() => { navigator.clipboard.writeText(terminalUrl); toast.success('Copied!'); }} data-testid="copy-terminal-url">
                <Copy size={14} />
              </Button>
            </div>
            <p className="text-xs text-slate-400 mt-1">Open this URL on the mobile device, then enter the code shown below.</p>
          </div>

          {/* Pairing code input */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <Label className="text-xs text-slate-500 mb-1 block">Code from Terminal</Label>
              <Input
                value={code}
                onChange={e => setCode(e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 6))}
                placeholder="ABC123"
                maxLength={6}
                className="font-mono text-lg tracking-widest text-center uppercase"
                data-testid="terminal-code-input"
              />
            </div>
            <div>
              <Label className="text-xs text-slate-500 mb-1 block">Assign to Branch</Label>
              <select
                value={branchId}
                onChange={e => setBranchId(e.target.value)}
                className="w-full h-10 rounded-md border border-slate-200 px-3 text-sm bg-white"
                data-testid="terminal-branch-select"
              >
                <option value="">Select branch...</option>
                {(branches || []).map(b => (
                  <option key={b.id} value={b.id}>{b.name}</option>
                ))}
              </select>
            </div>
            <div className="flex items-end">
              <Button
                onClick={handlePair}
                disabled={pairing || !code || !branchId}
                className="bg-[#1A4D2E] hover:bg-[#14532d] text-white w-full"
                data-testid="pair-terminal-btn"
              >
                {pairing ? <RefreshCw size={14} className="animate-spin mr-1.5" /> : <Smartphone size={14} className="mr-1.5" />}
                {pairing ? 'Pairing...' : 'Pair Terminal'}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Active Terminals */}
      <Card className="border-slate-200">
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <CardTitle className="text-base font-semibold" style={{ fontFamily: 'Manrope' }}>
              Active Terminals
            </CardTitle>
            <Button variant="outline" size="sm" onClick={loadTerminals} disabled={loading}>
              <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {terminals.length === 0 ? (
            <p className="text-sm text-slate-400 text-center py-6">No active terminals</p>
          ) : (
            <div className="space-y-2">
              {terminals.map(t => (
                <div key={t.terminal_id} className="flex items-center justify-between p-3 bg-slate-50 rounded-lg border border-slate-200" data-testid={`terminal-${t.terminal_id}`}>
                  <div>
                    <p className="text-sm font-medium text-slate-800">{t.branch_name || 'Unknown Branch'}</p>
                    <p className="text-xs text-slate-500">Paired by {t.user_name} · {fmtDate(t.paired_at)}</p>
                  </div>
                  <Button variant="outline" size="sm" onClick={() => disconnectTerminal(t.terminal_id)} className="text-red-500 hover:text-red-700 hover:bg-red-50" data-testid={`disconnect-${t.terminal_id}`}>
                    <Trash2 size={14} className="mr-1" /> Disconnect
                  </Button>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

export default function SettingsPage() {
  const { user: currentUser, refreshUser, branches } = useAuth();
  const isAdmin = currentUser?.role === 'admin';

  // ── Reset Company Modal ────────────────────────────────────────────────────
  const [showResetModal, setShowResetModal] = useState(false);

  // ── TOTP ──────────────────────────────────────────────────────────────────
  const [totpStatus, setTotpStatus] = useState({ enabled: false, verified: false });
  const [totpSetup, setTotpSetup] = useState(null);
  const [totpConfirmCode, setTotpConfirmCode] = useState('');
  const [totpLoading, setTotpLoading] = useState(false);
  const [totpStep, setTotpStep] = useState('idle');

  const loadTotpStatus = useCallback(async () => {
    if (!isAdmin) return;
    try { const res = await api.get('/auth/totp/status'); setTotpStatus(res.data); } catch {}
  }, [isAdmin]);

  useEffect(() => { loadTotpStatus(); }, [loadTotpStatus]);

  const startTotpSetup = async () => {
    setTotpLoading(true);
    try { const res = await api.post('/auth/totp/setup'); setTotpSetup(res.data); setTotpStep('scan'); }
    catch { toast.error('Failed to generate TOTP secret'); }
    setTotpLoading(false);
  };

  const confirmTotpSetup = async () => {
    if (totpConfirmCode.length !== 6) { toast.error('Enter 6-digit code'); return; }
    setTotpLoading(true);
    try {
      const res = await api.post('/auth/totp/verify-setup', { code: totpConfirmCode });
      if (res.data.verified) {
        toast.success('Authenticator app connected!');
        setTotpStep('idle'); setTotpSetup(null); setTotpConfirmCode(''); loadTotpStatus();
      } else { toast.error(res.data.error || 'Code mismatch'); }
    } catch { toast.error('Verification failed'); }
    setTotpLoading(false);
  };

  const disableTotp = async () => {
    if (!window.confirm('Disable TOTP?')) return;
    try { await api.delete('/auth/totp/disable'); toast.success('TOTP disabled'); loadTotpStatus(); }
    catch { toast.error('Failed'); }
  };

  // ── PIN Policies ──────────────────────────────────────────────────────────
  const [pinPolicyActions, setPinPolicyActions] = useState([]);
  const [pinMethods, setPinMethods] = useState([]);
  const [pinPolicies, setPinPolicies] = useState({});
  const [savingPolicies, setSavingPolicies] = useState(false);

  // Also keep old TOTP state for backward compat
  const [totpActions, setTotpActions] = useState([]);
  const [enabledActions, setEnabledActions] = useState([]);
  const [savingControls, setSavingControls] = useState(false);

  const loadPinPolicies = useCallback(async () => {
    if (!isAdmin) return;
    try {
      const res = await api.get('/settings/pin-policies');
      setPinPolicyActions(res.data.actions || []);
      setPinMethods(res.data.methods || []);
      setPinPolicies(res.data.policies || {});
    } catch {}
  }, [isAdmin]);

  useEffect(() => { loadPinPolicies(); }, [loadPinPolicies]);

  const togglePinMethod = (actionKey, method) => {
    setPinPolicies(prev => {
      const current = prev[actionKey] || pinPolicyActions.find(a => a.key === actionKey)?.defaults || [];
      const updated = current.includes(method)
        ? current.filter(m => m !== method)
        : [...current, method];
      if (updated.length === 0) { toast.error('At least one method must be enabled'); return prev; }
      return { ...prev, [actionKey]: updated };
    });
  };

  const savePinPolicies = async () => {
    setSavingPolicies(true);
    try {
      await api.put('/settings/pin-policies', { policies: pinPolicies });
      toast.success('PIN policies saved');
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed to save'); }
    setSavingPolicies(false);
  };

  const policyModules = pinPolicyActions.reduce((acc, a) => {
    if (!acc[a.module]) acc[a.module] = [];
    acc[a.module].push(a);
    return acc;
  }, {});

  const METHOD_LABELS = {
    admin_pin:   { label: 'Owner PIN',    color: 'bg-red-100 text-red-700 border-red-200' },
    manager_pin: { label: 'Manager PIN',  color: 'bg-blue-100 text-blue-700 border-blue-200' },
    totp:        { label: 'TOTP',         color: 'bg-purple-100 text-purple-700 border-purple-200' },
    auditor_pin: { label: 'Auditor PIN',  color: 'bg-amber-100 text-amber-700 border-amber-200' },
    staff_pin:   { label: 'Staff PIN',    color: 'bg-teal-100 text-teal-700 border-teal-200' },
  };

  const loadTotpControls = useCallback(async () => {
    if (!isAdmin) return;
    try { const res = await api.get('/settings/totp-controls'); setTotpActions(res.data.actions || []); setEnabledActions(res.data.enabled_actions || []); } catch {}
  }, [isAdmin]);

  useEffect(() => { loadTotpControls(); }, [loadTotpControls]);

  const toggleAction = (key) => setEnabledActions(prev => prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key]);

  const saveControls = async () => {
    setSavingControls(true);
    try { await api.put('/settings/totp-controls', { enabled_actions: enabledActions }); toast.success('TOTP controls saved'); }
    catch { toast.error('Failed to save'); }
    setSavingControls(false);
  };

  const actionsByModule = totpActions.reduce((acc, a) => { if (!acc[a.module]) acc[a.module] = []; acc[a.module].push(a); return acc; }, {});

  // ── Admin PIN ──────────────────────────────────────────────────────────────
  const [auditPinConfigured, setAuditPinConfigured] = useState(false);
  const [newAuditPin, setNewAuditPin] = useState('');
  const [confirmAuditPin, setConfirmAuditPin] = useState('');
  const [showAuditPin, setShowAuditPin] = useState(false);
  const [savingAuditPin, setSavingAuditPin] = useState(false);

  useEffect(() => {
    if (isAdmin) {
      api.get(`${BACKEND_URL}/api/verify/admin-pin/status`)
        .then(r => setAuditPinConfigured(r.data.configured)).catch(() => {});
    }
  }, [isAdmin]); // eslint-disable-line

  const saveAuditPin = async () => {
    if (newAuditPin.length < 4) { toast.error('PIN must be at least 4 digits'); return; }
    if (newAuditPin !== confirmAuditPin) { toast.error('PINs do not match'); return; }
    setSavingAuditPin(true);
    try {
      await api.post(`${BACKEND_URL}/api/verify/admin-pin/set`, { pin: newAuditPin });
      toast.success('Admin PIN saved');
      setNewAuditPin(''); setConfirmAuditPin(''); setAuditPinConfigured(true);
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed'); }
    setSavingAuditPin(false);
  };

  // ── Auditor Access ────────────────────────────────────────────────────────
  const [users, setUsers] = useState([]);
  const [auditorEdits, setAuditorEdits] = useState({});
  const [savingAuditor, setSavingAuditor] = useState({});

  const fetchUsers = useCallback(async () => {
    if (!isAdmin) return;
    try { const res = await api.get('/users'); setUsers(res.data); } catch {}
  }, [isAdmin]);

  useEffect(() => { fetchUsers(); }, [fetchUsers]);

  const getAuditorState = (u) => auditorEdits[u.id] !== undefined ? auditorEdits[u.id] : { is_auditor: u.is_auditor || false, auditor_pin: u.auditor_pin || '' };

  const updateAuditorEdit = (userId, field, value) => {
    setAuditorEdits(prev => ({
      ...prev,
      [userId]: { ...getAuditorState(users.find(u => u.id === userId) || {}), [field]: value },
    }));
  };

  const saveAuditorAccess = async (userId) => {
    const edit = auditorEdits[userId];
    if (!edit) return;
    if (edit.is_auditor && edit.auditor_pin && edit.auditor_pin.length < 4) { toast.error('PIN must be at least 4 digits'); return; }
    setSavingAuditor(prev => ({ ...prev, [userId]: true }));
    try {
      await api.put(`/users/${userId}`, { is_auditor: edit.is_auditor, auditor_pin: edit.is_auditor ? (edit.auditor_pin || undefined) : null });
      toast.success('Auditor access updated'); fetchUsers();
      setAuditorEdits(prev => { const n = { ...prev }; delete n[userId]; return n; });
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed'); }
    setSavingAuditor(prev => ({ ...prev, [userId]: false }));
  };

  // ── Profile Edit ───────────────────────────────────────────────────────────
  const [profileForm, setProfileForm] = useState({ full_name: '', email: '' });
  const [savingProfile, setSavingProfile] = useState(false);

  useEffect(() => {
    if (currentUser) {
      setProfileForm({ full_name: currentUser.full_name || '', email: currentUser.email || '' });
    }
  }, [currentUser]);

  const saveProfile = async () => {
    setSavingProfile(true);
    try {
      await api.put('/auth/update-profile', profileForm);
      toast.success('Profile updated');
      refreshUser?.();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed'); }
    setSavingProfile(false);
  };

  // ── Business Info ──────────────────────────────────────────────────────────
  const [bizInfo, setBizInfo] = useState({
    business_name: '', address: '', phone: '', tin: '', email: '',
    trust_receipt_terms: '', receipt_footer: 'This is not an official receipt.', thermal_width: '58mm',
  });
  const [savingBiz, setSavingBiz] = useState(false);
  const [bizLoaded, setBizLoaded] = useState(false);

  const loadBizInfo = useCallback(async () => {
    try {
      const res = await api.get('/settings/business-info');
      setBizInfo(res.data);
      setBizLoaded(true);
    } catch {}
  }, []);

  useEffect(() => { if (isAdmin) loadBizInfo(); }, [isAdmin, loadBizInfo]);

  const saveBizInfo = async () => {
    if (!bizInfo.business_name?.trim()) { toast.error('Business name is required'); return; }
    setSavingBiz(true);
    try {
      await api.put('/settings/business-info', bizInfo);
      toast.success('Business info saved');
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed'); }
    setSavingBiz(false);
  };

  // ── Organization Timezone ──────────────────────────────────────────────────
  // Drives the close-reminder SMS scheduler + the default sale date picker.
  // Changing the TZ takes effect on the next scheduler tick (≤1 minute) and
  // immediately for the Sales date, no restart required.
  const [tzValue, setTzValue] = useState('Asia/Manila');
  const [tzChoices, setTzChoices] = useState([]);
  const [tzDetected, setTzDetected] = useState('');
  const [savingTz, setSavingTz] = useState(false);

  const loadTimezone = useCallback(async () => {
    try {
      const r = await api.get('/settings/timezone');
      setTzValue(r.data.timezone || 'Asia/Manila');
      setTzChoices(r.data.choices || []);
    } catch {}
    try {
      // Browser's resolved IANA zone — used for the "Your device is in X"
      // hint so the admin can match their organization TZ at a glance.
      const dev = Intl.DateTimeFormat().resolvedOptions().timeZone;
      if (dev) setTzDetected(dev);
    } catch {}
  }, []);
  useEffect(() => { if (isAdmin) loadTimezone(); }, [isAdmin, loadTimezone]);

  const saveTimezone = async () => {
    setSavingTz(true);
    try {
      await api.put('/settings/timezone', { timezone: tzValue });
      toast.success(`Timezone saved: ${tzValue}`);
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed to save timezone'); }
    setSavingTz(false);
  };

  // Preview the wall-clock time in the selected TZ so the admin can sanity-
  // check their pick right next to the dropdown. Re-renders each minute.
  const [clockTick, setClockTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setClockTick(x => x + 1), 30000);
    return () => clearInterval(t);
  }, []);
  const tzPreview = (() => {
    try {
      // `clockTick` referenced so this re-runs when the interval fires
      void clockTick;
      return new Intl.DateTimeFormat('en-US', {
        timeZone: tzValue,
        hour: 'numeric', minute: '2-digit', hour12: true,
        weekday: 'short', year: 'numeric', month: 'short', day: 'numeric',
      }).format(new Date());
    } catch { return '—'; }
  })();

  return (
    <div className="space-y-6 animate-fadeIn" data-testid="settings-page">
      <div>
        <h1 className="text-2xl font-bold tracking-tight" style={{ fontFamily: 'Manrope' }}>Settings</h1>
        <p className="text-sm text-slate-500 mt-1">
          {isAdmin ? 'Your account, security & system settings' : 'Your account settings'}
        </p>
      </div>

      <Tabs defaultValue="account">
        <TabsList className="mb-4">
          <TabsTrigger value="account" data-testid="account-tab" className="flex items-center gap-1.5">
            <User size={14} /> My Account
          </TabsTrigger>
          {isAdmin && (
            <TabsTrigger value="security" data-testid="security-tab" className="flex items-center gap-1.5">
              <Shield size={14} /> Security
            </TabsTrigger>
          )}
          {isAdmin && (
            <TabsTrigger value="business" data-testid="business-tab" className="flex items-center gap-1.5">
              <Building2 size={14} /> Business Info
            </TabsTrigger>
          )}
          {isAdmin && (
            <TabsTrigger value="terminal" data-testid="terminal-tab" className="flex items-center gap-1.5">
              <Monitor size={14} /> Connect Terminal
            </TabsTrigger>
          )}
        </TabsList>

        {/* ── My Account Tab ─────────────────────────────────────────── */}
        <TabsContent value="account" className="space-y-6">
          {/* Profile Info */}
          <Card className="border-slate-200">
            <CardHeader className="pb-3">
              <CardTitle className="text-base font-semibold flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
                <User size={18} className="text-[#1A4D2E]" /> Profile
              </CardTitle>
              <p className="text-sm text-slate-500">Update your display name and email</p>
            </CardHeader>
            <CardContent>
              <div className="max-w-sm space-y-3">
                <div>
                  <Label className="text-xs text-slate-500">Full Name</Label>
                  <Input data-testid="profile-fullname" value={profileForm.full_name}
                    onChange={e => setProfileForm(p => ({ ...p, full_name: e.target.value }))}
                    placeholder="Your name" className="mt-1" />
                </div>
                <div>
                  <Label className="text-xs text-slate-500">Email</Label>
                  <Input data-testid="profile-email" type="email" value={profileForm.email}
                    onChange={e => setProfileForm(p => ({ ...p, email: e.target.value }))}
                    placeholder="you@example.com" className="mt-1" />
                </div>
                <Button data-testid="save-profile-btn" onClick={saveProfile} disabled={savingProfile}
                  className="bg-[#1A4D2E] hover:bg-[#14532d] text-white">
                  {savingProfile ? <RefreshCw size={13} className="animate-spin mr-1.5" /> : <CheckCircle2 size={13} className="mr-1.5" />}
                  Save Profile
                </Button>
              </div>
            </CardContent>
          </Card>

          {/* Change Password */}
          <Card className="border-slate-200">
            <CardHeader className="pb-3">
              <CardTitle className="text-base font-semibold flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
                <Lock size={18} className="text-[#1A4D2E]" /> Change Password
              </CardTitle>
            </CardHeader>
            <CardContent><ChangePasswordForm /></CardContent>
          </Card>

          {/* My PIN — all roles that support PIN auth */}
          {(() => {
            const staffRoles = ['cashier', 'inventory', 'inventory_clerk', 'staff'];
            const managerRoles = ['admin', 'manager'];
            const isStaff = staffRoles.includes(currentUser?.role);
            const isManager = managerRoles.includes(currentUser?.role);
            if (!isStaff && !isManager) return null;
            const pinLabel = isStaff ? 'Staff PIN' : 'Manager PIN';
            const hasPin = isStaff ? currentUser?.has_staff_pin : currentUser?.has_manager_pin;
            const pinDesc = isStaff
              ? 'Your personal PIN used by your manager to authorize stock releases on your behalf.'
              : 'Your personal PIN for verifying transactions and approving actions.';
            return (
              <Card className="border-slate-200">
                <CardHeader className="pb-3">
                  <CardTitle className="text-base font-semibold flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
                    <Key size={18} className={isStaff ? 'text-teal-600' : 'text-blue-600'} /> My {pinLabel}
                    {hasPin
                      ? <Badge className="text-[10px] bg-emerald-100 text-emerald-700 ml-2">Set</Badge>
                      : <Badge className="text-[10px] bg-amber-100 text-amber-700 ml-2">Not Set</Badge>
                    }
                  </CardTitle>
                  <p className="text-sm text-slate-500">{pinDesc}</p>
                </CardHeader>
                <CardContent><MyPinForm hasExistingPin={!!hasPin} pinLabel={pinLabel} /></CardContent>
              </Card>
            );
          })()}
        </TabsContent>

        {/* ── Security Tab (Admin Only) ──────────────────────────────── */}
        {isAdmin && (
          <TabsContent value="security" className="space-y-6">

            {/* Admin PIN */}
            <Card className="border-slate-200">
              <CardHeader className="pb-3">
                <CardTitle className="text-base font-semibold flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
                  <ShieldCheck size={18} className="text-[#1A4D2E]" /> Admin PIN
                  {auditPinConfigured
                    ? <Badge className="text-[10px] bg-emerald-100 text-emerald-700 ml-2">Configured</Badge>
                    : <Badge className="text-[10px] bg-amber-100 text-amber-700 ml-2">Not Set</Badge>}
                </CardTitle>
                <p className="text-sm text-slate-500">
                  A private PIN only you know. Used for sensitive actions like inventory corrections and price edits.
                </p>
              </CardHeader>
              <CardContent>
                <div className="max-w-sm space-y-3">
                  <div>
                    <Label className="text-xs text-slate-500">{auditPinConfigured ? 'New PIN' : 'Set PIN'}</Label>
                    <div className="relative mt-1">
                      <Input data-testid="audit-pin-input" type={showAuditPin ? 'text' : 'password'} value={newAuditPin}
                        onChange={e => setNewAuditPin(e.target.value.replace(/\D/g, '').slice(0, 8))} placeholder="Enter 4-8 digit PIN" className="pr-10" />
                      <button type="button" onClick={() => setShowAuditPin(v => !v)} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400">
                        {showAuditPin ? <EyeOff size={14} /> : <Eye size={14} />}
                      </button>
                    </div>
                  </div>
                  <div>
                    <Label className="text-xs text-slate-500">Confirm PIN</Label>
                    <Input data-testid="audit-pin-confirm" type="password" autoComplete="new-password" value={confirmAuditPin}
                      onChange={e => setConfirmAuditPin(e.target.value.replace(/\D/g, '').slice(0, 8))} placeholder="Re-enter PIN" className="mt-1" />
                  </div>
                  {newAuditPin && confirmAuditPin && newAuditPin !== confirmAuditPin && <p className="text-xs text-red-500">PINs do not match</p>}
                  <Button data-testid="save-audit-pin-btn" onClick={saveAuditPin}
                    disabled={savingAuditPin || !newAuditPin || newAuditPin !== confirmAuditPin}
                    className="bg-[#1A4D2E] hover:bg-[#14532d] text-white">
                    {savingAuditPin ? <RefreshCw size={13} className="animate-spin mr-1.5" /> : <ShieldCheck size={13} className="mr-1.5" />}
                    {auditPinConfigured ? 'Update Admin PIN' : 'Set Admin PIN'}
                  </Button>
                </div>
              </CardContent>
            </Card>

            {/* TOTP Setup */}
            <Card className="border-slate-200">
              <CardHeader className="pb-3">
                <CardTitle className="text-base font-semibold flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
                  <Smartphone size={18} className="text-[#1A4D2E]" /> Authenticator App (TOTP)
                  {totpStatus.enabled && totpStatus.verified
                    ? <Badge className="text-[10px] bg-emerald-100 text-emerald-700 ml-2">Active</Badge>
                    : <Badge className="text-[10px] bg-slate-100 text-slate-500 ml-2">Not Set Up</Badge>}
                </CardTitle>
                <p className="text-sm text-slate-500">
                  For remote approvals. Workers call you and you read the 6-digit code from your phone.
                </p>
              </CardHeader>
              <CardContent className="space-y-4">
                {totpStep === 'idle' && (
                  <>
                    {totpStatus.enabled && totpStatus.verified ? (
                      <div className="flex items-center justify-between p-4 bg-emerald-50 border border-emerald-200 rounded-lg">
                        <div className="flex items-center gap-3">
                          <CheckCircle2 size={20} className="text-emerald-600" />
                          <div>
                            <p className="font-semibold text-emerald-800 text-sm">TOTP is active</p>
                            <p className="text-xs text-emerald-600">Uses your authenticator app codes</p>
                          </div>
                        </div>
                        <div className="flex gap-2">
                          <Button size="sm" variant="outline" onClick={startTotpSetup} disabled={totpLoading}>
                            <RefreshCw size={13} className="mr-1" /> Regenerate
                          </Button>
                          <Button size="sm" variant="outline" onClick={disableTotp} className="text-red-500 border-red-200 hover:bg-red-50">
                            <XCircle size={13} className="mr-1" /> Disable
                          </Button>
                        </div>
                      </div>
                    ) : (
                      <div className="flex items-center justify-between p-4 bg-amber-50 border border-amber-200 rounded-lg">
                        <div className="flex items-center gap-3">
                          <AlertTriangle size={20} className="text-amber-600" />
                          <div>
                            <p className="font-semibold text-amber-800 text-sm">TOTP not configured</p>
                            <p className="text-xs text-amber-600">Falls back to login password</p>
                          </div>
                        </div>
                        <Button size="sm" onClick={startTotpSetup} disabled={totpLoading} className="bg-[#1A4D2E] hover:bg-[#14532d] text-white">
                          {totpLoading ? <RefreshCw size={13} className="animate-spin mr-1" /> : <Smartphone size={13} className="mr-1" />}
                          Set Up Now
                        </Button>
                      </div>
                    )}
                  </>
                )}
                {totpStep === 'scan' && totpSetup && (
                  <div className="space-y-4">
                    <div className="p-3 bg-blue-50 border border-blue-200 rounded-lg text-sm text-blue-800">
                      <strong>Step 1:</strong> Scan the QR code with your authenticator app.
                    </div>
                    <div className="flex flex-col items-center gap-4 py-4">
                      <div className="p-4 bg-white border-2 border-slate-200 rounded-xl shadow-sm">
                        <QRCodeSVG value={totpSetup.qr_uri} size={180} level="M" />
                      </div>
                      <div className="text-center">
                        <p className="text-xs text-slate-500 mb-1">Can&apos;t scan? Enter manually:</p>
                        <code className="text-sm bg-slate-100 px-3 py-1.5 rounded font-mono tracking-wider select-all">{totpSetup.secret}</code>
                      </div>
                    </div>
                    <div className="flex gap-2 justify-end">
                      <Button variant="outline" onClick={() => { setTotpStep('idle'); setTotpSetup(null); }}>Cancel</Button>
                      <Button onClick={() => setTotpStep('verify')} className="bg-[#1A4D2E] hover:bg-[#14532d] text-white">Next</Button>
                    </div>
                  </div>
                )}
                {totpStep === 'verify' && (
                  <div className="space-y-4">
                    <div className="p-3 bg-blue-50 border border-blue-200 rounded-lg text-sm text-blue-800">
                      <strong>Step 2:</strong> Enter the 6-digit code from your app.
                    </div>
                    <div className="max-w-xs mx-auto space-y-2">
                      <Label>Code</Label>
                      <Input data-testid="totp-confirm-input" value={totpConfirmCode}
                        onChange={e => setTotpConfirmCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                        placeholder="000000" className="text-center text-2xl tracking-[0.4em] font-mono h-12"
                        maxLength={6} autoFocus onKeyDown={e => e.key === 'Enter' && confirmTotpSetup()} />
                    </div>
                    <div className="flex gap-2 justify-end">
                      <Button variant="outline" onClick={() => setTotpStep('scan')}>Back</Button>
                      <Button data-testid="totp-confirm-btn" onClick={confirmTotpSetup}
                        disabled={totpLoading || totpConfirmCode.length !== 6}
                        className="bg-emerald-600 hover:bg-emerald-700 text-white">
                        {totpLoading ? <RefreshCw size={13} className="animate-spin mr-1" /> : <CheckCircle2 size={13} className="mr-1" />}
                        Activate
                      </Button>
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* PIN Policies */}
            <Card className="border-slate-200" data-testid="pin-policies-card">
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between">
                  <div>
                    <CardTitle className="text-base font-semibold flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
                      <Shield size={18} className="text-[#1A4D2E]" /> PIN Policies
                    </CardTitle>
                    <p className="text-sm text-slate-500 mt-0.5">Configure which PIN types are accepted for each action.</p>
                  </div>
                  <Button data-testid="save-pin-policies-btn" size="sm" onClick={savePinPolicies} disabled={savingPolicies}
                    className="bg-[#1A4D2E] hover:bg-[#14532d] text-white shrink-0">
                    {savingPolicies ? <RefreshCw size={13} className="animate-spin mr-1" /> : null} Save Policies
                  </Button>
                </div>
              </CardHeader>
              <CardContent>
                {/* Method Legend */}
                <div className="flex flex-wrap gap-2 mb-4 pb-3 border-b border-slate-100">
                  {pinMethods.map(m => (
                    <span key={m} className={`inline-flex items-center gap-1 text-xs font-medium px-2 py-1 rounded-md border ${METHOD_LABELS[m]?.color || 'bg-slate-100 text-slate-600'}`}>
                      {METHOD_LABELS[m]?.label || m}
                    </span>
                  ))}
                </div>

                <div className="space-y-5">
                  {Object.entries(policyModules).map(([module, actions]) => (
                    <div key={module}>
                      <p className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-2">{module}</p>
                      <div className="space-y-2">
                        {actions.map(action => {
                          const activeMethods = pinPolicies[action.key] || action.defaults;
                          return (
                            <div key={action.key} data-testid={`pin-policy-${action.key}`}
                              className="flex items-center justify-between p-3 rounded-lg border border-slate-200 bg-slate-50/50 hover:bg-slate-50 transition-colors">
                              <div className="min-w-0 mr-3">
                                <p className="text-sm font-medium text-slate-800 truncate">{action.label}</p>
                              </div>
                              <div className="flex gap-1.5 shrink-0">
                                {pinMethods.map(method => {
                                  const isActive = activeMethods.includes(method);
                                  const meta = METHOD_LABELS[method] || {};
                                  return (
                                    <button
                                      key={method}
                                      data-testid={`pin-policy-${action.key}-${method}`}
                                      onClick={() => togglePinMethod(action.key, method)}
                                      className={`text-[10px] font-medium px-2 py-1 rounded-md border transition-all ${
                                        isActive
                                          ? meta.color
                                          : 'bg-white text-slate-300 border-slate-200 hover:border-slate-300'
                                      }`}
                                    >
                                      {meta.label || method}
                                    </button>
                                  );
                                })}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                  {pinPolicyActions.length === 0 && <p className="text-sm text-slate-400 text-center py-4">Loading...</p>}
                </div>
              </CardContent>
            </Card>

            {/* Auditor Access */}
            <Card className="border-slate-200">
              <CardHeader className="pb-3">
                <CardTitle className="text-base font-semibold flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
                  <Shield size={18} className="text-amber-600" /> Auditor Access
                </CardTitle>
                <p className="text-sm text-slate-500">Grant auditor access so users can verify with their own PIN.</p>
              </CardHeader>
              <CardContent className="p-0">
                <Table>
                  <TableHeader>
                    <TableRow className="bg-slate-50">
                      <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">User</TableHead>
                      <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">Auditor</TableHead>
                      <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">Auditor PIN</TableHead>
                      <TableHead className="w-20" />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {users.map(u => {
                      const state = getAuditorState(u);
                      const isDirty = auditorEdits[u.id] !== undefined;
                      return (
                        <TableRow key={u.id}>
                          <TableCell>
                            <p className="font-medium text-sm">{u.full_name || u.email}</p>
                            <p className="text-xs text-slate-400">{u.email || u.username} &middot; {u.role}</p>
                          </TableCell>
                          <TableCell>
                            <button onClick={() => updateAuditorEdit(u.id, 'is_auditor', !state.is_auditor)}
                              className={`w-10 h-5 rounded-full relative transition-colors ${state.is_auditor ? 'bg-[#1A4D2E]' : 'bg-slate-200'}`}>
                              <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${state.is_auditor ? 'translate-x-5' : 'translate-x-0.5'}`} />
                            </button>
                          </TableCell>
                          <TableCell>
                            {state.is_auditor && (
                              <Input type="password" autoComplete="new-password" value={state.auditor_pin}
                                onChange={e => updateAuditorEdit(u.id, 'auditor_pin', e.target.value.replace(/\D/g, '').slice(0, 8))}
                                placeholder="4-8 digits" className="h-8 w-28 text-sm" />
                            )}
                          </TableCell>
                          <TableCell>
                            {isDirty && (
                              <Button size="sm" onClick={() => saveAuditorAccess(u.id)} disabled={savingAuditor[u.id]}
                                className="h-7 text-xs bg-[#1A4D2E] hover:bg-[#14532d] text-white">
                                {savingAuditor[u.id] ? <RefreshCw size={11} className="animate-spin" /> : 'Save'}
                              </Button>
                            )}
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          </TabsContent>
        )}

        {/* ── Business Info Tab ─────────────────────────────────────── */}
        {isAdmin && (
          <TabsContent value="business" className="space-y-6">
            <Card className="border-slate-200">
              <CardHeader className="pb-3">
                <CardTitle className="text-base font-semibold flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
                  <Building2 size={18} className="text-[#1A4D2E]" /> Business Information
                </CardTitle>
                <p className="text-sm text-slate-500">Used on printed receipts, order slips, and trust receipts</p>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 max-w-2xl">
                  <div>
                    <Label className="text-xs text-slate-500">Business Name <span className="text-red-500">*</span></Label>
                    <Input data-testid="biz-name" value={bizInfo.business_name}
                      onChange={e => setBizInfo(b => ({ ...b, business_name: e.target.value }))}
                      placeholder="Your business name" className="mt-1" />
                  </div>
                  <div>
                    <Label className="text-xs text-slate-500">Phone</Label>
                    <Input data-testid="biz-phone" value={bizInfo.phone}
                      onChange={e => setBizInfo(b => ({ ...b, phone: e.target.value }))}
                      placeholder="(optional)" className="mt-1" />
                  </div>
                  <div className="md:col-span-2">
                    <Label className="text-xs text-slate-500">Address</Label>
                    <Input data-testid="biz-address" value={bizInfo.address}
                      onChange={e => setBizInfo(b => ({ ...b, address: e.target.value }))}
                      placeholder="Street, City, Province (optional)" className="mt-1" />
                  </div>
                  <div>
                    <Label className="text-xs text-slate-500">TIN (Tax ID)</Label>
                    <Input data-testid="biz-tin" value={bizInfo.tin}
                      onChange={e => setBizInfo(b => ({ ...b, tin: e.target.value }))}
                      placeholder="(optional)" className="mt-1" />
                  </div>
                  <div>
                    <Label className="text-xs text-slate-500">Receipt Footer Text</Label>
                    <Input data-testid="biz-footer" value={bizInfo.receipt_footer}
                      onChange={e => setBizInfo(b => ({ ...b, receipt_footer: e.target.value }))}
                      placeholder="This is not an official receipt." className="mt-1" />
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card className="border-slate-200">
              <CardHeader className="pb-3">
                <CardTitle className="text-base font-semibold flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
                  Charge Agreement Terms
                </CardTitle>
                <p className="text-sm text-slate-500">Legal clause printed on Charge Agreements for credit sales. Use {'{business_name}'} as placeholder.</p>
              </CardHeader>
              <CardContent>
                <textarea
                  data-testid="biz-trust-terms"
                  value={bizInfo.trust_receipt_terms}
                  onChange={e => setBizInfo(b => ({ ...b, trust_receipt_terms: e.target.value }))}
                  rows={4}
                  className="w-full max-w-2xl border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[#1A4D2E]/30 resize-none"
                  placeholder="Received the above item in good condition and in trust from {business_name}..."
                />
                <div className="mt-4">
                  <Button onClick={saveBizInfo} disabled={savingBiz}
                    className="bg-[#1A4D2E] hover:bg-[#14532d] text-white" data-testid="save-biz-info">
                    <Save size={14} className="mr-1.5" /> {savingBiz ? 'Saving...' : 'Save Business Info'}
                  </Button>
                </div>
              </CardContent>
            </Card>

            {/* ── Timezone ────────────────────────────────────────────── */}
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base font-semibold flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
                  <Clock size={18} className="text-[#1A4D2E]" /> Organization Timezone
                </CardTitle>
                <p className="text-sm text-slate-500">
                  Drives the SMS close-reminder schedule, the default sale date,
                  and any wall-clock formatting. Each branch under this
                  organization uses this timezone.
                </p>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 max-w-3xl">
                  <div>
                    <label className="text-xs font-medium text-slate-600">Timezone</label>
                    <select
                      data-testid="org-timezone-select"
                      value={tzValue}
                      onChange={e => setTzValue(e.target.value)}
                      className="mt-1 w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[#1A4D2E]/30 bg-white">
                      {tzChoices.length === 0 && <option value={tzValue}>{tzValue}</option>}
                      {tzChoices.map(tz => (
                        <option key={tz} value={tz}>{tz}</option>
                      ))}
                    </select>
                    {tzDetected && tzDetected !== tzValue && (
                      <p className="mt-1 text-[11px] text-amber-700">
                        <AlertTriangle size={11} className="inline mr-0.5" />
                        Your device is in <strong>{tzDetected}</strong> — pick
                        it from the list if your organization operates there.
                      </p>
                    )}
                  </div>
                  <div>
                    <label className="text-xs font-medium text-slate-600">Current time in selected zone</label>
                    <div data-testid="org-timezone-preview"
                      className="mt-1 px-3 py-2 bg-emerald-50 border border-emerald-200 rounded-lg text-sm font-mono text-emerald-900">
                      {tzPreview}
                    </div>
                    <p className="mt-1 text-[11px] text-slate-400">
                      Updates every 30s. Scheduler picks up changes within 1 minute.
                    </p>
                  </div>
                </div>
                <div>
                  <Button onClick={saveTimezone} disabled={savingTz}
                    className="bg-[#1A4D2E] hover:bg-[#14532d] text-white"
                    data-testid="save-org-timezone">
                    <Save size={14} className="mr-1.5" /> {savingTz ? 'Saving…' : 'Save Timezone'}
                  </Button>
                </div>
              </CardContent>
            </Card>

            {/* ── Danger Zone ─────────────────────────────────────────── */}
            {currentUser?.organization_id && (
            <Card className="border-red-200 bg-red-50/30">
              <CardHeader className="pb-3">
                <CardTitle className="text-base font-semibold flex items-center gap-2 text-red-700" style={{ fontFamily: 'Manrope' }}>
                  <AlertTriangle size={18} className="text-red-600" /> Danger Zone
                </CardTitle>
                <p className="text-sm text-red-600/80">
                  Irreversible actions. Proceed with caution.
                </p>
              </CardHeader>
              <CardContent>
                <div className="flex items-start justify-between gap-4 p-4 border border-red-200 rounded-lg bg-white">
                  <div>
                    <p className="font-semibold text-slate-800 text-sm">Reset Company Data</p>
                    <p className="text-xs text-slate-500 mt-0.5 max-w-sm">
                      Wipe all customers, invoices, sales, payments, employees, and inventory back to zero.
                      A backup is automatically created before reset. Your owner account is preserved.
                    </p>
                  </div>
                  <Button
                    data-testid="open-reset-company-btn"
                    onClick={() => setShowResetModal(true)}
                    variant="outline"
                    size="sm"
                    className="shrink-0 border-red-300 text-red-700 hover:bg-red-50 hover:border-red-400"
                  >
                    <Trash2 size={13} className="mr-1.5" /> Reset Company
                  </Button>
                </div>
              </CardContent>
            </Card>
            )}
          </TabsContent>
        )}

        {/* ── Connect Terminal Tab ───────────────────────────────────── */}
        {isAdmin && (
          <TabsContent value="terminal" className="space-y-6">
            <ConnectTerminalPanel branches={branches} />
          </TabsContent>
        )}
      </Tabs>

      {/* ── Reset Company Modal ──────────────────────────────────────── */}
      <ResetCompanyModal
        open={showResetModal}
        onClose={() => setShowResetModal(false)}
        orgId={currentUser?.organization_id}
        companyName={bizInfo.business_name || 'Company'}
      />
    </div>
  );
}
