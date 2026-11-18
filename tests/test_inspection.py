from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pymupdf

from tournament_agent.webpage_extractor import check_language, inspect_html, inspect_pdf, section_html
from tournament_agent.models import SourceRef


class InspectionTests(unittest.TestCase):
    def test_legacy_unclosed_anchors_do_not_leak_next_section(self):
        html = '<pre><A name=one>first <sup>2</sup>\n<A name=two>second</pre><footer>navigation</footer>'
        self.assertEqual(section_html(html, "one", {"one", "two"}), '<A name=one>first <sup>2</sup>\n')
        self.assertEqual(section_html(html, "two", {"one", "two"}), '<A name=two>second')
        with self.assertRaises(ValueError):
            section_html(html, "missing", {"one", "two"})

    def test_ambiguous_boundary_is_not_silently_accepted(self):
        with self.assertRaises(ValueError):
            section_html('<a name=one>text<footer>other</footer>', "one", {"one"})

    def test_heading_only_section_is_a_pdf_pointer_not_a_set(self):
        html = '<pre><a name=advanced>8 - 9 классы, сложный вариант, 25 октября 2009 г.\nДоступен в <a href="./questions.pdf">PDF-формате</a>.</pre>'
        probe, original = inspect_html(html, SourceRef("https://www.turgor.ru/en/problems/31/index.php", "advanced"), {"advanced"})
        self.assertEqual(probe.status, "linked_pdf")
        self.assertEqual(probe.age_group, "junior")
        self.assertEqual(probe.difficulty, "advanced")
        self.assertEqual(probe.date, "2009-10-25")
        self.assertEqual(probe.document_links, ("https://www.turgor.ru/en/problems/31/questions.pdf",))
        self.assertIn('<a href="./questions.pdf">', original)

    def test_language_routing_does_not_treat_latin_variables_as_english(self):
        self.assertEqual(check_language("a b x y ABCD 123").language, "unknown")
        english = "Prove that the sum of the numbers on the board is greater than the product of the numbers in the boxes. " * 15
        russian = "Докажите, что сумма чисел на доске больше произведения чисел в коробках. Найдите все натуральные числа, удовлетворяющие условию задачи. " * 15
        self.assertEqual(check_language(english).language, "en")
        self.assertEqual(check_language(russian).language, "ru")
        self.assertNotEqual(check_language(english + russian).language, "en")

    def test_english_pdf_is_read_as_text_without_rendering(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "english.pdf"
            with pymupdf.open() as document:
                page = document.new_page()
                page.insert_text((40, 40), "Junior basic paper\n18 October 2009\n1. Prove that every number in this list is positive.\nThe numbers are chosen from the given collection of integers.")
                document.save(path)
            with patch.object(pymupdf.Page, "get_pixmap", side_effect=AssertionError("English must not be rendered")):
                probes = inspect_pdf(path, "https://example.com/english.pdf")
            self.assertEqual(probes[0].language.language, "en")
            self.assertEqual(probes[0].status, "skipped_english")


if __name__ == "__main__":
    unittest.main()
