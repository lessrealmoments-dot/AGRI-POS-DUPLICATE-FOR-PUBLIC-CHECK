import { useState, useEffect, useCallback, useRef } from 'react';
import { useAuth, api } from '../contexts/AuthContext';
import { BarcodeDisplay } from '../components/BarcodeDisplay';
import { Card, CardContent } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Badge } from '../components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../components/ui/dialog';
import { Printer, Search, Package, Plus, Minus, Trash2, ScanBarcode, X, CheckCircle, Warehouse } from 'lucide-react';
import { toast } from 'sonner';
import CalcInput from '../components/CalcInput';

const LABEL_SIZES = {
  '40x30': { w: 40, h: 30, label: '40 x 30 mm (Default)', barcodeH: 22, fontSize: 8, barcodeW: 1.0, nameSize: '7px' },
  '50x30': { w: 50, h: 30, label: '50 x 30 mm', barcodeH: 22, fontSize: 9, barcodeW: 1.2, nameSize: '8px' },
};

export default function BarcodePrintPage() {
  const { currentBranch } = useAuth();
  const [products, setProducts] = useState([]);
  const [search, setSearch] = useState('');
  const [searchResults, setSearchResults] = useState([]);
  const [printList, setPrintList] = useState([]); // [{product, qty}]
  const [labelSize, setLabelSize] = useState('40x30');
  const [previewOpen, setPreviewOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadingInventory, setLoadingInventory] = useState(false);
  const [source, setSource] = useState('inventory'); // 'inventory' | 'custom'
  const printRef = useRef(null);
  const searchTimer = useRef(null);

  // Load inventory products (with barcodes)
  const loadProducts = useCallback(async () => {
    setLoading(true);
    try {
      const params = { limit: 500, is_repack: false };
      const res = await api.get('/products', { params });
      setProducts((res.data.products || []).filter(p => p.barcode));
    } catch { toast.error('Failed to load products'); }
    setLoading(false);
  }, []);

  useEffect(() => { loadProducts(); }, [loadProducts]);

  // Load from inventory — auto-match label qty to stock count
  const loadFromInventory = async () => {
    if (!currentBranch?.id || currentBranch.id === 'all') {
      toast.error('Select a specific branch first');
      return;
    }
    setLoadingInventory(true);
    try {
      const res = await api.get(`/products/barcode-inventory/${currentBranch.id}`);
      const inv = res.data.products || [];
      if (!inv.length) {
        toast.info('No products with barcodes found in this branch inventory');
        setLoadingInventory(false);
        return;
      }
      const newList = inv.map(p => ({ product: p, qty: Math.max(1, Math.round(p.stock || 1)) }));
      setPrintList(newList);
      const totalLabels = newList.reduce((s, i) => s + i.qty, 0);
      toast.success(`Loaded ${inv.length} products (${totalLabels} labels) from inventory`);
    } catch { toast.error('Failed to load inventory'); }
    setLoadingInventory(false);
  };

  // Search products
  useEffect(() => {
    if (!search.trim()) { setSearchResults([]); return; }
    clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(async () => {
      try {
        const res = await api.get('/products', { params: { search, limit: 15, is_repack: false } });
        setSearchResults((res.data.products || []).filter(p => p.barcode));
      } catch { setSearchResults([]); }
    }, 250);
    return () => clearTimeout(searchTimer.current);
  }, [search]);

  const addToPrintList = (product, qty = 1) => {
    setPrintList(prev => {
      const exists = prev.find(p => p.product.id === product.id);
      if (exists) return prev.map(p => p.product.id === product.id ? { ...p, qty: p.qty + qty } : p);
      return [...prev, { product, qty }];
    });
    setSearch('');
    setSearchResults([]);
  };

  const updateQty = (productId, qty) => {
    if (qty <= 0) {
      setPrintList(prev => prev.filter(p => p.product.id !== productId));
    } else {
      setPrintList(prev => prev.map(p => p.product.id === productId ? { ...p, qty } : p));
    }
  };

  const removeFromList = (productId) => {
    setPrintList(prev => prev.filter(p => p.product.id !== productId));
  };

  const addAllInventory = () => {
    const newList = products.map(p => ({ product: p, qty: 1 }));
    setPrintList(newList);
    toast.success(`Added ${newList.length} products to print list`);
  };

  const totalLabels = printList.reduce((sum, p) => sum + p.qty, 0);
  const size = LABEL_SIZES[labelSize];

  const handlePrint = () => {
    if (!printList.length) { toast.error('Add products to print'); return; }
    setPreviewOpen(true);
    setTimeout(() => {
      const printWindow = window.open('', '_blank', 'width=800,height=600');
      if (!printWindow) { toast.error('Please allow pop-ups to print'); return; }

      // Build labels HTML
      const labelsHtml = printList.flatMap(({ product, qty }) =>
        Array.from({ length: qty }, (_, i) => `
          <div class="label" style="width:${size.w}mm;height:${size.h}mm;">
            <div class="product-name" style="font-size:${size.nameSize};">${product.name}</div>
            <svg id="bc-${product.id}-${i}"></svg>
          </div>
        `)
      ).join('');

      printWindow.document.write(`<!DOCTYPE html><html><head>
        <title>Barcode Labels - AgriBooks</title>
        <script src="https://cdn.jsdelivr.net/npm/jsbarcode@3.12.3/dist/JsBarcode.all.min.js"><\/script>
        <style>
          @page { size: auto; margin: 2mm; }
          * { margin:0; padding:0; box-sizing:border-box; }
          body { font-family: Arial, sans-serif; }
          .labels-grid {
            display: flex; flex-wrap: wrap; gap: 2mm;
            padding: 2mm;
          }
          .label {
            border: 0.5px dashed #ccc;
            display: flex; flex-direction: column;
            align-items: center; justify-content: center;
            overflow: hidden; padding: 1mm;
            page-break-inside: avoid;
          }
          .product-name {
            font-weight: 700; text-align: center;
            max-width: 100%; overflow: hidden;
            text-overflow: ellipsis; white-space: nowrap;
            line-height: 1.2; margin-bottom: 0.5mm;
          }
          .label svg { max-width: 100%; }
          @media print {
            .label { border: none; }
          }
        </style>
      </head><body>
        <div class="labels-grid">${labelsHtml}</div>
        <script>
          document.querySelectorAll('svg[id^="bc-"]').forEach(svg => {
            const barcode = svg.closest('.label').dataset?.barcode || svg.getAttribute('data-barcode');
          });
          // Generate barcodes
          ${printList.flatMap(({ product, qty }) =>
            Array.from({ length: qty }, (_, i) =>
              `try { JsBarcode('#bc-${product.id}-${i}', '${product.barcode}', { format:'CODE128', width:${size.barcodeW}, height:${size.barcodeH}, displayValue:true, fontSize:${size.fontSize}, margin:1, textMargin:1 }); } catch(e) {}`
            )
          ).join(';\n')}
          setTimeout(() => { window.print(); }, 500);
        <\/script>
      </body></html>`);
      printWindow.document.close();
      setPreviewOpen(false);
    }, 300);
  };

  return (
    <div className="space-y-6 animate-fadeIn" data-testid="barcode-print-page">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight" style={{ fontFamily: 'Manrope' }}>Print Barcodes</h1>
          <p className="text-sm text-slate-500 mt-1">
            Select products and quantity of labels to print
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Select value={labelSize} onValueChange={setLabelSize}>
            <SelectTrigger data-testid="label-size-select" className="w-[200px] h-10">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(LABEL_SIZES).map(([key, val]) => (
                <SelectItem key={key} value={key}>{val.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button variant="outline" onClick={addAllInventory} data-testid="add-all-btn" disabled={!products.length}>
            <Package size={15} className="mr-1.5" /> Add All Products
          </Button>
          <Button
            variant="outline"
            onClick={loadFromInventory}
            disabled={loadingInventory}
            data-testid="load-inventory-btn"
          >
            <Warehouse size={15} className="mr-1.5 text-blue-500" />
            {loadingInventory ? 'Loading...' : 'Load from Inventory'}
          </Button>
          <Button
            onClick={handlePrint}
            disabled={!printList.length}
            className="bg-[#1A4D2E] hover:bg-[#14532d] text-white"
            data-testid="print-barcodes-btn"
          >
            <Printer size={16} className="mr-2" /> Print {totalLabels} Label{totalLabels !== 1 ? 's' : ''}
          </Button>
        </div>
      </div>

      {/* Search to add products */}
      <Card className="border-slate-200">
        <CardContent className="p-4">
          <Label className="text-sm font-semibold mb-2 block">Add Products to Print</Label>
          <div className="relative max-w-md">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <Input
              data-testid="barcode-product-search"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search product by name, SKU, or barcode..."
              className="pl-9 h-10"
            />
            {searchResults.length > 0 && (
              <div className="absolute top-full left-0 right-0 mt-1 bg-white border border-slate-200 rounded-lg shadow-xl max-h-64 overflow-y-auto z-50">
                {searchResults.map(p => (
                  <button
                    key={p.id}
                    onClick={() => addToPrintList(p)}
                    className="w-full text-left px-3 py-2.5 hover:bg-slate-50 border-b border-slate-100 last:border-0 flex items-center justify-between"
                    data-testid={`search-add-${p.id}`}
                  >
                    <div>
                      <span className="font-medium text-sm">{p.name}</span>
                      <span className="text-xs text-slate-400 ml-2">{p.sku}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge variant="outline" className="text-[10px] font-mono">{p.barcode}</Badge>
                      <Plus size={14} className="text-emerald-600" />
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Print List Table */}
      {printList.length > 0 ? (
        <Card className="border-slate-200">
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow className="bg-slate-50">
                  <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">Product</TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">Barcode</TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">Preview</TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium text-center w-40">Labels Qty</TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium w-16"></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {printList.map(({ product, qty }) => (
                  <TableRow key={product.id}>
                    <TableCell>
                      <div className="font-medium">{product.name}</div>
                      <div className="text-xs text-slate-400">{product.sku} &middot; {product.category}</div>
                    </TableCell>
                    <TableCell>
                      <code className="text-xs bg-slate-100 px-2 py-1 rounded font-mono">{product.barcode}</code>
                    </TableCell>
                    <TableCell>
                      <BarcodeDisplay value={product.barcode} width={1} height={28} fontSize={9} />
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center justify-center gap-1">
                        <Button
                          variant="outline" size="sm"
                          onClick={() => updateQty(product.id, qty - 1)}
                          data-testid={`qty-minus-${product.id}`}
                          className="h-8 w-8 p-0"
                        >
                          <Minus size={12} />
                        </Button>
                        <CalcInput value={qty}
 onChange={(v) => updateQty(product.id, parseInt(v) || 1)}
 className="w-16 h-8 text-center text-sm font-mono"
 data-testid={`qty-input-${product.id}`} />
                        <Button
                          variant="outline" size="sm"
                          onClick={() => updateQty(product.id, qty + 1)}
                          data-testid={`qty-plus-${product.id}`}
                          className="h-8 w-8 p-0"
                        >
                          <Plus size={12} />
                        </Button>
                      </div>
                    </TableCell>
                    <TableCell>
                      <Button
                        variant="ghost" size="sm"
                        onClick={() => removeFromList(product.id)}
                        className="text-red-500"
                        data-testid={`remove-${product.id}`}
                      >
                        <Trash2 size={14} />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <div className="flex items-center justify-between px-4 py-3 border-t bg-slate-50">
              <span className="text-sm text-slate-500">{printList.length} product(s), {totalLabels} label(s) total</span>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" onClick={() => setPrintList([])} data-testid="clear-print-list">
                  <X size={14} className="mr-1" /> Clear All
                </Button>
                <Button
                  onClick={handlePrint}
                  size="sm"
                  className="bg-[#1A4D2E] hover:bg-[#14532d] text-white"
                  data-testid="print-bottom-btn"
                >
                  <Printer size={14} className="mr-1.5" /> Print Labels
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      ) : (
        <Card className="border-dashed border-slate-300">
          <CardContent className="py-12 text-center">
            <ScanBarcode size={40} className="mx-auto text-slate-300 mb-3" />
            <p className="text-slate-500 font-medium">No products added yet</p>
            <p className="text-sm text-slate-400 mt-1">Search and add products above, or click "Add All Products" to print barcodes for your entire inventory</p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
