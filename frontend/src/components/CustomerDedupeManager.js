/**
 * CustomerDedupeManager — Background scanner that surfaces possible duplicate
 * customers for review & merge. Same UX pattern as PriceScanManager:
 *   - Silent background scan on mount + every 5 min + on window focus.
 *   - "Review duplicates" floating button appears when clusters are found.
 *   - Dialog lets user pick a master, merge members, or mark them as different.
 *   - Respects a snooze window (localStorage) like the price scanner.
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import { useAuth, api } from '../contexts/AuthContext';
import { formatPHP, fmtDate } from '../lib/utils';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Badge } from './ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from './ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './ui/table';
import { Users, RefreshCw, Merge, X, ChevronDown, Clock } from 'lucide-react';
import { toast } from 'sonner';

const SCAN_INTERVAL_MS = 5 * 60 * 1000; // 5 minutes
const SKIP_KEY = 'agribooks_dedupe_scan_skip';

const SKIP_OPTIONS = [
  { label: '30 minutes', ms: 30 * 60 * 1000 },
  { label: '1 hour', ms: 60 * 60 * 1000 },
  { label: '4 hours', ms: 4 * 60 * 60 * 1000 },
  { label: 'Until tomorrow', ms: 12 * 60 * 60 * 1000 },
];

export default function CustomerDedupeManager() {
  const { user, currentBranch } = useAuth();
  const [clusters, setClusters] = useState([]);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [skipUntil, setSkipUntil] = useState(null);
  const [showSkipMenu, setShowSkipMenu] = useState(false);

  // Per-cluster working state — master id + canonical name + submitting
  const [clusterState, setClusterState] = useState({}); // { clusterId: { masterId, canonicalName, submitting } }
  const timerRef = useRef(null);
  const skipMenuRef = useRef(null);

  // Skip for super-admin / no-tenant
  const skipForSuperAdmin = !!user && (user.is_super_admin || !user.organization_id);

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

  const inSkipWindow = () => {
    const stored = localStorage.getItem(SKIP_KEY);
    if (!stored) return false;
    const until = parseInt(stored, 10);
    if (until > Date.now()) return true;
    localStorage.removeItem(SKIP_KEY);
    return false;
  };

  const runScan = useCallback(async (silent = true) => {
    if (!user || skipForSuperAdmin) return;
    setScanning(true);
    try {
      const params = {};
      if (currentBranch?.id) params.branch_id = currentBranch.id;
      const res = await api.get('/customers/-/duplicates', { params });
      const found = res.data?.clusters || [];
      setClusters(found);
      if (!silent) {
        toast.success(
          found.length === 0
            ? 'No duplicate customers found.'
            : `Found ${found.length} cluster${found.length === 1 ? '' : 's'} of possible duplicates.`
        );
      }
    } catch (e) {
      if (!silent) toast.error('Failed to scan for duplicates');
    }
    setScanning(false);
  }, [user, skipForSuperAdmin, currentBranch]);

  // Initial scan + polling + focus rescan
  useEffect(() => {
    if (!user || skipForSuperAdmin) return;
    runScan(true);
    const tick = () => { if (!inSkipWindow()) runScan(true); };
    timerRef.current = setInterval(tick, SCAN_INTERVAL_MS);
    window.addEventListener('focus', tick);
    // Expose a manual trigger (ImportPage fires this after a successful import)
    const handleCustomScan = () => runScan(true);
    window.addEventListener('customer-dedupe-rescan', handleCustomScan);
    return () => {
      clearInterval(timerRef.current);
      window.removeEventListener('focus', tick);
      window.removeEventListener('customer-dedupe-rescan', handleCustomScan);
    };
  }, [user, skipForSuperAdmin, currentBranch, runScan]);

  // Initialize cluster working state whenever the cluster list changes
  useEffect(() => {
    const next = {};
    clusters.forEach(c => {
      const existing = clusterState[c.id];
      next[c.id] = existing || {
        masterId: c.members[0]?.id || '',
        canonicalName: '',
        submitting: false,
      };
    });
    setClusterState(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clusters]);

  const applySkip = (ms) => {
    const until = Date.now() + ms;
    localStorage.setItem(SKIP_KEY, String(until));
    setSkipUntil(until);
    setShowSkipMenu(false);
    setDialogOpen(false);
    toast.info(`Snoozed duplicate review for ${Math.round(ms / 60000)} minutes`);
  };

  const clearSkip = () => {
    localStorage.removeItem(SKIP_KEY);
    setSkipUntil(null);
    runScan(true);
  };

  const doMerge = async (cluster) => {
    const state = clusterState[cluster.id] || {};
    const masterId = state.masterId;
    if (!masterId) {
      toast.error('Pick a preferred (master) customer first');
      return;
    }
    const duplicateIds = cluster.members.filter(m => m.id !== masterId).map(m => m.id);
    if (!duplicateIds.length) {
      toast.error('Nothing to merge — only one member selected');
      return;
    }
    setClusterState(s => ({ ...s, [cluster.id]: { ...s[cluster.id], submitting: true } }));
    try {
      const payload = { master_id: masterId, duplicate_ids: duplicateIds };
      if (state.canonicalName && state.canonicalName.trim()) {
        payload.canonical_name = state.canonicalName.trim();
      }
      const res = await api.post('/customers/merge', payload);
      toast.success(
        `Merged ${res.data.duplicates_merged} into master · ${res.data.invoices_moved} invoice${res.data.invoices_moved === 1 ? '' : 's'} moved · Balance ₱${Number(res.data.balance_after).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
      );
      // Remove merged cluster from UI + rescan for fresh clusters in the background
      setClusters(prev => prev.filter(c => c.id !== cluster.id));
      runScan(true);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Merge failed');
    } finally {
      setClusterState(s => ({ ...s, [cluster.id]: { ...s[cluster.id], submitting: false } }));
    }
  };

  const markDistinct = async (cluster) => {
    setClusterState(s => ({ ...s, [cluster.id]: { ...s[cluster.id], submitting: true } }));
    try {
      const ids = cluster.members.map(m => m.id);
      await api.post('/customers/mark-distinct', { customer_ids: ids });
      toast.success('Marked as different customers — we will not flag them again.');
      setClusters(prev => prev.filter(c => c.id !== cluster.id));
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to save decision');
    } finally {
      setClusterState(s => ({ ...s, [cluster.id]: { ...s[cluster.id], submitting: false } }));
    }
  };

  if (!user || skipForSuperAdmin) return null;

  const clusterCount = clusters.length;
  const totalCustomers = clusters.reduce((acc, c) => acc + c.member_count, 0);

  // Floating pill — only when there's something to review
  const showPill = clusterCount > 0;

  return (
    <>
      {showPill && (
        // Bottom-LEFT so it never overlays the cart Checkout button on the
        // right rail of UnifiedSalesPage (it previously sat at bottom-right
        // and intercepted clicks on `[data-testid='checkout-btn']`, blocking
        // the testing agent's E2E flow). Also lifted slightly so it doesn't
        // sit on top of the global sidebar footer.
        <div className="fixed bottom-5 left-72 z-[60]" data-testid="dedupe-pill-wrapper">
          <Button
            data-testid="open-dedupe-dialog-btn"
            onClick={() => setDialogOpen(true)}
            className="bg-amber-500 hover:bg-amber-600 text-white shadow-lg animate-pulse-slow rounded-full h-11 px-4"
          >
            <Users size={15} className="mr-2" />
            {clusterCount} possible duplicate{clusterCount === 1 ? '' : 's'}
            <Badge className="ml-2 bg-white/20 text-white border-0 text-[10px]">
              {totalCustomers} customer{totalCustomers === 1 ? '' : 's'}
            </Badge>
          </Button>
        </div>
      )}

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-w-5xl max-h-[90vh] overflow-hidden flex flex-col" data-testid="dedupe-dialog">
          <DialogHeader className="flex-shrink-0">
            <DialogTitle className="flex items-center gap-2 justify-between" style={{ fontFamily: 'Manrope' }}>
              <span className="flex items-center gap-2">
                <Users size={18} className="text-amber-600" />
                Possible Duplicate Customers
                {clusterCount > 0 && (
                  <Badge className="bg-amber-100 text-amber-700 border-amber-300 ml-2 text-[10px]">
                    {clusterCount} cluster{clusterCount === 1 ? '' : 's'}
                  </Badge>
                )}
              </span>
              <div className="flex items-center gap-2">
                <Button
                  size="sm" variant="outline"
                  onClick={() => runScan(false)} disabled={scanning}
                  data-testid="dedupe-rescan-btn"
                >
                  <RefreshCw size={12} className={`mr-1 ${scanning ? 'animate-spin' : ''}`} />
                  Rescan
                </Button>
                <div className="relative" ref={skipMenuRef}>
                  <Button size="sm" variant="outline" onClick={() => setShowSkipMenu(v => !v)}>
                    <Clock size={12} className="mr-1" /> Snooze <ChevronDown size={10} className="ml-1" />
                  </Button>
                  {showSkipMenu && (
                    <div className="absolute right-0 top-full mt-1 bg-white border border-slate-200 rounded shadow-lg z-50 min-w-[140px]">
                      {SKIP_OPTIONS.map(opt => (
                        <button key={opt.label} onClick={() => applySkip(opt.ms)}
                          className="block w-full text-left px-3 py-1.5 text-xs hover:bg-slate-100">
                          {opt.label}
                        </button>
                      ))}
                      {skipUntil && (
                        <>
                          <div className="border-t border-slate-100" />
                          <button onClick={clearSkip}
                            className="block w-full text-left px-3 py-1.5 text-xs text-emerald-600 hover:bg-slate-100">
                            Clear snooze
                          </button>
                        </>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </DialogTitle>
          </DialogHeader>

          <div className="flex-1 overflow-y-auto mt-2 pr-1 space-y-5" data-testid="dedupe-cluster-list">
            {clusterCount === 0 && (
              <div className="text-center py-16 text-slate-400">
                <Users size={36} className="mx-auto mb-3 opacity-40" />
                <p className="text-sm font-medium">No possible duplicates right now.</p>
                <p className="text-xs mt-1">We'll keep scanning in the background.</p>
              </div>
            )}

            {clusters.map((cluster) => {
              const state = clusterState[cluster.id] || {};
              const masterId = state.masterId || cluster.members[0].id;
              return (
                <div key={cluster.id} className="border border-amber-200 bg-amber-50/30 rounded-lg p-4 space-y-3"
                  data-testid={`dedupe-cluster-${cluster.id}`}>
                  <div className="flex items-center justify-between flex-wrap gap-2">
                    <div>
                      <p className="text-sm font-semibold text-slate-800">
                        {cluster.member_count} similar customer records
                      </p>
                      <p className="text-xs text-slate-500 mt-0.5">
                        Pick the <strong>preferred master</strong>. If it has empty fields, matching data from the duplicates will be copied over.
                      </p>
                    </div>
                  </div>

                  <Table>
                    <TableHeader>
                      <TableRow className="bg-slate-50">
                        <TableHead className="text-xs w-12">Preferred</TableHead>
                        <TableHead className="text-xs">Name</TableHead>
                        <TableHead className="text-xs">Phones</TableHead>
                        <TableHead className="text-xs text-right">Balance</TableHead>
                        <TableHead className="text-xs text-right">Invoices</TableHead>
                        <TableHead className="text-xs">Created</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {cluster.members.map((m) => (
                        <TableRow
                          key={m.id}
                          data-testid={`dedupe-member-${m.id}`}
                          className={masterId === m.id ? 'bg-emerald-50' : ''}
                        >
                          <TableCell>
                            <input
                              type="radio"
                              name={`master-${cluster.id}`}
                              data-testid={`pick-master-${m.id}`}
                              checked={masterId === m.id}
                              onChange={() => setClusterState(s => ({
                                ...s,
                                [cluster.id]: { ...(s[cluster.id] || {}), masterId: m.id },
                              }))}
                              className="w-4 h-4 accent-emerald-600 cursor-pointer"
                            />
                          </TableCell>
                          <TableCell className="text-sm font-medium">
                            {m.name}
                            {masterId === m.id && (
                              <Badge className="ml-2 bg-emerald-100 text-emerald-700 border-0 text-[10px]">
                                master
                              </Badge>
                            )}
                          </TableCell>
                          <TableCell className="text-xs text-slate-500">
                            {(m.phones?.length ? m.phones : (m.phone ? [m.phone] : [])).join(', ') || '—'}
                          </TableCell>
                          <TableCell className="text-right text-sm">
                            <span className={m.open_balance > 0 ? 'text-red-600 font-semibold' : 'text-slate-500'}>
                              {formatPHP(m.open_balance)}
                            </span>
                          </TableCell>
                          <TableCell className="text-right text-sm text-slate-600">
                            {m.invoice_count}
                          </TableCell>
                          <TableCell className="text-xs text-slate-400">
                            {fmtDate(m.created_at) || '—'}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>

                  <div className="flex items-center gap-2 flex-wrap pt-2 border-t border-amber-200">
                    <div className="flex items-center gap-2 flex-1 min-w-[240px]">
                      <label className="text-xs text-slate-600 whitespace-nowrap">
                        Canonical name (optional):
                      </label>
                      <Input
                        data-testid={`canonical-name-${cluster.id}`}
                        value={state.canonicalName || ''}
                        onChange={(e) => setClusterState(s => ({
                          ...s,
                          [cluster.id]: { ...(s[cluster.id] || {}), canonicalName: e.target.value },
                        }))}
                        placeholder="Leave blank to keep master's name"
                        className="h-8 text-xs flex-1"
                      />
                    </div>
                    <Button
                      size="sm"
                      onClick={() => markDistinct(cluster)}
                      disabled={state.submitting}
                      variant="outline"
                      className="text-xs h-8"
                      data-testid={`mark-distinct-${cluster.id}`}
                    >
                      <X size={12} className="mr-1" /> Different customers
                    </Button>
                    <Button
                      size="sm"
                      onClick={() => doMerge(cluster)}
                      disabled={state.submitting || !masterId}
                      className="bg-emerald-600 hover:bg-emerald-700 text-white text-xs h-8"
                      data-testid={`merge-cluster-${cluster.id}`}
                    >
                      <Merge size={12} className="mr-1" />
                      {state.submitting ? 'Merging…' : `Merge ${cluster.member_count - 1} into master`}
                    </Button>
                  </div>
                </div>
              );
            })}
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
