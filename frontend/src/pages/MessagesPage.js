import { useState, useEffect, useCallback, useRef } from 'react';
import { useAuth, api } from '../contexts/AuthContext';
import { formatPHP } from '../lib/utils';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Badge } from '../components/ui/badge';
import { Card, CardContent } from '../components/ui/card';
import TeamSmsRemindersCard from '../components/sms/TeamSmsRemindersCard';
import { Separator } from '../components/ui/separator';
import { ScrollArea } from '../components/ui/scroll-area';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '../components/ui/dialog';
import {
  MessageSquare, Send, Clock, AlertTriangle, SkipForward,
  RefreshCw, Users, Filter, Edit3, Check, X, Search,
  Megaphone, Loader2, Settings, CheckCircle2, XCircle,
  ChevronDown, ChevronUp, Phone, FileText, UserPlus, Activity, Wifi,
  BellRing
} from 'lucide-react';
import { formatDate, formatTime } from '../lib/dateFormat';
import { toast } from 'sonner';
import CalcInput from '../components/CalcInput';

const STATUS_CONFIG = {
  pending: { label: 'Pending', icon: Clock, cls: 'bg-amber-100 text-amber-700', dotCls: 'bg-amber-400' },
  sent: { label: 'Sent', icon: CheckCircle2, cls: 'bg-emerald-100 text-emerald-700', dotCls: 'bg-emerald-400' },
  failed: { label: 'Failed', icon: XCircle, cls: 'bg-red-100 text-red-700', dotCls: 'bg-red-400' },
  skipped: { label: 'Skipped', icon: SkipForward, cls: 'bg-slate-100 text-slate-500', dotCls: 'bg-slate-400' },
};

const TEMPLATE_BADGE = {
  credit_new: 'bg-blue-100 text-blue-700',
  reminder_15day: 'bg-amber-100 text-amber-700',
  reminder_7day: 'bg-orange-100 text-orange-700',
  overdue_notice: 'bg-red-100 text-red-700',
  payment_received: 'bg-emerald-100 text-emerald-700',
  charge_applied: 'bg-purple-100 text-purple-700',
  delivery_ready: 'bg-teal-100 text-teal-700',
  promo_blast: 'bg-pink-100 text-pink-700',
  monthly_summary: 'bg-indigo-100 text-indigo-700',
  credit_reminder_blast: 'bg-rose-100 text-rose-700',
  custom: 'bg-slate-100 text-slate-600',
};

// ── Gateway Log helpers ───────────────────────────────────────────────────────
const LOG_LEVEL_BG = {
  INFO:  'bg-slate-700 text-slate-300',
  DEBUG: 'bg-violet-900 text-violet-300',
  WARN:  'bg-amber-900 text-amber-300',
  ERROR: 'bg-red-900 text-red-400',
};
const LOG_MSG_COLOR = {
  INFO:  'text-slate-300',
  DEBUG: 'text-violet-300',
  WARN:  'text-amber-300',
  ERROR: 'text-red-400',
};
const EVENT_COLOR = {
  sent:            'text-emerald-400',
  send_queued:     'text-sky-400',
  received:        'text-blue-400',
  device_sent:     'text-teal-400',
  poll:            'text-slate-500',
  boot:            'text-violet-400',
  observer_start:  'text-emerald-500',
  observer_stop:   'text-red-400',
  token_loaded:    'text-cyan-400',
  sync:            'text-teal-400',
  db_error:        'text-red-400',
  error:           'text-red-400',
  failed:          'text-red-400',
};

function GatewayLogLine({ log }) {
  const ts = new Date(log.created_at);
  const timeStr = ts.toLocaleTimeString('en-PH', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const dateStr = ts.toLocaleDateString('en-PH', { month: '2-digit', day: '2-digit' });
  const msgColor = LOG_MSG_COLOR[log.level] || 'text-slate-300';
  const levelBg  = LOG_LEVEL_BG[log.level]  || 'bg-slate-700 text-slate-300';
  const evColor  = EVENT_COLOR[log.event_type] || 'text-slate-400';
  return (
    <div className="flex items-start gap-2 py-0.5 hover:bg-white/5 px-2 rounded group min-w-0">
      <span className="text-slate-600 shrink-0 select-none text-[10px] tabular-nums">{dateStr} {timeStr}</span>
      <span className={`shrink-0 px-1.5 py-px rounded text-[9px] font-bold ${levelBg}`}>{log.level}</span>
      {log.event_type && log.event_type !== 'custom' && (
        <span className={`shrink-0 ${evColor} text-[10px] font-mono`}>[{log.event_type}]</span>
      )}
      {log.phone && <span className="text-slate-500 shrink-0 text-[10px]">{log.phone}</span>}
      <span className={`${msgColor} leading-relaxed break-all text-[11px]`}>{log.message}</span>
    </div>
  );
}

export default function MessagesPage() {
  const { user, currentBranch, branches } = useAuth();
  const [activeTab, setActiveTab] = useState('conversations');

  // Queue state
  const [statusFilter, setStatusFilter] = useState('pending');
  const [queue, setQueue] = useState([]);
  const [queueTotal, setQueueTotal] = useState(0);
  const [stats, setStats] = useState({ pending: 0, sent: 0, failed: 0, skipped: 0, total: 0 });
  const [loading, setLoading] = useState(false);

  // Company name for signature hint
  const [companyName, setCompanyName] = useState('');

  // Conversations state
  const [convoSection, setConvoSection] = useState('customers'); // 'customers' | 'unknown' (admin only)
  const [unknownCount, setUnknownCount] = useState(0);
  const [conversations, setConversations] = useState([]);
  const [activeConvo, setActiveConvo] = useState(null);
  const [thread, setThread] = useState([]);
  const [threadLoading, setThreadLoading] = useState(false);
  const [replyMsg, setReplyMsg] = useState('');
  const [replying, setReplying] = useState(false);
  const [convoSearch, setConvoSearch] = useState('');

  // Assign-to-customer modal state
  const [assignModal, setAssignModal] = useState(false);
  const [assignSearch, setAssignSearch] = useState('');
  const [assignResults, setAssignResults] = useState([]);
  const [assigning, setAssigning] = useState(false);

  // Compose state
  const [customers, setCustomers] = useState([]);
  const [custSearch, setCustSearch] = useState('');
  const [custDropdownOpen, setCustDropdownOpen] = useState(false);
  const [selectedCust, setSelectedCust] = useState(null);
  const [composeMsg, setComposeMsg] = useState('');
  const [sending, setSending] = useState(false);

  // Blast state
  const [blastMsg, setBlastMsg] = useState('');
  const [blastMinBal, setBlastMinBal] = useState('');
  const [blasting, setBlasting] = useState(false);

  // Credit blast state
  const [creditBlastMinBal, setCreditBlastMinBal] = useState('');
  const [creditBlastPreview, setCreditBlastPreview] = useState(null);
  const [creditBlastLoading, setCreditBlastLoading] = useState(false);
  const [creditBlastSending, setCreditBlastSending] = useState(false);

  // Templates state
  const [templates, setTemplates] = useState([]);
  const [editingId, setEditingId] = useState(null);
  const [editBody, setEditBody] = useState('');

  // Settings state
  const [smsSettings, setSmsSettings] = useState([]);
  const [collectionRecipients, setCollectionRecipients] = useState({
    owner_phone: '', manager_phone: '', admin_phone: '', auditor_phone: '', branch_phones: {}
  });
  const [savingRecipients, setSavingRecipients] = useState(false);
  // Stock-request recipient role config (which user roles get the SMS)
  const [stockReqRecipients, setStockReqRecipients] = useState({
    include_admins: true,
    include_supply_manager: true,
    include_supply_auditor: false,
    include_all_supply_users: false,
  });
  const [savingStockReq, setSavingStockReq] = useState(false);
  const [sampleOpen, setSampleOpen] = useState(false);
  const [sendingSample, setSendingSample] = useState(false);
  // Tracks per-row "Test" button state — keyed by `${role}:${branchId||''}`
  const [testingRowKey, setTestingRowKey] = useState(null);

  // Gateway log state
  const [gatewayLogs, setGatewayLogs] = useState([]);
  const [gatewayLogsTotal, setGatewayLogsTotal] = useState(0);
  const [gatewayLogLevel, setGatewayLogLevel] = useState('ALL');
  const [gatewayLogLoading, setGatewayLogLoading] = useState(false);
  const [gatewayLogLive, setGatewayLogLive] = useState(true);
  const gatewayLogPollRef = useRef(null);

  // Adaptive polling ref + thread scroll anchor
  const pollIntervalRef = useRef(null);
  const messagesEndRef = useRef(null);

  // ── Data loaders ──
  const loadStats = useCallback(async () => {
    try {
      const res = await api.get('/sms/stats');
      setStats(res.data);
    } catch { /* ignore */ }
  }, []);

  const loadQueue = useCallback(async () => {
    setLoading(true);
    try {
      const params = { status: statusFilter, limit: 100 };
      if (currentBranch?.id) params.branch_id = currentBranch.id;
      const res = await api.get('/sms/queue', { params });
      setQueue(res.data.items || []);
      setQueueTotal(res.data.total || 0);
    } catch { /* ignore */ }
    setLoading(false);
  }, [statusFilter, currentBranch]);

  const loadTemplates = useCallback(async () => {
    try {
      const res = await api.get('/sms/templates');
      setTemplates(res.data || []);
    } catch { /* ignore */ }
  }, []);

  const loadSettings = useCallback(async () => {
    try {
      const res = await api.get('/sms/settings');
      setSmsSettings(res.data || []);
      const rec = await api.get('/settings/collection-recipients');
      setCollectionRecipients(rec.data || { owner_phone: '', manager_phone: '', admin_phone: '', auditor_phone: '', branch_phones: {} });
      try {
        const sr = await api.get('/sms/recipients/branch_stock_request');
        setStockReqRecipients({
          include_admins: !!sr.data?.include_admins,
          include_supply_manager: !!sr.data?.include_supply_manager,
          include_supply_auditor: !!sr.data?.include_supply_auditor,
          include_all_supply_users: !!sr.data?.include_all_supply_users,
        });
      } catch { /* keep defaults */ }
    } catch { /* ignore */ }
  }, []);

  const loadGatewayLogs = useCallback(async () => {
    setGatewayLogLoading(true);
    try {
      const params = { limit: 300 };
      if (gatewayLogLevel !== 'ALL') params.level = gatewayLogLevel;
      const res = await api.get('/sms/gateway/logs', { params });
      setGatewayLogs(res.data.items || []);
      setGatewayLogsTotal(res.data.total || 0);
    } catch { /* ignore */ }
    setGatewayLogLoading(false);
  }, [gatewayLogLevel]);

  useEffect(() => { loadStats(); }, [loadStats]);
  useEffect(() => { if (activeTab === 'queue') loadQueue(); }, [activeTab, loadQueue]);
  useEffect(() => { if (activeTab === 'templates') loadTemplates(); }, [activeTab, loadTemplates]);
  useEffect(() => { if (activeTab === 'settings') loadSettings(); }, [activeTab, loadSettings]);
  useEffect(() => { if (activeTab === 'gateway-log') loadGatewayLogs(); }, [activeTab, loadGatewayLogs]);

  // Per-row "Test SMS" handler — used by the small button next to each phone
  // field on the Settings tab. Sends ONE [SAMPLE] SMS to the given phone with
  // a role label, so the admin can verify a single number without spamming
  // the whole recipient list.
  const sendSingleSample = useCallback(async (phone, role, branchId, branchName) => {
    const trimmed = (phone || '').trim();
    if (!trimmed) {
      toast.error(`No ${role} phone configured`);
      return;
    }
    const rowKey = `${role}:${branchId || ''}`;
    setTestingRowKey(rowKey);
    try {
      await api.post('/sms/send-sample-single', {
        phone: trimmed, role, branch_id: branchId || '', branch_name: branchName || '',
      });
      toast.success(`[SAMPLE] queued to ${role}${branchName ? ` · ${branchName}` : ''} (${trimmed})`);
      loadQueue();
      loadStats();
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to queue sample');
    }
    setTestingRowKey(null);
  }, [loadQueue, loadStats]);

  // Gateway log live polling
  useEffect(() => {
    if (gatewayLogPollRef.current) clearInterval(gatewayLogPollRef.current);
    if (activeTab === 'gateway-log' && gatewayLogLive) {
      gatewayLogPollRef.current = setInterval(() => {
        if (!document.hidden) loadGatewayLogs();
      }, 5000);
    }
    return () => clearInterval(gatewayLogPollRef.current);
  }, [activeTab, gatewayLogLive, loadGatewayLogs]); // eslint-disable-line
  // Reload conversations + unknown count when tab, section, or branch changes
  useEffect(() => {
    if (activeTab === 'conversations') {
      loadConversations(convoSection);
      loadUnknownCount();
      setActiveConvo(null);
      setThread([]);
    }
  }, [activeTab, convoSection, currentBranch]); // eslint-disable-line

  // Fetch company name once for the auto-signature hint
  useEffect(() => {
    api.get('/settings/business-info').then(res => {
      setCompanyName(res.data.business_name || '');
    }).catch(() => {});
  }, []);

  // ── Adaptive polling ──────────────────────────────────────────────────────
  useEffect(() => {
    if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);

    const isConvoTab = activeTab === 'conversations';
    const hasThread  = Boolean(activeConvo?.phone);
    const pollMs = hasThread ? 7000 : isConvoTab ? 30000 : 60000;

    const poll = () => {
      if (document.hidden) return;
      api.get('/sms/stats').then(res => setStats(res.data)).catch(() => {});

      if (isConvoTab) {
        const params = { section: convoSection };
        if (convoSection === 'customers' && currentBranch?.id) params.branch_id = currentBranch.id;
        api.get('/sms/conversations', { params })
          .then(res => {
            const sorted = (res.data || []).sort((a, b) =>
              (b.last_time || '').localeCompare(a.last_time || '')
            );
            setConversations(sorted);
          })
          .catch(() => {});
        // Keep unknown count badge fresh
        api.get('/sms/conversations', { params: { section: 'unknown' } })
          .then(res => setUnknownCount(res.data.length || 0))
          .catch(() => {});

        if (hasThread) {
          const threadUrl = activeConvo.customer_id
            ? `/sms/conversation/customer/${activeConvo.customer_id}`
            : `/sms/conversation/${encodeURIComponent(activeConvo.phone)}`;
          api.get(threadUrl)
            .then(res => setThread(res.data.messages || []))
            .catch(() => {});
        }
      }
    };

    pollIntervalRef.current = setInterval(poll, pollMs);
    return () => clearInterval(pollIntervalRef.current);
  }, [activeTab, activeConvo?.phone, currentBranch?.id, convoSection]); // eslint-disable-line

  // Cleanup on unmount
  useEffect(() => () => clearInterval(pollIntervalRef.current), []);

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    if (thread.length > 0) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [thread.length]);

  // Scroll to bottom immediately when opening a conversation (no animation)
  useEffect(() => {
    if (activeConvo && thread.length > 0) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'instant' });
    }
  }, [activeConvo?.customer_id, activeConvo?.phone]); // eslint-disable-line

  // ── Conversations ──
  const loadConversations = async (section = convoSection) => {
    try {
      const params = { section };
      if (section === 'customers' && currentBranch?.id) params.branch_id = currentBranch.id;
      const res = await api.get('/sms/conversations', { params });
      // Ensure most recent first on client side too
      const sorted = (res.data || []).sort((a, b) =>
        (b.last_time || '').localeCompare(a.last_time || '')
      );
      setConversations(sorted);
    } catch { /* ignore */ }
  };

  const loadUnknownCount = async () => {
    if (user?.role !== 'admin') return;
    try {
      const res = await api.get('/sms/conversations', { params: { section: 'unknown' } });
      setUnknownCount(res.data.length || 0);
    } catch { /* ignore */ }
  };

  const openConversation = async (convo) => {
    setActiveConvo(convo);
    setThreadLoading(true);
    try {
      let res;
      if (convo.customer_id) {
        // Registered customer — load all phones merged into one thread
        res = await api.get(`/sms/conversation/customer/${convo.customer_id}`);
        // Enrich activeConvo with phones from response
        setActiveConvo(prev => ({ ...prev, phones: res.data.phones || [] }));
      } else {
        // Unknown number — load by phone
        res = await api.get(`/sms/conversation/${encodeURIComponent(convo.phone)}`);
      }
      setThread(res.data.messages || []);
      loadConversations(convoSection);
    } catch { setThread([]); }
    setThreadLoading(false);
  };

  const searchAssignCustomers = async (q) => {
    setAssignSearch(q);
    if (!q.trim()) { setAssignResults([]); return; }
    try {
      const res = await api.get('/customers', { params: { search: q, limit: 10 } });
      setAssignResults(res.data.customers || []);
    } catch { setAssignResults([]); }
  };

  const handleAssignPhone = async (customer) => {
    if (!activeConvo) return;
    setAssigning(true);
    try {
      const res = await api.patch('/sms/assign-phone', {
        phone: activeConvo.phone,
        customer_id: customer.id,
      });
      toast.success(`${activeConvo.phone} assigned to ${customer.name} — ${res.data.migrated_messages} messages migrated`);
      setAssignModal(false);
      setAssignSearch('');
      setAssignResults([]);
      setActiveConvo(null);
      setThread([]);
      loadConversations('customers');
      loadConversations('unknown').then(() => loadUnknownCount());
    } catch (e) { toast.error(e.response?.data?.detail || 'Assignment failed'); }
    setAssigning(false);
  };

  const sendReply = async () => {
    if (!activeConvo || !replyMsg.trim()) return;
    setReplying(true);
    try {
      const payload = {
        message: replyMsg,
        branch_id: currentBranch?.id || '',
        branch_name: currentBranch?.name || '',
      };
      if (activeConvo.customer_id) {
        // Registered customer — backend sends to ALL their phones
        payload.customer_id = activeConvo.customer_id;
        payload.customer_name = activeConvo.customer_name;
      } else {
        // Unknown number — send to specific phone only
        payload.phone = activeConvo.phone;
        payload.customer_name = activeConvo.phone;
      }
      await api.post('/sms/send', payload);
      setReplyMsg('');
      toast.success(activeConvo.phones?.length > 1
        ? `Message queued to ${activeConvo.phones.length} numbers`
        : 'Message queued');
      openConversation(activeConvo);
    } catch (e) { toast.error('Failed to send'); }
    setReplying(false);
  };


  // ── Queue actions ──
  const markSent = async (id) => {
    await api.patch(`/sms/queue/${id}/mark-sent`);
    toast.success('Marked as sent');
    loadQueue(); loadStats();
  };

  const retrySms = async (id) => {
    await api.post(`/sms/queue/${id}/retry`);
    toast.success('Re-queued for sending');
    loadQueue(); loadStats();
  };

  const skipSms = async (id) => {
    await api.post(`/sms/queue/${id}/skip`);
    toast('Message skipped');
    loadQueue(); loadStats();
  };

  // ── Compose ──
  const searchCustomers = async (q) => {
    setCustSearch(q);
    if (!q) { setCustomers([]); return; }
    try {
      const res = await api.get('/customers', { params: { search: q, limit: 12 } });
      setCustomers(res.data.customers || []);
    } catch { setCustomers([]); }
  };

  const handleComposeSend = async () => {
    if (!selectedCust?.phone || !composeMsg.trim()) return;
    setSending(true);
    try {
      await api.post('/sms/send', {
        customer_id: selectedCust.id, customer_name: selectedCust.name,
        phone: selectedCust.phone, message: composeMsg,
        branch_id: currentBranch?.id || '', branch_name: currentBranch?.name || '',
      });
      toast.success(`Message queued for ${selectedCust.name}`);
      setComposeMsg(''); setSelectedCust(null); setCustSearch('');
      loadStats();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed'); }
    setSending(false);
  };

  // ── Blast ──
  const handleBlast = async () => {
    if (!blastMsg.trim()) return;
    setBlasting(true);
    try {
      const payload = {
        message: blastMsg,
        branch_id: currentBranch?.id || '',
        branch_name: currentBranch?.name || '',
        filter: {},
      };
      if (blastMinBal) payload.filter.min_balance = parseFloat(blastMinBal);
      if (currentBranch?.id) payload.filter.branch_id = currentBranch.id;
      const res = await api.post('/sms/blast', payload);
      toast.success(`${res.data.queued} messages queued (${res.data.skipped_no_phone} skipped — no phone)`);
      setBlastMsg(''); setBlastMinBal('');
      loadStats();
    } catch (e) { toast.error(e.response?.data?.detail || 'Blast failed'); }
    setBlasting(false);
  };

  // ── Credit Blast ──
  const handleCreditBlastPreview = async () => {
    setCreditBlastLoading(true);
    setCreditBlastPreview(null);
    try {
      const res = await api.post('/sms/credit-blast', {
        dry_run: true,
        min_balance: parseFloat(creditBlastMinBal) || 0,
        branch_id: currentBranch?.id || '',
      });
      setCreditBlastPreview(res.data);
    } catch (e) { toast.error(e.response?.data?.detail || 'Preview failed'); }
    setCreditBlastLoading(false);
  };

  const handleCreditBlastSend = async () => {
    if (!creditBlastPreview || creditBlastPreview.total_customers === 0) return;
    setCreditBlastSending(true);
    try {
      const res = await api.post('/sms/credit-blast', {
        dry_run: false,
        min_balance: parseFloat(creditBlastMinBal) || 0,
        branch_id: currentBranch?.id || '',
      });
      toast.success(`${res.data.queued} messages queued to ${res.data.total_customers} customers`);
      setCreditBlastPreview(null);
      setCreditBlastMinBal('');
      loadStats();
    } catch (e) { toast.error(e.response?.data?.detail || 'Send failed'); }
    setCreditBlastSending(false);
  };

  // ── Templates ──
  const saveTemplate = async (id) => {
    try {
      await api.put(`/sms/templates/${id}`, { body: editBody });
      toast.success('Template saved');
      setEditingId(null);
      loadTemplates();
    } catch { toast.error('Failed to save'); }
  };

  const toggleTemplate = async (t) => {
    await api.put(`/sms/templates/${t.id}`, { active: !t.active });
    toast.success(`${t.name} ${t.active ? 'disabled' : 'enabled'}`);
    loadTemplates();
  };

  // ── Settings ──
  const toggleSetting = async (key) => {
    const current = smsSettings.find(s => s.trigger_key === key);
    const enabled = current ? !current.enabled : false;
    await api.put(`/sms/settings/${key}`, { enabled });
    toast.success(`${key.replace(/_/g, ' ')} ${enabled ? 'enabled' : 'disabled'}`);
    loadSettings();
  };

  // Stock-request recipient toggle (which user roles get SMS for branch_stock_request)
  const toggleStockReqRecipient = async (field) => {
    const next = { ...stockReqRecipients, [field]: !stockReqRecipients[field] };
    setStockReqRecipients(next);  // optimistic
    setSavingStockReq(true);
    try {
      await api.put('/sms/recipients/branch_stock_request', next);
      toast.success('Recipients updated');
    } catch {
      toast.error('Failed to update recipients');
      setStockReqRecipients(stockReqRecipients);  // revert
    }
    setSavingStockReq(false);
  };

  const clearGatewayLogs = async () => {
    if (!window.confirm('Clear all gateway logs? This cannot be undone.')) return;
    try {
      await api.delete('/sms/gateway/logs');
      toast.success('Gateway logs cleared');
      setGatewayLogs([]);
      setGatewayLogsTotal(0);
    } catch { toast.error('Failed to clear logs'); }
  };

  const fmtTime = (iso) => {
    if (!iso) return '—';
    const d = new Date(iso);
    return d.toLocaleDateString('en-PH', { month: 'short', day: 'numeric', year: 'numeric' }) + ' ' +
           d.toLocaleTimeString('en-PH', { hour: '2-digit', minute: '2-digit' });
  };

  return (
    <div className="space-y-4 animate-fadeIn" data-testid="messages-page">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-bold tracking-tight" style={{ fontFamily: 'Manrope' }} data-testid="messages-title">
            SMS Messages
          </h1>
          <div className="flex items-center gap-2">
            {stats.pending > 0 && <Badge className="bg-amber-100 text-amber-700 text-xs">{stats.pending} pending</Badge>}
            {stats.failed > 0 && <Badge className="bg-red-100 text-red-700 text-xs">{stats.failed} failed</Badge>}
            <span className="text-xs text-slate-400">{stats.total} total</span>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-slate-200 pb-px" data-testid="messages-tabs">
        {[
          { key: 'conversations', label: 'Conversations', icon: MessageSquare },
          { key: 'queue', label: 'Message Queue', icon: Clock },
          { key: 'compose', label: 'Compose', icon: Send },
          { key: 'credit-blast', label: 'Credit Blast', icon: Users },
          { key: 'blast', label: 'Promo Blast', icon: Megaphone },
          { key: 'templates', label: 'Templates', icon: FileText },
          { key: 'settings', label: 'Settings', icon: Settings },
          { key: 'gateway-log', label: 'Gateway Log', icon: Activity },
        ].map(t => {
          const Icon = t.icon;
          return (
            <button key={t.key} onClick={() => setActiveTab(t.key)}
              className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                activeTab === t.key
                  ? 'border-[#1A4D2E] text-[#1A4D2E]'
                  : 'border-transparent text-slate-400 hover:text-slate-600'
              }`}
              data-testid={`tab-${t.key}`}>
              <Icon size={14} /> {t.label}
            </button>
          );
        })}
      </div>

      {/* ═══ CONVERSATIONS TAB ═══ */}
      {activeTab === 'conversations' && (
        <div className="flex gap-4 h-[calc(100vh-220px)]">
          {/* Left — conversation list */}
          <div className="w-72 shrink-0 flex flex-col border border-slate-200 rounded-xl overflow-hidden bg-white">
            {/* Section toggle — Customers / Unknown (admin only) */}
            <div className="flex border-b border-slate-100">
              <button onClick={() => setConvoSection('customers')}
                className={`flex-1 py-2 text-xs font-semibold transition-colors ${
                  convoSection === 'customers' ? 'bg-[#1A4D2E] text-white' : 'text-slate-400 hover:bg-slate-50'
                }`}
                data-testid="section-customers">
                Customers
              </button>
              {user?.role === 'admin' && (
                <button onClick={() => setConvoSection('unknown')}
                  className={`flex-1 py-2 text-xs font-semibold transition-colors flex items-center justify-center gap-1.5 ${
                    convoSection === 'unknown' ? 'bg-amber-500 text-white' : 'text-slate-400 hover:bg-slate-50'
                  }`}
                  data-testid="section-unknown">
                  Unknown
                  {unknownCount > 0 && (
                    <span className={`text-[9px] font-bold rounded-full px-1.5 py-0.5 ${
                      convoSection === 'unknown' ? 'bg-white/30 text-white' : 'bg-amber-500 text-white'
                    }`}>{unknownCount}</span>
                  )}
                </button>
              )}
            </div>

            <div className="px-3 py-2 border-b border-slate-100 flex items-center gap-2">
              <Search size={13} className="text-slate-400 shrink-0" />
              <input value={convoSearch} onChange={e => setConvoSearch(e.target.value)}
                placeholder="Search by name or number…"
                className="flex-1 text-xs outline-none bg-transparent text-slate-700 placeholder-slate-400" />
              {/* Live polling indicator */}
              <div className="flex items-center gap-1 shrink-0">
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
                </span>
                <span className="text-[9px] text-slate-400">
                  {activeConvo ? '7s' : '30s'}
                </span>
              </div>
              <button onClick={() => loadConversations(convoSection)} className="text-slate-400 hover:text-slate-600">
                <RefreshCw size={12} />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto divide-y divide-slate-50">
              {conversations.length === 0 && !convoSearch && (
                <div className="text-center text-slate-400 text-xs py-12">
                  {convoSection === 'unknown' ? 'No unknown numbers' : 'No conversations yet'}
                </div>
              )}
              {convoSearch && conversations.filter(c => {
                const q = convoSearch.toLowerCase();
                return (
                  c.customer_name?.toLowerCase().includes(q) ||
                  c.phone?.includes(convoSearch) ||
                  c.phones?.some(p => p.includes(convoSearch))
                );
              }).length === 0 && (
                <div className="text-center text-slate-400 text-xs py-10 px-4">
                  <Search size={20} className="mx-auto mb-2 opacity-30" />
                  No results for <strong>"{convoSearch}"</strong>
                </div>
              )}
              {conversations
                .filter(c => {
                  if (!convoSearch) return true;
                  const q = convoSearch.toLowerCase();
                  return (
                    c.customer_name?.toLowerCase().includes(q) ||
                    c.phone?.includes(convoSearch) ||
                    c.phones?.some(p => p.includes(convoSearch))
                  );
                })
                .map(c => {
                  const isUnknown = convoSection === 'unknown';
                  return (
                    <button key={c.phone} onClick={() => openConversation(c)}
                      className={`w-full text-left px-3 py-3 hover:bg-slate-50 transition-colors ${activeConvo?.phone === c.phone ? 'bg-[#f0f7f3]' : ''}`}
                      data-testid={`convo-${c.phone}`}>
                      <div className="flex items-start justify-between gap-1">
                        <div className="flex items-center gap-2 shrink-0">
                          <div className={`w-7 h-7 rounded-full flex items-center justify-center text-white text-xs font-bold ${
                            isUnknown ? 'bg-amber-400' : 'bg-[#1A4D2E]'
                          }`}>
                            {isUnknown ? '?' : c.customer_name?.[0]?.toUpperCase() || '?'}
                          </div>
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-1.5 flex-wrap">
                            <span className="text-xs font-semibold text-slate-800 truncate">
                              {isUnknown ? c.phone : c.customer_name}
                            </span>
                            {c.unread > 0 && (
                              <span className={`shrink-0 text-white text-[9px] font-bold rounded-full px-1.5 py-0.5 ${isUnknown ? 'bg-amber-500' : 'bg-[#1A4D2E]'}`}>{c.unread}</span>
                            )}
                            {isUnknown && (
                              <span className="text-[8px] bg-amber-50 text-amber-600 border border-amber-200 px-1 py-0.5 rounded font-medium">Unregistered</span>
                            )}
                          </div>
                          {!isUnknown && (
                            <>
                              <p className="text-[10px] text-slate-400 font-mono">{c.phone}</p>
                              {c.branch_names?.length > 1 && (
                                <div className="flex gap-1 mt-0.5 flex-wrap">
                                  {c.branch_names.filter(Boolean).map((bn, i) => (
                                    <span key={i} className={`text-[8px] px-1.5 py-0.5 rounded-full font-medium ${
                                      bn === currentBranch?.name ? 'bg-[#1A4D2E] text-white' : 'bg-blue-100 text-blue-700'
                                    }`}>{bn}</span>
                                  ))}
                                </div>
                              )}
                              {c.branch_names?.length === 1 && c.branch_names[0] && (
                                <span className="text-[9px] text-slate-400">{c.branch_names[0]}</span>
                              )}
                            </>
                          )}
                          <p className={`text-[10px] mt-0.5 truncate ${c.last_direction === 'in' ? isUnknown ? 'text-amber-600 font-medium' : 'text-[#1A4D2E] font-medium' : 'text-slate-400'}`}>
                            {c.last_direction === 'in' ? '← ' : '→ '}{c.last_message}
                          </p>
                        </div>
                        <span className="text-[9px] text-slate-300 shrink-0 mt-0.5">
                          {c.last_time ? formatDate(c.last_time) : ''}
                        </span>
                      </div>
                    </button>
                  );
                })}
            </div>
          </div>

          {/* Right — thread */}
          <div className="flex-1 flex flex-col border border-slate-200 rounded-xl overflow-hidden bg-white">
            {!activeConvo ? (
              <div className="flex-1 flex items-center justify-center text-slate-400 text-sm">
                <div className="text-center">
                  <MessageSquare size={32} className="mx-auto mb-2 opacity-30" />
                  <p>Select a conversation</p>
                </div>
              </div>
            ) : (
              <>
                {/* Thread header */}
                <div className="px-4 py-3 border-b border-slate-100 flex items-center gap-3">
                  <div className={`w-8 h-8 rounded-full flex items-center justify-center text-white text-xs font-bold ${
                    convoSection === 'unknown' ? 'bg-amber-400' : 'bg-[#1A4D2E]'
                  }`}>
                    {convoSection === 'unknown' ? '?' : activeConvo.customer_name?.[0]?.toUpperCase() || '?'}
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-semibold text-slate-800">
                        {convoSection === 'unknown' ? activeConvo.phone : activeConvo.customer_name}
                      </p>
                      {convoSection === 'unknown' && (
                        <span className="text-[9px] bg-amber-50 text-amber-600 border border-amber-200 px-1.5 py-0.5 rounded font-medium">Unregistered</span>
                      )}
                    </div>
                    {/* Show all registered phones for this customer */}
                    {convoSection !== 'unknown' && activeConvo.phones?.length > 0 ? (
                      <div className="flex items-center gap-1 flex-wrap mt-0.5">
                        <Phone size={9} className="text-slate-400" />
                        {activeConvo.phones.map((p, i) => (
                          <span key={i} className={`text-[10px] font-mono ${i === 0 ? 'text-slate-600' : 'text-slate-400'}`}>
                            {p}{i < activeConvo.phones.length - 1 ? ' ·' : ''}
                          </span>
                        ))}
                      </div>
                    ) : convoSection !== 'unknown' && (
                      <p className="text-[10px] text-slate-400 font-mono flex items-center gap-1">
                        <Phone size={9} /> {activeConvo.phone}
                      </p>
                    )}
                  </div>
                  {/* Branch context + Assign button */}
                  <div className="ml-auto flex items-center gap-2">
                    {convoSection === 'unknown' ? (
                      <button onClick={() => setAssignModal(true)}
                        className="flex items-center gap-1.5 text-xs bg-[#1A4D2E] text-white px-3 py-1.5 rounded-lg hover:bg-[#14532d] transition-colors"
                        data-testid="assign-phone-btn">
                        <UserPlus size={12} /> Assign to Customer
                      </button>
                    ) : currentBranch ? (
                      <span className="text-[10px] bg-[#1A4D2E]/10 text-[#1A4D2E] px-2 py-0.5 rounded-full font-medium">
                        {currentBranch.name}
                      </span>
                    ) : (
                      <span className="text-[10px] bg-slate-100 text-slate-500 px-2 py-0.5 rounded-full font-medium">
                        All Branches
                      </span>
                    )}
                    <button onClick={() => openConversation(activeConvo)} className="text-slate-400 hover:text-slate-600">
                      <RefreshCw size={13} className={threadLoading ? 'animate-spin' : ''} />
                    </button>
                  </div>
                </div>

                {/* Bubbles */}
                <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
                  {threadLoading && (
                    <div className="flex justify-center py-8"><Loader2 size={20} className="animate-spin text-slate-400" /></div>
                  )}
                  {!threadLoading && thread.length === 0 && (
                    <div className="text-center text-slate-400 text-xs py-8">No messages yet</div>
                  )}
                  {thread.map((msg, i) => {
                    const isOut = msg.direction === 'out';
                    const isMyBranch = !currentBranch || msg.branch_id === currentBranch?.id || !msg.branch_id;
                    return (
                      <div key={msg.id || i} className={`flex ${isOut ? 'justify-end' : 'justify-start'}`}>
                        <div className={`max-w-[70%] px-3 py-2 text-xs leading-relaxed shadow-sm ${
                          isOut && isMyBranch
                            ? 'bg-[#1A4D2E] text-white rounded-t-2xl rounded-bl-2xl rounded-br-sm'
                            : isOut && !isMyBranch
                            ? 'bg-blue-600 text-white rounded-t-2xl rounded-bl-2xl rounded-br-sm opacity-80'
                            : 'bg-slate-100 text-slate-800 rounded-t-2xl rounded-br-2xl rounded-bl-sm'
                        }`}>
                          {/* Other-branch label */}
                          {isOut && !isMyBranch && msg.branch_name && (
                            <p className="text-[9px] font-semibold text-blue-200 mb-1">{msg.branch_name}</p>
                          )}
                          <p className="whitespace-pre-wrap">{msg.message}</p>
                          <p className={`text-[9px] mt-1 ${isOut ? 'text-white/60' : 'text-slate-400'}`}>
                            {msg.sent_by_name && <span className="mr-1">{msg.sent_by_name} ·</span>}
                            {msg.created_at ? formatTime(msg.created_at) : ''}
                            {isOut && msg.status && <span className="ml-1 opacity-60">· {msg.status}</span>}
                          </p>
                        </div>
                      </div>
                    );
                  })}
                  {/* Scroll anchor — new messages auto-scroll here */}
                  <div ref={messagesEndRef} />
                </div>

                {/* Reply box */}
                <div className="px-4 py-3 border-t border-slate-100">
                  {/* Auto-signature hint */}
                  {currentBranch && (
                    <p className="text-[10px] text-slate-400 mb-1.5 flex items-center gap-1">
                      <span className="font-mono bg-slate-50 border border-slate-200 rounded px-1.5 py-0.5 text-slate-500">
                        Auto-signature: - {[companyName, currentBranch.name].filter(Boolean).join(' | ')}
                      </span>
                      <span className="text-slate-300">(cannot be removed)</span>
                    </p>
                  )}
                  {/* Multi-phone hint */}
                  {activeConvo?.phones?.length > 1 && (
                    <p className="text-[10px] text-[#1A4D2E] font-medium mb-1.5 flex items-center gap-1">
                      <Phone size={9} /> Replying to {activeConvo.phones.length} numbers: {activeConvo.phones.join(', ')}
                    </p>
                  )}
                  <div className="flex items-center gap-2">
                    <input value={replyMsg} onChange={e => setReplyMsg(e.target.value)}
                      onKeyDown={e => e.key === 'Enter' && !e.shiftKey && sendReply()}
                      placeholder="Type a message…"
                      className="flex-1 text-sm border border-slate-200 rounded-full px-4 py-2 outline-none focus:border-[#1A4D2E] transition-colors" />
                    <button onClick={sendReply} disabled={replying || !replyMsg.trim()}
                      className="w-9 h-9 rounded-full bg-[#1A4D2E] flex items-center justify-center text-white disabled:opacity-40 hover:bg-[#14532d] transition-colors"
                      data-testid="send-reply-btn">
                      {replying ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
                    </button>
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* ═══ ASSIGN TO CUSTOMER MODAL ═══ */}
      {assignModal && (
        <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-md space-y-4 p-5">
            <div>
              <h3 className="text-sm font-bold text-slate-800 flex items-center gap-2">
                <UserPlus size={16} className="text-[#1A4D2E]" />
                Assign Number to Customer
              </h3>
              <p className="text-xs text-slate-400 mt-1">
                <span className="font-mono font-semibold text-slate-600">{activeConvo?.phone}</span>
                {' '}→ all past messages will move to the customer's branch.
              </p>
            </div>

            <div className="relative">
              <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <Input
                autoFocus
                value={assignSearch}
                onChange={e => searchAssignCustomers(e.target.value)}
                placeholder="Search customer by name or phone..."
                className="pl-8 h-9 text-sm"
                data-testid="assign-search-input"
              />
            </div>

            {assignResults.length > 0 && (
              <div className="border border-slate-200 rounded-lg overflow-hidden max-h-52 overflow-y-auto">
                {assignResults.map(c => {
                  const branchName = branches?.find(b => b.id === c.branch_id)?.name || '';
                  return (
                    <button key={c.id}
                      onClick={() => handleAssignPhone(c)}
                      disabled={assigning}
                      className="w-full text-left px-3 py-2.5 hover:bg-slate-50 border-b last:border-0 flex items-center justify-between gap-2 transition-colors"
                      data-testid={`assign-customer-${c.id}`}>
                      <div>
                        <p className="text-sm font-medium text-slate-800">{c.name}</p>
                        <p className="text-[10px] text-slate-400 font-mono">{c.phone || 'No phone'}</p>
                      </div>
                      <div className="text-right shrink-0">
                        {branchName && (
                          <span className="text-[9px] bg-[#1A4D2E]/10 text-[#1A4D2E] px-1.5 py-0.5 rounded-full font-medium">
                            {branchName}
                          </span>
                        )}
                        {assigning && <Loader2 size={12} className="animate-spin text-slate-400 ml-1 inline" />}
                      </div>
                    </button>
                  );
                })}
              </div>
            )}

            {assignSearch && assignResults.length === 0 && (
              <p className="text-xs text-center text-slate-400 py-2">No customers found</p>
            )}

            <div className="flex justify-end">
              <Button variant="ghost" size="sm" onClick={() => {
                setAssignModal(false);
                setAssignSearch('');
                setAssignResults([]);
              }}>Cancel</Button>
            </div>
          </div>
        </div>
      )}

      {/* ═══ QUEUE TAB ═══ */}
      {activeTab === 'queue' && (
        <Card className="border-slate-200">
          <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
            <div className="flex gap-1.5">
              {Object.entries(STATUS_CONFIG).map(([key, cfg]) => {
                const Icon = cfg.icon;
                const count = stats[key] || 0;
                return (
                  <button key={key} onClick={() => setStatusFilter(key)}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                      statusFilter === key ? 'bg-slate-800 text-white' : 'bg-slate-50 text-slate-500 hover:bg-slate-100'
                    }`}
                    data-testid={`filter-${key}`}>
                    <Icon size={12} /> {cfg.label}
                    {count > 0 && <span className="opacity-70">({count})</span>}
                  </button>
                );
              })}
            </div>
            <button onClick={() => { loadQueue(); loadStats(); }}
              className="text-slate-400 hover:text-slate-600 p-1" data-testid="refresh-queue-btn">
              <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            </button>
            {stats.pending > 0 && (
              <button
                onClick={async () => {
                  const n = stats.pending;
                  if (!window.confirm(
                    `🛑 Stop ALL ${n} pending SMS right now?\n\n` +
                    `Use this when the gateway phone is offline and is ` +
                    `spamming recipients with the same message. All pending ` +
                    `rows will be marked "skipped" and the gateway will stop ` +
                    `replaying them.\n\nThis cannot be undone.`
                  )) return;
                  try {
                    const r = await api.post('/sms/queue/clear-stuck', { include_pending: true });
                    toast.success(`Stopped ${r.data.cleared || 0} pending message(s)`);
                    loadQueue(); loadStats();
                  } catch (e) {
                    toast.error(e.response?.data?.detail || 'Failed to stop pending');
                  }
                }}
                className="ml-1 px-2 py-1 text-[10px] font-semibold uppercase tracking-wider rounded-full bg-rose-50 text-rose-700 border border-rose-200 hover:bg-rose-100 flex items-center gap-1"
                title="Emergency: mark every pending + deferred SMS as skipped. Use during gateway/DNS outages to stop spam replays."
                data-testid="stop-all-pending-btn"
              >
                🛑 Stop All Pending
              </button>
            )}
          </div>

          <div className="divide-y divide-slate-100 max-h-[calc(100vh-320px)] overflow-y-auto">
            {queue.length === 0 && (
              <div className="text-center text-slate-400 text-sm py-16">
                No {statusFilter} messages
              </div>
            )}
            {queue.map(sms => {
              const sc = STATUS_CONFIG[sms.status] || STATUS_CONFIG.pending;
              return (
                <div key={sms.id} className="px-4 py-3 hover:bg-slate-50/50 transition-colors" data-testid={`sms-row-${sms.id}`}>
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm font-semibold text-slate-800">{sms.customer_name || 'Unknown'}</span>
                        <span className="text-xs text-slate-400 font-mono flex items-center gap-1">
                          <Phone size={10} /> {sms.phone}
                        </span>
                        <Badge className={`text-[9px] ${TEMPLATE_BADGE[sms.template_key] || 'bg-slate-100 text-slate-600'}`}>
                          {sms.template_key.replace(/_/g, ' ')}
                        </Badge>
                        {sms.branch_name && (
                          <span className="text-[10px] text-slate-400">{sms.branch_name}</span>
                        )}
                      </div>
                      <p className="text-xs text-slate-500 mt-1 leading-relaxed">{sms.message}</p>
                    </div>
                    <div className="text-right shrink-0">
                      <Badge className={`text-[9px] ${sc.cls}`}>{sc.label}</Badge>
                      <p className="text-[10px] text-slate-400 mt-0.5">{fmtTime(sms.created_at)}</p>
                      {sms.sent_at && <p className="text-[10px] text-emerald-500">Sent {fmtTime(sms.sent_at)}</p>}
                      {sms.error && <p className="text-[10px] text-red-500 max-w-[140px] truncate">{sms.error}</p>}
                    </div>
                  </div>

                  {/* Actions per status */}
                  {sms.status === 'pending' && (
                    <div className="flex items-center gap-2 mt-2 pt-2 border-t border-slate-100">
                      <Button size="sm" className="h-7 text-[10px] bg-[#1A4D2E] hover:bg-[#14532d] text-white"
                        onClick={() => markSent(sms.id)} data-testid={`mark-sent-${sms.id}`}>
                        <CheckCircle2 size={10} className="mr-1" /> Mark Sent
                      </Button>
                      <Button size="sm" variant="ghost" className="h-7 text-[10px] text-slate-400"
                        onClick={() => skipSms(sms.id)}>
                        <SkipForward size={10} className="mr-1" /> Skip
                      </Button>
                      <div className="flex-1" />
                      <span className="text-[10px] text-slate-300">{sms.trigger}</span>
                    </div>
                  )}
                  {sms.status === 'failed' && (
                    <div className="flex items-center gap-2 mt-2 pt-2 border-t border-slate-100">
                      <Button size="sm" variant="outline" className="h-7 text-[10px] text-amber-600 border-amber-200"
                        onClick={() => retrySms(sms.id)}>
                        <RefreshCw size={10} className="mr-1" /> Retry
                      </Button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          {queueTotal > queue.length && (
            <div className="px-4 py-2 text-center text-xs text-slate-400 border-t border-slate-100">
              Showing {queue.length} of {queueTotal}
            </div>
          )}
        </Card>
      )}

      {/* ═══ COMPOSE TAB ═══ */}
      {activeTab === 'compose' && (
        <Card className="border-slate-200 max-w-2xl">
          <CardContent className="p-5 space-y-4">
            <h2 className="text-sm font-bold text-slate-700">Compose Message</h2>

            {/* Customer selector */}
            <div>
              <Label className="text-xs text-slate-500 uppercase tracking-wide">To (Customer)</Label>
              {selectedCust ? (
                <div className="flex items-center justify-between bg-emerald-50 border border-emerald-200 rounded-lg px-3 py-2.5 mt-1">
                  <div>
                    <span className="text-sm font-medium">{selectedCust.name}</span>
                    <span className="text-xs text-slate-400 ml-2 font-mono">{selectedCust.phone || 'No phone'}</span>
                  </div>
                  <button onClick={() => { setSelectedCust(null); setCustSearch(''); }} className="text-slate-400 hover:text-slate-600">
                    <X size={14} />
                  </button>
                </div>
              ) : (
                <div className="relative mt-1">
                  <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                  <Input value={custSearch}
                    onChange={e => searchCustomers(e.target.value)}
                    onFocus={() => setCustDropdownOpen(true)}
                    onBlur={() => setTimeout(() => setCustDropdownOpen(false), 200)}
                    placeholder="Search customer by name or phone..."
                    className="pl-8 h-9"
                    data-testid="compose-search" />
                  {custDropdownOpen && customers.length > 0 && (
                    <div className="absolute z-50 top-full mt-1 w-full bg-white border border-slate-200 rounded-lg shadow-lg max-h-48 overflow-y-auto">
                      {customers.map(c => (
                        <button key={c.id} onMouseDown={() => { setSelectedCust(c); setCustSearch(''); setCustomers([]); }}
                          className="w-full text-left px-3 py-2 hover:bg-slate-50 text-sm border-b last:border-0 flex justify-between"
                          data-testid={`compose-cust-${c.id}`}>
                          <span className="font-medium">{c.name}</span>
                          <span className="text-xs text-slate-400 font-mono">{c.phone || 'No phone'}</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Message */}
            <div>
              <Label className="text-xs text-slate-500 uppercase tracking-wide">Message</Label>
              <textarea value={composeMsg} onChange={e => setComposeMsg(e.target.value)}
                rows={5} maxLength={320}
                placeholder="Type your message here..."
                className="w-full mt-1 rounded-lg border border-slate-200 px-3 py-2.5 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-[#1A4D2E]/20"
                data-testid="compose-message" />
              <p className="text-[10px] text-slate-400 text-right mt-1">{composeMsg.length} / 320 characters</p>
            </div>

            <div className="flex items-center gap-3">
              <Button onClick={handleComposeSend}
                disabled={sending || !selectedCust?.phone || !composeMsg.trim()}
                className="bg-[#1A4D2E] hover:bg-[#14532d] text-white disabled:opacity-40"
                data-testid="compose-send-btn">
                {sending ? <Loader2 size={14} className="mr-2 animate-spin" /> : <Send size={14} className="mr-2" />}
                Queue Message
              </Button>
              {!selectedCust?.phone && selectedCust && (
                <span className="text-xs text-red-500">This customer has no phone number</span>
              )}
            </div>
          </CardContent>
        </Card>
      )}


      {/* ═══ CREDIT BLAST TAB ═══ */}
      {activeTab === 'credit-blast' && (
        <div className="max-w-2xl space-y-5">
          {/* Header */}
          <div className="flex items-start gap-3">
            <div className="w-10 h-10 rounded-xl bg-rose-100 flex items-center justify-center shrink-0">
              <Users size={18} className="text-rose-600" />
            </div>
            <div>
              <h2 className="text-sm font-bold text-slate-800">Credit Reminder Blast</h2>
              <p className="text-xs text-slate-400 mt-0.5">
                Send personalised credit reminders to all customers with outstanding balances.
                The system automatically picks the right message — detailed for overdue/near-due, short for general reminders.
              </p>
            </div>
          </div>

          {/* Smart template legend */}
          <div className="grid grid-cols-2 gap-3">
            <div className="bg-amber-50 border border-amber-200 rounded-xl p-3">
              <p className="text-xs font-semibold text-amber-700 mb-1">Detailed Message — Option B</p>
              <p className="text-[10px] text-amber-600">Used when customer has <strong>overdue balance</strong> OR due date is <strong>within 15 days</strong></p>
              <p className="text-[10px] text-amber-500 mt-1 font-mono">Includes: balance, overdue, due date, est. interest</p>
            </div>
            <div className="bg-blue-50 border border-blue-200 rounded-xl p-3">
              <p className="text-xs font-semibold text-blue-700 mb-1">Short Message — Option A</p>
              <p className="text-[10px] text-blue-600">Used for customers with <strong>current balance</strong> and due date more than 15 days away</p>
              <p className="text-[10px] text-blue-500 mt-1 font-mono">Includes: balance, next due date, interest rate</p>
            </div>
          </div>

          {/* Filters */}
          <Card className="border-slate-200">
            <CardContent className="p-4 space-y-3">
              <div className="flex items-center gap-2 mb-1">
                <Filter size={13} className="text-slate-400" />
                <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Filters</span>
              </div>
              <div className="flex items-center gap-4 flex-wrap">
                <div className="flex items-center gap-2">
                  <Label className="text-xs text-slate-500 shrink-0">Min Balance:</Label>
                  <CalcInput value={creditBlastMinBal}
 onChange={(v) => { setCreditBlastMinBal(v); setCreditBlastPreview(null); }}
 placeholder="0 (all)" className="h-8 w-28 text-xs"
 data-testid="credit-blast-min-balance" />
                </div>
                <div className="text-xs text-slate-400">
                  Branch: <strong>{currentBranch?.name || 'All branches'}</strong>
                </div>
              </div>
              <Button onClick={handleCreditBlastPreview}
                disabled={creditBlastLoading}
                variant="outline" className="w-full h-9 text-sm border-slate-300"
                data-testid="credit-blast-preview-btn">
                {creditBlastLoading
                  ? <><Loader2 size={14} className="mr-2 animate-spin" /> Analysing customers…</>
                  : <><Search size={14} className="mr-2" /> Preview Blast</>}
              </Button>
            </CardContent>
          </Card>

          {/* Preview Results */}
          {creditBlastPreview && (
            <div className="space-y-4 animate-fadeIn">
              {creditBlastPreview.total_customers === 0 ? (
                <div className="text-center py-8 text-slate-400 text-sm border border-slate-200 rounded-xl">
                  No customers with balance{creditBlastMinBal ? ` above P${creditBlastMinBal}` : ''}.
                </div>
              ) : (
                <>
                  {/* Stats */}
                  <div className="grid grid-cols-3 gap-3">
                    <div className="bg-slate-50 border border-slate-200 rounded-xl p-3 text-center">
                      <p className="text-2xl font-bold text-slate-800">{creditBlastPreview.total_customers}</p>
                      <p className="text-[10px] text-slate-400 mt-0.5">Customers</p>
                    </div>
                    <div className="bg-slate-50 border border-slate-200 rounded-xl p-3 text-center">
                      <p className="text-2xl font-bold text-slate-800">{creditBlastPreview.total_sms}</p>
                      <p className="text-[10px] text-slate-400 mt-0.5">SMS to send</p>
                    </div>
                    <div className="bg-slate-50 border border-slate-200 rounded-xl p-3 text-center">
                      <div className="flex items-center justify-center gap-1.5">
                        <span className="text-xs font-bold text-amber-600">{creditBlastPreview.detailed_count} detailed</span>
                        <span className="text-slate-300">·</span>
                        <span className="text-xs font-bold text-blue-600">{creditBlastPreview.short_count} short</span>
                      </div>
                      <p className="text-[10px] text-slate-400 mt-0.5">Templates</p>
                    </div>
                  </div>

                  {/* Sample messages */}
                  {creditBlastPreview.preview?.length > 0 && (
                    <div className="space-y-3">
                      <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Sample Messages</p>
                      {creditBlastPreview.preview.map((p, i) => (
                        <div key={i} className={`border rounded-xl p-3 space-y-2 ${
                          p.template === 'detailed' ? 'border-amber-200 bg-amber-50/40' : 'border-blue-200 bg-blue-50/40'
                        }`}>
                          <div className="flex items-center justify-between">
                            <span className="text-xs font-semibold text-slate-700">{p.customer_name}</span>
                            <div className="flex items-center gap-2">
                              <span className="text-[10px] font-mono text-slate-400">{p.phones?.join(', ')}</span>
                              <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded-full ${
                                p.template === 'detailed' ? 'bg-amber-200 text-amber-700' : 'bg-blue-200 text-blue-700'
                              }`}>
                                {p.template === 'detailed' ? 'Detailed' : 'Short'}
                                {p.overdue_amount > 0 ? ' · OVERDUE' : p.days_until_due !== null && p.days_until_due <= 15 ? ` · Due in ${p.days_until_due}d` : ''}
                              </span>
                            </div>
                          </div>
                          <p className="text-[10px] font-mono text-slate-600 bg-white rounded-lg p-2.5 border border-slate-100 whitespace-pre-wrap leading-relaxed">
                            {p.message}
                          </p>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Send button */}
                  <Button onClick={handleCreditBlastSend}
                    disabled={creditBlastSending}
                    className="w-full bg-rose-600 hover:bg-rose-700 text-white h-11 text-sm font-semibold"
                    data-testid="credit-blast-send-btn">
                    {creditBlastSending
                      ? <><Loader2 size={15} className="mr-2 animate-spin" /> Queueing messages…</>
                      : <><Users size={15} className="mr-2" /> Send to {creditBlastPreview.total_customers} Customers ({creditBlastPreview.total_sms} SMS)</>}
                  </Button>
                  <p className="text-[10px] text-center text-slate-400">
                    Messages will be sent one-by-one through your gateway phone. Each customer receives a personalised message.
                  </p>
                </>
              )}
            </div>
          )}
        </div>
      )}


      {/* ═══ BLAST TAB ═══ */}
      {activeTab === 'blast' && (
        <Card className="border-slate-200 max-w-2xl">
          <CardContent className="p-5 space-y-4">
            <div className="flex items-center gap-2">
              <Megaphone size={18} className="text-amber-500" />
              <h2 className="text-sm font-bold text-slate-700">Promo Blast</h2>
            </div>
            <p className="text-xs text-slate-400">
              Send a message to multiple customers at once. Use <code className="bg-slate-100 px-1 rounded text-[11px]">&lt;customer_name&gt;</code> to personalize each message.
            </p>

            {/* Filters */}
            <div className="bg-slate-50 rounded-lg p-4 space-y-3">
              <div className="flex items-center gap-2">
                <Filter size={13} className="text-slate-400" />
                <span className="text-xs text-slate-500 uppercase font-semibold">Customer Filters</span>
              </div>
              <div className="flex items-center gap-4 flex-wrap">
                <div className="flex items-center gap-2">
                  <Label className="text-xs text-slate-500">Min Balance:</Label>
                  <CalcInput value={blastMinBal}
 onChange={(v) => setBlastMinBal(v)}
 placeholder="e.g. 5000" className="h-8 w-28 text-xs"
 data-testid="blast-min-balance" />
                </div>
                <div className="text-xs text-slate-400">
                  Branch: <strong>{currentBranch?.name || 'All branches'}</strong>
                </div>
              </div>
            </div>

            {/* Message */}
            <div>
              <Label className="text-xs text-slate-500 uppercase tracking-wide">Message</Label>
              <textarea value={blastMsg} onChange={e => setBlastMsg(e.target.value)}
                rows={5} maxLength={320}
                placeholder="Hi <customer_name>, may special promo kami ngayong buwan..."
                className="w-full mt-1 rounded-lg border border-slate-200 px-3 py-2.5 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-[#1A4D2E]/20"
                data-testid="blast-message" />
              <p className="text-[10px] text-slate-400 text-right mt-1">{blastMsg.length} / 320 characters</p>
            </div>

            <Button onClick={handleBlast} disabled={blasting || !blastMsg.trim()}
              className="bg-amber-500 hover:bg-amber-600 text-white disabled:opacity-40"
              data-testid="blast-send-btn">
              {blasting ? <Loader2 size={14} className="mr-2 animate-spin" /> : <Megaphone size={14} className="mr-2" />}
              {blasting ? 'Sending...' : 'Send Blast'}
            </Button>
          </CardContent>
        </Card>
      )}

      {/* ═══ TEMPLATES TAB ═══ */}
      {activeTab === 'templates' && (
        <div className="space-y-3 max-w-3xl">
          <p className="text-xs text-slate-400">Edit message templates. Use <code className="bg-slate-100 px-1 rounded text-[11px]">&lt;placeholder&gt;</code> tags that get replaced with real data when the message is generated.</p>
          {templates.map(t => (
            <Card key={t.id} className="border-slate-200" data-testid={`template-card-${t.key}`}>
              <CardContent className="p-4">
                {editingId === t.id ? (
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-bold text-slate-700">{t.name}</span>
                      <div className="flex gap-2">
                        <Button size="sm" className="h-7 text-xs bg-[#1A4D2E] text-white" onClick={() => saveTemplate(t.id)}
                          data-testid={`save-template-${t.key}`}>
                          <Check size={11} className="mr-1" /> Save
                        </Button>
                        <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => setEditingId(null)}>Cancel</Button>
                      </div>
                    </div>
                    <textarea value={editBody} onChange={e => setEditBody(e.target.value)}
                      rows={5}
                      className="w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm font-mono resize-none focus:outline-none focus:ring-2 focus:ring-[#1A4D2E]/20" />
                    <div className="flex flex-wrap gap-1.5">
                      <span className="text-[10px] text-slate-400 mr-1">Available:</span>
                      {t.placeholders?.map(p => (
                        <button key={p} onClick={() => setEditBody(prev => prev + `<${p}>`)}
                          className="text-[10px] bg-blue-50 text-blue-600 border border-blue-200 px-1.5 py-0.5 rounded font-mono hover:bg-blue-100 cursor-pointer">
                          &lt;{p}&gt;
                        </button>
                      ))}
                    </div>
                  </div>
                ) : (
                  <div>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm font-bold text-slate-700">{t.name}</span>
                        <Badge className={`text-[9px] ${t.active ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-400'}`}>
                          {t.active ? 'Active' : 'Disabled'}
                        </Badge>
                        <Badge className="text-[9px] bg-slate-50 text-slate-500 border border-slate-200">{t.trigger}</Badge>
                      </div>
                      <div className="flex items-center gap-1">
                        <button onClick={() => { setEditingId(t.id); setEditBody(t.body); }}
                          className="text-slate-400 hover:text-[#1A4D2E] p-1.5 rounded-lg hover:bg-slate-50" title="Edit template"
                          data-testid={`edit-template-${t.key}`}>
                          <Edit3 size={13} />
                        </button>
                        <button onClick={() => toggleTemplate(t)}
                          className={`p-1.5 rounded-lg hover:bg-slate-50 ${t.active ? 'text-emerald-500 hover:text-red-500' : 'text-slate-300 hover:text-emerald-500'}`}
                          title={t.active ? 'Disable' : 'Enable'}
                          data-testid={`toggle-template-${t.key}`}>
                          {t.active ? <CheckCircle2 size={13} /> : <XCircle size={13} />}
                        </button>
                      </div>
                    </div>
                    <p className="text-xs text-slate-400 mt-2 font-mono leading-relaxed bg-slate-50 rounded-lg p-3">{t.body}</p>
                    <div className="flex flex-wrap gap-1 mt-2">
                      {t.placeholders?.map(p => (
                        <span key={p} className="text-[9px] bg-blue-50 text-blue-600 px-1.5 py-0.5 rounded font-mono">&lt;{p}&gt;</span>
                      ))}
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* ═══ SETTINGS TAB ═══ */}
      {activeTab === 'settings' && (
        <div className="space-y-4 max-w-2xl">
          {/* Team SMS Reminders — per-stage enable + per-role recipients */}
          <TeamSmsRemindersCard branches={branches} />

          {/* Collection Notification Recipients */}
          <Card className="border-amber-200">
            <CardContent className="p-5 space-y-5">
              <div>
                <h2 className="text-sm font-bold text-slate-700 flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-amber-500" />
                  Collection Notification Recipients
                </h2>
                <p className="text-xs text-slate-400 mt-1">
                  Owner &amp; Admin receive SMS for <strong>all branches</strong>.
                  Manager &amp; Auditor are <strong>branch-specific</strong> — configure per branch below.
                </p>
              </div>

              {/* Global: Owner + Admin */}
              <div>
                <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">Global — All Branches</p>
                <div className="grid grid-cols-2 gap-3">
                  {[{ key: 'owner_phone', label: 'Owner Phone', role: 'Owner' }, { key: 'admin_phone', label: 'Admin Phone', role: 'Admin' }].map(({ key, label, role }) => {
                    const phone = collectionRecipients[key] || '';
                    const rowKey = `${role}:`;
                    const testing = testingRowKey === rowKey;
                    return (
                      <div key={key}>
                        <label className="text-xs text-slate-600 font-medium">{label}</label>
                        <div className="mt-1 flex gap-1.5">
                          <input data-testid={`recipient-${key}`} type="tel" placeholder="09XX XXX XXXX"
                            value={phone}
                            onChange={e => setCollectionRecipients(r => ({ ...r, [key]: e.target.value }))}
                            className="flex-1 min-w-0 border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-300" />
                          <button
                            type="button"
                            data-testid={`test-${key}`}
                            disabled={!phone.trim() || testing}
                            onClick={() => sendSingleSample(phone, role, '', '')}
                            title={phone.trim() ? `Send sample SMS to ${role}` : 'Enter a phone first'}
                            className="shrink-0 px-2.5 py-2 inline-flex items-center gap-1 text-[11px] font-medium rounded-lg border border-amber-300 text-amber-700 hover:bg-amber-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
                            {testing
                              ? (<><Loader2 className="w-3 h-3 animate-spin" />…</>)
                              : (<><BellRing className="w-3 h-3" />Test</>)}
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* Global fallback: Manager + Auditor */}
              <div>
                <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">Global Fallback</p>
                <p className="text-[10px] text-slate-400 mb-2">Used when no branch-specific phone is set below.</p>
                <div className="grid grid-cols-2 gap-3">
                  {[
                    { key: 'manager_phone', label: 'Manager Phone (fallback)', role: 'Manager (Global Fallback)' },
                    { key: 'auditor_phone', label: 'Auditor Phone (fallback)', role: 'Auditor (Global Fallback)' },
                  ].map(({ key, label, role }) => {
                    const phone = collectionRecipients[key] || '';
                    const rowKey = `${role}:`;
                    const testing = testingRowKey === rowKey;
                    return (
                      <div key={key}>
                        <label className="text-xs text-slate-600 font-medium">{label}</label>
                        <div className="mt-1 flex gap-1.5">
                          <input data-testid={`recipient-${key}`} type="tel" placeholder="09XX XXX XXXX"
                            value={phone}
                            onChange={e => setCollectionRecipients(r => ({ ...r, [key]: e.target.value }))}
                            className="flex-1 min-w-0 border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-300" />
                          <button
                            type="button"
                            data-testid={`test-${key}`}
                            disabled={!phone.trim() || testing}
                            onClick={() => sendSingleSample(phone, role, '', '')}
                            title={phone.trim() ? `Send sample SMS to ${role}` : 'Enter a phone first'}
                            className="shrink-0 px-2.5 py-2 inline-flex items-center gap-1 text-[11px] font-medium rounded-lg border border-amber-300 text-amber-700 hover:bg-amber-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
                            {testing
                              ? (<><Loader2 className="w-3 h-3 animate-spin" />…</>)
                              : (<><BellRing className="w-3 h-3" />Test</>)}
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* Per-branch: Manager + Auditor */}
              {branches.length > 0 && (
                <div>
                  <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">Branch-Specific — Manager &amp; Auditor</p>
                  <div className="space-y-3">
                    {branches.map(br => {
                      const bp = collectionRecipients.branch_phones?.[br.id] || {};
                      const set = (field, val) => setCollectionRecipients(r => ({
                        ...r,
                        branch_phones: { ...r.branch_phones, [br.id]: { ...bp, [field]: val } }
                      }));
                      const mgrTesting = testingRowKey === `Manager:${br.id}`;
                      const audTesting = testingRowKey === `Auditor:${br.id}`;
                      return (
                        <div key={br.id} className="border border-slate-200 rounded-lg p-3">
                          <p className="text-xs font-semibold text-slate-700 mb-2">{br.name}</p>
                          <div className="grid grid-cols-2 gap-2">
                            <div>
                              <label className="text-[10px] text-slate-500">Manager Phone</label>
                              <div className="mt-0.5 flex gap-1">
                                <input type="tel" placeholder="09XX XXX XXXX"
                                  data-testid={`recipient-mgr-${br.id}`}
                                  value={bp.manager_phone || ''}
                                  onChange={e => set('manager_phone', e.target.value)}
                                  className="flex-1 min-w-0 border border-slate-200 rounded-md px-2 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-amber-300" />
                                <button
                                  type="button"
                                  data-testid={`test-mgr-${br.id}`}
                                  disabled={!(bp.manager_phone || '').trim() || mgrTesting}
                                  onClick={() => sendSingleSample(bp.manager_phone, 'Manager', br.id, br.name)}
                                  title={(bp.manager_phone || '').trim() ? `Send sample to ${br.name} Manager` : 'Enter a phone first'}
                                  className="shrink-0 px-2 py-1 inline-flex items-center gap-1 text-[10px] font-medium rounded-md border border-amber-300 text-amber-700 hover:bg-amber-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
                                  {mgrTesting ? <Loader2 className="w-2.5 h-2.5 animate-spin" /> : <BellRing className="w-2.5 h-2.5" />}
                                  Test
                                </button>
                              </div>
                            </div>
                            <div>
                              <label className="text-[10px] text-slate-500">Auditor Phone</label>
                              <div className="mt-0.5 flex gap-1">
                                <input type="tel" placeholder="09XX XXX XXXX"
                                  data-testid={`recipient-aud-${br.id}`}
                                  value={bp.auditor_phone || ''}
                                  onChange={e => set('auditor_phone', e.target.value)}
                                  className="flex-1 min-w-0 border border-slate-200 rounded-md px-2 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-amber-300" />
                                <button
                                  type="button"
                                  data-testid={`test-aud-${br.id}`}
                                  disabled={!(bp.auditor_phone || '').trim() || audTesting}
                                  onClick={() => sendSingleSample(bp.auditor_phone, 'Auditor', br.id, br.name)}
                                  title={(bp.auditor_phone || '').trim() ? `Send sample to ${br.name} Auditor` : 'Enter a phone first'}
                                  className="shrink-0 px-2 py-1 inline-flex items-center gap-1 text-[10px] font-medium rounded-md border border-amber-300 text-amber-700 hover:bg-amber-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
                                  {audTesting ? <Loader2 className="w-2.5 h-2.5 animate-spin" /> : <BellRing className="w-2.5 h-2.5" />}
                                  Test
                                </button>
                              </div>
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              <div className="flex flex-wrap items-center gap-2 pt-1">
                <button data-testid="save-recipients-btn" disabled={savingRecipients}
                  onClick={async () => {
                    setSavingRecipients(true);
                    try {
                      await api.put('/settings/collection-recipients', collectionRecipients);
                      toast.success('Collection recipients saved');
                    } catch { toast.error('Failed to save'); }
                    setSavingRecipients(false);
                  }}
                  className="px-4 py-2 bg-amber-600 hover:bg-amber-700 text-white text-xs rounded-lg font-medium transition-colors disabled:opacity-50">
                  {savingRecipients ? 'Saving...' : 'Save Recipients'}
                </button>
                <button data-testid="send-sample-recipients-btn"
                  onClick={() => setSampleOpen(true)}
                  className="px-4 py-2 bg-white hover:bg-slate-50 text-slate-700 border border-slate-300 text-xs rounded-lg font-medium transition-colors inline-flex items-center gap-1.5">
                  <BellRing className="w-3.5 h-3.5 text-amber-600" />
                  Send Sample SMS
                </button>
                <p className="text-[11px] text-slate-400 ml-auto">
                  Sample goes through the same SMS queue as live notifications.
                </p>
              </div>
            </CardContent>
          </Card>

          {/* Existing SMS Trigger Settings */}
          <Card className="border-slate-200">
            <CardContent className="p-5 space-y-4">
              <h2 className="text-sm font-bold text-slate-700">SMS Trigger Settings</h2>
              <p className="text-xs text-slate-400">Enable or disable automatic SMS triggers. Disabled triggers will not generate new messages.</p>
              <div className="divide-y divide-slate-100">
                {smsSettings.map(s => (
                  <div key={s.trigger_key} className="flex items-center justify-between py-3" data-testid={`setting-${s.trigger_key}`}>
                    <div>
                      <p className="text-sm font-medium text-slate-700">{s.template_name}</p>
                      <p className="text-[10px] text-slate-400">{s.trigger_key.replace(/_/g, ' ')}</p>
                    </div>
                    <button onClick={() => toggleSetting(s.trigger_key)}
                      className={`relative w-11 h-6 rounded-full transition-colors shrink-0 ${s.enabled ? 'bg-[#1A4D2E]' : 'bg-slate-300'}`}
                      data-testid={`toggle-setting-${s.trigger_key}`}>
                      <div className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${s.enabled ? 'translate-x-5' : 'translate-x-0.5'}`} />
                    </button>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>

          {/* Branch Stock Request — Recipient Role Config */}
          <Card className="border-emerald-200" data-testid="stock-req-recipients-card">
            <CardContent className="p-5 space-y-4">
              <div>
                <h2 className="text-sm font-bold text-slate-700 flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-emerald-500" />
                  Branch Stock Request — Recipients
                </h2>
                <p className="text-xs text-slate-400 mt-1">
                  Choose who receives the SMS when a branch sends a stock request.
                  The SMS includes a public view-only link to the request.
                  In-app notifications still go to all supply-branch users + admins regardless of these toggles.
                </p>
              </div>

              <div className="divide-y divide-slate-100">
                {[
                  { key: 'include_admins', label: 'Org Admins', hint: 'All admins of the organization (recommended)' },
                  { key: 'include_supply_manager', label: 'Manager — Supply Branch', hint: 'Manager(s) assigned to the receiving branch' },
                  { key: 'include_supply_auditor', label: 'Auditor — Supply Branch', hint: 'Auditor(s) assigned to the receiving branch' },
                  { key: 'include_all_supply_users', label: 'All Supply-Branch Users', hint: 'Every active user of the supply branch (overrides Manager/Auditor above)' },
                ].map(({ key, label, hint }) => (
                  <div key={key} className="flex items-center justify-between py-3" data-testid={`stock-req-recipient-row-${key}`}>
                    <div className="pr-4">
                      <p className="text-sm font-medium text-slate-700">{label}</p>
                      <p className="text-[10px] text-slate-400 mt-0.5">{hint}</p>
                    </div>
                    <button onClick={() => toggleStockReqRecipient(key)}
                      disabled={savingStockReq}
                      className={`relative w-11 h-6 rounded-full transition-colors shrink-0 ${stockReqRecipients[key] ? 'bg-emerald-600' : 'bg-slate-300'} disabled:opacity-50`}
                      data-testid={`toggle-stock-req-${key}`}>
                      <div className={`absolute top-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${stockReqRecipients[key] ? 'translate-x-5' : 'translate-x-0.5'}`} />
                    </button>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* ═══ GATEWAY LOG TAB ═══ */}
      {activeTab === 'gateway-log' && (
        <div className="space-y-3" data-testid="gateway-log-panel">
          {/* Controls row */}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-1.5">
                <span className={`inline-block w-2 h-2 rounded-full ${gatewayLogLive ? 'bg-emerald-500 animate-pulse' : 'bg-slate-400'}`} />
                <span className={`text-xs font-medium ${gatewayLogLive ? 'text-emerald-600' : 'text-slate-400'}`}>
                  {gatewayLogLive ? 'LIVE' : 'PAUSED'}
                </span>
              </div>
              <span className="text-xs text-slate-400">{gatewayLogsTotal} total entries</span>
              {/* Level filter */}
              <div className="flex items-center gap-1">
                {['ALL', 'INFO', 'WARN', 'ERROR', 'DEBUG'].map(l => (
                  <button key={l} onClick={() => setGatewayLogLevel(l)}
                    data-testid={`log-filter-${l}`}
                    className={`px-2 py-0.5 rounded text-[10px] font-bold transition-colors ${
                      gatewayLogLevel === l
                        ? l === 'ERROR' ? 'bg-red-600 text-white'
                          : l === 'WARN' ? 'bg-amber-500 text-white'
                          : l === 'DEBUG' ? 'bg-violet-600 text-white'
                          : 'bg-slate-700 text-white'
                        : 'bg-slate-100 text-slate-500 hover:bg-slate-200'
                    }`}>{l}</button>
                ))}
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button onClick={() => setGatewayLogLive(p => !p)}
                data-testid="toggle-live"
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                  gatewayLogLive ? 'bg-emerald-100 text-emerald-700 hover:bg-emerald-200' : 'bg-slate-100 text-slate-500 hover:bg-slate-200'
                }`}>
                <Wifi size={12} /> {gatewayLogLive ? 'Live On' : 'Live Off'}
              </button>
              <button onClick={loadGatewayLogs} data-testid="refresh-logs"
                className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-100 hover:bg-slate-200 rounded-lg text-xs font-medium text-slate-600 transition-colors">
                <RefreshCw size={12} className={gatewayLogLoading ? 'animate-spin' : ''} /> Refresh
              </button>
              {user?.role === 'admin' && (
                <button onClick={clearGatewayLogs} data-testid="clear-logs"
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-red-50 hover:bg-red-100 rounded-lg text-xs font-medium text-red-500 transition-colors">
                  <X size={12} /> Clear All
                </button>
              )}
            </div>
          </div>

          {/* Terminal window */}
          <div className="bg-slate-900 rounded-xl overflow-hidden border border-slate-700" style={{ height: 'calc(100vh - 285px)' }}>
            {/* Mac-style title bar */}
            <div className="flex items-center gap-2 px-4 py-2.5 border-b border-slate-700/80 bg-slate-800">
              <div className="flex gap-1.5">
                <div className="w-3 h-3 rounded-full bg-red-400/80" />
                <div className="w-3 h-3 rounded-full bg-amber-400/80" />
                <div className="w-3 h-3 rounded-full bg-green-400/80" />
              </div>
              <div className="flex items-center gap-2 ml-2">
                <Activity size={12} className="text-slate-400" />
                <span className="text-slate-400 text-xs font-mono">AgriSMS Gateway — Activity Log</span>
              </div>
              {gatewayLogLoading && <Loader2 size={11} className="text-slate-500 animate-spin ml-auto" />}
            </div>
            {/* Log lines */}
            <div className="h-[calc(100%-42px)] overflow-y-auto p-2 space-y-px" data-testid="log-lines">
              {gatewayLogs.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-full text-center pb-8">
                  <Activity size={28} className="text-slate-700 mb-3" />
                  <p className="text-slate-500 text-sm font-mono">No logs yet</p>
                  <p className="text-slate-600 text-xs font-mono mt-1">Start the AgriSMS Gateway app to see activity here</p>
                  <p className="text-slate-700 text-[10px] font-mono mt-4">Endpoint: POST /api/sms/gateway/log</p>
                </div>
              ) : gatewayLogs.map(log => (
                <GatewayLogLine key={log.id} log={log} />
              ))}
            </div>
          </div>

          {/* Integration hint card */}
          <div className="bg-slate-800 border border-slate-700 rounded-xl p-4 text-xs space-y-2">
            <div className="flex items-center justify-between">
              <span className="font-semibold text-slate-300 flex items-center gap-2">
                <Activity size={13} className="text-emerald-400" /> Android Integration
              </span>
              <span className="text-slate-500 font-mono text-[10px]">POST /api/sms/gateway/log</span>
            </div>
            <div className="grid grid-cols-2 gap-2 text-[10px]">
              <div className="space-y-1">
                <p className="text-slate-400 font-semibold">Event types to log from your APK:</p>
                {['boot', 'poll', 'send_queued', 'sent', 'failed', 'received', 'device_sent', 'token_loaded', 'observer_start', 'observer_stop', 'error', 'db_error'].map(e => (
                  <span key={e} className={`inline-block mr-1.5 px-1.5 py-px rounded font-mono ${EVENT_COLOR[e] || 'text-slate-400'}`}>{e}</span>
                ))}
              </div>
              <div className="space-y-1">
                <p className="text-slate-400 font-semibold">Payload format:</p>
                <pre className="text-slate-400 bg-slate-900 rounded p-2 leading-relaxed text-[9px] overflow-x-auto">{`{
  "level": "INFO",
  "event_type": "sent",
  "message": "Sent → 09xxxxxxxx",
  "phone": "09xxxxxxxx",
  "queue_id": "abc-123",
  "device_id": "phone-model"
}`}</pre>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ═══ Sample SMS Preview Dialog ═══ */}
      <Dialog open={sampleOpen} onOpenChange={setSampleOpen}>
        <DialogContent className="max-w-lg" data-testid="sample-sms-dialog">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-base">
              <BellRing className="w-4 h-4 text-amber-600" />
              Send Sample SMS to Recipients
            </DialogTitle>
            <DialogDescription className="text-xs">
              One short SMS will be queued for each configured number below.
              Messages are tagged <strong>[SAMPLE]</strong> so recipients know it is a test.
            </DialogDescription>
          </DialogHeader>

          {(() => {
            const r = collectionRecipients || {};
            const rows = [];
            const pushRow = (phone, role, branchName) => {
              const p = (phone || '').trim();
              if (!p) return;
              rows.push({ phone: p, role, branchName });
            };
            pushRow(r.owner_phone,   'Owner',   '');
            pushRow(r.admin_phone,   'Admin',   '');
            pushRow(r.manager_phone, 'Manager (Global Fallback)', '');
            pushRow(r.auditor_phone, 'Auditor (Global Fallback)', '');
            const bp = r.branch_phones || {};
            (branches || []).forEach(br => {
              const row = bp[br.id] || {};
              pushRow(row.manager_phone, 'Manager', br.name);
              pushRow(row.auditor_phone, 'Auditor', br.name);
            });
            // De-dupe by phone (first occurrence wins)
            const seen = new Set();
            const unique = rows.filter(x => {
              if (seen.has(x.phone)) return false;
              seen.add(x.phone);
              return true;
            });

            if (unique.length === 0) {
              return (
                <div className="p-4 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-800">
                  No recipient phone numbers configured yet. Fill in at least one number above, then try again.
                </div>
              );
            }
            return (
              <>
                <div className="rounded-lg border border-slate-200 bg-slate-50 max-h-64 overflow-y-auto">
                  <table className="w-full text-xs">
                    <thead className="text-[10px] text-slate-500 uppercase tracking-wide">
                      <tr>
                        <th className="text-left px-3 py-2">Role</th>
                        <th className="text-left px-3 py-2">Branch</th>
                        <th className="text-left px-3 py-2">Phone</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-200">
                      {unique.map((x, i) => (
                        <tr key={i} data-testid={`sample-row-${i}`} className="bg-white">
                          <td className="px-3 py-2 font-medium text-slate-700">{x.role}</td>
                          <td className="px-3 py-2 text-slate-500">{x.branchName || 'All Branches'}</td>
                          <td className="px-3 py-2 font-mono text-slate-800">{x.phone}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="mt-3 p-3 bg-slate-900 text-slate-100 rounded-lg text-[11px] leading-relaxed font-mono">
                  <p className="text-slate-400 mb-1 text-[10px] uppercase tracking-wide">Sample message preview</p>
                  [SAMPLE] Hi &lt;Role&gt;, ito ay test SMS mula sa &lt;Company&gt; — &lt;Branch&gt;. Kumpirmado na tama ang naka-configure na numero upang makatanggap ng Collection notifications. Walang aksyon na kailangan. Sent by: {user?.full_name || user?.email || '—'}.
                </div>
                <div className="flex items-center justify-end gap-2 pt-3">
                  <button
                    data-testid="sample-cancel-btn"
                    onClick={() => setSampleOpen(false)}
                    className="px-3 py-1.5 text-xs rounded-lg border border-slate-300 text-slate-600 hover:bg-slate-50">
                    Cancel
                  </button>
                  <button
                    data-testid="sample-confirm-btn"
                    disabled={sendingSample}
                    onClick={async () => {
                      setSendingSample(true);
                      try {
                        const res = await api.post('/sms/send-sample-recipients', collectionRecipients);
                        const n = res.data?.queued || 0;
                        toast.success(`Queued ${n} sample SMS — check the Queue tab to track delivery.`);
                        setSampleOpen(false);
                        loadQueue();
                        loadStats();
                      } catch (err) {
                        toast.error(err?.response?.data?.detail || 'Failed to queue sample SMS');
                      }
                      setSendingSample(false);
                    }}
                    className="px-4 py-1.5 bg-amber-600 hover:bg-amber-700 disabled:opacity-50 text-white text-xs rounded-lg font-medium inline-flex items-center gap-1.5">
                    {sendingSample
                      ? (<><Loader2 className="w-3.5 h-3.5 animate-spin" /> Queueing…</>)
                      : (<><Send className="w-3.5 h-3.5" /> Send {unique.length} Sample {unique.length === 1 ? 'SMS' : 'SMSes'}</>)}
                  </button>
                </div>
              </>
            );
          })()}
        </DialogContent>
      </Dialog>
    </div>
  );
}
