import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../contexts/AuthContext';
import { fmtDate } from '../lib/utils';
import { Card, CardContent } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Badge } from '../components/ui/badge';
import { Input } from '../components/ui/input';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { Checkbox } from '../components/ui/checkbox';
import { ScrollArea } from '../components/ui/scroll-area';
import {
  FolderOpen, Upload, Search, FileText, ChevronRight, ChevronLeft,
  Building2, Landmark, Receipt, Users, Leaf, Folder, Trash2, Download,
  Eye, Calendar, Shield, Clock, X, Plus, Edit2, ArrowLeft, QrCode,
  CheckCircle2, AlertTriangle, File, Image, FileSpreadsheet
} from 'lucide-react';
import { toast } from 'sonner';

const API = process.env.REACT_APP_BACKEND_URL;

const CATEGORY_ICONS = {
  business_registration: Building2,
  lgu_permits: Landmark,
  bir: Receipt,
  employer_compliance: Users,
  agrivet: Leaf,
  other: Folder,
};

const MONTH_NAMES = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

const QUARTER_OPTIONS = ['Q1', 'Q2', 'Q3', 'Q4'];

function formatFileSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

function getFileIcon(contentType) {
  if (contentType?.startsWith('image/')) return Image;
  if (contentType?.includes('spreadsheet') || contentType?.includes('excel')) return FileSpreadsheet;
  if (contentType?.includes('pdf')) return FileText;
  return File;
}

// ─────────────────────────────────────────────────────────────────────────────
//  Main Page
// ─────────────────────────────────────────────────────────────────────────────

export default function DocumentsPage() {
  const { user, token, branches, selectedBranchId } = useAuth();

  const [categories, setCategories] = useState({});
  const [documents, setDocuments] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');

  // Navigation state
  const [selectedCategory, setSelectedCategory] = useState(null);
  const [selectedSubCategory, setSelectedSubCategory] = useState(null);
  const [selectedYear, setSelectedYear] = useState(new Date().getFullYear());

  // Upload dialog
  const [uploadOpen, setUploadOpen] = useState(false);

  // Document detail / preview
  const [previewDoc, setPreviewDoc] = useState(null);

  // Edit dialog
  const [editDoc, setEditDoc] = useState(null);

  // QR dialog
  const [qrDialog, setQrDialog] = useState(false);

  // Compliance summary
  const [compliance, setCompliance] = useState(null);

  const fetchCategories = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/documents/categories`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) setCategories(await res.json());
    } catch {}
  }, [token]);

  const fetchDocuments = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (selectedCategory) params.set('category', selectedCategory);
      if (selectedSubCategory) params.set('sub_category', selectedSubCategory);
      if (selectedBranchId && selectedBranchId !== 'all') params.set('branch_id', selectedBranchId);
      if (selectedYear) params.set('year', selectedYear);
      if (search) params.set('search', search);
      params.set('limit', '100');

      const res = await fetch(`${API}/api/documents?${params}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        const data = await res.json();
        setDocuments(data.documents);
        setTotal(data.total);
      }
    } catch {
      toast.error('Failed to load documents');
    } finally {
      setLoading(false);
    }
  }, [token, selectedCategory, selectedSubCategory, selectedBranchId, selectedYear, search]);

  useEffect(() => { fetchCategories(); }, [fetchCategories]);
  useEffect(() => { fetchDocuments(); }, [fetchDocuments]);

  const fetchCompliance = useCallback(async () => {
    try {
      const params = new URLSearchParams({ year: selectedYear });
      if (selectedBranchId && selectedBranchId !== 'all') params.set('branch_id', selectedBranchId);
      const res = await fetch(`${API}/api/documents/compliance/summary?${params}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) setCompliance(await res.json());
    } catch {}
  }, [token, selectedYear, selectedBranchId]);

  useEffect(() => { fetchCompliance(); }, [fetchCompliance]);

  const handleDelete = async (docId) => {
    if (!window.confirm('Delete this document and all its files?')) return;
    try {
      const res = await fetch(`${API}/api/documents/${docId}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.ok) {
        toast.success('Document deleted');
        fetchDocuments();
        if (previewDoc?.id === docId) setPreviewDoc(null);
      } else {
        const err = await res.json();
        toast.error(err.detail || 'Delete failed');
      }
    } catch { toast.error('Delete failed'); }
  };

  // Breadcrumb navigation
  const breadcrumbs = [];
  breadcrumbs.push({ label: 'All Documents', onClick: () => { setSelectedCategory(null); setSelectedSubCategory(null); } });
  if (selectedCategory && categories[selectedCategory]) {
    breadcrumbs.push({
      label: categories[selectedCategory].label,
      onClick: () => setSelectedSubCategory(null),
    });
  }
  if (selectedSubCategory && selectedCategory && categories[selectedCategory]) {
    const sub = categories[selectedCategory].sub_categories[selectedSubCategory];
    if (sub) breadcrumbs.push({ label: sub.label });
  }

  // Compute folder counts for category view
  const catCounts = {};
  if (!selectedCategory) {
    documents.forEach(d => {
      catCounts[d.category] = (catCounts[d.category] || 0) + 1;
    });
  }

  const subCatCounts = {};
  if (selectedCategory && !selectedSubCategory) {
    documents.forEach(d => {
      if (d.category === selectedCategory) {
        subCatCounts[d.sub_category] = (subCatCounts[d.sub_category] || 0) + 1;
      }
    });
  }

  return (
    <div className="space-y-4" data-testid="documents-page">
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight" data-testid="documents-title">Documents</h1>
          <p className="text-sm text-muted-foreground mt-0.5">Business document cloud — upload, organize, track compliance</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => setQrDialog(true)} data-testid="qr-upload-btn">
            <QrCode className="h-4 w-4 mr-1.5" /> Upload via Phone
          </Button>
          <Button size="sm" onClick={() => setUploadOpen(true)} data-testid="upload-btn">
            <Upload className="h-4 w-4 mr-1.5" /> Upload Document
          </Button>
        </div>
      </div>

      {/* Search & Filters */}
      <div className="flex flex-col sm:flex-row gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search documents by name, tags, description..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="pl-9"
            data-testid="doc-search-input"
          />
        </div>
        <Select value={String(selectedYear)} onValueChange={v => setSelectedYear(Number(v))}>
          <SelectTrigger className="w-[120px]" data-testid="year-select">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {[2022, 2023, 2024, 2025, 2026, 2027].map(y => (
              <SelectItem key={y} value={String(y)}>{y}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Compliance Dashboard — visible on root view */}
      {!selectedCategory && !search && compliance && (
        <ComplianceDashboard compliance={compliance} categories={categories} year={selectedYear} />
      )}

      {/* Breadcrumb */}
      <div className="flex items-center gap-1 text-sm" data-testid="doc-breadcrumb">
        {breadcrumbs.map((bc, i) => (
          <span key={i} className="flex items-center gap-1">
            {i > 0 && <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />}
            {bc.onClick ? (
              <button
                onClick={bc.onClick}
                className="text-blue-600 hover:underline font-medium"
              >
                {bc.label}
              </button>
            ) : (
              <span className="text-muted-foreground">{bc.label}</span>
            )}
          </span>
        ))}
        <span className="text-muted-foreground ml-2">({total})</span>
      </div>

      {/* Category Folders View */}
      {!selectedCategory && !search && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3" data-testid="category-grid">
          {Object.entries(categories).map(([key, cat]) => {
            const Icon = CATEGORY_ICONS[key] || Folder;
            const count = catCounts[key] || 0;
            const isAudit = cat.audit_sensitive;
            return (
              <Card
                key={key}
                className="cursor-pointer hover:border-blue-400 hover:shadow-md transition-all group"
                onClick={() => setSelectedCategory(key)}
                data-testid={`cat-folder-${key}`}
              >
                <CardContent className="p-4 text-center">
                  <div className={`mx-auto w-12 h-12 rounded-xl flex items-center justify-center mb-2 ${
                    isAudit ? 'bg-red-50 text-red-600' : 'bg-blue-50 text-blue-600'
                  } group-hover:scale-110 transition-transform`}>
                    <Icon className="h-6 w-6" />
                  </div>
                  <p className="text-xs font-semibold leading-tight">{cat.label}</p>
                  <p className="text-[11px] text-muted-foreground mt-1">{count} doc{count !== 1 ? 's' : ''}</p>
                  {isAudit && (
                    <Badge variant="destructive" className="text-[9px] mt-1 px-1.5 py-0">AUDIT</Badge>
                  )}
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}

      {/* Sub-category Folders */}
      {selectedCategory && !selectedSubCategory && !search && categories[selectedCategory] && (
        <div className="space-y-3">
          <Button variant="ghost" size="sm" onClick={() => setSelectedCategory(null)} data-testid="back-to-categories">
            <ArrowLeft className="h-4 w-4 mr-1" /> Back
          </Button>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3" data-testid="subcat-grid">
            {Object.entries(categories[selectedCategory].sub_categories).map(([key, sub]) => {
              const count = subCatCounts[key] || 0;
              const isAudit = sub.audit_sensitive;
              const periodIcon = sub.period_type === 'monthly' ? Calendar :
                sub.period_type === 'validity' ? Clock :
                sub.period_type === 'annual' ? Calendar : FileText;
              const PIcon = periodIcon;
              return (
                <Card
                  key={key}
                  className="cursor-pointer hover:border-blue-400 hover:shadow-md transition-all group"
                  onClick={() => setSelectedSubCategory(key)}
                  data-testid={`subcat-folder-${key}`}
                >
                  <CardContent className="p-3">
                    <div className="flex items-start gap-2">
                      <div className={`w-9 h-9 rounded-lg flex items-center justify-center shrink-0 ${
                        isAudit ? 'bg-red-50 text-red-500' : 'bg-slate-100 text-slate-500'
                      }`}>
                        <PIcon className="h-4 w-4" />
                      </div>
                      <div className="min-w-0">
                        <p className="text-xs font-semibold leading-tight truncate">{sub.label}</p>
                        <div className="flex items-center gap-1.5 mt-1">
                          <Badge variant="secondary" className="text-[9px] px-1.5 py-0">{sub.period_type}</Badge>
                          <span className="text-[10px] text-muted-foreground">{count} file{count !== 1 ? 's' : ''}</span>
                        </div>
                        {isAudit && (
                          <Badge variant="destructive" className="text-[9px] mt-1 px-1.5 py-0">AUDIT-SENSITIVE</Badge>
                        )}
                      </div>
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </div>
      )}

      {/* Document List (when inside a sub-category or searching) */}
      {(selectedSubCategory || search) && (
        <div className="space-y-2">
          {selectedSubCategory && (
            <Button variant="ghost" size="sm" onClick={() => setSelectedSubCategory(null)} data-testid="back-to-subcats">
              <ArrowLeft className="h-4 w-4 mr-1" /> Back
            </Button>
          )}

          {/* Monthly grid for monthly period types */}
          {selectedSubCategory && categories[selectedCategory]?.sub_categories[selectedSubCategory]?.period_type === 'monthly' && !search && (
            <MonthlyGrid
              documents={documents}
              year={selectedYear}
              subCategory={selectedSubCategory}
              onMonthClick={(month) => {
                // Filter to specific month
              }}
            />
          )}

          {loading ? (
            <div className="text-center py-12 text-muted-foreground">Loading documents...</div>
          ) : documents.length === 0 ? (
            <Card>
              <CardContent className="py-12 text-center">
                <FolderOpen className="h-12 w-12 text-muted-foreground/30 mx-auto mb-3" />
                <p className="text-muted-foreground">No documents found</p>
                <Button size="sm" className="mt-3" onClick={() => setUploadOpen(true)}>
                  <Upload className="h-4 w-4 mr-1.5" /> Upload First Document
                </Button>
              </CardContent>
            </Card>
          ) : (
            <div className="space-y-2" data-testid="doc-list">
              {documents.map(doc => (
                <DocumentRow
                  key={doc.id}
                  doc={doc}
                  onPreview={() => setPreviewDoc(doc)}
                  onEdit={() => setEditDoc(doc)}
                  onDelete={() => handleDelete(doc.id)}
                  userRole={user?.role}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {/* Upload Dialog */}
      {uploadOpen && (
        <UploadDialog
          open={uploadOpen}
          onClose={() => setUploadOpen(false)}
          categories={categories}
          token={token}
          branchId={selectedBranchId}
          year={selectedYear}
          preselectedCategory={selectedCategory}
          preselectedSubCategory={selectedSubCategory}
          onSuccess={() => { setUploadOpen(false); fetchDocuments(); }}
        />
      )}

      {/* Preview Dialog */}
      {previewDoc && (
        <PreviewDialog
          doc={previewDoc}
          token={token}
          onClose={() => setPreviewDoc(null)}
          onEdit={() => { setEditDoc(previewDoc); setPreviewDoc(null); }}
          onDelete={() => { handleDelete(previewDoc.id); setPreviewDoc(null); }}
          userRole={user?.role}
        />
      )}

      {/* Edit Dialog */}
      {editDoc && (
        <EditDialog
          doc={editDoc}
          token={token}
          categories={categories}
          onClose={() => setEditDoc(null)}
          onSuccess={() => { setEditDoc(null); fetchDocuments(); }}
        />
      )}

      {/* QR Upload Dialog */}
      {qrDialog && (
        <QRUploadDialog
          open={qrDialog}
          onClose={() => setQrDialog(false)}
          categories={categories}
          token={token}
          branchId={selectedBranchId}
          year={selectedYear}
          preselectedCategory={selectedCategory}
          preselectedSubCategory={selectedSubCategory}
        />
      )}
    </div>
  );
}



// ─────────────────────────────────────────────────────────────────────────────
//  Compliance Dashboard — at-a-glance filing status + expiry alerts
// ─────────────────────────────────────────────────────────────────────────────

const TRACKED_MONTHLY = [
  { key: 'sss_contributions', label: 'SSS', barColor: 'bg-blue-400' },
  { key: 'philhealth_contributions', label: 'PhilHealth', barColor: 'bg-emerald-400' },
  { key: 'pagibig_contributions', label: 'Pag-IBIG', barColor: 'bg-amber-400' },
  { key: '1601c', label: 'BIR 1601-C', barColor: 'bg-purple-400' },
  { key: '0619e', label: 'BIR 0619-E', barColor: 'bg-purple-400' },
  { key: '2550m', label: 'BIR 2550M', barColor: 'bg-purple-400' },
];

function ComplianceDashboard({ compliance, categories, year }) {
  if (!compliance) return null;

  const { monthly_coverage, expiring_soon, expired } = compliance;
  const currentMonth = new Date().getMonth() + 1; // 1-12
  const hasAnyData = Object.keys(monthly_coverage).length > 0 || expiring_soon.length > 0 || expired.length > 0;

  // Calculate how many months should be filed (up to current month for current year, all 12 for past years)
  const isCurrentYear = year === new Date().getFullYear();
  const expectedMonths = isCurrentYear ? currentMonth : 12;

  return (
    <div className="space-y-3" data-testid="compliance-dashboard">
      {/* Expiry Alerts */}
      {expired.length > 0 && (
        <div className="flex items-start gap-2 p-3 bg-red-50 border border-red-200 rounded-lg" data-testid="expired-alert">
          <AlertTriangle className="h-4 w-4 text-red-600 mt-0.5 shrink-0" />
          <div>
            <p className="text-xs font-semibold text-red-700">Expired Documents ({expired.length})</p>
            <div className="flex flex-wrap gap-1.5 mt-1">
              {expired.map((e, i) => (
                <Badge key={i} variant="destructive" className="text-[10px]">
                  {e.sub_category_label} — expired {e.valid_until}
                </Badge>
              ))}
            </div>
          </div>
        </div>
      )}

      {expiring_soon.length > 0 && (
        <div className="flex items-start gap-2 p-3 bg-amber-50 border border-amber-200 rounded-lg" data-testid="expiring-alert">
          <Clock className="h-4 w-4 text-amber-600 mt-0.5 shrink-0" />
          <div>
            <p className="text-xs font-semibold text-amber-700">Expiring Soon ({expiring_soon.length})</p>
            <div className="flex flex-wrap gap-1.5 mt-1">
              {expiring_soon.map((e, i) => (
                <Badge key={i} className="text-[10px] bg-amber-100 text-amber-700">
                  {e.sub_category_label} — {e.days_left}d left (expires {e.valid_until})
                </Badge>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Monthly Filing Tracker */}
      {(hasAnyData || isCurrentYear) && (
        <Card data-testid="monthly-tracker">
          <CardContent className="p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold">Monthly Filing Tracker — {year}</h3>
              <span className="text-[10px] text-muted-foreground">
                {isCurrentYear ? `Through ${MONTH_NAMES[currentMonth - 1]}` : 'Full Year'}
              </span>
            </div>
            <div className="space-y-2">
              {TRACKED_MONTHLY.map(({ key, label, barColor }) => {
                const covered = monthly_coverage[key] || [];
                const filedCount = covered.filter(m => m <= expectedMonths).length;
                const pct = expectedMonths > 0 ? Math.round((filedCount / expectedMonths) * 100) : 0;
                const allFiled = filedCount >= expectedMonths;

                return (
                  <div key={key} className="flex items-center gap-3" data-testid={`tracker-${key}`}>
                    <span className="text-xs font-medium w-24 shrink-0">{label}</span>
                    <div className="flex-1 h-5 bg-slate-100 rounded-full overflow-hidden relative">
                      <div
                        className={`h-full rounded-full transition-all ${
                          allFiled ? 'bg-emerald-500' : pct > 0 ? barColor : 'bg-slate-200'
                        }`}
                        style={{ width: `${Math.max(pct, 2)}%` }}
                      />
                      <div className="absolute inset-0 flex items-center px-2">
                        <div className="flex gap-[3px]">
                          {Array.from({ length: 12 }, (_, i) => {
                            const m = i + 1;
                            const isFiled = covered.includes(m);
                            const isPast = m <= expectedMonths;
                            return (
                              <div
                                key={m}
                                className={`w-[6px] h-[6px] rounded-full ${
                                  isFiled ? 'bg-emerald-600' :
                                  isPast ? 'bg-red-400' :
                                  'bg-slate-300'
                                }`}
                                title={`${MONTH_NAMES[i]} — ${isFiled ? 'Filed' : isPast ? 'Missing' : 'Upcoming'}`}
                              />
                            );
                          })}
                        </div>
                      </div>
                    </div>
                    <span className={`text-[11px] font-semibold w-16 text-right ${allFiled ? 'text-emerald-600' : filedCount > 0 ? 'text-amber-600' : 'text-slate-400'}`}>
                      {filedCount}/{expectedMonths}
                    </span>
                  </div>
                );
              })}
            </div>
            <div className="flex items-center gap-4 mt-3 pt-2 border-t text-[10px] text-muted-foreground">
              <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-emerald-600" /> Filed</span>
              <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-red-400" /> Missing</span>
              <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-slate-300" /> Upcoming</span>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}



// ─────────────────────────────────────────────────────────────────────────────
//  Monthly Grid — shows 12 months with filed/missing status
// ─────────────────────────────────────────────────────────────────────────────

function MonthlyGrid({ documents, year, subCategory }) {
  const coveredMonths = new Set();
  documents.forEach(d => {
    if (d.sub_category === subCategory && d.year === year) {
      (d.coverage_months || []).forEach(m => coveredMonths.add(m));
    }
  });

  return (
    <div className="grid grid-cols-4 sm:grid-cols-6 lg:grid-cols-12 gap-2 mb-3" data-testid="monthly-grid">
      {MONTH_NAMES.map((name, i) => {
        const month = i + 1;
        const covered = coveredMonths.has(month);
        return (
          <div
            key={month}
            className={`rounded-lg p-2 text-center text-xs border ${
              covered
                ? 'bg-emerald-50 border-emerald-200 text-emerald-700'
                : 'bg-slate-50 border-slate-200 text-slate-400'
            }`}
            data-testid={`month-${month}-${covered ? 'filed' : 'missing'}`}
          >
            <div className="font-semibold">{name.slice(0, 3)}</div>
            {covered ? (
              <CheckCircle2 className="h-4 w-4 mx-auto mt-1 text-emerald-500" />
            ) : (
              <X className="h-4 w-4 mx-auto mt-1 text-slate-300" />
            )}
          </div>
        );
      })}
    </div>
  );
}


// ─────────────────────────────────────────────────────────────────────────────
//  Document Row
// ─────────────────────────────────────────────────────────────────────────────

function DocumentRow({ doc, onPreview, onEdit, onDelete, userRole }) {
  const FileIcon = doc.files?.[0] ? getFileIcon(doc.files[0].content_type) : FileText;
  const totalSize = (doc.files || []).reduce((s, f) => s + (f.size || 0), 0);
  const isAudit = doc.audit_sensitive;

  return (
    <Card className="hover:shadow-sm transition-shadow" data-testid={`doc-row-${doc.id}`}>
      <CardContent className="p-3">
        <div className="flex items-center gap-3">
          <div className={`w-10 h-10 rounded-lg flex items-center justify-center shrink-0 ${
            isAudit ? 'bg-red-50 text-red-500' : 'bg-blue-50 text-blue-500'
          }`}>
            <FileIcon className="h-5 w-5" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <p className="text-sm font-semibold truncate">{doc.name}</p>
              {isAudit && <Badge variant="destructive" className="text-[9px] px-1.5 py-0 shrink-0">AUDIT</Badge>}
              {doc.valid_until && (() => {
                const daysLeft = Math.floor((new Date(doc.valid_until) - new Date()) / 86400000);
                if (daysLeft < 0) return <Badge variant="destructive" className="text-[9px] px-1.5 py-0 shrink-0">EXPIRED</Badge>;
                if (daysLeft <= 30) return <Badge className="text-[9px] px-1.5 py-0 bg-amber-100 text-amber-700 shrink-0">{daysLeft}d left</Badge>;
                return null;
              })()}
            </div>
            <div className="flex items-center gap-2 mt-0.5">
              <span className="text-[11px] text-muted-foreground">{doc.sub_category_label}</span>
              {doc.coverage_months?.length > 0 && (
                <span className="text-[11px] text-muted-foreground">
                  {doc.coverage_months.map(m => MONTH_NAMES[m-1]?.slice(0,3)).join(', ')}
                </span>
              )}
              <span className="text-[11px] text-muted-foreground">{doc.file_count} file{doc.file_count !== 1 ? 's' : ''} · {formatFileSize(totalSize)}</span>
            </div>
            {doc.tags?.length > 0 && (
              <div className="flex gap-1 mt-1 flex-wrap">
                {doc.tags.map(t => <Badge key={t} variant="outline" className="text-[9px] px-1.5 py-0">{t}</Badge>)}
              </div>
            )}
          </div>
          <div className="flex items-center gap-1 shrink-0">
            <Button variant="ghost" size="icon" className="h-8 w-8" onClick={onPreview} data-testid={`preview-doc-${doc.id}`}>
              <Eye className="h-4 w-4" />
            </Button>
            <Button variant="ghost" size="icon" className="h-8 w-8" onClick={onEdit} data-testid={`edit-doc-${doc.id}`}>
              <Edit2 className="h-4 w-4" />
            </Button>
            {(userRole === 'admin' || userRole === 'manager') && (
              <Button variant="ghost" size="icon" className="h-8 w-8 text-red-500 hover:text-red-700" onClick={onDelete} data-testid={`delete-doc-${doc.id}`}>
                <Trash2 className="h-4 w-4" />
              </Button>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}


// ─────────────────────────────────────────────────────────────────────────────
//  Upload Dialog
// ─────────────────────────────────────────────────────────────────────────────

function UploadDialog({ open, onClose, categories, token, branchId, year, preselectedCategory, preselectedSubCategory, onSuccess }) {
  const [category, setCategory] = useState(preselectedCategory || '');
  const [subCategory, setSubCategory] = useState(preselectedSubCategory || '');
  const [docName, setDocName] = useState('');
  const [description, setDescription] = useState('');
  const [docYear, setDocYear] = useState(year || new Date().getFullYear());
  const [coverageMonths, setCoverageMonths] = useState([]);
  const [coverageQuarter, setCoverageQuarter] = useState('');
  const [validFrom, setValidFrom] = useState('');
  const [validUntil, setValidUntil] = useState('');
  const [tags, setTags] = useState('');
  const [employeeName, setEmployeeName] = useState('');
  const [files, setFiles] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [qrMode, setQrMode] = useState(false);
  const [qrData, setQrData] = useState(null);
  const [generatingQr, setGeneratingQr] = useState(false);

  const subCatDef = category && subCategory ? categories[category]?.sub_categories?.[subCategory] : null;
  const periodType = subCatDef?.period_type || 'one_time';

  const toggleMonth = (m) => {
    setCoverageMonths(prev => prev.includes(m) ? prev.filter(x => x !== m) : [...prev, m].sort((a, b) => a - b));
  };

  const handleFiles = (fileList) => {
    setFiles(prev => [...prev, ...Array.from(fileList)]);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    handleFiles(e.dataTransfer.files);
  };

  const handleSubmit = async () => {
    if (!category || !subCategory) return toast.error('Select a category and sub-category');
    if (files.length === 0) return toast.error('Add at least one file');

    setUploading(true);
    try {
      const fd = new FormData();
      files.forEach(f => fd.append('files', f));
      fd.append('category', category);
      fd.append('sub_category', subCategory);
      fd.append('name', docName);
      fd.append('description', description);
      fd.append('branch_id', branchId || '');
      fd.append('year', String(docYear));
      fd.append('coverage_months', coverageMonths.join(','));
      fd.append('coverage_quarter', coverageQuarter);
      fd.append('valid_from', validFrom);
      fd.append('valid_until', validUntil);
      fd.append('tags', tags);
      fd.append('employee_name', employeeName);

      const res = await fetch(`${API}/api/documents`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: fd,
      });

      if (res.ok) {
        toast.success('Document uploaded successfully');
        onSuccess();
      } else {
        const err = await res.json();
        toast.error(err.detail || 'Upload failed');
      }
    } catch { toast.error('Upload failed'); }
    finally { setUploading(false); }
  };

  const handleGenerateQR = async () => {
    if (!category || !subCategory) return toast.error('Select a category and type first');
    setGeneratingQr(true);
    try {
      const res = await fetch(`${API}/api/documents/qr-upload-token`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          category,
          sub_category: subCategory,
          branch_id: branchId || '',
          year: docYear,
          coverage_months: coverageMonths,
        }),
      });
      if (res.ok) {
        setQrData(await res.json());
        setQrMode(true);
      } else { toast.error('Failed to generate QR code'); }
    } catch { toast.error('Failed to generate QR code'); }
    finally { setGeneratingQr(false); }
  };

  const qrUploadUrl = qrData ? `${API}/doc-upload/${qrData.token}` : '';

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto" data-testid="upload-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Upload className="h-5 w-5 text-blue-600" /> Upload Document
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-4 mt-2">
          {/* Category */}
          <div>
            <label className="text-xs font-medium text-muted-foreground mb-1 block">Category</label>
            <Select value={category} onValueChange={v => { setCategory(v); setSubCategory(''); }}>
              <SelectTrigger data-testid="upload-category-select">
                <SelectValue placeholder="Select category..." />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(categories).map(([key, cat]) => (
                  <SelectItem key={key} value={key}>{cat.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Sub-category */}
          {category && (
            <div>
              <label className="text-xs font-medium text-muted-foreground mb-1 block">Document Type</label>
              <Select value={subCategory} onValueChange={setSubCategory}>
                <SelectTrigger data-testid="upload-subcategory-select">
                  <SelectValue placeholder="Select type..." />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(categories[category]?.sub_categories || {}).map(([key, sub]) => (
                    <SelectItem key={key} value={key}>{sub.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          {/* Period-specific fields — Year + Period grouped together */}
          {subCategory && periodType === 'monthly' && (
            <div className="space-y-3 p-3 bg-slate-50 rounded-lg border border-slate-200">
              <div>
                <label className="text-xs font-medium text-muted-foreground mb-1 block">Filing Year</label>
                <Select value={String(docYear)} onValueChange={v => setDocYear(Number(v))}>
                  <SelectTrigger data-testid="upload-year-select">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {[2022, 2023, 2024, 2025, 2026, 2027].map(y => <SelectItem key={y} value={String(y)}>{y}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground mb-1 block">
                  Coverage Month(s) — select all that apply
                </label>
                <div className="grid grid-cols-4 gap-1.5" data-testid="month-selector">
                  {MONTH_NAMES.map((name, i) => {
                    const m = i + 1;
                    const selected = coverageMonths.includes(m);
                    return (
                      <button
                        key={m}
                        type="button"
                        onClick={() => toggleMonth(m)}
                        className={`rounded-md px-2 py-1.5 text-xs font-medium border transition-colors ${
                          selected
                            ? 'bg-blue-600 text-white border-blue-600'
                            : 'bg-white text-slate-600 border-slate-200 hover:border-blue-300'
                        }`}
                        data-testid={`month-btn-${m}`}
                      >
                        {name.slice(0, 3)}
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>
          )}

          {subCategory && periodType === 'quarterly' && (
            <div className="space-y-3 p-3 bg-slate-50 rounded-lg border border-slate-200">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs font-medium text-muted-foreground mb-1 block">Filing Year</label>
                  <Select value={String(docYear)} onValueChange={v => setDocYear(Number(v))}>
                    <SelectTrigger data-testid="upload-year-select">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {[2022, 2023, 2024, 2025, 2026, 2027].map(y => <SelectItem key={y} value={String(y)}>{y}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground mb-1 block">Quarter</label>
                  <Select value={coverageQuarter} onValueChange={setCoverageQuarter}>
                    <SelectTrigger data-testid="quarter-select">
                      <SelectValue placeholder="Select quarter..." />
                    </SelectTrigger>
                    <SelectContent>
                      {QUARTER_OPTIONS.map(q => <SelectItem key={q} value={q}>{q}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
              </div>
            </div>
          )}

          {subCategory && periodType === 'annual' && (
            <div className="space-y-3 p-3 bg-slate-50 rounded-lg border border-slate-200">
              <div>
                <label className="text-xs font-medium text-muted-foreground mb-1 block">Filing Year</label>
                <Select value={String(docYear)} onValueChange={v => setDocYear(Number(v))}>
                  <SelectTrigger data-testid="upload-year-select">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {[2022, 2023, 2024, 2025, 2026, 2027].map(y => <SelectItem key={y} value={String(y)}>{y}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs font-medium text-muted-foreground mb-1 block">Valid From</label>
                  <Input type="date" value={validFrom} onChange={e => setValidFrom(e.target.value)} data-testid="valid-from" />
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground mb-1 block">Valid Until</label>
                  <Input type="date" value={validUntil} onChange={e => setValidUntil(e.target.value)} data-testid="valid-until" />
                </div>
              </div>
            </div>
          )}

          {subCategory && periodType === 'validity' && (
            <div className="space-y-3 p-3 bg-slate-50 rounded-lg border border-slate-200">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs font-medium text-muted-foreground mb-1 block">Valid From</label>
                  <Input type="date" value={validFrom} onChange={e => setValidFrom(e.target.value)} data-testid="valid-from" />
                </div>
                <div>
                  <label className="text-xs font-medium text-muted-foreground mb-1 block">Valid Until</label>
                  <Input type="date" value={validUntil} onChange={e => setValidUntil(e.target.value)} data-testid="valid-until" />
                </div>
              </div>
            </div>
          )}

          {/* Year for one_time types only (no period grouping needed) */}
          {subCategory && periodType === 'one_time' && (
            <div>
              <label className="text-xs font-medium text-muted-foreground mb-1 block">Year</label>
              <Select value={String(docYear)} onValueChange={v => setDocYear(Number(v))}>
                <SelectTrigger data-testid="upload-year-select">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {[2022, 2023, 2024, 2025, 2026, 2027].map(y => <SelectItem key={y} value={String(y)}>{y}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          )}

          {/* Name & Description */}
          <div>
            <label className="text-xs font-medium text-muted-foreground mb-1 block">Document Name (auto-generated if blank)</label>
            <Input value={docName} onChange={e => setDocName(e.target.value)} placeholder="Optional custom name..." data-testid="upload-name" />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground mb-1 block">Description / Notes</label>
            <Input value={description} onChange={e => setDescription(e.target.value)} placeholder="Optional..." data-testid="upload-desc" />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground mb-1 block">Tags (comma-separated)</label>
            <Input value={tags} onChange={e => setTags(e.target.value)} placeholder="e.g. urgent, original, copy" data-testid="upload-tags" />
          </div>

          {/* File Drop Zone */}
          <div
            onDragOver={e => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
            className={`border-2 border-dashed rounded-xl p-6 text-center transition-colors cursor-pointer ${
              dragOver ? 'border-blue-400 bg-blue-50' : 'border-slate-200 hover:border-blue-300'
            }`}
            onClick={() => document.getElementById('doc-file-input').click()}
            data-testid="file-drop-zone"
          >
            <Upload className="h-8 w-8 text-muted-foreground/40 mx-auto mb-2" />
            <p className="text-sm text-muted-foreground">Drag & drop files here, or click to browse</p>
            <p className="text-[11px] text-muted-foreground mt-1">PDF, Images, Word, Excel — max 25MB each</p>
            <input
              id="doc-file-input"
              type="file"
              multiple
              accept=".pdf,.jpg,.jpeg,.png,.webp,.doc,.docx,.xls,.xlsx"
              className="hidden"
              onChange={e => handleFiles(e.target.files)}
            />
          </div>

          {/* Selected files */}
          {files.length > 0 && (
            <div className="space-y-1">
              {files.map((f, i) => (
                <div key={i} className="flex items-center gap-2 p-2 bg-slate-50 rounded-lg text-xs">
                  <FileText className="h-4 w-4 text-blue-500 shrink-0" />
                  <span className="truncate flex-1">{f.name}</span>
                  <span className="text-muted-foreground shrink-0">{formatFileSize(f.size)}</span>
                  <button onClick={() => setFiles(prev => prev.filter((_, j) => j !== i))} className="text-red-400 hover:text-red-600">
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </div>
          )}

          <Button
            className="w-full"
            onClick={handleSubmit}
            disabled={uploading || !category || !subCategory || files.length === 0}
            data-testid="upload-submit-btn"
          >
            {uploading ? 'Uploading...' : `Upload ${files.length} File${files.length !== 1 ? 's' : ''}`}
          </Button>

          {/* QR phone upload option */}
          {!qrMode ? (
            <div className="relative">
              <div className="absolute inset-0 flex items-center"><span className="w-full border-t" /></div>
              <div className="relative flex justify-center text-xs"><span className="bg-white px-2 text-muted-foreground">or</span></div>
            </div>
          ) : null}

          {!qrMode ? (
            <Button
              variant="outline"
              className="w-full"
              onClick={handleGenerateQR}
              disabled={generatingQr || !category || !subCategory}
              data-testid="upload-qr-btn"
            >
              {generatingQr ? 'Generating...' : (
                <><QrCode className="h-4 w-4 mr-1.5" /> Upload via Phone Instead</>
              )}
            </Button>
          ) : (
            <div className="text-center space-y-3 p-3 bg-slate-50 rounded-xl border" data-testid="upload-qr-inline">
              <p className="text-sm font-medium">Scan with your phone to upload</p>
              <div className="bg-white p-3 rounded-xl border inline-block mx-auto">
                <img
                  src={`https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=${encodeURIComponent(qrUploadUrl)}`}
                  alt="QR Code"
                  className="w-40 h-40"
                />
              </div>
              <div className="text-xs text-muted-foreground space-y-0.5">
                <p><strong>{categories[category]?.label}</strong> / {categories[category]?.sub_categories?.[subCategory]?.label}</p>
                {coverageMonths.length > 0 && (
                  <p>Coverage: {coverageMonths.map(m => MONTH_NAMES[m-1]?.slice(0,3)).join(', ')}</p>
                )}
                <p>Expires in 15 minutes</p>
              </div>
              <Button variant="ghost" size="sm" onClick={() => { setQrMode(false); setQrData(null); }}>
                Back to computer upload
              </Button>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}


// ─────────────────────────────────────────────────────────────────────────────
//  Preview Dialog
// ─────────────────────────────────────────────────────────────────────────────

function PreviewDialog({ doc, token, onClose, onEdit, onDelete, userRole }) {
  const [fileUrls, setFileUrls] = useState({});
  const [loadingUrls, setLoadingUrls] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch(`${API}/api/documents/${doc.id}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.ok) {
          const data = await res.json();
          const urls = {};
          (data.files || []).forEach(f => { urls[f.id] = f.url; });
          setFileUrls(urls);
        }
      } catch {}
      setLoadingUrls(false);
    })();
  }, [doc.id, token]);

  return (
    <Dialog open={true} onOpenChange={onClose}>
      <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto" data-testid="preview-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-base">
            <Eye className="h-5 w-5 text-blue-600" />
            {doc.name}
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-4 mt-2">
          {/* Metadata */}
          <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
            <div><span className="text-muted-foreground">Category:</span> <span className="font-medium ml-1">{doc.category_label}</span></div>
            <div><span className="text-muted-foreground">Type:</span> <span className="font-medium ml-1">{doc.sub_category_label}</span></div>
            <div><span className="text-muted-foreground">Year:</span> <span className="font-medium ml-1">{doc.year}</span></div>
            <div><span className="text-muted-foreground">Period:</span> <span className="font-medium ml-1">{doc.period_type}</span></div>
            {doc.coverage_months?.length > 0 && (
              <div className="col-span-2">
                <span className="text-muted-foreground">Coverage:</span>
                <span className="font-medium ml-1">{doc.coverage_months.map(m => MONTH_NAMES[m-1]).join(', ')}</span>
              </div>
            )}
            {doc.valid_from && <div><span className="text-muted-foreground">Valid From:</span> <span className="font-medium ml-1">{doc.valid_from}</span></div>}
            {doc.valid_until && (
              <div>
                <span className="text-muted-foreground">Valid Until:</span>
                <span className="font-medium ml-1">{doc.valid_until}</span>
                {(() => {
                  const d = Math.floor((new Date(doc.valid_until) - new Date()) / 86400000);
                  if (d < 0) return <Badge variant="destructive" className="ml-1 text-[9px]">EXPIRED</Badge>;
                  if (d <= 30) return <Badge className="ml-1 text-[9px] bg-amber-100 text-amber-700">{d}d left</Badge>;
                  return <Badge className="ml-1 text-[9px] bg-emerald-100 text-emerald-700">{d}d</Badge>;
                })()}
              </div>
            )}
            {doc.description && <div className="col-span-2"><span className="text-muted-foreground">Notes:</span> <span className="ml-1">{doc.description}</span></div>}
            <div><span className="text-muted-foreground">Uploaded by:</span> <span className="font-medium ml-1">{doc.uploaded_by_name}</span></div>
            <div><span className="text-muted-foreground">Date:</span> <span className="font-medium ml-1">{fmtDate(doc.created_at)}</span></div>
          </div>

          {doc.tags?.length > 0 && (
            <div className="flex gap-1 flex-wrap">
              {doc.tags.map(t => <Badge key={t} variant="outline" className="text-[10px]">{t}</Badge>)}
            </div>
          )}

          {doc.audit_sensitive && (
            <div className="flex items-center gap-2 p-2 bg-red-50 rounded-lg text-xs text-red-700">
              <Shield className="h-4 w-4" /> This document is audit-sensitive
            </div>
          )}

          {/* Files */}
          <div>
            <h4 className="text-xs font-semibold text-muted-foreground mb-2">Files ({doc.file_count})</h4>
            <div className="space-y-2">
              {(doc.files || []).map(f => {
                const url = fileUrls[f.id];
                const isImage = f.content_type?.startsWith('image/');
                const isPdf = f.content_type?.includes('pdf');
                const FIcon = getFileIcon(f.content_type);
                return (
                  <div key={f.id} className="border rounded-lg overflow-hidden">
                    {/* Image preview */}
                    {isImage && url && (
                      <div className="bg-slate-100 p-2 flex justify-center">
                        <img src={url} alt={f.filename} className="max-h-[300px] rounded object-contain" />
                      </div>
                    )}
                    {/* PDF embed */}
                    {isPdf && url && (
                      <iframe src={url} title={f.filename} className="w-full h-[400px] border-0" />
                    )}
                    <div className="flex items-center gap-2 p-2 text-xs bg-white">
                      <FIcon className="h-4 w-4 text-blue-500 shrink-0" />
                      <span className="truncate flex-1">{f.filename}</span>
                      <span className="text-muted-foreground">{formatFileSize(f.size)}</span>
                      {url && (
                        <a href={url} target="_blank" rel="noopener noreferrer" download className="text-blue-600 hover:underline">
                          <Download className="h-4 w-4" />
                        </a>
                      )}
                    </div>
                  </div>
                );
              })}
              {loadingUrls && <p className="text-xs text-muted-foreground">Loading file previews...</p>}
            </div>
          </div>

          <div className="flex gap-2 pt-2">
            <Button variant="outline" size="sm" onClick={onEdit}>
              <Edit2 className="h-4 w-4 mr-1" /> Edit
            </Button>
            {(userRole === 'admin' || userRole === 'manager') && (
              <Button variant="outline" size="sm" className="text-red-600 hover:text-red-700" onClick={onDelete}>
                <Trash2 className="h-4 w-4 mr-1" /> Delete
              </Button>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}


// ─────────────────────────────────────────────────────────────────────────────
//  Edit Dialog
// ─────────────────────────────────────────────────────────────────────────────

function EditDialog({ doc, token, categories, onClose, onSuccess }) {
  const [name, setName] = useState(doc.name || '');
  const [description, setDescription] = useState(doc.description || '');
  const [coverageMonths, setCoverageMonths] = useState(doc.coverage_months || []);
  const [coverageQuarter, setCoverageQuarter] = useState(doc.coverage_quarter || '');
  const [validFrom, setValidFrom] = useState(doc.valid_from || '');
  const [validUntil, setValidUntil] = useState(doc.valid_until || '');
  const [tagsStr, setTagsStr] = useState((doc.tags || []).join(', '));
  const [year, setYear] = useState(doc.year || new Date().getFullYear());
  const [saving, setSaving] = useState(false);

  const toggleMonth = (m) => {
    setCoverageMonths(prev => prev.includes(m) ? prev.filter(x => x !== m) : [...prev, m].sort((a, b) => a - b));
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const res = await fetch(`${API}/api/documents/${doc.id}`, {
        method: 'PUT',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name,
          description,
          coverage_months: coverageMonths,
          coverage_quarter: coverageQuarter,
          valid_from: validFrom,
          valid_until: validUntil,
          tags: tagsStr.split(',').map(t => t.trim()).filter(Boolean),
          year,
        }),
      });
      if (res.ok) {
        toast.success('Document updated');
        onSuccess();
      } else {
        const err = await res.json();
        toast.error(err.detail || 'Update failed');
      }
    } catch { toast.error('Update failed'); }
    finally { setSaving(false); }
  };

  return (
    <Dialog open={true} onOpenChange={onClose}>
      <DialogContent className="max-w-md max-h-[90vh] overflow-y-auto" data-testid="edit-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Edit2 className="h-5 w-5 text-blue-600" /> Edit Document
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-3 mt-2">
          <div>
            <label className="text-xs font-medium text-muted-foreground mb-1 block">Name</label>
            <Input value={name} onChange={e => setName(e.target.value)} data-testid="edit-name" />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground mb-1 block">Description</label>
            <Input value={description} onChange={e => setDescription(e.target.value)} data-testid="edit-desc" />
          </div>
          <div>
            <label className="text-xs font-medium text-muted-foreground mb-1 block">Year</label>
            <Select value={String(year)} onValueChange={v => setYear(Number(v))}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {[2022, 2023, 2024, 2025, 2026, 2027].map(y => <SelectItem key={y} value={String(y)}>{y}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>

          {doc.period_type === 'monthly' && (
            <div>
              <label className="text-xs font-medium text-muted-foreground mb-1 block">Coverage Months</label>
              <div className="grid grid-cols-4 gap-1.5">
                {MONTH_NAMES.map((mn, i) => {
                  const m = i + 1;
                  const sel = coverageMonths.includes(m);
                  return (
                    <button
                      key={m}
                      type="button"
                      onClick={() => toggleMonth(m)}
                      className={`rounded-md px-2 py-1.5 text-xs font-medium border transition-colors ${
                        sel ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-slate-600 border-slate-200 hover:border-blue-300'
                      }`}
                    >
                      {mn.slice(0, 3)}
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {doc.period_type === 'quarterly' && (
            <div>
              <label className="text-xs font-medium text-muted-foreground mb-1 block">Quarter</label>
              <Select value={coverageQuarter} onValueChange={setCoverageQuarter}>
                <SelectTrigger><SelectValue placeholder="Select..." /></SelectTrigger>
                <SelectContent>
                  {QUARTER_OPTIONS.map(q => <SelectItem key={q} value={q}>{q}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          )}

          {(doc.period_type === 'validity' || doc.period_type === 'annual') && (
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-medium text-muted-foreground mb-1 block">Valid From</label>
                <Input type="date" value={validFrom} onChange={e => setValidFrom(e.target.value)} />
              </div>
              <div>
                <label className="text-xs font-medium text-muted-foreground mb-1 block">Valid Until</label>
                <Input type="date" value={validUntil} onChange={e => setValidUntil(e.target.value)} />
              </div>
            </div>
          )}

          <div>
            <label className="text-xs font-medium text-muted-foreground mb-1 block">Tags</label>
            <Input value={tagsStr} onChange={e => setTagsStr(e.target.value)} placeholder="comma-separated" />
          </div>

          <Button className="w-full" onClick={handleSave} disabled={saving} data-testid="edit-save-btn">
            {saving ? 'Saving...' : 'Save Changes'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}


// ─────────────────────────────────────────────────────────────────────────────
//  QR Upload Dialog
// ─────────────────────────────────────────────────────────────────────────────

function QRUploadDialog({ open, onClose, categories, token, branchId, year, preselectedCategory, preselectedSubCategory }) {
  const [category, setCategory] = useState(preselectedCategory || '');
  const [subCategory, setSubCategory] = useState(preselectedSubCategory || '');
  const [coverageMonths, setCoverageMonths] = useState([]);
  const [qrYear, setQrYear] = useState(year || new Date().getFullYear());
  const [qrData, setQrData] = useState(null);
  const [generating, setGenerating] = useState(false);

  const subCatDef = category && subCategory ? categories[category]?.sub_categories?.[subCategory] : null;
  const periodType = subCatDef?.period_type || '';

  const toggleMonth = (m) => {
    setCoverageMonths(prev => prev.includes(m) ? prev.filter(x => x !== m) : [...prev, m].sort((a, b) => a - b));
  };

  const generateQR = async () => {
    if (!category || !subCategory) return toast.error('Select category and type');
    setGenerating(true);
    try {
      const res = await fetch(`${API}/api/documents/qr-upload-token`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          category,
          sub_category: subCategory,
          branch_id: branchId || '',
          year: qrYear,
          coverage_months: coverageMonths,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setQrData(data);
      } else {
        toast.error('Failed to generate QR code');
      }
    } catch { toast.error('Failed to generate QR code'); }
    finally { setGenerating(false); }
  };

  const uploadUrl = qrData ? `${API}/doc-upload/${qrData.token}` : '';

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-w-md" data-testid="qr-upload-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <QrCode className="h-5 w-5 text-blue-600" /> Upload via Phone
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-4 mt-2">
          {!qrData ? (
            <>
              {category && subCategory ? (
                <div className="p-2.5 bg-blue-50 border border-blue-200 rounded-lg text-xs">
                  <p className="text-blue-600 font-medium">Pre-filled from your current folder:</p>
                  <p className="text-blue-800 font-semibold mt-0.5">{categories[category]?.label} / {categories[category]?.sub_categories?.[subCategory]?.label}</p>
                  <p className="text-blue-500 mt-0.5">You can change it below if needed.</p>
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">Select the document type. A QR code will be generated for phone scanning.</p>
              )}

              <Select value={category} onValueChange={v => { setCategory(v); setSubCategory(''); }}>
                <SelectTrigger data-testid="qr-category-select">
                  <SelectValue placeholder="Select category..." />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(categories).map(([key, cat]) => (
                    <SelectItem key={key} value={key}>{cat.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>

              {category && (
                <Select value={subCategory} onValueChange={setSubCategory}>
                  <SelectTrigger data-testid="qr-subcategory-select">
                    <SelectValue placeholder="Select type..." />
                  </SelectTrigger>
                  <SelectContent>
                    {Object.entries(categories[category]?.sub_categories || {}).map(([key, sub]) => (
                      <SelectItem key={key} value={key}>{sub.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}

              {/* Year selector for periodic types */}
              {subCategory && (periodType === 'monthly' || periodType === 'quarterly' || periodType === 'annual') && (
                <div>
                  <label className="text-xs font-medium text-muted-foreground mb-1 block">Filing Year</label>
                  <Select value={String(qrYear)} onValueChange={v => setQrYear(Number(v))}>
                    <SelectTrigger data-testid="qr-year-select">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {[2022, 2023, 2024, 2025, 2026, 2027].map(y => (
                        <SelectItem key={y} value={String(y)}>{y}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )}

              {periodType === 'monthly' && (
                <div>
                  <label className="text-xs font-medium text-muted-foreground mb-1 block">Coverage Months</label>
                  <div className="grid grid-cols-4 gap-1.5">
                    {MONTH_NAMES.map((name, i) => {
                      const m = i + 1;
                      const sel = coverageMonths.includes(m);
                      return (
                        <button
                          key={m}
                          type="button"
                          onClick={() => toggleMonth(m)}
                          className={`rounded-md px-2 py-1.5 text-xs font-medium border transition-colors ${
                            sel ? 'bg-blue-600 text-white border-blue-600' : 'bg-white text-slate-600 border-slate-200'
                          }`}
                        >
                          {name.slice(0, 3)}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}

              <Button className="w-full" onClick={generateQR} disabled={generating || !category || !subCategory} data-testid="generate-qr-btn">
                {generating ? 'Generating...' : 'Generate QR Code'}
              </Button>
            </>
          ) : (
            <div className="text-center space-y-3">
              <p className="text-sm font-medium">Scan this QR code with your phone</p>
              <div className="bg-white p-4 rounded-xl border inline-block mx-auto">
                <img
                  src={`https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(uploadUrl)}`}
                  alt="QR Code"
                  className="w-48 h-48"
                  data-testid="qr-code-image"
                />
              </div>
              <div className="text-xs text-muted-foreground space-y-1">
                <p><strong>{qrData.category_label}</strong> / {qrData.sub_category_label}</p>
                <p>Link expires in 15 minutes</p>
              </div>
              <Button variant="outline" size="sm" onClick={() => { setQrData(null); setCoverageMonths([]); }}>
                Generate Another
              </Button>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
