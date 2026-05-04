/**
 * CalcInput — QuickBooks-style inline calculator field.
 * --------------------------------------------------------------------
 * Behaviour (per Iter 225 spec):
 *  • Acts like a regular numeric text input until the user presses an
 *    operator key (+, −, *, /). At that moment a "calc tape" bubble
 *    pops up below the field and the input becomes a running formula.
 *  • The bubble shows the live expression and the running total.
 *  • Enter → commits the running total back to the field & calls onChange.
 *    Tab/blur → same commit behaviour.
 *  • Escape → cancels and reverts to the value the field had before the
 *    bubble opened.
 *  • Invalid expression on commit → silently reverts (no toast spam).
 *
 *  • 100 % offline-safe: no `eval`, no Function() — uses an internal
 *    shunting-yard parser limited to digits, decimal, +, −, *, /.
 *
 * Drop-in replacement for `<input type="text" inputMode="decimal">`.
 * Forwards refs, supports controlled `value` (string OR number) +
 * `onChange(stringValue)`, plus pass-through props (placeholder,
 * disabled, className, data-testid, autoFocus, onFocus, onBlur, …).
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { cn } from '@/lib/utils';

// Shadcn Input default classes — kept in sync with /components/ui/input.jsx
// so <CalcInput> is a true drop-in styled identically.
const SHADCN_INPUT_BASE =
  'flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-base shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 md:text-sm';

// ── Tiny safe evaluator (shunting-yard) ──────────────────────────────
//   Accepts:  digits, single dot per number, unary minus, + - * /
//   Rejects:  letters, parens, %, exponentiation, anything else
//   Returns:  number  OR  null if expression is malformed / divides by 0
const PREC = { '+': 1, '-': 1, '*': 2, '/': 2 };
function tokenize(s) {
  const out = [];
  let i = 0;
  let prev = null; // previous token (for unary minus disambiguation)
  while (i < s.length) {
    const ch = s[i];
    if (ch === ' ') { i += 1; continue; }
    if (ch === '+' || ch === '-' || ch === '*' || ch === '/') {
      // Treat leading '-' or '-' after another operator as unary
      const isUnary = (ch === '-' || ch === '+') &&
        (prev === null || (typeof prev === 'string' && '+-*/'.includes(prev)));
      if (isUnary && ch === '-') {
        // Read the number that follows as a negative literal
        let j = i + 1;
        let dot = false;
        while (j < s.length && (/[0-9]/.test(s[j]) || (!dot && s[j] === '.'))) {
          if (s[j] === '.') dot = true;
          j += 1;
        }
        if (j === i + 1) return null; // lone '-' with no number after
        out.push(parseFloat(s.slice(i, j)));
        prev = out[out.length - 1];
        i = j;
        continue;
      }
      if (isUnary && ch === '+') { i += 1; continue; } // skip unary '+'
      out.push(ch);
      prev = ch;
      i += 1;
      continue;
    }
    if (/[0-9.]/.test(ch)) {
      let j = i;
      let dot = false;
      while (j < s.length && (/[0-9]/.test(s[j]) || (!dot && s[j] === '.'))) {
        if (s[j] === '.') dot = true;
        j += 1;
      }
      if (j === i) return null;
      const num = parseFloat(s.slice(i, j));
      if (!Number.isFinite(num)) return null;
      out.push(num);
      prev = num;
      i = j;
      continue;
    }
    return null; // unknown character
  }
  return out;
}

function toRPN(tokens) {
  const out = [];
  const ops = [];
  for (const t of tokens) {
    if (typeof t === 'number') {
      out.push(t);
    } else {
      while (ops.length && PREC[ops[ops.length - 1]] >= PREC[t]) {
        out.push(ops.pop());
      }
      ops.push(t);
    }
  }
  while (ops.length) out.push(ops.pop());
  return out;
}

function evalRPN(rpn) {
  const st = [];
  for (const t of rpn) {
    if (typeof t === 'number') {
      st.push(t);
    } else {
      const b = st.pop();
      const a = st.pop();
      if (a === undefined || b === undefined) return null;
      let r;
      if (t === '+') r = a + b;
      else if (t === '-') r = a - b;
      else if (t === '*') r = a * b;
      else if (t === '/') {
        if (b === 0) return null;
        r = a / b;
      } else return null;
      if (!Number.isFinite(r)) return null;
      st.push(r);
    }
  }
  if (st.length !== 1) return null;
  return st[0];
}

/** Public helper — exported for tests. */
export function safeEval(expr) {
  if (expr === null || expr === undefined) return null;
  const s = String(expr).trim();
  if (!s) return null;
  // Allow trailing operator? No — caller should strip it before commit.
  if (/[+\-*/]\s*$/.test(s)) return null;
  const toks = tokenize(s);
  if (!toks || toks.length === 0) return null;
  // Validate alternation: number, op, number, op, ...
  for (let i = 0; i < toks.length; i += 1) {
    const expectNum = i % 2 === 0;
    const t = toks[i];
    if (expectNum && typeof t !== 'number') return null;
    if (!expectNum && typeof t !== 'string') return null;
  }
  return evalRPN(toRPN(toks));
}

// Round to 4 decimals to avoid float-noise like 0.1+0.2 = 0.30000000000000004
const _round = (n) => Math.round(n * 10000) / 10000;
const _fmt = (n) => {
  if (n === null || n === undefined || Number.isNaN(n)) return '';
  const r = _round(n);
  return Number.isInteger(r) ? String(r) : String(r);
};

const OP_KEYS = new Set(['+', '-', '*', '/']);

export const CalcInput = React.forwardRef(function CalcInput(
  {
    value,
    onChange,
    onBlur,
    onKeyDown,
    onFocus,
    className = '',
    style,
    placeholder,
    disabled,
    autoFocus,
    selectOnFocus = false,
    'data-testid': testId,
    ariaLabel,
    inputMode = 'decimal',
    // When true, commits the integer floor(running total). Used by qty fields.
    integerOnly = false,
    // Callbacks
    onCalcOpen,
    onCalcCommit,
    ...rest
  },
  ref
) {
  // ── state ──
  // exprMode = false → input acts as a plain numeric field, `value` is the
  // displayed value. When the user presses an operator key, we capture the
  // current numeric value as the seed of the formula and switch to expression
  // mode. Display switches to the expression string; the "tape" bubble is
  // shown beneath the field.
  const [exprMode, setExprMode] = useState(false);
  const [expr, setExpr] = useState('');
  // Snapshot of the field value at the moment we entered expression mode —
  // restored if the user cancels (Escape) or types an invalid expression.
  const snapshotRef = useRef('');
  const innerRef = useRef(null);
  // Forwarded ref support (RHF, parent measurement, etc.)
  React.useImperativeHandle(ref, () => innerRef.current, []);

  // Running total preview — null when expression isn't yet a complete,
  // valid formula (e.g. ends in an operator).
  const runningTotal = exprMode ? safeEval(expr) : null;

  // Reset expression mode when external `value` changes (e.g. parent reset
  // form, or programmatic price update). Without this, the bubble could be
  // left dangling.
  useEffect(() => {
    if (!exprMode) return;
    // No-op: we track expression locally; parent value updates after commit.
    // (Don't clobber the in-progress expression just because parent re-renders.)
  }, [value, exprMode]);

  const commitNumber = useCallback((num) => {
    const final = num === null || num === undefined || Number.isNaN(num)
      ? null
      : (integerOnly ? Math.floor(num) : _round(num));
    if (final === null) {
      // Invalid → revert silently
      setExpr('');
      setExprMode(false);
      return;
    }
    const out = _fmt(final);
    setExpr('');
    setExprMode(false);
    onChange?.(out);
    onCalcCommit?.(final, out);
  }, [integerOnly, onChange, onCalcCommit]);

  const cancel = useCallback(() => {
    setExpr('');
    setExprMode(false);
  }, []);

  const handleKeyDown = (e) => {
    const k = e.key;
    // ── Plain mode ──
    if (!exprMode) {
      if (OP_KEYS.has(k)) {
        // Snapshot current value, switch to expression mode.
        const seed = (value === null || value === undefined ? '' : String(value)).trim();
        // If field is empty, allow the operator only if it's '-' (negative literal)
        if (!seed && k !== '-') {
          // Ignore — operator without a left operand
          e.preventDefault();
          return;
        }
        snapshotRef.current = seed;
        const newExpr = (seed || '') + k;
        setExpr(newExpr);
        setExprMode(true);
        onCalcOpen?.();
        e.preventDefault();
        return;
      }
      // pass-through
      onKeyDown?.(e);
      return;
    }

    // ── Expression mode ──
    if (k === 'Enter') {
      e.preventDefault();
      // Strip trailing operator (so "20+" with Enter behaves the same as "20")
      let s = expr.replace(/[+\-*/]\s*$/, '');
      const r = safeEval(s);
      commitNumber(r);
      return;
    }
    if (k === 'Escape') {
      e.preventDefault();
      cancel();
      return;
    }
    if (k === 'Backspace') {
      e.preventDefault();
      const next = expr.slice(0, -1);
      if (!next) { cancel(); return; }
      setExpr(next);
      return;
    }
    if (OP_KEYS.has(k) || /^[0-9.]$/.test(k)) {
      e.preventDefault();
      // Block double-operator (e.g. "20++") — replace last operator instead.
      let next = expr;
      if (OP_KEYS.has(k) && /[+\-*/]\s*$/.test(next)) {
        next = next.replace(/[+\-*/]\s*$/, k);
      } else if (k === '.' && /\.[0-9]*$/.test(next.split(/[+\-*/]/).pop() || '')) {
        // Block second decimal in same number
        return;
      } else {
        next += k;
      }
      setExpr(next);
      return;
    }
    if (k === 'Tab') {
      // Let blur handler commit
      return;
    }
    // Block any other keys silently
    e.preventDefault();
  };

  const handleBlur = (e) => {
    if (exprMode) {
      const s = expr.replace(/[+\-*/]\s*$/, '');
      const r = safeEval(s);
      commitNumber(r); // null handled inside (silent revert)
    }
    onBlur?.(e);
  };

  const handleChange = (e) => {
    if (exprMode) {
      // In expr mode, key handler manages the buffer — onChange shouldn't fire.
      // (Some browsers still call onChange when text is pasted; ignore.)
      return;
    }
    onChange?.(e.target.value);
  };

  const handleFocus = (e) => {
    if (selectOnFocus) {
      try { e.target.select(); } catch { /* noop */ }
    }
    onFocus?.(e);
  };

  // What the input shows: the live expression while in calc mode, else
  // the parent-controlled value.
  const displayValue = exprMode
    ? expr
    : (value === null || value === undefined ? '' : String(value));

  // ── Bubble UI (calc tape) ──
  // Positioned absolutely below the input. Wraps in a relative container
  // so callers don't need extra layout work.
  return (
    <span className={`calc-input-wrap ${exprMode ? 'is-active' : ''}`} style={{ position: 'relative', display: 'inline-block', width: '100%' }}>
      <input
        ref={innerRef}
        type="text"
        inputMode={inputMode}
        autoComplete="off"
        spellCheck={false}
        value={displayValue}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        onBlur={handleBlur}
        onFocus={handleFocus}
        className={cn(SHADCN_INPUT_BASE, className)}
        style={style}
        placeholder={placeholder}
        disabled={disabled}
        autoFocus={autoFocus}
        aria-label={ariaLabel}
        data-testid={testId}
        data-calc-active={exprMode ? '1' : '0'}
        {...rest}
      />
      {exprMode && (
        <span
          role="status"
          aria-live="polite"
          data-testid={testId ? `${testId}-tape` : undefined}
          style={{
            position: 'absolute',
            top: 'calc(100% + 4px)',
            left: 0,
            zIndex: 50,
            background: '#0f172a',
            color: '#e2e8f0',
            border: '1px solid #1e293b',
            borderRadius: 8,
            padding: '6px 10px',
            fontSize: 12,
            fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
            whiteSpace: 'nowrap',
            boxShadow: '0 8px 24px rgba(0,0,0,.18)',
            pointerEvents: 'none',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
          }}
        >
          <span style={{ opacity: 0.85 }}>{expr || '—'}</span>
          <span style={{ opacity: 0.5 }}>=</span>
          <span style={{ color: runningTotal === null ? '#fbbf24' : '#34d399', fontWeight: 600 }}>
            {runningTotal === null ? '…' : _fmt(runningTotal)}
          </span>
          <span style={{ opacity: 0.45, marginLeft: 4 }}>↵ commit · Esc cancel</span>
        </span>
      )}
    </span>
  );
});

export default CalcInput;
