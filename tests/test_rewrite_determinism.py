"""Determinism guarantees: same input + same plan → same SHA-256 output."""

from __future__ import annotations

from compile_pdf_rewrite.engine import apply_plan
from compile_pdf_rewrite.plan_schema import RewritePlan


def test_three_runs_byte_identical(three_page_pdf: bytes) -> None:
    plan = RewritePlan.model_validate(
        {
            "ops": [
                {"op": "page_reorder", "order": [2, 0, 1]},
                {"op": "metadata_set", "key": "Title", "value": "deterministic"},
                {"op": "page_rotate", "at_index": 0, "degrees": 180},
                {"op": "pdfx_pin", "level": "PDF/X-4"},
                {"op": "producer_creator_stamp", "creator": "compile-pdf"},
                {"op": "normalize_page_tree"},
            ]
        }
    )
    r1 = apply_plan(three_page_pdf, plan)
    r2 = apply_plan(three_page_pdf, plan)
    r3 = apply_plan(three_page_pdf, plan)
    assert r1.output_bytes == r2.output_bytes == r3.output_bytes
    assert r1.pdf_sha256 == r2.pdf_sha256 == r3.pdf_sha256


def test_different_plans_produce_different_outputs(simple_pdf: bytes) -> None:
    plan_a = RewritePlan.model_validate(
        {"ops": [{"op": "metadata_set", "key": "Title", "value": "A"}]}
    )
    plan_b = RewritePlan.model_validate(
        {"ops": [{"op": "metadata_set", "key": "Title", "value": "B"}]}
    )
    a = apply_plan(simple_pdf, plan_a)
    b = apply_plan(simple_pdf, plan_b)
    assert a.pdf_sha256 != b.pdf_sha256


def test_op_order_affects_output(simple_pdf: bytes) -> None:
    """Set-then-strip differs from strip-then-set semantically."""
    set_then_strip = RewritePlan.model_validate(
        {
            "ops": [
                {"op": "metadata_set", "key": "Title", "value": "X"},
                {"op": "metadata_strip", "keys": ["Title"]},
            ]
        }
    )
    strip_then_set = RewritePlan.model_validate(
        {
            "ops": [
                {"op": "metadata_strip", "keys": ["Title"]},
                {"op": "metadata_set", "key": "Title", "value": "X"},
            ]
        }
    )
    a = apply_plan(simple_pdf, set_then_strip)
    b = apply_plan(simple_pdf, strip_then_set)
    assert a.pdf_sha256 != b.pdf_sha256
