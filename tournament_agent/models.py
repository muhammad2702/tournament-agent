"""Records shared by the inventory components."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class SourceRef:
    page_url: str
    anchor: str = ""
    label: str = ""


@dataclass(frozen=True)
class DocumentRef:
    url: str
    source_page_url: str
    anchor: str = ""
    label: str = ""


@dataclass(frozen=True)
class Download:
    url: str
    final_url: str
    path: str
    sha256: str
    content_type: str
    from_cache: bool


@dataclass(frozen=True)
class LanguageCheck:
    language: str
    confidence: float
    observed_languages: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class SourceProbe:
    source_url: str
    source_kind: str
    anchor: str
    page: int | None
    heading: str
    age_group: str | None
    difficulty: str | None
    date: str | None
    language: LanguageCheck
    status: str
    document_links: tuple[str, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class ImageRef:
    id: str
    url: str
    alt: str
    association: str = "within_question_source_range_unverified"


@dataclass(frozen=True)
class HtmlQuestion:
    id: str
    number: int
    points: int | None
    author: str | None
    source_start: int
    source_end: int
    start_line: int
    end_line: int
    original_html: str
    text: str
    images: tuple[ImageRef, ...]
    issues: tuple[str, ...]


@dataclass(frozen=True)
class HtmlQuestionSet:
    source: SourceRef
    format: str
    section_sha256: str
    preamble_html: str
    questions: tuple[HtmlQuestion, ...]
    trailing_html: str
    coverage_verified: bool
    status: str
    issues: tuple[str, ...]


@dataclass(frozen=True)
class PdfSpan:
    id: str
    line_id: str
    text: str
    bbox: tuple[float, float, float, float]
    origin: tuple[float, float]
    font: str
    size: float
    flags: int
    script: str = "normal"


@dataclass(frozen=True)
class PdfScore:
    value: int
    subpart: str | None
    span_ids: tuple[str, ...]


@dataclass(frozen=True)
class PdfGraphic:
    kind: str
    bbox: tuple[float, float, float, float]
    association: str = "within_question_region_unverified"


@dataclass(frozen=True)
class PdfQuestion:
    id: str
    number: int
    page: int
    bbox: tuple[float, float, float, float]
    points: int | None
    scores: tuple[PdfScore, ...]
    author: str | None
    text: str
    spans: tuple[PdfSpan, ...]
    graphics: tuple[PdfGraphic, ...]
    region_image: str | None
    issues: tuple[str, ...]


@dataclass(frozen=True)
class PdfQuestionSet:
    source_url: str
    page: int
    format: str
    age_group: str | None
    difficulty: str | None
    date: str | None
    heading: str
    questions: tuple[PdfQuestion, ...]
    preamble_spans: tuple[PdfSpan, ...]
    source_span_count: int
    span_coverage_verified: bool
    status: str
    issues: tuple[str, ...]


@dataclass(frozen=True)
class StatementSource:
    id: str
    source_sha256: str
    kind: str
    language: str
    text: str
    evidence: str


@dataclass(frozen=True)
class ModelRequest:
    purpose: str
    source_sha256: str
    prompt_version: str
    model: str
    messages: tuple[dict[str, Any], ...]
    max_output_tokens: int
    response_schema: dict[str, Any] | None = None


@dataclass(frozen=True)
class RequestBudget:
    """Caller-supplied conservative bound, not a character-count estimate.

    Input tokens must include images and prompt/schema overhead. Extra cost
    covers any documented non-token fees. Selecting/verifying current model
    pricing and its image-token rules is required before a live request.
    """
    input_token_bound: int
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal
    price_reference: str
    extra_cost_usd: Decimal = Decimal("0")


@dataclass(frozen=True)
class ModelResult:
    call_id: str
    provider_id: str
    model: str
    content: str
    cost_usd: Decimal
    from_cache: bool
