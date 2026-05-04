import { useState, useEffect, useCallback } from 'react';
import { useAuth, api } from '../contexts/AuthContext';
import { formatPHP } from '../lib/utils';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Badge } from '../components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from '../components/ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs';
import { ScrollArea } from '../components/ui/scroll-area';
import { 
  ClipboardList, Plus, Camera, CheckCircle, XCircle, Printer,
  AlertTriangle, ArrowRight, FileText, Calendar, User, ChevronLeft
} from 'lucide-react';
import { toast } from 'sonner';
import CalcInput from '../components/CalcInput';

const STATUS_STYLES = {
  draft: { bg: 'bg-slate-100', text: 'text-slate-700', label: 'Draft' },
  in_progress: { bg: 'bg-amber-100', text: 'text-amber-700', label: 'In Progress' },
  completed: { bg: 'bg-emerald-100', text: 'text-emerald-700', label: 'Completed' },
  cancelled: { bg: 'bg-red-100', text: 'text-red-700', label: 'Cancelled' },
};

export default function CountSheetsPage() {
  const { currentBranch, hasPerm } = useAuth();
  const [sheets, setSheets] = useState([]);
  const [selectedSheet, setSelectedSheet] = useState(null);
  const [view, setView] = useState('list'); // list | detail
  const [loading, setLoading] = useState(false);
  
  // Create dialog
  const [showCreate, setShowCreate] = useState(false);
  const [capitalSources, setCapitalSources] = useState([]);
  const [categories, setCategories] = useState([]);
  const [newSheet, setNewSheet] = useState({ capital_price_source: 'manual', filter_category: null, audit_mode: false });
  
  // Cancel dialog
  const [showCancel, setShowCancel] = useState(false);
  const [cancelReason, setCancelReason] = useState('');
  
  // Adjust dialog
  const [showAdjust, setShowAdjust] = useState(false);
  const [adjustNotes, setAdjustNotes] = useState('');
  
  // Filter for detail view
  const [detailFilter, setDetailFilter] = useState('all'); // all | uncounted | variance

  const fetchSheets = useCallback(async () => {
    try {
      const params = {};
      if (currentBranch) params.branch_id = currentBranch.id;
      const res = await api.get('/count-sheets', { params });
      setSheets(res.data.count_sheets || []);
    } catch (err) {
      toast.error('Failed to load count sheets');
    }
  }, [currentBranch]);

  const fetchSources = useCallback(async () => {
    try {
      const [srcRes, catRes] = await Promise.all([
        api.get('/count-sheets/capital-sources'),
        api.get('/count-sheets/categories/list')
      ]);
      setCapitalSources(srcRes.data);
      setCategories(catRes.data);
    } catch (err) {
      console.error('Failed to load sources/categories');
    }
  }, []);

  useEffect(() => { fetchSheets(); fetchSources(); }, [fetchSheets, fetchSources]);

  const loadSheet = async (id) => {
    setLoading(true);
    try {
      const res = await api.get(`/count-sheets/${id}`);
      setSelectedSheet(res.data);
      setView('detail');
    } catch (err) {
      toast.error('Failed to load count sheet');
    }
    setLoading(false);
  };

  const handleCreate = async () => {
    if (!currentBranch) {
      toast.error('Please select a branch');
      return;
    }
    setLoading(true);
    try {
      const payload = {
        branch_id: currentBranch.id,
        capital_price_source: newSheet.capital_price_source,
        filter_category: newSheet.filter_category === 'all' ? null : newSheet.filter_category,
        audit_mode: newSheet.audit_mode,
      };
      const res = await api.post('/count-sheets', payload);
      toast.success(`Created ${res.data.count_sheet_number}`);
      setShowCreate(false);
      setNewSheet({ capital_price_source: 'manual', filter_category: null, audit_mode: false });
      await loadSheet(res.data.id);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Failed to create count sheet');
    }
    setLoading(false);
  };

  const handleSnapshot = async () => {
    if (!selectedSheet) return;
    setLoading(true);
    try {
      const res = await api.post(`/count-sheets/${selectedSheet.id}/snapshot`);
      setSelectedSheet(res.data);
      toast.success(`Snapshot taken - ${res.data.items.length} products loaded`);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Failed to take snapshot');
    }
    setLoading(false);
  };

  // Update local state immediately for real-time display
  const updateLocalState = (productId, updateData) => {
    setSelectedSheet(prev => {
      if (!prev) return prev;
      return {
        ...prev,
        items: prev.items.map(item => {
          if (item.product_id !== productId) return item;

          let actualQty, actualWhole, actualLoose;

          if (updateData.actual_whole !== undefined || updateData.actual_loose !== undefined) {
            const whole = parseFloat(updateData.actual_whole ?? item.actual_whole ?? 0) || 0;
            const loose = parseFloat(updateData.actual_loose ?? item.actual_loose ?? 0) || 0;
            const upp = item.units_per_parent || 1;
            const carryOver = Math.floor(loose / upp);
            const normalizedLoose = loose % upp;
            const normalizedWhole = whole + carryOver;
            actualQty = normalizedWhole + (normalizedLoose / upp);
            actualWhole = Math.floor(normalizedWhole);
            actualLoose = Math.round(normalizedLoose);
          } else if (updateData.actual_quantity !== undefined) {
            actualQty = parseFloat(updateData.actual_quantity) || 0;
            const upp = item.units_per_parent || 1;
            actualWhole = Math.floor(actualQty);
            actualLoose = Math.round((actualQty - actualWhole) * upp);
          } else {
            return item;
          }

          const variance = actualQty - item.system_quantity;
          return {
            ...item,
            actual_quantity: Math.round(actualQty * 10000) / 10000,
            actual_whole: actualWhole,
            actual_loose: actualLoose,
            counted: true,
            variance: Math.round(variance * 10000) / 10000,
            loss_capital: Math.round(variance * item.capital_price * 100) / 100,
            loss_retail: Math.round(variance * item.retail_price * 100) / 100,
          };
        })
      };
    });
  };

  // Save to API — called on blur (tab / click outside)
  const saveToServer = async (productId, updateData) => {
    if (!selectedSheet) return;
    try {
      await api.put(`/count-sheets/${selectedSheet.id}/items`, {
        items: [{ product_id: productId, ...updateData }]
      });
    } catch (err) {
      toast.error('Failed to save count — please re-enter the value');
    }
  };

  // onChange: update display only (no API call)
  // onBlur: save to server
  const handleUpdateCount = (productId, updateData) => {
    updateLocalState(productId, updateData);
  };

  const handleComplete = async () => {
    if (!selectedSheet) return;
    setLoading(true);
    try {
      const res = await api.post(`/count-sheets/${selectedSheet.id}/complete`);
      setSelectedSheet(res.data);
      toast.success('Count sheet completed');
      fetchSheets();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Failed to complete');
    }
    setLoading(false);
  };

  const handleCancel = async () => {
    if (!selectedSheet) return;
    setLoading(true);
    try {
      await api.post(`/count-sheets/${selectedSheet.id}/cancel`, { reason: cancelReason });
      toast.success('Count sheet cancelled');
      setShowCancel(false);
      setCancelReason('');
      setView('list');
      setSelectedSheet(null);
      fetchSheets();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Failed to cancel');
    }
    setLoading(false);
  };

  const handleAdjust = async () => {
    if (!selectedSheet) return;
    setLoading(true);
    try {
      const res = await api.post(`/count-sheets/${selectedSheet.id}/adjust`, { notes: adjustNotes });
      toast.success(`Adjustments applied - ${res.data.adjustments_made} items updated`);
      setShowAdjust(false);
      setAdjustNotes('');
      await loadSheet(selectedSheet.id);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Failed to apply adjustments');
    }
    setLoading(false);
  };

  const handlePrint = () => {
    window.print();
  };

  // Get filtered items for detail view
  const getFilteredItems = () => {
    if (!selectedSheet?.items) return [];
    let items = selectedSheet.items;
    
    if (detailFilter === 'uncounted') {
      items = items.filter(i => !i.counted);
    } else if (detailFilter === 'variance') {
      items = items.filter(i => i.variance && i.variance !== 0);
    }
    
    return items;
  };

  // Group items by category
  const getItemsByCategory = () => {
    const items = getFilteredItems();
    const grouped = {};
    items.forEach(item => {
      const cat = item.category || 'General';
      if (!grouped[cat]) grouped[cat] = [];
      grouped[cat].push(item);
    });
    return Object.entries(grouped).sort((a, b) => a[0].localeCompare(b[0]));
  };

  const countedCount = selectedSheet?.items?.filter(i => i.counted).length || 0;
  const totalCount = selectedSheet?.items?.length || 0;
  const varianceCount = selectedSheet?.items?.filter(i => i.variance && i.variance !== 0).length || 0;

  // List View
  if (view === 'list') {
    return (
      <div className="space-y-6 animate-fadeIn" data-testid="count-sheets-page">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold tracking-tight" style={{ fontFamily: 'Manrope' }}>Count Sheets</h1>
            <p className="text-sm text-slate-500 mt-1">Inventory verification and audit for {currentBranch?.name || 'all branches'}</p>
          </div>
          {hasPerm('count_sheets', 'create') && (
            <Button onClick={() => setShowCreate(true)} data-testid="create-count-sheet-btn">
              <Plus size={16} className="mr-2" /> New Count Sheet
            </Button>
          )}
        </div>

        <Card className="border-slate-200">
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow className="bg-slate-50">
                  <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">Sheet #</TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">Branch</TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">Status</TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">Started</TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">Items</TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">Variance</TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">Created By</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sheets.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={7} className="text-center py-12 text-slate-400">
                      <ClipboardList size={40} className="mx-auto mb-3 opacity-50" />
                      <p>No count sheets yet</p>
                      <p className="text-sm mt-1">Create a new count sheet to start inventory verification</p>
                    </TableCell>
                  </TableRow>
                ) : sheets.map(sheet => {
                  const style = STATUS_STYLES[sheet.status] || STATUS_STYLES.draft;
                  return (
                    <TableRow 
                      key={sheet.id} 
                      className="cursor-pointer hover:bg-slate-50 transition-colors"
                      onClick={() => loadSheet(sheet.id)}
                      data-testid={`sheet-row-${sheet.id}`}
                    >
                      <TableCell className="font-mono font-medium">{sheet.count_sheet_number}</TableCell>
                      <TableCell>{sheet.branch_name}</TableCell>
                      <TableCell>
                        <Badge className={`${style.bg} ${style.text} border-0`}>{style.label}</Badge>
                      </TableCell>
                      <TableCell className="text-sm text-slate-500">
                        {sheet.started_at ? new Date(sheet.started_at).toLocaleDateString() : '--'}
                      </TableCell>
                      <TableCell>{sheet.summary?.total_items || '--'}</TableCell>
                      <TableCell>
                        {sheet.summary?.items_with_variance !== undefined ? (
                          <span className={sheet.summary.items_with_variance > 0 ? 'text-amber-600 font-medium' : ''}>
                            {sheet.summary.items_with_variance}
                          </span>
                        ) : '--'}
                      </TableCell>
                      <TableCell className="text-sm text-slate-500">{sheet.created_by_name}</TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        {/* Create Dialog */}
        <Dialog open={showCreate} onOpenChange={setShowCreate}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>New Count Sheet</DialogTitle>
              <DialogDescription>
                Create a new inventory count sheet for {currentBranch?.name}
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <div>
                <label className="text-sm font-medium mb-2 block">Capital Price Source</label>
                <Select value={newSheet.capital_price_source} onValueChange={v => setNewSheet(p => ({ ...p, capital_price_source: v }))}>
                  <SelectTrigger data-testid="capital-source-select">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {capitalSources.map(src => (
                      <SelectItem key={src.key} value={src.key}>
                        <div>
                          <div className="font-medium">{src.label}</div>
                          <div className="text-xs text-slate-500">{src.description}</div>
                        </div>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <label className="text-sm font-medium mb-2 block">Filter by Category (optional)</label>
                <Select value={newSheet.filter_category || 'all'} onValueChange={v => setNewSheet(p => ({ ...p, filter_category: v }))}>
                  <SelectTrigger data-testid="category-filter-select">
                    <SelectValue placeholder="All Categories" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Categories</SelectItem>
                    {categories.map(cat => (
                      <SelectItem key={cat} value={cat}>{cat}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              {/* Audit Mode Toggle */}
              <div className="col-span-2 p-3 rounded-lg bg-amber-50 border border-amber-200">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-semibold text-amber-900">Audit Mode</p>
                    <p className="text-xs text-amber-700 mt-0.5">
                      System quantities are hidden during counting — ensures an honest, unbiased physical count.
                      Quantities are revealed after completion for comparison.
                    </p>
                  </div>
                  <button
                    onClick={() => setNewSheet(p => ({ ...p, audit_mode: !p.audit_mode }))}
                    className={`relative inline-flex h-6 w-11 rounded-full transition-colors shrink-0 ml-4 ${newSheet.audit_mode ? 'bg-amber-600' : 'bg-slate-300'}`}>
                    <span className={`inline-block h-5 w-5 rounded-full bg-white shadow transform transition-transform mt-0.5 ${newSheet.audit_mode ? 'translate-x-5.5' : 'translate-x-0.5'}`} />
                  </button>
                </div>
                {newSheet.audit_mode && (
                  <p className="text-[10px] text-amber-700 mt-1.5 font-medium">
                    ✓ Auditors will count products WITHOUT seeing system quantities. Use for monthly audits.
                  </p>
                )}
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setShowCreate(false)}>Cancel</Button>
              <Button onClick={handleCreate} disabled={loading} data-testid="confirm-create-btn">
                Create Count Sheet
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    );
  }

  // Detail View
  return (
    <div className="space-y-6 animate-fadeIn print:space-y-2" data-testid="count-sheet-detail">
      {/* Header */}
      <div className="flex items-start justify-between print:hidden">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="sm" onClick={() => { setView('list'); setSelectedSheet(null); fetchSheets(); }}>
            <ChevronLeft size={16} className="mr-1" /> Back
          </Button>
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold tracking-tight" style={{ fontFamily: 'Manrope' }}>
                {selectedSheet?.count_sheet_number}
              </h1>
              <Badge className={`${STATUS_STYLES[selectedSheet?.status]?.bg} ${STATUS_STYLES[selectedSheet?.status]?.text} border-0`}>
                {STATUS_STYLES[selectedSheet?.status]?.label}
              </Badge>
            </div>
            <p className="text-sm text-slate-500 mt-1">
              {selectedSheet?.branch_name} &middot; Created {selectedSheet?.created_at ? new Date(selectedSheet.created_at).toLocaleString() : ''}
            </p>
          </div>
        </div>
        
        {/* Action Buttons */}
        <div className="flex items-center gap-2">
          {selectedSheet?.status === 'draft' && hasPerm('count_sheets', 'create') && (
            <Button onClick={handleSnapshot} disabled={loading} data-testid="snapshot-btn">
              <Camera size={16} className="mr-2" /> Take Snapshot
            </Button>
          )}
          
          {selectedSheet?.status === 'in_progress' && (
            <>
              <Button variant="outline" onClick={handlePrint}>
                <Printer size={16} className="mr-2" /> Print
              </Button>
              {hasPerm('count_sheets', 'cancel') && (
                <Button variant="outline" className="text-red-600 hover:text-red-700" onClick={() => setShowCancel(true)}>
                  <XCircle size={16} className="mr-2" /> Cancel
                </Button>
              )}
              {hasPerm('count_sheets', 'complete') && countedCount === totalCount && (
                <Button onClick={handleComplete} disabled={loading} className="bg-emerald-600 hover:bg-emerald-700" data-testid="complete-btn">
                  <CheckCircle size={16} className="mr-2" /> Complete Count
                </Button>
              )}
            </>
          )}
          
          {selectedSheet?.status === 'completed' && !selectedSheet?.adjustment_applied && hasPerm('count_sheets', 'adjust') && (
            <Button onClick={() => setShowAdjust(true)} className="bg-amber-600 hover:bg-amber-700" data-testid="adjust-btn">
              <ArrowRight size={16} className="mr-2" /> Adjust to Actual
            </Button>
          )}
        </div>
      </div>

      {/* Print Header */}
      <div className="hidden print:block border-b-2 border-black pb-4 mb-4">
        <div className="flex justify-between">
          <div>
            <h1 className="text-xl font-bold">INVENTORY COUNT SHEET</h1>
            <p className="text-sm">{selectedSheet?.branch_name}</p>
          </div>
          <div className="text-right">
            <p className="font-mono font-bold">{selectedSheet?.count_sheet_number}</p>
            <p className="text-sm">Date: {selectedSheet?.started_at ? new Date(selectedSheet.started_at).toLocaleDateString() : ''}</p>
          </div>
        </div>
      </div>

      {/* Stats Cards */}
      {selectedSheet?.status !== 'draft' && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 print:hidden">
          <Card>
            <CardContent className="pt-4">
              <div className="text-sm text-slate-500">Total Items</div>
              <div className="text-2xl font-bold">{totalCount}</div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-4">
              <div className="text-sm text-slate-500">Counted</div>
              <div className="text-2xl font-bold">
                {countedCount} <span className="text-sm text-slate-400">/ {totalCount}</span>
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-4">
              <div className="text-sm text-slate-500">With Variance</div>
              <div className="text-2xl font-bold text-amber-600">{varianceCount}</div>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-4">
              <div className="text-sm text-slate-500">Net Variance (Capital)</div>
              <div className={`text-2xl font-bold ${(selectedSheet?.summary?.net_variance_capital || 0) < 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                {formatPHP(selectedSheet?.summary?.net_variance_capital || 0)}
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Filter Tabs */}
      {selectedSheet?.status !== 'draft' && (
        <div className="flex items-center gap-4 print:hidden">
          <Tabs value={detailFilter} onValueChange={setDetailFilter}>
            <TabsList>
              <TabsTrigger value="all">All ({totalCount})</TabsTrigger>
              <TabsTrigger value="uncounted">Uncounted ({totalCount - countedCount})</TabsTrigger>
              <TabsTrigger value="variance">With Variance ({varianceCount})</TabsTrigger>
            </TabsList>
          </Tabs>
        </div>
      )}

      {/* Items Table - Grouped by Category */}
      {selectedSheet?.status === 'draft' ? (
        <Card>
          <CardContent className="py-12 text-center">
            <Camera size={48} className="mx-auto mb-4 text-slate-300" />
            <h3 className="text-lg font-medium mb-2">Ready to Start Counting</h3>
            <p className="text-slate-500 mb-4">Click "Take Snapshot" to capture current inventory levels and begin counting</p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-6">
          {getItemsByCategory().map(([category, items]) => (
            <Card key={category} className="print:break-inside-avoid">
              <CardHeader className="py-3 bg-slate-50">
                <CardTitle className="text-sm font-semibold uppercase tracking-wider text-slate-600">
                  {category} ({items.length})
                </CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="text-xs">Product</TableHead>
                      <TableHead className="text-xs w-20">Unit</TableHead>
                      <TableHead className="text-xs w-32 text-right">System</TableHead>
                      <TableHead className="text-xs text-right">Actual Count</TableHead>
                      <TableHead className="text-xs w-24 text-right">Diff</TableHead>
                      <TableHead className="text-xs w-28 text-right print:hidden">Capital</TableHead>
                      <TableHead className="text-xs w-28 text-right print:hidden">Retail</TableHead>
                      <TableHead className="text-xs w-32 text-right print:hidden">Loss/Gain</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {items.map(item => {
                      const hasRepack = item.has_repack && item.units_per_parent > 1;
                      const repackUnit = item.repack_unit || 'pcs';
                      
                      // Format system quantity display
                      const formatSplitQty = (whole, loose, unit, repackUnit) => {
                        if (loose > 0) {
                          return <span>{whole} <span className="text-slate-400">{unit}</span> + {loose} <span className="text-slate-400 text-xs">{repackUnit}</span></span>;
                        }
                        return <span>{whole} <span className="text-slate-400">{unit}</span></span>;
                      };
                      
                      return (
                        <TableRow key={item.product_id} className={!item.counted ? 'bg-amber-50/50' : ''}>
                          <TableCell>
                            <div className="font-medium">{item.product_name}</div>
                            <div className="text-xs text-slate-400 font-mono">{item.sku}</div>
                            {hasRepack && (
                              <div className="text-xs text-blue-500">1 {item.unit} = {item.units_per_parent} {repackUnit}</div>
                            )}
                          </TableCell>
                          <TableCell className="text-sm text-slate-500">{item.unit}</TableCell>
                          <TableCell className="text-right font-mono">
                            {hasRepack ? (
                              formatSplitQty(item.system_whole, item.system_loose, item.unit, repackUnit)
                            ) : (
                              <span className={item.has_negative_stock ? 'text-red-600 font-bold' : ''}>
                                {item.system_quantity}
                              </span>
                            )}
                            {item.has_negative_stock && (
                              <div className="text-[10px] text-red-600 font-normal mt-0.5 flex items-center gap-0.5">
                                <span>⚠</span> Negative — check open ticket
                              </div>
                            )}
                            {!item.has_negative_stock && item.system_reserved_qty > 0 && (
                              <div className="text-[10px] text-amber-600 font-normal mt-0.5">
                                {item.system_available_qty} avail + {item.system_reserved_qty} reserved
                              </div>
                            )}
                          </TableCell>
                          <TableCell className="text-right">
                            {selectedSheet?.status === 'in_progress' ? (
                              hasRepack ? (
                                // Split input: whole + loose — save only when leaving the pair
                                <div className="flex items-center gap-1.5 justify-end flex-nowrap">
                                  <div className="flex flex-col items-center gap-0.5">
                                    <span className="text-[10px] text-slate-400 leading-none">{item.unit}</span>
                                    <CalcInput className="w-16 h-8 text-center font-mono text-sm"
 placeholder={String(item.system_whole)}
 value={item.actual_whole ?? ''}
 onChange={(v) => {
 const whole = v === '' ? 0 : parseInt(v) || 0;
 const loose = item.actual_loose ?? 0;
 handleUpdateCount(item.product_id, { actual_whole: whole, actual_loose: loose });
 }}
 onBlur={e => {
 // Skip save if focus is moving to the paired loose input
 if (e.relatedTarget?.getAttribute('data-testid') === `actual-loose-${item.product_id}`) return;
 const whole = parseInt(e.target.value) || 0;
 const loose = item.actual_loose ?? 0;
 saveToServer(item.product_id, { actual_whole: whole, actual_loose: loose });
 }}
 data-testid={`actual-whole-${item.product_id}`} />
                                  </div>
                                  <span className="text-slate-300 text-sm mt-3">+</span>
                                  <div className="flex flex-col items-center gap-0.5">
                                    <span className="text-[10px] text-slate-400 leading-none">{repackUnit}</span>
                                    <CalcInput className="w-14 h-8 text-center font-mono text-sm"
 placeholder="0"
 value={item.actual_loose ?? ''}
 onChange={(v) => {
 const loose = v === '' ? 0 : parseInt(v) || 0;
 const whole = item.actual_whole ?? item.system_whole ?? 0;
 handleUpdateCount(item.product_id, { actual_whole: whole, actual_loose: loose });
 }}
 onBlur={e => {
 // Skip save if focus is moving to the paired whole input
 if (e.relatedTarget?.getAttribute('data-testid') === `actual-whole-${item.product_id}`) return;
 const loose = parseInt(e.target.value) || 0;
 const whole = item.actual_whole ?? item.system_whole ?? 0;
 saveToServer(item.product_id, { actual_whole: whole, actual_loose: loose });
 }}
 data-testid={`actual-loose-${item.product_id}`} />
                                  </div>
                                  {item.counted && item.actual_quantity !== undefined && (
                                    <span className="text-[10px] text-emerald-600 font-medium whitespace-nowrap mt-3">
                                      ={item.actual_quantity?.toFixed(2)}
                                    </span>
                                  )}
                                </div>
                              ) : (
                                // Simple decimal input — display updates on change, saves on blur
                                <CalcInput className="w-24 h-8 text-right font-mono"
 value={item.actual_quantity ?? ''}
 onChange={(v) => {
 const val = v;
 if (val === '') return;
 handleUpdateCount(item.product_id, { actual_quantity: parseFloat(val) });
 }}
 onBlur={e => {
 if (e.target.value === '' && !item.counted) return;
 saveToServer(item.product_id, { actual_quantity: parseFloat(e.target.value) || 0 });
 }}
 data-testid={`actual-input-${item.product_id}`} />
                              )
                            ) : (
                              // Read-only display
                              <span className="font-mono">
                                {item.actual_quantity !== null ? (
                                  hasRepack ? formatSplitQty(item.actual_whole, item.actual_loose, item.unit, repackUnit) : item.actual_quantity
                                ) : '--'}
                              </span>
                            )}
                            {/* Print line */}
                            <span className="hidden print:inline-block w-24 border-b border-slate-300 ml-2"></span>
                          </TableCell>
                          <TableCell className={`text-right font-mono ${
                            item.variance > 0 ? 'text-emerald-600' : item.variance < 0 ? 'text-red-600' : ''
                          }`}>
                            {item.variance !== null ? (item.variance > 0 ? '+' : '') + parseFloat(item.variance).toFixed(2) : '--'}
                          </TableCell>
                          <TableCell className="text-right text-sm print:hidden">{formatPHP(item.capital_price)}</TableCell>
                          <TableCell className="text-right text-sm print:hidden">{formatPHP(item.retail_price)}</TableCell>
                          <TableCell className={`text-right font-medium print:hidden ${
                            (item.loss_capital || 0) > 0 ? 'text-emerald-600' : (item.loss_capital || 0) < 0 ? 'text-red-600' : ''
                          }`}>
                            {item.loss_capital !== null ? formatPHP(item.loss_capital) : '--'}
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Summary Card for Completed */}
      {selectedSheet?.status === 'completed' && selectedSheet?.summary && (
        <Card className="border-slate-300 print:mt-8">
          <CardHeader>
            <CardTitle className="text-base">Count Summary</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
              <div>
                <div className="text-sm text-slate-500">Items with Shortage</div>
                <div className="text-xl font-bold text-red-600">{selectedSheet.summary.items_with_shortage}</div>
                <div className="text-sm text-slate-500">{formatPHP(selectedSheet.summary.total_loss_capital)} capital</div>
              </div>
              <div>
                <div className="text-sm text-slate-500">Items with Surplus</div>
                <div className="text-xl font-bold text-emerald-600">{selectedSheet.summary.items_with_surplus}</div>
                <div className="text-sm text-slate-500">{formatPHP(selectedSheet.summary.total_gain_capital)} capital</div>
              </div>
              <div>
                <div className="text-sm text-slate-500">Net Variance (Capital)</div>
                <div className={`text-xl font-bold ${selectedSheet.summary.net_variance_capital < 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                  {formatPHP(selectedSheet.summary.net_variance_capital)}
                </div>
              </div>
              <div>
                <div className="text-sm text-slate-500">Net Variance (Retail)</div>
                <div className={`text-xl font-bold ${selectedSheet.summary.net_variance_retail < 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                  {formatPHP(selectedSheet.summary.net_variance_retail)}
                </div>
              </div>
            </div>
            
            {selectedSheet.adjustment_applied ? (
              <div className="mt-6 p-4 bg-emerald-50 border border-emerald-200 rounded-lg flex items-center gap-3">
                <CheckCircle className="text-emerald-600" size={20} />
                <div>
                  <div className="font-medium text-emerald-800">Adjustments Applied</div>
                  <div className="text-sm text-emerald-600">
                    Inventory was adjusted on {new Date(selectedSheet.adjustment_applied_at).toLocaleString()}
                  </div>
                </div>
              </div>
            ) : (
              <div className="mt-6 p-4 bg-amber-50 border border-amber-200 rounded-lg flex items-center gap-3">
                <AlertTriangle className="text-amber-600" size={20} />
                <div>
                  <div className="font-medium text-amber-800">Adjustments Not Yet Applied</div>
                  <div className="text-sm text-amber-600">
                    Click "Adjust to Actual" to update inventory based on this count
                  </div>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Print Footer */}
      <div className="hidden print:block border-t-2 border-black pt-4 mt-8">
        <div className="grid grid-cols-2 gap-8">
          <div>
            <p className="text-sm mb-8">Counted by: _______________________</p>
            <p className="text-sm">Signature: _______________________</p>
          </div>
          <div>
            <p className="text-sm mb-8">Verified by: _______________________</p>
            <p className="text-sm">Signature: _______________________</p>
          </div>
        </div>
      </div>

      {/* Cancel Dialog */}
      <Dialog open={showCancel} onOpenChange={setShowCancel}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Cancel Count Sheet</DialogTitle>
            <DialogDescription>
              This will cancel the count sheet. No inventory adjustments will be made.
            </DialogDescription>
          </DialogHeader>
          <div className="py-4">
            <label className="text-sm font-medium mb-2 block">Reason (optional)</label>
            <Input 
              value={cancelReason} 
              onChange={e => setCancelReason(e.target.value)}
              placeholder="Why is this count sheet being cancelled?"
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowCancel(false)}>Keep Counting</Button>
            <Button variant="destructive" onClick={handleCancel} disabled={loading}>
              Cancel Count Sheet
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Adjust Dialog */}
      <Dialog open={showAdjust} onOpenChange={setShowAdjust}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Apply Inventory Adjustments</DialogTitle>
            <DialogDescription>
              This will update inventory quantities to match your actual counts. This action cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <div className="py-4 space-y-4">
            <div className="p-4 bg-slate-50 rounded-lg">
              <div className="grid grid-cols-2 gap-4 text-sm">
                <div>
                  <span className="text-slate-500">Items to adjust:</span>
                  <span className="ml-2 font-medium">{varianceCount}</span>
                </div>
                <div>
                  <span className="text-slate-500">Net impact:</span>
                  <span className={`ml-2 font-medium ${(selectedSheet?.summary?.net_variance_capital || 0) < 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                    {formatPHP(selectedSheet?.summary?.net_variance_capital || 0)}
                  </span>
                </div>
              </div>
            </div>
            <div>
              <label className="text-sm font-medium mb-2 block">Notes (optional)</label>
              <Input 
                value={adjustNotes} 
                onChange={e => setAdjustNotes(e.target.value)}
                placeholder="Audit reference, reason, etc."
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowAdjust(false)}>Cancel</Button>
            <Button onClick={handleAdjust} disabled={loading} className="bg-amber-600 hover:bg-amber-700">
              Apply Adjustments
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
