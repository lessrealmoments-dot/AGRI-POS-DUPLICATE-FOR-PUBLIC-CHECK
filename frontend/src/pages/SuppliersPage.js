import { useState, useEffect } from 'react';
import { api, useAuth } from '../contexts/AuthContext';
import { formatPHP, fmtDate } from '../lib/utils';
import { Card, CardContent } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Badge } from '../components/ui/badge';
import { Separator } from '../components/ui/separator';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../components/ui/dialog';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/tabs';
import { Search, Truck, FileText, DollarSign, ArrowRight, CheckCircle, AlertCircle, History, Plus, Edit2, Phone, Mail, MapPin, Clock } from 'lucide-react';
import { toast } from 'sonner';
import ReviewDetailDialog from '../components/ReviewDetailDialog';
import HistoricalSupplierPODialog from '../components/HistoricalSupplierPODialog';

export default function SuppliersPage() {
  const { currentBranch, hasPerm, user } = useAuth();
  const canCreateSupplier = hasPerm('suppliers', 'create');
  const canEditSupplier = hasPerm('suppliers', 'edit');
  // Historical Supplier PO is admin/owner only (matches dashboard AP widget gate).
  const canAddOldBalance = ['admin', 'owner', 'super_admin'].includes(user?.role || '');
  const [historicalDialog, setHistoricalDialog] = useState(false);
  const [historicalDefaultTab, setHistoricalDefaultTab] = useState('list');
  const [vendors, setVendors] = useState([]);
  const [suppliers, setSuppliers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [selectedVendor, setSelectedVendor] = useState(null);
  const [selectedSupplierDetails, setSelectedSupplierDetails] = useState(null);
  const [vendorPOs, setVendorPOs] = useState([]);
  const [vendorStats, setVendorStats] = useState(null);
  const [detailPO, setDetailPO] = useState(null);
  const [detailDialog, setDetailDialog] = useState(false);
  const [invoiceModalOpen, setInvoiceModalOpen] = useState(false);
  const [selectedInvoiceNumber, setSelectedInvoiceNumber] = useState(null);
  const openDetailModal = (num) => { setSelectedInvoiceNumber(num); setInvoiceModalOpen(true); };
  const [activeTab, setActiveTab] = useState('all');
  
  // New supplier dialog
  const [supplierDialog, setSupplierDialog] = useState(false);
  const [editMode, setEditMode] = useState(false);
  const [supplierForm, setSupplierForm] = useState({
    name: '', contact_person: '', phone: '', email: '', address: '', notes: ''
  });
  const [saving, setSaving] = useState(false);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    fetchData();
  }, [currentBranch]);

  const fetchData = async () => {
    setLoading(true);
    try {
      const params = currentBranch ? { branch_id: currentBranch.id } : {};
      const [vendorsRes, suppliersRes] = await Promise.all([
        api.get('/purchase-orders/vendors'),
        api.get('/suppliers', { params })
      ]);
      setVendors(vendorsRes.data);
      setSuppliers(suppliersRes.data);
    } catch (e) {
      toast.error('Failed to load suppliers');
    }
    setLoading(false);
  };

  // Combine vendors from POs and suppliers collection
  const allSupplierNames = [...new Set([
    ...vendors,
    ...suppliers.map(s => s.name)
  ])].sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));

  const selectVendor = async (vendor) => {
    setSelectedVendor(vendor);
    // Find supplier details if exists
    const supplierDetail = suppliers.find(s => s.name.toLowerCase() === vendor.toLowerCase());
    setSelectedSupplierDetails(supplierDetail || null);
    
    try {
      const res = await api.get('/purchase-orders/by-vendor', { params: { vendor } });
      setVendorPOs(res.data);
      
      // Calculate stats
      const stats = {
        totalPOs: res.data.length,
        totalPurchased: res.data.reduce((sum, po) => sum + (po.subtotal || 0), 0),
        totalPaid: res.data.reduce((sum, po) => sum + (po.amount_paid || 0), 0),
        pendingPayment: res.data.reduce((sum, po) => sum + (po.balance || (po.payment_status !== 'paid' ? po.subtotal : 0) || 0), 0),
        unpaidPOs: res.data.filter(po => po.payment_status !== 'paid').length,
        receivedPOs: res.data.filter(po => po.status === 'received').length,
        orderedPOs: res.data.filter(po => po.status === 'ordered').length,
      };
      setVendorStats(stats);
    } catch {
      setVendorPOs([]);
      setVendorStats(null);
    }
  };

  const openNewSupplier = () => {
    setEditMode(false);
    setSupplierForm({ name: '', contact_person: '', phone: '', email: '', address: '', notes: '' });
    setSupplierDialog(true);
  };

  const openEditSupplier = (supplier) => {
    setEditMode(true);
    setSupplierForm({
      id: supplier.id,
      name: supplier.name || '',
      contact_person: supplier.contact_person || '',
      phone: supplier.phone || '',
      email: supplier.email || '',
      address: supplier.address || '',
      notes: supplier.notes || '',
    });
    setSupplierDialog(true);
  };

  const saveSupplier = async () => {
    if (!supplierForm.name.trim()) {
      toast.error('Supplier name is required');
      return;
    }
    setSaving(true);
    try {
      const payload = { ...supplierForm };
      if (currentBranch) payload.branch_id = currentBranch.id;
      if (editMode && supplierForm.id) {
        await api.put(`/suppliers/${supplierForm.id}`, payload);
        toast.success('Supplier updated');
      } else {
        await api.post('/suppliers', payload);
        toast.success('Supplier created');
      }
      setSupplierDialog(false);
      fetchData();
      if (selectedVendor) selectVendor(selectedVendor);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to save supplier');
    }
    setSaving(false);
  };

  const filteredVendors = search
    ? allSupplierNames.filter(v => v.toLowerCase().includes(search.toLowerCase()))
    : allSupplierNames;

  const filteredPOs = activeTab === 'all'
    ? vendorPOs
    : activeTab === 'unpaid'
    ? vendorPOs.filter(po => po.payment_status !== 'paid')
    : activeTab === 'pending'
    ? vendorPOs.filter(po => po.status === 'ordered')
    : vendorPOs;

  const statusColor = (s) => {
    if (s === 'received') return 'bg-emerald-100 text-emerald-700';
    if (s === 'ordered') return 'bg-blue-100 text-blue-700';
    if (s === 'cancelled') return 'bg-red-100 text-red-700';
    return 'bg-slate-100 text-slate-700';
  };

  const paymentColor = (s) => {
    if (s === 'paid') return 'bg-emerald-100 text-emerald-700';
    if (s === 'partial') return 'bg-amber-100 text-amber-700';
    return 'bg-red-100 text-red-700';
  };

  return (
    <div className="space-y-5 animate-fadeIn" data-testid="suppliers-page">
      <div>
        <h1 className="text-2xl font-bold tracking-tight" style={{ fontFamily: 'Manrope' }}>Suppliers</h1>
        <p className="text-sm text-slate-500">
          View supplier list, purchase history, and payment status
          {currentBranch && <span className="ml-2 px-2 py-0.5 rounded-full bg-violet-100 text-violet-700 text-[10px] font-medium">{currentBranch.name}</span>}
        </p>
      </div>

      <div className="grid lg:grid-cols-4 gap-5">
        {/* Vendor List Panel */}
        <Card className="border-slate-200 lg:col-span-1">
          <CardContent className="p-4 space-y-3">
            {canCreateSupplier && (
              <Button onClick={openNewSupplier} className="w-full bg-[#1A4D2E] hover:bg-[#14532d] text-white" data-testid="new-supplier-btn">
                <Plus size={14} className="mr-2" /> New Supplier
              </Button>
            )}
            <div className="relative">
              <Search size={14} className="absolute left-2 top-1/2 -translate-y-1/2 text-slate-400" />
              <Input
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="Search suppliers..."
                className="pl-8 h-9"
                data-testid="supplier-search"
              />
            </div>
            <Separator />
            <div className="max-h-[calc(100vh-320px)] overflow-y-auto space-y-0.5">
              {loading ? (
                <p className="text-xs text-slate-400 text-center py-4">Loading...</p>
              ) : filteredVendors.length === 0 ? (
                <p className="text-xs text-slate-400 text-center py-4">No suppliers found</p>
              ) : (
                filteredVendors.map(v => (
                  <button
                    key={v}
                    onClick={() => selectVendor(v)}
                    data-testid={`supplier-item-${v}`}
                    className={`w-full text-left px-3 py-2.5 text-sm rounded-lg transition-all ${
                      selectedVendor === v
                        ? 'bg-[#1A4D2E]/10 border-l-3 border-l-[#1A4D2E] font-semibold text-[#1A4D2E]'
                        : 'hover:bg-slate-50'
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      <Truck size={14} className={selectedVendor === v ? 'text-[#1A4D2E]' : 'text-slate-400'} />
                      {v}
                    </div>
                  </button>
                ))
              )}
            </div>
          </CardContent>
        </Card>

        {/* Vendor Details Panel */}
        <div className="lg:col-span-3 space-y-4">
          {selectedVendor ? (
            <>
              {/* Supplier Contact Info (if available) */}
              {selectedSupplierDetails && (
                <Card className="border-slate-200 bg-slate-50">
                  <CardContent className="p-4">
                    <div className="flex items-start justify-between">
                      <div className="space-y-1">
                        <h3 className="font-bold text-lg" style={{ fontFamily: 'Manrope' }}>{selectedSupplierDetails.name}</h3>
                        <div className="flex flex-wrap gap-4 text-sm text-slate-600">
                          {selectedSupplierDetails.contact_person && (
                            <span>{selectedSupplierDetails.contact_person}</span>
                          )}
                          {selectedSupplierDetails.phone && (
                            <span className="flex items-center gap-1"><Phone size={12} /> {selectedSupplierDetails.phone}</span>
                          )}
                          {selectedSupplierDetails.email && (
                            <span className="flex items-center gap-1"><Mail size={12} /> {selectedSupplierDetails.email}</span>
                          )}
                          {selectedSupplierDetails.address && (
                            <span className="flex items-center gap-1"><MapPin size={12} /> {selectedSupplierDetails.address}</span>
                          )}
                        </div>
                      </div>
                      {canEditSupplier && (
                        <Button variant="outline" size="sm" onClick={() => openEditSupplier(selectedSupplierDetails)} data-testid="edit-supplier-btn">
                          <Edit2 size={12} className="mr-1" /> Edit
                        </Button>
                      )}
                      {canAddOldBalance && (
                        <Button
                          variant="outline" size="sm"
                          onClick={() => { setHistoricalDefaultTab('add'); setHistoricalDialog(true); }}
                          className="border-amber-300 text-amber-700 hover:bg-amber-50"
                          data-testid="supplier-add-old-balance-btn"
                          title="Encode pre-system supplier debt (admin PIN required)"
                        >
                          <Clock size={12} className="mr-1" /> Add Old Balance
                        </Button>
                      )}
                    </div>
                  </CardContent>
                </Card>
              )}

              {/* Stats Cards */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <Card className="border-slate-200">
                  <CardContent className="p-4">
                    <div className="flex items-center gap-2 text-slate-500 text-xs mb-1">
                      <FileText size={12} />
                      Total POs
                    </div>
                    <p className="text-2xl font-bold" style={{ fontFamily: 'Manrope' }}>{vendorStats?.totalPOs || 0}</p>
                  </CardContent>
                </Card>
                <Card className="border-slate-200">
                  <CardContent className="p-4">
                    <div className="flex items-center gap-2 text-slate-500 text-xs mb-1">
                      <DollarSign size={12} />
                      Total Purchased
                    </div>
                    <p className="text-2xl font-bold text-[#1A4D2E]" style={{ fontFamily: 'Manrope' }}>{formatPHP(vendorStats?.totalPurchased || 0)}</p>
                  </CardContent>
                </Card>
                <Card className="border-slate-200">
                  <CardContent className="p-4">
                    <div className="flex items-center gap-2 text-slate-500 text-xs mb-1">
                      <CheckCircle size={12} />
                      Total Paid
                    </div>
                    <p className="text-2xl font-bold text-emerald-600" style={{ fontFamily: 'Manrope' }}>{formatPHP(vendorStats?.totalPaid || 0)}</p>
                  </CardContent>
                </Card>
                <Card className="border-slate-200">
                  <CardContent className="p-4">
                    <div className="flex items-center gap-2 text-slate-500 text-xs mb-1">
                      <AlertCircle size={12} />
                      Pending Payment
                    </div>
                    <p className="text-2xl font-bold text-red-600" style={{ fontFamily: 'Manrope' }}>{formatPHP(vendorStats?.pendingPayment || 0)}</p>
                  </CardContent>
                </Card>
              </div>

              {/* PO List with Tabs */}
              <Card className="border-slate-200">
                <CardContent className="p-0">
                  <div className="p-4 border-b border-slate-100 flex items-center justify-between">
                    <div className="flex items-center gap-3 flex-wrap">
                      <h2 className="font-bold text-lg" style={{ fontFamily: 'Manrope' }}>{selectedVendor}</h2>
                      {!selectedSupplierDetails && canCreateSupplier && (
                        <Button variant="outline" size="sm" data-testid="save-as-supplier-btn" onClick={() => {
                          setSupplierForm({ ...supplierForm, name: selectedVendor });
                          setEditMode(false);
                          setSupplierDialog(true);
                        }}>
                          <Plus size={12} className="mr-1" /> Save as Supplier
                        </Button>
                      )}
                      {!selectedSupplierDetails && canAddOldBalance && (
                        <Button
                          variant="outline" size="sm"
                          onClick={() => { setHistoricalDefaultTab('add'); setHistoricalDialog(true); }}
                          className="border-amber-300 text-amber-700 hover:bg-amber-50"
                          data-testid="vendor-add-old-balance-btn"
                          title="Encode pre-system supplier debt (admin PIN required)"
                        >
                          <Clock size={12} className="mr-1" /> Add Old Balance
                        </Button>
                      )}
                      {canAddOldBalance && (
                        <Button
                          variant="ghost" size="sm"
                          onClick={() => { setHistoricalDefaultTab('list'); setHistoricalDialog(true); }}
                          className="text-amber-700 hover:bg-amber-50"
                          data-testid="vendor-view-old-balance-btn"
                          title="View pre-system supplier debt history"
                        >
                          <History size={12} className="mr-1" /> Old Balances
                        </Button>
                      )}
                    </div>
                    <div className="text-xs text-slate-500">
                      <span className="mr-3">{vendorStats?.unpaidPOs || 0} Unpaid</span>
                      <span>{vendorStats?.orderedPOs || 0} Pending Delivery</span>
                    </div>
                  </div>

                  <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
                    <div className="px-4 border-b border-slate-100">
                      <TabsList className="h-10 bg-transparent p-0 gap-4">
                        <TabsTrigger value="all" className="px-0 pb-2 rounded-none border-b-2 border-transparent data-[state=active]:border-[#1A4D2E] data-[state=active]:bg-transparent data-[state=active]:shadow-none">
                          All ({vendorPOs.length})
                        </TabsTrigger>
                        <TabsTrigger value="unpaid" className="px-0 pb-2 rounded-none border-b-2 border-transparent data-[state=active]:border-[#1A4D2E] data-[state=active]:bg-transparent data-[state=active]:shadow-none">
                          Unpaid ({vendorPOs.filter(po => po.payment_status !== 'paid').length})
                        </TabsTrigger>
                        <TabsTrigger value="pending" className="px-0 pb-2 rounded-none border-b-2 border-transparent data-[state=active]:border-[#1A4D2E] data-[state=active]:bg-transparent data-[state=active]:shadow-none">
                          Pending Delivery ({vendorPOs.filter(po => po.status === 'ordered').length})
                        </TabsTrigger>
                      </TabsList>
                    </div>

                    <TabsContent value={activeTab} className="m-0">
                      <Table>
                        <TableHeader>
                          <TableRow className="bg-slate-50">
                            <TableHead className="text-xs uppercase text-slate-500">PO #</TableHead>
                            <TableHead className="text-xs uppercase text-slate-500">Date</TableHead>
                            <TableHead className="text-xs uppercase text-slate-500">Items</TableHead>
                            <TableHead className="text-xs uppercase text-slate-500 text-right">Total</TableHead>
                            <TableHead className="text-xs uppercase text-slate-500 text-right">Paid</TableHead>
                            <TableHead className="text-xs uppercase text-slate-500 text-right">Balance</TableHead>
                            <TableHead className="text-xs uppercase text-slate-500">Delivery</TableHead>
                            <TableHead className="text-xs uppercase text-slate-500">Payment</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {filteredPOs.map(po => (
                            <TableRow
                              key={po.id}
                              className="table-row-hover cursor-pointer"
                              onClick={() => openDetailModal(po.po_number)}
                              data-testid={`po-row-${po.id}`}
                            >
                              <TableCell className="font-mono text-xs text-blue-600 font-semibold">{po.po_number}</TableCell>
                              <TableCell className="text-xs text-slate-500">{po.purchase_date || fmtDate(po.created_at)}</TableCell>
                              <TableCell className="text-sm">{po.items?.length || 0}</TableCell>
                              <TableCell className="text-right font-semibold">{formatPHP(po.subtotal)}</TableCell>
                              <TableCell className="text-right text-emerald-600">{formatPHP(po.amount_paid || 0)}</TableCell>
                              <TableCell className="text-right font-bold text-red-600">
                                {formatPHP(po.balance || (po.payment_status !== 'paid' ? po.subtotal : 0) || 0)}
                              </TableCell>
                              <TableCell>
                                <Badge className={`text-[10px] ${statusColor(po.status)}`}>{po.status}</Badge>
                              </TableCell>
                              <TableCell>
                                <Badge className={`text-[10px] ${paymentColor(po.payment_status || 'unpaid')}`}>
                                  {po.payment_status || 'unpaid'}
                                </Badge>
                              </TableCell>
                            </TableRow>
                          ))}
                          {filteredPOs.length === 0 && (
                            <TableRow>
                              <TableCell colSpan={8} className="text-center py-8 text-slate-400">
                                No purchase orders in this category
                              </TableCell>
                            </TableRow>
                          )}
                        </TableBody>
                      </Table>
                    </TabsContent>
                  </Tabs>
                </CardContent>
              </Card>

              {/* Recent Payments Section */}
              <Card className="border-slate-200">
                <CardContent className="p-4">
                  <h3 className="font-semibold text-sm mb-3 flex items-center gap-2">
                    <History size={14} className="text-slate-400" />
                    Payment History
                  </h3>
                  <div className="space-y-2 max-h-[300px] overflow-y-auto">
                    {vendorPOs.flatMap(po => 
                      (po.payment_history || []).map((pay, idx) => ({
                        ...pay,
                        po_number: po.po_number,
                        po_id: po.id,
                        key: `${po.id}-${idx}`
                      }))
                    )
                    .sort((a, b) => new Date(b.date) - new Date(a.date))
                    .slice(0, 20)
                    .map(pay => (
                      <div key={pay.key} className="flex items-center justify-between py-2 px-3 bg-slate-50 rounded-lg text-sm">
                        <div className="flex items-center gap-3">
                          <ArrowRight size={12} className="text-emerald-500" />
                          <span className="font-mono text-xs text-blue-600 hover:underline cursor-pointer" onClick={() => openDetailModal(pay.po_number)}>{pay.po_number}</span>
                          <span className="text-slate-600">{pay.date}</span>
                          {pay.check_number && (
                            <span className="text-xs text-slate-400">Check #{pay.check_number}</span>
                          )}
                        </div>
                        <span className="font-bold text-emerald-600">{formatPHP(pay.amount)}</span>
                      </div>
                    ))}
                    {vendorPOs.flatMap(po => po.payment_history || []).length === 0 && (
                      <p className="text-center py-4 text-slate-400 text-sm">No payment history</p>
                    )}
                  </div>
                </CardContent>
              </Card>
            </>
          ) : (
            <div className="flex items-center justify-center h-64 text-slate-400">
              <div className="text-center">
                <Truck size={48} className="mx-auto mb-3 opacity-30" />
                <p>Select a supplier to view details</p>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* PO Detail Modal */}
      <ReviewDetailDialog
        open={invoiceModalOpen}
        onOpenChange={setInvoiceModalOpen}
        poNumber={selectedInvoiceNumber}
        onUpdated={fetchData}
        showReviewAction={false}
        showPayAction={false}
      />

      {/* New/Edit Supplier Dialog */}
      <Dialog open={supplierDialog} onOpenChange={setSupplierDialog}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle style={{ fontFamily: 'Manrope' }}>{editMode ? 'Edit Supplier' : 'New Supplier'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 mt-2">
            <div>
              <Label className="text-xs text-slate-500">Supplier Name *</Label>
              <Input
                value={supplierForm.name}
                onChange={e => setSupplierForm(f => ({ ...f, name: e.target.value }))}
                placeholder="Enter supplier name"
                className="h-10"
                data-testid="supplier-name-input"
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label className="text-xs text-slate-500">Contact Person</Label>
                <Input
                  value={supplierForm.contact_person}
                  onChange={e => setSupplierForm(f => ({ ...f, contact_person: e.target.value }))}
                  placeholder="Contact name"
                  className="h-10"
                />
              </div>
              <div>
                <Label className="text-xs text-slate-500">Phone</Label>
                <Input
                  value={supplierForm.phone}
                  onChange={e => setSupplierForm(f => ({ ...f, phone: e.target.value }))}
                  placeholder="Phone number"
                  className="h-10"
                />
              </div>
            </div>
            <div>
              <Label className="text-xs text-slate-500">Email</Label>
              <Input
                value={supplierForm.email}
                onChange={e => setSupplierForm(f => ({ ...f, email: e.target.value }))}
                placeholder="Email address"
                className="h-10"
                type="email"
              />
            </div>
            <div>
              <Label className="text-xs text-slate-500">Address</Label>
              <Input
                value={supplierForm.address}
                onChange={e => setSupplierForm(f => ({ ...f, address: e.target.value }))}
                placeholder="Full address"
                className="h-10"
              />
            </div>
            <div>
              <Label className="text-xs text-slate-500">Notes</Label>
              <Input
                value={supplierForm.notes}
                onChange={e => setSupplierForm(f => ({ ...f, notes: e.target.value }))}
                placeholder="Additional notes"
                className="h-10"
              />
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="outline" onClick={() => setSupplierDialog(false)}>Cancel</Button>
              <Button
                onClick={saveSupplier}
                disabled={saving}
                className="bg-[#1A4D2E] hover:bg-[#14532d] text-white"
                data-testid="save-supplier-btn"
              >
                {saving ? 'Saving...' : editMode ? 'Update Supplier' : 'Create Supplier'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Historical Supplier PO (pre-system carry-forward). Pre-selects
          the currently viewed vendor so the operator only sees that
          supplier's history. Admin/owner only — backend re-validates
          via PIN policy. */}
      <HistoricalSupplierPODialog
        open={historicalDialog}
        onOpenChange={setHistoricalDialog}
        defaultBranchId={currentBranch?.id || ''}
        defaultSupplierName={selectedVendor || ''}
        defaultTab={historicalDefaultTab}
        onChange={() => fetchData()}
      />
    </div>
  );
}
