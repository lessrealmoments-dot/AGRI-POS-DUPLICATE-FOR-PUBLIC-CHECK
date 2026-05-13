/**
 * RequestQRDialog — Phase 2.1 polish.
 *
 * Surfaces the EXISTING `doc_code` for a Branch Stock Request PO so
 * supply-branch staff can scan it from the PC and use the Phase 2
 * mobile Confirm Quantities panel on `/doc/{code}`.
 *
 * - No new doc_code minted (relies on `GET /api/doc/by-ref/.../{po_id}`,
 *   which returns the code created at PO insertion).
 * - No backend touched.
 * - Read-only render: QR image + human URL + 8-char short code + copy.
 *
 * Props:
 *   open               — boolean
 *   onOpenChange(bool)
 *   request            — PO dict (must have `id`, optionally `po_number`,
 *                        `branch_id`, `supply_branch_id`)
 *   requestingBranchName — display string
 *   supplyBranchName     — display string
 */
import React, { useEffect, useState } from 'react';
import { api } from '../contexts/AuthContext';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
} from './ui/dialog';
import { Button } from './ui/button';
import { QRCodeSVG } from 'qrcode.react';
import { Copy, RefreshCw, Smartphone, ArrowRight } from 'lucide-react';
import { toast } from 'sonner';

export default function RequestQRDialog({
  open,
  onOpenChange,
  request,
  requestingBranchName = '',
  supplyBranchName = '',
}) {
  const [code, setCode] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open || !request?.id) return;
    let cancelled = false;
    setLoading(true);
    setError('');
    setCode('');
    api.get(`/doc/by-ref/purchase_order/${request.id}`)
      .then((res) => {
        if (cancelled) return;
        if (!res.data?.code) {
          setError('No QR code is available for this request yet.');
        } else {
          setCode(res.data.code);
        }
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e?.response?.data?.detail || 'Could not load QR code.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [open, request?.id]);

  const url = code ? `${window.location.origin}/doc/${code}` : '';

  const copy = async (value, label) => {
    try {
      await navigator.clipboard.writeText(value);
      toast.success(`${label} copied`);
    } catch (e) {
      toast.error('Copy failed');
    }
  };

  if (!request) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-w-md"
        data-testid="request-qr-dialog"
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Smartphone size={18} className="text-violet-700" />
            Request QR Code
          </DialogTitle>
          <DialogDescription>
            Scan with the supply-branch phone to open the request and
            confirm quantities with PIN.
          </DialogDescription>
        </DialogHeader>

        {(requestingBranchName || supplyBranchName) && (
          <div className="text-sm text-slate-600 flex items-center gap-2">
            <span className="font-semibold text-slate-800">
              {requestingBranchName || 'Requester'}
            </span>
            <ArrowRight size={14} className="text-slate-400" />
            <span className="font-semibold text-slate-800">
              {supplyBranchName || 'Supply'}
            </span>
            {request.po_number && (
              <span className="ml-auto font-mono text-xs text-slate-500">
                {request.po_number}
              </span>
            )}
          </div>
        )}

        {loading && (
          <div className="py-10 flex flex-col items-center gap-2 text-slate-500"
            data-testid="request-qr-loading">
            <RefreshCw size={18} className="animate-spin" />
            <span className="text-sm">Loading QR…</span>
          </div>
        )}

        {!loading && error && (
          <div className="py-6 text-center text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded-md"
            data-testid="request-qr-error">
            {error}
          </div>
        )}

        {!loading && !error && code && (
          <>
            <div className="flex justify-center py-4">
              <div className="p-3 bg-white rounded-lg border border-slate-200 shadow-sm">
                <QRCodeSVG
                  value={url}
                  size={200}
                  level="M"
                  fgColor="#1A4D2E"
                  bgColor="#FFFFFF"
                  data-testid="request-qr-image"
                />
              </div>
            </div>

            <div className="space-y-2">
              <div>
                <p className="text-[10px] text-slate-400 uppercase tracking-wide mb-1">
                  Short code
                </p>
                <div className="flex items-center gap-2">
                  <code
                    className="flex-1 bg-slate-100 border border-slate-200 rounded px-3 py-2 font-mono text-sm text-slate-800 tracking-widest text-center"
                    data-testid="request-qr-short-code">
                    {code}
                  </code>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => copy(code, 'Code')}
                    data-testid="request-qr-copy-code">
                    <Copy size={14} />
                  </Button>
                </div>
              </div>

              <div>
                <p className="text-[10px] text-slate-400 uppercase tracking-wide mb-1">
                  Direct link
                </p>
                <div className="flex items-center gap-2">
                  <input
                    type="text"
                    readOnly
                    value={url}
                    className="flex-1 bg-slate-50 border border-slate-200 rounded px-3 py-2 font-mono text-xs text-slate-700 truncate"
                    data-testid="request-qr-url"
                    onFocus={(e) => e.target.select()}
                  />
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => copy(url, 'Link')}
                    data-testid="request-qr-copy-url">
                    <Copy size={14} />
                  </Button>
                </div>
              </div>
            </div>
          </>
        )}

        <div className="flex justify-end pt-2">
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            data-testid="request-qr-close-btn">
            Close
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
