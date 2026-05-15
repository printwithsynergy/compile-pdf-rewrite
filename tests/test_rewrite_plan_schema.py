"""Plan-schema validation tests — all 15 ops + reject paths."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from compile_pdf_rewrite.plan_schema import RewritePlan, rewrite_plan_json_schema


def _validate(ops: list[dict[str, object]]) -> RewritePlan:
    return RewritePlan.model_validate({"schema_version": "1.0.0", "ops": ops})


def test_all_15_op_types_validate() -> None:
    plan = _validate(
        [
            {"op": "ocg_flip", "layer": "Bleed", "visible": False},
            {"op": "page_insert", "at_index": 0, "width_pt": 612, "height_pt": 792},
            {"op": "page_delete", "at_index": 0},
            {"op": "page_reorder", "order": [2, 0, 1]},
            {"op": "page_rotate", "at_index": 0, "degrees": 90},
            {"op": "box_set", "at_index": 0, "box": "TrimBox", "rect_pt": [0, 0, 612, 792]},
            {"op": "page_label_set", "start_index": 0, "style": "D", "start": 1},
            {"op": "normalize_page_tree"},
            {"op": "metadata_set", "key": "Title", "value": "X"},
            {"op": "metadata_strip", "keys": ["Author"]},
            {"op": "colorspace_swap", "target": "cmyk"},
            {"op": "strip_javascript"},
            {"op": "strip_embedded_files"},
            {"op": "pdfx_pin", "level": "PDF/X-4"},
            {"op": "producer_creator_stamp", "producer": "compile-pdf"},
        ]
    )
    assert len(plan.ops) == 15


def test_unknown_op_rejected() -> None:
    with pytest.raises(ValidationError):
        _validate([{"op": "wat"}])


def test_missing_required_field_rejected() -> None:
    with pytest.raises(ValidationError):
        _validate([{"op": "ocg_flip", "layer": "Bleed"}])  # missing visible


def test_extra_field_rejected_in_op() -> None:
    with pytest.raises(ValidationError):
        _validate([{"op": "metadata_set", "key": "Title", "value": "X", "extra": "no"}])


def test_extra_field_rejected_in_plan() -> None:
    with pytest.raises(ValidationError):
        RewritePlan.model_validate(
            {
                "schema_version": "1.0.0",
                "ops": [],
                "garbage": True,
            }
        )


def test_box_rect_pt_must_be_4_floats() -> None:
    with pytest.raises(ValidationError):
        _validate([{"op": "box_set", "at_index": 0, "box": "TrimBox", "rect_pt": [0, 0, 612]}])


def test_page_rotate_degrees_constrained() -> None:
    with pytest.raises(ValidationError):
        _validate([{"op": "page_rotate", "at_index": 0, "degrees": 45}])


def test_page_insert_dimensions_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        _validate([{"op": "page_insert", "at_index": 0, "width_pt": 0, "height_pt": 792}])


def test_metadata_key_constrained_to_known_set() -> None:
    with pytest.raises(ValidationError):
        _validate([{"op": "metadata_set", "key": "NotARealKey", "value": "X"}])


def test_pdfx_level_constrained() -> None:
    with pytest.raises(ValidationError):
        _validate([{"op": "pdfx_pin", "level": "PDF/X-99"}])


def test_metadata_strip_requires_at_least_one_key() -> None:
    with pytest.raises(ValidationError):
        _validate([{"op": "metadata_strip", "keys": []}])


def test_schema_version_locked_to_1_0_0() -> None:
    with pytest.raises(ValidationError):
        RewritePlan.model_validate({"schema_version": "2.0.0", "ops": []})


def test_default_schema_version_is_1_0_0() -> None:
    plan = RewritePlan.model_validate({"ops": []})
    assert plan.schema_version == "1.0.0"


def test_json_schema_contains_discriminator() -> None:
    schema = rewrite_plan_json_schema()
    raw = json.dumps(schema)
    assert "discriminator" in raw
    assert '"op"' in raw


def test_empty_ops_list_is_valid() -> None:
    plan = _validate([])
    assert plan.ops == []
