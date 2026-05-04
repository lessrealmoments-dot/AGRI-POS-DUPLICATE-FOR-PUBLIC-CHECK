/**
 * PriceScanManager — Global smart price scanner.
 * Two surfaces:
 *   1) "Below Cost" — products where any scheme is sold below capital.
 *      All price updates are PIN-gated (admin or TOTP only).
 *   2) "Capital Changes" — recent capital-cost moves from POs/transfers
 *      worth reviewing. ≥₱1 threshold. Skips admin-overridden ones.
 */
import { useState, useEffect, useRef, useCallback } from 'react';
import { useAuth, api } from '../contexts/AuthContext';
import { formatPHP } from '../lib/utils';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Badge } from './ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from './ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';
import { AlertTriangle, RefreshCw, Check, Clock, ChevronDown, X, TrendingUp, TrendingDown, Lock } from 'lucide-react';
import { toast } from 'sonner';
import CalcInput from './CalcInput';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const SCAN_INTERVAL_MS = 5 * 60 * 1000; // 5 minutes
const SKIP_KEY = 'agribooks_price_scan_skip';

const SKIP_OPTIONS = [
  { label: '30 minutes', ms: 30 * 60 * 1000 },
  { label: '1 hour', ms: 60 * 60 * 1000 },
  { label: '4 hours', ms: 4 * 60 * 60 * 1000 },
  { label: '6 hours', ms: 6 * 60 * 60 * 1000 },
];

export default function PriceScanManager() {
  const { user, selectedBranchId, currentBranch, branches } = useAuth();
  const [dialog, setDialog] = useState(false);
  const [tab, setTab] = useState('below_cost'); // 'below_cost' | 'capital_changes'
  const [issues, setIssues] = useState([]);
  const [schemes, setSchemes] = useState([]);
  const [editPrices, setEditPrices] = useState({}); // { product_id: { scheme_key: value } }
  const [saving, setSaving] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [showSkipMenu, setShowSkipMenu] = useState(false);
  const [skipUntil, setSkipUntil] = useState(null); // timestamp
  const [lastScanCount, setLastScanCount] = useState(null);

  // Capital-change alerts (Stage 2)
  const [capAlerts, setCapAlerts] = useState([]);
  const [capLoading, setCapLoading] = useState(false);

  // Admin PIN modal — gates ALL price changes from this dialog
  const [pinModal, setPinModal] = useState(null); // { kind: 'single'|'all', issue?, onConfirm }
  const [adminPin, setAdminPin] = useState('');
  const [pinSubmitting, setPinSubmitting] = useState(false);

  const scanTimerRef = useRef(null);
  const skipMenuRef = useRef(null);

  // Load skip state from localStorage
  useEffect(() => {
    const stored = localStorage.getItem(SKIP_KEY);
    if (stored) {
      const until = parseInt(stored, 10);
      if (until > Date.now()) setSkipUntil(until);
      else localStorage.removeItem(SKIP_KEY);
    }
  }, []);

  // Click outside skip menu
  useEffect(() => {
    const h = (e) => { if (skipMenuRef.current && !skipMenuRef.current.contains(e.target)) setShowSkipMenu(false); };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, []);

  const isInSkipWindow = () => {
    const stored = localStorage.getItem(SKIP_KEY);
    if (!stored) return false;
    const until = parseInt(stored, 10);
    if (until > Date.now()) return true;
    localStorage.removeItem(SKIP_KEY);
    return false;
  };

  // Skip the scan entirely for super admins (no tenant context — would either
  // leak cross-org data pre-fix, or pop an empty dialog post-fix). Same guard
  // for users with no organization — there's nothing to scan.
  const skipForSuperAdmin = !!user && (
    user.is_super_admin || !user.organization_id
  );

  const runScan = useCallback(async (showDialogIfFound = true) => {
    if (!user) return;
    if (skipForSuperAdmin) return; // hard guard — never scan without tenant context
    setScanning(true);
    try {
      const branchId = currentBranch?.id || '';
      const inSkip = isInSkipWindow();
      // Always notify if issues found (even during skip, creates notification)
      const params = new URLSearchParams();
      if (branchId) params.set('branch_id', branchId);
      if (!inSkip) params.set('notify', 'true'); // only notify outside skip window

      const res = await api.get(`${BACKEND_URL}/api/products/pricing-scan?${params}`);
      const data = res.data;
      setLastScanCount(data.critical_total || 0);  // only count critical (retail/wholesale)

      if (data.total > 0) {
        setIssues(data.issues);
        setSchemes(data.schemes || []);
        // Initialize edit prices from current values
        const init = {};
        data.issues.forEach(issue => {
          init[issue.product_id] = {};
          (data.schemes || []).forEach(s => {
            init[issue.product_id][s.key] = issue.prices[s.key] || '';
          });
        });
        setEditPrices(init);

        if (showDialogIfFound && !inSkip) {
          setDialog(true);
        }
      } else {
        setIssues([]);
        setLastScanCount(0);
      }
    } catch (e) {
      // Silent fail — don't alert user on background scan errors
    }
    setScanning(false);
  }, [user, currentBranch?.id]);

  // Start scan timer
  useEffect(() => {
    if (!user) return;
    if (skipForSuperAdmin) return; // no scanning for super admin / no-org context
    // Initial scan after 30 seconds (let app load first)
    const initialDelay = setTimeout(() => runScan(true), 30_000);
    // Then every 5 minutes
    scanTimerRef.current = setInterval(() => runScan(true), SCAN_INTERVAL_MS);
    return () => {
      clearTimeout(initialDelay);
      clearInterval(scanTimerRef.current);
    };
  }, [user, runScan, skipForSuperAdmin]);

  // Re-scan when branch changes
  useEffect(() => {
    if (user && currentBranch?.id && !skipForSuperAdmin) {
      runScan(false); // silent rescan on branch switch
    }
  }, [currentBranch?.id, skipForSuperAdmin]); // eslint-disable-line

  // ── Capital change alerts ──────────────────────────────────────────────
  const fetchCapAlerts = useCallback(async () => {
    if (!user || skipForSuperAdmin) return;
    setCapLoading(true);
    try {
      const params = new URLSearchParams();
      if (currentBranch?.id) params.set('branch_id', currentBranch.id);
      params.set('days', '14');
      const r = await api.get(`${BACKEND_URL}/api/products/capital-change-alerts?${params}`);
      setCapAlerts(r.data?.alerts || []);
    } catch { /* silent */ }
    setCapLoading(false);
  }, [user, currentBranch?.id, skipForSuperAdmin]);

  useEffect(() => { fetchCapAlerts(); }, [fetchCapAlerts]);

  const ackCapAlert = async (id) => {
    try {
      await api.post(`${BACKEND_URL}/api/products/capital-change-alerts/${id}/acknowledge`);
      setCapAlerts(prev => prev.filter(a => a.id !== id));
      toast.success('Alert dismissed');
    } catch (e) {
      toast.error('Failed to dismiss');
    }
  };

  const ackAllCapAlerts = async () => {
    if (user.role !== 'admin') { toast.error('Admin only'); return; }
    try {
      const body = currentBranch?.id ? { branch_id: currentBranch.id } : {};
      await api.post(`${BACKEND_URL}/api/products/capital-change-alerts/acknowledge-all`, body);
      setCapAlerts([]);
      toast.success('All alerts dismissed');
    } catch { toast.error('Failed'); }
  };

  const handleSkip = (skipMs) => {
    const until = Date.now() + skipMs;
    localStorage.setItem(SKIP_KEY, String(until));
    setSkipUntil(until);
    setShowSkipMenu(false);
    setDialog(false);
    // Still notify in the background
    const branchId = currentBranch?.id || '';
    const params = new URLSearchParams({ notify: 'true' });
    if (branchId) params.set('branch_id', branchId);
    api.get(`${BACKEND_URL}/api/products/pricing-scan?${params}`).catch(() => {});
    toast.info(`Price issue alert snoozed. Notification created for your records.`);
  };

  const updatePrice = (productId, schemeKey, value) => {
    setEditPrices(prev => ({
      ...prev,
      [productId]: { ...prev[productId], [schemeKey]: value },
    }));
  };

  const handleSaveProduct = (issue) => {
    // Open PIN modal — actual save happens after PIN is verified
    setPinModal({ kind: 'single', issue });
    setAdminPin('');
  };

  const handleSaveAll = () => {
    if (!issues.length) return;
    setPinModal({ kind: 'all' });
    setAdminPin('');
  };

  // PIN-gated execution. The backend /products/smart-price-update endpoint
  // re-validates the PIN against admin/TOTP — managers/cashiers cannot bypass.
  const executePinned = async () => {
    if (!adminPin) {
      toast.error('Admin PIN is required');
      return;
    }
    setPinSubmitting(true);
    try {
      if (pinModal.kind === 'single') {
        const issue = pinModal.issue;
        const prices = editPrices[issue.product_id] || {};
        const cleaned = {};
        Object.entries(prices).forEach(([k, v]) => {
          const num = parseFloat(v);
          if (!isNaN(num) && num > 0) cleaned[k] = num;
        });
        await api.post(`${BACKEND_URL}/api/products/smart-price-update`, {
          product_id: issue.product_id,
          prices: cleaned,
          pin: adminPin,
        });
        toast.success(`${issue.product_name} prices updated`);
        setIssues(prev => prev.filter(i => i.product_id !== issue.product_id));
        if (issues.length <= 1) {
          setDialog(false);
          toast.success('All pricing issues resolved!');
        }
      } else {
        // Bulk fix-all: one PIN unlocks the whole batch
        let fixed = 0;
        let failed = 0;
        for (const issue of issues) {
          const prices = editPrices[issue.product_id] || {};
          const cleaned = {};
          Object.entries(prices).forEach(([k, v]) => {
            const num = parseFloat(v);
            if (!isNaN(num) && num > 0) cleaned[k] = num;
          });
          if (!Object.keys(cleaned).length) continue;
          try {
            await api.post(`${BACKEND_URL}/api/products/smart-price-update`, {
              product_id: issue.product_id,
              prices: cleaned,
              pin: adminPin,
            });
            fixed++;
          } catch (e) {
            failed++;
            // If PIN was wrong, abort early to avoid dozens of identical errors
            if (e.response?.status === 403) {
              throw e;
            }
          }
        }
        toast.success(`${fixed} product(s) updated${failed ? `, ${failed} failed` : ''}`);
        setDialog(false);
        setIssues([]);
        setLastScanCount(0);
      }
      setPinModal(null);
      setAdminPin('');
    } catch (e) {
      const msg = e.response?.data?.detail || 'Failed';
      toast.error(msg);
      // Keep the modal open on PIN failure so they can retry
      if (e.response?.status !== 403) {
        setPinModal(null);
      }
    }
    setPinSubmitting(false);
  };

  // Primary schemes to show prominently
  const primarySchemes = schemes.filter(s => ['retail', 'wholesale'].includes(s.key));
  const otherSchemes = schemes.filter(s => !['retail', 'wholesale'].includes(s.key));

  if (!user) return null;

  return (
    <>
      {/* Floating scan indicator — shows when issues exist and dialog is closed */}
      {((lastScanCount !== null && lastScanCount > 0) || capAlerts.length > 0) && !dialog && (
        <button
          onClick={() => {
            setDialog(true);
            // Auto-open the more urgent tab
            if (lastScanCount > 0) setTab('below_cost');
            else setTab('capital_changes');
          }}
          className="fixed bottom-6 right-6 z-40 flex items-center gap-2 px-4 py-2.5 bg-amber-600 hover:bg-amber-700 text-white rounded-full shadow-lg text-sm font-medium transition-all animate-bounce"
          data-testid="price-scan-alert-btn"
        >
          <AlertTriangle size={16} />
          {lastScanCount > 0 && (
            <span>{lastScanCount} below cost</span>
          )}
          {lastScanCount > 0 && capAlerts.length > 0 && <span className="opacity-50">·</span>}
          {capAlerts.length > 0 && (
            <span>{capAlerts.length} capital change{capAlerts.length === 1 ? '' : 's'}</span>
          )}
        </button>
      )}

      {/* Price Scan Dialog */}
      <Dialog open={dialog} onOpenChange={v => { if (!v) setDialog(false); }}>
        <DialogContent className="max-w-6xl max-h-[90vh] flex flex-col p-0">
          {/* Header */}
          <div className="flex items-center justify-between px-6 py-4 border-b bg-amber-50">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-lg bg-amber-600 flex items-center justify-center">
                <AlertTriangle size={18} className="text-white" />
              </div>
              <div>
                <DialogTitle style={{ fontFamily: 'Manrope' }} className="text-base font-bold text-amber-900">
                  Smart Price Scan — {issues.length} Product{issues.length !== 1 ? 's' : ''} Below Cost
                </DialogTitle>
                <p className="text-xs text-amber-700 mt-0.5">
                  <b>Retail & Wholesale</b> are below capital cost and must be fixed.
                  Other schemes (Special, Government) are optional — skip or update as needed.
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline" size="sm"
                onClick={() => runScan(true)} disabled={scanning}
                className="h-8 text-xs border-amber-300 text-amber-800 hover:bg-amber-100"
                data-testid="rescan-btn"
              >
                <RefreshCw size={12} className={`mr-1.5 ${scanning ? 'animate-spin' : ''}`} />
                Re-scan
              </Button>
              <Button
                onClick={handleSaveAll} disabled={saving}
                className="h-8 text-xs bg-[#1A4D2E] hover:bg-[#14532d] text-white"
                data-testid="fix-all-btn"
              >
                {saving ? <RefreshCw size={12} className="animate-spin mr-1.5" /> : <Check size={12} className="mr-1.5" />}
                Fix All
              </Button>
              {/* Skip button with dropdown */}
              <div className="relative" ref={skipMenuRef}>
                <Button
                  variant="outline" size="sm"
                  onClick={() => setShowSkipMenu(v => !v)}
                  className="h-8 text-xs border-slate-300 text-slate-600"
                  data-testid="skip-btn"
                >
                  <Clock size={12} className="mr-1" />
                  Skip
                  <ChevronDown size={12} className="ml-1" />
                </Button>
                {showSkipMenu && (
                  <div className="absolute right-0 top-9 z-50 bg-white border border-slate-200 rounded-lg shadow-xl w-44 py-1">
                    <p className="text-[10px] text-slate-400 px-3 py-1.5 font-medium uppercase">Snooze for...</p>
                    {SKIP_OPTIONS.map(opt => (
                      <button key={opt.ms} onClick={() => handleSkip(opt.ms)}
                        className="w-full text-left px-3 py-2 text-sm hover:bg-slate-50 flex items-center gap-2">
                        <Clock size={12} className="text-slate-400" />
                        {opt.label}
                      </button>
                    ))}
                    <p className="text-[9px] text-slate-400 px-3 py-1.5 border-t mt-1">
                      Notification stays in your bell even when snoozed.
                    </p>
                  </div>
                )}
              </div>
              <button onClick={() => setDialog(false)} className="text-slate-400 hover:text-slate-600 ml-1">
                <X size={16} />
              </button>
            </div>
          </div>

          <div className="px-6 py-2 flex items-center gap-4 text-xs border-b bg-slate-50">
            {/* Tabs */}
            <button
              onClick={() => setTab('below_cost')}
              data-testid="tab-below-cost"
              className={`pb-1.5 -mb-2 border-b-2 transition-colors text-xs font-medium ${
                tab === 'below_cost' ? 'border-amber-600 text-amber-700' : 'border-transparent text-slate-500 hover:text-slate-700'
              }`}
            >
              Below Cost ({issues.length})
            </button>
            <button
              onClick={() => { setTab('capital_changes'); fetchCapAlerts(); }}
              data-testid="tab-capital-changes"
              className={`pb-1.5 -mb-2 border-b-2 transition-colors text-xs font-medium ${
                tab === 'capital_changes' ? 'border-amber-600 text-amber-700' : 'border-transparent text-slate-500 hover:text-slate-700'
              }`}
            >
              Capital Changes ({capAlerts.length})
            </button>
            <span className="ml-auto" />
            {tab === 'below_cost' && (
              <>
                <span className="flex items-center gap-1.5">
                  <div className="w-3 h-3 rounded bg-red-100 border border-red-400" />
                  <span className="text-slate-600">Below capital — <b>must fix</b></span>
                </span>
                <span className="flex items-center gap-1.5">
                  <div className="w-3 h-3 rounded bg-amber-50 border border-amber-300" />
                  <span className="text-slate-600">Below capital — <i>optional</i></span>
                </span>
                <span className="flex items-center gap-1.5">
                  <Lock size={11} className="text-slate-500" />
                  <span className="text-slate-600">Admin PIN required</span>
                </span>
              </>
            )}
            {tab === 'capital_changes' && (
              <span className="text-slate-500">
                Showing changes ≥ ₱1 from POs &amp; transfers (last 14 days). Admin overrides skipped.
              </span>
            )}
          </div>

          {/* Table */}
          {tab === 'below_cost' && (
          <div className="flex-1 overflow-auto">
            <table className="w-full text-sm border-collapse" data-testid="price-scan-table">
              <thead className="sticky top-0 z-10">
                <tr className="bg-slate-100 border-b-2 border-slate-300">
                  <th className="text-left px-3 py-2.5 text-xs uppercase text-slate-600 font-semibold" style={{minWidth:'200px'}}>Product</th>
                  <th className="text-right px-3 py-2.5 text-xs uppercase text-slate-600 font-semibold" style={{minWidth:'90px'}}>Last Purchase</th>
                  <th className="text-right px-3 py-2.5 text-xs uppercase text-slate-600 font-semibold" style={{minWidth:'90px'}}>Moving Avg</th>
                  <th className="px-1 py-2.5 w-px bg-slate-200" />
                  {/* Primary: Retail + Wholesale — REQUIRED */}
                  {primarySchemes.map(s => (
                    <th key={s.key} className="text-center px-3 py-2.5 text-xs uppercase font-semibold text-[#1A4D2E] bg-emerald-50/50" style={{minWidth:'130px'}}>
                      {s.name}
                      <span className="ml-1 text-[9px] bg-red-100 text-red-700 px-1 py-0.5 rounded font-bold">Required</span>
                    </th>
                  ))}
                  {/* Other schemes — OPTIONAL */}
                  {otherSchemes.map(s => (
                    <th key={s.key} className="text-center px-3 py-2.5 text-xs uppercase font-semibold text-slate-400" style={{minWidth:'120px'}}>
                      {s.name}
                      <span className="ml-1 text-[9px] bg-slate-100 text-slate-500 px-1 py-0.5 rounded">Optional</span>
                    </th>
                  ))}
                  <th className="text-center px-3 py-2.5 text-xs uppercase text-slate-600 font-semibold w-24">Issues</th>
                  <th className="w-20 px-3 py-2.5"></th>
                </tr>
              </thead>
              <tbody>
                {issues.map((issue) => {
                  const problemKeys = new Set(issue.problem_schemes.map(p => p.scheme_key));
                  return (
                    <tr key={issue.product_id} className="border-b border-slate-100 hover:bg-amber-50/20">
                      {/* Product info */}
                      <td className="px-3 py-2">
                        <p className="font-semibold text-sm text-slate-800">
                          {issue.product_name}
                          {issue.is_repack && (
                            <span className="ml-1.5 text-[9px] font-bold px-1.5 py-0.5 rounded bg-purple-100 text-purple-700 align-middle">REPACK</span>
                          )}
                        </p>
                        <p className="text-[10px] text-slate-400 font-mono">
                          {issue.sku} · {issue.category}
                          {issue.is_repack && issue.parent_name && (
                            <span className="ml-1 text-purple-500"> from {issue.parent_name} ({issue.units_per_parent || 1}/parent)</span>
                          )}
                        </p>
                        <p className="text-[10px] text-amber-700 font-semibold mt-0.5">
                          Capital: {formatPHP(issue.effective_cost)}
                          {issue.is_branch_specific_cost && (
                            <span className="ml-1 text-slate-400">(branch)</span>
                          )}
                          {issue.is_repack && (
                            <span className="ml-1 text-purple-500">(derived)</span>
                          )}
                        </p>
                      </td>

                      {/* Last purchase */}
                      <td className="px-3 py-2 text-right">
                        <span className="text-sm font-mono font-medium">{formatPHP(issue.last_purchase)}</span>
                        {issue.last_purchase > 0 && issue.last_purchase !== issue.effective_cost && (
                          <p className="text-[9px] text-slate-400">LP cost</p>
                        )}
                      </td>

                      {/* Moving average */}
                      <td className="px-3 py-2 text-right">
                        <span className="text-sm font-mono font-medium">{formatPHP(issue.moving_average)}</span>
                        <p className="text-[9px] text-slate-400">Avg cost</p>
                      </td>

                      {/* Divider */}
                      <td className="px-1 bg-slate-100 w-px" />

                      {/* Primary schemes (Retail, Wholesale) — editable */}
                      {primarySchemes.map(s => {
                        const isBad = problemKeys.has(s.key);
                        const currentPrice = issue.prices[s.key] || 0;
                        const editVal = editPrices[issue.product_id]?.[s.key] ?? '';
                        const editNum = parseFloat(editVal);
                        const isFixed = !isNaN(editNum) && editNum > 0 && editNum >= issue.effective_cost;
                        return (
                          <td key={s.key} className="px-2 py-2 bg-emerald-50/30">
                            <div>
                              {/* Current price (old) */}
                              <div className={`text-[10px] mb-0.5 font-mono ${isBad ? 'text-red-500 line-through' : 'text-slate-400'}`}>
                                was: {formatPHP(currentPrice)}
                              </div>
                              {/* New price input */}
                              <CalcInput value={editVal}
 onChange={(v) => updatePrice(issue.product_id, s.key, v)}
 placeholder={`min ₱${issue.effective_cost.toFixed(2)}`}
 className={`h-8 text-sm text-right font-mono font-bold w-full ${
 isBad && (!editNum || editNum < issue.effective_cost)
 ? 'border-red-400 bg-red-50 text-red-700'
 : isFixed
 ? 'border-emerald-400 bg-emerald-50 text-emerald-800'
 : 'border-slate-200'
 }`}
 data-testid={`price-${issue.product_id}-${s.key}`} />
                              {/* Margin indicator */}
                              {isFixed && (
                                <p className="text-[9px] text-emerald-600 mt-0.5 text-right font-mono">
                                  +{formatPHP(editNum - issue.effective_cost)} margin
                                </p>
                              )}
                              {isBad && editNum > 0 && editNum < issue.effective_cost && (
                                <p className="text-[9px] text-red-500 mt-0.5 text-right">still below cost</p>
                              )}
                            </div>
                          </td>
                        );
                      })}

                      {/* Other schemes — optional, lighter styling */}
                      {otherSchemes.map(s => {
                        const isBad = problemKeys.has(s.key);
                        const currentPrice = issue.prices[s.key] || 0;
                        const editVal = editPrices[issue.product_id]?.[s.key] ?? '';
                        const editNum = parseFloat(editVal);
                        return (
                          <td key={s.key} className="px-2 py-2 bg-slate-50/30">
                            <div>
                              <div className={`text-[10px] mb-0.5 font-mono ${isBad ? 'text-amber-500 line-through' : 'text-slate-400'}`}>
                                {formatPHP(currentPrice)}
                              </div>
                              <CalcInput value={editVal}
 onChange={(v) => updatePrice(issue.product_id, s.key, v)}
 placeholder="optional"
 className={`h-8 text-sm text-right font-mono w-full text-slate-600 ${
 isBad && (!editNum || editNum < issue.effective_cost)
 ? 'border-amber-300 bg-amber-50/50'
 : 'border-slate-200'
 }`}
 data-testid={`price-${issue.product_id}-${s.key}`} />
                              {isBad && (
                                <p className="text-[9px] text-amber-500 mt-0.5 text-right">optional to fix</p>
                              )}
                            </div>
                          </td>
                        );
                      })}

                      {/* Issue badges — critical in red, optional in amber */}
                      <td className="px-3 py-2 text-center">
                        <div className="flex flex-col gap-0.5 items-center">
                          {issue.problem_schemes.map(p => (
                            <Badge key={p.scheme_key}
                              className={`text-[9px] px-1.5 ${p.is_critical ? 'bg-red-100 text-red-700' : 'bg-amber-100 text-amber-700'}`}>
                              {p.scheme_name}: -{formatPHP(p.deficit)}
                              {!p.is_critical && <span className="ml-1 opacity-60">(opt)</span>}
                            </Badge>
                          ))}
                        </div>
                      </td>

                      {/* Per-row fix button */}
                      <td className="px-2 py-2 text-center">
                        <Button size="sm" variant="outline"
                          onClick={() => handleSaveProduct(issue)}
                          className="h-7 text-[11px] text-emerald-700 border-emerald-300 hover:bg-emerald-50"
                          data-testid={`fix-${issue.product_id}`}>
                          Fix
                        </Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          )}

          {/* Capital Changes tab */}
          {tab === 'capital_changes' && (
            <div className="flex-1 overflow-auto" data-testid="capital-changes-list">
              {capLoading && capAlerts.length === 0 && (
                <div className="p-12 text-center text-sm text-slate-400">Loading…</div>
              )}
              {!capLoading && capAlerts.length === 0 && (
                <div className="p-12 text-center">
                  <Check size={32} className="text-emerald-500 mx-auto mb-2" />
                  <p className="text-sm font-medium text-slate-700">No capital changes pending review</p>
                  <p className="text-xs text-slate-400 mt-1">
                    All recent POs and branch transfers within tolerance, or already acknowledged.
                  </p>
                </div>
              )}
              {capAlerts.length > 0 && (
                <div className="divide-y divide-slate-100">
                  {capAlerts.map(a => (
                    <div key={a.id} className="px-6 py-3 flex items-start gap-3 hover:bg-amber-50/30" data-testid={`cap-alert-${a.id}`}>
                      <div className={`w-9 h-9 rounded-full flex items-center justify-center flex-shrink-0 ${a.direction === 'up' ? 'bg-red-100 text-red-600' : 'bg-emerald-100 text-emerald-600'}`}>
                        {a.direction === 'up' ? <TrendingUp size={16} /> : <TrendingDown size={16} />}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-baseline gap-2 flex-wrap">
                          <p className="text-sm font-semibold text-slate-800 truncate">{a.product_name}</p>
                          <span className="text-[10px] text-slate-400 font-mono">{a.sku}</span>
                          {a.branch_name && (
                            <Badge variant="outline" className="text-[9px] py-0 px-1.5">{a.branch_name}</Badge>
                          )}
                        </div>
                        <p className={`text-sm font-medium mt-0.5 ${a.direction === 'up' ? 'text-red-700' : 'text-emerald-700'}`}>
                          Capital {a.direction === 'up' ? 'up' : 'down'} by {a.delta_pct !== null ? `${a.delta_pct >= 0 ? '+' : ''}${a.delta_pct.toFixed(1)}%` : '—'}
                          {' '}({a.delta_amount >= 0 ? '+' : ''}{formatPHP(a.delta_amount)})
                        </p>
                        <p className="text-xs text-slate-500 mt-0.5">
                          {formatPHP(a.old_capital)} → <strong>{formatPHP(a.new_capital)}</strong>
                          <span className="ml-2 text-slate-400">·</span>
                          <span className="ml-2">
                            {a.source_type === 'purchase_order' ? 'PO' : 'Transfer'} {a.source_ref}
                          </span>
                          {a.vendor && <span className="ml-2">· Vendor: <strong>{a.vendor}</strong></span>}
                          {a.from_branch && <span className="ml-2">· From: {a.from_branch}</span>}
                          <span className="ml-2 text-slate-400">·</span>
                          <span className="ml-2">{new Date(a.changed_at).toLocaleString()}</span>
                          {a.changed_by_name && <span className="ml-2">· {a.changed_by_name}</span>}
                        </p>
                      </div>
                      <Button
                        size="sm" variant="outline"
                        onClick={() => ackCapAlert(a.id)}
                        className="h-7 text-[11px] flex-shrink-0"
                        data-testid={`ack-cap-${a.id}`}
                      >
                        <Check size={11} className="mr-1" /> Acknowledge
                      </Button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Footer */}
          <div className="px-6 py-3 border-t bg-slate-50 flex items-center justify-between">
            <p className="text-xs text-slate-500">
              {tab === 'below_cost' ? (
                <>Scans run every 5 minutes. Notifications sent to owner and branch managers.</>
              ) : (
                <>Capital change alerts surface within 14 days of the change. ≥₱1 threshold.</>
              )}
              {skipUntil && skipUntil > Date.now() && tab === 'below_cost' && (
                <span className="ml-2 text-amber-600">
                  Next popup: {new Date(skipUntil).toLocaleTimeString()}
                </span>
              )}
            </p>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" onClick={() => setDialog(false)}>
                Close
              </Button>
              {tab === 'below_cost' && (
                <Button size="sm" onClick={handleSaveAll} disabled={saving || !issues.length}
                  className="bg-[#1A4D2E] hover:bg-[#14532d] text-white">
                  <Lock size={11} className="mr-1.5" />
                  Update All ({issues.length}) — PIN required
                </Button>
              )}
              {tab === 'capital_changes' && capAlerts.length > 0 && user?.role === 'admin' && (
                <Button size="sm" onClick={ackAllCapAlerts}
                  className="bg-[#1A4D2E] hover:bg-[#14532d] text-white" data-testid="ack-all-cap-btn">
                  <Check size={13} className="mr-1.5" />
                  Acknowledge All ({capAlerts.length})
                </Button>
              )}
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Admin PIN Modal — gates all price changes from Smart Price Checker */}
      <Dialog open={!!pinModal} onOpenChange={v => { if (!v) { setPinModal(null); setAdminPin(''); } }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-base">
              <Lock size={16} className="text-amber-600" />
              Admin Authorization Required
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <p className="text-sm text-slate-600">
              {pinModal?.kind === 'all'
                ? `Apply price updates to ${issues.length} product${issues.length === 1 ? '' : 's'}.`
                : `Apply price update to ${pinModal?.issue?.product_name}.`}
            </p>
            <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2">
              Only an admin PIN or TOTP code can authorize price changes from Smart Price Checker. Manager / cashier PINs will be rejected.
            </p>
            <div>
              <label className="text-xs font-medium text-slate-700 mb-1 block">Admin PIN or TOTP</label>
              <Input
                type="password" autoComplete="off" inputMode="numeric"
                value={adminPin} onChange={e => setAdminPin(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') executePinned(); }}
                placeholder="Enter PIN or 6-digit TOTP" className="h-9"
                autoFocus data-testid="smart-price-pin-input"
              />
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" size="sm" onClick={() => { setPinModal(null); setAdminPin(''); }}>
                Cancel
              </Button>
              <Button size="sm" onClick={executePinned} disabled={pinSubmitting || !adminPin}
                className="bg-[#1A4D2E] hover:bg-[#14532d] text-white" data-testid="smart-price-pin-confirm">
                {pinSubmitting ? <RefreshCw size={12} className="animate-spin mr-1.5" /> : <Check size={12} className="mr-1.5" />}
                Authorize &amp; Apply
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
