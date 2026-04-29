import { useState, useRef, useCallback } from 'react';
import { useAuth, api } from '../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Badge } from '../components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs';
import {
  Upload, FileSpreadsheet, CheckCircle, XCircle, AlertTriangle,
  Download, ChevronRight, RotateCcw, Zap, Package, Warehouse, Store, Users,
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

const BRANCH_STOCK_PRICE_FIELDS = [
  { key: 'name',           label: 'Product Name (must match catalog)', required: true  },
  { key: 'cost_price',     label: 'Cost / Capital Price',              required: false },
  { key: 'retail_price',   label: 'Retail Price',                      required: false },
  { key: 'wholesale_price',label: 'Wholesale Price',                   required: false },
  { key: 'quantity',       label: 'Quantity (empty = skip, type 0 to zero out)', required: false },
];

const CUSTOMER_FIELDS = [
  { key: 'name',            label: 'Customer Name',          required: true  },
  { key: 'phone',           label: 'Phone',                  required: false },
  { key: 'phone2',          label: 'Phone 2',                required: false },
  { key: 'email',           label: 'Email',                  required: false },
  { key: 'address',         label: 'Address',                required: false },
  { key: 'price_scheme',    label: 'Price Scheme (retail/wholesale)', required: false },
  { key: 'credit_limit',    label: 'Credit Limit',           required: false },
  { key: 'interest_rate',   label: 'Interest Rate (%)',      required: false },
  { key: 'grace_period',    label: 'Grace Period (days)',    required: false },
  { key: 'opening_balance', label: 'Opening Balance',        required: false },
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

  // importType: products | products-update | inventory-seed | branch-stock-price | customers
  const [importType, setImportType] = useState('products');
  const [step, setStep] = useState('type');                    // type | upload | map | preview | result
  const [file, setFile] = useState(null);
  const [parsed, setParsed] = useState(null);                  // { headers, sample_rows, total_rows }
  const [mapping, setMapping] = useState({});
  const [branchId, setBranchId] = useState('');
  const [pin, setPin] = useState('');
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [overwriteIds, setOverwriteIds] = useState(new Set());
  const [previewData, setPreviewData] = useState(null);        // for branch & customer preview
  const [decisions, setDecisions] = useState({});              // {row: {action, target_id}} for fuzzy customer matches
  const [openingBalanceDate, setOpeningBalanceDate] = useState(new Date().toISOString().slice(0, 10));
  const fileRef = useRef(null);

  const fields =
    importType === 'inventory-seed'      ? INVENTORY_FIELDS
    : importType === 'branch-stock-price'? BRANCH_STOCK_PRICE_FIELDS
    : importType === 'customers'         ? CUSTOMER_FIELDS
    : PRODUCT_FIELDS;
  const branchScoped = ['inventory-seed', 'branch-stock-price', 'customers'].includes(importType);
  const needsPin = ['inventory-seed', 'branch-stock-price'].includes(importType);
  const hasPreviewStep = ['branch-stock-price', 'customers'].includes(importType);

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
        setMapping(importType === 'inventory-seed' ? QB_INV_MAPPING : QB_MAPPING);
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

  // ── Import / Preview ─────────────────────────────────────────────────────
  const handleImport = async () => {
    if (!file || !mapping[fields.find(f => f.required)?.key]) {
      toast.error('Please map the required fields first');
      return;
    }
    if (branchScoped && !(branchId || currentBranch?.id)) {
      toast.error('Please select a target branch');
      return;
    }
    if (needsPin && !pin) {
      toast.error('Admin PIN is required');
      return;
    }
    setLoading(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('mapping', JSON.stringify(mapping));
      const useBranch = branchId || currentBranch?.id || '';

      if (importType === 'branch-stock-price') {
        fd.append('branch_id', useBranch);
        fd.append('pin', pin);
        fd.append('mode', 'preview');
        const res = await api.post('/import/branch-stock-and-price', fd);
        setPreviewData(res.data);
        setStep('preview');
      } else if (importType === 'customers') {
        fd.append('branch_id', useBranch);
        const res = await api.post('/import/customers/preview', fd);
        setPreviewData(res.data);
        // Init default decision for each fuzzy row = "skip" until user picks
        const init = {};
        (res.data.fuzzy || []).forEach(f => { init[f.row] = { action: 'skip' }; });
        setDecisions(init);
        setStep('preview');
      } else {
        if (importType === 'inventory-seed') {
          fd.append('branch_id', useBranch);
          fd.append('pin', pin);
        }
        const endpoint = importType === 'inventory-seed'
          ? '/import/inventory-seed'
          : importType === 'products-update'
            ? '/import/products/update-existing'
            : '/import/products';
        const res = await api.post(endpoint, fd);
        setResult(res.data);
        setStep('result');
      }
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Import failed');
    }
    setLoading(false);
  };

  // ── Commit after preview (branch-stock-price + customers) ────────────────
  const commitAfterPreview = async () => {
    if (!file) return;
    setLoading(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('mapping', JSON.stringify(mapping));
      const useBranch = branchId || currentBranch?.id || '';
      fd.append('branch_id', useBranch);

      if (importType === 'branch-stock-price') {
        fd.append('pin', pin);
        fd.append('mode', 'commit');
        const res = await api.post('/import/branch-stock-and-price', fd);
        setResult(res.data);
      } else {
        // customers commit
        const decisionList = Object.entries(decisions).map(([row, d]) => ({
          row: parseInt(row, 10), ...d,
        }));
        fd.append('decisions', JSON.stringify(decisionList));
        fd.append('opening_balance_date', openingBalanceDate);
        const res = await api.post('/import/customers/commit', fd);
        setResult(res.data);
      }
      setStep('result');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Commit failed');
    }
    setLoading(false);
  };

  // ── Overwrite selected skipped items: re-uploads the file and merges mapped fields ──
  const handleOverwrite = async () => {
    if (!overwriteIds.size) return;
    if (!file) {
      toast.error('Original file required to overwrite. Please re-import.');
      return;
    }
    setLoading(true);
    try {
      const ids = [...overwriteIds];
      const fd = new FormData();
      fd.append('file', file);
      fd.append('mapping', JSON.stringify(mapping));
      fd.append('product_ids', JSON.stringify(ids));
      const res = await api.post('/import/products/overwrite', fd);
      const { updated = 0, not_matched = 0, schemes_auto_created = [] } = res.data || {};
      let msg = `${updated} product${updated === 1 ? '' : 's'} updated`;
      if (schemes_auto_created.length) msg += ` · ${schemes_auto_created.length} scheme(s) auto-created`;
      if (not_matched) msg += ` · ${not_matched} unmatched row(s)`;
      toast.success(msg);
      setOverwriteIds(new Set());
      // Remove the overwritten items from the skipped list
      setResult(prev => ({
        ...prev,
        skipped: (prev.skipped || []).filter(s => !overwriteIds.has(s.existing_id)),
      }));
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Overwrite failed');
    }
    setLoading(false);
  };

  const reset = () => {
    setFile(null); setParsed(null); setMapping({});
    setResult(null); setPin(''); setStep('type');
    setOverwriteIds(new Set());
    setPreviewData(null); setDecisions({});
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
          <StepDot num={2} label="Upload" active={step === 'upload'} done={['map','preview','result'].includes(step)} />
          <Connector />
          <StepDot num={3} label="Map Columns" active={step === 'map'} done={['preview','result'].includes(step)} />
          {hasPreviewStep && (
            <>
              <Connector />
              <StepDot num={4} label="Review" active={step === 'preview'} done={step === 'result'} />
            </>
          )}
          <Connector />
          <StepDot num={hasPreviewStep ? 5 : 4} label="Results" active={step === 'result'} done={false} />
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
                  <h3 className="font-semibold text-slate-800">New Product Catalog</h3>
                  <Badge className="text-[10px] bg-emerald-100 text-emerald-700 border-0">Create</Badge>
                </div>
                <p className="text-sm text-slate-500">Import a fresh catalog. New products are created. Existing names are flagged as duplicates so you can review before overwriting.</p>
                <div className="flex items-center gap-2 mt-3 text-xs text-slate-400">
                  <Zap size={12} /> QuickBooks auto-detect
                </div>
              </div>
              <ChevronRight size={18} className="text-slate-300 group-hover:text-[#1A4D2E] transition-colors mt-1" />
            </div>
          </button>

          {/* Update Existing card */}
          <button
            onClick={() => { setImportType('products-update'); setStep('upload'); }}
            className="group text-left p-6 rounded-xl border-2 border-slate-200 hover:border-amber-600 bg-white transition-all hover:shadow-sm"
            data-testid="import-type-products-update"
          >
            <div className="flex items-start gap-4">
              <div className="w-12 h-12 rounded-lg bg-amber-50 flex items-center justify-center group-hover:bg-amber-100 transition-colors">
                <RotateCcw size={22} className="text-amber-600" />
              </div>
              <div className="flex-1">
                <div className="flex items-center gap-2 mb-1">
                  <h3 className="font-semibold text-slate-800">Update Existing Products</h3>
                  <Badge className="text-[10px] bg-amber-100 text-amber-700 border-0">Merge</Badge>
                </div>
                <p className="text-sm text-slate-500">Match by Product Name and merge only the columns you map. Retail/Wholesale/Cost stay untouched if not mapped. Best for bulk price updates.</p>
                <div className="flex items-center gap-2 mt-3 text-xs text-emerald-600">
                  <CheckCircle size={12} /> Safe: unmapped fields preserved
                </div>
              </div>
              <ChevronRight size={18} className="text-slate-300 group-hover:text-amber-600 transition-colors mt-1" />
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

          {/* Branch Stock + Price card */}
          <button
            onClick={() => { setImportType('branch-stock-price'); setStep('upload'); }}
            className="group text-left p-6 rounded-xl border-2 border-slate-200 hover:border-purple-600 bg-white transition-all hover:shadow-sm"
            data-testid="import-type-branch-stock-price"
          >
            <div className="flex items-start gap-4">
              <div className="w-12 h-12 rounded-lg bg-purple-50 flex items-center justify-center group-hover:bg-purple-100 transition-colors">
                <Store size={22} className="text-purple-600" />
              </div>
              <div className="flex-1">
                <div className="flex items-center gap-2 mb-1">
                  <h3 className="font-semibold text-slate-800">Branch Stock + Price</h3>
                  <Badge className="text-[10px] bg-purple-100 text-purple-700 border-0">Branch</Badge>
                </div>
                <p className="text-sm text-slate-500">Upload a per-branch CSV with cost, retail/wholesale prices, AND quantity. Matches against the global catalog. Never affects other branches.</p>
                <div className="flex items-center gap-2 mt-3 text-xs text-emerald-600">
                  <CheckCircle size={12} /> Safe: main branch untouched
                </div>
              </div>
              <ChevronRight size={18} className="text-slate-300 group-hover:text-purple-600 transition-colors mt-1" />
            </div>
          </button>

          {/* Customers card */}
          <button
            onClick={() => { setImportType('customers'); setStep('upload'); }}
            className="group text-left p-6 rounded-xl border-2 border-slate-200 hover:border-rose-600 bg-white transition-all hover:shadow-sm"
            data-testid="import-type-customers"
          >
            <div className="flex items-start gap-4">
              <div className="w-12 h-12 rounded-lg bg-rose-50 flex items-center justify-center group-hover:bg-rose-100 transition-colors">
                <Users size={22} className="text-rose-600" />
              </div>
              <div className="flex-1">
                <div className="flex items-center gap-2 mb-1">
                  <h3 className="font-semibold text-slate-800">Customers</h3>
                  <Badge className="text-[10px] bg-rose-100 text-rose-700 border-0">Branch</Badge>
                </div>
                <p className="text-sm text-slate-500">Bulk-import customers with credit limits + opening balances. Smart duplicate detection (fuzzy match) lets you merge or split.</p>
                <div className="flex items-center gap-2 mt-3 text-xs text-amber-500">
                  <AlertTriangle size={12} /> Sends one-time SMS for opening balances
                </div>
              </div>
              <ChevronRight size={18} className="text-slate-300 group-hover:text-rose-600 transition-colors mt-1" />
            </div>
          </button>

          {/* Templates */}
          <Card className="border-slate-100 bg-slate-50">
            <CardContent className="py-4 px-5">
              <div className="flex items-center justify-between flex-wrap gap-3">
                <div>
                  <p className="text-sm font-medium text-slate-700">Download Templates</p>
                  <p className="text-xs text-slate-500">Use these CSV templates to fill in data with the correct columns</p>
                </div>
                <div className="flex gap-2 flex-wrap">
                  <Button variant="outline" size="sm" onClick={() => downloadTemplate('products')}>
                    <Download size={13} className="mr-1.5" /> Products
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => downloadTemplate('inventory-seed')}>
                    <Download size={13} className="mr-1.5" /> Inventory
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => downloadTemplate('branch-stock-and-price')}>
                    <Download size={13} className="mr-1.5" /> Branch Setup
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => downloadTemplate('customers')}>
                    <Download size={13} className="mr-1.5" /> Customers
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
            <Badge className={
              importType === 'products' ? 'bg-emerald-100 text-emerald-700 border-0'
              : importType === 'products-update' ? 'bg-amber-100 text-amber-700 border-0'
              : importType === 'branch-stock-price' ? 'bg-purple-100 text-purple-700 border-0'
              : importType === 'customers' ? 'bg-rose-100 text-rose-700 border-0'
              : 'bg-blue-100 text-blue-700 border-0'
            }>
              {importType === 'products' ? 'New Product Catalog'
                : importType === 'products-update' ? 'Update Existing Products'
                : importType === 'branch-stock-price' ? 'Branch Stock + Price'
                : importType === 'customers' ? 'Customers'
                : 'Inventory Seed'}
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

          {/* Branch-scoped extras (branch picker + optional PIN + opening balance date) */}
          {branchScoped && (
            <div className={`grid sm:grid-cols-${importType === 'customers' ? '2' : '2'} gap-4 p-4 ${importType === 'customers' ? 'bg-rose-50 border-rose-200' : importType === 'branch-stock-price' ? 'bg-purple-50 border-purple-200' : 'bg-amber-50 border-amber-200'} border rounded-lg`}>
              <div>
                <label className="text-sm font-medium mb-1.5 block">Target Branch <span className="text-red-500">*</span></label>
                <Select value={branchId || currentBranch?.id || ''} onValueChange={setBranchId}>
                  <SelectTrigger className="h-9" data-testid="branch-picker"><SelectValue placeholder="Select branch" /></SelectTrigger>
                  <SelectContent>
                    {branches.map(b => <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              {needsPin && (
                <div>
                  <label className="text-sm font-medium mb-1.5 block">Admin PIN <span className="text-red-500">*</span></label>
                  <Input type="password" autoComplete="new-password" value={pin} onChange={e => setPin(e.target.value)}
                    placeholder="Enter admin PIN" className="h-9" maxLength={6} data-testid="admin-pin-input" />
                </div>
              )}
              {importType === 'customers' && (
                <div>
                  <label className="text-sm font-medium mb-1.5 block">Opening Balance Date</label>
                  <Input type="date" value={openingBalanceDate}
                    onChange={e => setOpeningBalanceDate(e.target.value)}
                    className="h-9" data-testid="opening-balance-date" />
                  <p className="text-[11px] text-slate-500 mt-1">All migrated balances will be invoiced as of this date.</p>
                </div>
              )}
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
                <><div className="w-4 h-4 rounded-full border-2 border-white border-t-transparent animate-spin mr-2" />{hasPreviewStep ? 'Analyzing...' : importType === 'products-update' ? 'Updating...' : 'Importing...'}</>
              ) : (
                <><Upload size={15} className="mr-2" />{
                  hasPreviewStep ? `Preview ${parsed.total_rows} rows`
                  : importType === 'products-update' ? `Update ${parsed.total_rows} rows`
                  : `Import ${parsed.total_rows} rows`
                }</>
              )}
            </Button>
          </div>
        </div>
      )}

      {/* ── STEP: Preview (Branch Stock+Price + Customers) ── */}
      {step === 'preview' && previewData && (
        <div className="space-y-5" data-testid="preview-step">
          {/* Summary banner */}
          <Card className={importType === 'branch-stock-price' ? 'border-purple-200 bg-purple-50' : 'border-rose-200 bg-rose-50'}>
            <CardContent className="py-3 px-5">
              <div className="flex items-center gap-3">
                <CheckCircle size={20} className={importType === 'branch-stock-price' ? 'text-purple-600' : 'text-rose-600'} />
                <div className="flex-1 text-sm">
                  <p className="font-medium text-slate-800">{previewData.summary}</p>
                  {importType === 'branch-stock-price' && previewData.branch?.name && (
                    <p className="text-xs text-slate-500 mt-0.5">Branch: <strong>{previewData.branch.name}</strong></p>
                  )}
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Branch Stock+Price preview table */}
          {importType === 'branch-stock-price' && previewData.matched && previewData.matched.length > 0 && (
            <Card className="border-slate-200">
              <CardHeader className="py-3 px-5 bg-slate-50 border-b">
                <CardTitle className="text-sm font-semibold text-slate-700">
                  Will Update ({previewData.matched_count}) — first {Math.min(previewData.matched.length, 200)} shown
                </CardTitle>
              </CardHeader>
              <CardContent className="p-0 max-h-96 overflow-y-auto">
                <Table>
                  <TableHeader>
                    <TableRow className="bg-slate-50">
                      <TableHead className="text-xs">Product</TableHead>
                      <TableHead className="text-xs text-right">New Cost</TableHead>
                      <TableHead className="text-xs text-right">New Retail</TableHead>
                      <TableHead className="text-xs text-right">New Wholesale</TableHead>
                      <TableHead className="text-xs text-right">New Qty</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {previewData.matched.map((m, i) => (
                      <TableRow key={i} className="text-sm">
                        <TableCell className="font-medium">{m.name}<div className="text-[10px] text-slate-400 font-mono">{m.sku}</div></TableCell>
                        <TableCell className="text-right text-xs">{m.new_cost !== null ? `₱${Number(m.new_cost).toFixed(2)}` : <span className="text-slate-300">—</span>}</TableCell>
                        <TableCell className="text-right text-xs">{m.new_prices?.retail !== undefined ? `₱${Number(m.new_prices.retail).toFixed(2)}` : <span className="text-slate-300">—</span>}</TableCell>
                        <TableCell className="text-right text-xs">{m.new_prices?.wholesale !== undefined ? `₱${Number(m.new_prices.wholesale).toFixed(2)}` : <span className="text-slate-300">—</span>}</TableCell>
                        <TableCell className="text-right text-xs">{m.new_qty !== null ? Number(m.new_qty).toFixed(0) : <span className="text-slate-300">—</span>}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          )}

          {/* Customers — auto_create summary */}
          {importType === 'customers' && previewData.auto_create?.length > 0 && (
            <Card className="border-emerald-200">
              <CardHeader className="py-3 px-5 bg-emerald-50 border-b border-emerald-200">
                <CardTitle className="text-sm font-semibold text-emerald-800">
                  Ready to Create ({previewData.auto_create.length})
                </CardTitle>
              </CardHeader>
              <CardContent className="p-0 max-h-72 overflow-y-auto">
                <Table>
                  <TableHeader>
                    <TableRow className="bg-emerald-50/50">
                      <TableHead className="text-xs">Name</TableHead>
                      <TableHead className="text-xs">Phone</TableHead>
                      <TableHead className="text-xs text-right">Credit Limit</TableHead>
                      <TableHead className="text-xs text-right">Opening Balance</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {previewData.auto_create.slice(0, 100).map((r, i) => (
                      <TableRow key={i}>
                        <TableCell className="text-sm font-medium">{r.payload.name}</TableCell>
                        <TableCell className="text-xs">{r.payload.phones?.[0] || ''}</TableCell>
                        <TableCell className="text-xs text-right">₱{Number(r.payload.credit_limit || 0).toFixed(2)}</TableCell>
                        <TableCell className="text-xs text-right font-medium">{r.payload.opening_balance > 0 ? `₱${Number(r.payload.opening_balance).toFixed(2)}` : <span className="text-slate-300">—</span>}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          )}

          {/* Customers — fuzzy review */}
          {importType === 'customers' && previewData.fuzzy?.length > 0 && (
            <Card className="border-amber-300 bg-amber-50/40">
              <CardHeader className="py-3 px-5 bg-amber-50 border-b border-amber-200">
                <CardTitle className="text-sm font-semibold text-amber-800">
                  Possible Duplicates — Decide ({previewData.fuzzy.length})
                </CardTitle>
                <p className="text-xs text-amber-700 mt-1">
                  These look similar to existing customers. For each row, choose: <strong>Merge</strong> (update existing), <strong>Create as new</strong>, <strong>Skip</strong>, or <strong>Skip & Remember</strong> (won't ask again).
                </p>
              </CardHeader>
              <CardContent className="p-0 max-h-[600px] overflow-y-auto">
                {previewData.fuzzy.map((f, i) => (
                  <div key={i} className="border-b border-amber-100 p-4 space-y-2" data-testid={`fuzzy-row-${f.row}`}>
                    <div className="flex items-center justify-between flex-wrap gap-2">
                      <div className="text-sm">
                        <span className="text-slate-400 text-xs mr-2">Row {f.row}</span>
                        <span className="font-semibold">{f.payload.name}</span>
                        {f.payload.phones?.[0] && <span className="text-xs text-slate-500 ml-2">· {f.payload.phones[0]}</span>}
                        {f.payload.opening_balance > 0 && (
                          <Badge className="ml-2 text-[10px] bg-blue-100 text-blue-700 border-0">
                            OB ₱{Number(f.payload.opening_balance).toFixed(2)}
                          </Badge>
                        )}
                      </div>
                    </div>
                    <div className="text-xs text-slate-500">
                      Looks like:
                    </div>
                    <div className="space-y-1.5 ml-4">
                      {f.candidates.map((c, j) => (
                        <div key={j} className="flex items-center gap-3 text-xs">
                          <span className="font-mono bg-slate-100 px-1.5 py-0.5 rounded">{Math.round(c.similarity * 100)}%</span>
                          <span className="font-medium">{c.name}</span>
                          {c.phone && <span className="text-slate-500">· {c.phone}</span>}
                          <span className="text-slate-400">({c.reason === 'phone_partial' ? 'phone match' : 'similar name'})</span>
                        </div>
                      ))}
                    </div>
                    <div className="flex flex-wrap gap-2 mt-2">
                      <Button size="sm" variant={decisions[f.row]?.action === 'merge' ? 'default' : 'outline'}
                        className="text-xs h-7"
                        onClick={() => setDecisions(prev => ({ ...prev, [f.row]: { action: 'merge', target_id: f.candidates[0].id } }))}
                        data-testid={`decide-merge-${f.row}`}>
                        Merge into "{f.candidates[0].name.slice(0, 20)}"
                      </Button>
                      <Button size="sm" variant={decisions[f.row]?.action === 'create' ? 'default' : 'outline'}
                        className="text-xs h-7"
                        onClick={() => setDecisions(prev => ({ ...prev, [f.row]: { action: 'create' } }))}
                        data-testid={`decide-create-${f.row}`}>
                        Create as new
                      </Button>
                      <Button size="sm" variant={decisions[f.row]?.action === 'skip' ? 'default' : 'outline'}
                        className="text-xs h-7"
                        onClick={() => setDecisions(prev => ({ ...prev, [f.row]: { action: 'skip' } }))}
                        data-testid={`decide-skip-${f.row}`}>
                        Skip
                      </Button>
                      <Button size="sm" variant={decisions[f.row]?.action === 'skip_and_remember' ? 'default' : 'outline'}
                        className="text-xs h-7"
                        onClick={() => setDecisions(prev => ({ ...prev, [f.row]: { action: 'skip_and_remember', target_id: f.candidates[0].id } }))}
                        data-testid={`decide-remember-${f.row}`}>
                        Skip &amp; Remember (different person)
                      </Button>
                    </div>
                  </div>
                ))}
              </CardContent>
            </Card>
          )}

          {/* Customers — exact dupes (auto-skipped report) */}
          {importType === 'customers' && previewData.exact_dupe?.length > 0 && (
            <Card className="border-slate-200">
              <CardHeader className="py-3 px-5 bg-slate-50 border-b">
                <CardTitle className="text-sm text-slate-700">
                  Auto-skipped Duplicates ({previewData.exact_dupe.length})
                </CardTitle>
                <p className="text-xs text-slate-500 mt-1">Hard duplicates (exact name or exact phone). These will not be imported.</p>
              </CardHeader>
              <CardContent className="p-0 max-h-48 overflow-y-auto">
                <Table>
                  <TableBody>
                    {previewData.exact_dupe.map((d, i) => (
                      <TableRow key={i}>
                        <TableCell className="text-xs text-slate-400 w-12">{d.row}</TableCell>
                        <TableCell className="text-sm">{d.name}</TableCell>
                        <TableCell className="text-xs text-slate-500">{d.reason.replace(/_/g, ' ')}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          )}

          {/* Unmatched / errors */}
          {previewData.unmatched?.length > 0 && (
            <Card className="border-amber-200">
              <CardHeader className="py-3 px-5 bg-amber-50 border-b border-amber-200">
                <CardTitle className="text-sm text-amber-800">{previewData.unmatched.length} Products Not Found</CardTitle>
                <p className="text-xs text-amber-700 mt-1">These rows didn't match any product in your global catalog. Add them to your catalog first, then re-import.</p>
              </CardHeader>
              <CardContent className="p-0 max-h-48 overflow-y-auto">
                <Table>
                  <TableBody>
                    {previewData.unmatched.map((u, i) => (
                      <TableRow key={i}>
                        <TableCell className="text-xs text-slate-400 w-12">{u.row}</TableCell>
                        <TableCell className="text-sm">{u.name}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          )}

          {/* Commit / Back actions */}
          <div className="flex justify-end gap-3">
            <Button variant="outline" onClick={() => setStep('map')}>Back to mapping</Button>
            <Button onClick={commitAfterPreview} disabled={loading}
              className="bg-[#1A4D2E] hover:bg-[#14532d] text-white min-w-40"
              data-testid="commit-import-btn">
              {loading ? (
                <><div className="w-4 h-4 rounded-full border-2 border-white border-t-transparent animate-spin mr-2" />Committing...</>
              ) : (
                <><Upload size={15} className="mr-2" />Confirm &amp; Import</>
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
                    <div className="text-2xl font-bold text-emerald-800">
                      {result.imported ?? result.updated ?? result.created ?? 0}
                      {result.merged > 0 && <span className="text-base font-medium ml-2">+{result.merged} merged</span>}
                      {(result.prices_updated > 0 || result.qty_updated > 0) && (
                        <span className="text-xs font-medium ml-1 text-emerald-700">
                          ({result.prices_updated || 0} prices · {result.qty_updated || 0} stock)
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-emerald-600">
                      {result.created !== undefined ? 'Customers created'
                        : result.updated !== undefined ? 'Successfully updated'
                        : result.prices_updated !== undefined ? 'Branch entries written'
                        : 'Successfully imported'}
                    </div>
                  </div>
                </div>
              </CardContent>
            </Card>
            <Card className="border-amber-200 bg-amber-50">
              <CardContent className="pt-4 pb-3">
                <div className="flex items-center gap-3">
                  <AlertTriangle size={24} className="text-amber-600" />
                  <div>
                    <div className="text-2xl font-bold text-amber-800">
                      {(result.skipped?.length ?? 0) + (result.not_found?.length ?? 0) + (result.unmatched?.length ?? 0)}
                    </div>
                    <div className="text-xs text-amber-600">
                      {result.unmatched?.length ? 'Not found in catalog' : result.skipped ? 'Skipped' : 'Not found'}
                    </div>
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

          {/* Customer-import: opening balance + SMS summary */}
          {result.invoiced_count > 0 && (
            <Card className="border-blue-200 bg-blue-50">
              <CardContent className="py-3 px-5">
                <div className="flex items-start gap-3">
                  <FileSpreadsheet size={18} className="text-blue-600 mt-0.5" />
                  <div className="flex-1 text-sm">
                    <p className="font-medium text-blue-900">
                      {result.invoiced_count} opening-balance invoice{result.invoiced_count === 1 ? '' : 's'} created
                    </p>
                    <p className="text-blue-700 mt-0.5 text-xs">
                      {result.sms_queued || 0} SMS notification{result.sms_queued === 1 ? '' : 's'} queued. These invoices appear in AR aging, customer statements, and the closing wizard like any credit sale.
                    </p>
                  </div>
                </div>
              </CardContent>
            </Card>
          )}

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
                  <div>
                    <CardTitle className="text-sm text-amber-800">
                      {result.skipped ? `${result.skipped.length} Duplicate Products — Review & Decide` : `${result.not_found.length} Products Not Found in System`}
                    </CardTitle>
                    {result.skipped && (
                      <p className="text-[11px] text-amber-700 mt-1">
                        Overwrite merges only the columns you mapped — unmapped fields (e.g. retail) are preserved.
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    {result.skipped && result.skipped.length > 0 && (
                      <Button
                        size="sm"
                        variant="outline"
                        data-testid="select-all-duplicates-btn"
                        onClick={() => {
                          const allSelected = overwriteIds.size === result.skipped.length;
                          setOverwriteIds(allSelected ? new Set() : new Set(result.skipped.map(s => s.existing_id)));
                        }}
                        className="text-xs h-7 border-amber-300 text-amber-800 hover:bg-amber-100"
                      >
                        {overwriteIds.size === result.skipped.length ? 'Deselect All' : `Select All (${result.skipped.length})`}
                      </Button>
                    )}
                    {result.skipped && overwriteIds.size > 0 && (
                      <Button size="sm" onClick={handleOverwrite} disabled={loading}
                        data-testid="overwrite-selected-btn"
                        className="bg-amber-600 hover:bg-amber-700 text-white text-xs h-7">
                        {loading ? 'Merging…' : `Overwrite ${overwriteIds.size} selected`}
                      </Button>
                    )}
                  </div>
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
