"""FastAPI router for the rewrite producer.

Mounts under ``/v1/rewrite`` from :mod:`compile_pdf.api.main`. Single
endpoint today: ``POST /v1/rewrite/apply``.
"""

from __future__ import annotations

import base64
import hashlib

import structlog
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from compile_pdf_core.cache import compute_cache_key, hash_canonical_plan
from compile_pdf_core.retention import (
    parse_consent,
    persist_if_opted_in,
    resolve_tenant,
)
from compile_pdf_rewrite.engine import RewritePlanError, apply_plan
from compile_pdf_rewrite.plan_schema import RewritePlan
from compile_pdf_rewrite.verify import verify_rewrite
from compile_pdf_core.version import (
    CODEX_DOCUMENT_SCHEMA_VERSION_PIN,
    REWRITE_SCHEMA_VERSION,
    VERSION,
)

logger = structlog.get_logger(__name__)

router = APIRouter()


class RewriteApplyRequest(BaseModel):
    """Request envelope: an inline base64-encoded PDF + a plan.

    Bytes-in / bytes-out. Lineage records persist to the configured S3
    bucket asynchronously and are addressable by the returned
    ``cache_key`` (Phase 5 lights up the actual store).
    """

    model_config = {"extra": "forbid"}

    input_pdf_b64: str = Field(min_length=1)
    plan: RewritePlan


class RewriteApplyResponse(BaseModel):
    """Response envelope. Output bytes are returned base64 so the
    transport stays JSON; bypassed by the streaming variant in Phase 1.x."""

    model_config = {"extra": "forbid"}

    output_pdf_b64: str
    pdf_sha256: str
    input_sha256: str
    plan_sha256: str
    cache_key: str
    cache_hit: bool = False
    ops_applied: int
    schema_version: str = REWRITE_SCHEMA_VERSION
    compile_version: str = VERSION


@router.post("/apply", response_model=RewriteApplyResponse, status_code=status.HTTP_200_OK)
async def rewrite_apply(payload: RewriteApplyRequest, request: Request) -> RewriteApplyResponse:
    """Apply a rewrite plan to an inline base64-encoded PDF.

    Verification (spec §2.3 — three layers) runs server-side before the
    response is returned. A failed verify is a 500 — the plan was valid
    but the engine produced output that doesn't satisfy the post-conditions.
    """
    try:
        input_bytes = base64.b64decode(payload.input_pdf_b64, validate=True)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"input_pdf_b64 is not valid base64: {exc}",
        ) from exc

    if not input_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="input is empty")

    input_sha256 = hashlib.sha256(input_bytes).hexdigest()
    plan_sha256 = hash_canonical_plan(payload.plan.model_dump(mode="json"))

    try:
        from codex_pdf.color import COLOR_SCHEMA_VERSION
        from codex_pdf.geom import GEOM_SCHEMA_VERSION
    except ImportError as exc:  # pragma: no cover — codex-pdf is a hard dep
        raise HTTPException(
            status_code=500, detail=f"codex-pdf surface unavailable: {exc}"
        ) from exc

    cache_key = compute_cache_key(
        producer="rewrite",
        input_sha256=input_sha256,
        canonical_plan_sha256=plan_sha256,
        codex_pdf_package_version=_resolve_codex_pdf_version(),
        color_schema_version=COLOR_SCHEMA_VERSION,
        geom_schema_version=GEOM_SCHEMA_VERSION,
        codex_document_schema_version=CODEX_DOCUMENT_SCHEMA_VERSION_PIN,
    )

    logger.info(
        "rewrite.apply.start",
        ops=len(payload.plan.ops),
        input_sha256=input_sha256[:16],
        plan_sha256=plan_sha256[:16],
        cache_key=cache_key[:16],
    )

    try:
        result = apply_plan(input_bytes, payload.plan)
    except RewritePlanError as exc:
        raise HTTPException(status_code=422, detail=f"plan rejected: {exc}") from exc

    verify = verify_rewrite(
        input_bytes=input_bytes,
        output_bytes=result.output_bytes,
        plan=payload.plan,
        determinism_replay=False,
    )
    if not (verify.layer1_schema and verify.layer3_unchanged):
        logger.error("rewrite.apply.verify_failed", failures=verify.failures)
        raise HTTPException(
            status_code=500,
            detail={"error": "verify failed", "failures": verify.failures},
        )

    consent = parse_consent(request)
    response = RewriteApplyResponse(
        output_pdf_b64=base64.b64encode(result.output_bytes).decode("ascii"),
        pdf_sha256=result.pdf_sha256,
        input_sha256=input_sha256,
        plan_sha256=plan_sha256,
        cache_key=cache_key,
        cache_hit=False,
        ops_applied=result.ops_applied,
    )
    retained = persist_if_opted_in(
        consent=consent,
        producer="rewrite",
        tenant=resolve_tenant(request),
        input_bytes=input_bytes,
        output_bytes=result.output_bytes,
        result=response.model_dump(mode="json"),
        input_sha256=input_sha256,
    )
    logger.info(
        "rewrite.apply.ok",
        output_sha256=result.pdf_sha256[:16],
        ops_applied=result.ops_applied,
        consent=consent,
        retained=retained,
    )
    return response


def _resolve_codex_pdf_version() -> str:
    """Read codex_pdf wheel version Compile was deployed against."""
    try:
        from codex_pdf import __version__ as codex_version
    except ImportError:
        return "unknown"
    return str(codex_version)
