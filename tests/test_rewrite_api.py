"""Integration tests for POST /v1/rewrite/apply."""

from __future__ import annotations

import base64
import io

import pikepdf
from fastapi.testclient import TestClient

from compile_pdf.api.main import app


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def test_rewrite_apply_round_trips(simple_pdf: bytes) -> None:
    client = TestClient(app)
    response = client.post(
        "/v1/rewrite/apply",
        json={
            "input_pdf_b64": _b64(simple_pdf),
            "plan": {
                "ops": [
                    {"op": "metadata_set", "key": "Title", "value": "via api"},
                    {"op": "pdfx_pin", "level": "PDF/X-4"},
                ]
            },
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ops_applied"] == 2
    assert body["pdf_sha256"]
    assert body["cache_key"]
    assert body["plan_sha256"]
    assert body["input_sha256"]
    assert body["compile_version"]

    output = pikepdf.open(io.BytesIO(base64.b64decode(body["output_pdf_b64"])))
    try:
        assert str(output.docinfo[pikepdf.Name.Title]) == "via api"
        assert pikepdf.Name.OutputIntents in output.Root
    finally:
        output.close()


def test_rewrite_apply_rejects_invalid_base64() -> None:
    client = TestClient(app)
    response = client.post(
        "/v1/rewrite/apply",
        json={"input_pdf_b64": "not-valid-base64!!!", "plan": {"ops": []}},
    )
    assert response.status_code == 400


def test_rewrite_apply_rejects_empty_input() -> None:
    """Empty base64 input is rejected by Pydantic's ``min_length=1`` (422)."""
    client = TestClient(app)
    response = client.post(
        "/v1/rewrite/apply",
        json={"input_pdf_b64": _b64(b""), "plan": {"ops": []}},
    )
    assert response.status_code == 422


def test_rewrite_apply_returns_422_for_unapplicable_plan(simple_pdf: bytes) -> None:
    """Plan-shape errors caught at engine time → 422."""
    client = TestClient(app)
    response = client.post(
        "/v1/rewrite/apply",
        json={
            "input_pdf_b64": _b64(simple_pdf),
            "plan": {"ops": [{"op": "page_delete", "at_index": 99}]},
        },
    )
    assert response.status_code == 422


def test_rewrite_apply_rejects_unknown_op_at_pydantic_layer(simple_pdf: bytes) -> None:
    client = TestClient(app)
    response = client.post(
        "/v1/rewrite/apply",
        json={
            "input_pdf_b64": _b64(simple_pdf),
            "plan": {"ops": [{"op": "wat"}]},
        },
    )
    assert response.status_code == 422


def test_contract_endpoint_lists_rewrite() -> None:
    client = TestClient(app)
    response = client.get("/v1/contract")
    assert response.status_code == 200
    endpoints = response.json()["endpoints"]
    assert any("/v1/rewrite/apply" in e for e in endpoints)


def test_same_input_same_plan_same_cache_key(simple_pdf: bytes) -> None:
    client = TestClient(app)
    payload = {
        "input_pdf_b64": _b64(simple_pdf),
        "plan": {"ops": [{"op": "metadata_set", "key": "Title", "value": "stable"}]},
    }
    a = client.post("/v1/rewrite/apply", json=payload).json()
    b = client.post("/v1/rewrite/apply", json=payload).json()
    assert a["cache_key"] == b["cache_key"]
    assert a["pdf_sha256"] == b["pdf_sha256"]
