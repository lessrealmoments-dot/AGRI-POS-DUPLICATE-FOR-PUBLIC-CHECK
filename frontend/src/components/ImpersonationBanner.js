/**
 * ImpersonationBanner — visible across every page when a super admin has
 * entered a "View as Tenant" session. Calls /superadmin/impersonate/status
 * on mount and listens for changes. Hard-to-miss amber bar with an Exit
 * button so the super admin can never forget they're scoped to a tenant.
 */
import { useEffect, useState, useCallback } from 'react';
import { Eye, X } from 'lucide-react';
import { api, useAuth } from '../contexts/AuthContext';
import { toast } from 'sonner';

export default function ImpersonationBanner() {
  const { user } = useAuth();
  const [session, setSession] = useState(null);
  const [exiting, setExiting] = useState(false);

  const refresh = useCallback(async () => {
    if (!user?.is_super_admin) {
      setSession(null);
      return;
    }
    try {
      const r = await api.get('/superadmin/impersonate/status');
      setSession(r.data?.active ? r.data : null);
    } catch {
      setSession(null);
    }
  }, [user]);

  useEffect(() => {
    refresh();
    // Re-check every 60s in case the 4-hour TTL expires while the tab is open
    const t = setInterval(refresh, 60_000);
    return () => clearInterval(t);
  }, [refresh]);

  if (!session) return null;

  const handleExit = async () => {
    setExiting(true);
    try {
      await api.post('/superadmin/impersonate/exit');
      toast.success(`Exited ${session.target_org_name}`);
      setSession(null);
      // Reload to flush all cached tenant data from the page
      window.location.href = '/superadmin';
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Failed to exit impersonation');
      setExiting(false);
    }
  };

  // Time remaining
  const expiresAt = session.expires_at ? new Date(session.expires_at) : null;
  const remainingMin = expiresAt ? Math.max(0, Math.round((expiresAt - new Date()) / 60000)) : null;
  const remainingLabel = remainingMin == null ? '' :
    remainingMin >= 60 ? `${Math.floor(remainingMin / 60)}h ${remainingMin % 60}m` : `${remainingMin}m`;

  return (
    <div
      data-testid="impersonation-banner"
      className="bg-amber-500 text-amber-950 px-4 py-2 flex items-center justify-between gap-3 shadow-lg sticky top-0 z-[100] border-b-2 border-amber-700">
      <div className="flex items-center gap-2 min-w-0">
        <Eye size={16} className="flex-shrink-0" />
        <span className="text-xs sm:text-sm font-semibold truncate">
          Viewing as <span className="underline decoration-amber-700/40 decoration-2">{session.target_org_name}</span>
        </span>
        {remainingLabel && (
          <span className="text-[11px] bg-amber-950/15 px-2 py-0.5 rounded-full font-medium hidden sm:inline">
            {remainingLabel} left
          </span>
        )}
      </div>
      <button
        onClick={handleExit}
        disabled={exiting}
        data-testid="impersonation-exit-btn"
        className="flex items-center gap-1.5 bg-amber-950/20 hover:bg-amber-950/30 text-amber-950 px-3 py-1 rounded-md text-xs font-bold transition-colors disabled:opacity-60 flex-shrink-0">
        <X size={13} />
        {exiting ? 'Exiting…' : 'Exit'}
      </button>
    </div>
  );
}
