import { useState, useEffect, useCallback, useMemo } from 'react';
import { api, useAuth } from '../contexts/AuthContext';
import { Card, CardContent } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Badge } from '../components/ui/badge';
import { Checkbox } from '../components/ui/checkbox';
import { Tag, Save, Copy, Filter, ArrowLeft, Package, Lock } from 'lucide-react';
import { toast } from 'sonner';
import { useNavigate } from 'react-router-dom';
import { formatPHP } from '../lib/utils';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../components/ui/dialog';

export default function RepackPricingPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [branches, setBranches] = useState([]);
  const [selectedBranchIds, setSelectedBranchIds] = useState(new Set());
  const [withInventoryOnly, setWithInventoryOnly] = useState(true);
  const [missingOnly, setMissingOnly] = useState(false);
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [edits, setEdits] = useState({}); // {`${repack_id}|${branch_id}`: retail}
  const [pinOpen, setPinOpen] = useState(false);
  const [pin, setPin] = useState('');
  const [saving, setSaving] = useState(false);
  const [search, setSearch] = useState('');

  const isPriv = user?.role === 'admin' || user?.role === 'manager';

  // Load branches
  useEffect(() => {
    api.get('/branches').then(r => {
      const list = Array.isArray(r.data) ? r.data : (r.data?.branches || []);
      setBranches(list.filter(b => b.active !== false));
    }).catch(() => {});
  }, []);

  const toggleBranch = (id) => {
    setSelectedBranchIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const selectAllBranches = () => setSelectedBranchIds(new Set(branches.map(b => b.id)));
  const clearBranches = () => setSelectedBranchIds(new Set());

  const loadGrid = useCallback(async () => {
    if (!selectedBranchIds.size) {
      toast.warning('Pick at least one branch');
      return;
    }
    setLoading(true);
    try {
      const res = await api.get('/products/repack-pricing/grid', {
        params: {
          branch_ids: [...selectedBranchIds].join(','),
          with_inventory_only: withInventoryOnly,
          missing_only: missingOnly,
        },
      });
      setRows(res.data?.rows || []);
      setEdits({});
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to load grid');
    } finally {
      setLoading(false);
    }
  }, [selectedBranchIds, withInventoryOnly, missingOnly]);

  const cellKey = (rid, bid) => `${rid}|${bid}`;
  const cellValue = (row, bData) => {
    const k = cellKey(row.repack_id, bData.branch_id);
    if (k in edits) return edits[k];
    return bData.current_retail != null ? String(bData.current_retail) : '';
  };

  const setCellValue = (rid, bid, val) => {
    setEdits(prev => ({ ...prev, [cellKey(rid, bid)]: val }));
  };

  const copyBranchToAll = (sourceBranchId) => {
    if (!selectedBranchIds.size) return;
    const targets = [...selectedBranchIds].filter(b => b !== sourceBranchId);
    if (!targets.length) {
      toast.info('Pick more than one branch first');
      return;
    }
    let copied = 0;
    const next = { ...edits };
    rows.forEach(row => {
      const src = row.branches.find(b => b.branch_id === sourceBranchId);
      if (!src) return;
      const srcVal = (cellKey(row.repack_id, sourceBranchId) in edits)
        ? edits[cellKey(row.repack_id, sourceBranchId)]
        : (src.current_retail != null ? String(src.current_retail) : '');
      if (!srcVal || parseFloat(srcVal) <= 0) return;
      targets.forEach(tgt => {
        next[cellKey(row.repack_id, tgt)] = srcVal;
        copied++;
      });
    });
    setEdits(next);
    toast.success(`Copied ${copied} cells from ${branches.find(b => b.id === sourceBranchId)?.name}`);
  };

  const dirtyUpdates = useMemo(() => {
    const list = [];
    Object.entries(edits).forEach(([k, val]) => {
      const [repack_id, branch_id] = k.split('|');
      const row = rows.find(r => r.repack_id === repack_id);
      if (!row) return;
      const cur = row.branches.find(b => b.branch_id === branch_id);
      const newVal = parseFloat(val);
      if (isNaN(newVal) || newVal <= 0) return;
      if (cur?.current_retail === newVal) return; // unchanged
      // Below capital guard
      if (cur && cur.capital > 0 && newVal < cur.capital) return;
      list.push({ repack_id, branch_id, retail: newVal });
    });
    return list;
  }, [edits, rows]);

  const belowCapitalCount = useMemo(() => {
    let n = 0;
    Object.entries(edits).forEach(([k, val]) => {
      const [repack_id, branch_id] = k.split('|');
      const row = rows.find(r => r.repack_id === repack_id);
      if (!row) return;
      const cur = row.branches.find(b => b.branch_id === branch_id);
      const newVal = parseFloat(val);
      if (isNaN(newVal) || newVal <= 0) return;
      if (cur && cur.capital > 0 && newVal < cur.capital) n++;
    });
    return n;
  }, [edits, rows]);

  const handleSaveClick = () => {
    if (!dirtyUpdates.length) { toast.info('Nothing to save'); return; }
    setPin('');
    setPinOpen(true);
  };

  const handlePinSubmit = async () => {
    if (!pin) { toast.error('PIN required'); return; }
    setSaving(true);
    try {
      const res = await api.post('/products/repack-pricing/bulk-save', {
        pin,
        updates: dirtyUpdates,
      });
      const saved = res.data?.saved || 0;
      toast.success(`Saved ${saved} retail price(s)`);
      setPinOpen(false);
      setPin('');
      await loadGrid();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  const filteredRows = useMemo(() => {
    if (!search.trim()) return rows;
    const s = search.toLowerCase();
    return rows.filter(r =>
      r.repack_name.toLowerCase().includes(s) ||
      (r.parent_name || '').toLowerCase().includes(s) ||
      (r.repack_sku || '').toLowerCase().includes(s)
    );
  }, [rows, search]);

  const selectedBranchList = useMemo(
    () => branches.filter(b => selectedBranchIds.has(b.id)),
    [branches, selectedBranchIds]
  );

  if (!isPriv) {
    return (
      <div className="p-8 text-center text-slate-500">
        Admin or Manager role required to manage repack pricing.
      </div>
    );
  }

  return (
    <div className="p-4 sm:p-6 space-y-4 max-w-[1500px] mx-auto" data-testid="repack-pricing-page">
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="ghost" size="sm" onClick={() => navigate('/products')} data-testid="back-to-products-btn">
          <ArrowLeft size={16} className="mr-1" /> Products
        </Button>
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Tag size={22} className="text-amber-500" /> Repack Pricing Manager
          </h1>
          <p className="text-sm text-slate-500">Per-branch retail prices for repacks. Capital is computed live from each branch's parent capital.</p>
        </div>
      </div>

      {/* Branch picker */}
      <Card>
        <CardContent className="p-4 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <Label className="font-semibold text-sm">Branches:</Label>
            <Button variant="outline" size="sm" onClick={selectAllBranches} data-testid="select-all-branches-btn">All</Button>
            <Button variant="outline" size="sm" onClick={clearBranches} data-testid="clear-branches-btn">Clear</Button>
          </div>
          <div className="flex flex-wrap gap-2">
            {branches.map(b => (
              <button
                key={b.id}
                onClick={() => toggleBranch(b.id)}
                data-testid={`branch-toggle-${b.id}`}
                className={`px-3 py-1.5 rounded-full text-xs border transition-all ${
                  selectedBranchIds.has(b.id)
                    ? 'bg-amber-500 text-white border-amber-500'
                    : 'bg-white text-slate-700 border-slate-300 hover:border-amber-300'
                }`}
              >
                {b.name}
              </button>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-4 pt-2 border-t">
            <div className="flex items-center gap-2">
              <Checkbox
                id="with-inv-only"
                checked={withInventoryOnly}
                onCheckedChange={setWithInventoryOnly}
                data-testid="filter-with-inventory-only"
              />
              <label htmlFor="with-inv-only" className="text-sm cursor-pointer">Only with parent inventory</label>
            </div>
            <div className="flex items-center gap-2">
              <Checkbox
                id="missing-only"
                checked={missingOnly}
                onCheckedChange={setMissingOnly}
                data-testid="filter-missing-only"
              />
              <label htmlFor="missing-only" className="text-sm cursor-pointer">Only rows missing retail</label>
            </div>
            <Button onClick={loadGrid} disabled={loading || !selectedBranchIds.size} data-testid="load-grid-btn">
              <Filter size={14} className="mr-1.5" /> {loading ? 'Loading…' : 'Load grid'}
            </Button>
            {rows.length > 0 && (
              <Input
                placeholder="Search repacks…"
                value={search}
                onChange={e => setSearch(e.target.value)}
                className="ml-auto max-w-[280px]"
                data-testid="search-input"
              />
            )}
          </div>
        </CardContent>
      </Card>

      {/* Grid */}
      {rows.length > 0 && (
        <Card>
          <CardContent className="p-0 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 border-b">
                <tr>
                  <th className="text-left p-3 font-semibold w-[280px]">Repack</th>
                  {selectedBranchList.map(b => (
                    <th key={b.id} className="text-left p-3 font-semibold min-w-[200px]">
                      <div className="flex items-center justify-between gap-2">
                        <span>{b.name}</span>
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-6 px-2 text-[10px] gap-1"
                          onClick={() => copyBranchToAll(b.id)}
                          title="Copy this branch's prices to all other selected branches"
                          data-testid={`copy-from-${b.id}-btn`}
                        >
                          <Copy size={11} /> Copy to all
                        </Button>
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filteredRows.map(row => (
                  <tr key={row.repack_id} className="border-b hover:bg-slate-50/50" data-testid={`row-${row.repack_id}`}>
                    <td className="p-3 align-top">
                      <div className="font-medium">{row.repack_name}</div>
                      <div className="text-xs text-slate-500 mt-0.5 flex items-center gap-1">
                        <Package size={10} /> Parent: {row.parent_name}
                      </div>
                      <div className="text-[10px] text-slate-400 font-mono mt-0.5">{row.repack_sku}</div>
                    </td>
                    {selectedBranchList.map(b => {
                      const bData = row.branches.find(x => x.branch_id === b.id);
                      if (!bData) return <td key={b.id} className="p-3 align-top text-slate-300">—</td>;
                      const cur = cellValue(row, bData);
                      const numCur = parseFloat(cur);
                      const isDirty = cellKey(row.repack_id, b.id) in edits;
                      const isBelowCapital = !isNaN(numCur) && numCur > 0 && bData.capital > 0 && numCur < bData.capital;
                      const markup = (!isNaN(numCur) && numCur > 0 && bData.capital > 0)
                        ? numCur - bData.capital
                        : null;
                      const markupPct = (markup != null && bData.capital > 0)
                        ? ((markup / bData.capital) * 100).toFixed(1)
                        : null;
                      return (
                        <td key={b.id} className="p-3 align-top" data-testid={`cell-${row.repack_id}-${b.id}`}>
                          <Input
                            type="number"
                            step="0.01"
                            value={cur}
                            onChange={(e) => setCellValue(row.repack_id, b.id, e.target.value)}
                            placeholder={bData.current_retail == null ? 'No retail' : ''}
                            className={`h-8 ${isDirty ? 'border-amber-400 bg-amber-50' : ''} ${isBelowCapital ? 'border-red-500' : ''} ${bData.current_retail == null && !isDirty ? 'border-red-300' : ''}`}
                            data-testid={`retail-input-${row.repack_id}-${b.id}`}
                          />
                          <div className="text-[11px] mt-1 space-y-0.5">
                            <div className="text-slate-500">
                              Capital: <span className="font-mono font-medium">{formatPHP(bData.capital)}</span>
                              {!bData.has_parent_stock && (
                                <span className="ml-1 text-slate-400 italic">(no stock)</span>
                              )}
                            </div>
                            {markup != null && (
                              <div className={`font-mono ${isBelowCapital ? 'text-red-600 font-semibold' : 'text-emerald-600'}`}>
                                {markup >= 0 ? '+' : ''}{formatPHP(markup)} ({markupPct}%)
                              </div>
                            )}
                            {bData.current_retail == null && !isDirty && (
                              <Badge variant="destructive" className="text-[9px] py-0 px-1.5">No retail</Badge>
                            )}
                          </div>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}

      {rows.length > 0 && (
        <div className="sticky bottom-0 bg-white border-t shadow-lg p-4 flex items-center justify-between" data-testid="save-bar">
          <div className="text-sm">
            <span className="font-semibold">{dirtyUpdates.length}</span> change{dirtyUpdates.length === 1 ? '' : 's'} ready to save.
            {belowCapitalCount > 0 && (
              <span className="ml-2 text-red-600 text-xs">{belowCapitalCount} below capital — will be skipped.</span>
            )}
          </div>
          <Button onClick={handleSaveClick} disabled={!dirtyUpdates.length || saving} data-testid="save-all-btn">
            <Save size={14} className="mr-1.5" /> Save all (PIN required)
          </Button>
        </div>
      )}

      <Dialog open={pinOpen} onOpenChange={setPinOpen}>
        <DialogContent className="max-w-md" data-testid="pin-dialog">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Lock size={18} className="text-amber-500" /> Owner PIN required
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <p className="text-sm text-slate-600">
              You're saving <strong>{dirtyUpdates.length}</strong> retail price{dirtyUpdates.length === 1 ? '' : 's'}.
              Enter Owner PIN (or 6-digit TOTP) to confirm.
            </p>
            <Input
              type="password"
              value={pin}
              onChange={(e) => setPin(e.target.value)}
              placeholder="Enter PIN"
              autoFocus
              onKeyDown={(e) => { if (e.key === 'Enter') handlePinSubmit(); }}
              data-testid="pin-input"
            />
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" onClick={() => setPinOpen(false)} disabled={saving} data-testid="pin-cancel-btn">
                Cancel
              </Button>
              <Button onClick={handlePinSubmit} disabled={saving || !pin} data-testid="pin-confirm-btn">
                {saving ? 'Saving…' : 'Confirm & Save'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
