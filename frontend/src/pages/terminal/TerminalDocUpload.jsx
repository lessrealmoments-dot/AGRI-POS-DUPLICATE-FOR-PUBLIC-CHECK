import { useState, useEffect, useRef } from 'react';
import { X, RefreshCw, FileText, Camera, Image as ImageIcon, Check, Lock, FolderUp } from 'lucide-react';
import { toast } from 'sonner';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

const MONTH_NAMES = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const QUARTER_OPTS = ['Q1','Q2','Q3','Q4'];
const YEAR_OPTS = [2022,2023,2024,2025,2026,2027];

export default function TerminalDocUpload({ branchId, onClose }) {
  const [step, setStep] = useState('pin');
  const [pin, setPin] = useState('');
  const [verifying, setVerifying] = useState(false);
  const [verifier, setVerifier] = useState(null);
  const [branches, setBranches] = useState([]);
  const [selectedBranch, setSelectedBranch] = useState(branchId || '');

  const [categories, setCategories] = useState(null);
  const [category, setCategory] = useState('');
  const [subCategory, setSubCategory] = useState('');
  const [year, setYear] = useState(new Date().getFullYear());
  const [coverageMonths, setCoverageMonths] = useState([]);
  const [coverageQuarter, setCoverageQuarter] = useState('');
  const [validFrom, setValidFrom] = useState('');
  const [validUntil, setValidUntil] = useState('');

  const [files, setFiles] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [result, setResult] = useState(null);
  const fileRef = useRef(null);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch(`${BACKEND_URL}/api/documents/categories`);
        if (res.ok) setCategories(await res.json());
      } catch {}
    })();
  }, []);

  const handleVerifyPin = async () => {
    if (!pin) return;
    setVerifying(true);
    try {
      const res = await fetch(`${BACKEND_URL}/api/documents/terminal/verify-pin`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pin, branch_id: branchId }),
      });
      if (res.ok) {
        const data = await res.json();
        setVerifier(data);
        setBranches(data.accessible_branches || []);
        if (data.accessible_branches?.length === 1) {
          setSelectedBranch(data.accessible_branches[0].id);
        } else if (!data.can_all_branches) {
          setSelectedBranch(branchId);
        }
        setStep('category');
      } else {
        const err = await res.json();
        toast.error(err.detail || 'Invalid PIN');
      }
    } catch { toast.error('Connection error'); }
    finally { setVerifying(false); }
  };

  const subCatDef = category && subCategory && categories
    ? categories[category]?.sub_categories?.[subCategory] : null;
  const periodType = subCatDef?.period_type || 'one_time';

  const toggleMonth = (m) => {
    setCoverageMonths(prev => prev.includes(m) ? prev.filter(x => x !== m) : [...prev, m].sort((a,b) => a-b));
  };

  const handleFiles = (e) => {
    const selected = Array.from(e.target.files || []);
    const withPreview = selected.map(f => ({
      file: f, name: f.name, size: f.size,
      previewUrl: f.type.startsWith('image/') ? URL.createObjectURL(f) : null,
    }));
    setFiles(prev => [...prev, ...withPreview].slice(0, 10));
  };

  const handleUpload = async () => {
    if (!files.length || !category || !subCategory) return;
    setUploading(true);
    try {
      const fd = new FormData();
      files.forEach(f => fd.append('files', f.file));
      fd.append('pin', pin);
      fd.append('category', category);
      fd.append('sub_category', subCategory);
      fd.append('branch_id', selectedBranch || branchId);
      fd.append('terminal_branch_id', branchId);
      fd.append('year', String(year));
      fd.append('coverage_months', coverageMonths.join(','));
      fd.append('coverage_quarter', coverageQuarter);
      fd.append('valid_from', validFrom);
      fd.append('valid_until', validUntil);

      const res = await fetch(`${BACKEND_URL}/api/documents/terminal/upload`, {
        method: 'POST', body: fd,
      });
      if (res.ok) {
        const data = await res.json();
        setResult(data);
        setStep('done');
      } else {
        const err = await res.json();
        toast.error(err.detail || 'Upload failed');
      }
    } catch { toast.error('Upload failed'); }
    finally { setUploading(false); }
  };

  if (step === 'pin') return (
    <div className="fixed inset-0 z-[200] bg-slate-900/95 flex items-center justify-center p-4" data-testid="terminal-doc-upload">
      <div className="bg-white rounded-2xl p-6 w-full max-w-xs space-y-4 text-center">
        <div className="w-14 h-14 rounded-2xl bg-teal-50 flex items-center justify-center mx-auto">
          <Lock size={24} className="text-teal-600" />
        </div>
        <h2 className="font-bold text-lg text-slate-800">Upload Document</h2>
        <p className="text-xs text-slate-500">Enter your PIN to access document upload.<br/>Manager PIN = your branch only. Admin/TOTP = all branches.</p>
        <input
          type="password"
          inputMode="numeric"
          autoComplete="new-password"
          value={pin}
          onChange={e => setPin(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleVerifyPin()}
          placeholder="Enter PIN"
          className="w-full text-center text-2xl tracking-[0.3em] py-3 border-2 border-slate-200 rounded-xl focus:border-teal-500 focus:outline-none"
          autoFocus
          data-testid="terminal-doc-pin"
        />
        <div className="flex gap-2">
          <button onClick={onClose}
            className="flex-1 py-3 rounded-xl border border-slate-200 text-slate-600 font-medium text-sm">
            Cancel
          </button>
          <button onClick={handleVerifyPin} disabled={verifying || !pin}
            className="flex-1 py-3 rounded-xl bg-teal-600 text-white font-medium text-sm disabled:opacity-50"
            data-testid="terminal-doc-pin-submit">
            {verifying ? 'Verifying...' : 'Continue'}
          </button>
        </div>
      </div>
    </div>
  );

  if (step === 'category') return (
    <div className="fixed inset-0 z-[200] bg-white overflow-y-auto" data-testid="terminal-doc-category">
      <div className="max-w-md mx-auto px-4 py-5 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="font-bold text-lg text-slate-800">Upload Document</h2>
          <button onClick={onClose} className="w-8 h-8 rounded-full bg-slate-100 flex items-center justify-center">
            <X size={16} className="text-slate-500" />
          </button>
        </div>
        <p className="text-xs text-slate-500">Uploading as <strong>{verifier?.verifier_name}</strong>
          {verifier?.can_all_branches ? ' (all branches)' : ''}</p>

        {verifier?.can_all_branches && branches.length > 1 && (
          <div>
            <label className="text-xs font-medium text-slate-500 mb-1 block">Upload to Branch</label>
            <select value={selectedBranch} onChange={e => setSelectedBranch(e.target.value)}
              className="w-full border border-slate-200 rounded-xl px-3 py-2.5 text-sm" data-testid="terminal-doc-branch">
              {branches.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
            </select>
          </div>
        )}

        <div>
          <label className="text-xs font-medium text-slate-500 mb-1 block">Category</label>
          <select value={category} onChange={e => { setCategory(e.target.value); setSubCategory(''); }}
            className="w-full border border-slate-200 rounded-xl px-3 py-2.5 text-sm" data-testid="terminal-doc-category-select">
            <option value="">Select category...</option>
            {categories && Object.entries(categories).map(([k, c]) => (
              <option key={k} value={k}>{c.label}</option>
            ))}
          </select>
        </div>

        {category && categories?.[category] && (
          <div>
            <label className="text-xs font-medium text-slate-500 mb-1 block">Document Type</label>
            <select value={subCategory} onChange={e => setSubCategory(e.target.value)}
              className="w-full border border-slate-200 rounded-xl px-3 py-2.5 text-sm" data-testid="terminal-doc-subcat-select">
              <option value="">Select type...</option>
              {Object.entries(categories[category].sub_categories).map(([k, s]) => (
                <option key={k} value={k}>{s.label}</option>
              ))}
            </select>
          </div>
        )}

        {subCategory && (periodType === 'monthly' || periodType === 'quarterly' || periodType === 'annual') && (
          <div>
            <label className="text-xs font-medium text-slate-500 mb-1 block">Filing Year</label>
            <select value={year} onChange={e => setYear(Number(e.target.value))}
              className="w-full border border-slate-200 rounded-xl px-3 py-2.5 text-sm">
              {YEAR_OPTS.map(y => <option key={y} value={y}>{y}</option>)}
            </select>
          </div>
        )}

        {subCategory && periodType === 'monthly' && (
          <div>
            <label className="text-xs font-medium text-slate-500 mb-1 block">Coverage Months</label>
            <div className="grid grid-cols-4 gap-1.5">
              {MONTH_NAMES.map((name, i) => {
                const m = i + 1;
                const sel = coverageMonths.includes(m);
                return (
                  <button key={m} type="button" onClick={() => toggleMonth(m)}
                    className={`rounded-lg px-2 py-2 text-xs font-medium border transition-colors ${
                      sel ? 'bg-teal-600 text-white border-teal-600' : 'bg-white text-slate-600 border-slate-200'
                    }`}>
                    {name}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {subCategory && periodType === 'quarterly' && (
          <div>
            <label className="text-xs font-medium text-slate-500 mb-1 block">Quarter</label>
            <div className="grid grid-cols-4 gap-2">
              {QUARTER_OPTS.map(q => (
                <button key={q} type="button" onClick={() => setCoverageQuarter(q)}
                  className={`rounded-lg px-2 py-2 text-sm font-medium border ${
                    coverageQuarter === q ? 'bg-teal-600 text-white border-teal-600' : 'bg-white text-slate-600 border-slate-200'
                  }`}>
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}

        {subCategory && (periodType === 'validity' || periodType === 'annual') && (
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-xs font-medium text-slate-500 mb-1 block">Valid From</label>
              <input type="date" value={validFrom} onChange={e => setValidFrom(e.target.value)}
                className="w-full border border-slate-200 rounded-xl px-3 py-2.5 text-sm" />
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500 mb-1 block">Valid Until</label>
              <input type="date" value={validUntil} onChange={e => setValidUntil(e.target.value)}
                className="w-full border border-slate-200 rounded-xl px-3 py-2.5 text-sm" />
            </div>
          </div>
        )}

        <button onClick={() => { if (category && subCategory) setStep('upload'); else toast.error('Select category and type'); }}
          disabled={!category || !subCategory}
          className="w-full py-3.5 rounded-xl bg-teal-600 text-white font-semibold text-sm disabled:opacity-40"
          data-testid="terminal-doc-next">
          Next — Select Files
        </button>
      </div>
    </div>
  );

  if (step === 'upload') {
    const catLabel = categories?.[category]?.label || category;
    const subLabel = categories?.[category]?.sub_categories?.[subCategory]?.label || subCategory;

    return (
      <div className="fixed inset-0 z-[200] bg-white overflow-y-auto" data-testid="terminal-doc-upload-screen">
        <div className="max-w-md mx-auto px-4 py-5 space-y-4">
          <div className="flex items-center justify-between">
            <button onClick={() => setStep('category')} className="text-sm text-teal-600 font-medium">Back</button>
            <button onClick={onClose} className="w-8 h-8 rounded-full bg-slate-100 flex items-center justify-center">
              <X size={16} className="text-slate-500" />
            </button>
          </div>

          <div className="p-3 bg-teal-50 rounded-xl border border-teal-200 text-xs">
            <p className="font-semibold text-teal-800">{catLabel}</p>
            <p className="text-teal-700">{subLabel}</p>
            {coverageMonths.length > 0 && (
              <p className="text-teal-600 mt-0.5">{year} — {coverageMonths.map(m => MONTH_NAMES[m-1]).join(', ')}</p>
            )}
          </div>

          <div className="space-y-3">
            <p className="text-sm font-semibold text-slate-700">Capture or Select Document</p>
            <div className="grid grid-cols-2 gap-3">
              <button onClick={() => { fileRef.current.setAttribute('capture', 'environment'); fileRef.current.click(); }}
                className="flex flex-col items-center gap-2 p-5 rounded-xl border-2 border-dashed border-teal-400 text-teal-600 active:bg-teal-50"
                data-testid="terminal-doc-camera">
                <Camera size={28} />
                <span className="text-xs font-medium">Take Photo</span>
                <span className="text-[10px] text-teal-500">Uses native camera</span>
              </button>
              <button onClick={() => { fileRef.current.removeAttribute('capture'); fileRef.current.click(); }}
                className="flex flex-col items-center gap-2 p-5 rounded-xl border-2 border-dashed border-slate-300 text-slate-500 active:bg-slate-50"
                data-testid="terminal-doc-browse">
                <ImageIcon size={28} />
                <span className="text-xs font-medium">Browse Files</span>
                <span className="text-[10px] text-slate-400">Gallery or file manager</span>
              </button>
            </div>
            <input ref={fileRef} type="file" accept="image/*,.pdf" multiple className="hidden" onChange={handleFiles} />
          </div>

          {files.length > 0 && (
            <div className="space-y-2">
              {files.map((f, i) => (
                <div key={i} className="flex items-center gap-2 p-2 bg-slate-50 rounded-xl border">
                  {f.previewUrl ? (
                    <img src={f.previewUrl} alt="" className="w-12 h-12 rounded-lg object-cover shrink-0" />
                  ) : (
                    <div className="w-12 h-12 rounded-lg bg-blue-50 flex items-center justify-center shrink-0">
                      <FileText size={18} className="text-blue-500" />
                    </div>
                  )}
                  <div className="flex-1 min-w-0">
                    <p className="text-xs font-medium truncate">{f.name}</p>
                    <p className="text-[10px] text-slate-400">{(f.size / 1024).toFixed(0)} KB</p>
                  </div>
                  <button onClick={() => setFiles(prev => prev.filter((_,j) => j !== i))}
                    className="w-7 h-7 rounded-full bg-red-100 text-red-500 flex items-center justify-center shrink-0">
                    <X size={14} />
                  </button>
                </div>
              ))}
            </div>
          )}

          {files.length > 0 && (
            <button onClick={handleUpload} disabled={uploading}
              className="w-full py-4 rounded-xl bg-teal-600 text-white font-bold text-base flex items-center justify-center gap-2 disabled:opacity-50 active:scale-[0.98] transition-transform"
              data-testid="terminal-doc-submit">
              {uploading ? <><RefreshCw size={18} className="animate-spin" /> Uploading...</>
                : <><FolderUp size={18} /> Upload {files.length} File{files.length !== 1 ? 's' : ''}</>}
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 z-[200] bg-white flex items-center justify-center p-4" data-testid="terminal-doc-done">
      <div className="text-center space-y-4 max-w-xs">
        <div className="w-16 h-16 rounded-full bg-emerald-100 flex items-center justify-center mx-auto">
          <Check size={28} className="text-emerald-600" />
        </div>
        <h2 className="font-bold text-xl text-slate-800">Document Uploaded!</h2>
        <p className="text-sm text-slate-500">{result?.uploaded} file{result?.uploaded !== 1 ? 's' : ''} saved</p>
        <p className="text-sm font-semibold text-teal-700">{result?.document_name}</p>
        <button onClick={onClose}
          className="w-full py-3.5 rounded-xl bg-teal-600 text-white font-semibold text-sm"
          data-testid="terminal-doc-close">
          Done
        </button>
      </div>
    </div>
  );
}
