import { useState, useEffect, useCallback, useRef } from 'react';
import { api, useAuth } from '../contexts/AuthContext';
import { useNavigate } from 'react-router-dom';
import { toast } from 'sonner';
import { formatDate, formatDateTime, localTodayStr } from '../lib/dateFormat';
import {
  Building2, Users, BarChart3, Shield, RefreshCw, ArrowLeft,
  CheckCircle, AlertTriangle, XCircle, ChevronDown, ChevronUp,
  Search, TrendingUp, Edit3, Save, X, Plus, Minus, GitBranch,
  CreditCard, Upload, Globe, Phone, Settings,
  Clock, Layers, Trash2, Star, HardDrive, Eye
} from 'lucide-react';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Badge } from '../components/ui/badge';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '../components/ui/tabs';
import CalcInput from '../components/CalcInput';

/* ── helpers ──────────────────────────────────────────────────────────────── */
const PLAN_COLORS = {
  trial:       'bg-blue-500/15 text-blue-300 border-blue-500/30',
  basic:       'bg-slate-500/15 text-slate-300 border-slate-500/30',
  standard:    'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
  pro:         'bg-indigo-500/15 text-indigo-300 border-indigo-500/30',
  founders:    'bg-amber-400/20 text-amber-300 border-amber-400/40',
  suspended:   'bg-red-500/15 text-red-300 border-red-500/30',
  grace_period:'bg-amber-500/15 text-amber-300 border-amber-500/30',
  expired:     'bg-red-500/15 text-red-400 border-red-500/30',
};

const STATUS_DOT = {
  active:       'bg-emerald-400',
  trial:        'bg-blue-400',
  grace_period: 'bg-amber-400',
  expired:      'bg-red-400',
  suspended:    'bg-red-600',
  founders:     'bg-amber-400',
};

function BranchGauge({ used, max }) {
  const pct = max === 0 ? 0 : Math.min((used / max) * 100, 100);
  const color = pct >= 100 ? '#ef4444' : pct >= 80 ? '#f59e0b' : '#10b981';
  const dots = max === 0 ? 10 : Math.min(max + (used > max ? used - max : 0), 10);
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-1.5">
        {Array.from({ length: dots }).map((_, i) => (
          <div key={i} className="w-2.5 h-2.5 rounded-full flex-shrink-0"
            style={{ background: i < used ? color : '#1e293b', border: `1px solid ${i < used ? color : '#334155'}` }} />
        ))}
        {max === 0 && <span className="text-xs text-slate-500 ml-1">∞</span>}
      </div>
      <span className="text-xs font-mono" style={{ color }}>
        {used}/{max === 0 ? '∞' : max} branches
      </span>
    </div>
  );
}

function PlanBadge({ plan }) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded border text-xs font-semibold uppercase tracking-wide ${PLAN_COLORS[plan] || PLAN_COLORS.basic}`}>
      {plan}
    </span>
  );
}

function KpiCard({ icon: Icon, label, value, sub, color = 'emerald' }) {
  return (
    <div className="bg-slate-800/50 border border-slate-700/50 rounded-2xl p-5">
      <div className="flex items-center justify-between mb-3">
        <span className="text-slate-400 text-xs font-medium uppercase tracking-wider">{label}</span>
        <div className={`w-8 h-8 rounded-lg bg-${color}-500/10 flex items-center justify-center`}>
          <Icon size={16} className={`text-${color}-400`} />
        </div>
      </div>
      <div className="text-3xl font-extrabold text-white">{value}</div>
      {sub && <div className="text-slate-500 text-xs mt-1">{sub}</div>}
    </div>
  );
}

/* ── Backups & Restore Panel ─────────────────────────────────────────────── */
function RestorePanel() {
  const [backups, setBackups] = useState([]);
  const [loading, setLoading] = useState(true);
  const [restoring, setRestoring] = useState(null); // backup record being restored
  const [restoreResult, setRestoreResult] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [filter, setFilter] = useState('pre_delete'); // 'all' | 'pre_delete'
  const fileRef = useRef();

  const load = async () => {
    setLoading(true);
    try {
      const r = await api.get('/superadmin/all-backups');
      setBackups(r.data || []);
    } catch { toast.error('Failed to load backups'); }
    setLoading(false);
  };

  useEffect(() => { load(); }, []); // eslint-disable-line

  const handleRestoreFromR2 = async (backup) => {
    if (!window.confirm(`Restore "${backup.org_name || backup.org_id}"?\n\nThis will:\n• Recreate the company and all its data\n• Overwrite any existing data for this company\n\nContinue?`)) return;
    setRestoring(backup);
    setRestoreResult(null);
    try {
      const r = await api.post(`/superadmin/restore/${backup._id || backup.id}`);
      setRestoreResult({ success: true, data: r.data, org_name: backup.org_name });
      toast.success(`${backup.org_name || 'Company'} restored successfully`);
      load();
    } catch (e) {
      setRestoreResult({ success: false, error: e.response?.data?.detail || 'Restore failed', org_name: backup.org_name });
    }
    setRestoring(null);
  };

  const handleUploadRestore = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) { toast.error('Select a .json.gz backup file'); return; }
    if (!window.confirm(`Restore from "${file.name}"?\n\nThis will recreate the company and all its data from this file.\n\nContinue?`)) return;
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      const r = await api.post('/superadmin/restore-upload', fd, { headers: { 'Content-Type': 'multipart/form-data' } });
      setRestoreResult({ success: true, data: r.data, org_name: r.data.org_name });
      toast.success(`${r.data.org_name || 'Company'} restored from file`);
      if (fileRef.current) fileRef.current.value = '';
      load();
    } catch (e) {
      setRestoreResult({ success: false, error: e.response?.data?.detail || 'Upload restore failed' });
    }
    setUploading(false);
  };

  const filtered = filter === 'pre_delete'
    ? backups.filter(b => b.backup_type === 'pre_delete' || b.deletion_completed_at)
    : backups;

  const fmtDate = (iso) => { try { return formatDateTime(iso); } catch { return iso; } };
  const fmtSize = (mb) => mb ? `${parseFloat(mb).toFixed(1)} MB` : '—';

  const typeLabel = (b) => {
    if (b.backup_type === 'pre_delete' || b.deletion_completed_at) return { label: 'Pre-Delete', cls: 'bg-red-500/20 text-red-300 border-red-500/30' };
    if (b.backup_type === 'restore') return { label: 'Restore Event', cls: 'bg-blue-500/20 text-blue-300 border-blue-500/30' };
    if (b.triggered_by?.includes('schedule')) return { label: 'Scheduled', cls: 'bg-slate-500/20 text-slate-300 border-slate-600' };
    return { label: 'Manual', cls: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30' };
  };

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h2 className="text-white font-semibold text-lg">Backups &amp; Restore</h2>
          <p className="text-slate-400 text-xs mt-0.5">Restore a deleted company or upload a backup file to recover data.</p>
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          {/* Filter toggle */}
          <div className="flex gap-1 bg-slate-800 rounded-lg p-1 border border-slate-700">
            {[['pre_delete', 'Deleted Companies'], ['all', 'All Backups']].map(([v, l]) => (
              <button key={v} onClick={() => setFilter(v)}
                className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${filter === v ? 'bg-slate-600 text-white' : 'text-slate-400 hover:text-slate-200'}`}>
                {l}
              </button>
            ))}
          </div>
          {/* Upload restore */}
          <div className="flex items-center gap-2">
            <input ref={fileRef} type="file" accept=".gz,.json.gz" className="hidden" id="restore-file-input" />
            <label htmlFor="restore-file-input"
              className="cursor-pointer flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-300 hover:text-white text-xs font-medium transition-colors border border-slate-600"
              data-testid="restore-file-label">
              <Upload size={13} /> Upload Backup File
            </label>
            <Button size="sm" onClick={handleUploadRestore} disabled={uploading}
              className="h-8 bg-blue-600 hover:bg-blue-700 text-white text-xs gap-1.5" data-testid="upload-restore-btn">
              {uploading ? <><RefreshCw size={12} className="animate-spin" /> Restoring…</> : 'Restore →'}
            </Button>
          </div>
          <button onClick={load} className="p-2 text-slate-400 hover:text-white">
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {/* Restore result banner */}
      {restoreResult && (
        <div className={`p-4 rounded-xl border flex items-start gap-3 ${restoreResult.success ? 'bg-emerald-500/10 border-emerald-500/30' : 'bg-red-500/10 border-red-500/30'}`}>
          {restoreResult.success ? <CheckCircle size={18} className="text-emerald-400 mt-0.5" /> : <XCircle size={18} className="text-red-400 mt-0.5" />}
          <div className="flex-1">
            {restoreResult.success ? (
              <>
                <p className="text-emerald-300 font-semibold text-sm">"{restoreResult.org_name}" restored successfully</p>
                <p className="text-emerald-400/70 text-xs mt-0.5">{restoreResult.data?.total_documents_restored?.toLocaleString()} documents · {restoreResult.data?.collections_restored} collections</p>
                {restoreResult.data?.errors?.length > 0 && (
                  <p className="text-amber-400 text-xs mt-1">⚠ {restoreResult.data.errors.length} minor errors: {restoreResult.data.errors.join(', ')}</p>
                )}
              </>
            ) : (
              <>
                <p className="text-red-300 font-semibold text-sm">Restore failed</p>
                <p className="text-red-400/70 text-xs mt-0.5">{restoreResult.error}</p>
              </>
            )}
          </div>
          <button onClick={() => setRestoreResult(null)} className="text-slate-500 hover:text-slate-300"><X size={14} /></button>
        </div>
      )}

      {/* Backup list */}
      {loading ? (
        <div className="text-center py-16 text-slate-500"><RefreshCw size={20} className="animate-spin mx-auto mb-2" /> Loading backups…</div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-16 text-slate-600 border border-slate-700/40 rounded-2xl">
          <HardDrive size={28} className="mx-auto mb-2 opacity-30" />
          <p className="text-sm">{filter === 'pre_delete' ? 'No deleted company backups yet' : 'No backups found'}</p>
        </div>
      ) : (
        <div className="space-y-2">
          {filtered.map((b, i) => {
            const { label, cls } = typeLabel(b);
            const isRestoring = restoring?._id === b._id || restoring?.id === b.id;
            const canRestore = b.r2_key && label !== 'Restore Event';
            return (
              <div key={b._id || i} className="bg-slate-800/30 border border-slate-700/40 rounded-xl px-4 py-3 flex items-center gap-4 flex-wrap">
                {/* Org info */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-white font-semibold text-sm truncate">{b.org_name || b.org_id}</span>
                    <span className={`text-xs px-1.5 py-0.5 rounded border font-medium ${cls}`}>{label}</span>
                    {b.org_doc && <span className="text-xs text-emerald-400/70 border border-emerald-500/20 px-1.5 py-0.5 rounded">org saved</span>}
                  </div>
                  <div className="flex gap-4 mt-0.5 text-[10px] text-slate-500">
                    <span>{fmtDate(b.created_at)}</span>
                    {b.size_mb && <span>{fmtSize(b.size_mb)}</span>}
                    {b.total_documents != null && <span>{b.total_documents?.toLocaleString()} docs</span>}
                    {b.triggered_by && <span title={b.triggered_by}>by {b.triggered_by.split(' ')[0]}</span>}
                    {b.deletion_completed_at && <span className="text-red-400/70">deleted {fmtDate(b.deletion_completed_at)}</span>}
                  </div>
                </div>
                {/* Restore button */}
                {canRestore ? (
                  <Button size="sm" onClick={() => handleRestoreFromR2(b)} disabled={isRestoring}
                    className="h-8 bg-blue-600 hover:bg-blue-700 text-white text-xs gap-1.5 shrink-0"
                    data-testid={`restore-btn-${i}`}>
                    {isRestoring ? <><RefreshCw size={11} className="animate-spin" /> Restoring…</> : <><RefreshCw size={11} /> Restore</>}
                  </Button>
                ) : (
                  <span className="text-xs text-slate-600 shrink-0">{label === 'Restore Event' ? 'Event log' : 'No R2 file'}</span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}


/* ── Main Component ─────────────────────────────────────────────────────── */
export default function SuperAdminPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [stats, setStats] = useState(null);
  const [orgs, setOrgs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [filterPlan, setFilterPlan] = useState('all');
  const [expandedOrg, setExpandedOrg] = useState(null);
  const [orgBranches, setOrgBranches] = useState({});
  const [editModal, setEditModal] = useState(null); // org being edited
  const [deleteModal, setDeleteModal] = useState(null); // org being deleted
  const [paymentSettings, setPaymentSettings] = useState({});
  const [savingPayment, setSavingPayment] = useState(false);

  // Payment submissions + approve/reject
  const [submissions, setSubmissions] = useState([]);
  const [submissionsLoading, setSubmissionsLoading] = useState(false);
  const [approveModal, setApproveModal] = useState(null); // { org, submission }
  const [rejectModal, setRejectModal] = useState(null);   // { org, submission }
  const [rejectReason, setRejectReason] = useState('');
  const [actionLoading, setActionLoading] = useState(false);
  const [approveForm, setApproveForm] = useState({ plan: 'basic', extra_branches: 0, subscription_expires_at: '', note: '' });
  const [proofPreview, setProofPreview] = useState(null); // image URL to preview

  const load = useCallback(async () => {
    if (!user?.is_super_admin) { navigate('/dashboard'); return; }
    setLoading(true);
    try {
      const [s, o] = await Promise.all([
        api.get('/superadmin/stats'),
        api.get('/superadmin/organizations'),
      ]);
      setStats(s.data);
      setOrgs(o.data);
    } catch { toast.error('Failed to load data'); }
    setLoading(false);
  }, [user, navigate]);

  const loadPayment = useCallback(async () => {
    try {
      const r = await api.get('/superadmin/settings/payment');
      setPaymentSettings(r.data?.value || {});
    } catch {}
  }, []);

  useEffect(() => { load(); loadPayment(); }, [load, loadPayment]);

  const loadSubmissions = useCallback(async () => {
    setSubmissionsLoading(true);
    try {
      const r = await api.get('/superadmin/payment-submissions?status=all');
      setSubmissions(r.data.submissions || []);
    } catch {}
    setSubmissionsLoading(false);
  }, []);

  const handleApprove = async () => {
    if (!approveModal) return;
    setActionLoading(true);
    try {
      await api.post(`/superadmin/organizations/${approveModal.org.id}/approve-subscription`, {
        plan: approveForm.plan,
        extra_branches: parseInt(approveForm.extra_branches) || 0,
        subscription_expires_at: approveForm.subscription_expires_at || null,
        note: approveForm.note,
      });
      toast.success(`Subscription approved for ${approveModal.org.name}`);
      setApproveModal(null);
      load();
      loadSubmissions();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed'); }
    setActionLoading(false);
  };

  const handleReject = async () => {
    if (!rejectModal || !rejectReason.trim()) { toast.error('Reason is required'); return; }
    setActionLoading(true);
    try {
      await api.post(`/superadmin/organizations/${rejectModal.org.id}/reject-subscription`, {
        reason: rejectReason,
        plan: rejectModal.submission?.plan_requested || rejectModal.org.plan,
      });
      toast.success(`Rejection sent to ${rejectModal.org.name}`);
      setRejectModal(null);
      setRejectReason('');
      loadSubmissions();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed'); }
    setActionLoading(false);
  };

  const loadOrgBranches = async (orgId) => {
    if (orgBranches[orgId]) return;
    try {
      const r = await api.get(`/superadmin/organizations/${orgId}/branches`);
      setOrgBranches(prev => ({ ...prev, [orgId]: r.data }));
    } catch {}
  };

  const toggleExpand = async (orgId) => {
    if (expandedOrg === orgId) { setExpandedOrg(null); return; }
    setExpandedOrg(orgId);
    await loadOrgBranches(orgId);
  };

  const filtered = orgs.filter(o => {
    const matchSearch = !search ||
      o.name.toLowerCase().includes(search.toLowerCase()) ||
      o.owner_email?.toLowerCase().includes(search.toLowerCase());
    const matchPlan = filterPlan === 'all' || (o.effective_plan || o.plan) === filterPlan;
    return matchSearch && matchPlan;
  });

  if (!user?.is_super_admin) return null;

  return (
    <div className="min-h-screen bg-[#060D1A]" style={{ fontFamily: 'Manrope, sans-serif' }}>
      {/* Top bar */}
      <header className="border-b border-white/5 bg-[#0A0F1C]/80 backdrop-blur-md sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-6 h-14 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button onClick={() => navigate('/dashboard')} className="text-slate-500 hover:text-slate-300 transition-colors">
              <ArrowLeft size={18} />
            </button>
            <div className="w-7 h-7 bg-emerald-500 rounded-lg flex items-center justify-center">
              <Shield size={14} className="text-white" />
            </div>
            <span className="text-white font-bold">Platform Admin</span>
            <span className="text-slate-600 text-xs hidden md:block">· AgriBooks</span>
          </div>
          <Button variant="ghost" size="sm" onClick={load} className="text-slate-400 hover:text-white gap-2">
            <RefreshCw size={14} />
          </Button>
        </div>
      </header>

      <div className="max-w-7xl mx-auto px-6 py-8">
        <Tabs defaultValue="overview" className="space-y-6">
          <TabsList className="bg-slate-800/50 border border-slate-700/50 p-1 rounded-xl">
            <TabsTrigger value="overview" className="data-[state=active]:bg-slate-700 data-[state=active]:text-white text-slate-400 rounded-lg px-5">
              Overview
            </TabsTrigger>
            <TabsTrigger value="organizations" className="data-[state=active]:bg-slate-700 data-[state=active]:text-white text-slate-400 rounded-lg px-5">
              Organizations {orgs.length > 0 && <span className="ml-1.5 bg-slate-600 text-slate-300 text-xs px-1.5 py-0.5 rounded-full">{orgs.length}</span>}
            </TabsTrigger>
            <TabsTrigger value="payments" className="data-[state=active]:bg-slate-700 data-[state=active]:text-white text-slate-400 rounded-lg px-5"
              onClick={loadSubmissions}>
              Payments
              {submissions.filter(s => s.status === 'pending').length > 0 && (
                <span className="ml-1.5 bg-amber-500 text-white text-xs px-1.5 py-0.5 rounded-full">
                  {submissions.filter(s => s.status === 'pending').length}
                </span>
              )}
            </TabsTrigger>
            <TabsTrigger value="features" className="data-[state=active]:bg-slate-700 data-[state=active]:text-white text-slate-400 rounded-lg px-5">
              Feature Flags
            </TabsTrigger>
            <TabsTrigger value="settings" className="data-[state=active]:bg-slate-700 data-[state=active]:text-white text-slate-400 rounded-lg px-5">
              Payment Settings
            </TabsTrigger>
            <TabsTrigger value="restore" className="data-[state=active]:bg-slate-700 data-[state=active]:text-white text-slate-400 rounded-lg px-5">
              Backups &amp; Restore
            </TabsTrigger>
          </TabsList>

          {/* ── OVERVIEW ────────────────────────────────────────────────── */}
          <TabsContent value="overview" className="space-y-6">
            {loading ? (
              <div className="text-center py-16 text-slate-500">Loading...</div>
            ) : stats && (
              <>
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-7 gap-3">
                  <KpiCard icon={Building2}     label="Total Orgs"    value={stats.total_organizations} color="slate" />
                  <KpiCard icon={CheckCircle}   label="Active (Paid)" value={stats.active}              color="emerald" />
                  <KpiCard icon={Clock}         label="On Trial"      value={stats.trial}               color="blue" />
                  <KpiCard icon={Star}          label="Founders"      value={stats.founders || 0}       color="amber" />
                  <KpiCard icon={AlertTriangle} label="Expiring Soon" value={stats.expiring_soon}       sub="within 7 days" color="amber" />
                  <KpiCard icon={XCircle}       label="Suspended"     value={stats.suspended}           color="red" />
                  <KpiCard icon={Users}         label="Total Users"   value={stats.total_users}         color="indigo" />
                </div>

                {/* Plan breakdown */}
                <div className="bg-slate-800/30 border border-slate-700/50 rounded-2xl p-6">
                  <h3 className="text-white font-semibold mb-5 flex items-center gap-2">
                    <Layers size={16} className="text-slate-400" /> Plan Breakdown
                  </h3>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                    {[
                      { plan: 'basic',    label: 'Basic',      color: '#64748b', price: '₱1,500' },
                      { plan: 'standard', label: 'Standard',   color: '#10b981', price: '₱4,000' },
                      { plan: 'pro',      label: 'Pro',        color: '#6366f1', price: '₱7,500' },
                      { plan: 'founders', label: '★ Founders', color: '#f59e0b', price: 'Lifetime' },
                    ].map(({ plan, label, color, price }) => {
                      const count = stats.by_plan?.[plan] || 0;
                      const maxVal = Math.max(...Object.values(stats.by_plan || {}), 1);
                      return (
                        <div key={plan} className="bg-slate-900/50 rounded-xl p-4">
                          <div className="flex items-center justify-between mb-2">
                            <span className="text-slate-300 text-sm font-medium">{label}</span>
                            <span className="text-xs text-slate-500">{price}</span>
                          </div>
                          <div className="text-2xl font-bold text-white mb-2">{count}</div>
                          <div className="w-full bg-slate-800 rounded-full h-1.5">
                            <div className="h-1.5 rounded-full transition-all" style={{ width: `${(count / maxVal) * 100}%`, background: color }} />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>

                {/* Orgs needing attention */}
                {orgs.filter(o => ['grace_period', 'expired', 'suspended'].includes(o.effective_plan)).length > 0 && (
                  <div className="bg-amber-500/5 border border-amber-500/20 rounded-2xl p-5">
                    <h3 className="text-amber-300 font-semibold mb-4 flex items-center gap-2">
                      <AlertTriangle size={16} /> Needs Attention
                    </h3>
                    <div className="space-y-2">
                      {orgs.filter(o => ['grace_period', 'expired', 'suspended'].includes(o.effective_plan)).map(org => (
                        <div key={org.id} className="flex items-center justify-between bg-slate-900/50 rounded-xl px-4 py-3">
                          <div>
                            <span className="text-white text-sm font-medium">{org.name}</span>
                            <span className="text-slate-500 text-xs ml-2">{org.owner_email}</span>
                          </div>
                          <PlanBadge plan={org.effective_plan} />
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}
          </TabsContent>

          {/* ── ORGANIZATIONS ────────────────────────────────────────────── */}
          <TabsContent value="organizations" className="space-y-4">
            {/* Filters */}
            <div className="flex gap-3 flex-wrap">
              <div className="relative flex-1 min-w-[200px]">
                <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
                <Input value={search} onChange={e => setSearch(e.target.value)}
                  placeholder="Search company or email..."
                  className="bg-slate-800/50 border-slate-700 text-white placeholder:text-slate-600 pl-9 h-10" />
              </div>
              <div className="flex gap-1.5">
                {['all', 'trial', 'basic', 'standard', 'pro', 'founders', 'suspended'].map(p => (
                  <button key={p} onClick={() => setFilterPlan(p)}
                    className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${filterPlan === p ? 'bg-emerald-600 text-white' : 'bg-slate-800 text-slate-400 hover:text-white'}`}>
                    {p === 'all' ? 'All' : p.charAt(0).toUpperCase() + p.slice(1)}
                  </button>
                ))}
              </div>
            </div>

            {loading ? (
              <div className="text-center py-16 text-slate-500">Loading organizations...</div>
            ) : filtered.length === 0 ? (
              <div className="text-center py-16 text-slate-500">No organizations found</div>
            ) : (
              <div className="space-y-2">
                {filtered.map(org => (
                  <OrgRow key={org.id} org={org} expanded={expandedOrg === org.id}
                    branches={orgBranches[org.id] || []}
                    onToggle={() => toggleExpand(org.id)}
                    onEdit={() => setEditModal(org)}
                    onDelete={() => setDeleteModal(org)}
                    onRefresh={load} />
                ))}
              </div>
            )}
          </TabsContent>

          {/* ── FEATURE FLAGS ────────────────────────────────────────────── */}
          <TabsContent value="features">
            <FeatureFlagsPanel />
          </TabsContent>

          {/* ── PAYMENT SETTINGS ─────────────────────────────────────────── */}
          <TabsContent value="settings">
            <PaymentSettingsPanel
              settings={paymentSettings}
              setSettings={setPaymentSettings}
              saving={savingPayment}
              setSaving={setSavingPayment}
            />
          </TabsContent>

          {/* ── BACKUPS & RESTORE ───────────────────────────────────────── */}
          <TabsContent value="restore" className="space-y-4">
            <RestorePanel />
          </TabsContent>

          {/* ── PAYMENT SUBMISSIONS ─────────────────────────────────────── */}
          <TabsContent value="payments" className="space-y-4">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-white font-semibold text-lg">Payment Submissions</h2>
                <p className="text-slate-400 text-xs mt-0.5">Customer payment proofs awaiting review. Approve to activate their plan.</p>
              </div>
              <button onClick={loadSubmissions} className="text-slate-400 hover:text-white p-2 rounded-lg hover:bg-slate-700">
                <RefreshCw size={14} className={submissionsLoading ? 'animate-spin' : ''} />
              </button>
            </div>

            {submissionsLoading ? (
              <div className="text-center py-12 text-slate-500">Loading submissions...</div>
            ) : submissions.length === 0 ? (
              <div className="text-center py-12 text-slate-600 border border-slate-700/40 rounded-2xl">
                <CreditCard size={28} className="mx-auto mb-2 opacity-30" />
                <p className="text-sm">No payment submissions yet</p>
              </div>
            ) : (
              <div className="space-y-3">
                {/* Pending first */}
                {['pending', 'approved', 'rejected'].map(statusGroup => {
                  const grouped = submissions.filter(s => s.status === statusGroup);
                  if (!grouped.length) return null;
                  return (
                    <div key={statusGroup}>
                      <p className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-2">
                        {statusGroup === 'pending' ? '⏳ Awaiting Review' : statusGroup === 'approved' ? '✓ Approved' : '✗ Rejected'}
                        {' '}({grouped.length})
                      </p>
                      <div className="space-y-2">
                        {grouped.map(sub => {
                          const org = orgs.find(o => o.id === sub.organization_id);
                          return (
                            <div key={sub.id} className={`rounded-2xl border p-4 ${
                              statusGroup === 'pending' ? 'bg-amber-500/5 border-amber-500/30' :
                              statusGroup === 'approved' ? 'bg-emerald-500/5 border-emerald-500/20' :
                              'bg-slate-800/30 border-slate-700/40'
                            }`}>
                              <div className="flex items-start justify-between gap-4">
                                <div className="flex-1 min-w-0">
                                  <div className="flex items-center gap-2 flex-wrap">
                                    <span className="text-white font-semibold text-sm">{sub.org_name || org?.name || 'Unknown'}</span>
                                    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                                      statusGroup === 'pending' ? 'bg-amber-500/20 text-amber-300' :
                                      statusGroup === 'approved' ? 'bg-emerald-500/20 text-emerald-300' :
                                      'bg-red-500/20 text-red-300'
                                    }`}>{statusGroup}</span>
                                    <span className="text-emerald-400 text-xs font-bold">₱{sub.amount?.toLocaleString()}</span>
                                    <span className="text-slate-400 text-xs">{sub.payment_method}</span>
                                  </div>
                                  <p className="text-slate-400 text-xs mt-0.5">{sub.owner_email} · Plan requested: <strong className="text-slate-300 capitalize">{sub.plan_requested}</strong></p>
                                  {sub.reference_number && <p className="text-slate-500 text-xs">Ref: {sub.reference_number}</p>}
                                  {sub.notes && <p className="text-slate-500 text-xs italic">{sub.notes}</p>}
                                  <p className="text-slate-600 text-[10px] mt-1">{formatDateTime(sub.submitted_at)}</p>
                                  {statusGroup === 'rejected' && sub.rejection_reason && (
                                    <p className="text-red-400 text-xs mt-1">Reason: {sub.rejection_reason}</p>
                                  )}
                                </div>
                                <div className="flex flex-col gap-2 shrink-0">
                                  {sub.proof_image && (
                                    <button onClick={() => setProofPreview(sub.proof_image)}
                                      className="text-xs bg-slate-700 hover:bg-slate-600 text-slate-300 px-3 py-1.5 rounded-lg flex items-center gap-1">
                                      <Upload size={11} /> View Proof
                                    </button>
                                  )}
                                  {statusGroup === 'pending' && org && (
                                    <>
                                      <button onClick={() => {
                                        setApproveModal({ org, submission: sub });
                                        setApproveForm({ plan: sub.plan_requested || 'basic', extra_branches: 0, subscription_expires_at: new Date(Date.now() + 30 * 86400000).toISOString().split('T')[0], note: '' });
                                      }}
                                        className="text-xs bg-emerald-600 hover:bg-emerald-500 text-white px-3 py-1.5 rounded-lg flex items-center gap-1">
                                        <CheckCircle size={11} /> Approve
                                      </button>
                                      <button onClick={() => { setRejectModal({ org, submission: sub }); setRejectReason(''); }}
                                        className="text-xs bg-red-600/80 hover:bg-red-600 text-white px-3 py-1.5 rounded-lg flex items-center gap-1">
                                        <XCircle size={11} /> Reject
                                      </button>
                                    </>
                                  )}
                                </div>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </TabsContent>
        </Tabs>
      </div>

      {/* Proof of Payment Preview */}
      {proofPreview && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80" onClick={() => setProofPreview(null)}>
          <div className="relative max-w-xl w-full" onClick={e => e.stopPropagation()}>
            <button onClick={() => setProofPreview(null)} className="absolute -top-10 right-0 text-white/60 hover:text-white text-sm">✕ Close</button>
            <img src={proofPreview} alt="Payment proof" className="w-full rounded-2xl shadow-2xl" />
          </div>
        </div>
      )}

      {/* Approve Modal */}
      {approveModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70">
          <div className="bg-[#0E1628] border border-slate-700 rounded-2xl shadow-2xl w-full max-w-md p-6 space-y-4">
            <h3 className="text-white font-bold text-lg flex items-center gap-2">
              <CheckCircle size={18} className="text-emerald-400" /> Approve Subscription
            </h3>
            <p className="text-slate-400 text-sm">
              Activating plan for <strong className="text-white">{approveModal.org.name}</strong>. An email will be sent to {approveModal.org.owner_email}.
            </p>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-slate-400">Plan</label>
                <select value={approveForm.plan} onChange={e => setApproveForm(f => ({ ...f, plan: e.target.value }))}
                  className="w-full mt-1 bg-slate-800 border border-slate-700 text-white rounded-lg px-3 py-2 text-sm">
                  {['basic', 'standard', 'pro', 'founders'].map(p => (
                    <option key={p} value={p}>{p.charAt(0).toUpperCase() + p.slice(1)}</option>
                  ))}
                </select>
              </div>
              {['basic', 'standard', 'pro'].includes(approveForm.plan) && (
                <div>
                  <label className="text-xs text-slate-400">Subscription Expires</label>
                  <input type="date" value={approveForm.subscription_expires_at}
                    onChange={e => setApproveForm(f => ({ ...f, subscription_expires_at: e.target.value }))}
                    className="w-full mt-1 bg-slate-800 border border-slate-700 text-white rounded-lg px-3 py-2 text-sm" />
                </div>
              )}
              <div>
                <label className="text-xs text-slate-400">Note (optional)</label>
                <input type="text" value={approveForm.note}
                  onChange={e => setApproveForm(f => ({ ...f, note: e.target.value }))}
                  placeholder="e.g. Payment confirmed via GCash"
                  className="w-full mt-1 bg-slate-800 border border-slate-700 text-white rounded-lg px-3 py-2 text-sm placeholder:text-slate-600" />
              </div>
            </div>
            <div className="flex gap-2 pt-1">
              <button onClick={() => setApproveModal(null)} className="flex-1 py-2 rounded-xl border border-slate-700 text-slate-400 hover:text-white text-sm">Cancel</button>
              <button onClick={handleApprove} disabled={actionLoading}
                className="flex-1 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-semibold text-sm flex items-center justify-center gap-2">
                {actionLoading ? <RefreshCw size={14} className="animate-spin" /> : <CheckCircle size={14} />} Approve & Send Email
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Reject Modal */}
      {rejectModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70">
          <div className="bg-[#0E1628] border border-slate-700 rounded-2xl shadow-2xl w-full max-w-md p-6 space-y-4">
            <h3 className="text-white font-bold text-lg flex items-center gap-2">
              <XCircle size={18} className="text-red-400" /> Reject Subscription Payment
            </h3>
            <p className="text-slate-400 text-sm">
              <strong className="text-white">{rejectModal.org.name}</strong> ({rejectModal.org.owner_email}) will receive a rejection email with your reason.
            </p>
            <div>
              <label className="text-xs text-slate-400">Reason for rejection *</label>
              <textarea value={rejectReason} onChange={e => setRejectReason(e.target.value)}
                placeholder="e.g. Payment amount does not match the plan price. Please resend the exact amount of ₱4,000 for the Standard plan."
                rows={3}
                className="w-full mt-1 bg-slate-800 border border-slate-700 text-white rounded-lg px-3 py-2 text-sm placeholder:text-slate-600 resize-none" />
            </div>
            <div className="flex gap-2 pt-1">
              <button onClick={() => setRejectModal(null)} className="flex-1 py-2 rounded-xl border border-slate-700 text-slate-400 hover:text-white text-sm">Cancel</button>
              <button onClick={handleReject} disabled={actionLoading || !rejectReason.trim()}
                className="flex-1 py-2 rounded-xl bg-red-600 hover:bg-red-500 text-white font-semibold text-sm flex items-center justify-center gap-2 disabled:opacity-50">
                {actionLoading ? <RefreshCw size={14} className="animate-spin" /> : <XCircle size={14} />} Reject & Notify Customer
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Edit subscription modal */}
      {editModal && (
        <EditSubscriptionModal
          org={editModal}
          onClose={() => setEditModal(null)}
          onSaved={(updated) => {
            setOrgs(prev => prev.map(o => o.id === updated.id ? { ...o, ...updated } : o));
            setEditModal(null);
            toast.success('Subscription updated');
          }}
        />
      )}

      {/* Delete organization modal */}
      {deleteModal && (
        <DeleteOrgModal
          org={deleteModal}
          onClose={() => setDeleteModal(null)}
          onDeleted={() => {
            setOrgs(prev => prev.filter(o => o.id !== deleteModal.id));
            toast.success(`${deleteModal.name} has been deleted and backed up.`);
          }}
        />
      )}
    </div>
  );
}

/* ── Org Row ────────────────────────────────────────────────────────────── */
function OrgRow({ org, expanded, branches, onToggle, onEdit, onDelete, onRefresh }) {
  const effectivePlan = org.effective_plan || org.plan;
  const statusKey = org.subscription_status === 'active' && effectivePlan === 'grace_period'
    ? 'grace_period'
    : effectivePlan === 'expired' ? 'expired' : org.subscription_status;

  const expiryDate = org.plan === 'trial' ? org.trial_ends_at : org.subscription_expires_at;
  const daysLeft = expiryDate
    ? Math.ceil((new Date(expiryDate) - new Date()) / 86400000)
    : null;

  return (
    <div className="bg-slate-800/30 border border-slate-700/40 rounded-2xl overflow-hidden hover:border-slate-600/60 transition-colors">
      {/* Row header */}
      <div className="flex items-center gap-4 px-5 py-4">
        {/* Status dot */}
        <div className={`w-2.5 h-2.5 rounded-full shrink-0 ${STATUS_DOT[statusKey] || 'bg-slate-500'}`} />

        {/* Company info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-white font-semibold text-sm truncate">{org.name}</span>
            {org.is_default && <span className="text-xs bg-slate-700 text-slate-400 px-1.5 py-0.5 rounded">Default</span>}
          </div>
          <div className="text-slate-500 text-xs mt-0.5 truncate">{org.owner_email || 'No email'}</div>
        </div>

        {/* Plan */}
        <div className="hidden sm:block">
          <PlanBadge plan={effectivePlan} />
        </div>

        {/* Branch gauge */}
        <div className="hidden md:block">
          <BranchGauge used={org.branch_count || 0} max={org.max_branches || 1} />
        </div>

        {/* Users */}
        <div className="hidden lg:flex items-center gap-1.5 text-slate-400">
          <Users size={13} />
          <span className="text-xs">{org.user_count || 0}</span>
        </div>

        {/* Expiry */}
        {org.plan === 'founders' ? (
          <div className="hidden lg:flex items-center gap-1 text-amber-300 text-xs font-medium">
            <span>★</span> Lifetime
          </div>
        ) : daysLeft !== null ? (
          <div className={`hidden lg:block text-xs font-medium ${daysLeft <= 0 ? 'text-red-400' : daysLeft <= 7 ? 'text-amber-400' : 'text-slate-500'}`}>
            {daysLeft <= 0 ? 'Expired' : `${daysLeft}d left`}
          </div>
        ) : ['basic', 'standard', 'pro'].includes(org.plan) ? (
          <div className="hidden lg:block text-xs text-amber-500/70" title="No expiry date set — plan won't auto-expire">
            No expiry set
          </div>
        ) : null}

        {/* Actions */}
        <div className="flex items-center gap-1.5 shrink-0">
          <button
            data-testid={`view-as-org-${org.id}`}
            onClick={async () => {
              try {
                await api.post(`/superadmin/impersonate/${org.id}/enter`);
                toast.success(`Now viewing as ${org.name}`);
                window.location.href = '/dashboard';
              } catch (e) {
                toast.error(e?.response?.data?.detail || 'Could not enter tenant view');
              }
            }}
            title={`View as ${org.name} (4-hour audit-logged session)`}
            className="w-8 h-8 rounded-lg bg-amber-500/20 hover:bg-amber-500/40 text-amber-300 hover:text-amber-200 flex items-center justify-center transition-colors">
            <Eye size={14} />
          </button>
          <button data-testid={`edit-org-${org.id}`} onClick={onEdit}
            className="w-8 h-8 rounded-lg bg-slate-700/60 hover:bg-slate-600 text-slate-300 hover:text-white flex items-center justify-center transition-colors">
            <Edit3 size={14} />
          </button>
          <button data-testid={`delete-org-${org.id}`} onClick={onDelete}
            className="w-8 h-8 rounded-lg bg-red-500/10 hover:bg-red-500/25 text-red-500/60 hover:text-red-400 flex items-center justify-center transition-colors"
            title={`Delete ${org.name}`}>
            <Trash2 size={14} />
          </button>
          <button data-testid={`expand-org-${org.id}`} onClick={onToggle}
            className="w-8 h-8 rounded-lg bg-slate-700/60 hover:bg-slate-600 text-slate-300 hover:text-white flex items-center justify-center transition-colors">
            {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="border-t border-slate-700/40 bg-slate-900/30 px-5 py-4">
          <div className="grid md:grid-cols-2 gap-6">
            {/* Branches */}
            <div>
              <h4 className="text-slate-300 text-xs font-semibold uppercase tracking-wider mb-3 flex items-center gap-2">
                <GitBranch size={13} /> Branches ({org.branch_count || 0}/{org.max_branches === 0 ? '∞' : org.max_branches})
              </h4>
              {branches.length === 0 ? (
                <p className="text-slate-600 text-xs">No branches yet</p>
              ) : (
                <div className="space-y-1.5">
                  {branches.map(b => (
                    <div key={b.id} className="flex items-center gap-2 text-sm">
                      <div className={`w-1.5 h-1.5 rounded-full ${b.active ? 'bg-emerald-400' : 'bg-slate-600'}`} />
                      <span className={b.active ? 'text-slate-300' : 'text-slate-600'}>{b.name}</span>
                      {b.is_main && <span className="text-xs text-emerald-500">main</span>}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Subscription details */}
            <div>
              <h4 className="text-slate-300 text-xs font-semibold uppercase tracking-wider mb-3 flex items-center gap-2">
                <CreditCard size={13} /> Subscription
              </h4>
              <div className="space-y-1.5 text-xs">
                <div className="flex justify-between">
                  <span className="text-slate-500">Plan</span>
                  <span className="text-slate-300 capitalize">{org.plan}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Effective</span>
                  <span className="text-slate-300 capitalize">{effectivePlan}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Status</span>
                  <span className="text-slate-300">{org.subscription_status}</span>
                </div>
                {org.trial_ends_at && org.plan === 'trial' && (
                  <div className="flex justify-between">
                    <span className="text-slate-500">Trial ends</span>
                    <span className="text-slate-300">{formatDate(org.trial_ends_at)}</span>
                  </div>
                )}
                {org.plan === 'founders' ? (
                  <div className="flex justify-between">
                    <span className="text-slate-500">Expires</span>
                    <span className="text-amber-300 font-semibold">★ Never (Lifetime)</span>
                  </div>
                ) : org.subscription_expires_at && (
                  <div className="flex justify-between">
                    <span className="text-slate-500">Sub expires</span>
                    <span className={`${
                      new Date(org.subscription_expires_at) < new Date() ? 'text-red-400' :
                      Math.ceil((new Date(org.subscription_expires_at) - new Date()) / 86400000) <= 7 ? 'text-amber-400' :
                      'text-slate-300'
                    }`}>
                      {formatDate(org.subscription_expires_at)}
                      {' '}
                      ({Math.ceil((new Date(org.subscription_expires_at) - new Date()) / 86400000)}d)
                    </span>
                  </div>
                )}
                {['basic', 'standard', 'pro'].includes(org.plan) && !org.subscription_expires_at && (
                  <div className="flex justify-between">
                    <span className="text-slate-500">Expires</span>
                    <span className="text-amber-500/70 text-xs">Not set — set via Edit</span>
                  </div>
                )}
                <div className="flex justify-between">
                  <span className="text-slate-500">Max branches</span>
                  <span className="text-slate-300">{org.max_branches === 0 ? '∞' : org.max_branches} (+{org.extra_branches || 0} add-on)</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Joined</span>
                  <span className="text-slate-300">{formatDate(org.created_at)}</span>
                </div>
                {org.admin_notes && (
                  <div className="mt-2 p-2 bg-slate-800 rounded-lg text-slate-400 text-xs">{org.admin_notes}</div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ── Delete Organization Modal ───────────────────────────────────────────── */
function DeleteOrgModal({ org, onClose, onDeleted }) {
  // step: 'confirm_name' → 'confirm_final' → 'working' → 'done' → 'error'
  const [step, setStep] = useState('confirm_name');
  const [nameInput, setNameInput] = useState('');
  const [finalInput, setFinalInput] = useState('');
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');

  const nameMatch = nameInput.trim() === org.name.trim();
  const finalMatch = finalInput.trim() === 'PERMANENTLY DELETE';

  const runDelete = async () => {
    setStep('working');
    setError('');
    try {
      const r = await api.delete(`/superadmin/organizations/${org.id}`);
      setResult(r.data);
      setStep('done');
      onDeleted();
    } catch (e) {
      setError(e.response?.data?.detail || 'An unexpected error occurred.');
      setStep('error');
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
      <div className="w-full max-w-md bg-[#0f172a] border border-red-500/30 rounded-2xl shadow-2xl overflow-hidden"
        data-testid="delete-org-modal">

        {/* Header */}
        <div className="flex items-center gap-3 px-6 pt-6 pb-4 border-b border-slate-700/50">
          <div className="w-10 h-10 rounded-xl bg-red-500/15 border border-red-500/30 flex items-center justify-center shrink-0">
            <Trash2 size={18} className="text-red-400" />
          </div>
          <div>
            <p className="text-white font-bold text-base">Delete Company</p>
            <p className="text-slate-400 text-xs mt-0.5 font-mono truncate max-w-xs">{org.name}</p>
          </div>
          {step !== 'working' && (
            <button onClick={onClose} className="ml-auto text-slate-500 hover:text-slate-300 p-1">
              <X size={16} />
            </button>
          )}
        </div>

        <div className="px-6 py-5 space-y-4">

          {/* ── Step 1: Type company name ───────────────────────────────── */}
          {step === 'confirm_name' && (
            <>
              <div className="bg-red-500/8 border border-red-500/20 rounded-xl p-4 space-y-1.5">
                <p className="text-red-300 text-sm font-semibold flex items-center gap-2">
                  <AlertTriangle size={15} /> This will permanently delete:
                </p>
                <ul className="text-red-400/80 text-xs space-y-1 ml-5 list-disc">
                  <li>All company data — products, inventory, sales, customers</li>
                  <li>All users, branches, purchase orders, reports</li>
                  <li>All documents, receipts, settings, SMS history</li>
                </ul>
                <p className="text-emerald-400 text-xs mt-2 flex items-center gap-1.5">
                  <CheckCircle size={12} /> A full backup will be saved to R2 before deletion.
                </p>
              </div>

              <div>
                <label className="text-slate-400 text-xs block mb-2">
                  Type the company name exactly to continue:
                  <span className="text-white font-mono ml-1.5 select-all">{org.name}</span>
                </label>
                <input
                  autoFocus
                  value={nameInput}
                  onChange={e => setNameInput(e.target.value)}
                  placeholder={org.name}
                  className="w-full h-10 bg-slate-800 border border-slate-600 rounded-lg px-3 text-sm text-white placeholder:text-slate-600 focus:outline-none focus:ring-2 focus:ring-red-500/50"
                  data-testid="delete-name-input"
                />
                {nameInput && !nameMatch && (
                  <p className="text-red-400 text-xs mt-1">Name doesn't match — check capitalisation and spaces.</p>
                )}
              </div>

              <div className="flex gap-3 pt-1">
                <Button variant="ghost" onClick={onClose}
                  className="flex-1 h-10 text-slate-400 hover:text-white border border-slate-700 hover:bg-slate-800">
                  Cancel
                </Button>
                <Button onClick={() => { setStep('confirm_final'); setFinalInput(''); }}
                  disabled={!nameMatch}
                  className="flex-1 h-10 bg-red-600 hover:bg-red-700 text-white disabled:opacity-40"
                  data-testid="delete-next-btn">
                  Continue →
                </Button>
              </div>
            </>
          )}

          {/* ── Step 2: Final confirmation ──────────────────────────────── */}
          {step === 'confirm_final' && (
            <>
              <div className="bg-slate-800/60 border border-slate-700 rounded-xl p-4 text-center space-y-1">
                <p className="text-white font-semibold text-sm">{org.name}</p>
                <p className="text-slate-400 text-xs">{org.owner_email}</p>
                <p className="text-slate-500 text-xs">{org.user_count || 0} users · {org.branch_count || 0} branches</p>
              </div>

              <div>
                <p className="text-slate-300 text-sm mb-1 font-medium">Final confirmation</p>
                <p className="text-slate-400 text-xs mb-3">
                  A backup will be created first, then all data will be permanently removed.<br />
                  Type <span className="font-mono text-white bg-slate-700 px-1.5 py-0.5 rounded text-xs">PERMANENTLY DELETE</span> to proceed.
                </p>
                <input
                  autoFocus
                  value={finalInput}
                  onChange={e => setFinalInput(e.target.value)}
                  placeholder="PERMANENTLY DELETE"
                  className="w-full h-10 bg-slate-800 border border-slate-600 rounded-lg px-3 text-sm text-white placeholder:text-slate-600 focus:outline-none focus:ring-2 focus:ring-red-500/50 font-mono"
                  data-testid="delete-final-input"
                />
                {finalInput && !finalMatch && (
                  <p className="text-red-400 text-xs mt-1">Must be exactly: PERMANENTLY DELETE</p>
                )}
              </div>

              <div className="flex gap-3 pt-1">
                <Button variant="ghost" onClick={() => setStep('confirm_name')}
                  className="flex-1 h-10 text-slate-400 hover:text-white border border-slate-700 hover:bg-slate-800">
                  ← Back
                </Button>
                <Button onClick={runDelete} disabled={!finalMatch}
                  className="flex-1 h-10 bg-red-600 hover:bg-red-700 text-white font-semibold disabled:opacity-40"
                  data-testid="delete-confirm-btn">
                  Backup & Delete
                </Button>
              </div>
            </>
          )}

          {/* ── Step 3: Working ─────────────────────────────────────────── */}
          {step === 'working' && (
            <div className="py-8 text-center space-y-4">
              <RefreshCw size={32} className="animate-spin text-red-400 mx-auto" />
              <div>
                <p className="text-white font-semibold">Processing…</p>
                <p className="text-slate-400 text-sm mt-1">Creating backup, then deleting all data.</p>
                <p className="text-slate-500 text-xs mt-2">This may take a few seconds. Do not close this window.</p>
              </div>
            </div>
          )}

          {/* ── Step 4: Done ────────────────────────────────────────────── */}
          {step === 'done' && result && (
            <div className="space-y-4">
              <div className="flex items-center gap-3 p-4 bg-emerald-500/10 border border-emerald-500/30 rounded-xl">
                <CheckCircle size={20} className="text-emerald-400 shrink-0" />
                <div>
                  <p className="text-emerald-300 font-semibold text-sm">Deleted successfully</p>
                  <p className="text-emerald-400/70 text-xs">{result.org_name}</p>
                </div>
              </div>
              <div className="bg-slate-800/50 border border-slate-700 rounded-xl p-4 space-y-2 text-xs">
                <p className="text-slate-300 font-semibold uppercase tracking-wider text-[10px]">Backup Details</p>
                <div className="flex justify-between">
                  <span className="text-slate-500">Filename</span>
                  <span className="text-slate-300 font-mono text-[10px]">{result.backup?.filename}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Size</span>
                  <span className="text-slate-300">{result.backup?.size_mb} MB</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Documents backed up</span>
                  <span className="text-slate-300">{result.backup?.total_documents?.toLocaleString()}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Stored in R2</span>
                  <span className={result.backup?.r2_uploaded ? 'text-emerald-400' : 'text-amber-400'}>
                    {result.backup?.r2_uploaded ? '✓ Uploaded' : '⚠ Local only'}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-500">Documents deleted</span>
                  <span className="text-red-400">{result.total_documents_deleted?.toLocaleString()}</span>
                </div>
              </div>
              <Button onClick={onClose} className="w-full h-10 bg-slate-700 hover:bg-slate-600 text-white">
                Close
              </Button>
            </div>
          )}

          {/* ── Step 5: Error ───────────────────────────────────────────── */}
          {step === 'error' && (
            <div className="space-y-4">
              <div className="flex items-start gap-3 p-4 bg-red-500/10 border border-red-500/30 rounded-xl">
                <XCircle size={20} className="text-red-400 shrink-0 mt-0.5" />
                <div>
                  <p className="text-red-300 font-semibold text-sm">Deletion failed</p>
                  <p className="text-red-400/80 text-xs mt-1">{error}</p>
                  <p className="text-slate-500 text-xs mt-2">No data was deleted — the operation was aborted before any changes.</p>
                </div>
              </div>
              <div className="flex gap-3">
                <Button variant="ghost" onClick={onClose}
                  className="flex-1 h-10 text-slate-400 border border-slate-700">Close</Button>
                <Button onClick={() => { setStep('confirm_final'); setFinalInput(''); }}
                  className="flex-1 h-10 bg-slate-700 hover:bg-slate-600 text-white">
                  Try Again
                </Button>
              </div>
            </div>
          )}

        </div>
      </div>
    </div>
  );
}


/* ── Edit Subscription Modal ─────────────────────────────────────────────── */
function EditSubscriptionModal({ org, onClose, onSaved }) {
  const today = localTodayStr();
  const plus30 = new Date(Date.now() + 30 * 86400000).toISOString().split('T')[0];

  const [form, setForm] = useState({
    plan: org.plan || 'trial',
    extra_branches: org.extra_branches || 0,
    trial_days: '',
    subscription_expires_at: org.subscription_expires_at
      ? org.subscription_expires_at.split('T')[0]
      : plus30,
    notes: org.admin_notes || '',
  });
  const [loading, setLoading] = useState(false);
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));

  // Auto-set 30-day expiry when switching to a paid plan
  const handlePlanChange = (newPlan) => {
    set('plan', newPlan);
    if (['basic', 'standard', 'pro'].includes(newPlan) && !org.subscription_expires_at) {
      set('subscription_expires_at', plus30);
    }
  };

  const setQuickExpiry = (days) => {
    const d = new Date(Date.now() + days * 86400000).toISOString().split('T')[0];
    set('subscription_expires_at', d);
  };

  const daysUntilExpiry = form.subscription_expires_at
    ? Math.ceil((new Date(form.subscription_expires_at) - new Date()) / 86400000)
    : null;

  const handleSave = async () => {
    setLoading(true);
    try {
      const payload = {
        plan: form.plan,
        extra_branches: parseInt(form.extra_branches) || 0,
        notes: form.notes,
      };
      if (form.trial_days && parseInt(form.trial_days) > 0)
        payload.trial_days = parseInt(form.trial_days);
      if (['basic', 'standard', 'pro'].includes(form.plan))
        payload.subscription_expires_at = form.subscription_expires_at || null;
      if (form.plan === 'founders')
        payload.subscription_expires_at = null;

      const r = await api.put(`/superadmin/organizations/${org.id}/subscription`, payload);
      onSaved(r.data);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Update failed');
    }
    setLoading(false);
  };

  const isPaidPlan = ['basic', 'standard', 'pro'].includes(form.plan);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />
      <div className="relative bg-slate-900 border border-slate-700 rounded-2xl p-6 w-full max-w-lg shadow-2xl max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-5">
          <div>
            <h3 className="text-white font-bold text-lg">Edit Subscription</h3>
            <p className="text-slate-400 text-sm">{org.name}</p>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-white"><X size={18} /></button>
        </div>

        <div className="space-y-5">
          {/* Plan selector */}
          <div>
            <label className="text-slate-400 text-xs font-semibold uppercase tracking-wider mb-2 block">Plan</label>
            <div className="grid grid-cols-3 gap-2">
              {[
                { key: 'trial',    label: 'Trial',    color: 'blue' },
                { key: 'basic',    label: 'Basic',    color: 'slate' },
                { key: 'standard', label: 'Standard', color: 'emerald' },
                { key: 'pro',      label: 'Pro',      color: 'indigo' },
                { key: 'founders', label: '★ Founders', color: 'amber', special: true },
                { key: 'suspended',label: 'Suspend',  color: 'red' },
              ].map(p => (
                <button key={p.key} onClick={() => handlePlanChange(p.key)}
                  className={`py-2.5 rounded-xl text-xs font-semibold border transition-all ${
                    form.plan === p.key
                      ? p.special
                        ? 'border-amber-400 bg-amber-400/15 text-amber-300'
                        : 'border-emerald-500 bg-emerald-500/10 text-emerald-300'
                      : 'border-slate-700 bg-slate-800 text-slate-400 hover:border-slate-600 hover:text-slate-300'
                  }`}>
                  {p.label}
                </button>
              ))}
            </div>

            {/* Founders info */}
            {form.plan === 'founders' && (
              <div className="mt-2 bg-amber-400/10 border border-amber-400/30 rounded-xl px-4 py-3 flex items-start gap-2">
                <span className="text-amber-300 text-lg">★</span>
                <div>
                  <p className="text-amber-300 text-xs font-semibold">Founders Plan — Lifetime Access</p>
                  <p className="text-amber-400/70 text-xs mt-0.5">All Pro features, never expires. Reserved for early adopters and special accounts.</p>
                </div>
              </div>
            )}
          </div>

          {/* Subscription expiry — paid plans only */}
          {isPaidPlan && (
            <div>
              <label className="text-slate-400 text-xs font-semibold uppercase tracking-wider mb-2 block">
                Subscription Expires On
              </label>

              {/* Quick buttons */}
              <div className="flex gap-1.5 mb-2 flex-wrap">
                {[
                  { label: '30 days', days: 30 },
                  { label: '60 days', days: 60 },
                  { label: '90 days', days: 90 },
                  { label: '6 months', days: 180 },
                  { label: '1 year', days: 365 },
                ].map(({ label, days }) => (
                  <button key={days} onClick={() => setQuickExpiry(days)}
                    className="text-xs px-3 py-1.5 bg-slate-800 hover:bg-emerald-600 text-slate-400 hover:text-white border border-slate-700 hover:border-emerald-500 rounded-lg transition-all">
                    +{label}
                  </button>
                ))}
              </div>

              <Input value={form.subscription_expires_at}
                onChange={e => set('subscription_expires_at', e.target.value)}
                type="date"
                className="bg-slate-800 border-slate-700 text-white h-10 text-sm" />

              {/* Expiry preview */}
              {daysUntilExpiry !== null && (
                <div className={`mt-2 text-xs flex items-center gap-1.5 ${
                  daysUntilExpiry <= 0 ? 'text-red-400' :
                  daysUntilExpiry <= 7 ? 'text-amber-400' : 'text-emerald-400'
                }`}>
                  <Clock size={12} />
                  {daysUntilExpiry <= 0
                    ? 'This date is in the past — plan will be in grace period'
                    : `Expires in ${daysUntilExpiry} days (${formatDate(form.subscription_expires_at)})`
                  }
                </div>
              )}
            </div>
          )}

          {/* Trial extension */}
          {form.plan === 'trial' && (
            <div>
              <label className="text-slate-400 text-xs font-semibold uppercase tracking-wider mb-2 block">
                Extend Trial By (days)
              </label>
              <div className="flex gap-1.5 mb-2">
                {[7, 14, 30].map(d => (
                  <button key={d} onClick={() => set('trial_days', d)}
                    className="text-xs px-3 py-1.5 bg-slate-800 hover:bg-blue-600/50 text-slate-400 hover:text-white border border-slate-700 rounded-lg transition-all">
                    +{d} days
                  </button>
                ))}
              </div>
              <CalcInput value={form.trial_days} onChange={(v) => set('trial_days', v)}
 placeholder="Custom days (e.g. 14)"
 className="bg-slate-800 border-slate-700 text-white placeholder:text-slate-600 h-9 text-sm" />
              {org.trial_ends_at && (
                <p className="text-slate-500 text-xs mt-1">
                  Current trial ends: {formatDate(org.trial_ends_at)}
                </p>
              )}
            </div>
          )}

          {/* Extra branches — not for founders */}
          {!['founders', 'suspended', 'trial'].includes(form.plan) && (
            <div>
              <label className="text-slate-400 text-xs font-semibold uppercase tracking-wider mb-2 block">
                Extra Branch Add-ons <span className="text-slate-600 normal-case font-normal">(₱1,500/mo each)</span>
              </label>
              <div className="flex items-center gap-3">
                <button onClick={() => set('extra_branches', Math.max(0, form.extra_branches - 1))}
                  className="w-9 h-9 rounded-lg bg-slate-800 border border-slate-700 text-slate-300 hover:text-white flex items-center justify-center">
                  <Minus size={14} />
                </button>
                <span className="text-white font-bold text-xl w-8 text-center">{form.extra_branches}</span>
                <button onClick={() => set('extra_branches', form.extra_branches + 1)}
                  className="w-9 h-9 rounded-lg bg-slate-800 border border-slate-700 text-slate-300 hover:text-white flex items-center justify-center">
                  <Plus size={14} />
                </button>
                <span className="text-slate-500 text-xs">
                  = {(PLAN_LIMITS_MAP[form.plan] || 1) + form.extra_branches} total branches
                </span>
              </div>
            </div>
          )}

          {/* Notes */}
          <div>
            <label className="text-slate-400 text-xs font-semibold uppercase tracking-wider mb-2 block">Admin Notes</label>
            <Input value={form.notes} onChange={e => set('notes', e.target.value)}
              placeholder="Internal notes (not visible to customer)"
              className="bg-slate-800 border-slate-700 text-white placeholder:text-slate-600 h-9 text-sm" />
          </div>
        </div>

        <div className="flex gap-3 mt-6">
          <Button onClick={handleSave} disabled={loading}
            className="flex-1 bg-emerald-600 hover:bg-emerald-500 text-white font-semibold h-10">
            {loading ? 'Saving...' : 'Save Changes'}
          </Button>
          <Button variant="outline" onClick={onClose}
            className="border-slate-700 text-slate-300 hover:text-white hover:bg-slate-800 h-10">
            Cancel
          </Button>
        </div>
      </div>
    </div>
  );
}

const PLAN_LIMITS_MAP = { trial: 5, basic: 1, standard: 2, pro: 5, founders: 0, suspended: 0 };

/* ── Feature Flags Panel ────────────────────────────────────────────────── */
function FeatureFlagsPanel() {
  const [defs, setDefs] = useState([]);
  const [flags, setFlags] = useState({ basic: {}, standard: {}, pro: {} });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [hasChanges, setHasChanges] = useState(false);

  useEffect(() => {
    api.get('/superadmin/settings/features').then(r => {
      setDefs(r.data.feature_definitions || []);
      setFlags(r.data.flags || { basic: {}, standard: {}, pro: {} });
      setLastUpdated(r.data.last_updated);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  const toggle = (plan, featureKey) => {
    if (plan === 'trial') return; // Trial always mirrors Pro - read-only
    setFlags(prev => ({
      ...prev,
      [plan]: { ...prev[plan], [featureKey]: !prev[plan]?.[featureKey] },
    }));
    setHasChanges(true);
  };

  const setAll = (plan, value) => {
    if (plan === 'trial') return;
    const locked = defs.filter(d => d.locked_on?.includes(plan)).map(d => d.key);
    setFlags(prev => ({
      ...prev,
      [plan]: {
        ...prev[plan],
        ...Object.fromEntries(defs.map(d => [d.key, locked.includes(d.key) ? true : value])),
      },
    }));
    setHasChanges(true);
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.put('/superadmin/settings/features', { flags });
      setHasChanges(false);
      setLastUpdated(new Date().toISOString());
      toast.success('Feature flags saved! Landing page updated live.');
    } catch {
      toast.error('Failed to save feature flags');
    }
    setSaving(false);
  };

  const categories = [...new Set(defs.map(d => d.category))];
  const PLANS = [
    { key: 'basic', label: 'Basic', color: '#64748b', price: '₱1,500' },
    { key: 'standard', label: 'Standard', color: '#10b981', price: '₱4,000' },
    { key: 'pro', label: 'Pro', color: '#6366f1', price: '₱7,500' },
  ];

  if (loading) return <div className="text-center py-12 text-slate-500">Loading feature flags...</div>;

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h2 className="text-white font-bold text-lg">Feature Flags</h2>
          <p className="text-slate-400 text-sm">
            Control which features are available per plan. Changes are live immediately on the pricing page.
            {lastUpdated && <span className="text-slate-600 ml-2">Last saved: {formatDateTime(lastUpdated)}</span>}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {hasChanges && (
            <span className="text-amber-400 text-xs flex items-center gap-1">
              <AlertTriangle size={12} /> Unsaved changes
            </span>
          )}
          <Button onClick={handleSave} disabled={saving || !hasChanges}
            className={`gap-2 font-semibold ${hasChanges ? 'bg-emerald-600 hover:bg-emerald-500 text-white' : 'bg-slate-700 text-slate-400 cursor-not-allowed'}`}>
            <Save size={14} /> {saving ? 'Saving...' : 'Save & Publish'}
          </Button>
        </div>
      </div>

      {/* Trial + founders note */}
      <div className="bg-blue-500/5 border border-blue-500/20 rounded-xl px-4 py-3 text-blue-300 text-xs flex items-start gap-2">
        <Shield size={14} className="mt-0.5 shrink-0" />
        <span>
          <strong>Trial</strong> and <strong className="text-amber-300">★ Founders</strong> plans always mirror Pro (all features unlocked) — this cannot be changed.
        </span>
      </div>

      {/* Feature table */}
      <div className="bg-slate-800/20 border border-slate-700/40 rounded-2xl overflow-hidden">
        {/* Column headers */}
        <div className="grid border-b border-slate-700/40" style={{ gridTemplateColumns: '1fr repeat(3, 160px)' }}>
          <div className="px-5 py-4 text-slate-400 text-xs font-semibold uppercase tracking-wider">Feature</div>
          {PLANS.map(p => (
            <div key={p.key} className="px-4 py-4 text-center border-l border-slate-700/30">
              <div className="font-bold text-sm" style={{ color: p.color }}>{p.label}</div>
              <div className="text-slate-500 text-xs">{p.price}/mo</div>
              <div className="flex gap-1.5 justify-center mt-2">
                <button onClick={() => setAll(p.key, true)}
                  className="text-xs px-2 py-0.5 bg-slate-700 hover:bg-emerald-600 text-slate-400 hover:text-white rounded transition-colors">
                  All On
                </button>
                <button onClick={() => setAll(p.key, false)}
                  className="text-xs px-2 py-0.5 bg-slate-700 hover:bg-red-600 text-slate-400 hover:text-white rounded transition-colors">
                  All Off
                </button>
              </div>
            </div>
          ))}
        </div>

        {/* Features grouped by category */}
        {categories.map(cat => (
          <div key={cat}>
            {/* Category header */}
            <div className="px-5 py-2.5 bg-slate-900/40 border-b border-slate-700/30">
              <span className="text-slate-400 text-xs font-semibold uppercase tracking-widest">{cat}</span>
            </div>
            {defs.filter(d => d.category === cat).map((feature, idx, arr) => {
              const isLast = idx === arr.length - 1;
              return (
                <div key={feature.key}
                  className={`grid items-center hover:bg-slate-800/30 transition-colors ${!isLast ? 'border-b border-slate-700/20' : ''}`}
                  style={{ gridTemplateColumns: '1fr repeat(3, 160px)' }}>
                  {/* Feature info */}
                  <div className="px-5 py-3.5">
                    <div className="text-slate-200 text-sm font-medium">{feature.name}</div>
                    <div className="text-slate-500 text-xs mt-0.5 leading-relaxed">{feature.description}</div>
                  </div>
                  {/* Toggle per plan */}
                  {PLANS.map(p => {
                    const isLocked = feature.locked_on?.includes(p.key);
                    const enabled = isLocked ? true : (flags[p.key]?.[feature.key] ?? false);
                    return (
                      <div key={p.key} className="px-4 py-3.5 flex justify-center border-l border-slate-700/20">
                        <button
                          data-testid={`toggle-${p.key}-${feature.key}`}
                          onClick={() => !isLocked && toggle(p.key, feature.key)}
                          disabled={isLocked}
                          title={isLocked ? 'This feature is always included' : (enabled ? 'Click to disable' : 'Click to enable')}
                          className={`relative w-11 h-6 rounded-full transition-all focus:outline-none ${
                            isLocked
                              ? 'opacity-50 cursor-not-allowed'
                              : 'cursor-pointer'
                          } ${enabled ? 'bg-emerald-500' : 'bg-slate-700'}`}
                        >
                          <div className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${enabled ? 'translate-x-5' : 'translate-x-0.5'}`} />
                        </button>
                        {isLocked && <span className="ml-2 text-xs text-slate-600">always</span>}
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>
        ))}
      </div>

      {/* Preview note */}
      <div className="bg-slate-800/30 border border-slate-700/40 rounded-xl px-4 py-3 text-slate-400 text-xs">
        <strong className="text-slate-300">How it works:</strong> When you save, the pricing page ({window.location.origin}) and feature comparison table update instantly — no code changes needed.
        Customers on active plans see the features their plan currently has. Changes don't revoke access mid-subscription.
      </div>
    </div>
  );
}

/* ── Payment Settings Panel ─────────────────────────────────────────────── */
function PaymentSettingsPanel({ settings, setSettings, saving, setSaving }) {
  const fileRefs = { gcash: useRef(), maya: useRef(), bank: useRef(), paypal: useRef() };

  const update = (method, key, value) => {
    setSettings(prev => ({
      ...prev,
      [method]: { ...(prev[method] || {}), [key]: value }
    }));
  };

  const handleFileUpload = (method, e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) { toast.error('File too large. Max 2MB.'); return; }
    const reader = new FileReader();
    reader.onload = (ev) => update(method, 'qr_base64', ev.target.result);
    reader.readAsDataURL(file);
  };

  const removeQR = (method) => update(method, 'qr_base64', '');

  const handleSave = async () => {
    setSaving(true);
    try {
      await api.put('/superadmin/settings/payment', { value: settings });
      toast.success('Payment settings saved!');
    } catch {
      toast.error('Failed to save settings');
    }
    setSaving(false);
  };

  const METHODS = [
    { key: 'gcash', label: 'GCash', icon: '💚', color: 'emerald', fields: [
      { field: 'number', label: 'GCash Number', placeholder: '09XX-XXX-XXXX' },
      { field: 'account_name', label: 'Account Name', placeholder: 'Full name on account' },
    ]},
    { key: 'maya', label: 'Maya', icon: '💜', color: 'purple', fields: [
      { field: 'number', label: 'Maya Number', placeholder: '09XX-XXX-XXXX' },
      { field: 'account_name', label: 'Account Name', placeholder: 'Full name on account' },
    ]},
    { key: 'bank', label: 'Bank Transfer', icon: '🏦', color: 'blue', fields: [
      { field: 'bank_name', label: 'Bank Name', placeholder: 'e.g. BDO, BPI, UnionBank' },
      { field: 'account_number', label: 'Account Number', placeholder: 'XXXX-XXXX-XXXX' },
      { field: 'account_name', label: 'Account Name', placeholder: 'Business name on account' },
    ]},
    { key: 'paypal', label: 'PayPal', icon: '🔵', color: 'indigo', fields: [
      { field: 'email', label: 'PayPal Email', placeholder: 'paypal@email.com' },
      { field: 'link', label: 'PayPal.me Link', placeholder: 'https://paypal.me/yourlink' },
    ]},
  ];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-white font-bold text-lg">Payment Methods</h2>
          <p className="text-slate-400 text-sm">Configure how customers pay for subscriptions. QR codes appear on the Upgrade page.</p>
        </div>
        <Button onClick={handleSave} disabled={saving}
          className="bg-emerald-600 hover:bg-emerald-500 text-white font-semibold gap-2">
          <Save size={14} /> {saving ? 'Saving...' : 'Save All'}
        </Button>
      </div>

      <div className="grid md:grid-cols-2 gap-4">
        {METHODS.map(({ key, label, icon, fields }) => (
          <div key={key} className="bg-slate-800/30 border border-slate-700/40 rounded-2xl p-5 space-y-4">
            <div className="flex items-center gap-2">
              <span className="text-2xl">{icon}</span>
              <h3 className="text-white font-semibold">{label}</h3>
            </div>

            {/* Text fields */}
            {fields.map(({ field, label: fLabel, placeholder }) => (
              <div key={field}>
                <label className="text-slate-400 text-xs font-medium mb-1.5 block">{fLabel}</label>
                <Input
                  value={settings[key]?.[field] || ''}
                  onChange={e => update(key, field, e.target.value)}
                  placeholder={placeholder}
                  className="bg-slate-900 border-slate-700 text-white placeholder:text-slate-600 h-9 text-sm"
                />
              </div>
            ))}

            {/* QR code upload */}
            <div>
              <label className="text-slate-400 text-xs font-medium mb-2 block">QR Code Image</label>
              {settings[key]?.qr_base64 ? (
                <div className="relative inline-block">
                  <img src={settings[key].qr_base64} alt={`${label} QR`}
                    className="w-32 h-32 rounded-xl border border-slate-600 object-contain bg-white p-1" />
                  <button onClick={() => removeQR(key)}
                    className="absolute -top-2 -right-2 w-6 h-6 bg-red-500 hover:bg-red-600 rounded-full flex items-center justify-center">
                    <Trash2 size={11} className="text-white" />
                  </button>
                  <button onClick={() => fileRefs[key]?.current?.click()}
                    className="absolute -bottom-2 left-1/2 -translate-x-1/2 bg-slate-700 hover:bg-slate-600 text-xs text-white px-2 py-0.5 rounded-full whitespace-nowrap">
                    Change
                  </button>
                </div>
              ) : (
                <button onClick={() => fileRefs[key]?.current?.click()}
                  className="w-32 h-32 border-2 border-dashed border-slate-700 hover:border-emerald-500/50 rounded-xl flex flex-col items-center justify-center gap-2 text-slate-500 hover:text-emerald-400 transition-colors group">
                  <Upload size={20} />
                  <span className="text-xs">Upload QR</span>
                </button>
              )}
              <input ref={fileRefs[key]} type="file" accept="image/*" className="hidden"
                onChange={e => handleFileUpload(key, e)} />
              <p className="text-slate-600 text-xs mt-2">PNG/JPG · Max 2MB</p>
            </div>
          </div>
        ))}
      </div>

      <div className="bg-blue-500/5 border border-blue-500/20 rounded-xl p-4">
        <p className="text-blue-300 text-sm font-medium mb-1">How this works</p>
        <p className="text-blue-400/70 text-xs leading-relaxed">
          Customers see these payment details when they click "Upgrade" in the app. They send payment manually,
          then contact you for activation. You then activate their plan from this admin panel.
        </p>
      </div>
    </div>
  );
}
