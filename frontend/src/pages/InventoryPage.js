import { useState, useEffect, useCallback } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAuth, api } from '../contexts/AuthContext';
import { formatPHP } from '../lib/utils';
import { Card, CardContent } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Badge } from '../components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table';
import { Search, AlertTriangle } from 'lucide-react';
import { toast } from 'sonner';

export default function InventoryPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { currentBranch } = useAuth();
  const [items, setItems] = useState([]);
  const [schemes, setSchemes] = useState([]);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState('');
  const [lowStock, setLowStock] = useState(false);
  const [sortBy, setSortBy] = useState('grouped'); // "name" | "type" | "grouped"
  const [page, setPage] = useState(0);
  const LIMIT = 30;

  const fetchInventory = useCallback(async () => {
    try {
      const params = { skip: page * LIMIT, limit: LIMIT, sort_by: sortBy };
      if (currentBranch) params.branch_id = currentBranch.id;
      if (search) params.search = search;
      if (lowStock) params.low_stock = true;
      const res = await api.get('/inventory', { params });
      setItems(res.data.items);
      setTotal(res.data.total);
    } catch { toast.error('Failed to load inventory'); }
  }, [currentBranch, search, lowStock, sortBy, page]);

  // Re-fetch whenever user navigates to this page (location.key changes on every navigation)
  useEffect(() => { fetchInventory(); }, [location.key]); // eslint-disable-line
  useEffect(() => { fetchInventory(); }, [fetchInventory]);
  useEffect(() => {
    api.get('/price-schemes').then(r => setSchemes(r.data || [])).catch(() => {});
  }, []);

  const totalPages = Math.ceil(total / LIMIT);

  return (
    <div className="space-y-6 animate-fadeIn" data-testid="inventory-page">
      <div>
        <h1 className="text-2xl font-bold tracking-tight" style={{ fontFamily: 'Manrope' }}>Inventory</h1>
        <p className="text-sm text-slate-500 mt-1">Stock levels for {currentBranch?.name || 'all branches'} &middot; Click a product for full details</p>
      </div>

      <div className="flex flex-col sm:flex-row gap-3 flex-wrap">
        <div className="relative flex-1 min-w-[200px]">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <Input data-testid="inventory-search" value={search} onChange={e => { setSearch(e.target.value); setPage(0); }} placeholder="Search products..." className="pl-9 h-10" />
        </div>
        <Button variant={lowStock ? "default" : "outline"} onClick={() => { setLowStock(!lowStock); setPage(0); }}
          data-testid="low-stock-filter" className={lowStock ? "bg-amber-500 hover:bg-amber-600 text-white" : ""}>
          <AlertTriangle size={14} className="mr-2" /> Low Stock
        </Button>
        <Select value={sortBy} onValueChange={v => { setSortBy(v); setPage(0); }}>
          <SelectTrigger data-testid="inventory-sort" className="w-[200px] h-10">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="name">Sort: Name (A–Z)</SelectItem>
            <SelectItem value="type">Sort: Type (Parents first)</SelectItem>
            <SelectItem value="grouped">Sort: Grouped (Repacks below parent)</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <Card className="border-slate-200">
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow className="bg-slate-50">
                <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">Product</TableHead>
                <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">SKU</TableHead>
                <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">Category</TableHead>
                <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">Type</TableHead>
                <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium text-right">On Hand</TableHead>
                <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium text-right">Total (All)</TableHead>
                {schemes.map(s => (
                  <TableHead key={s.key} className="text-xs uppercase tracking-wider text-slate-500 font-medium text-right capitalize">{s.name}</TableHead>
                ))}
                <TableHead className="text-xs uppercase tracking-wider text-slate-500 font-medium">Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map(item => {
                const branchQty = currentBranch 
                  ? (item.derived_from_parent ? item.total_stock : (item.branch_stock?.[currentBranch.id] || 0)) 
                  : item.total_stock;
                const isNegative = branchQty < 0;
                const isLow = branchQty <= (item.reorder_point || 10) && branchQty > 0;
                const isOut = branchQty === 0;
                const isGrouped = sortBy === 'grouped';
                return (
                  <TableRow key={item.id}
                    className={`cursor-pointer transition-colors hover:bg-slate-50 ${isGrouped && item.is_repack ? 'bg-amber-50/30' : ''} ${isNegative ? 'bg-red-50/40' : ''}`}
                    onClick={() => navigate(`/products/${item.id}`)} data-testid={`inv-row-${item.id}`}>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        {item.is_repack && (
                          <span className={`shrink-0 ${isGrouped ? 'w-5 h-4 border-l-2 border-b-2 border-amber-300 ml-1' : 'w-4 border-l-2 border-b-2 border-slate-300 h-3'}`} />
                        )}
                        <div>
                          <span className={`font-medium ${item.is_repack && isGrouped ? 'text-sm text-slate-700' : ''}`}>{item.name}</span>
                          {item.derived_from_parent && item.parent_name && (
                            <div className="text-[10px] text-slate-400">
                              From: {item.parent_name} ({item.parent_stock?.toFixed(2)} {item.parent_unit})
                            </div>
                          )}
                        </div>
                      </div>
                    </TableCell>
                    <TableCell className="font-mono text-xs">{item.sku}</TableCell>
                    <TableCell className="text-sm text-slate-500">{item.category}</TableCell>
                    <TableCell>
                      {item.is_repack ? (
                        <Badge variant="outline" className="text-[10px] border-amber-300 text-amber-700 bg-amber-50">
                          Repack {item.units_per_parent && `(×${item.units_per_parent})`}
                        </Badge>
                      ) : item.product_type === 'service' ? (
                        <Badge variant="outline" className="text-[10px] border-blue-300 text-blue-700 bg-blue-50">Service</Badge>
                      ) : (
                        <Badge variant="outline" className="text-[10px] border-emerald-300 text-emerald-700 bg-emerald-50">Stockable</Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      <span className={`font-semibold ${isNegative ? 'text-red-700' : isOut ? 'text-red-600' : isLow ? 'text-amber-600' : ''}`}>
                        {branchQty.toFixed(2)} {item.unit}
                      </span>
                      {item.derived_from_parent && (
                        <div className="text-[10px] text-slate-400 italic">derived</div>
                      )}
                    </TableCell>
                    <TableCell className="text-right text-slate-500">{item.total_stock?.toFixed(2)}</TableCell>
                    {schemes.map(s => (
                      <TableCell key={s.key} className="text-right font-mono text-sm">
                        {item.prices?.[s.key] > 0 ? formatPHP(item.prices[s.key]) : <span className="text-slate-300 text-xs">—</span>}
                      </TableCell>
                    ))}
                    <TableCell>
                      {isNegative ? <Badge className="bg-red-200 text-red-800 text-[10px]">Negative — Investigate</Badge>
                        : isOut ? <Badge className="bg-red-100 text-red-700 text-[10px]">Out of Stock</Badge>
                        : isLow ? <Badge className="bg-amber-100 text-amber-700 text-[10px]">Low</Badge>
                        : <Badge className="bg-emerald-100 text-emerald-700 text-[10px]">In Stock</Badge>}
                    </TableCell>
                  </TableRow>
                );
              })}
              {!items.length && (
                <TableRow><TableCell colSpan={7 + schemes.length} className="text-center py-8 text-slate-400">No inventory data</TableCell></TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <p className="text-xs text-slate-500">Page {page + 1} of {totalPages}</p>
          <div className="flex gap-1">
            <Button variant="outline" size="sm" disabled={page === 0} onClick={() => setPage(page - 1)}>Prev</Button>
            <Button variant="outline" size="sm" disabled={page >= totalPages - 1} onClick={() => setPage(page + 1)}>Next</Button>
          </div>
        </div>
      )}
    </div>
  );
}



