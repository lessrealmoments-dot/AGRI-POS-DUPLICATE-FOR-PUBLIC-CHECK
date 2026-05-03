/**
 * ViewReceiptsPage — public mobile photo gallery.
 * Accessed via QR code scan. Shows all uploaded photos for a transaction.
 * Allows verification directly from phone.
 * Route: /view-receipts/:token
 */
import { useState, useEffect, useRef } from 'react';
import { useParams } from 'react-router-dom';
import axios from 'axios';
import { fmtDateTime } from '../lib/utils';
import { ShieldCheck, ShieldAlert, ChevronLeft, ChevronRight, X, ZoomIn, FileText, RefreshCw, Shield } from 'lucide-react';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

export default function ViewReceiptsPage() {
  const { token } = useParams();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [lightbox, setLightbox] = useState(null); // index in allFiles
  const [showVerify, setShowVerify] = useState(false);
  const [pin, setPin] = useState('');
  const [hasDisc, setHasDisc] = useState(false);
  const [discNote, setDiscNote] = useState('');
  const [itemDesc, setItemDesc] = useState('');
  const [expQty, setExpQty] = useState('');
  const [fndQty, setFndQty] = useState('');
  const [unit, setUnit] = useState('');
  const [unitCost, setUnitCost] = useState('');
  const [verifyLoading, setVerifyLoading] = useState(false);
  const [verifyMsg, setVerifyMsg] = useState('');
  const touchStartX = useRef(null);

  useEffect(() => { loadSession(); }, [token]); // eslint-disable-line

  const loadSession = async () => {
    setLoading(true);
    setError('');
    try {
      const res = await axios.get(`${BACKEND_URL}/api/uploads/view-session/${token}`);
      setData(res.data);
    } catch (err) {
      setError(err.response?.data?.detail || 'Link not found or expired');
    }
    setLoading(false);
  };

  const allFiles = data
    ? data.sessions.flatMap(s => (s.files || []).map(f => ({ ...f, record_type: data.record_type, record_id: data.record_id })))
    : [];

  const fileUrl = (f) => `${BACKEND_URL}/api/uploads/file/${f.record_type}/${f.record_id}/${f.id}`;
  const isPdf = (f) => f.content_type === 'application/pdf' || f.filename?.endsWith('.pdf');

  // Swipe support
  const handleTouchStart = (e) => { touchStartX.current = e.touches[0].clientX; };
  const handleTouchEnd = (e) => {
    if (touchStartX.current === null) return;
    const diff = touchStartX.current - e.changedTouches[0].clientX;
    if (Math.abs(diff) > 50) {
      if (diff > 0 && lightbox < allFiles.length - 1) setLightbox(l => l + 1);
      if (diff < 0 && lightbox > 0) setLightbox(l => l - 1);
    }
    touchStartX.current = null;
  };

  const handleVerify = async () => {
    if (!pin) { setVerifyMsg('Please enter your PIN'); return; }
    if (hasDisc && !discNote) { setVerifyMsg('Please add a discrepancy note'); return; }
    setVerifyLoading(true);
    setVerifyMsg('');
    try {
      await axios.post(`${BACKEND_URL}/api/verify/public/${data.record_type}/${data.record_id}`, {
        pin, has_discrepancy: hasDisc, discrepancy_note: discNote,
        item_description: itemDesc,
        expected_qty: expQty !== '' ? parseFloat(expQty) : null,
        found_qty: fndQty !== '' ? parseFloat(fndQty) : null,
        unit, unit_cost: parseFloat(unitCost || 0),
      });
      setVerifyMsg('success');
      await loadSession(); // Refresh to show new badge
      setShowVerify(false);
    } catch (err) {
      setVerifyMsg(err.response?.data?.detail || 'Invalid PIN');
    }
    setVerifyLoading(false);
  };

  const php = (n) => '₱' + (parseFloat(n) || 0).toLocaleString('en-PH', { minimumFractionDigits: 2 });
  const valueImpact = hasDisc && expQty !== '' && fndQty !== '' && unitCost !== ''
    ? (parseFloat(fndQty) - parseFloat(expQty)) * parseFloat(unitCost || 0) : null;

  if (loading) return (
    <div className="min-h-screen bg-gray-950 flex items-center justify-center">
      <div className="flex flex-col items-center gap-3">
        <RefreshCw size={28} className="text-white animate-spin" />
        <p className="text-slate-400 text-sm">Loading…</p>
      </div>
    </div>
  );

  if (error) return (
    <div className="min-h-screen bg-gray-950 flex items-center justify-center p-6">
      <div className="text-center">
        <p className="text-red-400 font-semibold mb-2">Link Error</p>
        <p className="text-slate-400 text-sm">{error}</p>
      </div>
    </div>
  );

  const summary = data.record_summary || {};
  const verification = data.verification || {};

  return (
    <div className="min-h-screen bg-gray-950 text-white" style={{ WebkitTapHighlightColor: 'transparent' }}>
      {/* Header */}
      <div className="px-4 pt-safe-top pt-4 pb-3 border-b border-gray-800">
        <div className="flex items-start justify-between gap-2">
          <div className="flex-1 min-w-0">
            <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-0.5">
              {summary.type_label || data.record_type}
            </p>
            <p className="font-bold text-white leading-tight truncate">{summary.title || '—'}</p>
            {summary.description && <p className="text-xs text-slate-400 truncate">{summary.description}</p>}
            <div className="flex items-center gap-3 mt-1">
              {summary.amount > 0 && <span className="text-sm font-bold text-emerald-400 font-mono">{php(summary.amount)}</span>}
              {summary.date && <span className="text-[10px] text-slate-500">{summary.date}</span>}
            </div>
          </div>
          {/* Verification status */}
          <div className="shrink-0">
            {verification.verified ? (
              <span className={`inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-medium ${verification.verification_status === 'discrepancy' ? 'bg-amber-900/40 text-amber-300' : 'bg-emerald-900/40 text-emerald-300'}`}>
                {verification.verification_status === 'discrepancy' ? <ShieldAlert size={11} /> : <ShieldCheck size={11} />}
                {verification.verification_status === 'discrepancy' ? 'Discrepancy' : 'Verified'}
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] bg-gray-800 text-slate-400">
                <Shield size={11} /> Unverified
              </span>
            )}
          </div>
        </div>

        {verification.verified && (
          <p className="text-[10px] text-slate-500 mt-1">
            By {verification.verified_by_name} · {fmtDateTime(verification.verified_at)}
          </p>
        )}
        {verification.has_discrepancy && verification.discrepancy && (
          <div className="mt-2 rounded-lg bg-amber-900/30 border border-amber-700/40 px-2.5 py-2">
            <p className="text-[11px] font-semibold text-amber-300 mb-0.5">Discrepancy Noted</p>
            {verification.discrepancy.item_description && (
              <p className="text-[11px] text-amber-200">{verification.discrepancy.item_description}</p>
            )}
            {verification.discrepancy.expected_qty != null && (
              <p className="text-[10px] text-amber-300/80">
                Expected: {verification.discrepancy.expected_qty} {verification.discrepancy.unit} · Found: {verification.discrepancy.found_qty} {verification.discrepancy.unit}
                {verification.discrepancy.value_impact != null && (
                  <span className={`ml-2 font-bold ${verification.discrepancy.value_impact < 0 ? 'text-red-400' : 'text-green-400'}`}>
                    {verification.discrepancy.value_impact > 0 ? '+' : ''}{php(verification.discrepancy.value_impact)}
                  </span>
                )}
              </p>
            )}
            <p className="text-[10px] text-slate-400 mt-0.5">{verification.discrepancy.note}</p>
          </div>
        )}
      </div>

      {/* Photos */}
      <div className="px-4 py-3">
        <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-3">
          {allFiles.length} photo{allFiles.length !== 1 ? 's' : ''}
        </p>
        {allFiles.length === 0 ? (
          <div className="text-center py-12">
            <p className="text-slate-500 text-sm">No photos uploaded yet.</p>
          </div>
        ) : (
          <div className="grid grid-cols-3 gap-2">
            {allFiles.map((file, idx) => (
              <button
                key={file.id}
                onClick={() => setLightbox(idx)}
                className="aspect-square rounded-xl overflow-hidden bg-gray-800 border border-gray-700 active:scale-95 transition-transform"
              >
                {isPdf(file) ? (
                  <div className="w-full h-full flex items-center justify-center flex-col gap-1">
                    <FileText size={20} className="text-red-400" />
                    <p className="text-[9px] text-slate-400 px-1 truncate w-full text-center">{file.filename}</p>
                  </div>
                ) : (
                  <img src={fileUrl(file)} alt="" className="w-full h-full object-cover" onError={e => { e.target.style.display = 'none'; }} />
                )}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Verify button */}
      {!verification.verified && (
        <div className="px-4 pb-8">
          <button
            onClick={() => setShowVerify(true)}
            className="w-full py-3 rounded-2xl bg-[#1A4D2E] text-white font-semibold text-sm flex items-center justify-center gap-2 active:scale-95 transition-transform"
          >
            <ShieldCheck size={16} /> Verify This Transaction
          </button>
        </div>
      )}

      {/* Lightbox */}
      {lightbox !== null && (
        <div
          className="fixed inset-0 bg-black flex items-center justify-center"
          style={{ zIndex: 999 }}
          onTouchStart={handleTouchStart}
          onTouchEnd={handleTouchEnd}
        >
          <button onClick={() => setLightbox(null)} className="absolute top-4 right-4 z-10 w-9 h-9 rounded-full bg-black/60 flex items-center justify-center">
            <X size={16} className="text-white" />
          </button>
          {lightbox > 0 && (
            <button onClick={() => setLightbox(l => l - 1)} className="absolute left-3 top-1/2 -translate-y-1/2 z-10 w-9 h-9 rounded-full bg-black/60 flex items-center justify-center">
              <ChevronLeft size={18} className="text-white" />
            </button>
          )}
          {lightbox < allFiles.length - 1 && (
            <button onClick={() => setLightbox(l => l + 1)} className="absolute right-3 top-1/2 -translate-y-1/2 z-10 w-9 h-9 rounded-full bg-black/60 flex items-center justify-center">
              <ChevronRight size={18} className="text-white" />
            </button>
          )}
          {!isPdf(allFiles[lightbox]) && (
            <img src={fileUrl(allFiles[lightbox])} alt="" className="max-w-full max-h-screen object-contain" style={{ touchAction: 'pinch-zoom' }} />
          )}
          {isPdf(allFiles[lightbox]) && (
            <div className="flex flex-col items-center gap-3 p-8 text-white">
              <FileText size={48} className="text-red-400" />
              <p className="text-sm">{allFiles[lightbox].filename}</p>
              <a href={fileUrl(allFiles[lightbox])} target="_blank" rel="noreferrer" className="text-blue-300 underline text-sm">Open PDF</a>
            </div>
          )}
          <div className="absolute bottom-4 left-1/2 -translate-x-1/2 bg-black/60 text-white text-xs px-3 py-1 rounded-full">
            {lightbox + 1} / {allFiles.length}
          </div>
        </div>
      )}

      {/* Verify Panel */}
      {showVerify && (
        <div className="fixed inset-0 bg-black/80 flex items-end" style={{ zIndex: 9999 }}>
          <div className="w-full bg-gray-900 rounded-t-3xl p-5 pb-8 max-h-[90vh] overflow-y-auto">
            <div className="w-10 h-1 bg-gray-600 rounded-full mx-auto mb-4" />
            <p className="font-bold text-white mb-1">Verify Transaction</p>
            <p className="text-xs text-slate-400 mb-4">Enter Admin PIN, TOTP (6-digit), or Auditor PIN</p>

            {verifyMsg && verifyMsg !== 'success' && (
              <div className="rounded-xl bg-red-900/40 border border-red-700 px-3 py-2 mb-3">
                <p className="text-xs text-red-300">{verifyMsg}</p>
              </div>
            )}

            <input
              type="password" autoComplete="new-password"
              value={pin}
              onChange={e => setPin(e.target.value)}
              placeholder="Enter PIN…"
              className="w-full bg-gray-800 border border-gray-700 rounded-2xl px-4 py-3 text-white text-sm mb-3 focus:outline-none focus:border-[#1A4D2E]"
              autoFocus
            />

            {/* Discrepancy toggle */}
            <label className="flex items-center gap-3 mb-3 cursor-pointer">
              <div onClick={() => setHasDisc(v => !v)} className={`w-10 h-6 rounded-full relative transition-colors ${hasDisc ? 'bg-amber-500' : 'bg-gray-700'}`}>
                <div className={`absolute top-1 w-4 h-4 rounded-full bg-white shadow transition-transform ${hasDisc ? 'translate-x-5' : 'translate-x-1'}`} />
              </div>
              <span className="text-sm text-white">Flag discrepancy</span>
            </label>

            {hasDisc && (
              <div className="bg-amber-900/20 border border-amber-700/40 rounded-2xl p-3 mb-3 flex flex-col gap-2">
                <input type="text" value={itemDesc} onChange={e => setItemDesc(e.target.value)} placeholder="Item / description" className="w-full bg-gray-800 rounded-xl px-3 py-2 text-white text-xs focus:outline-none" />
                <div className="grid grid-cols-3 gap-2">
                  <input type="number" value={expQty} onChange={e => setExpQty(e.target.value)} placeholder="Expected" className="bg-gray-800 rounded-xl px-3 py-2 text-white text-xs focus:outline-none" />
                  <input type="number" value={fndQty} onChange={e => setFndQty(e.target.value)} placeholder="Found" className="bg-gray-800 rounded-xl px-3 py-2 text-white text-xs focus:outline-none" />
                  <input type="text" value={unit} onChange={e => setUnit(e.target.value)} placeholder="Unit" className="bg-gray-800 rounded-xl px-3 py-2 text-white text-xs focus:outline-none" />
                </div>
                <input type="number" value={unitCost} onChange={e => setUnitCost(e.target.value)} placeholder="Unit cost (₱)" className="w-full bg-gray-800 rounded-xl px-3 py-2 text-white text-xs focus:outline-none" />
                {valueImpact !== null && (
                  <p className={`text-xs font-bold text-center ${valueImpact < 0 ? 'text-red-400' : 'text-green-400'}`}>
                    Value: {valueImpact > 0 ? '+' : ''}{php(valueImpact)}
                  </p>
                )}
                <textarea value={discNote} onChange={e => setDiscNote(e.target.value)} placeholder="Explain the discrepancy…" rows={2} className="w-full bg-gray-800 rounded-xl px-3 py-2 text-white text-xs focus:outline-none resize-none" />
              </div>
            )}

            <div className="flex gap-3">
              <button onClick={() => setShowVerify(false)} className="flex-1 py-3 rounded-2xl bg-gray-800 text-slate-300 text-sm font-medium">Cancel</button>
              <button
                onClick={handleVerify}
                disabled={verifyLoading}
                className={`flex-1 py-3 rounded-2xl text-white text-sm font-semibold flex items-center justify-center gap-2 ${hasDisc ? 'bg-amber-600' : 'bg-[#1A4D2E]'} disabled:opacity-50`}
              >
                {verifyLoading ? <RefreshCw size={15} className="animate-spin" /> : hasDisc ? <><ShieldAlert size={15} /> Flag & Verify</> : <><ShieldCheck size={15} /> Verify</>}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
