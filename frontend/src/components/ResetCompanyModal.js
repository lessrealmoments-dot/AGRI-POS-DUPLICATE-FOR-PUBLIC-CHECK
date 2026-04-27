import { useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from './ui/dialog';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { AlertTriangle, Shield, KeyRound, Download, CheckCircle2, RotateCcw, Eye, EyeOff } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '../contexts/AuthContext';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

const STEPS = [
  { id: 1, label: 'Confirm', icon: AlertTriangle },
  { id: 2, label: 'Password', icon: KeyRound },
  { id: 3, label: 'TOTP', icon: Shield },
  { id: 4, label: 'Reset', icon: AlertTriangle },
];

export default function ResetCompanyModal({ open, onClose, orgId, companyName }) {
  const [step, setStep] = useState(1);
  const [confirmation, setConfirmation] = useState('');
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [totpCode, setTotpCode] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [downloadLoading, setDownloadLoading] = useState(false);
  const [restoreLoading, setRestoreLoading] = useState(false);

  const expectedConfirmation = `${companyName} Reset`;

  const reset = () => {
    setStep(1);
    setConfirmation('');
    setPassword('');
    setTotpCode('');
    setLoading(false);
    setResult(null);
    setShowPassword(false);
  };

  const handleClose = () => {
    reset();
    onClose();
  };

  const handleStep1 = () => {
    if (confirmation !== expectedConfirmation) {
      toast.error(`Type exactly: ${expectedConfirmation}`);
      return;
    }
    setStep(2);
  };

  const handleStep2 = () => {
    if (!password.trim()) { toast.error('Password is required'); return; }
    setStep(3);
  };

  const handleStep3 = () => {
    if (totpCode.length !== 6) { toast.error('Enter your 6-digit TOTP code'); return; }
    setStep(4);
  };

  const handleReset = async () => {
    setLoading(true);
    try {
      const res = await api.post(`/backups/org/${orgId}/reset`, {
        confirmation,
        password,
        totp_code: totpCode,
      });
      setResult(res.data);
      setStep(5);
      toast.success('Company data has been reset successfully');
    } catch (e) {
      const msg = e.response?.data?.detail || 'Reset failed';
      toast.error(msg);
      // If wrong password/TOTP, go back to that step
      if (msg.toLowerCase().includes('password')) setStep(2);
      else if (msg.toLowerCase().includes('totp')) setStep(3);
    }
    setLoading(false);
  };

  const handleDownload = async () => {
    if (!result?.backup?.filename) return;
    setDownloadLoading(true);
    try {
      const res = await api.get(`/backups/org/${orgId}/download/${result.backup.filename}`);
      window.open(res.data.download_url, '_blank');
      toast.success('Download started — link valid for 1 hour');
    } catch {
      toast.error('Failed to generate download link');
    }
    setDownloadLoading(false);
  };

  const handleRestore = async () => {
    if (!result?.backup?.filename) return;
    if (!window.confirm('Are you sure you want to restore from this backup? This will overwrite current data.')) return;
    setRestoreLoading(true);
    try {
      await api.post(`/backups/org/${orgId}/restore/${result.backup.filename}`);
      toast.success('Restore complete! Refreshing...');
      setTimeout(() => window.location.reload(), 1500);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Restore failed');
    }
    setRestoreLoading(false);
  };

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="max-w-md" data-testid="reset-company-modal">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-red-700">
            <AlertTriangle size={18} className="text-red-600" />
            Reset Company Data
          </DialogTitle>
        </DialogHeader>

        {/* Step indicator */}
        {step < 5 && (
          <div className="flex items-center gap-1 mb-2">
            {STEPS.map((s, i) => (
              <div key={s.id} className="flex items-center">
                <div className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold transition-colors
                  ${step > s.id ? 'bg-green-600 text-white' : step === s.id ? 'bg-red-600 text-white' : 'bg-slate-200 text-slate-400'}`}>
                  {step > s.id ? '✓' : s.id}
                </div>
                {i < STEPS.length - 1 && (
                  <div className={`h-0.5 w-8 mx-1 ${step > s.id ? 'bg-green-400' : 'bg-slate-200'}`} />
                )}
              </div>
            ))}
          </div>
        )}

        {/* ── Step 1: Type confirmation ─────────────────────────────── */}
        {step === 1 && (
          <div className="space-y-4">
            <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-800">
              <p className="font-semibold mb-1">This action cannot be undone.</p>
              <p>All customers, invoices, sales, payments, employees, inventory, and transactional data will be permanently deleted. Your owner account will be preserved.</p>
            </div>
            <div>
              <Label className="text-xs text-slate-500 mb-1 block">
                Type <span className="font-mono font-bold text-red-700 bg-red-50 px-1 rounded">{expectedConfirmation}</span> to continue
              </Label>
              <Input
                data-testid="reset-confirmation-input"
                value={confirmation}
                onChange={e => setConfirmation(e.target.value)}
                placeholder={expectedConfirmation}
                className="font-mono"
                onKeyDown={e => e.key === 'Enter' && handleStep1()}
              />
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={handleClose}>Cancel</Button>
              <Button
                data-testid="reset-step1-next"
                onClick={handleStep1}
                disabled={confirmation !== expectedConfirmation}
                className="bg-red-600 hover:bg-red-700 text-white"
              >
                Continue
              </Button>
            </div>
          </div>
        )}

        {/* ── Step 2: Admin Password ────────────────────────────────── */}
        {step === 2 && (
          <div className="space-y-4">
            <p className="text-sm text-slate-600">Enter your admin login password to verify your identity.</p>
            <div>
              <Label className="text-xs text-slate-500">Admin Password</Label>
              <div className="relative mt-1">
                <Input
                  data-testid="reset-password-input"
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  placeholder="Your login password"
                  className="pr-10"
                  autoFocus
                  onKeyDown={e => e.key === 'Enter' && handleStep2()}
                />
                <button type="button" onClick={() => setShowPassword(v => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400">
                  {showPassword ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </div>
            </div>
            <div className="flex justify-between">
              <Button variant="outline" onClick={() => setStep(1)}>Back</Button>
              <Button
                data-testid="reset-step2-next"
                onClick={handleStep2}
                disabled={!password.trim()}
                className="bg-red-600 hover:bg-red-700 text-white"
              >
                Continue
              </Button>
            </div>
          </div>
        )}

        {/* ── Step 3: TOTP ─────────────────────────────────────────── */}
        {step === 3 && (
          <div className="space-y-4">
            <p className="text-sm text-slate-600">Enter the 6-digit code from your authenticator app (owner's time-based PIN).</p>
            <div>
              <Label className="text-xs text-slate-500">TOTP Code</Label>
              <Input
                data-testid="reset-totp-input"
                value={totpCode}
                onChange={e => setTotpCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                placeholder="000000"
                className="font-mono text-center text-xl tracking-widest mt-1"
                maxLength={6}
                autoFocus
                onKeyDown={e => e.key === 'Enter' && handleStep3()}
              />
            </div>
            <div className="flex justify-between">
              <Button variant="outline" onClick={() => setStep(2)}>Back</Button>
              <Button
                data-testid="reset-step3-next"
                onClick={handleStep3}
                disabled={totpCode.length !== 6}
                className="bg-red-600 hover:bg-red-700 text-white"
              >
                Continue
              </Button>
            </div>
          </div>
        )}

        {/* ── Step 4: Final Confirmation ────────────────────────────── */}
        {step === 4 && (
          <div className="space-y-4">
            <div className="bg-red-50 border-2 border-red-300 rounded-lg p-4 space-y-2 text-sm">
              <p className="font-bold text-red-800 flex items-center gap-2">
                <AlertTriangle size={16} /> Final Warning
              </p>
              <ul className="text-red-700 space-y-1 list-disc list-inside">
                <li>A backup will be created automatically before reset</li>
                <li>All transactional data will be permanently deleted</li>
                <li>Only your owner account will be preserved</li>
                <li>This cannot be undone without restoring the backup</li>
              </ul>
            </div>
            <div className="flex justify-between">
              <Button variant="outline" onClick={() => setStep(3)}>Back</Button>
              <Button
                data-testid="reset-confirm-btn"
                onClick={handleReset}
                disabled={loading}
                className="bg-red-700 hover:bg-red-800 text-white font-bold"
              >
                {loading ? (
                  <span className="flex items-center gap-2">
                    <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
                    </svg>
                    Resetting...
                  </span>
                ) : (
                  'CONFIRM RESET'
                )}
              </Button>
            </div>
          </div>
        )}

        {/* ── Step 5: Success ───────────────────────────────────────── */}
        {step === 5 && result && (
          <div className="space-y-4">
            <div className="flex items-center gap-3 p-4 bg-green-50 border border-green-200 rounded-lg">
              <CheckCircle2 size={28} className="text-green-600 shrink-0" />
              <div>
                <p className="font-semibold text-green-800">Reset Complete</p>
                <p className="text-sm text-green-700">{result.total_deleted} records deleted. Your account is preserved.</p>
              </div>
            </div>

            {result.backup && (
              <div className="border border-slate-200 rounded-lg p-4 space-y-3">
                <p className="text-sm font-semibold text-slate-700">Backup Created</p>
                <div className="grid grid-cols-2 gap-2 text-xs text-slate-600">
                  <div><span className="text-slate-400">File:</span> <span className="font-mono">{result.backup.filename}</span></div>
                  <div><span className="text-slate-400">Size:</span> {result.backup.size_mb} MB</div>
                  <div><span className="text-slate-400">Documents:</span> {result.backup.total_documents?.toLocaleString()}</div>
                  <div><span className="text-slate-400">Stored:</span> {result.backup.r2_uploaded ? 'Cloudflare R2 ✓' : 'Local only'}</div>
                </div>
                <div className="flex gap-2 pt-1">
                  <Button
                    data-testid="reset-download-backup"
                    onClick={handleDownload}
                    disabled={downloadLoading || !result.backup.r2_uploaded}
                    variant="outline"
                    size="sm"
                    className="flex-1"
                  >
                    <Download size={13} className="mr-1.5" />
                    {downloadLoading ? 'Generating...' : 'Download Backup'}
                  </Button>
                  <Button
                    data-testid="reset-restore-backup"
                    onClick={handleRestore}
                    disabled={restoreLoading || !result.backup.r2_uploaded}
                    variant="outline"
                    size="sm"
                    className="flex-1 border-amber-300 text-amber-700 hover:bg-amber-50"
                  >
                    <RotateCcw size={13} className="mr-1.5" />
                    {restoreLoading ? 'Restoring...' : 'Restore This Backup'}
                  </Button>
                </div>
              </div>
            )}

            <Button
              data-testid="reset-done-btn"
              onClick={() => { handleClose(); window.location.reload(); }}
              className="w-full bg-[#1A4D2E] hover:bg-[#14532d] text-white"
            >
              Done — Reload App
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
