import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useAuth, api } from '../contexts/AuthContext';
import { formatPHP } from '../lib/utils';
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Badge } from '../components/ui/badge';
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '../components/ui/accordion';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import {
  ArrowLeft, Package, Tags, DollarSign, Warehouse, Info, Users, History, ShoppingCart,
  Plus, Pencil, Trash2, Link2, AlertTriangle, TrendingDown, TrendingUp, Save, Activity, ScanBarcode, Printer
} from 'lucide-react';
import { toast } from 'sonner';
import { BarcodeDisplay } from '../components/BarcodeDisplay';
import CategorySelect from '../components/CategorySelect';
import GlobalPriceBadge from '../components/GlobalPriceBadge';
import CapitalSourceBadge from '../components/CapitalSourceBadge';
import CalcInput from '../components/CalcInput';

export default function ProductDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { currentBranch, branches, user, hasPerm } = useAuth();
  const canEditCost = hasPerm('products', 'edit_cost');
  const [detail, setDetail] = useState(null);
  const [movements, setMovements] = useState([]);
  const [orders, setOrders] = useState([]);
  const [capitalHistory, setCapitalHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editMode, setEditMode] = useState(false);
  const [editForm, setEditForm] = useState({});
  const [repackDialog, setRepackDialog] = useState(false);
  const [vendorDialog, setVendorDialog] = useState(false);
  const [schemes, setSchemes] = useState([]);
  const [branchSuppliers, setBranchSuppliers] = useState([]);
  const [repackForm, setRepackForm] = useState({ name: '', unit: 'Pack', units_per_parent: 1, cost_price: 0, add_on_cost: 0, prices: {} });
  const [vendorForm, setVendorForm] = useState({ supplier_id: '', last_price: 0, is_preferred: false });
  const [barcodeGenerating, setBarcodeGenerating] = useState(false);

  // Branch pricing state
  const [branchOverrides, setBranchOverrides] = useState({}); // { branch_id: override_doc }
  const [branchPriceEdit, setBranchPriceEdit] = useState(null); // { branch_id, prices: {}, cost_price }
  const [savingBranchPrice, setSavingBranchPrice] = useState(false);

  // Global Price (price-review) status for the active branch
  const [pendingReviewAtBranch, setPendingReviewAtBranch] = useState(false);
  useEffect(() => {
    if (!id || !currentBranch?.id) { setPendingReviewAtBranch(false); return; }
    api.get('/inventory/pending-review-ids', { params: { branch_id: currentBranch.id } })
      .then(r => setPendingReviewAtBranch((r.data?.product_ids || []).includes(id)))
      .catch(() => {});
  }, [id, currentBranch]);

  const isAdmin = user?.role === 'admin';

  const handleGenerateBarcode = async () => {
    setBarcodeGenerating(true);
    try {
      const res = await api.post(`/products/${id}/generate-barcode`);
      if (res.data.already_existed) {
        toast.info(`Product already has barcode: ${res.data.barcode}`);
      } else {
        toast.success(`Barcode generated: ${res.data.barcode}`);
      }
      fetchDetail();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed to generate barcode'); }
    setBarcodeGenerating(false);
  };

  const fetchDetail = useCallback(async () => {
    try {
      const params = currentBranch ? { branch_id: currentBranch.id } : {};
      const res = await api.get(`/products/${id}/detail`, { params });
      setDetail(res.data);
      // If on a branch, merge branch-specific prices into the edit form
      const product = res.data.product;
      const branchPrices = res.data.cost?.branch_retail_prices;
      if (currentBranch && branchPrices && Object.keys(branchPrices).length > 0) {
        setEditForm({ ...product, prices: { ...(product.prices || {}), ...branchPrices } });
      } else {
        setEditForm(product);
      }
    } catch (e) { toast.error('Failed to load product'); navigate('/products'); }
    setLoading(false);
  }, [id, navigate, currentBranch]);

  const fetchBranchOverrides = useCallback(async () => {
    if (!isAdmin) return;
    try {
      const res = await api.get('/branch-prices', { params: { product_id: id } });
      const map = {};
      (res.data || []).forEach(o => { map[o.branch_id] = o; });
      setBranchOverrides(map);
    } catch { /* silent */ }
  }, [id, isAdmin]);

  const [movementLimit, setMovementLimit] = useState(50);
  const [hasMoreMovements, setHasMoreMovements] = useState(false);
  const [totalMovements, setTotalMovements] = useState(0);

  const fetchMovements = useCallback(async (lim) => {
    try {
      const useLimit = lim || movementLimit;
      const params = { limit: useLimit };
      if (currentBranch) params.branch_id = currentBranch.id;
      const res = await api.get(`/products/${id}/movements`, { params });
      setMovements(res.data.movements);
      setTotalMovements(res.data.total || res.data.movements.length);
      setHasMoreMovements((res.data.total || 0) > useLimit);
    } catch { }
  }, [id, currentBranch, movementLimit]);

  const fetchOrders = useCallback(async () => {
    try {
      const params = { limit: 50 };
      if (currentBranch) params.branch_id = currentBranch.id;
      const res = await api.get(`/products/${id}/orders`, { params });
      setOrders(res.data.orders);
    } catch { }
  }, [id, currentBranch]);

  const fetchCapitalHistory = useCallback(async () => {
    try {
      const params = {};
      if (currentBranch) params.branch_id = currentBranch.id;
      const res = await api.get(`/products/${id}/capital-history`, { params });
      setCapitalHistory(res.data || []);
    } catch { }
  }, [id, currentBranch]);

  useEffect(() => { fetchDetail(); api.get('/price-schemes').then(r => setSchemes(r.data)).catch(() => {}); }, [fetchDetail]);
  useEffect(() => { fetchBranchOverrides(); }, [fetchBranchOverrides]);
  useEffect(() => { fetchCapitalHistory(); }, [fetchCapitalHistory]);
  // Reset lazy-loaded data when branch changes so stale cross-branch data doesn't linger
  useEffect(() => { setMovements([]); setOrders([]); }, [currentBranch]);

  const handleSave = async () => {
    try {
      // Strip cost_price and capital_method from payload if user cannot edit capital
      const payload = canEditCost
        ? editForm
        : (({ cost_price, capital_method, ...rest }) => rest)(editForm);

      if (currentBranch) {
        // When on a branch: save prices to branch_prices, other fields to global product
        const { prices, ...nonPriceFields } = payload;
        // Save branch-specific prices
        await api.put(`/branch-prices/${id}`, {
          branch_id: currentBranch.id,
          prices: prices || {},
          cost_price: canEditCost ? (payload.cost_price || null) : null,
        });
        // Save non-price fields (name, category, etc.) to global product - exclude prices
        const globalPayload = { ...nonPriceFields };
        delete globalPayload.prices;
        if (!canEditCost) {
          delete globalPayload.cost_price;
          delete globalPayload.capital_method;
        }
        if (Object.keys(globalPayload).length > 0) {
          await api.put(`/products/${id}`, globalPayload);
        }
        toast.success(`Prices saved for ${currentBranch.name}`);
      } else {
        // No branch context: save everything globally
        await api.put(`/products/${id}`, payload);
        toast.success('Product updated (global)');
      }
      setEditMode(false);
      fetchDetail();
      fetchBranchOverrides();
    } catch (e) { toast.error(e.response?.data?.detail || 'Error saving'); }
  };

  const handleRepack = async () => {
    try {
      await api.post(`/products/${id}/generate-repack`, repackForm);
      toast.success('Repack generated!');
      setRepackDialog(false);
      fetchDetail();
    } catch (e) { toast.error(e.response?.data?.detail || 'Error'); }
  };

  const fetchBranchSuppliers = async () => {
    try {
      const params = currentBranch ? { branch_id: currentBranch.id } : {};
      const res = await api.get('/suppliers', { params });
      setBranchSuppliers(res.data || []);
    } catch { setBranchSuppliers([]); }
  };

  const handleAddVendor = async () => {
    if (!vendorForm.supplier_id) {
      toast.error('Please select a supplier');
      return;
    }
    try {
      const payload = { ...vendorForm };
      if (currentBranch) payload.branch_id = currentBranch.id;
      await api.post(`/products/${id}/vendors`, payload);
      toast.success('Vendor added');
      setVendorDialog(false);
      fetchDetail();
    } catch (e) { toast.error(e.response?.data?.detail || 'Error'); }
  };

  const removeVendor = async (vendorId) => {
    try { await api.delete(`/products/${id}/vendors/${vendorId}`); toast.success('Removed'); fetchDetail(); }
    catch { toast.error('Error'); }
  };

  const handleSaveBranchPrice = async () => {
    if (!branchPriceEdit) return;
    setSavingBranchPrice(true);
    try {
      await api.put(`/branch-prices/${id}`, {
        branch_id: branchPriceEdit.branch_id,
        prices: branchPriceEdit.prices,
        cost_price: branchPriceEdit.cost_price || null,
      });
      toast.success('Branch prices saved');
      setBranchPriceEdit(null);
      fetchBranchOverrides();
      fetchDetail();
    } catch (e) { toast.error(e.response?.data?.detail || 'Error saving'); }
    setSavingBranchPrice(false);
  };

  const handleRemoveBranchOverride = async (branchId) => {
    try {
      await api.delete(`/branch-prices/${id}`, { params: { branch_id: branchId } });
      toast.success('Override removed — now using global prices');
      setBranchPriceEdit(null);
      fetchBranchOverrides();
      fetchDetail();
    } catch (e) { toast.error(e.response?.data?.detail || 'Error'); }
  };

  const updatePrice = (key, val) => setEditForm({ ...editForm, prices: { ...editForm.prices, [key]: parseFloat(val) || 0 } });
  const updateRepackPrice = (key, val) => setRepackForm({ ...repackForm, prices: { ...repackForm.prices, [key]: parseFloat(val) || 0 } });

  if (loading) return <div className="flex items-center justify-center h-64 text-slate-400">Loading...</div>;
  if (!detail) return null;

  const { product, inventory, cost, repacks, vendors } = detail;
  const branchStock = currentBranch ? (inventory.on_hand[currentBranch.id] || 0) : inventory.total;

  return (
    <div className="space-y-6 animate-fadeIn" data-testid="product-detail-page">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-4">
          <Button variant="ghost" size="sm" onClick={() => navigate('/products')} data-testid="back-to-products">
            <ArrowLeft size={16} />
          </Button>
          <div>
            {editMode ? (
              <Input data-testid="edit-product-name" value={editForm.name} onChange={e => setEditForm({ ...editForm, name: e.target.value })}
                className="text-2xl font-bold h-10 mb-1" style={{ fontFamily: 'Manrope' }} />
            ) : (
              <h1 className="text-2xl font-bold tracking-tight" style={{ fontFamily: 'Manrope' }}>{product.name}</h1>
            )}
            <div className="flex items-center gap-2 mt-1 flex-wrap">
              <span className="font-mono text-sm text-slate-500">{product.sku}</span>
              <Badge variant="outline" className={`text-[10px] ${product.is_repack ? 'border-amber-300 text-amber-700 bg-amber-50' : 'border-emerald-300 text-emerald-700 bg-emerald-50'}`}>
                {product.is_repack ? 'Repack' : 'Parent'}
              </Badge>
              <Badge variant="outline" className="text-[10px]">{product.product_type || 'stockable'}</Badge>
              <Badge variant="outline" className="text-[10px]">{product.category}</Badge>
              {pendingReviewAtBranch && currentBranch?.id && (
                <GlobalPriceBadge
                  productId={id}
                  branchId={currentBranch.id}
                  reviewed={false}
                  onMarked={() => setPendingReviewAtBranch(false)}
                />
              )}
            </div>
          </div>
        </div>
        <div className="flex gap-2">
          {editMode ? (
            <>
              <Button variant="outline" size="sm" onClick={() => { setEditMode(false); setEditForm(product); }}>Cancel</Button>
              <Button size="sm" data-testid="save-product-detail" onClick={handleSave} className="bg-[#1A4D2E] hover:bg-[#14532d] text-white">
                <Save size={14} className="mr-1" /> {currentBranch ? `Save (${currentBranch.name})` : 'Save (Global)'}
              </Button>
            </>
          ) : (
            <Button size="sm" data-testid="edit-product-detail" onClick={() => setEditMode(true)} variant="outline">
              <Pencil size={14} className="mr-1" /> Edit
            </Button>
          )}
        </div>
      </div>

      {/* Quick Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Card className="border-slate-200"><CardContent className="p-4">
          <p className="text-xs text-slate-500 uppercase font-medium mb-1">On Hand ({currentBranch?.name || 'Total'})</p>
          <p className="text-2xl font-bold" style={{ fontFamily: 'Manrope' }}>{branchStock.toFixed(2)} <span className="text-sm font-normal text-slate-400">{product.unit}</span></p>
        </CardContent></Card>
        <Card className="border-slate-200"><CardContent className="p-4">
          <p className="text-xs text-slate-500 uppercase font-medium mb-1">Coming (On Order)</p>
          <p className="text-2xl font-bold text-blue-600" style={{ fontFamily: 'Manrope' }}>{inventory.coming}</p>
        </CardContent></Card>
        <Card className="border-slate-200"><CardContent className="p-4">
          <p className="text-xs text-slate-500 uppercase font-medium mb-1">Reserved</p>
          <p className="text-2xl font-bold text-amber-600" style={{ fontFamily: 'Manrope' }}>{inventory.reserved}</p>
        </CardContent></Card>
        <Card className="border-slate-200"><CardContent className="p-4">
          <p className="text-xs text-slate-500 uppercase font-medium mb-1 flex items-center gap-1.5">
            Cost ({cost.capital_method || 'moving_avg'})
            <CapitalSourceBadge source={cost.cost_source} dataTestId="capital-source-badge" />
          </p>
          {cost.is_branch_specific ? (
            <div>
              <p className="text-2xl font-bold text-amber-700" style={{ fontFamily: 'Manrope' }}>{formatPHP(cost.branch_cost_price)}</p>
              <p className="text-[10px] text-amber-600 mt-0.5">Branch cost {cost.cost_transfer_order ? `(via ${cost.cost_transfer_order})` : ''}</p>
              <p className="text-[10px] text-slate-400">Global: {formatPHP(cost.cost_price)}</p>
            </div>
          ) : (
            <p className="text-2xl font-bold" style={{ fontFamily: 'Manrope' }}>{formatPHP(cost.moving_average || cost.cost_price)}</p>
          )}
          {cost.last_purchase_warning && <p className="text-[11px] text-amber-600 flex items-center gap-1 mt-1"><AlertTriangle size={10} /> Last purchase was cheaper</p>}
          {/* Always show both MA and LP */}
          <div className="flex gap-3 mt-2 pt-2 border-t border-slate-100">
            <div>
              <p className="text-[9px] text-slate-400 uppercase font-medium">Mov. Avg</p>
              <p className="text-xs font-bold font-mono text-slate-700">{formatPHP(cost.moving_average)}</p>
            </div>
            <div>
              <p className="text-[9px] text-slate-400 uppercase font-medium">Last PO</p>
              <p className={`text-xs font-bold font-mono ${cost.last_purchase_warning ? 'text-amber-600' : 'text-slate-700'}`}>{formatPHP(cost.last_purchase)}</p>
            </div>
          </div>
        </CardContent></Card>
      </div>

      {/* Barcode Section */}
      <Card className="border-slate-200">
        <CardContent className="p-4 flex items-center justify-between gap-4">
          <div className="flex items-center gap-4">
            <ScanBarcode size={20} className="text-slate-400 shrink-0" />
            <div>
              <p className="text-xs text-slate-500 uppercase font-medium mb-1">Product Barcode</p>
              {product.barcode ? (
                <div className="flex items-center gap-4">
                  <BarcodeDisplay value={product.barcode} width={1.5} height={40} fontSize={12} />
                  <code className="text-sm font-mono bg-slate-100 px-2 py-1 rounded">{product.barcode}</code>
                </div>
              ) : (
                <p className="text-sm text-slate-400">No barcode assigned</p>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {!product.barcode && (
              <Button
                variant="outline" size="sm"
                onClick={handleGenerateBarcode}
                disabled={barcodeGenerating}
                data-testid="generate-barcode-btn"
              >
                <ScanBarcode size={14} className="mr-1.5" />
                {barcodeGenerating ? 'Generating...' : 'Generate Barcode'}
              </Button>
            )}
            {product.barcode && (
              <Button
                variant="outline" size="sm"
                onClick={() => navigate('/barcode-print')}
                data-testid="print-barcode-btn"
              >
                <Printer size={14} className="mr-1.5" /> Print Label
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Accordion Sections */}
      <Accordion type="multiple" defaultValue={["sales", "inventory"]} className="space-y-3">
        {/* Sales Information */}
        <AccordionItem value="sales" className="border border-slate-200 rounded-lg bg-white">
          <AccordionTrigger className="px-5 py-4 hover:no-underline" data-testid="section-sales">
            <div className="flex items-center gap-2 text-base font-semibold" style={{ fontFamily: 'Manrope' }}>
              <Tags size={18} className="text-[#1A4D2E]" strokeWidth={1.5} /> Sales Information (Pricing Tiers)
              {currentBranch && (
                <span className="text-[10px] font-normal px-2 py-0.5 rounded-full bg-violet-100 text-violet-700">
                  {currentBranch.name}
                </span>
              )}
            </div>
          </AccordionTrigger>
          <AccordionContent className="px-5 pb-5">
            {/* Capital reference bar — shown in edit mode */}
            {editMode && (
              <div className="mb-4 p-3 rounded-lg bg-blue-50 border border-blue-200 flex flex-wrap gap-5 text-sm">
                <div className="flex items-center gap-2">
                  <span className="text-[10px] font-medium text-blue-500 uppercase tracking-wide">Moving Avg</span>
                  <span className="font-bold text-blue-800 font-mono">{formatPHP(cost.moving_average)}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-[10px] font-medium text-blue-500 uppercase tracking-wide">Last PO</span>
                  <span className="font-bold text-blue-800 font-mono">{formatPHP(cost.last_purchase)}</span>
                  {cost.last_purchase_warning && (
                    <span className="text-[10px] text-amber-600 flex items-center gap-0.5">
                      <AlertTriangle size={11} /> Cheaper than avg
                    </span>
                  )}
                </div>
                {cost.is_branch_specific && (
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] font-medium text-amber-500 uppercase tracking-wide">Branch Cost</span>
                    <span className="font-bold text-amber-700 font-mono">{formatPHP(cost.branch_cost_price)}</span>
                    {cost.cost_transfer_order && <span className="text-[10px] text-slate-400">via {cost.cost_transfer_order}</span>}
                  </div>
                )}
                <div className="flex items-center gap-2">
                  <span className="text-[10px] font-medium text-blue-500 uppercase tracking-wide">Global Cost</span>
                  <span className="font-bold text-blue-800 font-mono">{formatPHP(cost.cost_price)}</span>
                </div>
              </div>
            )}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              {schemes.map(s => {
                const currentPrice = parseFloat(editForm.prices?.[s.key]) || 0;
                const refCost = cost.moving_average || cost.last_purchase || cost.cost_price || 0;
                const markup = refCost > 0 && currentPrice > 0
                  ? ((currentPrice - refCost) / refCost * 100)
                  : null;
                const isBelowCost = currentPrice > 0 && currentPrice < refCost;
                return (
                  <div key={s.id} className={`p-3 rounded-lg border ${editMode && isBelowCost ? 'border-red-300 bg-red-50' : 'border-slate-100 bg-slate-50'}`}>
                    <p className="text-xs text-slate-500 font-medium mb-1">{s.name}</p>
                    {editMode ? (
                      <div>
                        <CalcInput value={editForm.prices?.[s.key] || ''}
 onChange={(v) => updatePrice(s.key, v)}
 className={`h-9 text-lg font-bold ${isBelowCost ? 'border-red-400 text-red-700' : ''}`}
 data-testid={`edit-price-${s.key}`} />
                        {/* Reference indicators below input */}
                        <div className="mt-2 space-y-1 border-t border-slate-200 pt-2">
                          {cost.moving_average > 0 && (
                            <div className="flex justify-between text-[11px]">
                              <span className="text-slate-400">vs Moving Avg</span>
                              <span className={`font-semibold ${currentPrice < cost.moving_average ? 'text-red-500' : 'text-slate-500'}`}>
                                {formatPHP(cost.moving_average)}
                              </span>
                            </div>
                          )}
                          {cost.last_purchase > 0 && cost.last_purchase !== cost.moving_average && (
                            <div className="flex justify-between text-[11px]">
                              <span className="text-slate-400">vs Last PO</span>
                              <span className={`font-semibold ${currentPrice < cost.last_purchase ? 'text-red-500' : 'text-slate-500'}`}>
                                {formatPHP(cost.last_purchase)}
                              </span>
                            </div>
                          )}
                          {markup !== null && (
                            <div className="flex justify-between text-[11px] border-t border-slate-100 pt-1">
                              <span className="text-slate-400">Markup</span>
                              <span className={`font-bold ${markup < 0 ? 'text-red-600' : markup < 5 ? 'text-amber-500' : 'text-emerald-600'}`}>
                                {markup >= 0 ? '+' : ''}{markup.toFixed(1)}%
                              </span>
                            </div>
                          )}
                          {isBelowCost && (
                            <p className="text-[10px] text-red-500 flex items-center gap-0.5 pt-0.5">
                              <AlertTriangle size={10} /> Below capital
                            </p>
                          )}
                        </div>
                      </div>
                    ) : (
                      <div>
                        {(() => {
                          const branchPrice = cost.branch_retail_prices?.[s.key];
                          const globalPrice = product.prices?.[s.key];
                          const effectivePrice = branchPrice !== undefined ? branchPrice : globalPrice;
                          const isBranchOverride = branchPrice !== undefined && currentBranch;
                          const p = parseFloat(effectivePrice) || 0;
                          const c = cost.moving_average || cost.cost_price || 0;
                          const m = (p > 0 && c > 0) ? (p - c) / c * 100 : null;
                          return (
                            <>
                              <p className="text-lg font-bold" style={{ fontFamily: 'Manrope' }}>{formatPHP(effectivePrice)}</p>
                              {isBranchOverride && (
                                <p className="text-[10px] text-violet-600 font-medium mt-0.5">Branch price</p>
                              )}
                              {m !== null && (
                                <p className={`text-[10px] mt-0.5 font-medium ${m < 0 ? 'text-red-500' : m < 5 ? 'text-amber-500' : 'text-slate-400'}`}>
                                  {m >= 0 ? '+' : ''}{m.toFixed(1)}% markup
                                </p>
                              )}
                            </>
                          );
                        })()}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
            {editMode && (
              <div className="grid grid-cols-3 gap-4 mt-4">
                <div><Label>Category</Label><CategorySelect value={editForm.category} onChange={v => setEditForm({ ...editForm, category: v })} testId="product-detail-category-input" /></div>
                <div><Label>Type</Label>
                  <Select value={editForm.product_type || 'stockable'} onValueChange={v => setEditForm({ ...editForm, product_type: v })}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent><SelectItem value="stockable">Stockable</SelectItem><SelectItem value="service">Service</SelectItem></SelectContent>
                  </Select>
                </div>
                <div><Label>Description</Label><Input value={editForm.description || ''} onChange={e => setEditForm({ ...editForm, description: e.target.value })} /></div>
              </div>
            )}
          </AccordionContent>
        </AccordionItem>

        {/* Repack Section */}
        {!product.is_repack && (
          <AccordionItem value="repack" className="border border-slate-200 rounded-lg bg-white">
            <AccordionTrigger className="px-5 py-4 hover:no-underline" data-testid="section-repack">
              <div className="flex items-center gap-2 text-base font-semibold" style={{ fontFamily: 'Manrope' }}>
                <Link2 size={18} className="text-amber-600" strokeWidth={1.5} /> Repacks ({repacks.length})
              </div>
            </AccordionTrigger>
            <AccordionContent className="px-5 pb-5">
              <Button size="sm" data-testid="create-repack-btn" onClick={() => {
                const autoCost = product.cost_price ? Math.round(product.cost_price * 100) / 100 : 0;
                setRepackForm({ name: `R ${product.name}`, unit: 'Pack', units_per_parent: 1, cost_price: autoCost, add_on_cost: 0, prices: {} });
                setRepackDialog(true);
              }} className="mb-4 bg-[#D97706] hover:bg-[#b45309] text-white">
                <Plus size={14} className="mr-1" /> Generate Repack
              </Button>
              {repacks.length > 0 && (
                <Table>
                  <TableHeader><TableRow className="bg-slate-50">
                    <TableHead className="text-xs uppercase text-slate-500">SKU</TableHead>
                    <TableHead className="text-xs uppercase text-slate-500">Name</TableHead>
                    <TableHead className="text-xs uppercase text-slate-500">Unit</TableHead>
                    <TableHead className="text-xs uppercase text-slate-500 text-right">Per Parent</TableHead>
                    <TableHead className="text-xs uppercase text-slate-500 text-right">Retail</TableHead>
                  </TableRow></TableHeader>
                  <TableBody>
                    {repacks.map(r => (
                      <TableRow key={r.id} className="cursor-pointer table-row-hover" onClick={() => navigate(`/products/${r.id}`)}>
                        <TableCell className="font-mono text-xs">{r.sku}</TableCell>
                        <TableCell className="font-medium">{r.name}</TableCell>
                        <TableCell>{r.unit}</TableCell>
                        <TableCell className="text-right">{r.units_per_parent}</TableCell>
                        <TableCell className="text-right font-semibold">{formatPHP(r.prices?.retail)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </AccordionContent>
          </AccordionItem>
        )}

        {/* Capital / Cost */}
        <AccordionItem value="capital" className="border border-slate-200 rounded-lg bg-white">
          <AccordionTrigger className="px-5 py-4 hover:no-underline" data-testid="section-capital">
            <div className="flex items-center gap-2 text-base font-semibold" style={{ fontFamily: 'Manrope' }}>
              <DollarSign size={18} className="text-emerald-600" strokeWidth={1.5} /> Capital / Cost
            </div>
          </AccordionTrigger>
          <AccordionContent className="px-5 pb-5">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div className="p-3 rounded-lg border bg-slate-50">
                <p className="text-xs text-slate-500 mb-1">Method</p>
                {editMode ? (
                  canEditCost ? (
                    <Select value={editForm.capital_method || 'manual'} onValueChange={v => setEditForm({ ...editForm, capital_method: v })}>
                      <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="moving_average">Moving Average</SelectItem>
                        <SelectItem value="last_purchase">Last Purchase</SelectItem>
                        <SelectItem value="manual">Manual</SelectItem>
                      </SelectContent>
                    </Select>
                  ) : (
                    <p className="font-semibold capitalize text-slate-400">{(cost.method || 'manual').replace('_', ' ')}</p>
                  )
                ) : (
                  <p className="font-semibold capitalize">{(cost.method || 'manual').replace('_', ' ')}</p>
                )}
              </div>
              <div className="p-3 rounded-lg border bg-emerald-50">
                <p className="text-xs text-slate-500 mb-1">Moving Average</p>
                <p className="text-xl font-bold text-emerald-700">{formatPHP(cost.moving_average)}</p>
              </div>
              <div className="p-3 rounded-lg border bg-slate-50">
                <p className="text-xs text-slate-500 mb-1">Last Purchase</p>
                <p className="text-xl font-bold">{formatPHP(cost.last_purchase)}</p>
                {cost.last_purchase_warning && <p className="text-[10px] text-amber-600"><AlertTriangle size={10} className="inline" /> Cheaper than avg</p>}
              </div>
              <div className="p-3 rounded-lg border bg-slate-50">
                <p className="text-xs text-slate-500 mb-1 flex items-center gap-1">
                  Manual Cost
                  {editMode && !canEditCost && <span className="text-amber-500 text-[10px] flex items-center gap-0.5">🔒 locked</span>}
                </p>
                {editMode ? (
                  canEditCost ? (
                    <CalcInput value={editForm.cost_price || 0} onChange={(v) => setEditForm({ ...editForm, cost_price: parseFloat(v) || 0 })} className="h-9" />
                  ) : (
                    <p className="text-xl font-bold text-slate-400">{formatPHP(product.cost_price)}</p>
                  )
                ) : (
                  <p className="text-xl font-bold">{formatPHP(product.cost_price)}</p>
                )}
              </div>
            </div>
          </AccordionContent>
        </AccordionItem>

        {/* Capital Change History */}
        <AccordionItem value="capital-history" className="border border-slate-200 rounded-lg bg-white">
          <AccordionTrigger className="px-5 py-4 hover:no-underline" data-testid="section-capital-history">
            <div className="flex items-center gap-2 text-base font-semibold" style={{ fontFamily: 'Manrope' }}>
              <Activity size={18} className="text-violet-500" strokeWidth={1.5} />
              Capital History
              {currentBranch && <span className="text-[10px] font-normal px-2 py-0.5 rounded-full bg-violet-100 text-violet-700">{currentBranch.name}</span>}
              {capitalHistory.length > 0 && (
                <span className="ml-2 text-xs font-normal text-slate-400">{capitalHistory.length} changes</span>
              )}
            </div>
          </AccordionTrigger>
          <AccordionContent className="px-5 pb-5">
            {capitalHistory.length === 0 ? (
              <div className="text-center py-8 text-slate-400 text-sm">
                <Activity size={28} className="mx-auto mb-2 opacity-30" />
                No capital changes recorded yet. Changes will appear here when you receive Purchase Orders or Branch Transfers.
              </div>
            ) : (
              <div className="relative">
                {/* Timeline line */}
                <div className="absolute left-[19px] top-2 bottom-2 w-px bg-slate-200" />
                <div className="space-y-3">
                  {capitalHistory.map((entry, i) => {
                    const increased = entry.new_capital > entry.old_capital;
                    const decreased = entry.new_capital < entry.old_capital;
                    const unchanged = entry.new_capital === entry.old_capital;
                    const diff = entry.new_capital - entry.old_capital;
                    const diffPct = entry.old_capital > 0 ? Math.abs(diff / entry.old_capital * 100).toFixed(1) : null;

                    const sourceLabel = {
                      purchase_order: 'Purchase Order',
                      branch_transfer: 'Branch Transfer',
                      manual_edit: 'Manual Edit',
                    }[entry.source_type] || entry.source_type;

                    const methodLabel = {
                      last_purchase: 'Last Purchase Price',
                      moving_average: 'Moving Average',
                      transfer_capital: 'Transfer Capital',
                      manual: 'Manual',
                    }[entry.method] || entry.method;

                    const dotColor = decreased ? 'bg-amber-400' : increased ? 'bg-emerald-400' : 'bg-slate-300';

                    return (
                      <div key={entry.id || i} className="flex gap-4 relative">
                        {/* Timeline dot */}
                        <div className={`w-10 h-10 rounded-full flex items-center justify-center shrink-0 z-10 border-2 border-white shadow-sm ${
                          decreased ? 'bg-amber-50' : increased ? 'bg-emerald-50' : 'bg-slate-50'
                        }`}>
                          {decreased ? <TrendingDown size={16} className="text-amber-500" /> :
                           increased ? <TrendingUp size={16} className="text-emerald-500" /> :
                           <Activity size={16} className="text-slate-400" />}
                        </div>
                        {/* Content */}
                        <div className={`flex-1 p-3 rounded-lg border text-sm mb-1 ${
                          decreased ? 'bg-amber-50/60 border-amber-100' :
                          increased ? 'bg-emerald-50/60 border-emerald-100' :
                          'bg-slate-50 border-slate-200'
                        }`}>
                          <div className="flex items-start justify-between gap-2 flex-wrap">
                            <div>
                              <span className="font-semibold text-slate-800">
                                ₱{entry.old_capital?.toFixed(2) ?? '–'} → ₱{entry.new_capital?.toFixed(2)}
                              </span>
                              {diffPct && !unchanged && (
                                <span className={`ml-2 text-xs font-medium ${decreased ? 'text-amber-600' : 'text-emerald-600'}`}>
                                  {decreased ? '▼' : '▲'} {diffPct}%
                                </span>
                              )}
                            </div>
                            <span className="text-[10px] text-slate-400 shrink-0">
                              {entry.changed_at ? new Date(entry.changed_at).toLocaleString('en-PH', { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''}
                            </span>
                          </div>
                          <div className="mt-1 flex flex-wrap gap-2 items-center">
                            <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
                              entry.source_type === 'purchase_order' ? 'bg-blue-100 text-blue-700' :
                              entry.source_type === 'branch_transfer' ? 'bg-violet-100 text-violet-700' :
                              'bg-slate-100 text-slate-600'
                            }`}>{sourceLabel}</span>
                            {entry.source_ref && <span className="text-[10px] font-mono text-slate-500">{entry.source_ref}</span>}
                            <span className="text-[10px] text-slate-400">{methodLabel}</span>
                            {entry.vendor && <span className="text-[10px] text-slate-500">· {entry.vendor}</span>}
                            {entry.from_branch && <span className="text-[10px] text-slate-500">· {entry.from_branch} → {entry.to_branch}</span>}
                          </div>
                          <div className="mt-1 text-[10px] text-slate-400">by {entry.changed_by_name}</div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </AccordionContent>
        </AccordionItem>

        {/* Branch Pricing — Admin/Owner only */}
        {isAdmin && (
          <AccordionItem value="branch-pricing" className="border border-slate-200 rounded-lg bg-white">
            <AccordionTrigger className="px-5 py-4 hover:no-underline" data-testid="section-branch-pricing">
              <div className="flex items-center gap-2 text-base font-semibold" style={{ fontFamily: 'Manrope' }}>
                <Tags size={18} className="text-violet-600" strokeWidth={1.5} />
                Branch Pricing
                {currentBranch ? (
                  <span className="text-xs font-normal px-2 py-0.5 rounded-full bg-violet-100 text-violet-700">{currentBranch.name}</span>
                ) : (
                  <span className="text-xs font-normal text-slate-400 ml-1">
                    ({Object.keys(branchOverrides).length} custom / {branches.length} branches)
                  </span>
                )}
              </div>
            </AccordionTrigger>
            <AccordionContent className="px-5 pb-5">
              {currentBranch ? (
                /* ── Single-branch view ── */
                (() => {
                  const b = currentBranch;
                  const override = branchOverrides[b.id];
                  const isEditing = branchPriceEdit?.branch_id === b.id;
                  return (
                    <div className="space-y-4">
                      <p className="text-xs text-slate-500">
                        Prices specific to <strong>{b.name}</strong>. Editing here sets a branch-only override without affecting other branches.
                      </p>
                      {/* Price tiers */}
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                        {schemes.map(s => {
                          const overrideVal = override?.prices?.[s.key];
                          const globalVal = product.prices?.[s.key];
                          const displayVal = overrideVal !== undefined ? overrideVal : globalVal;
                          return (
                            <div key={s.id} className={`p-3 rounded-lg border ${overrideVal !== undefined ? 'bg-violet-50 border-violet-200' : 'bg-slate-50 border-slate-100'}`}>
                              <p className="text-xs text-slate-500 font-medium mb-1">{s.name}</p>
                              {isEditing ? (
                                <CalcInput className="h-8 text-sm font-bold"
 value={branchPriceEdit.prices[s.key] ?? ''}
 onChange={(v) => setBranchPriceEdit(prev => ({
 ...prev,
 prices: { ...prev.prices, [s.key]: parseFloat(v) || 0 }
 }))}
 data-testid={`branch-price-${b.id}-${s.key}`} />
                              ) : (
                                <>
                                  <p className={`text-lg font-bold ${overrideVal !== undefined ? 'text-violet-700' : ''}`} style={{ fontFamily: 'Manrope' }}>
                                    {formatPHP(displayVal)}
                                  </p>
                                  {overrideVal !== undefined ? (
                                    <p className="text-[10px] text-violet-500 mt-0.5">Branch override</p>
                                  ) : (
                                    <p className="text-[10px] text-slate-400 mt-0.5">Global default</p>
                                  )}
                                </>
                              )}
                            </div>
                          );
                        })}
                      </div>
                      {/* Cost (Landed) */}
                      <div className={`p-3 rounded-lg border flex items-center justify-between ${override?.cost_price !== undefined ? 'bg-amber-50 border-amber-200' : 'bg-slate-50 border-slate-100'}`}>
                        <div>
                          <p className="text-xs text-slate-500 font-medium">Cost / Capital (Landed)</p>
                          {isEditing ? (
                            <CalcInput className="h-8 w-36 text-sm font-bold mt-1"
 placeholder="Use global"
 value={branchPriceEdit.cost_price ?? ''}
 onChange={(v) => setBranchPriceEdit(prev => ({
 ...prev, cost_price: parseFloat(v) || null
 }))} />
                          ) : (
                            <>
                              <p className={`text-lg font-bold mt-0.5 ${override?.cost_price !== undefined ? 'text-amber-700' : ''}`} style={{ fontFamily: 'Manrope' }}>
                                {override?.cost_price !== undefined ? formatPHP(override.cost_price) : '—'}
                              </p>
                              {override?.cost_price !== undefined ? (
                                <p className="text-[10px] text-amber-600">Branch cost override</p>
                              ) : (
                                <p className="text-[10px] text-slate-400">Using global cost: {formatPHP(product.cost_price)}</p>
                              )}
                            </>
                          )}
                        </div>
                        <Badge className={`text-[10px] ${override ? 'bg-violet-100 text-violet-700' : 'bg-slate-100 text-slate-500'} border-0`}>
                          {override ? 'Custom' : 'Default'}
                        </Badge>
                      </div>
                      {/* Edit / Save actions */}
                      <div className="flex items-center gap-2 pt-1">
                        {isEditing ? (
                          <>
                            <Button size="sm" className="bg-[#1A4D2E] hover:bg-[#14532d] text-white" onClick={handleSaveBranchPrice} disabled={savingBranchPrice}>
                              <Save size={13} className="mr-1" /> Save Branch Prices
                            </Button>
                            <Button size="sm" variant="outline" onClick={() => setBranchPriceEdit(null)}>Cancel</Button>
                          </>
                        ) : (
                          <>
                            <Button
                              size="sm" variant="outline" className="text-violet-600 border-violet-200 hover:bg-violet-50"
                              onClick={() => setBranchPriceEdit({
                                branch_id: b.id,
                                prices: { ...(override?.prices || product.prices || {}) },
                                cost_price: override?.cost_price ?? null,
                              })}
                              data-testid={`edit-branch-price-${b.id}`}
                            >
                              <Pencil size={13} className="mr-1" /> Edit {b.name} Prices
                            </Button>
                            {override && (
                              <Button
                                size="sm" variant="ghost" className="text-red-500 hover:text-red-600"
                                onClick={() => handleRemoveBranchOverride(b.id)}
                              >
                                <Trash2 size={13} className="mr-1" /> Remove Override
                              </Button>
                            )}
                          </>
                        )}
                      </div>
                    </div>
                  );
                })()
              ) : (
                /* ── All-branches table view ── */
                <>
                  <p className="text-xs text-slate-500 mb-3">
                    Set branch-specific prices. Branches without an override use the global default prices above.
                  </p>
                  <Table>
                    <TableHeader>
                      <TableRow className="bg-slate-50">
                        <TableHead className="text-xs">Branch</TableHead>
                        {schemes.map(s => (
                          <TableHead key={s.id} className="text-xs text-right">{s.name}</TableHead>
                        ))}
                        <TableHead className="text-xs text-right">Cost (Landed)</TableHead>
                        <TableHead className="text-xs w-24">Override</TableHead>
                        <TableHead className="w-20"></TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {branches.map(b => {
                        const override = branchOverrides[b.id];
                        const isEditing = branchPriceEdit?.branch_id === b.id;
                        return (
                          <TableRow key={b.id} className={override ? 'bg-violet-50/50' : ''}>
                            <TableCell className="font-medium text-sm">{b.name}</TableCell>
                            {schemes.map(s => (
                              <TableCell key={s.id} className="text-right">
                                {isEditing ? (
                                  <CalcInput className="w-24 h-7 text-right text-xs"
 value={branchPriceEdit.prices[s.key] ?? ''}
 onChange={(v) => setBranchPriceEdit(prev => ({
 ...prev,
 prices: { ...prev.prices, [s.key]: parseFloat(v) || 0 }
 }))}
 data-testid={`branch-price-${b.id}-${s.key}`} />
                                ) : (
                                  <span className={`font-mono text-sm ${override?.prices?.[s.key] !== undefined ? 'text-violet-700 font-semibold' : 'text-slate-400'}`}>
                                    {override?.prices?.[s.key] !== undefined
                                      ? formatPHP(override.prices[s.key])
                                      : formatPHP(product.prices?.[s.key])}
                                  </span>
                                )}
                              </TableCell>
                            ))}
                            <TableCell className="text-right">
                              {isEditing ? (
                                <CalcInput className="w-24 h-7 text-right text-xs"
 placeholder="Global"
 value={branchPriceEdit.cost_price ?? ''}
 onChange={(v) => setBranchPriceEdit(prev => ({
 ...prev, cost_price: parseFloat(v) || null
 }))} />
                              ) : (
                                <span className={`font-mono text-sm ${override?.cost_price !== undefined ? 'text-violet-700 font-semibold' : 'text-slate-400'}`}>
                                  {override?.cost_price !== undefined ? formatPHP(override.cost_price) : '—'}
                                </span>
                              )}
                            </TableCell>
                            <TableCell>
                              {override ? (
                                <Badge className="text-[9px] bg-violet-100 text-violet-700 border-0">Custom</Badge>
                              ) : (
                                <Badge className="text-[9px] bg-slate-100 text-slate-500 border-0">Default</Badge>
                              )}
                            </TableCell>
                            <TableCell>
                              {isEditing ? (
                                <div className="flex gap-1">
                                  <Button size="sm" className="h-7 px-2 text-xs bg-[#1A4D2E]" onClick={handleSaveBranchPrice} disabled={savingBranchPrice}>
                                    Save
                                  </Button>
                                  <Button size="sm" variant="ghost" className="h-7 px-2 text-xs" onClick={() => setBranchPriceEdit(null)}>
                                    Cancel
                                  </Button>
                                </div>
                              ) : (
                                <div className="flex gap-1">
                                  <Button
                                    size="sm" variant="ghost" className="h-7 px-2 text-xs text-violet-600 hover:text-violet-700"
                                    onClick={() => setBranchPriceEdit({
                                      branch_id: b.id,
                                      prices: { ...(override?.prices || product.prices || {}) },
                                      cost_price: override?.cost_price ?? null,
                                    })}
                                    data-testid={`edit-branch-price-${b.id}`}
                                  >
                                    <Pencil size={11} />
                                  </Button>
                                  {override && (
                                    <Button
                                      size="sm" variant="ghost" className="h-7 px-2 text-xs text-red-500 hover:text-red-600"
                                      onClick={() => handleRemoveBranchOverride(b.id)}
                                      title="Remove override — revert to global"
                                    >
                                      <Trash2 size={11} />
                                    </Button>
                                  )}
                                </div>
                              )}
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </>
              )}
            </AccordionContent>
          </AccordionItem>
        )}

        {/* Inventory */}
        <AccordionItem value="inventory" className="border border-slate-200 rounded-lg bg-white">
          <AccordionTrigger className="px-5 py-4 hover:no-underline" data-testid="section-inventory">
            <div className="flex items-center gap-2 text-base font-semibold" style={{ fontFamily: 'Manrope' }}>
              <Warehouse size={18} className="text-blue-600" strokeWidth={1.5} /> Inventory
            </div>
          </AccordionTrigger>
          <AccordionContent className="px-5 pb-5">
            <div className="grid md:grid-cols-2 gap-6">
              <div>
                <h4 className="text-sm font-semibold mb-2">Stock by Branch</h4>
                <Table>
                  <TableHeader><TableRow><TableHead className="text-xs">Branch</TableHead><TableHead className="text-xs text-right">On Hand</TableHead></TableRow></TableHeader>
                  <TableBody>
                    {branches.map(b => (
                      <TableRow key={b.id} className={b.id === currentBranch?.id ? 'bg-emerald-50' : ''}>
                        <TableCell className="text-sm">{b.name} {b.id === currentBranch?.id && <Badge className="ml-1 text-[9px] bg-emerald-100 text-emerald-700">Current</Badge>}</TableCell>
                        <TableCell className="text-right font-mono font-semibold">{(inventory.on_hand[b.id] || 0).toFixed(2)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              <div className="space-y-3">
                <div className="p-3 rounded-lg border border-blue-200 bg-blue-50">
                  <p className="text-xs text-blue-600 font-medium">Coming Inventory (On Order)</p>
                  <p className="text-2xl font-bold text-blue-800">{inventory.coming} <span className="text-sm font-normal">{product.unit}</span></p>
                  <p className="text-[11px] text-blue-500">From pending purchase orders</p>
                </div>
                <div className="p-3 rounded-lg border border-amber-200 bg-amber-50">
                  <p className="text-xs text-amber-600 font-medium">Reserved (Pending Delivery)</p>
                  <p className="text-2xl font-bold text-amber-800">{inventory.reserved} <span className="text-sm font-normal">{product.unit}</span></p>
                  <p className="text-[11px] text-amber-500">From sales not yet released</p>
                </div>
                <div className="p-3 rounded-lg border border-slate-200">
                  <p className="text-xs text-slate-500 font-medium">Available ({currentBranch?.name || 'Total'} — On Hand minus Reserved)</p>
                  <p className="text-2xl font-bold">{Math.max(0, branchStock - inventory.reserved).toFixed(2)} <span className="text-sm font-normal">{product.unit}</span></p>
                </div>
              </div>
            </div>
          </AccordionContent>
        </AccordionItem>

        {/* Extra Info */}
        <AccordionItem value="extra" className="border border-slate-200 rounded-lg bg-white">
          <AccordionTrigger className="px-5 py-4 hover:no-underline" data-testid="section-extra">
            <div className="flex items-center gap-2 text-base font-semibold" style={{ fontFamily: 'Manrope' }}>
              <Info size={18} className="text-slate-500" strokeWidth={1.5} /> Extra Information
            </div>
          </AccordionTrigger>
          <AccordionContent className="px-5 pb-5">
            <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
              {[
                { label: 'Barcode', key: 'barcode', placeholder: 'Scan or enter' },
                { label: 'Reorder Point', key: 'reorder_point', type: 'number' },
                { label: 'Reorder Quantity', key: 'reorder_quantity', type: 'number' },
                { label: 'Last Vendor', key: 'last_vendor' },
                { label: 'Unit of Measurement', key: 'unit_of_measurement', placeholder: 'Pack, Box, Bag...' },
              ].map(f => (
                <div key={f.key}>
                  <Label className="text-xs text-slate-500">{f.label}</Label>
                  {editMode ? (
                    <Input type={f.type || 'text'} value={editForm[f.key] || ''} placeholder={f.placeholder}
                      onChange={e => setEditForm({ ...editForm, [f.key]: f.type === 'number' ? parseFloat(e.target.value) || 0 : e.target.value })} className="h-9" />
                  ) : (
                    <p className="font-medium text-sm mt-1">{product[f.key] || '—'}</p>
                  )}
                </div>
              ))}
            </div>
          </AccordionContent>
        </AccordionItem>

        {/* Product Vendors */}
        <AccordionItem value="vendors" className="border border-slate-200 rounded-lg bg-white">
          <AccordionTrigger className="px-5 py-4 hover:no-underline" data-testid="section-vendors">
            <div className="flex items-center gap-2 text-base font-semibold" style={{ fontFamily: 'Manrope' }}>
              <Users size={18} className="text-violet-600" strokeWidth={1.5} /> Product Vendors ({vendors.length})
              {currentBranch && <span className="text-[10px] font-normal px-2 py-0.5 rounded-full bg-violet-100 text-violet-700">{currentBranch.name}</span>}
            </div>
          </AccordionTrigger>
          <AccordionContent className="px-5 pb-5">
            <Button size="sm" onClick={() => { setVendorForm({ supplier_id: '', last_price: 0, is_preferred: false }); fetchBranchSuppliers(); setVendorDialog(true); }}
              className="mb-3 bg-[#1A4D2E] hover:bg-[#14532d] text-white" data-testid="add-vendor-btn">
              <Plus size={14} className="mr-1" /> Add Vendor
            </Button>
            {vendors.length > 0 ? (
              <Table>
                <TableHeader><TableRow className="bg-slate-50">
                  <TableHead className="text-xs uppercase text-slate-500">Vendor</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500">Contact</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500 text-right">Last Price</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500">Last Order</TableHead>
                  <TableHead className="w-16"></TableHead>
                </TableRow></TableHeader>
                <TableBody>
                  {vendors.map(v => (
                    <TableRow key={v.id}>
                      <TableCell className="font-medium">{v.vendor_name} {v.is_preferred && <Badge className="ml-1 text-[9px] bg-emerald-100 text-emerald-700">Preferred</Badge>}</TableCell>
                      <TableCell className="text-sm text-slate-500">{v.vendor_contact || '—'}</TableCell>
                      <TableCell className="text-right font-semibold">{formatPHP(v.last_price)}</TableCell>
                      <TableCell className="text-sm text-slate-500">{v.last_order_date || '—'}</TableCell>
                      <TableCell><Button variant="ghost" size="sm" onClick={() => removeVendor(v.id)} className="text-red-500"><Trash2 size={12} /></Button></TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : <p className="text-sm text-slate-400">No vendors added yet</p>}
          </AccordionContent>
        </AccordionItem>

        {/* Movement History */}
        <AccordionItem value="movements" className="border border-slate-200 rounded-lg bg-white">
          <AccordionTrigger className="px-5 py-4 hover:no-underline" data-testid="section-movements" onClick={() => { if (!movements.length) fetchMovements(); }}>
            <div className="flex items-center gap-2 text-base font-semibold" style={{ fontFamily: 'Manrope' }}>
              <History size={18} className="text-slate-500" strokeWidth={1.5} /> Movement History
              {currentBranch && <span className="text-[10px] font-normal px-2 py-0.5 rounded-full bg-violet-100 text-violet-700">{currentBranch.name}</span>}
            </div>
          </AccordionTrigger>
          <AccordionContent className="px-5 pb-5">
            {movements.length > 0 ? (
              <div>
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs text-slate-500">Showing {movements.length} of {totalMovements} movements</span>
                </div>
                <Table>
                <TableHeader><TableRow className="bg-slate-50">
                  <TableHead className="text-xs uppercase text-slate-500">Date</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500">Type</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500">Ref</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500 text-right">Qty</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500 text-right">Price</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500">By</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500">Notes</TableHead>
                </TableRow></TableHeader>
                <TableBody>
                  {movements.map(m => (
                    <TableRow key={m.id}>
                      <TableCell className="text-xs">{new Date(m.created_at).toLocaleDateString()}</TableCell>
                      <TableCell><Badge className={`text-[10px] ${m.type === 'sale' ? 'bg-red-100 text-red-700' : m.type === 'purchase' ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-700'}`}>{m.type}</Badge></TableCell>
                      <TableCell className="font-mono text-xs">{m.reference_number}</TableCell>
                      <TableCell className={`text-right font-semibold ${m.quantity_change < 0 ? 'text-red-600' : 'text-emerald-600'}`}>{m.quantity_change > 0 ? '+' : ''}{m.quantity_change}</TableCell>
                      <TableCell className="text-right">{formatPHP(m.price_at_time)}</TableCell>
                      <TableCell className="text-xs text-slate-500">{m.user_name}</TableCell>
                      <TableCell className="text-xs text-slate-400">{m.notes}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              {hasMoreMovements && (
                <div className="flex justify-center mt-3">
                  <Button variant="outline" size="sm" className="text-xs"
                    data-testid="load-more-movements"
                    onClick={() => {
                      const newLimit = movementLimit + 100;
                      setMovementLimit(newLimit);
                      fetchMovements(newLimit);
                    }}>
                    Load more ({totalMovements - movements.length} remaining)
                  </Button>
                </div>
              )}
              </div>
            ) : <p className="text-sm text-slate-400">No movement history. Click to load.</p>}
          </AccordionContent>
        </AccordionItem>

        {/* Order History */}
        <AccordionItem value="orders" className="border border-slate-200 rounded-lg bg-white">
          <AccordionTrigger className="px-5 py-4 hover:no-underline" data-testid="section-orders" onClick={() => { if (!orders.length) fetchOrders(); }}>
            <div className="flex items-center gap-2 text-base font-semibold" style={{ fontFamily: 'Manrope' }}>
              <ShoppingCart size={18} className="text-blue-500" strokeWidth={1.5} /> Order History
              {currentBranch && <span className="text-[10px] font-normal px-2 py-0.5 rounded-full bg-violet-100 text-violet-700">{currentBranch.name}</span>}
            </div>
          </AccordionTrigger>
          <AccordionContent className="px-5 pb-5">
            {orders.length > 0 ? (
              <Table>
                <TableHeader><TableRow className="bg-slate-50">
                  <TableHead className="text-xs uppercase text-slate-500">Date</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500">Type</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500">Reference</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500">Party</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500 text-right">Qty</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500 text-right">Price</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500 text-right">Total</TableHead>
                  <TableHead className="text-xs uppercase text-slate-500">Status</TableHead>
                </TableRow></TableHeader>
                <TableBody>
                  {orders.map((o, i) => (
                    <TableRow key={i}>
                      <TableCell className="text-xs">{new Date(o.date).toLocaleDateString()}</TableCell>
                      <TableCell><Badge className={`text-[10px] ${o.type === 'sale' ? 'bg-blue-100 text-blue-700' : 'bg-emerald-100 text-emerald-700'}`}>{o.type}</Badge></TableCell>
                      <TableCell className="font-mono text-xs">{o.reference}</TableCell>
                      <TableCell className="text-sm">{o.party}</TableCell>
                      <TableCell className="text-right">{o.quantity}</TableCell>
                      <TableCell className="text-right">{formatPHP(o.price)}</TableCell>
                      <TableCell className="text-right font-semibold">{formatPHP(o.total)}</TableCell>
                      <TableCell><Badge variant="outline" className="text-[10px]">{o.status}</Badge></TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : <p className="text-sm text-slate-400">No order history. Click to load.</p>}
          </AccordionContent>
        </AccordionItem>
      </Accordion>

      {/* Repack Dialog */}
      <Dialog open={repackDialog} onOpenChange={setRepackDialog}>
        <DialogContent className="sm:max-w-lg max-h-[85vh] overflow-y-auto">
          <DialogHeader><DialogTitle style={{ fontFamily: 'Manrope' }}>Generate Repack</DialogTitle></DialogHeader>
          <div className="space-y-4 mt-2">
            <div className="p-3 bg-emerald-50 rounded-lg border border-emerald-200 text-sm">
              <p className="font-semibold text-emerald-800">Parent: {product.name}</p>
              <p className="text-emerald-600 text-xs">SKU: {product.sku} | Unit: {product.unit}</p>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div><Label>Repack Name</Label><Input data-testid="repack-name" value={repackForm.name} onChange={e => setRepackForm({ ...repackForm, name: e.target.value })} /></div>
              <div><Label>Unit</Label><Input data-testid="repack-unit" value={repackForm.unit} onChange={e => setRepackForm({ ...repackForm, unit: e.target.value })} placeholder="Pack, Sachet, Piece" /></div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div><Label>Units per {product.unit}</Label><CalcInput data-testid="repack-per-parent" value={repackForm.units_per_parent} onChange={(v) => {
 const units = parseInt(v) || 1;
 const autoCost = product.cost_price ? Math.round((product.cost_price / units + (repackForm.add_on_cost || 0)) * 100) / 100 : 0;
 setRepackForm({ ...repackForm, units_per_parent: units, cost_price: autoCost });
 }} /></div>
              <div><Label>Add-on Cost</Label><CalcInput value={repackForm.add_on_cost || 0} onChange={(v) => {
 const addon = parseFloat(v) || 0;
 const autoCost = Math.round((product.cost_price / (repackForm.units_per_parent || 1) + addon) * 100) / 100;
 setRepackForm({ ...repackForm, add_on_cost: addon, cost_price: autoCost });
 }} /></div>
            </div>
            <div className="p-2 bg-blue-50 rounded border border-blue-200 text-sm">
              <span className="text-blue-700">Auto Cost: ₱{(product.cost_price / (repackForm.units_per_parent || 1)).toFixed(2)} ÷ {repackForm.units_per_parent}
              {(repackForm.add_on_cost || 0) > 0 && ` + ₱${repackForm.add_on_cost.toFixed(2)}`}
              {' '}= <b className="text-emerald-700">₱{repackForm.cost_price.toFixed(2)}</b></span>
            </div>
            <div><Label className="font-semibold">Prices</Label>
              <div className="grid grid-cols-2 gap-3 mt-2">
                {schemes.map(s => (
                  <div key={s.id}><Label className="text-xs text-slate-500">{s.name}</Label>
                    <CalcInput value={repackForm.prices[s.key] || ''} onChange={(v) => updateRepackPrice(s.key, v)} placeholder="0.00" /></div>
                ))}
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setRepackDialog(false)}>Cancel</Button>
              <Button data-testid="generate-repack-confirm" onClick={handleRepack} className="bg-[#D97706] hover:bg-[#b45309] text-white"><Link2 size={14} className="mr-1" /> Generate</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Vendor Dialog — Supplier Picker */}
      <Dialog open={vendorDialog} onOpenChange={setVendorDialog}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader><DialogTitle style={{ fontFamily: 'Manrope' }}>Link Supplier to Product</DialogTitle></DialogHeader>
          <div className="space-y-4 mt-2">
            <div>
              <Label>Supplier {currentBranch && <span className="text-[10px] text-violet-600">({currentBranch.name})</span>}</Label>
              {branchSuppliers.length > 0 ? (
                <Select value={vendorForm.supplier_id} onValueChange={v => setVendorForm({ ...vendorForm, supplier_id: v })}>
                  <SelectTrigger data-testid="vendor-supplier-select"><SelectValue placeholder="Select supplier..." /></SelectTrigger>
                  <SelectContent>
                    {branchSuppliers.map(s => (
                      <SelectItem key={s.id} value={s.id}>
                        {s.name} {s.phone ? `· ${s.phone}` : ''}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ) : (
                <p className="text-sm text-slate-400 mt-1">No suppliers found for this branch. Add suppliers first in the Suppliers page.</p>
              )}
            </div>
            <div><Label>Last Price</Label><CalcInput data-testid="vendor-last-price" value={vendorForm.last_price} onChange={(v) => setVendorForm({ ...vendorForm, last_price: parseFloat(v) || 0 })} /></div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setVendorDialog(false)}>Cancel</Button>
              <Button data-testid="save-vendor" onClick={handleAddVendor} disabled={!vendorForm.supplier_id} className="bg-[#1A4D2E] hover:bg-[#14532d] text-white">Link Supplier</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
