/**
 * TermsPicker — Payment terms picker for credit / partial sales.
 *
 * Surfaced inline inside `CheckoutDialog` when the user picks Credit or
 * Partial payment type, so they can't accidentally ship a credit sale on
 * "COD / 0 days" and forget to set the due date.
 *
 * UX:
 *   • Row of preset chips: COD, Net 7, Net 15, Net 30, Net 45, Net 60, Net 90.
 *     Whatever is in `options` becomes a chip — so future org-customized
 *     terms (e.g. "Net 14", "EOM") just need to be added to the GET
 *     `/api/settings/terms-options` response.
 *   • "Custom" chip → reveals a small day-count input. Saves as
 *     `Custom Net <N>` label with `terms_days = N`.
 *   • Live due-date preview computed from `transactionDate + termsDays`,
 *     so the cashier sees the date the receipt will print with.
 *
 * Inputs:
 *   - value          (string)         current `terms` label
 *   - days           (number)         current `terms_days`
 *   - options        (array)          [{ key, label, days }, ...]
 *   - transactionDate (string)        the invoice's `transaction_date`
 *                                     (YYYY-MM-DD) used to compute due-date.
 *   - onChange(label, days)           callback when the user picks anything.
 */
import React, { useMemo, useState, useEffect } from 'react';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Calendar, Check } from 'lucide-react';

// Universal fallback preset list — used when the org's terms-options
// endpoint hasn't returned anything yet (initial render).
const FALLBACK_PRESETS = [
  { key: 'COD',    label: 'COD',         days: 0 },
  { key: 'NET7',   label: 'Net 7 Days',  days: 7 },
  { key: 'NET15',  label: 'Net 15 Days', days: 15 },
  { key: 'NET30',  label: 'Net 30 Days', days: 30 },
  { key: 'NET45',  label: 'Net 45 Days', days: 45 },
  { key: 'NET60',  label: 'Net 60 Days', days: 60 },
  { key: 'NET90',  label: 'Net 90 Days', days: 90 },
];

function addDays(yyyymmdd, n) {
  if (!yyyymmdd) return '';
  const d = new Date(yyyymmdd + 'T00:00:00');
  if (Number.isNaN(d.getTime())) return '';
  d.setDate(d.getDate() + Number(n || 0));
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${dd}`;
}

function prettyDate(yyyymmdd) {
  if (!yyyymmdd) return '';
  const d = new Date(yyyymmdd + 'T00:00:00');
  return d.toLocaleDateString('en-PH', {
    month: 'short', day: 'numeric', year: 'numeric'
  });
}

export default function TermsPicker({
  value,
  days,
  options,
  transactionDate,
  onChange,
}) {
  const presets = (options && options.length) ? options : FALLBACK_PRESETS;
  const isCustom = useMemo(() => {
    // A term is "custom" when its current label isn't found in the preset list.
    if (!value) return false;
    return !presets.some(p => p.label === value);
  }, [presets, value]);

  // Local state for the custom day input (debounced commit on change).
  const [customDays, setCustomDays] = useState(isCustom ? String(days || '') : '');
  const [showCustom, setShowCustom] = useState(isCustom);

  useEffect(() => {
    if (isCustom) {
      setCustomDays(String(days || ''));
      setShowCustom(true);
    }
  }, [isCustom, days]);

  const dueDate = useMemo(
    () => addDays(transactionDate, days || 0),
    [transactionDate, days]
  );

  const handlePreset = (preset) => {
    setShowCustom(false);
    setCustomDays('');
    onChange(preset.label, preset.days);
  };

  const handleCustomClick = () => {
    setShowCustom(true);
    // If we're switching from a preset to custom, seed with current days.
    if (!isCustom) setCustomDays(String(days || ''));
  };

  const handleCustomDaysChange = (raw) => {
    setCustomDays(raw);
    const n = Math.max(0, parseInt(raw, 10) || 0);
    onChange(n === 0 ? 'COD' : `Custom Net ${n} Days`, n);
  };

  return (
    <div
      className="rounded-xl border border-amber-200 bg-amber-50/50 p-3 space-y-2.5"
      data-testid="terms-picker"
    >
      <div className="flex items-center justify-between">
        <Label className="text-xs font-semibold text-amber-800 uppercase tracking-wide">
          Payment Terms
        </Label>
        {value && (
          <span className="text-[11px] text-amber-700 flex items-center gap-1 font-medium" data-testid="terms-picker-current">
            <Check size={11} />
            {value}{(days || 0) > 0 ? ` · ${days} day${days === 1 ? '' : 's'}` : ''}
          </span>
        )}
      </div>

      <div className="flex flex-wrap gap-1.5">
        {presets.map(p => {
          const active = !showCustom && value === p.label;
          return (
            <button
              key={p.key || p.label}
              type="button"
              onClick={() => handlePreset(p)}
              data-testid={`terms-chip-${(p.key || p.label).toLowerCase().replace(/\s+/g, '-')}`}
              className={`text-xs px-2.5 py-1.5 rounded-lg border-2 transition-all font-medium ${
                active
                  ? 'border-amber-600 bg-amber-600 text-white shadow-sm'
                  : 'border-amber-200 bg-white text-amber-800 hover:border-amber-400'
              }`}
            >
              {p.label.replace(/ Days?$/i, '')}
            </button>
          );
        })}
        <button
          type="button"
          onClick={handleCustomClick}
          data-testid="terms-chip-custom"
          className={`text-xs px-2.5 py-1.5 rounded-lg border-2 transition-all font-medium ${
            showCustom || isCustom
              ? 'border-amber-600 bg-amber-600 text-white shadow-sm'
              : 'border-dashed border-amber-300 bg-white text-amber-700 hover:border-amber-500'
          }`}
        >
          Custom
        </button>
      </div>

      {showCustom && (
        <div className="flex items-center gap-2 pt-1" data-testid="terms-custom-row">
          <Label className="text-xs text-amber-700 shrink-0">Days from today</Label>
          <Input
            type="number"
            min="0"
            inputMode="numeric"
            value={customDays}
            onChange={(e) => handleCustomDaysChange(e.target.value)}
            placeholder="e.g. 14"
            className="h-8 w-24 text-sm border-amber-300"
            data-testid="terms-custom-input"
          />
          {(days || 0) > 0 && (
            <span className="text-[11px] text-amber-700">
              = Custom Net {days} Days
            </span>
          )}
        </div>
      )}

      {dueDate && (
        <div
          className="flex items-center gap-1.5 text-[11px] text-amber-800 bg-amber-100/70 rounded-md px-2 py-1.5"
          data-testid="terms-due-preview"
        >
          <Calendar size={12} />
          <span>
            Due date: <span className="font-semibold">{prettyDate(dueDate)}</span>
            {(days || 0) === 0 && <span className="ml-1 italic">(today — COD)</span>}
          </span>
        </div>
      )}
    </div>
  );
}
