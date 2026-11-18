from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tournament_agent.extractor import extract_html_questions, section_html
from tournament_agent.models import SourceRef
from tournament_agent.pipeline import extract_cached_html


REF = SourceRef("https://www.turgor.ru/en/problems/31/index.php", "example")
SAMPLE = """<a name=example>
баллы задачи
      1. Prove that x<sup>2</sup> + a<sub>1</sub> is positive.
  3      Keep 100 and 200 inside this question.
         1) first subpart;
         2) second subpart.
                              (A.B.Author)

      2. Construct a triangle.
  4      Use the following diagram.
         <img src="./triangle.gif" alt="triangle">
                              (C.D.Writer)

Примечание:
This note refers to the whole section.
"""


class HtmlQuestionTests(unittest.TestCase):
    def test_questions_scores_authors_and_subparts_stay_together(self):
        result = extract_html_questions(SAMPLE, REF)
        self.assertEqual([q.number for q in result.questions], [1, 2])
        self.assertEqual([q.points for q in result.questions], [3, 4])
        self.assertEqual([q.author for q in result.questions], ["A.B.Author", "C.D.Writer"])
        self.assertIn("1) first subpart", result.questions[0].text)
        self.assertIn("2) second subpart", result.questions[0].text)
        self.assertIn("100 and 200", result.questions[0].text)
        self.assertNotIn("Construct a triangle", result.questions[0].text)

    def test_no_source_characters_are_lost_and_note_is_not_question_two(self):
        result = extract_html_questions(SAMPLE, REF)
        self.assertTrue(result.coverage_verified)
        self.assertEqual(result.preamble_html + "".join(q.original_html for q in result.questions) + result.trailing_html, SAMPLE)
        for question in result.questions:
            self.assertEqual(SAMPLE[question.source_start:question.source_end], question.original_html)
        self.assertIn("Примечание:", result.trailing_html)
        self.assertNotIn("whole section", result.questions[1].text)

    def test_math_markup_and_image_ownership_are_preserved_for_review(self):
        result = extract_html_questions(SAMPLE, REF)
        self.assertIn("x<sup>2</sup>", result.questions[0].original_html)
        self.assertIn("x^{2} + a_{1}", result.questions[0].text)
        self.assertEqual(result.questions[0].images, ())
        image = result.questions[1].images[0]
        self.assertEqual(image.id, "example:q2:img1")
        self.assertEqual(image.url, "https://www.turgor.ru/en/problems/31/triangle.gif")
        self.assertEqual(result.status, "needs_review")

    def test_missing_score_or_duplicate_number_is_flagged(self):
        missing = extract_html_questions(SAMPLE.replace("  3      ", "         "), REF)
        self.assertIsNone(missing.questions[0].points)
        self.assertEqual(missing.status, "needs_review")
        duplicate = extract_html_questions(SAMPLE.replace("      2. Construct", "      1. Construct"), REF)
        self.assertEqual(duplicate.status, "needs_review")
        self.assertTrue(any("duplicated" in issue for issue in duplicate.issues))

    def test_unknown_html_layout_is_retained_for_review(self):
        html = '<ol><li>Question one</li><li>Question two</li></ol>'
        result = extract_html_questions(html, REF)
        self.assertEqual(result.status, "needs_review")
        self.assertEqual(result.questions, ())
        self.assertEqual(result.preamble_html, html)

    def test_pipeline_uses_original_hashes_and_has_no_network_calls(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_file = root / "source.html"
            page_html = ("<pre>" + SAMPLE + "</pre>").replace("\n", "\r\n")
            raw_file.write_text(page_html, encoding="utf-8", newline="")
            fragment = section_html(page_html, REF.anchor, {REF.anchor})
            section_file = root / "section.html"
            section_file.write_text(fragment, encoding="utf-8", newline="")
            source_hash = hashlib.sha256(raw_file.read_bytes()).hexdigest()
            inspection_file = root / "inspection.json"
            inspection_file.write_text(json.dumps({"html_sections": [{
                "anchor": REF.anchor, "source_url": REF.page_url,
                "status": "ready_for_extraction", "source_sha256": source_hash,
                "section_html_path": str(section_file), "age_group": "junior",
                "difficulty": "basic", "date": "2009-10-18"}]}), encoding="utf-8")
            manifest = {"pages": [{"url": REF.page_url, "path": str(raw_file), "content_type": "text/html; charset=utf-8"}],
                        "references": [asdict(REF)], "inspection": {"path": str(inspection_file),
                        "sha256": hashlib.sha256(inspection_file.read_bytes()).hexdigest()}}
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with patch("tournament_agent.fetcher.urlopen", side_effect=AssertionError("No network allowed")):
                report = extract_cached_html(root, [REF.anchor])
            self.assertEqual(report["errors"], [])
            self.assertEqual(report["model_calls"], 0)
            self.assertEqual(len(report["sets"][0]["questions"]), 2)
            self.assertTrue((root / "extracted" / "html_questions_review.html").exists())
            section_file.write_text(fragment + "changed", encoding="utf-8")
            report = extract_cached_html(root, [REF.anchor])
            self.assertIn("no longer matches original", report["errors"][0]["error"])


if __name__ == "__main__":
    unittest.main()
