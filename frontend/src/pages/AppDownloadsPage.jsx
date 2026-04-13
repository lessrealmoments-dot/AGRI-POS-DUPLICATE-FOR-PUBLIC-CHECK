import { useState, useEffect, useRef } from 'react';
import { api, useAuth } from '../contexts/AuthContext';
import { toast } from 'sonner';
import { Download, Upload, Smartphone, MessageSquare, CheckCircle, Clock, RefreshCw, Trash2, ChevronDown, ChevronUp, Info } from 'lucide-react';
import { Button } from '../components/ui/button';
import { Badge } from '../components/ui/badge';

const APPS_STATIC = {
  'agrisms-gateway': {
    slug: 'agrisms-gateway',
    name: 'AgriSMS Gateway 2.0',
    tagline: 'Turn a dedicated Android phone into a secure SMS bridge between your SIM and AgriBooks.',
    package: 'com.agrism.gateway',
    min_android: 'Android 8.0+ (API 26)',
    icon_color: '#16a34a',
    icon: MessageSquare,
    description: `AgriSMS Gateway 2.0 is the companion Android app that keeps your store phone in sync with the AgriBooks web dashboard. Install it on a dedicated SIM phone, sign in with your server URL and credentials, and keep the gateway service running in the foreground.

Outbound SMS jobs queued from the web browser are delivered as real text messages through your SIM. When a customer replies, the message is captured and synced back to AgriBooks for full conversation threads — all visible from the Messages page.`,
    features: [
      'Browser-driven outbound SMS via server queue — real SMS from your SIM',
      'Inbound reply capture & sync for full conversation threads in browser',
      'Foreground gateway service with persistent notification + boot restart',
      'Adaptive polling: ~15s while charging, ~60s on battery',
      'Room-backed local cache for offline-tolerant buffering and retry',
      'JWT login with saved server URL, auto re-login on 401',
      'Dashboard with sent/failed/pending counters and rolling activity log',
      'Conversations list with search and bubble thread view',
      'Default SMS app detection prompts + battery optimization helper',
      'Optional remote log batching for live debugging in browser',
    ],
    specs: [
      { label: 'Package', value: 'com.agrism.gateway' },
      { label: 'Language', value: 'Java' },
      { label: 'Min Android', value: '8.0 (API 26)' },
      { label: 'Target SDK', value: '34' },
      { label: 'Networking', value: 'OkHttp + JSON' },
      { label: 'Local DB', value: 'Room 2.6.x' },
      { label: 'Poll interval', value: '~15s charging / ~60s battery' },
    ],
  },
  'agrismart-terminal': {
    slug: 'agrismart-terminal',
    name: 'AgriSmart Terminal',
    tagline: 'The official AgriBooks Android app for in-store terminals — native thermal printing, scanner, and camera QR support.',
    package: 'com.agribooks.terminal',
    min_android: 'Android 8.0+',
    icon_color: '#1d4ed8',
    icon: Smartphone,
    description: `AgriSmart Terminal is the official AgriBooks app for dedicated handheld terminals (e.g. Senraise H10P). It is a Capacitor shell that loads the live AgriBooks web app at agri-books.com — business logic, screens, and features ship through the website first while the APK provides secure, device-integrated hardware access.

Most fixes and features reach terminals as soon as the website is deployed without requiring a Play Store update. Only native changes (printer plugin, permissions, Capacitor upgrades) need a new APK.`,
    features: [
      'Full AgriBooks terminal experience in a secure WebView',
      'Store pairing and device identity binding per branch session',
      'Sales, checkout, receipts, and document workflows',
      'QR-driven warehouse actions: stock release, payments, transfers',
      'Native thermal printing via H10P SDK (HTML → bitmap → print head)',
      'Camera QR scanning and hardware HID barcode scanner support',
      'Live updates — most changes roll out through the website automatically',
      'IndexedDB Smart Sync: instant load from cache on every open',
      'Offline-tolerant with background delta sync every 5 minutes',
    ],
    specs: [
      { label: 'Platform', value: 'Capacitor 6 (Android)' },
      { label: 'App ID', value: 'com.agribooks.terminal' },
      { label: 'Web content', value: 'https://agri-books.com (live)' },
      { label: 'Printing', value: 'H10P native SDK plugin' },
      { label: 'Camera', value: 'QR + payment proof flows' },
      { label: 'Scanner', value: 'HID keyboard wedge (H10P)' },
    ],
  },
};

function formatBytes(bytes) {
  if (!bytes) return '—';
  const mb = bytes / (1024 * 1024);
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${(bytes / 1024).toFixed(0)} KB`;
}

function formatDate(iso) {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleDateString('en-PH', { year: 'numeric', month: 'short', day: 'numeric' }); }
  catch { return iso; }
}

function AppCard({ app, isSuperAdmin, onRefresh }) {
  const static_info = APPS_STATIC[app.slug] || {};
  const Icon = static_info.icon || Smartphone;
  const [expanded, setExpanded] = useState(false);
  const [specsOpen, setSpecsOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [showUpload, setShowUpload] = useState(false);
  const [version, setVersion] = useState('');
  const [changelog, setChangelog] = useState('');
  const fileRef = useRef();

  const handleDownload = async () => {
    setDownloading(true);
    try {
      const res = await api.get(`/app-downloads/${app.slug}/download-url`);
      const a = document.createElement('a');
      a.href = res.data.url;
      a.download = res.data.filename || `${app.slug}.apk`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      toast.success('Download started');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Download failed');
    }
    setDownloading(false);
  };

  const handleUpload = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) { toast.error('Select an APK file'); return; }
    if (!version.trim()) { toast.error('Enter a version number'); return; }
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('version', version.trim());
      fd.append('changelog', changelog.trim());
      await api.post(`/app-downloads/${app.slug}/upload`, fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      toast.success(`${static_info.name} uploaded successfully`);
      setShowUpload(false);
      setVersion(''); setChangelog('');
      if (fileRef.current) fileRef.current.value = '';
      onRefresh();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Upload failed');
    }
    setUploading(false);
  };

  const handleDelete = async () => {
    if (!window.confirm(`Remove the current APK for ${static_info.name}?`)) return;
    setDeleting(true);
    try {
      await api.delete(`/app-downloads/${app.slug}`);
      toast.success('APK removed');
      onRefresh();
    } catch (e) { toast.error('Failed to remove'); }
    setDeleting(false);
  };

  return (
    <div className="rounded-2xl border border-slate-700/60 bg-slate-800/50 backdrop-blur overflow-hidden" data-testid={`app-card-${app.slug}`}>
      {/* Header */}
      <div className="p-6 flex items-start gap-4">
        <div className="w-14 h-14 rounded-2xl flex items-center justify-center shrink-0" style={{ backgroundColor: static_info.icon_color + '22', border: `1.5px solid ${static_info.icon_color}44` }}>
          <Icon size={26} style={{ color: static_info.icon_color }} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-3 flex-wrap">
            <div>
              <h2 className="text-white font-bold text-lg leading-tight" style={{ fontFamily: 'Manrope' }}>{static_info.name}</h2>
              <p className="text-slate-400 text-xs mt-0.5 font-mono">{static_info.package}</p>
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              {app.has_apk ? (
                <Badge className="bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 text-xs">
                  v{app.version}
                </Badge>
              ) : (
                <Badge className="bg-slate-600/50 text-slate-400 border border-slate-600 text-xs">
                  No APK yet
                </Badge>
              )}
              <Badge className="bg-slate-700/50 text-slate-400 border border-slate-600/50 text-xs">
                {static_info.min_android}
              </Badge>
            </div>
          </div>
          <p className="text-slate-300 text-sm mt-2 leading-relaxed">{static_info.tagline}</p>
        </div>
      </div>

      {/* Metadata row */}
      {app.has_apk && (
        <div className="px-6 pb-3 flex flex-wrap gap-4 text-xs text-slate-500">
          <span className="flex items-center gap-1"><Clock size={11} /> {formatDate(app.upload_date)}</span>
          <span>{formatBytes(app.file_size)}</span>
          <span className="flex items-center gap-1"><Download size={11} /> {app.download_count || 0} downloads</span>
          {app.changelog && <span className="text-slate-400 italic truncate max-w-xs">"{app.changelog}"</span>}
        </div>
      )}

      {/* Description expand */}
      {expanded && (
        <div className="px-6 pb-4 space-y-4">
          <p className="text-slate-400 text-sm leading-relaxed whitespace-pre-line">{static_info.description}</p>
          <div>
            <p className="text-slate-300 text-xs font-semibold uppercase tracking-wider mb-2">Key Features</p>
            <ul className="space-y-1">
              {(static_info.features || []).map((f, i) => (
                <li key={i} className="flex items-start gap-2 text-slate-400 text-xs">
                  <CheckCircle size={12} className="text-emerald-500 mt-0.5 shrink-0" /> {f}
                </li>
              ))}
            </ul>
          </div>
          {specsOpen && (
            <div>
              <p className="text-slate-300 text-xs font-semibold uppercase tracking-wider mb-2">Technical Details</p>
              <div className="grid grid-cols-2 gap-x-6 gap-y-1.5">
                {(static_info.specs || []).map((s, i) => (
                  <div key={i} className="flex gap-2 text-xs">
                    <span className="text-slate-500 w-24 shrink-0">{s.label}</span>
                    <span className="text-slate-300 font-mono">{s.value}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
          {static_info.specs?.length > 0 && (
            <button onClick={() => setSpecsOpen(v => !v)} className="text-xs text-slate-500 hover:text-slate-300 flex items-center gap-1">
              {specsOpen ? <><ChevronUp size={12}/> Hide specs</> : <><Info size={12}/> Technical specs</>}
            </button>
          )}
        </div>
      )}

      {/* Actions row */}
      <div className="px-6 pb-5 flex flex-wrap items-center gap-3">
        {app.has_apk ? (
          <Button onClick={handleDownload} disabled={downloading} className="bg-emerald-600 hover:bg-emerald-700 text-white h-9 px-4 text-sm gap-2" data-testid={`download-btn-${app.slug}`}>
            {downloading ? <RefreshCw size={14} className="animate-spin" /> : <Download size={14} />}
            {downloading ? 'Preparing…' : `Download APK`}
          </Button>
        ) : (
          <Button disabled className="bg-slate-700 text-slate-500 cursor-not-allowed h-9 px-4 text-sm gap-2">
            <Download size={14} /> Not Available Yet
          </Button>
        )}

        <button onClick={() => setExpanded(v => !v)} className="text-slate-400 hover:text-slate-200 text-xs flex items-center gap-1 transition-colors">
          {expanded ? <><ChevronUp size={13} /> Less</> : <><ChevronDown size={13} /> About this app</>}
        </button>

        {/* Super admin controls */}
        {isSuperAdmin && (
          <div className="ml-auto flex gap-2">
            <Button variant="outline" size="sm" onClick={() => setShowUpload(v => !v)}
              className="h-8 text-xs border-slate-600 text-slate-300 hover:bg-slate-700 gap-1.5" data-testid={`upload-btn-${app.slug}`}>
              <Upload size={12} /> {app.has_apk ? 'Update APK' : 'Upload APK'}
            </Button>
            {app.has_apk && (
              <Button variant="ghost" size="sm" onClick={handleDelete} disabled={deleting}
                className="h-8 text-xs text-red-400 hover:text-red-300 hover:bg-red-900/20" data-testid={`delete-btn-${app.slug}`}>
                <Trash2 size={12} />
              </Button>
            )}
          </div>
        )}
      </div>

      {/* Upload form (super admin) */}
      {isSuperAdmin && showUpload && (
        <div className="mx-5 mb-5 p-4 rounded-xl border border-slate-600 bg-slate-900/60 space-y-3">
          <p className="text-slate-300 text-xs font-semibold uppercase tracking-wider">Upload New APK</p>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-slate-400 block mb-1">Version *</label>
              <input
                value={version}
                onChange={e => setVersion(e.target.value)}
                placeholder="e.g. 2.0.1"
                className="w-full h-8 bg-slate-800 border border-slate-600 rounded-lg px-3 text-sm text-white placeholder:text-slate-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
                data-testid={`version-input-${app.slug}`}
              />
            </div>
            <div>
              <label className="text-xs text-slate-400 block mb-1">Changelog (optional)</label>
              <input
                value={changelog}
                onChange={e => setChangelog(e.target.value)}
                placeholder="What changed..."
                className="w-full h-8 bg-slate-800 border border-slate-600 rounded-lg px-3 text-sm text-white placeholder:text-slate-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
              />
            </div>
          </div>
          <div>
            <label className="text-xs text-slate-400 block mb-1">APK File *</label>
            <input
              ref={fileRef}
              type="file"
              accept=".apk,application/vnd.android.package-archive,application/octet-stream"
              className="w-full text-xs text-slate-400 file:mr-3 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:bg-emerald-700 file:text-emerald-100 file:text-xs file:cursor-pointer hover:file:bg-emerald-600"
              data-testid={`file-input-${app.slug}`}
            />
            <p className="text-[10px] text-slate-500 mt-1">Max 200 MB. APK will be stored securely in Cloudflare R2.</p>
          </div>
          <div className="flex gap-2 pt-1">
            <Button size="sm" onClick={handleUpload} disabled={uploading}
              className="h-8 bg-emerald-600 hover:bg-emerald-700 text-white text-xs gap-1.5" data-testid={`confirm-upload-${app.slug}`}>
              {uploading ? <><RefreshCw size={11} className="animate-spin" /> Uploading…</> : <><Upload size={11} /> Upload</>}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setShowUpload(false)} className="h-8 text-slate-400 text-xs">Cancel</Button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function AppDownloadsPage() {
  const { user } = useAuth();
  const isSuperAdmin = user?.is_super_admin;
  const isCompanyAdmin = isSuperAdmin || ['admin', 'owner'].includes(user?.role);
  const [apps, setApps] = useState([]);
  const [loading, setLoading] = useState(true);

  const loadApps = async () => {
    if (!isCompanyAdmin) return;
    setLoading(true);
    try {
      const res = await api.get('/app-downloads');
      setApps(res.data || []);
    } catch {
      toast.error('Failed to load downloads');
    }
    setLoading(false);
  };

  useEffect(() => { loadApps(); }, []); // eslint-disable-line

  if (!isCompanyAdmin) {
    return (
      <div className="min-h-screen bg-[#0a0f1a] flex items-center justify-center">
        <div className="text-center">
          <Smartphone size={40} className="mx-auto mb-4 text-slate-600" />
          <p className="text-white font-semibold text-lg mb-1">Access Restricted</p>
          <p className="text-slate-500 text-sm">App downloads are available to company admins only.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#0a0f1a]">
      {/* Hero */}
      <div className="relative overflow-hidden border-b border-slate-800">
        <div className="absolute inset-0 bg-gradient-to-br from-[#0d1f12] via-[#0a0f1a] to-[#0a0f1a]" />
        <div className="relative max-w-4xl mx-auto px-6 py-12">
          <div className="flex items-center gap-2 mb-4">
            <div className="w-8 h-8 rounded-xl bg-emerald-500/20 border border-emerald-500/30 flex items-center justify-center">
              <Smartphone size={16} className="text-emerald-400" />
            </div>
            <span className="text-emerald-400 text-xs font-mono uppercase tracking-widest">AgriBooks Mobile</span>
          </div>
          <h1 className="text-white text-3xl sm:text-4xl font-bold tracking-tight mb-3" style={{ fontFamily: 'Manrope' }}>
            Download Center
          </h1>
          <p className="text-slate-400 text-base max-w-xl leading-relaxed">
            Official Android applications for AgriBooks terminal operations and SMS gateway.
            Download and install on dedicated store devices.
          </p>
          {isSuperAdmin && (
            <div className="mt-4 inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-violet-500/10 border border-violet-500/30">
              <div className="w-1.5 h-1.5 rounded-full bg-violet-400" />
              <span className="text-violet-300 text-xs font-medium">Super Admin — APK upload enabled</span>
            </div>
          )}
        </div>
      </div>

      {/* App cards */}
      <div className="max-w-4xl mx-auto px-6 py-8 space-y-5">
        {loading ? (
          <div className="text-center py-16 text-slate-500 text-sm">
            <RefreshCw size={20} className="animate-spin mx-auto mb-3 text-slate-600" />
            Loading apps…
          </div>
        ) : (
          apps.map(app => (
            <AppCard
              key={app.slug}
              app={app}
              isSuperAdmin={isSuperAdmin}
              onRefresh={loadApps}
            />
          ))
        )}
      </div>

      {/* Footer note */}
      <div className="max-w-4xl mx-auto px-6 pb-10 text-center">
        <p className="text-slate-600 text-xs leading-relaxed">
          APKs are served from secure Cloudflare R2 storage. Download links expire after 5 minutes.
          Contact your system administrator if you experience installation issues.
        </p>
      </div>
    </div>
  );
}
