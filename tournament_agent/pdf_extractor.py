"""Reader for the observed single-column, scored PDF format.

PDF is the first format-specific reader: it needs positioned spans rather
than HTML boundaries. No network, model calls, or image rendering happen here.
Raw spans and page regions remain the source evidence; formatted text is only
a candidate transcription, especially for complex mathematics.
"""

from collections import Counter
from dataclasses import replace
from statistics import median
import re
import pymupdf
from .webpage_extractor import _metadata
from .models import PdfGraphic, PdfQuestion, PdfQuestionSet, PdfScore, PdfSpan






def _read_lines(page: pymupdf.Page) -> list[list[PdfSpan]]:
    lines = []
    for block_index, block in enumerate(page.get_text("dict")["blocks"]):
        if block["type"] != 0:
            continue
        for line_index, line in enumerate(block["lines"]):
            line_id = f"p{page.number + 1}:b{block_index}:l{line_index}"
            spans = [PdfSpan(f"{line_id}:s{i}", line_id, span["text"], tuple(span["bbox"]),
                             tuple(span["origin"]), span["font"], span["size"], span["flags"])
                     for i, span in enumerate(line["spans"])]
            if spans:
                lines.append(spans)
    return sorted(lines, key=lambda line: (min(s.bbox[1] for s in line), min(s.bbox[0] for s in line)))


def _line_text(line: list[PdfSpan], scripts: bool = True) -> str:
    result = ""
    previous = None
    for span in sorted(line, key=lambda span: span.bbox[0]):
        text = span.text
        if scripts and span.script == "superscript_candidate":
            text = "^{" + text + "}"
        elif scripts and span.script == "subscript_candidate":
            text = "_{" + text + "}"
        if (previous and result and text and not result[-1].isspace() and not text[0].isspace()
                and span.bbox[0] - previous.bbox[2] > max(span.size, previous.size) * 0.18):
            result += " "
        result += text
        previous = span
    return result


def _line_y(line: list[PdfSpan]) -> float:
    return median(span.origin[1] for span in line)


def _script_evidence(lines: list[list[PdfSpan]], body_size: float) -> list[list[PdfSpan]]:
    result = []
    for line in lines:
        normal = [span for span in line if span.size >= body_size * 0.9]
        baseline = median(span.origin[1] for span in normal) if normal else None
        classified = []
        for span in line:
            script = "normal"
            if span.size < body_size * 0.85:
                if baseline is None:
                    script = "small_separate_line"
                elif span.origin[1] < baseline - body_size * 0.15:
                    script = "superscript_candidate"
                elif span.origin[1] > baseline + body_size * 0.15:
                    script = "subscript_candidate"
                else:
                    script = "small_unresolved"
            elif span.flags & 1:
                script = "raised_unresolved"
            classified.append(replace(span, script=script))
        result.append(classified)
    return result


def _question(lines: list[list[PdfSpan]], marker: list[PdfSpan], number: int,
              page: pymupdf.Page, top: float, bottom: float, number_x: float,
              body_size: float, graphics: list[PdfGraphic], source_url: str) -> PdfQuestion:
    classified = _script_evidence(lines, body_size)
    marker_id = marker[0].line_id
    scores = []
    score_line_ids = set()
    subparts = [(line, _line_text(line, False).strip()) for line in classified
                if re.fullmatch(r"[а-яa-z]\)", _line_text(line, False).strip())
                and abs(min(s.bbox[0] for s in line) - number_x) < 3]
    for line in classified:
        text = _line_text(line, False).strip()
        if text.isdigit() and max(s.bbox[2] for s in line) < number_x - 4:
            close = [(part, abs(_line_y(part) - _line_y(line)), label)
                     for part, label in subparts if abs(_line_y(part) - _line_y(line)) < body_size * 0.6]
            label = min(close, key=lambda item: item[1])[2] if close else None
            scores.append(PdfScore(int(text), label, tuple(s.id for s in line)))
            score_line_ids.add(line[0].line_id)

    body_lines = [line for line in classified if line[0].line_id != marker_id and line[0].line_id not in score_line_ids]
    author = None
    author_id = None
    issues = []
    if body_lines:
        last = max(body_lines, key=_line_y)
        candidate = _line_text(last, False).strip()
        if (re.search(r"[^\W\d_]\.", candidate, re.UNICODE)
                and all(span.flags & 2 for span in last if span.text.strip())):
            author = candidate
            author_id = last[0].line_id
    if not author:
        issues.append("Cannot identify a final author line from initials and italic font evidence")
    body_lines = [line for line in body_lines if line[0].line_id != author_id]
    points = scores[0].value if len(scores) == 1 and scores[0].subpart is None else None
    if not scores:
        issues.append("No score found in the left column")
    elif len(scores) > 1 and (any(score.subpart is None for score in scores)
                             or len({score.subpart for score in scores}) != len(scores)):
        issues.append("Multiple scores cannot be mapped uniquely to subparts")
    spans = tuple(span for line in classified for span in line)
    if any(span.script != "normal" for span in spans):
        issues.append("Small or raised/lowered notation needs comparison with the original page")
    if any("\ufffd" in span.text for span in spans):
        issues.append("Replacement characters in extracted text")
    question_graphics = tuple(graphic for graphic in graphics if top <= (graphic.bbox[1] + graphic.bbox[3]) / 2 < bottom)
    if question_graphics:
        issues.append("Raster/vector content retained by source region; mathematical marks and diagrams are not classified automatically")
        if any(graphic.bbox[1] < top or graphic.bbox[3] > bottom for graphic in question_graphics):
            issues.append("Graphic crosses the inferred question boundary")
    text = "\n".join(_line_text(line) for line in body_lines)
    if not text.strip():
        issues.append("Question has no body text")
    return PdfQuestion(f"{source_url}#p{page.number + 1}:q{number}", number, page.number + 1,
                       (page.rect.x0, top, page.rect.x1, bottom), points, tuple(scores), author,
                       text, spans, question_graphics, None, tuple(issues))


def extract_pdf_page(page: pymupdf.Page, source_url: str) -> PdfQuestionSet:
    """Extract a complete single-column set on one page or flag it for review.

    This reader deliberately does not claim to join questions across pages.
    All text spans are retained, even for unsupported or ambiguous layouts.
    """
    lines = _read_lines(page)
    all_spans = tuple(span for line in lines for span in line)
    header = "\n".join(page.get_text(sort=True).splitlines()[:6])
    group, difficulty, date = _metadata(header)
    def unsupported(reason):
        return PdfQuestionSet(source_url, page.number + 1, "unsupported", group, difficulty, date,
                              header, (), all_spans, len(all_spans), True, "needs_review", (reason,))
    if not all_spans:
        return unsupported("No readable text spans; scanned PDFs need a separate reading path")
    sizes = Counter(round(span.size, 1) for span in all_spans if span.text.strip())
    body_size = sizes.most_common(1)[0][0]
    markers = []
    for line in lines:
        if match := re.fullmatch(r"\s*(\d+)\.\s*", _line_text(line, False)):
            markers.append((line, int(match.group(1)), min(span.bbox[0] for span in line)))
    clusters = []
    for marker in sorted(markers, key=lambda marker: marker[2]):
        if not clusters or abs(marker[2] - clusters[-1][0][2]) > 3:
            clusters.append([marker])
        else:
            clusters[-1].append(marker)
    valid = [sorted(cluster, key=lambda marker: _line_y(marker[0])) for cluster in clusters if len(cluster) >= 2]
    valid = [cluster for cluster in valid if [marker[1] for marker in cluster] == list(range(1, len(cluster) + 1))]
    if len(valid) != 1:
        return unsupported("Expected one question-number column with sequential numbering from 1")
    selected = valid[0]
    number_x = median(marker[2] for marker in selected)
    if any(abs(marker[2] - number_x) <= 3 and marker not in selected for marker in markers):
        return unsupported("Extra or duplicate question markers in the selected column")
    tops = [max(page.rect.y0, min(span.bbox[1] for span in marker[0]) - body_size * 0.4) for marker in selected]
    graphics = [PdfGraphic("raster_content", tuple(image["bbox"])) for image in page.get_image_info()]
    graphics.extend(PdfGraphic("vector_content", tuple(drawing["rect"])) for drawing in page.get_drawings())
    preamble = tuple(span for line in lines if _line_y(line) < tops[0] for span in line)
    questions = []
    for index, (marker, number, _) in enumerate(selected):
        top = tops[index]
        bottom = tops[index + 1] if index + 1 < len(tops) else page.rect.y1
        question_lines = [line for line in lines if top <= _line_y(line) < bottom]
        questions.append(_question(question_lines, marker, number, page, top, bottom,
                                   number_x, body_size, graphics, source_url))
    ids = [span.id for span in preamble] + [span.id for question in questions for span in question.spans]
    coverage = len(ids) == len(set(ids)) == len(all_spans) and set(ids) == {span.id for span in all_spans}
    issues = []
    if not all((group, difficulty, date)):
        issues.append("Heading does not establish group, difficulty and date")
    if not coverage:
        issues.append("Text span coverage failed")
    issues.extend(f"Question {question.number}: {issue}" for question in questions for issue in question.issues)
    return PdfQuestionSet(source_url, page.number + 1, "single_page_score_column", group, difficulty,
                          date, header, tuple(questions), preamble, len(all_spans), coverage,
                          "needs_review" if issues else "extracted", tuple(issues))
