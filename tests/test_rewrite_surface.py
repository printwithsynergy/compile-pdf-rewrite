"""Surface test: rewrite producer must re-export the codex symbols it
consumes, so downstream engine code can rely on a stable producer-side
import path even when codex moves things internally.
"""

from __future__ import annotations


def test_rewrite_module_reexports_codex_document() -> None:
    from compile_pdf import rewrite

    assert rewrite.CodexDocument is not None
    assert "CodexDocument" in rewrite.__all__
    assert "REWRITE_SCHEMA_VERSION" in rewrite.__all__


def test_rewrite_codex_document_matches_canonical_import() -> None:
    from codex_pdf import CodexDocument as CanonicalCodexDocument

    from compile_pdf.rewrite import CodexDocument

    assert CodexDocument is CanonicalCodexDocument
