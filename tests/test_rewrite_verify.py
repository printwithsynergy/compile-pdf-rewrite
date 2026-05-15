"""Three-layer post-condition verifier tests."""

from __future__ import annotations

import io

import pikepdf

from compile_pdf_rewrite.engine import apply_plan
from compile_pdf_rewrite.plan_schema import RewritePlan
from compile_pdf_rewrite.verify import verify_rewrite


def _plan(ops: list[dict[str, object]]) -> RewritePlan:
    return RewritePlan.model_validate({"ops": ops})


def test_verify_passes_clean_run(simple_pdf: bytes) -> None:
    plan = _plan([{"op": "metadata_set", "key": "Title", "value": "X"}])
    result = apply_plan(simple_pdf, plan)
    check = verify_rewrite(input_bytes=simple_pdf, output_bytes=result.output_bytes, plan=plan)
    assert check.passed, check.failures


def test_verify_l1_catches_non_observable_metadata(simple_pdf: bytes) -> None:
    """If the output's Title doesn't match the plan, L1 must flag it."""
    plan = _plan([{"op": "metadata_set", "key": "Title", "value": "actually wanted"}])
    result = apply_plan(simple_pdf, plan)
    pdf = pikepdf.open(io.BytesIO(result.output_bytes))
    pdf.docinfo[pikepdf.Name.Title] = pikepdf.String("tampered")
    buf = io.BytesIO()
    pdf.save(buf, deterministic_id=True)
    pdf.close()
    tampered_output = buf.getvalue()
    check = verify_rewrite(
        input_bytes=simple_pdf,
        output_bytes=tampered_output,
        plan=plan,
        determinism_replay=False,
    )
    assert not check.layer1_schema
    assert any("metadata_set" in f for f in check.failures)


def test_verify_l3_catches_unintended_metadata_change(simple_pdf: bytes) -> None:
    """The plan only changes Title; if Author drifted, L3 must flag it."""
    plan = _plan([{"op": "metadata_set", "key": "Title", "value": "X"}])
    result = apply_plan(simple_pdf, plan)
    pdf = pikepdf.open(io.BytesIO(result.output_bytes))
    pdf.docinfo[pikepdf.Name.Author] = pikepdf.String("snuck in")
    buf = io.BytesIO()
    pdf.save(buf, deterministic_id=True)
    pdf.close()
    tampered_output = buf.getvalue()
    check = verify_rewrite(
        input_bytes=simple_pdf,
        output_bytes=tampered_output,
        plan=plan,
        determinism_replay=False,
    )
    assert not check.layer3_unchanged
    assert any("Author" in f for f in check.failures)


def test_verify_l3_page_count_after_insert(three_page_pdf: bytes) -> None:
    plan = _plan([{"op": "page_insert", "at_index": 1, "width_pt": 612, "height_pt": 792}])
    result = apply_plan(three_page_pdf, plan)
    check = verify_rewrite(input_bytes=three_page_pdf, output_bytes=result.output_bytes, plan=plan)
    assert check.passed, check.failures


def test_verify_l3_page_count_after_delete(three_page_pdf: bytes) -> None:
    plan = _plan([{"op": "page_delete", "at_index": 1}])
    result = apply_plan(three_page_pdf, plan)
    check = verify_rewrite(input_bytes=three_page_pdf, output_bytes=result.output_bytes, plan=plan)
    assert check.passed, check.failures


def test_verify_l2_replay_disabled_short_circuits(simple_pdf: bytes) -> None:
    plan = _plan([{"op": "metadata_set", "key": "Title", "value": "Y"}])
    result = apply_plan(simple_pdf, plan)
    check = verify_rewrite(
        input_bytes=simple_pdf,
        output_bytes=result.output_bytes,
        plan=plan,
        determinism_replay=False,
    )
    assert check.layer2_determinism is True


def test_verify_l1_catches_missing_pdfx(simple_pdf: bytes) -> None:
    plan = _plan([{"op": "pdfx_pin", "level": "PDF/X-4"}])
    result = apply_plan(simple_pdf, plan)
    pdf = pikepdf.open(io.BytesIO(result.output_bytes))
    del pdf.Root[pikepdf.Name.OutputIntents]
    buf = io.BytesIO()
    pdf.save(buf, deterministic_id=True)
    pdf.close()
    check = verify_rewrite(
        input_bytes=simple_pdf,
        output_bytes=buf.getvalue(),
        plan=plan,
        determinism_replay=False,
    )
    assert not check.layer1_schema
    assert any("pdfx_pin" in f for f in check.failures)
