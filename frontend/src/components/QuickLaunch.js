import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ShoppingCart, Receipt, FileText, Wallet,
  Banknote, ArrowRightLeft, Lock,
  ChevronDown, ChevronUp, Zap,
} from 'lucide-react';

/**
 * QuickLaunch — collapsible row of the 7 most-used manager actions.
 * - Pinned at the top of the Dashboard for fast access
 * - Collapsed state persists per-user in localStorage
 * - Purely navigation; doesn't alter any business state
 */
const ACTIONS = [
  {
    key: 'sales',
    label: 'Sales',
    icon: ShoppingCart,
    path: '/sales-new',
    color: 'bg-emerald-50 text-emerald-700 hover:bg-emerald-100 border-emerald-200',
    iconBg: 'bg-emerald-600',
  },
  {
    key: 'receive-payment',
    label: 'Receive Payment',
    icon: Receipt,
    path: '/payments',
    color: 'bg-blue-50 text-blue-700 hover:bg-blue-100 border-blue-200',
    iconBg: 'bg-blue-600',
  },
  {
    key: 'purchase-order',
    label: 'Purchase Order',
    icon: FileText,
    path: '/purchase-orders',
    color: 'bg-purple-50 text-purple-700 hover:bg-purple-100 border-purple-200',
    iconBg: 'bg-purple-600',
  },
  {
    key: 'pay-supplier',
    label: 'Pay Supplier',
    icon: Wallet,
    path: '/pay-supplier',
    color: 'bg-indigo-50 text-indigo-700 hover:bg-indigo-100 border-indigo-200',
    iconBg: 'bg-indigo-600',
  },
  {
    key: 'expense',
    label: 'Expense',
    icon: Banknote,
    path: '/accounting',
    color: 'bg-rose-50 text-rose-700 hover:bg-rose-100 border-rose-200',
    iconBg: 'bg-rose-600',
  },
  {
    key: 'branch-transfer',
    label: 'Branch Transfer',
    icon: ArrowRightLeft,
    path: '/branch-transfers',
    color: 'bg-amber-50 text-amber-700 hover:bg-amber-100 border-amber-200',
    iconBg: 'bg-amber-600',
  },
  {
    key: 'close-wizard',
    label: 'Closing Wizard',
    icon: Lock,
    path: '/close-wizard',
    color: 'bg-slate-100 text-slate-800 hover:bg-slate-200 border-slate-300',
    iconBg: 'bg-[#1A4D2E]',
  },
];

const STORAGE_KEY = 'agripos_quick_launch_collapsed';

export default function QuickLaunch() {
  const navigate = useNavigate();
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem(STORAGE_KEY) === '1'; }
    catch { return false; }
  });

  const toggle = () => {
    const next = !collapsed;
    setCollapsed(next);
    try { localStorage.setItem(STORAGE_KEY, next ? '1' : '0'); }
    catch { /* localStorage unavailable */ }
  };

  return (
    <div
      className="rounded-xl border border-slate-200 bg-white overflow-hidden"
      data-testid="quick-launch"
    >
      {/* Header — clickable to toggle */}
      <button
        type="button"
        onClick={toggle}
        className="w-full flex items-center justify-between px-3 py-2 bg-gradient-to-r from-emerald-50 via-white to-white hover:from-emerald-100 transition-colors"
        data-testid="quick-launch-toggle"
      >
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded-md bg-[#1A4D2E] flex items-center justify-center">
            <Zap size={13} className="text-white" />
          </div>
          <span className="text-sm font-semibold text-slate-800" style={{ fontFamily: 'Manrope' }}>
            Quick Launch
          </span>
          <span className="text-[10px] text-slate-400">7 most-used actions</span>
        </div>
        {collapsed ? (
          <ChevronDown size={16} className="text-slate-500" />
        ) : (
          <ChevronUp size={16} className="text-slate-500" />
        )}
      </button>

      {/* Body */}
      {!collapsed && (
        <div className="p-3 border-t border-slate-100">
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-2">
            {ACTIONS.map(action => {
              const Icon = action.icon;
              return (
                <button
                  key={action.key}
                  type="button"
                  onClick={() => navigate(action.path)}
                  className={`group flex flex-col items-center justify-center gap-1.5 py-3 px-2 rounded-lg border transition-all ${action.color}`}
                  data-testid={`quick-launch-${action.key}`}
                >
                  <div
                    className={`w-9 h-9 rounded-lg ${action.iconBg} flex items-center justify-center text-white shadow-sm group-hover:scale-105 transition-transform`}
                  >
                    <Icon size={17} />
                  </div>
                  <span className="text-xs font-medium leading-tight text-center">
                    {action.label}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
