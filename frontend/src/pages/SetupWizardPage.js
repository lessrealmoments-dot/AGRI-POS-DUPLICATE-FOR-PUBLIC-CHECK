import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import axios from 'axios';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { 
  Building2, Store, User, Wallet, ChevronRight, ChevronLeft, 
  Check, Loader2, Eye, EyeOff, DollarSign, Vault, Building
} from 'lucide-react';
import { toast } from 'sonner';
import CalcInput from '../components/CalcInput';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

const STEPS = [
  { id: 'company', title: 'Company Info', icon: Building2, desc: 'Basic business details' },
  { id: 'branch', title: 'First Branch', icon: Store, desc: 'Your main location' },
  { id: 'admin', title: 'Admin Account', icon: User, desc: 'Owner login credentials' },
  { id: 'funds', title: 'Opening Balances', icon: Wallet, desc: 'Current cash positions' },
];

export default function SetupWizardPage() {
  const navigate = useNavigate();
  const [currentStep, setCurrentStep] = useState(0);
  const [loading, setLoading] = useState(false);
  const [checkingStatus, setCheckingStatus] = useState(true);
  const [showPassword, setShowPassword] = useState(false);
  
  const [formData, setFormData] = useState({
    // Company
    company_name: '',
    company_address: '',
    company_phone: '',
    company_email: '',
    tax_id: '',
    currency: 'PHP',
    
    // Branch
    branch_name: '',
    branch_address: '',
    branch_phone: '',
    
    // Admin
    admin_username: '',
    admin_password: '',
    admin_full_name: '',
    admin_email: '',
    manager_pin: '1234',
    
    // Funds
    opening_cashier_balance: '',
    opening_safe_balance: '',
    opening_bank_balance: '',
    bank_name: '',
    bank_account_number: '',
  });

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    checkSetupStatus();
  }, []);

  const checkSetupStatus = async () => {
    try {
      const res = await axios.get(`${BACKEND_URL}/api/setup/status`);
      if (res.data.setup_completed) {
        navigate('/login');
      }
    } catch (err) {
      console.error('Failed to check setup status', err);
    }
    setCheckingStatus(false);
  };

  const updateField = (field, value) => {
    setFormData(prev => ({ ...prev, [field]: value }));
  };

  const validateStep = () => {
    switch (currentStep) {
      case 0: // Company
        if (!formData.company_name.trim()) {
          toast.error('Company name is required');
          return false;
        }
        break;
      case 1: // Branch
        if (!formData.branch_name.trim()) {
          toast.error('Branch name is required');
          return false;
        }
        break;
      case 2: // Admin
        if (!formData.admin_username.trim()) {
          toast.error('Username is required');
          return false;
        }
        if (formData.admin_password.length < 6) {
          toast.error('Password must be at least 6 characters');
          return false;
        }
        break;
      default:
        break;
    }
    return true;
  };

  const nextStep = () => {
    if (validateStep()) {
      setCurrentStep(prev => Math.min(prev + 1, STEPS.length - 1));
    }
  };

  const prevStep = () => {
    setCurrentStep(prev => Math.max(prev - 1, 0));
  };

  const handleSubmit = async () => {
    setLoading(true);
    try {
      const payload = {
        ...formData,
        opening_cashier_balance: parseFloat(formData.opening_cashier_balance) || 0,
        opening_safe_balance: parseFloat(formData.opening_safe_balance) || 0,
        opening_bank_balance: parseFloat(formData.opening_bank_balance) || 0,
      };
      
      const res = await axios.post(`${BACKEND_URL}/api/setup/initialize`, payload);
      toast.success('Setup complete! Please login with your credentials.');
      navigate('/login');
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Setup failed');
    }
    setLoading(false);
  };

  if (checkingStatus) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[#F5F5F0]">
        <Loader2 className="w-8 h-8 animate-spin text-[#1A4D2E]" />
      </div>
    );
  }

  const renderStepContent = () => {
    switch (currentStep) {
      case 0:
        return (
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="company_name">Company Name *</Label>
              <Input
                id="company_name"
                data-testid="setup-company-name"
                value={formData.company_name}
                onChange={e => updateField('company_name', e.target.value)}
                placeholder="e.g., AgriSupply Trading"
                className="h-11"
                autoFocus
              />
            </div>
            <div className="grid md:grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="company_phone">Phone</Label>
                <Input
                  id="company_phone"
                  value={formData.company_phone}
                  onChange={e => updateField('company_phone', e.target.value)}
                  placeholder="+63 912 345 6789"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="company_email">Email</Label>
                <Input
                  id="company_email"
                  type="email"
                  value={formData.company_email}
                  onChange={e => updateField('company_email', e.target.value)}
                  placeholder="company@email.com"
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="company_address">Address</Label>
              <Input
                id="company_address"
                value={formData.company_address}
                onChange={e => updateField('company_address', e.target.value)}
                placeholder="Full business address"
              />
            </div>
            <div className="grid md:grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="tax_id">Tax ID / TIN (Optional)</Label>
                <Input
                  id="tax_id"
                  value={formData.tax_id}
                  onChange={e => updateField('tax_id', e.target.value)}
                  placeholder="000-000-000-000"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="currency">Currency</Label>
                <select
                  id="currency"
                  value={formData.currency}
                  onChange={e => updateField('currency', e.target.value)}
                  className="w-full h-10 px-3 rounded-md border border-slate-200 bg-white text-sm"
                >
                  <option value="PHP">₱ Philippine Peso (PHP)</option>
                  <option value="USD">$ US Dollar (USD)</option>
                  <option value="EUR">€ Euro (EUR)</option>
                </select>
              </div>
            </div>
          </div>
        );
        
      case 1:
        return (
          <div className="space-y-4">
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-sm text-blue-800">
              This is your first branch. You can add more branches later from Settings.
            </div>
            <div className="space-y-2">
              <Label htmlFor="branch_name">Branch Name *</Label>
              <Input
                id="branch_name"
                data-testid="setup-branch-name"
                value={formData.branch_name}
                onChange={e => updateField('branch_name', e.target.value)}
                placeholder="e.g., Main Branch, Downtown Store"
                className="h-11"
                autoFocus
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="branch_address">Branch Address</Label>
              <Input
                id="branch_address"
                value={formData.branch_address}
                onChange={e => updateField('branch_address', e.target.value)}
                placeholder="Branch location address"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="branch_phone">Branch Phone</Label>
              <Input
                id="branch_phone"
                value={formData.branch_phone}
                onChange={e => updateField('branch_phone', e.target.value)}
                placeholder="+63 912 345 6789"
              />
            </div>
          </div>
        );
        
      case 2:
        return (
          <div className="space-y-4">
            <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-sm text-amber-800">
              This will be the owner/admin account with full system access.
            </div>
            <div className="grid md:grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="admin_username">Username *</Label>
                <Input
                  id="admin_username"
                  data-testid="setup-admin-username"
                  value={formData.admin_username}
                  onChange={e => updateField('admin_username', e.target.value)}
                  placeholder="e.g., owner, admin"
                  className="h-11"
                  autoFocus
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="admin_full_name">Full Name</Label>
                <Input
                  id="admin_full_name"
                  value={formData.admin_full_name}
                  onChange={e => updateField('admin_full_name', e.target.value)}
                  placeholder="Your full name"
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="admin_password">Password *</Label>
              <div className="relative">
                <Input
                  id="admin_password"
                  data-testid="setup-admin-password"
                  type={showPassword ? 'text' : 'password'}
                  value={formData.admin_password}
                  onChange={e => updateField('admin_password', e.target.value)}
                  placeholder="At least 6 characters"
                  className="h-11 pr-10"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
                >
                  {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </div>
            <div className="grid md:grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="admin_email">Email</Label>
                <Input
                  id="admin_email"
                  type="email"
                  value={formData.admin_email}
                  onChange={e => updateField('admin_email', e.target.value)}
                  placeholder="owner@company.com"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="manager_pin">Manager PIN (4 digits)</Label>
                <Input
                  id="manager_pin"
                  value={formData.manager_pin}
                  onChange={e => updateField('manager_pin', e.target.value.replace(/\D/g, '').slice(0, 4))}
                  placeholder="1234"
                  maxLength={4}
                />
                <p className="text-xs text-slate-500">Used for approving credit sales</p>
              </div>
              <div className="mt-4 bg-amber-50 border border-amber-200 rounded-lg p-4 space-y-3">
                <p className="text-amber-800 text-sm font-semibold flex items-center gap-2">
                  🔐 Security Setup (Complete after first login)
                </p>
                <div className="space-y-2 text-xs text-amber-700">
                  <div className="flex items-start gap-2">
                    <span className="font-bold shrink-0">1.</span>
                    <div>
                      <strong>Owner PIN</strong> — Go to Settings → Audit Setup → Set Owner PIN.
                      This is your private PIN for authorizing sensitive operations (inventory corrections, capital injections). Never share with employees.
                    </div>
                  </div>
                  <div className="flex items-start gap-2">
                    <span className="font-bold shrink-0">2.</span>
                    <div>
                      <strong>Google Authenticator (TOTP)</strong> — Go to Settings → Security → Set Up Now.
                      Scan the QR code with your phone. When you're away, employees call you and you read the 6-digit code — it expires every 30 seconds and cannot be reused.
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        );
        
      case 3:
        return (
          <div className="space-y-4">
            <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-3 text-sm text-emerald-800">
              Enter your current cash positions. This sets your starting balances for accurate tracking.
            </div>
            
            <div className="space-y-4">
              <div className="flex items-start gap-3 p-4 border border-slate-200 rounded-lg">
                <div className="w-10 h-10 rounded-lg bg-green-100 flex items-center justify-center flex-shrink-0">
                  <DollarSign size={20} className="text-green-600" />
                </div>
                <div className="flex-1 space-y-2">
                  <Label htmlFor="opening_cashier_balance">Operating Fund Balance</Label>
                  <CalcInput id="opening_cashier_balance"
 data-testid="setup-cashier-balance"
 value={formData.opening_cashier_balance}
 onChange={(v) => updateField('opening_cashier_balance', v)}
 placeholder="0.00"
 className="max-w-[200px]" />
                  <p className="text-xs text-slate-500">Cash currently in the register</p>
                </div>
              </div>
              
              <div className="flex items-start gap-3 p-4 border border-slate-200 rounded-lg">
                <div className="w-10 h-10 rounded-lg bg-amber-100 flex items-center justify-center flex-shrink-0">
                  <Vault size={20} className="text-amber-600" />
                </div>
                <div className="flex-1 space-y-2">
                  <Label htmlFor="opening_safe_balance">Safe Balance</Label>
                  <CalcInput id="opening_safe_balance"
 data-testid="setup-safe-balance"
 value={formData.opening_safe_balance}
 onChange={(v) => updateField('opening_safe_balance', v)}
 placeholder="0.00"
 className="max-w-[200px]" />
                  <p className="text-xs text-slate-500">Cash stored in your safe</p>
                </div>
              </div>
              
              <div className="flex items-start gap-3 p-4 border border-slate-200 rounded-lg">
                <div className="w-10 h-10 rounded-lg bg-blue-100 flex items-center justify-center flex-shrink-0">
                  <Building size={20} className="text-blue-600" />
                </div>
                <div className="flex-1 space-y-2">
                  <Label htmlFor="bank_name">Bank Account (Optional)</Label>
                  <div className="grid md:grid-cols-2 gap-3">
                    <Input
                      id="bank_name"
                      value={formData.bank_name}
                      onChange={e => updateField('bank_name', e.target.value)}
                      placeholder="Bank name"
                    />
                    <Input
                      value={formData.bank_account_number}
                      onChange={e => updateField('bank_account_number', e.target.value)}
                      placeholder="Account number"
                    />
                  </div>
                  <CalcInput id="opening_bank_balance"
 data-testid="setup-bank-balance"
 value={formData.opening_bank_balance}
 onChange={(v) => updateField('opening_bank_balance', v)}
 placeholder="Current bank balance"
 className="max-w-[200px]" />
                </div>
              </div>
            </div>
          </div>
        );
        
      default:
        return null;
    }
  };

  return (
    <div className="min-h-screen bg-[#F5F5F0] py-8 px-4">
      <div className="max-w-2xl mx-auto">
        {/* Header */}
        <div className="text-center mb-8">
          <div className="w-14 h-14 rounded-2xl bg-[#1A4D2E] flex items-center justify-center mx-auto mb-4">
            <Store size={28} className="text-white" />
          </div>
          <h1 className="text-2xl font-bold" style={{ fontFamily: 'Manrope' }}>Welcome to AgriBooks</h1>
          <p className="text-slate-500 mt-1">Let's set up your business in a few steps</p>
        </div>
        
        {/* Progress Steps */}
        <div className="flex items-center justify-center gap-2 mb-8">
          {STEPS.map((step, index) => (
            <div key={step.id} className="flex items-center">
              <div 
                className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-medium transition-colors ${
                  index < currentStep 
                    ? 'bg-[#1A4D2E] text-white' 
                    : index === currentStep 
                      ? 'bg-[#1A4D2E] text-white ring-4 ring-[#1A4D2E]/20' 
                      : 'bg-slate-200 text-slate-500'
                }`}
              >
                {index < currentStep ? <Check size={16} /> : index + 1}
              </div>
              {index < STEPS.length - 1 && (
                <div className={`w-12 h-0.5 mx-1 ${index < currentStep ? 'bg-[#1A4D2E]' : 'bg-slate-200'}`} />
              )}
            </div>
          ))}
        </div>
        
        {/* Step Card */}
        <Card className="border-slate-200 shadow-sm">
          <CardHeader className="pb-4">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg bg-[#1A4D2E]/10 flex items-center justify-center">
                {(() => {
                  const StepIcon = STEPS[currentStep].icon;
                  return <StepIcon size={20} className="text-[#1A4D2E]" />;
                })()}
              </div>
              <div>
                <CardTitle className="text-lg" style={{ fontFamily: 'Manrope' }}>
                  {STEPS[currentStep].title}
                </CardTitle>
                <CardDescription>{STEPS[currentStep].desc}</CardDescription>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            {renderStepContent()}
            
            {/* Navigation */}
            <div className="flex justify-between mt-8 pt-6 border-t border-slate-100">
              <Button
                variant="ghost"
                onClick={prevStep}
                disabled={currentStep === 0}
                className="gap-2"
              >
                <ChevronLeft size={16} /> Back
              </Button>
              
              {currentStep < STEPS.length - 1 ? (
                <Button
                  onClick={nextStep}
                  className="gap-2 bg-[#1A4D2E] hover:bg-[#14532d]"
                  data-testid="setup-next-btn"
                >
                  Next <ChevronRight size={16} />
                </Button>
              ) : (
                <Button
                  onClick={handleSubmit}
                  disabled={loading}
                  className="gap-2 bg-[#1A4D2E] hover:bg-[#14532d]"
                  data-testid="setup-complete-btn"
                >
                  {loading ? (
                    <>
                      <Loader2 size={16} className="animate-spin" /> Setting up...
                    </>
                  ) : (
                    <>
                      <Check size={16} /> Complete Setup
                    </>
                  )}
                </Button>
              )}
            </div>
          </CardContent>
        </Card>
        
        {/* Step indicator text */}
        <p className="text-center text-sm text-slate-400 mt-4">
          Step {currentStep + 1} of {STEPS.length}
        </p>
      </div>
    </div>
  );
}
