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
  Truck, UserCog, BarChart3, AlertTriangle, Layers, Copy
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
  full_name: '', email: '', phone: '', role: 'cashier',
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
  const isAdmin = currentUser?.role === 'admin';
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

  // Custom roles state
  const [systemRoles, setSystemRoles] = useState([]);
  const [customRoles, setCustomRoles] = useState([]);
  const [roleDialog, setRoleDialog] = useState(false);
  const [editingRole, setEditingRole] = useState(null);
  const [deleteRoleDialog, setDeleteRoleDialog] = useState(false);
  const [deleteRoleTarget, setDeleteRoleTarget] = useState(null);
  const [roleForm, setRoleForm] = useState({ label: '', description: '', pin_tier: 'staff', base_preset: 'cashier', permissions: {} });
  const [roleFormPerms, setRoleFormPerms] = useState({});
  const [savingRole, setSavingRole] = useState(false);

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

  const fetchRoles = useCallback(async () => {
    try {
      const res = await api.get('/roles');
      setSystemRoles(res.data.system_roles || []);
      setCustomRoles(res.data.custom_roles || []);
    } catch {}
  }, []);

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
  useEffect(() => { fetchRoles(); }, [fetchRoles]);
  useEffect(() => { loadPermData(); }, [loadPermData]);

  const filteredUsers = users.filter(u =>
    !search || u.full_name?.toLowerCase().includes(search.toLowerCase()) ||
    u.username?.toLowerCase().includes(search.toLowerCase()) ||
    u.role?.toLowerCase().includes(search.toLowerCase()) ||
    getRoleLabel(u.role)?.toLowerCase().includes(search.toLowerCase())
  );

  // Resolve role display info — system roles first, then custom roles
  function getRoleLabel(roleKey) {
    const sys = ROLES.find(r => r.key === roleKey);
    if (sys) return sys.label;
    const custom = customRoles.find(r => r.id === roleKey);
    return custom ? custom.label : roleKey;
  }

  const getRoleMeta = (roleKey) => {
    const sys = ROLES.find(r => r.key === roleKey);
    if (sys) return sys;
    const custom = customRoles.find(r => r.id === roleKey);
    if (custom) return { key: custom.id, label: custom.label, color: 'bg-cyan-100 text-cyan-700', avatar: 'bg-cyan-600' };
    return { label: roleKey, color: 'bg-slate-100 text-slate-600', avatar: 'bg-slate-500' };
  };

  const getBranchName = (id) => branches.find(b => b.id === id)?.name || (id ? 'Unknown' : 'All Branches');

  // ── Custom Role CRUD ────────────────────────────────────────────────────────
  const openCreateRole = () => {
    setEditingRole(null);
    setRoleForm({ label: '', description: '', pin_tier: 'staff', base_preset: 'cashier', permissions: {} });
    setRoleFormPerms({});
    setRoleDialog(true);
  };

  const openEditRole = async (role) => {
    setEditingRole(role);
    setRoleForm({ label: role.label, description: role.description || '', pin_tier: role.pin_tier, base_preset: role.base_preset || 'cashier', permissions: role.permissions });
    setRoleFormPerms(JSON.parse(JSON.stringify(role.permissions || {})));
    setRoleDialog(true);
  };

  // When base_preset changes in role form, load those permissions as starting point
  const handleBasePresetChange = (preset) => {
    // Permissions will be loaded from backend when we save; just track choice
    setRoleForm(f => ({ ...f, base_preset: preset }));
  };

  const handleRolePermToggle = (module, action) => {
    setRoleFormPerms(prev => {
      const n = { ...prev, [module]: { ...(prev[module] || {}), [action]: !prev[module]?.[action] } };
      return n;
    });
  };

  const handleRoleModuleToggleAll = (module, enabled) => {
    const actions = modules[module]?.actions || {};
    setRoleFormPerms(prev => ({
      ...prev,
      [module]: Object.fromEntries(Object.keys(actions).map(a => [a, enabled]))
    }));
  };

  const handleSaveRole = async () => {
    if (!roleForm.label.trim()) { toast.error('Role name is required'); return; }
    setSavingRole(true);
    try {
      const payload = {
        label: roleForm.label,
        description: roleForm.description,
        pin_tier: roleForm.pin_tier,
        base_preset: roleForm.base_preset,
        permissions: Object.keys(roleFormPerms).length > 0 ? roleFormPerms : undefined,
      };
      if (editingRole) {
        await api.put(`/roles/${editingRole.id}`, payload);
        toast.success(`Role "${roleForm.label}" updated`);
      } else {
        await api.post('/roles', payload);
        toast.success(`Role "${roleForm.label}" created`);
      }
      setRoleDialog(false);
      fetchRoles();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed to save role'); }
    setSavingRole(false);
  };

  const openDeleteRole = (role) => { setDeleteRoleTarget(role); setDeleteRoleDialog(true); };

  const handleDeleteRole = async () => {
    try {
      await api.delete(`/roles/${deleteRoleTarget.id}`);
      toast.success(`Role "${deleteRoleTarget.label}" deleted`);
      setDeleteRoleDialog(false);
      fetchRoles();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed to delete role'); }
  };

  const handleCloneRole = async (role) => {
    try {
      const res = await api.post(`/roles/${role.id}/clone`);
      toast.success(`Cloned as "${res.data.label}"`);
      fetchRoles();
      // Auto-open edit dialog so user can rename right away
      openEditRole(res.data);
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed to clone role'); }
  };

  // ── User CRUD ──────────────────────────────────────────────────────────────
  const openCreate = () => { setEditingUser(null); setForm(BLANK_FORM); setUserDialog(true); };
  const openEdit = (u) => {
    setEditingUser(u);
    setForm({
      full_name: u.full_name || '', email: u.email || '',
      phone: u.phone || '',
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
      const payload = {
        full_name: form.full_name,
        email: form.email,
        phone: (form.phone || '').trim(),
        role: form.role,
        branch_id: form.branch_id || null,
      };
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
          {isAdmin && (
            <TabsTrigger value="roles" data-testid="roles-tab" className="flex items-center gap-1.5">
              <Layers size={14} /> Roles
            </TabsTrigger>
          )}
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
                              <p className="text-[10px] mt-0.5 flex items-center gap-1"
                                 data-testid={`user-phone-display-${u.id}`}>
                                {u.phone
                                  ? <span className="text-slate-500 font-mono">📱 {u.phone}</span>
                                  : <span className="text-amber-600 italic">No phone — won't receive SMS</span>}
                              </p>
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

        {/* ── Roles Tab ────────────────────────────────────────────────── */}
        {isAdmin && (
          <TabsContent value="roles" className="space-y-5">
            {/* System Roles — read only */}
            <div>
              <p className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-3">System Roles</p>
              <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
                {systemRoles.map(r => (
                  <Card key={r.id} className="border-slate-200">
                    <CardContent className="p-4">
                      <div className="flex items-center gap-2 mb-2">
                        <Badge className={`text-[10px] ${getRoleMeta(r.id).color}`}>{r.label}</Badge>
                        <Badge className={`text-[10px] ${r.pin_tier === 'manager' ? 'bg-blue-100 text-blue-600' : 'bg-teal-100 text-teal-600'}`}>
                          {r.pin_tier === 'manager' ? 'Manager PIN' : 'Staff PIN'}
                        </Badge>
                      </div>
                      <p className="text-xs text-slate-500">{r.description}</p>
                      <p className="text-[10px] text-slate-300 mt-2 font-mono">System — read only</p>
                    </CardContent>
                  </Card>
                ))}
              </div>
            </div>

            {/* Custom Roles */}
            <div>
              <div className="flex items-center justify-between mb-3">
                <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">Custom Roles</p>
                <Button size="sm" onClick={openCreateRole} className="bg-[#1A4D2E] hover:bg-[#14532d] text-white h-8 text-xs gap-1.5" data-testid="create-role-btn">
                  <Plus size={13} /> New Role
                </Button>
              </div>
              {customRoles.length === 0 ? (
                <Card className="border-dashed border-slate-200">
                  <CardContent className="p-10 text-center">
                    <Layers size={36} className="mx-auto text-slate-200 mb-3" />
                    <p className="text-sm text-slate-400 font-medium">No custom roles yet</p>
                    <p className="text-xs text-slate-300 mt-1">Create roles tailored to your business — e.g. "Warehouse Lead", "Delivery Staff"</p>
                    <Button size="sm" onClick={openCreateRole} variant="outline" className="mt-4 text-xs">
                      <Plus size={12} className="mr-1" /> Create First Role
                    </Button>
                  </CardContent>
                </Card>
              ) : (
                <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
                  {customRoles.map(r => (
                    <Card key={r.id} className="border-slate-200 hover:border-slate-300 transition-colors" data-testid={`role-card-${r.id}`}>
                      <CardContent className="p-4">
                        <div className="flex items-start justify-between mb-2">
                          <div>
                            <p className="font-semibold text-sm text-slate-800">{r.label}</p>
                            <p className="text-xs text-slate-400 mt-0.5">{r.description || 'No description'}</p>
                          </div>
                          <div className="flex gap-1 shrink-0 ml-2">
                            <Button variant="ghost" size="sm" className="h-7 w-7 p-0 text-slate-400 hover:text-cyan-600" onClick={() => handleCloneRole(r)} title="Clone role" data-testid={`clone-role-${r.id}`}>
                              <Copy size={13} />
                            </Button>
                            <Button variant="ghost" size="sm" className="h-7 w-7 p-0 text-slate-400 hover:text-slate-700" onClick={() => openEditRole(r)} data-testid={`edit-role-${r.id}`}>
                              <Edit2 size={13} />
                            </Button>
                            <Button variant="ghost" size="sm" className="h-7 w-7 p-0 text-slate-400 hover:text-red-500" onClick={() => openDeleteRole(r)} data-testid={`delete-role-${r.id}`}>
                              <Trash2 size={13} />
                            </Button>
                          </div>
                        </div>
                        <div className="flex items-center gap-2 mt-3">
                          <Badge className={`text-[10px] ${r.pin_tier === 'manager' ? 'bg-blue-100 text-blue-600' : 'bg-teal-100 text-teal-600'}`}>
                            {r.pin_tier === 'manager' ? 'Manager PIN' : 'Staff PIN'}
                          </Badge>
                          {r.user_count > 0 && (
                            <Badge className="text-[10px] bg-slate-100 text-slate-600">{r.user_count} user{r.user_count !== 1 ? 's' : ''}</Badge>
                          )}
                        </div>
                      </CardContent>
                    </Card>
                  ))}
                </div>
              )}
            </div>
          </TabsContent>
        )}
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
                    {customRoles.length > 0 && (
                      <>
                        <div className="px-2 py-1.5 text-[10px] font-semibold text-slate-400 uppercase tracking-wider border-t border-slate-100 mt-1">Custom Roles</div>
                        {customRoles.map(r => (
                          <SelectItem key={r.id} value={r.id}>{r.label}</SelectItem>
                        ))}
                      </>
                    )}
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
              <Label className="text-xs flex items-center gap-1">
                Mobile Phone
                <span className="text-[10px] font-normal text-slate-400">(used for SMS reminders & alerts)</span>
              </Label>
              <Input
                type="tel"
                value={form.phone}
                onChange={e => setForm({ ...form, phone: e.target.value })}
                placeholder="09XX XXX XXXX"
                className="h-9"
                data-testid="user-phone"
              />
              <p className="text-[10px] text-slate-400 mt-1">
                Optional now, but required to receive close-day SMS, daily Z-report
                summaries, and approval pings. The Collection Recipients in
                Messages → Settings is just a fallback for when no team user
                with this role has a phone set.
              </p>
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

      {/* ── Role Create/Edit Dialog ──────────────────────────────────────── */}
      <Dialog open={roleDialog} onOpenChange={setRoleDialog}>
        <DialogContent className="sm:max-w-2xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle style={{ fontFamily: 'Manrope' }} className="flex items-center gap-2">
              <Layers size={18} className="text-cyan-600" />
              {editingRole ? `Edit Role: ${editingRole.label}` : 'Create Custom Role'}
            </DialogTitle>
            <DialogDescription>
              {editingRole ? 'Update this role\'s name, PIN tier, and permissions.' : 'Define a new role with a name, PIN tier, and permission set.'}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label className="text-xs">Role Name <span className="text-red-500">*</span></Label>
                <Input value={roleForm.label} onChange={e => setRoleForm(f => ({ ...f, label: e.target.value }))} placeholder="e.g. Warehouse Lead" className="h-9" data-testid="role-label-input" />
              </div>
              <div>
                <Label className="text-xs">PIN Tier</Label>
                <Select value={roleForm.pin_tier} onValueChange={v => setRoleForm(f => ({ ...f, pin_tier: v }))}>
                  <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="staff">Staff PIN — stock releases only</SelectItem>
                    <SelectItem value="manager">Manager PIN — financial approvals</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div>
              <Label className="text-xs">Description (optional)</Label>
              <Input value={roleForm.description} onChange={e => setRoleForm(f => ({ ...f, description: e.target.value }))} placeholder="Brief description of this role's purpose" className="h-9" />
            </div>
            {!editingRole && (
              <div>
                <Label className="text-xs">Start from preset</Label>
                <Select value={roleForm.base_preset} onValueChange={handleBasePresetChange}>
                  <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="admin">Administrator (full access)</SelectItem>
                    <SelectItem value="manager">Branch Manager</SelectItem>
                    <SelectItem value="cashier">Cashier</SelectItem>
                    <SelectItem value="inventory_clerk">Inventory Clerk</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-[10px] text-slate-400 mt-1">You can fine-tune permissions below after selecting a base.</p>
              </div>
            )}

            {/* Permission editor */}
            <div>
              <Label className="text-xs font-semibold">Permissions</Label>
              <div className="mt-2 border border-slate-200 rounded-lg overflow-hidden">
                <ScrollArea className="h-64">
                  <div className="divide-y divide-slate-100">
                    {Object.entries(modules).map(([mk, md]) => {
                      const mp = roleFormPerms[mk] || {};
                      const total = Object.keys(md.actions || {}).length;
                      const enabled = Object.values(mp).filter(Boolean).length;
                      const status = enabled === 0 ? { label: 'None', cls: 'text-slate-400' } : enabled === total ? { label: 'Full', cls: 'text-emerald-600' } : { label: `${enabled}/${total}`, cls: 'text-amber-600' };
                      return (
                        <div key={mk} className="p-3">
                          <div className="flex items-center justify-between mb-2">
                            <div className="flex items-center gap-2">
                              <span className="text-xs font-semibold text-slate-700">{md.label}</span>
                              <span className={`text-[10px] font-medium ${status.cls}`}>{status.label}</span>
                            </div>
                            <div className="flex gap-1">
                              <button className="text-[10px] text-slate-400 hover:text-slate-600 px-1.5 py-0.5 rounded hover:bg-slate-100" onClick={() => handleRoleModuleToggleAll(mk, false)}>None</button>
                              <button className="text-[10px] text-slate-400 hover:text-slate-600 px-1.5 py-0.5 rounded hover:bg-slate-100" onClick={() => handleRoleModuleToggleAll(mk, true)}>All</button>
                            </div>
                          </div>
                          <div className="flex flex-wrap gap-1.5">
                            {Object.entries(md.actions).map(([ak, al]) => (
                              <button key={ak} onClick={() => handleRolePermToggle(mk, ak)}
                                className={`text-[10px] px-2 py-1 rounded-md border transition-colors ${mp[ak] ? 'bg-emerald-50 border-emerald-200 text-emerald-700' : 'bg-white border-slate-200 text-slate-400 hover:border-slate-300'}`}>
                                {al}
                              </button>
                            ))}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </ScrollArea>
              </div>
            </div>

            <div className="flex gap-2 pt-2">
              <Button variant="outline" className="flex-1" onClick={() => setRoleDialog(false)}>Cancel</Button>
              <Button className="flex-1 bg-[#1A4D2E] hover:bg-[#14532d] text-white" onClick={handleSaveRole} disabled={savingRole} data-testid="save-role-btn">
                {savingRole ? <RefreshCw size={13} className="animate-spin mr-1.5" /> : null}
                {editingRole ? 'Save Changes' : 'Create Role'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* ── Delete Role Dialog ───────────────────────────────────────────── */}
      <Dialog open={deleteRoleDialog} onOpenChange={setDeleteRoleDialog}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-red-600" style={{ fontFamily: 'Manrope' }}>
              <AlertTriangle size={18} /> Delete Role
            </DialogTitle>
            <DialogDescription>
              Delete <strong>{deleteRoleTarget?.label}</strong>?
              {deleteRoleTarget?.user_count > 0
                ? <span className="text-red-500 block mt-1"> {deleteRoleTarget.user_count} user(s) are still assigned. Reassign them first.</span>
                : ' This cannot be undone.'
              }
            </DialogDescription>
          </DialogHeader>
          <div className="flex gap-2 mt-2">
            <Button variant="outline" className="flex-1" onClick={() => setDeleteRoleDialog(false)}>Cancel</Button>
            <Button className="flex-1 bg-red-600 hover:bg-red-700 text-white" onClick={handleDeleteRole} disabled={deleteRoleTarget?.user_count > 0} data-testid="confirm-delete-role-btn">
              <Trash2 size={14} className="mr-1" /> Delete Role
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
