/* eslint-disable react-hooks/exhaustive-deps */
import { useState, useEffect, useCallback } from 'react';
import { toast } from 'sonner';
import { Loader2, BellRing, BellOff, Clock, CheckCircle2, Send } from 'lucide-react';

import { api } from '../../contexts/AuthContext';
import { Card, CardContent } from '../ui/card';

const ROLE_META = {
  cashier: { label: 'Cashier', color: 'bg-slate-100 text-slate-700 border-slate-300' },
  manager: { label: 'Manager', color: 'bg-blue-100 text-blue-700 border-blue-300' },
  owner:   { label: 'Owner',   color: 'bg-amber-100 text-amber-700 border-amber-300' },
  admin:   { label: 'Admin',   color: 'bg-emerald-100 text-emerald-700 border-emerald-300' },
  auditor: { label: 'Auditor', color: 'bg-purple-100 text-purple-700 border-purple-300' },
};

// Render a read-only time string (e.g. "15:00") given a branch close_time_h
// and a stage's `timing` hint — lets the admin see EXACTLY what local time
// the stage fires at for a given branch without maths.
function computeDisplayTime(timing, closeH) {
  // Simple heuristic: parse hints like "3 hours before close" / "At closing
  // time" / "1.5 h after close" and produce HH:mm. Falls back to the hint
  // text when the phrase doesn't match a known shape.
  if (!timing || typeof closeH !== 'number') return timing;
  const close = closeH;
  let offsetH = null;
  let label = null;
  if (/at\s+closing/i.test(timing)) { offsetH = 0; label = ''; }
  else if (/(\d+(?:\.\d+)?)\s*h(ours?)?\s+before\s+close/i.test(timing)) {
    offsetH = -parseFloat(RegExp.$1);
    label = `${RegExp.$1}h before`;
  } else if (/(\d+(?:\.\d+)?)\s*h(ours?)?\s+after\s+close/i.test(timing)) {
    offsetH = parseFloat(RegExp.$1);
    label = `${RegExp.$1}h after`;
  } else if (/7\s*am/i.test(timing)) {
    return '07:00 (Day+1)';
  } else if (/noon.*day\s*\+?1/i.test(timing)) {
    return '12:00 (Day+1)';
  }
  if (offsetH === null) return timing;
  const t = close + offsetH;
  const h = Math.floor(t);
  const m = Math.round((t - h) * 60);
  const hh = String(((h % 24) + 24) % 24).padStart(2, '0');
  const mm = String(m).padStart(2, '0');
  return `${hh}:${mm}`;
}

export default function TeamSmsRemindersCard({ branches = [] }) {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState({});   // { stage_key: true }
  const [stages, setStages] = useState([]);
  const [validRoles, setValidRoles] = useState([]);
  // Per-branch close_time_h input state, keyed by branch id. Seeded from
  // `branches` when mounted; local edits stay here until "Save" is clicked.
  const [branchCloseH, setBranchCloseH] = useState({});
  const [savingBranch, setSavingBranch] = useState({});
  // Per-branch "no close-day SMS" opt-out — keyed by branch id. Kept in
  // local state so toggling feels instant; mirrored to the server on click.
  const [branchDisabled, setBranchDisabled] = useState({});
  const [togglingDisabled, setTogglingDisabled] = useState({});
  // Fresh copy of branches fetched directly from the server. Parent passes a
  // cached copy from AuthContext/localStorage which can be stale for
  // `close_reminder_disabled` right after a mute toggle. Internal fetch
  // keeps the UI honest.
  const [liveBranches, setLiveBranches] = useState(null);
  const effectiveBranches = liveBranches || branches || [];
  // Which branch drives the "Fires at" preview next to each stage row.
  const [previewBranchId, setPreviewBranchId] = useState('');
  // Which stage row is currently firing a test SMS (so we can disable the
  // button + show a spinner without a full-card re-render).
  const [testing, setTesting] = useState({});

  const refreshBranches = useCallback(async () => {
    try {
      const r = await api.get('/branches');
      setLiveBranches(r.data || []);
    } catch { /* fall back to prop */ }
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get('/sms/close-reminder/stages');
      setStages(r.data?.stages || []);
      setValidRoles(r.data?.valid_roles || []);
    } catch (e) {
      toast.error('Failed to load Team SMS settings');
    }
    setLoading(false);
  }, []);
  useEffect(() => { load(); refreshBranches(); }, [load, refreshBranches]);

  useEffect(() => {
    // Seed per-branch close-time input from the effective branch list
    // (live > prop). We convert a 24h float (e.g. 18.5) into the HH:mm shape
    // the <input type="time"> expects — and back on save.
    const next = {};
    const disabledNext = {};
    (effectiveBranches || []).forEach(b => {
      const h = Number.isFinite(b.close_time_h) ? b.close_time_h : 18;
      const hh = String(Math.floor(h)).padStart(2, '0');
      const mm = String(Math.round((h - Math.floor(h)) * 60)).padStart(2, '0');
      next[b.id] = `${hh}:${mm}`;
      disabledNext[b.id] = !!b.close_reminder_disabled;
    });
    setBranchCloseH(next);
    setBranchDisabled(disabledNext);
    if (!previewBranchId && effectiveBranches?.[0]?.id) setPreviewBranchId(effectiveBranches[0].id);
  }, [effectiveBranches]);

  const updateStage = async (stage_key, patch) => {
    const stage = stages.find(s => s.stage_key === stage_key);
    if (!stage) return;
    const next = { enabled: stage.enabled, recipients: stage.recipients, ...patch };
    setSaving(s => ({ ...s, [stage_key]: true }));
    // Optimistic update so toggles feel instant
    setStages(list => list.map(s => s.stage_key === stage_key ? { ...s, ...next } : s));
    try {
      const r = await api.put(`/sms/close-reminder/stages/${stage_key}`, next);
      // Echo back to reconcile any server-dropped roles
      setStages(list => list.map(s => s.stage_key === stage_key
        ? { ...s, enabled: r.data.enabled, recipients: r.data.recipients } : s));
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Save failed');
      load();
    }
    setSaving(s => ({ ...s, [stage_key]: false }));
  };

  const toggleRole = (stage_key, role) => {
    const stage = stages.find(s => s.stage_key === stage_key);
    if (!stage) return;
    const has = stage.recipients.includes(role);
    const recipients = has
      ? stage.recipients.filter(r => r !== role)
      : [...stage.recipients, role];
    updateStage(stage_key, { recipients });
  };

  const saveBranchClose = async (branchId) => {
    const raw = branchCloseH[branchId] || '';
    const [hhStr, mmStr] = raw.split(':');
    const hh = parseInt(hhStr, 10);
    const mm = parseInt(mmStr || '0', 10);
    if (Number.isNaN(hh) || Number.isNaN(mm)) {
      toast.error('Invalid time');
      return;
    }
    const close_time_h = hh + mm / 60;
    setSavingBranch(s => ({ ...s, [branchId]: true }));
    try {
      await api.put(`/sms/close-reminder/branch-close-time/${branchId}`, { close_time_h });
      toast.success('Close time saved');
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Save failed');
    }
    setSavingBranch(s => ({ ...s, [branchId]: false }));
  };

  // Flip the "no close-day SMS reminders for this branch" flag. Some branches
  // (warehouses, transfer-only) never handle money — owners want silence.
  // After save we RECONCILE with the server's echoed value (source of truth)
  // and re-fetch the branches list so subsequent re-renders don't overwrite
  // the new state from a stale localStorage cache.
  const toggleBranchDisabled = async (branchId) => {
    const nextDisabled = !branchDisabled[branchId];
    setTogglingDisabled(s => ({ ...s, [branchId]: true }));
    // Optimistic flip so the UI feels instant
    setBranchDisabled(s => ({ ...s, [branchId]: nextDisabled }));
    try {
      const r = await api.put(`/sms/close-reminder/branch-toggle/${branchId}`, { disabled: nextDisabled });
      const serverValue = !!r.data?.close_reminder_disabled;
      const purged = Number(r.data?.purged || 0);
      setBranchDisabled(s => ({ ...s, [branchId]: serverValue }));
      if (serverValue) {
        toast.success(
          purged > 0
            ? `🔕 MUTED — ${purged} pending close-day SMS cancelled for this branch`
            : '🔕 Close-day SMS MUTED for this branch'
        );
      } else {
        toast.success('🔔 Close-day SMS RE-ENABLED for this branch');
      }
      // Refresh the fresh-from-server copy so optimistic state sticks
      refreshBranches();
    } catch (e) {
      // Roll back on failure
      setBranchDisabled(s => ({ ...s, [branchId]: !nextDisabled }));
      toast.error(e.response?.data?.detail || 'Save failed');
    }
    setTogglingDisabled(s => ({ ...s, [branchId]: false }));
  };

  // Emergency "Stop All Pending Close-Day SMS" — cancels every pending,
  // deferred, or failed close-reminder row across ALL branches in this org.
  // Used during gateway-replay storms (DNS outage on the phone → same
  // SMS keeps being re-sent via GSM). Confirms first because it's
  // destructive; customer/expense SMS are untouched.
  const [cancellingAll, setCancellingAll] = useState(false);
  const cancelAllPending = async () => {
    // eslint-disable-next-line no-alert
    const ok = window.confirm(
      'Cancel ALL pending close-day SMS across every branch?\n\n'
      + 'This only affects close-reminder messages (did-not-close, approaching-close, overdue, day-after recap).\n'
      + 'Customer, expense, and other SMS are untouched.\n\n'
      + 'Use this to stop a gateway-replay storm immediately.'
    );
    if (!ok) return;
    setCancellingAll(true);
    try {
      const r = await api.post('/sms/queue/cancel-pending-close-reminders');
      const n = Number(r.data?.cancelled || 0);
      if (n > 0) {
        toast.success(`🛑 ${n} pending close-day SMS cancelled. Gateway will stop receiving these.`);
      } else {
        toast.success('Nothing to cancel — the close-day queue is already clean.');
      }
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Cancel failed');
    }
    setCancellingAll(false);
  };


  // Fire a [SAMPLE] SMS for a single stage right now, against the branch
  // currently selected in the Preview column. Resolves recipients exactly
  // like the live scheduler does, so admins can verify routing without
  // waiting for the real trigger time.
  const testStage = async (stage_key) => {
    if (!previewBranchId) {
      toast.error('Pick a branch with "Preview" first so the test knows which team to notify.');
      return;
    }
    setTesting(t => ({ ...t, [stage_key]: true }));
    try {
      const r = await api.post(`/sms/close-reminder/test-stage/${stage_key}`, {
        branch_id: previewBranchId,
      });
      const count = r.data?.queued || 0;
      const resolution = r.data?.resolution || {};
      const recipients = r.data?.recipients || [];
      // Build a concise per-role breakdown so admins can see WHY a role
      // resolved to N recipients (or why it fell back).
      const lines = [];
      Object.entries(resolution).forEach(([role, info]) => {
        const matched = info?.matched_users || 0;
        const noPhone = info?.users_without_phone || 0;
        const fb = info?.fallback_used;
        let bits = [];
        if (matched) bits.push(`${matched} user${matched === 1 ? '' : 's'}`);
        if (noPhone) bits.push(`${noPhone} no-phone`);
        if (fb) bits.push('+fallback');
        if (!bits.length) bits.push('none');
        lines.push(`${role}: ${bits.join(', ')}`);
      });
      const fbCount = recipients.filter(x => x.fallback).length;
      if (count === 0) {
        toast.warning(
          'Queued 0 recipients. ' + (lines.length ? '(' + lines.join(' · ') + ')' : '')
          + ' Check Team phone numbers and Collection Recipient fallbacks.'
        );
      } else {
        const fbNote = fbCount > 0 ? ` (${fbCount} via Collection-Recipient fallback)` : '';
        toast.success(
          `[SAMPLE] queued for ${count} recipient${count === 1 ? '' : 's'}${fbNote}.`
          + (lines.length ? ' — ' + lines.join(' · ') : '')
        );
      }
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Test SMS failed');
    }
    setTesting(t => ({ ...t, [stage_key]: false }));
  };

  if (loading) {
    return (
      <Card className="border-emerald-200">
        <CardContent className="p-5 flex items-center justify-center text-sm text-slate-400">
          <Loader2 className="w-4 h-4 mr-2 animate-spin" /> Loading Team SMS settings…
        </CardContent>
      </Card>
    );
  }

  const previewBranch = effectiveBranches.find(b => b.id === previewBranchId);
  const previewCloseH = previewBranch
    ? (() => {
      const raw = branchCloseH[previewBranch.id] || '18:00';
      const [h, m] = raw.split(':').map(x => parseInt(x, 10) || 0);
      return h + m / 60;
    })()
    : 18;

  return (
    <Card className="border-emerald-200">
      <CardContent className="p-5 space-y-5">
        <div>
          <h2 className="text-sm font-bold text-slate-700 flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-emerald-500" />
            Team SMS Reminders
          </h2>
          <p className="text-xs text-slate-400 mt-1">
            Toggle each reminder stage on/off and pick which of your team
            roles receives it. Times are computed from each branch's close
            time — configure that below.
          </p>
        </div>

        {/* Clarifier — these toggles are ORG-wide, not per-branch. The
            "Preview" button only swaps which branch's close time drives the
            "Fires at" label. Without this banner, admins were toggling roles
            after clicking Preview on Branch B and assuming the change was
            scoped to that branch — it wasn't. */}
        <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-[11px] text-amber-900 leading-relaxed">
          <p className="font-semibold mb-0.5">How these settings apply</p>
          <p>
            Stage <strong>on/off</strong> and <strong>recipient roles</strong> are saved <strong>once for your whole company</strong> — they
            apply to every branch. Only the <strong>close time</strong> below
            is per-branch. The <em>Preview</em> button just swaps which branch's
            close time drives the "Fires at" preview; it doesn't scope the toggles.
          </p>
        </div>

        {/* ── Per-branch close time ──────────────────────────────────────── */}
        {effectiveBranches.length > 0 && (
          <div className="border border-slate-200 rounded-lg p-3 bg-slate-50">
            <div className="flex items-center gap-2 mb-2">
              <Clock size={14} className="text-slate-500" />
              <p className="text-xs font-semibold text-slate-600 uppercase tracking-wide">
                Branch Closing Times
              </p>
            </div>
            <div className="space-y-2">
              {effectiveBranches.map(br => {
                const isDisabled = !!branchDisabled[br.id];
                const isToggling = !!togglingDisabled[br.id];
                return (
                <div key={br.id}
                  data-testid={`branch-row-${br.id}`}
                  data-muted={isDisabled ? 'true' : 'false'}
                  className={`flex flex-wrap items-center gap-2 text-xs rounded-md border p-2 transition-colors ${
                    isDisabled
                      ? 'bg-amber-50 border-amber-300'
                      : 'bg-white border-transparent'
                  }`}>
                  {/* Huge, unmissable status chip on the left */}
                  <span
                    className={`shrink-0 inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-wide px-2 py-1 rounded-md border ${
                      isDisabled
                        ? 'bg-amber-500 text-white border-amber-600'
                        : 'bg-emerald-500 text-white border-emerald-600'
                    }`}
                    data-testid={`branch-status-chip-${br.id}`}>
                    {isDisabled ? <><BellOff size={11} /> Muted</> : <><BellRing size={11} /> Active</>}
                  </span>
                  <span className={`flex-1 min-w-[120px] font-medium ${isDisabled ? 'text-slate-500 line-through decoration-amber-400' : 'text-slate-700'}`}>
                    {br.name}
                  </span>
                  <input
                    type="time"
                    data-testid={`branch-close-time-${br.id}`}
                    value={branchCloseH[br.id] || '18:00'}
                    disabled={isDisabled}
                    onChange={e => setBranchCloseH(v => ({ ...v, [br.id]: e.target.value }))}
                    className="border border-slate-200 rounded-md px-2 py-1 text-xs bg-white disabled:bg-slate-100 disabled:text-slate-400 disabled:cursor-not-allowed"
                  />
                  <button
                    type="button"
                    data-testid={`save-branch-close-${br.id}`}
                    disabled={savingBranch[br.id] || isDisabled}
                    onClick={() => saveBranchClose(br.id)}
                    className="px-2 py-1 text-[11px] font-medium rounded-md border border-emerald-300 text-emerald-700 hover:bg-emerald-50 disabled:opacity-40 disabled:cursor-not-allowed">
                    {savingBranch[br.id] ? '…' : 'Save'}
                  </button>
                  {/* Mute toggle — text + color changes dramatically with state.
                      Solid filled while active (loud) vs outlined while muted (quiet). */}
                  <button
                    type="button"
                    data-testid={`branch-mute-toggle-${br.id}`}
                    aria-pressed={isDisabled}
                    disabled={isToggling}
                    onClick={() => toggleBranchDisabled(br.id)}
                    title={isDisabled
                      ? 'Close-day SMS is currently MUTED — click to turn alerts back on'
                      : 'Close-day SMS is currently ACTIVE — click to MUTE for warehouse/transfer-only branches'}
                    className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-semibold rounded-md border-2 transition-all shadow-sm disabled:opacity-50 ${
                      isDisabled
                        ? 'bg-white text-slate-600 border-slate-300 hover:bg-slate-50 hover:border-slate-400'
                        : 'bg-amber-500 text-white border-amber-600 hover:bg-amber-600'
                    }`}>
                    {isToggling
                      ? <Loader2 size={12} className="animate-spin" />
                      : isDisabled ? <BellRing size={12} /> : <BellOff size={12} />}
                    {isToggling ? 'Saving…' : isDisabled ? 'Click to Un-Mute' : 'Click to Mute'}
                  </button>
                  <button
                    type="button"
                    onClick={() => setPreviewBranchId(br.id)}
                    title="Use this branch for the 'Fires at' preview"
                    className={`px-2 py-1 text-[11px] rounded-md transition-colors ${
                      previewBranchId === br.id
                        ? 'bg-emerald-600 text-white'
                        : 'text-emerald-700 hover:bg-emerald-50'
                    }`}>
                    {previewBranchId === br.id ? '◉ Preview' : 'Preview'}
                  </button>
                </div>
                );
              })}
            </div>
            {Object.values(branchDisabled).some(Boolean) && (
              <p className="mt-2 text-[10px] text-slate-500 italic">
                Muted branches are skipped by the close-day scheduler — no "approaching close", "overdue", or "day-after recap" SMS will fire for them. Z-Report summaries (sent when you actively close the day) still go out.
              </p>
            )}
            {/* Emergency kill-switch — pulls every pending close-reminder row
                out of the queue across ALL branches. Only close-reminder
                templates; customer/expense SMS untouched. Appears as a subtle
                red link so admins don't hit it by accident, but is obviously
                clickable. */}
            <div className="mt-3 pt-3 border-t border-slate-200 flex items-center justify-between gap-2">
              <p className="text-[10px] text-slate-400 italic">
                Gateway storming you with duplicate alerts? Stop them instantly:
              </p>
              <button
                type="button"
                data-testid="emergency-cancel-close-sms-btn"
                disabled={cancellingAll}
                onClick={cancelAllPending}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-semibold rounded-md border-2 border-red-300 text-red-700 bg-red-50 hover:bg-red-100 hover:border-red-400 transition-colors disabled:opacity-50"
                title="Cancel every pending close-day SMS across all branches (customer / expense SMS are NOT affected)">
                {cancellingAll
                  ? <><Loader2 size={12} className="animate-spin" /> Cancelling…</>
                  : <><BellOff size={12} /> Emergency: Stop All Pending Close-Day SMS</>}
              </button>
            </div>
          </div>
        )}

        {/* ── Stage rows ─────────────────────────────────────────────────── */}
        <div className="space-y-3">
          {stages.map(s => {
            const fires = computeDisplayTime(s.timing, previewCloseH);
            return (
              <div key={s.stage_key}
                data-testid={`stage-row-${s.stage_key}`}
                className={`border rounded-lg p-3 transition-colors ${
                  s.enabled ? 'border-slate-200 bg-white' : 'border-slate-200 bg-slate-50 opacity-70'
                }`}>
                <div className="flex flex-wrap items-start gap-3">
                  {/* Enable toggle */}
                  <button
                    type="button"
                    data-testid={`stage-toggle-${s.stage_key}`}
                    onClick={() => updateStage(s.stage_key, { enabled: !s.enabled })}
                    disabled={saving[s.stage_key]}
                    className={`shrink-0 w-10 h-6 rounded-full relative transition-colors ${
                      s.enabled ? 'bg-emerald-500' : 'bg-slate-300'
                    }`}>
                    <span className={`absolute top-0.5 w-5 h-5 bg-white rounded-full shadow transition-transform ${
                      s.enabled ? 'translate-x-4' : 'translate-x-0.5'
                    }`} />
                  </button>

                  <div className="flex-1 min-w-[180px]">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-semibold text-slate-800">{s.label}</span>
                      {previewBranch && fires && (
                        <span className="inline-flex items-center gap-1 text-[11px] font-mono text-emerald-700 bg-emerald-50 border border-emerald-200 px-1.5 py-0.5 rounded">
                          <BellRing size={10} />
                          Fires at {fires}
                        </span>
                      )}
                      {/* Test button — fires a [SAMPLE] SMS now to whoever is
                          currently configured for this stage, against the
                          branch picked in Preview. Hidden when stage is off. */}
                      {s.enabled && previewBranch && (s.recipients || []).length > 0 && (
                        <button
                          type="button"
                          data-testid={`stage-test-${s.stage_key}`}
                          disabled={!!testing[s.stage_key]}
                          onClick={() => testStage(s.stage_key)}
                          title={`Send a [SAMPLE] SMS now to ${(s.recipients || []).join(', ')} for ${previewBranch.name}`}
                          className="ml-auto inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-md border border-emerald-300 text-emerald-700 hover:bg-emerald-50 disabled:opacity-40">
                          {testing[s.stage_key]
                            ? <Loader2 size={10} className="animate-spin" />
                            : <Send size={10} />}
                          Test
                        </button>
                      )}
                    </div>
                    <p className="text-[11px] text-slate-500 mt-0.5">{s.timing}</p>

                    {/* Role checkboxes */}
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {validRoles.map(role => {
                        const on = s.recipients.includes(role);
                        const meta = ROLE_META[role] || { label: role, color: 'bg-slate-100 text-slate-600 border-slate-300' };
                        return (
                          <button
                            key={role}
                            type="button"
                            data-testid={`stage-${s.stage_key}-role-${role}`}
                            disabled={!s.enabled || saving[s.stage_key]}
                            onClick={() => toggleRole(s.stage_key, role)}
                            className={`px-2 py-0.5 text-[11px] rounded-md border transition-colors inline-flex items-center gap-1 ${
                              on
                                ? meta.color
                                : 'bg-white text-slate-400 border-dashed border-slate-200 hover:border-slate-300'
                            } ${!s.enabled ? 'opacity-40 cursor-not-allowed' : ''}`}>
                            {on && <CheckCircle2 size={10} />}
                            {meta.label}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        <p className="text-[11px] text-slate-400">
          Changes take effect within 1 minute (next scheduler tick). No restart required.
          Recipient roles resolve to the phone numbers you set on each user in the <strong>Team</strong> section.
        </p>
      </CardContent>
    </Card>
  );
}
