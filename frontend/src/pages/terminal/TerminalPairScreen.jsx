import { useState, useEffect, useRef } from 'react';
import { Loader2, Smartphone, RefreshCw, KeyRound, Hash, ChevronRight } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const WS_URL = BACKEND_URL.replace(/^http/, 'ws');

export default function TerminalPairScreen({ onPaired }) {
  const [mode, setMode] = useState('code'); // 'code' | 'credential'
  const [code, setCode] = useState('');
  const [status, setStatus] = useState('loading');
  const [expiresIn, setExpiresIn] = useState(300);
  const [qrMessage, setQrMessage] = useState('');
  const pollRef = useRef(null);
  const countdownRef = useRef(null);
  const wsRef = useRef(null);

  // Credential login state
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [credLoading, setCredLoading] = useState(false);
  const [credError, setCredError] = useState('');
  const [branches, setBranches] = useState(null);
  const [credUserName, setCredUserName] = useState('');

  // Check for QR pair token in URL
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const pairToken = params.get('pair');
    if (pairToken) {
      window.history.replaceState({}, '', window.location.pathname);
      setStatus('qr_pairing');
      setQrMessage('Connecting to branch...');
      fetch(`${BACKEND_URL}/api/terminal/qr-pair`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: pairToken }),
      })
        .then(res => res.json().then(data => ({ ok: res.ok, data })))
        .then(({ ok, data }) => {
          if (ok && data.status === 'paired') {
            onPaired({
              token: data.token,
              terminalId: data.terminal_id,
              branchId: data.branch_id,
              branchName: data.branch_name,
              userName: data.user_name,
              organizationId: data.organization_id,
            });
          } else {
            setQrMessage(data.detail || 'QR pairing failed — use the code instead');
            setTimeout(() => { setStatus('loading'); generateCode(); }, 2000);
          }
        })
        .catch(() => {
          setQrMessage('Connection failed — falling back to code');
          setTimeout(() => { setStatus('loading'); generateCode(); }, 2000);
        });
      return;
    }
    generateCode();
  }, []); // eslint-disable-line

  const generateCode = async () => {
    setStatus('loading');
    try {
      const res = await fetch(`${BACKEND_URL}/api/terminal/generate-code`, { method: 'POST' });
      const data = await res.json();
      setCode(data.code);
      setExpiresIn(data.expires_in);
      setStatus('showing');
    } catch {
      setStatus('error');
    }
  };

  // Countdown timer
  useEffect(() => {
    if (status !== 'showing' || mode !== 'code') return;
    countdownRef.current = setInterval(() => {
      setExpiresIn(prev => {
        if (prev <= 1) { setStatus('expired'); return 0; }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(countdownRef.current);
  }, [status, mode]);

  // WebSocket for instant pairing notification
  useEffect(() => {
    if (status !== 'showing' || !code || mode !== 'code') return;
    const connectWS = () => {
      try {
        const ws = new WebSocket(`${WS_URL}/api/terminal/ws/pairing/${code}`);
        wsRef.current = ws;
        ws.onmessage = (event) => {
          const msg = JSON.parse(event.data);
          if (msg.type === 'paired') {
            clearInterval(pollRef.current);
            clearInterval(countdownRef.current);
            onPaired({
              token: msg.data.token,
              terminalId: msg.data.terminal_id,
              branchId: msg.data.branch_id,
              branchName: msg.data.branch_name,
              userName: msg.data.user_name,
              organizationId: msg.data.organization_id,
            });
          }
        };
        ws.onclose = () => { wsRef.current = null; };
        ws.onerror = () => { ws.close(); };
      } catch { /* WebSocket not available */ }
    };
    connectWS();
    return () => { if (wsRef.current) { wsRef.current.close(); wsRef.current = null; } };
  }, [status, code, onPaired, mode]);

  // Polling fallback
  useEffect(() => {
    if (status !== 'showing' || !code || mode !== 'code') return;
    pollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`${BACKEND_URL}/api/terminal/poll/${code}`);
        const data = await res.json();
        if (data.status === 'paired') {
          clearInterval(pollRef.current);
          clearInterval(countdownRef.current);
          onPaired({
            token: data.token,
            terminalId: data.terminal_id,
            branchId: data.branch_id,
            branchName: data.branch_name,
            userName: data.user_name,
            organizationId: data.organization_id,
          });
        } else if (data.status === 'expired') {
          setStatus('expired');
        }
      } catch { /* keep polling */ }
    }, 3000);
    return () => clearInterval(pollRef.current);
  }, [status, code, onPaired, mode]);

  // Credential login
  const handleCredentialLogin = async (selectedBranchId) => {
    setCredLoading(true);
    setCredError('');
    try {
      const res = await fetch(`${BACKEND_URL}/api/terminal/credential-pair`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, branch_id: selectedBranchId || undefined }),
      });
      const data = await res.json();
      if (!res.ok) {
        setCredError(data.detail || 'Login failed');
        setCredLoading(false);
        return;
      }
      if (data.status === 'select_branch') {
        setBranches(data.branches);
        setCredUserName(data.user_name);
        setCredLoading(false);
        return;
      }
      if (data.status === 'paired') {
        onPaired({
          token: data.token,
          terminalId: data.terminal_id,
          branchId: data.branch_id,
          branchName: data.branch_name,
          userName: data.user_name,
          organizationId: data.organization_id,
        });
      }
    } catch {
      setCredError('Connection failed. Check your network.');
    }
    setCredLoading(false);
  };

  const mins = Math.floor(expiresIn / 60);
  const secs = expiresIn % 60;

  return (
    <div className="min-h-screen bg-gradient-to-b from-slate-900 via-slate-800 to-slate-900 flex items-center justify-center p-4" data-testid="terminal-pair-screen">
      <div className="w-full max-w-md text-center">
        {/* Logo */}
        <div className="mb-6">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-emerald-500/10 border border-emerald-500/20 mb-4">
            <Smartphone className="w-8 h-8 text-emerald-400" />
          </div>
          <h1 className="text-2xl font-bold text-white tracking-tight" style={{ fontFamily: 'Manrope, system-ui' }}>
            AgriSmart Terminal
          </h1>
        </div>

        {/* Mode Tabs */}
        <div className="flex rounded-xl bg-slate-800/60 border border-slate-700/50 p-1 mb-6" data-testid="pair-mode-tabs">
          <button
            onClick={() => { setMode('code'); setBranches(null); setCredError(''); }}
            className={`flex-1 flex items-center justify-center gap-2 py-2.5 rounded-lg text-sm font-medium transition-all ${
              mode === 'code' ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30' : 'text-slate-400 hover:text-slate-300'
            }`}
            data-testid="mode-code-btn">
            <Hash size={15} /> Pairing Code
          </button>
          <button
            onClick={() => { setMode('credential'); setBranches(null); setCredError(''); }}
            className={`flex-1 flex items-center justify-center gap-2 py-2.5 rounded-lg text-sm font-medium transition-all ${
              mode === 'credential' ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30' : 'text-slate-400 hover:text-slate-300'
            }`}
            data-testid="mode-credential-btn">
            <KeyRound size={15} /> Login
          </button>
        </div>

        {/* ═══ CODE MODE ═══ */}
        {mode === 'code' && (
          <>
            <p className="text-slate-400 text-sm mb-4">Enter this code on your computer to connect</p>
            <div className="bg-slate-800/60 backdrop-blur-md rounded-2xl border border-slate-700/50 p-8 mb-6">
              {status === 'loading' && (
                <div className="py-8">
                  <Loader2 className="w-8 h-8 animate-spin text-emerald-400 mx-auto" />
                  <p className="text-slate-400 text-sm mt-3">Generating code...</p>
                </div>
              )}
              {status === 'qr_pairing' && (
                <div className="py-8">
                  <Loader2 className="w-8 h-8 animate-spin text-emerald-400 mx-auto" />
                  <p className="text-emerald-400 text-sm mt-3 font-medium">{qrMessage || 'Connecting...'}</p>
                </div>
              )}
              {status === 'showing' && (
                <>
                  <div className="flex justify-center gap-2 mb-4" data-testid="pairing-code">
                    {code.split('').map((char, i) => (
                      <div key={i} className="w-12 h-14 sm:w-14 sm:h-16 flex items-center justify-center bg-slate-900/80 rounded-xl border border-slate-600/50 text-2xl sm:text-3xl font-mono font-bold text-white tracking-widest">
                        {char}
                      </div>
                    ))}
                  </div>
                  <p className="text-slate-500 text-xs">
                    Code expires in <span className="text-emerald-400 font-mono">{mins}:{secs.toString().padStart(2, '0')}</span>
                  </p>
                  <div className="mt-2 flex items-center justify-center gap-1.5">
                    <span className="w-1.5 h-1.5 bg-emerald-400 rounded-full animate-pulse" />
                    <span className="text-emerald-400/70 text-[10px]">Waiting for connection...</span>
                  </div>
                </>
              )}
              {status === 'expired' && (
                <div className="py-6">
                  <p className="text-slate-400 text-sm mb-4">Code expired</p>
                  <Button onClick={generateCode} variant="outline" className="border-emerald-500/30 text-emerald-400 hover:bg-emerald-500/10" data-testid="regenerate-code-btn">
                    <RefreshCw className="w-4 h-4 mr-2" /> Generate New Code
                  </Button>
                </div>
              )}
              {status === 'error' && (
                <div className="py-6">
                  <p className="text-red-400 text-sm mb-4">Failed to generate code. Check your connection.</p>
                  <Button onClick={generateCode} variant="outline" className="border-red-500/30 text-red-400 hover:bg-red-500/10" data-testid="retry-code-btn">
                    <RefreshCw className="w-4 h-4 mr-2" /> Retry
                  </Button>
                </div>
              )}
            </div>
            {/* Instructions */}
            <div className="text-left space-y-3 px-2">
              <h3 className="text-slate-300 text-xs font-semibold uppercase tracking-wider">How to connect</h3>
              <div className="flex items-start gap-3">
                <span className="flex-shrink-0 w-6 h-6 rounded-full bg-emerald-500/20 text-emerald-400 text-xs font-bold flex items-center justify-center">1</span>
                <p className="text-slate-400 text-sm">Open <strong className="text-slate-300">Settings</strong> on your computer</p>
              </div>
              <div className="flex items-start gap-3">
                <span className="flex-shrink-0 w-6 h-6 rounded-full bg-emerald-500/20 text-emerald-400 text-xs font-bold flex items-center justify-center">2</span>
                <p className="text-slate-400 text-sm">Go to <strong className="text-slate-300">Connect Terminal</strong> tab</p>
              </div>
              <div className="flex items-start gap-3">
                <span className="flex-shrink-0 w-6 h-6 rounded-full bg-emerald-500/20 text-emerald-400 text-xs font-bold flex items-center justify-center">3</span>
                <p className="text-slate-400 text-sm">Enter the code above and select a branch</p>
              </div>
            </div>
          </>
        )}

        {/* ═══ CREDENTIAL MODE ═══ */}
        {mode === 'credential' && (
          <div className="bg-slate-800/60 backdrop-blur-md rounded-2xl border border-slate-700/50 p-6">
            {!branches ? (
              <>
                <p className="text-slate-400 text-sm mb-5">Login with your account to connect this terminal</p>
                <div className="space-y-4 text-left">
                  <div>
                    <Label className="text-xs text-slate-400 uppercase tracking-wide">Email</Label>
                    <Input
                      type="email"
                      value={email}
                      onChange={e => setEmail(e.target.value)}
                      placeholder="your@email.com"
                      className="mt-1 bg-slate-900/80 border-slate-600/50 text-white placeholder:text-slate-500"
                      data-testid="cred-email"
                    />
                  </div>
                  <div>
                    <Label className="text-xs text-slate-400 uppercase tracking-wide">Password</Label>
                    <Input
                      type="password"
                      autoComplete="new-password"
                      value={password}
                      onChange={e => setPassword(e.target.value)}
                      placeholder="Enter password"
                      onKeyDown={e => { if (e.key === 'Enter') handleCredentialLogin(); }}
                      className="mt-1 bg-slate-900/80 border-slate-600/50 text-white placeholder:text-slate-500"
                      data-testid="cred-password"
                    />
                  </div>
                  {credError && (
                    <p className="text-red-400 text-xs text-center" data-testid="cred-error">{credError}</p>
                  )}
                  <Button
                    onClick={() => handleCredentialLogin()}
                    disabled={credLoading || !email || !password}
                    className="w-full bg-emerald-600 hover:bg-emerald-700 text-white disabled:opacity-40"
                    data-testid="cred-login-btn">
                    {credLoading ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : <KeyRound size={15} className="mr-2" />}
                    {credLoading ? 'Connecting...' : 'Connect Terminal'}
                  </Button>
                </div>
                <p className="text-slate-500 text-[10px] mt-4">
                  Manager accounts auto-link to their assigned branch.
                  Admin accounts can select any branch.
                </p>
              </>
            ) : (
              /* Branch selection (admin login) */
              <div data-testid="branch-selection">
                <p className="text-emerald-400 text-sm font-medium mb-1">Welcome, {credUserName}</p>
                <p className="text-slate-400 text-xs mb-4">Select a branch for this terminal:</p>
                <div className="space-y-2 max-h-64 overflow-y-auto">
                  {branches.map(b => (
                    <button
                      key={b.id}
                      onClick={() => handleCredentialLogin(b.id)}
                      disabled={credLoading}
                      className="w-full flex items-center justify-between px-4 py-3 rounded-xl bg-slate-900/60 border border-slate-700/50 hover:border-emerald-500/40 hover:bg-emerald-500/5 transition-all text-left"
                      data-testid={`branch-option-${b.id}`}>
                      <span className="text-sm text-white font-medium">{b.name}</span>
                      <ChevronRight size={16} className="text-slate-500" />
                    </button>
                  ))}
                </div>
                {credError && (
                  <p className="text-red-400 text-xs text-center mt-3">{credError}</p>
                )}
                <button
                  onClick={() => { setBranches(null); setCredError(''); }}
                  className="text-slate-500 text-xs hover:text-slate-300 mt-3 underline">
                  Back to login
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
