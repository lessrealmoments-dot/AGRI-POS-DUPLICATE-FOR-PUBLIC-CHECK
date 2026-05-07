import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { useState, useEffect } from 'react';
import axios from 'axios';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { UnsavedChangesProvider } from './contexts/UnsavedChangesContext';
import { Toaster } from './components/ui/sonner';
import Layout from './components/Layout';
import FeatureGate from './components/FeatureGate';
import LandingPage from './pages/LandingPage';
import LoginPage from './pages/LoginPage';
import RegisterPage from './pages/RegisterPage';
import UpgradePage from './pages/UpgradePage';
import SuperAdminPage from './pages/SuperAdminPage';
import AdminLoginPage from './pages/AdminLoginPage';
import SetupWizardPage from './pages/SetupWizardPage';
import DashboardPage from './pages/DashboardPage';
import BranchesPage from './pages/BranchesPage';
import BranchTransferPage from './pages/BranchTransferPage';
import ApproveTransferPage from './pages/ApproveTransferPage';
import InternalInvoicesPage from './pages/InternalInvoicesPage';
import ProductsPage from './pages/ProductsPage';
import RepackPricingPage from './pages/RepackPricingPage';
import ProductDetailPage from './pages/ProductDetailPage';
import InventoryPage from './pages/InventoryPage';
import CustomersPage from './pages/CustomersPage';
import PriceSchemesPage from './pages/PriceSchemesPage';
import PriceManagerPage from './pages/PriceManagerPage';
import SalesPage from './pages/SalesPage';
import UnifiedSalesPage from './pages/UnifiedSalesPage';
import PurchaseOrderPage from './pages/PurchaseOrderPage';
import SuppliersPage from './pages/SuppliersPage';
import DailyLogPage from './pages/DailyLogPage';
import CloseWizardPage from './pages/CloseWizardPage';
import ZReportSharePage from './pages/ZReportSharePage';
import PaymentsPage from './pages/PaymentsPage';
import FundManagementPage from './pages/FundManagementPage';
import AccountingPage from './pages/AccountingPage';
import SettingsPage from './pages/SettingsPage';
import UserPermissionsPage from './pages/UserPermissionsPage';
import CountSheetsPage from './pages/CountSheetsPage';
import PendingReleasesPage from './pages/PendingReleasesPage';
import AccountsPage from './pages/AccountsPage';
import EmployeesPage from './pages/EmployeesPage';
import NotificationsPage from './pages/NotificationsPage';
import PaySupplierPage from './pages/PaySupplierPage';
import ImportPage from './pages/ImportPage';
import ReportsPage from './pages/ReportsPage';
import PriceScanManager from './components/PriceScanManager';
import CustomerDedupeManager from './components/CustomerDedupeManager';
import ImpersonationBanner from './components/ImpersonationBanner';
import ReturnRefundWizard from './pages/ReturnRefundWizard';
import AuditCenterPage from './pages/AuditCenterPage';
import TeamPage from './pages/TeamPage';
import UploadPage from './pages/UploadPage';
import DocUploadPage from './pages/DocUploadPage';
import ViewReceiptsPage from './pages/ViewReceiptsPage';
import BackupManagementPage from './pages/BackupManagementPage';
import IncidentTicketsPage from './pages/IncidentTicketsPage';
import BarcodePrintPage from './pages/BarcodePrintPage';
import BarcodeManagePage from './pages/BarcodeManagePage';
import MobileScannerPage from './pages/MobileScannerPage';
import ExpensesPage from './pages/ExpensesPage';
import TransactionSearchPage from './pages/TransactionSearchPage';
import JournalEntriesPage from './pages/JournalEntriesPage';
import DocumentsPage from './pages/DocumentsPage';
import MessagesPage from './pages/MessagesPage';
import AppDownloadsPage from './pages/AppDownloadsPage';
import ForgotPasswordPage from './pages/ForgotPasswordPage';
import ResetPasswordPage from './pages/ResetPasswordPage';
import BudgetChecker from './components/BudgetChecker';
import TerminalPage from './pages/terminal/TerminalPage';
import DocViewerPage from './pages/DocViewerPage';
import CropCreditsPage from './pages/CropCreditsPage';
import SignaturePage from './pages/SignaturePage';
import PrintQueuePage from './pages/PrintQueuePage';

// Legacy pages removed (POSPage, SalesOrderPage) — replaced by UnifiedSalesPage

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

function ProtectedRoute({ children }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="flex items-center justify-center h-screen bg-[#F5F5F0]"><div className="text-slate-400 text-sm">Loading...</div></div>;
  if (!user) return <Navigate to="/login" replace />;
  return (
    <Layout>
      <ImpersonationBanner />
      <PriceScanManager />
      <CustomerDedupeManager />
      {children}
    </Layout>
  );
}

function AdminRoute({ children }) {
  // Admin/owner-only route guard. Bounces non-admins to /dashboard with a
  // toast hint so a direct URL hit (e.g. cashier pasting /messages into the
  // address bar) cannot bypass the sidebar's adminOnly visibility filter.
  const { user, loading } = useAuth();
  if (loading) return <div className="flex items-center justify-center h-screen bg-[#F5F5F0]"><div className="text-slate-400 text-sm">Loading...</div></div>;
  if (!user) return <Navigate to="/login" replace />;
  const role = user.role;
  const isAdmin = role === 'admin' || role === 'owner' || user.is_super_admin;
  if (!isAdmin) {
    return <Navigate to="/dashboard" replace />;
  }
  return (
    <Layout>
      <ImpersonationBanner />
      <PriceScanManager />
      <CustomerDedupeManager />
      {children}
    </Layout>
  );
}

function AppRoutes() {
  const { user, loading, effectiveBranchId } = useAuth();
  const [setupNeeded, setSetupNeeded] = useState(null);
  const [kioskLocked, setKioskLocked] = useState(() => localStorage.getItem('kiosk_locked') === 'true');

  // F1 to lock kiosk
  useEffect(() => {
    const handler = (e) => {
      if (e.key === 'F1') {
        e.preventDefault();
        if (user) {
          setKioskLocked(true);
          localStorage.setItem('kiosk_locked', 'true');
        }
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [user]);

  // Global guard: prevent <input type="number"> values from being mutated
  // by mouse-wheel scroll or Arrow Up/Down key presses. Spinners themselves
  // are hidden via CSS (index.css). Users reported accidental edits to
  // payment/discount/quantity fields — this makes those fields behave like
  // plain text inputs for numeric entry while keeping the numeric keyboard
  // on mobile.
  useEffect(() => {
    const isNumberInput = (el) => el && el.tagName === 'INPUT' && el.type === 'number';
    const onWheel = (e) => {
      if (isNumberInput(e.target) && document.activeElement === e.target) {
        // Blur so the wheel event scrolls the page instead of the input value.
        e.target.blur();
      }
    };
    const onKeyDown = (e) => {
      if (isNumberInput(e.target) && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
        e.preventDefault();
      }
    };
    document.addEventListener('wheel', onWheel, { passive: true, capture: true });
    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      document.removeEventListener('wheel', onWheel, { capture: true });
      document.removeEventListener('keydown', onKeyDown, true);
    };
  }, []);

  useEffect(() => {
    const checkSetup = async () => {
      try {
        const res = await axios.get(`${BACKEND_URL}/api/setup/status`);
        setSetupNeeded(!res.data.setup_completed);
      } catch {
        setSetupNeeded(false); // If check fails, assume setup is done
      }
    };
    checkSetup();
  }, []);

  if (loading || setupNeeded === null) {
    return <div className="flex items-center justify-center h-screen bg-[#F5F5F0]"><div className="text-slate-400 text-sm">Loading...</div></div>;
  }

  // If setup is needed (fresh install, no users), show setup wizard
  // NOTE: /admin and /login are always accessible so users can log in even on fresh installs
  if (setupNeeded && !user) {
    return (
      <Routes>
        <Route path="/zr/:token" element={<ZReportSharePage />} />
        <Route path="/setup" element={<SetupWizardPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route path="/" element={<LandingPage />} />
        <Route path="/admin" element={<AdminLoginPage />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }

  return (
    <>
      {kioskLocked && user && (
        <BudgetChecker
          branchId={effectiveBranchId}
          onUnlock={() => { setKioskLocked(false); localStorage.removeItem('kiosk_locked'); }}
        />
      )}
      <Routes>
      {/* Public routes */}
      <Route path="/zr/:token" element={<ZReportSharePage />} />
      <Route path="/" element={user ? <Navigate to="/dashboard" replace /> : <LandingPage />} />
      <Route path="/register" element={user ? <Navigate to="/dashboard" replace /> : <RegisterPage />} />
      <Route path="/setup" element={<Navigate to="/dashboard" replace />} />
      <Route path="/login" element={user ? <Navigate to="/dashboard" replace /> : <LoginPage />} />
      <Route path="/forgot-password" element={user ? <Navigate to="/dashboard" replace /> : <ForgotPasswordPage />} />
      <Route path="/reset-password" element={user ? <Navigate to="/dashboard" replace /> : <ResetPasswordPage />} />

      {/* Platform admin portal — separate entry point */}
      <Route path="/admin" element={<AdminLoginPage />} />

      {/* Document viewer (PIN-protected, no login required) */}
      <Route path="/doc" element={<DocViewerPage />} />
      <Route path="/doc/:code" element={<DocViewerPage />} />

      {/* Super admin panel (protected, requires is_super_admin) */}
      <Route path="/superadmin" element={<ProtectedRoute><SuperAdminPage /></ProtectedRoute>} />

      {/* Upgrade page (inside app) */}
      <Route path="/upgrade" element={<ProtectedRoute><UpgradePage /></ProtectedRoute>} />

      <Route path="/dashboard" element={<ProtectedRoute><DashboardPage /></ProtectedRoute>} />
      <Route path="/branches" element={<ProtectedRoute><BranchesPage /></ProtectedRoute>} />
      <Route path="/branch-transfers" element={<ProtectedRoute><FeatureGate featureKey="branch_transfers"><BranchTransferPage /></FeatureGate></ProtectedRoute>} />
      <Route path="/approve-transfer/:id" element={<ProtectedRoute><FeatureGate featureKey="branch_transfers"><ApproveTransferPage /></FeatureGate></ProtectedRoute>} />
      <Route path="/internal-invoices" element={<ProtectedRoute><InternalInvoicesPage /></ProtectedRoute>} />
      <Route path="/products" element={<ProtectedRoute><ProductsPage /></ProtectedRoute>} />
      <Route path="/products/repack-pricing" element={<ProtectedRoute><RepackPricingPage /></ProtectedRoute>} />
      <Route path="/products/:id" element={<ProtectedRoute><ProductDetailPage /></ProtectedRoute>} />
      <Route path="/inventory" element={<ProtectedRoute><InventoryPage /></ProtectedRoute>} />
      <Route path="/sales-new" element={<ProtectedRoute><UnifiedSalesPage /></ProtectedRoute>} />
      <Route path="/pos" element={<ProtectedRoute><UnifiedSalesPage /></ProtectedRoute>} />
      <Route path="/customers" element={<ProtectedRoute><CustomersPage /></ProtectedRoute>} />
      <Route path="/price-schemes" element={<ProtectedRoute><PriceSchemesPage /></ProtectedRoute>} />
      <Route path="/price-manager" element={<ProtectedRoute><PriceManagerPage /></ProtectedRoute>} />
      <Route path="/sales" element={<ProtectedRoute><SalesPage /></ProtectedRoute>} />
      <Route path="/sales-order" element={<ProtectedRoute><UnifiedSalesPage /></ProtectedRoute>} />
      <Route path="/purchase-orders" element={<ProtectedRoute><FeatureGate featureKey="purchase_orders"><PurchaseOrderPage /></FeatureGate></ProtectedRoute>} />
      <Route path="/suppliers" element={<ProtectedRoute><FeatureGate featureKey="supplier_management"><SuppliersPage /></FeatureGate></ProtectedRoute>} />
      <Route path="/daily-ops" element={<ProtectedRoute><DailyLogPage /></ProtectedRoute>} />
      <Route path="/close-wizard" element={<ProtectedRoute><CloseWizardPage /></ProtectedRoute>} />
      <Route path="/payments" element={<ProtectedRoute><PaymentsPage /></ProtectedRoute>} />
      <Route path="/notifications" element={<ProtectedRoute><NotificationsPage /></ProtectedRoute>} />
      <Route path="/fund-management" element={<ProtectedRoute><FeatureGate featureKey="full_fund_management"><FundManagementPage /></FeatureGate></ProtectedRoute>} />
      <Route path="/expenses" element={<ProtectedRoute><ExpensesPage /></ProtectedRoute>} />
      <Route path="/accounting" element={<ProtectedRoute><AccountingPage /></ProtectedRoute>} />
      <Route path="/journal-entries" element={<ProtectedRoute><JournalEntriesPage /></ProtectedRoute>} />
      <Route path="/documents" element={<ProtectedRoute><DocumentsPage /></ProtectedRoute>} />
      <Route path="/messages" element={<ProtectedRoute><MessagesPage /></ProtectedRoute>} />
      <Route path="/settings" element={<ProtectedRoute><SettingsPage /></ProtectedRoute>} />
      <Route path="/team" element={<ProtectedRoute><TeamPage /></ProtectedRoute>} />
      <Route path="/user-permissions" element={<ProtectedRoute><FeatureGate featureKey="granular_permissions"><UserPermissionsPage /></FeatureGate></ProtectedRoute>} />
      <Route path="/accounts" element={<ProtectedRoute><AccountsPage /></ProtectedRoute>} />
      <Route path="/employees" element={<ProtectedRoute><FeatureGate featureKey="employee_management"><EmployeesPage /></FeatureGate></ProtectedRoute>} />
      <Route path="/pay-supplier" element={<ProtectedRoute><FeatureGate featureKey="purchase_orders"><PaySupplierPage /></FeatureGate></ProtectedRoute>} />
      <Route path="/count-sheets" element={<ProtectedRoute><CountSheetsPage /></ProtectedRoute>} />
      <Route path="/pending-releases" element={<ProtectedRoute><PendingReleasesPage /></ProtectedRoute>} />
      <Route path="/import" element={<ProtectedRoute><ImportPage /></ProtectedRoute>} />
      <Route path="/reports" element={<ProtectedRoute><FeatureGate featureKey="advanced_reports"><ReportsPage /></FeatureGate></ProtectedRoute>} />
      <Route path="/returns" element={<ProtectedRoute><ReturnRefundWizard /></ProtectedRoute>} />
      <Route path="/audit" element={<ProtectedRoute><FeatureGate featureKey="audit_center"><AuditCenterPage /></FeatureGate></ProtectedRoute>} />
      <Route path="/incident-tickets" element={<ProtectedRoute><IncidentTicketsPage /></ProtectedRoute>} />
      <Route path="/backups" element={<ProtectedRoute><BackupManagementPage /></ProtectedRoute>} />
      <Route path="/downloads" element={<ProtectedRoute><AppDownloadsPage /></ProtectedRoute>} />
      <Route path="/barcode-print" element={<ProtectedRoute><BarcodePrintPage /></ProtectedRoute>} />
      <Route path="/barcode-manage" element={<ProtectedRoute><BarcodeManagePage /></ProtectedRoute>} />
      <Route path="/find-transaction" element={<ProtectedRoute><TransactionSearchPage /></ProtectedRoute>} />
      <Route path="/crop-credits" element={<ProtectedRoute><CropCreditsPage /></ProtectedRoute>} />
      <Route path="/print-center" element={<ProtectedRoute><PrintQueuePage /></ProtectedRoute>} />
      <Route path="/scanner/:sessionId" element={<MobileScannerPage />} />
      {/* Public upload page — no auth, token-based */}
      <Route path="/upload/:token" element={<UploadPage />} />
      <Route path="/doc-upload/:token" element={<DocUploadPage />} />
      {/* Public signature page — no auth, token-based */}
      <Route path="/sign/:token" element={<SignaturePage />} />
      {/* Public view-receipts page — no auth, token-based */}
      <Route path="/view-receipts/:token" element={<ViewReceiptsPage />} />
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
    </>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* Terminal route — independent of main auth, manages its own session */}
        <Route path="/terminal" element={<TerminalPage />} />
        {/* All other routes go through AuthProvider */}
        <Route path="*" element={
          <AuthProvider>
            <UnsavedChangesProvider>
              <AppRoutes />
              <Toaster position="bottom-right" richColors />
            </UnsavedChangesProvider>
          </AuthProvider>
        } />
      </Routes>
    </BrowserRouter>
  );
}
