import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pymupdf

from tournament_agent.models import PdfSpan
from tournament_agent.pdf_extractor import _line_text, _script_evidence, extract_pdf_page
from tournament_agent.main import extract_cached_pdf


URL = "https://example.com/questions.pdf"


def create_source(path: Path, *, duplicate=False, drawing=False):
    with pymupdf.open() as document:
        page = document.new_page()
        for y, text in ((30, "Junior advanced paper"), (48, "25 October 2009"), (70, "points problems")):
            page.insert_text((40, y), text, fontname="tiro", fontsize=12)
        page.insert_text((70, 100), "1.", fontname="tiro", fontsize=12)
        page.insert_text((95, 100), "Find all the numbers satisfying this condition.", fontname="tiro", fontsize=12)
        page.insert_text((35, 112), "6", fontname="tiro", fontsize=12)
        page.insert_text((260, 140), "A.B.Author", fontname="tiit", fontsize=12)
        page.insert_text((70, 170), "1." if duplicate else "2.", fontname="tiro", fontsize=12)
        page.insert_text((95, 170), "Prove both statements about the numbers.", fontname="tiro", fontsize=12)
        for y, label, score in ((195, "a)", "2"), (215, "b)", "7")):
            page.insert_text((70, y), label, fontname="tiro", fontsize=12)
            page.insert_text((35, y), score, fontname="tiro", fontsize=12)
            page.insert_text((95, y), "The condition holds.", fontname="tiro", fontsize=12)
        if drawing:
            page.draw_rect(pymupdf.Rect(320, 240, 380, 280))
        page.insert_text((260, 310), "C.D.Writer", fontname="tiit", fontsize=12)
        document.save(path)


def configure_cache(root: Path, source: Path, status="ready_for_extraction"):
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    inspection = root / "inspection.json"
    inspection.write_text(json.dumps({"pdf_pages": [{"source_url": URL, "page": 1,
                                                   "status": status, "source_sha256": digest}]}), encoding="utf-8")
    manifest = {"documents": [{"url": URL, "path": str(source), "sha256": digest, "status": "downloaded"}],
                "inspection": {"path": str(inspection), "sha256": hashlib.sha256(inspection.read_bytes()).hexdigest()}}
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


class PdfQuestionTests(unittest.TestCase):
    def test_positioned_numbers_scores_subparts_and_authors(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.pdf"
            create_source(path)
            with pymupdf.open(path) as doc:
                result = extract_pdf_page(doc[0], URL)
            self.assertEqual(result.status, "extracted")
            self.assertEqual([q.number for q in result.questions], [1, 2])
            self.assertEqual(result.questions[0].points, 6)
            self.assertIsNone(result.questions[1].points)
            self.assertEqual([(s.value, s.subpart) for s in result.questions[1].scores], [(2, "a)"), (7, "b)")])
            self.assertEqual([q.author for q in result.questions], ["A.B.Author", "C.D.Writer"])
            self.assertNotIn("Prove both", result.questions[0].text)
            self.assertTrue(result.span_coverage_verified)
            spans = list(result.preamble_spans) + [s for q in result.questions for s in q.spans]
            self.assertEqual(len({s.id for s in spans}), result.source_span_count)

    def test_superscripts_and_subscripts_remain_explicit_candidates(self):
        spans = [PdfSpan("base", "line", "x", (0, 88, 6, 102), (0, 100), "Times", 12, 4),
                 PdfSpan("exponent", "line", "2", (6, 88, 10, 97), (6, 96), "Times", 8, 5),
                 PdfSpan("subscript", "line", "i", (10, 98, 14, 106), (10, 104), "Times", 8, 4)]
        classified = _script_evidence([spans], 12)[0]
        self.assertEqual([s.script for s in classified], ["normal", "superscript_candidate", "subscript_candidate"])
        self.assertEqual(_line_text(classified), "x^{2}_{i}")
        self.assertEqual([s.text for s in classified], ["x", "2", "i"])

    def test_unknown_and_duplicate_number_layouts_are_not_accepted(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.pdf"
            create_source(path, duplicate=True)
            with pymupdf.open(path) as doc:
                result = extract_pdf_page(doc[0], URL)
            self.assertEqual(result.status, "needs_review")
            self.assertEqual(result.questions, ())
            self.assertTrue(result.preamble_spans)
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.draw_rect(pymupdf.Rect(20, 20, 100, 100))
            self.assertIn("No readable text", extract_pdf_page(page, URL).issues[0])

    def test_vector_content_is_retained_by_region_and_flagged(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.pdf"
            create_source(path, drawing=True)
            with pymupdf.open(path) as doc:
                result = extract_pdf_page(doc[0], URL)
            self.assertEqual(result.questions[0].graphics, ())
            self.assertEqual(len(result.questions[1].graphics), 1)
            self.assertEqual(result.status, "needs_review")

    def test_english_pages_are_not_extracted_or_rendered(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.pdf"
            create_source(source)
            configure_cache(root, source, "skipped_english")
            with patch.object(pymupdf.Page, "get_pixmap", side_effect=AssertionError("English rendering prohibited")):
                result = extract_cached_pdf(root, [URL])
            self.assertEqual(result["sets"], [])
            self.assertEqual(result["skipped"][0]["reason"], "skipped_english")
            self.assertFalse((root / "extracted" / "pdf_source").exists())

    def test_pipeline_creates_review_evidence_without_network_and_rejects_corruption(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.pdf"
            create_source(source)
            configure_cache(root, source)
            with patch("tournament_agent.archive_builder.urlopen", side_effect=AssertionError("No network allowed")):
                result = extract_cached_pdf(root, [URL])
            self.assertEqual(result["errors"], [])
            self.assertEqual(result["model_calls"], 0)
            self.assertTrue(Path(result["sets"][0]["page_image"]).exists())
            self.assertTrue(all(Path(q["region_image"]).exists() for q in result["sets"][0]["questions"]))
            self.assertTrue((root / "extracted" / "pdf_questions_review.html").exists())
            source.write_bytes(source.read_bytes() + b"changed")
            result = extract_cached_pdf(root, [URL])
            self.assertIn("hash changed", result["errors"][0]["error"])


if __name__ == "__main__":
    unittest.main()
