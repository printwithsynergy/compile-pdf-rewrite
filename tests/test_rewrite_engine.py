"""Engine behavior tests — one test per mutation, plus error paths."""

from __future__ import annotations

import io

import pikepdf
import pytest

from compile_pdf_rewrite.engine import RewritePlanError, apply_plan
from compile_pdf_rewrite.plan_schema import RewritePlan


def _open(blob: bytes) -> pikepdf.Pdf:
    return pikepdf.open(io.BytesIO(blob))


def _run(input_bytes: bytes, ops: list[dict[str, object]]) -> bytes:
    plan = RewritePlan.model_validate({"ops": ops})
    return apply_plan(input_bytes, plan).output_bytes


# --- Structural ----------------------------------------------------------


def test_ocg_flip_off(ocg_pdf: bytes) -> None:
    out = _run(ocg_pdf, [{"op": "ocg_flip", "layer": "Bleed", "visible": False}])
    pdf = _open(out)
    try:
        d = pdf.Root.OCProperties.D
        on_arr = d.get(pikepdf.Name.ON)
        off_arr = d.get(pikepdf.Name.OFF)
        assert isinstance(on_arr, pikepdf.Array)
        assert isinstance(off_arr, pikepdf.Array)
        assert len(on_arr) == 0
        assert len(off_arr) == 1
    finally:
        pdf.close()


def test_ocg_flip_unknown_layer_raises(ocg_pdf: bytes) -> None:
    with pytest.raises(RewritePlanError, match="OCG layer not found"):
        _run(ocg_pdf, [{"op": "ocg_flip", "layer": "NoSuch", "visible": True}])


def test_page_insert(simple_pdf: bytes) -> None:
    out = _run(
        simple_pdf,
        [{"op": "page_insert", "at_index": 1, "width_pt": 595, "height_pt": 842}],
    )
    pdf = _open(out)
    try:
        assert len(pdf.pages) == 2
    finally:
        pdf.close()


def test_page_insert_at_end_appends(simple_pdf: bytes) -> None:
    out = _run(
        simple_pdf,
        [{"op": "page_insert", "at_index": 1, "width_pt": 595, "height_pt": 842}],
    )
    pdf = _open(out)
    try:
        # New page at the end has the requested size.
        media = pdf.pages[1].obj.get(pikepdf.Name.MediaBox)
        assert isinstance(media, pikepdf.Array)
        assert float(media[2]) == pytest.approx(595)
        assert float(media[3]) == pytest.approx(842)
    finally:
        pdf.close()


def test_page_insert_index_out_of_range(simple_pdf: bytes) -> None:
    with pytest.raises(RewritePlanError):
        _run(simple_pdf, [{"op": "page_insert", "at_index": 99, "width_pt": 1, "height_pt": 1}])


def test_page_delete(three_page_pdf: bytes) -> None:
    out = _run(three_page_pdf, [{"op": "page_delete", "at_index": 1}])
    pdf = _open(out)
    try:
        assert len(pdf.pages) == 2
    finally:
        pdf.close()


def test_page_delete_oob_raises(simple_pdf: bytes) -> None:
    with pytest.raises(RewritePlanError, match="page_delete"):
        _run(simple_pdf, [{"op": "page_delete", "at_index": 99}])


def test_page_delete_last_page_raises(simple_pdf: bytes) -> None:
    with pytest.raises(RewritePlanError, match="only page"):
        _run(simple_pdf, [{"op": "page_delete", "at_index": 0}])


def test_page_reorder(three_page_pdf: bytes) -> None:
    out = _run(three_page_pdf, [{"op": "page_reorder", "order": [2, 0, 1]}])
    pdf = _open(out)
    try:
        assert len(pdf.pages) == 3
    finally:
        pdf.close()


def test_page_reorder_wrong_length_raises(three_page_pdf: bytes) -> None:
    with pytest.raises(RewritePlanError, match="length"):
        _run(three_page_pdf, [{"op": "page_reorder", "order": [0, 1]}])


def test_page_reorder_not_a_permutation_raises(three_page_pdf: bytes) -> None:
    with pytest.raises(RewritePlanError, match="permutation"):
        _run(three_page_pdf, [{"op": "page_reorder", "order": [0, 0, 0]}])


def test_page_rotate(simple_pdf: bytes) -> None:
    out = _run(simple_pdf, [{"op": "page_rotate", "at_index": 0, "degrees": 270}])
    pdf = _open(out)
    try:
        rot = pdf.pages[0].obj.get(pikepdf.Name.Rotate)
        assert int(rot) == 270
    finally:
        pdf.close()


def test_box_set(simple_pdf: bytes) -> None:
    out = _run(
        simple_pdf,
        [{"op": "box_set", "at_index": 0, "box": "BleedBox", "rect_pt": [-9, -9, 621, 801]}],
    )
    pdf = _open(out)
    try:
        bleed = pdf.pages[0].obj.get(pikepdf.Name.BleedBox)
        assert isinstance(bleed, pikepdf.Array)
        assert [float(v) for v in (bleed[0], bleed[1], bleed[2], bleed[3])] == [-9, -9, 621, 801]
    finally:
        pdf.close()


def test_box_set_invalid_rect_raises(simple_pdf: bytes) -> None:
    with pytest.raises(RewritePlanError, match="llx<urx"):
        _run(
            simple_pdf,
            [{"op": "box_set", "at_index": 0, "box": "TrimBox", "rect_pt": [10, 10, 5, 5]}],
        )


def test_page_label_set(simple_pdf: bytes) -> None:
    out = _run(
        simple_pdf,
        [{"op": "page_label_set", "start_index": 0, "style": "R", "prefix": "App-", "start": 1}],
    )
    pdf = _open(out)
    try:
        labels = pdf.Root.get(pikepdf.Name.PageLabels)
        assert isinstance(labels, pikepdf.Dictionary)
    finally:
        pdf.close()


def test_normalize_page_tree(three_page_pdf: bytes) -> None:
    out = _run(three_page_pdf, [{"op": "normalize_page_tree"}])
    pdf = _open(out)
    try:
        assert len(pdf.pages) == 3
    finally:
        pdf.close()


# --- Hygiene -------------------------------------------------------------


def test_metadata_set_updates_info_dict(simple_pdf: bytes) -> None:
    out = _run(simple_pdf, [{"op": "metadata_set", "key": "Title", "value": "Mutated"}])
    pdf = _open(out)
    try:
        assert str(pdf.docinfo[pikepdf.Name.Title]) == "Mutated"
    finally:
        pdf.close()


def test_metadata_strip(simple_pdf: bytes) -> None:
    out = _run(simple_pdf, [{"op": "metadata_strip", "keys": ["Title"]}])
    pdf = _open(out)
    try:
        assert pikepdf.Name.Title not in pdf.docinfo
    finally:
        pdf.close()


def test_colorspace_swap_no_op_on_clean_doc(simple_pdf: bytes) -> None:
    # Document has no colorspace dict; the op should be a no-op (not an error).
    out = _run(simple_pdf, [{"op": "colorspace_swap", "target": "cmyk"}])
    pdf = _open(out)
    try:
        assert len(pdf.pages) == 1
    finally:
        pdf.close()


def test_strip_javascript(js_pdf: bytes) -> None:
    out = _run(js_pdf, [{"op": "strip_javascript"}])
    pdf = _open(out)
    try:
        names = pdf.Root.get(pikepdf.Name.Names)
        if isinstance(names, pikepdf.Dictionary):
            assert pikepdf.Name.JavaScript not in names
    finally:
        pdf.close()


def test_strip_embedded_files(embedded_files_pdf: bytes) -> None:
    out = _run(embedded_files_pdf, [{"op": "strip_embedded_files"}])
    pdf = _open(out)
    try:
        names = pdf.Root.get(pikepdf.Name.Names)
        if isinstance(names, pikepdf.Dictionary):
            assert pikepdf.Name.EmbeddedFiles not in names
    finally:
        pdf.close()


# --- Lifecycle -----------------------------------------------------------


def test_pdfx_pin_writes_output_intent(simple_pdf: bytes) -> None:
    out = _run(simple_pdf, [{"op": "pdfx_pin", "level": "PDF/X-4"}])
    pdf = _open(out)
    try:
        intents = pdf.Root.get(pikepdf.Name.OutputIntents)
        assert isinstance(intents, pikepdf.Array)
        assert len(intents) == 1
        intent = intents[0]
        assert isinstance(intent, pikepdf.Dictionary)
        assert intent.get(pikepdf.Name.S) == pikepdf.Name.GTS_PDFX
    finally:
        pdf.close()


def test_pdfx_pin_none_clears(simple_pdf: bytes) -> None:
    # First pin to PDF/X-4, then clear.
    out = _run(
        simple_pdf,
        [
            {"op": "pdfx_pin", "level": "PDF/X-4"},
            {"op": "pdfx_pin", "level": "none"},
        ],
    )
    pdf = _open(out)
    try:
        assert pikepdf.Name.OutputIntents not in pdf.Root
    finally:
        pdf.close()


def test_producer_creator_stamp(simple_pdf: bytes) -> None:
    out = _run(
        simple_pdf,
        [{"op": "producer_creator_stamp", "producer": "compile-pdf 0.1.0", "creator": "PWS"}],
    )
    pdf = _open(out)
    try:
        assert str(pdf.docinfo[pikepdf.Name.Producer]) == "compile-pdf 0.1.0"
        assert str(pdf.docinfo[pikepdf.Name.Creator]) == "PWS"
    finally:
        pdf.close()


# --- Multi-op composition ------------------------------------------------


def test_multiple_ops_apply_in_order(three_page_pdf: bytes) -> None:
    out = _run(
        three_page_pdf,
        [
            {"op": "page_reorder", "order": [2, 0, 1]},
            {"op": "metadata_set", "key": "Title", "value": "after reorder"},
            {"op": "page_rotate", "at_index": 0, "degrees": 90},
        ],
    )
    pdf = _open(out)
    try:
        assert len(pdf.pages) == 3
        assert str(pdf.docinfo[pikepdf.Name.Title]) == "after reorder"
        assert int(pdf.pages[0].obj.get(pikepdf.Name.Rotate)) == 90
    finally:
        pdf.close()


def test_apply_plan_rejects_empty_input() -> None:
    plan = RewritePlan.model_validate({"ops": []})
    with pytest.raises(ValueError, match="non-empty"):
        apply_plan(b"", plan)


def test_empty_plan_round_trips(simple_pdf: bytes) -> None:
    """An empty plan should still produce a valid (deterministic) output."""
    plan = RewritePlan.model_validate({"ops": []})
    result = apply_plan(simple_pdf, plan)
    assert result.ops_applied == 0
    pdf = _open(result.output_bytes)
    try:
        assert len(pdf.pages) == 1
    finally:
        pdf.close()
