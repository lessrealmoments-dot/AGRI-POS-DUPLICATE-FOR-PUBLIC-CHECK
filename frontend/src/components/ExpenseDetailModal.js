import { useState, useEffect } from 'react';
import { api, useAuth } from '../contexts/AuthContext';
import { formatPHP, fmtDateTime } from '../lib/utils';
import UploadQRDialog from './UploadQRDialog';
import ReceiptGallery from './ReceiptGallery';
import VerificationBadge from './VerificationBadge';
import VerifyPinDialog from './VerifyPinDialog';
import ViewQRDialog from './ViewQRDialog';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from './ui/dialog';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Badge } from './ui/badge';
import { Separator } from './ui/separator';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './ui/table';
import {
  ShieldCheck, Upload, Pencil, Check, AlertTriangle,
  RefreshCw, Trash2, DollarSign, Wallet, FileText, User, Calendar, Tag, Printer
} from 'lucide-react';
import { toast } from 'sonner';
import PrintEngine from '../lib/PrintEngine';
import CalcInput from './CalcInput';

const fundBadge = (src) => {
  if (src === 'safe') return 'bg-amber-100 text-amber-700';
  if (src === 'digital') return 'bg-blue-100 text-blue-700';
  return 'bg-slate-100 text-slate-700';
};

export default function ExpenseDetailModal({ open, onOpenChange, expenseId, onUpdated }) {
  const { user, hasPerm } = useAuth();
  const isAdmin = user?.role === 'admin';

  const [expense, setExpense] = useState(null);
  const [loading, setLoading] = useState(true);
  const [bizInfo, setBizInfo] = useState({});

  useEffect(() => { api.get('/settings/business-info').then(r => setBizInfo(r.data)).catch(() => {}); }, []);

  const handlePrintExpense = () => {
    if (!expense) return;
    PrintEngine.print({ type: 'expense_voucher', data: expense, format: 'full_page', businessInfo: bizInfo });
  };

  // Edit
  const [editMode, setEditMode] = useState(false);
  const [editForm, setEditForm] = useState({});
  const [saving, setSaving] = useState(false);

  // Delete / Void
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [voidPin, setVoidPin] = useState('');

  // QR/Receipt
  const [uploadQROpen, setUploadQROpen] = useState(false);
  const [viewQROpen, setViewQROpen] = useState(false);
  const [verifyDialogOpen, setVerifyDialogOpen] = useState(false);

  useEffect(() => {
    if (open && expenseId) {
      loadExpense();
      setEditMode(false);
      setDeleteConfirm(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, expenseId]);

  const loadExpense = async () => {
    setLoading(true);
    try {
      const res = await api.get(`/expenses/${expenseId}`);
      setExpense(res.data);
    } catch {
      toast.error('Failed to load expense');
      onOpenChange(false);
    }
    setLoading(false);
  };

  const canEdit = isAdmin || hasPerm('accounting', 'edit_expense');
  const isVoided = expense?.voided === true;

  // ── Edit ──
  const openEdit = () => {
    setEditForm({
      category: expense.category || '',
      description: expense.description || '',
      amount: expense.amount || 0,
      payment_method: expense.payment_method || 'Cash',
      reference_number: expense.reference_number || '',
      date: expense.date || '',
    });
    setEditMode(true);
  };

  const saveEdit = async () => {
    setSaving(true);
    try {
      const res = await api.put(`/expenses/${expense.id}`, editForm);
      toast.success('Expense updated');
      setExpense(res.data);
      setEditMode(false);
      onUpdated?.();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed to save'); }
    setSaving(false);
  };

  // ── Delete / Void ──
  const handleDelete = async () => {
    if (!voidPin) { toast.error('PIN is required'); return; }
    setDeleting(true);
    try {
      await api.delete(`/expenses/${expense.id}`, { data: { pin: voidPin } });
      toast.success('Expense voided — funds returned');
      setDeleteConfirm(false);
      setVoidPin('');
      onOpenChange(false);
      onUpdated?.();
    } catch (e) { toast.error(e.response?.data?.detail || 'Failed to delete'); }
    setDeleting(false);
  };

  if (!open) return null;

  return (
    <>
      <Dialog open={open} onOpenChange={v => { onOpenChange(v); if (!v) setEditMode(false); }}>
        <DialogContent className="sm:max-w-lg max-h-[90vh] overflow-y-auto overflow-x-hidden" data-testid="expense-detail-modal">
          <DialogHeader>
            <DialogTitle style={{ fontFamily: 'Manrope' }} data-testid="expense-detail-title">
              {editMode ? 'Edit Expense' : 'Expense Detail'}
            </DialogTitle>
            {expense?.verified && (
              <div className="mt-1.5 flex items-center gap-2">
                <VerificationBadge doc={expense} />
                {expense.verified_at && <span className="text-[10px] text-slate-400">{fmtDateTime(expense.verified_at)}</span>}
              </div>
            )}
          </DialogHeader>

          {/* Action toolbar — below header, wraps on narrow screens */}
          {expense && !loading && !isVoided && (
            <div className="flex flex-wrap items-center gap-1.5 pb-2 border-b border-slate-100" data-testid="expense-action-bar">
              <Button size="sm" variant="outline" className="h-7 text-xs whitespace-nowrap bg-slate-800 text-white border-slate-600 hover:bg-slate-700"
                onClick={() => setViewQROpen(true)} data-testid="expense-view-phone-btn">
                <span className="mr-1">📱</span> View
              </Button>
              <Button size="sm" variant="outline" className="h-7 text-xs whitespace-nowrap"
                onClick={() => setUploadQROpen(true)} data-testid="expense-upload-receipt-btn">
                <Upload size={12} className="mr-1" /> Upload
              </Button>
              {!expense.verified && (
                <Button size="sm" variant="outline" className="h-7 text-xs whitespace-nowrap text-[#1A4D2E] border-[#1A4D2E]/40 hover:bg-[#1A4D2E]/10"
                  onClick={() => setVerifyDialogOpen(true)} data-testid="expense-verify-btn">
                  <ShieldCheck size={12} className="mr-1" /> Verify
                </Button>
              )}
              {canEdit && !editMode && (
                <Button size="sm" variant="outline" className="h-7 text-xs whitespace-nowrap text-amber-600 border-amber-300"
                  onClick={openEdit} data-testid="expense-edit-btn">
                  <Pencil size={12} className="mr-1" /> Edit
                </Button>
              )}
              <Button size="sm" variant="outline" className="h-7 text-xs whitespace-nowrap"
                onClick={handlePrintExpense} data-testid="expense-print-btn">
                <Printer size={12} className="mr-1" /> Print
              </Button>
            </div>
          )}

          {loading ? (
            <div className="flex items-center justify-center py-12"><div className="text-slate-400">Loading...</div></div>
          ) : expense ? (
            <div className="space-y-4 mt-2">
              {/* Voided banner */}
              {isVoided && (
                <div className="p-2.5 rounded-lg bg-red-50 border border-red-200 text-xs text-red-800 flex items-center gap-2">
                  <AlertTriangle size={12} className="shrink-0 text-red-600" />
                  <span>This expense has been voided. Funds were returned to <b>{expense.fund_source || 'cashier'}</b>.</span>
                </div>
              )}

              {/* Receipts gallery */}
              <ReceiptGallery recordType="expense" recordId={expense.id} />

              {/* Detail view or edit form */}
              {editMode ? (
                <div className="space-y-3">
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <Label className="text-xs text-slate-500">Category</Label>
                      <Input value={editForm.category} onChange={e => setEditForm(f => ({ ...f, category: e.target.value }))} className="mt-1 h-9" />
                    </div>
                    <div>
                      <Label className="text-xs text-slate-500">Amount</Label>
                      <CalcInput value={editForm.amount} onChange={(v) => setEditForm(f => ({ ...f, amount: parseFloat(v) || 0 }))} className="mt-1 h-9" />
                    </div>
                    <div>
                      <Label className="text-xs text-slate-500">Date</Label>
                      <Input type="date" value={editForm.date} onChange={e => setEditForm(f => ({ ...f, date: e.target.value }))} className="mt-1 h-9" />
                    </div>
                    <div>
                      <Label className="text-xs text-slate-500">Payment Method</Label>
                      <Input value={editForm.payment_method} onChange={e => setEditForm(f => ({ ...f, payment_method: e.target.value }))} className="mt-1 h-9" />
                    </div>
                    <div className="col-span-2">
                      <Label className="text-xs text-slate-500">Reference #</Label>
                      <Input value={editForm.reference_number} onChange={e => setEditForm(f => ({ ...f, reference_number: e.target.value }))} className="mt-1 h-9" />
                    </div>
                    <div className="col-span-2">
                      <Label className="text-xs text-slate-500">Description</Label>
                      <Input value={editForm.description} onChange={e => setEditForm(f => ({ ...f, description: e.target.value }))} className="mt-1 h-9" />
                    </div>
                  </div>
                  <div className="flex gap-2 pt-2 border-t">
                    <Button variant="outline" onClick={() => setEditMode(false)} className="flex-1">Cancel</Button>
                    <Button onClick={saveEdit} disabled={saving} className="flex-1 bg-amber-600 hover:bg-amber-700 text-white">
                      {saving ? <RefreshCw size={13} className="animate-spin mr-1.5" /> : <Check size={13} className="mr-1.5" />}
                      Save Changes
                    </Button>
                  </div>
                </div>
              ) : (
                <>
                  {/* Expense info grid */}
                  <div className="grid grid-cols-2 gap-3 text-sm">
                    <div className="flex items-start gap-2">
                      <Tag size={14} className="text-slate-400 mt-0.5 shrink-0" />
                      <div>
                        <span className="text-slate-500 text-xs block">Category</span>
                        <span className="font-semibold">{expense.category || '—'}</span>
                      </div>
                    </div>
                    <div className="flex items-start gap-2">
                      <DollarSign size={14} className="text-red-500 mt-0.5 shrink-0" />
                      <div>
                        <span className="text-slate-500 text-xs block">Amount</span>
                        <span className="font-bold text-red-600 text-lg">{formatPHP(expense.amount || 0)}</span>
                      </div>
                    </div>
                    <div className="flex items-start gap-2">
                      <Calendar size={14} className="text-slate-400 mt-0.5 shrink-0" />
                      <div>
                        <span className="text-slate-500 text-xs block">Date</span>
                        <span className="font-medium">{expense.date || '—'}</span>
                      </div>
                    </div>
                    <div className="flex items-start gap-2">
                      <Wallet size={14} className="text-slate-400 mt-0.5 shrink-0" />
                      <div>
                        <span className="text-slate-500 text-xs block">Fund Source</span>
                        <Badge className={`text-[10px] ${fundBadge(expense.fund_source)}`}>{expense.fund_source || 'cashier'}</Badge>
                      </div>
                    </div>
                  </div>

                  {/* Additional info */}
                  <div className="grid grid-cols-2 gap-3 text-sm bg-slate-50 rounded-lg p-3">
                    <div><span className="text-slate-500 text-xs">Payment Method:</span> <span className="font-medium">{expense.payment_method || 'Cash'}</span></div>
                    {expense.reference_number && <div><span className="text-slate-500 text-xs">Reference #:</span> <span className="font-mono">{expense.reference_number}</span></div>}
                    {expense.vendor_name && <div><span className="text-slate-500 text-xs">Vendor:</span> <span className="font-medium">{expense.vendor_name}</span></div>}
                    {expense.customer_name && <div><span className="text-slate-500 text-xs">Customer:</span> <span className="font-medium">{expense.customer_name}</span></div>}
                    {expense.employee_name && <div><span className="text-slate-500 text-xs">Employee:</span> <span className="font-medium">{expense.employee_name}</span></div>}
                    {expense.linked_invoice_number && <div><span className="text-slate-500 text-xs">Linked Invoice:</span> <span className="font-mono text-blue-600">{expense.linked_invoice_number}</span></div>}
                  </div>

                  {/* Description */}
                  {expense.description && (
                    <>
                      <Separator />
                      <div>
                        <Label className="text-xs text-slate-500">Description</Label>
                        <p className="text-sm text-slate-800 mt-1 bg-slate-50 rounded-lg p-3">{expense.description}</p>
                      </div>
                    </>
                  )}

                  {/* Notes */}
                  {expense.notes && (
                    <div>
                      <Label className="text-xs text-slate-500">Notes</Label>
                      <p className="text-sm text-slate-600 mt-1">{expense.notes}</p>
                    </div>
                  )}

                  {/* Created/updated info */}
                  <div className="text-xs text-slate-400 space-y-0.5 border-t pt-2">
                    <p>Created: {expense.created_at ? new Date(expense.created_at).toLocaleString() : '—'} by {expense.created_by_name || '—'}</p>
                    {expense.updated_by_name && <p>Updated: {expense.updated_at ? new Date(expense.updated_at).toLocaleString() : '—'} by {expense.updated_by_name}</p>}
                  </div>

                  {/* Delete/Void button */}
                  {canEdit && !isVoided && (
                    <div className="border-t pt-3">
                      {!deleteConfirm ? (
                        <Button variant="destructive" className="w-full" onClick={() => { setDeleteConfirm(true); setVoidPin(''); }} data-testid="expense-delete-btn">
                          <Trash2 size={14} className="mr-2" /> Void Expense
                        </Button>
                      ) : (
                        <div className="space-y-2">
                          <p className="text-sm text-red-600 text-center font-medium">Funds will be returned to {expense.fund_source || 'cashier'}.</p>
                          <Input type="password" autoComplete="new-password" placeholder="Enter PIN to confirm" value={voidPin}
                            onChange={e => setVoidPin(e.target.value)} data-testid="expense-void-pin"
                            onKeyDown={e => { if (e.key === 'Enter' && voidPin) handleDelete(); }} />
                          <div className="flex gap-2">
                            <Button variant="outline" className="flex-1" onClick={() => { setDeleteConfirm(false); setVoidPin(''); }}>Cancel</Button>
                            <Button variant="destructive" className="flex-1" onClick={handleDelete} disabled={deleting || !voidPin} data-testid="expense-confirm-delete-btn">
                              {deleting ? <RefreshCw size={13} className="animate-spin mr-1.5" /> : <Trash2 size={13} className="mr-1.5" />}
                              Confirm Void
                            </Button>
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </>
              )}
            </div>
          ) : (
            <div className="flex items-center justify-center py-12"><div className="text-slate-400">Expense not found</div></div>
          )}
        </DialogContent>
      </Dialog>

      {/* Upload QR Dialog */}
      <UploadQRDialog
        open={uploadQROpen}
        onClose={(count) => { setUploadQROpen(false); if (count > 0) { toast.success(`${count} photo(s) uploaded`); onUpdated?.(); } }}
        recordType="expense"
        recordId={expense?.id}
      />

      {/* View QR Dialog */}
      <ViewQRDialog open={viewQROpen} onClose={() => setViewQROpen(false)} recordType="expense" recordId={expense?.id} />

      {/* Verify PIN Dialog */}
      <VerifyPinDialog
        open={verifyDialogOpen}
        onClose={() => setVerifyDialogOpen(false)}
        docType="expense"
        docId={expense?.id}
        docLabel={expense?.description || expense?.category}
        onVerified={(result) => {
          setVerifyDialogOpen(false);
          setExpense(prev => ({ ...prev, verified: true, verified_by_name: result.verified_by, verified_at: new Date().toISOString() }));
          onUpdated?.();
        }}
      />
    </>
  );
}
