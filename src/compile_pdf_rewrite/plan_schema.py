"""Rewrite plan schema — 15 mutations grouped by category, expressed as
a discriminated union over the ``op`` field.

Plan canonicalization (sort + drop comments + normalize numbers) is handled
by :mod:`compile_pdf.cache`; this module only defines shape + validation.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, NonNegativeInt, RootModel


class _OpBase(BaseModel):
    """Common envelope. Each subclass declares ``op`` as a Literal so
    Pydantic can build a discriminated union without manual type tags."""

    model_config = {"extra": "forbid", "frozen": True}


# --- Structural ----------------------------------------------------------


class OcgFlipOp(_OpBase):
    op: Literal["ocg_flip"]
    layer: str = Field(min_length=1, description="OCG layer name (matches CodexOCG.name).")
    visible: bool


class PageInsertOp(_OpBase):
    op: Literal["page_insert"]
    at_index: NonNegativeInt = Field(description="0-based index where the new page is inserted.")
    width_pt: float = Field(gt=0)
    height_pt: float = Field(gt=0)


class PageDeleteOp(_OpBase):
    op: Literal["page_delete"]
    at_index: NonNegativeInt


class PageReorderOp(_OpBase):
    op: Literal["page_reorder"]
    order: list[NonNegativeInt] = Field(
        min_length=1,
        description="Permutation of original page indices. Length must equal page count.",
    )


class PageRotateOp(_OpBase):
    op: Literal["page_rotate"]
    at_index: NonNegativeInt
    degrees: Literal[0, 90, 180, 270]


BoxName = Literal["TrimBox", "BleedBox", "ArtBox", "CropBox", "MediaBox"]


class BoxSetOp(_OpBase):
    op: Literal["box_set"]
    at_index: NonNegativeInt
    box: BoxName
    rect_pt: tuple[float, float, float, float] = Field(
        description="(llx, lly, urx, ury) in points; must satisfy llx<urx, lly<ury.",
    )


PageLabelStyle = Literal["D", "R", "r", "A", "a", "none"]


class PageLabelSetOp(_OpBase):
    op: Literal["page_label_set"]
    start_index: NonNegativeInt = Field(
        description="0-based index where this label range begins.",
    )
    style: PageLabelStyle = Field(
        description=(
            "PDF page-label style: D (decimal), R/r (Roman upper/lower), "
            "A/a (alpha upper/lower), or 'none' to drop the entry."
        ),
    )
    prefix: str | None = None
    start: int = Field(default=1, ge=1)


class NormalizePageTreeOp(_OpBase):
    op: Literal["normalize_page_tree"]


# --- Hygiene -------------------------------------------------------------

MetadataKey = Literal[
    "Title", "Author", "Subject", "Keywords", "Creator", "Producer", "CreationDate", "ModDate"
]


class MetadataSetOp(_OpBase):
    op: Literal["metadata_set"]
    key: MetadataKey
    value: str


class MetadataStripOp(_OpBase):
    op: Literal["metadata_strip"]
    keys: list[MetadataKey] = Field(min_length=1)


class ColorspaceSwapOp(_OpBase):
    op: Literal["colorspace_swap"]
    target: Literal["rgb", "cmyk"]


class StripJavascriptOp(_OpBase):
    op: Literal["strip_javascript"]


class StripEmbeddedFilesOp(_OpBase):
    op: Literal["strip_embedded_files"]


# --- Lifecycle -----------------------------------------------------------

PdfxLevel = Literal["PDF/X-1a", "PDF/X-3", "PDF/X-4", "PDF/X-6", "none"]


class PdfxPinOp(_OpBase):
    op: Literal["pdfx_pin"]
    level: PdfxLevel


class ProducerCreatorStampOp(_OpBase):
    op: Literal["producer_creator_stamp"]
    producer: str | None = None
    creator: str | None = None


# --- Top-level plan ------------------------------------------------------

RewriteOp = Annotated[
    OcgFlipOp
    | PageInsertOp
    | PageDeleteOp
    | PageReorderOp
    | PageRotateOp
    | BoxSetOp
    | PageLabelSetOp
    | NormalizePageTreeOp
    | MetadataSetOp
    | MetadataStripOp
    | ColorspaceSwapOp
    | StripJavascriptOp
    | StripEmbeddedFilesOp
    | PdfxPinOp
    | ProducerCreatorStampOp,
    Field(discriminator="op"),
]


class RewritePlan(BaseModel):
    """A rewrite plan — schema-versioned, ordered list of operations.

    Operations execute in order; later ops see the document state produced
    by earlier ops.
    """

    model_config = {"extra": "forbid"}

    schema_version: Literal["1.0.0"] = Field(
        default="1.0.0",
        description="Bumps when the plan-document schema changes (per producer; spec §6.2).",
    )
    ops: list[RewriteOp] = Field(default_factory=list)


class RewritePlanRoot(RootModel[RewritePlan]):
    """Root model — emit the JSON Schema directly without a wrapping object."""


def rewrite_plan_json_schema() -> dict[str, object]:
    """Return the JSON Schema for a rewrite plan document.

    Surfaced via ``compile-pdf schema rewrite`` and ``GET /v1/schema/rewrite``.
    """
    return RewritePlan.model_json_schema()
