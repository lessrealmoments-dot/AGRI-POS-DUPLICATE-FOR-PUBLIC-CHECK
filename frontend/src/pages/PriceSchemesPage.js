import { useState, useEffect } from 'react';
import { api } from '../contexts/AuthContext';
import { Card, CardContent } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { Tags, Plus, Pencil, Trash2, RotateCcw } from 'lucide-react';
import { toast } from 'sonner';

const CALC_METHODS = [
  { value: 'fixed', label: 'Fixed Price' },
  { value: 'percent_plus_capital', label: '% Plus from Cost' },
  { value: 'plus_capital', label: 'Amount Plus from Cost' },
  { value: 'percent_plus_retail', label: '% Plus from Retail' },
  { value: 'percent_minus_retail', label: '% Minus from Retail' },
  { value: 'plus_retail', label: 'Amount Plus from Retail' },
  { value: 'minus_retail', label: 'Amount Minus from Retail' },
];

export default function PriceSchemesPage() {
  const [schemes, setSchemes] = useState([]);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({ name: '', key: '', description: '', calculation_method: 'fixed', calculation_value: 0, base_scheme: 'cost_price' });

  const fetchSchemes = async () => {
    try { const res = await api.get('/price-schemes'); setSchemes(res.data); }
    catch { toast.error('Failed to load price schemes'); }
  };

  useEffect(() => { fetchSchemes(); }, []);

  const openCreate = () => { setEditing(null); setForm({ name: '', key: '', description: '', calculation_method: 'fixed', calculation_value: 0, base_scheme: 'cost_price' }); setDialogOpen(true); };
  const openEdit = (s) => { setEditing(s); setForm({ name: s.name, key: s.key, description: s.description || '', calculation_method: s.calculation_method, calculation_value: s.calculation_value, base_scheme: s.base_scheme }); setDialogOpen(true); };

  const handleSave = async () => {
    try {
      const data = { ...form, key: form.key || form.name.toLowerCase().replace(/\s+/g, '_') };
      if (editing) { await api.put(`/price-schemes/${editing.id}`, data); toast.success('Price scheme updated'); }
      else { await api.post('/price-schemes', data); toast.success('Price scheme created'); }
      setDialogOpen(false); fetchSchemes();
    } catch (e) { toast.error(e.response?.data?.detail || 'Error saving'); }
  };

  const handleDelete = async (id) => {
    if (!window.confirm('Delete this price scheme?')) return;
    try { await api.delete(`/price-schemes/${id}`); toast.success('Deleted'); fetchSchemes(); }
    catch { toast.error('Failed to delete'); }
  };

  const handleRestoreDefaults = async () => {
    if (!window.confirm('Restore the default Retail / Wholesale / Special schemes? Existing schemes are not modified.')) return;
    try {
      const res = await api.post('/price-schemes/restore-defaults');
      const created = res.data?.created || [];
      if (created.length === 0) toast.info('All default schemes already exist.');
      else toast.success(`${created.length} scheme(s) restored: ${created.map(c => c.name).join(', ')}`);
      fetchSchemes();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to restore defaults');
    }
  };

  return (
    <div className="space-y-6 animate-fadeIn" data-testid="price-schemes-page">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight" style={{ fontFamily: 'Manrope' }}>Price Schemes</h1>
          <p className="text-sm text-slate-500 mt-1">Manage pricing tiers for different customer types</p>
        </div>
        <Button data-testid="create-scheme-btn" onClick={openCreate} className="bg-[#1A4D2E] hover:bg-[#14532d] text-white">
          <Plus size={16} className="mr-2" /> Add Scheme
        </Button>
      </div>

      {schemes.length < 3 && (
        <Card className="border-amber-200 bg-amber-50">
          <CardContent className="py-3 px-5 flex items-center justify-between gap-3">
            <div className="text-sm">
              <p className="font-medium text-amber-900">
                {schemes.length === 0 ? 'No price schemes found.' : `Only ${schemes.length} scheme${schemes.length === 1 ? '' : 's'} configured.`}
              </p>
              <p className="text-amber-700 text-xs mt-0.5">
                Restore the standard Retail / Wholesale / Special schemes to enable multi-tier pricing across Sales, Terminal, and Reports.
              </p>
            </div>
            <Button
              size="sm"
              variant="outline"
              data-testid="restore-default-schemes-btn"
              onClick={handleRestoreDefaults}
              className="border-amber-400 text-amber-800 hover:bg-amber-100 shrink-0"
            >
              <RotateCcw size={14} className="mr-2" /> Restore Defaults
            </Button>
          </CardContent>
        </Card>
      )}

      <Card className="border-slate-200">
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow className="bg-slate-50">
                <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">Name</TableHead>
                <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">Key</TableHead>
                <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">Calculation</TableHead>
                <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">Value</TableHead>
                <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">Base</TableHead>
                <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium w-24">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {schemes.map(s => (
                <TableRow key={s.id} className="table-row-hover">
                  <TableCell className="font-medium">{s.name}</TableCell>
                  <TableCell className="font-mono text-xs text-slate-500">{s.key}</TableCell>
                  <TableCell className="text-sm">{CALC_METHODS.find(m => m.value === s.calculation_method)?.label || s.calculation_method}</TableCell>
                  <TableCell>{s.calculation_value}</TableCell>
                  <TableCell className="text-sm text-slate-500 capitalize">{s.base_scheme?.replace('_', ' ')}</TableCell>
                  <TableCell>
                    <div className="flex gap-1">
                      <Button variant="ghost" size="sm" onClick={() => openEdit(s)}><Pencil size={14} /></Button>
                      <Button variant="ghost" size="sm" onClick={() => handleDelete(s.id)} className="text-red-500"><Trash2 size={14} /></Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader><DialogTitle style={{ fontFamily: 'Manrope' }}>{editing ? 'Edit Scheme' : 'New Price Scheme'}</DialogTitle></DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="grid grid-cols-2 gap-4">
              <div><Label>Name</Label><Input data-testid="scheme-name-input" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="e.g. Wholesale" /></div>
              <div><Label>Key</Label><Input data-testid="scheme-key-input" value={form.key} onChange={e => setForm({ ...form, key: e.target.value })} placeholder="auto-generated" /></div>
            </div>
            <div><Label>Description</Label><Input value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} /></div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label>Calculation Method</Label>
                <Select value={form.calculation_method} onValueChange={v => setForm({ ...form, calculation_method: v })}>
                  <SelectTrigger data-testid="scheme-method-select"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {CALC_METHODS.map(m => <SelectItem key={m.value} value={m.value}>{m.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div><Label>Value</Label><Input data-testid="scheme-value-input" type="number" value={form.calculation_value} onChange={e => setForm({ ...form, calculation_value: parseFloat(e.target.value) || 0 })} /></div>
            </div>
            <div>
              <Label>Base Scheme</Label>
              <Select value={form.base_scheme} onValueChange={v => setForm({ ...form, base_scheme: v })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="cost_price">Cost Price</SelectItem>
                  <SelectItem value="retail">Retail Price</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setDialogOpen(false)}>Cancel</Button>
              <Button data-testid="save-scheme-btn" onClick={handleSave} className="bg-[#1A4D2E] hover:bg-[#14532d] text-white">Save</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
