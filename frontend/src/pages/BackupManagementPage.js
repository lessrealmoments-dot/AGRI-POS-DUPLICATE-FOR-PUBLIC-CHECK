import { useState, useEffect, useCallback } from 'react';
import { useAuth, api } from '../contexts/AuthContext';
import { formatPHP } from '../lib/utils';
import { Card, CardContent } from '../components/ui/card';
import { Button } from '../components/ui/button';
import { Badge } from '../components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../components/ui/table';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select';
import { Database, Download, RotateCcw, Clock, Shield, HardDrive, RefreshCw, AlertTriangle, CheckCircle2 } from 'lucide-react';
import { toast } from 'sonner';
import { formatDateTime } from '../lib/dateFormat';

export default function BackupManagementPage() {
  const { user } = useAuth();
  const isSuper = user?.is_super_admin;
  const [orgSummary, setOrgSummary] = useState([]);
  const [siteBackups, setSiteBackups] = useState([]);
  const [orgBackups, setOrgBackups] = useState([]);
  const [selectedOrg, setSelectedOrg] = useState(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState('');
  const [restoreDialog, setRestoreDialog] = useState(null);
  const [schedule, setSchedule] = useState(null);

  const orgId = user?.organization_id;

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      if (isSuper) {
        const [summaryRes, siteRes, schedRes] = await Promise.all([
          api.get('/backups/org-summary'),
          api.get('/backups/site/list'),
          api.get('/backups/schedule'),
        ]);
        setOrgSummary(summaryRes.data.organizations || []);
        setSiteBackups(siteRes.data.backups || []);
        setSchedule(schedRes.data);
      }
      if (orgId) {
        const res = await api.get(`/backups/org/${orgId}/list`);
        setOrgBackups(res.data.backups || []);
      }
    } catch { toast.error('Failed to load backup data'); }
    setLoading(false);
  }, [isSuper, orgId]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleSiteBackup = async () => {
    setActionLoading('site');
    try {
      const res = await api.post('/backups/site/trigger');
      toast.success(`Site backup created: ${res.data.filename} (${res.data.size_mb} MB)`);
      fetchData();
    } catch (e) { toast.error(e.response?.data?.detail || 'Backup failed'); }
    setActionLoading('');
  };

  const handleOrgBackup = async (oid) => {
    setActionLoading(`org-${oid}`);
    try {
      const res = await api.post(`/backups/org/${oid}/trigger`);
      toast.success(`Org backup: ${res.data.total_documents} docs (${res.data.size_mb} MB) → R2`);
      fetchData();
      if (selectedOrg === oid) {
        const listRes = await api.get(`/backups/org/${oid}/list`);
        setOrgBackups(listRes.data.backups || []);
      }
    } catch (e) { toast.error(e.response?.data?.detail || 'Backup failed'); }
    setActionLoading('');
  };

  const handleRestore = async () => {
    if (!restoreDialog) return;
    setActionLoading('restore');
    try {
      const res = await api.post(`/backups/org/${restoreDialog.org_id}/restore/${restoreDialog.filename}`);
      if (res.data.success) {
        toast.success(`Restored ${res.data.total_documents_restored} docs. Safety backup: ${res.data.safety_backup}`);
      } else {
        toast.error(`Restore had errors: ${res.data.errors?.join(', ')}`);
      }
      setRestoreDialog(null);
      fetchData();
    } catch (e) { toast.error(e.response?.data?.detail || 'Restore failed'); }
    setActionLoading('');
  };

  const selectOrg = async (oid) => {
    setSelectedOrg(oid);
    try {
      const res = await api.get(`/backups/org/${oid}/list`);
      setOrgBackups(res.data.backups || []);
    } catch { setOrgBackups([]); }
  };

  const fmtDate = (d) => d ? formatDateTime(d) : '—';

  if (loading) return <div className="flex items-center justify-center h-64 text-slate-400">Loading backup data...</div>;

  return (
    <div className="space-y-6 animate-fadeIn" data-testid="backup-management-page">
      <div>
        <h1 className="text-2xl font-bold tracking-tight" style={{ fontFamily: 'Manrope' }}>Backup Management</h1>
        <p className="text-sm text-slate-500">Manage database backups, restore points, and schedules</p>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Card className="border-slate-200"><CardContent className="p-4">
          <div className="flex items-center gap-2 text-slate-500 text-xs mb-1"><Database size={12} /> Site Backups</div>
          <p className="text-2xl font-bold" style={{ fontFamily: 'Manrope' }}>{siteBackups.length}</p>
          <p className="text-[10px] text-slate-400">Full DB snapshots on R2</p>
        </CardContent></Card>
        {isSuper && <Card className="border-slate-200"><CardContent className="p-4">
          <div className="flex items-center gap-2 text-slate-500 text-xs mb-1"><HardDrive size={12} /> Organizations</div>
          <p className="text-2xl font-bold" style={{ fontFamily: 'Manrope' }}>{orgSummary.length}</p>
          <p className="text-[10px] text-slate-400">{orgSummary.filter(o => o.last_backup_at).length} with backups</p>
        </CardContent></Card>}
        <Card className="border-slate-200"><CardContent className="p-4">
          <div className="flex items-center gap-2 text-slate-500 text-xs mb-1"><Clock size={12} /> Schedule</div>
          <p className="text-2xl font-bold" style={{ fontFamily: 'Manrope' }}>Every 6h</p>
          <p className="text-[10px] text-slate-400">{schedule?.org_backup_hours?.map(h => `${h}:00`).join(', ') || '1, 7, 13, 19'}</p>
        </CardContent></Card>
        <Card className="border-slate-200"><CardContent className="p-4">
          <div className="flex items-center gap-2 text-slate-500 text-xs mb-1"><Shield size={12} /> Storage</div>
          <p className="text-2xl font-bold text-emerald-600" style={{ fontFamily: 'Manrope' }}>R2</p>
          <p className="text-[10px] text-slate-400">Cloudflare R2 encrypted</p>
        </CardContent></Card>
      </div>

      {/* Quick Actions */}
      <div className="flex gap-3 flex-wrap">
        {isSuper && (
          <Button onClick={handleSiteBackup} disabled={!!actionLoading} className="bg-[#1A4D2E] hover:bg-[#14532d] text-white" data-testid="site-backup-btn">
            {actionLoading === 'site' ? <RefreshCw size={14} className="animate-spin mr-1.5" /> : <Database size={14} className="mr-1.5" />}
            Backup Entire Site
          </Button>
        )}
        {orgId && (
          <Button onClick={() => handleOrgBackup(orgId)} disabled={!!actionLoading} variant="outline" data-testid="my-org-backup-btn">
            {actionLoading === `org-${orgId}` ? <RefreshCw size={14} className="animate-spin mr-1.5" /> : <Download size={14} className="mr-1.5" />}
            Backup My Company
          </Button>
        )}
      </div>

      {/* Super Admin: Organization Backup Overview */}
      {isSuper && orgSummary.length > 0 && (
        <Card className="border-slate-200">
          <CardContent className="p-0">
            <div className="p-4 border-b border-slate-100">
              <h2 className="font-bold text-lg" style={{ fontFamily: 'Manrope' }}>Organizations</h2>
            </div>
            <Table>
              <TableHeader><TableRow className="bg-slate-50">
                <TableHead className="text-xs uppercase text-slate-500">Organization</TableHead>
                <TableHead className="text-xs uppercase text-slate-500">Plan</TableHead>
                <TableHead className="text-xs uppercase text-slate-500 text-right">Documents</TableHead>
                <TableHead className="text-xs uppercase text-slate-500">Last Backup</TableHead>
                <TableHead className="text-xs uppercase text-slate-500 text-right">Size</TableHead>
                <TableHead className="w-40"></TableHead>
              </TableRow></TableHeader>
              <TableBody>
                {orgSummary.map(org => (
                  <TableRow key={org.org_id} className={selectedOrg === org.org_id ? 'bg-emerald-50/50' : 'hover:bg-slate-50'}>
                    <TableCell className="font-medium">{org.org_name}</TableCell>
                    <TableCell><Badge className="text-[10px]" variant="outline">{org.plan}</Badge></TableCell>
                    <TableCell className="text-right font-mono">{org.total_documents?.toLocaleString()}</TableCell>
                    <TableCell className="text-xs text-slate-500">{fmtDate(org.last_backup_at)}</TableCell>
                    <TableCell className="text-right font-mono text-xs">{org.last_backup_size_mb ? `${org.last_backup_size_mb} MB` : '—'}</TableCell>
                    <TableCell>
                      <div className="flex gap-1">
                        <Button size="sm" variant="ghost" className="h-7 px-2 text-xs text-emerald-600"
                          onClick={() => handleOrgBackup(org.org_id)} disabled={!!actionLoading}
                          data-testid={`backup-org-${org.org_id}`}>
                          {actionLoading === `org-${org.org_id}` ? <RefreshCw size={11} className="animate-spin" /> : <Download size={11} />}
                        </Button>
                        <Button size="sm" variant="ghost" className="h-7 px-2 text-xs text-blue-600"
                          onClick={() => selectOrg(org.org_id)}
                          data-testid={`view-org-${org.org_id}`}>
                          View
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {/* Org Backup History (selected org or own org) */}
      {(selectedOrg || orgId) && (
        <Card className="border-slate-200">
          <CardContent className="p-0">
            <div className="p-4 border-b border-slate-100 flex items-center justify-between">
              <h2 className="font-bold text-lg" style={{ fontFamily: 'Manrope' }}>
                Restore Points {selectedOrg && isSuper
                  ? `— ${orgSummary.find(o => o.org_id === selectedOrg)?.org_name || selectedOrg.slice(0,8)}`
                  : '— My Company'}
              </h2>
              <Badge variant="outline" className="text-[10px]">{orgBackups.filter(b => !b.type).length} backups</Badge>
            </div>
            <Table>
              <TableHeader><TableRow className="bg-slate-50">
                <TableHead className="text-xs uppercase text-slate-500">Date</TableHead>
                <TableHead className="text-xs uppercase text-slate-500">Type</TableHead>
                <TableHead className="text-xs uppercase text-slate-500 text-right">Documents</TableHead>
                <TableHead className="text-xs uppercase text-slate-500 text-right">Size</TableHead>
                <TableHead className="text-xs uppercase text-slate-500">Triggered By</TableHead>
                <TableHead className="w-32"></TableHead>
              </TableRow></TableHeader>
              <TableBody>
                {orgBackups.map((b, i) => (
                  <TableRow key={i} className={b.type === 'restore' ? 'bg-amber-50/50' : ''}>
                    <TableCell className="text-xs font-mono">{fmtDate(b.created_at)}</TableCell>
                    <TableCell>
                      {b.type === 'restore' ? (
                        <Badge className="text-[10px] bg-amber-100 text-amber-700">Restore</Badge>
                      ) : b.triggered_by === 'scheduled' ? (
                        <Badge className="text-[10px] bg-blue-100 text-blue-700">Auto</Badge>
                      ) : (
                        <Badge className="text-[10px] bg-emerald-100 text-emerald-700">Manual</Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-right font-mono">{b.total_documents?.toLocaleString() || b.collections_restored || '—'}</TableCell>
                    <TableCell className="text-right font-mono text-xs">{b.size_mb ? `${b.size_mb} MB` : '—'}</TableCell>
                    <TableCell className="text-xs text-slate-500">
                      {b.type === 'restore' ? `by ${b.restored_by}` : (b.triggered_by || '—')}
                    </TableCell>
                    <TableCell>
                      {!b.type && b.filename && (
                        <Button size="sm" variant="ghost" className="h-7 px-2 text-xs text-amber-600"
                          onClick={() => setRestoreDialog({ org_id: selectedOrg || orgId, filename: b.filename, backup: b })}
                          data-testid={`restore-${b.filename}`}>
                          <RotateCcw size={11} className="mr-1" /> Restore
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
                {orgBackups.length === 0 && (
                  <TableRow><TableCell colSpan={6} className="text-center py-8 text-slate-400">No backups yet</TableCell></TableRow>
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {/* Site Backups (super admin) */}
      {isSuper && siteBackups.length > 0 && (
        <Card className="border-slate-200">
          <CardContent className="p-0">
            <div className="p-4 border-b border-slate-100">
              <h2 className="font-bold text-lg" style={{ fontFamily: 'Manrope' }}>Site Backups (Full Database)</h2>
            </div>
            <Table>
              <TableHeader><TableRow className="bg-slate-50">
                <TableHead className="text-xs uppercase text-slate-500">Filename</TableHead>
                <TableHead className="text-xs uppercase text-slate-500 text-right">Size</TableHead>
                <TableHead className="text-xs uppercase text-slate-500">Date</TableHead>
                <TableHead className="text-xs uppercase text-slate-500">Source</TableHead>
              </TableRow></TableHeader>
              <TableBody>
                {siteBackups.slice(0, 20).map((b, i) => (
                  <TableRow key={i}>
                    <TableCell className="font-mono text-xs">{b.filename}</TableCell>
                    <TableCell className="text-right font-mono text-xs">{b.size_mb} MB</TableCell>
                    <TableCell className="text-xs text-slate-500">{fmtDate(b.created_at)}</TableCell>
                    <TableCell><Badge className="text-[10px]" variant="outline">{b.source}</Badge></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      {/* Restore Confirmation Dialog */}
      <Dialog open={!!restoreDialog} onOpenChange={() => setRestoreDialog(null)}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-amber-700" style={{ fontFamily: 'Manrope' }}>
              <AlertTriangle size={18} /> Restore from Backup
            </DialogTitle>
          </DialogHeader>
          {restoreDialog && (
            <div className="space-y-4">
              <div className="p-3 rounded-lg bg-amber-50 border border-amber-200 text-sm text-amber-800">
                <p className="font-semibold mb-1">This will:</p>
                <ul className="list-disc list-inside text-xs space-y-0.5">
                  <li>Create a safety backup of current data first</li>
                  <li>Replace ALL company data with backup from <b>{fmtDate(restoreDialog.backup?.created_at)}</b></li>
                  <li>Affect {restoreDialog.backup?.total_documents?.toLocaleString() || '?'} documents</li>
                  <li>Other companies' data will NOT be affected</li>
                </ul>
              </div>
              <div className="p-3 rounded-lg bg-red-50 border border-red-200 text-xs text-red-700">
                Any changes made AFTER this backup point will be lost. A safety backup will be created automatically so you can undo if needed.
              </div>
              <div className="flex gap-2 justify-end pt-2 border-t">
                <Button variant="outline" onClick={() => setRestoreDialog(null)}>Cancel</Button>
                <Button onClick={handleRestore} disabled={actionLoading === 'restore'}
                  className="bg-amber-600 hover:bg-amber-700 text-white" data-testid="confirm-restore-btn">
                  {actionLoading === 'restore' ? <RefreshCw size={14} className="animate-spin mr-1.5" /> : <RotateCcw size={14} className="mr-1.5" />}
                  Confirm Restore
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
