/**
 * CropCreditTypeDialog — Shown before confirming a credit sale.
 * Asks staff: "By Term" or "Charged to Crop?"
 *
 * Handles:
 * - Active season detection (auto-stack, no planting date needed)
 * - New season (ask planting date)
 * - Expired + unpaid (soft block, show outstanding balance)
 * - Offer to link existing open term credit invoices to the season
 */
import { useState, useEffect } from 'react';
import { api } from '../contexts/AuthContext';
import { toast } from 'sonner';
import { Sprout, Calendar, AlertTriangle, CheckCircle2, Clock, ArrowRight } from 'lucide-react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from './ui/dialog';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Badge } from './ui/badge';

const formatPHP = (n) => `₱${parseFloat(n || 0).toLocaleString('en-PH', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

/**
 * Props:
 *   open              - boolean
 *   onClose           - fn()
 *   onConfirm         - fn({ type, plantingDate, activeCreditId, linkExistingTerms })
 *   customerId        - string
 *   customerName      - string
 *   saleAmount        - number
 *   branchId          - string
 */
export default function CropCreditTypeDialog({ open, onClose, onConfirm, customerId, customerName, saleAmount, branchId }) {
  const [step, setStep] = useState('type'); // 'type' | 'planting' | 'link_terms' | 'blocked'
  const [blockInfo, setBlockInfo] = useState(null);
  const [loading, setLoading] = useState(false);
  const [plantingDate, setPlantingDate] = useState('');
  const [openTermInvoices, setOpenTermInvoices] = useState([]);
  const [linkTerms, setLinkTerms] = useState(false);
  const [loadingCheck, setLoadingCheck] = useState(false);

  // Reset on open
  useEffect(() => {
    if (open) {
      setStep('type');
      setBlockInfo(null);
      setPlantingDate('');
      setOpenTermInvoices([]);
      setLinkTerms(false);
    }
  }, [open]);

  // Fetch customer's crop credit status when dialog opens
  useEffect(() => {
    if (open && customerId) {
      setLoadingCheck(true);
      api.get(`/crop-credits/check-block/${customerId}`)
        .then(r => setBlockInfo(r.data))
        .catch(() => {})
        .finally(() => setLoadingCheck(false));
    }
  }, [open, customerId]);

  const handleSelectCrop = async () => {
    if (!blockInfo) return;

    // Hard block — expired season with unpaid balance
    if (blockInfo.blocked && blockInfo.reason === 'expired_season_unpaid') {
      setStep('blocked');
      return;
    }

    // Active season — will stack, check for open term invoices to link
    if (blockInfo.blocked && blockInfo.reason === 'active_crop_credit') {
      // Check for open term credit invoices
      try {
        const res = await api.get('/invoices', {
          params: { customer_id: customerId, status: 'open', limit: 20 }
        });
        const termInvoices = (res.data.invoices || []).filter(inv =>
          inv.balance > 0 &&
          inv.payment_type === 'credit' &&
          !inv.crop_credit_id
        );
        setOpenTermInvoices(termInvoices);
      } catch { /* ignore */ }
      setStep('link_terms');
      return;
    }

    // No active season — ask for planting date
    // But first check for existing term invoices to offer linking
    try {
      const res = await api.get('/invoices', {
        params: { customer_id: customerId, status: 'open', limit: 20 }
      });
      const termInvoices = (res.data.invoices || []).filter(inv =>
        inv.balance > 0 &&
        inv.payment_type === 'credit' &&
        !inv.crop_credit_id
      );
      setOpenTermInvoices(termInvoices);
    } catch { /* ignore */ }

    setStep('planting');
  };

  const handleConfirmByTerm = () => {
    onConfirm({ type: 'by_term' });
  };

  const handleConfirmCrop = () => {
    if (step === 'planting' && !plantingDate) {
      toast.error('Please enter the planting date');
      return;
    }

    const activeCreditId = (blockInfo?.reason === 'active_crop_credit')
      ? blockInfo.active_credit_id
      : null;

    onConfirm({
      type: 'charged_to_crop',
      plantingDate: activeCreditId ? null : plantingDate,
      activeCreditId,
      linkExistingTerms: linkTerms,
      termInvoices: linkTerms ? openTermInvoices : [],
    });
  };

  // Compute harvest date preview
  const harvestDate = plantingDate
    ? new Date(new Date(plantingDate).getTime() + 127 * 24 * 60 * 60 * 1000)
        .toLocaleDateString('en-PH', { month: 'long', day: 'numeric', year: 'numeric' })
    : null;

  const activeCredit = blockInfo?.active_credit;
  const termTotal = openTermInvoices.reduce((sum, inv) => sum + inv.balance, 0);

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-sm">
            <Sprout size={16} className="text-emerald-600" /> Credit Type
          </DialogTitle>
        </DialogHeader>

        {loadingCheck ? (
          <div className="py-8 text-center text-sm text-slate-400">Checking customer status...</div>
        ) : (
          <>
            {/* STEP: Type Selection */}
            {step === 'type' && (
              <div className="space-y-3">
                <p className="text-xs text-slate-500">
                  How should <strong>{customerName}</strong>'s credit of <strong>{formatPHP(saleAmount)}</strong> be handled?
                </p>

                {/* Crop active season notice */}
                {blockInfo?.reason === 'active_crop_credit' && activeCredit && (
                  <div className="flex items-start gap-2 p-3 bg-emerald-50 border border-emerald-200 rounded-lg text-xs text-emerald-700">
                    <CheckCircle2 size={14} className="shrink-0 mt-0.5" />
                    <div>
                      <p className="font-medium">Active crop season found</p>
                      <p>Season ends: {activeCredit.season_end_date} · Running total: {formatPHP(activeCredit.total_due)}</p>
                      <p className="text-emerald-600">Selecting "Charged to Crop" will add to this season.</p>
                    </div>
                  </div>
                )}

                {/* Hard block notice */}
                {blockInfo?.reason === 'expired_season_unpaid' && (
                  <div className="flex items-start gap-2 p-3 bg-red-50 border border-red-200 rounded-lg text-xs text-red-700">
                    <AlertTriangle size={14} className="shrink-0 mt-0.5" />
                    <div>
                      <p className="font-medium">Previous crop season unpaid</p>
                      <p>Outstanding: {formatPHP(activeCredit?.total_due)}. Customer must settle or get an extension before a new crop season can start.</p>
                    </div>
                  </div>
                )}

                <div className="grid grid-cols-2 gap-3 pt-1">
                  {/* By Term */}
                  <button
                    data-testid="credit-type-by-term"
                    onClick={handleConfirmByTerm}
                    className="p-4 border-2 border-slate-200 rounded-xl hover:border-slate-400 hover:bg-slate-50 text-left transition-all group">
                    <div className="text-slate-600 mb-2">
                      <Calendar size={20} />
                    </div>
                    <p className="font-semibold text-sm text-slate-800">By Term</p>
                    <p className="text-[11px] text-slate-400 mt-0.5">Standard payment terms with due date</p>
                  </button>

                  {/* Charged to Crop */}
                  <button
                    data-testid="credit-type-charged-to-crop"
                    onClick={handleSelectCrop}
                    disabled={blockInfo?.reason === 'expired_season_unpaid'}
                    className={`p-4 border-2 rounded-xl text-left transition-all ${
                      blockInfo?.reason === 'expired_season_unpaid'
                        ? 'border-red-200 bg-red-50 opacity-60 cursor-not-allowed'
                        : blockInfo?.reason === 'active_crop_credit'
                        ? 'border-emerald-300 bg-emerald-50 hover:border-emerald-500'
                        : 'border-emerald-200 hover:border-emerald-400 hover:bg-emerald-50'
                    }`}>
                    <div className="text-emerald-600 mb-2">
                      <Sprout size={20} />
                    </div>
                    <p className="font-semibold text-sm text-slate-800">Charged to Crop</p>
                    <p className="text-[11px] text-slate-400 mt-0.5">
                      {blockInfo?.reason === 'active_crop_credit' ? 'Add to active season' : 'Harvest-backed credit'}
                    </p>
                  </button>
                </div>
              </div>
            )}

            {/* STEP: Blocked — expired unpaid season */}
            {step === 'blocked' && (
              <div className="space-y-3">
                <div className="p-4 bg-red-50 border border-red-200 rounded-xl text-sm text-red-700 space-y-2">
                  <p className="font-semibold flex items-center gap-2"><AlertTriangle size={16} /> Crop Season Expired — Unpaid Balance</p>
                  <p>This customer's previous crop season ended on <strong>{activeCredit?.season_end_date}</strong> with an outstanding balance of <strong>{formatPHP(activeCredit?.total_due)}</strong>.</p>
                  <p className="text-xs">The customer must fully pay or get a season extension before new crop credit can be issued.</p>
                </div>
                <p className="text-xs text-slate-500">You can still proceed as a <strong>By Term</strong> credit if approved.</p>
                <div className="flex gap-2">
                  <Button variant="outline" className="flex-1 text-xs" onClick={onClose}>Cancel</Button>
                  <Button className="flex-1 text-xs bg-slate-700 text-white" onClick={handleConfirmByTerm}>Proceed as By Term</Button>
                </div>
              </div>
            )}

            {/* STEP: Planting date for new season */}
            {step === 'planting' && (
              <div className="space-y-3">
                <div className="p-3 bg-emerald-50 border border-emerald-200 rounded-lg text-xs text-emerald-700">
                  <p className="font-medium">New Crop Season for {customerName}</p>
                  <p className="mt-0.5">Enter when they started growing their crops.</p>
                </div>

                <div>
                  <Label className="text-xs">Date Started Growing (Planting Date) *</Label>
                  <Input
                    data-testid="planting-date-input"
                    type="date"
                    value={plantingDate}
                    onChange={e => setPlantingDate(e.target.value)}
                    className="mt-1"
                  />
                  {harvestDate && (
                    <p className="text-[11px] text-emerald-600 mt-1 flex items-center gap-1">
                      <Calendar size={11} /> Expected harvest: <strong>{harvestDate}</strong>
                    </p>
                  )}
                </div>

                {/* Offer to link existing term credit invoices */}
                {openTermInvoices.length > 0 && (
                  <div className="p-3 bg-amber-50 border border-amber-200 rounded-lg text-xs space-y-2">
                    <p className="font-medium text-amber-700">Existing Term Credit Detected</p>
                    <p className="text-amber-600">
                      {customerName} has {openTermInvoices.length} open term credit invoice{openTermInvoices.length > 1 ? 's' : ''} totaling <strong>{formatPHP(termTotal)}</strong>.
                    </p>
                    <p>Would you like to link them to this crop season so they're tracked under the same harvest?</p>
                    <div className="flex gap-2 pt-1">
                      <button
                        onClick={() => setLinkTerms(true)}
                        className={`flex-1 py-1.5 rounded-lg text-[11px] font-medium border transition-all ${linkTerms ? 'bg-amber-600 text-white border-amber-600' : 'border-amber-300 text-amber-700 hover:bg-amber-100'}`}>
                        Yes, link {openTermInvoices.length} invoice{openTermInvoices.length > 1 ? 's' : ''}
                      </button>
                      <button
                        onClick={() => setLinkTerms(false)}
                        className={`flex-1 py-1.5 rounded-lg text-[11px] font-medium border transition-all ${!linkTerms ? 'bg-slate-600 text-white border-slate-600' : 'border-slate-300 text-slate-600 hover:bg-slate-100'}`}>
                        No, keep separate
                      </button>
                    </div>
                  </div>
                )}

                <div className="flex gap-2">
                  <Button variant="outline" className="flex-1 text-xs" onClick={() => setStep('type')}>Back</Button>
                  <Button
                    data-testid="confirm-crop-credit-btn"
                    className="flex-1 text-xs bg-emerald-600 hover:bg-emerald-700 text-white"
                    onClick={handleConfirmCrop}
                    disabled={!plantingDate}>
                    <Sprout size={13} className="mr-1" /> Confirm Crop Credit
                  </Button>
                </div>
              </div>
            )}

            {/* STEP: Link terms for existing active season */}
            {step === 'link_terms' && (
              <div className="space-y-3">
                <div className="p-3 bg-emerald-50 border border-emerald-200 rounded-lg text-xs text-emerald-700">
                  <p className="font-medium flex items-center gap-1.5">
                    <CheckCircle2 size={13} /> Adding to Active Season
                  </p>
                  <p className="mt-0.5">Season ends: <strong>{activeCredit?.season_end_date}</strong> · Current total: <strong>{formatPHP(activeCredit?.total_due)}</strong></p>
                  <p>This sale of <strong>{formatPHP(saleAmount)}</strong> will be added to their running crop total.</p>
                </div>

                {openTermInvoices.length > 0 && (
                  <div className="p-3 bg-amber-50 border border-amber-200 rounded-lg text-xs space-y-2">
                    <p className="font-medium text-amber-700">Existing Term Credits Detected</p>
                    <p className="text-amber-600">
                      {openTermInvoices.length} open term invoice{openTermInvoices.length > 1 ? 's' : ''} totaling <strong>{formatPHP(termTotal)}</strong> — link to this crop season?
                    </p>
                    <div className="flex gap-2">
                      <button onClick={() => setLinkTerms(true)}
                        className={`flex-1 py-1.5 rounded-lg text-[11px] font-medium border transition-all ${linkTerms ? 'bg-amber-600 text-white border-amber-600' : 'border-amber-300 text-amber-700 hover:bg-amber-100'}`}>
                        Yes, link them
                      </button>
                      <button onClick={() => setLinkTerms(false)}
                        className={`flex-1 py-1.5 rounded-lg text-[11px] font-medium border transition-all ${!linkTerms ? 'bg-slate-600 text-white border-slate-600' : 'border-slate-300 text-slate-600 hover:bg-slate-100'}`}>
                        No thanks
                      </button>
                    </div>
                  </div>
                )}

                <div className="flex gap-2">
                  <Button variant="outline" className="flex-1 text-xs" onClick={() => setStep('type')}>Back</Button>
                  <Button
                    data-testid="confirm-crop-credit-btn"
                    className="flex-1 text-xs bg-emerald-600 hover:bg-emerald-700 text-white"
                    onClick={handleConfirmCrop}>
                    <ArrowRight size={13} className="mr-1" /> Add to Crop Season
                  </Button>
                </div>
              </div>
            )}
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
