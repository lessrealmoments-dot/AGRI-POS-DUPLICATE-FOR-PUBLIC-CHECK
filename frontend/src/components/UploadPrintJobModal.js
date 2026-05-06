/**
 * UploadPrintJobModal — Upload an external document (PDF / image) and
 * send it as a print job to a selected branch terminal.
 *
 * Supports: PDF, JPG, PNG, TIFF, WEBP, GIF, BMP  (max 20 MB)
 *
 * Flow:
 *   1. Pick file (drag-drop or click)
 *   2. Enter title + optional description
 *   3. Select branch → terminal
 *   4. Choose manual / auto print mode hint
 *   5. Submit → multipart POST → print job created
 */
import { useState, useEffect, useCallback, useRef } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from './ui/dialog';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Textarea } from './ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';
import { Badge } from './ui/badge';
import {
  Upload, FileText, Image, Printer, Building2, Wifi, WifiOff,
  X, CheckCircle2, Loader2, AlertTriangle, CloudUpload, Send
} from 'lucide-react';
import { api } from '../contexts/AuthContext';
import { toast } from 'sonner';

const ACCEPTED = '.pdf,.jpg,.jpeg,.png,.tiff,.tif,.webp,.gif,.bmp';
const MAX_BYTES = 20 * 1024 * 1024;

function fileSizeFmt(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function FileIcon({ type }) {
  if (type === 'application/pdf') return <FileText size={28} className="text-red-500" />;
  return <Image size={28} className="text-blue-500" />;
}

export default function UploadPrintJobModal({ open, onOpenChange }) {
  const [file, setFile]               = useState(null);
  const [title, setTitle]             = useState('');
  const [description, setDescription] = useState('');
  const [branchId, setBranchId]       = useState('');
  const [terminalId, setTerminalId]   = useState('');
  const [printMode, setPrintMode]     = useState('manual');
  const [branches, setBranches]       = useState([]);
  const [terminals, setTerminals]     = useState([]);
  const [loadingT, setLoadingT]       = useState(false);
  const [uploading, setUploading]     = useState(false);
  const [progress, setProgress]       = useState(0);
  const [done, setDone]               = useState(null); // { status, message }
  const [dragOver, setDragOver]       = useState(false);
  const inputRef = useRef(null);

  // Load branches on open
  useEffect(() => {
    if (!open) return;
    setFile(null); setTitle(''); setDescription('');
    setBranchId(''); setTerminalId(''); setDone(null);
    setProgress(0);
    api.get('/branches').then(r => setBranches(r.data?.branches || r.data || [])).catch(() => {});
  }, [open]);

  // Load terminals when branch changes
  useEffect(() => {
    if (!branchId) { setTerminals([]); setTerminalId(''); return; }
    setLoadingT(true);
    api.get(`/print/terminals/for-branch/${branchId}`)
      .then(r => {
        const list = r.data || [];
        setTerminals(list);
        if (list.length === 1) setTerminalId(list[0].terminal_id);
        else { const online = list.find(t => t.is_online); setTerminalId(online?.terminal_id || list[0]?.terminal_id || ''); }
      })
      .catch(() => setTerminals([]))
      .finally(() => setLoadingT(false));
  }, [branchId]);

  const handleFile = useCallback((f) => {
    if (!f) return;
    if (f.size > MAX_BYTES) { toast.error(`File too large. Max 20 MB.`); return; }
    setFile(f);
    if (!title) setTitle(f.name.replace(/\.[^/.]+$/, '')); // auto-fill title from filename
  }, [title]);

  const onDrop = useCallback((e) => {
    e.preventDefault(); setDragOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) handleFile(f);
  }, [handleFile]);

  const handleSubmit = async () => {
    if (!file)       { toast.error('Please select a file'); return; }
    if (!title.trim()) { toast.error('Please enter a document title'); return; }
    if (!terminalId) { toast.error('Please select a print terminal'); return; }

    setUploading(true); setProgress(10);

    const formData = new FormData();
    formData.append('file', file);
    formData.append('terminal_id', terminalId);
    formData.append('branch_id', branchId);
    formData.append('title', title.trim());
    formData.append('description', description.trim());
    formData.append('print_mode', printMode);

    try {
      setProgress(40);
      const res = await api.post('/print/upload-job', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        onUploadProgress: (evt) => {
          if (evt.total) setProgress(Math.round(10 + (evt.loaded / evt.total) * 70));
        },
      });
      setProgress(100);
      setDone({ status: res.data.status, message: res.data.message });
      toast.success(res.data.status === 'sent' ? 'Document sent to printer!' : 'Document queued for branch printer');
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Upload failed');
      setProgress(0);
    }
    setUploading(false);
  };

  const selectedTerminal = terminals.find(t => t.terminal_id === terminalId);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg" data-testid="upload-print-job-modal">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-base">
            <CloudUpload size={17} className="text-emerald-600" />
            Send Document to Branch Printer
          </DialogTitle>
        </DialogHeader>

        {done ? (
          /* ── Success screen ── */
          <div className="text-center py-8 space-y-3" data-testid="upload-print-done">
            {done.status === 'sent' ? (
              <CheckCircle2 size={40} className="text-emerald-500 mx-auto" />
            ) : (
              <Printer size={40} className="text-amber-500 mx-auto" />
            )}
            <p className="font-semibold text-slate-800 text-base">
              {done.status === 'sent' ? 'Document Sent!' : 'Job Queued'}
            </p>
            <p className="text-sm text-slate-500">{done.message}</p>
            <div className="flex gap-2 justify-center pt-2">
              <Button variant="outline" size="sm" onClick={() => { setDone(null); setFile(null); setTitle(''); setDescription(''); }}>
                Send Another
              </Button>
              <Button size="sm" className="bg-emerald-700 hover:bg-emerald-800 text-white" onClick={() => onOpenChange(false)}>
                Done
              </Button>
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            {/* ── File drop zone ── */}
            <div
              onDrop={onDrop}
              onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
              onDragLeave={() => setDragOver(false)}
              onClick={() => !file && inputRef.current?.click()}
              className={`border-2 border-dashed rounded-xl p-4 transition-all cursor-pointer ${
                dragOver ? 'border-emerald-400 bg-emerald-50' :
                file ? 'border-emerald-300 bg-emerald-50/50 cursor-default' : 'border-slate-300 hover:border-emerald-300 hover:bg-slate-50'
              }`}
              data-testid="file-drop-zone"
            >
              <input
                ref={inputRef}
                type="file"
                accept={ACCEPTED}
                className="hidden"
                onChange={e => handleFile(e.target.files?.[0])}
              />
              {file ? (
                <div className="flex items-center gap-3">
                  <FileIcon type={file.type} />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-semibold text-slate-800 truncate">{file.name}</p>
                    <p className="text-xs text-slate-500">{fileSizeFmt(file.size)} · {file.type || 'document'}</p>
                  </div>
                  <button
                    onClick={(e) => { e.stopPropagation(); setFile(null); }}
                    className="p-1.5 rounded-lg hover:bg-red-50 text-slate-400 hover:text-red-500 transition-colors"
                    data-testid="remove-file-btn"
                  >
                    <X size={14} />
                  </button>
                </div>
              ) : (
                <div className="text-center py-2">
                  <Upload size={24} className="text-slate-400 mx-auto mb-2" />
                  <p className="text-sm font-medium text-slate-600">Drop file here or click to browse</p>
                  <p className="text-xs text-slate-400 mt-1">PDF, JPG, PNG, TIFF, WEBP · Max 20 MB</p>
                </div>
              )}
            </div>

            {/* Upload progress bar */}
            {uploading && (
              <div className="w-full bg-slate-100 rounded-full h-1.5">
                <div
                  className="bg-emerald-500 h-1.5 rounded-full transition-all duration-300"
                  style={{ width: `${progress}%` }}
                />
              </div>
            )}

            {/* Document title */}
            <div className="space-y-1">
              <Label className="text-xs font-semibold text-slate-600">Document Title <span className="text-red-500">*</span></Label>
              <Input
                value={title}
                onChange={e => setTitle(e.target.value)}
                placeholder="e.g., Business Permit 2025, Signed Contract, Branch Memo"
                maxLength={120}
                data-testid="doc-title-input"
              />
            </div>

            {/* Description (optional) */}
            <div className="space-y-1">
              <Label className="text-xs font-semibold text-slate-600">Description <span className="text-xs font-normal text-slate-400">(optional)</span></Label>
              <Textarea
                value={description}
                onChange={e => setDescription(e.target.value)}
                placeholder="Notes for branch staff, e.g., Print 3 copies, urgent, attach to file..."
                rows={2}
                maxLength={500}
                className="resize-none text-sm"
                data-testid="doc-description-input"
              />
            </div>

            {/* Branch + Terminal selector (side by side) */}
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <Label className="text-xs font-semibold text-slate-600">Target Branch <span className="text-red-500">*</span></Label>
                <Select value={branchId} onValueChange={setBranchId}>
                  <SelectTrigger className="h-9 text-sm" data-testid="branch-select">
                    <SelectValue placeholder="Select branch" />
                  </SelectTrigger>
                  <SelectContent>
                    {branches.map(b => (
                      <SelectItem key={b.id} value={b.id}>{b.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-1">
                <Label className="text-xs font-semibold text-slate-600">Printer Terminal <span className="text-red-500">*</span></Label>
                <Select value={terminalId} onValueChange={setTerminalId} disabled={!branchId || loadingT}>
                  <SelectTrigger className="h-9 text-sm" data-testid="terminal-select">
                    <SelectValue placeholder={loadingT ? 'Loading...' : !branchId ? 'Pick branch first' : 'Select terminal'} />
                  </SelectTrigger>
                  <SelectContent>
                    {terminals.map(t => (
                      <SelectItem key={t.terminal_id} value={t.terminal_id}>
                        <span className="flex items-center gap-1.5">
                          {t.is_online ? <Wifi size={10} className="text-emerald-500" /> : <WifiOff size={10} className="text-slate-400" />}
                          {t.user_name || t.branch_name || 'Terminal'}
                        </span>
                      </SelectItem>
                    ))}
                    {!loadingT && branchId && terminals.length === 0 && (
                      <SelectItem value="" disabled>No terminals for this branch</SelectItem>
                    )}
                  </SelectContent>
                </Select>
              </div>
            </div>

            {/* Offline warning */}
            {selectedTerminal && !selectedTerminal.is_online && (
              <div className="flex items-start gap-2 p-2.5 bg-amber-50 border border-amber-100 rounded-lg text-xs text-amber-700">
                <AlertTriangle size={13} className="mt-0.5 shrink-0" />
                Terminal is offline. Your document will be queued and delivered automatically when it reconnects.
              </div>
            )}

            {/* Print mode hint */}
            <div className="space-y-1.5">
              <Label className="text-xs font-semibold text-slate-600">Preferred Print Mode</Label>
              <div className="flex gap-2">
                {[
                  { value: 'manual', label: 'Manual', desc: 'Staff clicks Print Now' },
                  { value: 'auto',   label: 'Auto Print', desc: 'Prints on arrival' },
                ].map(opt => (
                  <button
                    key={opt.value}
                    onClick={() => setPrintMode(opt.value)}
                    data-testid={`print-mode-${opt.value}`}
                    className={`flex-1 p-2.5 rounded-xl border text-left transition-all ${
                      printMode === opt.value
                        ? 'border-emerald-400 bg-emerald-50 ring-1 ring-emerald-300'
                        : 'border-slate-200 hover:border-slate-300'
                    }`}
                  >
                    <p className="text-xs font-semibold text-slate-800">{opt.label}</p>
                    <p className="text-[10px] text-slate-400 mt-0.5">{opt.desc}</p>
                  </button>
                ))}
              </div>
              <p className="text-[10px] text-slate-400">
                Terminal's own print mode setting takes precedence. This is a suggestion only.
              </p>
            </div>

            {/* Actions */}
            <div className="flex gap-2 pt-1">
              <Button variant="outline" className="flex-1" onClick={() => onOpenChange(false)} data-testid="upload-cancel-btn">
                Cancel
              </Button>
              <Button
                className="flex-1 bg-emerald-700 hover:bg-emerald-800 text-white"
                onClick={handleSubmit}
                disabled={uploading || !file || !title.trim() || !terminalId}
                data-testid="upload-submit-btn"
              >
                {uploading ? (
                  <><Loader2 size={14} className="animate-spin mr-1.5" /> Uploading {progress}%</>
                ) : (
                  <><Send size={14} className="mr-1.5" /> Send to Printer</>
                )}
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
