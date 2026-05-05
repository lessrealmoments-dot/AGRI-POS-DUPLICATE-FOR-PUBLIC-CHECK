/**
 * NotificationsPage — Full-page notification center.
 * Shows category summary cards + filterable notification list.
 * Accessed via the bell icon in the nav header.
 */
import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, useAuth } from '../contexts/AuthContext';
import { formatPHP } from '../lib/utils';
import { Badge } from '../components/ui/badge';
import { Button } from '../components/ui/button';
import { ScrollArea } from '../components/ui/scroll-area';
import ReviewDetailDialog from '../components/ReviewDetailDialog';
import {
  Bell, CheckCheck, Shield, AlertTriangle, Tag, Zap,
  ArrowLeftRight, Banknote, TrendingDown, Package,
  CreditCard, RefreshCw, ChevronLeft, X,
  ShieldAlert, ClipboardCheck, Receipt, BarChart3, FileWarning,
  User, MapPin, Smartphone, FileText, ExternalLink, Lock
} from 'lucide-react';

import { formatDate } from '../lib/dateFormat';
// ── Category config ───────────────────────────────────────────────────────────
const CATEGORIES = [
  {
    key: 'all',
    label: 'All',
    icon: Bell,
    color: 'text-slate-600',
    bg: 'bg-slate-50',
    border: 'border-slate-200',
    activeBg: 'bg-slate-800',
    activeText: 'text-white',
  },
  {
    key: 'security',
    label: 'Security',
    icon: ShieldAlert,
    color: 'text-red-600',
    bg: 'bg-red-50',
    border: 'border-red-200',
    activeBg: 'bg-red-600',
    activeText: 'text-white',
  },
  {
    key: 'action',
    label: 'Action Required',
    icon: AlertTriangle,
    color: 'text-amber-600',
    bg: 'bg-amber-50',
    border: 'border-amber-200',
    activeBg: 'bg-amber-600',
    activeText: 'text-white',
  },
  {
    key: 'approvals',
    label: 'Approvals & Overrides',
    icon: Tag,
    color: 'text-violet-600',
    bg: 'bg-violet-50',
    border: 'border-violet-200',
    activeBg: 'bg-violet-600',
    activeText: 'text-white',
  },
  {
    key: 'operations',
    label: 'Operations',
    icon: ArrowLeftRight,
    color: 'text-blue-600',
    bg: 'bg-blue-50',
    border: 'border-blue-200',
    activeBg: 'bg-blue-600',
    activeText: 'text-white',
  },
  {
    key: 'finance',
    label: 'Finance',
    icon: Banknote,
    color: 'text-emerald-600',
    bg: 'bg-emerald-50',
    border: 'border-emerald-200',
    activeBg: 'bg-emerald-700',
    activeText: 'text-white',
  },
];

// ── Per-type icon and color ───────────────────────────────────────────────────
const TYPE_CONFIG = {
  security_alert:           { icon: ShieldAlert,    color: 'text-red-600',     bg: 'bg-red-100'     },
  po_receipt_review:        { icon: ClipboardCheck, color: 'text-amber-600',   bg: 'bg-amber-100'   },
  transfer_variance_review: { icon: AlertTriangle,  color: 'text-amber-600',   bg: 'bg-amber-100'   },
  incident_created:         { icon: AlertTriangle,  color: 'text-amber-700',   bg: 'bg-amber-100'   },
  pricing_issue:            { icon: TrendingDown,   color: 'text-amber-600',   bg: 'bg-amber-100'   },
  branch_stock_request:     { icon: Package,        color: 'text-blue-600',    bg: 'bg-blue-100'    },
  negative_stock_override:  { icon: AlertTriangle,  color: 'text-red-500',     bg: 'bg-red-100'     },
  transfer_disputed:        { icon: X,              color: 'text-red-600',     bg: 'bg-red-100'     },
  credit_sale:              { icon: CreditCard,     color: 'text-violet-500',  bg: 'bg-violet-100'  },
  price_override:           { icon: Zap,            color: 'text-violet-500',  bg: 'bg-violet-100'  },
  discount_given:           { icon: Tag,            color: 'text-violet-600',  bg: 'bg-violet-100'  },
  below_cost_sale:          { icon: TrendingDown,   color: 'text-red-500',     bg: 'bg-red-100'     },
  admin_action:             { icon: Shield,         color: 'text-slate-500',   bg: 'bg-slate-100'   },
  return_pullout_loss:      { icon: Receipt,        color: 'text-amber-500',   bg: 'bg-amber-100'   },
  employee_advance:         { icon: Banknote,       color: 'text-violet-500',  bg: 'bg-violet-100'  },
  reservation_expired:      { icon: Package,        color: 'text-slate-500',   bg: 'bg-slate-100'   },
  transfer_incoming:        { icon: ArrowLeftRight, color: 'text-blue-600',    bg: 'bg-blue-100'    },
  transfer_accepted:        { icon: ClipboardCheck, color: 'text-emerald-600', bg: 'bg-emerald-100' },
  ap_payment:               { icon: Banknote,       color: 'text-emerald-600', bg: 'bg-emerald-100' },
  internal_invoice_due:     { icon: AlertTriangle,  color: 'text-amber-600',   bg: 'bg-amber-100'   },
  internal_invoice_overdue: { icon: AlertTriangle,  color: 'text-red-600',     bg: 'bg-red-100'     },
  internal_invoice_paid:    { icon: BarChart3,      color: 'text-emerald-600', bg: 'bg-emerald-100' },
  compliance_deadline:      { icon: FileWarning,    color: 'text-orange-600',  bg: 'bg-orange-100'  },
};

const SEVERITY_BADGE = {
  critical: 'bg-red-100 text-red-700 border-red-300',
  warning:  'bg-amber-100 text-amber-700 border-amber-300',
  info:     'bg-slate-100 text-slate-500 border-slate-200',
};

function timeAgo(isoStr) {
  const diff = Date.now() - new Date(isoStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d ago`;
  return formatDate(isoStr);
}

// ── Security Alert detail inline block ───────────────────────────────────────
function SecurityAlertDetail({ n, onOpenDoc }) {
  const m = n.metadata || {};
  const isQR   = m.alert_source === 'qr_terminal';
  const isAuth = m.alert_source === 'authenticated_pin';
  const locked = m.locked;

  const cardBase = 'rounded-lg border p-3 text-[11px] space-y-1.5 flex-1 min-w-0';

  if (isAuth) {
    const canOpen = !!(m.doc_id && m.doc_type);
    return (
      <div className="mt-2 flex gap-2">
        <div className={`${cardBase} border-red-100 bg-red-50/60`}>
          <p className="font-semibold text-red-700 flex items-center gap-1 text-[10px] uppercase tracking-wide mb-1">
            <User size={10} /> Who
          </p>
          <AlertRow label="Name"   value={m.user_name} />
          <AlertRow label="Role"   value={m.user_role ? m.user_role.charAt(0).toUpperCase() + m.user_role.slice(1) : '—'} />
          <AlertRow label="Email"  value={m.user_email || '—'} mono />
          <AlertRow label="Branch" value={m.branch_name || '—'} />
        </div>
        <div className={`${cardBase} border-slate-100 bg-slate-50/60`}>
          <p className="font-semibold text-slate-600 flex items-center gap-1 text-[10px] uppercase tracking-wide mb-1">
            <Shield size={10} /> What
          </p>
          <AlertRow label="Action" value={m.action_label || '—'} />
          <AlertRow label="Detail" value={m.context || '—'} />
          <AlertRow label="Fails"  value={`${m.failure_count}x in 30 min`} />
          <div className="flex items-center justify-between pt-1 gap-2 flex-wrap">
            <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full ${
              m.severity === 'high' ? 'bg-red-100 text-red-700' : 'bg-amber-100 text-amber-700'
            }`}>
              {m.severity === 'high' ? 'HIGH — Possible brute force' : 'Warning — Monitor'}
            </span>
            {canOpen && (
              <button
                onClick={() => onOpenDoc(m.doc_id, m.doc_type)}
                className="flex items-center gap-1 text-[10px] font-semibold text-blue-600 hover:text-blue-800 bg-blue-50 hover:bg-blue-100 border border-blue-200 rounded-md px-2 py-0.5 transition-colors"
                data-testid="security-alert-view-receipt-btn"
              >
                <FileText size={10} /> View Receipt
              </button>
            )}
          </div>
        </div>
      </div>
    );
  }

  if (isQR) {
    const canOpen = !!(m.doc_id && m.doc_type);
    const docTypeLabel = { invoice: 'Invoice', purchase_order: 'Purchase Order', branch_transfer: 'Branch Transfer' };
    return (
      <div className="mt-2 space-y-2">
        <div className="flex gap-2">
          <div className={`${cardBase} border-blue-100 bg-blue-50/60`}>
            <p className="font-semibold text-blue-700 flex items-center gap-1 text-[10px] uppercase tracking-wide mb-1">
              <Smartphone size={10} /> Terminal
            </p>
            <AlertRow label="Device" value={m.terminal_label || '—'} />
            <AlertRow label="Branch" value={m.branch_name || '—'} />
            <AlertRow label="Action" value={m.action_label || '—'} />
            <AlertRow label="Fails"  value={`${m.failure_count}x`} />
          </div>
          <div className={`${cardBase} border-slate-100 bg-slate-50/60`}>
            <p className="font-semibold text-slate-600 flex items-center gap-1 text-[10px] uppercase tracking-wide mb-1">
              <FileText size={10} /> Document
            </p>
            <AlertRow label="Type" value={docTypeLabel[m.doc_type] || m.doc_type || '—'} />
            <AlertRow label="Number" value={
              canOpen ? (
                <button
                  onClick={() => onOpenDoc(m.doc_id, m.doc_type)}
                  className="font-mono text-blue-600 hover:underline flex items-center gap-0.5"
                  data-testid="security-alert-view-doc-btn"
                >
                  {m.doc_number || m.doc_code} <ExternalLink size={9} />
                </button>
              ) : (
                <span className="font-mono">{m.doc_number || m.doc_code || '—'}</span>
              )
            } />
            <AlertRow label={m.doc_type === 'purchase_order' ? 'Supplier' : 'Customer'} value={m.counterparty || '—'} />
            {m.doc_amount != null && <AlertRow label="Amount" value={formatPHP(m.doc_amount)} />}
          </div>
        </div>
        {locked && (
          <div className="flex items-center gap-1.5 text-[10px] font-semibold text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-1.5">
            <Lock size={11} /> Document locked for 15 minutes — too many failed attempts
          </div>
        )}
      </div>
    );
  }

  // Fallback for old-format alerts (no alert_source)
  return (
    <div className="mt-2 rounded-lg border border-slate-100 bg-slate-50/60 p-2.5 text-[11px] text-slate-600 space-y-0.5">
      {m.context && <p><span className="font-medium">Context:</span> {m.context}</p>}
      {m.user_name && <p><span className="font-medium">User:</span> {m.user_name}</p>}
      {m.failure_count && <p><span className="font-medium">Failures:</span> {m.failure_count}</p>}
    </div>
  );
}

function AlertRow({ label, value, mono }) {
  return (
    <div className="flex items-start justify-between gap-2">
      <span className="text-slate-400 shrink-0">{label}:</span>
      <span className={`text-slate-700 text-right break-all ${mono ? 'font-mono text-[10px]' : ''}`}>
        {value ?? '—'}
      </span>
    </div>
  );
}

// ── Compliance detail inline block ───────────────────────────────────────────
function ComplianceDetail({ n }) {
  const m = n.metadata || {};
  const statusColors = {
    expired:        'text-red-700 bg-red-50 border-red-200',
    expiring:       'text-amber-700 bg-amber-50 border-amber-200',
    missing_filing: 'text-orange-700 bg-orange-50 border-orange-200',
  };
  const statusLabels = {
    expired:        'EXPIRED',
    expiring:       `Expires in ${m.days_left} day${m.days_left !== 1 ? 's' : ''}`,
    missing_filing: `${m.month} — Not Filed`,
  };
  const colorClass = statusColors[m.status] || statusColors.missing_filing;
  return (
    <div className={`mt-2 rounded-lg border p-2.5 text-[10px] space-y-1 ${colorClass}`}>
      <div className="flex justify-between font-semibold">
        <span>{m.sub_category_label || m.sub_category}</span>
        <span>{statusLabels[m.status]}</span>
      </div>
      {m.valid_until && (
        <div className="text-[10px] opacity-80">
          {m.status === 'expired' ? 'Expired on' : 'Valid until'}: {m.valid_until}
        </div>
      )}
      {m.month && (
        <div className="text-[10px] opacity-80">Period: {m.month}</div>
      )}
    </div>
  );
}

// ── Discount detail inline block ──────────────────────────────────────────────
function DiscountDetail({ n }) {
  const m = n.metadata || {};
  const items = m.items || [];
  const weekCount = m.cashier_discounts_this_week;
  if (!items.length) return null;
  return (
    <div className="mt-2 rounded-lg border border-violet-100 bg-violet-50/60 p-2.5 space-y-1.5">
      <div className="flex items-center justify-between text-[10px] text-violet-600">
        <span className="font-semibold">Invoice: {m.invoice_number} · {m.customer_name || 'Walk-in'}</span>
        {weekCount > 1 && (
          <Badge className="text-[9px] bg-amber-100 text-amber-700 border-amber-300">
            {weekCount} discounts this week by {m.cashier_name}
          </Badge>
        )}
      </div>
      <table className="w-full text-[10px]">
        <thead>
          <tr className="text-violet-400 uppercase">
            <th className="text-left pb-0.5">Product</th>
            <th className="text-right pb-0.5">Orig.</th>
            <th className="text-right pb-0.5">Sold</th>
            <th className="text-right pb-0.5">Discount</th>
            <th className="text-right pb-0.5">Capital</th>
          </tr>
        </thead>
        <tbody>
          {items.slice(0, 5).map((item, i) => {
            const belowCost = item.net_per_unit < item.capital;
            return (
              <tr key={i} className={belowCost ? 'text-red-600 font-semibold' : 'text-slate-700'}>
                <td className="py-0.5 truncate max-w-[120px]">{item.product_name}</td>
                <td className="text-right font-mono">{formatPHP(item.original_price)}</td>
                <td className="text-right font-mono">{formatPHP(item.sold_price)}</td>
                <td className="text-right font-mono text-violet-600">
                  -{item.discount_value}{item.discount_type === 'percent' ? '%' : '₱'}
                  {' '}(−{formatPHP(item.discount_amount)})
                </td>
                <td className={`text-right font-mono ${belowCost ? 'text-red-600' : 'text-slate-400'}`}>
                  {formatPHP(item.capital)}{belowCost ? ' ⚠' : ''}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="flex justify-between text-[10px] font-semibold border-t border-violet-100 pt-1">
        <span className="text-violet-700">Total discounted: {formatPHP(m.total_discount)}</span>
        <span className="text-slate-500">Grand total: {formatPHP(m.grand_total)}</span>
      </div>
      {m.has_below_cost && (
        <div className="text-[10px] text-red-600 font-semibold flex items-center gap-1">
          <AlertTriangle size={9} /> One or more items sold BELOW capital cost
        </div>
      )}
    </div>
  );
}

// ── AP Payment detail inline block ───────────────────────────────────────────
function ApPaymentDetail({ n }) {
  const m = n.metadata || {};
  if (!m.po_number) return null;
  return (
    <div className="mt-2 rounded-lg border border-emerald-100 bg-emerald-50/60 p-2.5 text-[10px] space-y-0.5">
      <div className="flex justify-between text-emerald-700">
        <span><span className="font-semibold">{m.po_number}</span> · {m.vendor}</span>
        <span className="font-mono font-bold">−{formatPHP(m.amount)}</span>
      </div>
      <div className="flex justify-between text-slate-500">
        <span>From: <span className="font-medium capitalize">{m.fund_source}</span> wallet</span>
        <span>{m.payment_status === 'paid' ? 'Fully paid' : `₱${Number(m.new_balance).toFixed(2)} remaining`}</span>
      </div>
      <div className="text-slate-400">Authorized by: {m.authorized_by}</div>
    </div>
  );
}

// ── Notification row ──────────────────────────────────────────────────────────
function NotifRow({ n, onMarkRead, onOpenDoc }) {
  const cfg = TYPE_CONFIG[n.type] || TYPE_CONFIG.admin_action;
  const Icon = cfg.icon;
  const [expanded, setExpanded] = useState(false);
  const hasDetail = ['discount_given', 'below_cost_sale', 'ap_payment', 'compliance_deadline', 'security_alert'].includes(n.type);

  return (
    <div
      className={`px-5 py-3.5 border-b border-slate-50 last:border-0 transition-colors ${
        n.is_read ? 'bg-white hover:bg-slate-50/60' : 'bg-blue-50/30 hover:bg-blue-50/50'
      }`}
      data-testid={`notif-${n.id}`}
    >
      <div className="flex gap-3">
        {/* Icon */}
        <div className={`w-8 h-8 rounded-full ${cfg.bg} flex items-center justify-center shrink-0 mt-0.5`}>
          <Icon size={14} className={cfg.color} />
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-2">
            <div className="flex-1 min-w-0">
              <p className={`text-sm leading-snug ${n.is_read ? 'text-slate-600' : 'text-slate-800 font-medium'}`}>
                {n.message}
              </p>
              <div className="flex items-center flex-wrap gap-x-2 gap-y-0.5 mt-1">
                <span className="text-[11px] text-slate-400">{timeAgo(n.created_at)}</span>
                {n.branch_name && <span className="text-[11px] text-slate-400">· {n.branch_name}</span>}
                <Badge className={`text-[9px] border ${SEVERITY_BADGE[n.severity] || SEVERITY_BADGE.info}`}>
                  {n.severity}
                </Badge>
                {hasDetail && (
                  <button
                    onClick={() => setExpanded(e => !e)}
                    className="text-[11px] text-blue-500 hover:underline font-medium"
                  >
                    {expanded ? 'Hide detail ↑' : 'View detail ↓'}
                  </button>
                )}
              </div>
            </div>
            <div className="flex items-center gap-1.5 shrink-0">
              {!n.is_read && <div className="w-2 h-2 rounded-full bg-blue-500" />}
              {!n.is_read && (
                <button onClick={() => onMarkRead(n.id)}
                  className="text-[10px] text-slate-400 hover:text-slate-600 p-1 rounded hover:bg-slate-100">
                  <CheckCheck size={12} />
                </button>
              )}
            </div>
          </div>

          {/* Expandable detail */}
          {expanded && n.type === 'discount_given' && <DiscountDetail n={n} />}
          {expanded && n.type === 'below_cost_sale' && <DiscountDetail n={n} />}
          {expanded && n.type === 'ap_payment' && <ApPaymentDetail n={n} />}
          {expanded && n.type === 'compliance_deadline' && <ComplianceDetail n={n} />}
          {expanded && n.type === 'security_alert' && <SecurityAlertDetail n={n} onOpenDoc={onOpenDoc} />}
        </div>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function NotificationsPage() {
  const { user } = useAuth();
  const navigate = useNavigate();

  const [activeCategory, setActiveCategory] = useState('all');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [reviewRecord, setReviewRecord] = useState(null); // { id, type }

  const load = useCallback(async (cat = activeCategory) => {
    setLoading(true);
    try {
      const params = { limit: 100 };
      if (cat !== 'all') params.category = cat;
      const res = await api.get('/notifications', { params });
      setData(res.data);
    } catch {}
    setLoading(false);
  }, [activeCategory]);

  useEffect(() => { load(activeCategory); }, [activeCategory]); // eslint-disable-line

  const markRead = async (id) => {
    await api.put(`/notifications/${id}/read`);
    setData(prev => prev ? {
      ...prev,
      notifications: prev.notifications.map(n => n.id === id ? { ...n, is_read: true } : n),
      unread_count: Math.max(0, (prev.unread_count || 1) - 1),
    } : prev);
  };

  const markAllRead = async () => {
    await api.put('/notifications/mark-all-read');
    setData(prev => prev ? {
      ...prev,
      notifications: prev.notifications.map(n => ({ ...n, is_read: true })),
      unread_count: 0,
    } : prev);
  };

  const counts = data?.category_counts || {};
  const notifications = data?.notifications || [];
  const totalUnread = data?.unread_count || 0;
  // globalTotal always reflects the full count regardless of active filter
  const [globalTotal, setGlobalTotal] = useState(0);
  useEffect(() => {
    if (activeCategory === 'all' && data?.total) setGlobalTotal(data.total);
  }, [activeCategory, data]);

  return (
    <div className="flex flex-col h-[calc(100vh-60px)] bg-white" data-testid="notifications-page">

      {/* ── Page Header ── */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200 bg-white shrink-0">
        <div className="flex items-center gap-3">
          <button onClick={() => navigate(-1)}
            className="p-1.5 rounded-lg hover:bg-slate-100 transition-colors text-slate-500">
            <ChevronLeft size={18} />
          </button>
          <div>
            <h1 className="text-xl font-bold tracking-tight" style={{ fontFamily: 'Manrope' }}>
              Notification Center
            </h1>
            <p className="text-xs text-slate-400 mt-0.5">
              {totalUnread > 0 ? `${totalUnread} unread` : 'All caught up'}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => load(activeCategory)} className="h-8">
            <RefreshCw size={13} className="mr-1" /> Refresh
          </Button>
          {totalUnread > 0 && (
            <Button variant="outline" size="sm" onClick={markAllRead} className="h-8 text-blue-600 border-blue-200 hover:bg-blue-50">
              <CheckCheck size={13} className="mr-1" /> Mark all read
            </Button>
          )}
        </div>
      </div>

      {/* ── Category Summary Cards ── */}
      <div className="px-6 py-4 border-b border-slate-100 bg-slate-50/50 shrink-0">
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          {CATEGORIES.map(cat => {
            const Icon = cat.icon;
            const isActive = activeCategory === cat.key;
            const catData = cat.key === 'all'
              ? { total: globalTotal || data?.total || 0, unread: totalUnread }
              : counts[cat.key] || { total: 0, unread: 0 };
            return (
              <button key={cat.key} onClick={() => setActiveCategory(cat.key)}
                data-testid={`category-card-${cat.key}`}
                className={`rounded-xl border p-3 text-left transition-all ${
                  isActive
                    ? `${cat.activeBg} ${cat.activeText} border-transparent shadow-sm`
                    : `bg-white ${cat.border} hover:shadow-sm`
                }`}>
                <div className="flex items-center justify-between mb-2">
                  <div className={`w-7 h-7 rounded-lg flex items-center justify-center ${isActive ? 'bg-white/20' : cat.bg}`}>
                    <Icon size={14} className={isActive ? 'text-current' : cat.color} />
                  </div>
                  {catData.unread > 0 && (
                    <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-full ${
                      isActive ? 'bg-white/25 text-current' : 'bg-red-100 text-red-600'
                    }`}>
                      {catData.unread}
                    </span>
                  )}
                </div>
                <p className={`text-xs font-semibold leading-tight ${isActive ? 'text-current' : 'text-slate-700'}`}>
                  {cat.label}
                </p>
                <p className={`text-[10px] mt-0.5 ${isActive ? 'text-current/70' : 'text-slate-400'}`}>
                  {catData.total} total
                </p>
              </button>
            );
          })}
        </div>
      </div>

      {/* ── Notification List ── */}
      {loading ? (
        <div className="flex-1 flex items-center justify-center">
          <RefreshCw size={20} className="animate-spin text-slate-300 mr-2" />
          <span className="text-slate-400 text-sm">Loading…</span>
        </div>
      ) : notifications.length === 0 ? (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center py-16">
            <Bell size={40} className="mx-auto mb-3 text-slate-200" />
            <p className="text-slate-400 text-sm">No notifications in this category</p>
          </div>
        </div>
      ) : (
        <ScrollArea className="flex-1">
          <div className="divide-y divide-slate-50">
            {notifications.map(n => (
              <NotifRow key={n.id} n={n} onMarkRead={markRead} onOpenDoc={(id, type) => setReviewRecord({ id, type })} />
            ))}
          </div>
          <div className="py-4 text-center text-xs text-slate-400 border-t border-slate-100">
            Showing {notifications.length} notifications
          </div>
        </ScrollArea>
      )}

      {/* ── Review Dialog (opened from security alert doc links) ── */}
      <ReviewDetailDialog
        open={!!reviewRecord}
        onClose={() => setReviewRecord(null)}
        recordType={reviewRecord?.type}
        recordId={reviewRecord?.id}
        showReviewAction={reviewRecord?.type === 'purchase_order' || reviewRecord?.type === 'branch_transfer'}
        showPayAction={false}
        onReviewed={() => setReviewRecord(null)}
      />
    </div>
  );
}
