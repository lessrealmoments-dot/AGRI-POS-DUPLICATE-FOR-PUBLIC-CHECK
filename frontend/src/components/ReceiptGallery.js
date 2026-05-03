/**
 * ReceiptGallery — shows uploaded photos for a record.
 * Fetches upload sessions and displays thumbnails with lightbox.
 */
import { useState, useEffect } from 'react';
import { api } from '../contexts/AuthContext';
import { fmtDateTime } from '../lib/utils';
import { Button } from './ui/button';
import { Dialog, DialogContent } from './ui/dialog';
import { ImageIcon, X, ChevronLeft, ChevronRight, ZoomIn, FileText } from 'lucide-react';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;

export default function ReceiptGallery({ recordType, recordId, onClose }) {
  const [sessions, setSessions] = useState([]);
  const [totalFiles, setTotalFiles] = useState(0);
  const [lightbox, setLightbox] = useState(null); // { sessionIdx, fileIdx }
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (recordType && recordId) load();
  }, [recordType, recordId]); // eslint-disable-line

  const load = async () => {
    setLoading(true);
    try {
      const res = await api.get(`${BACKEND_URL}/api/uploads/record/${recordType}/${recordId}`);
      setSessions(res.data.sessions || []);
      setTotalFiles(res.data.total_files || 0);
    } catch {}
    setLoading(false);
  };

  // File URL is now public (UUID-based security, no auth header needed)
  const fileUrl = (f) =>
    `${BACKEND_URL}/api/uploads/file/${recordType}/${recordId}/${f.id}`;

  const isPdf = (f) => f.content_type === 'application/pdf' || f.filename?.endsWith('.pdf');

  // Flatten all files for lightbox navigation
  const allFiles = sessions.flatMap((s, si) =>
    (s.files || []).map((f, fi) => ({ ...f, sessionIdx: si, fileIdx: fi, session: s }))
  );
  const currentLbFile = lightbox !== null ? allFiles[lightbox] : null;

  return (
    <div className="space-y-3">
      {loading ? (
        <p className="text-xs text-slate-400 text-center py-4">Loading receipts...</p>
      ) : totalFiles === 0 ? (
        <div className="text-center py-6 text-slate-400">
          <ImageIcon size={28} className="mx-auto mb-2 opacity-40" />
          <p className="text-sm">No receipts uploaded yet.</p>
        </div>
      ) : (
        <>
          {sessions.map((session, si) => (
            session.files?.length > 0 && (
              <div key={si}>
                <p className="text-[10px] text-slate-400 mb-1.5">
                  Uploaded by {session.created_by_name} · {fmtDateTime(session.created_at)}
                </p>
                <div className="grid grid-cols-4 gap-2">
                  {session.files.map((file, fi) => {
                    const globalIdx = allFiles.findIndex(f => f.id === file.id);
                    return (
                      <button key={fi} onClick={() => setLightbox(globalIdx)}
                        className="aspect-square rounded-lg overflow-hidden border border-slate-200 hover:border-[#1A4D2E] transition-colors group relative bg-slate-50">
                        {isPdf(file) ? (
                          <div className="w-full h-full flex flex-col items-center justify-center">
                            <FileText size={24} className="text-red-500" />
                            <p className="text-[9px] text-slate-500 mt-1 px-1 truncate w-full text-center">{file.filename}</p>
                          </div>
                        ) : (
                          <img src={fileUrl(file)} alt={file.filename}
                            className="w-full h-full object-cover group-hover:scale-105 transition-transform"
                            onError={e => { e.target.style.display = 'none'; }}
                          />
                        )}
                        <div className="absolute inset-0 bg-black/0 group-hover:bg-black/10 transition-colors flex items-center justify-center">
                          <ZoomIn size={14} className="text-white opacity-0 group-hover:opacity-100 transition-opacity" />
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>
            )
          ))}
        </>
      )}

      {/* Lightbox */}
      <Dialog open={!!currentLbFile} onOpenChange={() => setLightbox(null)}>
        <DialogContent className="max-w-3xl p-0 bg-black/95">
          <div className="relative">
            <button onClick={() => setLightbox(null)}
              className="absolute top-3 right-3 z-10 w-8 h-8 rounded-full bg-black/50 text-white flex items-center justify-center hover:bg-black/70">
              <X size={15} />
            </button>
            {lightbox > 0 && (
              <button onClick={() => setLightbox(l => l - 1)}
                className="absolute left-3 top-1/2 -translate-y-1/2 z-10 w-8 h-8 rounded-full bg-black/50 text-white flex items-center justify-center hover:bg-black/70">
                <ChevronLeft size={16} />
              </button>
            )}
            {lightbox < allFiles.length - 1 && (
              <button onClick={() => setLightbox(l => l + 1)}
                className="absolute right-3 top-1/2 -translate-y-1/2 z-10 w-8 h-8 rounded-full bg-black/50 text-white flex items-center justify-center hover:bg-black/70">
                <ChevronRight size={16} />
              </button>
            )}
            {currentLbFile && !isPdf(currentLbFile) && (
              <img src={fileUrl(currentLbFile)} alt={currentLbFile.filename}
                className="w-full max-h-[80vh] object-contain" />
            )}
            {currentLbFile && isPdf(currentLbFile) && (
              <div className="flex flex-col items-center justify-center p-10 text-white">
                <FileText size={48} className="text-red-400 mb-3" />
                <p className="text-sm">{currentLbFile.filename}</p>
                <a href={fileUrl(currentLbFile)} target="_blank" rel="noreferrer"
                  className="mt-3 text-xs underline text-blue-300">Open PDF in new tab</a>
              </div>
            )}
            <div className="absolute bottom-3 left-1/2 -translate-x-1/2 bg-black/50 text-white text-xs px-3 py-1 rounded-full">
              {(lightbox ?? 0) + 1} / {allFiles.length}
              {currentLbFile && <span className="ml-2 text-slate-300">{currentLbFile.filename}</span>}
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
