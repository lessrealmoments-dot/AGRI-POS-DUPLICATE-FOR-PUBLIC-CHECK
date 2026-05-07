/**
 * Iter 253 — Z-Report Share Links tab (Audit Center)
 *
 * Lists active and revoked tokenized share links so admins/owners can audit
 * who received what and revoke proactively if needed.
 */
import { useEffect, useState, useCallback } from 'react';
import { api } from '../../contexts/AuthContext';
import { Card, CardContent } from '../ui/card';
import { Button } from '../ui/button';
import { Badge } from '../ui/badge';
import { RefreshCw, X, ExternalLink, ShieldAlert } from 'lucide-react';
import { toast } from 'sonner';
import { fmtDateTime } from '../../lib/utils';

export default function ZReportShareLinksTab({ branchId, currentUser }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [includeRevoked, setIncludeRevoked] = useState(false);
  const isOwnerOrAdmin = ['admin', 'owner'].includes(currentUser?.role);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (branchId) params.set('branch_id', branchId);
      params.set('include_revoked', String(includeRevoked));
      const res = await api.get(`/zreport-share/links/list?${params.toString()}`);
      setItems(res.data.items || []);
    } catch (err) {
      toast.error(`Failed to load share links: ${err?.response?.data?.detail || err.message}`);
    } finally {
      setLoading(false);
    }
  }, [branchId, includeRevoked]);

  useEffect(() => { load(); }, [load]);

  const revoke = async (token) => {
    if (!window.confirm('Revoke this share link? The recipient will lose access immediately.')) return;
    try {
      await api.post(`/zreport-share/links/${token}/revoke`, { note: 'Revoked from Audit Center' });
      toast.success('Share link revoked');
      load();
    } catch (err) {
      toast.error(`Failed: ${err?.response?.data?.detail || err.message}`);
    }
  };

  return (
    <div className="space-y-3" data-testid="zreport-share-links-tab">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h3 className="text-sm font-semibold text-slate-700">Z-Report SMS Share Links</h3>
        <div className="flex items-center gap-2">
          <label className="text-xs text-slate-500 flex items-center gap-1.5 cursor-pointer">
            <input
              type="checkbox" checked={includeRevoked}
              onChange={(e) => setIncludeRevoked(e.target.checked)}
              className="rounded border-slate-300"
            />
            Include revoked
          </label>
          <Button size="sm" variant="outline" onClick={load} disabled={loading}>
            <RefreshCw size={13} className={`mr-1 ${loading ? 'animate-spin' : ''}`} /> Refresh
          </Button>
        </div>
      </div>

      <p className="text-xs text-slate-500 leading-relaxed">
        Tokenized links sent via the closing SMS. Each recipient gets a unique link that opens the day's Z-Report on their phone with a Detailed PDF download. Links auto-revoke after 5 unique IPs to prevent SMS forwarding.
      </p>

      {loading ? (
        <div className="text-center py-6 text-slate-400 text-sm">Loading…</div>
      ) : items.length === 0 ? (
        <Card className="border-slate-200">
          <CardContent className="p-5 text-center text-slate-400 text-sm">
            No {includeRevoked ? '' : 'active '}share links yet.
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-2">
          {items.map((it) => {
            const expired = it.expires_at && it.expires_at < new Date().toISOString();
            const revoked = !!it.revoked;
            return (
            <Card key={it.token} className={revoked ? 'border-red-200' : expired ? 'border-amber-200' : 'border-emerald-200'}>
              <CardContent className="p-3.5">
                <div className="flex items-start justify-between gap-3 flex-wrap">
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-mono text-xs font-bold text-slate-700">{it.token}</span>
                      {revoked ? (
                        <Badge className="bg-red-100 text-red-700 border-red-300 text-[10px]">REVOKED</Badge>
                      ) : expired ? (
                        <Badge className="bg-amber-100 text-amber-700 border-amber-300 text-[10px]">EXPIRED</Badge>
                      ) : (
                        <Badge className="bg-emerald-100 text-emerald-700 border-emerald-300 text-[10px]">ACTIVE</Badge>
                      )}
                      {(it.unique_ips || []).length >= 3 && !revoked && (
                        <Badge className="bg-amber-50 text-amber-700 border-amber-200 text-[10px]">
                          <ShieldAlert size={9} className="mr-1" />
                          {(it.unique_ips || []).length} unique IPs
                        </Badge>
                      )}
                    </div>
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
                      <div>
                        <p className="text-[10px] uppercase text-slate-500 font-semibold">Recipient</p>
                        <p className="text-slate-700 truncate">{it.recipient_name} <span className="text-slate-400">({it.recipient_role})</span></p>
                      </div>
                      <div>
                        <p className="text-[10px] uppercase text-slate-500 font-semibold">Date</p>
                        <p className="font-mono text-slate-700">{it.date}</p>
                      </div>
                      <div>
                        <p className="text-[10px] uppercase text-slate-500 font-semibold">Accesses</p>
                        <p className="font-mono text-slate-700">{it.access_count || 0}</p>
                      </div>
                      <div>
                        <p className="text-[10px] uppercase text-slate-500 font-semibold">Expires</p>
                        <p className="text-slate-700 text-[11px]">{it.expires_at ? new Date(it.expires_at).toLocaleDateString('en-PH') : '—'}</p>
                      </div>
                    </div>
                    {revoked && it.revoke_reason && (
                      <p className="text-[11px] text-red-600 italic">Revoke reason: {it.revoke_reason}</p>
                    )}
                    <p className="text-[10px] text-slate-400">Created {fmtDateTime(it.created_at)}</p>
                  </div>
                  {!revoked && !expired && isOwnerOrAdmin && (
                    <Button
                      size="sm" variant="outline"
                      className="text-red-600 border-red-200 hover:bg-red-50"
                      onClick={() => revoke(it.token)}
                      data-testid={`zr-share-revoke-${it.token.slice(0, 8)}`}
                    >
                      <X size={13} className="mr-1" /> Revoke
                    </Button>
                  )}
                  {!revoked && !expired && isOwnerOrAdmin && (
                    <Button
                      size="sm" variant="ghost"
                      onClick={() => window.open(`/zr/${it.token}`, '_blank')}
                      title="Open in new tab"
                    >
                      <ExternalLink size={13} />
                    </Button>
                  )}
                </div>
              </CardContent>
            </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
