/**
 * PrintQueuePage — Remote Branch Printing Terminal Management
 *
 * Admin view for:
 *   - Monitoring all registered print terminals (online/offline status)
 *   - Viewing and managing the print job queue (last 15 days)
 *   - Toggling auto/manual print mode per terminal
 *   - Cancelling or resending jobs
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '../contexts/AuthContext';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { Card, CardContent } from '../components/ui/card';
import { Separator } from '../components/ui/separator';
import {
  Printer, Wifi, WifiOff, Clock, CheckCircle2, XCircle, AlertTriangle,
  RefreshCw, Send, Ban, Building2, User, Calendar, ChevronDown, ChevronRight,
  Loader2, RotateCcw, History, Settings2, Info, CloudUpload, FileText, Image,
  Unlink, Trash2,
} from 'lucide-react';
import { toast } from 'sonner';
import { formatDistanceToNow } from 'date-fns';
import UploadPrintJobModal from '../components/UploadPrintJobModal';

const STATUS_CONFIG = {
  pending:   { label: 'Pending',   color: 'bg-amber-100 text-amber-700',   icon: Clock },
  sent:      { label: 'Sent',      color: 'bg-blue-100 text-blue-700',     icon: Send },
  printed:   { label: 'Printed',   color: 'bg-emerald-100 text-emerald-700', icon: CheckCircle2 },
  failed:    { label: 'Failed',    color: 'bg-red-100 text-red-700',       icon: XCircle },
  cancelled: { label: 'Cancelled', color: 'bg-slate-100 text-slate-500',   icon: Ban },
};

const DOC_TYPE_LABELS = {
  sales_receipt:    'Sales Receipt',
  purchase_order:   'Purchase Order',
  z_report:         'Z-Report',
  advance_z_report: 'Advance Z-Report',
  branch_transfer:  'Branch Transfer',
  expense_receipt:  'Expense Receipt',
  return_receipt:   'Return/Refund Receipt',
  statement:        'Customer Statement',
};

function TimeAgo({ isoDate }) {
  if (!isoDate) return <span className="text-slate-400">—</span>;
  try {
    return <span className="text-slate-500 text-xs">{formatDistanceToNow(new Date(isoDate), { addSuffix: true })}</span>;
  } catch {
    return <span className="text-slate-400 text-xs">{isoDate.slice(0, 16)}</span>;
  }
}

function StatusBadge({ status }) {
  const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.pending;
  const Icon = cfg.icon;
  return (
    <span className={`inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-full ${cfg.color}`}>
      <Icon size={10} />
      {cfg.label}
    </span>
  );
}

function TerminalCard({ terminal, onModeChange, onRefresh }) {
  const [changing, setChanging] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);

  const toggleMode = async () => {
    const newMode = terminal.print_mode === 'auto' ? 'manual' : 'auto';
    setChanging(true);
    try {
      await api.post('/print/terminal/set-mode', {
        terminal_id: terminal.terminal_id,
        mode: newMode,
      });
      toast.success(`Print mode changed to ${newMode}`);
      onRefresh();
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to change mode');
    }
    setChanging(false);
  };

  const handleDisconnect = async () => {
    if (!window.confirm(`Remove terminal "${terminal.user_name || terminal.terminal_id.slice(0,8)}"? This only removes the session record — the EXE will create a new one on next login.`)) return;
    setDisconnecting(true);
    try {
      await api.post(`/terminal/disconnect/${terminal.terminal_id}`);
      toast.success('Terminal removed');
      onRefresh();
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to remove terminal');
    }
    setDisconnecting(false);
  };

  return (
    <Card className={`border transition-all ${terminal.is_online ? 'border-emerald-200 bg-emerald-50/30' : 'border-slate-200 bg-white'}`} data-testid={`terminal-card-${terminal.terminal_id}`}>
      <CardContent className="p-4">
        <div className="flex items-start gap-3">
          {/* Status dot + icon */}
          <div className="relative shrink-0">
            <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${terminal.is_online ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-400'}`}>
              <Printer size={18} />
            </div>
            <span className={`absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full border-2 border-white ${terminal.is_online ? 'bg-emerald-500' : 'bg-slate-300'}`} />
          </div>

          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <p className="text-sm font-bold text-slate-900 truncate">
                {terminal.user_name || terminal.code || 'Print Terminal'}
              </p>
              <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded-full ${terminal.is_online ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-500'}`}>
                {terminal.is_online ? 'Online' : 'Offline'}
              </span>
            </div>

            <div className="flex items-center gap-3 mt-1 text-xs text-slate-500 flex-wrap">
              <span className="flex items-center gap-1"><Building2 size={10} />{terminal.branch_name || '—'}</span>
              <span className="flex items-center gap-1"><User size={10} />{terminal.user_name || '—'}</span>
            </div>

            <div className="flex items-center gap-2 mt-2 flex-wrap">
              {terminal.pending_jobs > 0 && (
                <span className="text-[10px] bg-amber-100 text-amber-700 px-2 py-0.5 rounded-full font-medium">
                  {terminal.pending_jobs} pending
                </span>
              )}
              <span className="text-[10px] text-slate-400">
                Last seen: <TimeAgo isoDate={terminal.last_seen} />
              </span>
            </div>
          </div>

          {/* Mode toggle */}
          <div className="flex flex-col items-end gap-2 shrink-0">
            <button
              onClick={toggleMode}
              disabled={changing}
              data-testid={`toggle-mode-${terminal.terminal_id}`}
              className={`text-[10px] px-2.5 py-1.5 rounded-lg border font-semibold transition-all flex items-center gap-1 ${
                terminal.print_mode === 'auto'
                  ? 'bg-emerald-600 text-white border-emerald-700 hover:bg-emerald-700'
                  : 'bg-white text-slate-600 border-slate-300 hover:bg-slate-50'
              }`}
            >
              {changing ? <Loader2 size={10} className="animate-spin" /> : <Settings2 size={10} />}
              {terminal.print_mode === 'auto' ? 'Auto Print' : 'Manual'}
            </button>
            <span className="text-[9px] text-slate-400">via {terminal.paired_via || 'code'}</span>
            <button
              onClick={handleDisconnect}
              disabled={disconnecting}
              className="text-[10px] text-red-400 hover:text-red-600 flex items-center gap-0.5 transition-colors"
              title="Remove this terminal session"
              data-testid={`disconnect-terminal-${terminal.terminal_id}`}
            >
              {disconnecting ? <Loader2 size={9} className="animate-spin" /> : <Unlink size={9} />}
              Remove
            </button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function JobRow({ job, onResend, onCancel }) {
  const [expanded, setExpanded] = useState(false);
  const [acting, setActing] = useState(false);

  const handleResend = async () => {
    setActing(true);
    try {
      const res = await onResend(job.id);
      const msg = res.status === 'sent' ? 'Job resent to terminal' : 'Job re-queued (terminal offline)';
      toast.success(msg);
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to resend job');
    }
    setActing(false);
  };

  const handleCancel = async () => {
    setActing(true);
    try {
      await onCancel(job.id);
      toast.success('Job cancelled');
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to cancel job');
    }
    setActing(false);
  };

  return (
    <>
      <tr className="border-b border-slate-50 hover:bg-slate-50/50 transition-colors" data-testid={`job-row-${job.id}`}>
        <td className="py-2.5 px-3">
          <button onClick={() => setExpanded(v => !v)} className="flex items-center gap-1 text-slate-400 hover:text-slate-600">
            {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          </button>
        </td>
        <td className="py-2.5 px-2">
          <div className="flex items-center gap-1.5">
            {job.source_type === 'external'
              ? <FileText size={11} className="text-blue-500 shrink-0" title="External document" />
              : <Printer size={11} className="text-slate-400 shrink-0" title="AgriBooks document" />
            }
            <div>
              <p className="text-xs font-semibold text-slate-800 truncate max-w-[180px]">{job.document_name}</p>
              <p className="text-[10px] text-slate-400">
                {DOC_TYPE_LABELS[job.document_type] || job.document_type}
                {job.source_type === 'external' && (
                  <span className="ml-1 text-blue-500 font-medium">· External</span>
                )}
              </p>
            </div>
          </div>
        </td>
        <td className="py-2.5 px-2 text-xs text-slate-600 truncate max-w-[100px]">
          {job.terminal_name || '—'}
        </td>
        <td className="py-2.5 px-2">
          <StatusBadge status={job.status} />
        </td>
        <td className="py-2.5 px-2">
          <TimeAgo isoDate={job.created_at} />
        </td>
        <td className="py-2.5 px-2">
          <div className="flex items-center gap-1">
            {['failed', 'cancelled', 'pending'].includes(job.status) && (
              <button
                onClick={handleResend}
                disabled={acting}
                className="p-1 rounded hover:bg-emerald-50 text-emerald-600 transition-colors"
                title="Resend"
                data-testid={`resend-job-${job.id}`}
              >
                {acting ? <Loader2 size={12} className="animate-spin" /> : <RotateCcw size={12} />}
              </button>
            )}
            {['pending', 'sent'].includes(job.status) && (
              <button
                onClick={handleCancel}
                disabled={acting}
                className="p-1 rounded hover:bg-red-50 text-red-500 transition-colors"
                title="Cancel"
                data-testid={`cancel-job-${job.id}`}
              >
                <Ban size={12} />
              </button>
            )}
          </div>
        </td>
      </tr>
      {expanded && (
        <tr className="bg-slate-50/60">
          <td colSpan={6} className="px-4 py-3">
            <div className="grid grid-cols-2 gap-3 text-xs text-slate-600">
              <div><span className="font-medium text-slate-400">Branch: </span>{job.branch_name || '—'}</div>
              <div><span className="font-medium text-slate-400">Created by: </span>{job.created_by_name || '—'}</div>
              <div><span className="font-medium text-slate-400">Sent at: </span>{job.sent_at ? new Date(job.sent_at).toLocaleString() : '—'}</div>
              <div><span className="font-medium text-slate-400">Printed at: </span>{job.printed_at ? new Date(job.printed_at).toLocaleString() : '—'}</div>
              {job.source_type === 'external' && job.file_name && (
                <div className="col-span-2 flex items-center gap-1.5 text-blue-700">
                  <FileText size={11} />
                  <span className="font-medium text-slate-400">File: </span>{job.file_name}
                  {job.description && <span className="ml-2 text-slate-500 italic">— {job.description}</span>}
                </div>
              )}
              {job.error_message && (
                <div className="col-span-2 text-red-600">
                  <span className="font-medium">Error: </span>{job.error_message}
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

export default function PrintQueuePage() {
  const [terminals, setTerminals]     = useState([]);
  const [jobs, setJobs]               = useState([]);
  const [loadingT, setLoadingT]       = useState(true);
  const [loadingJ, setLoadingJ]       = useState(true);
  const [filterStatus, setFilterStatus] = useState('all');
  const [filterTerminal, setFilterTerminal] = useState('all');
  const [tab, setTab]                 = useState('queue'); // queue | history | terminals
  const [uploadOpen, setUploadOpen]   = useState(false);
  const pollRef = useRef(null);

  const fetchTerminals = useCallback(async () => {
    try {
      const res = await api.get('/print/terminals');
      setTerminals(res.data || []);
    } catch { /* silent */ }
    setLoadingT(false);
  }, []);

  const fetchJobs = useCallback(async () => {
    try {
      const params = {};
      if (filterTerminal !== 'all') params.terminal_id = filterTerminal;
      if (filterStatus !== 'all')   params.status      = filterStatus;
      const res = await api.get('/print/jobs', { params });
      setJobs(res.data || []);
    } catch { /* silent */ }
    setLoadingJ(false);
  }, [filterTerminal, filterStatus]);

  // Initial load + poll
  useEffect(() => {
    fetchTerminals();
    fetchJobs();
    pollRef.current = setInterval(() => {
      fetchTerminals();
      fetchJobs();
    }, 30000);
    return () => clearInterval(pollRef.current);
  }, [fetchTerminals, fetchJobs]);

  const handleRefresh = () => {
    setLoadingT(true);
    setLoadingJ(true);
    fetchTerminals();
    fetchJobs();
  };

  const handleResend = async (jobId) => {
    const res = await api.post(`/print/jobs/${jobId}/resend`);
    fetchJobs();
    return res.data;
  };

  const handleCancel = async (jobId) => {
    await api.put(`/print/jobs/${jobId}/status`, { status: 'cancelled' });
    fetchJobs();
  };

  // Split jobs into queue (active) and history (done)
  const activeJobs = jobs.filter(j => ['pending', 'sent'].includes(j.status));
  const historyJobs = jobs.filter(j => ['printed', 'failed', 'cancelled'].includes(j.status));

  const displayJobs = tab === 'queue' ? activeJobs : historyJobs;
  const filteredJobs = displayJobs.filter(j => {
    if (filterStatus !== 'all' && j.status !== filterStatus) return false;
    if (filterTerminal !== 'all' && j.terminal_id !== filterTerminal) return false;
    return true;
  });

  const onlineCount = terminals.filter(t => t.is_online).length;

  return (
    <div className="p-4 sm:p-6 space-y-5 max-w-6xl mx-auto" data-testid="print-queue-page">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-bold text-slate-900 flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
            <Printer size={20} className="text-emerald-600" />
            Print Center
          </h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Remote Branch Printing Terminal — {onlineCount} of {terminals.length} terminals online
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            className="bg-emerald-700 hover:bg-emerald-800 text-white flex items-center gap-1.5"
            onClick={() => setUploadOpen(true)}
            data-testid="upload-print-job-btn"
          >
            <CloudUpload size={13} />
            Upload Document
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={async () => {
              try {
                const res = await api.post('/terminal/cleanup-duplicates');
                toast.success(res.data.message);
                fetchTerminals();
              } catch { toast.error('Cleanup failed'); }
            }}
            className="flex items-center gap-1.5 text-amber-600 border-amber-200 hover:bg-amber-50"
            data-testid="cleanup-duplicates-btn"
            title="Remove duplicate terminal sessions (same user + branch)"
          >
            <Trash2 size={13} />
            Clean Duplicates
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={handleRefresh}
            className="flex items-center gap-1.5"
            data-testid="refresh-print-center"
          >
            <RefreshCw size={13} />
            Refresh
          </Button>
        </div>
      </div>

      {/* Terminals Grid */}
      <div>
        <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-3">Print Terminals</p>
        {loadingT ? (
          <div className="flex items-center justify-center py-8 text-slate-400">
            <Loader2 size={18} className="animate-spin mr-2" /> Loading terminals...
          </div>
        ) : terminals.length === 0 ? (
          <div className="flex items-center gap-3 p-4 bg-slate-50 rounded-xl border border-dashed border-slate-300 text-slate-500 text-sm">
            <Info size={16} className="text-slate-400 shrink-0" />
            <div>
              <p className="font-semibold">No print terminals registered yet</p>
              <p className="text-xs text-slate-400 mt-0.5">
                Install the AgriBooks Print Terminal app on a branch computer,
                log in with your credentials, and select a branch to link it.
              </p>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {terminals.map(t => (
              <TerminalCard
                key={t.terminal_id}
                terminal={t}
                onModeChange={fetchTerminals}
                onRefresh={fetchTerminals}
              />
            ))}
          </div>
        )}
      </div>

      <Separator />

      {/* Jobs section */}
      <div>
        {/* Tab switcher */}
        <div className="flex items-center justify-between flex-wrap gap-3 mb-4">
          <div className="flex gap-1 bg-slate-100 rounded-lg p-1">
            {[
              { key: 'queue',   label: 'Active Queue', icon: Clock,   count: activeJobs.length },
              { key: 'history', label: 'History',      icon: History, count: historyJobs.length },
            ].map(t => (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold transition-all ${
                  tab === t.key ? 'bg-white shadow-sm text-slate-800' : 'text-slate-500 hover:text-slate-700'
                }`}
                data-testid={`print-tab-${t.key}`}
              >
                <t.icon size={12} />
                {t.label}
                {t.count > 0 && (
                  <span className={`px-1.5 rounded-full text-[10px] ${tab === t.key ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-200 text-slate-500'}`}>
                    {t.count}
                  </span>
                )}
              </button>
            ))}
          </div>

          {/* Filters */}
          <div className="flex items-center gap-2">
            <Select value={filterTerminal} onValueChange={setFilterTerminal}>
              <SelectTrigger className="h-8 text-xs w-40" data-testid="filter-terminal">
                <SelectValue placeholder="All terminals" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All terminals</SelectItem>
                {terminals.map(t => (
                  <SelectItem key={t.terminal_id} value={t.terminal_id}>
                    {t.user_name || t.branch_name || t.terminal_id.slice(0, 8)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={filterStatus} onValueChange={setFilterStatus}>
              <SelectTrigger className="h-8 text-xs w-32" data-testid="filter-status">
                <SelectValue placeholder="All status" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All status</SelectItem>
                {Object.entries(STATUS_CONFIG).map(([k, v]) => (
                  <SelectItem key={k} value={k}>{v.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        {loadingJ ? (
          <div className="flex items-center justify-center py-8 text-slate-400">
            <Loader2 size={18} className="animate-spin mr-2" /> Loading jobs...
          </div>
        ) : filteredJobs.length === 0 ? (
          <div className="text-center py-12 text-slate-400">
            {tab === 'queue'
              ? <><Clock size={28} className="mx-auto mb-2 opacity-40" /><p className="text-sm">No active print jobs</p><p className="text-xs mt-1">Print jobs sent to terminals will appear here</p></>
              : <><History size={28} className="mx-auto mb-2 opacity-40" /><p className="text-sm">No print history</p><p className="text-xs mt-1">Last 15 days of print activity</p></>
            }
          </div>
        ) : (
          <div className="rounded-xl border border-slate-200 overflow-hidden">
            <table className="w-full text-sm" data-testid="jobs-table">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  <th className="py-2 px-3 text-[10px] font-semibold text-slate-400 uppercase text-left w-8"></th>
                  <th className="py-2 px-2 text-[10px] font-semibold text-slate-400 uppercase text-left">Document</th>
                  <th className="py-2 px-2 text-[10px] font-semibold text-slate-400 uppercase text-left">Terminal</th>
                  <th className="py-2 px-2 text-[10px] font-semibold text-slate-400 uppercase text-left">Status</th>
                  <th className="py-2 px-2 text-[10px] font-semibold text-slate-400 uppercase text-left">Created</th>
                  <th className="py-2 px-2 text-[10px] font-semibold text-slate-400 uppercase text-left w-16">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredJobs.map(job => (
                  <JobRow
                    key={job.id}
                    job={job}
                    onResend={handleResend}
                    onCancel={handleCancel}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Info footer */}
      <div className="bg-blue-50 border border-blue-100 rounded-xl p-3 text-xs text-blue-700 flex items-start gap-2">
        <Info size={14} className="mt-0.5 shrink-0" />
        <div>
          <p className="font-semibold mb-0.5">How Remote Printing Works</p>
          <p>Print jobs are pushed instantly to online terminals via WebSocket.
          Offline terminals receive their jobs automatically when they reconnect.
          Jobs older than 15 days are purged from history. Terminals inactive for 30 days are auto-purged (staff can log in again).</p>
        </div>
      </div>

      <UploadPrintJobModal
        open={uploadOpen}
        onOpenChange={(v) => {
          setUploadOpen(v);
          if (!v) { fetchTerminals(); fetchJobs(); } // refresh after closing
        }}
      />
    </div>
  );
}
