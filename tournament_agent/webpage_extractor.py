"""Local source inspection. No HTTP or model calls belong here.

Supports source inspection and the fixed-width score-column HTML layout.
Positioned PDF question extraction lives in pdf_extractor.py.
"""

from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
import hashlib
import re
from urllib.parse import urldefrag, urljoin, urlsplit
from bs4 import BeautifulSoup
from lingua import LanguageDetectorBuilder
import pymupdf
from .models import (HtmlQuestion, HtmlQuestionSet, ImageRef, LanguageCheck,
                     SourceProbe, SourceRef)






@lru_cache(maxsize=1)
def _detector():
    return LanguageDetectorBuilder.from_all_languages().with_low_accuracy_mode().build()


def check_language(text: str) -> LanguageCheck:
    """Check prose throughout a unit, rather than its filename or navigation.

    Thresholds are conservative routing rules, not calibrated probabilities.
    Disagreement or insufficient prose cannot produce an English skip.
    """
    chunks = [text[i:i + 1200] for i in range(0, len(text), 1200)]
    observations = []
    for chunk in chunks:
        prose = " ".join(re.findall(r"[^\W\d_]+", chunk, re.UNICODE))
        if sum(char.isalpha() for char in prose) < 80:
            continue
        scores = _detector().compute_language_confidence_values(prose)
        if len(scores) < 2:
            continue
        best, second = scores[:2]
        code = best.language.iso_code_639_1.name.lower()
        confident = best.value >= 0.75 and best.value - second.value >= 0.20
        observations.append((code if confident else "unknown", float(best.value)))
    languages = tuple(sorted({code for code, _ in observations}))
    confidence = min((score for _, score in observations), default=0.0)
    if not observations:
        return LanguageCheck("unknown", 0.0, (), "Not enough readable prose for a confident decision")
    if len(languages) > 1:
        return LanguageCheck("mixed_or_uncertain", confidence, languages, "Text chunks disagree; do not skip this unit as English")
    code = languages[0]
    return LanguageCheck(code, confidence, languages, "Local language evidence across readable prose chunks")


class _Markers(HTMLParser):
    def __init__(self, html: str, anchors: set[str]):
        super().__init__(convert_charrefs=False)
        self.offsets = [0]
        for line in html.splitlines(keepends=True):
            self.offsets.append(self.offsets[-1] + len(line))
        self.anchors = anchors
        self.markers: list[tuple[str, int, bool]] = []
        self.pre_ends: list[int] = []
        self.in_pre = False

    def _offset(self) -> int:
        line, column = self.getpos()
        return self.offsets[line - 1] + column

    def handle_starttag(self, tag, attrs):
        if tag == "pre":
            self.in_pre = True
        attrs = dict(attrs)
        for key in ("id", "name"):
            value = attrs.get(key)
            if value in self.anchors:
                self.markers.append((value, self._offset(), self.in_pre))
                break

    def handle_endtag(self, tag):
        if tag == "pre":
            self.pre_ends.append(self._offset())
            self.in_pre = False


def section_html(html: str, anchor: str, known_anchors: set[str]) -> str:
    """Slice unchanged HTML, respecting referenced anchors inside legacy PREs.

    Parsing offsets avoids relying on a well-formed DOM: old named anchors in
    this archive are often not closed. Unknown boundaries fail for review.
    """
    parser = _Markers(html, known_anchors | {anchor})
    parser.feed(html)
    matches = [(i, item) for i, item in enumerate(parser.markers) if item[0] == anchor]
    if len(matches) != 1:
        raise ValueError(f"Expected one section anchor {anchor!r}; found {len(matches)}")
    index, (_, start, in_pre) = matches[0]
    ends = []
    if index + 1 < len(parser.markers):
        ends.append(parser.markers[index + 1][1])
    if in_pre:
        ends.extend(end for end in parser.pre_ends if end > start)
    if not ends:
        raise ValueError(f"Cannot establish the end of section {anchor!r}")
    return html[start:min(ends)]


_MONTHS = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4,
           "мая": 5, "июня": 6, "июля": 7, "августа": 8,
           "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
           "january": 1, "february": 2, "march": 3, "april": 4,
           "may": 5, "june": 6, "july": 7, "august": 8,
           "september": 9, "october": 10, "november": 11, "december": 12}


def _metadata(heading: str) -> tuple[str | None, str | None, str | None]:
    lower = heading.lower()
    junior = bool(re.search(r"8\s*[-–—]\s*9|\bjunior\b", lower))
    senior = bool(re.search(r"10\s*[-–—]\s*11|\bsenior\b", lower))
    group = ("junior" if junior else "senior") if junior != senior else None
    basic = bool(re.search(r"базов\w*\s+вариант|\bbasic\b", lower))
    advanced = bool(re.search(r"сложн\w*\s+вариант|\badvanced\b", lower))
    level = ("basic" if basic else "advanced") if basic != advanced else None
    date = None
    match = re.search(r"\b(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})\b", lower)
    if match:
        day, month, year = match.groups()
        date = f"{year}-{_MONTHS[month]:02d}-{int(day):02d}"
    return group, level, date


def _probe(text: str, source_url: str, kind: str, *, anchor: str = "",
           page: int | None = None, links: tuple[str, ...] = ()) -> SourceProbe:
    lines = [line.strip() for line in text.splitlines() if line.strip() and not re.fullmatch(r"[-_=]+", line.strip())]
    heading = "\n".join(lines[:6])
    group, level, date = _metadata(heading)
    language = check_language(text)
    notes = []
    has_questions = bool(re.search(r"(?m)^\s*\d+\.\s+", text))
    if links and not has_questions:
        status = "linked_pdf"
        notes.append("Section is a document pointer; inspect linked PDF before extracting a set")
    elif language.language == "en":
        status = "skipped_english"
        notes.append("English unit: no translation, rebuilding or output rendering")
    elif language.language in ("unknown", "mixed_or_uncertain") or not all((group, level, date)):
        status = "needs_review"
        notes.append("Insufficient language or heading evidence; no output accepted")
    else:
        status = "ready_for_extraction"
    if kind == "pdf":
        notes.append("This is a page probe, not a confirmed complete problem set; extraction must establish continuations and boundaries")
    return SourceProbe(source_url, kind, anchor, page, heading, group, level,
                       date, language, status, links, tuple(notes))


def inspect_html(html: str, ref: SourceRef, known_anchors: set[str]) -> tuple[SourceProbe, str]:
    fragment = section_html(html, ref.anchor, known_anchors)
    soup = BeautifulSoup(fragment, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text()
    links = tuple(dict.fromkeys(
        urldefrag(urljoin(ref.page_url, tag["href"]))[0]
        for tag in soup.find_all("a", href=True)
        if urlsplit(urljoin(ref.page_url, tag["href"])).netloc == urlsplit(ref.page_url).netloc
        and urlsplit(urljoin(ref.page_url, tag["href"])).path.lower().endswith(".pdf")
    ))
    return _probe(text, ref.page_url, "html", anchor=ref.anchor, links=links), fragment


def inspect_pdf(path: Path, source_url: str) -> list[SourceProbe]:
    """Read PDF text only. In particular English pages are not rendered."""
    with pymupdf.open(path) as document:
        return [_probe(page.get_text(sort=True), source_url, "pdf", page=page.number + 1)
                for page in document]


_QUESTION_START = re.compile(r"^([ \t]*)(\d+)\.[ \t]+(?=\S)")
_AUTHOR_LINE = re.compile(r"^[ \t]*\(([^()\n]*[^\W\d_]\.[^()\n]+)\)[ \t]*$", re.UNICODE)


def _statement_text(html: str) -> str:
    """Review text with explicit superscripts/subscripts; original HTML is canonical."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    for tag in soup.find_all(["sup", "sub"]):
        tag.replace_with(("^{" if tag.name == "sup" else "_{") + tag.get_text() + "}")
    for image in soup.find_all("img"):
        image.replace_with("[[IMAGE:" + str(image.get("src", "")) + "]]")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    return soup.get_text()


def _question_record(raw: str, ref: SourceRef, number: int, start: int,
                     start_line: int, number_column: int) -> HtmlQuestion:
    question_id = f"{ref.anchor}:q{number}"
    text_lines = _statement_text(raw).splitlines()
    author_matches = [(i, match) for i, line in enumerate(text_lines)
                      if (match := _AUTHOR_LINE.fullmatch(line))]
    issues = []
    author = None
    if len(author_matches) == 1:
        author_index, author_match = author_matches[0]
        author = author_match.group(1)
        if any(line.strip() for line in text_lines[author_index + 1:]):
            issues.append("Content follows author inside question range; inspect boundary")
    else:
        author_index = len(text_lines)
        issues.append("Expected one standalone parenthesized author line with initials")

    body = []
    scores = []
    body_column = None
    for i, line in enumerate(text_lines[:author_index]):
        if i == 0:
            marker = _QUESTION_START.match(line)
            if marker:
                body_column = marker.end()
                line = " " * marker.end() + line[marker.end():]
        else:
            score = re.match(r"^([ \t]*)(\d+)([ \t]{2,}.*|[ \t]*)$", line)
            if score and len(score.group(1).expandtabs(8)) < number_column:
                scores.append(int(score.group(2)))
                line = line[:score.start(2)] + " " * len(score.group(2)) + line[score.end(2):]
        # Remove only layout indentation, retaining relative preformatted spacing.
        trim = min(body_column or 0, len(line) - len(line.lstrip(" ")))
        body.append(line[trim:])
    points = scores[0] if len(scores) == 1 else None
    if len(scores) != 1:
        issues.append(f"Expected one score in left column; found {len(scores)}")
    if not any(line.strip() for line in body):
        issues.append("Question has no readable body")

    images = []
    for index, image in enumerate(BeautifulSoup(raw, "html.parser").find_all("img"), 1):
        url = urljoin(ref.page_url, str(image.get("src", "")))
        if not image.get("src") or urlsplit(url).scheme not in ("http", "https"):
            issues.append(f"Image {index} has no supported source URL")
        images.append(ImageRef(f"{question_id}:img{index}", url, str(image.get("alt", ""))))
    if images:
        issues.append("Image references retained; visual ownership and shared figures need review")
    return HtmlQuestion(question_id, number, points, author, start, start + len(raw),
                        start_line, start_line + len(raw.splitlines()) - 1,
                        raw, "\n".join(body).strip("\n"), tuple(images), tuple(issues))


def extract_html_questions(fragment: str, ref: SourceRef) -> HtmlQuestionSet:
    """Split the observed PRE-style score-column format without dropping source.

    Offsets are character offsets in the saved section (end exclusive), and
    line numbers are 1-based. Unknown layouts and suspicious numbering remain
    needs_review. No page-specific answers or question counts are hardcoded.
    """
    digest = hashlib.sha256(fragment.encode("utf-8")).hexdigest()
    lines = fragment.splitlines(keepends=True)
    candidates = []
    offset = 0
    for line_index, raw_line in enumerate(lines):
        visible = BeautifulSoup(raw_line, "html.parser").get_text()
        if match := _QUESTION_START.match(visible):
            candidates.append((line_index, offset, int(match.group(2)),
                               len(match.group(1).expandtabs(8))))
        offset += len(raw_line)
    if not candidates or candidates[0][3] < 4:
        return HtmlQuestionSet(ref, "unsupported", digest, fragment, (), "", True,
                               "needs_review", ("No supported fixed-width question-number column found",))
    number_column = candidates[0][3]
    starts = [item for item in candidates if item[3] == number_column]
    issues = []
    if not re.search(r"баллы\s+задачи|points\s+problems", _statement_text(fragment[:starts[0][1]]), re.I):
        issues.append("Missing score-column heading; layout requires review")
    if [item[2] for item in starts] != list(range(1, len(starts) + 1)):
        issues.append("Question numbers are missing, duplicated or not sequential from 1")

    last_start = starts[-1][0]
    final_author_lines = [i for i in range(last_start, len(lines))
                          if _AUTHOR_LINE.fullmatch(_statement_text(lines[i]).rstrip("\r\n"))]
    content_end = len(fragment)
    if len(final_author_lines) == 1:
        content_end = sum(len(line) for line in lines[:final_author_lines[0] + 1])
        tail_text = _statement_text(fragment[content_end:]).strip()
        if tail_text and not re.match(r"(?:Примечание|Примечания|Note|Notes)\s*:", tail_text, re.I):
            issues.append("Trailing content is retained but is not a recognized section note")

    questions = []
    for index, (line_index, start, number, _) in enumerate(starts):
        end = starts[index + 1][1] if index + 1 < len(starts) else content_end
        question = _question_record(fragment[start:end], ref, number, start, line_index + 1, number_column)
        questions.append(question)
        issues.extend(f"Question {number}: {issue}" for issue in question.issues)
    preamble = fragment[:starts[0][1]]
    tail = fragment[content_end:]
    coverage = preamble + "".join(question.original_html for question in questions) + tail == fragment
    if not coverage:
        issues.append("Source coverage check failed")
    return HtmlQuestionSet(ref, "fixed_width_score_column", digest, preamble,
                           tuple(questions), tail, coverage,
                           "needs_review" if issues else "extracted", tuple(issues))
