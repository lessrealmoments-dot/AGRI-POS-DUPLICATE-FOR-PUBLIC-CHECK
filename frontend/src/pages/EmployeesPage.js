import { useState, useEffect, useCallback } from 'react';
import { useAuth, api } from '../contexts/AuthContext';
import { formatPHP } from '../lib/utils';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Badge } from '../components/ui/badge';
import { Card, CardContent } from '../components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '../components/ui/dialog';
import { Separator } from '../components/ui/separator';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs';
import { Progress } from '../components/ui/progress';
import {
  Plus, User, Building2, Phone, Mail, Search, Edit2, Trash2,
  TrendingUp, AlertTriangle, CreditCard, Calendar, ChevronRight, UserCheck
} from 'lucide-react';
import { toast } from 'sonner';
import CalcInput from '../components/CalcInput';

const EMPLOYMENT_TYPES = [
  { key: 'regular', label: 'Regular' },
  { key: 'contractual', label: 'Contractual' },
  { key: 'daily_wage', label: 'Daily Wage' },
  { key: 'probationary', label: 'Probationary' },
];

const BLANK_EMP = {
  name: '', position: '', employment_type: 'regular', phone: '', email: '', address: '',
  branch_id: '', hire_date: new Date().toISOString().slice(0, 10),
  salary: 0, daily_rate: 0, monthly_ca_limit: 0,
  sss_number: '', philhealth_number: '', pagibig_number: '', tin_number: '',
  emergency_contact_name: '', emergency_contact_phone: '', notes: ''
};

export default function EmployeesPage() {
  const { currentBranch, branches } = useAuth();
  const [employees, setEmployees] = useState([]);
  const [search, setSearch] = useState('');
  const [empDialog, setEmpDialog] = useState(false);
  const [detailDialog, setDetailDialog] = useState(false);
  const [editingEmp, setEditingEmp] = useState(null);
  const [viewingEmp, setViewingEmp] = useState(null);
  const [caSummary, setCaSummary] = useState(null);
  const [caHistory, setCaHistory] = useState([]);
  const [deductDialog, setDeductDialog] = useState(false);
  const [deductForm, setDeductForm] = useState({ amount: 0, reason: 'Salary deduction' });
  const [form, setForm] = useState(BLANK_EMP);
  const [saving, setSaving] = useState(false);
  const [activeTab, setActiveTab] = useState('profile');

  const fetchEmployees = useCallback(async () => {
    try {
      const params = currentBranch ? { branch_id: currentBranch.id } : {};
      const res = await api.get('/employees', { params });
      setEmployees(res.data);
    } catch { toast.error('Failed to load employees'); }
  }, [currentBranch]);

  useEffect(() => { fetchEmployees(); }, [fetchEmployees]);

  const fetchCaSummary = async (emp) => {
    try {
      const [sumRes, histRes] = await Promise.all([
        api.get(`/employees/${emp.id}/ca-summary`),
        api.get(`/employees/${emp.id}/advances`),
      ]);
      setCaSummary(sumRes.data);
      setCaHistory(histRes.data);
    } catch { toast.error('Failed to load CA data'); }
  };

  const openDetail = async (emp) => {
    setViewingEmp(emp);
    setActiveTab('profile');
    setCaSummary(null);
    setCaHistory([]);
    setDetailDialog(true);
    fetchCaSummary(emp);
  };

  const openCreate = () => {
    setEditingEmp(null);
    setForm({ ...BLANK_EMP, branch_id: currentBranch?.id || '' });
    setEmpDialog(true);
  };

  const openEdit = (emp) => {
    setEditingEmp(emp);
    setForm({
      name: emp.name || '', position: emp.position || '',
      employment_type: emp.employment_type || 'regular',
      phone: emp.phone || '', email: emp.email || '', address: emp.address || '',
      branch_id: emp.branch_id || '', hire_date: emp.hire_date || new Date().toISOString().slice(0, 10),
      salary: emp.salary || 0, daily_rate: emp.daily_rate || 0, monthly_ca_limit: emp.monthly_ca_limit || 0,
      sss_number: emp.sss_number || '', philhealth_number: emp.philhealth_number || '',
      pagibig_number: emp.pagibig_number || '', tin_number: emp.tin_number || '',
      emergency_contact_name: emp.emergency_contact_name || '',
      emergency_contact_phone: emp.emergency_contact_phone || '',
      notes: emp.notes || ''
    });
    setEmpDialog(true);
  };

  const handleSave = async () => {
    if (!form.name.trim()) { toast.error('Employee name is required'); return; }
    setSaving(true);
    try {
      if (editingEmp) {
        await api.put(`/employees/${editingEmp.id}`, form);
        toast.success(`${form.name} updated`);
      } else {
        await api.post('/employees', form);
        toast.success(`${form.name} added`);
      }
      setEmpDialog(false);
      fetchEmployees();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed to save'); }
    setSaving(false);
  };

  const handleDeduct = async () => {
    if (!deductForm.amount || deductForm.amount <= 0) { toast.error('Enter a valid amount'); return; }
    try {
      const res = await api.post(`/employees/${viewingEmp.id}/deduct-advance`, deductForm);
      toast.success(res.data.message);
      setDeductDialog(false);
      fetchCaSummary(viewingEmp);
      fetchEmployees();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed'); }
  };

  const handleDelete = async (emp) => {
    if (!window.confirm(`Deactivate ${emp.name}?`)) return;
    try {
      await api.delete(`/employees/${emp.id}`);
      toast.success(`${emp.name} deactivated`);
      fetchEmployees();
    } catch { toast.error('Failed to deactivate'); }
  };

  const getBranchName = (id) => branches.find(b => b.id === id)?.name || (id ? 'Unknown' : '—');

  const filtered = employees.filter(e =>
    !search || e.name?.toLowerCase().includes(search.toLowerCase()) ||
    e.position?.toLowerCase().includes(search.toLowerCase())
  );

  // Dashboard stats
  const totalActive = employees.filter(e => e.active !== false).length;
  const totalCABalance = employees.reduce((s, e) => s + (e.advance_balance || 0), 0);
  const overLimitCount = employees.filter(e => {
    const limit = e.monthly_ca_limit || 0;
    return limit > 0 && (e.advance_balance || 0) > limit;
  }).length;

  const CaProgressBar = ({ emp }) => {
    const limit = emp.monthly_ca_limit || 0;
    if (limit === 0) return null;
    const pct = Math.min(100, ((emp.advance_balance || 0) / limit) * 100);
    return (
      <div className="mt-2 space-y-1">
        <div className="flex justify-between text-[10px] text-slate-400">
          <span>CA Balance</span>
          <span className={pct >= 100 ? 'text-red-600 font-medium' : ''}>{formatPHP(emp.advance_balance || 0)} / {formatPHP(limit)}</span>
        </div>
        <Progress value={pct} className={`h-1.5 ${pct >= 100 ? '[&>div]:bg-red-500' : pct >= 80 ? '[&>div]:bg-amber-500' : '[&>div]:bg-emerald-500'}`} />
      </div>
    );
  };

  return (
    <div className="space-y-6 animate-fadeIn" data-testid="employees-page">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight" style={{ fontFamily: 'Manrope' }}>Employees</h1>
          <p className="text-sm text-slate-500 mt-0.5">Staff profiles, cash advances, and attendance</p>
        </div>
        <Button onClick={openCreate} className="bg-[#1A4D2E] hover:bg-[#14532d] text-white gap-2" data-testid="add-employee-btn">
          <Plus size={16} /> Add Employee
        </Button>
      </div>

      {/* Dashboard Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          { label: 'Active Staff', value: totalActive, icon: UserCheck, color: 'text-emerald-600 bg-emerald-50' },
          { label: 'Total CA Balance', value: formatPHP(totalCABalance), icon: CreditCard, color: 'text-blue-600 bg-blue-50' },
          { label: 'Over Limit', value: overLimitCount, icon: AlertTriangle, color: overLimitCount > 0 ? 'text-red-600 bg-red-50' : 'text-slate-400 bg-slate-50' },
          { label: 'Total Employees', value: employees.length, icon: User, color: 'text-slate-600 bg-slate-100' },
        ].map((stat, i) => (
          <Card key={i} className="border-slate-200">
            <CardContent className="p-3 flex items-center gap-3">
              <div className={`w-9 h-9 rounded-lg flex items-center justify-center ${stat.color}`}>
                <stat.icon size={17} />
              </div>
              <div>
                <p className="text-lg font-bold">{stat.value}</p>
                <p className="text-[11px] text-slate-500">{stat.label}</p>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Search */}
      <div className="relative w-72">
        <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
        <Input className="pl-9 h-9" placeholder="Search employees..." value={search} onChange={e => setSearch(e.target.value)} />
      </div>

      {/* Employee Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {filtered.map(emp => (
          <Card key={emp.id} className="border-slate-200 hover:shadow-md transition-shadow cursor-pointer" onClick={() => openDetail(emp)}>
            <CardContent className="p-4">
              <div className="flex items-start justify-between mb-3">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-full bg-[#1A4D2E] flex items-center justify-center text-white font-bold text-sm">
                    {emp.name?.[0]?.toUpperCase() || 'E'}
                  </div>
                  <div>
                    <p className="font-semibold text-sm">{emp.name}</p>
                    <p className="text-[11px] text-slate-500">{emp.position || 'No position'}</p>
                  </div>
                </div>
                <div className="flex gap-1" onClick={e => e.stopPropagation()}>
                  <Button variant="ghost" size="sm" className="h-7 w-7 p-0 text-slate-400 hover:text-slate-600" onClick={() => openEdit(emp)}>
                    <Edit2 size={13} />
                  </Button>
                </div>
              </div>
              <div className="space-y-1 text-xs text-slate-500">
                {emp.branch_id && (
                  <div className="flex items-center gap-1">
                    <Building2 size={11} /> {getBranchName(emp.branch_id)}
                  </div>
                )}
                {emp.phone && <div className="flex items-center gap-1"><Phone size={11} /> {emp.phone}</div>}
                <div className="flex items-center gap-1">
                  <Calendar size={11} /> Hired {emp.hire_date || '—'}
                </div>
              </div>
              <CaProgressBar emp={emp} />
              {emp.advance_balance > 0 && !emp.monthly_ca_limit && (
                <p className="text-[10px] text-amber-600 mt-2 flex items-center gap-1">
                  <CreditCard size={9} /> CA Balance: {formatPHP(emp.advance_balance)}
                </p>
              )}
              <div className="flex items-center justify-between mt-3 pt-2 border-t border-slate-100">
                <Badge variant="outline" className="text-[10px] capitalize">{emp.employment_type || 'regular'}</Badge>
                <span className="text-[10px] text-slate-400 flex items-center gap-0.5">View <ChevronRight size={10} /></span>
              </div>
            </CardContent>
          </Card>
        ))}
        {filtered.length === 0 && (
          <div className="col-span-full py-12 text-center text-slate-400">
            <User size={40} className="mx-auto mb-3 opacity-30" />
            <p>No employees found</p>
            <Button variant="outline" size="sm" className="mt-3" onClick={openCreate}>Add First Employee</Button>
          </div>
        )}
      </div>

      {/* Create/Edit Dialog */}
      <Dialog open={empDialog} onOpenChange={setEmpDialog}>
        <DialogContent className="sm:max-w-2xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle style={{ fontFamily: 'Manrope' }}>{editingEmp ? `Edit — ${editingEmp.name}` : 'Add New Employee'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label className="text-xs">Full Name *</Label>
                <Input value={form.name} onChange={e => setForm({...form, name: e.target.value})} placeholder="Juan dela Cruz" className="h-9" data-testid="emp-name" />
              </div>
              <div>
                <Label className="text-xs">Position / Job Title</Label>
                <Input value={form.position} onChange={e => setForm({...form, position: e.target.value})} placeholder="Cashier, Driver, etc." className="h-9" />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label className="text-xs">Employment Type</Label>
                <Select value={form.employment_type} onValueChange={v => setForm({...form, employment_type: v})}>
                  <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
                  <SelectContent>{EMPLOYMENT_TYPES.map(t => <SelectItem key={t.key} value={t.key}>{t.label}</SelectItem>)}</SelectContent>
                </Select>
              </div>
              <div>
                <Label className="text-xs">Branch</Label>
                <Select value={form.branch_id || 'none'} onValueChange={v => setForm({...form, branch_id: v === 'none' ? '' : v})}>
                  <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">No branch assigned</SelectItem>
                    {branches.map(b => <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label className="text-xs">Phone</Label>
                <Input value={form.phone} onChange={e => setForm({...form, phone: e.target.value})} placeholder="09xx xxx xxxx" className="h-9" />
              </div>
              <div>
                <Label className="text-xs">Hire Date</Label>
                <Input type="date" value={form.hire_date} onChange={e => setForm({...form, hire_date: e.target.value})} className="h-9" />
              </div>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <Label className="text-xs">Monthly Salary</Label>
                <CalcInput value={form.salary} onChange={(v) => setForm({...form, salary: parseFloat(v) || 0})} className="h-9" />
              </div>
              <div>
                <Label className="text-xs">Daily Rate</Label>
                <CalcInput value={form.daily_rate} onChange={(v) => setForm({...form, daily_rate: parseFloat(v) || 0})} className="h-9" />
              </div>
              <div>
                <Label className="text-xs flex items-center gap-1"><CreditCard size={10} /> Monthly CA Limit</Label>
                <CalcInput value={form.monthly_ca_limit} onChange={(v) => setForm({...form, monthly_ca_limit: parseFloat(v) || 0})}
 placeholder="0 = no limit" className="h-9" />
              </div>
            </div>
            <Separator />
            <p className="text-xs font-medium text-slate-500 uppercase tracking-wide">Government IDs</p>
            <div className="grid grid-cols-2 gap-3">
              {[['sss_number', 'SSS Number'], ['philhealth_number', 'PhilHealth Number'], ['pagibig_number', 'Pag-IBIG Number'], ['tin_number', 'TIN Number']].map(([field, label]) => (
                <div key={field}>
                  <Label className="text-xs">{label}</Label>
                  <Input value={form[field]} onChange={e => setForm({...form, [field]: e.target.value})} className="h-9" />
                </div>
              ))}
            </div>
            <Separator />
            <p className="text-xs font-medium text-slate-500 uppercase tracking-wide">Emergency Contact</p>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label className="text-xs">Contact Name</Label>
                <Input value={form.emergency_contact_name} onChange={e => setForm({...form, emergency_contact_name: e.target.value})} className="h-9" />
              </div>
              <div>
                <Label className="text-xs">Contact Phone</Label>
                <Input value={form.emergency_contact_phone} onChange={e => setForm({...form, emergency_contact_phone: e.target.value})} className="h-9" />
              </div>
            </div>
            <div>
              <Label className="text-xs">Notes</Label>
              <Input value={form.notes} onChange={e => setForm({...form, notes: e.target.value})} placeholder="Additional notes..." className="h-9" />
            </div>
            <div className="flex gap-2 pt-2">
              <Button variant="outline" className="flex-1" onClick={() => setEmpDialog(false)}>Cancel</Button>
              <Button className="flex-1 bg-[#1A4D2E] hover:bg-[#14532d] text-white" onClick={handleSave} disabled={saving} data-testid="save-employee-btn">
                {saving ? 'Saving...' : (editingEmp ? 'Save Changes' : 'Add Employee')}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Employee Detail Dialog */}
      <Dialog open={detailDialog} onOpenChange={setDetailDialog}>
        <DialogContent className="sm:max-w-xl max-h-[90vh] overflow-y-auto">
          {viewingEmp && (
            <>
              <DialogHeader>
                <div className="flex items-center gap-3">
                  <div className="w-12 h-12 rounded-full bg-[#1A4D2E] flex items-center justify-center text-white font-bold text-lg">
                    {viewingEmp.name?.[0]?.toUpperCase()}
                  </div>
                  <div>
                    <DialogTitle style={{ fontFamily: 'Manrope' }}>{viewingEmp.name}</DialogTitle>
                    <p className="text-sm text-slate-500">{viewingEmp.position || 'Employee'} • {getBranchName(viewingEmp.branch_id)}</p>
                  </div>
                </div>
              </DialogHeader>
              <Tabs value={activeTab} onValueChange={setActiveTab}>
                <TabsList className="grid grid-cols-2 w-full">
                  <TabsTrigger value="profile">Profile</TabsTrigger>
                  <TabsTrigger value="ca">Cash Advances</TabsTrigger>
                </TabsList>

                <TabsContent value="profile" className="space-y-4 mt-4">
                  <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
                    {[
                      ['Employment', viewingEmp.employment_type || 'Regular'],
                      ['Hire Date', viewingEmp.hire_date || '—'],
                      ['Phone', viewingEmp.phone || '—'],
                      ['Email', viewingEmp.email || '—'],
                      ['Monthly Salary', formatPHP(viewingEmp.salary || 0)],
                      ['Daily Rate', formatPHP(viewingEmp.daily_rate || 0)],
                      ['SSS', viewingEmp.sss_number || '—'],
                      ['PhilHealth', viewingEmp.philhealth_number || '—'],
                      ['Pag-IBIG', viewingEmp.pagibig_number || '—'],
                      ['TIN', viewingEmp.tin_number || '—'],
                    ].map(([k, v]) => (
                      <div key={k}>
                        <p className="text-xs text-slate-400">{k}</p>
                        <p className="font-medium text-slate-700">{v}</p>
                      </div>
                    ))}
                  </div>
                  {(viewingEmp.emergency_contact_name || viewingEmp.emergency_contact_phone) && (
                    <div className="bg-slate-50 rounded-lg p-3">
                      <p className="text-xs font-medium text-slate-500 mb-1">Emergency Contact</p>
                      <p className="text-sm font-medium">{viewingEmp.emergency_contact_name}</p>
                      <p className="text-sm text-slate-500">{viewingEmp.emergency_contact_phone}</p>
                    </div>
                  )}
                  <Button variant="outline" size="sm" className="gap-2" onClick={() => { setDetailDialog(false); openEdit(viewingEmp); }}>
                    <Edit2 size={13} /> Edit Profile
                  </Button>
                </TabsContent>

                <TabsContent value="ca" className="space-y-4 mt-4">
                  {caSummary ? (
                    <>
                      {/* CA Summary Cards */}
                      <div className="grid grid-cols-2 gap-3">
                        <div className={`rounded-lg p-3 ${caSummary.is_over_limit ? 'bg-red-50 border border-red-200' : 'bg-slate-50'}`}>
                          <p className="text-xs text-slate-500">This Month</p>
                          <p className={`text-xl font-bold ${caSummary.is_over_limit ? 'text-red-600' : ''}`}>{formatPHP(caSummary.this_month_total)}</p>
                          {caSummary.monthly_ca_limit > 0 && (
                            <p className="text-xs text-slate-400">Limit: {formatPHP(caSummary.monthly_ca_limit)}</p>
                          )}
                        </div>
                        <div className="bg-slate-50 rounded-lg p-3">
                          <p className="text-xs text-slate-500">Total Unpaid Balance</p>
                          <p className="text-xl font-bold text-amber-600">{formatPHP(caSummary.total_advance_balance)}</p>
                          {caSummary.total_advance_balance > 0 && <p className="text-xs text-slate-400">Pending deduction</p>}
                        </div>
                      </div>

                      {/* Over limit warning */}
                      {caSummary.is_over_limit && (
                        <div className="bg-red-50 border border-red-200 rounded-lg p-3 flex items-start gap-2">
                          <AlertTriangle size={15} className="text-red-500 mt-0.5 flex-shrink-0" />
                          <div>
                            <p className="text-sm font-medium text-red-700">Monthly Limit Reached</p>
                            <p className="text-xs text-red-600">This employee has used all their monthly cash advance allowance. Manager approval required for additional advances.</p>
                          </div>
                        </div>
                      )}

                      {/* Previous month overage */}
                      {caSummary.prev_month_overage > 0 && (
                        <div className="bg-amber-50 border border-amber-200 rounded-lg p-3">
                          <p className="text-xs font-medium text-amber-700">Previous Month Overage</p>
                          <p className="text-sm text-amber-600">{formatPHP(caSummary.prev_month_overage)} carried over from last month — deduct from next salary</p>
                        </div>
                      )}

                      {/* CA Progress */}
                      {caSummary.monthly_ca_limit > 0 && (
                        <div>
                          <div className="flex justify-between text-xs text-slate-500 mb-1">
                            <span>CA Usage This Month</span>
                            <span>{((caSummary.this_month_total / caSummary.monthly_ca_limit) * 100).toFixed(0)}%</span>
                          </div>
                          <Progress
                            value={Math.min(100, (caSummary.this_month_total / caSummary.monthly_ca_limit) * 100)}
                            className={`h-2 ${caSummary.is_over_limit ? '[&>div]:bg-red-500' : '[&>div]:bg-emerald-500'}`}
                          />
                          <p className="text-xs text-slate-400 mt-1">
                            {caSummary.remaining_this_month !== null
                              ? `Remaining: ${formatPHP(caSummary.remaining_this_month)}`
                              : 'No limit set'}
                          </p>
                        </div>
                      )}

                      {/* Deduct button */}
                      {caSummary.total_advance_balance > 0 && (
                        <Button variant="outline" size="sm" className="gap-2 text-amber-600 border-amber-200 hover:bg-amber-50"
                          onClick={() => { setDeductDialog(true); setDeductForm({ amount: 0, reason: 'Salary deduction' }); }}>
                          <TrendingUp size={13} /> Record Salary Deduction
                        </Button>
                      )}

                      {/* CA History */}
                      <div>
                        <p className="text-xs font-medium text-slate-500 uppercase tracking-wide mb-2">This Month's Advances</p>
                        {caSummary.recent_advances.length > 0 ? (
                          <div className="space-y-2">
                            {caSummary.recent_advances.map(a => (
                              <div key={a.id} className="flex items-center justify-between py-2 border-b border-slate-100 last:border-0">
                                <div>
                                  <p className="text-sm font-medium">{a.description || 'Cash Advance'}</p>
                                  <p className="text-xs text-slate-400">{a.date} • {a.created_by_name || '—'}</p>
                                  {a.notes && <p className="text-xs text-slate-400 italic">{a.notes}</p>}
                                </div>
                                <span className="text-sm font-semibold text-amber-600">{formatPHP(a.amount)}</span>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <p className="text-sm text-slate-400 py-4 text-center">No advances this month</p>
                        )}
                      </div>
                    </>
                  ) : (
                    <div className="py-8 text-center text-slate-400 text-sm">Loading CA data...</div>
                  )}
                </TabsContent>
              </Tabs>
            </>
          )}
        </DialogContent>
      </Dialog>

      {/* Deduction Dialog */}
      <Dialog open={deductDialog} onOpenChange={setDeductDialog}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle style={{ fontFamily: 'Manrope' }}>Record Salary Deduction</DialogTitle>
            <DialogDescription>Deduct from {viewingEmp?.name}'s advance balance (unpaid: {formatPHP(caSummary?.total_advance_balance || 0)})</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <Label className="text-xs">Amount to Deduct</Label>
              <CalcInput value={deductForm.amount} onChange={(v) => setDeductForm({...deductForm, amount: parseFloat(v) || 0})}
 className="h-10 text-lg" />
            </div>
            <div>
              <Label className="text-xs">Reason</Label>
              <Input value={deductForm.reason} onChange={e => setDeductForm({...deductForm, reason: e.target.value})} className="h-9" />
            </div>
            <div className="flex gap-2">
              <Button variant="outline" className="flex-1" onClick={() => setDeductDialog(false)}>Cancel</Button>
              <Button className="flex-1 bg-amber-500 hover:bg-amber-600 text-white" onClick={handleDeduct}>Record Deduction</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
