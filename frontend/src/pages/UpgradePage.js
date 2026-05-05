import { useState, useEffect } from 'react';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { CheckCircle, X, MessageCircle, Phone, CreditCard, ArrowLeft, Upload, RefreshCw } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useAuth, api } from '../contexts/AuthContext';
import axios from 'axios';
import { toast } from 'sonner';
import { formatDate } from '../lib/dateFormat';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

const PLANS = [
  {
    key: "basic", name: "Basic", php: 1500, usd: 30,
    branches: "1 Branch", users: "5 Users",
    features: [
      "POS & Split Payments", "Inventory Management", "Customer Management",
      "Daily Close Wizard", "Basic Reports", "Expense Tracking",
      "2 Fund Wallets (Cashier + Safe)",
    ],
    notIncluded: ["Purchase Orders", "Multi-Branch", "Audit Center"],
    color: "slate",
  },
  {
    key: "standard", name: "Standard", php: 4000, usd: 80,
    branches: "2 Branches", users: "15 Users",
    popular: true,
    features: [
      "Everything in Basic",
      "Purchase Orders & Receiving",
      "Supplier Management",
      "Employee + Cash Advances",
      "4-Wallet Fund Management",
      "Multi-Branch Transfers",
      "Standard Audit Trail",
      "Advanced Reports",
    ],
    notIncluded: ["Full Audit Center", "Transaction Verification", "2FA"],
    color: "emerald",
  },
  {
    key: "pro", name: "Pro", php: 7500, usd: 150,
    branches: "5 Branches", users: "Unlimited Users",
    features: [
      "Everything in Standard",
      "Full Audit Center",
      "Transaction Verification",
      "Discrepancy Flagging",
      "Branch Transfers + Repack Pricing",
      "Granular Role Permissions",
      "2FA Security",
      "Advanced Financial Reports",
    ],
    notIncluded: [],
    color: "indigo",
  },
];

const PAYMENT_METHODS = [
  { name: "GCash", instructions: "Send to: 09XX-XXX-XXXX\nAccount Name: [Your Name]\nAmount: Based on selected plan", icon: "💚", placeholder: true },
  { name: "Maya", instructions: "Send to: 09XX-XXX-XXXX\nAccount Name: [Your Name]\nAmount: Based on selected plan", icon: "💜", placeholder: true },
  { name: "Bank Transfer", instructions: "Bank: BDO / BPI / UnionBank\nAccount: XXXX-XXXX-XXXX\nAccount Name: [Business Name]", icon: "🏦", placeholder: true },
  { name: "PayPal", instructions: "PayPal.me/[your-link]\nAmount in USD as shown on plan", icon: "🔵", placeholder: true },
];


function PaymentProofForm({ plan, amount, paymentMethod }) {
  const [refNumber, setRefNumber] = useState('');
  const [notes, setNotes] = useState('');
  const [proofImage, setProofImage] = useState(null); // base64
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  const handleFile = (e) => {
    const file = e.target.files[0];
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) { toast.error('Image must be under 5MB'); return; }
    const reader = new FileReader();
    reader.onloadend = () => setProofImage(reader.result);
    reader.readAsDataURL(file);
  };

  const handleSubmit = async () => {
    if (!proofImage) { toast.error('Please attach a payment screenshot'); return; }
    setSubmitting(true);
    try {
      await api.post('/organizations/submit-payment-proof', {
        plan_requested: plan,
        amount,
        payment_method: paymentMethod,
        reference_number: refNumber,
        notes,
        proof_image: proofImage,
      });
      setSubmitted(true);
      toast.success('Payment proof submitted! We\'ll activate within 24 hours.');
    } catch (e) { toast.error(e.response?.data?.detail || 'Submission failed'); }
    setSubmitting(false);
  };

  if (submitted) {
    return (
      <div className="bg-emerald-50 border border-emerald-200 rounded-xl p-4 text-center">
        <CheckCircle size={24} className="text-emerald-500 mx-auto mb-2" />
        <p className="text-emerald-700 font-semibold text-sm">Payment proof submitted!</p>
        <p className="text-emerald-600 text-xs mt-1">We'll review and activate your plan within 24 hours. You'll receive a confirmation email.</p>
      </div>
    );
  }

  return (
    <div className="border border-slate-200 rounded-xl p-4 space-y-3">
      <p className="text-sm font-semibold text-slate-800 flex items-center gap-2">
        <Upload size={14} className="text-emerald-600" /> Upload Payment Proof
      </p>

      <div>
        <Label className="text-xs text-slate-500">Payment Screenshot *</Label>
        <label className="mt-1 flex flex-col items-center justify-center border-2 border-dashed border-slate-200 rounded-lg p-4 cursor-pointer hover:border-emerald-300 hover:bg-emerald-50/30 transition-colors">
          {proofImage ? (
            <div className="text-center">
              <img src={proofImage} alt="proof" className="max-h-32 mx-auto rounded-lg mb-1" />
              <p className="text-xs text-emerald-600 font-medium">Screenshot attached</p>
              <p className="text-[10px] text-slate-400">Click to change</p>
            </div>
          ) : (
            <>
              <Upload size={20} className="text-slate-400 mb-1" />
              <p className="text-xs text-slate-500">Click to upload GCash/Maya receipt or bank confirmation</p>
              <p className="text-[10px] text-slate-400 mt-0.5">JPG, PNG — max 5MB</p>
            </>
          )}
          <input type="file" accept="image/*" className="hidden" onChange={handleFile} />
        </label>
      </div>

      <div>
        <Label className="text-xs text-slate-500">Reference / Transaction Number</Label>
        <Input value={refNumber} onChange={e => setRefNumber(e.target.value)}
          placeholder="e.g. GC123456789" className="mt-1 h-9 text-sm" />
      </div>

      <div>
        <Label className="text-xs text-slate-500">Notes (optional)</Label>
        <Input value={notes} onChange={e => setNotes(e.target.value)}
          placeholder="e.g. Paid via GCash to 09XX XXX XXXX" className="mt-1 h-9 text-sm" />
      </div>

      <Button onClick={handleSubmit} disabled={submitting || !proofImage}
        className="w-full bg-emerald-600 hover:bg-emerald-700 text-white h-10">
        {submitting ? <RefreshCw size={14} className="animate-spin mr-2" /> : <Upload size={14} className="mr-2" />}
        Submit Payment Proof
      </Button>
      <p className="text-[10px] text-slate-400 text-center">
        Include your company name as payment reference. We'll activate within 24 hours.
      </p>
    </div>
  );
}

export default function UpgradePage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [selectedPlan, setSelectedPlan] = useState(null);
  const [selectedPayment, setSelectedPayment] = useState(null);
  const [extraBranches, setExtraBranches] = useState(0);
  const [annual, setAnnual] = useState(false);
  const [paymentInfo, setPaymentInfo] = useState(null);

  useEffect(() => {
    axios.get(`${BACKEND_URL}/api/organizations/payment-info`)
      .then(r => setPaymentInfo(r.data))
      .catch(() => {});
  }, []);

  // Dynamic payment methods from backend
  const PAYMENT_METHODS = [
    { name: 'GCash', key: 'gcash', icon: '💚' },
    { name: 'Maya', key: 'maya', icon: '💜' },
    { name: 'Bank Transfer', key: 'bank', icon: '🏦' },
    { name: 'PayPal', key: 'paypal', icon: '🔵' },
  ].filter(pm => paymentInfo?.configured || true); // Always show all options

  const totalPhp = selectedPlan
    ? Math.round((annual ? selectedPlan.php * 10 : selectedPlan.php * 12) +
        extraBranches * (annual ? 1500 * 10 : 1500 * 12))
    : 0;

  const monthlyPhp = selectedPlan
    ? Math.round((annual ? selectedPlan.php * 10 / 12 : selectedPlan.php) + extraBranches * 1500)
    : 0;

  return (
    <div className="min-h-screen bg-[#F5F5F0] p-6">
      <div className="max-w-5xl mx-auto">
        <div className="flex items-center gap-3 mb-8">
          <button onClick={() => navigate(-1)} className="text-slate-500 hover:text-slate-700 flex items-center gap-1.5 text-sm">
            <ArrowLeft size={16} /> Back
          </button>
          <div className="h-4 w-px bg-slate-300" />
          <h1 className="text-2xl font-bold text-slate-900" style={{ fontFamily: 'Manrope' }}>
            Upgrade Your Plan
          </h1>
        </div>

        {/* Current plan notice */}
        {user?.subscription && (
          <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 mb-8 flex items-start gap-3">
            <div className="text-amber-500 mt-0.5">ℹ️</div>
            <div>
              <p className="text-amber-800 text-sm font-medium">
                You're currently on the <strong>{user.subscription.effective_plan?.toUpperCase()}</strong> plan
                {user.subscription.plan === 'trial' && (
                  <span className="ml-1">(Trial ends: {formatDate(user.subscription.trial_ends_at)})</span>
                )}
              </p>
              <p className="text-amber-600 text-xs mt-0.5">
                To upgrade, select a plan below and complete payment. Then contact us with your receipt.
              </p>
            </div>
          </div>
        )}

        {/* Billing toggle */}
        <div className="flex items-center justify-center gap-3 mb-8">
          <span className={`text-sm ${!annual ? 'text-slate-900 font-semibold' : 'text-slate-500'}`}>Monthly</span>
          <button
            onClick={() => setAnnual(!annual)}
            className={`relative w-12 h-6 rounded-full transition-colors ${annual ? 'bg-emerald-500' : 'bg-slate-300'}`}
          >
            <div className={`absolute top-1 w-4 h-4 bg-white rounded-full shadow transition-transform ${annual ? 'translate-x-7' : 'translate-x-1'}`} />
          </button>
          <span className={`text-sm ${annual ? 'text-slate-900 font-semibold' : 'text-slate-500'}`}>
            Annual <span className="text-emerald-600 text-xs font-medium">2 months free</span>
          </span>
        </div>

        {/* Plan cards */}
        <div className="grid md:grid-cols-3 gap-5 mb-8">
          {PLANS.map(plan => {
            const php = annual ? Math.round(plan.php * 10 / 12) : plan.php;
            const usd = annual ? Math.round(plan.usd * 10 / 12) : plan.usd;
            const isSelected = selectedPlan?.key === plan.key;
            return (
              <button
                key={plan.key}
                data-testid={`plan-${plan.key}`}
                onClick={() => setSelectedPlan(plan)}
                className={`text-left rounded-2xl border-2 p-5 transition-all ${
                  isSelected
                    ? 'border-emerald-500 bg-emerald-50 shadow-lg shadow-emerald-100'
                    : plan.popular
                    ? 'border-emerald-200 bg-white'
                    : 'border-slate-200 bg-white hover:border-slate-300'
                }`}
              >
                {plan.popular && (
                  <div className="text-xs font-bold text-emerald-600 mb-2 uppercase tracking-wide">Most Popular</div>
                )}
                <h3 className="font-bold text-slate-900 text-lg">{plan.name}</h3>
                <div className="mt-2 mb-3">
                  <span className="text-2xl font-extrabold text-slate-900">₱{php.toLocaleString()}</span>
                  <span className="text-slate-500 text-sm">/mo</span>
                  <div className="text-slate-400 text-xs">${usd}/month</div>
                </div>
                <div className="text-xs text-slate-500 mb-4">{plan.branches} · {plan.users}</div>
                <ul className="space-y-1.5">
                  {plan.features.map(f => (
                    <li key={f} className="flex items-start gap-2 text-xs text-slate-600">
                      <CheckCircle size={12} className="text-emerald-500 shrink-0 mt-0.5" />
                      {f}
                    </li>
                  ))}
                  {plan.notIncluded.map(f => (
                    <li key={f} className="flex items-start gap-2 text-xs text-slate-400">
                      <X size={12} className="text-slate-300 shrink-0 mt-0.5" />
                      {f}
                    </li>
                  ))}
                </ul>
              </button>
            );
          })}
        </div>

        {/* Extra branches add-on */}
        {selectedPlan && selectedPlan.key !== 'basic' && (
          <div className="bg-white border border-slate-200 rounded-2xl p-5 mb-6">
            <h3 className="font-semibold text-slate-800 mb-3 flex items-center gap-2">
              <CreditCard size={16} className="text-slate-500" />
              Add Extra Branches
            </h3>
            <div className="flex items-center gap-4">
              <div className="flex items-center border border-slate-200 rounded-lg overflow-hidden">
                <button onClick={() => setExtraBranches(Math.max(0, extraBranches - 1))}
                  className="px-4 py-2 text-slate-500 hover:bg-slate-50">-</button>
                <span className="px-4 py-2 font-semibold text-slate-800 min-w-[3rem] text-center">{extraBranches}</span>
                <button onClick={() => setExtraBranches(extraBranches + 1)}
                  className="px-4 py-2 text-slate-500 hover:bg-slate-50">+</button>
              </div>
              <span className="text-slate-600 text-sm">
                × ₱1,500/branch/mo {extraBranches > 0 && `= +₱${(extraBranches * 1500).toLocaleString()}/mo`}
              </span>
            </div>
          </div>
        )}

        {/* Payment section */}
        {selectedPlan && (
          <div className="bg-white border border-slate-200 rounded-2xl p-6 mb-6">
            <h3 className="font-bold text-slate-900 mb-1">Payment Summary</h3>
            <p className="text-slate-500 text-sm mb-5">
              Select a payment method and send the exact amount. Include your company name in the reference.
            </p>

            <div className="bg-slate-50 rounded-xl p-4 mb-6 flex items-center justify-between">
              <div>
                <div className="font-semibold text-slate-800">{selectedPlan.name} Plan{extraBranches > 0 ? ` + ${extraBranches} extra branch${extraBranches > 1 ? 'es' : ''}` : ''}</div>
                <div className="text-slate-500 text-sm">{annual ? 'Annual billing' : 'Monthly billing'}</div>
              </div>
              <div className="text-right">
                <div className="text-xl font-extrabold text-slate-900">₱{(annual ? totalPhp : monthlyPhp).toLocaleString()}</div>
                <div className="text-slate-400 text-xs">{annual ? 'billed annually' : 'per month'}</div>
              </div>
            </div>

            {/* Payment method selector */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
              {PAYMENT_METHODS.map(pm => (
                <button
                  key={pm.name}
                  data-testid={`payment-${pm.name.toLowerCase().replace(' ', '-')}`}
                  onClick={() => setSelectedPayment(selectedPayment === pm.name ? null : pm.name)}
                  className={`border-2 rounded-xl p-3 text-center transition-all ${
                    selectedPayment === pm.name
                      ? 'border-emerald-500 bg-emerald-50'
                      : 'border-slate-200 hover:border-slate-300'
                  }`}
                >
                  <div className="text-2xl mb-1">{pm.icon}</div>
                  <div className="text-xs font-semibold text-slate-700">{pm.name}</div>
                </button>
              ))}
            </div>

            {selectedPayment && (() => {
              const pm = PAYMENT_METHODS.find(p => p.name === selectedPayment);
              const info = paymentInfo?.methods?.[pm?.key] || {};
              const hasInfo = Object.values(info).some(v => v && v !== '');

              return (
                <div className="bg-slate-50 border border-slate-200 rounded-xl p-4 mb-5">
                  <h4 className="font-semibold text-slate-800 mb-3 text-sm flex items-center gap-2">
                    <span>{pm.icon}</span> {selectedPayment} Payment Details
                  </h4>
                  {!hasInfo ? (
                    <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-xs text-amber-700">
                      Payment details for {selectedPayment} haven't been configured yet.
                      Please contact us directly for payment instructions.
                    </div>
                  ) : (
                    <div className="space-y-2">
                      {/* QR Code */}
                      {info.qr_base64 && (
                        <div className="flex justify-center mb-3">
                          <div className="bg-white p-3 rounded-xl border border-slate-200 inline-block">
                            <img src={info.qr_base64} alt={`${selectedPayment} QR`} className="w-40 h-40 object-contain" />
                            <p className="text-xs text-slate-500 text-center mt-2">Scan to pay</p>
                          </div>
                        </div>
                      )}
                      {/* Account details */}
                      <div className="bg-white border border-slate-200 rounded-lg p-3 space-y-1.5 text-sm">
                        {info.number && <div><span className="text-slate-500">Number:</span> <strong className="text-slate-800">{info.number}</strong></div>}
                        {info.account_name && <div><span className="text-slate-500">Name:</span> <strong className="text-slate-800">{info.account_name}</strong></div>}
                        {info.bank_name && <div><span className="text-slate-500">Bank:</span> <strong className="text-slate-800">{info.bank_name}</strong></div>}
                        {info.account_number && <div><span className="text-slate-500">Account:</span> <strong className="text-slate-800">{info.account_number}</strong></div>}
                        {info.email && <div><span className="text-slate-500">Email:</span> <strong className="text-slate-800">{info.email}</strong></div>}
                        {info.link && (
                          <div><span className="text-slate-500">Link:</span>{' '}
                            <a href={info.link} target="_blank" rel="noreferrer" className="text-emerald-600 hover:underline font-medium">{info.link}</a>
                          </div>
                        )}
                      </div>
                      <p className="text-xs text-slate-500">
                        Include your company name <strong>"{user?.subscription?.org_name || 'your company'}"</strong> as payment reference.
                      </p>
                    </div>
                  )}
                </div>
              );
            })()}

            <div className="bg-blue-50 border border-blue-200 rounded-xl p-4">
              <div className="flex items-start gap-3">
                <MessageCircle size={18} className="text-blue-500 mt-0.5 shrink-0" />
                <div>
                  <p className="text-blue-800 text-sm font-medium">After paying, submit your proof below</p>
                  <p className="text-blue-600 text-xs mt-1">
                    Upload your payment screenshot and we'll activate your plan within 24 hours.
                  </p>
                </div>
              </div>
            </div>

            {/* Payment proof upload form */}
            <PaymentProofForm
              plan={selectedPlan?.key}
              amount={annual ? totalPhp : monthlyPhp}
              paymentMethod={selectedPayment || ''}
            />
          </div>
        )}

        {!selectedPlan && (
          <div className="text-center py-8 text-slate-400 text-sm">
            Select a plan above to see payment options
          </div>
        )}
      </div>
    </div>
  );
}
