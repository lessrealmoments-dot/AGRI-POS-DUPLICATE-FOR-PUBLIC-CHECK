/**
 * OfflineIndicator
 * Shows download progress + "Ready for offline" status in the sidebar.
 *
 * States:
 *  never-synced  → estimate + "Download offline data" button (auto-triggers on first load)
 *  syncing       → animated progress bar with step name + percentage
 *  ready         → green "Ready for offline" + last-synced time + counts
 *  stale (>4h)   → amber "Update recommended"
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import { useAuth, api } from '../contexts/AuthContext';
import { Progress } from './ui/progress';
import { getMeta, getPendingSaleCount } from '../lib/offlineDB';
import { refreshPOSCache, syncPendingSales, onSyncUpdate } from '../lib/syncManager';
import {
  Wifi, WifiOff, RefreshCw, CheckCircle, AlertTriangle,
  CloudDownload, CloudOff,
} from 'lucide-react';
import { formatDate } from '../lib/dateFormat';
import { toast } from 'sonner';

const STALE_HOURS = 4;

function timeAgo(isoStr) {
  if (!isoStr) return null;
  const diff = Date.now() - new Date(isoStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ${mins % 60}m ago`;
  return formatDate(isoStr);
}

function fmtSize(kb) {
  if (kb < 1024) return `~${kb} KB`;
  return `~${(kb / 1024).toFixed(1)} MB`;
}

export default function OfflineIndicator() {
  const { effectiveBranchId, currentBranch } = useAuth();

  const [isOnline, setIsOnline] = useState(navigator.onLine);
  const [pendingCount, setPendingCount] = useState(0);

  const [syncing, setSyncing] = useState(false);
  const [syncPct, setSyncPct] = useState(0);
  const [stepLabel, setStepLabel] = useState('');

  const [lastSync, setLastSync] = useState(null);
  const [syncCounts, setSyncCounts] = useState(null);
  const [estimate, setEstimate] = useState(null);

  const prevBranchRef = useRef(effectiveBranchId);
  const [installPrompt, setInstallPrompt] = useState(null);

  const isStale = lastSync
    ? (Date.now() - new Date(lastSync).getTime()) > STALE_HOURS * 3600 * 1000
    : false;
  const isReady = !!lastSync && !isStale && !syncing;
  const isNeverSynced = !lastSync && !syncing;
  const canSync = isOnline && effectiveBranchId && effectiveBranchId !== 'all';

  const loadCacheInfo = useCallback(async () => {
    const ts = await getMeta('last_sync');
    const counts = await getMeta('last_sync_counts');
    setLastSync(ts);
    setSyncCounts(counts);
    const pending = await getPendingSaleCount();
    setPendingCount(pending);
  }, []);

  const fetchEstimate = useCallback(async (branchId) => {
    if (!navigator.onLine || !branchId || branchId === 'all') return;
    try {
      const res = await api.get('/sync/estimate', { params: { branch_id: branchId } });
      setEstimate(res.data);
    } catch { /* silent */ }
  }, []);

  const startSync = useCallback(async () => {
    if (syncing || !navigator.onLine) return;
    setSyncing(true);
    setSyncPct(5);
    setStepLabel('Starting...');
    const branchId = effectiveBranchId !== 'all' ? effectiveBranchId : null;
    const ok = await refreshPOSCache(branchId);
    if (!ok) toast.error('Data download failed. Check your connection.');
    setSyncing(false);
    setSyncPct(0);
    setEstimate(null);
    await loadCacheInfo();
  }, [syncing, effectiveBranchId, loadCacheInfo]);

  // Initial load + auto-sync when never synced
  useEffect(() => {
    loadCacheInfo().then(async () => {
      const ts = await getMeta('last_sync');
      const branchId = effectiveBranchId !== 'all' ? effectiveBranchId : null;
      if (!ts && navigator.onLine && branchId) {
        fetchEstimate(branchId);
        setTimeout(startSync, 1800);
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Re-sync + re-estimate when branch changes
  useEffect(() => {
    if (prevBranchRef.current !== effectiveBranchId) {
      prevBranchRef.current = effectiveBranchId;
      if (navigator.onLine && effectiveBranchId && effectiveBranchId !== 'all') {
        fetchEstimate(effectiveBranchId);
        startSync();
      }
    }
  }, [effectiveBranchId, startSync, fetchEstimate]);

  // Fetch estimate when entering never-synced state
  useEffect(() => {
    if (isNeverSynced && canSync && !estimate && !syncing) {
      fetchEstimate(effectiveBranchId);
    }
  }, [isNeverSynced, canSync, estimate, syncing, effectiveBranchId, fetchEstimate]);

  // Sync progress events
  useEffect(() => {
    const unsub = onSyncUpdate((data) => {
      if (data.type === 'sync_step') {
        setSyncPct(data.pct);
        setStepLabel(data.stepLabel);
      }
      if (data.type === 'cache_refreshed') {
        setSyncPct(100);
        setStepLabel('Done!');
        loadCacheInfo();
      }
      if (data.type === 'sync_error') {
        setSyncing(false);
        setSyncPct(0);
      }
      if (data.type === 'sync_start') {
        setStepLabel(`Syncing ${data.count} sale${data.count !== 1 ? 's' : ''}…`);
      }
      if (data.type === 'sync_progress') {
        setStepLabel(`Syncing sale ${data.current}/${data.total}…`);
        setSyncPct(Math.round((data.current / data.total) * 100));
      }
      if (data.type === 'sync_complete') {
        loadCacheInfo();
        if (data.synced > 0) {
          setStepLabel(`${data.synced} sale${data.synced !== 1 ? 's' : ''} synced`);
        }
        if (data.networkError) {
          setStepLabel('Paused — network unstable');
        }
      }
      if (data.type === 'sync_paused') {
        setStepLabel('Sync paused — network unstable');
        setSyncing(false);
      }
      // Background refresh events (silent — just update cache info)
      if (data.type === 'background_refresh') {
        if (data.status === 'complete') loadCacheInfo();
      }
    });
    return unsub;
  }, [loadCacheInfo]);

  // Online/offline + PWA install events
  useEffect(() => {
    const goOnline = () => setIsOnline(true);
    const goOffline = () => setIsOnline(false);
    window.addEventListener('online', goOnline);
    window.addEventListener('offline', goOffline);

    const handleInstall = (e) => { e.preventDefault(); setInstallPrompt(e); };
    window.addEventListener('beforeinstallprompt', handleInstall);

    const interval = setInterval(() => getPendingSaleCount().then(setPendingCount), 8000);

    return () => {
      window.removeEventListener('online', goOnline);
      window.removeEventListener('offline', goOffline);
      window.removeEventListener('beforeinstallprompt', handleInstall);
      clearInterval(interval);
    };
  }, []);

  const handleManualSync = async () => {
    if (!navigator.onLine) {
      toast.error('Cannot sync — device is offline');
      return;
    }
    if (pendingCount > 0) {
      try {
        const result = await syncPendingSales();
        await loadCacheInfo();
        if (result && result.synced > 0) {
          toast.success(`${result.synced} sale(s) synced`);
        } else if (result && result.networkError) {
          toast.error('Network unstable — will retry automatically');
        } else {
          toast.success('Queue cleared');
        }
      } catch {
        toast.error('Sync failed');
      }
    }
  };

  const handleInstall = async () => {
    if (!installPrompt) return;
    installPrompt.prompt();
    const { outcome } = await installPrompt.userChoice;
    if (outcome === 'accepted') toast.success('AgriBooks installed!');
    setInstallPrompt(null);
  };

  return (
    <div className="px-3 py-2 space-y-2">

      {/* Online / Offline badge */}
      <div className={`flex items-center gap-2 px-3 py-1.5 rounded-md text-xs font-medium ${
        isOnline ? 'bg-emerald-500/10 text-emerald-400' : 'bg-red-500/10 text-red-400'
      }`}>
        {isOnline ? <Wifi size={13} /> : <WifiOff size={13} />}
        <span>{isOnline ? 'Online' : 'Offline'}</span>
        {!isOnline && <CloudOff size={11} className="ml-auto opacity-60" />}
      </div>

      {/* Sync / Download section */}
      {(canSync || (!isOnline && lastSync)) && (
        <div className={`rounded-md px-3 py-2 text-xs border ${
          syncing ? 'bg-blue-500/10 border-blue-500/20'
          : isReady ? 'bg-emerald-500/10 border-emerald-500/20'
          : isStale ? 'bg-amber-500/10 border-amber-500/20'
          : 'bg-slate-700/50 border-white/10'
        }`}>

          {/* SYNCING */}
          {syncing && (
            <div className="space-y-1.5">
              <div className="flex items-center gap-2 text-blue-300">
                <RefreshCw size={12} className="animate-spin shrink-0" />
                <span className="truncate">{stepLabel || 'Downloading...'}</span>
              </div>
              <Progress value={syncPct} className="h-1.5 bg-blue-500/20" />
              <div className="text-[10px] text-blue-400/70 text-right">{syncPct}%</div>
            </div>
          )}

          {/* READY */}
          {isReady && !syncing && (
            <div className="space-y-1">
              <div className="flex items-center gap-2 text-emerald-400 font-semibold">
                <CheckCircle size={13} />
                <span>Ready for offline</span>
              </div>
              <div className="text-[10px] text-slate-400">
                Synced {timeAgo(lastSync)}{currentBranch && ` · ${currentBranch.name}`}
              </div>
              {syncCounts && (
                <div className="text-[10px] text-slate-500">
                  {syncCounts.products} products · {syncCounts.customers} customers
                  {syncCounts.inventory > 0 && ` · ${syncCounts.inventory} stock records`}
                </div>
              )}
              <div className="text-[10px] text-slate-600 flex items-center gap-1">
                <RefreshCw size={8} className="text-slate-500" />
                Auto-refreshes every 5 min
              </div>
              <button onClick={startSync} disabled={!isOnline}
                className="mt-1 text-[10px] text-slate-500 hover:text-slate-300 underline underline-offset-2 transition-colors disabled:opacity-30">
                Re-sync now
              </button>
            </div>
          )}

          {/* STALE */}
          {isStale && !syncing && (
            <div className="space-y-1.5">
              <div className="flex items-center gap-2 text-amber-400">
                <AlertTriangle size={12} />
                <span className="font-medium">Update recommended</span>
              </div>
              <div className="text-[10px] text-slate-400">Last synced {timeAgo(lastSync)}</div>
              <button onClick={startSync} disabled={!isOnline}
                className="w-full mt-1 py-1 rounded bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 text-[11px] font-medium transition-colors disabled:opacity-30">
                Sync Now
              </button>
            </div>
          )}

          {/* NEVER SYNCED */}
          {isNeverSynced && !syncing && (
            <div className="space-y-1.5">
              <div className="flex items-center gap-2 text-slate-300">
                <CloudDownload size={13} />
                <span className="font-medium">Offline data: Not ready</span>
              </div>
              {estimate ? (
                <div className="text-[10px] text-slate-400 leading-relaxed">
                  <span className="text-slate-300 font-medium">{fmtSize(estimate.estimated_kb)}</span>
                  {' · '}{estimate.products} products
                  {' · '}{estimate.customers} customers
                  {estimate.inventory > 0 && ` · ${estimate.inventory} stock records`}
                </div>
              ) : (
                <div className="text-[10px] text-slate-500">
                  Download data for {currentBranch?.name || 'this branch'} to work offline
                </div>
              )}
              <button onClick={startSync} disabled={!isOnline}
                data-testid="download-offline-btn"
                className="w-full mt-1 py-1.5 rounded bg-white/10 hover:bg-white/15 text-white text-[11px] font-medium flex items-center justify-center gap-1.5 transition-colors disabled:opacity-30">
                <CloudDownload size={11} />
                Download offline data
              </button>
            </div>
          )}
        </div>
      )}

      {effectiveBranchId === 'all' && isOnline && (
        <div className="px-3 py-1.5 rounded-md bg-slate-700/30 border border-white/5 text-[10px] text-slate-500">
          Select a branch to enable offline mode
        </div>
      )}

      {/* Pending sales — enhanced sync progress */}
      {pendingCount > 0 && (
        <div className="rounded-md bg-amber-500/10 border border-amber-500/20 overflow-hidden">
          <div className="flex items-center justify-between px-3 py-2">
            <div className="flex items-center gap-2 text-xs text-amber-400">
              <RefreshCw size={11} className={syncing ? 'animate-spin' : ''} />
              <span className="font-semibold">{pendingCount} pending sale{pendingCount > 1 ? 's' : ''}</span>
            </div>
            {!isOnline && (
              <span className="text-[9px] text-amber-500 bg-amber-500/20 px-1.5 py-0.5 rounded">waiting for internet</span>
            )}
          </div>
          {/* Sync progress detail */}
          {syncing && stepLabel && stepLabel.includes('sale') && (
            <div className="px-3 pb-2">
              <div className="text-[10px] text-amber-300 mb-1">{stepLabel}</div>
              <Progress value={syncPct} className="h-1 bg-amber-500/20" />
            </div>
          )}
          {/* Sync button */}
          {isOnline && !syncing && (
            <button data-testid="manual-sync-btn" onClick={handleManualSync}
              className="w-full py-1.5 bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 text-[11px] font-medium transition-colors flex items-center justify-center gap-1.5 border-t border-amber-500/20">
              <RefreshCw size={10} />
              Sync Now
            </button>
          )}
          {!isOnline && (
            <div className="px-3 pb-2 text-[9px] text-amber-500/70">
              Sales are safely stored. They will auto-sync when connection returns.
            </div>
          )}
        </div>
      )}

      {/* Install PWA */}
      {installPrompt && (
        <button data-testid="install-pwa-btn" onClick={handleInstall}
          className="w-full flex items-center justify-center gap-2 py-1.5 rounded-md border border-white/10 text-slate-300 hover:text-white hover:bg-white/5 text-xs transition-colors">
          <CloudDownload size={12} /> Install App
        </button>
      )}
    </div>
  );
}
