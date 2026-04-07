import { useState, useEffect, useCallback } from 'react';
import { useAuth, api } from '../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Badge } from '../components/ui/badge';
import { Switch } from '../components/ui/switch';
import { Separator } from '../components/ui/separator';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '../components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { ScrollArea } from '../components/ui/scroll-area';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs';
import {
  Users, Plus, Search, Edit2, KeyRound, Trash2, Ban, CheckCircle2,
  Building2, Lock, Unlock, User, Shield, Settings, Save, RefreshCw,
  X, Check, Eye, ShoppingCart, Package, Warehouse, DollarSign, FileText,
  Truck, UserCog, BarChart3, AlertTriangle
} from 'lucide-react';
import { toast } from 'sonner';

const ROLES = [
  { key: 'admin',            label: 'Administrator',     color: 'bg-purple-100 text-purple-700', avatar: 'bg-purple-600' },
  { key: 'manager',          label: 'Manager',            color: 'bg-blue-100 text-blue-700',     avatar: 'bg-blue-600' },
  { key: 'cashier',          label: 'Cashier',            color: 'bg-green-100 text-green-700',   avatar: 'bg-green-600' },
  { key: 'inventory',        label: 'Inventory Clerk',    color: 'bg-orange-100 text-orange-700', avatar: 'bg-orange-500' },
  { key: 'inventory_clerk',  label: 'Inventory Clerk',    color: 'bg-orange-100 text-orange-700', avatar: 'bg-orange-500' },
  { key: 'staff',            label: 'Staff',              color: 'bg-slate-100 text-slate-700',   avatar: 'bg-slate-500' },
];

const BLANK_FORM = {
  full_name: '', email: '', role: 'cashier',
  branch_id: '', password: '', confirm_password: '', manager_pin: ''
};

const MODULE_ICONS = {
  dashboard: BarChart3, branches: Building2, products: Package,
  inventory: Warehouse, sales: ShoppingCart, purchase_orders: Truck,
  suppliers: Truck, customers: Users, accounting: DollarSign,
  price_schemes: FileText, reports: BarChart3, settings: Settings,
  count_sheets: FileText,
};

export default function TeamPage() {
  const { user: currentUser, branches } = useAuth();
  const [users, setUsers] = useState([]);
  const [search, setSearch] = useState('');
  const [showInactive, setShowInactive] = useState(false);
  const [userDialog, setUserDialog] = useState(false);
  const [pinDialog, setPinDialog] = useState(false);
  const [deleteDialog, setDeleteDialog] = useState(false);
  const [editingUser, setEditingUser] = useState(null);
  const [pinTarget, setPinTarget] = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [form, setForm] = useState(BLANK_FORM);
  const [pinForm, setPinForm] = useState({ pin: '', confirm: '' });
  const [saving, setSaving] = useState(false);
  const [expandedUser, setExpandedUser] = useState(null);

  // Permissions state
  const [modules, setModules] = useState({});
  const [presets, setPresets] = useState({});
  const [selectedPermUser, setSelectedPermUser] = useState(null);
  const [userPermissions, setUserPermissions] = useState({});
  const [originalPermissions, setOriginalPermissions] = useState({});
  const [hasPermChanges, setHasPermChanges] = useState(false);
  const [savingPerms, setSavingPerms] = useState(false);

  const fetchUsers = useCallback(async () => {
    try {
      const res = await api.get('/users', { params: { include_inactive: showInactive } });
      setUsers(res.data);
    } catch { toast.error('Failed to load users'); }
  }, [showInactive]);

  const loadPermData = useCallback(async () => {
    try {
      const [modulesRes, presetsRes] = await Promise.all([
        api.get('/permissions/modules'),
        api.get('/permissions/presets'),
      ]);
      setModules(modulesRes.data);
      setPresets(presetsRes.data);
    } catch {}
  }, []);

  useEffect(() => { fetchUsers(); }, [fetchUsers]);
  useEffect(() => { loadPermData(); }, [loadPermData]);

  const filteredUsers = users.filter(u =>
    !search || u.full_name?.toLowerCase().includes(search.toLowerCase()) ||
    u.username?.toLowerCase().includes(search.toLowerCase()) ||
    u.role?.toLowerCase().includes(search.toLowerCase())
  );

  const getRoleMeta = (role) => ROLES.find(r => r.key === role) || { label: role, color: 'bg-slate-100 text-slate-600', avatar: 'bg-slate-500' };
  const getBranchName = (id) => branches.find(b => b.id === id)?.name || (id ? 'Unknown' : 'All Branches');

  // ── User CRUD ──────────────────────────────────────────────────────────────
  const openCreate = () => { setEditingUser(null); setForm(BLANK_FORM); setUserDialog(true); };
  const openEdit = (u) => {
    setEditingUser(u);
    setForm({
      full_name: u.full_name || '', email: u.email || '',
      role: u.role || 'cashier', branch_id: u.branch_id || '',
      password: '', confirm_password: '', manager_pin: ''
    });
    setUserDialog(true);
  };

  const handleSave = async () => {
    if (!form.full_name.trim()) { toast.error('Full name is required'); return; }
    if (!form.email.trim()) { toast.error('Email address is required'); return; }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email.trim())) { toast.error('Please enter a valid email address'); return; }
    if (!editingUser && !form.password) { toast.error('Password is required'); return; }
    if (form.password && form.password.length < 6) { toast.error('Password must be at least 6 characters'); return; }
    if (form.password && form.password !== form.confirm_password) { toast.error('Passwords do not match'); return; }
    setSaving(true);
    try {
      const payload = { full_name: form.full_name, email: form.email, role: form.role, branch_id: form.branch_id || null };
      if (form.password) payload.password = form.password;
      let savedUser;
      if (editingUser) {
        const res = await api.put(`/users/${editingUser.id}`, payload);
        savedUser = res.data;
        toast.success(`${form.full_name} updated`);
      } else {
        const res = await api.post('/users', payload);
        savedUser = res.data;
        toast.success(`${form.full_name} added to team`);
      }
      if (form.manager_pin && form.manager_pin.length >= 4 && savedUser?.id) {
        await api.put(`/users/${savedUser.id}/pin`, { pin: form.manager_pin });
      }
      setUserDialog(false);
      fetchUsers();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed to save'); }
    setSaving(false);
  };

  // ── PIN ────────────────────────────────────────────────────────────────────
  const openPinDialog = (u) => { setPinTarget(u); setPinForm({ pin: '', confirm: '' }); setPinDialog(true); };
  const handleSetPin = async () => {
    if (pinForm.pin && pinForm.pin.length < 4) { toast.error('PIN must be at least 4 digits'); return; }
    if (pinForm.pin && pinForm.pin !== pinForm.confirm) { toast.error('PINs do not match'); return; }
    try {
      await api.put(`/users/${pinTarget.id}/pin`, { pin: pinForm.pin });
      toast.success(pinForm.pin ? `PIN set for ${pinTarget.full_name || pinTarget.username}` : 'PIN cleared');
      setPinDialog(false); fetchUsers();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed'); }
  };

  // ── Disable / Delete ───────────────────────────────────────────────────────
  const toggleActive = async (u) => {
    if (u.id === currentUser?.id) { toast.error("Can't modify your own account"); return; }
    try {
      if (u.active === false) {
        await api.put(`/users/${u.id}/reactivate`);
        toast.success(`${u.full_name || u.username} reactivated`);
      } else {
        await api.delete(`/users/${u.id}`);
        toast.success(`${u.full_name || u.username} disabled`);
      }
      fetchUsers();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed'); }
  };

  const openDeleteDialog = (u) => { setDeleteTarget(u); setDeleteDialog(true); };
  const handlePermanentDelete = async () => {
    try {
      await api.delete(`/users/${deleteTarget.id}/permanent`);
      toast.success(`${deleteTarget.full_name || deleteTarget.username} permanently deleted`);
      setDeleteDialog(false); fetchUsers();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed'); }
  };

  const handleResetPassword = async (u) => {
    const newPw = window.prompt(`Enter new password for ${u.full_name || u.username}:`);
    if (!newPw) return;
    try {
      await api.put(`/users/${u.id}/reset-password`, { new_password: newPw });
      toast.success('Password reset');
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed'); }
  };

  // ── Permissions ────────────────────────────────────────────────────────────
  const selectPermUser = async (u) => {
    setSelectedPermUser(u);
    setHasPermChanges(false);
    try {
      const res = await api.get(`/users/${u.id}/permissions`);
      const perms = res.data.permissions || {};
      setUserPermissions(perms);
      setOriginalPermissions(JSON.parse(JSON.stringify(perms)));
    } catch { toast.error('Failed to load permissions'); }
  };

  const handlePermToggle = (module, action) => {
    setUserPermissions(prev => {
      const n = { ...prev };
      const mp = { ...(prev[module] || {}) };
      mp[action] = !mp[action];
      n[module] = mp;
      return n;
    });
    setHasPermChanges(true);
  };

  const handleModuleToggleAll = (module, enabled) => {
    const actions = modules[module]?.actions || {};
    setUserPermissions(prev => {
      const n = { ...prev };
      n[module] = {};
      Object.keys(actions).forEach(a => { n[module][a] = enabled; });
      return n;
    });
    setHasPermChanges(true);
  };

  const applyPreset = async (presetKey) => {
    if (!selectedPermUser) return;
    try {
      const res = await api.post(`/users/${selectedPermUser.id}/apply-preset`, { preset: presetKey });
      setUserPermissions(res.data.permissions);
      setOriginalPermissions(JSON.parse(JSON.stringify(res.data.permissions)));
      setSelectedPermUser(res.data);
      toast.success(`Applied ${presets[presetKey]?.label} preset`);
      setHasPermChanges(false); fetchUsers();
    } catch { toast.error('Failed to apply preset'); }
  };

  const savePermissions = async () => {
    if (!selectedPermUser) return;
    setSavingPerms(true);
    try {
      await api.put(`/users/${selectedPermUser.id}/permissions`, { permissions: userPermissions });
      setOriginalPermissions(JSON.parse(JSON.stringify(userPermissions)));
      setHasPermChanges(false);
      toast.success('Permissions saved'); fetchUsers();
    } catch { toast.error('Failed to save'); }
    setSavingPerms(false);
  };

  const getModuleStatus = (module) => {
    const mp = userPermissions[module] || {};
    const actions = modules[module]?.actions || {};
    const total = Object.keys(actions).length;
    const enabled = Object.values(mp).filter(Boolean).length;
    if (enabled === 0) return { label: 'No Access', color: 'bg-slate-100 text-slate-500' };
    if (enabled === total) return { label: 'Full', color: 'bg-emerald-100 text-emerald-700' };
    return { label: 'Partial', color: 'bg-amber-100 text-amber-700' };
  };

  // ── Stats ──────────────────────────────────────────────────────────────────
  const activeUsers = users.filter(u => u.active !== false);

  return (
    <div className="space-y-6 animate-fadeIn" data-testid="team-page">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight" style={{ fontFamily: 'Manrope' }}>Team</h1>
          <p className="text-sm text-slate-500 mt-0.5">Manage users, roles, PINs, and permissions</p>
        </div>
        <Button onClick={openCreate} className="bg-[#1A4D2E] hover:bg-[#14532d] text-white gap-2" data-testid="create-user-btn">
          <Plus size={16} /> New User
        </Button>
      </div>

      {/* Stats row — only primary roles */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          { key: 'admin',     label: 'Admins',      avatar: 'bg-purple-600', match: r => r === 'admin' },
          { key: 'manager',   label: 'Managers',    avatar: 'bg-blue-600',   match: r => r === 'manager' },
          { key: 'cashier',   label: 'Cashiers',    avatar: 'bg-green-600',  match: r => r === 'cashier' },
          { key: 'inventory', label: 'Inv. Clerks', avatar: 'bg-orange-500', match: r => r === 'inventory' || r === 'inventory_clerk' || r === 'staff' },
        ].map(r => {
          const count = activeUsers.filter(u => r.match(u.role)).length;
          return (
            <Card key={r.key} className="border-slate-200">
              <CardContent className="p-3 flex items-center gap-3">
                <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${r.avatar}`}>
                  <User size={15} className="text-white" />
                </div>
                <div>
                  <p className="text-xl font-bold">{count}</p>
                  <p className="text-xs text-slate-500">{r.label}</p>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      <Tabs defaultValue="members">
        <TabsList className="mb-4">
          <TabsTrigger value="members" data-testid="members-tab" className="flex items-center gap-1.5">
            <Users size={14} /> Members
          </TabsTrigger>
          <TabsTrigger value="permissions" data-testid="permissions-tab" className="flex items-center gap-1.5">
            <Shield size={14} /> Permissions
          </TabsTrigger>
        </TabsList>

        {/* ── Members Tab ─────────────────────────────────────────────── */}
        <TabsContent value="members">
          <div className="flex items-center gap-3 mb-4">
            <div className="relative flex-1 max-w-sm">
              <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <Input className="pl-9 h-9" placeholder="Search users..." value={search} onChange={e => setSearch(e.target.value)} data-testid="user-search" />
            </div>
            <label className="flex items-center gap-2 text-sm text-slate-500 cursor-pointer select-none">
              <Switch checked={showInactive} onCheckedChange={setShowInactive} className="data-[state=checked]:bg-slate-600" />
              Show disabled
            </label>
          </div>

          <Card className="border-slate-200">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-slate-50 border-b border-slate-200">
                  <tr>
                    <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">User</th>
                    <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">Role</th>
                    <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">Branch</th>
                    <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">PIN</th>
                    <th className="text-left px-4 py-3 text-xs font-medium text-slate-500 uppercase">Status</th>
                    <th className="px-4 py-3 text-xs font-medium text-slate-500 uppercase text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredUsers.map(u => {
                    const role = getRoleMeta(u.role);
                    const isMe = u.id === currentUser?.id;
                    const isActive = u.active !== false;
                    return (
                      <tr key={u.id} className={`border-b border-slate-100 transition-colors cursor-pointer ${isActive ? 'hover:bg-slate-50/50' : 'bg-slate-50/30 opacity-60'} ${expandedUser === u.id ? 'bg-slate-50' : ''}`}
                        data-testid={`user-row-${u.id}`}
                        onClick={() => setExpandedUser(expandedUser === u.id ? null : u.id)}>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-3">
                            <div className={`w-9 h-9 rounded-full flex items-center justify-center text-white text-sm font-bold ${role.avatar}`}>
                              {(u.full_name?.[0] || u.email?.[0] || 'U').toUpperCase()}
                            </div>
                            <div>
                              <p className="font-medium text-slate-800">
                                {u.full_name || u.email}
                                {isMe && <span className="text-[10px] text-slate-400 ml-1">(you)</span>}
                              </p>
                              <p className="text-xs text-slate-400">{u.email || u.username}</p>
                              {expandedUser === u.id && (
                                <div className="mt-2 pt-2 border-t border-slate-100 space-y-1 text-xs text-slate-500" onClick={e => e.stopPropagation()}>
                                  <p>Created: {u.created_at ? new Date(u.created_at).toLocaleDateString() : 'N/A'}</p>
                                  {u.pin_set_by_name && <p>PIN set by: {u.pin_set_by_name}</p>}
                                  {u.permission_preset && <p>Permission preset: <Badge className="text-[9px] bg-slate-100 text-slate-600">{u.permission_preset}</Badge></p>}
                                  {u.is_auditor && <p className="text-amber-600">Has auditor access</p>}
                                  <div className="flex gap-1 pt-1">
                                    <Button size="sm" variant="outline" className="h-6 text-[10px] px-2" onClick={() => openEdit(u)}>Edit</Button>
                                    <Button size="sm" variant="outline" className="h-6 text-[10px] px-2" onClick={() => openPinDialog(u)}>Set PIN</Button>
                                    <Button size="sm" variant="outline" className="h-6 text-[10px] px-2" onClick={() => handleResetPassword(u)}>Reset PW</Button>
                                  </div>
                                </div>
                              )}
                            </div>
                          </div>
                        </td>
                        <td className="px-4 py-3">
                          <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium ${role.color}`}>{role.label}</span>
                        </td>
                        <td className="px-4 py-3">
                          <span className="flex items-center gap-1 text-slate-600 text-xs">
                            <Building2 size={12} /> {getBranchName(u.branch_id)}
                          </span>
                        </td>
                        <td className="px-4 py-3">
                          {(u.manager_pin || u.staff_pin)
                            ? <span className="flex items-center gap-1 text-xs text-emerald-600"><Lock size={11} /> {['cashier','inventory','staff','inventory_clerk'].includes(u.role) ? 'Staff PIN' : 'Set'}</span>
                            : <span className="flex items-center gap-1 text-xs text-slate-400"><Unlock size={11} /> Not set</span>
                          }
                        </td>
                        <td className="px-4 py-3">
                          <Badge className={`text-[10px] ${isActive ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-600'}`}>
                            {isActive ? 'Active' : 'Disabled'}
                          </Badge>
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-1 justify-end" onClick={e => e.stopPropagation()}>
                            <Button variant="ghost" size="sm" className="h-8 w-8 p-0 text-slate-400 hover:text-slate-700" onClick={() => openEdit(u)} title="Edit" data-testid={`edit-user-${u.id}`}>
                              <Edit2 size={14} />
                            </Button>
                            <Button variant="ghost" size="sm" className="h-8 w-8 p-0 text-slate-400 hover:text-amber-600" onClick={() => openPinDialog(u)} title="Set PIN" data-testid={`pin-user-${u.id}`}>
                              <KeyRound size={14} />
                            </Button>
                            <Button variant="ghost" size="sm" className="h-8 w-8 p-0 text-slate-400 hover:text-blue-600" onClick={() => handleResetPassword(u)} title="Reset Password" data-testid={`reset-pw-${u.id}`}>
                              <Lock size={14} />
                            </Button>
                            {!isMe && (
                              <>
                                <Button variant="ghost" size="sm" className={`h-8 w-8 p-0 ${isActive ? 'text-slate-400 hover:text-orange-500' : 'text-slate-400 hover:text-green-600'}`}
                                  onClick={() => toggleActive(u)} title={isActive ? 'Disable' : 'Reactivate'} data-testid={`toggle-active-${u.id}`}>
                                  {isActive ? <Ban size={14} /> : <CheckCircle2 size={14} />}
                                </Button>
                                <Button variant="ghost" size="sm" className="h-8 w-8 p-0 text-slate-400 hover:text-red-600" onClick={() => openDeleteDialog(u)} title="Delete permanently" data-testid={`delete-user-${u.id}`}>
                                  <Trash2 size={14} />
                                </Button>
                              </>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                  {filteredUsers.length === 0 && (
                    <tr><td colSpan={6} className="px-4 py-12 text-center text-slate-400">No users found</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </Card>
        </TabsContent>

        {/* ── Permissions Tab ─────────────────────────────────────────── */}
        <TabsContent value="permissions">
          <div className="grid lg:grid-cols-3 gap-5">
            <Card className="border-slate-200">
              <CardHeader className="pb-3">
                <CardTitle className="text-sm font-semibold flex items-center gap-2" style={{ fontFamily: 'Manrope' }}>
                  <Users size={16} /> Select User
                </CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <ScrollArea className="h-[500px]">
                  {users.filter(u => u.active !== false).map(u => {
                    const role = getRoleMeta(u.role);
                    return (
                      <button key={u.id} data-testid={`perm-user-${u.id}`}
                        onClick={() => selectPermUser(u)}
                        className={`w-full text-left px-4 py-3 border-b border-slate-50 hover:bg-slate-50 transition-colors ${selectedPermUser?.id === u.id ? 'bg-[#1A4D2E]/5 border-l-2 border-l-[#1A4D2E]' : ''}`}>
                        <div className="flex items-center justify-between">
                          <div>
                            <p className="font-medium text-sm">{u.full_name || u.email}</p>
                            <p className="text-xs text-slate-400">{u.email || u.username}</p>
                          </div>
                          <Badge className={`text-[10px] ${role.color}`}>{u.permission_preset || u.role}</Badge>
                        </div>
                      </button>
                    );
                  })}
                </ScrollArea>
              </CardContent>
            </Card>

            <div className="lg:col-span-2">
              {selectedPermUser ? (
                <Card className="border-slate-200">
                  <CardHeader className="pb-3">
                    <div className="flex items-center justify-between">
                      <div>
                        <CardTitle className="text-lg font-bold" style={{ fontFamily: 'Manrope' }}>{selectedPermUser.full_name || selectedPermUser.email}</CardTitle>
                        <p className="text-sm text-slate-500 mt-0.5">{selectedPermUser.email || selectedPermUser.username}</p>
                      </div>
                      <Select onValueChange={applyPreset}>
                        <SelectTrigger className="w-40 h-9"><SelectValue placeholder="Apply Preset" /></SelectTrigger>
                        <SelectContent>
                          {Object.entries(presets).map(([key, p]) => <SelectItem key={key} value={key}>{p.label}</SelectItem>)}
                        </SelectContent>
                      </Select>
                    </div>
                    {hasPermChanges && (
                      <div className="flex items-center justify-between mt-3 p-2 bg-amber-50 border border-amber-200 rounded-lg">
                        <p className="text-sm text-amber-700">Unsaved changes</p>
                        <div className="flex gap-2">
                          <Button size="sm" variant="ghost" data-testid="discard-perm-btn" onClick={() => { setUserPermissions(JSON.parse(JSON.stringify(originalPermissions))); setHasPermChanges(false); }}>Discard</Button>
                          <Button size="sm" data-testid="save-perm-btn" onClick={savePermissions} disabled={savingPerms} className="bg-[#1A4D2E] hover:bg-[#14532d] text-white">
                            <Save size={14} className="mr-1" /> {savingPerms ? 'Saving...' : 'Save'}
                          </Button>
                        </div>
                      </div>
                    )}
                  </CardHeader>
                  <CardContent className="p-0">
                    <ScrollArea className="h-[450px]">
                      <div className="divide-y divide-slate-100">
                        {Object.entries(modules).map(([mk, md]) => {
                          const Icon = MODULE_ICONS[mk] || Settings;
                          const status = getModuleStatus(mk);
                          const mp = userPermissions[mk] || {};
                          return (
                            <div key={mk} className="p-4">
                              <div className="flex items-center justify-between mb-3">
                                <div className="flex items-center gap-2">
                                  <Icon size={18} className="text-slate-500" />
                                  <span className="font-semibold text-sm">{md.label}</span>
                                  <Badge className={`text-[9px] ${status.color}`}>{status.label}</Badge>
                                </div>
                                <div className="flex items-center gap-2">
                                  <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => handleModuleToggleAll(mk, false)}><X size={12} className="mr-1" /> None</Button>
                                  <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => handleModuleToggleAll(mk, true)}><Check size={12} className="mr-1" /> All</Button>
                                </div>
                              </div>
                              <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
                                {Object.entries(md.actions).map(([ak, al]) => (
                                  <div key={ak} onClick={() => handlePermToggle(mk, ak)}
                                    className={`flex items-center gap-2 p-2 rounded-lg border cursor-pointer transition-colors ${mp[ak] ? 'bg-emerald-50 border-emerald-200' : 'bg-slate-50 border-slate-200 hover:bg-slate-100'}`}>
                                    <Switch checked={mp[ak] || false} onCheckedChange={() => handlePermToggle(mk, ak)} className="data-[state=checked]:bg-emerald-500" onClick={e => e.stopPropagation()} />
                                    <span className="text-xs select-none">{al}</span>
                                  </div>
                                ))}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </ScrollArea>
                  </CardContent>
                </Card>
              ) : (
                <Card className="border-slate-200">
                  <CardContent className="p-12 text-center">
                    <UserCog size={48} className="mx-auto text-slate-200 mb-4" />
                    <p className="text-slate-400">Select a user to manage permissions</p>
                  </CardContent>
                </Card>
              )}
            </div>
          </div>

          {/* Preset Legend */}
          <Card className="border-slate-200 mt-5">
            <CardHeader className="pb-2"><CardTitle className="text-sm font-semibold" style={{ fontFamily: 'Manrope' }}>Role Presets</CardTitle></CardHeader>
            <CardContent>
              <div className="grid md:grid-cols-4 gap-4">
                {Object.entries(presets).map(([key, p]) => (
                  <div key={key} className="p-3 rounded-lg bg-slate-50 border border-slate-100">
                    <Badge className={`text-[10px] ${getRoleMeta(key).color}`}>{p.label}</Badge>
                    <p className="text-xs text-slate-500 mt-1">{p.description}</p>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {/* ── Create/Edit User Dialog ─────────────────────────────────────── */}
      <Dialog open={userDialog} onOpenChange={setUserDialog}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle style={{ fontFamily: 'Manrope' }}>{editingUser ? 'Edit User' : 'Create New User'}</DialogTitle>
            <DialogDescription>{editingUser ? `Editing ${editingUser.full_name || editingUser.email}` : 'Add a new team member'}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label className="text-xs">Full Name <span className="text-red-500">*</span></Label>
                <Input value={form.full_name} onChange={e => setForm({ ...form, full_name: e.target.value })} placeholder="Juan dela Cruz" className="h-9" data-testid="user-fullname" />
              </div>
              <div>
                <Label className="text-xs">Role <span className="text-red-500">*</span></Label>
                <Select value={form.role} onValueChange={v => setForm({ ...form, role: v })}>
                  <SelectTrigger className="h-9" data-testid="user-role-select"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="admin">Administrator</SelectItem>
                    <SelectItem value="manager">Manager</SelectItem>
                    <SelectItem value="cashier">Cashier</SelectItem>
                    <SelectItem value="inventory">Inventory Clerk</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div>
              <Label className="text-xs">Email Address <span className="text-red-500">*</span></Label>
              <Input type="email" value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} placeholder="user@example.com" className="h-9" data-testid="user-email" disabled={!!editingUser} />
              {!editingUser && <p className="text-[10px] text-slate-400 mt-1">Used as login. Cannot be changed after creation.</p>}
            </div>
            <div>
              <Label className="text-xs">Branch</Label>
              <Select value={form.branch_id || 'all'} onValueChange={v => setForm({ ...form, branch_id: v === 'all' ? '' : v })}>
                <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Branches</SelectItem>
                  {branches.map(b => <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <Separator />
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label className="text-xs">{editingUser ? 'New Password (optional)' : 'Password'} {!editingUser && <span className="text-red-500">*</span>}</Label>
                <Input type="password" autoComplete="new-password" value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} placeholder="Min. 6 characters" className="h-9" data-testid="user-password" />
              </div>
              <div>
                <Label className="text-xs">Confirm Password</Label>
                <Input type="password" autoComplete="new-password" value={form.confirm_password} onChange={e => setForm({ ...form, confirm_password: e.target.value })} placeholder="Repeat password" className="h-9" />
              </div>
            </div>
            {(form.role === 'admin' || form.role === 'manager') && (
              <div>
                <Label className="text-xs flex items-center gap-1"><KeyRound size={11} /> Manager PIN (4-8 digits, optional)</Label>
                <Input type="password" autoComplete="new-password" value={form.manager_pin} onChange={e => setForm({ ...form, manager_pin: e.target.value.replace(/\D/g, '').slice(0, 8) })} placeholder="Used for approvals" className="h-9 tracking-widest text-center" data-testid="user-pin" />
              </div>
            )}
            {(form.role === 'cashier' || form.role === 'inventory') && (
              <div>
                <Label className="text-xs flex items-center gap-1"><KeyRound size={11} /> Staff PIN (4-8 digits, optional)</Label>
                <Input type="password" autoComplete="new-password" value={form.manager_pin} onChange={e => setForm({ ...form, manager_pin: e.target.value.replace(/\D/g, '').slice(0, 8) })} placeholder="Used for stock releases" className="h-9 tracking-widest text-center" data-testid="user-staff-pin" />
              </div>
            )}
            <div className="flex gap-2 pt-2">
              <Button variant="outline" className="flex-1" onClick={() => setUserDialog(false)}>Cancel</Button>
              <Button className="flex-1 bg-[#1A4D2E] hover:bg-[#14532d] text-white" onClick={handleSave} disabled={saving} data-testid="save-user-btn">
                {saving ? 'Saving...' : (editingUser ? 'Save Changes' : 'Create User')}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* ── PIN Dialog ──────────────────────────────────────────────────── */}
      <Dialog open={pinDialog} onOpenChange={setPinDialog}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle style={{ fontFamily: 'Manrope' }} className="flex items-center gap-2">
              <KeyRound size={18} className="text-amber-500" />
              {['cashier','inventory','staff','inventory_clerk'].includes(pinTarget?.role) ? 'Staff PIN' : 'Manager PIN'}
            </DialogTitle>
            <DialogDescription>
              {['cashier','inventory','staff','inventory_clerk'].includes(pinTarget?.role)
                ? <>Set Staff PIN for <strong>{pinTarget?.full_name || pinTarget?.username}</strong> — used for stock releases</>
                : <>Set or clear PIN for <strong>{pinTarget?.full_name || pinTarget?.username}</strong></>
              }
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <Label className="text-xs">New PIN (4-8 digits)</Label>
              <Input type="password" autoComplete="new-password" value={pinForm.pin} onChange={e => setPinForm({ ...pinForm, pin: e.target.value.replace(/\D/g, '').slice(0, 8) })} placeholder="Leave blank to clear" className="h-10 text-center text-2xl tracking-widest" data-testid="pin-input" />
            </div>
            {pinForm.pin && (
              <div>
                <Label className="text-xs">Confirm PIN</Label>
                <Input type="password" autoComplete="new-password" value={pinForm.confirm} onChange={e => setPinForm({ ...pinForm, confirm: e.target.value.replace(/\D/g, '').slice(0, 8) })} placeholder="Repeat PIN" className="h-10 text-center text-2xl tracking-widest" />
              </div>
            )}
            <div className="flex gap-2">
              <Button variant="outline" className="flex-1" onClick={() => setPinDialog(false)}>Cancel</Button>
              {(pinTarget?.manager_pin || pinTarget?.staff_pin) && !pinForm.pin && (
                <Button variant="outline" className="text-red-600 border-red-200 hover:bg-red-50" onClick={handleSetPin}>Clear PIN</Button>
              )}
              <Button className="flex-1 bg-amber-500 hover:bg-amber-600 text-white" onClick={handleSetPin} data-testid="set-pin-btn">
                {pinForm.pin ? 'Set PIN' : 'Save'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* ── Delete Confirmation Dialog ──────────────────────────────────── */}
      <Dialog open={deleteDialog} onOpenChange={setDeleteDialog}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle style={{ fontFamily: 'Manrope' }} className="flex items-center gap-2 text-red-600"><AlertTriangle size={18} /> Permanently Delete User</DialogTitle>
            <DialogDescription>
              This will permanently remove <strong>{deleteTarget?.full_name || deleteTarget?.username}</strong> and all their data. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <div className="flex gap-2 mt-2">
            <Button variant="outline" className="flex-1" onClick={() => setDeleteDialog(false)}>Cancel</Button>
            <Button className="flex-1 bg-red-600 hover:bg-red-700 text-white" onClick={handlePermanentDelete} data-testid="confirm-delete-btn">
              <Trash2 size={14} className="mr-1" /> Delete Forever
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
