# compile-pdf-rewrite

Object-tree mutations for CompilePDF: OCG, metadata, colour-space, hygiene, lifecycle.

Fifteen mutations across structural, hygiene, and lifecycle categories. OCG flips, page lifecycle ops, page-box patches, metadata set/strip, colour-space swap, JavaScript strip, PDF/X pin. Three-layer verifier: schema, determinism, nothing-else-touched.

## Install

```bash
uv pip install compile-pdf-rewrite
```

## Position in the stack

One of four [CompilePDF](https://compilepdf.com) producers (trap, impose, marks, rewrite). Each lives in its own repo and PyPI package so you install only what you need. Producers depend on `compile-pdf-core`, never on each other.

- Repo: https://github.com/printwithsynergy/compile-pdf-rewrite
- Deployment host: https://github.com/printwithsynergy/compile-pdf
- License: AGPL-3.0-or-later
