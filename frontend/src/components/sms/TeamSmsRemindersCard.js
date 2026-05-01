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
  // Which branch drives the "Fires at" preview next to each stage row.
  const [previewBranchId, setPreviewBranchId] = useState('');
  // Which stage row is currently firing a test SMS (so we can disable the
  // button + show a spinner without a full-card re-render).
  const [testing, setTesting] = useState({});

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
  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    // Seed per-branch close-time input from the `branches` prop. We convert
    // a 24 h float (e.g. 18.5) into the HH:mm shape the <input type="time">
    // expects — and back on save.
    const next = {};
    const disabledNext = {};
    (branches || []).forEach(b => {
      const h = Number.isFinite(b.close_time_h) ? b.close_time_h : 18;
      const hh = String(Math.floor(h)).padStart(2, '0');
      const mm = String(Math.round((h - Math.floor(h)) * 60)).padStart(2, '0');
      next[b.id] = `${hh}:${mm}`;
      disabledNext[b.id] = !!b.close_reminder_disabled;
    });
    setBranchCloseH(next);
    setBranchDisabled(disabledNext);
    if (!previewBranchId && branches?.[0]?.id) setPreviewBranchId(branches[0].id);
  }, [branches]);

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

  const previewBranch = branches.find(b => b.id === previewBranchId);
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
        {branches.length > 0 && (
          <div className="border border-slate-200 rounded-lg p-3 bg-slate-50">
            <div className="flex items-center gap-2 mb-2">
              <Clock size={14} className="text-slate-500" />
              <p className="text-xs font-semibold text-slate-600 uppercase tracking-wide">
                Branch Closing Times
              </p>
            </div>
            <div className="space-y-2">
              {branches.map(br => {
                const isDisabled = !!branchDisabled[br.id];
                return (
                <div key={br.id} className={`flex flex-wrap items-center gap-2 text-xs ${isDisabled ? 'opacity-60' : ''}`}>
                  <span className="flex-1 min-w-[120px] font-medium text-slate-700 flex items-center gap-1.5">
                    {br.name}
                    {isDisabled && (
                      <span className="inline-flex items-center gap-1 text-[10px] font-medium text-slate-500 bg-slate-200 border border-slate-300 px-1.5 py-0.5 rounded"
                        data-testid={`branch-disabled-pill-${br.id}`}>
                        <BellOff size={10} /> Muted
                      </span>
                    )}
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
                  <button
                    type="button"
                    data-testid={`branch-mute-toggle-${br.id}`}
                    disabled={!!togglingDisabled[br.id]}
                    onClick={() => toggleBranchDisabled(br.id)}
                    title={isDisabled
                      ? 'Turn close-day SMS back on for this branch'
                      : 'Mute close-day SMS reminders for this branch (useful for transfer-only / warehouse branches)'}
                    className={`inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded-md border transition-colors disabled:opacity-40 ${
                      isDisabled
                        ? 'border-slate-300 text-slate-600 hover:bg-slate-100'
                        : 'border-amber-300 text-amber-700 hover:bg-amber-50'
                    }`}>
                    {togglingDisabled[br.id]
                      ? <Loader2 size={10} className="animate-spin" />
                      : isDisabled ? <BellRing size={10} /> : <BellOff size={10} />}
                    {isDisabled ? 'Un-mute' : 'Mute'}
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
