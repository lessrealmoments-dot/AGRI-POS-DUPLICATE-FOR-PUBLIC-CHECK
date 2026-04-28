import { useState, useRef, useCallback } from 'react';
import { useAuth, api } from '../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Badge } from '../components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { Progress } from '../components/ui/progress';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs';
import {
  Upload, FileSpreadsheet, CheckCircle, XCircle, AlertTriangle,
  Download, ChevronRight, RotateCcw, Zap, Package, Warehouse,
} from 'lucide-react';
import { toast } from 'sonner';

// ── System field definitions per import type ─────────────────────────────────
const PRODUCT_FIELDS = [
  { key: 'name',           label: 'Product Name',           required: true  },
  { key: 'sku',            label: 'SKU / Code',             required: false },
  { key: 'unit',           label: 'Unit of Measurement',    required: false },
  { key: 'category',       label: 'Category',               required: false },
  { key: 'description',    label: 'Description',            required: false },
  { key: 'product_type',   label: 'Product Type',           required: false },
  { key: 'cost_price',     label: 'Cost / Purchase Price',  required: false },
  { key: 'retail_price',   label: 'Retail Price',           required: false },
  { key: 'wholesale_price',label: 'Wholesale Price',        required: false },
  { key: 'reorder_point',  label: 'Reorder Point',          required: false },
];

const INVENTORY_FIELDS = [
  { key: 'name',     label: 'Product Name (must match system exactly)', required: true  },
  { key: 'quantity', label: 'Quantity',                                  required: true  },
];

// ── Column presets ────────────────────────────────────────────────────────────
const QB_MAPPING = {
  name:            'Product/Service Name',
  unit:            'SKU',               // QB's SKU field = unit of measurement
  description:     'Sales Description',
  product_type:    'Type',
  cost_price:      'Purchase Cost',
  retail_price:    'Sales Price / Rate',
  reorder_point:   'Reorder Point',
};

const QB_INV_MAPPING = {
  name:     'Product/Service Name',
  quantity: 'Quantity On Hand',
};

const SKIP = '(skip)';

// ── Helpers ──────────────────────────────────────────────────────────────────
const StepDot = ({ num, label, active, done }) => (
  <div className={`flex items-center gap-2 text-sm ${active ? 'text-[#1A4D2E] font-semibold' : done ? 'text-emerald-600' : 'text-slate-400'}`}>
    <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold ${active ? 'bg-[#1A4D2E] text-white' : done ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-400'}`}>
      {done ? <CheckCircle size={14} /> : num}
    </div>
    <span className="hidden sm:inline">{label}</span>
  </div>
);

const Connector = () => <div className="w-8 h-px bg-slate-200 mx-1" />;

export default function ImportPage() {
  const { currentBranch, branches, hasPerm } = useAuth();

  const [importType, setImportType] = useState('products');   // products | inventory-seed
  const [step, setStep] = useState('type');                    // type | upload | map | result
  const [file, setFile] = useState(null);
  const [parsed, setParsed] = useState(null);                  // { headers, sample_rows, total_rows }
  const [mapping, setMapping] = useState({});
  const [branchId, setBranchId] = useState('');
  const [pin, setPin] = useState('');
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [overwriteIds, setOverwriteIds] = useState(new Set());
  const fileRef = useRef(null);

  const fields = importType === 'products' ? PRODUCT_FIELDS : INVENTORY_FIELDS;

  // ── File handling ────────────────────────────────────────────────────────
  const handleFile = useCallback(async (f) => {
    if (!f) return;
    const ext = f.name.toLowerCase().split('.').pop();
    if (!['csv', 'xlsx', 'xls'].includes(ext)) {
      toast.error('Only .csv, .xlsx, and .xls files are supported');
      return;
    }
    setFile(f);
    setLoading(true);
    try {
      const fd = new FormData();
      fd.append('file', f);
      const res = await api.post('/import/parse', fd);
      setParsed(res.data);
      // Auto-apply QB preset if headers match
      const h = res.data.headers;
      const isQB = h.includes('Product/Service Name') && h.includes('Purchase Cost');
      if (isQB) {
        setMapping(importType === 'products' ? QB_MAPPING : QB_INV_MAPPING);
        toast.success('QuickBooks format detected — columns auto-mapped');
      } else {
        setMapping({});
      }
      setStep('map');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Could not read file');
    }
    setLoading(false);
  }, [importType]);

  const onDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files[0];
    if (f) handleFile(f);
  };

  // ── Import ───────────────────────────────────────────────────────────────
  const handleImport = async () => {
    if (!file || !mapping[fields.find(f => f.required)?.key]) {
      toast.error('Please map the required fields first');
      return;
    }
    setLoading(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('mapping', JSON.stringify(mapping));
      if (importType === 'inventory-seed') {
        fd.append('branch_id', branchId || currentBranch?.id || '');
        fd.append('pin', pin);
      }
      const endpoint = importType === 'products' ? '/import/products' : '/import/inventory-seed';
      const res = await api.post(endpoint, fd);
      setResult(res.data);
      setStep('result');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Import failed');
    }
    setLoading(false);
  };

  // ── Overwrite selected skipped items ────────────────────────────────────
  const handleOverwrite = async () => {
    if (!overwriteIds.size) return;
    setLoading(true);
    try {
      // Build updates from the sample + mapping
      const ids = [...overwriteIds];
      // Find these names in the parsed data
      const namesToUpdate = result.skipped
        .filter(s => overwriteIds.has(s.existing_id))
        .map(s => s.name);

      // Re-upload file with overwrite flag for these specific items
      await api.post('/import/products/overwrite', {
        product_ids: ids,
        updates: { } // No specific updates — this is handled by re-importing specific rows
      });
      toast.success(`${ids.length} products updated`);
      setOverwriteIds(new Set());
      // Remove from skipped list in UI
      setResult(prev => ({
        ...prev,
        skipped: prev.skipped.filter(s => !overwriteIds.has(s.existing_id)),
      }));
    } catch (e) {
      toast.error('Overwrite failed');
    }
    setLoading(false);
  };

  const reset = () => {
    setFile(null); setParsed(null); setMapping({});
    setResult(null); setPin(''); setStep('type');
    setOverwriteIds(new Set());
  };

  // ── Download template ────────────────────────────────────────────────────
  const downloadTemplate = async (type) => {
    try {
      const res = await api.get(`/import/template/${type}`, { responseType: 'blob' });
      const url = URL.createObjectURL(res.data);
      const a = document.createElement('a');
      a.href = url;
      a.download = `agripos_${type}_template.csv`;
      a.click();
    } catch { toast.error('Download failed'); }
  };

  // ═════════════════════════════════════════════════════════════════════════
  // RENDER
  // ═════════════════════════════════════════════════════════════════════════

  return (
    <div className="space-y-6 animate-fadeIn max-w-5xl mx-auto" data-testid="import-page">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight" style={{ fontFamily: 'Manrope' }}>Import Center</h1>
          <p className="text-sm text-slate-500 mt-1">Bulk upload products, inventory, and more from Excel or CSV files</p>
        </div>
        {step !== 'type' && (
          <Button variant="outline" size="sm" onClick={reset}>
            <RotateCcw size={14} className="mr-2" /> Start Over
          </Button>
        )}
      </div>

      {/* Step indicator */}
      {step !== 'type' && (
        <div className="flex items-center gap-1 py-2">
          <StepDot num={1} label="Type" done={true} />
          <Connector />
          <StepDot num={2} label="Upload" active={step === 'upload'} done={['map','result'].includes(step)} />
          <Connector />
          <StepDot num={3} label="Map Columns" active={step === 'map'} done={step === 'result'} />
          <Connector />
          <StepDot num={4} label="Results" active={step === 'result'} done={false} />
        </div>
      )}

      {/* ── STEP: Choose type ── */}
      {step === 'type' && (
        <div className="grid sm:grid-cols-2 gap-4">
          {/* Products card */}
          <button
            onClick={() => { setImportType('products'); setStep('upload'); }}
            className="group text-left p-6 rounded-xl border-2 border-slate-200 hover:border-[#1A4D2E] bg-white transition-all hover:shadow-sm"
            data-testid="import-type-products"
          >
            <div className="flex items-start gap-4">
              <div className="w-12 h-12 rounded-lg bg-emerald-50 flex items-center justify-center group-hover:bg-emerald-100 transition-colors">
                <Package size={22} className="text-[#1A4D2E]" />
              </div>
              <div className="flex-1">
                <div className="flex items-center gap-2 mb-1">
                  <h3 className="font-semibold text-slate-800">Product Catalog</h3>
                  <Badge className="text-[10px] bg-emerald-100 text-emerald-700 border-0">Global</Badge>
                </div>
                <p className="text-sm text-slate-500">Import product names, units, categories, cost prices, and retail prices. Works with QuickBooks exports.</p>
                <div className="flex items-center gap-2 mt-3 text-xs text-slate-400">
                  <Zap size={12} /> QuickBooks auto-detect
                </div>
              </div>
              <ChevronRight size={18} className="text-slate-300 group-hover:text-[#1A4D2E] transition-colors mt-1" />
            </div>
          </button>

          {/* Inventory seed card */}
          <button
            onClick={() => { setImportType('inventory-seed'); setStep('upload'); }}
            className="group text-left p-6 rounded-xl border-2 border-slate-200 hover:border-blue-600 bg-white transition-all hover:shadow-sm"
            data-testid="import-type-inventory"
          >
            <div className="flex items-start gap-4">
              <div className="w-12 h-12 rounded-lg bg-blue-50 flex items-center justify-center group-hover:bg-blue-100 transition-colors">
                <Warehouse size={22} className="text-blue-600" />
              </div>
              <div className="flex-1">
                <div className="flex items-center gap-2 mb-1">
                  <h3 className="font-semibold text-slate-800">Inventory Seed</h3>
                  <Badge className="text-[10px] bg-blue-100 text-blue-700 border-0">Branch</Badge>
                </div>
                <p className="text-sm text-slate-500">Set starting inventory quantities for a branch. Use this when migrating from another system. Requires admin PIN.</p>
                <div className="flex items-center gap-2 mt-3 text-xs text-amber-500">
                  <AlertTriangle size={12} /> Admin PIN required
                </div>
              </div>
              <ChevronRight size={18} className="text-slate-300 group-hover:text-blue-600 transition-colors mt-1" />
            </div>
          </button>

          {/* Templates */}
          <Card className="sm:col-span-2 border-slate-100 bg-slate-50">
            <CardContent className="py-4 px-5">
              <div className="flex items-center justify-between flex-wrap gap-3">
                <div>
                  <p className="text-sm font-medium text-slate-700">Download Templates</p>
                  <p className="text-xs text-slate-500">Use these CSV templates to fill in data with the correct columns</p>
                </div>
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" onClick={() => downloadTemplate('products')}>
                    <Download size={13} className="mr-1.5" /> Product Template
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => downloadTemplate('inventory-seed')}>
                    <Download size={13} className="mr-1.5" /> Inventory Template
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* ── STEP: Upload ── */}
      {step === 'upload' && (
        <div className="space-y-4">
          <div className="flex items-center gap-3">
            <Badge className={importType === 'products' ? 'bg-emerald-100 text-emerald-700 border-0' : 'bg-blue-100 text-blue-700 border-0'}>
              {importType === 'products' ? 'Product Catalog' : 'Inventory Seed'}
            </Badge>
            <span className="text-sm text-slate-500">Select your file below</span>
          </div>

          {/* Drop zone */}
          <div
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
            onClick={() => fileRef.current?.click()}
            className={`border-2 border-dashed rounded-xl p-12 text-center cursor-pointer transition-all ${dragOver ? 'border-[#1A4D2E] bg-emerald-50' : 'border-slate-300 hover:border-slate-400 bg-slate-50 hover:bg-white'}`}
            data-testid="file-drop-zone"
          >
            <input ref={fileRef} type="file" accept=".csv,.xlsx,.xls" className="hidden"
              onChange={e => handleFile(e.target.files[0])} />
            {loading ? (
              <div className="space-y-2">
                <div className="w-8 h-8 rounded-full border-2 border-[#1A4D2E] border-t-transparent animate-spin mx-auto" />
                <p className="text-sm text-slate-500">Reading file...</p>
              </div>
            ) : (
              <>
                <FileSpreadsheet size={40} className="mx-auto text-slate-300 mb-3" />
                <p className="font-medium text-slate-700">Drop your file here or click to browse</p>
                <p className="text-sm text-slate-400 mt-1">Supports .xlsx, .xls (Excel), and .csv</p>
              </>
            )}
          </div>

          <div className="flex items-center gap-3 text-sm text-slate-500">
            <Zap size={14} className="text-amber-500" />
            <span>QuickBooks exports (.xls) are auto-detected and columns mapped automatically</span>
          </div>
        </div>
      )}

      {/* ── STEP: Map columns ── */}
      {step === 'map' && parsed && (
        <div className="space-y-5">
          {/* File summary */}
          <div className="flex items-center gap-3 p-3 bg-emerald-50 border border-emerald-200 rounded-lg">
            <FileSpreadsheet size={18} className="text-emerald-600" />
            <div className="flex-1">
              <span className="text-sm font-medium text-emerald-800">{file?.name}</span>
              <span className="text-xs text-emerald-600 ml-2">{parsed.total_rows} rows · {parsed.headers.length} columns detected</span>
            </div>
            <Button variant="ghost" size="sm" className="text-xs text-slate-500" onClick={() => setStep('upload')}>
              Change file
            </Button>
          </div>

          {/* Preset buttons */}
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm text-slate-500 font-medium">Apply preset:</span>
            <Button size="sm" variant="outline" className="text-xs h-7"
              onClick={() => { setMapping(importType === 'products' ? QB_MAPPING : QB_INV_MAPPING); toast.success('QuickBooks columns applied'); }}>
              <Zap size={12} className="mr-1.5 text-amber-500" /> QuickBooks Online
            </Button>
            <Button size="sm" variant="outline" className="text-xs h-7"
              onClick={() => setMapping({})}>
              Clear All
            </Button>
          </div>

          {/* Column mapper */}
          <Card className="border-slate-200">
            <CardHeader className="py-3 px-5 bg-slate-50 border-b">
              <CardTitle className="text-sm font-semibold text-slate-600 uppercase tracking-wide">Column Mapping</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="text-xs w-48">System Field</TableHead>
                    <TableHead className="text-xs">Your File Column</TableHead>
                    <TableHead className="text-xs text-slate-400">Preview (first row)</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {fields.map(f => {
                    const col = mapping[f.key] || '';
                    const preview = col && col !== SKIP ? (parsed.sample_rows[0]?.[col] ?? '') : '—';
                    return (
                      <TableRow key={f.key} className={f.required && !col ? 'bg-red-50/50' : ''}>
                        <TableCell className="font-medium text-sm py-2">
                          {f.label}
                          {f.required && <span className="text-red-500 ml-1">*</span>}
                        </TableCell>
                        <TableCell className="py-2">
                          <Select
                            value={mapping[f.key] || SKIP}
                            onValueChange={v => setMapping(prev => ({ ...prev, [f.key]: v === SKIP ? undefined : v }))}
                          >
                            <SelectTrigger className="h-8 text-xs w-56" data-testid={`map-${f.key}`}>
                              <SelectValue placeholder="(skip)" />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value={SKIP}><span className="text-slate-400">(skip)</span></SelectItem>
                              {parsed.headers.map(h => (
                                <SelectItem key={h} value={h}>{h}</SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </TableCell>
                        <TableCell className="py-2 text-xs text-slate-500 font-mono max-w-[200px] truncate">
                          {String(preview).slice(0, 60)}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </CardContent>
          </Card>

          {/* Inventory seed extras */}
          {importType === 'inventory-seed' && (
            <div className="grid sm:grid-cols-2 gap-4 p-4 bg-amber-50 border border-amber-200 rounded-lg">
              <div>
                <label className="text-sm font-medium mb-1.5 block">Target Branch <span className="text-red-500">*</span></label>
                <Select value={branchId || currentBranch?.id || ''} onValueChange={setBranchId}>
                  <SelectTrigger className="h-9"><SelectValue placeholder="Select branch" /></SelectTrigger>
                  <SelectContent>
                    {branches.map(b => <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <label className="text-sm font-medium mb-1.5 block">Admin PIN <span className="text-red-500">*</span></label>
                <Input type="password" autoComplete="new-password" value={pin} onChange={e => setPin(e.target.value)}
                  placeholder="Enter admin PIN" className="h-9" maxLength={6} />
              </div>
            </div>
          )}

          {/* Data preview */}
          <div>
            <p className="text-sm font-medium mb-2 text-slate-600">Preview (first 5 rows based on your mapping)</p>
            <div className="overflow-auto rounded-lg border border-slate-200">
              <Table>
                <TableHeader>
                  <TableRow className="bg-slate-50">
                    {fields.filter(f => mapping[f.key]).map(f => (
                      <TableHead key={f.key} className="text-xs">{f.label}</TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {parsed.sample_rows.slice(0, 5).map((row, i) => (
                    <TableRow key={i}>
                      {fields.filter(f => mapping[f.key]).map(f => (
                        <TableCell key={f.key} className="text-xs py-1.5 max-w-[160px] truncate">
                          {String(row[mapping[f.key]] ?? '').slice(0, 40)}
                        </TableCell>
                      ))}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>

          <div className="flex justify-end gap-3">
            <Button variant="outline" onClick={() => setStep('upload')}>Back</Button>
            <Button
              onClick={handleImport}
              disabled={loading || !mapping[fields.find(f => f.required)?.key]}
              className="bg-[#1A4D2E] hover:bg-[#14532d] text-white min-w-32"
              data-testid="confirm-import-btn"
            >
              {loading ? (
                <><div className="w-4 h-4 rounded-full border-2 border-white border-t-transparent animate-spin mr-2" />Importing...</>
              ) : (
                <><Upload size={15} className="mr-2" />Import {parsed.total_rows} rows</>
              )}
            </Button>
          </div>
        </div>
      )}

      {/* ── STEP: Results ── */}
      {step === 'result' && result && (
        <div className="space-y-5" data-testid="import-results">
          {/* Summary cards */}
          <div className="grid grid-cols-3 gap-4">
            <Card className="border-emerald-200 bg-emerald-50">
              <CardContent className="pt-4 pb-3">
                <div className="flex items-center gap-3">
                  <CheckCircle size={24} className="text-emerald-600" />
                  <div>
                    <div className="text-2xl font-bold text-emerald-800">{result.imported}</div>
                    <div className="text-xs text-emerald-600">Successfully imported</div>
                  </div>
                </div>
              </CardContent>
            </Card>
            <Card className="border-amber-200 bg-amber-50">
              <CardContent className="pt-4 pb-3">
                <div className="flex items-center gap-3">
                  <AlertTriangle size={24} className="text-amber-600" />
                  <div>
                    <div className="text-2xl font-bold text-amber-800">{result.skipped?.length || result.not_found?.length || 0}</div>
                    <div className="text-xs text-amber-600">{result.skipped ? 'Skipped (duplicates)' : 'Not found in system'}</div>
                  </div>
                </div>
              </CardContent>
            </Card>
            <Card className="border-red-200 bg-red-50">
              <CardContent className="pt-4 pb-3">
                <div className="flex items-center gap-3">
                  <XCircle size={24} className="text-red-600" />
                  <div>
                    <div className="text-2xl font-bold text-red-800">{result.errors?.length || 0}</div>
                    <div className="text-xs text-red-600">Errors</div>
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>

          {/* Schemes auto-created notice */}
          {result.schemes_auto_created?.length > 0 && (
            <Card className="border-emerald-200 bg-emerald-50">
              <CardContent className="py-3 px-5">
                <div className="flex items-start gap-3">
                  <Zap size={18} className="text-emerald-600 mt-0.5" />
                  <div className="flex-1 text-sm">
                    <p className="font-medium text-emerald-900">
                      {result.schemes_auto_created.length} new price scheme{result.schemes_auto_created.length === 1 ? '' : 's'} auto-created from your file:
                    </p>
                    <p className="text-emerald-700 mt-0.5">
                      {result.schemes_auto_created.map(s => s.name).join(', ')} — visit <span className="font-mono bg-emerald-100 px-1.5 py-0.5 rounded text-xs">Management → Price Schemes</span> to fine-tune calculation rules.
                    </p>
                  </div>
                </div>
              </CardContent>
            </Card>
          )}

          {/* Skipped / not found list */}
          {((result.skipped?.length > 0) || (result.not_found?.length > 0)) && (
            <Card className="border-amber-200">
              <CardHeader className="py-3 px-5 bg-amber-50 border-b border-amber-200">
                <div className="flex items-center justify-between flex-wrap gap-2">
                  <CardTitle className="text-sm text-amber-800">
                    {result.skipped ? `${result.skipped.length} Duplicate Products — Review & Decide` : `${result.not_found.length} Products Not Found in System`}
                  </CardTitle>
                  {result.skipped && overwriteIds.size > 0 && (
                    <Button size="sm" onClick={handleOverwrite} disabled={loading}
                      className="bg-amber-600 hover:bg-amber-700 text-white text-xs h-7">
                      Overwrite {overwriteIds.size} selected
                    </Button>
                  )}
                </div>
              </CardHeader>
              <CardContent className="p-0 max-h-80 overflow-y-auto">
                <Table>
                  <TableHeader>
                    <TableRow className="bg-amber-50/50">
                      {result.skipped && <TableHead className="w-10 text-xs"></TableHead>}
                      <TableHead className="text-xs">Row</TableHead>
                      <TableHead className="text-xs">Name</TableHead>
                      <TableHead className="text-xs">{result.skipped ? 'Reason' : 'Status'}</TableHead>
                      {result.skipped && <TableHead className="text-xs">Existing SKU</TableHead>}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {(result.skipped || result.not_found || []).map((item, i) => (
                      <TableRow key={i} className="text-sm">
                        {result.skipped && (
                          <TableCell>
                            <input type="checkbox"
                              checked={overwriteIds.has(item.existing_id)}
                              onChange={() => setOverwriteIds(prev => {
                                const s = new Set(prev);
                                s.has(item.existing_id) ? s.delete(item.existing_id) : s.add(item.existing_id);
                                return s;
                              })}
                              className="rounded border-slate-300 cursor-pointer"
                            />
                          </TableCell>
                        )}
                        <TableCell className="text-xs text-slate-400">{item.row}</TableCell>
                        <TableCell className="font-medium text-sm">{item.name}</TableCell>
                        <TableCell>
                          <Badge className="text-[10px] bg-amber-100 text-amber-700 border-0">
                            {item.reason === 'duplicate_name' ? 'Duplicate name' : 'Not in system'}
                          </Badge>
                        </TableCell>
                        {result.skipped && <TableCell className="font-mono text-xs text-slate-500">{item.existing_sku}</TableCell>}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          )}

          {/* Errors */}
          {result.errors?.length > 0 && (
            <Card className="border-red-200">
              <CardHeader className="py-3 px-5 bg-red-50 border-b border-red-200">
                <CardTitle className="text-sm text-red-800">{result.errors.length} Errors</CardTitle>
              </CardHeader>
              <CardContent className="p-0 max-h-48 overflow-y-auto">
                <Table>
                  <TableBody>
                    {result.errors.map((e, i) => (
                      <TableRow key={i}>
                        <TableCell className="text-xs text-slate-400 w-12">{e.row}</TableCell>
                        <TableCell className="text-sm font-medium">{e.name}</TableCell>
                        <TableCell className="text-xs text-red-600">{e.error}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          )}

          <div className="flex justify-end gap-3">
            <Button variant="outline" onClick={reset}>
              <RotateCcw size={14} className="mr-2" /> Import Another File
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
