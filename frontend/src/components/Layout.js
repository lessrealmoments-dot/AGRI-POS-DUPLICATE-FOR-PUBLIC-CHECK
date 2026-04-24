import { useState, useEffect, useRef } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useAuth, api } from '../contexts/AuthContext';
import { useOnlineStatus } from '../lib/useOnlineStatus';
import { Button } from './ui/button';
import { ScrollArea } from './ui/scroll-area';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from './ui/dropdown-menu';
import { Separator } from './ui/separator';
import {
  LayoutDashboard, Building2, Package, Warehouse, ShoppingCart,
  Users, Tags, Receipt, Calculator, Settings, Menu, X,
  ChevronDown, LogOut, User, Store, Truck, Shield, ClipboardList, UserCog, Briefcase, Upload, Lock, ArrowRight, BarChart3, RotateCcw, ShieldCheck, WifiOff, FileText, AlertTriangle, HardDrive, ScanBarcode, Search, BookOpen, FolderOpen, MessageSquare, Smartphone, Sprout
} from 'lucide-react';
import OfflineIndicator from './OfflineIndicator';
import NotificationBell from './NotificationBell';
import QuickSearch from './QuickSearch';
import SectionOverrideDialog from './SectionOverrideDialog';
import { toast } from 'sonner';

// offlineOk: 'full' = works offline | 'readonly' = view cached only | false/omit = requires internet
// featureFlag: subscription feature key required to access this nav item (null = always shown)
const NAV_SECTIONS = [
  {
    label: null,
    items: [
      { path: '/dashboard', label: 'Dashboard', icon: LayoutDashboard, perm: null, offlineOk: 'readonly' },
      { path: '/find-transaction', label: 'Find Transaction', icon: Search, perm: null },
      { path: '/messages', label: 'SMS Messages', icon: MessageSquare, perm: 'settings.edit', adminOnly: true },
    ],
  },
  {
    label: 'Transactions',
    items: [
      { path: '/sales-new',  label: 'Sales',             icon: ShoppingCart, perm: 'sales.view',       offlineOk: 'full' },
      { path: '/sales',      label: 'Sales History',     icon: ClipboardList, perm: 'sales.view' },
      { path: '/returns',    label: 'Return & Refund',   icon: RotateCcw,    perm: 'sales.view' },
      { path: '/customers',  label: 'Customers',         icon: Users,        perm: 'customers.view',   offlineOk: 'readonly' },
      { path: '/payments',   label: 'Receive Payments',  icon: Tags,         perm: 'accounting.view' },
      { path: '/crop-credits', label: 'Crop Credits',    icon: Sprout,       perm: 'pos.sell' },
      { path: '/expenses',   label: 'Expenses',          icon: Receipt,      perm: 'accounting.view' },
    ],
  },
  {
    label: 'Inventory & Purchasing',
    items: [
      { path: '/products',        label: 'Products',        icon: Package,   perm: 'products.view',        offlineOk: 'readonly' },
      { path: '/inventory',       label: 'Inventory',       icon: Warehouse, perm: 'inventory.view',       offlineOk: 'readonly' },
      { path: '/pending-releases', label: 'Pending Releases', icon: Package, perm: 'inventory.view' },
      { path: '/purchase-orders', label: 'Purchase Orders', icon: Truck,     perm: 'purchase_orders.view', featureFlag: 'purchase_orders' },
      { path: '/pay-supplier',    label: 'Pay Supplier',    icon: Building2, perm: 'purchase_orders.view', featureFlag: 'purchase_orders' },
      { path: '/suppliers',       label: 'Suppliers',       icon: Truck,     perm: 'suppliers.view',       featureFlag: 'supplier_management' },
      { path: '/barcode-print',   label: 'Print Barcodes',  icon: ScanBarcode, perm: 'products.view' },
      { path: '/barcode-manage',  label: 'Barcode Manager', icon: ScanBarcode, perm: 'products.edit' },
    ],
  },
  {
    label: 'Branches',
    items: [
      { path: '/branches',         label: 'Branches',         icon: Building2, perm: 'branches.view' },
      { path: '/branch-transfers', label: 'Branch Transfers', icon: ArrowRight, perm: 'branches.view', featureFlag: 'branch_transfers' },
      { path: '/internal-invoices', label: 'Internal Invoices', icon: FileText, perm: 'branches.view', featureFlag: 'branch_transfers' },
    ],
  },
  {
    label: 'End of Day',
    items: [
      { path: '/fund-management', label: 'Fund Management',  icon: Calculator, perm: 'accounting.manage_funds', featureFlag: 'full_fund_management' },
      { path: '/count-sheets',    label: 'Count Sheets',     icon: ClipboardList, perm: 'count_sheets.view' },
      { path: '/daily-ops',       label: 'Daily Operations', icon: Receipt,    perm: 'reports.view' },
      { path: '/close-wizard',    label: 'Close Wizard',     icon: Lock,       perm: 'reports.close_day' },
    ],
  },
  {
    label: 'Reports & Audit',
    items: [
      { path: '/reports',          label: 'Reports',          icon: BarChart3,    perm: 'reports.view',  featureFlag: 'advanced_reports' },
      { path: '/audit',            label: 'Audit Center',     icon: ShieldCheck,  perm: 'reports.view',  featureFlag: 'audit_center' },
      { path: '/incident-tickets', label: 'Incident Tickets', icon: AlertTriangle, perm: 'reports.view' },
      { path: '/accounting',       label: 'Accounting',       icon: Calculator,   perm: 'accounting.view' },
      { path: '/journal-entries',  label: 'Journal Entries',  icon: BookOpen,     perm: 'accounting.view' },
    ],
  },
  {
    label: 'Management',
    items: [
      { path: '/documents',      label: 'Documents',    icon: FolderOpen, perm: null },
      { path: '/employees',       label: 'Employees',    icon: Briefcase, perm: 'settings.manage_users',       featureFlag: 'employee_management' },
      { path: '/price-schemes',   label: 'Price Schemes', icon: Tags,     perm: 'price_schemes.view' },
      { path: '/team',            label: 'Team',         icon: UserCog,   perm: 'settings.manage_users' },
      { path: '/import',          label: 'Import Center', icon: Upload,   perm: 'products.create' },
      { path: '/downloads',       label: 'App Downloads', icon: Smartphone, perm: null, adminOnly: true },
      { path: '/backups',         label: 'Backups',      icon: HardDrive, perm: 'settings.edit' },
      { path: '/settings',        label: 'Settings',     icon: Settings },
    ],
  },
];

// Flat list used for offline-banner path lookup
const NAV_ITEMS = NAV_SECTIONS.flatMap(s => s.items);

export default function Layout({ children }) {
  const { 
    user, logout, branches, switchBranch, hasPerm, delegations,
    selectedBranchId, canViewAllBranches, viewingBranchName, isConsolidatedView
  } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const isOnline = useOnlineStatus();

  // Background SMS unread badge — polls every 60s so the nav badge stays fresh
  // even when the user is on Sales, Inventory, etc.
  const [smsPending, setSmsPending] = useState(0);
  const smsPollRef = useRef(null);
  useEffect(() => {
    if (!user || user.role !== 'admin') return;
    const fetchStats = () => {
      // Interceptor auto-appends branch_id when a specific branch is selected
      api.get('/sms/stats').then(res => setSmsPending(res.data.unread || 0)).catch(() => {});
    };
    fetchStats();
    smsPollRef.current = setInterval(fetchStats, 60000);
    return () => clearInterval(smsPollRef.current);
  }, [user?.role]); // eslint-disable-line

  // Section override dialog state
  const [overrideDialog, setOverrideDialog] = useState({ open: false, module: '', label: '', path: '' });

  const getItemPermStatus = (item) => {
    // Check subscription feature flag first
    if (item.featureFlag && user && !user.is_super_admin) {
      const features = user?.subscription?.features;
      if (features && features[item.featureFlag] === false) return 'feature_locked';
    }
    // Check permission
    if (item.perm) {
      const [mod, act] = item.perm.split('.');
      if (!hasPerm(mod, act)) return 'perm_locked';
    }
    return 'allowed';
  };

  // Show ALL items, but mark locked ones
  const allSections = NAV_SECTIONS.map(section => ({
    ...section,
    items: section.items.map(item => ({
      ...item,
      _status: getItemPermStatus(item),
    })).filter(item => {
      if (item._status === 'feature_locked') return false;
      if (item.adminOnly && user?.role !== 'admin' && user?.role !== 'owner') return false;
      return true;
    }),
  })).filter(section => section.items.length > 0);

  const NavLink = ({ item }) => {
    const active = location.pathname === item.path;
    const offlineLocked = !isOnline && !item.offlineOk;
    const readOnly = !isOnline && item.offlineOk === 'readonly';
    const permLocked = item._status === 'perm_locked';

    const handleClick = (e) => {
      if (offlineLocked) {
        e.preventDefault();
        toast.error('You\'re offline — this area requires internet', { duration: 2500 });
        return;
      }
      if (permLocked) {
        e.preventDefault();
        const [mod] = (item.perm || '').split('.');
        setOverrideDialog({ open: true, module: mod, label: item.label, path: item.path });
        return;
      }
      setSidebarOpen(false);
    };

    return (
      <Link
        to={permLocked ? '#' : item.path}
        data-testid={`nav-${item.path.slice(1)}`}
        onClick={handleClick}
        className={`relative flex items-center gap-3 px-4 py-2.5 rounded-md text-sm font-medium transition-all duration-200 ${
          offlineLocked
            ? 'text-slate-600 opacity-50 cursor-not-allowed'
            : permLocked
            ? 'text-slate-500 hover:text-slate-300 hover:bg-white/5 cursor-pointer'
            : active
            ? 'bg-[#1A4D2E] text-white shadow-sm'
            : 'text-slate-400 hover:text-white hover:bg-white/5'
        }`}
      >
        <item.icon size={18} strokeWidth={1.5} />
        <span className="flex-1">{item.label}</span>
        {/* SMS unread badge — red, branch-specific, visible on all pages */}
        {item.path === '/messages' && smsPending > 0 && (
          <span className="bg-red-500 text-white text-[9px] font-bold rounded-full px-1.5 py-0.5 min-w-[18px] text-center">
            {smsPending}
          </span>
        )}
        {offlineLocked && <WifiOff size={11} className="text-slate-500 shrink-0" />}
        {permLocked && <Lock size={11} className="text-amber-500/70 shrink-0" />}
        {readOnly && !active && <span className="text-[9px] text-slate-500 shrink-0">cached</span>}
      </Link>
    );
  };

  const SidebarContent = () => (
    <div className="flex flex-col h-full">
      <div className="px-5 py-5 flex items-center gap-3">
        <div className="w-9 h-9 rounded-lg bg-[#1A4D2E] flex items-center justify-center">
          <Store size={18} className="text-white" strokeWidth={2} />
        </div>
        <div>
          <h1 className="text-base font-bold text-white tracking-tight" style={{ fontFamily: 'Manrope' }}>AgriBooks</h1>
          <p className="text-[11px] text-slate-500">Business Management</p>
        </div>
      </div>
      <Separator className="bg-white/5" />
      <div className="px-3 py-3">
        {canViewAllBranches ? (
          <Select
            value={selectedBranchId}
            onValueChange={(val) => switchBranch(val)}
          >
            <SelectTrigger
              data-testid="branch-selector"
              className="bg-white/5 border-white/10 text-white text-xs h-9"
            >
              <div className="flex items-center gap-2">
                {isConsolidatedView && <Building2 size={14} className="text-emerald-400" />}
                <SelectValue placeholder="Select Branch">{viewingBranchName}</SelectValue>
              </div>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">
                <div className="flex items-center gap-2">
                  <Building2 size={14} className="text-emerald-500" />
                  <span className="font-medium">All Branches</span>
                  <span className="text-xs text-slate-400">(Consolidated)</span>
                </div>
              </SelectItem>
              <Separator className="my-1" />
              {branches.map(b => (
                <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : (
          <div className="bg-white/5 border border-white/10 rounded-md px-3 py-2 text-xs text-white flex items-center gap-2">
            <Building2 size={14} className="text-slate-400" />
            <span>{viewingBranchName}</span>
            <span className="ml-auto text-slate-500">🔒</span>
          </div>
        )}
      </div>
      <ScrollArea className="flex-1 px-3">
        <nav className="py-2">
          {allSections.map((section, si) => (
            <div key={section.label || 'top'} className={si > 0 ? 'mt-1' : ''}>
              {section.label && (
                <div className={`flex items-center gap-2 px-2 ${si > 0 ? 'pt-4' : 'pt-2'} pb-1.5`}>
                  <span className="text-[10px] font-semibold uppercase tracking-widest text-slate-600">
                    {section.label}
                  </span>
                  <div className="flex-1 h-px bg-white/5" />
                </div>
              )}
              <div className="space-y-0.5">
                {section.items.map(item => <NavLink key={item.path} item={item} />)}
              </div>
            </div>
          ))}
        </nav>
      </ScrollArea>
      <Separator className="bg-white/5" />
      <OfflineIndicator />
      <Separator className="bg-white/5" />
      <div className="p-3">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              data-testid="user-menu-trigger"
              className="w-full flex items-center gap-3 px-3 py-2.5 rounded-md hover:bg-white/5 transition-colors"
            >
              <div className="w-8 h-8 rounded-full bg-[#1A4D2E] flex items-center justify-center text-white text-xs font-bold">
                {user?.full_name?.[0] || user?.username?.[0] || 'U'}
              </div>
              <div className="flex-1 text-left">
                <p className="text-sm text-white font-medium truncate">{user?.full_name || user?.username}</p>
                <p className="text-[11px] text-slate-500 capitalize">{user?.role}</p>
              </div>
              <ChevronDown size={14} className="text-slate-500" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-48">
            <DropdownMenuItem onClick={() => navigate('/settings')}>
              <User size={14} className="mr-2" /> Profile
            </DropdownMenuItem>
            {!user?.is_super_admin && (
              <DropdownMenuItem onClick={() => navigate('/upgrade')}>
                <BarChart3 size={14} className="mr-2" /> Upgrade Plan
              </DropdownMenuItem>
            )}
            {user?.is_super_admin && (
              <DropdownMenuItem onClick={() => navigate('/superadmin')}>
                <Shield size={14} className="mr-2" /> Platform Admin
              </DropdownMenuItem>
            )}
            <DropdownMenuSeparator />
            <DropdownMenuItem data-testid="logout-btn" onClick={logout} className="text-red-600">
              <LogOut size={14} className="mr-2" /> Logout
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  );

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Desktop Sidebar */}
      <aside className="hidden lg:flex w-[250px] flex-col bg-[#0F172A] sidebar-texture shrink-0">
        {SidebarContent()}
      </aside>
      {/* Mobile Sidebar Overlay */}
      {sidebarOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div className="absolute inset-0 bg-black/60" onClick={() => setSidebarOpen(false)} />
          <aside className="absolute left-0 top-0 bottom-0 w-[250px] bg-[#0F172A] sidebar-texture">
            <button onClick={() => setSidebarOpen(false)} className="absolute top-4 right-4 text-white">
              <X size={20} />
            </button>
            {SidebarContent()}
          </aside>
        </div>
      )}
      {/* Main Content */}
      <main className="flex-1 flex flex-col overflow-hidden bg-[#F5F5F0]">
        <header className="h-14 border-b border-slate-200 bg-white flex items-center justify-between px-4 lg:px-6 shrink-0">
          <div className="flex items-center gap-3">
            <button data-testid="mobile-menu-btn" onClick={() => setSidebarOpen(true)} className="lg:hidden p-1">
              <Menu size={20} />
            </button>
            <div className="flex items-center gap-2">
              {isConsolidatedView ? (
                <div className="flex items-center gap-2 px-2 py-1 rounded-md bg-emerald-50 border border-emerald-200">
                  <Building2 size={14} className="text-emerald-600" />
                  <span className="text-sm font-medium text-emerald-700">All Branches</span>
                </div>
              ) : (
                <h2 className="text-sm font-semibold text-slate-700" style={{ fontFamily: 'Manrope' }}>
                  {viewingBranchName}
                </h2>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <QuickSearch />
            <NotificationBell />
            <span className="hidden sm:inline">{user?.full_name || user?.username}</span>
            <span className="capitalize bg-slate-100 px-2 py-0.5 rounded text-[11px]">{user?.role}</span>
          </div>
        </header>
        {/* Offline banner — only on pages that require internet */}
        {!isOnline && (() => {
          const currentItem = NAV_ITEMS.find(i => i.path === location.pathname);
          const isLocked = !currentItem?.offlineOk;
          if (!isLocked) return null;
          return (
            <div className="shrink-0 bg-amber-50 border-b border-amber-200 px-4 py-2 flex items-center gap-2">
              <WifiOff size={14} className="text-amber-600 shrink-0" />
              <span className="text-xs font-medium text-amber-800">
                You&apos;re offline — this page requires internet. Data shown may be outdated.
                Switch to <strong>Sales</strong> for offline operations.
              </span>
            </div>
          );
        })()}
        {/* Trial expiry banner */}
        {user?.subscription && (() => {
          const sub = user.subscription;
          const effective = sub.effective_plan;
          const grace = sub.grace_info;

          // Grace period banner (highest priority)
          if (effective === 'grace_period' && grace?.in_grace) {
            const urgency = grace.days_left <= 1;
            return (
              <div className={`shrink-0 border-b px-4 py-2 flex items-center justify-between gap-2 ${urgency ? 'bg-red-50 border-red-200' : 'bg-amber-50 border-amber-200'}`}>
                <span className={`text-xs font-medium ${urgency ? 'text-red-800' : 'text-amber-800'}`}>
                  {grace.days_left === 0
                    ? 'Your account locks TODAY. '
                    : `Access locks in ${grace.days_left} day${grace.days_left !== 1 ? 's' : ''} (${grace.locked_at}). `}
                  <strong>Renew your subscription to keep access.</strong>
                </span>
                <button onClick={() => navigate('/upgrade')}
                  className={`text-xs px-3 py-1 rounded-full shrink-0 text-white ${urgency ? 'bg-red-600 hover:bg-red-700' : 'bg-amber-600 hover:bg-amber-700'}`}>
                  Renew Now
                </button>
              </div>
            );
          }

          // Expired — lock features
          if (effective === 'expired') {
            return (
              <div className="shrink-0 bg-red-50 border-b border-red-300 px-4 py-2 flex items-center justify-between gap-2">
                <span className="text-xs font-medium text-red-800">
                  Your subscription has expired. Access to most features is locked.
                </span>
                <button onClick={() => navigate('/upgrade')}
                  className="text-xs bg-red-600 text-white px-3 py-1 rounded-full hover:bg-red-700 shrink-0">
                  Reactivate
                </button>
              </div>
            );
          }

          // Trial expiry warning (≤ 5 days)
          if (sub.plan === 'trial') {
            const daysLeft = sub.trial_ends_at
              ? Math.ceil((new Date(sub.trial_ends_at) - new Date()) / 86400000)
              : null;
            if (!daysLeft || daysLeft > 5) return null;
            return (
              <div className="shrink-0 bg-amber-50 border-b border-amber-200 px-4 py-2 flex items-center justify-between gap-2">
                <span className="text-xs font-medium text-amber-800">
                  Trial ends in <strong>{daysLeft} day{daysLeft !== 1 ? 's' : ''}</strong>. Upgrade to keep access.
                </span>
                <button onClick={() => navigate('/upgrade')}
                  className="text-xs bg-amber-600 text-white px-3 py-1 rounded-full hover:bg-amber-700 shrink-0">
                  Upgrade Now
                </button>
              </div>
            );
          }

          return null;
        })()}
        <div className="flex-1 overflow-auto p-4 lg:p-6">
          {children}
        </div>
      </main>

      {/* Section Override Dialog */}
      <SectionOverrideDialog
        open={overrideDialog.open}
        onOpenChange={(open) => setOverrideDialog(prev => ({ ...prev, open }))}
        module={overrideDialog.module}
        moduleLabel={overrideDialog.label}
        onGranted={() => {
          navigate(overrideDialog.path);
          setSidebarOpen(false);
        }}
      />
    </div>
  );
}
