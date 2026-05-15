"""Rewrite engine — pikepdf-driven mutations for the 15 in-scope ops.

Per spec §2.1: object-tree only — no content-stream surgery, no font
subsetting, no image recompression. Every mutation must round-trip
deterministically (same input + same plan → same SHA-256 output).

Determinism comes from ``Pdf.save(deterministic_id=True, linearize=False)``
plus rejecting any op that would introduce wall-clock time
(``CreationDate``/``ModDate`` set explicitly via plan, never auto-stamped).

Plan operations execute in declaration order; later ops see the document
state produced by earlier ops.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass

import pikepdf
import pikepdf.models
from pikepdf import Array, Dictionary, Name, Object, Pdf, String

from compile_pdf_rewrite.plan_schema import (
    BoxSetOp,
    ColorspaceSwapOp,
    MetadataSetOp,
    MetadataStripOp,
    NormalizePageTreeOp,
    OcgFlipOp,
    PageDeleteOp,
    PageInsertOp,
    PageLabelSetOp,
    PageReorderOp,
    PageRotateOp,
    PdfxPinOp,
    ProducerCreatorStampOp,
    RewriteOp,
    RewritePlan,
    StripEmbeddedFilesOp,
    StripJavascriptOp,
)


@dataclass(frozen=True)
class RewriteResult:
    """Outcome of running a plan against an input PDF."""

    output_bytes: bytes
    pdf_sha256: str
    ops_applied: int


class RewritePlanError(ValueError):
    """Plan references an entity that doesn't exist in the document
    (e.g. delete page 12 of a 10-page PDF, flip an OCG layer that isn't
    defined). Raised before any mutation is committed."""


# --- Op handlers ---------------------------------------------------------


def _apply_ocg_flip(pdf: Pdf, op: OcgFlipOp) -> None:
    target: Dictionary | None = None
    for ocg in _get_ocgs(pdf):
        if _name_of_ocg(ocg) == op.layer:
            target = ocg
            break
    if target is None:
        raise RewritePlanError(f"OCG layer not found: {op.layer!r}")
    d = _ensure_ocg_default_config(pdf)
    on_filtered = Array([ref for ref in _as_list(d.get(Name.ON)) if ref.objgen != target.objgen])
    off_filtered = Array([ref for ref in _as_list(d.get(Name.OFF)) if ref.objgen != target.objgen])
    if op.visible:
        on_filtered.append(target)
    else:
        off_filtered.append(target)
    d[Name.ON] = on_filtered
    d[Name.OFF] = off_filtered


def _apply_page_insert(pdf: Pdf, op: PageInsertOp) -> None:
    if op.at_index > len(pdf.pages):
        raise RewritePlanError(
            f"page_insert at_index {op.at_index} > current page count {len(pdf.pages)}"
        )
    blank = pikepdf.Page(
        pdf.make_indirect(
            Dictionary(
                Type=Name.Page,
                MediaBox=Array([0, 0, op.width_pt, op.height_pt]),
                Resources=Dictionary(),
                Contents=pdf.make_stream(b""),
            )
        )
    )
    if op.at_index == len(pdf.pages):
        pdf.pages.append(blank)
    else:
        pdf.pages.insert(op.at_index, blank)


def _apply_page_delete(pdf: Pdf, op: PageDeleteOp) -> None:
    if op.at_index >= len(pdf.pages):
        raise RewritePlanError(f"page_delete at_index {op.at_index} >= page count {len(pdf.pages)}")
    if len(pdf.pages) == 1:
        raise RewritePlanError("cannot delete the only page in the document")
    del pdf.pages[op.at_index]


def _apply_page_reorder(pdf: Pdf, op: PageReorderOp) -> None:
    n = len(pdf.pages)
    if len(op.order) != n:
        raise RewritePlanError(f"page_reorder.order length {len(op.order)} != page count {n}")
    if sorted(op.order) != list(range(n)):
        raise RewritePlanError("page_reorder.order must be a permutation of 0..n-1")
    snapshot = [pdf.pages[i] for i in op.order]
    while len(pdf.pages) > 0:
        del pdf.pages[0]
    for page in snapshot:
        pdf.pages.append(page)


def _apply_page_rotate(pdf: Pdf, op: PageRotateOp) -> None:
    if op.at_index >= len(pdf.pages):
        raise RewritePlanError(f"page_rotate at_index {op.at_index} >= page count")
    pdf.pages[op.at_index].rotate(op.degrees, relative=False)


def _apply_box_set(pdf: Pdf, op: BoxSetOp) -> None:
    if op.at_index >= len(pdf.pages):
        raise RewritePlanError(f"box_set at_index {op.at_index} >= page count")
    llx, lly, urx, ury = op.rect_pt
    if not (llx < urx and lly < ury):
        raise RewritePlanError(f"box_set rect_pt must satisfy llx<urx, lly<ury: {op.rect_pt}")
    page = pdf.pages[op.at_index]
    page.obj[Name(f"/{op.box}")] = Array([llx, lly, urx, ury])


def _apply_page_label_set(pdf: Pdf, op: PageLabelSetOp) -> None:
    if op.start_index >= len(pdf.pages):
        raise RewritePlanError(f"page_label_set.start_index {op.start_index} >= page count")
    catalog = pdf.Root
    page_labels = catalog.get(Name.PageLabels)
    if not isinstance(page_labels, Dictionary):
        page_labels = Dictionary(Nums=Array([]))
        catalog[Name.PageLabels] = page_labels
    nums = _as_list(page_labels.get(Name.Nums))
    pruned = Array([])
    i = 0
    while i < len(nums):
        if nums[i] == op.start_index:
            i += 2
            continue
        pruned.append(nums[i])
        if i + 1 < len(nums):
            pruned.append(nums[i + 1])
        i += 2
    if op.style == "none":
        page_labels[Name.Nums] = pruned
        return
    entry = Dictionary(St=op.start)
    entry[Name.S] = Name(f"/{op.style}")
    if op.prefix is not None:
        entry[Name.P] = String(op.prefix)
    pruned.append(op.start_index)
    pruned.append(entry)
    page_labels[Name.Nums] = pruned


def _apply_normalize_page_tree(pdf: Pdf, _op: NormalizePageTreeOp) -> None:
    # pikepdf flattens the page tree on save; we explicitly touch every
    # page reference so an unbalanced /Pages tree gets re-emitted as a
    # single-level /Kids array.
    snapshot = list(pdf.pages)
    while len(pdf.pages) > 0:
        del pdf.pages[0]
    for page in snapshot:
        pdf.pages.append(page)


def _apply_metadata_set(pdf: Pdf, op: MetadataSetOp) -> None:
    pdf.docinfo[Name(f"/{op.key}")] = String(op.value)
    with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as meta:
        _xmp_set(meta, op.key, op.value)


def _apply_metadata_strip(pdf: Pdf, op: MetadataStripOp) -> None:
    for key in op.keys:
        info_key = Name(f"/{key}")
        if info_key in pdf.docinfo:
            del pdf.docinfo[info_key]
    with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as meta:
        for key in op.keys:
            _xmp_delete(meta, key)


_RGB_NAMES = {Name.DeviceRGB, Name.RGB, Name("/CalRGB")}
_CMYK_NAMES = {Name.DeviceCMYK, Name.CMYK}


def _apply_colorspace_swap(pdf: Pdf, op: ColorspaceSwapOp) -> None:
    target = Name.DeviceRGB if op.target == "rgb" else Name.DeviceCMYK
    sources = _RGB_NAMES if op.target == "cmyk" else _CMYK_NAMES
    for page in pdf.pages:
        resources = page.obj.get(Name.Resources)
        if resources is None:
            continue
        cs = resources.get(Name.ColorSpace)
        if cs is None:
            continue
        for key in list(cs.keys()):
            value = cs[key]
            if isinstance(value, Name) and value in sources:
                cs[key] = target


def _apply_strip_javascript(pdf: Pdf, _op: StripJavascriptOp) -> None:
    catalog = pdf.Root
    names = catalog.get(Name.Names)
    if isinstance(names, Dictionary) and Name.JavaScript in names:
        del names[Name.JavaScript]
    for action_root in (catalog, *(p.obj for p in pdf.pages)):
        aa = action_root.get(Name.AA) if isinstance(action_root, Dictionary) else None
        if aa is None:
            continue
        for trigger in list(aa.keys()):
            entry = aa[trigger]
            if isinstance(entry, Dictionary) and entry.get(Name.S) == Name.JavaScript:
                del aa[trigger]
    open_action = catalog.get(Name.OpenAction)
    if isinstance(open_action, Dictionary) and open_action.get(Name.S) == Name.JavaScript:
        del catalog[Name.OpenAction]


def _apply_strip_embedded_files(pdf: Pdf, _op: StripEmbeddedFilesOp) -> None:
    catalog = pdf.Root
    names = catalog.get(Name.Names)
    if isinstance(names, Dictionary) and Name.EmbeddedFiles in names:
        del names[Name.EmbeddedFiles]
    for page in pdf.pages:
        annots = _as_list(page.obj.get(Name.Annots))
        if not annots:
            continue
        kept_items = [
            ann
            for ann in annots
            if not (isinstance(ann, Dictionary) and ann.get(Name.Subtype) == Name.FileAttachment)
        ]
        page.obj[Name.Annots] = Array(kept_items)


_PDFX_GTS_VERSION = {
    "PDF/X-1a": "PDF/X-1a:2001",
    "PDF/X-3": "PDF/X-3:2002",
    "PDF/X-4": "PDF/X-4",
    "PDF/X-6": "PDF/X-6",
}


def _apply_pdfx_pin(pdf: Pdf, op: PdfxPinOp) -> None:
    catalog = pdf.Root
    if op.level == "none":
        if Name.OutputIntents in catalog:
            del catalog[Name.OutputIntents]
        with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as meta:
            for ns_key in ("pdfx:GTS_PDFXVersion", "pdfxid:GTS_PDFXVersion"):
                if ns_key in meta:
                    del meta[ns_key]
        return
    intent = Dictionary(
        Type=Name.OutputIntent,
        S=Name.GTS_PDFX,
        OutputConditionIdentifier=String("Custom"),
        Info=String(_PDFX_GTS_VERSION[op.level]),
    )
    catalog[Name.OutputIntents] = Array([pdf.make_indirect(intent)])
    with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as meta:
        meta["pdfx:GTS_PDFXVersion"] = _PDFX_GTS_VERSION[op.level]


def _apply_producer_creator_stamp(pdf: Pdf, op: ProducerCreatorStampOp) -> None:
    if op.producer is not None:
        pdf.docinfo[Name.Producer] = String(op.producer)
    if op.creator is not None:
        pdf.docinfo[Name.Creator] = String(op.creator)
    with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as meta:
        if op.producer is not None:
            meta["pdf:Producer"] = op.producer
        if op.creator is not None:
            meta["xmp:CreatorTool"] = op.creator


# --- Dispatch ------------------------------------------------------------


def _dispatch(pdf: Pdf, op: RewriteOp) -> None:
    match op:
        case OcgFlipOp():
            _apply_ocg_flip(pdf, op)
        case PageInsertOp():
            _apply_page_insert(pdf, op)
        case PageDeleteOp():
            _apply_page_delete(pdf, op)
        case PageReorderOp():
            _apply_page_reorder(pdf, op)
        case PageRotateOp():
            _apply_page_rotate(pdf, op)
        case BoxSetOp():
            _apply_box_set(pdf, op)
        case PageLabelSetOp():
            _apply_page_label_set(pdf, op)
        case NormalizePageTreeOp():
            _apply_normalize_page_tree(pdf, op)
        case MetadataSetOp():
            _apply_metadata_set(pdf, op)
        case MetadataStripOp():
            _apply_metadata_strip(pdf, op)
        case ColorspaceSwapOp():
            _apply_colorspace_swap(pdf, op)
        case StripJavascriptOp():
            _apply_strip_javascript(pdf, op)
        case StripEmbeddedFilesOp():
            _apply_strip_embedded_files(pdf, op)
        case PdfxPinOp():
            _apply_pdfx_pin(pdf, op)
        case ProducerCreatorStampOp():
            _apply_producer_creator_stamp(pdf, op)


# --- Entry point ---------------------------------------------------------


def apply_plan(input_bytes: bytes, plan: RewritePlan) -> RewriteResult:
    """Apply a validated rewrite plan to an input PDF.

    Returns a :class:`RewriteResult` carrying the output bytes and their
    SHA-256. Raises :class:`RewritePlanError` if any op references an
    entity the document doesn't contain (caught before the first byte is
    written, so a failed apply never produces a partial output).
    """
    if not isinstance(input_bytes, (bytes, bytearray)) or not input_bytes:
        raise ValueError("input_bytes must be non-empty bytes")

    pdf = pikepdf.open(io.BytesIO(input_bytes))
    try:
        for op in plan.ops:
            _dispatch(pdf, op)
        buffer = io.BytesIO()
        pdf.save(
            buffer,
            deterministic_id=True,
            linearize=False,
            qdf=False,
            recompress_flate=False,
        )
    finally:
        pdf.close()

    output_bytes = buffer.getvalue()
    digest = hashlib.sha256(output_bytes).hexdigest()
    return RewriteResult(
        output_bytes=output_bytes,
        pdf_sha256=digest,
        ops_applied=len(plan.ops),
    )


# --- Helpers -------------------------------------------------------------


def _as_list(value: object | None) -> list[Object]:
    """Treat the pikepdf entry as an iterable Array; empty if absent or wrong type.

    Workaround for pikepdf's stub declaring ``Array.__iter__ -> Iterable[Object]``
    (should be ``Iterator``) — pulling items by index gives mypy a real
    list to chew on without the broken iterator protocol.
    """
    if not isinstance(value, Array):
        return []
    return [value[i] for i in range(len(value))]


def _get_ocgs(pdf: Pdf) -> list[Dictionary]:
    oc_props = pdf.Root.get(Name.OCProperties)
    if not isinstance(oc_props, Dictionary):
        return []
    out: list[Dictionary] = []
    for ref in _as_list(oc_props.get(Name.OCGs)):
        if isinstance(ref, Dictionary):
            out.append(ref)
    return out


def _name_of_ocg(ocg: Dictionary) -> str:
    name = ocg.get(Name.Name)
    if isinstance(name, String):
        return str(name)
    return ""


def _ensure_ocg_default_config(pdf: Pdf) -> Dictionary:
    oc_props = pdf.Root.get(Name.OCProperties)
    if oc_props is None:
        raise RewritePlanError("document has no /OCProperties — nothing to flip")
    d = oc_props.get(Name.D)
    if not isinstance(d, Dictionary):
        d = Dictionary(BaseState=Name.ON, ON=Array([]), OFF=Array([]))
        oc_props[Name.D] = d
    if Name.ON not in d:
        d[Name.ON] = Array([])
    if Name.OFF not in d:
        d[Name.OFF] = Array([])
    return d


_XMP_KEY_MAP = {
    "Title": "dc:title",
    "Author": "dc:creator",
    "Subject": "dc:description",
    "Keywords": "pdf:Keywords",
    "Creator": "xmp:CreatorTool",
    "Producer": "pdf:Producer",
    "CreationDate": "xmp:CreateDate",
    "ModDate": "xmp:ModifyDate",
}


def _xmp_set(meta: pikepdf.models.PdfMetadata, key: str, value: str) -> None:
    """Mirror an Info-dict update into the XMP packet."""
    xmp_key = _XMP_KEY_MAP.get(key)
    if xmp_key is None:
        return
    meta[xmp_key] = value


def _xmp_delete(meta: pikepdf.models.PdfMetadata, key: str) -> None:
    xmp_key = _XMP_KEY_MAP.get(key)
    if xmp_key is None:
        return
    if xmp_key in meta:
        del meta[xmp_key]
