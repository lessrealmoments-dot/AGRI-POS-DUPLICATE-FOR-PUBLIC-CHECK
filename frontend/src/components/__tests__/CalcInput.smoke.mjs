/* eslint-disable no-undef */
/**
 * Unit smoke for CalcInput.safeEval — covers the QuickBooks-style cases.
 * Run: node /app/frontend/src/components/__tests__/CalcInput.smoke.mjs
 */
import { safeEval } from '../CalcInput.jsx';

const cases = [
  ['20', 20],
  ['20+5', 25],
  ['20-5', 15],
  ['20*3', 60],
  ['100/4', 25],
  ['1500-10', 1490],
  ['1.5+2.5', 4],
  ['10+5*2', 20],          // precedence
  ['100-25*2', 50],        // precedence
  ['1500/0', null],        // div by zero
  ['20+', null],           // trailing op (safeEval rejects; commit strips it)
  ['abc', null],           // garbage
  ['', null],              // empty
  ['-5+10', 5],            // unary minus
  ['10--5', 15],           // op then unary minus
  ['1.2.3', null],         // double decimal
  ['(1+2)', null],         // parens NOT allowed
  ['2**3', null],          // exponent NOT allowed
  ['100%', null],          // percent NOT allowed
];

let pass = 0, fail = 0;
for (const [expr, expected] of cases) {
  const got = safeEval(expr);
  const ok = (got === expected) ||
             (typeof got === 'number' && typeof expected === 'number' && Math.abs(got - expected) < 1e-9);
  if (ok) { pass += 1; } else { fail += 1; console.error(`FAIL: safeEval(${JSON.stringify(expr)}) = ${got}, expected ${expected}`); }
}
console.log(`${pass}/${pass + fail} passed`);
process.exit(fail ? 1 : 0);
