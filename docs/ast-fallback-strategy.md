# AST Fallback Strategy (Non-Tree-Sitter Path)

## Context

Covers what happens when tree-sitter **cannot help** — either because no grammar
exists for the language, or tree-sitter is opted out entirely.

Grammar-available-but-not-installed is a separate concern — that's grammar
resolution, handled in `ast-tree-sitter-integration.md` (scenario 2).

**Parent plan:** `ast-code-splitting.md`
**Depends on:** `ast-tree-sitter-integration.md` (steps 1-4) being done first
**Related bug:** Current regex fallback is Python-only (`^\s*#`, `"""..."""`,
`def`/`class` at column 0). For non-Python code, regex compression is effectively
a no-op.

---

## Scenarios

### 1. No grammar exists at all

Language identified by magika/extension but no tree-sitter grammar on PyPI.
Examples: Zig, Elixir, Haskell (may have grammars but not as `tree-sitter-*`
PyPI packages).

### 2. Tree-sitter opted out

User explicitly disables tree-sitter (e.g., `ast` extras not installed).
All code goes through the non-AST path.

---

## Current degradation chain (broken)

```
full AST (tree-sitter)
  → Python-only regex → nearly useless for non-Python
```

## Proposed degradation chain

```
full AST (tree-sitter)
  → generic regex — catches common comment/signature patterns across languages
    → blank-line splitting — last structural heuristic
      → keep whole + flag for review
```

### Tier: Generic regex patterns

Language-agnostic heuristics:
- Comments: `//`, `/* */`, `#`, `--`, `"""`
- Signatures: `def `, `fn `, `func `, `function `, `class `, `struct `, `impl `
- Split on blank lines between top-level blocks
- Not precise, but better than nothing for ~80% of languages

### Tier: Blank-line splitting

Split on double blank lines. Crude but universal. Most codebases use blank lines
between functions/classes.

### Tier: Keep whole + flag

Don't split. Run compression if possible. Flag `requires_human_review` for large files.

---

## Open decisions

1. **Generic regex tier** — worth building, or skip straight to blank-line?
2. **Logging** — what level? Warning per-file? Summary at end of pipeline run?

---

## Status: NOT STARTED

Blocked on tree-sitter integration (steps 1-4) being complete.
