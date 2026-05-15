"""Rewrite producer — single-PDF-in/out object-tree mutations.

Per spec §2.1 — 15 in-scope mutations across structural / hygiene /
lifecycle / page-level categories. **No content-stream surgery** (font
subsetting, image recompression, color reflow are out of scope and
gated by a STOP-gate).

Module structure (lands in Phase 1.x):

- ``compile_pdf.rewrite.engine`` — pikepdf-driven mutator
- ``compile_pdf.rewrite.plan_schema`` — JSON Schema validator
- ``compile_pdf.rewrite.verify`` — three-layer post-condition checks (§2.3)
- ``compile_pdf.rewrite.api`` — ``router`` exposing /v1/rewrite/apply
- ``compile_pdf.rewrite.cli`` — ``register(group)`` for the top-level CLI

Codex surface consumed (read-only context for plan validation):

- :class:`codex_pdf.CodexDocument` — the document model rewrite plans
  reference for page-index / OCG / metadata addressing.
"""

from __future__ import annotations

from codex_pdf import CodexDocument

from compile_pdf_core.version import REWRITE_SCHEMA_VERSION

__all__ = ["CodexDocument", "REWRITE_SCHEMA_VERSION"]
