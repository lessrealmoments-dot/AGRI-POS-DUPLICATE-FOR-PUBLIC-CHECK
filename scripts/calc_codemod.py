#!/usr/bin/env python3
"""
Codemod: replace <Input type="number" …> / <input type="text" inputMode="decimal" …>
with <CalcInput …>.

Strategy
--------
* Conservative — only matches inputs that are CLEARLY numeric.
* Uses regex with backtracking guards. Skips inputs that already use CalcInput.
* Drops attrs that CalcInput handles internally:
    type, inputMode, min, max, step, onWheel
* Rewrites:
    onChange={e => …e.target.value…}   →  onChange={(v) => …v…}
    onFocus={e => e.target.select()}    →  selectOnFocus  (separate prop)
* Leaves everything else untouched (className, value, data-testid, autoFocus,
  placeholder, disabled, ref, etc.)
* Inserts `import CalcInput from '<rel>'` near the top if missing.

Files are mutated in-place. Run from /app:
    python3 scripts/calc_codemod.py file1 file2 …
"""
import re
import sys
import os
from pathlib import Path

# ── Helpers ──────────────────────────────────────────────────────────────
def import_path(file_path: Path) -> str:
    """Compute relative import path to /app/frontend/src/components/CalcInput."""
    target = Path("/app/frontend/src/components/CalcInput.jsx")
    rel = os.path.relpath(target, file_path.parent)
    rel = rel.replace(".jsx", "").replace(".js", "")
    if not rel.startswith("."):
        rel = "./" + rel
    return rel.replace(os.sep, "/")


def ensure_import(src: str, rel: str) -> str:
    if "from '" + rel + "'" in src or 'from "' + rel + '"' in src:
        return src
    if re.search(r"import\s+CalcInput\b", src):
        return src
    # Insert after the last existing import statement (handles multi-line
    # `import { ... } from '...'` blocks correctly).
    lines = src.split("\n")
    in_multiline = False
    last_import_end = -1
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not in_multiline and re.match(r"^import\s+", s):
            if s.rstrip().endswith(";") or "from " in s:
                last_import_end = i
            else:
                in_multiline = True
        elif in_multiline:
            if "}" in s and "from" in s:
                last_import_end = i
                in_multiline = False
            elif s.rstrip().endswith(";"):
                last_import_end = i
                in_multiline = False
    if last_import_end == -1:
        return f"import CalcInput from '{rel}';\n" + src
    lines.insert(last_import_end + 1, f"import CalcInput from '{rel}';")
    return "\n".join(lines)


# ── Attribute transformations (operates on the inner attrs string) ──────
ATTR_DROP_RE = [
    re.compile(r"\btype\s*=\s*\"(?:number|text)\"\s*"),
    re.compile(r"\binputMode\s*=\s*\"(?:decimal|numeric)\"\s*"),
    re.compile(r"\bmin\s*=\s*(?:\"[^\"]*\"|\{[^}]*\})\s*"),
    re.compile(r"\bmax\s*=\s*(?:\"[^\"]*\"|\{[^}]*\})\s*"),
    re.compile(r"\bstep\s*=\s*(?:\"[^\"]*\"|\{[^}]*\})\s*"),
    re.compile(r"\bonWheel\s*=\s*\{[^}]*\}\s*"),
]


def rewrite_onchange(attrs: str) -> str:
    """onChange={e => foo(e.target.value, ...)}  →  onChange={(__cv) => foo(__cv, ...)}.
    Uses an obscure parameter name (__cv) to avoid collisions with inner
    `const v` / `let v` declarations in handler bodies.
    """
    def repl(m):
        body = m.group(1)
        am = re.match(r"\s*\(?\s*(\w+)\s*\)?\s*=>\s*(.*)", body, re.DOTALL)
        if not am:
            return m.group(0)
        var, rest = am.group(1), am.group(2)
        if f"{var}.target.value" not in rest:
            return m.group(0)
        new_rest = re.sub(rf"\b{re.escape(var)}\.target\.value\b", "__cv", rest)
        return f"onChange={{(__cv) => {new_rest}}}"

    return re.sub(
        r"onChange=\{((?:[^{}]|\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\})*)\}",
        repl, attrs, flags=re.DOTALL,
    )


def rewrite_onfocus(attrs: str) -> str:
    """onFocus={e => e.target.select()}  →  selectOnFocus.
    Removes the original prop and appends `selectOnFocus`.
    """
    pat = re.compile(
        r"onFocus\s*=\s*\{\s*\(?(\w+)\)?\s*=>\s*\1\.target\.select\(\)\s*;?\s*\}\s*",
        re.DOTALL,
    )
    if pat.search(attrs):
        attrs = pat.sub("", attrs)
        # Append selectOnFocus prop (will be normalised by JSX formatter)
        attrs = attrs.rstrip() + " selectOnFocus "
    return attrs


def transform_attrs(attrs: str) -> str:
    for r in ATTR_DROP_RE:
        attrs = r.sub("", attrs)
    attrs = rewrite_onchange(attrs)
    attrs = rewrite_onfocus(attrs)
    # Collapse triple+ spaces / dangling whitespace before tag close
    attrs = re.sub(r"[ \t]+", " ", attrs).strip()
    return attrs


# ── Top-level element transformer ────────────────────────────────────────
# We match either:
#   <Input type="number" …/>          (self-closing)
#   <input type="text" inputMode="decimal" …/>
# And ONLY when type is `number` or (type="text" AND inputMode is decimal/numeric).
#
# We capture EVERYTHING between the tag name and `/>` so attributes can be
# transformed.

ELEMENT_RE = re.compile(
    r"""<(?P<tag>Input|input)
        (?P<pre>(?:\s+(?:[a-zA-Z_:][\w:.\-]*)\s*=\s*(?:"[^"]*"|\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\})|\s+(?:[a-zA-Z_:][\w:.\-]*)(?=\s|/>|$))*?)
        \s*/>""",
    re.VERBOSE | re.DOTALL,
)


def is_numeric_input(attrs: str) -> bool:
    """Decide whether this <Input>/<input> is numeric based on attrs."""
    has_number = re.search(r'\btype\s*=\s*"number"', attrs)
    has_text = re.search(r'\btype\s*=\s*"text"', attrs)
    has_decimal = re.search(r'\binputMode\s*=\s*"(?:decimal|numeric)"', attrs)
    if has_number:
        return True
    if has_text and has_decimal:
        return True
    return False


def transform_file(path: Path) -> int:
    src = path.read_text()
    if "<Input" not in src and "<input" not in src:
        return 0

    changes = 0

    def repl(m):
        nonlocal changes
        attrs = m.group("pre")
        if not is_numeric_input(attrs):
            return m.group(0)
        new_attrs = transform_attrs(attrs)
        changes += 1
        return f"<CalcInput {new_attrs} />"

    new_src = ELEMENT_RE.sub(repl, src)

    if changes > 0:
        rel = import_path(path)
        new_src = ensure_import(new_src, rel)
        path.write_text(new_src)
    return changes


# ── Entry point ──────────────────────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        print(f"usage: {sys.argv[0]} file1 [file2 …]")
        sys.exit(1)
    total = 0
    for arg in sys.argv[1:]:
        p = Path(arg)
        if not p.exists():
            print(f"  SKIP (missing): {arg}")
            continue
        n = transform_file(p)
        if n:
            print(f"  ✓ {n:3d} change(s)  {arg}")
            total += n
        else:
            print(f"  -   no match    {arg}")
    print(f"\nTotal replacements: {total}")


if __name__ == "__main__":
    main()
